# CodeMind 领域适配计划

> **当前分支**：`domain-refactor` | **更新时间**：2026-05-08
>
> 目标：将 CodeMind 从「Java 代码库专用」重构为「领域可配置的通用数据智能助手」。
> 切换领域只需修改 `domain.toml` + 提供新的 entity 包。

---

## 一、整体架构

```
domain.toml                     ← 唯一切换点：active = "codebase" | "reqmgmt"
    ↓
DomainManager                   ← 单例，加载当前 domain 配置
    ↓
DomainConfig                    ← 封装所有领域差异
    ├── entity_package          ← ORM 实体包路径
    ├── entity_classes          ← ORM 类列表
    ├── schema_ddl              ← DDL 文件路径
    ├── db_path                 ← 领域数据库路径
    ├── gate_rules              ← Gate 关键词规则
    ├── gate_llm_prompt         ← Gate LLM 分类 prompt
    ├── scenes                  ← 领域场景定义
    └── template_dir            ← 领域模板目录（可选）
```

两个领域（codebase vs reqmgmt）在**操作模式**上高度一致：查询 → Text2SQL、写入 → Parse+CRUD、编排 → Planner+Capability 组合。差异仅在 Entity 定义和 Prompt 上下文，**框架层不动**。

---

## 二、进度总览

| Phase | 名称 | 状态 | 关键产出 |
|-------|------|------|---------|
| 1 | Domain 抽象层 | ✅ | `base/domain.py`、`domain.toml`、`runtime.py` 改造 |
| 2 | 领域模块化 | ✅ | `domains/codebase/`、`domains/reqmgmt/` |
| 3 | Workflow 层解耦 | ✅ | `_nodes_sql.py` 从 Domain 获取 schema/engine |
| 4 | Agent 重命名 | ✅ | `GraphQueryAgent` → `DataQueryAgent`，`agent_scope` 动态 |
| 5 | Gate/Config 解耦 | ✅ | Gate 规则和 prompt 从 DomainConfig 获取 |
| 6 | 通用 Skill 新增 | ✅ | `entity_crud`、`export_data`、`entity_extract`（直接调用工具）|
| 7 | reqmgmt 实体+Word 解析 | ✅ | 11 个 ORM + DDL + Word→实体→入库 e2e |
| 8 | Planner/模板可靠性 | ✅ | `visible_to_planner` 字段、`_format_template` 强约束、planner.j2 重构 |
| 9 | 清理与文档 | 🔄 | 本文档 + Guide 更新完成；模板场景验证进行中 |

---

## 三、已完成内容

### 基础设施

- **`base/domain.py`**：`DomainManager` 单例 + `DomainConfig` 数据类 + `GateRule` + `SceneDef`。`build_schema_text()` 从 entity classes 的 `LLM_NODE_NOTE` 生成 schema 描述。
- **`domain.toml`**：`active = "reqmgmt"`（当前激活需求管理领域）
- **`base/entity/runtime.py`**：`get_domain_engine()`、`init_domain_database()`、向后兼容旧函数
- **`utils/paths.py`**：`domain_db_path()` 替代硬编码路径

### 领域模块

- **`domains/codebase/__init__.py`**：提取原散落在 `gate.py`/`config.py` 的 codebase 配置
- **`domains/reqmgmt/__init__.py`**：需求管理领域配置（Gate 规则、Scenes、LLM prompt）

### Workflow / Agent 解耦

- `_nodes_sql.py`：`load_schema()` 和 `ExecuteSQLNode` 从 DomainManager 获取 schema 和 engine
- `gate.py`：规则和 prompt 从 `DomainConfig` 动态获取
- `config.py`：SCENES 从 Domain 合并
- `GraphQueryAgent` → `DataQueryAgent`（`agent_scope` 和 `capability_meta` 动态）
- 模板中 `graph_query_agent` → `domain_query_agent`

### 通用 Skill

| Skill | 注册名 | 类型 | 功能 |
|-------|--------|------|------|
| `EntityCRUDSkill` | `entity_crud` | 注册能力 | 批量导入/删除实体（自动去重）；`require_confirm` |
| `ExportSkill` | `export_data` | 注册能力 | Excel/CSV/DOCX 导出 |
| `EntityExtractSkill` | — | 直接调用工具 | LLM 从文本提取实体字段值；无 `capability_meta`，由特定流程直接调用 |

### Planner / 模板可靠性改进（Phase 8）

**问题背景**：Planner 在接收到匹配模板后仍可能忽略模板步骤，自插无关能力（如用 `domain_query_agent` 替换 `skeleton_guidance`）。根因是模板引导措辞太弱（"仅供参考"）且 Planner prompt 主动削弱模板约束力。

**已实施的修复**：

| 改动 | 位置 | 效果 |
|------|------|------|
| `visible_to_planner: bool = True` | `CapabilityMeta` 新增字段 | `False` 的能力仅模板可引用，自主规划时隐藏 |
| `skeleton_guidance` / `leaf_match_fill` | `visible_to_planner=False` | 这两个 docx 填充专用技能不污染自主规划候选清单 |
| `entity_extract` 去掉 `capability_meta` | 降级为直接调用工具 | Planner 不再能看到它（`file_parse` 已内置实体提取） |
| `_format_template()` 有模板时强约束 | `planner_skill.py` | 标题改为「必须严格遵循，禁止增删步骤或替换能力名」 |
| `_format_template()` 无模板时给要点 | `planner_skill.py` | 返回自主规划要点（原 planner.j2 中的内容） |
| `planner.j2` 去掉削弱措辞 | 删除"模板仅作参考"等文字 | `{template_section}` 内部控制强弱，planner.j2 不再反着说 |
| `_INFRA_NAMES` 加 `generic_agent` | `autonomous_agent.py` | GenericAgent 不参与 Planner 候选 |
| 有模板时不过滤 `visible_to_planner` | `autonomous_agent.py` | 模板步骤引用的专用能力在候选清单中可见 |
| 模板 `graph_query_agent` → `domain_query_agent` | 两个 builtin 模板 | 能力名与实际注册名一致 |

### 文件解析（Word → 实体）

| 组件 | 文件 | 职责 |
|------|------|------|
| `FileParseSkill` | `skills/file_parse/__init__.py` | MIME 门面，分发到 handler |
| `DocumentParseHandler` | `skills/file_parse/document_parse.py` | Word→实体核心：分组调 LLM（`asyncio.Semaphore` 并发控制）→ 组装 FK → 正文回设 |
| `extract_outline` | `tools/file_parser/word_parser.py` | 纯 python-docx：标题树提取 + 叶子剥离 + 分组展平 + `paragraphs_lookup` |

关键设计：
- 叶子节点（H3）正文先剥离再发 LLM，节省 token
- LLM 仅做 entity type 分类 + 字段填充
- 分类完成后，`_assemble_entities` 从 `paragraphs_lookup` 回设真实正文
- 回设检查 LLM **输入**（`sec.paragraphs` 含"已剥离"）而非输出，兼容傻模型和聪明模型

### 模板系统

已注册模板（5 个）：

| 模板 ID | 步骤 | 用途 |
|---------|------|------|
| `builtin_query_codebase` | `domain_query_agent` → `render_result` | 代码库数据查询 |
| `builtin_search_web` | `web_search_parser` → `render_result` | 外部知识搜索 |
| `builtin_analyze_recommend` | `domain_query_agent` → `web_search_parser` → `render_result` | 综合分析建议 |
| `word_req_extract` | `file_parse` → `entity_crud` | Word 文档解析入库 |
| `req_analysis_to_prd` | `file_parse` → `skeleton_guidance` → `leaf_match_fill` → `export_data` | 需求文档→PRD 说明书 |

模板步骤 params 区分两类：
- **具体参数**（值不含 `<>`）：直接复制到 plan step params，planner 严禁修改
- **参数提示**（值含 `<>`）：planner 根据用户意图生成具体值

`_format_template()` 有模板时输出强约束，`_merge_template_concrete_params()` 兜底注入。

### reqmgmt 实体（11 个 ORM）

```
Product → RequirementModel → IR / PR → Part → TC → TS
                  ↓              ↓       ↓     ↓
              (type: user/      ir_pr   pr_part part_tc tc_ts
               product)         _links  _links  _links  _links
```

所有实体遵循 codebase entity 协议：`__tablename__`、`TABLE`、`LLM_NODE_NOTE`、`LLM_TAGS`。

---

## 四、已验证

```
✅ DomainManager 双领域加载 (codebase / reqmgmt)
✅ reqmgmt schema 文本生成
✅ ORM 与 DDL 对齐 (表名、列名完全一致)
✅ 种子数据写入/查询
✅ 未覆盖 IR 查询 (LEFT JOIN)
✅ 验证状态分布查询 (GROUP BY)
✅ 全链路追踪矩阵 (5 表 JOIN)
✅ EntityCRUDSkill 通用 CRUD
✅ ExportSkill Excel/CSV/DOCX 导出
✅ EntityExtractSkill LLM 字段提取（直接调用）
✅ Word 文档解析 e2e (file_parse → entity_crud, deepseek-v4-flash, 1P/4RM/12IR, 0 warnings)
✅ 模板召回 word_req_extract (置信度 10/10)
✅ 模板具体参数注入 (user_prompt 正确流到 LLM)
✅ 正文剥离后回设 (paragraphs_lookup)
✅ LLM 分组并发 (asyncio.Semaphore)
✅ Planner 模板强约束 (_format_template + planner.j2 重构)
✅ entity_extract 从注册表移除 (不再被 Planner 误选)
✅ visible_to_planner 过滤 (模板专用能力不出现自主规划候选)
✅ 模板能力名修正 (graph_query_agent → domain_query_agent)
```

---

## 五、待完成

### 5.1 模板补充

- [ ] `templates/reqmgmt/` 目录下的领域模板（覆盖率分析、验证统计、需求导出）
- [ ] `req_analysis_to_prd` 模板 e2e 验证（Planner 严格遵循 + 完整 4 步执行）

### 5.2 PPT 场景验证

| # | 场景 | 状态 | 备注 |
|---|------|------|------|
| 1 | 提取用户需求并结构化存储 | ✅ | Word 解析 e2e 已验证 |
| 2 | 基于模板生成产品需求 | ⏳ | `req_analysis_to_prd` 模板就绪，待 e2e 验证 |
| 3 | 多角度分析/补充产品需求 | ⏳ | 需 Agent 编排验证 |
| 4 | 产品需求结构化及关系关联 | ⏳ | EntityCRUD + 边表写入 |
| 5 | 产品需求查看 (Text2SQL) | ⏳ | 需切换 domain 启动服务验证 |
| 6 | 需求覆盖率统计 | ✅ | LEFT JOIN 查询已验证 |
| 7 | 验证情况统计 | ✅ | GROUP BY 查询已验证 |
| 8 | 全链路看板+导出 | ⏳ | 全链路 SQL 已验证，Export 需端到端验证 |

### 5.3 稳定性

- [x] ~~Planner 跨模型一致性~~：`planner.j2` 重构 + `_format_template` 强约束后，模板遵从度不再依赖模型判断；具体参数通过 `_merge_template_concrete_params` 兜底
- [x] ~~entity_extract 被 planner 误用~~：已从注册表移除，降级为直接调用工具
- [ ] `req_analysis_to_prd` 模板 e2e 验证：需确认 Planner 在 deepseek-v4-flash 下严格遵循 4 步模板

### 5.4 清理

- [ ] 删除 `agents/graph_query_agent.py` 中的 `GraphQueryAgent` 向后兼容别名（确认无引用后）
- [ ] 统一 `graph_query` → `domain_query` 命名
- [ ] `templates/` 下 reqmgmt 专属模板可考虑迁移到子目录

---

## 六、技术备忘

### Planner + 模板的协作方式（更新）

```
用户输入 → TemplateRecall (LLM 打分，≥7 且差距≥2) → 匹配模板
    ↓
AutonomousAgent 候选过滤:
  - 有模板 → 不过滤 visible_to_planner（模板步骤可能引用专用能力）
  - 无模板 → 过滤 visible_to_planner=False（隐藏模板专用能力）
    ↓
PlannerSkill:
  - _format_template(template):
    有模板 → 「必须严格遵循，禁止增删步骤或替换能力名」+ 步骤清单
    无模板 → 「请根据能力清单自主规划」+ 自主规划要点
  - 生成 Plan
    ↓
AutonomousAgent:
  - _merge_template_concrete_params 兜底注入具体参数
  - _execute_plan 逐步执行（CapabilityRegistry.get_by_name 查找能力）
    ↓
各能力单元 execute(**params) 含 _ctx 上下文传递
```

### 能力可见性控制

```
CapabilityMeta.visible_to_planner:
  True  (默认) → 通用能力，自主规划可选
  False         → 模板专用能力，仅被模板显式引用时出现在候选清单

_INFRA_NAMES = {"planner", "template_recall", "generic_agent"}
  → 基础设施，始终不出现在 Planner 候选清单

非注册 Skill（无 capability_meta）：
  → 直接调用工具，Planner 完全不可见
```

### file_parse 的实体提取流程

```
Word 文档
  → extract_outline()          纯代码：标题树 + 段落
  → build_structure_for_llm()  剥离叶子正文 + 分组 + 展平 + paragraphs_lookup
  → 4 个分组并行调 LLM          asyncio.Semaphore(3) 控并发
  → _assemble_entities()       按 parent_id 建 FK + 回设真实正文
  → _validate_entities_generic() FK 有效性 + 必要字段校验
  → 输出 entities JSON          {entities, stats, warnings}
```
