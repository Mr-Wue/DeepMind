# 记忆线索化 + 锚点式模板 改造设计

> 本文档记录 2026-05-09 对话中确定的设计结论，供 AI 下次继续。面向 AI 阅读，非人类文档。

---

## 一、当前记忆系统全貌

### 1.1 四层记忆注入（每轮重新构造）

```
SOUL → USER → MEMORY → KNOWLEDGE
prompts/soul.md  prompts/user.md  prompts/memory.md  InMemoryStore(RAG)
(静态)          (静态)           (可蒸馏)           (向量检索)
```

- 由 `engine/workflow/skills/system_memory_prompt.py:build_system_memory_prompt()` 组装
- 通过 `system_memory_prompt.j2` Jinja2 模板渲染
- 注入 `GraphQueryState.system_context`，每轮重新读取文件（非 checkpoint 持久化）

### 1.2 两条持久化路径

| | 任务记忆 (Task) | 用户记忆 (User) |
|---|---|---|
| 范围 | 同 thread_id 跨轮 | 跨会话 |
| 存储 | LangGraph MemorySaver (checkpoint) | `data/memory/user_memories/{uid}.md` |
| 读写 | `get_turn_state_graph().aget_state/aupdate_state` | `WriteUserMemorySkill` 追加, `MemoryDistillSkill` 蒸馏 |
| 策略 | `TaskMemoryPolicy(max_retain_turns)` | `UserMemoryPolicy(write_to_file_enabled)` |

### 1.3 共享 Checkpoint 架构（当前实测）

`engine/base/workflow.py` 定义了进程内单例 `MemorySaver`（`get_workflow_checkpointer()`），两个 CompiledStateGraph 共享：

- `GraphQueryWorkflow.compiled` — DataQueryAgent 使用
- `_turn_state_graph` (极简 noop graph) — AutonomousAgent 使用，Router._compensate() 读取

两者通过同一 `thread_id` 读写同一 checkpoint。turn_history 是扁平列表，两个 Agent 写入的 entry 结构不同：

```python
# DataQueryAgent 写入 (via commit_graph_query_turn_to_checkpoint)
{user_input, intent, sub_queries: [str], sql_digest: [{id, name}, ...]}

# AutonomousAgent 写入 (via _invoke 末尾直接 aupdate_state)
{user_input, intent: "plan", sub_queries: [], files: [str], output_info: [{type, uri}]}
```

### 1.4 每轮记忆写入点

```
通道 A: BaseNode._maybe_write_memory()
  write_to_memory=True 的节点 (QuerySplitNode, ExecuteSQLNode, MemoryCompensateNode)
  → format_memory_content() → ctx.add_memory_payload()

通道 B: Agent 直接 add_memory_payload
  AutonomousAgent._invoke() 末尾 → ctx.add_memory_payload(agent级摘要)

通道 C: Checkpoint 写入
  DataQueryAgent → commit_graph_query_turn_to_checkpoint()
  AutonomousAgent → 内联 get_turn_state_graph().aupdate_state()

回合结束:
  MemoryAgent.on_agent_success()
  → ctx.pop_memory_payloads() → WriteUserMemorySkill → MD文件写入
  → bump_and_should_distill(uid) → 每3轮触发 MemoryDistillSkill(后台)
```

---

## 二、当前问题

### 2.1 模板太死板

- `templates/*.md` 固定步骤序列，planner 被强制锁定（`_format_template()` 硬编码 "必须严格遵循，禁止增删步骤"）
- 参数硬编码（如 `word_req_extract.md` 的 `user_prompt` 是一大段固定指令）
- 用户无法指定部分入库、跳过某类实体、插入验证步骤等

### 2.2 turn_history 污染

- `sql_digest` 存 20 行完整查询结果，checkpoint 随轮次线性膨胀
- `sub_queries` 存完整 SQL 文本
- MemoryComposeSkill（指代消解）实际只需要 "上轮做了什么，涉及了哪些实体名"
- 详细信息（完整SQL、完整结果）几乎从未被消费，却一直占用内存

---

## 三、锚点式模板设计

### 3.1 概念

模板不再定义完整步骤序列，而是定义 **必选锚点 + 参数校验规则 + 可选建议**。Planner 在锚点之间自由插入步骤。

```
当前（固定）:
  file_parse → entity_crud    (不可变)

锚点式:
  [必选] file_parse ─→ [可选: entity_validate] ─→ [必选] entity_crud ─→ [可选: render_result]
  锚点保证关键路径不丢失，中间由 planner + 用户需求决定
```

### 3.2 模板文件格式

存储在 `templates/anchors/` 目录下，与旧模板 `templates/` 分目录：

```markdown
---
template_id: word_req_extract
type: anchor
short_description: 解析需求Word文档提取实体并入库
applicable: 用户上传需求Word文档，要求解析并保存到数据库
not_applicable: 纯代码库查询；非Word文档
expected_output: 入库统计
---

# 锚点模板：需求文档解析入库

## 锚点步骤

### 1. file_parse (必选)
reason: 解析Word文档提取全部实体
depends_on: []
params:
  file_index: {type: int, min: 0}
  mode: {type: enum, values: [content_as_item, leaf_flatten_bulk], default: content_as_item}
  user_prompt: {type: str, max_len: 1000}

### 2. entity_crud (必选)
reason: 批量保存到数据库
depends_on: [1]
params:
  operation: {type: enum, values: [batch_upsert, delete]}

## 可选建议
- entity_validate: 用户要求验证数据质量时，插入在锚点1和2之间
- render_result: 用户需要查看入库统计时，插入在锚点2之后
```

### 3.3 参数校验

锚点步骤执行前，用 `CapabilityMeta.input_schema` 做确定性校验（JSON Schema，非 LLM），不合规 → `ExecutionAborted` → 复用现有重规划机制。

### 3.4 Planner prompt 差异

```
旧 (固定模板):  "必须严格遵循，禁止增删步骤或替换能力名"
新 (锚点模板):  "锚点步骤不可跳过/替换，参数需通过校验。锚点之间可插入其他能力。"
```

---

## 四、线索机制设计

### 4.1 核心思路

turn_history 只存轻量线索（summary + 文件指针），完整执行记录存磁盘文件。LLM 消费 summary 做指代消解，需要细节时用通用文件工具打开 detail 文件。

### 4.2 turn_history Entry 新格式

```python
# 统一格式（5 个扁平字段，零嵌套）
{
    "turn_id": int,
    "user_input": str,       # 用户原始输入
    "intent": str,           # "domain_query" | "plan" | "plan_anchor" | ...
    "summary": str,          # ≤300 字自然语言，做了什么/涉及什么实体
    "detail": str | None,    # detail 文件路径，如 "dataskill/turns/{thread_id}/turn_5.md"
}
```

### 4.3 Summary 信息源

**PlanAnchorAgent / AutonomousAgent:**
- `plan.steps` 的能力名列表
- 每步 `CapabilityResult.output_data` 的摘要（已有 `_truncate_summary()`）
- `answer` 前 100 字
- 产物: `"执行3步: file_parse→skeleton_guidance→export_data。解析2个文档，生成PRD。产出: /output/prd.docx"`

**DataQueryAgent:**
- `intent` (domain_query)
- `sub_queries` 的子查询描述（非完整 SQL）
- `query_result` 前 8 行的 name/id/title 字段
- 产物: `"查询IR和PR及匹配关系；拆解3步。12条IR, 47条PR, 8条匹配。涉及:需求结构树管理,需求分配管理..."`

### 4.4 Detail 文件格式

存储路径：`data/turns/{thread_id}/turn_{turn_id}.md`

**Plan 场景:**
```markdown
# Turn 5
> plan_anchor | 2026-05-09 14:30 | 3步 | 12.3s

**用户**: 根据需求文档生成PRD
**补偿后**: 根据用户需求文档生成产品需求规格说明书

## 计划
1. file_parse — 解析所有上传文档
2. skeleton_guidance — 生成骨架对照指导
3. export_data — 生成PRD文档

## 执行详情
### Step 1: file_parse (3.2s)
- 参数: mode=leaf_flatten_bulk, summary_threshold=400
- 输出: 提取 3 产品, 8 需求模型, 12 需求项

### Step 2: skeleton_guidance (5.1s)
- 输出: 术语统一3处, 范围约束2条

### Step 3: export_data (4.0s)
- 参数: format=docx
- 输出: /data/output/prd_2026-05-09.docx

## 结果
已生成产品需求规格说明书，包含...
```

**DataQuery 场景:**
```markdown
# Turn 5
> domain_query | 2026-05-09 14:30 | 15行 | 2.3s

**用户**: 这些项目的模块呢？
**补偿后**: 查询 ProjectA, ProjectB, ProjectC 下的模块

## SQL
```sql
SELECT m.* FROM modules m WHERE m.project_id IN (1, 2, 3)
```

## 结果 (前20行)
| id | name | project_name |
|----|------|-------------|
| 1  | ModuleA | ProjectA |
| 2  | ModuleB | ProjectA |
...
```

**选择 Markdown 而非 JSON 的原因**：LLM 是主要消费者，Markdown 训练密度远高于 JSON Schema。表格、标题层级、代码块 —— LLM 原生解析。

### 4.5 读取记忆的工具化

`MemoryComposeSkill` 不是唯一需要读记忆的地方。后续步骤（如 planner 需要知道上轮产出了什么文件）也可能需要。因此"读记忆"应是一个通用能力，而非固化在某个 Skill 里。

**两种读取方式:**

1. **直接读 checkpoint**（已有）: `get_turn_state_graph().aget_state()` → turn_history（含 summary + detail 指针）
2. **打开 detail 文件**: 用现有 `read` 工具 → `data/turns/{thread_id}/turn_N.md`

方式1 返回轻量线索，方式2 返回完整执行记录。LLM/Tool 根据需求自行选择。

**（具体设计后续确定，当前只确认原则：读记忆 = 通用操作，不绑定特定 Skill）**

---

## 五、新增场景

### 5.1 场景配置 (config.py)

```python
"plan_anchor": {
    "name": "锚点式自主决策",
    "agent": "PlanAnchorAgent",
    "note": "多步规划与能力编排（锚点模板），新机制验证",
    "task_policy": TaskMemoryPolicy(max_retain_turns=6),
    "user_policy": UserMemoryPolicy(write_to_file_enabled=True),
},
```

### 5.2 Gate 路由

Gate 新增 `plan_anchor` 路由。原有 `plan` 路由不变（仍到 AutonomousAgent）。两者互不牵扯。

---

## 六、改动物理清单

### 新建文件 (5)

| 文件 | 行数 | 职责 |
|------|------|------|
| `agents/plan_anchor_agent.py` | ~200 | 继承 AutonomousAgent，覆写模板加载/计划生成/执行校验/checkpoint写入 |
| `templates/anchors/word_req_extract.md` | ~40 | 锚点模板示例 |
| `engine/workflow/skills/anchor_template_parser.py` | ~50 | `parse_anchor_template(md_text)` → `{anchors, suggestions}` |
| `memory/normalize.py` | ~50 | `normalize_turn_history(th, thread_id)` → `(th, bool)` 旧→新格式幂等转换 |
| `engine/workflow/skills/turn_detail.py` | ~40 | `write_turn_detail_md(path, data)` 写 Markdown 详情 |

### 修改文件 (8)

| 文件 | 改动量 | 改什么 |
|------|--------|--------|
| `router.py` | +20 | `_agents` 注册 PlanAnchorAgent；`_compensate()` 中调 normalize |
| `config.py` | +6 | 新增 plan_anchor SCENE |
| `gate.py` | +5 | Gate prompt 增加 plan_anchor 路由 |
| `engine/base/workflow.py` | -30+20 | `_slim_turn_history()`/`build_checkpoint_memory()` 适配 clue 格式 |
| `engine/workflow/graph_query/_nodes_memory.py` | ~15 | `_slim_turn_entry()`/`_memory_context_for_compose()` 适配 |
| `prompts/memory_compose.j2` | -40+20 | 删除 sub_queries/files/output_info 相关指令，基于 summary+detail |
| `engine/workflow/graph_query/runner.py` | ~15 | `commit_graph_query_turn_to_checkpoint()` 写 detail 文件 + clue 格式 entry |
| `agents/autonomous_agent.py` | ~15 | `_invoke()` 末尾 checkpoint 写入改 clue 格式 + 写 detail |
| `engine/workflow/skills/planner_skill.py` | +2 | `plan()` 加可选参数 `template_section` |

### 不改动

```
CapabilityRegistry / CapabilityMeta
TemplateRecallSkill (仍为 AutonomousAgent 服务)
PlannerSkill (主体不变)
所有 Skill: file_parse, entity_crud, ir_pr_link, export_data, render, skeleton_guidance,
           leaf_match_fill, content_locate, websearch
BaseNode / BaseRunner / BaseAgent / MemoryAgent
WriteUserMemorySkill / MemoryDistillSkill (操作 MD 文件，不碰 checkpoint)
templates/*.md (旧模板，AutonomousAgent 继续使用)
```

---

## 七、执行链路 (PlanAnchorAgent 完整一轮)

```
Router.dispatch(scene="plan_anchor" 或 Gate→plan_anchor)
│
├─ _compensate(user_input)
│   ├─ get_turn_state_graph().aget_state() → turn_history
│   ├─ normalize_turn_history(th, tid)  ← ★ 旧格式迁移（幂等）
│   ├─ build_checkpoint_memory(th)  ← 只处理 clue 格式
│   ├─ _parse_user_memory_sections(uid) → 画像/知识
│   └─ MemoryComposeSkill.refine() → compensated_intent
│
├─ PlanAnchorAgent.arun(user_input, compensated_intent)
│   └─ _invoke(ctx)
│       ├─ _get_memory_context()  → intent_input
│       ├─ 模板匹配: scan templates/anchors/ → anchor_template
│       ├─ _generate_plan()
│       │   └─ PlannerSkill.plan(template_section=_format_anchor_template(anchor))
│       ├─ _confirm_plan()
│       ├─ _execute_plan()
│       │   for each step:
│       │     if anchor: validate_params(params, anchor.params_schema)
│       │     cap.execute() → result
│       │     step_details.append({step_id, capability, params, output_summary, elapsed})
│       ├─ _summarize_results() → answer
│       ├─ ctx.add_memory_payload()  ← 写 MD 文件 (不变)
│       ├─ _build_clue_and_detail(turn_id, user_input, intent, plan, step_details, answer)
│       │   → {summary, detail_file, detail_data}
│       ├─ write_turn_detail_md(detail_file, detail_data)
│       └─ get_turn_state_graph().aupdate_state()
│           entry = {turn_id, user_input, intent:"plan_anchor", summary, detail}
│
└─ MemoryAgent.on_agent_success()
    → WriteUserMemorySkill → MD文件
    → bump_and_should_distill()
```

---

## 八、build_checkpoint_memory 适配后产物对比

```
旧 (传给 MemoryComposeSkill 的 JSON):
{
    "turn_timeline": [
        {"turn_id": 1, "route": "domain_query"},
        {"turn_id": 2, "route": "plan", "output_info": [...], "files": [...]}
    ],
    "turns_by_route": {
        "domain_query": [{"turn_id": 1, "user_input": "...", "sub_queries": ["SELECT..."]}],
        "plan": [{"turn_id": 2, "user_input": "...", "sub_queries": []}]
    }
}
~2KB, 嵌套 JSON, sql_digest 未在 turns_by_route 中展示但仍在原 entry 中

新:
{
    "turn_timeline": [
        {"turn_id": 1, "route": "domain_query", "summary": "查IR和PR; 拆解3步; 涉及:需求结构树管理,需求分配管理...", "detail": "data/turns/x/turn_1.md"},
        {"turn_id": 2, "route": "plan", "summary": "解析需求文档→提取12实体→入库成功", "detail": "data/turns/x/turn_2.md"}
    ]
}
~500B, 扁平结构, 只剩 summary + detail 指针
```

---

## 九、存量迁移 (normalize_turn_history)

### 目的

存量 thread 的 checkpoint 中是旧格式 entry（含 sub_queries, sql_digest, files, output_info）。`build_checkpoint_memory()` 改为只处理 clue 格式后，旧格式无法被消费。

### 方案

Router._compensate() 读 checkpoint 后、build_checkpoint_memory() 前执行一次性转换：

```
旧 entry:
  {turn_id, user_input, intent, sub_queries:[...], sql_digest:[{id,name},...]}

      ↓ normalize (提取 sub_queries 描述 + sql_digest 的 name 字段 → 拼 summary)

新 entry:
  {turn_id, user_input, intent, summary:"查IR和PR；拆解3步；涉及:需求结构树管理,...", detail: null}
  ← detail 为 null（历史数据无详情文件）
```

**幂等**：已有 `summary` 字段的 entry 直接跳过。

**下游可用性**：MemoryComposeSkill 做指代消解只需要实体名。旧 sql_digest 的 name 字段被提取进 summary 的自然语言中，信息量不丢失。唯一损失：无 detail 文件（历史执行细节不可追溯）。

### 位置

`memory/normalize.py` → `normalize_turn_history(th: list, thread_id: str) -> tuple[list, bool]`

---

## 十、未决问题（下次讨论）

1. **读记忆工具的通用设计**：哪些 Tool 需要读取记忆？是统一封装为 `read_turn_detail(turn_id)` 还是直接复用 `read()` 文件工具？MemoryComposeSkill 之外的调用方（如 planner 需要知道上轮产出物）怎么自然地触发读取？

2. **entities_touched 的提取标准化**：当前各 Agent 各自构造 summary，提取实体名的规则不统一。是否需要在 `BaseCapability` 层增加接口（如 `extract_entities(result) -> list[str]`）？

3. **detail 文件生命周期**：跟随 checkpoint 裁尾（保留最近 N 轮），还是惰性清理（孤儿文件下次扫描删）？detail 文件可能在渲染、导出等场景被用户引用，保留策略是否要长于 checkpoint？

4. **锚点模板的数量和覆盖范围**：除 `word_req_extract` 外还有哪些场景需要锚点模板？`prd_to_product_requirements`、`req_analysis_to_prd`、`ir_pr_link` 是否都需要锚点版本？

5. **两个 Agent 共存的长期策略**：锚点机制验证成功后，AutonomousAgent 是否有必要保留？还是逐步将所有模板迁移到锚点格式后废弃旧 Agent？

6. **CapabilityMeta.input_schema 补全**：当前只有 `entity_crud` 声明了 `required` 字段，其余能力直接缺少 type/enum/required 约束。补全工作量和优先级？
