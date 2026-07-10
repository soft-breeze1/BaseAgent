# Chat Models - MySQL persistent storage for conversations
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Text,
    DateTime,
    ForeignKey,
    Float,
    Integer,
    Boolean,
    Index,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


def generate_uuid():
    return str(uuid.uuid4())


class Conversation(Base):
    """会话目录表 - 对应每个「新建对话」"""
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="新的对话")
    kb_id = Column(String(36), ForeignKey("knowledge_bases.id", ondelete="SET NULL"), nullable=True, index=True)
    # RAG parameters — persisted per conversation
    top_k = Column(Integer, nullable=True, default=5)
    score_threshold = Column(Float, nullable=True, default=0.5)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    user = relationship("User", back_populates="conversations")
    messages = relationship(
        "ChatMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )

    __table_args__ = (
        Index("idx_conversations_user_updated", "user_id", "updated_at"),
    )


class ChatMessage(Base):
    """消息记录表 - 会话中的每一条消息"""
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    conversation_id = Column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(20), nullable=False, comment="user / assistant / tool / system")
    content = Column(Text, nullable=False)
    tool_call_id = Column(String(100), nullable=True, comment="For tool role: the tool_call id this result belongs to")
    tool_calls = Column(Text, nullable=True, comment="JSON serialized tool_call list (name, args, id) for assistant messages")
    steps = Column(Text, nullable=True, comment="JSON serialized thinking steps")
    sources = Column(Text, nullable=True, comment="JSON serialized RAG sources")
    route_used = Column(String(50), nullable=True, comment="rag / web_search / llm / tools / skill")
    aborted = Column(Boolean, nullable=True, default=False, comment="用户终止/被中断")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        Index("idx_chat_messages_conv_created", "conversation_id", "created_at"),
    )