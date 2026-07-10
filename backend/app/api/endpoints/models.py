# Model Configuration Endpoints
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select
from pydantic import BaseModel
from app.core.database import get_db
from app.models.model_config import ModelConfig
from app.models.user import User
from app.schemas.model_config import ModelConfigCreate, ModelConfigUpdate, ModelConfigOut
from app.services.auth_deps import get_current_user
import httpx
from app.core.config import get_settings

settings = get_settings()

router = APIRouter(prefix="/models", tags=["Model Management"])


class TestConnectionResult(BaseModel):
    success: bool
    message: str


@router.post("/{config_id}/test", response_model=TestConnectionResult)
async def test_model_connection(
    config_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Test connection to a model provider by making a simple API call."""
    result = await db.execute(
        select(ModelConfig).where(ModelConfig.id == config_id, ModelConfig.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Model config not found")

    # Build the request URL and headers based on provider
    api_base = config.api_base
    if not api_base:
        base_url_map = {
            "deepseek": "https://api.deepseek.com",
            "openai": "https://api.openai.com",
            "zhipu": "https://open.bigmodel.cn",
            "moonshot": "https://api.moonshot.cn",
            "alibaba": "https://dashscope.aliyuncs.com",
            "ollama": "http://host.docker.internal:11434",
        }
        api_base = base_url_map.get(config.provider.lower(), config.api_base or "https://api.openai.com")

    try:
        provider = config.provider.lower()
        api_base_clean = api_base.rstrip('/')

        if provider == "ollama":
            # Ollama: use /api/tags endpoint to test
            url = f"{api_base_clean}/api/tags"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return TestConnectionResult(success=True, message="连接成功")
                else:
                    return TestConnectionResult(success=False, message=f"连接失败 (HTTP {resp.status_code})")
        elif provider == "alibaba":
            # Alibaba Cloud (DashScope / 百炼) uses /compatible-mode/v1/models
            url = f"{api_base_clean}/compatible-mode/v1/models"
            headers = {"Authorization": f"Bearer {config.api_key}"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return TestConnectionResult(success=True, message="连接成功")
                elif resp.status_code == 401:
                    return TestConnectionResult(success=False, message="连接失败：API Key 无效")
                elif resp.status_code == 404:
                    return TestConnectionResult(success=False, message="连接失败 (HTTP 404) — 请检查 API-Base URL 是否填写正确，阿里云百炼需使用 https://dashscope.aliyuncs.com/compatible-mode/v1")
                else:
                    return TestConnectionResult(success=False, message=f"连接失败 (HTTP {resp.status_code})")
        else:
            # OpenAI-compatible: list models endpoint
            url = f"{api_base_clean}/v1/models"
            headers = {"Authorization": f"Bearer {config.api_key}"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    # API Key is valid; now check if account has available quota
                    # by making a minimal chat completion call (max_tokens=1, negligible cost)
                    quota_msg = ""
                    try:
                        chat_url = f"{api_base_clean}/v1/chat/completions"
                        chat_body = {
                            "model": config.model_name,
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1,
                        }
                        chat_resp = await client.post(chat_url, json=chat_body, headers=headers, timeout=10.0)
                        if chat_resp.status_code == 200:
                            quota_msg = ""
                        elif chat_resp.status_code == 402 or chat_resp.status_code == 429:
                            detail = ""
                            try:
                                detail = chat_resp.json().get("error", {}).get("message", "")
                            except Exception:
                                pass
                            if "quota" in detail.lower() or "billing" in detail.lower() or "insufficient" in detail.lower():
                                quota_msg = "，但账户余额不足（无可用额度）"
                            else:
                                quota_msg = f"，但聊天接口返回异常 ({chat_resp.status_code})"
                        else:
                            try:
                                err = chat_resp.json().get("error", {})
                                err_msg = err.get("message", "") or err.get("code", "")
                                if "quota" in err_msg.lower() or "billing" in err_msg.lower() or "insufficient" in err_msg.lower():
                                    quota_msg = "，但账户余额不足（无可用额度）"
                                else:
                                    quota_msg = f"，但聊天接口返回 {chat_resp.status_code}"
                            except Exception:
                                quota_msg = ""
                    except Exception:
                        quota_msg = "（无法验证账户余额）"

                    return TestConnectionResult(success=True, message=f"连接成功{quota_msg}")
                elif resp.status_code == 401:
                    return TestConnectionResult(success=False, message="连接失败：API Key 无效")
                else:
                    return TestConnectionResult(success=False, message=f"连接失败 (HTTP {resp.status_code})")

    except httpx.TimeoutException:
        return TestConnectionResult(success=False, message="连接失败：请求超时")
    except httpx.ConnectError:
        return TestConnectionResult(success=False, message="连接失败：无法连接到服务器")
    except Exception as e:
        return TestConnectionResult(success=False, message=f"连接失败：{str(e)[:100]}")



@router.get("/", response_model=list[ModelConfigOut])
async def list_models(
    model_type: str = Query(default=None, pattern="^(llm|embedding)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(ModelConfig).where(ModelConfig.user_id == current_user.id)
    if model_type:
        query = query.where(ModelConfig.model_type == model_type)
    query = query.order_by(ModelConfig.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/", response_model=ModelConfigOut, status_code=status.HTTP_201_CREATED)
async def create_model_config(
    data: ModelConfigCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if data.is_default:
        await _unset_defaults_for_type(db, current_user.id, data.model_type)

    config = ModelConfig(user_id=current_user.id, **data.model_dump())
    db.add(config)
    await db.flush()
    return config


@router.put("/{config_id}", response_model=ModelConfigOut)
async def update_model_config(
    config_id: str,
    data: ModelConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ModelConfig).where(ModelConfig.id == config_id, ModelConfig.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Model config not found")

    update_data = data.model_dump(exclude_unset=True)
    if update_data.get("is_default"):
        target_type = update_data.get("model_type", config.model_type)
        await _unset_defaults_for_type(db, current_user.id, target_type)

    for key, value in update_data.items():
        setattr(config, key, value)
    await db.flush()
    return config


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model_config(
    config_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ModelConfig).where(ModelConfig.id == config_id, ModelConfig.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Model config not found")
    await db.delete(config)


@router.get("/ollama/models")
async def list_ollama_models(
    current_user: User = Depends(get_current_user),
):
    """List locally installed Ollama models."""
    ollama_host = getattr(settings, 'OLLAMA_HOST', None) or "http://host.docker.internal:11434"
    url = f"{ollama_host}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            models = data.get("models", [])
            result = []
            for m in models:
                name = m.get("name", "")
                # Strip :latest suffix for cleaner display
                if name.endswith(":latest"):
                    name = name[:-7]
                result.append({
                    "name": name,
                    "size": str(m.get("size", 0)),
                    "modified_at": m.get("modified_at", ""),
                })
            return result
    except Exception:
        return []


async def _unset_defaults_for_type(db: AsyncSession, user_id: str, model_type: str):
    result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.user_id == user_id,
            ModelConfig.model_type == model_type,
            ModelConfig.is_default.is_(True),
        )
    )
    for cfg in result.scalars().all():
        cfg.is_default = False
