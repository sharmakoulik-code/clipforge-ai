from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

from app.core.config import settings
from app.video.ffmpeg import probe_media


def _smooth(values: list[float], radius: int = 2) -> list[float]:
    return [median(values[max(0, i - radius):min(len(values), i + radius + 1)]) for i in range(len(values))]


def _crop_geometry(width: int, height: int, center_x: float, center_y: float, out_w: int, out_h: int) -> tuple[int, int, int, int]:
    target_ratio = out_w / out_h
    if width / height >= target_ratio:
        crop_h, crop_w = height, int(round(height * target_ratio))
    else:
        crop_w, crop_h = width, int(round(width / target_ratio))
    x = int(round(center_x - crop_w / 2))
    y = int(round(center_y - crop_h * 0.38))
    return crop_w, crop_h, max(0, min(width - crop_w, x)), max(0, min(height - crop_h, y))


def track_speakers_and_crops(source: Path, start: float, end: float, output_width: int | None = None,
                             output_height: int | None = None, sample_interval: float | None = None) -> dict[str, Any]:
    info = probe_media(source)
    width, height = info["width"], info["height"]
    output_width = output_width or settings.render_width
    output_height = output_height or settings.render_height
    interval = max(0.25, sample_interval or settings.video_sample_interval_seconds)
    try:
        import cv2
    except ImportError:
        cw, ch, x, y = _crop_geometry(width, height, width / 2, height / 2, output_width, output_height)
        return {"method": "center_fallback", "active_speaker_confidence": 0.0, "faces": [], "samples": [{"time": start, "x": x, "y": y, "width": cw, "height": ch}, {"time": end, "x": x, "y": y, "width": cw, "height": ch}], "source_width": width, "source_height": height}

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError("OpenCV could not open the source video for speaker tracking.")
    mp_detector = None
    detector_name = "opencv_haar"
    try:
        import mediapipe as mp
        mp_detector = mp.solutions.face_detection.FaceDetection(model_selection=0, min_detection_confidence=0.6)
        detector_name = "mediapipe"
    except (ImportError, AttributeError, RuntimeError):
        mp_detector = None
    cascade_path = str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    detector = cv2.CascadeClassifier(cascade_path) if mp_detector is None else None
    if mp_detector is None and (detector is None or detector.empty()):
        cap.release()
        raise RuntimeError("OpenCV Haar face-detection model could not be loaded.")
    tracks: dict[int, list[dict[str, float]]] = defaultdict(list)
    next_id = 1
    previous_centers: dict[int, tuple[float, float]] = {}
    previous_lower: dict[int, Any] = {}
    motion_sum: dict[int, float] = defaultdict(float)
    motion_n: dict[int, int] = defaultdict(int)
    duration = max(0.0, end - start)
    samples_n = min(360, max(2, int(duration / interval) + 1))
    times = [min(end, start + i * interval) for i in range(samples_n)]
    if times[-1] < end:
        times.append(end)
    for timestamp in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if mp_detector is not None:
            result = mp_detector.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            boxes = []
            for detection in (result.detections or []) if result else []:
                box = detection.location_data.relative_bounding_box
                fx = max(0, int(box.xmin * width)); fy = max(0, int(box.ymin * height))
                fw = min(width - fx, int(box.width * width)); fh = min(height - fy, int(box.height * height))
                if fw > 0 and fh > 0:
                    boxes.append((fx, fy, fw, fh))
        else:
            boxes = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(max(24, width // 40), max(24, height // 40)))
        available = set(previous_centers)
        for (fx, fy, fw, fh) in boxes:
            center = (float(fx + fw / 2), float(fy + fh / 2))
            nearest = min(available, key=lambda tid: ((previous_centers[tid][0] - center[0]) ** 2 + (previous_centers[tid][1] - center[1]) ** 2) ** .5, default=None)
            if nearest is not None and ((previous_centers[nearest][0] - center[0]) ** 2 + (previous_centers[nearest][1] - center[1]) ** 2) ** .5 < max(fw, fh) * 1.5:
                track_id = nearest
                available.remove(nearest)
            else:
                track_id = next_id
                next_id += 1
            lower = gray[int(fy + fh * .48):int(fy + fh * .93), int(fx + fw * .16):int(fx + fw * .84)]
            if lower.size:
                lower = cv2.resize(lower, (32, 20))
                old = previous_lower.get(track_id)
                if old is not None:
                    motion = float(cv2.absdiff(old, lower).mean()) / 255.0
                    motion_sum[track_id] += motion
                    motion_n[track_id] += 1
                previous_lower[track_id] = lower
            previous_centers[track_id] = center
            observation = {"time": float(timestamp), "x": center[0], "y": center[1], "width": float(fw), "height": float(fh)}
            tracks[track_id].append(observation)
            current.append((track_id, observation))
    cap.release()
    if mp_detector is not None:
        mp_detector.close()

    active_id = None
    confidence = 0.0
    motion_scores = {tid: motion_sum[tid] / max(1, motion_n[tid]) for tid in tracks}
    if len(tracks) == 1 and next(iter(tracks), None) is not None:
        active_id = next(iter(tracks))
        confidence = 0.55
    elif len(motion_scores) > 1:
        ranked = sorted(motion_scores.items(), key=lambda x: x[1], reverse=True)
        if ranked[0][1] > 0.035 and ranked[0][1] > ranked[1][1] * 1.35:
            active_id = ranked[0][0]
            confidence = min(0.7, (ranked[0][1] - ranked[1][1]) / max(ranked[0][1], .001))

    selected = tracks.get(active_id, []) if active_id is not None else []
    sample_rows = []
    target_points = []
    # No confident active-speaker evidence: center crop, not largest-face guessing.
    if selected:
        selected = sorted(selected, key=lambda item: item["time"])
        for obs in selected:
            target_points.append(_crop_geometry(width, height, obs["x"], obs["y"], output_width, output_height))
    else:
        target_points = [_crop_geometry(width, height, width / 2, height / 2, output_width, output_height)] * max(1, len(times))
    if target_points:
        xs = _smooth([float(row[2]) for row in target_points])
        ys = _smooth([float(row[3]) for row in target_points])
        source_times = [row["time"] for row in selected] if selected else [start, end]
        if len(source_times) != len(xs):
            source_times = [start + (end - start) * i / max(1, len(xs) - 1) for i in range(len(xs))]
        for i, (x, y) in enumerate(zip(xs, ys)):
            crop_w, crop_h = target_points[i][0], target_points[i][1]
            sample_rows.append({"time": round(source_times[i], 3), "x": int(x), "y": int(y), "width": crop_w, "height": crop_h})
    if not sample_rows:
        cw, ch, x, y = _crop_geometry(width, height, width / 2, height / 2, output_width, output_height)
        sample_rows = [{"time": start, "x": x, "y": y, "width": cw, "height": ch}, {"time": end, "x": x, "y": y, "width": cw, "height": ch}]
    else:
        if sample_rows[0]["time"] > start:
            sample_rows.insert(0, {**sample_rows[0], "time": start})
        if sample_rows[-1]["time"] < end:
            sample_rows.append({**sample_rows[-1], "time": end})
    faces = [{"speaker_id": f"speaker_{tid}", "observations": rows, "motion_score": round(motion_scores.get(tid, 0), 5)} for tid, rows in tracks.items()]
    return {"method": f"{detector_name}_face_track" if selected else "center_fallback", "detector": detector_name, "active_speaker_id": f"speaker_{active_id}" if active_id else None,
            "active_speaker_confidence": round(confidence, 3), "faces": faces, "samples": sample_rows, "source_width": width, "source_height": height}


def manual_crop_plan(source: Path, start: float, end: float, center_x: float, center_y: float,
                     zoom: float, output_width: int | None = None, output_height: int | None = None) -> dict[str, Any]:
    info = probe_media(source)
    width, height = int(info["width"]), int(info["height"])
    out_w, out_h = output_width or settings.render_width, output_height or settings.render_height
    ratio = out_w / out_h
    if width / height >= ratio:
        base_h, base_w = height, int(round(height * ratio))
    else:
        base_w, base_h = width, int(round(width / ratio))
    zoom = max(1.0, min(3.0, float(zoom)))
    crop_w = max(2, int(round(base_w / zoom)))
    crop_h = max(2, int(round(crop_w / ratio)))
    if crop_h > height:
        crop_h = height
        crop_w = max(2, int(round(crop_h * ratio)))
    cx = max(0.0, min(1.0, float(center_x))) * width
    cy = max(0.0, min(1.0, float(center_y))) * height
    x = max(0, min(width - crop_w, int(round(cx - crop_w / 2))))
    y = max(0, min(height - crop_h, int(round(cy - crop_h / 2))))
    return {"method": "manual_crop", "active_speaker_confidence": 0.0, "faces": [],
            "source_width": width, "source_height": height,
            "samples": [{"time": float(start), "x": x, "y": y, "width": crop_w, "height": crop_h},
                        {"time": float(end), "x": x, "y": y, "width": crop_w, "height": crop_h}]}
