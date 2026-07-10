# User Profile Schemas (个人用户信息相关Schema)
from pydantic import BaseModel, Field


class UserInfoOut(BaseModel):
    """用户基本信息返回"""
    id: str
    username: str
    avatar: str | None = None
    nickname: str | None = None
    email: str

    class Config:
        from_attributes = True


class UserInfoUpdate(BaseModel):
    """用户基本信息更新"""
    username: str | None = Field(None, min_length=2, max_length=20)
    avatar: str | None = None


class PasswordModify(BaseModel):
    """密码修改"""
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=100)
    confirm_password: str = Field(..., min_length=6, max_length=100)
