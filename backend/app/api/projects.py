from datetime import datetime, timezone
import json
import os
import logging
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request as UrlRequest, urlopen
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.billing.credits import InsufficientCredits, ensure_free_allocation, estimate_job_credits, refund_job, reserve_for_job
from app.billing.plans import get_user_plan
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import enforce_rate_limit
from app.core.security import get_current_user
from app.models.models import Clip, ClipRenderVersion, CreditWallet, ProcessingCheckpoint, ProcessingJob, ProcessingStage, Project, Subscription, Transcript, User
from app.schemas.api import (
    DashboardResponse, ProcessingJobRead, ProjectDetail, ProjectRead,
    ProjectRenameRequest, UploadResponse, YouTubeImportRequest,
)
from app.services.job_queue import dispatch_job
from app.storage.local import storage
from app.video.caption_options import validate_caption_options
from app.video.metadata import MetadataError, extract_video_metadata

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["projects"])
ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mpeg", ".mpg"}


def _owned_project(db: Session, user: User, project_id: str) -> Project:
    project = db.query(Project).filter_by(id=project_id, owner_id=user.id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _project_read(project: Project) -> ProjectRead:
    return ProjectRead.model_validate(project)


def _job_read(job: ProcessingJob) -> ProcessingJobRead:
    return ProcessingJobRead.model_validate(job)


def _filename(value: str | None) -> str:
    name = Path(value or "video").name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")[:200]
    return name or "video"


def _valid_container_header(suffix: str, header: bytes) -> bool:
    if suffix in {".mp4", ".mov", ".m4v"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if suffix in {".mkv", ".webm"}:
        return header.startswith(bytes.fromhex("1a45dfa3"))
    if suffix == ".avi":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"AVI "
    if suffix in {".mpeg", ".mpg"}:
        return header.startswith(b"\x00\x00\x01\xba") or header.startswith(b"\x00\x00\x01\xb3")
    return False


async def _save_upload(upload: UploadFile, suffix: str, request: Request) -> tuple[str, int]:
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > settings.max_upload_bytes + 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload request is too large")
    key = storage.new_key(suffix)
    path = storage.resolve(key)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    byte_count = 0
    first_chunk = await upload.read(64 * 1024)
    if not _valid_container_header(suffix, first_chunk[:32]):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="File signature does not match a supported video container")
    try:
        file_descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(file_descriptor, "wb") as target:
            chunk = first_chunk
            while chunk:
                byte_count += len(chunk)
                if byte_count > settings.max_upload_bytes:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Video exceeds the configured upload limit")
                target.write(chunk)
                chunk = await upload.read(1024 * 1024)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if byte_count == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is empty")
    return key, byte_count


@router.post("/projects/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_project(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    project_name: str | None = Form(default=None),
    num_clips: int = Form(default=3),
    min_clip_seconds: int = Form(default=20),
    max_clip_seconds: int = Form(default=60),
    ai_provider: str | None = Form(default=None),
    transcription_provider: str | None = Form(default=None),
    caption_style: str = Form(default="creator"),
    caption_options_json: str = Form(default="{}"),
) -> dict:
    enforce_rate_limit(request, scope="video-upload", limit=10)
    ensure_free_allocation(db, user.id)
    plan_key, plan = get_user_plan(db, user.id)
    max_clips = min(settings.processing_max_clips, int(plan.get("max_clips", settings.processing_max_clips)))
    if not 1 <= num_clips <= max_clips:
        raise HTTPException(status_code=422, detail=f"Your {plan['name']} plan allows between 1 and {max_clips} clips per job.")
    active_jobs = db.query(func.count(ProcessingJob.id)).join(Project, Project.id == ProcessingJob.project_id).filter(
        Project.owner_id == user.id, ProcessingJob.status.in_({"queued", "processing"})
    ).scalar() or 0
    if active_jobs >= int(plan.get("max_concurrent_jobs", 1)):
        raise HTTPException(status_code=429, detail=f"Your {plan['name']} plan allows {int(plan.get('max_concurrent_jobs', 1))} concurrent processing job(s).")
    if not 5 <= min_clip_seconds <= max_clip_seconds <= 180:
        raise HTTPException(status_code=422, detail="Clip duration must be between 5 and 180 seconds, with minimum no greater than maximum.")
    if caption_style not in {"classic", "creator", "minimal", "dynamic", "custom"}:
        raise HTTPException(status_code=422, detail="Choose a supported caption style.")
    try:
        parsed_caption_options = json.loads(caption_options_json)
        caption_options = validate_caption_options(parsed_caption_options)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid caption options: {exc}") from exc
    if ai_provider and ai_provider not in {"openai", "openai-compatible", "gemini", "claude"}:
        raise HTTPException(status_code=422, detail="Choose OpenAI, Gemini, or Claude for analysis.")
    if transcription_provider and transcription_provider not in {"openai", "faster-whisper"}:
        raise HTTPException(status_code=422, detail="Choose OpenAI or local faster-whisper for transcription.")
    filename = _filename(file.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_VIDEO_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Unsupported video extension")
    key: str | None = None
    try:
        key, size_bytes = await _save_upload(file, suffix, request)
        storage.commit(key)
        try:
            metadata = extract_video_metadata(storage.resolve(key))
        except MetadataError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        if metadata["duration_seconds"] <= 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The video has no readable duration")
        if metadata["duration_seconds"] > int(plan.get("max_video_seconds", 2**31 - 1)):
            raise HTTPException(status_code=413, detail=f"Your {plan['name']} plan allows videos up to {int(plan['max_video_seconds']) // 60} minutes.")
        metadata["file_size_bytes"] = size_bytes
        project = Project(
            owner_id=user.id,
            name=(project_name or Path(filename).stem or "Untitled video")[:200],
            source_video_key=key,
            source_filename=filename,
            source_metadata=metadata,
            status="processing",
        )
        db.add(project)
        db.flush()
        job = ProcessingJob(
            project_id=project.id,
            requested_by_id=user.id,
            job_type="repurpose",
            status="queued",
            current_stage="source_validation",
            progress=0,
            options={"pipeline_version": "stage-3", "num_clips": num_clips, "min_clip_seconds": min_clip_seconds,
                     "max_clip_seconds": max_clip_seconds, "ai_provider": ai_provider,
                     "transcription_provider": transcription_provider, "caption_style": caption_style,
                     "caption_options": caption_options, "plan_key": plan_key,
                     "render_profile": {"width": min(settings.render_width, int(plan["export_width"])),
                                       "height": min(settings.render_height, int(plan["export_height"])),
                                       "watermark": bool(plan.get("watermark", False))}},
        )
        db.add(job)
        db.flush()
        reserve_amount = estimate_job_credits(metadata["duration_seconds"], num_clips)
        try:
            reserve_for_job(db, job, user.id, reserve_amount)
        except InsufficientCredits as exc:
            raise HTTPException(status_code=402, detail=f"This job is estimated at {reserve_amount} credits. Add credits or choose a smaller job.") from exc
        job.options = {**(job.options or {}), "reserved_credits": reserve_amount}
        db.add_all([
            ProcessingStage(
                job_id=job.id, name="upload_validation", sequence=1, status="completed",
                output_data={"size_bytes": size_bytes, "container": metadata.get("container")},
                completed_at=datetime.now(timezone.utc),
            ),
            ProcessingStage(
                job_id=job.id, name="metadata_extraction", sequence=2, status="completed",
                output_data=metadata, completed_at=datetime.now(timezone.utc),
            ),
            ProcessingStage(job_id=job.id, name="source_validation", sequence=3, status="pending"),
        ])
        db.commit()
        db.refresh(project)
        db.refresh(job)
        queued = dispatch_job(job, db)
        if not queued:
            refund_job(db, job.id, "Processing queue unavailable; reserved credits returned")
            db.commit()
            db.refresh(job)
        return {"project": _project_read(project), "job": _job_read(job), "credits_reserved": reserve_amount if queued else 0}
    except Exception:
        db.rollback()
        if key:
            storage.delete(key)
        raise
    finally:
        await file.close()


def _youtube_video_id(raw_url: str) -> tuple[str, str]:
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if parsed.scheme not in {"https", "http"} or host not in {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "youtube-nocookie.com"}:
        raise HTTPException(status_code=422, detail="Enter a valid YouTube video URL")
    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif parsed.path == "/watch":
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    else:
        pieces = [part for part in parsed.path.split("/") if part]
        if len(pieces) == 2 and pieces[0] in {"shorts", "embed", "live"}:
            video_id = pieces[1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise HTTPException(status_code=422, detail="The YouTube URL does not contain a supported video ID")
    return video_id, f"https://www.youtube.com/watch?v={video_id}"


def _youtube_oembed(url: str) -> dict:
    endpoint = "https://www.youtube.com/oembed?" + urlencode({"url": url, "format": "json"})
    request = UrlRequest(endpoint, headers={"User-Agent": "AI-Video-Repurposer/0.1"})
    try:
        with urlopen(request, timeout=3) as response:
            data = json.loads(response.read(256 * 1024))
            return {key: data[key] for key in ("title", "author_name", "thumbnail_url") if key in data}
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}


@router.post("/projects/youtube", status_code=status.HTTP_201_CREATED)
def import_youtube_reference(
    payload: YouTubeImportRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    enforce_rate_limit(request, scope="youtube-import", limit=10)
    if not payload.user_confirms_rights:
        raise HTTPException(status_code=422, detail="Confirm that you have permission to use this video")
    video_id, canonical_url = _youtube_video_id(payload.url)
    # Only request YouTube's public metadata endpoint; this code does not download or bypass access controls.
    metadata = _youtube_oembed(canonical_url)
    title = payload.project_name or metadata.get("title") or f"YouTube video {video_id}"
    project = Project(
        owner_id=user.id,
        name=title[:200],
        source_url=canonical_url,
        source_metadata={"provider": "youtube", "video_id": video_id, **metadata, "downloaded": False},
        status="awaiting_upload",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return {
        "project": _project_read(project),
        "import_status": "metadata_retrieved" if metadata else "metadata_unavailable",
        "requires_authorized_upload": True,
        "message": "We never download YouTube content. Upload a video file you are authorized to use to begin processing.",
    }


@router.get("/projects", response_model=list[ProjectRead])
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Project]:
    return db.query(Project).filter_by(owner_id=user.id).order_by(Project.updated_at.desc()).all()


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    project = _owned_project(db, user, project_id)
    jobs = db.query(ProcessingJob).filter_by(project_id=project.id).order_by(ProcessingJob.created_at.desc()).all()
    clip_count = db.query(func.count(Clip.id)).filter_by(project_id=project.id).scalar() or 0
    return {**_project_read(project).model_dump(), "jobs": [_job_read(job).model_dump() for job in jobs], "clip_count": clip_count}


@router.patch("/projects/{project_id}", response_model=ProjectRead)
def rename_project(project_id: str, payload: ProjectRenameRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Project:
    project = _owned_project(db, user, project_id)
    project.name = payload.name
    db.commit()
    db.refresh(project)
    return project


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    project = _owned_project(db, user, project_id)
    keys: set[str] = {project.source_video_key} if project.source_video_key else set()
    for transcript in db.query(Transcript).filter_by(project_id=project.id).all():
        artifact = (transcript.metadata_json or {}).get("artifact_key")
        if artifact:
            keys.add(artifact)
    for clip in db.query(Clip).filter_by(project_id=project.id).all():
        if clip.output_video_key:
            keys.add(clip.output_video_key)
        keys.update(value for value in (clip.metadata_json or {}).values() if isinstance(value, str) and value.startswith("artifacts/"))
    processing_jobs = db.query(ProcessingJob).filter_by(project_id=project.id).all()
    for job in processing_jobs:
        if job.status in {"queued", "processing"}:
            job.status = "cancelled"
            job.error_code = "project_deleted"
            job.error_message = "Project deletion cancelled the pending processing task."
            job.completed_at = datetime.now(timezone.utc)
            refund_job(db, job.id, "Project deleted; reserved credits returned")
    checkpoint_rows = db.query(ProcessingCheckpoint).join(ProcessingJob, ProcessingJob.id == ProcessingCheckpoint.job_id).filter(ProcessingJob.project_id == project.id).all()
    for checkpoint in checkpoint_rows:
        if checkpoint.artifact_key:
            keys.add(checkpoint.artifact_key)
        for value in (checkpoint.data or {}).values():
            if isinstance(value, str) and value.startswith("artifacts/"):
                keys.add(value)
    db.delete(project)
    db.commit()
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            logger.exception("Unable to clean up deleted project artifact project_id=%s key=%s", project_id, key)
    try:
        storage.delete_prefix(f"artifacts/{project_id}")
    except Exception:
        logger.exception("Unable to remove derived project artifacts project_id=%s", project_id)


@router.get("/projects/{project_id}/status")
def project_status(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    project = _owned_project(db, user, project_id)
    job = db.query(ProcessingJob).filter_by(project_id=project.id).order_by(ProcessingJob.created_at.desc()).first()
    if job is None:
        return {"project_id": project.id, "status": project.status, "message": "Upload an authorized source file to create a processing job."}
    return _job_read(job).model_dump(mode="json")


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    ensure_free_allocation(db, user.id)
    db.commit()
    projects = db.query(Project).filter_by(owner_id=user.id)
    project_ids = select(Project.id).where(Project.owner_id == user.id)
    total_projects = projects.count()
    total_clips = db.query(func.count(Clip.id)).filter(Clip.project_id.in_(project_ids)).scalar() or 0
    active_statuses = {"queued", "processing", "uploading", "extracting_audio", "transcribing", "analyzing", "detecting_speakers", "cropping", "captioning", "rendering"}
    processing_videos = db.query(func.count(ProcessingJob.id)).filter(
        ProcessingJob.project_id.in_(project_ids), ProcessingJob.status.in_(active_statuses)
    ).scalar() or 0
    wallet = db.query(CreditWallet).filter_by(user_id=user.id).first()
    subscription = db.query(Subscription).filter_by(user_id=user.id).order_by(Subscription.created_at.desc()).first()
    recent_projects = projects.order_by(Project.updated_at.desc()).limit(6).all()
    recent_jobs = db.query(ProcessingJob, Project.name).join(Project, Project.id == ProcessingJob.project_id).filter(
        Project.owner_id == user.id
    ).order_by(ProcessingJob.created_at.desc()).limit(8).all()
    exports = db.query(ClipRenderVersion, Clip.title).join(Clip, Clip.id == ClipRenderVersion.clip_id).join(
        Project, Project.id == Clip.project_id
    ).filter(Project.owner_id == user.id, ClipRenderVersion.status == "completed").order_by(
        ClipRenderVersion.created_at.desc()
    ).limit(6).all()
    return {
        "total_projects": total_projects,
        "total_clips": total_clips,
        "processing_videos": processing_videos,
        "available_credits": wallet.balance if wallet else 0,
        "subscription_status": subscription.status if subscription else None,
        "recent_projects": [_project_read(project) for project in recent_projects],
        "recent_exports": [{"clip_title": title, "output_key": render.output_key, "created_at": render.created_at} for render, title in exports],
        "processing_history": [{"id": job.id, "project_id": job.project_id, "project_name": name, "status": job.status,
                                "current_stage": job.current_stage, "progress": job.progress, "created_at": job.created_at}
                               for job, name in recent_jobs],
    }
