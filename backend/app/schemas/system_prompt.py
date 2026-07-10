# System Prompt Schemas
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator


class SystemPromptOut(BaseModel):
    """Response schema for GET /api/system-prompt"""
    id: int
    content: str
    updated_at: Optional[datetime] = None


class SystemPromptUpdate(BaseModel):
    """Request schema for PUT /api/system-prompt"""
    content: str

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("提示词内容不能为空")
        return v.strip()


class SystemPromptResetResponse(BaseModel):
    """Response schema for POST /api/system-prompt/reset"""
    success: bool
    message: str
    content: str


class SystemPromptUpdateResponse(BaseModel):
    """Response schema for PUT /api/system-prompt"""
    success: bool
    message: str