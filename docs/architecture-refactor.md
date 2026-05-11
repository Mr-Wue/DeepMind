# CodeMind 架构重构记录

> 核心原则：面向对象、总线驱动、锚点模板、垂直切片验证。

---

## 一、架构原则

### 1.1 面向对象强制要求

- 所有模板/计划相关数据结构必须使用 `bus/dto/plan.py` 中的 Pydantic 模型，禁止裸 dict/魔法值
- `PlanStep` / `Plan` / `AnchorTemplate` / `AnchorStep` / `OptionalStepGroup` 是唯一真相来源
- 模型自带 coercer（容错 LLM 输出格式），`Plan.normalize_step_ids()` 自动编号
- 模板匹配阶段的轻量结构 `TemplateMeta`（dataclass）通过 `load_template()` 转为 `AnchorTemplate`

### 1.2 总线驱动数据流

能力间数据传递通过 DataBus 而非步骤依赖链：

```
file_parse ──entities──→ data_bus.entity["entities"]
entity_filter ←──读── data_bus.entity["entities"]
entity_filter ──kept──→ data_bus.entity["entities"] (覆盖)
entity_crud   ←──读── data_bus.entity["entities"]
```

- `depends_on` 退化为**执行顺序约束**，数据始终从总线取最新版本
- 能力自己读写 DataBus，不依赖 work_memory 或 _ctx.previous
- `StepExecutor._publish_to_data_bus()` 已自动写入，能力需主动读取

### 1.3 锚点模板机制

模板 = 锚点步骤（固定骨架） + 可选步骤组（允许插入点）：

```
模板定义:
  [锚点1] file_parse
  @optional 数据筛选 → allowed_capabilities: [entity_filter], max_steps: 2
  @optional 入库前预览 → allowed_capabilities: [render_result], max_steps: 1
  [锚点2] entity_crud

Planner 决策:
  场景A: "解析需求文档并入库"      → file_parse → entity_crud (无插入)
  场景B: "只入库性能相关的"        → file_parse → entity_filter → entity_crud
  场景C: "筛选安全相关，预览后入库" → file_parse → entity_filter → render_result → entity_crud
```

- 锚点步骤不可删改顺序，必须全部保留
- 可选步骤组定义插入位置、允许能力和最多步数
- Planner 按用户意图决定是否插入、插入几步
- `is_anchor` 由代码自动标记（模板锚点名单对齐），不依赖 LLM 输出

---

## 二、当前状态

### 2.1 总线系统（已完成）

- **DataBus** (`bus/data_bus.py`) — 请求级四层 KV（entity/view/llm/step），ContextVar 隔离
- **EventBus** (`bus/event_bus.py`) — 全局发布-订阅，`threading.Lock` 保护
- **ViewBus** (`bus/view_bus.py`) — 高频 UI 渲染事件，无锁（单线程异步）
- **RequestScope** (`bus/request_scope.py`) — 对话全局作用域（InputInfo/OutputInfo/EntityStore）
- **StepExecutor** (`bus/step_executor.py`) — 8 阶段步骤执行（解析→验证→构建→确认→启动→执行→错误处理→发布），自动写 DataBus（entity_keys 匹配）
- **Listeners** — LogListener、TurnDetailListener、UserMemoryListener、ConsoleListener、MemoryCheckpointListener
- **DTO 层** (`bus/dto/`) — Plan/PlanStep/AnchorTemplate/AnchorStep/OptionalStepGroup 等 Pydantic 模型；TurnClue/TurnDetail；EntityDTO/LLMDTO/ViewDTO

### 2.2 记忆系统（已完成）

- checkpoint 读写（`memory/checkpoint.py`）、turn 生命周期（`memory/turn.py`）、用户记忆解析（`memory/user_memory.py`）
- 记忆补偿链路：Router → `turn_mgr.compensate()` → MemoryComposeSkill → compensated_input
- 用户记忆写入 + 蒸馏触发（`skills/memory/`）

### 2.3 Data Query 流程（已完成）

- GraphQueryWorkflow：`START → memory_compensate → query_split → sql_generate → execute_sql → (advance_query ↻) → render`
- reqmgmt 领域 ORM 实体完整（Product/RM/IR/PR/Part/TC/TS + 4 边表）
- 边表已注册 `LLM_NODE_NOTE`，schema 完整
- Text2SQL 空前序结果已处理（避免 WHERE IN (0)）

### 2.4 Skills 目录（已完成）

```
skills/
  base.py           — BaseSkill + SkillRegistry + parse_json_from_text
  common/           — entity_crud, entity_filter, export_data, render, websearch
  dataskill/        — entity_extract, failure_analysis, ir_pr_link, text2sql
  file_parse/       — file_parse_skill(门面), document_parse, entity_assembly, image_parse, _mime_utils, response_utils
  memory/           — memory_compose, memory_distill, write_user_memory
  planning/         — intent_classify, planner_skill, template_recall
  template/         — content_locate, leaf_match_fill, skeleton_guidance
  utils/            — stream_utils, system_memory_prompt, system_prompt_builder, tool_dispatcher, voice_skill
```

> **注意**：目录名为 `dataskill`（不是 `data`），与 `common` 区分——`common` 存放注册到 CapabilityRegistry 的通用能力，`dataskill` 存放直接调用的数据操作 Skill。

### 2.5 能力注册（已完成）

11 个能力注册，`visible_to_planner` 已标记：

```
VISIBLE (自主规划可用): entity_crud, export_data, ir_pr_link, web_search_parser
HIDDEN (仅模板引用):    file_parse, entity_filter, planner, template_recall,
                        render_result, content_locate_and_generate,
                        leaf_match_fill, skeleton_guidance
```

`GenericAgent._get_tools()` 改为 `visible_to_planner` 过滤，不再硬编码排除列表。

### 2.6 模板系统（已完成）

- **格式**: YAML 只留 `template_id`，其余字段从正文 `##` 段落提取（描述/适用/不适用/预期输出/步骤）
- **解析**: `_extract_body_section()` 通用提取 + `_resolve_field()` YAML 兜底
- **锚点语法**: `@optional 组名` + `允许能力: [...]` + `最多步骤: N`（中英文 key 兼容）
- **word_req_extract.md**: 2 锚点 + 2 可选组，user_prompt 含 8 条映射规则
- backward compat: 旧模板 YAML 字段自动兜底

### 2.7 OO 模型统一（已完成）

`bus/dto/plan.py` 是唯一的 Plan/Template 模型来源：

| 类 | 用途 |
|----|------|
| `PlanStep` | Planner 输出步骤，含 `is_anchor`、coercer |
| `Plan` | 执行计划，含 `template_id`、`normalize_step_ids()` |
| `AnchorTemplate` | 模板完整解析结果 |
| `AnchorStep` | 锚点步骤定义 |
| `OptionalStepGroup` | 可选步骤组（allowed_capabilities + max_steps） |
| `AnchorParamDef` | 参数类型化定义（未来用） |

`planner_skill.py` 本地 PlanStep/Plan 类已删除，改为 import。

### 2.8 P0 机制闭环（已完成）

| # | 任务 | 状态 |
|---|------|------|
| P0.1 `template_dir` 接线 | `autonomous_agent.py:92` — `DomainManager.current().template_dir` 传入 `TemplateRecallSkill()` |
| P0.2 `is_anchor` 自动标记 | `planner_skill.py:271-276` — `plan()` 返回前用模板 `anchor_steps` 名单自动设置 |
| P0.3 Plan 后验证 | `planner_skill.py:151-169` — `_validate_plan()` 校验能力存在性 + 锚点完整性 + 锚点顺序；最多重试 3 次，失败时注入错误反馈 |
| P0.4 DataBus 数据流 | `entity_crud.py` 和 `entity_filter.py` — 优先从 `data_bus.entity` 读取，兜底 `input.previous` |
| P0.5 depends_on 修正 | 锚点步骤 LLM 输出的 depends_on 被模板定义覆盖，`normalize_step_ids()` 自动重编号 |

### 2.9 PlanExecutor 错误恢复（已完成）

`engine/workflow/plan_executor.py`：
- `on_error` — 支持 `abort` / `replan_with_context`（含错误上下文重规划，仅一次）
- `on_empty_result` — 支持 `abort` / `replan_with_context` / `skip_dependents`
- `_replan()` — 调用 `generate_plan_callback` 重生成计划，`merge_plans()` 合并已执行步骤 + 新步骤

---

## 三、待完成（按优先级）

### P1 — word_req_extract 模板 e2e（垂直切片）

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| P1.1 | entity_filter 核心逻辑 | **已完成** | LLM 结构化调用，逐条标记 kept/discarded（含 reason）。从 DataBus 读取 EntityDTO → `to_llm_dto()` → `to_text()` → LLM FilterResult 决策 → FK 完整性修复 → 写回 DataBus |
| P1.2 | file_parse → entity_filter → entity_crud e2e | **已完成** | word_req_extract 和 prd_to_product_requirements 两个模板均跑通：模板召回 → Plan(3步含filter) → file_parse → entity_filter → entity_crud，FK 修复、幂等验证均通过 |
| P1.3 | entity_crud 读取 DataBus | **已完成** | `entity_crud.py:114-125` — 优先读 `data_bus.entity`，兜底 `input.previous`；`entity_filter.py:62-64` 同样优先读 DataBus |
| P1.4 | 确认框展示过滤明细 | **已完成** | entity_crud 入库确认时展示 discarded 列表及原因，`frontend/plan_confirm.py` 当前无此逻辑 |
| P1.5 | 幂等验证 | **已完成** | `upsert_by_biz_key()` 按业务键去重，word_req_extract 和 prd_to_product_requirements 两次 upsert 后数据量不变（inserted=0, updated=N），e2e 验证通过 |

### P2 — 扩展（垂直切片验证后再做）

| # | 任务 | 说明 |
|---|------|------|
| P2.1 | 旧模板迁移 | req_analysis_to_prd / ir_pr_link 已迁移新格式，4 个对应 skill（skeleton_guidance/leaf_match_fill/export_data/ir_pr_link）execute(**kwargs)→execute(input:CapabilityInput) |
| P2.2 | reqmgmt 新模板 | `req_query`（data query）、`coverage_analysis`（覆盖率分析） |
| P2.3 | DataBus entity 层标准化 | key 约定文档化，避免能力间 key 冲突 |

### P3 — Tool Calling 重构（远期）

| # | 任务 | 说明 |
|---|------|------|
| P3.1 | `capability_to_tool()` | 从 `generic_agent._get_tools()` 提取为共享函数 |
| P3.2 | Planner Tool Calling | JSON 输出 → Function Calling agent loop |
| P3.3 | template_recall Tool Calling | 减少打分 JSON 解析脆弱性 |
| P3.4 | 动态步骤插入 | PlanExecutor + DiscoveryNeeded 信号 |

### 后续

| # | 任务 | 说明 |
|---|------|------|
| 后续 | DTO 三层截断 | EntityDTO(完整) → to_llm_dto(截断) → to_view_dto(截断) |
| 后续 | 用户记忆自动蒸馏 | UserMemoryListener 接线 |
| 后续 | codebase 领域回归 | 切换 domain.toml 验证 |

---

## 四、垂直切片策略

**不水平铺开**。用一个模板（word_req_extract）和它涉及的有限技能（file_parse、entity_filter、entity_crud），把锚点机制 + DataBus 数据流 + Plan 验证整条链路跑通。验证通过后再把模式复制到其他模板。

当前切片涉及文件：

```
模板:       templates/word_req_extract.md
能力:       skills/file_parse/file_parse_skill.py  (门面, 已注册)
            skills/common/entity_filter.py         (骨架, 已注册)
            skills/common/entity_crud.py           (已注册)
模型:       bus/dto/plan.py                        (AnchorTemplate/Plan/PlanStep...)
解析:       skills/planning/template_recall.py     (load_template, @optional 解析)
计划:       skills/planning/planner_skill.py       (_format_template, plan, _validate_plan)
总线:       bus/data_bus.py                        (entity 层读写)
执行:       bus/step_executor.py                   (自动写 DataBus entity 层)
编排:       agents/autonomous_agent.py             (_recall_template, _generate_plan, _merge_template_params)
测试:       test/skill/anchor_template_test.py     (3 场景 Plan 生成验证)
```

---

## 五、文件结构

```
app.py                   # Chainlit 入口
config.py                # SCENES 定义
router.py                # AgentRouter — turn 生命周期编排
domain.toml              # 领域切换

bus/                    # 总线基础设施
  data_bus.py            # DataBus — 请求级四层 KV (entity/view/llm/step)
  event_bus.py           # EventBus + EventKind
  view_bus.py            # ViewBus — UI 渲染事件
  request_scope.py       # RequestScope / InputInfo / OutputInfo / EntityStore
  step_executor.py       # 8 阶段步骤执行 + DataBus 自动写入
  initializer.py         # 总线初始化
  dto/                   # 数据模型（Pydantic）
    plan.py              # PlanStep / Plan / AnchorTemplate / AnchorStep / OptionalStepGroup
    events.py            # 事件 DTO
    turn.py              # TurnClue / TurnDetail
    common.py            # DataDTO / LLMField / EntityField / ViewField
    layers.py            # LLMDTO / EntityDTO / ViewDTO
    workflow.py          # WorkflowOutput / StepInput
  listeners/             # 事件监听器
    log_listener.py      #   日志持久化
    turn_detail.py       #   轮次详情写入
    memory_checkpoint.py #   检查点写入
    user_memory.py       #   用户记忆触发
    console.py           #   控制台输出

agents/                  # Agent 层
  base_agent.py          # 生命周期
  graph_query_agent.py   # DataQueryAgent (别名 GraphQueryAgent)
  autonomous_agent.py    # 模板→规划→执行→汇总
  generic_agent.py       # ReAct 工具调用
  llm_callback.py        # LLM 回调（ContextCallbackHandler）

skills/                  # 能力单元（根级，按类型分子包）
  base.py                # BaseSkill 抽象 + parse_json_from_text
  common/                # entity_crud, entity_filter, export_data, render, websearch
  dataskill/             # entity_extract, failure_analysis, ir_pr_link, text2sql
  file_parse/            # file_parse_skill (门面), document_parse, entity_assembly, image_parse, _mime_utils, response_utils
  memory/                # memory_compose, memory_distill, write_user_memory
  planning/              # planner_skill, template_recall, intent_classify
  template/              # content_locate, leaf_match_fill, skeleton_guidance
  utils/                 # stream_utils, system_memory_prompt, system_prompt_builder, tool_dispatcher, voice_skill

templates/               # 模板文件（Markdown + YAML frontmatter）
  word_req_extract.md    # Word→实体→筛选→入库（垂直切片验证模板）
  req_analysis_to_prd.md # 需求文档→PRD 说明书
  ir_pr_link.md          # IR/PR 关联查询
  prd_to_product_requirements.md  # PRD→需求条目入库
  builtin_query_codebase.md       # 代码库数据查询
  builtin_analyze_recommend.md    # 综合分析

memory/                  # 记忆模块
  __init__.py            #   导出
  checkpoint.py          #   checkpoint 读写
  turn.py                #   turn 生命周期（assign_turn_id / compensate / commit_turn）
  request_context.py     #   RequestMemoryContext（含 file_paths）
  user_memory.py         #   用户记忆解析
  policies/              #   记忆策略
  writers/               #   记忆写入器

engine/
  base/                  # Node/Workflow/Runner/Capability 抽象
    capability.py        #   CapabilityMeta / BaseCapability / CapabilityResult / CapabilityInput / ParamSource
    capability_registry.py  # CapabilityRegistry
    node.py              #   BaseNode
  workflow/
    graph_query/         # Text2SQL 查询工作流
    plan_executor.py     # 计划确认+执行+重规划+结果聚合
    tools/               # Tool 实现 + 注册表

base/                    # 基础层
  domain.py              # DomainManager + DomainConfig
  llm/                   # 多角色 LLM 工厂 (default/text2sql/vision/voice/embeddings)
  entity/                # ORM 实体
    runtime.py           #   SQLite 引擎管理
    reqmgmt/             #   Product/RM/IR/PR/Part/TC/TS + edges
    codebase/            #   App/Project/BizModel/Class/Method...
    meta/                #   users, DesignSession

domains/                 # 领域模块
  codebase/              #   代码库领域
  reqmgmt/               #   需求管理领域

test/
  e2e/                   #   端到端测试（e2e_plan_scenario / e2e_real_flow / word_parse_e2e）
  agent/                 #   Agent 集成测试（word_parse / ir_pr_link / prd_to_pr / scenario2）
  skill/                 #   Skill 单元测试（anchor_template / template_recall / databus_summaries）
```
