# Skill Model - 纯 SKILL.md 技能模型（skills.sh 官方标准）
# 
# 唯一技能载体：skills.sh 标准 SKILL.md 文件
# 无 execution_plan、无 DAG、无步骤编排

from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.user import generate_uuid


class Skill(Base):
    """Skill 定义：skills.sh 官方标准 SKILL.md 技能

    一个 Skill 就是一个声明式技能规范，
    通过 SKILL.md 全文向 LLM 注入执行规则，
    由 LLM 自主推理完成工作。
    """
    __tablename__ = "skills"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), unique=True, nullable=False, index=True)  # 英文唯一标识
    display_name = Column(String(200), nullable=False)                   # 中文名称
    description = Column(Text, nullable=False, default="")               # 能力简介
    category = Column(String(50), nullable=True, default="general")      # 分类
    
    # SKILL.md Frontmatter 元数据
    trigger_keywords = Column(Text, nullable=True, default="[]")          # JSON 数组，触发关键词
    version = Column(String(20), nullable=False, default="1.0.0")        # 版本号
    author = Column(String(100), nullable=True, default="")              # 作者
    priority = Column(Integer, nullable=True, default=0)                 # 优先级

    # 核心内容：完整 SKILL.md 原文
    skill_content = Column(Text, nullable=True, default="")             # SKILL.md 全文

    # 触发模式
    trigger_mode = Column(String(20), nullable=False, default="auto")    # auto / manual

    # 状态
    is_active = Column(Boolean, default=True, nullable=False)

    # 时间戳
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "trigger_keywords": json.loads(self.trigger_keywords) if self.trigger_keywords else [],
            "version": self.version,
            "author": self.author or "",
            "priority": self.priority or 0,
            "skill_content": self.skill_content or "",
            "trigger_mode": self.trigger_mode,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }