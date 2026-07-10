# User Profile Endpoints (用户个人信息接口)
import os
import uuid
import aiofiles
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import verify_password, hash_password
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.user import UserInfoOut, UserInfoUpdate, PasswordModify
from app.services.auth_deps import get_current_user
from app.core.config import get_settings
import random

router = APIRouter(prefix="/user", tags=["User Profile"])
settings = get_settings()


async def _get_or_create_profile(current_user: User, db: AsyncSession) -> UserProfile:
    """获取或创建用户的Profile记录（显式查询，避免async lazy-load问题）"""
    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        random_suffix = str(random.randint(100000, 999999))
        profile = UserProfile(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            nickname=f"用户{random_suffix}",
        )
        db.add(profile)
        await db.flush()
    return profile


@router.get("/info", response_model=UserInfoOut)
async def get_user_info(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前登录用户的基本信息"""
    profile = await _get_or_create_profile(current_user, db)
    return UserInfoOut(
        id=current_user.id,
        username=current_user.username,
        avatar=profile.avatar,
        nickname=profile.nickname,
        email=current_user.email,
    )


@router.put("/info", response_model=dict)
async def update_user_info(
    data: UserInfoUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新用户基本信息（昵称、头像）"""
    profile = await _get_or_create_profile(current_user, db)
    if data.username is not None:
        profile.nickname = data.username
    if data.avatar is not None:
        profile.avatar = data.avatar
    await db.flush()
    return {"success": True, "message": "信息更新成功"}


@router.post("/avatar", response_model=dict)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """上传用户头像"""
    # 验证文件类型
    if file.content_type not in ["image/jpeg", "image/png", "image/jpg"]:
        raise HTTPException(status_code=400, detail="仅支持jpg、png格式的图片")

    # 验证文件大小（2MB限制）
    content = await file.read()
    file_size = len(content)
    if file_size > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小不能超过2MB")

    # 保存文件
    upload_dir = settings.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)
    ext = os.path.splitext(file.filename)[1] if file.filename else ".jpg"
    filename = f"avatar_{current_user.id}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(upload_dir, filename)

    async with aiofiles.open(filepath, "wb") as f:
        await f.write(content)

    # 返回可访问的URL（生产环境应使用nginx等反向代理的路径）
    avatar_url = f"/uploads/{filename}"
    return {"success": True, "url": avatar_url}


@router.put("/password", response_model=dict)
async def modify_password(
    data: PasswordModify,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """修改用户密码"""
    # 验证原密码
    if not verify_password(data.old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="原密码不正确")

    # 验证两次新密码一致
    if data.new_password != data.confirm_password:
        raise HTTPException(status_code=400, detail="两次输入的新密码不一致")

    # 更新密码
    current_user.hashed_password = hash_password(data.new_password)
    await db.flush()
    return {"success": True, "message": "密码修改成功"}
