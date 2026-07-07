"""Notebook request/response models."""

from datetime import datetime

from pydantic import BaseModel


class NotebookResponse(BaseModel):
    id: str
    title: str
    created_at: datetime | None = None
    sources_count: int = 0
    is_owner: bool = True


class CreateNotebookRequest(BaseModel):
    title: str


class RenameNotebookRequest(BaseModel):
    title: str


class NotebookDescriptionResponse(BaseModel):
    summary: str
    suggested_topics: list[dict[str, str]] = []


class ShareNotebookRequest(BaseModel):
    public: bool = True
    artifact_id: str | None = None


class ShareResponse(BaseModel):
    public: bool
    url: str | None = None
    artifact_id: str | None = None
