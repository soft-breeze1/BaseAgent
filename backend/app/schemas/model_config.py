# Model Config Schemas
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional


class ModelConfigCreate(BaseModel):
    provider: str = Field(..., min_length=1, max_length=50)
    model_name: str = Field(..., min_length=1, max_length=100)
    model_type: str = Field(default="llm", pattern="^(llm|embedding)$")
    api_key: Optional[str] = Field(None, min_length=0, max_length=500)
    api_base: Optional[str] = Field(None, max_length=500)
    is_default: bool = False
    extra_config: Optional[str] = None


class ModelConfigUpdate(BaseModel):
    provider: Optional[str] = Field(None, min_length=1, max_length=50)
    model_name: Optional[str] = Field(None, min_length=1, max_length=100)
    model_type: Optional[str] = Field(None, pattern="^(llm|embedding)$")
    api_key: Optional[str] = None
    api_base: Optional[str] = Field(None, max_length=500)
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None
    extra_config: Optional[str] = None


class ModelConfigOut(BaseModel):
    id: str
    user_id: str
    provider: str
    model_name: str
    model_type: str
    api_key: str
    api_base: Optional[str]
    is_default: bool
    is_active: bool
    extra_config: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True