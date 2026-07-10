# System Prompt Model - stores the global system prompt
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, Boolean, DateTime, Text

from app.core.database import Base


class SystemPrompt(Base):
    __tablename__ = "system_prompt"

    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(Text, nullable=False)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )