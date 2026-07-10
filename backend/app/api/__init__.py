# API Router Package
from fastapi import APIRouter
from app.api.endpoints import auth, models, knowledge, chat, tools, mcp, skills, system_prompt, user

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(models.router)
api_router.include_router(knowledge.router)
api_router.include_router(chat.router)
api_router.include_router(tools.router)
api_router.include_router(mcp.router)
api_router.include_router(skills.router)
api_router.include_router(system_prompt.router)
api_router.include_router(user.router)
