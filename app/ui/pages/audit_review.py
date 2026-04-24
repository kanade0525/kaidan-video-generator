from __future__ import annotations

import csv
from pathlib import Path

from nicegui import ui

AUDIT_CSV = Path("/app/data/audit_voicevox.csv")

CHOICES = ("override", "keep_kanji", "ignore")


def _load_rows() -> list[dict]:
    if not AUDIT_CSV.exists():
        return []
    with AUDIT_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save_rows(rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    tmp = AUDIT_CSV.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(AUDIT_CSV)


def audit_review_page():
    ui.label("読み監査レビュー").classes("text-2xl font-bold mb-2")

    rows = _load_rows()
    if not rows:
        ui.label(f"CSVが見つかりません: {AUDIT_CSV}").classes("text-red-500")
        return

    # Sort by count desc; preserve original index for save-back
    rows.sort(key=lambda r: int(r.get("count") or 0), reverse=True)

    state = {
        "idx": 0,
        "min_count": 5,
    }

    def _visible() -> list[int]:
        return [
            i for i, r in enumerate(rows)
            if int(r.get("count") or 0) >= state["min_count"]
        ]

    visible_idx = _visible()
    if not visible_idx:
        state["min_count"] = 1
        visible_idx = _visible()

    # --- Header controls ---
    header = ui.row().classes("gap-4 items-center mb-2")
    with header:
        progress_label = ui.label("").classes("text-sm text-gray-600")
        ui.label("最小 count:").classes("text-sm")
        count_input = ui.number(value=state["min_count"], min=1, step=1).props("dense").classes("w-20")

    # --- Main card ---
    card = ui.card().classes("p-6 w-full max-w-3xl")
    with card:
        surface_label = ui.label("").classes("text-5xl font-bold text-center my-2")

        with ui.row().classes("w-full justify-center gap-8 my-3"):
            with ui.column().classes("items-center"):
                ui.label("MeCab").classes("text-xs text-gray-500")
                mecab_label = ui.label("").classes("text-2xl font-mono")
            with ui.column().classes("items-center"):
                ui.label("VOICEVOX").classes("text-xs text-gray-500")
                vvx_label = ui.label("").classes("text-2xl font-mono")

        meta_label = ui.label("").classes("text-sm text-gray-600 text-center")
        context_label = ui.label("").classes("text-base bg-gray-100 p-3 rounded font-mono")

        ui.label("VOICEVOXにどう読ませたい？").classes("text-sm text-gray-600 text-center mt-4")

        # Choice buttons — labeled by the actual reading the user wants
        with ui.row().classes("gap-3 justify-center mt-2"):
            btn_override = ui.button("").props("size=lg color=green")
            btn_ignore = ui.button("").props("size=lg color=blue")
            btn_keep = ui.button("漢字のまま (VVX文脈判断) [K]", color="grey").props("size=lg")

        with ui.row().classes("gap-3 justify-center mt-2"):
            ui.button("← 戻る (J)", on_click=lambda: _move(-1)).props("flat")
            ui.button("スキップ (S) →", on_click=lambda: _move(1)).props("flat")

        current_label_display = ui.label("").classes("text-xs text-gray-500 text-center mt-2")

    def _render():
        vis = _visible()
        if not vis:
            surface_label.text = "— 対象行なし —"
            return
        if state["idx"] >= len(vis):
            state["idx"] = len(vis) - 1
        if state["idx"] < 0:
            state["idx"] = 0
        real_i = vis[state["idx"]]
        r = rows[real_i]
        surface_label.text = r["surface"]
        mecab_label.text = r["mecab_reading"]
        vvx_label.text = r["voicevox_reading"]
        meta_label.text = (
            f"count: {r['count']}  /  "
            f"既存辞書: {'あり' if r.get('already_handled') == '1' else 'なし'}"
        )
        context_label.text = r.get("sample_context", "")
        btn_override.text = f'「{r["mecab_reading"]}」と読ませる [O]'
        btn_ignore.text = f'「{r["voicevox_reading"]}」のままでOK [I]'
        current_label_display.text = f"現在の選択: {r.get('label', '(未選択)')}"
        progress_label.text = f"{state['idx'] + 1} / {len(vis)}  (全{len(rows)}行中 count≥{state['min_count']}で絞り込み)"

    def _set_label(choice: str):
        vis = _visible()
        if not vis:
            return
        real_i = vis[state["idx"]]
        rows[real_i]["label"] = choice
        _save_rows(rows)
        _move(1)

    def _move(delta: int):
        vis = _visible()
        if not vis:
            return
        new_idx = state["idx"] + delta
        if 0 <= new_idx < len(vis):
            state["idx"] = new_idx
        _render()

    def _on_count_change(e):
        try:
            state["min_count"] = max(1, int(e.value or 1))
        except (TypeError, ValueError):
            state["min_count"] = 1
        state["idx"] = 0
        _render()

    count_input.on("change", _on_count_change)
    btn_override.on_click(lambda: _set_label("override"))
    btn_keep.on_click(lambda: _set_label("keep_kanji"))
    btn_ignore.on_click(lambda: _set_label("ignore"))

    # Keyboard shortcuts — only fire on keydown to avoid double-triggering
    def _on_key(e):
        if not isinstance(e.args, dict):
            return
        if e.args.get("action") != "keydown":
            return
        key = (e.args.get("key") or "").lower()
        if key == "o":
            _set_label("override")
        elif key == "k":
            _set_label("keep_kanji")
        elif key == "i":
            _set_label("ignore")
        elif key in ("s", "arrowright"):
            _move(1)
        elif key in ("j", "arrowleft"):
            _move(-1)

    ui.keyboard(on_key=_on_key)

    _render()
