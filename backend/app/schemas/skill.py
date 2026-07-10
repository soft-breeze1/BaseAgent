# Skill Pydantic Schemas - 纯 SKILL.md 技能
#
# 已彻底删除：SkillStep、execution_plan、DAG 相关字段
# 唯一技能载体：SKILL.md 全文

from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field


class SkillBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, pattern=r'^[a-z][a-z0-9_]*$')
    display_name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    category: Optional[str] = "general"
    trigger_keywords: List[str] = Field(default_factory=list)
    version: str = Field(default="1.0.0", max_length=20)
    author: str = Field(default="", max_length=100)
    priority: int = Field(default=0, ge=0, le=100)
    skill_content: str = Field(default="", description="完整 SKILL.md 原文")
    trigger_mode: str = Field(default="auto", pattern=r'^(auto|manual)$')
    is_active: bool = True


class SkillCreate(SkillBase):
    pass


class SkillUpdate(BaseModel):
    """仅允许更新以下字段（name 不可变更）"""
    display_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    trigger_keywords: Optional[List[str]] = None
    version: Optional[str] = None
    author: Optional[str] = None
    priority: Optional[int] = None
    skill_content: Optional[str] = None
    trigger_mode: Optional[str] = None
    is_active: Optional[bool] = None


class SkillOut(SkillBase):
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SkillListOut(BaseModel):
    total: int
    items: List[SkillOut]


class SkillPreviewOut(BaseModel):
    """SKILL.md 预览解析结果（仅解析 Frontmatter，不保存）"""
    name: str
    display_name: str
    description: str = ""
    category: str = "general"
    trigger_keywords: List[str] = Field(default_factory=list)
    version: str = "1.0.0"
    author: str = ""
    priority: int = 0
    trigger_mode: str = "auto"
    body: str = ""
    has_valid_frontmatter: bool = True


class SkillExecuteRequest(BaseModel):
    query: str = Field(..., min_length=1)
    variables: dict = Field(default_factory=dict)


class SkillExecuteResult(BaseModel):
    skill_id: str
    skill_name: str
    session_id: str
    status: str  # running / completed / failed
    final_output: Optional[str] = None
    error: Optional[str] = None