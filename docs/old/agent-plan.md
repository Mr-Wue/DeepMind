# CodeMind 能力单元与自主 Agent 架构设计规格 v2.0

> **目标读者**：执行开发的 AI 协作者
> **原则**：本规格是实现的唯一事实来源，不得自行增加、删减或修改设计。有疑问须向人类确认。

---

## 修订记录

| 版本 | 日期 | 变更说明 |
|------|------|---------|
| v1.0 | - | 初始设计（`$step_N.field` 参数传递、scan_module、Dispatcher 在 Agent 内部） |
| v2.0 | 2026-05-05 | 重构参数传递为 `_ctx` 注入，Tool/Skill 分拆，Agent 注册为能力，新增 TemplateRecallSkill |
| v2.1 | 2026-05-05 | Text2SQL 降级为 Agent 内部组件（不注册），补充 §6.3 双模式详解，移除 Task 2.5 |
| v2.2 | 2026-05-05 | **第一轮实施**：BaseCapability 基础设施 + Executor _ctx 注入 + Agent 注册；**第二轮实施**：Render/WebSearch/TemplateRecall 接 _ctx，WebSearch 分拆，记忆写入；**修复**：所有能力类改为多继承 BaseCapability（原 BaseSkill/BaseAgent 不继承导致 self.extract_context() 运行时 AttributeError） |

**v2.0 核心变更**：

1. **`$step_N.field` → `_ctx` 注入**：能力单元间不再传递字段级引用，而是注入完整执行上下文（`CapabilityRunContext`），由能力单元内部的 LLM 自行提取所需信息。
2. **Tool/Skill 分拆**：纯 API 封装（无 LLM）不再是能力单元，不注册到 CapabilityRegistry；由 Skill 内部持有。
3. **Agent 注册为能力**：GraphQueryAgent、GenericAgent 带 `capability_meta` + `execute()`，可被 Planner 编排。
4. **BaseCapability 提供 LLM 基础设施**：`get_llm()`、`extract_context()`、`format_context()` 作为基类方法。

---

## 1. 架构总览

### 1.1 分层模型

```
┌─────────────────────────────────────────────────┐
│                   CapabilityRegistry              │
│  ┌───────────┐  ┌──────────┐  ┌───────────────┐ │
│  │  Agent     │  │  Skill   │  │ (Tool 不注册)  │ │
│  │ category=  │  │ category=│  └───────────────┘ │
│  │ "agent"   │  │ "skill"  │                      │
│  ├───────────┤  ├──────────┤  ┌───────────────┐ │
│  │GraphQuery │  │Planner   │  │ WebSearchCli   │ │
│  │Agent      │  │Skill     │  │ent (pure API)  │ │
│  │Generic    │  │Render    │  │ ExcelWriter    │ │
│  │Agent      │  │Skill     │  │ (pure tool)    │ │
│  └───────────┘  │WebSearch │  └───────────────┘ │
│                  │Parser    │                     │
│                  │Text2SQL  │   ← Skill 内部持有 │
│                  │Template  │   ← 不暴露给       │
│                  │Recall    │   ← Planner        │
│                  └──────────┘                     │
└─────────────────────────────────────────────────┘
```

### 1.2 三类能力的注册规则

| 类型 | 注册到 CapabilityRegistry？ | 持有 LLM？ | 继承链 | 示例 |
|------|---------------------------|-----------|--------|------|
| **Agent** | 是（`category="agent"`） | 是（内部 workflow 调用） | `BaseAgent → MemoryAgent → XxxAgent` + `capability_meta` + `execute()` | GraphQueryAgent, GenericAgent |
| **Skill** | 是（`category="skill"`） | 是（自身就是 LLM 决策单元） | `BaseSkill + capability_meta + execute()` | PlannerSkill, RenderSkill, WebSearchParserSkill |
| **Tool** | **否**（纯 API 封装） | 否 | 普通类或函数，由 Skill 内部持有 | WebSearchClient, ExcelWriter |

**Tool 不是能力单元**——它被 Skill 内部组合，不从 `__init_subclass__` 自动注册，不在 Planner 的能力清单中。

---

## 2. 核心数据流：`_ctx` 注入

### 2.1 CapabilityRunContext

每个能力单元执行时，Executor 自动注入 `_ctx` 参数，包含**完整的执行上下文**而非字段级引用。

```python
class StepOutput(BaseModel):
    step_id: int
    capability_name: str
    description: str = ""       # Planner 在该步骤写的 reason
    output: Any = None           # 原始 output_data（完整）
    output_summary: str = ""     # 截断摘要（≤500 字符）

class CapabilityRunContext(BaseModel):
    step_id: int
    intent: str = ""             # 记忆补偿后的全局用户意图
    reason: str = ""             # Planner 给本步骤写的 reason
    previous: list[StepOutput] = []
    user_profile: dict = {}
    meta: dict = {}              # 扩展预留
```

### 2.2 Executor 注入方式

```python
async def _execute_plan(plan, exec_context):
    work_memory = {}
    for step in plan.steps:
        cap = CapabilityRegistry.get_by_name(step.capability_name)
        meta = cap.meta

        # 构造 _ctx
        _ctx = CapabilityRunContext(
            step_id=step.step_id,
            intent=exec_context["intent_input"],
            reason=step.reason or "",
            previous=[
                StepOutput(
                    step_id=s.step_id,
                    capability_name=s.capability_name,
                    output=work_memory[s.step_id],
                )
                for s in completed_steps
            ],
        )

        # 注入到能力参数中
        params = {
            **step.params,     # Planner 显式参数（可选）
            "_ctx": _ctx,      # Executor 自动注入
        }

        result = await cap.execute(**params)
        work_memory[step.step_id] = result.output_data
```

### 2.3 能力单元内部如何使用

**典型 Skill（有 LLM）**：
```python
async def execute(self, query=None, _ctx=None):
    # 1. 从上下文提取信息
    terms = await self.extract_context(
        _ctx, "列出所有需要搜索的关键词，一行一个"
    )
    # 2. 调内部 Tool
    raw = await self._client.search(terms or query)
    # 3. LLM 结构化输出
    result = await self.get_llm().ainvoke(f"整理结果：{raw}")
    return CapabilityResult(output_data=result)
```

**Agent 作为能力**：
```python
async def execute(self, query=None, _ctx=None):
    if _ctx:
        refined = await self.extract_context(
            _ctx, "提取需要查询的关键词"
        )
        query = refined or query or ""
    runner = GraphQueryRunner()
    state = await runner.arun(query)
    return CapabilityResult(output_data=state)
```

**纯工具能力（非 LLM）**：
```python
async def execute(self, data=None, _ctx=None):
    # 非 LLM 能力，用 extract_context 会报错或退化
    # 它只认规整的 list[dict]，否则 error → 触发 execution_policy
    ...
```

---

## 3. 已实现的组件

以下组件已在 v1.0 实现，v2.0 保留不动：

### 3.1 CapabilityMeta 数据类

`engine/base/capability.py`。不变。

```python
class CapabilityMeta(BaseModel):
    name: str
    category: str                         # "tool" | "skill" | "agent"
    tags: list[str] = []
    short_description: str                # ≤100 字符
    input_schema: dict
    output_schema: dict
    dependencies: list[dict] = []
    execution_policy: dict = {}           # on_error, on_empty_result, fallback, max_retries
    needs_session_context: bool = False
    needs_user_profile: bool = False
    output_field: str = "result"
    permissions: list[str] = []
    version: str = "1.0.0"
```

### 3.2 CapabilityResult

`engine/base/capability.py`。不变。

```python
class CapabilityResult(BaseModel, Generic[T]):
    capability_name: str
    output_data: T
    metadata: dict = {}
    error: Optional[str] = None
```

### 3.3 CapabilityRegistry

`engine/base/capability_registry.py`。不变。支持：
- `register(obj)` — 显式注册
- `register_capability` — 类装饰器
- `register_from_class(cls)` — 从类自动实例化注册（供 `__init_subclass__` 调用）
- `scan_module(module_path)` — 扫描模块（已存在，保留向后兼容）
- `scan_package(package_path)` — 逐个导入 .py 文件，独立 try/except（v2.0 新增，在后台线程调用）
- `get_by_name(name)` / `list_by_tags(tags)` / `all_meta()` / `names()` / `count()`
- `load_md_supplements(md_dir)` — MD 补充合并

### 3.4 PlannerSkill

`engine/workflow/skills/planner_skill.py`。模型不变，prompt 中 `template_section` 由 TemplateRecallSkill 提供。

```python
class PlanStep(BaseModel):
    step_id: int
    capability_name: str
    params: dict = {}
    depends_on: list[int] = []
    reason: str = ""

class Plan(BaseModel):
    steps: list[PlanStep]  # ≤7 步
```

**v2.0 变化**：`params` 不再使用 `$step_N.field` 占位符。Planner 在 `reason` 中详细描述每个步骤需要的输入来自何处。Executor 通过 `_ctx` 注入后，由能力单元自行解析。

### 3.5 AutonomousAgent 主流程

`agents/autonomous_agent.py`。流程不变：
1. 记忆补偿 → 2. 模板召回 → 3. 计划生成 → 4. 用户确认 → 5. 执行 → 6. 汇总 → 7. 写记忆

**v2.0 变化**：
- 步骤 2 由 TemplateRecallSkill 实现
- 步骤 5 使用 `_ctx` 注入替代 `resolve_params`
- 步骤 7 通过 `ctx.record_memory_payload()` 补丁实现

### 3.6 PlanConfirmView

`frontend/plan_confirm.py`。不变。

### 3.7 场景配置与路由

`config.py` + `router.py` + `gate.py`。不变。

---

## 4. BaseCapability 基础设施（v2.0 新增）

```python
class BaseCapability(ABC):
    meta: CapabilityMeta

    @abstractmethod
    async def execute(self, **kwargs) -> CapabilityResult:
        """执行能力，返回强类型结果。
        
        调用方（Executor）会注入 _ctx 参数：
          kwargs["_ctx"] = CapabilityRunContext(...)
        能力单元可以选择使用或忽略。
        """

    # ------------------------------------------------------------------
    # 基类提供的 LLM 基础设施
    # ------------------------------------------------------------------

    def get_llm(self, role: str = "default", **llm_kwargs):
        """统一获取 LLM 实例。
        
        所有能力单元通过此方法获取 LLM，而非直接调用 base.llm.get_llm()。
        role 可通过子类或 meta 属性覆盖。
        """

    async def extract_context(self, _ctx: CapabilityRunContext | None, instruction: str) -> str:
        """用 LLM 从 _ctx.previous 中提取本步骤所需的信息。
        
        典型用法：
          terms = await self.extract_context(_ctx, "列出需要搜索的关键词")
        
        Args:
            _ctx: Executor 注入的上下文。为 None 或不含 previous 时返回空串。
            instruction: 描述需要提取什么信息。
        
        Returns:
            LLM 提取结果的文本。
        """

    def format_context(self, _ctx: CapabilityRunContext | None) -> str:
        """将 _ctx 格式化为纯文本段落（不调用 LLM）。
        
        适用于将上下文注入 prompt 模板变量。
        """
```

---

## 5. Tool/Skill 分拆规则

### 5.1 分拆原则

| 特征 | 作为 Tool（不注册） | 作为 Skill（注册为能力） |
|------|-------------------|------------------------|
| 是否调 LLM | 否 | 是 |
| 是否理解 `_ctx` | 否 | 是 |
| 是否被 Planner 发现 | 否 | 是 |
| 是否被 Executor 调度 | 否 | 是 |
| 示例 | WebSearchClient, ExcelWriter | WebSearchParserSkill, ExportExcelSkill |

### 5.2 已有组件的分拆方案

| 当前 | 新形态 | 说明 |
|------|--------|------|
| `WebSearchSkill` | `WebSearchClient` (Tool) + `WebSearchParserSkill` (Skill+Capability) | Client 仅封装 API，ParserSkill 加 LLM 解析/结构化 |
| `Text2SQLSkill` | `Text2SQLSkill`（**不注册**，Agent 内部组件） | 不加 `capability_meta`、不加 `execute()`，由 Agent 直接调用 `generate()`/`fix()` |
| `RenderSkill` | `RenderSkill` (Skill+Capability) | 保持原样，`execute()` 接 `_ctx` |
| `GraphQueryAgent` | `GraphQueryAgent` (Agent+Capability) | 加 `capability_meta` + `execute()` |
| `GenericAgent` | `GenericAgent` (Agent+Capability) | 加 `capability_meta` + `execute()` |
| - | `ExportExcelSkill` + `ExcelWriter` (新建) | 未来需要时 |

### 5.3 Tool 的形态

Tool 是普通类或函数，不继承 BaseSkill，不设 capability_meta，不触发 `__init_subclass__` 注册。

```python
# tools/web_search_client.py — 纯 API 封装，不是能力单元
class WebSearchClient:
    """Web 搜索 API 客户端。纯工具，无 LLM。"""
    
    async def search(self, query: str) -> list[dict]:
        """返回原始搜索结果。"""
        ...

    async def asearch(self, query: str) -> list[dict]:
        ...
```

---

## 6. Agent 作为能力单元

### 6.1 双模式：Router 调度 vs Executor 调用

```
Router 调度（用户交互）：
  GraphQueryAgent(on_event=xxx).arun(user_input)
  → 有 UI 回调，有 session 记忆，写日志

Executor 调用（Plan 编排）：
  CapabilityRegistry.get_by_name("graph_query_agent").execute(query=xxx, _ctx=xxx)
  → 无 UI 回调，自包含执行，返回 CapabilityResult
```

### 6.2 Agent 能力单元的定义模式

```python
class GraphQueryAgent(MemoryAgent):
    capability_meta = CapabilityMeta(
        name="graph_query_agent",
        category="agent",
        tags=["codebase", "query", "graph"],
        short_description="代码库结构化数据的查询与分析",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "查询描述"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "answer": {"type": "string", "description": "Markdown 格式的查询结果"},
            },
        },
        output_field="answer",
    )

    async def execute(self, query=None, _ctx=None):
        """自包含执行（不依赖 UI 回调）。"""
        if _ctx:
            refined = await self.extract_context(_ctx, "提取需要查询的关键词")
            query = refined or query or ""
        
        from engine.workflow.graph_query import GraphQueryRunner
        runner = GraphQueryRunner()
        state = await runner.arun(query or "")
        return CapabilityResult(
            capability_name="graph_query_agent",
            output_data={"answer": state.get("answer", "")},
        )
```

### 6.3 双模式详解与约束

Agent 在两种场景下被调用，走不同的执行路径：

```
场景 A — Router 调度（用户直接交互）：
  GraphQueryAgent.arun(user_input)
  → 走 _invoke() → 完整的 BaseAgent 生命周期
  → 有 Context 记录、on_agent_success 钩子（写记忆、蒸馏）
  → 结果通过 UI 回调返回给用户

场景 B — Executor 编排（被 Planner 作为计划中的一个步骤调用）：
  CapabilityRegistry.get_by_name("graph_query_agent").execute(query=..., _ctx=...)
  → 走 execute() → 纯自包含执行
  → 不写记忆（那是顶层 AutonomousAgent 的职责）
  → 返回 CapabilityResult，由 Executor 统一处理
```

**硬性约束**：

- Agent 的 `execute()` 中不允许调用其他已注册的能力单元（防止嵌套编排——一层 Plan 里不应该再套一层 Plan，违反最大深度 1 的约束）
- Agent 的 `execute()` 只能调用纯 Tool 或纯内部 Skill（不经过 CapabilityRegistry，直接 import 使用）
- `execute()` 的执行不触发 MemoryAgent 的 `on_agent_success` 钩子（那是 Router 路径的职责）。记忆写入统一由顶层 AutonomousAgent 在 `_invoke` 完成后处理

---

## 7. TemplateRecallSkill

### 7.1 职责

```python
class TemplateRecallSkill(BaseSkill):
    """模板召回：从 templates/ 目录匹配用户意图的最佳模板。"""

    capability_meta = CapabilityMeta(
        name="template_recall",
        category="skill",
        tags=["plan", "template", "recall"],
        short_description="从模板库中召回与用户意图匹配的执行模板",
        ...
    )

    async def recall(self, intent: str) -> str | None:
        """用 LLM 对每个模板置信度打分，返回最优 template_id 或 None。
        
        阈值：仅当最高分 ≥ 7/10 时返回，否则 None。
        """
```

### 7.2 匹配方式

- 启动时扫描 `templates/*.md`，解析 YAML frontmatter → `list[TemplateMeta]`
- 运行时 LLM 对比 intent 与每个模板的 `tags` + `short_description` + 步骤摘要
- 输出置信度 0-10，最高 ≥ 7 时返回该 `template_id`
- 低于阈值或无模板 → 返回 None，Planner 自由发挥

### 7.3 模板文件格式（不变）

```markdown
---
template_id: export_order_report
tags: [order, export, report]
short_description: 导出订单报表
---
# 模板：导出订单报表

## 步骤
1. graph_query_agent
   params:
     query: "查询最近的订单数据"
2. web_search_parser
   params: {}
```

模板中每个步骤的 `params` 是 Planner 参考的示例，`reason` 由 Planner 填充。

### 7.4 作用在 Planner 上的位置

```
AutonomousAgent._invoke:
  1. 记忆补偿 → intent_input
  2. template_id = await TemplateRecallSkill.recall(intent_input)
  3. if template_id: template = load_template(template_id)
  4. Planner.plan(intent, capabilities, template=template)
     → template 作为 few-shot 注入 prompt
```

---

## 8. Executor 改造（_ctx 注入）

### 8.1 变化摘要

| 旧（v1.0） | 新（v2.0） |
|-----------|-----------|
| `resolve_params(step.params, work_memory)` 解析 `$step_N.field` | 构造 `_ctx` 直接注入 |
| 依赖 `parse_dependency_ref`、`merge_plans`、`skip_dependent_steps` | 不再依赖（可保留函数作为工具） |
| 参数在 Executor 中精确匹配 | 参数在能力单元内由 LLM 解析 |
| 模板作者要知道字段名 | 模板作者只需写自然语言描述 |

### 8.2 核心逻辑

```python
async def _execute_plan(self, ctx, plan, exec_context):
    work_memory: dict[int, Any] = {}
    steps = [s.model_dump() for s in plan.steps]
    has_replanned = False

    idx = 0
    while idx < len(steps):
        step = steps[idx]
        cap = CapabilityRegistry.get_by_name(step["capability_name"])
        if cap is None:
            return self._abort(ctx, f"未注册的能力: {step['capability_name']}")

        meta = cap.meta

        # 1. 构造 _ctx
        _ctx = CapabilityRunContext(
            step_id=step["step_id"],
            intent=exec_context["intent_input"],
            reason=step.get("reason", ""),
            previous=[
                StepOutput(
                    step_id=sid,
                    capability_name=...,
                    output=work_memory[sid],
                )
                for sid in completed_step_ids
            ],
        )

        # 2. 组装参数
        params = dict(step.get("params", {}))
        params["_ctx"] = _ctx
        if meta.needs_session_context:
            params["intent_input"] = exec_context["intent_input"]
        if meta.needs_user_profile:
            params["user_profile"] = exec_context["user_profile"]

        # 3. 执行 + 错误处理（同 v1.0）
        ...

        # 4. 存结果
        work_memory[step["step_id"]] = result.output_data
```

### 8.3 错误策略（不变）

| 键 | 可选值 | 默认行为 |
|----|--------|---------|
| `on_error` | `"abort"`, `"retry"`, `"replan_with_context"` | `"abort"` |
| `on_empty_result` | `"skip_dependents"`, `"replan_with_context"`, `"abort"` | `"abort"` |
| `fallback_capability` | 能力名称或 null | null |
| `max_retries` | 整数 | 1 |

重规划只允许一次。

---

## 9. 改造范围与文件清单

### 9.1 任务清单

| # | 任务 | 涉及文件 | 优先级 | 状态 |
|---|------|---------|--------|------|
| 2.1 | BaseCapability 基础 + CapabilityRunContext | `engine/base/capability.py` | P0 | ✅ 完成 |
| 2.2 | Executor `_ctx` 注入 | `agents/autonomous_agent.py` + `engine/workflow/skills/planner_skill.py` | P0 | ✅ 完成 |
| 2.3 | Agent 注册为能力 | `agents/graph_query_agent.py` + `agents/generic_agent.py` | P0 | ✅ 完成 |
| 2.4 | WebSearch Skill/Tool 分拆 | `engine/workflow/skills/websearch.py` + 新建 `tools/web_search_client.py` | P1 | ✅ 完成 |
| 2.5 | Render 接 `_ctx` | `engine/workflow/skills/render.py` | P1 | ✅ 完成 |
| 2.6 | TemplateRecallSkill | 新建 `engine/workflow/skills/template_recall.py` | P1 | ✅ 完成 |
| 2.7 | 记忆写入补丁 | `agents/autonomous_agent.py` | P1 | ✅ 完成 |
| 2.8 | 集成测试 | `test/autonomous_agent.py` | P2 | ✅ 完成 |

### 9.2 严格禁止改动

- `engine/workflow/graph_query/` 下所有文件
- `BaseAgent`、`MemoryAgent` 核心执行流程（只允许子类新增 `execute()` 方法）
- `memory/` 下现有的记忆策略和 `MemoryComposeSkill`

### 9.3 已实现不再变动的组件

- `engine/base/capability.py` 中的 CapabilityMeta、CapabilityResult（数据模型）
- `engine/base/capability_registry.py`（注册中心）
- `engine/workflow/skills/planner_skill.py`（PlannerSkill、Plan、PlanStep）
- `frontend/plan_confirm.py`（计划确认组件）
- `config.py`、`router.py`、`gate.py`（场景配置和路由）
- `app.py` 中的后台线程预加载模式

### 9.4 v2.2 实施详情：多继承 BaseCapability 修复

**问题根源**：`BaseCapability` 定义了 `get_llm()`、`extract_context()`、`format_context()` 实例方法，但 `BaseSkill(ABC)` 和 `BaseAgent(ABC)` 均不继承 `BaseCapability`。导致能力单元中调用 `self.extract_context()` 时 MRO 无法解析，运行时 `AttributeError`。

**修复方案**：所有注册为能力的类改为多继承，在自身 Skill/Agent 继承链上混入 `BaseCapability`：

```
PlannerSkill(BaseSkill, BaseCapability)
RenderSkill(BaseSkill, BaseCapability)
WebSearchParserSkill(BaseSkill, BaseCapability)
TemplateRecallSkill(BaseSkill, BaseCapability)
GraphQueryAgent(MemoryAgent, BaseCapability)
GenericAgent(MemoryAgent, BaseCapability)
```

MRO 示例（以 GraphQueryAgent 为例）：
```
GraphQueryAgent → MemoryAgent → BaseAgent → BaseCapability → ABC
                   (Router 路径)              (Executor 路径)
```

- Router 路径 `arun() → _invoke()` 不调用 BaseCapability 方法，不受影响
- Executor 路径 `execute()` 通过 MRO 解析到 `self.get_llm()` / `self.extract_context()` / `self.format_context()`
- `__init_subclass__` 调用链正确：`BaseSkill/BaseAgent.__init_subclass__` → `super()` → `ABC.__init_subclass__`
- 所有直接调用 `get_llm()` 处改为 `self.get_llm()`（PlannerSkill.plan、RenderSkill._render_ainvoke、TemplateRecallSkill.recall、GenericAgent.execute）

**RenderSkill _ctx 数据提取**：`execute()` 中当 `data` 参数为空时，遍历 `_ctx.previous` 收集 `prev.output` 作为渲染数据。优先级：显式 data 参数 > `_ctx.previous` 提取。

---

## 10. 测试与验证

### 10.1 测试场景设计

**场景：「订单项目分析」** — 同时使用 Agent 和 Skill，验证完整 _ctx 数据流

```
用户: "分析订单项目的模块结构，并搜索微服务拆分最佳实践"

Planner 生成计划:
  Step 1: graph_query_agent {query: "查 description 含订单的项目及其模块"}
          → Agent 内部走 GraphQueryRunner → SQL 查询 → 返回结果
  Step 2: web_search_parser {query: "微服务拆分最佳实践"}
          → Skill 内部 WebSearchClient → MCP 搜索 → LLM 解析 → 返回结果
  Step 3: render_result {user_input: "分析订单项目..."}
          → data 为空时从 _ctx.previous 提取 Step1+Step2 的输出
          → LLM 综合渲染 → 最终回答
```

**验证点**：

| 步骤 | 验证内容 |
|------|---------|
| Step 1 → Step 2 | _ctx.previous 包含 Step 1 的 output，Step 2 可用 extract_context 提取关键词 |
| Step 1+2 → Step 3 | render_result 从 _ctx.previous[0].output 和 _ctx.previous[1].output 收集数据 |
| 全流程 | work_memory 格式正确（直接存 output_data），_summarize_results 正常汇总 |
| 记忆链路 | AutonomousAgent._invoke 末尾 add_memory_payload，MemoryAgent.on_agent_success 收集写入 |

### 10.2 单元测试

1. **模型测试**：`CapabilityRunContext` 构造/序列化，`StepOutput` 创建，`build_step_output` 截断（>500 字符）
2. **BaseCapability 方法**：`format_context` 文本格式，`extract_context` LLM 提取（需 mock LLM）
3. **_ctx 注入**：Executor 构造 _ctx 时 `previous` 列表正确包含已完成的步骤
4. **工具函数**：`_step_cap_name`、`_truncate_summary`

### 10.3 集成测试（`test/autonomous_agent.py`）

- Mock 能力单元验证 `_execute_plan` _ctx 注入和数据传递
- Mock Planner 验证模板召回分支（有匹配 / 无匹配）
- 错误处理策略测试（abort / retry / replan_with_context / skip_dependents）
- 回归测试：`GraphQueryAgent.arun()` 和 `GenericAgent.arun()` 不受影响

---

## 11. 已识别风险与规避

| 风险 | 规避 |
|------|------|
| Token 膨胀：`_ctx.previous` 携带大量原始输出 | `output_summary` 截断 + `execution_policy` 中可配 `context_inject_max_chars` |
| LLM 从 `_ctx` 中提取错误信息 | 能力单元无法降级时触发 `execution_policy` 兜底（retry/replan/abort） |
| Agent 递归调用：Agent.execute 里又调能力 | `CapabilityRegistry` 注册时检查循环依赖，运行时限制嵌套最大深度 1 |
| 纯工具能力无法理解 `_ctx` | 其 `execute()` 中 `extract_context` 返回空，需退而用 `params` 或报错 |
| 用户长时间不确认计划 | 前端超时 300 秒，自动取消 |
