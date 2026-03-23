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

        image_model = ui.input("モデル", value=config.get("image_model", "z-image")).classes("w-64")
        image_size = ui.select(
            ["1792x1024", "1024x1024", "1280x720", "1024x576"],
            value=config.get("image_size", "1792x1024"),
            label="サイズ",
        ).classes("w-48")
        num_scenes = ui.number("シーン数", value=config.get("num_scenes", 3), min=1, max=10)
        image_style = ui.textarea(
            "スタイルプロンプト", value=config.get("image_style", "")
        ).classes("w-full")
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

    # Text settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("テキスト設定").classes("text-lg font-bold mb-2")

        gemini_model = ui.input(
            "Geminiモデル", value=config.get("gemini_model", "gemini-2.5-flash-lite")
        ).classes("w-64")
        max_chunk = ui.number("最大チャンク長", value=config.get("max_chunk", 200), min=50, max=1000)
        text_prompt = ui.textarea(
            "テキスト処理プロンプト", value=config.get("text_prompt", "")
        ).classes("w-full")

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
            "num_scenes": int(num_scenes.value),
            "image_style": image_style.value,
            "image_rate_limit": int(image_rate.value),
            "fps": int(fps.value),
            "fade_in": fade_in.value,
            "fade_out": fade_out.value,
            "bgm_path": bgm_select.value,
            "bgm_volume": bgm_volume.value,
            "gemini_model": gemini_model.value,
            "max_chunk": int(max_chunk.value),
            "text_prompt": text_prompt.value,
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
    bgm_dir = Path("bgm")
    if not bgm_dir.exists():
        return []
    return sorted(bgm_dir.glob("*.*"))
