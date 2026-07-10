# Chat Celery Tasks - MySQL persistence for messages & conversations (sync)
import json
from datetime import datetime, timezone

from celery import Task
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.celery_app import celery_app
from app.core.config import settings
from app.models.chat import Conversation, ChatMessage

CHAT_KEY_PREFIX = ""


def _conv_key(user_id: str, conv_id: str) -> str:
    return f"{CHAT_KEY_PREFIX}:{user_id}:conv:{conv_id}"


class MySQLTask(Task):
    """Base celery task that provides sync DB session."""
    _engine = None
    _SessionLocal = None

    @property
    def engine(self):
        if self._engine is None:
            database_url = settings.DATABASE_URL
            self._engine = create_engine(
                database_url,
                echo=False,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                pool_recycle=3600,
            )
        return self._engine

    @property
    def session_factory(self):
        if self._SessionLocal is None:
            self._SessionLocal = sessionmaker(
                bind=self.engine,
                class_=Session,
                expire_on_commit=False,
            )
        return self._SessionLocal


@celery_app.task(
    base=MySQLTask,
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    acks_late=True,
    autoretry_for=(Exception,),
)
def persist_message(
    self,
    conv_id: str,
    user_id: str,
    role: str,
    content: str,
    sources: list | None = None,
    route_used: str | None = None,
    is_new_conversation: bool = False,
    title: str = "新的对话",
    kb_id: str | None = None,
    top_k: int = 5,
    score_threshold: float = 0.5,
    created_at: str | None = None,
    steps: list | None = None,
):
    """
    Persist a single message to MySQL synchronously.

    If is_new_conversation, creates the Conversation row with RAG params first.
    """
    db: Session = self.session_factory()

    try:
        # Ensure conversation row exists
        conv = db.execute(
            select(Conversation).where(
                Conversation.id == conv_id,
                Conversation.user_id == user_id,
            )
        ).scalar_one_or_none()

        if not conv:
            # Create new conversation with RAG params
            conv = Conversation(
                id=conv_id,
                user_id=user_id,
                title=title,
                kb_id=kb_id,
                top_k=top_k,
                score_threshold=score_threshold,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(conv)
        else:
            # Update RAG params and optional title/kb_id
            if title != "新的对话" and conv.title == "新的对话":
                conv.title = title
            if kb_id:
                conv.kb_id = kb_id
            # Always update RAG params to latest
            conv.top_k = top_k
            conv.score_threshold = score_threshold
            conv.updated_at = datetime.now(timezone.utc)

        # Append message — convert sources to JSON string
        sources_json = None
        if sources:
            serializable_sources = []
            for s in sources:
                if hasattr(s, 'model_dump'):
                    serializable_sources.append(s.model_dump())
                elif hasattr(s, 'dict'):
                    serializable_sources.append(s.dict())
                elif isinstance(s, dict):
                    serializable_sources.append(s)
                else:
                    serializable_sources.append(str(s))
            sources_json = json.dumps(serializable_sources, ensure_ascii=False)

        msg_created_at = datetime.fromisoformat(created_at) if created_at else datetime.now(timezone.utc)

        steps_json = None
        if steps:
            steps_json = json.dumps(steps, ensure_ascii=False)

        msg = ChatMessage(
            conversation_id=conv_id,
            role=role,
            content=content,
            steps=steps_json,
            sources=sources_json,
            route_used=route_used,
            created_at=msg_created_at,
        )
        db.add(msg)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()