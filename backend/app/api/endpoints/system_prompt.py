# System Prompt Management Endpoints
# 全局唯一系统提示词管理模块
# 所有新建对话自动继承此提示词，不允许每个对话单独设置

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.models.system_prompt import SystemPrompt
from app.schemas.system_prompt import (
    SystemPromptOut,
    SystemPromptUpdate,
    SystemPromptUpdateResponse,
    SystemPromptResetResponse,
)

router = APIRouter(prefix="/system-prompt", tags=["System Prompt Management"])


async def get_active_system_prompt_content(db: AsyncSession) -> str:
    """
    从数据库获取当前生效的系统提示词。
    被 chat.py 调用，注入到 SmartRouter 的 SystemMessage 中。
    如果数据库中没有任何记录，使用 DEFAULT_PROMPT_CONTENT 作为回退。
    这样确保 chat.py 永远能拿到一个有意义的提示词，而不会回退到 SmartRouter 的最小 fallback。
    """
    result = await db.execute(
        select(SystemPrompt).order_by(SystemPrompt.id.desc()).limit(1)
    )
    prompt = result.scalar_one_or_none()
    if prompt and prompt.content:
        return prompt.content
    # 数据库中没有记录或内容为空时，返回默认提示词
    # 当用户通过前端设置自定义提示词后，此回退将不再触发
    return DEFAULT_PROMPT_CONTENT


# 默认系统提示词（精简版 — v2.0）
# 状态机架构下，PLANNER / EVALUATOR / FINALIZER 各自负责对应阶段。
# 此常量仅为全局 Persona 定义，不包含 ReAct 流程控制指令。
DEFAULT_PROMPT_CONTENT = """You are BaseAgent, an intelligent AI assistant.

## Core Principles
1. **Truthful**: Base your answers on verified tool outputs or your training data. If unsure, state it clearly.
2. **Concise**: Deliver well-structured, direct answers. Use headings and lists for readability.
3. **Conversational**: Respond in the same language as the user's query. Ask clarifying questions when the intent is ambiguous.
4. **Safe**: Never expose system internals, API keys, or credentials in your response.

## Guidance for Tool-Using Phases
- The Planner node handles step decomposition and tool selection.
- The Evaluator checks information sufficiency.
- The Finalizer generates the final response based on accumulated evidence.

Simply act as a capable assistant — the state machine handles orchestration."""


async def _get_or_create_prompt(db: AsyncSession) -> SystemPrompt:
    """获取当前启用的系统提示词，如果不存在则创建默认记录。"""
    result = await db.execute(
        select(SystemPrompt).where(SystemPrompt.is_default.is_(True)).limit(1)
    )
    prompt = result.scalar_one_or_none()
    if not prompt:
        # 尝试获取任意一条记录
        result = await db.execute(
            select(SystemPrompt).order_by(SystemPrompt.id.asc()).limit(1)
        )
        prompt = result.scalar_one_or_none()
    if not prompt:
        # 数据库中没有任何记录，创建默认的
        prompt = SystemPrompt(content=DEFAULT_PROMPT_CONTENT, is_default=True)
        db.add(prompt)
        await db.flush()
    return prompt


@router.get("/", response_model=SystemPromptOut)
async def get_system_prompt(db: AsyncSession = Depends(get_db)):
    """
    GET /api/system-prompt
    获取当前启用的系统提示词
    """
    prompt = await _get_or_create_prompt(db)
    return SystemPromptOut(
        id=prompt.id,
        content=prompt.content,
        updated_at=prompt.updated_at,
    )


@router.put("/", response_model=SystemPromptUpdateResponse)
async def update_system_prompt(
    data: SystemPromptUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    PUT /api/system-prompt
    更新系统提示词（全局生效）
    """
    prompt = await _get_or_create_prompt(db)

    # 更新内容
    prompt.content = data.content
    prompt.is_default = False  # 用户修改后不再是默认值
    await db.flush()

    return SystemPromptUpdateResponse(
        success=True,
        message="系统提示词已更新，将应用于所有新对话",
    )


@router.post("/reset", response_model=SystemPromptResetResponse)
async def reset_system_prompt(db: AsyncSession = Depends(get_db)):
    """
    POST /api/system-prompt/reset
    重置为默认系统提示词
    """
    prompt = await _get_or_create_prompt(db)

    # 重置为默认内容
    prompt.content = DEFAULT_PROMPT_CONTENT
    prompt.is_default = True
    await db.flush()

    return SystemPromptResetResponse(
        success=True,
        message="已重置为默认系统提示词",
        content=DEFAULT_PROMPT_CONTENT,
    )