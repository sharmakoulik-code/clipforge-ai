from __future__ import annotations

from pathlib import Path
import re
from typing import Any


STYLES = {
    "classic": {"font": "Arial", "size": 64, "text": "&H00FFFFFF", "active": "&H0000D7FF", "outline": 3, "back": "&H80000000", "align": 2, "margin_v": 120},
    "creator": {"font": "Arial", "size": 70, "text": "&H00FFFFFF", "active": "&H0000EFFF", "outline": 4, "back": "&H90000000", "align": 2, "margin_v": 145},
    "minimal": {"font": "Arial", "size": 58, "text": "&H00FFFFFF", "active": "&H0000D7FF", "outline": 2, "back": "&H00000000", "align": 2, "margin_v": 105},
    "dynamic": {"font": "Arial", "size": 72, "text": "&H00FFFFFF", "active": "&H0000FFFF", "outline": 5, "back": "&HA0000000", "align": 2, "margin_v": 175},
    "custom": {"font": "Arial", "size": 64, "text": "&H00FFFFFF", "active": "&H0000D7FF", "outline": 3, "back": "&H80000000", "align": 2, "margin_v": 120},
}


def _ass_time(seconds: float) -> str:
    total = max(0, round(seconds * 100))
    hours, rem = divmod(total, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, centis = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ").strip()


def _wrap_tokens(tokens: list[str], words_per_line: int = 7, max_chars: int = 38) -> list[list[int]]:
    lines: list[list[int]] = []
    current: list[int] = []
    current_chars = 0
    for idx, token in enumerate(tokens):
        length = len(token)
        if current and (len(current) >= words_per_line or current_chars + length + 1 > max_chars):
            lines.append(current)
            current = []
            current_chars = 0
        current.append(idx)
        current_chars += length + (1 if len(current) > 1 else 0)
    if current:
        lines.append(current)
    return lines


def _event(start: float, end: float, body: str, layer: int = 0) -> str:
    return f"Dialogue: {layer},{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{body}"


def _ass_color(value: str, opacity: int | None = None) -> str:
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        red, green, blue = value[1:3], value[3:5], value[5:7]
        alpha = max(0, min(255, round(255 * (1 - (opacity if opacity is not None else 100) / 100))) if opacity is not None else 0)
        return f"&H{alpha:02X}{blue}{green}{red}"
    if re.fullmatch(r"&H[0-9A-Fa-f]{8}", value):
        return value.upper()
    raise ValueError("Caption colors must be #RRGGBB or eight-digit ASS colors.")


def generate_ass(transcript: dict[str, Any], destination: Path, clip_start: float, clip_end: float, style: str = "creator",
                 options: dict[str, Any] | None = None, width: int = 1080, height: int = 1920) -> dict[str, Any]:
    options = options or {}
    selected = STYLES.get(style, STYLES["creator"]).copy()
    selected["font"] = str(options.get("font_family", selected["font"]))[:64]
    if not re.fullmatch(r"[A-Za-z0-9 _-]{1,64}", selected["font"]):
        selected["font"] = "Arial"
    selected["size"] = max(18, min(120, int(options.get("font_size", selected["size"]))))
    opacity = max(0, min(100, int(options.get("background_opacity", 55))))
    selected["text"] = _ass_color(str(options.get("text_color", selected["text"])))
    selected["active"] = _ass_color(str(options.get("active_word_color", selected["active"])))
    selected["outline"] = max(0, min(10, int(options.get("outline", selected["outline"]))))
    selected["back"] = _ass_color(str(options.get("background_color", "#000000")), opacity) if "background_color" in options or "background_opacity" in options else selected["back"]
    position = options.get("position", selected["align"])
    selected["align"] = {"bottom": 2, "middle": 5, "top": 8}.get(str(position).lower(), int(position) if str(position).isdigit() else selected["align"])
    selected["margin_v"] = max(40, min(height // 2, int(options.get("margin", selected["margin_v"]))))
    animation = str(options.get("animation", "none")).lower()
    animation_tag = {"fade": r"{\fad(120,120)}", "pop": r"{\fscx85\fscy85\t(0,120,\fscx100\fscy100)}"}.get(animation, "")
    words_per_line = max(2, min(12, int(options.get("words_per_line", 7))))
    max_chars = max(18, min(48, int(options.get("max_chars_per_line", 38))))
    header = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]", "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: Default,{selected['font']},{selected['size']},{selected['text']},{selected['text']},&H00101010,{selected['back']},-1,0,0,0,100,100,0,0,1,{selected['outline']},1,{selected['align']},72,72,{selected['margin_v']},1", "",
        "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    events = []
    caption_count = 0
    for segment in transcript.get("segments", []):
        start, end = float(segment.get("start", 0)), float(segment.get("end", 0))
        start, end = max(start, clip_start), min(end, clip_end)
        if end <= start:
            continue
        local_start, local_end = start - clip_start, end - clip_start
        words = segment.get("words")
        if isinstance(words, list) and words:
            clean = []
            for word in words:
                try:
                    ws, we = float(word["start"]), float(word["end"])
                    token = _escape(str(word.get("word", "")))
                except (KeyError, TypeError, ValueError):
                    continue
                ws, we = max(ws, start), min(we, end)
                if token and we > ws:
                    clean.append({"word": token, "start": ws, "end": we})
            if clean:
                clean.sort(key=lambda word: word["start"])
                groups = _wrap_tokens([word["word"] for word in clean], words_per_line, max_chars)[:2]
                active_color = f"&H{selected['active'][-6:]}&"
                normal_color = f"&H{selected['text'][-6:]}&"
                for active_index, active_word in enumerate(clean):
                    lines = []
                    for group in groups:
                        pieces = []
                        for index in group:
                            token = clean[index]["word"]
                            if index == active_index:
                                pieces.append(f"{{\\1c{active_color}}}{token}{{\\1c{normal_color}}}")
                            else:
                                pieces.append(token)
                        lines.append(" ".join(pieces))
                    body = animation_tag + r"\N".join(lines)
                    events.append(_event(active_word["start"] - clip_start, active_word["end"] - clip_start, body))
                    caption_count += 1
                continue
        text = _escape(str(segment.get("text", "")))
        tokens = re.findall(r"\S+", text)
        if not tokens:
            continue
        groups = _wrap_tokens(tokens, words_per_line, max_chars)[:2]
        phrase = r"\N".join(" ".join(tokens[i] for i in group) for group in groups)
        events.append(_event(local_start, local_end, animation_tag + phrase))
        caption_count += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(header + events) + "\n", encoding="utf-8")
    return {"caption_style": style, "caption_count": caption_count, "uses_word_timing": any(s.get("words") for s in transcript.get("segments", [])), "file": str(destination)}
