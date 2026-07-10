"""
Tools Management Endpoints (v9.1 - 增加翻译功能)
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import requests

from app.models.user import User
from app.services.auth_deps import get_current_user
from app.services.tool_manager import tool_manager, ToolDescriptor

router = APIRouter(prefix="/tools", tags=["Tools 管理"])

# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class ToolOut(BaseModel):
    name: str
    display_name: str
    description: str
    tool_type: str
    is_enabled: bool
    config: dict


class ToolToggleRequest(BaseModel):
    is_enabled: bool


class ToolConfigUpdateRequest(BaseModel):
    config: dict


class TavilyTestRequest(BaseModel):
    api_key: str


class UnsplashTestRequest(BaseModel):
    api_key: str


class TranslateRequest(BaseModel):
    text: str


class TranslateResponse(BaseModel):
    translated: str


# ---------------------------------------------------------------------------
# Tool Management Endpoints
# ---------------------------------------------------------------------------


@router.post("/unsplash-test")
async def test_unsplash_connection(
    req: UnsplashTestRequest,
    current_user: User = Depends(get_current_user),
):
    """Test Unsplash API key by making a simple search request."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.unsplash.com/search/photos",
                params={"query": "test", "per_page": 1},
                headers={"Authorization": f"Client-ID {req.api_key}", "Accept-Version": "v1"},
            )
        if resp.status_code == 200:
            return {"success": True, "message": "连接成功"}
        elif resp.status_code == 401 or resp.status_code == 403:
            return {"success": False, "message": "API Key 无效，请检查配置"}
        else:
            return {"success": False, "message": f"连接失败（状态码 {resp.status_code}）"}
    except httpx.TimeoutException:
        return {"success": False, "message": "连接超时，请检查网络"}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {str(e)}"}


@router.post("/tavily-test")
async def test_tavily_connection(
    req: TavilyTestRequest,
    current_user: User = Depends(get_current_user),
):
    """Test Tavily API key by making a simple search request."""
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": req.api_key, "query": "test", "search_depth": "basic", "max_results": 1},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code == 200:
            return {"success": True, "message": "连接成功"}
        else:
            error_msg = data.get("error", str(resp.status_code))
            return {"success": False, "message": f"连接失败: {error_msg}"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "连接超时，请检查网络"}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {str(e)}"}


@router.get("", response_model=List[ToolOut])
async def list_tools(current_user: User = Depends(get_current_user)):
    """List all registered tools (包括内置工具 + 动态加载的 Skill 工具)."""
    # 1. 内置工具
    builtin_tools = tool_manager.list_tools()
    
    # 2. 动态 Skill 工具（从 Skill YAML Frontmatter 实时加载）
    from app.progressive_disclosure import SkillManager
    skill_mgr = SkillManager()
    skills_meta = skill_mgr.get_active_skills_metadata()
    
    skill_tools = []
    for meta in skills_meta:
        skill_tools.append(ToolOut(
            name=f"load_skill_context_{meta['folder_name']}",
            display_name=f"{meta['display_name']}",
            description=meta['description'],
            tool_type="skill",
            is_enabled=True,
            config={"folder_name": meta['folder_name'], "version": meta['version']},
        ))
    
    # 合并返回
    return builtin_tools + skill_tools


@router.post("/translate", response_model=TranslateResponse)
async def translate_text(
    req: TranslateRequest,
    current_user: User = Depends(get_current_user),
):
    """
    将英文工具描述翻译为中文。
    
    优先使用系统配置的 LLM 进行高质量翻译。
    如果 LLM 不可用，回退到基本规则翻译。
    """
    text = req.text.strip()
    if not text:
        return TranslateResponse(translated="")
    
    # 如果文本已经是中文，直接返回
    import re
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    if chinese_chars > len(text) * 0.3:
        return TranslateResponse(translated=text)
    
    # 尝试使用 LLM 翻译
    try:
        translated = await _llm_translate(text)
        if translated and translated != text:
            return TranslateResponse(translated=translated)
    except Exception as e:
        from app.core.config import get_settings
        settings = get_settings()
        logger = logging.getLogger(__name__)
        logger.warning(f"LLM 翻译失败，回退到规则翻译: {e}")
    
    # 回退：使用规则翻译（仅作为保底）
    from app.core.config import get_settings
    settings = get_settings()
    translated = _fallback_translate(text)
    return TranslateResponse(translated=translated)


import logging
logger = logging.getLogger(__name__)


async def _llm_translate(text: str) -> str:
    """
    使用 LLM 将英文翻译为中文。
    
    调用 LLMFactory 创建模型实例进行翻译。
    """
    from app.services.llm_service import LLMFactory
    from app.core.config import get_settings
    from langchain_core.messages import SystemMessage, HumanMessage
    
    settings = get_settings()
    
    # 使用默认模型配置
    try:
        # 从数据库获取第一个启用的模型
        from app.core.database import get_db
        from app.models.model_config import ModelConfig
        from sqlalchemy import select
        
        descriptor = None
        async for db_session in get_db():
            result = await db_session.execute(
                select(ModelConfig).order_by(ModelConfig.id).limit(1)
            )
            model = result.scalars().first()
            if model:
                from app.services.llm_service import ModelDescriptor
                descriptor = ModelDescriptor(
                    provider=model.provider,
                    model_name=model.model_name,
                    api_key=model.api_key,
                    api_base_url=model.api_base_url,
                )
            break
        
        if not descriptor:
            # 没有配置模型，回退
            return _fallback_translate(text)
        
        llm = LLMFactory.create(descriptor)
        
        messages = [
            SystemMessage(content="你是一个专业的中英文翻译助手。请将以下英文内容翻译为流畅、准确的中文。只输出翻译结果，不要有任何额外说明。"),
            HumanMessage(content=text),
        ]
        
        response = await llm.ainvoke(messages, temperature=0.1, max_tokens=1000)
        translated = response.content if hasattr(response, 'content') else str(response)
        
        return translated.strip()
    except Exception as e:
        logger.warning(f"LLM 翻译调用失败: {e}")
        raise


# 缓存规则翻译结果
_translation_cache = {}

def _fallback_translate(text: str) -> str:
    """保底翻译：使用预先定义的整句映射。"""
    if text in _translation_cache:
        return _translation_cache[text]
    
    # 完整句子的翻译映射
    full_text_map = {
        "Search the web for current information. Use this when you need up-to-date facts, news, or information beyond your knowledge cutoff.":
            "搜索网络获取最新信息。当你需要最新的事实、新闻或超出知识截止日期的信息时使用此工具。",
        "Get the current date and time. Input: optional UTC offset like '+8' for Asia/Shanghai. Output: formatted datetime string.":
            "获取当前日期和时间。输入：可选的 UTC 时区偏移（如 '+8' 代表东八区）。输出：格式化的日期时间字符串。",
        "Create stunning, animation-rich HTML presentations from scratch or by converting PowerPoint files. Use when the user wants to build a presentation, convert a PPT/PPTX to web, or create slides for a talk/pitch. Helps non-designers discover their aesthetic through visual exploration rather than abstract choices.":
            "从零创建精美的、动画丰富的 HTML 演示文稿，或通过转换 PowerPoint 文件生成。适用于用户需要构建演示文稿、将 PPT/PPTX 转换为网页、或为演讲/推介创建幻灯片的场景。帮助非设计师通过视觉探索而非抽象选择来发现审美风格。",
        "Formats plain text or markdown files with frontmatter, titles, summaries, headings, bold, lists, and code blocks. Outputs to {filename}-formatted.md.":
            "格式化纯文本或 Markdown 文件，支持 Frontmatter、标题、摘要、粗体、列表和代码块。输出为 {filename}-formatted.md。",
    }
    
    if text in full_text_map:
        result = full_text_map[text]
        _translation_cache[text] = result
        return result
    
    # 不在预设映射中，返回原文（LLM 翻译已在 translate_text 中尝试过）
    _translation_cache[text] = text
    logger.info(f"翻译回退：预设映射中无匹配项，长度={len(text)}")
    return text


@router.get("/{tool_name}", response_model=ToolOut)
async def get_tool(tool_name: str, current_user: User = Depends(get_current_user)):
    """Get a specific tool by name."""
    tool = tool_manager.get_tool(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    return ToolOut(
        name=tool.name,
        display_name=tool.display_name,
        description=tool.description,
        tool_type=tool.tool_type,
        is_enabled=tool.is_enabled,
        config=tool.config,
    )


@router.put("/{tool_name}/toggle", response_model=ToolOut)
async def toggle_tool(
    tool_name: str,
    req: ToolToggleRequest,
    current_user: User = Depends(get_current_user),
):
    """Enable or disable a tool."""
    if req.is_enabled:
        if not tool_manager.enable_tool(tool_name):
            raise HTTPException(status_code=404, detail="Tool not found")
    else:
        if not tool_manager.disable_tool(tool_name):
            raise HTTPException(status_code=404, detail="Tool not found")

    tool = tool_manager.get_tool(tool_name)
    return ToolOut(
        name=tool.name,
        display_name=tool.display_name,
        description=tool.description,
        tool_type=tool.tool_type,
        is_enabled=tool.is_enabled,
        config=tool.config,
    )


@router.put("/{tool_name}/config", response_model=ToolOut)
async def update_tool_config(
    tool_name: str,
    req: ToolConfigUpdateRequest,
    current_user: User = Depends(get_current_user),
):
    """Update a tool's configuration."""
    success = tool_manager.update_tool_config(tool_name, req.config)
    if not success:
        raise HTTPException(status_code=404, detail="Tool not found")
    tool = tool_manager.get_tool(tool_name)
    return ToolOut(
        name=tool.name,
        display_name=tool.display_name,
        description=tool.description,
        tool_type=tool.tool_type,
        is_enabled=tool.is_enabled,
        config=tool.config,
    )