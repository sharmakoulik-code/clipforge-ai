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

from app.core.database import get_db
from app.core.rate_limit import enforce_rate_limit
from app.core.security import get_current_user
from app.models.models import Clip, ClipEdit, ClipRenderVersion, ProcessingJob, Project, User
from app.storage.local import storage
from app.video.caption_options import validate_caption_options

router = APIRouter(prefix="/api", tags=["generated clips"])


class ClipEditRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, gt=0)
    caption_style: str | None = None
    caption_options: dict[str, Any] | None = None


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


@router.get("/clips/{clip_id}/video")
def get_clip_video(clip_id: str, download: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    clip, _ = _owned_clip(db, user, clip_id)
    base = "".join(c if c.isalnum() or c in "- _" else "_" for c in clip.title).strip()[:120] or "clip"
    return _file_response(clip, clip.output_video_key, f"{base}.mp4", download, "video/mp4")


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
    if "title" in values:
        title = values["title"].strip()
        if not title:
            raise HTTPException(status_code=422, detail="Clip title cannot be blank.")
        clip.title = title
    if "start_time" in values or "end_time" in values:
        start = values.get("start_time", float(clip.start_seconds))
        end = values.get("end_time", float(clip.end_seconds))
        duration = float((project.source_metadata or {}).get("duration_seconds") or 0)
        if not 5 <= end - start <= 180 or end > duration:
            raise HTTPException(status_code=422, detail="Clip timestamps must be within the source video and span 5–180 seconds.")
        clip.start_seconds, clip.end_seconds = Decimal(str(round(start, 3))), Decimal(str(round(end, 3)))
        clip.status = "needs_render"
    metadata = dict(clip.metadata_json or {})
    if "caption_style" in values:
        if values["caption_style"] not in {"classic", "creator", "minimal", "dynamic", "custom"}:
            raise HTTPException(status_code=422, detail="Choose a supported caption style.")
        metadata["caption_style"] = values["caption_style"]
        clip.status = "needs_render"
    if "caption_options" in values:
        try:
            metadata["caption_options"] = validate_caption_options(values["caption_options"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        clip.status = "needs_render"
    clip.metadata_json = metadata
    revision = (db.query(func.max(ClipEdit.revision)).filter_by(clip_id=clip.id).scalar() or 0) + 1
    db.add(ClipEdit(clip_id=clip.id, user_id=user.id, revision=revision, edit_data=values))
    db.commit()
    db.refresh(clip)
    return _clip_json(clip)


@router.post("/clips/{clip_id}/render", status_code=status.HTTP_202_ACCEPTED)
def rerender_clip(clip_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    enforce_rate_limit(request, scope="clip-render", limit=5)
    clip, _ = _owned_clip(db, user, clip_id)
    if clip.status == "rendering":
        raise HTTPException(status_code=409, detail="This clip is already rendering.")
    clip.status = "rendering"
    db.commit()
    try:
        from app.workers.tasks import render_edited_clip
        render_edited_clip.apply_async(args=[clip.id])
    except Exception as exc:
        clip.status = "needs_render" if clip.output_video_key else "failed"
        db.commit()
        raise HTTPException(status_code=503, detail="The rendering queue is unavailable. Try again later.") from exc
    return {"clip_id": clip.id, "status": clip.status}


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
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for clip in clips:
            path = storage.resolve(clip.output_video_key)
            if not path.is_file():
                raise HTTPException(status_code=404, detail="A selected clip file is missing from storage.")
            safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in clip.title).strip()[:100] or clip.id
            archive.write(path, f"{safe_title}-{clip.id[:8]}.mp4")
    buffer.seek(0)
    def stream_archive():
        try:
            while chunk := buffer.read(1024 * 1024):
                yield chunk
        finally:
            buffer.close()
    return StreamingResponse(stream_archive(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="clips-{project.id[:8]}.zip"', "Cache-Control": "private, no-store"})
