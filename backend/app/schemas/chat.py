# Chat & RAG Schemas
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    kb_id: Optional[str] = None
    top_k: int = Field(5, ge=1, le=20)
    score_threshold: float = Field(0.5, ge=0.0, le=1.0)
    conversation_id: Optional[str] = None


class RAGSource(BaseModel):
    document_id: str
    filename: str
    content: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: List[RAGSource] = []
    route_used: str  # "rag", "web_search", "llm", "tools", "skill"
    conversation_id: str
    created_at: datetime
    steps: Optional[List[str]] = None  # Fix #6: 非流式路径的中间步骤记录


class AbortMessageRequest(BaseModel):
    """Save an aborted/interrupted assistant message."""
    conversation_id: str
    content: str = ""
    steps: Optional[list[str]] = None
    sources: Optional[list[dict]] = None


class ConversationRename(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)


class ConversationKbUpdate(BaseModel):
    kb_id: Optional[str] = None


class ConversationOut(BaseModel):
    id: str
    title: str
    kb_id: Optional[str] = None
    # RAG parameters — persisted per conversation (managed internally by smart_router)
    top_k: int = 5
    score_threshold: float = 0.5
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
