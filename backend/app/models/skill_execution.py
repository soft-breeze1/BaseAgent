# Skill Execution Model - 纯 SKILL.md 执行日志
#
# 仅保留全局执行记录，移除步骤级日志

from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer, BigInteger
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.user import generate_uuid


class SkillExecution(Base):
    """Skill 执行历史记录"""
    __tablename__ = "skill_executions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_id = Column(String(50), nullable=False, index=True)
    skill_id = Column(String(36), nullable=False)
    skill_name = Column(String(100), nullable=False)
    skill_display_name = Column(String(200), nullable=False)

    # 执行信息
    user_query = Column(Text, nullable=False, default="")
    output = Column(Text, nullable=True, default="")           # 最终输出
    status = Column(String(20), nullable=False, default="running")  # running / completed / failed
    error_msg = Column(Text, nullable=True, default="")        # 错误信息

    # 日志
    trace_log = Column(Text, nullable=True, default="[]")      # 全链路 Trace

    # 时间
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "session_id": self.session_id,
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "skill_display_name": self.skill_display_name,
            "user_query": self.user_query,
            "output": self.output,
            "status": self.status,
            "error_msg": self.error_msg,
            "trace_log": json.loads(self.trace_log) if self.trace_log else [],
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_detail_dict(self):
        d = self.to_dict()
        import json
        d["trace_log"] = json.loads(self.trace_log) if self.trace_log else []
        return d