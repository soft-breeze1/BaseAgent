"""
MCP Server 数据库模型
======================
存储用户配置的 MCP Server 连接信息。
已存在数据库表 `mcp_server`，包含字段：
  - id, name, type (http/stdio), config, status, user_id

本模型映射该表并提供 ORM 操作接口。
"""
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.core.database import Base


class MCPServer(Base):
    """MCP Server 持久化模型。"""
    __tablename__ = "mcp_server"

    id = Column(String(36), primary_key=True)
    name = Column(String(100), nullable=False, index=True)
    type = Column(String(20), nullable=False, default="stdio")  # http | stdio
    config = Column(Text, nullable=True)  # JSON 字符串，存储连接配置
    status = Column(String(20), nullable=False, default="disconnected")  # connected, disconnected, error
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationship
    user = relationship("User", back_populates="mcp_servers")