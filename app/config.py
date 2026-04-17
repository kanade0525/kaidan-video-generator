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
    "max_chunk": 200,
    "text_prompt": (
        "以下の日本語テキストの漢字をすべて自然なひらがなに変換してください。\n\n"
        "ルール:\n"
        "・漢字を自然な日本語読みでひらがなに変換する。文脈に応じた正しい読みを使うこと\n"
        "  例: 巷→ちまた、人→ひと（文脈による）、今日→きょう、一人→ひとり\n"
        "  例: 話→はなし、話題→わだい、銀行→ぎんこう、母→はは\n"
        "・助詞の「は」は「わ」に、助詞の「へ」は「え」に変換する\n"
        "  例: 私は→わたしわ、学校へ→がっこうえ、彼は→かれわ\n"
        "・カタカナはそのまま保持する\n"
        "・句読点、記号、数字はそのまま保持する\n"
        "・文の構造や改行はそのまま保持する\n"
        "・1人は いちにん ではなく ひとり に変換する\n"
        "・説明や注釈、例示は不要。変換結果のテキストのみ出力する\n"
        "・余計なマークダウンや箇条書きは出力しない\n"
        "・入力と類似した長さ・区切りを保つようにする\n"
        "例: 母は走った → ははわはしった\n"
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
    "shorts_youtube_tags": "怪談,ホラー,朗読,怖い話,Shorts,音読さん,都市伝説",
}


def load_config() -> dict:
    """Load config from TOML file, falling back to defaults."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            user_config = tomllib.load(f)
        return {**_DEFAULTS, **user_config}
    return dict(_DEFAULTS)


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
        else:
            lines.append(f"{key} = {value!r}")
    CONFIG_PATH.write_text("\n".join(lines) + "\n")


def get(key: str):
    """Get a single config value."""
    return load_config().get(key, _DEFAULTS.get(key))
