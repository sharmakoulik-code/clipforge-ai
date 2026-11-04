from __future__ import annotations

import re
from typing import Any


ALLOWED = {"font_family", "font_size", "text_color", "active_word_color", "background_color", "background_opacity", "outline", "position", "animation", "words_per_line", "max_chars_per_line", "margin"}


def validate_caption_options(raw: dict[str, Any] | None) -> dict[str, Any]:
    options = raw or {}
    if not isinstance(options, dict) or set(options) - ALLOWED:
        raise ValueError("Caption options contain unsupported settings.")
    result = dict(options)
    if "font_family" in result and (not isinstance(result["font_family"], str) or not re.fullmatch(r"[A-Za-z0-9 _-]{1,64}", result["font_family"])):
        raise ValueError("Font family may contain only letters, numbers, spaces, hyphens, and underscores.")
    for key in ("text_color", "active_word_color", "background_color"):
        if key in result and (not isinstance(result[key], str) or not re.fullmatch(r"(?:#[0-9A-Fa-f]{6}|&H[0-9A-Fa-f]{8})", result[key])):
            raise ValueError(f"{key} must be a hexadecimal color.")
    ranges = {"font_size": (18, 120), "background_opacity": (0, 100), "outline": (0, 10), "words_per_line": (2, 12), "max_chars_per_line": (18, 48), "margin": (40, 960)}
    for key, (minimum, maximum) in ranges.items():
        if key in result:
            try:
                value = int(result[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be an integer.") from exc
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}.")
            result[key] = value
    if "position" in result and result["position"] not in {"top", "middle", "bottom", 2, 5, 8}:
        raise ValueError("Caption position must be top, middle, or bottom.")
    if "animation" in result and result["animation"] not in {"none", "fade", "pop"}:
        raise ValueError("Caption animation must be none, fade, or pop.")
    return result
