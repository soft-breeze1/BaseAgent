# Skill Tasks - Celery Beat scheduled execution for Skills
import json
import logging
from datetime import datetime, timezone

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def execute_skill_scheduled(self, skill_name: str, query_template: str = ""):
    """Celery task: execute a skill by name with optional query.

    Called by Celery Beat scheduler for cron-triggered Skill execution.
    This runs in a separate worker process and calls SkillManager directly.
    """
    import asyncio

    from app.services.skill_manager import skill_manager

    try:
        # Resolve skill by name
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            skill = loop.run_until_complete(
                skill_manager.get_skill_by_name(skill_name)
            )
        finally:
            loop.close()

        if not skill:
            logger.error(f"Scheduled skill '{skill_name}' not found")
            return {"status": "failed", "error": f"Skill '{skill_name}' not found"}

        # Build query from template, default to skill name
        query = query_template or f"定时执行: {skill['display_name']}"

        # Execute (async call in sync Celery task)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                skill_manager.execute_skill(
                    skill_id=skill["id"],
                    query=query,
                    variables={"trigger": "scheduled", "scheduled_at": datetime.now(timezone.utc).isoformat()},
                )
            )
        finally:
            loop.close()

        logger.info(
            f"Scheduled skill '{skill_name}' executed: {result.status}"
        )
        return {
            "status": result.status,
            "skill_name": skill_name,
            "session_id": result.session_id,
            "output_length": len(result.final_output) if result.final_output else 0,
        }

    except Exception as e:
        logger.error(f"Scheduled skill '{skill_name}' execution failed: {e}", exc_info=True)
        try:
            self.retry(exc=e, countdown=60)
        except Exception:
            return {"status": "failed", "error": str(e)}