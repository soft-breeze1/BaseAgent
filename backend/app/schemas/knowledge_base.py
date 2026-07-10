# Knowledge Base Schemas
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    embedding_model: str = "all-MiniLM-L6-v2"
    chunk_size: int = Field(512, ge=128, le=2048)
    chunk_overlap: int = Field(50, ge=0, le=500)


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    chunk_size: Optional[int] = Field(None, ge=128, le=2048)
    chunk_overlap: Optional[int] = Field(None, ge=0, le=500)


class KnowledgeBaseOut(BaseModel):
    id: str
    user_id: str
    name: str
    description: Optional[str]
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    collection_name: str
    document_count: int
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class KnowledgeDocumentOut(BaseModel):
    id: str
    kb_id: str
    filename: str
    file_type: str
    file_size: int
    chunk_count: int
    status: str
    error_message: Optional[str]
    progress: int = 0
    progress_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
