from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.video.ffmpeg import MediaError, _binary, probe_media, run_ffmpeg


def _expr(samples: list[dict[str, Any]], axis: str, clip_start: float, fallback: int) -> str:
    rows = sorted(samples, key=lambda item: float(item.get("time", clip_start)))
    if not rows:
        return str(fallback)
    points = [(max(0.0, float(row.get("time", clip_start)) - clip_start), int(row[axis])) for row in rows]
    if len({value for _, value in points}) == 1:
        return str(points[0][1])
    expression = str(points[-1][1])
    for index in range(len(points) - 2, -1, -1):
        t0, v0 = points[index]
        t1, v1 = points[index + 1]
        if t1 <= t0:
            continue
        linear = f"{v0}+({v1}-{v0})*(t-{t0:.3f})/{t1-t0:.3f}"
        expression = f"if(lt(t\\,{t1:.3f})\\,{linear}\\,{expression})"
    return expression


def _filter_path(path: Path) -> str:
    return path.as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def _interpolate(samples: list[dict[str, Any]], axis: str, timestamp: float, clip_start: float, fallback: int) -> int:
    points = sorted((float(row.get("time", clip_start)), int(row.get(axis, fallback))) for row in samples)
    if not points:
        return fallback
    if timestamp <= points[0][0]:
        return points[0][1]
    for left, right in zip(points, points[1:]):
        if left[0] <= timestamp <= right[0]:
            fraction = (timestamp - left[0]) / max(0.001, right[0] - left[0])
            return int(round(left[1] + (right[1] - left[1]) * fraction))
    return points[-1][1]


def _dynamic_crop_render(source: Path, output: Path, start: float, duration: float, samples: list[dict[str, Any]],
                         subtitle_path: Path, width: int, height: int, fps: int, bitrate: str, watermark: bool = False) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise MediaError("opencv_unavailable", "OpenCV is required to render tracked speaker crop movement.") from exc
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise MediaError("video_decode_failed", "OpenCV could not decode the source for moving crop rendering.")
    source_fps = cap.get(cv2.CAP_PROP_FPS)
    if not source_fps or source_fps < 1 or source_fps > 120:
        source_fps = float(fps)
    crop_w, crop_h = int(samples[0]["width"]), int(samples[0]["height"])
    fallback_x, fallback_y = int(samples[0]["x"]), int(samples[0]["y"])
    y_values = sorted(int(row.get("y", fallback_y)) for row in samples)
    fixed_y = y_values[len(y_values) // 2]
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    frame_count = max(1, int(round(duration * source_fps)))
    dynamic_filters = f"setsar=1,fps={fps},subtitles='{_filter_path(subtitle_path)}'"
    if watermark:
        dynamic_filters += ",drawtext=text='ClipForge':fontcolor=white@0.70:fontsize=28:x=w-tw-24:y=h-th-24:box=1:boxcolor=black@0.35:boxborderw=6"
    command = [_binary("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{width}x{height}", "-r", f"{source_fps:.6f}", "-i", "pipe:0", "-ss", f"{start:.3f}", "-i", str(source),
               "-t", f"{duration:.3f}", "-map", "0:v:0", "-map", "1:a:0?", "-vf", dynamic_filters,
               "-c:v", "libx264", "-preset", "veryfast", "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", "10M",
               "-pix_fmt", "yuv420p", "-fps_mode", "cfr", "-c:a", "aac", "-b:a", "192k", "-af", "aresample=async=1:first_pts=0",
               "-movflags", "+faststart", "-max_muxing_queue_size", "1024", str(output)]
    error_file = tempfile.TemporaryFile()
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=error_file)
        assert process.stdin is not None
        written = 0
        while written < frame_count:
            ok, frame = cap.read()
            if not ok:
                break
            timestamp = start + written / source_fps
            x = max(0, min(frame.shape[1] - crop_w, _interpolate(samples, "x", timestamp, start, fallback_x)))
            y = max(0, min(frame.shape[0] - crop_h, fixed_y))
            cropped = frame[y:y + crop_h, x:x + crop_w]
            if cropped.shape[0] != crop_h or cropped.shape[1] != crop_w:
                raise MediaError("crop_bounds_invalid", "Tracked crop coordinates exceeded the decoded frame bounds.")
            resized = cv2.resize(cropped, (width, height), interpolation=cv2.INTER_LANCZOS4)
            process.stdin.write(resized.tobytes())
            written += 1
        process.stdin.close()
        code = process.wait(timeout=max(900, int(duration * 60)))
        if code != 0 or written < max(1, int(frame_count * .8)):
            error_file.seek(0)
            message = error_file.read().decode("utf-8", "replace")[-1000:]
            raise MediaError("dynamic_render_failed", message or "Dynamic crop rendering did not complete successfully.")
    except (BrokenPipeError, OSError, subprocess.TimeoutExpired) as exc:
        if process and process.poll() is None:
            process.kill()
            process.wait()
        error_file.seek(0)
        message = error_file.read().decode("utf-8", "replace")[-1000:]
        raise MediaError("dynamic_render_failed", message or "Dynamic crop rendering was interrupted.") from exc
    finally:
        cap.release()
        error_file.close()
        if process and process.stdin and not process.stdin.closed:
            process.stdin.close()


def render_clip(source: Path, destination: Path, start: float, end: float, crop: dict[str, Any], subtitle_path: Path,
                thumbnail_path: Path, preview_path: Path, width: int | None = None, height: int | None = None,
                fps: int | None = None, bitrate: str | None = None, watermark: bool = False) -> dict[str, Any]:
    width, height = width or settings.render_width, height or settings.render_height
    fps, bitrate = fps or settings.render_fps, bitrate or settings.render_video_bitrate
    duration = end - start
    if start < 0 or duration <= 0:
        raise MediaError("invalid_clip_timestamps", "Clip boundaries are invalid.")
    samples = crop.get("samples", [])
    if not samples:
        raise MediaError("crop_data_missing", "No crop coordinates were generated for this clip.")
    crop_w, crop_h = int(samples[0]["width"]), int(samples[0]["height"])
    fallback_x, fallback_y = int(samples[0]["x"]), int(samples[0]["y"])
    x_expr = _expr(samples, "x", start, fallback_x)
    # FFmpeg crop expressions can safely animate one axis; use a smoothed fixed vertical center.
    y_values = sorted(int(row.get("y", fallback_y)) for row in samples)
    y_expr = str(y_values[len(y_values) // 2] if y_values else fallback_y)
    video_filter = f"crop={crop_w}:{crop_h}:'{x_expr}':{y_expr},scale={width}:{height}:flags=lanczos,setsar=1,fps={fps},subtitles='{_filter_path(subtitle_path)}'"
    if watermark:
        video_filter += ",drawtext=text='ClipForge':fontcolor=white@0.70:fontsize=28:x=w-tw-24:y=h-th-24:box=1:boxcolor=black@0.35:boxborderw=6"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.stem + ".partial" + destination.suffix)
    temp.unlink(missing_ok=True)
    try:
        moving_x = str(crop.get("method", "")).endswith("_face_track") and len({int(row.get("x", fallback_x)) for row in samples}) > 1
        if moving_x:
            _dynamic_crop_render(source, temp, start, duration, samples, subtitle_path, width, height, fps, bitrate, watermark)
        else:
            run_ffmpeg(["-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}", "-map", "0:v:0", "-map", "0:a:0?",
                        "-vf", video_filter, "-c:v", "libx264", "-preset", "veryfast", "-b:v", bitrate, "-maxrate", bitrate,
                        "-bufsize", "10M", "-pix_fmt", "yuv420p", "-fps_mode", "cfr", "-c:a", "aac", "-b:a", "192k",
                        "-af", "aresample=async=1:first_pts=0", "-movflags", "+faststart", "-max_muxing_queue_size", "1024", str(temp)], timeout=max(900, int(duration * 60)))
        if not temp.is_file() or temp.stat().st_size < 1024:
            raise MediaError("render_output_missing", "FFmpeg did not produce a non-empty clip.")
        output = probe_media(temp)
        if output["width"] != width or output["height"] != height:
            raise MediaError("render_dimensions_invalid", "Rendered output dimensions do not match the configured vertical format.")
        if output["width"] >= output["height"] or output["video_codec"] != "h264":
            raise MediaError("render_validation_failed", "The rendered output is not a vertical H.264 video.")
        if not duration - 1.25 <= output["duration_seconds"] <= duration + 0.5:
            raise MediaError("render_duration_invalid", "Rendered clip duration differs materially from the selected source interval.")
        os.replace(temp, destination)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-ss", f"{min(max(0.25, output['duration_seconds'] / 2), max(0.25, output['duration_seconds'] - .1)):.3f}", "-i", str(destination), "-frames:v", "1", "-q:v", "2", str(thumbnail_path)], timeout=120)
    run_ffmpeg(["-i", str(destination), "-vf", "scale=480:854:force_original_aspect_ratio=decrease,pad=480:854:(ow-iw)/2:(oh-ih)/2", "-c:v", "libx264", "-preset", "veryfast", "-b:v", "700k", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(preview_path)], timeout=max(240, int(duration * 20)))
    return {"duration_seconds": output["duration_seconds"], "width": output["width"], "height": output["height"],
            "video_codec": output["video_codec"], "audio_streams": output["audio_streams"], "file_size_bytes": output["file_size_bytes"],
            "thumbnail_size_bytes": thumbnail_path.stat().st_size, "preview_size_bytes": preview_path.stat().st_size, "validated": True}
