# IR ↔ PR 关系自动建立 — 实现指南

## 概述

两端需求入库后自动建立覆盖关系（`ir_pr_links`）：
- **IR（用户需求项）**：来自 `word_req_extract.md` 模板，`RequirementItem` 实体，通过 `rm_id` FK 关联 `RequirementModel`
- **PR（产品需求项）**：来自 `prd_to_product_requirements.md` 模板，`ProductRequirement` 实体，通过 `parent_id` 自引用形成层级树

匹配依据：**full_path** + **description**，LLM 四态匹配（参考 `leaf_match_fill.py` 模式）。

---

## 1. 整体数据流

```
模板步骤:
  1. domain_query_agent  →  查询 IR 列表（按用户描述范围）
  2. domain_query_agent  →  查询 PR 列表（按用户描述范围，type="正文"）
  3. ir_pr_link          →  核心匹配 skill（内部调 get_llm_info + LLM 匹配）
  4. render_result       →  展示匹配结果（high/partial/missing 分档）
  5. entity_crud         →  写 IRPRLink（用户确认 partial 后执行）

ir_pr_link skill 内部:
  RequirementItem.get_llm_info(ir_list)  →  [{id, title, description, full_path, entity_type}, ...]
  ProductRequirement.get_llm_info(pr_list) →  [{id, title, description, full_path, entity_type}, ...]
  格式化 → LLM 四态匹配 → 返回 [{ir_id, pr_id, confidence, match_level, reasoning}, ...]
```

---

## 2. 新建文件

### 2.1 `tools/path_assembler.py` — 通用 parent_id → full_path 工具

**定位**：纯内存工具，零依赖（不依赖 ORM / DB / 实体类），输入已查出的 dict 列表，原地附加 `full_path` 字段。

**函数签名**：

```python
def assemble_paths(
    entities: list[dict],
    *,
    id_field: str = "id",
    parent_field: str = "parent_id",
    name_field: str = "name",
    separator: str = " > ",
) -> list[dict]:
```

**逻辑**：

```
1. 建索引: id_map = {e[id_field]: e for e in entities}
2. 遍历每条 entity:
   a. parts = [entity.get(name_field, "")]
   b. pid = entity.get(parent_field)
   c. while pid 且 pid 在 id_map 中:
        parent = id_map[pid]
        parts.insert(0, parent.get(name_field, ""))
        pid = parent.get(parent_field)
   d. entity["full_path"] = separator.join(p for p in parts if p)
3. 返回 entities（原地修改）
```

**注意**：
- 不处理循环引用（业务保证无环）
- 不处理跨类型 parent_id（如 PR→Part，当前不需要）
- 空 parent_id 或不在 id_map 中的 parent_id 视为根节点
- `name_field` 作为路径片段，如果实体用 `title` 做路径片段则调用时传 `name_field="title"`

**用法示例**：

```python
from tools.path_assembler import assemble_paths

pr_dicts = [{"id": e.id, "parent_id": e.parent_id, "name": e.name, ...} for e in pr_list]
assemble_paths(pr_dicts)
# pr_dicts[0]["full_path"] == "PRD说明书 > 功能需求 > 文件管理 > 上传模块"
```

---

### 2.2 `engine/workflow/skills/ir_pr_link.py` — IR-PR 四态匹配 Skill

**定位**：注册为 capability `ir_pr_link`，LLM 四态匹配 IR ↔ PR，参考 `leaf_match_fill.py` 的结构。

**输入**：
- `ir_data`: list[dict] — 从 `_ctx.previous` 自动获取（domain_query_agent 产出）或显式传入
- `pr_data`: list[dict] — 同上
- `high_threshold`: float = 0.8
- `missing_threshold`: float = 0.3

**内部流程**：

```
1. 从 _ctx 或参数获取 IR 列表、PR 列表
2. 调 RequirementItem.get_llm_info(ir_list)    → ir_infos
3. 调 ProductRequirement.get_llm_info(pr_list)  → pr_infos
4. 格式化 ir_infos + pr_infos 为 LLM 可读文本
5. LLM 结构化输出: [{ir_id, pr_id, confidence, reasoning}, ...]
6. 按阈值分档: high(≥0.8) / partial(0.3~0.8) / missing(<0.3)
7. 返回 CapabilityResult
```

**输出 `CapabilityResult.output_data`**：

```python
{
    "stats": {"total": 10, "high": 5, "partial": 3, "missing": 2},
    "match_results": [
        {
            "ir_id": "IR-1",
            "pr_id": "PR-3",
            "confidence": 0.85,
            "match_level": "high",
            "reasoning": "IR描述'超大文件上传'与PR正文'支持1000页以上文档上传'高度一致，且全路径均在文件管理模块下",
        },
        ...
    ],
    "unmatched_ir": [...],  # missing 的 IR 列表
    "unmatched_pr": [...],  # 未被任何 IR 匹配的 PR 列表
}
```

**LLM Prompt 核心要点**：

- 同时给两边的 full_path + title + description
- 说明匹配信号：
  1. description 语义重叠度（核心）
  2. full_path 上下文一致性（消歧）
  3. title 关键词命中
- 要求输出 confidence 值 0-1 + 一行 reasoning
- 每条 IR 找 0 或 1 个最佳 PR（不是多对多展开）

**格式化函数**（参考 `leaf_match_fill.py` 的 `_format_template_leaves` / `_format_source_leaves`）：

```python
def _format_ir_list(ir_infos: list[dict]) -> str:
    """每条 IR 一行，展示 full_path + title + description 摘要"""

def _format_pr_list(pr_infos: list[dict]) -> str:
    """每条 PR 一行，同上"""
```

**并发**：如果 IR 数量大，可按功能域（full_path 一级章节）分组并发匹配，参考 `_group_template_leaves` + `asyncio.Semaphore` 模式。初期可先单组全量匹配，后续优化。

**CapabilityMeta 注册要点**：

```python
capability_meta = CapabilityMeta(
    name="ir_pr_link",
    category="skill",
    tags=["match", "link", "requirement"],
    short_description="基于全路径和描述对IR和PR进行四态匹配，建立覆盖关系",
    dependencies=[
        {
            "capability": "domain_query_agent",
            "provides": ["query_result"],
            "description": "需前置 domain_query_agent 查询 IR 和 PR 列表",
        },
    ],
    input_schema={...},
    output_schema={...},
    output_field="match_results",
    visible_to_planner=True,
)
```

---

### 2.3 `prompts/ir_pr_link.j2` — 匹配 Prompt 模板

**结构**：

```
System:
  你是需求分析专家。给定用户需求(IR)列表和产品需求(PR)列表，
  为每条IR找到最佳匹配的PR。

  匹配信号（按重要性排序）:
  1. description 语义重叠度 — 核心，两个描述说的是不是同一件事
  2. full_path 上下文一致性 — 消歧，全路径是否在同一功能域
  3. title 关键词命中 — 辅助

  对每条 IR，输出：
  - ir_id: IR 标识
  - pr_id: 最佳匹配的 PR 标识，无合适匹配则为空
  - confidence: 0-1 置信度
  - reasoning: 简短匹配理由（20字以内）

  阈值指导:
  - ≥0.8: 描述高度一致，路径匹配
  - 0.5-0.8: 描述部分重叠，路径相关
  - ≤0.3: 描述无关，或路径完全不相关

Human:
  用户需求(IR)列表:
  {ir_list_text}

  产品需求(PR)列表:
  {pr_list_text}

  请为每条IR找到最佳匹配的PR。
```

---

### 2.4 `templates/ir_pr_link.md` — 模板文件

```markdown
---
template_id: ir_pr_link
short_description: 对已入库的用户需求和产品需求建立覆盖关系
applicable: IR和PR均已入库，用户要求建立/分析覆盖关系
not_applicable: IR或PR尚未入库（需先走 word_req_extract 或 prd_to_product_requirements）
expected_output: IRPRLink 关系入库统计 + 匹配分析报告
---

# 模板：IR ↔ PR 覆盖关系建立

## 适用场景

用户需求和产品需求均已结构化入库，需要分析两者间的覆盖关系并建立链接。

典型问法：
- "把XX产品下的用户需求和产品需求建立覆盖关系"
- "分析当前所有IR和PR的对应关系"
- "检查用户需求是否都有对应的产品需求覆盖"

## 步骤

1. domain_query_agent
   reason: 查询指定范围内的用户需求项(IR)
   depends_on: []
   params:
     query: <按用户描述查询 RequirementItem，如"查询[产品名]下所有用户需求项">

2. domain_query_agent
   reason: 查询指定范围内的产品需求正文节点(PR)
   depends_on: []
   params:
     query: <按用户描述查询 type=正文 的 ProductRequirement，如"查询[产品名]下所有产品需求正文节点">

3. ir_pr_link
   reason: 对IR和PR进行四态匹配
   depends_on: [1, 2]
   params:
     high_threshold: 0.8
     missing_threshold: 0.3

4. render_result
   reason: 展示匹配分析报告
   depends_on: [3]
   params:
     user_input: <原始用户问题>
     data: <从步骤3获取 match_results>

5. entity_crud
   reason: 将确认的高置信度和用户选中的部分匹配关系写入 IRPRLink
   depends_on: [4]
   params:
     operation: batch_upsert
     data: <high + 用户确认的 partial 的 IRPRLink 数据>
```
```

---

## 3. 修改文件

### 3.1 `base/entity/reqmgmt/node_base.py` — 基类加 `get_llm_info`

在 `ReqMgmtEntity` 类中添加类方法：

```python
@classmethod
def get_llm_info(cls, entities: list[G]) -> list[dict[str, Any]]:
    """将实体列表转为 LLM 匹配用的信息 dict 列表。

    子类应覆写此方法以提供各自的全路径组装逻辑。

    返回每个 dict 包含:
      - id: 实体标识
      - name: 实体名
      - title: 标题
      - description: 描述文本
      - full_path: 全路径（由子类逻辑组装）
      - entity_type: 实体类型标识（如 "IR", "PR"）
    """
    return [
        {
            "id": e.id,
            "name": getattr(e, "name", ""),
            "title": getattr(e, "title", ""),
            "description": getattr(e, "description", ""),
            "full_path": getattr(e, "name", "") or getattr(e, "title", "") or "",
            "entity_type": cls.__tablename__,
        }
        for e in entities
    ]
```

默认实现返回基本字段，`full_path` 仅取 name 或 title（无层级路径）。子类覆写提供真正的全路径。

---

### 3.2 `base/entity/reqmgmt/requirement_item.py` — IR 覆写 `get_llm_info`

```python
@classmethod
def get_llm_info(cls, entities: list[RequirementItem]) -> list[dict[str, Any]]:
    """批量组装 IR 的全路径信息。

    路径格式: Product.name > RequirementModel.name > IR.title
    与 word_req_extract.md 的三级映射一致。
    """
    if not entities:
        return []

    # 1. 收集所有 rm_id，批量查询 RequirementModel
    rm_ids = list({e.rm_id for e in entities if e.rm_id})
    from .requirement_model import RequirementModel
    rm_map: dict[str, Any] = {}
    if rm_ids:
        from base.domain import DomainManager
        session = DomainManager.current().resolve_session(None)
        from sqlalchemy import select
        rms = session.scalars(
            select(RequirementModel).where(RequirementModel.id.in_(rm_ids))
        ).all()
        rm_map = {rm.id: rm for rm in rms}

    # 2. 收集所有 product_id，批量查询 Product
    product_ids = list({rm.product_id for rm in rm_map.values() if rm.product_id})
    from .product import Product
    product_map: dict[str, Any] = {}
    if product_ids:
        from base.domain import DomainManager
        session = DomainManager.current().resolve_session(None)
        from sqlalchemy import select
        products = session.scalars(
            select(Product).where(Product.id.in_(product_ids))
        ).all()
        product_map = {p.id: p for p in products}

    # 3. 内存组装
    result = []
    for ir in entities:
        parts = []
        rm = rm_map.get(ir.rm_id) if ir.rm_id else None
        if rm and rm.product_id:
            product = product_map.get(rm.product_id)
            if product and product.name:
                parts.append(product.name)
        if rm and rm.name:
            parts.append(rm.name)
        parts.append(ir.title or ir.name or "")
        result.append({
            "id": ir.id,
            "name": ir.name or "",
            "title": ir.title or "",
            "description": ir.description or "",
            "full_path": " > ".join(p for p in parts if p),
            "entity_type": "IR",
        })
    return result
```

**关键点**：
- 两次批量查询（RM + Product），O(N) 变 O(2)，避免 N+1
- 路径格式与 `word_req_extract.md` 解析规则一致
- 返回 dict 列表，不返回 ORM 对象（解耦）

---

### 3.3 `base/entity/reqmgmt/product_requirement.py` — PR 覆写 `get_llm_info`

```python
@classmethod
def get_llm_info(cls, entities: list[ProductRequirement]) -> list[dict[str, Any]]:
    """批量组装 PR 的全路径信息。

    路径格式: 通过 parent_id 链自底向上追溯，用 " > " 拼接。
    依赖 tools.path_assembler.assemble_paths 工具。
    """
    if not entities:
        return []

    # 1. 转为 dict 列表
    dicts = [
        {
            "id": e.id,
            "name": e.name or "",
            "title": e.title or "",
            "description": e.description or "",
        }
        for e in entities
    ]

    # 2. 建立 parent_id 映射（从 ORM 对象取 parent_id）
    for i, e in enumerate(entities):
        dicts[i]["parent_id"] = getattr(e, "parent_id", None)

    # 3. 调路径组装工具
    from tools.path_assembler import assemble_paths
    assemble_paths(dicts, name_field="name")

    # 4. 返回标准格式
    return [
        {
            "id": d["id"],
            "name": d.get("name", ""),
            "title": d.get("title", ""),
            "description": d.get("description", ""),
            "full_path": d.get("full_path", d.get("name", "")),
            "entity_type": "PR",
        }
        for d in dicts
    ]
```

**注意**：
- `name_field` 传 `"name"`（PR 用 name 字段做路径片段）
- `assemble_paths` 原地修改 dicts，附加 `full_path` 字段
- `parent_id` 从 ORM 对象取，不额外查库（数据已由 domain_query_agent 查出）

---

## 4. 实现顺序

| 顺序 | 文件 | 原因 |
|------|------|------|
| 1 | `tools/path_assembler.py` | 底层通用工具，无依赖，先建 |
| 2 | `base/entity/reqmgmt/node_base.py` | 基类加默认 `get_llm_info` |
| 3 | `base/entity/reqmgmt/product_requirement.py` | PR 覆写，依赖步骤 1,2 |
| 4 | `base/entity/reqmgmt/requirement_item.py` | IR 覆写，依赖步骤 2 |
| 5 | `prompts/ir_pr_link.j2` | Prompt 模板，独立 |
| 6 | `engine/workflow/skills/ir_pr_link.py` | Skill，依赖步骤 1-5 |
| 7 | `templates/ir_pr_link.md` | 模板，依赖步骤 1-6 |

---

## 5. 现有能力复用清单

| 能力 | 用途 | 入参要点 |
|------|------|----------|
| `domain_query_agent` | 查询 IR / PR 数据 | query 描述范围 + entity 表名，走 Text2SQL |
| `render_result` | 展示匹配结果 | user_input 原始问题 + data=match_results，LLM 渲染为自然语言报告 |
| `entity_crud` | 写 IRPRLink | operation=batch_upsert, data=[{_type:"ir_pr_links", ir_id, pr_id}, ...]，自带确认弹窗 |

**entity_crud 写 IRPRLink 时**：
- `_type` 需要映射到 `ir_pr_links`，当前 `_TYPE_CLASS_MAP` 里没有，需要添加：`"ir_pr_links": "IRPRLink"`
- 或者不走 `entity_crud`，skill 内部直接 `session.merge(IRPRLink(...))`。但如果要保留用户确认，建议走 `entity_crud` 并补映射。

---

## 6. 匹配确认展示方案

`render_result` 的 data 传入 `match_results`，LLM 渲染为如下结构的报告：

```markdown
## IR ↔ PR 覆盖关系分析报告

### 高置信度匹配（自动链接）— 5 对
| IR | IR 全路径 | PR | PR 全路径 | 置信度 | 理由 |
|----|----------|----|----------|--------|------|
| IR-1 | 智能文档 > 用户需求 > 超大文件上传 | PR-3 | PRD > 功能 > 文件管理 > 上传 | 0.85 | 语义一致 |

### 建议确认 — 3 对
| IR | PR | 置信度 | 理由 | 确认？ |
|----|----|--------|------|--------|
| IR-5 | PR-7 | 0.65 | 部分重叠 | ⬜ |

### 未覆盖
- IR-9 语音备注（无对应 PR）
- IR-10 AI摘要（无对应 PR）

### 未被引用的 PR
- PR-12 XXX功能（无 IR 引用）
```

用户确认 partial 后，high + 确认的 partial → `entity_crud` 写库。

---

## 7. 边界情况

1. **IR 列表为空**：直接返回空结果，错误信息"未查询到用户需求项"
2. **PR 列表为空**：直接返回空结果，错误信息"未查询到产品需求项"
3. **IR 的 rm_id 为 null**：full_path 只有 IR 自身的 title
4. **PR 的 parent_id 链断裂**（查出的集合不包含某 parent_id）：assemble_paths 将该 parent_id 值作为路径片段（看作根）
5. **同 Product 下多个 RM**：IR 的 full_path 能区分不同需求模型
6. **一条 IR 匹配到多条 PR**：取 confidence 最高的；confidence 相同时都保留，标记为"一对多"
7. **匹配结果已存在**：entity_crud 的 upsert 按 (ir_id, pr_id) 联合主键自动去重
