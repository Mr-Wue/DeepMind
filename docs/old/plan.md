# CodeMind 架构改进计划

> 本计划基于对 `CodeMind-AI-Guide.md` 及当前代码库的分析，按严重程度排序。  
> 供 AI 协作者（或未来的自己）按优先级逐步执行。

---

##  severity 说明

| 等级 | 含义 |
|------|------|
| **P0** | 架构阻断。不改则后续功能（多 Agent、独立 Agent）无法干净接入。 |
| **P1** | 技术债。当前可用，但会导致代码腐烂、维护成本递增。 |
| **P2** | 扩展准备。为 RAG、Web 搜索、论文翻译等新能力铺路的结构性工作。 |
| **P3** | 可选优化。有收益，但可延后；看不懂或成本过高时可跳过。 |

---

## P0 — 架构阻断（最高优先级）

### P0-1 [DONE] `ExecutionContext` 下沉到 `base/context.py`

- **问题**：`engine/base/node.py`、`engine/workflow/skills/render.py` 等引擎层文件通过延迟导入反向依赖 `agents/context.py`，形成 `engine → agents` 的反向依赖。
- **影响**：Agent 层与引擎层无法独立演进；新增 Agent 时容易触发循环导入。
- **方案**：将 `ExecutionContext`、`EventKind`、`Event` 等核心定义迁移到 `base/context.py`；`agents/context.py` 改为向后兼容 re-export。
- **相关文件**：`base/context.py`（新建）、`agents/context.py`、`engine/base/node.py`、`engine/workflow/graph_query/_nodes_*.py`、`frontend/log_viewer.py`、`router.py`
- **状态**：✅ 已完成。

---

### P0-2 Agent 职责声明 + 二元意图识别（替代全局路由拆分）

- **问题**：`IntentClassifySkill` 目前同时承担两件事：
  1. **场景匹配判断**（属于当前 Agent 职责 vs 不属于）；
  2. **任务拆解**（`sub_queries`）——把代码库查询拆成多条 SQL 子查询。  
  原有方案是拆出独立的 `RouterSkill`，但实践发现：
  - 规则/关键词做路由 → 不准（"帮我查订单服务"、"这个项目怎么设计的"难以匹配）
  - 单独 LLM 做路由 → 浪费（后面 IntentClassifySkill 还要调一次）
  - 用户选错场景时系统需要"纠错" → 复杂度高、收益低
- **影响**：新增多 Agent 时，每个 Agent 需要独立的意图判断逻辑。
- **方案**：
  - 在 `BaseAgent` 新增类属性 `agent_scope`（一句话职责描述，如"代码库结构化数据的查询与分析"）；
  - `IntentClassifySkill` 接收 `agent_scope` 并注入 prompt，LLM 根据职责描述判断：
    - **`graph_query`**：属于当前 Agent 职责 → 拆解 `queries`
    - **`scene_mismatch`**：不属于当前 Agent 职责 → 返回 `suggestion`（建议切换的场景）
  - `IntentResult` 只保留两种意图（去掉 `other`，后续由 WebSearchAgent/RAGAgent 替代）；
  - **不新增 RouterSkill**，用户手动选场景时系统信任用户选择（不纠错），每个 Agent 自行兜底 `scene_mismatch`；
  - `auto` 场景后续可在 `router.py` 层做轻量分类（关键词 + LLM 兜底），而非全局路由。
- **Prompt 改进**：原 prompt 示例中 `scene_mismatch` 的建议是写死的（"网络搜索"、"论文翻译"），应改为动态生成（根据问题内容建议合适的场景）。
- **相关文件**：`agents/base_agent.py`（加 `agent_scope`）、`agents/graph_query_agent.py`（声明职责）、`engine/workflow/skills/intent_classify.py`（`IntentResult` 改为二元 + prompt 优化）、`engine/workflow/graph_query/state.py`（加 `agent_scope` 字段）、`engine/workflow/graph_query/runner.py`（传入 `agent_scope`）、`app.py`（处理 `scene_mismatch` 提示）

---

### P0-3 降级为 P2 — 引入 `StreamBus` 统一事件与流式输出（延后）

- **问题**：当前有两套并行的输出管道：
  - 业务事件：`ExecutionContext.on_event` → `frontend/log_viewer.py` → Chainlit Step
  - 渲染流式：`ExecutionContext.on_render_chunk` → `frontend/md_renderer.py` → Chainlit Message  
  两者独立投递，Render Skill 出错时前端消息卡住，但 Step 轨迹仍在跑，体验不一致。
- **影响**：每新增一个需要流式展示的 Skill（如 Web 搜索的"来源卡片"、论文翻译的"章节进度"），都要在 Skill 内部硬编码 `ctx.on_render_chunk` 的调用，耦合深。
- **当前评估**：`ExecutionContext` 已下沉到 `base/context.py`，解耦完成。当前只有 Chainlit 前端 + Render Skill 需要流式，收益有限。
- **方案**：延后至需要多前端（Gradio + Chainlit）或多流式 Skill 时再实施。
- **相关文件**：`agents/stream.py`（新建）、`agents/context.py`、`frontend/log_viewer.py`、`frontend/md_renderer.py`、`engine/workflow/skills/render.py`
- **状态**：⏸ 降级为 P2，当前不急

---

## P1 — 技术债

### P1-1 明确 Tool 路径与 Text2SQL 路径的主次关系

- **问题**：`engine/workflow/tools/` 下有一套完整的 Tool 注册、过滤、调度机制（`ToolRegistry`、`auto_codebase_tools.py`、`@tool_method`），但主工作流 `GraphQueryWorkflow` 当前以 Text2SQL 为默认路径，工具路径未被主工作流使用。
- **影响**：双轨维护：改 ORM/实体时需要同步改 `schema.sql`（给 Text2SQL）和 `@tool_method` 注册（给工具路径），容易遗漏。
- **方案**：
  - **选项 A**（推荐）：明确 Text2SQL 为主路径，Tool 系统降级为**外部脚本/独立 Agent** 使用（如 Maven `.dot` 导入、批量写入）。`GraphQueryWorkflow` 不再引用 Tool 调度器。
  - **选项 B**：如果未来需要混合（SQL + 外部 API），把 Tool 执行升级为 LangGraph 的一个节点（`tool_execute`），由 `intent_classify` 或路由决定走 `sql_generate` 还是 `tool_dispatch`。
- **相关文件**：`engine/workflow/tools/registry.py`、`engine/workflow/skills/tool_dispatcher.py`、`engine/workflow/graph_query/graph.py`

---

### P1-2 清理剩余的延迟导入

- **问题**：虽然 `ExecutionContext` 已下沉，但项目中仍有部分延迟导入（如 `agents/graph_query_agent.py` 里延迟导入 `GraphQueryRunner`）。这些延迟导入曾是循环依赖的补丁，现在应评估是否仍需保留。
- **影响**：延迟导入会掩盖真正的架构问题；IDE 和类型检查器无法正确推断类型。
- **方案**：
  - 逐个检查 `if TYPE_CHECKING:` 和函数内 `from xxx import yyy`；
  - 对于 `engine` 层依赖 `base` 层的导入，改为顶部导入；
  - 如果 `agents/graph_query_agent.py` 与 `engine.workflow.graph_query` 之间仍有循环，考虑把 `GraphQueryRunner` 的构造延迟到 `_invoke` 中（已如此），保留此延迟导入但更新注释。
- **相关文件**：`agents/graph_query_agent.py`、`engine/base/node.py` 等

---

### P1-3 Checkpoint 持久化（进程重启不丢会话）

- **问题**：`MemorySaver`（LangGraph checkpoint）和 `InMemoryStore`（用户记忆 RAG）都是内存存储，进程重启后会话上下文丢失。
- **影响**：对于"个人本地工具"当前可接受；但如果计划长期后台运行（如 chainlit 服务），用户体验会受损。
- **方案**：
  - LangGraph checkpoint：将 `MemorySaver` 替换为 `SqliteSaver`（或 `PostgresSaver`），数据落在 `data/meta/`；
  - `InMemoryStore`：考虑定期序列化到磁盘（LangGraph 的 `InMemoryStore` 本身无持久化，可自定义 `JsonFileStore` 套壳）。
- **优先级调整**：如果使用 chainlit 常驻服务，这个优先级应该提升。
- **相关文件**：`engine/base/workflow.py`、`memory/store.py`

---

## P2 — 未来扩展准备

### P2-1 RAG 接入代码库文档/规范

- **问题**：代码库规范、历史设计文档等当前未进入检索体系。
- **方案**：
  - 复用 `base/llm/embeddings.py` + `memory/store.py` 的 Chroma 向量存储；
  - 在 `GraphQueryWorkflow` 中 `START → memory_compensate` 之后，新增可选的 `rag_retrieve` 节点；
  - 当 `scene_key` 为 `data_query` 且用户问题含"规范/文档/设计"时，检索相关文本注入 `state["rag_context"]`，供 `render` 节点参考。
- **相关文件**：`engine/workflow/graph_query/graph.py`、`engine/workflow/graph_query/state.py`、`engine/workflow/skills/user_memory_rag.py`（可复用模式）

---

### P2-2 Web 搜索作为独立 Agent

- **问题**：Web 搜索没有代码结构库概念，不需要 Text2SQL，也不适合塞进 `GraphQueryWorkflow`。
- **方案**：
  - 新增 `WebSearchAgent`，继承 `BaseAgent`；
  - 内部走纯 LangChain LCEL（搜索 → 摘要），不经过 LangGraph；
  - 在 `router.py` 的 `AgentRouter._agents` 中注册。
- **相关文件**：`agents/web_search_agent.py`（新建）、`router.py`

---

### P2-3 论文翻译等批量任务不经过 LangGraph

- **问题**：结构化论文翻译通常是"解析 → 分段 → 并行翻译 → 按模板组装"，不需要跨轮 checkpoint 和状态机。
- **方案**：
  - 新增 `PaperTranslateAgent`，继承 `BaseAgent`；
  - `_invoke` 内直接调用 `PaperTranslateSkill`（或 Skills 组），不走 `Runner`/LangGraph；
  - 保留 Agent 生命周期（日志、钩子、文件落盘）的一致性，但底层不强制使用 LangGraph。
- **相关文件**：`agents/paper_translate_agent.py`（新建）、`router.py`

---

## P3 — 可选优化

### P3-1 自定义事件系统与 LangChain Callback 的桥接

> **前置说明**：这一条不是让你推翻 `ExecutionContext`，而是解释两套系统如何互通。

#### 当前现状（两套并行系统）

| 系统 | 事件源 | 消费方 | 当前用法 |
|------|--------|--------|---------|
| **A: 你的 `ExecutionContext`** | `BaseNode.__call__`、`BaseSkill`、`_record_sql_ctx` | 控制台、Chainlit Step（`log_viewer.py`）、`data/logs/*.json` | `ctx.record(EventKind.NODE_START, ...)` |
| **B: LangChain Callback** | `ChatOpenAI.invoke()`、`chain.ainvoke()` | `ContextCallbackHandler`（`agents/llm_callback.py`） | LangChain 自动触发 `on_chat_model_start/end` |

你现在做的：在系统 B 里埋了一个"翻译官"（`ContextCallbackHandler`），把 LangChain 的 LLM 事件翻译成系统 A 的事件，再写进 `ExecutionContext`。

#### "桥接"是什么意思？

桥接是**双向**的：
- **你已完成的（B → A）**：LangChain 回调 → `ExecutionContext.record()` ✅
- **建议补充的（A → B）**：让 `ExecutionContext` 也实现 LangChain 的 `BaseCallbackHandler`，或者让 `ExecutionContext` 的事件流能被 LangChain 的 `astream_events()` 消费。

#### 为什么要做 A → B？

如果你未来需要：
- 接入 **LangSmith** 做云端 trace（LangSmith 只认 LangChain callback，不认你的 `EventKind`）；
- 让前端统一通过 `chain.astream_events()` 消费所有事件（包括 `NODE_START`、`SQL_GENERATED`），而不是维护 `on_event` + `on_render_chunk` 两条管道；
- 让其他 LangChain 组件（如自定义 output parser、retriever）能监听你的节点事件。

#### 最简单的桥接方式（不改现有架构）

```python
# agents/stream.py 或 base/context.py
class ExecutionContext:
    # ... 现有代码 ...

    def to_langchain_callback(self) -> "BaseCallbackHandler | None":
        """返回一个只读回调句柄，供 LangChain 生态消费。"""
        from .llm_callback import ContextCallbackHandler
        return ContextCallbackHandler()  # 已有

    def emit_as_langchain_event(self, kind: EventKind, payload: dict):
        """将 ExecutionContext 事件转换为 LangChain 的 astream_events 格式。"""
        # 仅在需要接入 LangSmith / Langfuse 时实现
        pass
```

#### 结论

这不是紧急需求。如果你一直保持在"本地工具、自己维护"的范围内，完全可以**不做**；只有当出现以下信号时才考虑：
- 你想接入 LangSmith / Langfuse / 其他 LangChain 生态观测工具；
- 你觉得 `frontend/log_viewer.py` + `md_renderer.py` 两条管道维护成本越来越高；
- 你开始用 `astream_events()` 做其他功能，发现 ExecutionContext 的事件进不了同一个流。

---

## 附录：P0-2 详细实施规格

### 改动清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `agents/base_agent.py` | 加 `agent_scope: str = ""` 类属性 | 所有 Agent 的基础职责声明 |
| `agents/graph_query_agent.py` | 声明 `agent_scope = "代码库结构化数据的查询与分析"` | GraphQueryAgent 的职责描述 |
| `agents/graph_query_agent.py` | 构造 Runner 时传入 `agent_scope` | `GraphQueryRunner(agent_scope=self.agent_scope)` |
| `engine/workflow/skills/intent_classify.py` | `IntentResult` 改为二元：`graph_query` / `scene_mismatch` | 去掉 `other`，加 `suggestion` 字段 |
| `engine/workflow/skills/intent_classify.py` | prompt 优化（见下方 Prompt 设计） | `{agent_scope}` 占位符动态注入 |
| `engine/workflow/skills/intent_classify.py` | `classify()` 加 `agent_scope` 参数 | `async def classify(..., agent_scope: str = "")` |
| `engine/workflow/graph_query/state.py` | state 加 `agent_scope: str` 字段 | 供 IntentClassifyNode 读取 |
| `engine/workflow/graph_query/state.py` | `initial_state()` 加 `agent_scope` 参数 | 由 Runner 传入 |
| `engine/workflow/graph_query/runner.py` | Runner 加 `agent_scope` 属性 | 构造时接收并透传 |
| `engine/workflow/graph_query/_nodes_tool.py` | 从 state 读 `agent_scope` 传入 `classify()` | `IntentClassifyNode.execute()` |
| `app.py` | 捕获 `scene_mismatch` 给用户友好提示 | 展示 `suggestion` 建议切换的场景 |
| `routing.py` | **不用改** | `scene_mismatch` 不等于 `graph_query`，自然走 `general_answer` |

### IntentClassifySkill Prompt 设计

```python
_SYSTEM = """\
你是查询助手，判断用户问题是否属于当前场景，如果属于则拆解查询步骤。

【当前场景】{agent_scope}

只输出JSON，两种格式：
{{"intent":"graph_query","queries":["查询语句"]}}
{{"intent":"scene_mismatch","suggestion":"建议切换到xxx","queries":[]}}

判断规则：
- 用户问题属于当前场景职责 → graph_query
- 不属于当前场景职责 → scene_mismatch，suggestion 写明应该切换到什么场景（根据问题内容推断）

【数据库表结构与字段语义】
{schema}

queries 拆分规则（仅 graph_query 时需要）：

规则A【全字段优先拆】：仅当用户**明确**要求某张表的「全部信息/所有字段/详情」时，该表才单独一步全字段查询；其余表只需定位主键 id。
- 每张需要全字段的表单独占一步，用前步 id 过滤

规则B【表数量】：不需要全字段时，按涉及表数量决定：
- 涉及1~2张表 → 单条查询
- 涉及3张及以上表 → 必须拆分，每步1~2张主表，最多3步

规则C【跨步引用】：拆分时后续步骤必须说明用前步的哪个字段过滤哪张表，不写具体 id 值。

规则D【中文·硬约束，违反则整条 queries 算错】：
- 中文业务词（订单、电商、用户等）必须用 description 列筛选，禁止用 name 列
- 仅英文/拉丁标识（order-service、Controller）才允许用 name 列
- 自检：输出 JSON 前逐句检查 queries——若句子里有汉字业务词却出现「name」作筛选列，必须改成 description

【示例 — 属于当前场景】
输入：order-service 下有哪些模块
{{"intent":"graph_query","queries":["查 name=order-service 的项目及其所有模块(modules.project_id)和类(classes.module_id)，返回 project_id、module_id、class_id"]}}

输入：查询订单服务下所有的模型和类
{{"intent":"graph_query","queries":["查 description 含订单服务的项目及其业务模型，返回 project_id、biz_models.id","用前步 biz_models.id 查对应类(classes.biz_model_id)，返回 class_id"]}}

【示例 — 不属于当前场景】
输入：帮我翻译这篇论文
{{"intent":"scene_mismatch","suggestion":"论文翻译","queries":[]}}

输入：今天天气怎么样
{{"intent":"scene_mismatch","suggestion":"通用问答","queries":[]}}"""
```

**Prompt 改进要点**：
1. 去掉了原来写死的 `scene_mismatch` 示例（"网络搜索"、"论文翻译"），改为**动态推断**（根据问题内容）
2. `suggestion` 的描述改为"根据问题内容推断"，让 LLM 自主判断
3. 保留 `graph_query` 的查询拆解示例（这些是领域规则，需要保留）

### 未来新增 Agent 示例

```python
# agents/web_search_agent.py
class WebSearchAgent(BaseAgent):
    name = "WebSearchAgent"
    agent_scope = "网络信息搜索与摘要"
    
    async def _invoke(self, ctx: ExecutionContext) -> dict[str, Any]:
        # 纯 LCEL 或简单 LangGraph，不需要 Text2SQL
        ...

# router.py
class AgentRouter:
    def __init__(self) -> None:
        self._agents: dict[str, type[BaseAgent]] = {
            "GraphQueryAgent": GraphQueryAgent,
            "WebSearchAgent": WebSearchAgent,     # 新增
        }
```

**关键**：IntentClassifySkill 的 prompt **完全不用改**，`{agent_scope}` 占位符自动适配新 Agent。

### 设计原则

| 原则 | 说明 |
|------|------|
| **信任用户选择** | 用户手动选场景 = 明确意图，系统不纠错 |
| **Agent 自行兜底** | 每个 Agent 在 `_invoke` 中捕获 `scene_mismatch`，友好提示 |
| **一次 LLM 调用** | IntentClassifySkill 同时做场景匹配 + 任务拆解，不额外调 LLM |
| **Prompt 自适应** | `{agent_scope}` 占位符让同一 Skill 适配所有 Agent |
| **不新增 RouterSkill** | 避免双 LLM 调用 + 全局路由膨胀 |

---

*文档版本：v1.1 | 生成时间：2026-04-23 | 更新：2026-04-24*