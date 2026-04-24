"""Batch audit: compare MeCab readings vs VOICEVOX audio_query readings.

Scans scraped-stage stories, tokenizes the raw text with MeCab, and for every
kanji-containing surface calls VOICEVOX `audio_query` to discover what reading
VOICEVOX would actually use. Discrepancies are written to a CSV for review.

CSV columns:
    surface              — original kanji surface (as tokenized by MeCab)
    mecab_reading        — MeCab's predicted reading (hiragana)
    voicevox_reading     — VOICEVOX's reading for this surface (hiragana)
    count                — how many story-tokens matched this surface
    sample_context       — short context snippet from one of the stories
    already_handled      — "1" if the surface is already in an existing dict
                           (_DEFAULT_KEEP_AS_KANJI / _DEFAULT_COMPOUND_REPLACEMENTS /
                            _DEFAULT_READING_OVERRIDES), else ""

The reviewer adds a `label` column with one of:
    override     → add {surface: mecab_reading} to reading_overrides
    keep_kanji   → add surface to keep_as_kanji
    ignore       → skip

Usage:
    docker compose exec -T app python scripts/audit_voicevox_readings.py \
        --limit 10 --out /tmp/audit.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

# Ensure the project root is importable regardless of invocation cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import database as db  # noqa: E402
from app.services.text_processor import (  # noqa: E402
    _DEFAULT_COMPOUND_REPLACEMENTS,
    _DEFAULT_KEEP_AS_KANJI,
    _DEFAULT_READING_OVERRIDES,
    _extract_reading,
    _katakana_to_hiragana,
)
from app.utils.paths import raw_content_path  # noqa: E402

VOICEVOX_HOST = os.environ.get("VOICEVOX_HOST", "http://localhost:50021")
DEFAULT_SPEAKER = int(os.environ.get("VOICEVOX_SPEAKER", "3"))

_KANJI_RE_SRC = "[一-龯々〆]"

# Morae whose vowel is /o/ — an immediately-following う is pronounced as an
# /o:/ long vowel, identical to お. Same for /e/-morae + い (→ え long).
_O_MORAE = set("おこそとのほもよろごぞどぼぽ") | {
    "きょ", "しょ", "ちょ", "にょ", "ひょ", "みょ", "りょ",
    "ぎょ", "じょ", "びょ", "ぴょ",
}
_E_MORAE = set("えけせてねへめれげぜでべぺ")


def _normalize_longvowel(s: str) -> str:
    """Collapse う/い long-vowel variants that sound identical.

    VOICEVOX outputs long /o:/ as 「...お」 while MeCab / dictionaries typically
    write it 「...う」 (e.g., 本当=ほんとう→VVX returns ほんとお). The two
    transcriptions pronounce the same. Normalize both sides to compare on
    actual pronunciation.
    """
    out: list[str] = []
    i = 0
    while i < len(s):
        # Check 2-char yōon first (きょ etc.) followed by う
        pair = s[i:i + 2]
        if pair in _O_MORAE and i + 2 < len(s) and s[i + 2] == "う":
            out.append(pair)
            out.append("お")
            i += 3
            continue
        ch = s[i]
        if ch in _O_MORAE and i + 1 < len(s) and s[i + 1] == "う":
            out.append(ch)
            out.append("お")
            i += 2
            continue
        if ch in _E_MORAE and i + 1 < len(s) and s[i + 1] == "い":
            out.append(ch)
            out.append("え")
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _has_kanji(s: str) -> bool:
    import re
    return bool(re.search(_KANJI_RE_SRC, s))


def _tokenize(text: str) -> list[tuple[str, str | None]]:
    """Return (surface, hiragana_reading) for every token with kanji."""
    import MeCab
    import unidic_lite

    tagger = MeCab.Tagger(f"-d {unidic_lite.DICDIR}")
    tagger.parse("")
    out: list[tuple[str, str | None]] = []
    node = tagger.parseToNode(text)
    while node:
        surface = node.surface
        if surface and _has_kanji(surface):
            feature = node.feature.split(",")
            kata = _extract_reading(feature)
            hira = _katakana_to_hiragana(kata) if kata else None
            out.append((surface, hira))
        node = node.next
    return out


def _voicevox_reading(surface: str, speaker: int) -> str | None:
    """Ask VOICEVOX how it would read `surface`. Returns hiragana or None."""
    try:
        r = requests.post(
            f"{VOICEVOX_HOST}/audio_query",
            params={"text": surface, "speaker": speaker},
            timeout=10,
        )
        r.raise_for_status()
        query = r.json()
    except Exception as e:
        print(f"  ! audio_query failed for {surface!r}: {e}", file=sys.stderr)
        return None

    kata_parts: list[str] = []
    for phrase in query.get("accent_phrases", []):
        for mora in phrase.get("moras", []):
            t = mora.get("text") or ""
            kata_parts.append(t)
        if phrase.get("pause_mora"):
            pass
    if not kata_parts:
        return None
    return _katakana_to_hiragana("".join(kata_parts))


def _already_handled(surface: str) -> bool:
    return (
        surface in _DEFAULT_KEEP_AS_KANJI
        or surface in _DEFAULT_COMPOUND_REPLACEMENTS
        or surface in _DEFAULT_READING_OVERRIDES
    )


def _suggest_label(count: int, already_handled: bool) -> str:
    """Heuristic pre-label so reviewer edits only disagreements.

    - already in an existing dict → review_existing (manual check)
    - count >= 2 (recurring) → override (VVX likely wrong)
    - count == 1 (singleton) → ignore (rare, low ROI)
    """
    if already_handled:
        return "review_existing"
    if count >= 2:
        return "override"
    return "ignore"


def audit(limit: int, out_path: Path, speaker: int) -> None:
    stories = db.get_stories(limit=limit)
    print(f"Loaded {len(stories)} stories", file=sys.stderr)

    # Aggregate: surface -> {mecab_reading, count, sample}
    agg: dict[str, dict] = defaultdict(lambda: {"count": 0, "mecab": None, "sample": ""})

    for i, story in enumerate(stories, 1):
        raw_path = raw_content_path(story.title, story.content_type)
        if not raw_path.exists():
            print(f"  [{i}/{len(stories)}] skip (no raw): {story.title}", file=sys.stderr)
            continue
        text = raw_path.read_text(encoding="utf-8")
        print(f"  [{i}/{len(stories)}] {story.title} ({len(text)} chars)", file=sys.stderr)

        for surface, reading in _tokenize(text):
            if not reading:
                continue
            entry = agg[surface]
            entry["count"] += 1
            entry["mecab"] = reading
            if not entry["sample"]:
                idx = text.find(surface)
                if idx >= 0:
                    lo = max(0, idx - 15)
                    hi = min(len(text), idx + len(surface) + 15)
                    entry["sample"] = text[lo:hi].replace("\n", " ")

    unique_surfaces = sorted(agg.keys(), key=lambda s: agg[s]["count"], reverse=True)
    print(f"Unique kanji surfaces: {len(unique_surfaces)}", file=sys.stderr)

    # Query VOICEVOX for each unique surface and record discrepancies
    rows: list[dict] = []
    for j, surface in enumerate(unique_surfaces, 1):
        entry = agg[surface]
        mecab = entry["mecab"]
        vv = _voicevox_reading(surface, speaker)
        if j % 50 == 0:
            print(f"  queried {j}/{len(unique_surfaces)}", file=sys.stderr)
        if vv is None or mecab is None:
            continue
        if vv == mecab:
            continue
        # Skip pairs that only differ in long-vowel orthography (same sound).
        if _normalize_longvowel(vv) == _normalize_longvowel(mecab):
            continue
        handled = _already_handled(surface)
        rows.append({
            "surface": surface,
            "mecab_reading": mecab,
            "voicevox_reading": vv,
            "count": entry["count"],
            "sample_context": entry["sample"],
            "already_handled": "1" if handled else "",
            "suggested": _suggest_label(entry["count"], handled),
            "label": _suggest_label(entry["count"], handled),
        })
        time.sleep(0.02)  # gentle on VOICEVOX

    rows.sort(key=lambda r: r["count"], reverse=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "surface", "mecab_reading", "voicevox_reading",
                "count", "sample_context", "already_handled",
                "suggested", "label",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} discrepancy rows to {out_path}", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--out", type=Path, default=Path("/tmp/audit_voicevox.csv"))
    p.add_argument("--speaker", type=int, default=DEFAULT_SPEAKER)
    args = p.parse_args()
    audit(args.limit, args.out, args.speaker)


if __name__ == "__main__":
    main()
