from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class MediaError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _binary(name: str) -> str:
    path = shutil.which(name)
    if path:
        return path
    # Supports the sandbox's ffmpeg-binaries package and local development installs.
    try:
        import ffmpeg
        bundled = Path(ffmpeg.__file__).parent / "binaries" / name
        if bundled.is_file() and os.access(bundled, os.X_OK):
            return str(bundled)
    except (ImportError, TypeError):
        pass
    raise MediaError("ffmpeg_unavailable", f"{name} is not installed on this worker.")


def run_ffmpeg(args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    command = [_binary("ffmpeg"), "-hide_banner", "-nostdin", "-y", *args]
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaError("ffmpeg_timeout", "FFmpeg exceeded the configured processing timeout.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        message = detail[-1][:500] if detail else "FFmpeg could not process the media file."
        raise MediaError("ffmpeg_failed", message) from exc


def probe_media(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [_binary("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=45,
        )
        data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise MediaError("media_probe_failed", "FFprobe could not validate this media file.") from exc
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError("video_stream_missing", "The media file does not contain a readable video stream.")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    return {
        "duration_seconds": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "video_codec": video.get("codec_name"),
        "audio_streams": sum(s.get("codec_type") == "audio" for s in streams),
        "audio_codecs": [s.get("codec_name") for s in streams if s.get("codec_type") == "audio"],
        "format": data.get("format", {}).get("format_name"),
        "file_size_bytes": path.stat().st_size,
    }


def extract_audio(source: Path, destination: Path, normalize: bool = True) -> dict[str, Any]:
    source_info = probe_media(source)
    if source_info["audio_streams"] == 0:
        return {"has_audio": False, "reason": "source_has_no_audio", "source_duration": source_info["duration_seconds"]}
    destination.parent.mkdir(parents=True, exist_ok=True)
    filters = "aresample=16000:async=1:first_pts=0" + (",loudnorm=I=-16:TP=-1.5:LRA=11" if normalize else "")
    try:
        run_ffmpeg(["-i", str(source), "-vn", "-map", "0:a:0", "-af", filters, "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1", "-f", "wav", str(destination)], timeout=1800)
    except MediaError as exc:
        destination.unlink(missing_ok=True)
        if exc.code == "ffmpeg_failed":
            raise MediaError("audio_extraction_failed", "The source audio stream is corrupted or could not be decoded.") from exc
        raise
    if not destination.is_file() or destination.stat().st_size < 48:
        destination.unlink(missing_ok=True)
        raise MediaError("audio_extraction_failed", "FFmpeg did not produce a valid temporary audio file.")
    return {"has_audio": True, "path": str(destination), "duration_seconds": source_info["duration_seconds"], "format": "wav", "sample_rate": 16000, "channels": 1, "normalized": normalize, "size_bytes": destination.stat().st_size}
