"""Artifact request/response models."""

from datetime import datetime

from pydantic import BaseModel


class ArtifactResponse(BaseModel):
    id: str
    title: str
    kind: str
    status: int
    status_str: str
    created_at: datetime | None = None
    url: str | None = None
    is_completed: bool = False


class GenerationStatusResponse(BaseModel):
    task_id: str
    status: str
    url: str | None = None
    error: str | None = None
    is_complete: bool = False
    is_failed: bool = False


class GenerateAudioRequest(BaseModel):
    instructions: str | None = None
    source_ids: list[str] | None = None


class GenerateVideoRequest(BaseModel):
    instructions: str | None = None
    source_ids: list[str] | None = None


class GenerateReportRequest(BaseModel):
    prompt: str | None = None
    source_ids: list[str] | None = None
