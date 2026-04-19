from __future__ import annotations

_PINNED_COLORS: dict[str, str] = {
    "怪談": "red-7",
}

_PALETTE: list[str] = [
    "blue-7",
    "purple-7",
    "teal-7",
    "orange-8",
    "indigo-7",
    "pink-7",
    "green-8",
    "brown-6",
    "cyan-7",
    "deep-orange-7",
    "blue-grey-7",
    "lime-9",
]


def category_color(category: str) -> str:
    """Return a stable Quasar color name for a category.

    「怪談」 is pinned to red for visual priority; other categories hash
    into a fixed palette so the same name always gets the same color.
    """
    if category in _PINNED_COLORS:
        return _PINNED_COLORS[category]
    idx = sum(ord(c) for c in category) % len(_PALETTE)
    return _PALETTE[idx]
