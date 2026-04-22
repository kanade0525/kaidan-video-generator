from __future__ import annotations

from pathlib import Path

import tomllib

CONFIG_PATH = Path("data/config.toml")

_DEFAULTS = {
    "speaker_id": 47,
    "speed": 0.9,
    "pitch": 0.0,
    "intonation": 1.0,
    "volume": 2.0,
    "image_model": "gemini-2.5-flash-image",
    "image_size": "1792x1024",
    "image_aspect_ratio": "16:9",
    "image_person_generation": "DONT_ALLOW",
    "image_output_mime": "image/png",
    "image_compression_quality": 90,
    "image_negative_prompt": (
        "text, letters, words, writing, captions, watermark, signature, logo, "
        "title, subtitle, label, UI, numbers, symbols, typography, font, "
        "anime, cartoon, illustration, drawing, painting, sketch, "
        "bright colors, vibrant, cheerful, happy"
    ),
    "image_guidance_scale": 7.0,
    "image_seed": 0,
    "image_enhance_prompt": False,
    "image_add_watermark": False,
    "num_scenes": 3,
    "image_style": (
        "found footage style, low quality home video camera capture, "
        "surveillance camera footage, CCTV recording, VHS tape quality, "
        "heavy noise and static grain, scan lines, signal distortion, "
        "low resolution, slightly blurry, poor lighting, "
        "infrared night vision green tint OR washed-out security camera colors, "
        "timestamp overlay aesthetic, dark corners, lens flare artifacts, "
        "Japanese horror atmosphere, creepy unsettling mood, "
        "dread, foreboding, supernatural horror, cursed imagery, "
        "absolutely no text, no letters, no words, no writing, no watermarks, no UI elements, "
        "no high quality, no professional photography, no vivid colors, no cheerful mood"
    ),
    "image_rate_limit": 15,
    "fps": 30,
    "fade_in": 1.0,
    "fade_out": 1.0,
    "bgm_path": "",
    "bgm_volume": 0.1,
    "op_path": "",
    "op_fade_out": 1.0,
    "ed_path": "",
    "text_model": "gemini-2.5-flash",
    "gemini_model": "gemini-2.5-flash-lite",
    # LLM主導モード: True (default) → LLM優先(文脈依存読みに対応)、品質チェック
    # 不合格/失敗時に MeCab フォールバック。False → 従来通り MeCab 優先。
    "text_llm_primary": True,
    # アクセント補正: pyopenjtalk + marine (ML) を使って VOICEVOX 内蔵の
    # OpenJTalk 規則ベースアクセントを上書き。marine が利用不可なら自動でスキップ。
    "accent_correction_enabled": True,
    # 間（ま）制御 — 朗読の呼吸を調整
    # VOICEVOX の全 pause (。/、) をスケール (1.0=標準, 1.3=怪談向け)
    "pause_length_scale": 1.3,
    # チャンク結合時の無音 (文末 vs 文中で切替)
    "inter_chunk_gap_sentence": 0.6,  # 「。」「！」「？」で終わるチャンク後
    "inter_chunk_gap_default": 0.25,  # それ以外
    # YouTubeチャプター自動生成: LLMでチャンクにセクション名を振り、
    # description にチャプター (例: 「1:23 事件の発覚」) として挿入
    "chapter_auto_enabled": True,
    # 冒頭フック自動生成: LLM がストーリーから 30-60字 の煽り文を作り、
    # VOICEVOX で合成→ scene_001 の画像 + フック音声の短いクリップを
    # OP の前に挿入。視聴者の最初 5-8秒のフック強化。
    "hook_auto_enabled": True,
    # サムネイル自動生成: LLMが短い煽り文を作り、最もドラマティックなシーン画像に
    # タイトル + 煽り文をオーバーレイしてYouTubeサムネイルとして使用。
    "thumbnail_auto_enabled": True,
    "max_chunk": 200,
    "text_prompt": (
        "以下の日本語テキストをVOICEVOX朗読用にひらがな化してください。\n\n"
        "【変換ルール】\n"
        "1. 漢字は文脈に合う自然な読みでひらがなに変換する\n"
        "   例: 反省→はんせい、教訓→きょうくん、話→はなし、話題→わだい、\n"
        "       銀行→ぎんこう、母→はは、一人→ひとり、今日→きょう\n"
        "2. 助詞の「は」は「わ」、助詞の「へ」は「え」に変換する（助詞以外の「は」「へ」は変えない）\n"
        "   例: 私は走った→わたしわはしった、学校へ行く→がっこうえいく\n"
        "   注意: 「反省した」「話した」「入って」の「は」「へ」は助詞ではないので変えない\n"
        "3. 助詞の「を」はそのまま「を」で出力する（「お」に置換しない）\n"
        "4. 「である」「であり」「でした」「です」「だった」などの語尾は原形を崩さない\n"
        "5. カタカナ、句読点、記号、数字、空白はすべて原文のまま保持する\n\n"
        "【出力形式の厳守事項】\n"
        "・入力の改行位置を1つ残らず保持する。段落を結合したり一行にまとめない\n"
        "・元の文字を「（）」「［］」「【】」などで併記・注釈しない（例: ❌「わ（は）」）\n"
        "・変換結果のテキストのみを出力する。前置き・後書き・解説・箇条書きは一切不要\n"
        "・マークダウン記法（**, *, #, - など）は使わない\n"
        "・入力と近い長さ・区切りを保ち、内容を勝手に要約・追加しない\n"
    ),
    "scrape_delay": 2.0,
    "youtube_category_id": "24",
    "youtube_privacy_status": "private",
    "youtube_title_template": "【怪談朗読】{title}｜怖い話・睡眠用・作業用BGM",
    "youtube_playlist_url": "https://www.youtube.com/playlist?list=PLBj7GhxNHZWufHlq6pdSboVkOfx8uNGLY",
    "youtube_description_template": (
        "【怪談朗読】{title}\n\n"
        "百鬼朗読へようこそ。\n"
        "日本各地に伝わる怪談・不思議な話・人怖を朗読でお届けします。\n"
        "チャンネル登録・高評価よろしくお願いします。\n\n"
        "▶ 怪談再生リスト: {playlist_url}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "引用元:HHS図書館より\n"
        "「{title}」{url}\n\n"
        "音声: VOICEVOX:{speaker}\n"
        "BGM: 「Where the Light Never Speaks」松浦洋介\n"
        "(DOVA-SYNDROME: https://dova-s.jp/bgm/play22758.html)\n"
        "SE: 「オカルト系タイトルコール」Causality Sound\n"
        "(DOVA-SYNDROME: https://dova-s.jp/se/detail/1489)\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "#怪談 #ホラー #朗読 #怖い話 #ホラホリ #百鬼朗読"
    ),
    # 奇々怪々由来のストーリーが長尺に移送された場合のテンプレート。
    # HHS規約の「ホラホリ」タグは付与しない。
    "long_kikikaikai_youtube_description_template": (
        "【怪談朗読】{title}\n\n"
        "百鬼朗読へようこそ。\n"
        "日本各地に伝わる怪談・不思議な話・人怖を朗読でお届けします。\n"
        "チャンネル登録・高評価よろしくお願いします。\n\n"
        "▶ 怪談再生リスト: {playlist_url}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "引用元: 怖い話投稿サイト 奇々怪々\n"
        "「{title}」{url}\n"
        "作者: {author}\n\n"
        "音声: VOICEVOX:{speaker}\n"
        "BGM: 「Where the Light Never Speaks」松浦洋介\n"
        "(DOVA-SYNDROME: https://dova-s.jp/bgm/play22758.html)\n"
        "SE: 「オカルト系タイトルコール」Causality Sound\n"
        "(DOVA-SYNDROME: https://dova-s.jp/se/detail/1489)\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "#怪談 #ホラー #朗読 #怖い話 #百鬼朗読"
    ),
    "youtube_tags": "怪談,ホラー,朗読,怖い話,心霊,恐怖",
    "youtube_pinned_comment_template": (
        "ご視聴ありがとうございます！\n"
        "チャンネル登録・高評価していただけると励みになります🙏\n\n"
        "▶ 怪談再生リスト: {playlist_url}\n"
        "他にもたくさんの怪談を朗読しています。ぜひご覧ください！\n\n"
        "怖かったらコメントで教えてください👻"
    ),
    "youtube_channel_name": "",
    "youtube_contact_email": "",
    "youtube_schedule_enabled": True,
    "youtube_schedule_day": "saturday",
    "youtube_schedule_hour": 20,
    "youtube_schedule_minute": 0,
    # Shorts settings
    "shorts_leading_silence": 0.0,
    "shorts_trailing_silence": 0.5,
    "shorts_num_scenes": 2,
    "shorts_image_size": "1024x1792",
    "shorts_image_aspect_ratio": "9:16",
    "shorts_max_char_count": 880,
    "shorts_target_char_count": 440,
    "shorts_vhs_enabled": True,
    "shorts_speed": 1.15,
    "shorts_scrape_delay": 2.0,
    "shorts_endscreen_duration": 5.0,
    "shorts_bgm_volume": 0.1,
    "shorts_youtube_title_template": "【怪談朗読】{title}｜怖い話 #Shorts",
    "shorts_youtube_description_template": (
        "【怪談朗読】{title}\n\n"
        "百鬼朗読へようこそ。\n"
        "日本各地に伝わる怪談・不思議な話・人怖を朗読でお届けします。\n"
        "チャンネル登録・高評価よろしくお願いします。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "引用元: 怖い話投稿サイト 奇々怪々\n"
        "「{title}」{url}\n"
        "作者: {author}\n\n"
        "音声: VOICEVOX:{speaker}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "#怪談 #ホラー #朗読 #怖い話 #Shorts #百鬼朗読"
    ),
    # HHS-sourced stories migrated to Shorts pipeline use this template instead
    # (HHS 規約で正しい引用元表記が必要なため)。`#ホラホリ` タグも付与。
    "shorts_hhs_youtube_description_template": (
        "【怪談朗読】{title} #Shorts\n\n"
        "百鬼朗読へようこそ。\n"
        "日本各地に伝わる怪談・不思議な話・人怖を朗読でお届けします。\n"
        "チャンネル登録・高評価よろしくお願いします。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "引用元:HHS図書館より\n"
        "「{title}」{url}\n\n"
        "音声: VOICEVOX:{speaker}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "#怪談 #ホラー #朗読 #怖い話 #Shorts #ホラホリ #百鬼朗読"
    ),
    # Shorts 特化タグ: 睡眠用/作業用BGM 等の長尺向けは除外。
    # ホラホリは HHS由来 Short のみ upload 時に追加（source依存）。
    "shorts_youtube_tags": (
        "怪談,ホラー,朗読,怖い話,心霊,恐怖,怪談朗読,実話怪談,都市伝説,"
        "百鬼朗読,VOICEVOX,怖い話朗読,人怖,不思議な話,Shorts"
    ),
    # Narration dictionary customization (user additions on top of hardcoded defaults)
    "reading_overrides": {},      # surface(漢字等) → ひらがな読み
    "compound_replacements": {},  # 置換元 → 置換後（MeCab前に適用）
    "keep_as_kanji": [],          # 漢字のまま残す語のリスト
}


def load_config() -> dict:
    """Load config from TOML file, falling back to defaults."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            user_config = tomllib.load(f)
        return {**_DEFAULTS, **user_config}
    return dict(_DEFAULTS)


def _toml_quote(s: str) -> str:
    """Escape a string for inclusion in a TOML basic string."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_config(config: dict) -> None:
    """Save config to TOML file."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in config.items():
        if isinstance(value, str):
            if "\n" in value:
                lines.append(f'{key} = """\n{value}"""')
            else:
                lines.append(f'{key} = "{value}"')
        elif isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, float):
            lines.append(f"{key} = {value}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        elif isinstance(value, list):
            items = ", ".join(_toml_quote(str(v)) for v in value)
            lines.append(f"{key} = [{items}]")
        elif isinstance(value, dict):
            items = ", ".join(
                f"{_toml_quote(str(k))} = {_toml_quote(str(v))}"
                for k, v in value.items()
            )
            lines.append(f"{key} = {{{items}}}")
        else:
            lines.append(f"{key} = {value!r}")
    CONFIG_PATH.write_text("\n".join(lines) + "\n")


def get(key: str):
    """Get a single config value."""
    return load_config().get(key, _DEFAULTS.get(key))
