"""Source request/response models."""

from datetime import datetime

from pydantic import BaseModel


class SourceResponse(BaseModel):
    id: str
    title: str | None = None
    url: str | None = None
    kind: str = "unknown"
    created_at: datetime | None = None
    status: int = 2
    is_ready: bool = True
    is_processing: bool = False


class AddUrlRequest(BaseModel):
    url: str
    wait: bool = False
    wait_timeout: float = 120.0


class AddTextRequest(BaseModel):
    title: str
    content: str
    wait: bool = False
    wait_timeout: float = 120.0


class AddDriveRequest(BaseModel):
    file_id: str
    title: str
    mime_type: str = "application/vnd.google-apps.document"
    wait: bool = False
    wait_timeout: float = 120.0


class SourceGuideResponse(BaseModel):
    summary: str = ""
    keywords: list[str] = []


class SourceFulltextResponse(BaseModel):
    source_id: str
    title: str
    content: str
    kind: str = "unknown"
    url: str | None = None
    char_count: int = 0
