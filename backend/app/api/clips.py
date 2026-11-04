from __future__ import annotations

import tempfile
import zipfile
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.billing.credits import InsufficientCredits, ensure_free_allocation, estimate_job_credits, reserve_for_job
from app.billing.plans import get_user_plan
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import enforce_rate_limit
from app.core.security import get_current_user
from app.models.models import Clip, ClipEdit, ClipRenderVersion, ProcessingJob, Project, Transcript, User
from app.storage.local import storage
from app.video.caption_options import validate_caption_options

router = APIRouter(prefix="/api", tags=["generated clips"])


class ClipEditRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    hashtags: list[str] | None = Field(default=None, max_length=30)
    export_filename: str | None = Field(default=None, max_length=160)
    start_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, gt=0)
    caption_style: str | None = None
    caption_options: dict[str, Any] | None = None
    caption_segments: list[dict[str, Any]] | None = Field(default=None, max_length=3000)
    hide_captions: bool | None = None
    crop_settings: dict[str, Any] | None = None


def _owned_project(db: Session, user: User, project_id: str) -> Project:
    project = db.query(Project).filter_by(id=project_id, owner_id=user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _owned_clip(db: Session, user: User, clip_id: str) -> tuple[Clip, Project]:
    row = db.query(Clip, Project).join(Project, Project.id == Clip.project_id).filter(Clip.id == clip_id, Project.owner_id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Clip not found")
    return row


def _clip_json(clip: Clip) -> dict[str, Any]:
    metadata = clip.metadata_json or {}
    duration = float(clip.end_seconds - clip.start_seconds)
    return {
        "id": clip.id, "project_id": clip.project_id, "title": clip.title,
        "viral_score": float(clip.score or 0), "score_components": metadata.get("score_components", {}),
        "duration_seconds": duration, "start_time": float(clip.start_seconds), "end_time": float(clip.end_seconds),
        "category": metadata.get("category", "Other"), "hook": metadata.get("hook", ""),
        "reason": metadata.get("reason", ""), "transcript": metadata.get("transcript", ""), "status": clip.status,
        "caption_style": metadata.get("caption_style", "creator"), "caption_options": metadata.get("caption_options", {}),
        "description": metadata.get("description", ""), "hashtags": metadata.get("hashtags", []),
        "export_filename": metadata.get("export_filename", ""),
        "original_start_time": float(metadata.get("original_start_time", clip.start_seconds)),
        "original_end_time": float(metadata.get("original_end_time", clip.end_seconds)),
        "crop_settings": metadata.get("crop_settings", {"mode": "auto", "zoom": 1.0, "center_x": 0.5, "center_y": 0.5}),
        "hide_captions": bool(metadata.get("hide_captions", False)),
        "caption_segments": metadata.get("caption_segments"),
        "thumbnail_url": f"/api/clips/{clip.id}/thumbnail" if metadata.get("thumbnail_key") else None,
        "preview_url": f"/api/clips/{clip.id}/preview" if metadata.get("preview_key") else None,
        "video_url": f"/api/clips/{clip.id}/video" if clip.output_video_key else None,
        "created_at": clip.created_at.isoformat(), "updated_at": clip.updated_at.isoformat(),
    }


@router.get("/projects/{project_id}/clips")
def list_clips(project_id: str, sort: str = Query(default="score", pattern="^(score|newest|duration|category)$"), category: str | None = None,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    project = _owned_project(db, user, project_id)
    query = db.query(Clip).filter_by(project_id=project.id)
    if sort == "newest":
        query = query.order_by(Clip.created_at.desc())
    elif sort == "duration":
        query = query.order_by((Clip.end_seconds - Clip.start_seconds).desc())
    else:
        query = query.order_by(Clip.score.desc().nullslast(), Clip.created_at.desc())
    clips = query.all()
    categories = sorted({str((clip.metadata_json or {}).get("category", "Other")) for clip in clips}, key=str.lower)
    if sort == "category":
        clips.sort(key=lambda clip: str((clip.metadata_json or {}).get("category", "Other")).lower())
    if category and category != "all":
        clips = [clip for clip in clips if str((clip.metadata_json or {}).get("category", "Other")).lower() == category.lower()]
    return {"project_id": project.id, "categories": categories, "clips": [_clip_json(clip) for clip in clips]}


def _validated_crop_settings(value: dict[str, Any]) -> dict[str, Any]:
    mode = value.get("mode", "auto")
    if mode not in {"auto", "manual"}:
        raise ValueError("Crop mode must be auto or manual.")
    try:
        center_x = float(value.get("center_x", 0.5))
        center_y = float(value.get("center_y", 0.5))
        zoom = float(value.get("zoom", 1.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Crop position and zoom must be numeric.") from exc
    if not 0 <= center_x <= 1 or not 0 <= center_y <= 1 or not 1 <= zoom <= 3:
        raise ValueError("Crop center must be between 0 and 1 and zoom between 1 and 3.")
    return {"mode": mode, "center_x": center_x, "center_y": center_y, "zoom": zoom,
            "auto_speaker_center": mode == "auto"}


def _safe_export_filename(value: str) -> str:
    name = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = "".join(char if char.isalnum() or char in "-_ ." else "_" for char in name).strip(" .")[:140]
    if not name:
        raise ValueError("Export filename is empty after sanitization.")
    return name if name.lower().endswith(".mp4") else f"{name}.mp4"


def _validate_caption_segments(segments: list[dict[str, Any]], clip_start: float, clip_end: float) -> list[dict[str, Any]]:
    validated = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Each caption entry must be an object.")
        try:
            start, end = float(segment["start"]), float(segment["end"])
            text = str(segment.get("text", "")).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Caption entries need numeric start/end times.") from exc
        if not clip_start <= start < end <= clip_end or len(text) > 1000:
            raise ValueError("Caption timing or text is outside the selected clip.")
        words = segment.get("words")
        clean_words = None
        if words is not None:
            if not isinstance(words, list) or len(words) > 300:
                raise ValueError("Caption word timing is invalid.")
            clean_words = []
            for word in words:
                if not isinstance(word, dict):
                    raise ValueError("Caption words must be objects.")
                try:
                    w_start, w_end = float(word["start"]), float(word["end"])
                    w_text = str(word.get("word", word.get("text", ""))).strip()
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("Caption words need text and numeric timing.") from exc
                if not clip_start <= w_start < w_end <= clip_end or len(w_text) > 100:
                    raise ValueError("Caption word timing or text is outside the selected clip.")
                clean_words.append({"word": w_text, "start": w_start, "end": w_end})
        validated.append({"start": start, "end": end, "text": text, "words": clean_words})
    return validated


def _file_response(clip: Clip, key: str | None, filename: str, download: bool = False, media_type: str = "application/octet-stream") -> FileResponse:
    if not key or not storage.exists(key):
        raise HTTPException(status_code=404, detail="This clip artifact is not available yet.")
    path = storage.resolve(key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This clip artifact is not available yet.")
    response = FileResponse(path, media_type=media_type, filename=filename if download else None)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/projects/{project_id}/source")
def get_project_source(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    project = _owned_project(db, user, project_id)
    key = project.source_video_key
    if not key or not storage.exists(key):
        raise HTTPException(status_code=404, detail="Project source is not available.")
    filename = (project.source_filename or "source.mp4").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    media_type = "video/webm" if filename.lower().endswith(".webm") else "video/mp4"
    response = FileResponse(storage.resolve(key), media_type=media_type)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/clips/{clip_id}/captions")
def get_clip_captions(clip_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    clip, _ = _owned_clip(db, user, clip_id)
    metadata = clip.metadata_json or {}
    if metadata.get("caption_segments") is not None:
        return {"segments": metadata["caption_segments"]}
    transcript = db.get(Transcript, clip.transcript_id) if clip.transcript_id else None
    rows = []
    for segment in (transcript.segments if transcript and transcript.segments else []):
        if float(segment.get("end", 0)) <= float(clip.start_seconds) or float(segment.get("start", 0)) >= float(clip.end_seconds):
            continue
        rows.append(segment)
    return {"segments": rows}


@router.get("/clips/{clip_id}/video")
def get_clip_video(clip_id: str, download: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    clip, _ = _owned_clip(db, user, clip_id)
    metadata = clip.metadata_json or {}
    base = metadata.get("export_filename") or "".join(c if c.isalnum() or c in "- _" else "_" for c in clip.title).strip()[:120] or "clip.mp4"
    if download and not base.lower().endswith(".mp4"):
        base += ".mp4"
    return _file_response(clip, clip.output_video_key, base, download, "video/mp4")


@router.get("/clips/{clip_id}/preview")
def get_clip_preview(clip_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    clip, _ = _owned_clip(db, user, clip_id)
    return _file_response(clip, (clip.metadata_json or {}).get("preview_key"), f"{clip.id}-preview.mp4", False, "video/mp4")


@router.get("/clips/{clip_id}/thumbnail")
def get_clip_thumbnail(clip_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    clip, _ = _owned_clip(db, user, clip_id)
    return _file_response(clip, (clip.metadata_json or {}).get("thumbnail_key"), f"{clip.id}.jpg", False, "image/jpeg")


@router.patch("/clips/{clip_id}")
def update_clip(clip_id: str, payload: ClipEditRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    clip, project = _owned_clip(db, user, clip_id)
    values = payload.model_dump(exclude_unset=True)
    duration = float((project.source_metadata or {}).get("duration_seconds") or 0)
    start = float(values.get("start_time", clip.start_seconds))
    end = float(values.get("end_time", clip.end_seconds))
    if "start_time" in values or "end_time" in values:
        if not 5 <= end - start <= 180 or start < 0 or end > duration:
            raise HTTPException(status_code=422, detail="Clip timestamps must be within the source video and span 5–180 seconds.")
    metadata = dict(clip.metadata_json or {})
    try:
        if "title" in values:
            title = values["title"].strip()
            if not title:
                raise ValueError("Clip title cannot be blank.")
            clip.title = title
        if "description" in values:
            metadata["description"] = values["description"].strip()
        if "hashtags" in values:
            tags = values["hashtags"] or []
            if len(tags) > 30 or any(not isinstance(tag, str) or len(tag) > 50 for tag in tags):
                raise ValueError("Use up to 30 hashtags, each no longer than 50 characters.")
            metadata["hashtags"] = ["#" + "".join(ch for ch in tag.strip().lstrip("#") if ch.isalnum() or ch == "_")
                                     for tag in tags if tag.strip()]
        if "export_filename" in values:
            metadata["export_filename"] = _safe_export_filename(values["export_filename"])
        if "caption_style" in values:
            if values["caption_style"] not in {"classic", "creator", "minimal", "dynamic", "custom"}:
                raise ValueError("Choose a supported caption style.")
            metadata["caption_style"] = values["caption_style"]
        if "caption_options" in values:
            metadata["caption_options"] = validate_caption_options(values["caption_options"])
        if "caption_segments" in values:
            metadata["caption_segments"] = _validate_caption_segments(values["caption_segments"], start, end)
        if "hide_captions" in values:
            metadata["hide_captions"] = bool(values["hide_captions"])
        if "crop_settings" in values:
            metadata["crop_settings"] = _validated_crop_settings(values["crop_settings"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "start_time" in values or "end_time" in values:
        clip.start_seconds, clip.end_seconds = Decimal(str(round(start, 3))), Decimal(str(round(end, 3)))
    if any(key in values for key in {"start_time", "end_time", "caption_style", "caption_options", "caption_segments", "hide_captions", "crop_settings"}):
        clip.status = "needs_render"
    clip.metadata_json = metadata
    revision = (db.query(func.max(ClipEdit.revision)).filter_by(clip_id=clip.id).scalar() or 0) + 1
    db.add(ClipEdit(clip_id=clip.id, user_id=user.id, revision=revision, edit_data=values))
    db.commit()
    db.refresh(clip)
    return _clip_json(clip)


@router.post("/clips/{clip_id}/restore-ai-boundaries")
def restore_ai_boundaries(clip_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    clip, _ = _owned_clip(db, user, clip_id)
    metadata = dict(clip.metadata_json or {})
    clip.start_seconds = Decimal(str(metadata.get("original_start_time", clip.start_seconds)))
    clip.end_seconds = Decimal(str(metadata.get("original_end_time", clip.end_seconds)))
    clip.status = "needs_render"
    db.add(ClipEdit(clip_id=clip.id, user_id=user.id,
                    revision=(db.query(func.max(ClipEdit.revision)).filter_by(clip_id=clip.id).scalar() or 0) + 1,
                    edit_data={"restore_ai_boundaries": True}))
    db.commit()
    db.refresh(clip)
    return _clip_json(clip)


@router.post("/clips/{clip_id}/restore-ai-crop")
def restore_ai_crop(clip_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    clip, _ = _owned_clip(db, user, clip_id)
    metadata = dict(clip.metadata_json or {})
    metadata["crop_settings"] = {"mode": "auto", "center_x": 0.5, "center_y": 0.5, "zoom": 1.0, "auto_speaker_center": True}
    clip.metadata_json = metadata
    clip.status = "needs_render"
    db.add(ClipEdit(clip_id=clip.id, user_id=user.id,
                    revision=(db.query(func.max(ClipEdit.revision)).filter_by(clip_id=clip.id).scalar() or 0) + 1,
                    edit_data={"restore_ai_crop": True}))
    db.commit()
    db.refresh(clip)
    return _clip_json(clip)


@router.get("/clips/{clip_id}/suggestions")
def clip_suggestions(clip_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    clip, _ = _owned_clip(db, user, clip_id)
    metadata = clip.metadata_json or {}
    category = str(metadata.get("category", "video"))
    title = clip.title.strip()
    hook = str(metadata.get("hook", "")).strip()
    transcript = str(metadata.get("transcript", "")).strip()
    tags = ["#" + "".join(ch for ch in category.title().replace(" ", "") if ch.isalnum()), "#shorts", "#video"]
    return {"source": "AI highlight analysis", "title_suggestions": list(dict.fromkeys([title, hook[:100] or title])),
            "description_suggestion": str(metadata.get("reason") or transcript[:300]), "hashtag_suggestions": tags}


@router.get("/clips/{clip_id}/versions")
def list_render_versions(clip_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    clip, _ = _owned_clip(db, user, clip_id)
    versions = db.query(ClipRenderVersion).filter_by(clip_id=clip.id).order_by(ClipRenderVersion.version.desc()).all()
    return {"clip_id": clip.id, "active_video_key": clip.output_video_key,
            "versions": [{"version": row.version, "status": row.status, "created_at": row.created_at,
                          "video_url": f"/api/clips/{clip.id}/versions/{row.version}/video",
                          "settings": row.render_settings or {}, "validation": row.validation_data or {}} for row in versions]}


@router.get("/clips/{clip_id}/versions/{version}/video")
def get_render_version_video(clip_id: str, version: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    clip, _ = _owned_clip(db, user, clip_id)
    row = db.query(ClipRenderVersion).filter_by(clip_id=clip.id, version=version, status="completed").first()
    if not row:
        raise HTTPException(status_code=404, detail="Render version not found.")
    return _file_response(clip, row.output_key, f"{clip.id}-v{version}.mp4", media_type="video/mp4")


@router.post("/clips/{clip_id}/versions/{version}/restore")
def restore_render_version(clip_id: str, version: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    enforce_rate_limit(request, scope="clip-version-restore", limit=10)
    clip, _ = _owned_clip(db, user, clip_id)
    row = db.query(ClipRenderVersion).filter_by(clip_id=clip.id, version=version, status="completed").first()
    if not row or not storage.exists(row.output_key):
        raise HTTPException(status_code=404, detail="A validated render version is not available.")
    clip.output_video_key = row.output_key
    clip.status = "completed"
    metadata = dict(clip.metadata_json or {})
    settings_row = row.render_settings or {}
    for key in ("thumbnail_key", "preview_key", "caption_key", "crop_key"):
        if settings_row.get(key):
            metadata[key] = settings_row[key]
    if settings_row.get("start_time") is not None:
        clip.start_seconds = Decimal(str(settings_row["start_time"]))
        metadata["rendered_start_time"] = settings_row["start_time"]
    if settings_row.get("end_time") is not None:
        clip.end_seconds = Decimal(str(settings_row["end_time"]))
        metadata["rendered_end_time"] = settings_row["end_time"]
    if "title" in settings_row:
        clip.title = str(settings_row["title"])
    for key in ("crop_settings", "caption_style", "caption_options", "caption_segments", "description", "hashtags", "export_filename", "hide_captions", "watermark"):
        if key in settings_row:
            metadata[key] = settings_row[key]
    metadata["render_validation"] = row.validation_data or {}
    metadata["active_render_version"] = version
    clip.metadata_json = metadata
    db.commit()
    db.refresh(clip)
    return {"clip": _clip_json(clip), "restored_version": version}


@router.post("/clips/{clip_id}/render", status_code=status.HTTP_202_ACCEPTED)
def rerender_clip(clip_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    enforce_rate_limit(request, scope="clip-render", limit=5)
    clip, project = _owned_clip(db, user, clip_id)
    if clip.status == "rendering":
        raise HTTPException(status_code=409, detail="This clip is already rendering.")
    _, plan = get_user_plan(db, user.id)
    active_jobs = db.query(ProcessingJob).join(Project, Project.id == ProcessingJob.project_id).filter(
        Project.owner_id == user.id, ProcessingJob.status.in_(["queued", "processing"])
    ).count()
    if active_jobs >= int(plan.get("max_concurrent_jobs", 1)):
        raise HTTPException(status_code=429, detail="Your plan's concurrent processing limit has been reached.")
    ensure_free_allocation(db, user.id)
    job = ProcessingJob(project_id=project.id, requested_by_id=user.id, job_type="clip_render", status="queued",
                        current_stage="clip_render", options={"clip_id": clip.id})
    db.add(job)
    db.flush()
    reserve_amount = max(1, estimate_job_credits(0, 1))
    job.options = {"clip_id": clip.id, "reserved_credits": reserve_amount}
    try:
        reserve_for_job(db, job, user.id, reserve_amount)
    except InsufficientCredits as exc:
        db.rollback()
        raise HTTPException(status_code=402, detail=f"Rendering this edit requires {reserve_amount} credits. Add credits to continue.") from exc
    clip.status = "rendering"
    db.commit()
    try:
        from app.workers.tasks import render_edited_clip
        render_edited_clip.apply_async(args=[clip.id, job.id])
    except Exception as exc:
        from app.billing.credits import refund_job
        clip.status = "needs_render" if clip.output_video_key else "failed"
        job.status, job.error_code, job.error_message = "failed", "queue_unavailable", "The rendering queue is unavailable."
        job.completed_at = datetime.now(timezone.utc)
        refund_job(db, job.id, "Rendering queue unavailable; reserved credits returned")
        db.commit()
        raise HTTPException(status_code=503, detail="The rendering queue is unavailable. Try again later.") from exc
    return {"clip_id": clip.id, "job_id": job.id, "status": "rendering", "reserved_credits": reserve_amount}


@router.post("/projects/{project_id}/clips/download")
def download_clips(project_id: str, clip_ids: list[str], request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> StreamingResponse:
    enforce_rate_limit(request, scope="clip-download", limit=10)
    project = _owned_project(db, user, project_id)
    if not 1 <= len(clip_ids) <= 20:
        raise HTTPException(status_code=422, detail="Select between 1 and 20 clips.")
    clips = db.query(Clip).filter(Clip.project_id == project.id, Clip.id.in_(set(clip_ids))).all()
    if len(clips) != len(set(clip_ids)) or any(not clip.output_video_key for clip in clips):
        raise HTTPException(status_code=404, detail="One or more selected clips are unavailable.")
    buffer = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024, mode="w+b")
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for clip in clips:
            path = storage.resolve(clip.output_video_key)
            if not path.is_file():
                raise HTTPException(status_code=404, detail="A selected clip file is missing from storage.")
            metadata = clip.metadata_json or {}
            stored_name = str(metadata.get("export_filename") or f"{clip.title}.mp4")
            safe_title = "".join(c if c.isalnum() or c in "-_ ." else "_" for c in stored_name).strip(" .")[:120] or f"{clip.id}.mp4"
            if not safe_title.lower().endswith(".mp4"):
                safe_title += ".mp4"
            if safe_title in used_names:
                stem, suffix = safe_title[:-4], safe_title[-4:]
                safe_title = f"{stem}-{clip.id[:8]}{suffix}"
            used_names.add(safe_title)
            archive.write(path, safe_title)
    buffer.seek(0)
    def stream_archive():
        try:
            while chunk := buffer.read(1024 * 1024):
                yield chunk
        finally:
            buffer.close()
    return StreamingResponse(stream_archive(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="clips-{project.id[:8]}.zip"', "Cache-Control": "private, no-store"})
