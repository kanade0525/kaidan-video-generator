from __future__ import annotations

import os
from pathlib import Path

from nicegui import ui

from app.config import load_config, save_config


def settings_page():
    """Settings page for all pipeline parameters."""
    ui.label("設定").classes("text-2xl font-bold mb-4")

    config = load_config()

    # Voice settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("音声設定").classes("text-lg font-bold mb-2")

        speakers = _get_speakers()
        speaker_options = {s["id"]: s["name"] for s in speakers} if speakers else {}

        speaker_select = ui.select(
            speaker_options, value=config.get("speaker_id", 47), label="話者"
        ).classes("w-64")

        with ui.row().classes("gap-4"):
            speed_slider = ui.slider(min=0.5, max=2.0, step=0.1, value=config.get("speed", 0.9))
            ui.label().bind_text_from(speed_slider, "value", lambda v: f"速度: {v}")

        with ui.row().classes("gap-4"):
            pitch_slider = ui.slider(min=-1.0, max=1.0, step=0.1, value=config.get("pitch", 0.0))
            ui.label().bind_text_from(pitch_slider, "value", lambda v: f"ピッチ: {v}")

        with ui.row().classes("gap-4"):
            intonation_slider = ui.slider(
                min=0.0, max=2.0, step=0.1, value=config.get("intonation", 1.0)
            )
            ui.label().bind_text_from(intonation_slider, "value", lambda v: f"抑揚: {v}")

        with ui.row().classes("gap-4"):
            volume_slider = ui.slider(min=0.0, max=2.0, step=0.1, value=config.get("volume", 1.2))
            ui.label().bind_text_from(volume_slider, "value", lambda v: f"音量: {v}")

    # Image settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("画像設定").classes("text-lg font-bold mb-2")

        image_model = ui.input("モデル", value=config.get("image_model", "gemini-2.5-flash-image")).classes("w-64")

        with ui.row().classes("gap-4 items-end"):
            image_size = ui.select(
                ["1792x1024", "1024x1024", "1280x720", "1024x576"],
                value=config.get("image_size", "1792x1024"),
                label="サイズ (AirForce用)",
            ).classes("w-48")
            image_aspect = ui.select(
                ["16:9", "4:3", "1:1", "3:4", "9:16"],
                value=config.get("image_aspect_ratio", "16:9"),
                label="アスペクト比 (Gemini/Imagen用)",
            ).classes("w-48")

        num_scenes = ui.number("シーン数", value=config.get("num_scenes", 3), min=1, max=10)
        image_style = ui.textarea(
            "スタイルプロンプト", value=config.get("image_style", "")
        ).classes("w-full")
        image_negative = ui.textarea(
            "ネガティブプロンプト", value=config.get("image_negative_prompt", "")
        ).classes("w-full")

        ui.label("Imagen専用パラメータ").classes("text-md font-bold mt-4 mb-2")
        ui.label("以下はimagenモデル使用時のみ有効です").classes("text-xs text-gray-500 mb-2")

        with ui.row().classes("gap-4 items-end flex-wrap"):
            image_person = ui.select(
                ["DONT_ALLOW", "ALLOW_ADULT", "ALLOW_ALL"],
                value=config.get("image_person_generation", "DONT_ALLOW"),
                label="人物生成",
            ).classes("w-48")
            image_mime = ui.select(
                ["image/png", "image/jpeg"],
                value=config.get("image_output_mime", "image/png"),
                label="出力フォーマット",
            ).classes("w-48")

        with ui.row().classes("gap-4"):
            image_compression = ui.slider(min=10, max=100, step=5, value=config.get("image_compression_quality", 90))
            ui.label().bind_text_from(image_compression, "value", lambda v: f"圧縮品質: {int(v)}")

        with ui.row().classes("gap-4"):
            image_guidance = ui.slider(min=1.0, max=20.0, step=0.5, value=config.get("image_guidance_scale", 7.0))
            ui.label().bind_text_from(image_guidance, "value", lambda v: f"ガイダンススケール: {v}")

        with ui.row().classes("gap-4 items-end"):
            image_seed = ui.number("シード (0=ランダム)", value=config.get("image_seed", 0), min=0)
            image_enhance = ui.checkbox("プロンプト自動改善", value=config.get("image_enhance_prompt", False))
            image_watermark = ui.checkbox("透かし追加", value=config.get("image_add_watermark", False))

        image_rate = ui.number(
            "API間隔(秒)", value=config.get("image_rate_limit", 15), min=1, max=120
        )

    # Video settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("動画設定").classes("text-lg font-bold mb-2")

        fps = ui.number("FPS", value=config.get("fps", 30), min=1, max=60)

        with ui.row().classes("gap-4"):
            fade_in = ui.slider(min=0.0, max=5.0, step=0.5, value=config.get("fade_in", 1.0))
            ui.label().bind_text_from(fade_in, "value", lambda v: f"フェードイン: {v}s")

        with ui.row().classes("gap-4"):
            fade_out = ui.slider(min=0.0, max=5.0, step=0.5, value=config.get("fade_out", 1.0))
            ui.label().bind_text_from(fade_out, "value", lambda v: f"フェードアウト: {v}s")

        # BGM selection
        bgm_files = _get_bgm_files()
        bgm_options = {"": "なし", **{str(f): f.name for f in bgm_files}}
        bgm_select = ui.select(
            bgm_options, value=config.get("bgm_path", ""), label="BGM"
        ).classes("w-64")

        with ui.row().classes("gap-4"):
            bgm_volume = ui.slider(
                min=0.0, max=1.0, step=0.05, value=config.get("bgm_volume", 0.1)
            )
            ui.label().bind_text_from(bgm_volume, "value", lambda v: f"BGM音量: {v}")

        # OP/ED selection
        op_files = _get_video_files("op")
        op_options = {"": "なし", **{str(f): f.name for f in op_files}}
        op_select = ui.select(
            op_options, value=config.get("op_path", ""), label="OP動画"
        ).classes("w-64")

        with ui.row().classes("gap-4"):
            op_fade = ui.slider(min=0.0, max=3.0, step=0.5, value=config.get("op_fade_out", 1.0))
            ui.label().bind_text_from(op_fade, "value", lambda v: f"OPフェードアウト: {v}s")

        ed_files = _get_video_files("ed")
        ed_options = {"": "なし", **{str(f): f.name for f in ed_files}}
        ed_select = ui.select(
            ed_options, value=config.get("ed_path", ""), label="ED動画"
        ).classes("w-64")

    # Text settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("テキスト設定").classes("text-lg font-bold mb-2")

        text_model_input = ui.input(
            "テキスト処理モデル", value=config.get("text_model", "gpt-4o-mini")
        ).classes("w-64")
        gemini_model = ui.input(
            "Geminiモデル (画像プロンプト用)", value=config.get("gemini_model", "gemini-2.5-flash-lite")
        ).classes("w-64")
        max_chunk = ui.number("最大チャンク長", value=config.get("max_chunk", 200), min=50, max=1000)
        text_prompt = ui.textarea(
            "テキスト処理プロンプト", value=config.get("text_prompt", "")
        ).classes("w-full")

    # YouTube settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("YouTube設定").classes("text-lg font-bold mb-2")

        from app.services import youtube_uploader

        # Auth status
        with ui.row().classes("gap-4 items-center mb-2"):
            if youtube_uploader.is_authenticated():
                ui.label("認証済み").classes("text-green-500 font-bold")
            elif youtube_uploader.is_configured():
                ui.label("未認証（client_secret.json あり）").classes("text-yellow-500")
            else:
                ui.label("未設定（data/client_secret.json を配置してください）").classes("text-red-500")

            def do_auth():
                try:
                    youtube_uploader.authenticate()
                    ui.notify("YouTube認証成功！", color="positive")
                except Exception as e:
                    ui.notify(f"認証エラー: {e}", color="negative")

            ui.button("YouTube認証", on_click=do_auth, color="red").props("size=sm")

        yt_category = ui.select(
            {"24": "エンターテインメント", "22": "ブログ", "27": "教育", "1": "映画", "10": "音楽"},
            value=config.get("youtube_category_id", "24"),
            label="カテゴリ",
        ).classes("w-48")

        yt_privacy = ui.select(
            {"private": "非公開", "unlisted": "限定公開", "public": "公開"},
            value=config.get("youtube_privacy_status", "private"),
            label="公開状態",
        ).classes("w-48")

        yt_title_tmpl = ui.input(
            "タイトルテンプレート（{title}で怪談名に置換）",
            value=config.get("youtube_title_template", "【怪談朗読】{title}"),
        ).classes("w-full")

        yt_description = ui.textarea(
            "説明テンプレート（{title}でタイトルに置換）",
            value=config.get("youtube_description_template", ""),
        ).classes("w-full")

        yt_tags = ui.input(
            "タグ（カンマ区切り）",
            value=config.get("youtube_tags", "怪談,ホラー,朗読"),
        ).classes("w-full")

        ui.label("使用報告（ホラホリ利用規約で必須）").classes("text-md font-bold mt-4 mb-2")
        yt_channel = ui.input(
            "チャンネル名", value=config.get("youtube_channel_name", "")
        ).classes("w-64")
        yt_email = ui.input(
            "連絡先メールアドレス", value=config.get("youtube_contact_email", "")
        ).classes("w-64")
        ui.label("アップロード成功時にHHS図書館に使用報告を自動送信します").classes("text-xs text-gray-500")

        ui.label("予約投稿").classes("text-md font-bold mt-4 mb-2")
        yt_schedule_enabled = ui.checkbox(
            "予約投稿を有効にする", value=config.get("youtube_schedule_enabled", True)
        )
        with ui.row().classes("gap-4 items-end"):
            yt_schedule_day = ui.select(
                {
                    "monday": "月曜", "tuesday": "火曜", "wednesday": "水曜",
                    "thursday": "木曜", "friday": "金曜", "saturday": "土曜", "sunday": "日曜",
                },
                value=config.get("youtube_schedule_day", "saturday"),
                label="曜日",
            ).classes("w-32")
            yt_schedule_hour = ui.number(
                "時", value=config.get("youtube_schedule_hour", 20), min=0, max=23
            ).classes("w-20")
            yt_schedule_minute = ui.number(
                "分", value=config.get("youtube_schedule_minute", 0), min=0, max=59
            ).classes("w-20")
        ui.label("次の指定曜日・時間に公開されます（日本時間）").classes("text-xs text-gray-500")

    # Save button
    def save():
        new_config = {
            "speaker_id": speaker_select.value,
            "speed": speed_slider.value,
            "pitch": pitch_slider.value,
            "intonation": intonation_slider.value,
            "volume": volume_slider.value,
            "image_model": image_model.value,
            "image_size": image_size.value,
            "image_aspect_ratio": image_aspect.value,
            "image_person_generation": image_person.value,
            "image_output_mime": image_mime.value,
            "image_compression_quality": int(image_compression.value),
            "image_negative_prompt": image_negative.value,
            "image_guidance_scale": image_guidance.value,
            "image_seed": int(image_seed.value),
            "image_enhance_prompt": image_enhance.value,
            "image_add_watermark": image_watermark.value,
            "num_scenes": int(num_scenes.value),
            "image_style": image_style.value,
            "image_rate_limit": int(image_rate.value),
            "fps": int(fps.value),
            "fade_in": fade_in.value,
            "fade_out": fade_out.value,
            "bgm_path": bgm_select.value,
            "bgm_volume": bgm_volume.value,
            "op_path": op_select.value,
            "op_fade_out": op_fade.value,
            "ed_path": ed_select.value,
            "text_model": text_model_input.value,
            "gemini_model": gemini_model.value,
            "max_chunk": int(max_chunk.value),
            "text_prompt": text_prompt.value,
            "youtube_title_template": yt_title_tmpl.value,
            "youtube_category_id": yt_category.value,
            "youtube_privacy_status": yt_privacy.value,
            "youtube_description_template": yt_description.value,
            "youtube_tags": yt_tags.value,
            "youtube_channel_name": yt_channel.value,
            "youtube_contact_email": yt_email.value,
            "youtube_schedule_enabled": yt_schedule_enabled.value,
            "youtube_schedule_day": yt_schedule_day.value,
            "youtube_schedule_hour": int(yt_schedule_hour.value),
            "youtube_schedule_minute": int(yt_schedule_minute.value),
        }
        save_config(new_config)
        ui.notify("設定を保存しました", color="positive")

    ui.button("設定を保存", on_click=save, color="green").classes("mt-4")


def _get_speakers() -> list[dict]:
    """Fetch speakers from VOICEVOX, return empty list on failure."""
    try:
        from app.services.voice_generator import get_speakers
        result = []
        for s in get_speakers():
            for style in s.get("styles", []):
                result.append({"id": style["id"], "name": f"{s['name']} ({style['name']})"})
        return result
    except Exception:
        return []


def _get_bgm_files() -> list[Path]:
    """List available BGM files."""
    for d in [Path("assets/bgm"), Path("bgm")]:
        if d.exists():
            return sorted(d.glob("*.*"))
    return []


def _get_video_files(subdir: str) -> list[Path]:
    """List available video files in assets subdirectory."""
    d = Path(f"assets/{subdir}")
    if not d.exists():
        return []
    return sorted(d.glob("*.mp4"))
