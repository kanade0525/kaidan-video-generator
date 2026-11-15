from __future__ import annotations

from nicegui import ui

# ブランドカラー。動画側の classic_red に合わせた深い赤を差し色にして、
# 画面の大半は無彩色で静かにまとめる。
ACCENT = "#b91c1c"

# サイドバーの項目: (見出し, [(ラベル, パス, アイコン), ...])
NAV_SECTIONS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("長編動画", [
        ("パイプライン", "/", "account_tree"),
        ("ストーリー管理", "/stories", "menu_book"),
        ("生成結果", "/results", "movie"),
        ("詰め合わせ動画", "/long/bundle", "video_library"),
    ]),
    ("ショート動画", [
        ("Shortsパイプライン", "/shorts", "account_tree"),
        ("Shortsストーリー", "/shorts/stories", "menu_book"),
        ("Shorts生成結果", "/shorts/results", "smartphone"),
    ]),
    ("", [
        ("設定", "/settings", "settings"),
    ]),
]

# 全ページ共通の見た目。Quasar 標準 (影の強いカード・原色ボタン) を、
# 細い枠線と落ち着いた配色に置き換える。個々のページのコードは触らずに
# 効くよう、Quasar のクラスに対して当てている。
THEME_CSS = """
:root {
  --app-bg: #f3f4f6;
  --app-surface: #ffffff;
  --app-border: #e5e7eb;
  --app-ink: #111827;
  --app-muted: #6b7280;
  --app-accent: #b91c1c;
  --app-chrome: #0f141c;
  --app-chrome-2: #161c27;
}
body {
  background: var(--app-bg);
  color: var(--app-ink);
  font-family: "Inter", "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP",
    "Yu Gothic UI", "Meiryo", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  font-feature-settings: "palt";
}
.nicegui-content { padding: 28px 32px 48px; gap: 0; }

/* ── ヘッダーとサイドバー ── */
.app-header {
  background: var(--app-chrome);
  border-bottom: 1px solid rgba(255, 255, 255, .06);
  padding: 0 20px;
  min-height: 52px;
}
.app-brand-mark {
  width: 26px; height: 26px; border-radius: 7px;
  background: var(--app-accent); color: #fff;
  display: grid; place-items: center;
  font-size: 15px; font-weight: 800; line-height: 1;
}
.app-brand-name { font-size: 15px; font-weight: 700; letter-spacing: .04em; }
.app-drawer { background: var(--app-chrome-2); padding: 12px 10px; }
.app-nav-heading {
  font-size: 11px; font-weight: 600; letter-spacing: .12em;
  color: #7b8597; padding: 14px 12px 6px;
}
.app-nav-link {
  display: flex; align-items: center; gap: 10px; width: 100%;
  padding: 8px 12px; border-radius: 8px;
  color: #c9d1dd; font-size: 13.5px; text-decoration: none;
  transition: background .12s ease, color .12s ease;
}
.app-nav-link .q-icon { font-size: 18px; color: #7b8597; }
.app-nav-link:hover { background: rgba(255, 255, 255, .06); color: #fff; }
.app-nav-link--active {
  background: rgba(185, 28, 28, .18); color: #fff; font-weight: 600;
  box-shadow: inset 3px 0 0 var(--app-accent);
}
.app-nav-link--active .q-icon { color: #f87171; }
.app-nav-divider { height: 1px; background: rgba(255, 255, 255, .07); margin: 10px 4px 0; }

/* ── カード・ボタン・表 (全ページ共通) ── */
.q-card {
  border-radius: 12px;
  border: 1px solid var(--app-border);
  box-shadow: 0 1px 2px rgba(16, 24, 40, .05);
}
.q-btn { border-radius: 8px; font-weight: 600; text-transform: none; letter-spacing: .02em; }
.q-btn:before { box-shadow: none; }
.q-table__card, .q-table__container {
  border-radius: 12px; border: 1px solid var(--app-border); box-shadow: none;
}
.q-table thead th {
  font-size: 12px; font-weight: 600; color: var(--app-muted);
  background: #f9fafb; letter-spacing: .04em;
}
.q-table tbody td { font-size: 13.5px; }
.q-table tbody tr:hover { background: #f9fafb; }
.q-separator { background: var(--app-border); }
.q-tab { text-transform: none; }
.q-tab__label { font-weight: 600; letter-spacing: .02em; }
.q-tab-panels { border-radius: 12px; border: 1px solid var(--app-border); }
.q-expansion-item { border-radius: 10px; }

/* ── パイプライン画面 ── */
.stage-card { padding: 16px 18px; gap: 0; transition: border-color .15s ease; }
.stage-card--running { border-color: #bbf7d0; }
.stage-card--blocked { border-color: #fde68a; }
.stage-card__step {
  font-size: 11px; font-weight: 700; color: var(--app-muted);
  font-variant-numeric: tabular-nums; letter-spacing: .06em;
}
.stage-card__title { font-size: 15px; font-weight: 700; }
.status-pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 2px 10px 2px 8px; border-radius: 999px;
  font-size: 12px; font-weight: 600; white-space: nowrap;
}
.status-pill::before {
  content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor;
}
.status-pill--idle { background: #f3f4f6; color: #6b7280; }
.status-pill--running { background: #dcfce7; color: #15803d; }
.status-pill--running::before { animation: app-pulse 1.6s ease-in-out infinite; }
@keyframes app-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
.metric__value {
  font-size: 26px; font-weight: 700; line-height: 1.1;
  font-variant-numeric: tabular-nums;
}
.metric__value--zero { color: #c4c9d2; }
.metric__label { font-size: 11.5px; color: var(--app-muted); letter-spacing: .04em; }
.stage-card__blocked {
  font-size: 12px; line-height: 1.45; color: #92400e;
  background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px;
  padding: 6px 9px; margin-top: 12px;
}
.count-tile { padding: 12px 16px; min-width: 128px; gap: 2px; }
.section-title { font-size: 16px; font-weight: 700; margin: 32px 0 12px; }
.log-panel {
  width: 100%; background: #0f141c; border-radius: 12px;
  padding: 14px 16px; gap: 3px; overflow-x: auto;
}
.log-line {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px; line-height: 1.55; color: #aab4c3; white-space: nowrap;
}
.log-line--error { color: #fca5a5; }
.log-line__time { color: #5d6778; }
.halt-banner {
  width: 100%; padding: 12px 14px; margin-bottom: 16px; border-radius: 10px;
  background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c;
  font-size: 13.5px; font-weight: 600;
}
"""


def _current_path() -> str:
    """いま開いているページのパス。取れない環境 (テスト等) では空文字。"""
    try:
        return ui.context.client.request.url.path
    except Exception:
        return ""


def create_layout():
    """Create the common page layout with sidebar navigation."""
    ui.colors(
        primary="#1f2937",
        secondary="#4b5563",
        accent=ACCENT,
        positive="#15803d",
        negative="#dc2626",
        warning="#b45309",
        info="#2563eb",
    )
    ui.add_css(THEME_CSS)
    current = _current_path()

    with ui.header().classes("app-header items-center text-white"):
        with ui.row().classes("items-center gap-3 no-wrap"):
            # 狭い画面ではサイドバーが本文に重なるので、畳めるよう開閉ボタンを置く
            ui.button(icon="menu", on_click=lambda: drawer.toggle(), color=None).props(
                "flat round dense color=white",
            )
            ui.label("怪").classes("app-brand-mark")
            ui.label("怪談動画ジェネレータ").classes("app-brand-name")

    # value=True で初期状態を固定する。未指定だと NiceGUI が接続のたびにブラウザへ
    # 開閉状態を問い合わせ、1秒以内に応答が無いと TimeoutError をログに残す。
    with ui.left_drawer(value=True).classes("app-drawer text-white") as drawer:
        with ui.column().classes("gap-0 w-full"):
            for i, (heading, items) in enumerate(NAV_SECTIONS):
                if i > 0:
                    ui.element("div").classes("app-nav-divider")
                if heading:
                    ui.label(heading).classes("app-nav-heading")
                else:
                    ui.element("div").style("height: 10px")
                for label, path, icon in items:
                    cls = "app-nav-link"
                    if path == current:
                        cls += " app-nav-link--active"
                    with ui.link(target=path).classes(cls):
                        ui.icon(icon)
                        ui.label(label)

    return drawer
