---
name: test_skill_crafter
display_name: 测试技能制作助手
description: 用于测试 Progressive Disclosure 动态工具注入机制的示例技能
version: 1.0.0
category: development
author: BaseAgent
icon: 🧪
trigger_keywords:
  - 测试
  - 技能
  - progressive disclosure
trigger_mode: auto
parameters:
  type: object
  properties:
    task_description:
      type: string
      description: 任务描述
  required:
    - task_description
---

# 测试技能制作助手

## 简介
这是一个用于测试 Progressive Disclosure 系统的示例技能。

## 能力
1. **测试能力 1**: 能够验证 SKILL.md 扫描和解析
2. **测试能力 2**: 能够验证 Tool Schema 自动构建
3. **测试能力 3**: 能够验证 PLANNER 工具注入
4. **测试能力 4**: 能够验证 EXECUTOR 拦截回调

## 使用说明
当需要测试技能系统时，调用本技能获取完整的上下文规范。

## 注意事项
- 这是一个测试技能，不会执行真实的业务逻辑
- 主要用于验证四模块的协作流程