"""
SkillRunner — 单轮 LLM + 工具调用，执行完扫描输出目录检测产物
"""
import asyncio
import json
import os
import logging
from typing import Optional, List, Dict

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

from app.core.config import get_settings
from app.services.llm_service import LLMFactory, ModelDescriptor
from app.services.tool_manager import tool_manager as tm
from app.tools.dict_tool_adapter import normalize_tools_list
from app.core.mcp.discovery import get_tool_route, is_mcp_tool
from app.core.mcp.process_manager import process_manager
from app.core.mcp.executor import MCPHttpExecutor
from typing import Protocol

logger = logging.getLogger(__name__)
settings = get_settings()

DEFAULT_MAX_STEPS = 25
DEFAULT_TIMEOUT_SECONDS = 300

_BLOCKED_ESCAPE_TOOLS = {
    "execute_bash_command", "python_executor", "read_local_file", "document_reader",
}


class SkillInterceptor(Protocol):
    async def intercept_async(self, tool_name: str, tool_args: dict, user_query: str = "") -> Optional[tuple]: ...
    def is_skill_tool(self, tool_name: str) -> bool: ...


class SkillResult:
    def __init__(self, success: bool, output: str = "",
                 artifacts: Optional[List[dict]] = None,
                 stats: Optional[dict] = None,
                 error: Optional[dict] = None):
        self.success = success
        self.output = output
        self.artifacts = artifacts or []
        self.stats = stats or {"total_steps": 0, "tools_called": {}, "phases_completed": 0}
        self.error = error

    def to_json(self) -> str:
        return json.dumps({
            "success": self.success,
            "output": self.output[:2000],
            "artifacts": self.artifacts,
            "stats": self.stats,
            "error": self.error,
        }, ensure_ascii=False)


# 产物检测目录列表（按优先级排序）
_OUTPUT_DIRS = ["/app/output", "/app/workspace/output", "/app/data/output"]


def _collect_artifacts() -> List[dict]:
    """扫描所有可能的输出目录，收集 .md 产物"""
    seen = set()
    artifacts = []
    for d in _OUTPUT_DIRS:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.endswith(".md"):
                continue
            fp = os.path.join(d, f)
            if fp in seen:
                continue
            seen.add(fp)
            artifacts.append({
                "type": "file", "path": fp, "name": f,
                "size_bytes": os.path.getsize(fp), "description": "产物",
            })
    return artifacts


class SkillRunner:
    def __init__(self, skill_body: str, skill_name: str, display_name: str,
                 allowed_tools: Optional[List[str]] = None,
                 max_steps: int = DEFAULT_MAX_STEPS,
                 timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
                 user_query: str = "",
                 model_descriptor: Optional[ModelDescriptor] = None,
                 execution_interceptor: Optional[SkillInterceptor] = None):
        self._skill_body = skill_body
        self._skill_name = skill_name
        self._display_name = display_name
        self._allowed_tools = allowed_tools
        self._max_steps = max_steps
        self._timeout_seconds = timeout_seconds
        self._user_query = user_query
        self._model_descriptor = model_descriptor
        self._execution_interceptor = execution_interceptor

    async def run(self) -> str:
        start_time = __import__('time').time()
        try:
            result = await self._run_with_timeout(start_time)
        except asyncio.TimeoutError:
            elapsed = __import__('time').time() - start_time
            artifacts = _collect_artifacts()
            result = SkillResult(
                success=bool(artifacts),
                output="超时" if not artifacts else "超时但已检测到产物",
                artifacts=artifacts,
                error=None if artifacts else {"type": "timeout", "message": f"超时({elapsed:.0f}s)"},
            )
        except Exception as e:
            logger.error(f"[SkillRunner] Error: {e}", exc_info=True)
            result = SkillResult(success=False, error={"type": "runtime", "message": str(e)[:200]})
        return result.to_json()

    async def _run_with_timeout(self, start_time: float) -> SkillResult:
        all_tools = self._collect_tools()
        if not all_tools:
            # 无工具可用时直接返回
            return SkillResult(success=True, output="完成（无可用工具）", artifacts=_collect_artifacts())

        model_desc = self._model_descriptor or ModelDescriptor(
            provider=settings.DEFAULT_LLM_PROVIDER or "openai",
            model_name=settings.DEFAULT_LLM_MODEL or "gpt-4o",
            supports_tool_calling=True,
        )
        llm = LLMFactory.create(model_desc)
        llm_with_tools = llm.bind_tools(all_tools) if all_tools else llm

        # 整份 SKILL.md 作为 System Prompt，不拆分 Phase
        sp = (
            f"你正在执行技能「{self._display_name}」。\n\n"
            f"{self._skill_body}\n\n"
            f"## 执行规则\n"
            f"1. 按上述规范逐步执行，可调用工具获取信息或保存结果\n"
            f"2. 完成后直接输出最终结果，无需额外说明\n"
        )
        messages = [SystemMessage(content=sp), HumanMessage(content=self._user_query)]

        tool_counts: Dict[str, int] = {}
        total_steps = 0

        while total_steps < self._max_steps:
            elapsed = __import__('time').time() - start_time
            if elapsed > self._timeout_seconds:
                raise asyncio.TimeoutError()

            total_steps += 1
            try:
                response = await llm_with_tools.ainvoke(messages, temperature=0.3)
            except Exception as e:
                logger.error(f"[SkillRunner] LLM failed: {e}")
                break

            content = getattr(response, 'content', '') or ''
            tool_calls = getattr(response, 'tool_calls', None) or []

            if not tool_calls:
                # LLM 决定停止调工具
                break

            for tc in tool_calls:
                tc_name = tc.get('name', '') if isinstance(tc, dict) else getattr(tc, 'name', '')
                tool_counts[tc_name] = tool_counts.get(tc_name, 0) + 1

            tool_results = await self._execute_tool_calls(tool_calls)

            normalized_tcs = []
            for tc in tool_calls:
                if isinstance(tc, dict):
                    normalized_tcs.append(tc)
                else:
                    normalized_tcs.append({
                        "id": getattr(tc, 'id', ''),
                        "type": "function",
                        "function": {"name": getattr(tc, 'name', ''), "arguments": getattr(tc, 'args', '{}')},
                    })
            messages.append(AIMessage(content=content, tool_calls=normalized_tcs))
            for tr in tool_results:
                messages.append(ToolMessage(content=tr.get("content", ""), tool_call_id=tr.get("tool_call_id", "")))

        artifacts = _collect_artifacts()
        elapsed = __import__('time').time() - start_time
        return SkillResult(
            success=bool(artifacts) or total_steps > 0,
            output=content if content else f"完成 {len(artifacts)} 个产物",
            artifacts=artifacts,
            stats={
                "total_steps": total_steps,
                "tools_called": dict(sorted(tool_counts.items())),
                "elapsed_seconds": round(elapsed, 1),
            },
            error=None if artifacts else {"type": "no_output", "message": "未检测到产物文件"},
        )

    def _collect_tools(self) -> list:
        available = list(tm.get_enabled_tool_instances())
        if self._allowed_tools:
            allowed = set(self._allowed_tools)
            available = [t for t in available if getattr(t, "name", "") in allowed]
        else:
            available = [t for t in available if getattr(t, "name", "") not in _BLOCKED_ESCAPE_TOOLS]
        return normalize_tools_list(available)

    async def _execute_tool_calls(self, tool_calls: list) -> List[Dict[str, str]]:
        results = []
        for tc in tool_calls:
            tc_name = tc.get('name', '') if isinstance(tc, dict) else getattr(tc, 'name', '')
            tc_id = tc.get('id', '') if isinstance(tc, dict) else getattr(tc, 'id', '')
            tc_args_str = tc.get('args', '{}') if isinstance(tc, dict) else getattr(tc, 'args', '{}')
            tc_args = json.loads(tc_args_str) if isinstance(tc_args_str, str) else tc_args_str

            if self._execution_interceptor and self._execution_interceptor.is_skill_tool(tc_name):
                intercept = await self._execution_interceptor.intercept_async(tc_name, tc_args, self._user_query)
                if intercept:
                    _, rs, _ = intercept
                    results.append({"tool_call_id": tc_id or f"auto_{tc_name}", "content": rs, "tool_name": tc_name})
                    continue

            try:
                if is_mcp_tool(tc_name):
                    route = get_tool_route(tc_name)
                    if route:
                        if route["executor_type"] == "stdio":
                            tr = await process_manager.call_tool(route["server_name"], route["raw_tool_name"], tc_args)
                        elif route["executor_type"] == "http":
                            from sqlalchemy import select
                            from app.core.database import get_db
                            from app.models.mcp_server import MCPServer
                            he = None
                            async for db_session in get_db():
                                r = await db_session.execute(select(MCPServer).where(MCPServer.name == route["server_name"]))
                                s = r.scalar_one_or_none()
                                if s and s.config:
                                    cfg = json.loads(s.config)
                                    if cfg.get("url"):
                                        he = MCPHttpExecutor(base_url=cfg["url"])
                            if he:
                                rd = await he.call_tool(route["raw_tool_name"], tc_args)
                                tr = rd.get("content", "") if rd.get("success") else json.dumps({"error": rd.get("error")})
                            else:
                                tr = json.dumps({"error": "no URL"})
                        else:
                            tr = json.dumps({"error": f"unknown executor: {route['executor_type']}"})
                    else:
                        tr = json.dumps({"error": f"route not found: {tc_name}"})
                else:
                    tr = await tm.execute_tool(tc_name, tc_args)
                rs = str(tr) if tr else ""
                if len(rs) > 8000:
                    rs = rs[:8000] + "\n\n[Truncated]"
                results.append({"tool_call_id": tc_id or f"auto_{tc_name}", "content": rs, "tool_name": tc_name})
            except Exception as e:
                logger.error(f"[SkillRunner] Tool '{tc_name}' failed: {e}")
                results.append({"tool_call_id": tc_id or f"auto_{tc_name}", "content": f"Error: {str(e)}", "tool_name": tc_name})
        return results