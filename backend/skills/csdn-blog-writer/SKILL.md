---
name: csdn-blog-writer
display_name: CSDN 博客写作助手
description: 用于在CSDN平台撰写高质量技术博客的SOP技能，包含资料研究、图片获取、文章生成与本地保存
version: 1.0.0
category: writing
author: BaseAgent
icon: ✍️
trigger_keywords:
  - CSDN
  - 博客
  - 文章
  - blog
trigger_mode: auto
parameters:
  type: object
  properties:
    topic:
      type: string
      description: 目标博客主题
  required:
    - topic
---
