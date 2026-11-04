from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from typing_extensions import Annotated


DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
StrongPassword = Annotated[str, StringConstraints(min_length=12, max_length=128)]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: EmailStr
    display_name: str | None
    is_admin: bool
    created_at: datetime


class RegisterRequest(BaseModel):
    email: EmailStr
    password: StrongPassword
    display_name: DisplayName


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: Annotated[str, StringConstraints(min_length=24, max_length=256)]
    new_password: StrongPassword


class ProfileUpdate(BaseModel):
    display_name: DisplayName


class YouTubeImportRequest(BaseModel):
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2048)]
    user_confirms_rights: bool
    project_name: ProjectName | None = None


class ProjectRenameRequest(BaseModel):
    name: ProjectName


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str | None
    status: str
    source_filename: str | None
    source_url: str | None
    source_metadata: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


JobState = Literal[
    "queued", "processing", "uploading", "extracting_audio", "transcribing", "analyzing",
    "detecting_speakers", "cropping", "captioning", "rendering", "completed", "failed",
]


class ProcessingJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    status: JobState
    current_stage: str | None
    progress: int
    attempt: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectRead):
    jobs: list[ProcessingJobRead] = Field(default_factory=list)
    clip_count: int = 0


class UploadResponse(BaseModel):
    project: ProjectRead
    job: ProcessingJobRead


class DashboardResponse(BaseModel):
    total_projects: int
    total_clips: int
    processing_videos: int
    available_credits: int
    subscription_status: str | None
    recent_projects: list[ProjectRead]
    recent_exports: list[dict[str, Any]]
    processing_history: list[dict[str, Any]]
