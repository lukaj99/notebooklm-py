"""Chat endpoints."""

from fastapi import APIRouter, Depends
from notebooklm import NotebookLMClient

from ..deps import get_client
from ..models.chat import (
    AskRequest,
    AskResponse,
    ChatHistoryResponse,
    ChatReferenceResponse,
    ConfigureChatRequest,
)

router = APIRouter(prefix="/notebooks/{notebook_id}/chat", tags=["chat"])


@router.post("/ask", response_model=AskResponse)
async def ask_question(
    notebook_id: str,
    body: AskRequest,
    client: NotebookLMClient = Depends(get_client),
):
    result = await client.chat.ask(
        notebook_id,
        body.question,
        source_ids=body.source_ids,
        conversation_id=body.conversation_id,
    )
    return AskResponse(
        answer=result.answer,
        conversation_id=result.conversation_id,
        turn_number=result.turn_number,
        is_follow_up=result.is_follow_up,
        references=[
            ChatReferenceResponse(
                source_id=ref.source_id,
                citation_number=ref.citation_number,
                cited_text=ref.cited_text,
                start_char=ref.start_char,
                end_char=ref.end_char,
            )
            for ref in result.references
        ],
    )


@router.get("/history", response_model=ChatHistoryResponse)
async def get_history(
    notebook_id: str,
    limit: int = 100,
    conversation_id: str | None = None,
    client: NotebookLMClient = Depends(get_client),
):
    conv_id = conversation_id or await client.chat.get_conversation_id(notebook_id)
    pairs = await client.chat.get_history(notebook_id, limit=limit, conversation_id=conversation_id)
    return ChatHistoryResponse(
        conversation_id=conv_id,
        turns=[{"question": q, "answer": a} for q, a in pairs],
    )


@router.post("/configure", status_code=204)
async def configure_chat(
    notebook_id: str,
    body: ConfigureChatRequest,
    client: NotebookLMClient = Depends(get_client),
):
    from notebooklm import ChatGoal, ChatResponseLength

    goal = ChatGoal(body.goal) if body.goal else None
    response_length = ChatResponseLength(body.response_length) if body.response_length else None

    await client.chat.configure(
        notebook_id,
        goal=goal,
        response_length=response_length,
        custom_prompt=body.custom_prompt,
    )
