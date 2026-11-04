import json
from fractions import Fraction
from math import gcd
from pathlib import Path
import shutil
import subprocess

from fastapi import HTTPException, status


class MetadataError(Exception):
    """Raised when a file is not a readable video or FFprobe is unavailable."""


def extract_video_metadata(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FFprobe is not installed on this server. Use the Docker backend image or install FFmpeg/FFprobe.",
        )
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
        )
        data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise MetadataError("FFprobe could not read this video file") from exc

    streams = data.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video is None:
        raise MetadataError("The uploaded file has no video stream")
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    frame_rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
    try:
        fps = float(Fraction(frame_rate)) if frame_rate and frame_rate != "0/0" else None
    except (ValueError, ZeroDivisionError):
        fps = None
    width, height = video.get("width"), video.get("height")
    aspect_ratio = video.get("display_aspect_ratio")
    if aspect_ratio in (None, "N/A") and width and height:
        divisor = gcd(width, height)
        aspect_ratio = f"{width // divisor}:{height // divisor}"
    return {
        "duration_seconds": float(data.get("format", {}).get("duration") or video.get("duration") or 0),
        "width": width,
        "height": height,
        "frame_rate": fps,
        "codec": video.get("codec_name"),
        "container": data.get("format", {}).get("format_name"),
        "audio_streams": len(audio_streams),
        "audio_codecs": [stream.get("codec_name") for stream in audio_streams],
        "aspect_ratio": aspect_ratio,
        "file_size_bytes": path.stat().st_size,
    }
