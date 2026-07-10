# Knowledge Base Model - metadata for uploaded document sets
import enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.user import generate_uuid


class KBStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    collection_name = Column(String(200), unique=True, nullable=False, index=True)
    embedding_model = Column(String(100), default="BAAI/bge-small-zh-v1.5")
    chunk_size = Column(Integer, default=512)
    chunk_overlap = Column(Integer, default=50)
    document_count = Column(Integer, default=0)
    total_chunks = Column(Integer, default=0)
    status = Column(String(20), default=KBStatus.READY.value)
    is_active = Column(String(1), default="1")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = relationship("User", back_populates="knowledge_bases")
    documents = relationship("KnowledgeDocument", back_populates="knowledge_base", cascade="all, delete-orphan")


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    kb_id = Column(String(36), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(500), nullable=False)
    file_type = Column(String(20), nullable=False)
    file_size = Column(Integer, default=0)
    file_path = Column(String(1000), nullable=True)
    chunk_count = Column(Integer, default=0)
    status = Column(String(20), default=KBStatus.PENDING.value)
    error_message = Column(Text, nullable=True)
    progress = Column(Integer, default=0)
    progress_message = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    knowledge_base = relationship("KnowledgeBase", back_populates="documents")