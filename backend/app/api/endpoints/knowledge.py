# Knowledge Base Endpoints
import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.config import get_settings
from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument, KBStatus
from app.models.model_config import ModelConfig
from app.models.user import User
from app.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    KnowledgeBaseOut,
    KnowledgeDocumentOut,
)
from app.services.auth_deps import get_current_user
from app.services.rag_service import rag_service

settings = get_settings()
router = APIRouter(prefix="/knowledge", tags=["Knowledge Base"])

ALLOWED_EXTENSIONS = {
    "pdf", "docx", "doc", "csv", "md", "txt",
    "ppt", "pptx", "xlsx", "xls",
    "html", "htm", "xml", "json", "epub",
}


# ---- Knowledge Base CRUD ----

@router.get("/", response_model=list[KnowledgeBaseOut])
async def list_kbs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(KnowledgeBase)
        .where(KnowledgeBase.user_id == current_user.id)
        .order_by(KnowledgeBase.created_at.desc())
    )
    kbs = result.scalars().all()
    # 动态计算 document_count，确保始终与知识库中的文档实际数量一致
    for kb in kbs:
        count_result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb.id)
        )
        kb.document_count = len(count_result.scalars().all())
    return kbs


@router.post("/", response_model=KnowledgeBaseOut, status_code=status.HTTP_201_CREATED)
async def create_kb(
    data: KnowledgeBaseCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    collection_name = f"kb_{current_user.id}_{uuid.uuid4().hex[:12]}"
    kb = KnowledgeBase(
        user_id=current_user.id,
        name=data.name,
        description=data.description,
        embedding_model=data.embedding_model,
        chunk_size=data.chunk_size,
        chunk_overlap=data.chunk_overlap,
        collection_name=collection_name,
    )
    db.add(kb)
    await db.flush()
    return kb


@router.get("/{kb_id}", response_model=KnowledgeBaseOut)
async def get_kb(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id, KnowledgeBase.user_id == current_user.id
        )
    )
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    # 动态计算 document_count，确保始终与知识库中的文档实际数量一致
    count_result = await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb.id)
    )
    kb.document_count = len(count_result.scalars().all())
    return kb


@router.put("/{kb_id}", response_model=KnowledgeBaseOut)
async def update_kb(
    kb_id: str,
    data: KnowledgeBaseUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id, KnowledgeBase.user_id == current_user.id
        )
    )
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(kb, key, value)
    await db.flush()
    return kb


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id, KnowledgeBase.user_id == current_user.id
        )
    )
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    rag_service.delete_collection(kb.collection_name)
    await db.delete(kb)


# ---- Document Upload & Management ----

@router.get("/{kb_id}/documents", response_model=list[KnowledgeDocumentOut])
async def list_documents(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_user_kb(kb_id, current_user.id, db)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    result = await db.execute(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.kb_id == kb_id)
        .order_by(KnowledgeDocument.created_at.desc())
    )
    return result.scalars().all()


from app.tasks.document_tasks import process_document_task as process_document_celery_task


@router.post("/{kb_id}/upload", response_model=KnowledgeDocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    kb_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_user_kb(kb_id, current_user.id, db)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type .{ext} not supported")

    # Save file to disk
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    file_path = os.path.join(settings.UPLOAD_DIR, safe_name)
    content = await file.read()
    file_size = len(content)
    if file_size > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large")

    with open(file_path, "wb") as f:
        f.write(content)

    doc = KnowledgeDocument(
        kb_id=kb_id,
        filename=file.filename,
        file_type=ext,
        file_size=file_size,
        file_path=file_path,
        status=KBStatus.PROCESSING,
    )
    db.add(doc)
    await db.flush()

    # 更新知识库文档计数（基于MySQL文档表）
    _count_result = await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id)
    )
    kb.document_count = len(_count_result.scalars().all())
    await db.flush()

    # Look up the activated embedding model config — always use the user's active embedding model
    embedding_provider = None
    embedding_api_key = None
    active_embedding_model = kb.embedding_model
    model_result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.user_id == current_user.id,
            ModelConfig.model_type == "embedding",
            ModelConfig.is_active == True,
        ).order_by(ModelConfig.is_default.desc(), ModelConfig.updated_at.desc())
    )
    active_model_config = model_result.scalars().first()
    if active_model_config:
        active_embedding_model = active_model_config.model_name
        embedding_provider = active_model_config.provider
        embedding_api_key = active_model_config.api_key or None
        # Sync kb.embedding_model to the active model if different
        if kb.embedding_model != active_embedding_model:
            kb.embedding_model = active_embedding_model
            await db.flush()

    # Dispatch document processing to Celery worker (async)
    # This prevents the HTTP request from blocking or timing out
    # during long embedding operations (especially on first run with model download)
    process_document_celery_task.delay(
        file_path=file_path,
        file_type=ext,
        collection_name=kb.collection_name,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
        embedding_model=active_embedding_model,
        embedding_api_key=embedding_api_key,
        embedding_provider=embedding_provider,
        doc_id=doc.id,
        kb_id=kb.id,
    )

    await db.flush()
    return doc


@router.post("/{kb_id}/documents/{doc_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
async def reprocess_document(
    kb_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reprocess a failed document by re-adding it to the Celery queue."""
    kb = await _get_user_kb(kb_id, current_user.id, db)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    result = await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id, KnowledgeDocument.kb_id == kb_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.file_path or not os.path.exists(doc.file_path):
        raise HTTPException(status_code=400, detail="Document file not found on disk")

    # Reset status to PROCESSING and clear error
    doc.status = KBStatus.PROCESSING
    doc.error_message = None
    doc.chunk_count = 0
    await db.flush()

    # Look up the model config
    embedding_provider = None
    embedding_api_key = None
    if kb.embedding_model:
        model_result = await db.execute(
            select(ModelConfig).where(
                ModelConfig.user_id == current_user.id,
                ModelConfig.model_name == kb.embedding_model,
                ModelConfig.model_type == "embedding",
                ModelConfig.is_active == True,
            )
        )
        model_config = model_result.scalar_one_or_none()
        if model_config:
            embedding_provider = model_config.provider
            embedding_api_key = model_config.api_key or None

    # Re-dispatch to Celery
    process_document_celery_task.delay(
        file_path=doc.file_path,
        file_type=doc.file_type,
        collection_name=kb.collection_name,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
        embedding_model=kb.embedding_model,
        embedding_api_key=embedding_api_key,
        embedding_provider=embedding_provider,
        doc_id=doc.id,
        kb_id=kb.id,
    )

    return {"message": "Document queued for reprocessing"}


@router.delete("/{kb_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    kb_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_user_kb(kb_id, current_user.id, db)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    result = await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id, KnowledgeDocument.kb_id == kb_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.file_path and os.path.exists(doc.file_path):
        os.remove(doc.file_path)
    await db.delete(doc)
    # 删除后基于MySQL文档表更新计数
    _count_result = await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id)
    )
    kb.document_count = len(_count_result.scalars().all())
    await db.flush()


async def _get_user_kb(kb_id: str, user_id: str, db: AsyncSession) -> KnowledgeBase | None:
    result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.user_id == user_id)
    )
    return result.scalar_one_or_none()