# Model Config Model - stores user's LLM provider configurations
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.user import generate_uuid


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(50), nullable=False)  # openai, deepseek, zhipu, qwen, ollama, etc.
    model_name = Column(String(100), nullable=False)  # gpt-4o, deepseek-chat, glm-4, qwen2.5:7b, etc.
    model_type = Column(String(20), nullable=False, default="llm", server_default="llm")  # llm or embedding
    api_key = Column(String(500), nullable=False)
    api_base = Column(String(500), nullable=True)  # optional custom endpoint
    extra_config = Column(Text, nullable=True)
    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="model_configs")