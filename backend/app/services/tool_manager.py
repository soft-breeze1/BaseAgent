# Tool Manager - Manages tools available to the Agent
import json
import math
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Callable, Any
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool, tool
from langchain_core.tools import StructuredTool

from app.core.config import get_settings

settings = get_settings()


@dataclass
class ToolDescriptor:
    """Describes a registered tool that the agent can call."""
    name: str
    display_name: str
    description: str
    tool_type: str  # builtin, api
    tool_instance: Optional[BaseTool] = None
    config: dict = field(default_factory=dict)
    is_enabled: bool = True


class ToolManager:
    """
    Manages tools available to the Agent.
    
    Tools are callable functions that the Agent can invoke autonomously
    (e.g., web search via Tavily, current time, etc.).
    The Agent decides which tool to call based on the user's question.
    """

    def __init__(self):
        self._tools: dict[str, ToolDescriptor] = {}
        self._config_path = "/app/data/tool_configs.json"
        self._register_builtin_tools()
        self._load_persisted_configs()

    # ---- Built-in tools ----

    def _register_builtin_tools(self):
        """Register built-in tools."""

        # Tavily Web Search tool
        @tool
        def tavily_web_search(query: str) -> str:
            """Search the web for current information. Use this when you need up-to-date facts, news, or information beyond your knowledge cutoff."""
            import requests
            import logging
            _logger = logging.getLogger(__name__)
            # Read API key from config (user can set it in UI)
            tool_desc = self._tools.get("tavily_web_search")
            api_key = ""
            if tool_desc and "api_key" in tool_desc.config:
                api_key = tool_desc.config["api_key"]
            if not api_key:
                api_key = settings.TAVILY_API_KEY
            if not api_key:
                return "Tavily 搜索未配置 API Key，请在 Tools 管理中设置。"
            try:
                # 优化参数：返回10条结果、深度检索、优先最新内容
                _logger.info(f"[Tavily Search] Query: {query[:200]}")
                resp = requests.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "search_depth": "advanced",  # 深度检索，获取更全面结果
                        "max_results": 10,            # 返回10条结果，保证信息样本充足
                        "include_answer": True,       # 包含综合摘要
                        "include_raw_content": False,  # 不包含原始HTML
                    },
                    timeout=30,  # 超时放宽到30秒
                )
                if resp.status_code != 200:
                    _logger.warning(f"[Tavily Search] HTTP {resp.status_code}: {resp.text[:200]}")
                    return f"联网搜索服务暂时不可用（状态码 {resp.status_code}），请稍后重试。"

                data = resp.json()
                results = data.get("results", [])
                # 如果有综合摘要，优先输出作为总览
                answer = data.get("answer", "")
                
                if not results:
                    _logger.warning(f"[Tavily Search] No results for query: {query[:100]}")
                    return "未找到相关搜索结果。请尝试更换搜索关键词或补充更多描述。"

                _logger.info(f"[Tavily Search] Got {len(results)} results for query: {query[:100]}")

                # 构建完整搜索结果输出，确保所有信息都透传给Agent
                output_parts = []
                if answer:
                    output_parts.append(f"【搜索摘要】{answer}")
                    output_parts.append("---")
                
                for i, r in enumerate(results, 1):
                    title = r.get('title', 'N/A')
                    content = r.get('content', '')
                    url = r.get('url', '')
                    score = r.get('score', '')
                    output_parts.append(
                        f"[结果 {i}] {title}\n"
                        f"{content}\n"
                        f"来源: {url}"
                    )
                
                return "\n\n".join(output_parts)
            except requests.Timeout:
                _logger.error(f"[Tavily Search] Timeout after 30s for query: {query[:100]}")
                return "联网搜索请求超时，请稍后重试或简化查询条件。"
            except requests.ConnectionError:
                _logger.error(f"[Tavily Search] Connection error for query: {query[:100]}")
                return "联网搜索服务无法连接，请检查网络后重试。"
            except Exception as e:
                _logger.error(f"[Tavily Search] Error: {e}", exc_info=True)
                return f"联网搜索异常：{str(e)}"

        self.register_tool(ToolDescriptor(
            name="tavily_web_search",
            display_name="Tavily",
            description="联网搜索，获取最新信息（如新闻、实时数据等），需要配置 API Key",
            tool_type="api",
            tool_instance=tavily_web_search,
            is_enabled=True,
            config={"api_key": settings.TAVILY_API_KEY or ""},
        ))

        # Current time tool
        @tool
        def get_current_time(timezone_offset: str = "+8") -> str:
            """Get the current date and time. Input: optional UTC offset like '+8' for Asia/Shanghai. Output: formatted datetime string."""
            try:
                offset_hours = int(timezone_offset)
                tz = timezone(timedelta(hours=offset_hours))
                now = datetime.now(tz)
                return now.strftime("%Y-%m-%d %H:%M:%S %Z")
            except Exception:
                return datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

        self.register_tool(ToolDescriptor(
            name="get_current_time",
            display_name="当前时间",
            description="获取当前日期和时间，支持时区偏移（默认东八区）",
            tool_type="builtin",
            tool_instance=get_current_time,
            is_enabled=True,
        ))

    # ---- Tool registration & management ----

    def register_tool(self, tool_descriptor: ToolDescriptor) -> None:
        """Register a new tool. Overwrites if name already exists."""
        self._tools[tool_descriptor.name] = tool_descriptor

    def unregister_tool(self, name: str) -> bool:
        """Remove a tool by name."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get_tool(self, name: str) -> Optional[ToolDescriptor]:
        return self._tools.get(name)

    def list_tools(self) -> List[ToolDescriptor]:
        return list(self._tools.values())

    def list_enabled_tools(self) -> List[ToolDescriptor]:
        return [t for t in self._tools.values() if t.is_enabled]

    def get_enabled_tool_instances(self) -> List[BaseTool]:
        """Return LangChain tool instances for all enabled tools."""
        return [t.tool_instance for t in self.list_enabled_tools() if t.tool_instance is not None]

    def enable_tool(self, name: str) -> bool:
        tool = self._tools.get(name)
        if tool:
            tool.is_enabled = True
            return True
        return False

    def disable_tool(self, name: str) -> bool:
        tool = self._tools.get(name)
        if tool:
            tool.is_enabled = False
            return True
        return False

    def update_tool_config(self, name: str, config: dict) -> bool:
        tool = self._tools.get(name)
        if tool:
            tool.config.update(config)
            self._save_configs()
            return True
        return False

    # ---- Config persistence (JSON file on Docker volume) ----

    def _save_configs(self):
        """Persist all tool configs to JSON file so they survive container restarts."""
        data = {}
        for name, tool in self._tools.items():
            if tool.config:
                data[name] = {"config": tool.config, "is_enabled": tool.is_enabled}
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # Non-critical

    def _load_persisted_configs(self):
        """Load tool configs from JSON file (if exists)."""
        import os
        if not os.path.exists(self._config_path):
            return
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name, values in data.items():
                tool = self._tools.get(name)
                if tool:
                    if "config" in values:
                        tool.config.update(values["config"])
                    if "is_enabled" in values:
                        tool.is_enabled = values["is_enabled"]
        except Exception:
            pass

    async def execute_tool(self, name: str, arguments: dict) -> str:
        """Execute a tool by name with the given arguments.
        
        Runs the synchronous tool call in a thread pool to avoid blocking
        the asyncio event loop, which would freeze all other API requests.
        """
        tool_desc = self._tools.get(name)
        if not tool_desc or not tool_desc.is_enabled:
            return f"Error: Tool '{name}' not found or disabled"
        
        if tool_desc.tool_instance:
            try:
                import asyncio
                # Run the synchronous invoke in a thread to prevent
                # blocking the entire event loop during long tool calls
                result = await asyncio.to_thread(tool_desc.tool_instance.invoke, arguments)
                return str(result)
            except Exception as e:
                return f"Error executing tool '{name}': {str(e)}"
        
        return f"Error: Tool '{name}' has no executable instance"


# Singleton
tool_manager = ToolManager()