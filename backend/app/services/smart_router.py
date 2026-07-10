"""
Smart Router: RAG -> Agent Tool Calling -> LLM Fallback Chain
v11.0 — Industry Standard ReAct Architecture
======================================================
彻底重构 v8.0 的 Pure ReAct，参考 OpenAI/Anthropic/LangGraph 的行业标准模式。
"""

import json
import asyncio
import os
import re
import uuid
from functools import lru_cache
from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from dataclasses import dataclass, field

import logging

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.documents import Document
from langchain_core.tools import BaseTool as LCBaseTool
from pydantic import BaseModel, Field, create_model

from app.core.config import get_settings
from app.services.rag_service import rag_service
from app.services.llm_service import LLMFactory, ModelDescriptor
from app.services.tool_manager import tool_manager as tm
from app.services.memory_service import memory_service

from app.progressive_disclosure import (
    SkillManager,
    ToolInjector,
    ExecutionInterceptor,
    create_progressive_disclosure_system,
)
from app.rag.libs.reranker.cross_encoder_reranker import CrossEncoderReranker
from app.core.mcp.discovery import (
    get_tool_route,
    is_mcp_tool,
)
from app.core.mcp.process_manager import process_manager
from app.core.mcp.executor import MCPHttpExecutor

from app.services.agent_state import (
    AgentState,
    AgentNode,
    REACT_SYSTEM_PROMPT_WITH_FALLBACK,
    FINALIZER_SYSTEM_PROMPT,
    format_tools_for_planner,
)
from app.schemas.agent_events import make_event, AgentEventType

from app.tools.dict_tool_adapter import normalize_tools_list

from app.services.tool_retrieval import get_tool_retrieval_service

logger = logging.getLogger(__name__)

settings = get_settings()

_pd_system = create_progressive_disclosure_system()
_pd_skill_manager: SkillManager = _pd_system["skill_manager"]
_pd_tool_injector: ToolInjector = _pd_system["tool_injector"]
_pd_execution_interceptor: ExecutionInterceptor = _pd_system["execution_interceptor"]

DEFAULT_TOP_K_CHILDREN = 15
DEFAULT_TOP_K_PARENTS = 3
MIN_RERANK_SCORE = 0.1


@dataclass
class RoutingResult:
    answer: str
    sources: list[dict]
    route_used: str
    tool_calls_detail: Optional[list[dict]] = None
    conversation_history: Optional[list] = None
    assistant_tool_calls: Optional[list[dict]] = None
    steps: Optional[list[str]] = None
    memory_context: Optional[list[dict]] = None


BASE_SYSTEM_PROMPT = """You are BaseAgent, an intelligent AI assistant powered by a large language model.

When answering:
- If provided with **context from documents**, base your answer strictly on that context and cite the sources.
- If no external information is available, answer honestly using your own knowledge, and note when you are unsure.

Always respond in the same language as the user's question. Be concise and accurate."""

FALLBACK_AGENT_PROMPT = """You are BaseAgent, an intelligent AI assistant with access to tools.

Use tools to gather information and answer the user's question.
Always respond in the same language as the user's question. Be concise and accurate.

### File Operations
- Default working directory is `/app/workspace/` for relative paths in the Docker container.
- Use read_local_file to read files and write_local_file to write files.

### Web Search
For time-sensitive queries (news, prices, versions, etc.), use tavily_web_search.

### Available Tools
{tools_description}"""


# ── P1: 全局提示词合并辅助函数 ──
def _merge_react_prompt(custom_system_prompt: Optional[str]) -> str:
    """
    将用户自定义全局提示词与 ReAct 工具调用规则合并注入。
    自定义提示词放在最前面作为基础设定，后面拼接 ReAct 核心规则。
    """
    if custom_system_prompt and custom_system_prompt.strip():
        return custom_system_prompt.strip() + "\n\n" + REACT_SYSTEM_PROMPT_WITH_FALLBACK
    return REACT_SYSTEM_PROMPT_WITH_FALLBACK


# ── 辅助：工具执行结束事件的推送（确保异常路径也不丢失） ──
def _emit_tool_end(event_sink, call_id, tool_name, error_msg=None):
    if not event_sink:
        return
    status = "failed" if error_msg else "success"
    message = f"✔ {tool_name} 完成" if not error_msg else f"✘ {tool_name} 失败: {error_msg[:80]}"
    event_sink(make_event(AgentEventType.TOOL_END, tool_name=tool_name, status=status,
                          call_id=call_id, message=message))


# ── P2: 工具摘要格式化（供意图分类器使用） ──
def _format_tools_summary(all_tools: list) -> str:
    """将工具列表格式化为简洁的一行摘要。"""
    names = []
    for t in all_tools:
        if isinstance(t, dict):
            func_info = t.get("function", t)
            names.append(func_info.get("name", "unknown"))
        else:
            names.append(getattr(t, "name", "unknown"))
    if not names:
        return "（无可用工具）"
    return "、".join(names)


class SmartRouter:
    """Three-tier intelligent routing strategy."""

    def __init__(self):
        self._reranker = None

    def _get_reranker(self):
        if self._reranker is None:
            from app.rag.libs.reranker.reranker_factory import create_reranker
            self._reranker = create_reranker(backend="cross_encoder")
        return self._reranker

    @staticmethod
    def _apply_white_tool_result(state, forced_tool_name, forced_call_id, results):
        """
        统一收口：白名单工具执行成功后，格式化结果并设置 TERMINATED 状态。
        所有触发白名单工具的路径（LLM主动调用、语义兜底触发）共用此方法。
        对 bing_image_search 等图片工具的 JSON 结果自动解析为 Markdown 图片格式。
        """
        _fallback_answer_parts = []
        for r_item in results:
            _content = r_item.get("content", "")
            _name = r_item.get("tool_name", forced_tool_name)

            # ── 尝试从 JSON 中提取图片 URL ──
            _image_url = None
            if _content and _content.startswith("["):
                try:
                    _parsed = json.loads(_content)
                    if isinstance(_parsed, list) and len(_parsed) > 0:
                        first = _parsed[0]
                        if isinstance(first, dict):
                            _image_url = first.get("original_url") or first.get("url") or first.get("thumbnail_url")
                except (json.JSONDecodeError, TypeError):
                    pass

            if _image_url:
                _fallback_answer_parts.append(f"![{_name} 搜索图片]({_image_url})")
            else:
                _fallback_answer_parts.append(f"【{_name} 搜索结果】\n{_content}")

        state.final_answer = "\n\n".join(_fallback_answer_parts)
        state.route_used = "tools"
        state.current_node = AgentNode.TERMINATED
        logger.info(f"[WhiteTool] Tool '{forced_tool_name}' result applied, state set to TERMINATED")

    @staticmethod
    def _truncate_observation(content: str, max_length: int = None) -> str:
        if max_length is None:
            max_length = settings.REACT_MAX_OBSERVATION_LENGTH
        if not content:
            return ""
        if isinstance(content, str) and len(content) > max_length:
            return content[:max_length] + f"\n\n[System: Response truncated at {max_length} characters. Original length: {len(content)}]"
        return content if isinstance(content, str) else str(content)

    @staticmethod
    def _convert_content_to_str(content) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, (dict, list)):
            return json.dumps(content, ensure_ascii=False)
        try:
            return str(content)
        except Exception:
            return ""

    async def _execute_single_tool(self, tool_name: str, tool_args: dict, available_tool_names: set = None,
                                    _event_sink: Optional[callable] = None) -> tuple[str, str, dict]:
        logger.info(f"[Agent] Executing tool: {tool_name}")

        # ── 生成唯一 call_id，确保 tool_start / tool_end 配对 ──
        _call_id = uuid.uuid4().hex[:12]

        if _event_sink:
            _event_sink(make_event(AgentEventType.TOOL_START, tool_name=tool_name, status="running",
                                    call_id=_call_id, message=f"正在调用 {tool_name}..."))

        try:
            intercept_result = await _pd_execution_interceptor.intercept_async(tool_name, tool_args)
            if intercept_result is not None:
                _emit_tool_end(_event_sink, _call_id, tool_name)
                return intercept_result

            if available_tool_names and tool_name not in available_tool_names:
                error_msg = (
                    f"[Execution Error]: The tool '{tool_name}' does not exist. "
                    f"Available tools are {sorted(available_tool_names)}. "
                    f"Please try again with the correct tool names."
                )
                logger.warning(f"[Agent] Unknown tool intercepted: {tool_name}")
                _emit_tool_end(_event_sink, _call_id, tool_name, error_msg)
                return (tool_name, error_msg, {"error": "unknown_tool", "message": error_msg, "trigger_replan": True})

            try:
                if is_mcp_tool(tool_name):
                    route = get_tool_route(tool_name)
                    if route:
                        server_name = route["server_name"]
                        raw_tool_name = route["raw_tool_name"]
                        executor_type = route["executor_type"]

                        import os
                        _mcp_stdio_enabled = os.getenv("MCP_STDIO_ENABLED", "true").lower() in ("true", "1", "yes")
                        if executor_type == "stdio" and not _mcp_stdio_enabled:
                            logger.warning(f"[MCP] stdio mode disabled for '{tool_name}'")
                            tool_result = json.dumps({
                                "error": "stdio_disabled",
                                "message": f"MCP server '{server_name}' runs in stdio mode which is disabled.",
                            })
                        elif executor_type == "stdio":
                            tool_result = await process_manager.call_tool(server_name, raw_tool_name, tool_args)
                        elif executor_type == "http":
                            from app.core.mcp.discovery import _parse_config
                            from sqlalchemy import select
                            from app.core.database import get_db
                            from app.models.mcp_server import MCPServer
                            http_executor = None
                            async for db_session in get_db():
                                result = await db_session.execute(
                                    select(MCPServer).where(MCPServer.name == server_name)
                                )
                                server = result.scalar_one_or_none()
                                if server and server.config:
                                    config = json.loads(server.config)
                                    url = config.get("url", "")
                                    if url:
                                        http_executor = MCPHttpExecutor(base_url=url)
                            if http_executor:
                                result_dict = await http_executor.call_tool(raw_tool_name, tool_args)
                                tool_result = result_dict.get("content", "") if result_dict.get("success") else json.dumps({"error": result_dict.get("error", "Unknown error")})
                            else:
                                tool_result = json.dumps({"error": f"No URL configured for HTTP MCP server '{server_name}'"})
                        else:
                            tool_result = json.dumps({"error": f"Unknown MCP executor type: {executor_type}"})
                    else:
                        tool_result = json.dumps({"error": f"MCP tool '{tool_name}' not found in route cache"})
                else:
                    tool_result = await tm.execute_tool(tool_name, tool_args)

                result_str = self._convert_content_to_str(tool_result)
                truncated = self._truncate_observation(result_str)
                _emit_tool_end(_event_sink, _call_id, tool_name)
                return tool_name, truncated, {}
            except asyncio.TimeoutError:
                error_msg = f"Tool '{tool_name}' timed out after {settings.REACT_TOOL_TIMEOUT}s"
                logger.warning(f"[Agent] Tool timeout: {tool_name}")
                _emit_tool_end(_event_sink, _call_id, tool_name, error_msg)
                return tool_name, error_msg, {"error": "timeout", "message": error_msg}
            except Exception as e:
                error_msg = f"Error executing tool '{tool_name}': {str(e)}"
                logger.warning(f"[Agent] {error_msg}")
                _emit_tool_end(_event_sink, _call_id, tool_name, error_msg)
                return tool_name, error_msg, {"error": "execution_error", "message": error_msg}
        except Exception as e:
            # 最外层兜底：确保事件不丢失
            _emit_tool_end(_event_sink, _call_id, tool_name, str(e))
            raise

    def _build_available_tool_names(self, all_tools: list) -> set:
        names = set()
        for t in all_tools:
            if isinstance(t, dict):
                func_info = t.get("function", t)
                name = func_info.get("name", "unknown")
            else:
                name = getattr(t, "name", "unknown")
            names.add(name)
        return names

    async def _execute_tools_parallel(self, tool_calls: list[dict]) -> list[dict]:
        if not tool_calls:
            return []

        available_tool_names = getattr(self, '_current_available_tool_names', None)

        tasks = []
        for tc in tool_calls:
            _sink = getattr(self, '_event_sink', None)
            task = self._execute_single_tool(tc["name"], tc.get("args", {}), available_tool_names, _event_sink=_sink)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=False)

        tool_messages = []
        for tc, (executed_name, result_str, error_info) in zip(tool_calls, results):
            # ── TOOL_END 事件已由 _execute_single_tool() 内部通过 _emit_tool_end 推送，避免重复 ──
            tool_messages.append({
                "tool_call_id": tc.get("id", f"auto_{tc['name']}"),
                "content": result_str,
                "tool_name": executed_name,
                "error_info": error_info,
            })

        return tool_messages

    @staticmethod
    def _build_conversation_context(conversation_history: Optional[list[dict]]) -> list:
        if not conversation_history:
            return []

        messages = []
        for msg in conversation_history:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                messages.append(SystemMessage(content=content))
            elif role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    if isinstance(tool_calls, str):
                        try:
                            tool_calls = json.loads(tool_calls)
                        except json.JSONDecodeError:
                            tool_calls = []
                    normalized = []
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            if "function" not in tc:
                                tc_id = tc.get("id", tc.get("tool_call_id", ""))
                                tc_name = tc.get("name", tc.get("function_name", ""))
                                tc_args = tc.get("args", tc.get("arguments", {}))
                                normalized.append({
                                    "id": tc_id,
                                    "type": "function",
                                    "function": {
                                        "name": tc_name,
                                        "arguments": json.dumps(tc_args) if isinstance(tc_args, dict) else str(tc_args),
                                    },
                                })
                            else:
                                normalized.append(tc)
                    messages.append(AIMessage(content=content, tool_calls=normalized if normalized else None))
                else:
                    messages.append(AIMessage(content=content))
            elif role == "tool":
                tool_call_id = msg.get("tool_call_id")
                if tool_call_id:
                    messages.append(ToolMessage(content=content, tool_call_id=tool_call_id))
                else:
                    messages.append(ToolMessage(content=content, tool_call_id="legacy_tool_call"))

        return messages

    async def _inject_memories(self, system_prompt: str, query: str, user_id: Optional[str]) -> str:
        if not user_id:
            return system_prompt

        try:
            memories = await memory_service.retrieve_memories(
                user_id=user_id,
                query=query,
                top_k=5,
            )
            if memories:
                memory_context = memory_service.format_memories_for_context(memories)
                if memory_context:
                    logger.info(f"[Memory] Injected {len(memories)} memories into system prompt")
                    return system_prompt + "\n\n" + memory_context
        except Exception as e:
            logger.warning(f"[Memory] Injection failed (non-blocking): {e}")

        return system_prompt

    async def _finalize(self, state: AgentState) -> AgentState:
        final_messages = [SystemMessage(content=FINALIZER_SYSTEM_PROMPT)]
        final_messages.extend(state.messages)
        final_messages.append(HumanMessage(
            content=f"基于以上所有已收集的信息，请对用户的原始问题给出全面回答：{state.query}"
        ))

        llm = LLMFactory.create(state.model_descriptor)
        try:
            response = await llm.ainvoke(final_messages, temperature=0.3)
            state.final_answer = response.content if hasattr(response, 'content') else str(response)
        except Exception as e:
            logger.error(f"[Finalizer] LLM call failed: {e}")
            if state.tool_results:
                parts = [r.get("content", "") for r in state.tool_results if r.get("content")]
                state.final_answer = "\n\n".join(parts) if parts else "处理完成，但生成最终回答时出现错误。"
            else:
                state.final_answer = "处理完成。"

        state.route_used = "tools" if state.tool_results else "llm"
        state.current_node = AgentNode.TERMINATED
        logger.info(f"[Finalizer] Answer generated ({len(state.final_answer)} chars, route={state.route_used})")
        return state

    async def _react_step(self, state: AgentState) -> AgentState:
        llm_with_tools = state._llm_with_tools
        llm_plain = state._llm

        try:
            response = await llm_with_tools.ainvoke(state.messages, temperature=0.3)
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)[:200]
            logger.error(f"[ReAct] LLM bind_tools invoke failed: {error_type}: {error_msg}")

            logger.info("[ReAct] Falling back to plain LLM (no tool binding)")
            try:
                fallback_response = await llm_plain.ainvoke(state.messages, temperature=0.3)
                content = getattr(fallback_response, 'content', '') or ''
                state.final_answer = content
                state.route_used = "llm" if not state.tool_results else "tools"
            except Exception as e2:
                logger.error(f"[ReAct] Plain LLM fallback also failed: {e2}")
                state.final_answer = "处理您的问题时出现错误，请稍后重试。"
                state.route_used = "error"

            state.current_node = AgentNode.TERMINATED
            return state

        content = getattr(response, 'content', '') or ''
        tool_calls = getattr(response, 'tool_calls', None) or []

        if tool_calls:
            tc_names = [tc.get('name', '') if isinstance(tc, dict) else getattr(tc, 'name', '') for tc in tool_calls]
            logger.info(f"[ToolCallDebug] Checkpoint C: LLM returned tool_calls={tc_names}")
        else:
            logger.info(f"[ToolCallDebug] Checkpoint C: LLM returned NO tool_calls. content_preview='{content[:100]}'")

        if not tool_calls:
            # ── P1 + P0.1: 通用语义兜底 + 白名单校验 ──
            if settings.TOOL_RETRIEVAL_FALLBACK_ENABLED:
                try:
                    if get_tool_retrieval_service().has_negation_intent(state.query):
                        logger.info(f"[SemanticFallback] Negation intent detected for query='{state.query[:40]}', skip fallback")
                    else:
                        _scored = await asyncio.to_thread(
                            get_tool_retrieval_service().retrieve_with_scores,
                            state.query,
                            top_k=1,
                            threshold=0.0,
                        )
                        if _scored:
                            _top_name, _top_score = _scored[0]
                            if _top_score >= settings.TOOL_FALLBACK_SEMANTIC_THRESHOLD:
                                # ── P0.1: 白名单校验 ──
                                if _top_name in settings.TOOL_FALLBACK_WHITELIST:
                                    logger.warning(
                                        f"[SemanticFallback] query='{state.query[:40]}' top_tool='{_top_name}' "
                                        f"score={_top_score:.4f} whitelist=YES "
                                        f">= threshold={settings.TOOL_FALLBACK_SEMANTIC_THRESHOLD}"
                                    )
                                    import uuid
                                    forced_call_id = f"force_{uuid.uuid4().hex[:8]}"
                                    forced_tool_name = _top_name
                                    forced_tool_args = {"query": state.query}
                                    forced_calls = [{"name": forced_tool_name, "args": forced_tool_args, "id": forced_call_id}]

                                    self._current_available_tool_names = self._build_available_tool_names(state._all_tools)
                                    results = await self._execute_tools_parallel(forced_calls)

                                    state.messages.append(AIMessage(
                                        content="",
                                        tool_calls=[{
                                            "id": forced_call_id,
                                            "type": "function",
                                            "function": {
                                                "name": forced_tool_name,
                                                "arguments": json.dumps(forced_tool_args, ensure_ascii=False),
                                            },
                                        }]
                                    ))

                                    for r_item in results:
                                        result_str = r_item.get("content", "")
                                        truncated = self._truncate_observation(result_str)
                                        state.tool_results.append({
                                            "tool_call_id": forced_call_id,
                                            "content": truncated,
                                            "tool_name": r_item.get("tool_name", forced_tool_name),
                                            "error_info": r_item.get("error_info", {}),
                                        })
                                        state.messages.append(ToolMessage(content=truncated, tool_call_id=forced_call_id))

                                    # ── 统一收口：白名单工具结果格式化 + TERMINATED ──
                                    self._apply_white_tool_result(state, forced_tool_name, forced_call_id, results)
                                    return state
                                else:
                                    logger.info(
                                        f"[SemanticFallback] query='{state.query[:40]}' top_tool='{_top_name}' "
                                        f"score={_top_score:.4f} whitelist=NO, skip"
                                    )
                            else:
                                logger.debug(
                                    f"[SemanticFallback] query='{state.query[:40]}' top_tool='{_top_name}' "
                                    f"score={_top_score:.4f} < threshold={settings.TOOL_FALLBACK_SEMANTIC_THRESHOLD}, skip"
                                )
                except Exception as e:
                    logger.warning(f"[SemanticFallback] Check failed (non-blocking): {e}")

            required_tool = state.required_tool_name
            if required_tool:
                is_valid, content_len, fail_reason = SmartRouter._validate_tool_call_payload(
                    state.messages, required_tool, min_length=1000
                )
                if not is_valid:
                    guard_msg = (
                        f"System Guard Error: Validation failed for tool '{required_tool}'. "
                        f"{fail_reason} "
                        f"Please generate the complete article with real images (via image_search) "
                        f"and Mermaid diagrams, call the tool again with the full content, "
                        f"and do NOT output your final delivery report until the tool returns "
                        f"a success message."
                    )
                    logger.warning(
                        f"[Guard] Payload validation failed for '{required_tool}': "
                        f"content_len={content_len}, reason={fail_reason}, "
                        f"rejecting FINALIZER, forcing re-plan"
                    )
                    state.messages.append(HumanMessage(content=guard_msg))
                    state.current_node = AgentNode.REACT_LOOP
                    return state
                # 校验通过：提取 filepath（成功时 fail_reason 为 filepath 字符串）
                if isinstance(fail_reason, str) and fail_reason.startswith("/app/output/"):
                    state.output_file_path = fail_reason

            state.current_node = AgentNode.FINALIZER
            state.messages.append(AIMessage(content=content))
            logger.info(f"[ReAct] LLM finished tool calls ({len(content)} chars), entering FINALIZER")
            return state

        content_lower = content.lower()
        if any(kw.lower() in content_lower for kw in SmartRouter._STOP_KEYWORDS):
            logger.info(f"[EarlyStop] 命中关键词，跳过工具调用，进入 Finalizer")
            state.current_node = AgentNode.FINALIZER
            state.messages.append(AIMessage(content=content))
            return state

        state.iteration += 1
        logger.info(f"[ReAct] Round {state.iteration}: {len(tool_calls)} tool(s) called")

        calls = []
        for tc in tool_calls:
            tc_id = tc.get('id', '') if isinstance(tc, dict) else getattr(tc, 'id', '')
            tc_name = tc.get('name', '') if isinstance(tc, dict) else getattr(tc, 'name', '')
            tc_args_str = tc.get('args', '{}') if isinstance(tc, dict) else getattr(tc, 'args', '{}')
            # ── flatten OpenAI nested function format ──
            if isinstance(tc, dict) and not tc_name and "function" in tc:
                func_data = tc["function"]
                if isinstance(func_data, dict):
                    tc_name = func_data.get("name", tc_name)
                    tc_args_str = func_data.get("arguments", tc_args_str)
            if isinstance(tc_args_str, str):
                try:
                    tc_args = json.loads(tc_args_str)
                except json.JSONDecodeError:
                    tc_args = {}
            else:
                tc_args = tc_args_str
            calls.append({"name": tc_name, "args": tc_args, "id": tc_id})

        import hashlib
        deduped_calls = []
        for c in calls:
            canonical = json.dumps({"name": c["name"], "args": c["args"]}, sort_keys=True, ensure_ascii=False)
            fp = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if fp in state.executed_action_fingerprints:
                warning_msg = (
                    f"System Warning: Duplicate action detected. "
                    f"You have already called this tool with the exact same arguments. "
                    f"Please analyze the previous observation and try a different approach."
                )
                state.tool_results.append({
                    "tool_call_id": c["id"],
                    "content": warning_msg,
                    "tool_name": c["name"],
                    "error_info": {"error": "duplicate_action"},
                })
                state.messages.append(ToolMessage(content=warning_msg, tool_call_id=c["id"]))
                logger.warning(f"[ReAct] Duplicate action blocked: {c['name']} (fp={fp[:12]}...)")
            else:
                state.executed_action_fingerprints.add(fp)
                deduped_calls.append(c)
        calls = deduped_calls
        if not calls:
            logger.warning("[ReAct] All tool calls were duplicates, entering FINALIZER")
            state.current_node = AgentNode.FINALIZER
            return state

        self._current_available_tool_names = self._build_available_tool_names(state._all_tools)
        results = await self._execute_tools_parallel(calls)

        normalized_tcs = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                normalized_tcs.append(tc)
            else:
                normalized_tcs.append({
                    "id": getattr(tc, 'id', ''),
                    "type": "function",
                    "function": {
                        "name": getattr(tc, 'name', ''),
                        "arguments": getattr(tc, 'args', '{}'),
                    },
                })
        state.messages.append(AIMessage(content=content, tool_calls=normalized_tcs))

        for r_item, call in zip(results, calls):
            tool_call_id = call["id"]
            executed_name = r_item.get("tool_name", call["name"])
            result_str = r_item.get("content", "")
            truncated = self._truncate_observation(result_str)

            state.tool_results.append({
                "tool_call_id": tool_call_id,
                "content": truncated,
                "tool_name": executed_name,
                "error_info": r_item.get("error_info", {}),
            })
            state.messages.append(ToolMessage(content=truncated, tool_call_id=tool_call_id))

        state.current_node = AgentNode.REACT_LOOP
        return state

    async def _run_agent_loop(self, state: AgentState) -> AgentState:
        while state.current_node != AgentNode.TERMINATED:
            if state.iteration >= state.max_iterations:
                logger.warning(f"[ReAct] Max iterations ({state.max_iterations}) reached")
                state.final_answer = f"已达到最大迭代次数 ({state.max_iterations})，请简化您的问题或重新提问。"
                state.route_used = "error"
                state.current_node = AgentNode.TERMINATED
                break

            if state.current_node == AgentNode.REACT_LOOP:
                state = await self._react_step(state)
            elif state.current_node == AgentNode.FINALIZER:
                state = await self._finalize(state)
            else:
                logger.error(f"[ReAct] Unknown state: {state.current_node}")
                break

        return state

    async def _init_agent_state(
        self,
        query: str,
        model_descriptor: ModelDescriptor,
        conversation_history: Optional[list],
        system_prompt: str,
        all_tools: list,
        llm,
        llm_with_tools,
        memory_context: Optional[str] = None,
    ) -> AgentState:
        final_system_prompt = system_prompt
        if memory_context:
            final_system_prompt += f"\n\n{memory_context}"

        messages = [SystemMessage(content=final_system_prompt)]
        if conversation_history:
            messages.extend(conversation_history)
        messages.append(HumanMessage(content=query))

        return AgentState(
            query=query,
            system_prompt=final_system_prompt,
            model_descriptor=model_descriptor,
            conversation_history=conversation_history,
            max_iterations=settings.REACT_MAX_ITERATIONS,
            messages=messages,
            _all_tools=all_tools,
            _llm=llm,
            _llm_with_tools=llm_with_tools,
        )

    INTENT_CLASSIFIER_PROMPT = """你是一个严格的意图分类器。判断用户的提问是否需要调用外部工具。

## 当前可用的工具列表
用户提问时，以下工具可供调用：
{tools_summary}

## 分类规则

### 必须归为 REQUIRES_TOOL 的情况（当可用工具能完成以下需求时）：
- 用户要求获取/查看/搜索任何类型的**图片、照片、壁纸**，例如"来一张猫的图片"、"看看长什么样"、"找一张风景图"
- 需要联网搜索实时最新信息（新闻、价格、版本、天气等）
- 需要读写文件、执行命令、操作本地系统
- 需要调用任何 API 或 MCP 工具
- 用户明确提到了某个技能/工具名称
- 内容创作类任务（写博客、文章、报告、文档、教程、笔记等），需要输出到文件或遵循特定模板

### 必须归为 DIRECT_CHAT 的情况：
- 日常闲聊、问候、打招呼
- 纯知识问答（百科类、概念解释）
- 翻译、润色、摘要等纯语言处理
- 数学计算、逻辑推理
- 纯代码示例（不需要保存到文件）

### 典型反例（帮助理解分类边界）：
- ❌ 用户说"来一张猫的图片" → 用户要的是**真实图片**，不可用文字描述替代，且当前有 bing_image_search 工具可用 → REQUIRES_TOOL
- ❌ 用户说"查一下今天北京的天气" → 需要联网获取实时数据，且当前有 tavily_web_search 工具可用 → REQUIRES_TOOL
- ❌ 用户说"帮我把这个文件格式化成标准格式" → 涉及文件操作，且当前有 document_reader 等工具可用 → REQUIRES_TOOL
- ✅ 用户说"介绍一下你自己" → 只需对话 → DIRECT_CHAT

## 输出格式
只输出一个词：REQUIRES_TOOL 或 DIRECT_CHAT，不要任何其他文字。"""

    @staticmethod
    def _classify_intent_cache_key(query: str) -> str:
        return query.strip().lower()[:50]

    @staticmethod
    @lru_cache(maxsize=256)
    def _classify_intent_cache_lookup(cache_key: str) -> Optional[str]:
        SHORT_CHAT_CACHE: dict[str, str] = {
            "你好": "DIRECT_CHAT", "您好": "DIRECT_CHAT",
            "hi": "DIRECT_CHAT", "hello": "DIRECT_CHAT",
            "早上好": "DIRECT_CHAT", "下午好": "DIRECT_CHAT",
            "晚上好": "DIRECT_CHAT", "在吗": "DIRECT_CHAT",
            "在不在": "DIRECT_CHAT", "谢谢": "DIRECT_CHAT",
            "感谢": "DIRECT_CHAT", "好的": "DIRECT_CHAT",
            "ok": "DIRECT_CHAT", "再见": "DIRECT_CHAT",
            "拜拜": "DIRECT_CHAT", "你是谁": "DIRECT_CHAT",
            "你能做什么": "DIRECT_CHAT",
        }
        return SHORT_CHAT_CACHE.get(cache_key)

    @staticmethod
    async def _classify_intent(query: str, model_descriptor: ModelDescriptor) -> str:
        if len(query.strip()) <= 5:
            return "DIRECT_CHAT"

        # ── P0.2: 语义前置路由校验（在短文本缓存之前执行） ──
        _semantic_route_hit = False
        try:
            from app.services.tool_retrieval import get_tool_retrieval_service
            from app.core.config import get_settings as _cfg
            _tr_svc = get_tool_retrieval_service()
            _threshold = _cfg().TOOL_ROUTING_SEMANTIC_THRESHOLD
            _scored = _tr_svc.retrieve_with_scores(query, top_k=1, threshold=0.0)
            if _scored:
                _top_name, _top_score = _scored[0]
                if _top_score >= _threshold:
                    _semantic_route_hit = True
                    logger.info(
                        f"[SemanticRouting] query='{query[:40]}' top_tool='{_top_name}' "
                        f"score={_top_score:.4f} >= threshold={_threshold} → REQUIRES_TOOL"
                    )
                    return "REQUIRES_TOOL"
                else:
                    logger.debug(
                        f"[SemanticRouting] query='{query[:40]}' top_tool='{_top_name}' "
                        f"score={_top_score:.4f} < threshold={_threshold}, fall through to LLM"
                    )
        except Exception as _e:
            logger.warning(f"[SemanticRouting] Service unavailable (non-blocking): {_e}")

        # ── P0.2: 语义路由命中后不检查短文本缓存；未命中时继续 ──
        if not _semantic_route_hit:
            cache_key = SmartRouter._classify_intent_cache_key(query)
            cached = SmartRouter._classify_intent_cache_lookup(cache_key)
            if cached is not None:
                logger.debug(f"[Intent] Cache HIT: {cached}")
                return cached

        try:
            llm = LLMFactory.create(model_descriptor)
            # ── P0: 获取工具摘要注入 ──
            tools_summary = "（无法获取工具列表）"
            try:
                _all_tools = tm.get_enabled_tool_instances()
                if _all_tools:
                    tools_summary = _format_tools_summary(_all_tools)
            except Exception:
                pass
            prompt_content = SmartRouter.INTENT_CLASSIFIER_PROMPT.format(tools_summary=tools_summary)
            messages = [
                SystemMessage(content=prompt_content),
                HumanMessage(content=query),
            ]
            response = await llm.ainvoke(messages, temperature=0, max_tokens=10)
            result = (response.content or "").strip().upper()
            if result == "DIRECT_CHAT":
                logger.info(f"[ToolCallDebug] Checkpoint A: intent_classifier=DIRECT_CHAT for query='{query[:40]}'")
                return "DIRECT_CHAT"
            elif result == "REQUIRES_TOOL":
                logger.info(f"[ToolCallDebug] Checkpoint A: intent_classifier=REQUIRES_TOOL for query='{query[:40]}'")
                return "REQUIRES_TOOL"
            else:
                logger.warning(f"[Intent] Unrecognized '{result}', defaulting to REQUIRES_TOOL")
                return "REQUIRES_TOOL"
        except Exception as e:
            logger.warning(f"[Intent] Classifier failed: {e}, defaulting to REQUIRES_TOOL")
            return "REQUIRES_TOOL"

    def _inject_skill_tools(self, all_tools: list) -> list:
        return _pd_tool_injector.inject_skill_tools(all_tools)

    SKILL_PROMPT_TEMPLATE = """You are BaseAgent, following a skill specification to process the given file.

## Skill Instructions
{skill_body}

## File Content to Process
```text
{file_content}
```

Follow the skill instructions step by step. Wrap ONLY the final output inside `<final_output>` and `</final_output>` tags. Output nothing else outside these tags.
"""

    WORKSPACE_DIRS: list = ["/app/workspace/", "/app/data/"]

    @staticmethod
    def _match_skill(query: str) -> Optional[dict]:
        folder_name = _pd_skill_manager.match_skill(query)
        if not folder_name:
            return None
        for skill in _pd_skill_manager.get_active_skills_metadata():
            if skill.get("folder_name") == folder_name:
                return skill
        return None

    @staticmethod
    def _should_route_to_agent(query: str) -> Optional[str]:
        """
        检查匹配的技能是否声明 requires_tools=true。
        如果是，则必须走 ReAct Agent 路径（而非纯文本技能路径）。

        Returns:
            skill_folder_name: 需要走 Agent 路径的技能文件夹名
            None: 可以走纯文本技能路径
        """
        skill = SmartRouter._match_skill(query)
        if not skill:
            return None
        requires_tools = skill.get("requires_tools", False)
        if requires_tools:
            _folder = skill["folder_name"]
            logger.info(
                f"[Router] Skill '{_folder}' has requires_tools=true, "
                f"skipping non-stream skill tier, routing directly to Agent"
            )
            return _folder
        return None

    @staticmethod
    def _get_required_tool_name(query: str) -> Optional[str]:
        """
        检查匹配的技能是否声明 requires_tools=true。
        如果是，返回 "write_local_file"（强制写入工具名）。
        
        Returns:
            工具名（如 "write_local_file"），或 None
        """
        skill_folder = SmartRouter._should_route_to_agent(query)
        if skill_folder:
            return "write_local_file"
        return None

    @staticmethod
    def _validate_tool_call_payload(messages: list, tool_name: str, min_length: int = 1000) -> tuple[bool, int, str]:
        """
        深度校验：遍历消息历史，找到对该工具的最后一次调用并校验其 content。
        LLM 可能在 ReAct 循环中多次调用同一工具（草稿/正式），
        我们只校验最后一次调用的 payload。

        Args:
            messages: 状态机的消息链
            tool_name: 要校验的工具名（如 "write_local_file"）
            min_length: content 参数的最小字符数阈值，默认 1000

        Returns:
            (is_valid: bool, content_length: int, reason: str)
            - is_valid: True 通过所有校验
            - content_length: 实际字符数
            - reason: 校验失败的详细原因（成功时为 filepath 字符串）
        """
        last_result = (False, 0, f"tool '{tool_name}' was never called")

        for msg in messages:
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_name = ""
                    tc_args = {}
                    if isinstance(tc, dict):
                        if tc.get("function"):
                            tc_name = tc["function"].get("name", "")
                            try:
                                tc_args = json.loads(tc["function"].get("arguments", "{}"))
                            except (json.JSONDecodeError, TypeError):
                                tc_args = {}
                        else:
                            tc_name = tc.get("name", "")
                            tc_args = tc.get("args", {})
                    else:
                        tc_name = getattr(tc, "name", "")
                        tc_args = getattr(tc, "args", {})

                    if tc_name != tool_name:
                        continue

                    # 提取并校验本次调用的 payload
                    content_str = tc_args.get("content", "")
                    filepath = tc_args.get("filepath", "")
                    if not isinstance(content_str, str):
                        last_result = (False, 0, "content is not a string")
                        continue

                    clen = len(content_str)

                    if clen < min_length:
                        last_result = (False, clen,
                            f"content too short ({clen} chars, need >= {min_length})")
                        continue

                    if not ("![" in content_str and (".jpg" in content_str or ".png" in content_str)):
                        last_result = (False, clen,
                            "缺少真实图片链接。文章必须通过 image_search 获取真实图片 URL，"
                            "并使用 ![alt](url) 格式插入至少一张配图。")
                        continue

                    if "```mermaid" not in content_str:
                        last_result = (False, clen,
                            "缺少 Mermaid 流程图。文章必须包含至少一个 ```mermaid 代码块（如架构图、调用链路图）。")
                        continue

                    if not filepath.startswith("/app/output/"):
                        last_result = (False, clen,
                            f"filepath '{filepath}' 不是 /app/output/ 下的路径，")
                        continue

                    # 全部校验通过
                    last_result = (True, clen, filepath)

        return last_result

    @staticmethod
    def _validate_and_resolve_path(raw_path: str) -> Optional[str]:
        if not raw_path:
            return None
        abspath = os.path.abspath(raw_path)
        for base in SmartRouter.WORKSPACE_DIRS:
            if abspath.startswith(base):
                if os.path.isfile(abspath):
                    return abspath
        return None

    @staticmethod
    def _extract_file_path_from_query(query: str) -> Optional[str]:
        patterns = [
            r'(/[^\s<>"\'|]+\.\w+)',
            r'(/[^\s<>"\'|]+)',
        ]
        for p in patterns:
            m = re.search(p, query)
            if m:
                path = m.group(1)
                validated = SmartRouter._validate_and_resolve_path(path)
                if validated:
                    return validated
        return None

    @staticmethod
    async def _generate_english_slug(topic: str, model_descriptor: ModelDescriptor) -> str:
        """
        利用内部 LLM 实例将主题转换为精简英文 URL Slug。
        
        流程：
          1. 调用 LLM 进行一次轻量级翻译 + 压缩
          2. 正则清洗非法字符
          3. 兜底缺省名 untitled-post

        Args:
            topic: 中英文主题文本（如 "Redis 缓存穿透击穿雪崩"）
            model_descriptor: 当前使用的模型描述（复用现有 LLMFactory）

        Returns:
            纯英文 slug（如 "redis-cache-issues"），不含非法字符
        """
        if not topic or not topic.strip():
            return "untitled-post"
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            llm = LLMFactory.create(model_descriptor)
            prompt = (
                "Translate and condense the following topic into a concise URL slug "
                "(lowercase, hyphen-separated, max 3-4 English words). "
                f"Topic: {topic}. Output ONLY the slug, without quotes or markdown."
            )
            messages = [
                SystemMessage(content="You are a slug generator. Output ONLY the slug."),
                HumanMessage(content=prompt),
            ]
            response = await llm.ainvoke(messages, temperature=0, max_tokens=20)
            slug = response.content if hasattr(response, 'content') else str(response)
            slug = slug.strip().lower()
            # 正则清洗：仅保留小写字母、数字、连字符
            slug = re.sub(r'[^a-z0-9\-]', '', slug).strip('-')
            slug = slug[:50]
            return slug if slug else "untitled-post"
        except Exception as e:
            logger.warning(f"[Slug] LLM slug generation failed, using fallback: {e}")
            return "untitled-post"

    @staticmethod
    def _extract_output_filename(input_path: str) -> str:
        """根据输入文件路径推导输出文件名。"""
        if not input_path:
            return "untitled-post.md"
        base, ext = os.path.splitext(input_path)
        return f"{base}{ext}"

    async def _run_skill_tier_nonstream(
        self,
        query: str,
        model_descriptor: ModelDescriptor,
        system_prompt: Optional[str] = None,
        conversation_history: Optional[list] = None,
    ) -> Optional[RoutingResult]:
        steps: list[str] = []

        skill = self._match_skill(query)
        if skill is None:
            return None

        skill_folder = skill["folder_name"]
        display_name = skill.get("display_name", skill_folder)
        steps.append(f"已加载技能「{display_name}」")

        skill_body = _pd_skill_manager.read_skill_content(skill_folder)
        if not skill_body:
            return None

        skill_params = skill.get("parameters", {})
        if isinstance(skill_params, dict):
            required_params = skill_params.get("required", [])
            if required_params and required_params != ["query"]:
                logger.info(f"[SkillTier] Non-stream: skipping '{skill_folder}': requires parameters {required_params}")
                return None

        file_path = self._extract_file_path_from_query(query)
        file_content = ""
        if file_path:
            steps.append(f"正在读取源文件 {os.path.basename(file_path)}")
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    file_content = f.read()
                steps.append(f"已读取 {len(file_content)} 字符")
            except Exception as e:
                steps.append(f"文件读取失败: {e}")

        steps.append(f"正在执行技能「{display_name}」")
        _prompt = self.SKILL_PROMPT_TEMPLATE.format(
            skill_body=skill_body,
            file_content=file_content or "(无文件内容，请根据技能描述直接处理用户请求)",
        )
        _system = system_prompt or "You are BaseAgent, a helpful assistant."
        _llm = LLMFactory.create(model_descriptor)
        _messages = [SystemMessage(content=_system), HumanMessage(content=_prompt)]

        try:
            response = await _llm.ainvoke(_messages, temperature=0.1)
            full_result = response.content if hasattr(response, 'content') else str(response)
        except Exception as e:
            return RoutingResult(answer=f"技能执行失败: {e}", sources=[], route_used="skill", steps=steps)

        _match = re.search(r'<final_output>\s*(.*?)\s*</final_output>', full_result, re.DOTALL)
        if _match:
            write_content = _match.group(1).strip()
            slug = await self._generate_english_slug(query, model_descriptor)
            output_path = self._extract_output_filename(file_path or f"/app/output/{slug}.md")
            try:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(write_content)
                steps.append(f"格式化完成，已保存到 {os.path.basename(output_path)}")
            except Exception:
                steps.append("格式化完成，但文件写入失败")
        else:
            steps.append("技能执行完成")

        return RoutingResult(answer=full_result, sources=[], route_used="skill", steps=steps)

    async def _run_skill_tier_stream(
        self,
        query: str,
        model_descriptor: ModelDescriptor,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        skill = self._match_skill(query)
        if skill is None:
            return

        skill_folder = skill["folder_name"]
        display_name = skill.get("display_name", skill_folder)

        skill_params = skill.get("parameters", {})
        if isinstance(skill_params, dict):
            required_params = skill_params.get("required", [])
            props = skill_params.get("properties", {})
            if required_params:
                if len(required_params) == 1 and len(props) <= 2:
                    pass
                else:
                    logger.info(f"[SkillTier] Skipping '{skill_folder}' in pre-route tier: requires parameters {required_params}")
                    return

        yield {"type": "step", "content": "正在分析技能需求..."}
        yield {"type": "step", "content": f"已加载技能「{display_name}」"}

        skill_body = _pd_skill_manager.read_skill_content(skill_folder)
        if not skill_body:
            yield {"type": "step", "content": f"技能「{display_name}」加载失败，直接回答"}
            async for event in self._run_llm_stream(query, model_descriptor):
                yield event
            return

        file_path = self._extract_file_path_from_query(query)
        file_content = ""
        if file_path:
            yield {"type": "step", "content": f"正在读取源文件 {os.path.basename(file_path)}..."}
            loop = asyncio.get_event_loop()
            try:
                file_content = await loop.run_in_executor(
                    None, lambda p=file_path: open(p, "r", encoding="utf-8").read()
                )
                yield {"type": "step", "content": f"已读取 {len(file_content)} 字符"}
            except Exception as e:
                yield {"type": "step", "content": f"文件读取失败: {e}，继续处理"}

        # 统一走 SkillRunner（单轮 LLM + 工具调用）
        from app.progressive_disclosure.skill_runner import SkillRunner
        yield {"type": "step", "content": f"正在执行技能「{display_name}」..."}
        runner = SkillRunner(
            skill_body=skill_body,
            skill_name=skill_folder,
            display_name=display_name,
            max_steps=25,
            timeout_seconds=300,
            user_query=query,
            model_descriptor=model_descriptor,
        )
        try:
            result_json = await runner.run()
            result = json.loads(result_json) if isinstance(result_json, str) else result_json
            artifacts = result.get("artifacts", [])
            output_path = artifacts[0]["path"] if artifacts else None
            success = result.get("success", False)
            if not success:
                logger.warning(f"[SkillTier] SkillRunner failed: {result.get('error')}")
            yield {
                "type": "skill_done",
                "content": result.get("output", "") or "技能执行完成",
                "file_path": output_path,
                "error": result.get("error"),
            }
        except Exception as e:
            logger.error(f"[SkillTier] SkillRunner error: {e}")
            yield {"type": "skill_done", "content": None, "file_path": None, "error": str(e)}
        return

    async def _run_agent(
        self,
        query: str,
        model_descriptor: ModelDescriptor,
        conversation_history: Optional[list] = None,
        system_prompt: Optional[str] = None,
        user_id: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
        mcp_tools: Optional[list] = None,
    ) -> Optional[RoutingResult]:
        _system_prompt = system_prompt or FALLBACK_AGENT_PROMPT

        if not model_descriptor.supports_tool_calling:
            logger.info(f"[Agent] Model '{model_descriptor.model_name}' does not support tool calling, falling back to LLM")
            llm = LLMFactory.create(model_descriptor)
            _sys = system_prompt or BASE_SYSTEM_PROMPT
            messages = [SystemMessage(content=_sys)]
            if conversation_history:
                messages.extend(conversation_history)
            messages.append(HumanMessage(content=query))
            try:
                response = await llm.ainvoke(messages, temperature=0.3)
                answer = response.content if hasattr(response, 'content') else str(response)
                return RoutingResult(answer=answer, sources=[], route_used="llm")
            except Exception as e:
                logger.error(f"[Agent] LLM fallback failed: {e}")
                return None

        available_tools = tm.get_enabled_tool_instances()
        all_tools = list(available_tools)
        if mcp_tools:
            all_tools.extend(mcp_tools)
            logger.info(f"[Agent] Merged {len(mcp_tools)} MCP tools")

        all_tools = self._inject_skill_tools(all_tools)
        all_tools = normalize_tools_list(all_tools)

        # ── v18.0: 工具语义召回过滤 ──
        try:
            tool_retrieval = get_tool_retrieval_service()
            relevant_names = tool_retrieval.retrieve(query)
            if relevant_names:
                filtered_tools = [t for t in all_tools if (
                    (isinstance(t, dict) and (t.get("function", t).get("name") in relevant_names)) or
                    (not isinstance(t, dict) and getattr(t, "name", "") in relevant_names)
                )]
                if filtered_tools:
                    logger.info(f"[ToolCallDebug] Checkpoint B: semantic retrieval matched tools={relevant_names}, filtered to {len(filtered_tools)} tools")
                    all_tools = filtered_tools
                else:
                    logger.info(f"[ToolCallDebug] Checkpoint B: all tools filtered out, fallback to full set")
            else:
                logger.info(f"[ToolCallDebug] Checkpoint B: retrieval returned empty, fallback to full set")
        except Exception as e:
            logger.warning(f"[ToolRetrieval] Retrieval failed (non-blocking): {e}")

        if not all_tools:
            llm = LLMFactory.create(model_descriptor)
            messages = [SystemMessage(content=_system_prompt)]
            if conversation_history:
                messages.extend(conversation_history)
            messages.append(HumanMessage(content=query))
            response = await llm.ainvoke(messages, temperature=0.3)
            answer = response.content if hasattr(response, 'content') else str(response)
            return RoutingResult(answer=answer, sources=[], route_used="llm")

        memory_context = None
        if user_id:
            try:
                memories = await memory_service.retrieve_memories(
                    user_id=user_id, query=query, top_k=5
                )
                if memories:
                    memory_context = memory_service.format_memories_for_context(memories)
            except Exception as e:
                logger.warning(f"[Memory] Retrieve failed (non-blocking): {e}")

        try:
            llm = LLMFactory.create(model_descriptor)
            llm_with_tools = llm.bind_tools(all_tools) if all_tools else llm

            # ── P1: 合并全局提示词与 ReAct 规则 ──
            _merged_prompt = _merge_react_prompt(_system_prompt)
            # 把工具列表注入 — 占位符在 REACT_SYSTEM_PROMPT_WITH_FALLBACK 中
            _tool_desc = format_tools_for_planner(all_tools)
            _tool_names = ", ".join(sorted(self._build_available_tool_names(all_tools)))
            _merged_prompt = _merged_prompt.replace("{tools_description}", _tool_desc or "（无工具）")
            _merged_prompt = _merged_prompt.replace("{available_tool_names}", _tool_names or "（无工具）")

            _system_prompt_final = await self._inject_memories(_merged_prompt, query, user_id)

            state = await self._init_agent_state(
                query=query,
                model_descriptor=model_descriptor,
                conversation_history=conversation_history,
                system_prompt=_system_prompt_final,
                all_tools=all_tools,
                llm=llm,
                llm_with_tools=llm_with_tools,
            )

            state = await asyncio.wait_for(
                self._run_agent_loop(state),
                timeout=settings.REACT_GLOBAL_TIMEOUT,
            )

            return RoutingResult(
                answer=state.final_answer,
                sources=[],
                route_used=state.route_used,
                tool_calls_detail=state.tool_results if state.tool_results else None,
                conversation_history=state.messages if state.tool_results else None,
                assistant_tool_calls=state.tool_results,
            )

        except asyncio.TimeoutError:
            logger.error(f"[Agent] Global timeout after {settings.REACT_GLOBAL_TIMEOUT}s")
            return RoutingResult(
                answer="请求处理超时，请简化您的问题或稍后重试。",
                sources=[],
                route_used="error",
            )
        except Exception as e:
            logger.error(f"[Agent] Agent tier error: {e}", exc_info=True)
            return None

    _STOP_KEYWORDS = [
        "我有足够的信息", "可以开始写", "足够的材料", "开始撰写",
        "基于以上信息", "现在可以", "enough material",
        "sufficient information", "I have enough", "ready to write",
        "已经收集到足够", "信息已经足够", "可以开始",
    ]

    async def _run_agent_stream(
        self,
        query: str,
        model_descriptor: ModelDescriptor,
        conversation_history: Optional[list] = None,
        system_prompt: Optional[str] = None,
        mcp_tools: Optional[list] = None,
        user_id: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        _system_prompt = system_prompt or FALLBACK_AGENT_PROMPT

        if not model_descriptor.supports_tool_calling:
            logger.info(f"[Agent] Model '{model_descriptor.model_name}' does not support tool calling, falling back to LLM")
            async for event in self._run_llm_stream(query, model_descriptor):
                yield event
            return

        # ── 1. 收集工具 ──
        available_tools = tm.get_enabled_tool_instances()
        all_tools = list(available_tools)
        if mcp_tools:
            all_tools.extend(mcp_tools)
            logger.info(f"[Agent] Streaming: merged {len(mcp_tools)} MCP tools")

        all_tools = self._inject_skill_tools(all_tools)
        all_tools = normalize_tools_list(all_tools)

        # ── v18.0: 流式链路接入工具语义召回过滤 ──
        retrieval_tool_names = []
        try:
            tool_retrieval = get_tool_retrieval_service()
            relevant_names = tool_retrieval.retrieve(query)
            if relevant_names:
                filtered_tools = [t for t in all_tools if (
                    (isinstance(t, dict) and (t.get("function", t).get("name") in relevant_names)) or
                    (not isinstance(t, dict) and getattr(t, "name", "") in relevant_names)
                )]
                if filtered_tools:
                    logger.info(f"[ToolCallDebug] Checkpoint B: retrieval matched={relevant_names}, final_tools={[getattr(t,'name','') if not isinstance(t,dict) else t.get('function',t).get('name','') for t in filtered_tools]}")
                    retrieval_tool_names = [getattr(t,'name','') if not isinstance(t,dict) else t.get('function',t).get('name','') for t in filtered_tools]
                    all_tools = filtered_tools
                else:
                    logger.info(f"[ToolCallDebug] Checkpoint B: all tools filtered out, using full set")
            else:
                logger.info(f"[ToolCallDebug] Checkpoint B: no relevant tools, using full set")
        except Exception as e:
            logger.warning(f"[ToolRetrieval] Streaming retrieval failed (non-blocking): {e}")

        if not all_tools:
            yield {"type": "step", "content": "无需调用工具，直接使用自身知识回答"}
            async for event in self._run_llm_stream(query, model_descriptor):
                yield event
            return

        # ── 2. 注入记忆 ──
        memory_context = None
        if user_id:
            try:
                memories = await memory_service.retrieve_memories(
                    user_id=user_id, query=query, top_k=5
                )
                if memories:
                    memory_context = memory_service.format_memories_for_context(memories)
                    logger.info(f"[Memory] Injected {len(memories)} memories")
            except Exception as e:
                logger.warning(f"[Memory] Retrieve failed (non-blocking): {e}")

        # ── P1: 全局提示词合并（流式链路）──
        _react_part = _merge_react_prompt(_system_prompt)
        _tool_desc = format_tools_for_planner(all_tools)
        _tool_names = ", ".join(sorted(self._build_available_tool_names(all_tools)))
        _react_part = _react_part.replace("{tools_description}", _tool_desc or "（无工具）")
        _react_part = _react_part.replace("{available_tool_names}", _tool_names or "（无工具）")

        _system_prompt_final = _react_part
        if memory_context:
            _system_prompt_final += f"\n\n{memory_context}"

        # ── 5. 构建 LLM ──
        llm = LLMFactory.create(model_descriptor)
        llm_with_tools = llm.bind_tools(all_tools) if all_tools else llm

        # ── 6. 初始化状态 ──
        state = await self._init_agent_state(
            query=query,
            model_descriptor=model_descriptor,
            conversation_history=conversation_history,
            system_prompt=_system_prompt_final,
            all_tools=all_tools,
            llm=llm,
            llm_with_tools=llm_with_tools,
            memory_context=memory_context,
        )

        # ── P5: 强制工具守卫 — 检查当前匹配的技能是否要求必须写文件 ──
        _required_tool = self._get_required_tool_name(query)
        if _required_tool:
            state.required_tool_name = _required_tool
            logger.info(f"[Guard] required_tool_name set to '{_required_tool}' for current session")

        # ── 事件收集器（由 _execute_single_tool / _execute_tools_parallel 写入） ──
        _pending_events: list[dict] = []
        self._event_sink = lambda e: _pending_events.append(e)

        yield {"type": "step", "content": "正在分析问题类别与所需工具..."}

        # ── 意图分类（快速 bypass）──
        intent = await self._classify_intent(query, model_descriptor)

        if intent == "DIRECT_CHAT":
            logger.info(f"[ToolCallDebug] Checkpoint A: DIRECT_CHAT bypass for query='{query[:40]}', ReAct loop SKIPPED")
            yield {"type": "step", "content": "简单对话，无需调用工具"}
            yield {"type": "step", "content": "正在生成回答..."}
            llm = LLMFactory.create(model_descriptor)
            bypass_messages = [SystemMessage(content=_system_prompt_final or FALLBACK_AGENT_PROMPT)]
            if conversation_history:
                for msg in conversation_history:
                    role = msg.get("role", "")
                    content_val = msg.get("content", "")
                    if role == "user":
                        bypass_messages.append(HumanMessage(content=content_val))
                    elif role == "assistant":
                        bypass_messages.append(AIMessage(content=content_val))
            bypass_messages.append(HumanMessage(content=query))
            try:
                async for chunk in llm.astream(bypass_messages, temperature=0.3):
                    if hasattr(chunk, 'content') and chunk.content:
                        yield {"type": "token", "content": chunk.content}
            except Exception:
                yield {"type": "token", "content": "抱歉，生成回答时出现了错误。"}
            yield {"type": "done", "conversation_id": None}
            return

        # ── v11.0: ReAct 主循环 ──
        yield {"type": "step", "content": "启动 ReAct 动态决策循环..."}

        global_timeout = settings.REACT_GLOBAL_TIMEOUT
        loop_deadline = datetime.now(timezone.utc).timestamp() + global_timeout

        while state.current_node != AgentNode.TERMINATED:
            if datetime.now(timezone.utc).timestamp() > loop_deadline:
                logger.error(f"[ReAct] Global timeout after {global_timeout}s")
                yield {"type": "step", "content": "请求处理超时，请简化您的问题或稍后重试。"}
                yield {"type": "done"}
                return

            if state.iteration >= settings.REACT_MAX_ROUNDS:
                logger.warning(f"[ReAct] Max rounds ({settings.REACT_MAX_ROUNDS}) reached, forcing Finalizer")
                yield {"type": "step", "content": f"已达到最大工具调用轮数 ({settings.REACT_MAX_ROUNDS})，强制生成最终回答"}
                plain_llm = LLMFactory.create(model_descriptor)
                final_msgs = [SystemMessage(content=FINALIZER_SYSTEM_PROMPT)]
                final_msgs.extend(state.messages)
                final_msgs.append(HumanMessage(content=f"基于以上所有已收集的信息，请对用户的原始问题给出全面回答：{state.query}"))
                try:
                    resp = await plain_llm.ainvoke(final_msgs, temperature=0.3)
                    answer = resp.content if hasattr(resp, 'content') else str(resp)
                    yield {"type": "token", "content": answer}
                except Exception:
                    yield {"type": "token", "content": "处理完成，但生成最终回答时出现错误。"}
                yield {"type": "done"}
                return

            if state.iteration >= state.max_iterations:
                logger.warning(f"[ReAct] Max iterations ({state.max_iterations}) reached")
                yield {"type": "step", "content": f"已达到最大迭代次数 ({state.max_iterations})，自动终止"}
                yield {"type": "done"}
                return

            if state.current_node == AgentNode.REACT_LOOP:
                # ── round_start 结构化事件 ──
                yield make_event(AgentEventType.ROUND_START, round=state.iteration + 1,
                                 message=f"ReAct 第 {state.iteration + 1} 轮")

                # ── 排出事件收集器中的结构化事件（tool_start / tool_end） ──
                while _pending_events:
                    yield _pending_events.pop(0)

                current_round = state.iteration + 1
                if current_round >= 5 and current_round <= settings.REACT_MAX_ROUNDS:
                    last_msg = state.messages[-1] if state.messages else None
                    if isinstance(last_msg, HumanMessage):
                        if current_round >= 7:
                            hint = f"\n\n【警告】已进行 {current_round}/{settings.REACT_MAX_ROUNDS} 轮工具调用，即将达到上限。除非缺少关键信息，否则请直接输出最终答案，禁止继续调用工具。"
                        else:
                            hint = f"\n\n【进度提示】已进行 {current_round}/{settings.REACT_MAX_ROUNDS} 轮工具调用。如果你认为已有足够信息完成任务，请直接生成最终回答。"
                        last_msg.content += hint

                try:
                    remaining = max(10.0, loop_deadline - datetime.now(timezone.utc).timestamp())
                    state = await asyncio.wait_for(
                        self._react_step(state),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    logger.error("[ReAct] Step timed out")
                    yield {"type": "step", "content": "⚠️ 决策超时，强制结束"}
                    yield {"type": "done"}
                    return
                except Exception as e:
                    logger.error(f"[ReAct] Step error: {e}")
                    yield {"type": "step", "content": "处理过程出现错误，正在生成最后回答..."}
                    try:
                        async for chunk in llm.astream(
                            [SystemMessage(content=_system_prompt_final), HumanMessage(content=query)],
                            temperature=0.3,
                        ):
                            if hasattr(chunk, 'content') and chunk.content:
                                yield {"type": "token", "content": chunk.content}
                    except Exception:
                        yield {"type": "token", "content": "抱歉，处理您的问题时出现了错误。"}
                    yield {"type": "done"}
                    return

                # ── 再次排出事件（第 N 轮工具执行完后） ──
                while _pending_events:
                    yield _pending_events.pop(0)

                if state.current_node == AgentNode.TERMINATED:
                    yield {"type": "token", "content": state.final_answer}
                    yield {"type": "done", "final_answer": state.final_answer, "route_used": state.route_used}
                    return
                elif state.current_node == AgentNode.FINALIZER:
                    await self._stream_finalizer(state, model_descriptor)
                    return
                else:
                    yield {"type": "step", "content": f"✓ 第 {state.iteration} 轮完成"}

            elif state.current_node == AgentNode.FINALIZER:
                await self._stream_finalizer(state, model_descriptor)
                return

            elif state.current_node == AgentNode.TERMINATED:
                yield {"type": "token", "content": state.final_answer}
                yield {"type": "done", "final_answer": state.final_answer, "route_used": state.route_used}
                return

            else:
                logger.error(f"[ReAct] Unknown state: {state.current_node}")
                yield {"type": "done"}
                return

    async def route(
        self,
        query: str,
        model_descriptor: ModelDescriptor,
        kb_collection_name: Optional[str] = None,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
        embedding_model: Optional[str] = None,
        embedding_provider: Optional[str] = None,
        conversation_history: Optional[list] = None,
        system_prompt: Optional[str] = None,
        mcp_tools: Optional[list] = None,
        user_id: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
    ) -> RoutingResult:
        if kb_collection_name:
            try:
                result = await self._run_rag(
                    query=query,
                    collection_name=kb_collection_name,
                    top_k_parents=top_k or DEFAULT_TOP_K_PARENTS,
                    top_k_children=DEFAULT_TOP_K_CHILDREN,
                    model_descriptor=model_descriptor,
                    embedding_model=embedding_model,
                    embedding_provider=embedding_provider,
                )
                if result:
                    return result
            except Exception as e:
                logger.warning(f"RAG tier failed: {e}")

        if not self._should_route_to_agent(query):
            try:
                skill_result = await self._run_skill_tier_nonstream(
                    query=query,
                    model_descriptor=model_descriptor,
                    system_prompt=system_prompt,
                    conversation_history=conversation_history,
                )
                if skill_result:
                    return skill_result
            except Exception as e:
                logger.warning(f"Skill tier failed (non-streaming): {e}")

        try:
            result = await self._run_agent(
                query=query,
                model_descriptor=model_descriptor,
                conversation_history=conversation_history,
                system_prompt=system_prompt,
                mcp_tools=mcp_tools,
                user_id=user_id,
                db_session=db_session,
            )
            if result and result.answer:
                return result
        except Exception as e:
            logger.warning(f"Agent tier failed: {e}")

        try:
            return await self._run_llm(query, model_descriptor)
        except Exception as e:
            logger.error(f"All tiers failed: {e}")
            return RoutingResult(
                answer="抱歉，处理您的问题时出现了错误。请稍后重试。",
                sources=[],
                route_used="error",
            )

    async def route_stream(
        self,
        query: str,
        model_descriptor: ModelDescriptor,
        kb_collection_name: Optional[str] = None,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
        embedding_model: Optional[str] = None,
        embedding_provider: Optional[str] = None,
        conversation_history: Optional[list] = None,
        system_prompt: Optional[str] = None,
        mcp_tools: Optional[list] = None,
        user_id: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        top_k_parents = top_k or DEFAULT_TOP_K_PARENTS
        top_k_children = DEFAULT_TOP_K_CHILDREN

        if kb_collection_name:
            yield {"type": "step", "content": "正在检索知识库..."}
            try:
                result = await self._run_rag(
                    query=query,
                    collection_name=kb_collection_name,
                    top_k_parents=top_k_parents,
                    top_k_children=top_k_children,
                    model_descriptor=model_descriptor,
                    embedding_model=embedding_model,
                    embedding_provider=embedding_provider,
                )
                if result and result.sources:
                    first_source = result.sources[0]
                    doc_filename = first_source.get("filename", "未知文档")
                    yield {"type": "step", "content": f"检索到知识库文档: {doc_filename}"}
                    yield {"type": "meta", "route": result.route_used, "sources": result.sources}
                    yield {"type": "step", "content": "正在生成回答..."}
                    llm = LLMFactory.create(model_descriptor)
                    context = result.answer
                    stream_messages = [
                        SystemMessage(content=BASE_SYSTEM_PROMPT),
                        HumanMessage(content=f"请根据以下知识库文档内容回答用户的问题。\n\n## 知识库参考文档\n{context}\n\n## 用户问题\n{query}\n\n请基于以上参考文档回答，如果文档不足以回答请说明。"),
                    ]
                    try:
                        async for chunk in llm.astream(stream_messages, temperature=0.3):
                            if hasattr(chunk, 'content') and chunk.content:
                                yield {"type": "token", "content": chunk.content}
                    except Exception:
                        _cs = 5
                        for i in range(0, len(result.answer), _cs):
                            yield {"type": "token", "content": result.answer[i:i+_cs]}
                    yield {"type": "done"}
                    return
                else:
                    yield {"type": "step", "content": "未检索到相关信息"}
            except Exception as e:
                logger.warning(f"RAG tier failed in stream: {e}")
                yield {"type": "step", "content": "知识库检索失败，转入其他方式回答"}

        # ── 检查匹配的技能是否需要工具调用（流式路径） ──
        if self._should_route_to_agent(query):
            yield {"type": "step", "content": "检测到技能需要工具调用，直接启动 ReAct 动态决策循环..."}
            async for event in self._run_agent_stream(
                query=query,
                model_descriptor=model_descriptor,
                conversation_history=conversation_history,
                system_prompt=system_prompt,
                mcp_tools=mcp_tools,
                user_id=user_id,
            ):
                yield event
            return

        try:
            skill_hit = False
            skill_result_content = None
            skill_result_file = None
            async for event in self._run_skill_tier_stream(
                query=query,
                model_descriptor=model_descriptor,
                system_prompt=system_prompt,
            ):
                skill_hit = True
                if event["type"] == "skill_done":
                    skill_result_content = event.get("content")
                    skill_result_file = event.get("file_path")
                    continue
                yield event
            if skill_hit:
                if skill_result_content:
                    yield {"type": "step", "content": "正在生成回答..."}
                    llm = LLMFactory.create(model_descriptor)
                    slug = await self._generate_english_slug(query, model_descriptor)
                    user_msg = f"""You are BaseAgent. The formatting skill has completed successfully. 

Formatting result has been saved to: {skill_result_file or f'{slug}.md'}

Now tell the user what happened. Give them a brief friendly message that:
1. The file has been formatted
2. Where it was saved
3. Then show them the formatted content below

Here is the formatted content to show:

```
{skill_result_content}
```"""
                    try:
                        async for chunk in llm.astream(
                            [SystemMessage(content=BASE_SYSTEM_PROMPT), HumanMessage(content=user_msg)],
                            temperature=0.3,
                        ):
                            if hasattr(chunk, 'content') and chunk.content:
                                yield {"type": "token", "content": chunk.content}
                    except Exception:
                        yield {"type": "token", "content": f"格式化完成，已保存到 {skill_result_file}。\n\n{skill_result_content}"}
                else:
                    yield {"type": "step", "content": "技能执行失败"}
                yield {"type": "done"}
                return
        except Exception as e:
            logger.warning(f"Skill tier failed (streaming): {e}")

        try:
            async for event in self._run_agent_stream(
                query=query,
                model_descriptor=model_descriptor,
                conversation_history=conversation_history,
                system_prompt=system_prompt,
                mcp_tools=mcp_tools,
                user_id=user_id,
            ):
                yield event
            return
        except Exception as e:
            logger.warning(f"Agent tier failed in stream: {e}")

        yield {"type": "step", "content": "正在生成回答..."}
        async for event in self._run_llm_stream(query, model_descriptor):
            yield event

    async def _run_rag(self, query: str, collection_name: str, top_k_parents: int,
                       top_k_children: int, model_descriptor: ModelDescriptor,
                       embedding_model: Optional[str] = None,
                       embedding_provider: Optional[str] = None) -> Optional[RoutingResult]:
        import logging as _lg
        _lg.getLogger(__name__).warning(f"_RAGRAG: START query={query[:30]} collection={collection_name} model={embedding_model} provider={embedding_provider}")
        children = rag_service.search_child_chunks(
            query=query, collection_name=collection_name, top_k=top_k_children,
            embedding_model=embedding_model, embedding_provider=embedding_provider,
        )
        _lg.getLogger(__name__).warning(f"_RAGRAG: children={len(children)}")
        if not children:
            _lg.getLogger(__name__).warning(f"RAG: No child chunks found in '{collection_name}' for query")
            return None

        parents = rag_service.resolve_parents(collection_name, children)
        if not parents:
            seen_texts = set()
            legacy_parents = []
            for child in children:
                txt = child.get("text", "")
                if txt and txt not in seen_texts:
                    seen_texts.add(txt)
                    child_score = child.get("score", 0)
                    legacy_parents.append({
                        "content": txt,
                        "max_child_score": child_score,
                        "parent_id": child.get("parent_id", ""),
                        "rerank_score": child_score,
                    })
                    if len(legacy_parents) >= top_k_parents * 3:
                        break
            if legacy_parents:
                try:
                    reranker = self._get_reranker()
                    reranked_parents = reranker.rerank(query=query, results=legacy_parents, top_k=top_k_parents)
                except Exception:
                    legacy_parents.sort(key=lambda x: x.get("max_child_score", 0), reverse=True)
                    reranked_parents = legacy_parents[:top_k_parents]
            else:
                reranked_parents = []
        else:
            try:
                reranker = self._get_reranker()
                reranked_parents = reranker.rerank(query=query, results=parents, top_k=top_k_parents)
            except Exception as e:
                logger.warning(f"RAG: Cross-Encoder rerank failed, using top parents by score: {e}")
                for p in parents:
                    p["rerank_score"] = p.get("max_child_score", 0.0)
                parents.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
                reranked_parents = parents[:top_k_parents]

        if not reranked_parents:
            return None

        best_score = max((p.get("rerank_score", 0.0) for p in reranked_parents), default=0.0)
        if best_score < MIN_RERANK_SCORE:
            return None

        context_parts = []
        sources = []
        seen_filenames = set()
        for parent in reranked_parents:
            content = parent.get("content", "")
            if not content:
                continue
            metadata = {}
            parent_id = parent.get("parent_id", "")
            matching_child = next((c for c in children if c.get("parent_id") == parent_id), None)
            if matching_child:
                metadata = matching_child.get("metadata", {})
            filename = SmartRouter._clean_filename(
                metadata.get("filename", metadata.get("source", "未知文档"))
            )
            if filename not in seen_filenames:
                seen_filenames.add(filename)
            context_parts.append(f"[来源: {filename}]\n{content}")
            sources.append({
                "parent_id": parent_id,
                "filename": filename,
                "content": content,
                "rerank_score": round(parent.get("rerank_score", 0), 4),
                "max_child_score": round(parent.get("max_child_score", 0), 4),
            })

        if not context_parts:
            return None

        context = "\n\n---\n\n".join(context_parts)
        llm = LLMFactory.create(model_descriptor)
        user_msg = f"""请根据以下知识库文档内容回答用户的问题。
## 知识库参考文档
{context}
## 用户问题
{query}
请基于以上参考文档回答，如果文档不足以回答请说明。"""
        messages = [SystemMessage(content=BASE_SYSTEM_PROMPT), HumanMessage(content=user_msg)]
        response = await llm.ainvoke(messages, temperature=0.3)
        answer = response.content if hasattr(response, 'content') else str(response)
        return RoutingResult(answer=answer, sources=sources, route_used="rag")

    async def _run_llm(self, query: str, model_descriptor: ModelDescriptor) -> RoutingResult:
        llm = LLMFactory.create(model_descriptor)
        messages = [SystemMessage(content=BASE_SYSTEM_PROMPT), HumanMessage(content=query)]
        response = await llm.ainvoke(messages, temperature=0.3)
        answer = response.content if hasattr(response, 'content') else str(response)
        return RoutingResult(answer=answer, sources=[], route_used="llm")

    async def _run_llm_stream(self, query: str, model_descriptor: ModelDescriptor) -> AsyncIterator[dict]:
        llm = LLMFactory.create(model_descriptor)
        messages = [SystemMessage(content=BASE_SYSTEM_PROMPT), HumanMessage(content=query)]
        try:
            async for chunk in llm.astream(messages, temperature=0.3):
                if hasattr(chunk, 'content') and chunk.content:
                    yield {"type": "token", "content": chunk.content}
        except Exception:
            response = await llm.ainvoke(messages, temperature=0.3)
            answer = response.content if hasattr(response, 'content') else str(response)
            yield {"type": "token", "content": answer}
        yield {"type": "done", "conversation_id": None}

    async def _stream_finalizer(self, state: AgentState, model_descriptor: ModelDescriptor) -> AsyncIterator[dict]:
        if state.output_file_path:
            yield {"type": "step", "content": f"✅ 文章已成功生成并保存到 {state.output_file_path}（本地映射: C:\\Users\\YA\\Desktop\\BaseAgent\\output\\{os.path.basename(state.output_file_path)}）"}
        yield {"type": "step", "content": "正在生成最终回答..."}

        valid_tool_call_ids = set()
        for msg in state.messages:
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    tid = tc.get('id', '') if isinstance(tc, dict) else getattr(tc, 'id', '')
                    if tid:
                        valid_tool_call_ids.add(tid)

        final_messages = [SystemMessage(content=FINALIZER_SYSTEM_PROMPT)]
        for msg in state.messages:
            if isinstance(msg, ToolMessage) and msg.tool_call_id not in valid_tool_call_ids:
                continue
            final_messages.append(msg)
        final_messages.append(HumanMessage(
            content=f"基于以上所有已收集的信息，请对用户的原始问题给出全面回答：{state.query}"
        ))

        llm_final = LLMFactory.create(model_descriptor)
        full_answer_parts = []
        try:
            async for chunk in llm_final.astream(final_messages, temperature=0.3):
                if hasattr(chunk, 'content') and chunk.content:
                    full_answer_parts.append(chunk.content)
                    yield {"type": "token", "content": chunk.content}
        except Exception as e:
            logger.error(f"[Finalizer] Stream failed: {e}")
            try:
                response = await llm_final.ainvoke(final_messages, temperature=0.3)
                fallback_text = response.content if hasattr(response, 'content') else str(response)
                full_answer_parts.append(fallback_text)
                yield {"type": "token", "content": fallback_text}
            except Exception:
                yield {"type": "token", "content": "抱歉，生成最终回答时出现了错误。"}

        state.final_answer = "".join(full_answer_parts)
        state.route_used = "tools" if state.tool_results else "llm"
        state.current_node = AgentNode.TERMINATED
        yield {"type": "done", "final_answer": state.final_answer, "route_used": state.route_used}

    @staticmethod
    def _clean_filename(raw: str) -> str:
        if not raw:
            return "未知文档"
        basename = raw.split("/")[-1].split("\\")[-1]
        if "_" in basename:
            parts = basename.split("_", 1)
            first_part = parts[0].replace("-", "")
            if len(first_part) >= 16 and all(c in "0123456789abcdefABCDEF" for c in first_part):
                return parts[1]
        return basename


# Singleton
smart_router = SmartRouter()