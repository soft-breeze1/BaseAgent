# LLM Service - Dynamic model loading and tool calling
import json
from typing import Any, AsyncIterator, Optional
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from langchain_community.chat_models import ChatZhipuAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.tools import BaseTool, tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.core.config import get_settings

settings = get_settings()


# ── 已知不支持或 tool calling 有问题的模型关键词 ──
_MODELS_WITHOUT_TOOL_CALLING: frozenset = frozenset({
    # Ollama 常见不支持 function calling 的模型
    "qwen2.5:7b", "qwen2.5:14b", "qwen2.5:72b",
    "llama3.2", "llama3.1", "llama3",
    "mistral", "mixtral",
    "deepseek-r1", "deepseek-v2",
    # 不支持 tool calling 的系列前缀
    "qwen2.5:",
    "gemma", "gemma2",
    "phi3", "phi-3",
    "codellama",
})

_PROVIDERS_NO_TOOL_CALLING: frozenset = frozenset({
    "zhipu",           # glm-4 系列部分版本不支持 bind_tools
})


def _check_tool_calling_support(provider: str, model_name: str) -> bool:
    """根据 provider 和 model_name 判断是否支持 function calling / tool calling。"""
    p = provider.lower().strip()
    m = model_name.lower().strip()

    # Provider 黑名单
    if p in _PROVIDERS_NO_TOOL_CALLING:
        return False

    # Ollama 模型按名称判断
    if p == "ollama":
        # 白名单：Ollama 上明确支持 tool calling 的模型
        # qwen2.5 系列一般不支持（除非指定支持 tool calling 的版本）
        for name in _MODELS_WITHOUT_TOOL_CALLING:
            if m == name or m.startswith(name.rstrip(":")):
                return False
        # Ollama 其他模型默认回退为不支持（安全策略）
        return False

    # OpenAI 兼容系列：deepseek, moonshot, together, alibaba 默认支持
    return True


@dataclass
class ModelDescriptor:
    """Describes a loaded model configuration."""
    provider: str
    model_name: str
    api_key: str
    api_base: Optional[str] = None
    extra_config: Optional[dict] = None
    supports_tool_calling: bool = True

    def __post_init__(self):
        """自动检测 tool calling 支持能力。"""
        self.supports_tool_calling = _check_tool_calling_support(self.provider, self.model_name)


class LLMFactory:
    """Factory to create LangChain chat model instances from config descriptors."""

    @staticmethod
    def create(model: ModelDescriptor) -> ChatOpenAI | ChatZhipuAI:
        provider = model.provider.lower()

        if provider in ("openai", "deepseek", "moonshot", "qwen", "together", "ollama", "alibaba"):
            base_url = model.api_base
            if not base_url:
                base_url_map = {
                    "openai": None,  # uses default
                    "deepseek": "https://api.deepseek.com/v1",
                    "moonshot": "https://api.moonshot.cn/v1",
                    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "alibaba": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "together": "https://api.together.xyz/v1",
                }
                base_url = base_url_map.get(provider)

            return ChatOpenAI(
                model=model.model_name,
                api_key=model.api_key,
                base_url=base_url,
                temperature=0.7,
                streaming=True,
            )

        elif provider == "zhipu":
            return ChatZhipuAI(
                model=model.model_name,
                api_key=model.api_key,
                temperature=0.7,
            )

        else:
            # Fallback: treat unknown providers as OpenAI-compatible
            return ChatOpenAI(
                model=model.model_name,
                api_key=model.api_key,
                base_url=model.api_base or "https://api.openai.com/v1",
                temperature=0.7,
                streaming=True,
            )


# Built-in web search tool (Tavily-based, will be enabled via config)
def create_web_search_tool(api_key: Optional[str] = None) -> BaseTool:
    @tool
    def web_search(query: str) -> str:
        """Search the web for current information. Use this when you need facts you don't know."""
        import requests
        key = api_key or settings.TAVILY_API_KEY
        if not key:
            return "Web search is not configured. Please set TAVILY_API_KEY."
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query, "search_depth": "basic", "max_results": 3},
                timeout=15,
            )
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return "No search results found."
            return "\n\n".join(
                f"[{r.get('title', 'N/A')}]\n{r.get('content', '')}\nURL: {r.get('url', '')}"
                for r in results
            )
        except Exception as e:
            return f"Web search error: {str(e)}"

    return web_search