from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_PATH = Path("data/config.toml")

_DEFAULTS = {
    "speaker_id": 47,
    "speed": 0.9,
    "pitch": 0.0,
    "intonation": 1.0,
    "volume": 1.2,
    "image_model": "z-image",
    "image_size": "1792x1024",
    "num_scenes": 3,
    "image_style": (
        "photorealistic, cinematic lighting, dark atmosphere, Japanese horror, "
        "no text, no letters, no words, no writing, no captions"
    ),
    "image_rate_limit": 15,
    "fps": 30,
    "fade_in": 1.0,
    "fade_out": 1.0,
    "bgm_path": "",
    "bgm_volume": 0.1,
    "gemini_model": "gemini-2.5-flash-lite",
    "max_chunk": 200,
    "text_prompt": (
        "以下のテキストをVOICEVOX用に変換してください。\n"
        "・すべての漢字をひらがなに変換\n"
        "・カタカナはそのまま保持\n"
        "・句読点や記号はそのまま保持\n"
        "・余計な説明は不要。変換結果のみ出力"
    ),
    "scrape_delay": 2.0,
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
