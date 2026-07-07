"""Chat request/response models."""

from pydantic import BaseModel


class AskRequest(BaseModel):
    question: str
    source_ids: list[str] | None = None
    conversation_id: str | None = None


class ChatReferenceResponse(BaseModel):
    source_id: str
    citation_number: int | None = None
    cited_text: str | None = None
    start_char: int | None = None
    end_char: int | None = None


class AskResponse(BaseModel):
    answer: str
    conversation_id: str
    turn_number: int
    is_follow_up: bool
    references: list[ChatReferenceResponse] = []


class ChatHistoryResponse(BaseModel):
    conversation_id: str | None = None
    turns: list[dict[str, str]] = []


class ConfigureChatRequest(BaseModel):
    goal: str | None = None
    response_length: str | None = None
    custom_prompt: str | None = None
