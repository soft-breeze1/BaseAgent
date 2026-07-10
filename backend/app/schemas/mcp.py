"""
MCP 服务器 API Schema 定义。
"""
from typing import Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field


class MCPServerCreate(BaseModel):
    """创建 MCP Server 请求。"""
    name: str = Field(..., min_length=1, max_length=100, description="服务器名称")
    type: str = Field(default="stdio", pattern="^(http|stdio)$", description="传输类型：http 或 stdio")
    config: dict = Field(default_factory=dict, description="配置信息，如 url、command、args、env 等")
    status: str = Field(default="connected", description="连接状态")


class MCPServerUpdate(BaseModel):
    """更新 MCP Server 请求。"""
    name: Optional[str] = Field(None, max_length=100)
    type: Optional[str] = Field(None, pattern="^(http|stdio)$")
    config: Optional[dict] = None
    status: Optional[str] = None


class MCPServerOut(BaseModel):
    """MCP Server 输出。"""
    id: str
    name: str
    type: str
    config: Optional[Any] = None  # JSON
    status: str
    user_id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MCPServerTestRequest(BaseModel):
    """测试 MCP Server 连接请求。"""
    type: str = Field(default="stdio", pattern="^(http|stdio)$")
    config: dict = Field(default_factory=dict)


class MCPServerTestResult(BaseModel):
    """测试 MCP Server 连接结果。"""
    success: bool
    message: str
    tool_count: int = 0
    tools: list[dict] = Field(default_factory=list)