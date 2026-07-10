# 🧠 BaseAgent — 基于 RAG + MCP 的全栈智能 Agent 平台

<p align="center">
  <img src="https://img.shields.io/badge/Vue-3.4-4FC08D?logo=vue.js&logoColor=white" alt="Vue 3.4"/>
  <img src="https://img.shields.io/badge/Vite-5-646CFF?logo=vite&logoColor=white" alt="Vite 5"/>
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white" alt="Python 3.11"/>
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/Docker-Compse-2496ED?logo=docker&logoColor=white" alt="Docker Compose"/>
  <img src="https://img.shields.io/badge/MySQL-8.0-4479A1?logo=mysql&logoColor=white" alt="MySQL 8.0"/>
  <img src="https://img.shields.io/badge/Redis-7-FF4438?logo=redis&logoColor=white" alt="Redis 7"/>
  <img src="https://img.shields.io/badge/Qdrant-1.10-6C2BD9?logo=qdrant&logoColor=white" alt="Qdrant 1.10"/>
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License MIT"/>
</p>

---

## 📋 项目简介

BaseAgent 是一个面向开发者的全栈 **RAG（检索增强生成）** 与 **MCP（模型上下文协议）** 智能 Agent 平台。它通过本地大语言模型和向量数据库驱动，提供知识库管理、技能编排、多模型调度与 ReAct 循环推理等核心能力。

### ✨ 核心特性

| 特性 | 描述 |
|------|------|
| **🧩 技能系统** | 基于 SKILL.md 的渐进式技能加载，支持动态工具注入与执行拦截器 |
| **📚 RAG 知识库** | Parent-Child 分块 + Qdrant 向量检索 + Cross-Encoder 本地重排序 |
| **🔧 MCP 协议** | 原生支持 Model Context Protocol，可接入任意 MCP Server |
| **🧠 多模型聚合** | 统一接口对接 OpenAI / Ollama / 本地开源模型，支持工具调用 |
| **⚙️ ReAct 循环** | 内置思考-行动-观察推理引擎，支持智能路由与工具语义召回 |
| **🎨 前端仪表盘** | Vue 3 + Element Plus 构建的现代化管理界面 |
| **📂 多格式文档** | PDF（含 OCR）/ DOCX / Markdown / CSV / PPT / Excel 全系列文档解析 |

---

## 🏗️ 架构设计

```
┌─────────────────────────────────────────────────────┐
│                    Frontend (Vue 3)                  │
│          :8080 (Nginx) / :5173 (Dev Server)         │
└──────────────────┬──────────────────────────────────┘
                   │ HTTP / SSE
                   ▼
┌─────────────────────────────────────────────────────┐
│              FastAPI Backend (:8000)                  │
│  ┌──────────┐ ┌────────┐ ┌───────┐ ┌───────────┐   │
│  │ Chat API │ │ RAG    │ │ MCP   │ │ Auth/Sys  │   │
│  │ (SSE)    │ │ Service│ │ Proxy │ │ Mgmt      │   │
│  └──────────┘ └────────┘ └───────┘ └───────────┘   │
│  ┌──────────┐ ┌────────┐ ┌────────────────────┐   │
│  │ Tool Mgr │ │ Skill  │ │ Progressive        │   │
│  │          │ │ Engine │ │ Disclosure         │   │
│  └──────────┘ └────────┘ └────────────────────┘   │
└──────┬──────────┬──────────┬────────────────────────┘
       │          │          │
       ▼          ▼          ▼
  ┌────────┐ ┌────────┐ ┌────────┐
  │ MySQL  │ │ Redis  │ │ Qdrant │
  │ (8.0)  │ │ (7)    │ │(Vector)│
  └────────┘ └────────┘ └────────┘
```

### 服务组件关系

| 容器 | 端口 | 职责 |
|------|------|------|
| `baseagent-backend` | 8000 | FastAPI 应用服务（API + SSE 流 + Celery 任务生产） |
| `baseagent-celery` | — | 异步任务消费者（文档处理、技能执行） |
| `baseagent-frontend` | 8080 → 80 | Nginx 静态托管 Vue 前端 |
| `baseagent-mysql` | 3306 | 主数据库（用户、对话、会话、系统配置） |
| `baseagent-redis` | 6379 | 缓存 & Celery 消息代理 |
| `baseagent-qdrant` | 6333 | 向量数据库（知识库文档向量存储与检索） |

---

## ⚠️ 环境与大模型准备（必读）

> 本项目依赖两个本地大模型文件，**Git 仓库不包含它们**，必须手动下载。

### 你需要下载的模型

| 模型 | 用途 | 大小 | 下载地址 |
|------|------|------|---------|
| **bge-reranker-v2-m3** | RAG 重排序（Cross-Encoder，精确度排序） | ~2.1 GB | [Hugging Face](https://huggingface.co/BAAI/bge-reranker-v2-m3) |
| **bge-small-zh-v1.5** | 文本向量化（Embedding） | ~133 MB | [Hugging Face](https://huggingface.co/BAAI/bge-small-zh-v1.5) |

### 保姆级下载步骤（Windows）

#### 方法一：使用 `git lfs` 克隆
```bash
# 1. 安装 Git LFS（如果没有）
git lfs install

# 2. 克隆 bge-reranker-v2-m3（约 2.1GB，耗时较长）
cd BaseAgent
git clone https://huggingface.co/BAAI/bge-reranker-v2-m3

# 3. 克隆 bge-small-zh-v1.5（约 133MB）
git clone https://huggingface.co/BAAI/bge-small-zh-v1.5
```

#### 方法二：手动下载（逐个文件）
> 访问上述 Hugging Face 页面，手动下载以下文件：

**`bge-reranker-v2-m3/` 需要下载的核心文件：**

| 文件 | 必须 |
|------|:----:|
| `model.safetensors` | ✅ **核心模型权重（~2.1GB）** |
| `config.json` | ✅ |
| `tokenizer.json` | ✅ |
| `tokenizer_config.json` | ✅ |
| `sentencepiece.bpe.model` | ✅ |
| `special_tokens_map.json` | ✅ |
| `README.md` | 可选 |

**`bge-small-zh-v1.5/` 需要下载的核心文件：**

| 文件 | 必须 |
|------|:----:|
| `model.safetensors` | ✅ **核心模型权重（~133MB）** |
| `config.json` | ✅ |
| `tokenizer.json` | ✅ |
| `tokenizer_config.json` | ✅ |
| `vocab.txt` | ✅ |
| `special_tokens_map.json` | ✅ |

### 最终文件放置结构（绝对路径，严防套娃）

确保放置后目录结构如下（**不是** `bge-reranker-v2-m3/bge-reranker-v2-m3/` 这种嵌套）：

```
BaseAgent/
├── bge-reranker-v2-m3/         ← 一级目录，直接包含模型文件
│   ├── model.safetensors        (2.1GB，核心文件)
│   ├── config.json
│   ├── tokenizer.json
│   ├── tokenizer_config.json
│   ├── sentencepiece.bpe.model
│   ├── special_tokens_map.json
│   └── assets/
├── hf_cache/                    ← bge-small-zh-v1.5 的副本
│   ├── model.safetensors        (133MB)
│   ├── config.json
│   └── ...
```

> **⚠️ 关键注意：**
> - 两个目录必须与 `docker-compose.yml` 平级
> - 严禁目录嵌套（例如 `bge-reranker-v2-m3/bge-reranker-v2-m3/model.safetensors` 是**错误**的）
> - `hf_cache` 是 `bge-small-zh-v1.5` 的完整副本，Docker 容器通过卷挂载映射到 `/app/models_cache/bge-small-zh-v1.5`

---

## 🚀 快速启动

### 前提条件

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)（Windows）
- 模型文件已按上述步骤准备完毕
- 系统建议可用内存 ≥ 8GB

### 一键启动

```bash
# 1. 进入项目目录
cd BaseAgent

# 2. 启动所有服务
docker-compose up -d

# 3. 查看启动日志
docker-compose logs -f
```

### 访问入口

| 组件 | 地址 | 说明 |
|------|------|------|
| **前端 UI** | [http://localhost:8080](http://localhost:8080) | 管理仪表盘 |
| **后端 API** | [http://localhost:8000](http://localhost:8000) | FastAPI 服务 |
| **API 文档** | [http://localhost:8000/docs](http://localhost:8000/docs) | Swagger UI |
| **Qdrant UI** | [http://localhost:6333/dashboard](http://localhost:6333/dashboard) | 向量数据库看板 |

> **首次启动后，需要注册一个管理员账号**，登录后即可使用全部功能。

---

## 📁 目录结构

```
BaseAgent/
├── docker-compose.yml           # Docker 服务编排（6个容器）
├── README.md                    # 本文件
│
├── backend/                     # 🐍 Python 后端
│   ├── Dockerfile               #   生产镜像构建
│   ├── Dockerfile.celery        #   Celery Worker 镜像
│   ├── requirements.txt         #   Python 依赖（84个包）
│   ├── .env                     #   环境变量配置
│   │
│   ├── app/                     #   主应用代码
│   │   ├── main.py              #   FastAPI 入口 + 生命周期
│   │   ├── celery_app.py        #   Celery Worker 入口
│   │   │
│   │   ├── api/                 #   📡 API 路由
│   │   │   └── endpoints/       #       9 个资源端点
│   │   │       ├── auth.py      #       认证（JWT 登录/注册）
│   │   │       ├── chat.py      #       SSE 流式对话
│   │   │       ├── knowledge.py #       知识库 CRUD
│   │   │       ├── mcp.py       #       MCP Server 管理
│   │   │       ├── models.py    #       模型配置
│   │   │       ├── skills.py    #       技能列表/同步
│   │   │       ├── tools.py     #       工具注册与路由
│   │   │       ├── user.py      #       用户信息
│   │   │       └── system_prompt.py  # 系统提示词
│   │   │
│   │   ├── core/                #   ⚙️ 核心基础设施
│   │   │   ├── config.py        #       配置中心（Pydantic Settings）
│   │   │   ├── database.py      #       数据库引擎（SQLAlchemy）
│   │   │   ├── redis.py         #       Redis 客户端
│   │   │   ├── security.py      #       JWT 密码工具
│   │   │   └── mcp/             #       MCP 协议实现
│   │   │       ├── executor.py  #       HTTP/Stdio 执行器
│   │   │       ├── protocol.py  #       JSON-RPC 协议封装
│   │   │       ├── discovery.py #       工具发现与路由
│   │   │       └── process_manager.py  # Stdio 子进程管理
│   │   │
│   │   ├── models/              #   🗃️ SQLAlchemy ORM 模型
│   │   │   ├── chat.py          #       对话/消息
│   │   │   ├── knowledge_base.py #     知识库
│   │   │   ├── mcp_server.py    #       MCP Server 配置
│   │   │   ├── model_config.py  #       模型配置
│   │   │   ├── user.py          #       用户
│   │   │   ├── skill.py         #       技能
│   │   │   └── system_prompt.py #       系统提示词
│   │   │
│   │   ├── schemas/             #   📐 Pydantic 校验模型
│   │   │
│   │   ├── services/            #   🔧 业务服务层
│   │   │   ├── llm_service.py   #       LLM 聚合工厂
│   │   │   ├── rag_service.py   #       RAG 检索管道
│   │   │   ├── tool_manager.py  #       工具注册调度
│   │   │   ├── tool_retrieval.py#       工具语义召回
│   │   │   ├── smart_router.py  #       智能路由
│   │   │   ├── memory_service.py#       对话记忆
│   │   │   └── agent_state.py   #       Agent 状态管理
│   │   │
│   │   ├── progressive_disclosure/  # 🧩 技能系统
│   │   │   ├── skill_manager.py #       磁盘扫描与元数据提取
│   │   │   ├── skill_runner.py  #       单轮 LLM+工具执行
│   │   │   ├── schema_builder.py#       工具 Schema 构建
│   │   │   ├── tool_injector.py #       工具注入器
│   │   │   └── execution_interceptor.py # 执行拦截器
│   │   │
│   │   ├── rag/                 #   📚 RAG 引擎
│   │   │   ├── ingestion/       #       文档导入管道
│   │   │   │   ├── chunking/    #       Parent-Child 分块
│   │   │   │   ├── storage/     #       Parent 内容存储
│   │   │   │   └── loader/      #       PDF OCR 增强加载
│   │   │   └── libs/
│   │   │       ├── embedding/   #       向量化抽象
│   │   │       └── reranker/    #       Cross-Encoder 重排序
│   │   │
│   │   ├── tools/               #   🔨 工具实现
│   │   │   ├── system_tools/    #       文件系统 / 终端执行
│   │   │   └── utility_tools/   #       网页抓取 / Python沙箱
│   │   │
│   │   ├── tasks/               #   ⏳ Celery 异步任务
│   │   │   ├── chat_tasks.py    #       对话处理
│   │   │   ├── document_tasks.py#       文档索引
│   │   │   └── skill_tasks.py   #       技能执行
│   │   │
│   │   └── utils/               #   🛠️ 工具函数
│   │
│   ├── skills/                  #   📖 内建技能（SKILL.md）
│   │   ├── example_skill/       #       示例技能
│   │   ├── baoyu-format-markdown/ #     Markdown 排版
│   │   ├── csdn-blog-writer/    #       CSDN 博客写作
│   │   ├── slide-craft-skill-main/ #  幻灯片制作
│   │   └── test_skill_crafter/  #       测试技能
│   │
│   ├── migrations/              #   🗄️ 数据库迁移脚本
│   │   ├── init.sql             #       MySQL 初始化表结构
│   │   └── add_*.sql            #       增量迁移
│   │
│   └── data/                    #   📦 运行时数据/脚本
│       └── mcp_servers.json     #       MCP Server 示例配置
│
├── frontend/                    # 🎨 Vue 3 前端
│   ├── Dockerfile               #   Nginx 生产镜像
│   ├── nginx.conf               #   路由转发配置
│   ├── index.html               #   入口 HTML
│   ├── vite.config.ts           #   Vite 打包配置
│   ├── package.json             #   依赖清单
│   └── src/
│       ├── main.ts              #   Vue 应用入口
│       ├── App.vue              #   根组件
│       ├── router/              #   路由配置
│       ├── stores/              #   Pinia 状态管理（auth / chat）
│       ├── api/                 #   Axios API 客户端
│       ├── views/               #   页面组件（10个）
│       ├── components/          #   通用组件
│       └── styles/              #   全局样式
│
├── bge-reranker-v2-m3/          # 🧠 重排序模型（手动下载）
├── hf_cache/                    # 🧠 向量化模型（手动下载）
│
├── data/db/                     # 🗄️ SQLite / BM25 索引（运行时）
└── output/                      # 📤 技能产物输出目录
```

---

## 💻 开发指南

### 前端开发（热重载）

```bash
# 1. 进入前端目录
cd frontend

# 2. 安装依赖
npm install

# 3. 启动 Vite 开发服务器（默认 :5173）
npm run dev
```

> 开发模式下前端默认连接 `http://localhost:8000`（后端 API），可在 `vite.config.ts` 中修改代理配置。

### 后端开发（独立调试）

```bash
# 1. 创建虚拟环境
cd backend
python -m venv .venv
.venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 确保 MySQL / Redis / Qdrant 已启动（可用 Docker 只启动必要服务）
docker-compose up -d mysql redis qdrant

# 4. 启动 FastAPI 开发服务器（热重载）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 常用 Docker 命令

```bash
# 查看所有容器状态
docker ps

# 查看后端日志
docker logs baseagent-backend -f

# 重启特定服务
docker restart baseagent-backend

# 重新构建并启动
docker-compose up -d --build

# 停止所有服务
docker-compose down

# 停止并清除数据卷（危险：会丢失数据库数据！）
docker-compose down -v
```

---

## 🔗 API 概览

| 端点 | 前缀 | 说明 |
|------|------|------|
| `POST /api/v1/auth/register` | 认证 | 用户注册 |
| `POST /api/v1/auth/login` | 认证 | 登录获取 JWT |
| `GET /api/v1/chat/stream` | 对话 | SSE 流式对话 |
| `GET /api/v1/knowledge/` | 知识库 | 列表查询 |
| `POST /api/v1/knowledge/upload` | 知识库 | 文档上传并索引 |
| `GET /api/v1/mcp/servers` | MCP | Server 列表 |
| `GET /api/v1/tools` | 工具 | 已注册工具列表 |
| `GET /api/v1/skills` | 技能 | 已发现技能列表 |
| `POST /api/v1/skills/execute` | 技能 | 执行一个技能 |
| `GET /api/v1/models/` | 模型 | 模型配置列表 |

---

## 📄 许可证

本项目基于 MIT 协议开源，详见 `LICENSE` 文件。
