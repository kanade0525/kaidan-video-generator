"""Accent estimation using pyopenjtalk + marine for VOICEVOX.

VOICEVOX internally uses OpenJTalk's rule-based accent estimation. Marine is
an ML-based estimator that can produce more natural accents for some words.

This module:
1. Runs pyopenjtalk + marine to estimate per-phrase accent positions.
2. Overrides VOICEVOX's audio_query `accent` fields with marine values,
   matching phrases by mora count (only override when counts match).

If marine is unavailable, silently falls back to VOICEVOX's native accents.
"""
from __future__ import annotations

import re

from app.utils.log import get_logger

log = get_logger("kaidan.accent")


def estimate_phrases(text: str) -> list[tuple[int, int]] | None:
    """Estimate (mora_count, accent_position) per phrase using marine.

    Returns None if pyopenjtalk/marine are unavailable.
    """
    try:
        import pyopenjtalk
    except ImportError:
        return None

    try:
        njd = pyopenjtalk.run_frontend(text)
        try:
            njd = pyopenjtalk.estimate_accent(njd)
        except Exception as e:
            log.debug("marine estimation unavailable, using openjtalk defaults: %s", e)

        labels = pyopenjtalk.make_label(njd)
        phrases: list[tuple[int, int]] = []
        seen: set[str] = set()
        for lab in labels:
            m = re.search(r"/F:(\d+)_(\d+)", lab)
            if m:
                key = m.group(0)
                if key not in seen:
                    seen.add(key)
                    phrases.append((int(m.group(1)), int(m.group(2))))
        return phrases
    except Exception as e:
        log.warning("accent estimation failed: %s", e)
        return None


def apply_accent_override(query: dict, text: str) -> dict:
    """Override VOICEVOX audio_query accent_phrases with marine estimates.

    Matches phrases by mora count. Only overrides when counts match exactly —
    otherwise VOICEVOX's original accent is kept (safer fallback).

    Modifies `query` in place and returns it.
    """
    estimates = estimate_phrases(text)
    if not estimates:
        return query

    ap = query.get("accent_phrases", [])
    overridden = 0
    skipped_mismatch = 0
    for i, phrase in enumerate(ap):
        if i >= len(estimates):
            skipped_mismatch += 1
            continue
        vox_mora_count = len(phrase.get("moras", []))
        est_mora_count, est_accent = estimates[i]
        if vox_mora_count == est_mora_count:
            original = phrase.get("accent")
            if original != est_accent:
                phrase["accent"] = est_accent
                overridden += 1
        else:
            skipped_mismatch += 1

    if overridden or skipped_mismatch:
        log.debug(
            "accent override: %d overridden, %d skipped (mora mismatch)",
            overridden, skipped_mismatch,
        )
    return query
