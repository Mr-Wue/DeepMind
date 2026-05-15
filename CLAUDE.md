# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用命令

```bash
# 一键启动前后端 (FastAPI :8000 + Next.js :3000)
python main.py

# 单独启动后端
.venv/Scripts/python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# 单独启动前端
cd frontend && npm run dev

# 安装 Python 依赖
.venv/Scripts/pip install -r requirements.txt

# 安装前端依赖
cd frontend && npm install

# 运行 Text2SQL 单元测试
.venv/Scripts/python tests/text2sql_reqmgmt_test.py

# 通过 pytest 运行单个测试
.venv/Scripts/python -m pytest tests/test_deepagents_skill.py -v
```

## 编码规范

- **禁止硬编码路径**：任何文件/目录路径必须通过 `utils/paths.py` 中 `DataPaths` 的方法获取（如 `data_paths.logs()`, `data_paths.reqmgmt_db()`）。如果现有方法不满足需求，先在 `DataPaths` 中新增方法再调用。禁止在代码中直接写魔法路径字符串（如 `"data/logs"`, `"data/reqmgmt/reqmgmt.db"` 等）。

## 整体架构

```
┌──────────────────────────────────────────────────┐
│  前端 (frontend/)                                  │
│  Next.js 15 + CopilotKit v2 + shadcn/ui            │
│  3 栏布局: TodoPanel | CopilotChat | ToolCallPanel │
│  通过 /api/copilotkit 代理到后端 127.0.0.1:8000     │
└──────────────────┬───────────────────────────────┘
                   │ AG-UI 协议 (CopilotKitMiddleware)
┌──────────────────┴───────────────────────────────┐
│  后端 (main.py)                                    │
│  FastAPI + LangGraph Agent + CopilotKit AG-UI      │
│  Agent 名: "deepmind"                              │
└──────────────────────────────────────────────────┘
```

基于 `deepagents`（LangGraph Agent 框架）构建的**需求管理 Agent 系统**。入口为 `python main.py`，一键启动前后端。

### Agent 拓扑（SubAgent 模式）

```
Main Agent ("deepmind")
├── tools: [query_reqmgmt, web_search]  # 自然语言 → SQL 数据库查询, 互联网搜索
├── middleware: [CopilotKitMiddleware, InvocationLoggingHandler, ContextMonitorMiddleware]
└── subagents:
    └── "req-parse"                     # 持有文档解析工具 + skill
        ├── tools: [parse_docx_outline, extract_entities, store_entities]
        └── skills: [skills/req-parse]
```

- **主 Agent 不能**直接调用文档解析工具 — 必须通过 `task()` 工具委托给 `req-parse` 子 Agent。这强制执行了工具-技能绑定。
- `CopilotKitMiddleware` 将 LangGraph Agent 桥接到 AG-UI 协议，供前端 CopilotKit 消费。

### 前端（`frontend/`）

Next.js 15 应用，使用 CopilotKit v2 (`@copilotkit/react-core/v2`) 与后端通信：

- **`app/layout.tsx`** — `CopilotKit` provider，`runtimeUrl="/api/copilotkit"`，`agent="deepmind"`
- **`app/page.tsx`** — 3 栏布局: `TodoPanel` | `CopilotChat` | `ToolCallPanel` + `InterruptHandler`
- **`app/api/copilotkit/route.ts`** — API Route Handler，将请求代理到后端 `127.0.0.1:8000/copilotkit`
- **`components/TodoPanel.tsx`** — 左侧栏，通过 `useAgent({ agentId: "deepmind" })` 读取 `agent.state.todos`
- **`components/ToolCallPanel.tsx`** — 右侧栏，从 `agent.messages` 中提取工具调用（AG-UI 格式：assistant 消息中的 `toolCalls` + tool 角色的 `toolCallId` 结果）
- **`components/InterruptCard.tsx`** — 通过 `useInterrupt({ agentId: "deepmind" })` 处理 LangGraph `interrupt()`，渲染确认/取消卡片

### Text2SQL 流水线（`agents/text2sql/`）

独立的 LangGraph 工作流，可将自然语言转为 SQL 查询，可配合 deepagents 或单独使用：

`START → parse_query → sql_generate → execute_sql → render → END`

- `graph.py` — 编译 StateGraph，使用 MemorySaver 做 checkpoint
- `_nodes.py` — 异步/同步节点函数；`sql_generate` 使用 `get_llm("text2sql")` 并加载 `prompts/text2sql.j2` 提示词；`render` 输出 Markdown 表格
- `routing.py` — 条件边（出错时跳过后续节点直接到 render）
- `state.py` — TypedDict，字段：`user_input`, `intent`, `sql`, `query_result`, `answer`, `error`
- `agents/text2sql_agent.py` — 门面类 `ReqMgmtText2SQLAgent`，负责初始化数据库、编译工作流、对外暴露 `query()`/`query_sync()`

### LLM 层（`llm/`）

基于角色（Role）的适配器工厂，按 temperature 缓存。通过 `__init_subclass__` 自动注册角色：

| 角色 | 类 | 环境变量 |
|------|------|----------|
| `"default"` | `DefaultLLM` | `LLM_MODEL_ID`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_TIMEOUT` |
| `"text2sql"` | `Text2SQLLLM` | `TEXT2SQL_MODEL_ID`, `TEXT2SQL_API_KEY`, `TEXT2SQL_BASE_URL`（回退到 LLM_*） |

用法：`get_llm("default", temperature=0.0)` 返回缓存的 `BaseChatModel`。

### ORM 与数据库（`models/reqmgmt.py`）

需求管理领域的 SQLAlchemy 模型 — **11 张表**，涵盖 products、requirement_models、requirement_items、product_requirements、parts、test_cases、test_items 以及 4 张关系/链接表。每个模型类都有 `TABLE` 和 `LLM_NODE_NOTE` 类变量，用于生成 schema 文本。`build_schema_text()` 从 ORM 元数据生成 Text2SQL 提示词描述。`init_db()` 初始化 SQLite（通过 sqlite3），`get_session()` 返回作用域会话。

数据库路径：`data/reqmgmt/reqmgmt.db`（由 `utils/paths.py` 管理）。

### 文档解析工具链（`tools/`）

需求文档入库的三阶段流水线：

1. **`parse_docx_outline`** — 纯代码实现，通过 python-docx 将 .docx 解析为结构化标题树。返回 JSON，包含 `sections`、`stats` 和 `llm_structure`（扁平化的节列表，带稳定 ID，供 LLM 分类使用）。
2. **`extract_entities`** — 异步，基于 LLM 将文档节分类为实体类型。对每个文档分组并行调用 LLM（最多 6 并发），然后确定性组装 ID 和外键关系。
3. **`store_entities`** — 通过 SQLAlchemy `session.merge()` 批量 upsert（幂等，INSERT OR REPLACE 语义）。

另有 `query_reqmgmt` 工具，将 `ReqMgmtText2SQLAgent` 封装为 deepagents 兼容工具。

### Web 搜索工具（`tools/web_search.py` + `tools/mcp_client.py`）

通过智谱 MCP Broker 连接 Zhipu Web Search MCP 服务器，提供实时互联网搜索。

- `tools/mcp_client.py` — 封装 `langchain_mcp_adapters.MultiServerMCPClient`，提供 `MCPClient` 类和 `get_zhipu_mcp_config()` 配置工厂
- `tools/web_search.py` — `create_web_search_tool(search_engine)` 工厂函数，返回 deepagents 兼容的 `@tool` 函数。支持 4 种搜索引擎：`search_pro`、`search_std`、`search_pro_sogou`、`search_pro_quark`
- 配置：`.env` 中 `ZHIPU_API_KEY`（已配置）
- 返回结构化 `WebSearchResult`（含 Markdown 格式化输出和链接列表）

### 记忆系统（`memory/backends.py`）

通过 `deepMind.toml` 配置。创建 `CompositeBackend`：
- `/` → `FilesystemBackend(virtual_mode=True)` — 可读取真实文件，写入为虚拟操作（不污染磁盘）
- `/memories/` → `StoreBackend` — 长期记忆，跨会话持久化，按 user_id 隔离
  - 文件列表从 `deepMind.toml` 的 `[memory.long_term].files` 加载
- `/thread/` → `StateBackend` — 短期记忆，同 thread 范围内有效

通过 `[backend].type` 切换后端类型：`"memory"`（InMemoryStore）或 `"postgres"`（PostgresStore）。

### 配置

- **`.env`** — LLM API Key、模型 ID、Base URL、超时等。还支持 `VVLM_*`（视觉/多模态）和 `SENSEVOICE_*`（语音识别）配置。
- **`deepMind.toml`** — 用户默认值、记忆文件列表、后端类型选择。
- **`utils/paths.py`** — `DataPaths` 单例，管理 `data/` 目录结构（logs、output、memory、files）。支持 `DATA_DIR` 环境变量与 CodeMind 共享数据。

### 中间件（`middleware/`）

- **`CopilotKitMiddleware`** — 将 LangGraph Agent 接入 AG-UI 协议，使前端 CopilotKit 可通过标准化事件流与 Agent 通信。
- **`InvocationLoggingHandler`** — 双用途日志记录器：
  1. 作为 `BaseCallbackHandler` — 传入 LangGraph `ainvoke(config={"callbacks": [handler]})`
  2. 作为 `AgentMiddleware` — 使用 `.as_middleware()` 适配 deepagents
  将调用记录以 JSON 格式写入 `data/logs/<thread_id>_<timestamp>.json`，包含 LLM 调用次数、工具执行、耗时和错误信息。
- **`ContextMonitorMiddleware`** — 上下文监控。

### 技能

`skills/req-parse/SKILL.md` — 定义文档解析的四步工作流：解析 → 提取 → 存储 → 确认。Skill 的 `allowed-tools` 字段将子 Agent 限制为只能使用三个解析工具。
