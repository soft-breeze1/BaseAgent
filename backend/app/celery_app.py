# Celery Application
# Provides async task queue for long-running operations like:
# - Document embedding & indexing (offloaded from FastAPI request cycle)
# - Batch knowledge base operations
# - Skill script execution

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "baseagent",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.document_tasks",
        "app.tasks.chat_tasks",
    ],
)

# Optional configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes max per task
    task_soft_time_limit=25 * 60,  # 25 minutes soft limit
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
)