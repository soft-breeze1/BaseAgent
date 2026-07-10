# Chat Endpoint - Smart routing with RAG, Tools, and LLM
# MySQL + Redis hybrid architecture:
#   - Redis as hot cache (1h TTL)
#   - MySQL as permanent storage
#   - Celery tasks for async MySQL writes
# v5.0 — Full ReAct loop support:
#   - All message types (user, assistant, tool) properly persisted
#   - tool_call_id and tool_calls fields for complete ReAct context recovery
#   - Unified Observation persistence for ALL tool types
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from app.core.database import get_db
from app.core import redis as redis_module
from app.models.model_config import ModelConfig
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.models.chat import Conversation, ChatMessage
from app.schemas.chat import ChatRequest, ChatResponse, RAGSource, ConversationOut, ConversationRename, ConversationKbUpdate, AbortMessageRequest
from langchain_core.messages import HumanMessage, SystemMessage

from app.services.auth_deps import get_current_user
from app.services.smart_router import smart_router
from app.services.tool_manager import tool_manager as tm
from app.services.llm_service import LLMFactory, ModelDescriptor
from app.services.memory_service import memory_service
from app.tasks.chat_tasks import persist_message
from app.api.endpoints.system_prompt import get_active_system_prompt_content
from app.core.mcp.discovery import discover_mcp_tools, clear_session_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])

# Redis key constants
CHAT_KEY_PREFIX = "chat"
REDIS_TTL = 60 * 60  # 1 hour hot cache TTL for messages
CONV_TTL = 60 * 60 * 24 * 30  # 30 days TTL for conversation metadata


def _conv_key(user_id: str, conv_id: str) -> str:
    """Build Redis key for a conversation metadata hash."""
    return f"{CHAT_KEY_PREFIX}:{user_id}:conv:{conv_id}"


def _conv_msgs_key(user_id: str, conv_id: str) -> str:
    """Build Redis key for conversation messages list."""
    return f"{CHAT_KEY_PREFIX}:{user_id}:conv:{conv_id}:msgs"


def _conv_list_key(user_id: str) -> str:
    """Build Redis key for user's conversation id set."""
    return f"{CHAT_KEY_PREFIX}:{user_id}:convs"


@router.post("/send", response_model=ChatResponse)
async def chat_send(
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    background_tasks: BackgroundTasks = None,
):
    """Non-streaming chat with smart routing. Supports full ReAct loop.
    
    v7.0: Added memory context injection and async memory storage.
    """
    if background_tasks is None:
        background_tasks = BackgroundTasks()
    
    model_desc = await _load_model_descriptor(current_user.id, db)
    kb_collection, kb_embedding_model, kb_embedding_provider = (await _get_kb_info(req.kb_id, current_user.id, db)) if req.kb_id else (None, None, None)
    
    # 加载对话历史作为上下文
    conv_history = None
    if req.conversation_id:
        conv_history = await _load_conversation_messages(req.conversation_id, current_user.id, db)

    # 从数据库加载系统提示词（如果存在）
    system_prompt = await get_active_system_prompt_content(db)

    # ── v1.1: 前置记忆注入（coupled with memory_context for state machine） ──
    # 在路由前自动检索用户长期记忆，注入到 system_prompt 的 Context 区域
    injected_memories = None
    try:
        user_id_str = str(current_user.id)
        injected_memories = await memory_service.retrieve_memories(
            user_id=user_id_str,
            query=req.message,
            top_k=5,
        )
        if injected_memories:
            memory_context_str = memory_service.format_memories_for_context(injected_memories)
            if system_prompt:
                system_prompt = system_prompt + memory_context_str
            else:
                from app.services.smart_router import FALLBACK_AGENT_PROMPT
                system_prompt = FALLBACK_AGENT_PROMPT + memory_context_str
            logger.info(f"[Memory] Injected {len(injected_memories)} memories into system prompt")
    except Exception as e:
        logger.warning(f"[Memory] Memory retrieval failed (non-blocking): {e}")
        injected_memories = None

    # ── Docker 环境提示注入 ──
    try:
        from app.utils.docker_env import get_docker_prompt_suffix
        docker_suffix = get_docker_prompt_suffix()
        if docker_suffix:
            if system_prompt:
                system_prompt = system_prompt + docker_suffix
            else:
                from app.services.smart_router import FALLBACK_AGENT_PROMPT
                system_prompt = FALLBACK_AGENT_PROMPT + docker_suffix
            logger.info("[Docker] Injected Docker environment prompt into system prompt")
    except Exception as e:
        logger.warning(f"[Docker] Prompt injection failed (non-blocking): {e}")

    # ── v7.0 fix: 非流式接口 MCP 工具发现 ──
    # 与流式接口（route_stream）保持完全一致：提前发现 MCP 工具
    mcp_tools = None
    try:
        user_id_str = str(current_user.id)
        mcp_tools = await discover_mcp_tools(
            user_id=user_id_str,
            db=db,
            session_id=req.conversation_id or str(uuid.uuid4()),
            force_refresh=False,
        )
        if mcp_tools:
            logger.info(f"[Chat] Non-streaming: discovered {len(mcp_tools)} MCP tools for user {user_id_str}")
    except Exception as e:
        logger.warning(f"[Chat] MCP discovery failed in non-streaming (non-blocking): {e}")
        mcp_tools = None

    result = await smart_router.route(
        query=req.message,
        model_descriptor=model_desc,
        kb_collection_name=kb_collection,
        top_k=req.top_k,
        score_threshold=req.score_threshold,
        embedding_model=kb_embedding_model,
        embedding_provider=kb_embedding_provider,
        conversation_history=conv_history,
        system_prompt=system_prompt,
        mcp_tools=mcp_tools,  # v7.0 fix: 传入 MCP 工具列表
        user_id=str(current_user.id),
        db_session=db,
    )

    conv_id = req.conversation_id or str(uuid.uuid4())
    user_id = str(current_user.id)
    is_new = req.conversation_id is None

    # 如果 ReAct 循环中有工具调用，也持久化 tool_calls 和 tool_messages
    await _save_conversation(
        user_id, conv_id, req.message, result.answer,
        db=db,
        sources=result.sources, route_used=result.route_used,
        is_new=is_new, kb_id=req.kb_id,
        top_k=req.top_k, score_threshold=req.score_threshold,
        tool_calls_detail=result.tool_calls_detail,
        assistant_tool_calls=result.assistant_tool_calls,
        steps=result.steps,
    )

    # ── v1.1: 异步记忆沉淀（asyncio.create_task 替代 BackgroundTasks） ──
    # BackgroundTasks 不支持 async def 函数，使用 create_task 在后台安全执行
    try:
        extract_llm = LLMFactory.create(model_desc)
    except Exception:
        extract_llm = None

    async def _schedule_memory_extraction():
        try:
            await memory_service.extract_and_store_memories(
                user_id=str(current_user.id),
                conversation_history=(conv_history or []),
                query=req.message,
                response=result.answer,
                llm=extract_llm,  # v1.1: 传递 LLM 用于轻量级记忆提取
            )
        except Exception as e:
            logger.warning(f"[Memory] Async memory extraction failed (non-blocking): {e}")

    try:
        asyncio.create_task(_schedule_memory_extraction())
    except Exception as e:
        logger.warning(f"[Memory] Failed to schedule memory storage task: {e}")

    return ChatResponse(
        answer=result.answer,
        sources=[RAGSource(**s) for s in result.sources],
        route_used=result.route_used,
        conversation_id=conv_id,
        created_at=datetime.now(timezone.utc),
        steps=result.steps,  # Fix #6: 包含中间步骤记录
    )


async def _load_conversation_messages(conv_id: str, user_id: str, db: AsyncSession) -> Optional[list[dict]]:
    """
    从数据库加载对话历史，返回 dict 列表格式。
    v1.1: 在返回前自动应用短期记忆滑动窗口，防止长对话 Context Window 溢出。
    这些消息将作为 ReAct 循环的上下文传入 smart_router。
    """
    from app.models.chat import ChatMessage, Conversation
    
    # 验证对话属于该用户
    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.id == conv_id,
            Conversation.user_id == user_id,
        )
    )
    conv = conv_result.scalar_one_or_none()
    if not conv:
        return None
    
    # 加载所有消息
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conv_id)
        .order_by(ChatMessage.created_at)
    )
    msgs = result.scalars().all()
    
    if not msgs:
        return None
    
    conversation = []
    for msg in msgs:
        entry = {
            "role": msg.role,
            "content": msg.content,
        }
        
        # 还原 tool_calls（assistant 角色的工具调用请求）
        if msg.tool_calls:
            try:
                entry["tool_calls"] = json.loads(msg.tool_calls)
            except (json.JSONDecodeError, TypeError):
                pass
        
        # 还原 tool_call_id（tool 角色的关联 ID）
        if msg.tool_call_id:
            entry["tool_call_id"] = msg.tool_call_id
        
        conversation.append(entry)
    
    # v1.1: 应用滑动窗口截断，保留最近的 SHORT_TERM_WINDOW 轮 + 系统提示 + 工具链
    conversation = memory_service.apply_short_term_window_dicts(conversation)
    
    return conversation


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    v8.0: 真流式 SSE 端点，100% 兼容 OpenAI API 格式。
    
    支持:
      - OpenAI chat.completion.chunk 格式
      - 15s 心跳包（防 Nginx 断开）
      - 事件类型: step, meta, error
      - 标准 [DONE] 结束标记
    
    可通过替换 OpenAI 接口地址直接使用:
      原: https://api.openai.com/v1/chat/completions
      新: http://localhost:8000/api/v1/chat/stream
    """
    model_desc = await _load_model_descriptor(current_user.id, db)
    kb_collection, kb_embedding_model, kb_embedding_provider = (await _get_kb_info(req.kb_id, current_user.id, db)) if req.kb_id else (None, None, None)
    user_id = str(current_user.id)
    
    async def event_generator():
        nonlocal user_id
        conv_id = req.conversation_id or str(uuid.uuid4())
        
        # v8.0: SSE 格式化工具
        from app.services.sse_utils import (
            make_chunk, make_done, make_step_event, 
            make_meta_event, make_error_chunk, make_heartbeat,
        )
        
        is_new = req.conversation_id is None
        full_answer_parts = []
        sources = []
        route_used = "llm"
        steps = []
        has_sent_first_token = False
        last_heartbeat_ts = 0.0  # 上次心跳时间（秒级时间戳）
        
        # Save user message to DB immediately so it's not lost on page refresh
        now = datetime.now(timezone.utc)
        try:
            from app.core.database import get_db as _get_db
            async for db_session in _get_db():
                conv_key = _conv_key(user_id, conv_id)
                msgs_key = _conv_msgs_key(user_id, conv_id)
                list_key = _conv_list_key(user_id)
                now_ts = now.timestamp()
                auto_title = "新的对话"

                from app.models.chat import Conversation, ChatMessage
                result = await db_session.execute(
                    select(Conversation).where(
                        Conversation.id == conv_id,
                        Conversation.user_id == user_id,
                    )
                )
                conv = result.scalar_one_or_none()
                if not conv:
                    if is_new:
                        from zoneinfo import ZoneInfo
                        tz_shanghai = ZoneInfo("Asia/Shanghai")
                        now_sh = datetime.now(tz_shanghai)
                        auto_title = f"对话_{now_sh.year}_{now_sh.month}_{now_sh.day}_{now_sh.hour:02d}_{now_sh.minute:02d}_{now_sh.second:02d}"
                    conv = Conversation(
                        id=conv_id,
                        user_id=user_id,
                        title=auto_title,
                        kb_id=req.kb_id,
                        top_k=req.top_k,
                        score_threshold=req.score_threshold,
                        created_at=now,
                        updated_at=now,
                    )
                    db_session.add(conv)
                else:
                    if conv.title == "新的对话" and auto_title:
                        conv.title = auto_title
                    if req.kb_id:
                        conv.kb_id = req.kb_id
                    conv.top_k = req.top_k
                    conv.score_threshold = req.score_threshold
                    conv.updated_at = now

                user_msg_obj = ChatMessage(
                    conversation_id=conv_id,
                    role="user",
                    content=req.message,
                    sources=None,
                    created_at=datetime.fromtimestamp(now_ts - 1.0, tz=timezone.utc),
                )
                db_session.add(user_msg_obj)
                await db_session.commit()

                rc = redis_module.redis_client
                if rc is not None:
                    key_exists = await rc.exists(conv_key)
                    if not key_exists:
                        mapping = {
                            "user_id": str(user_id),
                            "title": auto_title,
                            "created_at": str(now_ts),
                            "updated_at": str(now_ts),
                        }
                        if req.kb_id:
                            mapping["kb_id"] = req.kb_id
                        mapping["top_k"] = str(req.top_k)
                        mapping["score_threshold"] = str(req.score_threshold)
                        await rc.hset(conv_key, mapping=mapping)
                    else:
                        updates = {"updated_at": str(now_ts)}
                        if req.kb_id:
                            updates["kb_id"] = req.kb_id
                        updates["top_k"] = str(req.top_k)
                        updates["score_threshold"] = str(req.score_threshold)
                        await rc.hset(conv_key, mapping=updates)
                    await rc.expire(conv_key, CONV_TTL)
                    user_entry = json.dumps({"role": "user", "content": req.message, "timestamp": now_ts}, ensure_ascii=False)
                    await rc.rpush(msgs_key, user_entry)
                    await rc.expire(msgs_key, REDIS_TTL)
                    await rc.sadd(list_key, conv_id)
                    await rc.expire(list_key, CONV_TTL)
                break
        except Exception as e:
            logger.warning(f"Failed to save user message immediately: {e}")

        # ── v22.4: Layer 0 极速网关（Fast Path）— 双阶段并行工具流 ──
        # 任意命中图片关键字即触发，无 is_new 限制
        _q_fast = req.message.lower()
        _fast_keywords = ["图片", "照片", "来一张", "找一张", "搜一张", "看一张"]
        if any(kw in _q_fast for kw in _fast_keywords):
            logger.info(f"[FastPath] Keyword matched for '{req.message[:60]}', entering Fast Path")
            _fast_steps = []
            try:
                _fast_steps.append("检测到图片请求，进入极速模式...")
                yield make_step_event("检测到图片请求，进入极速模式...")
                _all_tools_fp = tm.get_enabled_tool_instances()
                _search_tool_fp = None
                for _t in _all_tools_fp:
                    if _t.name == "image_search":
                        _search_tool_fp = _t
                        break
                if _search_tool_fp:
                    # ── Phase 1: 多意图并行提取 ──
                    _fp_llm = LLMFactory.create(model_desc)
                    _fp_llm_bound = _fp_llm.bind_tools([_search_tool_fp])
                    _fp_extract_prompt = SystemMessage(content=(
                        "你是一个精准的参数提取器。用户需要调用 image_search 搜索图片。\n"
                        "重要指令：如果用户同时请求了多个不同主题的图片，你必须输出多个独立的 tool_calls"
                        "（每个 tool_call 对应一个独立的搜索主题）。\n"
                        "严格禁止：生成任何解释、开场白、或拒绝调用工具的文本。\n"
                        "例1：'来一张猫的图片' → 1 个 tool_call(query='猫')\n"
                        "例2：'来一张猫和一张狗的照片' → 2 个 tool_call(query='猫') + tool_call(query='狗')\n"
                        "例3：'找一张风景图以及一张跑车的照片' → tool_call(query='风景') + tool_call(query='跑车')"
                    ))
                    _fp_resp = await _fp_llm_bound.ainvoke(
                        [_fp_extract_prompt, HumanMessage(content=req.message)], temperature=0)
                    _fp_tcs = getattr(_fp_resp, 'tool_calls', None) or []
                    if _fp_tcs:
                        # ── Phase 2: 并发执行所有搜索 ──
                        _fp_tasks = []
                        for _fp_tc in _fp_tcs:
                            _fp_args_inner = _fp_tc.get('args', {}) if isinstance(_fp_tc, dict) else getattr(_fp_tc, 'args', {})
                            if isinstance(_fp_args_inner, str):
                                try:
                                    _fp_args_inner = json.loads(_fp_args_inner)
                                except json.JSONDecodeError:
                                    _fp_args_inner = {}
                            _fp_query = _fp_args_inner.get('query', req.message)
                            _fp_tasks.append(_fp_query)
                        logger.info(f"[FastPath] Concurrent search for {len(_fp_tasks)} queries: {_fp_tasks}")
                        _fp_raw_results = await asyncio.gather(*[
                            tm.execute_tool("image_search", {"query": q}) for q in _fp_tasks
                        ])
                        # ── 构建 tool results 上下文 ──
                        _fp_tool_context_parts = []
                        for idx, (_fp_q, _fp_raw) in enumerate(zip(_fp_tasks, _fp_raw_results)):
                            _fp_str = str(_fp_raw)
                            _fp_img_urls = []
                            if _fp_str.startswith("["):
                                try:
                                    _fp_parsed = json.loads(_fp_str)
                                    for _fp_item in _fp_parsed[:3]:
                                        if isinstance(_fp_item, dict):
                                            _fp_u = _fp_item.get("original_url") or _fp_item.get("url") or _fp_item.get("thumbnail_url")
                                            if _fp_u:
                                                _fp_img_urls.append(_fp_u)
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            _fp_tool_context_parts.append(f"搜索主题：{_fp_q}\n找到 {len(_fp_img_urls)} 张图片\n" + "\n".join(f"![{_fp_q}]({u})" for u in _fp_img_urls))
                        _fp_tool_context = "\n\n---\n\n".join(_fp_tool_context_parts)
                        # ── Phase 3: 流式语义润色 ──
                        _fast_steps.append("图片搜索完成，正在编排图文回答...")
                        yield make_step_event("图片搜索完成，正在编排图文回答...")
                        _fp_synth_prompt = SystemMessage(content=(
                            "你是一个专业的图文编排助手。\n"
                            "请根据工具返回的真实图片搜索结果，为用户生成图文并茂的回答。\n"
                            "要求：\n"
                            "1. 使用温和、自然的自然语言过渡描述每张图片（如：'下面为您找到的是猫和狗玩耍的照片：'）。\n"
                            "2. 必须严格使用 Markdown 格式渲染图片 `![描述](图片URL)`。\n"
                            "3. 如果搜索到多张图片，用自然的语言连接。\n"
                            "4. 严禁说'我无法显示图片'等废话，直接根据工具数据出内容。"
                        ))
                        _fp_synth_messages = [
                            _fp_synth_prompt,
                            HumanMessage(content=f"用户请求：{req.message}\n\n工具返回的搜索结果：\n{_fp_tool_context}\n\n请整理成图文回答。"),
                        ]
                        _fp_full_answer = ""
                        try:
                            async for _fp_chunk in _fp_llm.astream(_fp_synth_messages, temperature=0.3):
                                if hasattr(_fp_chunk, 'content') and _fp_chunk.content:
                                    _fp_full_answer += _fp_chunk.content
                                    role = "assistant" if not has_sent_first_token else None
                                    has_sent_first_token = True
                                    yield make_chunk(_fp_chunk.content, conversation_id=conv_id, role=role)
                        except Exception:
                            # Fallback: 直接拼接
                            _fp_full_answer = f"为您找到以下图片：\n\n{_fp_tool_context}"
                            yield make_chunk(_fp_full_answer, conversation_id=conv_id, role="assistant")
                        # 持久化
                        try:
                            from app.core.database import get_db as _fp_db
                            async for _fp_session in _fp_db():
                                await _save_conversation(user_id, conv_id, req.message, _fp_full_answer,
                                    db=_fp_session, route_used="tools", is_new=True,
                                    steps=_fast_steps)
                                break
                        except Exception as _fp_save:
                            logger.warning(f"[FastPath] Save failed: {_fp_save}")
                        yield f"event: done\ndata: {json.dumps({'type': 'done', 'conversation_id': conv_id})}\n\n"
                        yield make_done()
                        return
                logger.warning("[FastPath] No bing tool or LLM refused, fallthrough")
            except Exception as _fp_e:
                logger.warning(f"[FastPath] Error fallthrough: {_fp_e}")

        # 加载对话历史
        conv_history = None
        if req.conversation_id:
            conv_history = await _load_conversation_messages(req.conversation_id, user_id, db)

        # 加载系统提示词
        system_prompt = await get_active_system_prompt_content(db)

        # 流式接口前置记忆注入
        try:
            user_id_str = str(current_user.id)
            memories = await memory_service.retrieve_memories(
                user_id=user_id_str,
                query=req.message,
                top_k=5,
            )
            if memories:
                memory_context = memory_service.format_memories_for_context(memories)
                if system_prompt:
                    system_prompt = system_prompt + memory_context
                else:
                    from app.services.smart_router import FALLBACK_AGENT_PROMPT
                    system_prompt = FALLBACK_AGENT_PROMPT + memory_context
                logger.info(f"[Memory] Streaming: Injected {len(memories)} memories into system prompt")
        except Exception as e:
            logger.warning(f"[Memory] Streaming memory retrieval failed (non-blocking): {e}")

        # Docker 环境提示注入
        try:
            from app.utils.docker_env import get_docker_prompt_suffix
            docker_suffix = get_docker_prompt_suffix()
            if docker_suffix:
                if system_prompt:
                    system_prompt = system_prompt + docker_suffix
                else:
                    from app.services.smart_router import FALLBACK_AGENT_PROMPT
                    system_prompt = FALLBACK_AGENT_PROMPT + docker_suffix
                logger.info("[Docker] Streaming: Injected Docker environment prompt into system prompt")
        except Exception as e:
            logger.warning(f"[Docker] Streaming prompt injection failed (non-blocking): {e}")

        # 发现 MCP 工具
        mcp_tools = None
        try:
            user_id_str = str(current_user.id)
            mcp_tools = await discover_mcp_tools(
                user_id=user_id_str,
                db=db,
                session_id=req.conversation_id or str(uuid.uuid4()),
                force_refresh=False,
            )
            if mcp_tools:
                logger.info(f"[Chat] Streaming: discovered {len(mcp_tools)} MCP tools for user {user_id_str}")
        except Exception as e:
            logger.warning(f"[Chat] Streaming MCP discovery failed (non-blocking): {e}")
            mcp_tools = None

        # 用于累积 tool 消息在 done 时持久化
        pending_observations = []

        # ── v13.0: 所有路由统一由 smart_router 处理（包括 Skill）
        # ModelDescriptor.supports_tool_calling 会自检测模型能力
        # 不支持 tool calling 的模型走 _run_skill_prompt_mode（含 step 事件）
        # 支持 tool calling 的模型走标准 ReAct 循环

        # ── v8.0: 流式主循环（直接 yield SSE 字符串） ──
        async for event in smart_router.route_stream(
            query=req.message,
            model_descriptor=model_desc,
            kb_collection_name=kb_collection,
            top_k=req.top_k,
            score_threshold=req.score_threshold,
            embedding_model=kb_embedding_model,
            embedding_provider=kb_embedding_provider,
            conversation_history=conv_history,
            system_prompt=system_prompt,
            mcp_tools=mcp_tools,
            user_id=str(current_user.id),
        ):
            # 检查客户端断开
            if await request.is_disconnected():
                logger.info(f"Client disconnected during stream for request, stopping")
                yield make_done()
                return

            # v8.0: 每次 yield 前检查心跳（避免手动管理心跳协程）
            now_hb = datetime.now(timezone.utc).timestamp()
            if now_hb - last_heartbeat_ts >= 15.0:
                yield make_heartbeat()
                last_heartbeat_ts = now_hb

            try:
                if event["type"] == "step":
                    steps.append(event["content"])
                    yield make_step_event(event["content"])
                elif event["type"] == "meta":
                    route_used = event.get("route", "llm")
                    sources = event.get("sources", [])
                    yield make_meta_event(route_used, sources)
                elif event["type"] == "observation":
                    obs_content = event.get("content", "")
                    tool_name = event.get("tool_name", "tool")
                    tool_call_id = event.get("tool_call_id", None)
                    steps.append(f"工具 {tool_name} 返回数据")
                    
                    pending_observations.append({
                        "content": obs_content,
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                    })
                    
                    yield make_step_event(f"工具 {tool_name} 返回数据")
                elif event["type"] == "token":
                    full_answer_parts.append(event["content"])
                    # v8.0: 使用 OpenAI 兼容 chunk 格式
                    role = "assistant" if not has_sent_first_token else None
                    has_sent_first_token = True
                    yield make_chunk(
                        token=event["content"],
                        conversation_id=conv_id,
                        role=role,
                    )
                elif event["type"] == "done":
                    full_answer = "".join(full_answer_parts)
                    
                    # 发送最终 finish_reason
                    if full_answer_parts:
                        yield make_chunk("", conversation_id=conv_id, finish_reason="stop")
                    
                    # 发送 conversation_id 便于前端持久化
                    yield f"event: done\ndata: {json.dumps({'type': 'done', 'conversation_id': conv_id})}\n\n"
                    
                    yield make_done()
                    
                    # 持久化
                    try:
                        from app.core.database import get_db as _get_db_save
                        async for db_session in _get_db_save():
                            await _save_conversation(
                                user_id, conv_id, req.message, full_answer,
                                db=db_session,
                                sources=sources, route_used=route_used, is_new=is_new,
                                kb_id=req.kb_id, top_k=req.top_k,
                                score_threshold=req.score_threshold,
                                steps=steps,
                                pending_observations=pending_observations,
                            )
                            break
                    except Exception as e:
                        logger.error(f"Failed to save conversation on stream done: {e}")
                    
                    # 异步记忆沉淀
                    try:
                        extract_llm_stream = LLMFactory.create(model_desc)
                    except Exception:
                        extract_llm_stream = None

                    async def _extract_stream_memory():
                        try:
                            await memory_service.extract_and_store_memories(
                                user_id=user_id,
                                conversation_history=(conv_history or []),
                                query=req.message,
                                response=full_answer,
                                llm=extract_llm_stream,
                            )
                        except Exception as e:
                            logger.warning(f"[Memory] Stream memory extraction failed (non-blocking): {e}")

                    try:
                        asyncio.create_task(_extract_stream_memory())
                    except Exception as e:
                        logger.warning(f"[Memory] Failed to schedule stream memory task: {e}")
                    
                    return
            except Exception as e:
                logger.error(f"[Chat] Stream event processing error: {e}", exc_info=True)
                try:
                    yield make_error_chunk(code="stream_error", message=str(e))
                    yield make_done()
                except Exception:
                    pass
                return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
        },
    )


@router.post("/conversations", response_model=ConversationOut)
async def create_conversation(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new empty conversation with a timestamp-based title."""
    user_id = str(current_user.id)
    rc = redis_module.redis_client
    conv_id = str(uuid.uuid4())

    tz_shanghai = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz_shanghai)
    title = f"对话_{now.year}_{now.month}_{now.day}_{now.hour:02d}_{now.minute:02d}_{now.second:02d}"
    now_ts = now.timestamp()

    # Persist to Redis
    if rc:
        conv_key = _conv_key(user_id, conv_id)
        mapping = {
            "user_id": user_id,
            "title": title,
            "created_at": str(now_ts),
            "updated_at": str(now_ts),
        }
        await rc.hset(conv_key, mapping=mapping)
        await rc.expire(conv_key, CONV_TTL)
        await rc.sadd(_conv_list_key(user_id), conv_id)

    # Persist to MySQL immediately
    now_utc = datetime.now(timezone.utc)
    conv = Conversation(
        id=conv_id,
        user_id=user_id,
        title=title,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(conv)
    await db.commit()

    return ConversationOut(
        id=conv_id,
        title=title,
        created_at=now_utc,
        updated_at=now_utc,
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List user conversations. Redis first, MySQL fallback."""
    user_id = str(current_user.id)
    rc = redis_module.redis_client

    # 1) Always start with MySQL as the authoritative source of truth
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(desc(Conversation.updated_at))
    )
    mysql_convs = {c.id: c for c in result.scalars().all()}

    # 2) Try Redis for metadata cache and backfill
    redis_result = []
    if rc and mysql_convs:
        list_key = _conv_list_key(user_id)
        conv_ids_raw = await rc.smembers(list_key)
        if conv_ids_raw:
            mysql_conv_map = {cid: mysql_convs[cid] for cid in mysql_convs if cid in [str(x) for x in conv_ids_raw]}

            if redis_module.redis_client:
                await _clean_truly_stale_redis_entries(user_id, conv_ids_raw, mysql_convs)

            for cid_str in conv_ids_raw:
                cid_str = str(cid_str)
                if cid_str not in mysql_convs:
                    continue
                meta = await rc.hgetall(_conv_key(user_id, cid_str))
                mysql_row = mysql_convs.get(cid_str)
                if meta:
                    title_raw = meta.get("title", None)
                    if title_raw is not None:
                        title = title_raw
                    elif mysql_row:
                        title = mysql_row.title[:60]
                        await rc.hset(_conv_key(user_id, cid_str), "title", title)
                    else:
                        continue
                    created = meta.get("created_at", str(datetime.now(timezone.utc).timestamp()))
                    if mysql_row and mysql_row.updated_at:
                        updated_dt = mysql_row.updated_at
                        if updated_dt.tzinfo is None:
                            updated_dt = updated_dt.replace(tzinfo=timezone.utc)
                    else:
                        updated_raw = meta.get("updated_at", created)
                        updated_dt = datetime.fromtimestamp(float(updated_raw), tz=timezone.utc)
                    # Read RAG params: prefer MySQL, fallback to Redis
                    rag_kwargs = _build_rag_kwargs(meta, mysql_row)
                    redis_result.append(ConversationOut(
                        id=cid_str,
                        title=title[:60],
                        kb_id=meta.get("kb_id") or (mysql_row.kb_id if mysql_row else None),
                        created_at=datetime.fromtimestamp(float(created), tz=timezone.utc),
                        updated_at=updated_dt,
                        **rag_kwargs,
                    ))

    # 3) Merge: Redis results first (have cached metadata), then MySQL-only conversations
    #    This ensures NO conversation is ever lost due to Redis cache issues
    seen_ids = {c.id for c in redis_result}
    merged = list(redis_result)
    for conv in mysql_convs.values():
        if conv.id not in seen_ids:
            created_at = conv.created_at
            updated_at = conv.updated_at
            # MySQL returns offset-naive datetimes; ensure they're offset-aware UTC for sorting
            if created_at and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if updated_at and updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            merged.append(ConversationOut(
                id=conv.id,
                title=conv.title[:60],
                kb_id=conv.kb_id,
                top_k=conv.top_k,
                score_threshold=conv.score_threshold,
                created_at=created_at,
                updated_at=updated_at,
            ))

    # Backfill Redis for any MySQL-only conversations
    if rc and mysql_convs:
        for conv in mysql_convs.values():
            if conv.id not in seen_ids:
                await _backfill_redis_conv(user_id, conv)

    return sorted(merged, key=lambda c: c.updated_at, reverse=True)


@router.get("/conversations/{conv_id}", response_model=dict)
async def get_conversation(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get conversation messages. Always reads from MySQL as the source of truth.
    Redis is updated after read as a performance cache for subsequent reads.
    
    v5.0: Returns tool_call_id and tool_calls for full ReAct context recovery.
    """
    user_id = str(current_user.id)
    rc = redis_module.redis_client
    conv_key = _conv_key(user_id, conv_id)

    # Verify conversation exists in MySQL (the source of truth)
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id)
    )
    conv = conv_result.scalar_one_or_none()

    if conv is None:
        # Conversation was deleted from MySQL — clean any stale Redis data
        if rc:
            msgs_key = _conv_msgs_key(user_id, conv_id)
            await rc.delete(msgs_key, conv_key)
            await rc.srem(_conv_list_key(user_id), conv_id)
        return {"id": conv_id, "messages": []}

    # Always read messages from MySQL (source of truth)
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conv_id)
        .order_by(ChatMessage.created_at)
    )
    msgs = result.scalars().all()

    messages = []
    for msg in msgs:
        sources = None
        if msg.sources:
            try:
                sources = json.loads(msg.sources)
            except json.JSONDecodeError:
                pass
        steps = None
        if msg.steps:
            try:
                steps = json.loads(msg.steps)
            except json.JSONDecodeError:
                pass
        # v5.0: 还原 tool_calls 和 tool_call_id
        tool_calls = None
        if msg.tool_calls:
            try:
                tool_calls = json.loads(msg.tool_calls)
            except (json.JSONDecodeError, TypeError):
                pass
        
        messages.append({
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "tool_call_id": msg.tool_call_id,
            "tool_calls": tool_calls,
            "steps": steps,
            "sources": sources,
            "route_used": msg.route_used,
            "aborted": msg.aborted or False,
        })

    # Read RAG params: prefer MySQL, fallback to Redis
    rag_params = _build_rag_kwargs(await rc.hgetall(conv_key) if rc else {}, conv)

    # Backfill Redis for performance on subsequent reads
    if rc:
        msgs_key = _conv_msgs_key(user_id, conv_id)
        # Clear stale Redis cache first, then repopulate with fresh MySQL data
        await rc.delete(msgs_key)
        if msgs:
            await _backfill_redis_msgs(user_id, conv_id, msgs)
        # Also update conversation metadata in Redis
        await _backfill_redis_conv(user_id, conv)

    return {
        "id": conv_id,
        "kb_id": conv.kb_id,
        **rag_params,
        "messages": messages,
    }


@router.post("/abort")
async def save_aborted_message(
    req: AbortMessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save an aborted/interrupted assistant message to the conversation."""
    user_id = str(current_user.id)
    now = datetime.now(timezone.utc)
    assistant_msg = req.content or "(用户终止)"
    steps_json = json.dumps(req.steps or [], ensure_ascii=False) if req.steps else None
    sources_json = json.dumps(req.sources, ensure_ascii=False) if req.sources else None

    from app.models.chat import ChatMessage, Conversation

    try:
        # Ensure conversation exists
        result = await db.execute(
            select(Conversation).where(Conversation.id == req.conversation_id, Conversation.user_id == user_id)
        )
        conv = result.scalar_one_or_none()
        if not conv:
            conv = Conversation(id=req.conversation_id, user_id=user_id, title="新的对话", created_at=now, updated_at=now)
            db.add(conv)
        else:
            conv.updated_at = now

        msg = ChatMessage(
            conversation_id=req.conversation_id,
            role="assistant",
            content=assistant_msg,
            steps=steps_json,
            sources=sources_json,
            aborted=True,
            created_at=now,
        )
        db.add(msg)
        await db.commit()
    except Exception as e:
        logger.error(f"Failed to save aborted message: {e}")
        # Still return ok — frontend already has the message in memory

    return {"ok": True}


@router.post("/conversations/{conv_id}/kb")
async def update_conversation_kb(
    conv_id: str,
    body: ConversationKbUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the knowledge base binding for a conversation (Redis + MySQL)."""
    user_id = str(current_user.id)
    rc = redis_module.redis_client

    result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == user_id)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv.kb_id = body.kb_id
    conv.updated_at = datetime.now(timezone.utc)
    await db.commit()

    if rc:
        conv_key = _conv_key(user_id, conv_id)
        if body.kb_id:
            await rc.hset(conv_key, mapping={"kb_id": body.kb_id, "updated_at": str(datetime.now(timezone.utc).timestamp())})
        else:
            await rc.hset(conv_key, "updated_at", str(datetime.now(timezone.utc).timestamp()))
            await rc.hdel(conv_key, "kb_id")
        await rc.expire(conv_key, CONV_TTL)

    return {"ok": True}


@router.put("/conversations/{conv_id}")
async def rename_conversation(
    conv_id: str,
    body: ConversationRename,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename a conversation title (Redis + MySQL)."""
    user_id = str(current_user.id)
    rc = redis_module.redis_client

    if rc:
        conv_key = _conv_key(user_id, conv_id)
        if await rc.exists(conv_key):
            await rc.hset(conv_key, "title", body.title)
            await rc.expire(conv_key, CONV_TTL)
        else:
            now_ts = str(datetime.now(timezone.utc).timestamp())
            await rc.hset(conv_key, mapping={
                "user_id": user_id,
                "title": body.title,
                "created_at": now_ts,
                "updated_at": now_ts,
            })
            await rc.expire(conv_key, CONV_TTL)
            await rc.sadd(_conv_list_key(user_id), conv_id)

    # Update MySQL
    result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == user_id)
    )
    conv = result.scalar_one_or_none()
    if conv:
        conv.title = body.title
        await db.commit()
    else:
        conv = Conversation(
            id=conv_id,
            user_id=user_id,
            title=body.title,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.commit()

    return {"ok": True}


@router.delete("/conversations/{conv_id}/messages/{msg_id}")
async def delete_conversation_message(
    conv_id: str,
    msg_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single message from a conversation.
    
    v1.2: 级联删除 Qdrant 长期记忆（异步后台执行，不阻塞 HTTP 响应）
    """
    user_id = str(current_user.id)

    # Verify message belongs to user's conversation
    result = await db.execute(
        select(ChatMessage).where(
            ChatMessage.id == msg_id,
            ChatMessage.conversation_id == conv_id,
        )
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    # Verify conversation belongs to user
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == user_id)
    )
    conv = conv_result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await db.delete(msg)
    await db.commit()

    # Clear Redis cache for this conversation
    rc = redis_module.redis_client
    if rc:
        await rc.delete(_conv_msgs_key(user_id, conv_id))

    # v1.2: 异步级联删除 Qdrant 长期记忆（不阻塞 HTTP 响应）
    # BackgroundTasks 是 FastAPI 内置机制，会在 HTTP 响应返回后自动执行
    # 使用 create_task 确保 async def 方法能被正确调度
    async def _cascade_delete_qdrant_memories():
        try:
            from app.services.memory_service import memory_service
            await memory_service.delete_memories_by_message_id(user_id, msg_id)
        except Exception as e:
            logger.warning(f"[Chat] Cascade delete Qdrant memories failed (non-blocking): {e}")

    background_tasks.add_task(_cascade_delete_qdrant_memories)

    return {"ok": True}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a conversation (Redis + MySQL cascade)."""
    user_id = str(current_user.id)
    rc = redis_module.redis_client

    if rc:
        await rc.delete(_conv_key(user_id, conv_id))
        await rc.delete(_conv_msgs_key(user_id, conv_id))
        await rc.srem(_conv_list_key(user_id), conv_id)

    result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == user_id)
    )
    conv = result.scalar_one_or_none()
    if conv:
        await db.delete(conv)
        await db.commit()
        return {"ok": True}
    else:
        raise HTTPException(status_code=404, detail="Conversation not found")


# ---- Helpers ----

async def _load_model_descriptor(user_id: str, db: AsyncSession) -> ModelDescriptor:
    """
    Load the LLM model config for the given user.
    Priority: default+active > first active (by creation order) > raise error.
    """
    result = await db.execute(
        select(ModelConfig)
        .where(
            ModelConfig.user_id == user_id,
            ModelConfig.model_type == "llm",
            ModelConfig.is_default.is_(True),
            ModelConfig.is_active.is_(True),
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        result = await db.execute(
            select(ModelConfig)
            .where(
                ModelConfig.user_id == user_id,
                ModelConfig.model_type == "llm",
                ModelConfig.is_active.is_(True),
            )
            .order_by(ModelConfig.created_at.asc())  # 选择第一个开启的模型
        )
        config = result.scalars().first()
    if not config:
        raise HTTPException(
            status_code=400,
            detail="No active model configuration found. Please add a model in Settings.",
        )
    return ModelDescriptor(
        provider=config.provider,
        model_name=config.model_name,
        api_key=config.api_key,
        api_base=config.api_base,
    )


async def _load_active_embedding_descriptor(user_id: str, db: AsyncSession) -> ModelDescriptor | None:
    """
    Load the first active embedding model config (by creation order).
    Returns None if no active embedding model is found.
    """
    result = await db.execute(
        select(ModelConfig)
        .where(
            ModelConfig.user_id == user_id,
            ModelConfig.model_type == "embedding",
            ModelConfig.is_active.is_(True),
        )
        .order_by(ModelConfig.created_at.asc())  # 选择第一个开启的 embedding 模型
    )
    config = result.scalars().first()
    if not config:
        return None
    return ModelDescriptor(
        provider=config.provider,
        model_name=config.model_name,
        api_key=config.api_key,
        api_base=config.api_base,
    )



async def _get_kb_info(kb_id: str, user_id: str, db: AsyncSession) -> tuple[str | None, str | None, str | None]:
    """Get the Qdrant collection name, embedding model, and embedding provider for a knowledge base."""
    result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.user_id == user_id)
    )
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    embedding_provider = None
    if kb.embedding_model:
        model_result = await db.execute(
            select(ModelConfig).where(
                ModelConfig.user_id == user_id,
                ModelConfig.model_name == kb.embedding_model,
                ModelConfig.model_type == "embedding",
                ModelConfig.is_active == True,
            )
        )
        model_config = model_result.scalar_one_or_none()
        if model_config:
            embedding_provider = model_config.provider
        else:
            # 如果没有找到活跃的 ModelConfig，根据模型名自动推断 provider
            model_lower = kb.embedding_model.lower()
            if any(kw in model_lower for kw in ["qwen", "bge", "nomic", "mxbai"]):
                embedding_provider = "ollama"
            elif kb.embedding_model.startswith("text-embedding-"):
                embedding_provider = "openai"
    return kb.collection_name, kb.embedding_model, embedding_provider


async def _save_conversation(
    user_id: str,
    conv_id: str,
    user_msg: str,
    assistant_msg: str,
    db: AsyncSession,
    sources: list | None = None,
    route_used: str | None = None,
    is_new: bool = False,
    kb_id: str | None = None,
    top_k: int = 5,
    score_threshold: float = 0.5,
    steps: list | None = None,
    aborted: bool = False,
    tool_calls_detail: list[dict] | None = None,
    pending_observations: list[dict] | None = None,
    assistant_tool_calls: list[dict] | None = None,  # 新增：assistant 消息的 tool_calls，用于跨轮次上下文恢复
):
    """Persist a conversation turn to Redis (hot cache) + MySQL (direct).
    
    v5.0: Supports persisting tool_calls_detail and pending_observations.
    """
    from app.models.chat import Conversation, ChatMessage

    rc = redis_module.redis_client
    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    conv_key = _conv_key(user_id, conv_id)
    msgs_key = _conv_msgs_key(user_id, conv_id)
    list_key = _conv_list_key(user_id)
    auto_title = "新的对话"

    # ---- MySQL writes first (source of truth) ----
    # Ensure conversation row exists
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conv_id,
            Conversation.user_id == user_id,
        )
    )
    conv = result.scalar_one_or_none()

    if not conv:
        if is_new:
            tz_shanghai = ZoneInfo("Asia/Shanghai")
            now_sh = datetime.now(tz_shanghai)
            auto_title = f"对话_{now_sh.year}_{now_sh.month}_{now_sh.day}_{now_sh.hour:02d}_{now_sh.minute:02d}_{now_sh.second:02d}"
        conv = Conversation(
            id=conv_id,
            user_id=user_id,
            title=auto_title,
            kb_id=kb_id,
            top_k=top_k,
            score_threshold=score_threshold,
            created_at=now,
            updated_at=now,
        )
        db.add(conv)
    else:
        if conv.title == "新的对话" and auto_title:
            conv.title = auto_title
        if kb_id:
            conv.kb_id = kb_id
        conv.top_k = top_k
        conv.score_threshold = score_threshold
        conv.updated_at = now

    # Convert sources to JSON string
    sources_json = None
    if sources:
        serialized = []
        for s in sources:
            if hasattr(s, 'model_dump'):
                serialized.append(s.model_dump())
            elif hasattr(s, 'dict'):
                serialized.append(s.dict())
            elif isinstance(s, dict):
                serialized.append(s)
            else:
                serialized.append(str(s))
        sources_json = json.dumps(serialized, ensure_ascii=False)

    # User message already saved by chat_stream at stream start — skip duplicate
    # Convert steps to JSON string
    steps_json = None
    if steps:
        steps_json = json.dumps(steps, ensure_ascii=False)

    # Serialize assistant tool_calls for DB persistence
    assistant_tool_calls_json = None
    if assistant_tool_calls:
        # 只保留序列化必需字段，不存储原始对象
        serialized_tcs = []
        for tc in assistant_tool_calls:
            if isinstance(tc, dict):
                serialized_tcs.append(tc)
            elif hasattr(tc, 'dict'):
                serialized_tcs.append(tc.dict())
            elif hasattr(tc, 'model_dump'):
                serialized_tcs.append(tc.model_dump())
        if serialized_tcs:
            assistant_tool_calls_json = json.dumps(serialized_tcs, ensure_ascii=False)

    # Assistant message — 现在保存 tool_calls 用于跨轮次上下文恢复
    assistant_msg_obj = ChatMessage(
        conversation_id=conv_id,
        role="assistant",
        content=assistant_msg,
        steps=steps_json,
        sources=sources_json,
        route_used=route_used,
        aborted=aborted,
        tool_calls=assistant_tool_calls_json,  # 保存 tool_calls 到 DB
        created_at=now,
    )
    db.add(assistant_msg_obj)

    # v5.0: 如果有 tool_calls_detail，持久化为独立的 tool 角色消息
    if tool_calls_detail:
        obs_created_at = datetime.fromtimestamp(now_ts - 0.5, tz=timezone.utc)
        for detail in tool_calls_detail:
            tool_call_id = detail.get("tool_call_id", "")
            content = detail.get("content", "")[:20000]
            obs_msg = ChatMessage(
                conversation_id=conv_id,
                role="tool",
                content=content,
                tool_call_id=tool_call_id,
                created_at=obs_created_at,
            )
            db.add(obs_msg)

    if pending_observations:
        obs_created_at = datetime.fromtimestamp(now_ts - 0.5, tz=timezone.utc)
        for obs in pending_observations:
            obs_msg = ChatMessage(
                conversation_id=conv_id,
                role="tool",
                content=obs.get("content", "")[:20000],
                tool_call_id=obs.get("tool_call_id", None),
                created_at=obs_created_at,
            )
            db.add(obs_msg)

    await db.commit()

    # ---- Redis as hot cache ----
    if rc is not None:
        key_exists = await rc.exists(conv_key)
        if not key_exists:
            mapping: dict[str, str] = {
                "user_id": user_id,
                "title": auto_title,
                "created_at": str(now_ts),
                "updated_at": str(now_ts),
            }
            if kb_id:
                mapping["kb_id"] = kb_id
            mapping["top_k"] = str(top_k)
            mapping["score_threshold"] = str(score_threshold)
            await rc.hset(conv_key, mapping=mapping)
        else:
            updates: dict[str, str] = {"updated_at": str(now_ts)}
            if kb_id:
                updates["kb_id"] = kb_id
            updates["top_k"] = str(top_k)
            updates["score_threshold"] = str(score_threshold)
            await rc.hset(conv_key, mapping=updates)

        await rc.expire(conv_key, CONV_TTL)

        # User message already saved in chat_stream — only push assistant entry
        assistant_entry = json.dumps({
            "role": "assistant",
            "content": assistant_msg,
            "tool_calls": None,
            "tool_call_id": None,
            "sources": [s.dict() if isinstance(s, RAGSource) else s for s in (sources or [])],
            "route_used": route_used,
            "aborted": aborted,
            "timestamp": now_ts + 0.001,
        }, ensure_ascii=False)
        await rc.rpush(msgs_key, assistant_entry)
        await rc.expire(msgs_key, REDIS_TTL)
        await rc.sadd(list_key, conv_id)
        await rc.expire(list_key, CONV_TTL)



def _parse_rag_params_from_redis(meta: dict) -> dict:
    """Extract RAG parameters from Redis hash metadata."""
    result = {}
    try:
        if meta.get("top_k"):
            result["top_k"] = int(meta["top_k"])
    except (ValueError, TypeError):
        pass
    try:
        if meta.get("score_threshold"):
            result["score_threshold"] = float(meta["score_threshold"])
    except (ValueError, TypeError):
        pass
    return result


def _build_rag_kwargs(meta: dict, mysql_row) -> dict:
    """Build RAG kwargs dict: prefer MySQL values, fallback to Redis."""
    kwargs = {}
    if mysql_row:
        kwargs["top_k"] = mysql_row.top_k
        kwargs["score_threshold"] = mysql_row.score_threshold
    else:
        kwargs = _parse_rag_params_from_redis(meta)
    return kwargs


async def _batch_mysql_lookup(
    user_id: str, conv_ids: list[str], db: AsyncSession
) -> dict[str, Conversation]:
    if not conv_ids:
        return {}
    result = await db.execute(
        select(Conversation).where(
            Conversation.id.in_(conv_ids),
            Conversation.user_id == user_id,
        )
    )
    return {c.id: c for c in result.scalars().all()}


async def _clean_truly_stale_redis_entries(
    user_id: str,
    conv_ids_raw: set,
    mysql_conv_map: dict[str, Conversation],
):
    rc = redis_module.redis_client
    if not rc:
        return

    valid_ids = set(mysql_conv_map.keys())
    stale_ids = [str(cid) for cid in conv_ids_raw if str(cid) not in valid_ids]

    if stale_ids:
        pipe = rc.pipeline()
        for sid in stale_ids:
            pipe.delete(_conv_key(user_id, sid))
            pipe.delete(_conv_msgs_key(user_id, sid))
            pipe.srem(_conv_list_key(user_id), sid)
        await pipe.execute()


async def _backfill_redis_conv(user_id: str, conv: Conversation):
    rc = redis_module.redis_client
    if rc is None:
        return
    conv_key = _conv_key(user_id, conv.id)
    exists = await rc.exists(conv_key)
    if not exists:
        mapping: dict[str, str] = {
            "user_id": user_id,
            "title": conv.title,
            "created_at": str(conv.created_at.timestamp()),
            "updated_at": str(conv.updated_at.timestamp()),
        }
        if conv.kb_id:
            mapping["kb_id"] = conv.kb_id
        if conv.top_k is not None:
            mapping["top_k"] = str(conv.top_k)
        if conv.score_threshold is not None:
            mapping["score_threshold"] = str(conv.score_threshold)
        await rc.hset(conv_key, mapping=mapping)
        await rc.expire(conv_key, CONV_TTL)
        await rc.sadd(_conv_list_key(user_id), conv.id)


async def _backfill_redis_msgs(user_id: str, conv_id: str, msgs: list[ChatMessage]):
    rc = redis_module.redis_client
    if rc is None:
        return
    msgs_key = _conv_msgs_key(user_id, conv_id)
    exists = await rc.exists(msgs_key)
    if not exists:
        pipe = rc.pipeline()
        for msg in msgs:
            entry = json.dumps({
                "role": msg.role,
                "content": msg.content,
                "tool_call_id": msg.tool_call_id,
                "tool_calls": json.loads(msg.tool_calls) if msg.tool_calls else None,
                "steps": json.loads(msg.steps) if msg.steps else None,
                "sources": json.loads(msg.sources) if msg.sources else None,
                "route_used": msg.route_used,
                "aborted": msg.aborted or False,
                "timestamp": msg.created_at.timestamp(),
            }, ensure_ascii=False)
            pipe.rpush(msgs_key, entry)
        pipe.expire(msgs_key, REDIS_TTL)
        await pipe.execute()