"""
Agent 状态机核心定义 (v11.0 — Industry Standard ReAct)
======================================================
基于 OpenAI/Anthropic/LangGraph 等业界主流模式重构的纯 ReAct 循环。

核心设计理念：
  1. 不限制 LLM 的行为方式 — 让 LLM 通过 bind_tools 自由决定
  2. 不对工具调用方式做任何硬限制（不限制数量、不限制参数写法）
  3. 工具调用完成后必须经过 FINALIZER 合成最终回答
  4. 记忆系统在循环开始前注入，不打断循环

状态流转：
  REACT_LOOP → (LLM 调用工具) → 执行 → REACT_LOOP
  REACT_LOOP → (LLM 输出文本) → FINALIZER → TERMINATED
"""

from __future__ import annotations

import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AgentNode(str, Enum):
    """状态机节点枚举（v11.0 简化版）。"""
    REACT_LOOP = "react_loop"
    FINALIZER = "finalizer"
    TERMINATED = "terminated"


@dataclass
class AgentState:
    """
    Industry-standard ReAct 状态机的运行时上下文（v11.0 精简版）。
    
    相比 v8.0 移除的字段：
      - tool_failure_counts / tool_banned / max_tool_failures / has_banned_tool
        → 不再对工具做熔断，由 LLM 自行决定是否重试
      - _skill_context_replan_done → Skill 在循环开始前已预加载
      - steps_log → 不再维护步骤日志列表（改为事件流）
      - accumulated_information → 消息链自行承载
    """
    query: str
    system_prompt: str
    model_descriptor: Any = None
    conversation_history: Optional[list] = None

    # ── 状态机控制 ──
    current_node: AgentNode = AgentNode.REACT_LOOP
    max_iterations: int = 25  # 放宽熔断保护
    iteration: int = 0

    # ── 工具调用结果 ──
    tool_results: list[dict] = field(default_factory=list)

    # ── 消息链（LangChain 格式，完整上下文） ──
    messages: list = field(default_factory=list)

    # ── 输出 ──
    final_answer: str = ""
    route_used: str = "tools"

    # ── 组件引用（由 SmartRouter 注入） ──
    _all_tools: list = field(default_factory=list)
    _llm: Any = None
    _llm_with_tools: Any = None

    # ── 流式缓冲 ──
    thinking_buffer: str = ""

    # ── 动作去重：SHA-256 指纹集合（跨轮次累计） ──
    executed_action_fingerprints: set = field(default_factory=set)

    # ── 强制工具守卫：如果非空，LLM 在进入 FINALIZER 前必须调用过此工具 ──
    required_tool_name: Optional[str] = None

    # ── 工具写入成功后记录的文件路径 ──
    output_file_path: Optional[str] = None

    def to_routing_result(self) -> dict:
        """转换为 RoutingResult 兼容的 dict。"""
        return {
            "answer": self.final_answer,
            "sources": [],
            "route_used": self.route_used,
            "tool_calls_detail": self.tool_results if self.tool_results else None,
            "conversation_history": self.messages if self.tool_results else None,
            "assistant_tool_calls": self.tool_results,
        }


# ── ReAct System Prompt（英文原版，遗留兼容） ────────────────────────────

REACT_SYSTEM_PROMPT = """You are BaseAgent, an intelligent AI assistant with access to tools.

You operate in a ReAct (Thought → Action → Observation) loop:
- **Think**: Analyze the current state and decide what to do next.
- **Act**: Call tools to gather information or perform actions. You can call multiple tools at once if they are independent.
- **Observe**: Tool results will be added to the conversation for you to use.

You have full freedom to decide:
- Which tools to call
- How many tools to call at once
- What arguments to pass to each tool
- When you have enough information to answer

Once you have all the information needed, stop calling tools. A FINALIZER step will synthesize your accumulated results into a polished response.

## Available Tools
{tools_description}

Use only tool names from this list:
{available_tool_names}

## Critical Reasoning Rules (MUST follow in order)

### Step 1: Classify User Intent
Determine the user's core need:
- **IMAGE_REQUEST**: User asks for/viewing/getting an image, photo, picture ("来一张猫的图片", "看看长什么样", "找一张风景图", "show me a picture")
- **INFO_NEED**: User asks for information, news, facts
- **TASK**: User wants a task done (write, format, analyze, calculate)
- **CHAT**: Simple conversation, greeting

### Step 2: Determine Tool Necessity
- If IMAGE_REQUEST: You MUST call `bing_image_search` — do NOT use your internal knowledge to describe images.
- If INFO_NEED: Use available search tools to find current information.
- If TASK: Check if a skill tool exists; if not, answer directly.
- If CHAT: Answer directly.

### Step 3: Degradation Violation Check (MUST NEVER do these)
- When user asks for a REAL image → NEVER output text description, ASCII art, or emoji as a substitute.
- When user asks for a file/operation → NEVER provide instructions-only without actually doing it.
- If you realize you're about to do any of the above, STOP and call the appropriate tool instead.

### Step 4: Tool Selection & Execution
- Only call tools listed in ## Available Tools.
- Pass all required arguments — do not omit parameters.
- After tools return results, examine the observations. If they contain the information needed, proceed to answer.

IMPORTANT: NEVER replace real tool execution with text descriptions or code that the user would have to run themselves."""


# ── P2: REACT_SYSTEM_PROMPT_WITH_FALLBACK（中文版，用于 _merge_react_prompt） ──

REACT_SYSTEM_PROMPT_WITH_FALLBACK = """## 工具调用规则

你是一个可以调用外部工具的智能助手。

### 操作模式
你运行在 ReAct 循环中：思考当前状态 → 调用工具获取信息 → 观察工具返回结果 → 决定下一步。

### 自由决策权
你可以完全自由地决定：
- 调用哪些工具
- 一次调用多少个工具
- 向每个工具传递什么参数
- 何时已有足够信息回答问题

一旦收集到足够信息，停止调用工具。Finalizer 阶段会将所有已收集结果合成为最终回答。

## 可用工具
{tools_description}

仅使用以下工具名列表中的工具：
{available_tool_names}

## 关键推理规则（必须严格按顺序执行）

### 第一步：意图分类
判断用户的核心需求：
- **图像请求**：用户要求获取/查看图片、照片，如"来一张猫的图片"、"看看长什么样"
- **信息需求**：用户查询信息、新闻、事实
- **任务**：用户需要完成某项工作（写作、格式化、分析、计算）
- **对话**：简单对话、问候

### 第二步：判断是否需要工具
- 如果是图像请求 → **必须调用图片搜索工具**，禁止用自身知识描述图片
- 如果是信息需求 → 使用可用搜索工具获取最新信息
- 如果是任务 → 检查是否有技能工具可用；如果没有则直接回答
- 如果是对话 → 直接回答

### 第三步：退化行为检查（绝对禁止）
- 用户要求真实图片时 → 绝不能用文字描述、ASCII艺术或emoji替代
- 用户要求文件/操作时 → 绝不能不实际执行而仅提供操作说明
- 如果发现即将做上述任何行为 → 立即停止并调用正确工具

### 第四步：工具选择与执行
- 仅调用 ## 可用工具 中列出的工具
- 传递所有必需参数，不要省略
- 工具返回结果后，检查结果是否包含所需信息，然后决定是否继续

重要：绝对不能用文本描述或代码替代真实的工具执行。"""


# ── P1.2: Finalizer System Prompt（中文 + 场景化格式约束） ──

FINALIZER_SYSTEM_PROMPT = """你是 BaseAgent，正在基于收集到的所有信息合成最终回答。

### 你的任务
使用以下材料生成对用户原始问题的全面、结构清晰的回答：
1. 工具调用结果和对话中的观察信息
2. 你自身的知识（适当补充）

### 输出规则
- 简洁准确，用要点/列表形式结构化展示工具返回的关键信息
- 如果涉及图片搜索结果，**将图片URL单独列出**，每张图片格式为：`![alt文本](图片URL)`
- 工具返回的数据优先用结构化方式（列表、表格、分类）展示，不使用段落描述
- 用与用户问题相同的语言回答
- **禁止输出内部推理过程、工具调用细节、规划步骤**
- 只输出最终回答"""


# ── 工具描述格式化 ─────────────────────────────────────────────────────

def format_tools_for_planner(tools: list) -> str:
    """
    将 LangChain 工具列表格式化为 LLM 可读的描述。
    
    支持两种格式：
      1. LangChain BaseTool 实例（带 name, description, args 属性）
      2. OpenAI function calling dict（含 type: "function", function: {name, description, parameters}）
    
    Args:
        tools: LangChain BaseTool 实例列表或 OpenAI schema dict 列表或混合
    
    Returns:
        格式化的工具描述字符串
    """
    parts = []
    for t in tools:
        if isinstance(t, dict):
            func_info = t.get("function", t)
            name = func_info.get("name", "unknown")
            description = str(func_info.get("description", ""))[:800]
            params = func_info.get("parameters", {})
            props = params.get("properties", {}) if isinstance(params, dict) else {}
            arg_names = list(props.keys())
        else:
            name = getattr(t, "name", "unknown")
            description = str(getattr(t, "description", ""))[:800]
            args_schema = getattr(t, "args", {})
            arg_names = list(args_schema.keys()) if isinstance(args_schema, dict) else []
        parts.append(f"- {name}: {description} (参数: {', '.join(arg_names) if arg_names else '无'})")
    return "\n".join(parts)