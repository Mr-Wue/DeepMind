# DeepMind

需求管理智能助手 — 基于 DeepAgents (LangGraph) + CopilotKit 构建的 AI Agent 系统。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt
cd frontend && npm install && cd ..

# 2. 配置环境变量
cp .env.example .env   # 编辑 .env 填入 LLM API Key 等

# 3. 一键启动
python main.py
```

启动后访问 **http://127.0.0.1:3000**

- 后端 FastAPI → `http://127.0.0.1:8000` (AG-UI 端点 `/copilotkit`)
- 前端 Next.js → `http://127.0.0.1:3000` (CopilotKit Chat UI)
- 健康检查 → `http://127.0.0.1:8000/health`

## 架构

```
┌─────────────────────────────────────────────┐
│  前端 (Next.js 15 + CopilotKit v2)           │
│  ┌──────────┬───────────┬────────────────┐  │
│  │ TodoPanel│ CopilotChat│ ToolCallPanel │  │
│  │ (任务规划) │  (对话区)   │  (工具调用详情)  │  │
│  └──────────┴───────────┴────────────────┘  │
│         InterruptHandler (中断确认)           │
└──────────────────┬──────────────────────────┘
                   │ AG-UI 协议
┌──────────────────┴──────────────────────────┐
│  后端 (FastAPI + LangGraph Agent)            │
│  ┌──────────────────────────────────────┐   │
│  │  Main Agent ("deepmind")             │   │
│  │  ├── query_reqmgmt (Text2SQL)        │   │
│  │  ├── web_search (互联网搜索)           │   │
│  │  └── subagent: req-parse             │   │
│  │       ├── parse_docx_outline         │   │
│  │       ├── extract_entities           │   │
│  │       └── store_entities             │   │
│  └──────────────────────────────────────┘   │
│  SQLite (需求管理数据库, 11 张表)             │
└─────────────────────────────────────────────┘
```

## 功能

- **智能问答** — 自然语言查询需求数据库，自动生成 SQL
- **文档解析** — 上传 .docx 需求文档，自动提取实体并入库
- **任务规划** — Agent 自动分解任务，前端实时展示进度
- **中断确认** — 关键操作（入库/修改）需用户确认后执行
- **互联网搜索** — 通过智谱 MCP 实时搜索补充信息

## 技术栈

| 层 | 技术 |
|---|------|
| Agent 框架 | DeepAgents (LangGraph) |
| 通信协议 | AG-UI (CopilotKit) |
| 后端 | FastAPI + Python |
| 前端 | Next.js 15 + CopilotKit v2 + shadcn/ui |
| 数据库 | SQLite + SQLAlchemy ORM |
| LLM | 可配置 (通过 .env 切换模型) |

## 项目结构

```
DeepMind/
├── main.py                 # 一键启动入口
├── agents/                 # Agent 定义与初始化
│   ├── init.py             # DeepMindConfig, init_deepmind()
│   └── deep_agent.py       # create_deepmind_agent()
├── agents/text2sql/        # Text2SQL 流水线 (LangGraph)
├── llm/                    # LLM 适配器工厂
├── models/                 # SQLAlchemy ORM 模型
├── tools/                  # Agent 工具 (查询/搜索/解析)
├── middleware/              # Agent 中间件
├── memory/                 # 记忆系统 (Store/Backend)
├── skills/                 # Agent Skills
├── utils/                  # 工具函数 (paths.py)
├── prompts/                # LLM 提示词模板
├── frontend/               # Next.js 前端
│   ├── app/
│   │   ├── page.tsx        # 3 栏布局主页面
│   │   ├── layout.tsx      # CopilotKit Provider
│   │   └── api/copilotkit/ # API 代理
│   └── components/
│       ├── TodoPanel.tsx    # 任务规划面板
│       ├── ToolCallPanel.tsx# 工具调用面板
│       └── InterruptCard.tsx# 中断确认处理
└── data/                   # 运行时数据 (数据库/日志等)
```
