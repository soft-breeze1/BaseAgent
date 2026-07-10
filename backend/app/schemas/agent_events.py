"""
Agent 流式步骤事件结构化定义
================================
业界标准的 SSE 结构化事件协议。
所有事件通过统一格式透传到前端，辅助 ThinkingTimeline 组件展示。

事件类型枚举：
  round_start / think / tool_start / tool_end / round_end / finalize / error

通用字段：
  type      - 事件类型
  round     - ReAct 轮次编号
  timestamp - 毫秒级事件时间戳

工具事件特有字段：
  tool_name - 工具名
  status    - running / success / failed
  message   - 描述信息
"""

import time
import uuid
from enum import Enum
from typing import Optional


class AgentEventType(str, Enum):
    ROUND_START = "round_start"
    ROUND_END = "round_end"
    THINK = "think"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    FINALIZE = "finalize"
    ERROR = "error"


def make_event(
    event_type: AgentEventType,
    round: int = 0,
    tool_name: Optional[str] = None,
    status: Optional[str] = None,
    message: Optional[str] = None,
    call_id: Optional[str] = None,
) -> dict:
    """
    构造结构化事件 dict。
    全字段规范：
      type      - 事件类型枚举值
      round     - ReAct 轮次编号
      timestamp - 毫秒级事件时间戳
      call_id   - 工具调用的唯一标识（tool_start/tool_end 携带相同的 call_id）
      tool_name - 工具名
      status    - running / success / failed
      message   - 人类可读的描述信息
    """
    event: dict = {
        "type": event_type.value,
        "round": round,
        "timestamp": int(time.time() * 1000),
    }
    if call_id is not None:
        event["call_id"] = call_id
    if tool_name is not None:
        event["tool_name"] = tool_name
    if status is not None:
        event["status"] = status
    if message is not None:
        event["message"] = message
    return event
