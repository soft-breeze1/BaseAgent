---
name: example_skill
display_name: 示例技能
description: 这是一个示例技能，用于演示 Progressive Disclosure 架构
category: demo
trigger_keywords:
  - 示例
  - demo
version: 1.0.0
author: BaseAgent
priority: 0
icon: 📝
trigger_mode: auto
---

# 示例技能说明

这是一个示例 SKILL.md 文件，用于测试技能的自动发现和按需加载。

## 操作说明

1. 当用户询问关于示例技能的问题时，PLANNER 会自动发现 `load_skill_context_example_skill` 工具
2. PLANNER 决定是否调用该工具
3. EXECUTOR 加载此 SKILL.md 全文作为 Tool Observation
4. LLM 阅读说明书后，使用基础工具完成任务