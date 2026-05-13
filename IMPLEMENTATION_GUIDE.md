# 实施指南：req_analysis_to_prd 场景迁移

## 目标

将 CodeMind 的 `templates/req_analysis_to_prd.md` 场景迁移到当前 DeepMind 工程，
实现 **「上传 N 个需求文档 + 1 个 PRD 模板 .docx → 自动填充生成完整 PRD 说明书」**。

## 场景流水线（4 步）

```
用户上传（信息源 .docx × N + 模板 .docx × 1）
  ↓
Step 1: file_parse      批量解析、区分模板/信息源、叶子展平、长文本摘要
  ↓
Step 2: skeleton_guidance   LLM 对比骨架 → 术语统一 + 范围约束 + 填充策略
  ↓
Step 3: leaf_match_fill     LLM 逐叶子四态匹配（skip/high/partial/missing）
  ↓
Step 4: export_data        纯代码回写模板 .docx → 生成最终 PRD 文件
```

## 已有可复用组件

| 组件 | 路径 | 说明 |
|------|------|------|
| `extract_outline` | `tools/word_parser.py:52` | .docx → 标题树，与 CodeMind 同源 |
| `parse_docx_outline` | `tools/word_parser.py:366` | @tool 封装，输出 llm_structure |
| `extract_entities` | `tools/entity_extract.py:291` | LLM 实体分类 + 组装 |
| `store_entities` | `tools/store_entities.py` | ORM 批量 upsert |
| `data_paths` | `utils/paths.py:75` | 统一路径管理（含 `output_dir()`） |
| `get_llm` | `llm/base.py` | LLM 工厂，按 role+temperature 缓存 |
| `req-parse` subagent | `agents/subagents/req_parse.py` | 参考模板，照着新增 prd-fill |

---

## 实施步骤（严格按顺序）

### 文件清单总览

```
新建 (11):
  prompts/skeleton_guidance.j2
  prompts/skeleton_guidance_human.j2
  prompts/leaf_match_fill.j2
  prompts/leaf_match_fill_human.j2
  tools/leaf_flattener.py
  tools/docx_writer.py
  tools/skeleton_guidance.py
  tools/leaf_match_fill.py
  skills/req-analysis-to-prd/SKILL.md
  agents/subagents/prd_fill.py

修改 (3):
  tools/__init__.py
  agents/deep_agent.py
  deepMind.toml
```

---

### Step A：提示词文件（4 个 .j2）

> 这 4 个文件直接复制自 CodeMind 同路径，无需修改。放好后验证文件存在即可。

**A1. `prompts/skeleton_guidance.j2`**

完整内容（从 `D:\ai\codeMind\prompts\skeleton_guidance.j2` 复制）：

```
你是一个技术文档架构师。你的任务是对比一份**模板骨架**和多份**信息源骨架**，
生成一份全局指导文档，供后续的逐章节填充工作使用。

## 输入说明

你将收到:
1. **模板骨架**: 最终文档的目录结构, 每个节点标注了是否有占位内容或为空
2. **信息源骨架列表**: 每个信息源的目录结构, 标注了叶子节点的内容摘要

## 你的任务

分析三者之间的对应关系, 生成三部分指导:

### 1. 术语统一
- 从信息源中识别同义词/近义词, 确定本文档统一使用的术语
- 例如: "需求条目/需求项/需求条目" → 统一用 "需求项"
- 例如: "审批/签审/评审" → 根据上下文确定统一用词
- 同时标注**禁用词**（信息源中用了但本文档不应使用的词）

### 2. 范围约束
- 明确本文档覆盖的业务范围和边界
- 明确指出哪些信息源中的内容**不属于**本文档范围（即使信息源有提及）
- 例如: "信息源提到了CAD/PLM系统, 但本文档范围仅为需求管理平台, 不应扩展"

### 3. 填充策略
- 对模板的每个一级章节, 说明主要数据来源是谁
- 标注哪些章节信息充足（直接改写即可）
- 标注哪些章节信息不足（需要推断, 标记[待确认]）
- 标注哪些信息源内容在当前模板中**无处安放**（不要强行塞入）

## 输出格式

返回 JSON:
{{
  "terminology": "1. 统一使用「需求项」而非「需求条目」「需求条目」\\n2. 统一使用「签审」而非「审批」\\n3. ...",
  "scope_constraints": "## 文档范围\\n本文档聚焦需求管理平台...\\n\\n## 排除内容\\n- 不涉及CAD/PLM集成...",
  "fill_strategy": "## 第1章 文档说明\\n来源: 信息源2-引言, 轻量改写...\\n\\n## 第2章 方案目标及范围\\n..."
}}

每个字段的值是 Markdown 格式的详细说明文本, 段落清晰, 可用列表。
```

**A2. `prompts/skeleton_guidance_human.j2`**

```
请分析以下骨架并生成全局指导。

## 模板骨架: {template_label}
{skeleton}

## 信息源骨架列表
{sources_text}
```

**A3. `prompts/leaf_match_fill.j2`**

完整内容（从 `D:\ai\codeMind\prompts\leaf_match_fill.j2` 复制）：

```
你是一个文档匹配填充专家。根据全局指导和信息源内容，为每个模板叶子评估匹配置信度。

## 叶子节点类型识别

模板叶子标注了三种类型:
- **[章标题, 跳过不填]**: 纯结构节点(H1/H2章标题), 不是内容槽。**置信度填 0.0**
- **[空槽位, 需填充]**: 需要填充内容的叶子节点
- **[占位: ...]**: 模板已有示例文字, 需要替换填充

## 章节内容分类

对于需填充的叶子, 根据内容性质评估匹配程度:

### 事实性章节 (可直填)
- 特点: 具体指标、操作步骤、列表、定义、规范
- 信息源有精确来源 → 给高置信度 (0.8-1.0)
- 示例: 性能指标、安全要求、术语定义、角色列表

### 总结性章节 (适度整理)
- 特点: 需要归纳、对比、概述的主观内容
- 关键词: "业务现状"、"方案思路"、"业务转变"、"核心需求"、"分析"、"总结"、"目标"
- **重要: 信息源内容已在 file_parse 阶段提炼到 500 字以内**
- 如果信息源内容充分 → 给高置信度 (0.8-1.0)
- 仅在需要跨多个信息源整合时 → 给中等置信度 (0.3-0.8)，并在 supplement 中输出整合版(≤400字)

### 结构化章节 (重新组织)
- 特点: "术语"、"定义"、"角色列表"、"编写依据" 等列表/表格型内容
- 关键词: "术语"、"定义"、"角色"、"编写依据"、"参考"
- **需要重新整理** → 给中等置信度 (0.3-0.8)，从信息源各处提取相关条目, 在 supplement 中重新组织为**整洁的条目列表**
- 每条: 术语名 — 简要定义 (≤50字)
- 示例输出: "需求项 — 需求管理的最小单元, 可被分配、签审、变更和验证。\n需求基线 — 经正式签审固化的需求集合快照。\n..."

## 置信度评估标准

- **0.8-1.0**: 信息源内容与模板叶子高度匹配，原文可直接使用
- **0.3-0.8**: 信息源内容部分相关，需要补充说明或跨信息源整合
- **0.0-0.3**: 信息源中无相关内容

### 跳过节标题
- 章标题节点 (is_leaf=False, 无占位) → 置信度填 0.0

## 重要约束

{guidance}

## 输出格式
{{
  "leaves": [
    {{"leaf_id": "t1", "confidence": 0.0, "source_refs": []}},
    {{"leaf_id": "t2", "confidence": 0.95, "source_refs": ["b3"]}},
    {{"leaf_id": "t3", "confidence": 0.6, "source_refs": ["b12"], "supplement": "补充说明..."}},
    {{"leaf_id": "t4", "confidence": 0.1, "source_refs": [], "generated_content": "纯文本段落内容..."}}
  ]
}}

规则:
- 章标题置信度填 0.0
- 信息源内容已在 file_parse 阶段提炼到 500 字，**避免二次提炼**
- confidence 必须是 0-1 的浮点数
- generated_content 和 supplement 用纯文本, 不用 markdown
- 不编造信息源中不存在的数据
```

**A4. `prompts/leaf_match_fill_human.j2`**

```
## 当前章节组: {group_path}

## 模板叶子列表 (需填充)
{template_leaves_text}

## 信息源叶子摘要 (供匹配, 原文已保留不在本次上下文中)
{source_leaves_text}

请为每个模板叶子输出匹配结果。
```

---

### Step B：纯代码工具（2 个 .py）

#### B1. `tools/leaf_flattener.py` — 文档叶子展平 + 摘要

**移植源**: `D:\ai\codeMind\tools\file_parser\leaf_flattener.py`

**功能说明**:
- 调用 `tools.word_parser.extract_outline` 解析 .docx
- 将标题树递归展平为带全路径的叶子列表（每个叶子有 id/path/heading/level/original_content/summary/is_leaf/parent_id/source）
- 超过阈值的长文本叶子 → 并发调 LLM 做摘要
- `flatten_document_auto` 自动探测最佳 heading_styles

**适配要点**:

1. 顶部 import 改为：
```python
from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import Any

from tools.word_parser import extract_outline
```

2. LLM 获取方式改为：
```python
from llm.base import get_llm
llm = get_llm("default", temperature=0.0)
```

3. 删除 CodeMind 特有的 `from agents.llm_callback import make_llm_meta` 依赖。
   在 `llm.ainvoke()` 时不传 `config` 参数（或传空 `{}`），因为 DeepMind 不需要 `make_llm_meta`。

4. 保留核心函数签名不变：
   - `flatten_outline(outline, *, source_label="", id_prefix="s") -> list[dict]`
   - `summarize_leaves(leaves, *, threshold=100, concurrency=5) -> list[dict]`
   - `flatten_document(file_path, *, source_label="", summary_threshold=100, summary_concurrency=5, heading_styles=("Heading",), id_prefix="s") -> tuple[list[dict], dict]`
   - `flatten_document_auto(file_path, *, source_label="", summary_threshold=100, summary_concurrency=5, id_prefix="s") -> tuple[list[dict], dict]`
   - `leaves_to_llm_view(leaves) -> str`
   - `leaves_to_skeleton_view(leaves) -> str`

5. 在文件末尾添加 `@tool` 封装（供 deepagents 调用）：
```python
@tool
async def parse_docx_leaf_flatten(
    file_path: str,
    source_label: str = "",
    summary_threshold: int = 400,
    id_prefix: str = "s",
) -> str:
    """Parse a .docx into flattened leaf nodes with LLM summarization.

    Auto-detects the best heading styles. Returns leaves ready for skeleton_guidance
    and leaf_match_fill tools.

    Args:
        file_path: Path to the .docx file.
        source_label: Label for this source (e.g. "输入1", "模板").
        summary_threshold: Max chars before LLM summarization is triggered.
        id_prefix: Prefix for leaf IDs. Use different prefixes per document
                   to avoid collisions (e.g. "t" for template, "a" for source A).
    """
    pass  # 调用 flatten_document_auto，返回 JSON
```

6. 叶子 ID 前缀约定（多文档时防止碰撞）：
   - 模板 docx → `id_prefix="t"`
   - 信息源 1 → `id_prefix="a"`
   - 信息源 2 → `id_prefix="b"`
   - ...

**验证标准**:
- 调用 `flatten_document_auto("path/to/doc.docx", source_label="test")` 返回 `(leaves, stats)`
- `leaves` 是 list，每个元素有 `id/path/heading/level/original_content/summary/is_leaf/parent_id/source`
- `stats` 包含 `total/with_content/summarized/by_level`

---

#### B2. `tools/docx_writer.py` — Docx 回写工具

**移植源**: `D:\ai\codeMind\tools\file_parser\docx_writer.py`

**功能说明**:
- 纯代码，无 LLM 调用
- 按叶子匹配结果从文档尾到头回写模板 docx（逆序防止索引偏移）
- 保持模板原有样式，追加来源追溯
- 4 种匹配等级处理：skip（跳过）/ high（原文直填）/ partial（原文+补充）/ missing（LLM 生成内容）

**适配要点**:

1. 顶部 import：
```python
from __future__ import annotations
import logging
import re
from collections import Counter
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from utils.paths import data_paths
```

2. **不需要任何修改** — 此文件是纯代码，无 CodeMind 框架依赖。直接复制原文件全部函数即可。

3. 核心函数：
   - `_cached_paragraphs(doc)`
   - `_find_heading_paragraph(para_list, heading_text, level, exclude=None)`
   - `_find_heading_by_text(para_list, heading_text, exclude=None)`
   - `_detect_body_style(heading_para, para_list)`
   - `_clear_body_after_heading(heading_para, para_list)`
   - `_resolve_style_id(doc, style_name)`
   - `_insert_paragraphs_after(element, texts, style_name, doc=None)`
   - `_insert_source_annotation(element, match_level, traces, annotation_style, doc=None)`
   - `_clean_markdown(text)`
   - `_resolve_content(match_result, source_map, template_leaf) -> tuple[str, list[str]]`
   - `backfill_docx(template_path, output_path, match_results, template_leaves, source_leaves, *, default_body_style="") -> dict`

4. 在文件末尾添加 `@tool` 封装：
```python
import json
from langchain_core.tools import tool
from pathlib import Path as _Path

@tool
def write_prd_docx(
    template_path: str,
    match_results_json: str,
    template_leaves_json: str,
    source_leaves_json: str = "[]",
    body_style: str = "",
    output_filename: str = "",
) -> str:
    """Write matched content into a PRD template .docx and produce the final document.

    Args:
        template_path: Path to the template .docx file.
        match_results_json: JSON array of match results from leaf_match_fill.
        template_leaves_json: JSON array of template leaf nodes.
        source_leaves_json: JSON array of source leaf nodes (for content lookup).
        body_style: Body paragraph style name in the template (e.g. "FIT_WDT_正文").
                    Auto-detected if empty.
        output_filename: Custom output filename stem. Auto-derived from template name if empty.

    Returns:
        JSON with output file path, filled count, skipped count.
    """
    match_results = json.loads(match_results_json)
    template_leaves = json.loads(template_leaves_json)
    source_leaves = json.loads(source_leaves_json)

    # 生成输出路径
    output_dir = data_paths.output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not output_filename:
        stem = Path(template_path).stem
        # 清理文件名中的 "输入N_" "模板" 等前缀/后缀
        stem = re.sub(r'^(输入|模板|输出)\d+[_\s]*', '', stem)
        stem = re.sub(r'[_\s]*(模板|template|_v\d+)$', '', stem, flags=re.IGNORECASE)
        stem = stem.strip("_") or f"prd_{ts}"
        output_filename = f"{stem}_生成版"
    output_path = str(output_dir / f"{output_filename}_{ts}.docx")

    result = backfill_docx(
        template_path=template_path,
        output_path=output_path,
        match_results=match_results,
        template_leaves=template_leaves,
        source_leaves=source_leaves,
        default_body_style=body_style,
    )

    return json.dumps({
        "file_path": output_path,
        "filled": result["filled"],
        "skipped": result.get("skipped", 0),
    }, ensure_ascii=False)
```

**验证标准**:
- 准备一个模板 .docx + match_results → 调用 `backfill_docx()` → 生成输出 .docx
- 输出文件中的章节内容与 match_results 的匹配等级对应
- TOC 段落被自动清除

---

### Step C：LLM 工具（2 个 .py）

#### C1. `tools/skeleton_guidance.py` — 骨架对比指导

**移植源**: `D:\ai\codeMind\skills\template\skeleton_guidance.py`（精简，去掉 BaseSkill/Capability 框架）

**功能说明**:
- 输入：模板骨架文本 + 信息源骨架列表
- LLM 分析三者的对应关系
- 输出：GlobalGuidance（terminology + scope_constraints + fill_strategy）

**完整实现**:

```python
"""
skeleton_guidance tool — LLM compares template skeleton with source skeletons
and generates global fill guidance (terminology / scope / strategy).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    p = _PROMPTS_DIR / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


# ── 输出模型 ──────────────────────────────────────────────────────────

class GlobalGuidance(BaseModel):
    terminology: str = Field(default="", description="术语统一说明")
    scope_constraints: str = Field(default="", description="范围约束")
    fill_strategy: str = Field(default="", description="填充策略")


# ── 格式化 ────────────────────────────────────────────────────────────

def _format_sources(source_skeletons: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for i, src in enumerate(source_skeletons, 1):
        label = src.get("label", f"信息源{i}")
        skeleton = src.get("skeleton", "")
        parts.append(f"### 信息源{i}: {label}\n\n{skeleton}")
    return "\n\n---\n\n".join(parts)


def guidance_to_prompt_fragment(guidance: dict[str, str]) -> str:
    """将 GlobalGuidance 转为可注入 LLM prompt 的文本片段。"""
    parts = ["## 全局指导（必须遵守）\n"]
    if guidance.get("terminology"):
        parts.append("### 术语统一要求\n" + guidance["terminology"] + "\n")
    if guidance.get("scope_constraints"):
        parts.append("### 文档范围约束\n" + guidance["scope_constraints"] + "\n")
    if guidance.get("fill_strategy"):
        parts.append("### 本章填充策略\n" + guidance["fill_strategy"] + "\n")
    return "\n".join(parts)


# ── @tool ─────────────────────────────────────────────────────────────

@tool
async def skeleton_guidance(
    template_label: str,
    template_skeleton: str,
    source_skeletons_json: str,
) -> str:
    """Generate global fill guidance by comparing template and source skeletons.

    Call this AFTER parse_docx_leaf_flatten on all documents, before leaf_match_fill.

    Args:
        template_label: Name label for the template (e.g. "PRD模板").
        template_skeleton: Skeleton text from leaves_to_skeleton_view(template_leaves).
        source_skeletons_json: JSON array of {label, skeleton} objects.
            e.g. [{"label":"输入1-用户需求","skeleton":"- 概述\n  - 背景..."}]

    Returns:
        JSON with guidance dict: {terminology, scope_constraints, fill_strategy}
    """
    source_skeletons = json.loads(source_skeletons_json)

    if not template_skeleton or not source_skeletons:
        return json.dumps({
            "error": "template_skeleton 和 source_skeletons 不能为空"
        }, ensure_ascii=False)

    from llm.base import get_llm

    system_text = _load_prompt("skeleton_guidance.j2")
    human_text = _load_prompt("skeleton_guidance_human.j2")

    sources_text = _format_sources(source_skeletons)
    human = human_text.format(
        template_label=template_label,
        skeleton=template_skeleton,
        sources_text=sources_text,
    )

    llm = get_llm("default", temperature=0.0)
    response = await llm.ainvoke([
        SystemMessage(content=system_text),
        HumanMessage(content=human),
    ])

    raw = response.content if hasattr(response, "content") else str(response)
    # 提取 JSON
    json_start = raw.find("{")
    json_end = raw.rfind("}") + 1
    if json_start >= 0 and json_end > json_start:
        raw = raw[json_start:json_end]

    try:
        guidance = json.loads(raw)
    except json.JSONDecodeError:
        return json.dumps({"error": f"LLM 未返回有效 JSON: {raw[:200]}"}, ensure_ascii=False)

    logger.info("[skeleton_guidance] 生成完成: 术语=%d字 范围=%d字 策略=%d字",
                len(guidance.get("terminology", "")),
                len(guidance.get("scope_constraints", "")),
                len(guidance.get("fill_strategy", "")))

    return json.dumps(guidance, ensure_ascii=False, indent=2)
```

---

#### C2. `tools/leaf_match_fill.py` — 叶子匹配填充

**移植源**: `D:\ai\codeMind\skills\template\leaf_match_fill.py`（精简，去掉 BaseSkill/Capability 框架）

**功能说明**:
- 输入：模板叶子列表 + 信息源叶子列表 + GlobalGuidance
- 按 H1 分组，每组并发调 LLM
- 每个模板叶子输出：match_level（skip/high/partial/missing）+ confidence + source_refs + supplement/generated_content

**完整实现要点**:

1. 核心逻辑平移自 CodeMind，去掉 `BaseSkill`/`BaseCapability`/`CapabilityInput`/`CapabilityResult` 依赖。

2. 保留函数：
   - `_confidence_to_match_level(confidence, high_threshold, missing_threshold) -> str`
   - `_group_template_leaves(template_leaves) -> list[tuple[str, list[dict]]]`
   - `_format_template_leaves(leaves) -> str`
   - `_format_source_leaves(leaves, content_threshold=300) -> str`

3. `@tool` 函数签名：
```python
@tool
async def leaf_match_fill(
    template_leaves_json: str,
    source_leaves_json: str,
    guidance_json: str = "{}",
    source_content_threshold: int = 300,
    high_confidence_threshold: float = 0.8,
    missing_confidence_threshold: float = 0.3,
) -> str:
    """Match source leaves to template leaves with four confidence levels.

    Call this AFTER skeleton_guidance.

    Args:
        template_leaves_json: JSON array of template leaf nodes (from parse_docx_leaf_flatten).
        source_leaves_json: JSON array of ALL source leaves combined.
        guidance_json: JSON guidance dict from skeleton_guidance.
        source_content_threshold: Truncation threshold for source content display.
        high_confidence_threshold: Confidence >= this → high match.
        missing_confidence_threshold: Confidence <= this → missing (LLM generates content).

    Returns:
        JSON with {stats: {total, high, partial, missing}, match_results: [...]}
    """
```

4. 并发控制：
   - 每组（按 H1 分组）一个 LLM 调用
   - 使用 `asyncio.Semaphore(3)` 限制并发（常量 `_MATCH_CONCURRENCY = 3`）
   - 使用 `asyncio.gather` 并发执行各组

5. LLM 结构化输出：
   - 使用 Pydantic `_GroupMatchOutput` 模型
   - 调用 `llm.with_structured_output(_GroupMatchOutput).ainvoke(messages)`

6. 降级策略：
   - 单组 LLM 失败 → 该组所有叶子标记为 `missing`，置信度 0.0
   - 整批 gather 异常 → 记录日志，继续处理成功的组

**验证标准**:
- 给定 template_leaves（~20 条）+ source_leaves（~50 条）→ 返回 match_results 覆盖每个模板叶子
- match_results 中的 match_level 只有 skip/high/partial/missing 四种
- high 的叶子有 source_refs 指向信息源叶子 ID
- missing 的叶子有 generated_content

---

### Step D：Skill 定义

#### D1. `skills/req-analysis-to-prd/SKILL.md`

```markdown
---
name: req-analysis-to-prd
description: 将需求文档填充到 PRD 模板，生成产品需求规格说明书。上传 1~N 个需求文档 + 1 个 PRD 模板 .docx 时使用。
allowed-tools: parse_docx_leaf_flatten, skeleton_guidance, leaf_match_fill, write_prd_docx
---

## 适用场景

- 用户上传需求文档 + PRD 模板，要求生成完整 PRD
- 用户要求将需求分析报告填充到产品需求规格说明书

## 不适用

- 无 Word 模板的 PRD 生成
- 纯文本需求编写
- 对已生成 PRD 做增量修改

## 工作步骤

### 1. 解析所有文档

对每个上传的 .docx 文件调用 `parse_docx_leaf_flatten`：

- **模板文档**（文件名含"模板"/"template"）：传入 `source_label="模板"`, `id_prefix="t"`
- **信息源文档**（其余文件）：每个传入不同 `source_label`（如"输入1"、"输入2"）和不同 `id_prefix`（如"a"、"b"、"c"）

工具返回每个文档的叶子列表和统计信息，汇总后继续。

### 2. 生成骨架指导

从步骤 1 的结果中提取：
- `template_skeleton`：对模板叶子调用 `leaves_to_skeleton_view`（在 Python 代码中完成，不是单独工具）
- `source_skeletons`：对每个信息源叶子调用 `leaves_to_skeleton_view`，组装为 `[{label, skeleton}, ...]`

然后调用 `skeleton_guidance` 获取：
- `terminology`：术语统一要求
- `scope_constraints`：文档范围约束
- `fill_strategy`：各章节填充策略

### 3. 叶子匹配填充

调用 `leaf_match_fill`，传入：
- `template_leaves_json`：模板叶子 JSON
- `source_leaves_json`：所有信息源叶子合并后的 JSON
- `guidance_json`：步骤 2 的指导 JSON

返回每个模板叶子的匹配结果：
- `high`（置信度 ≥ 0.8）：信息源原文直填
- `partial`（0.3 < 置信度 < 0.8）：原文 + LLM 整理/补充
- `missing`（置信度 ≤ 0.3）：LLM 生成内容
- `skip`（章标题）：跳过不填

### 4. 回写生成 PRD

调用 `write_prd_docx`，传入：
- `template_path`：模板 .docx 路径
- `match_results_json`：步骤 3 的匹配结果
- `template_leaves_json`：模板叶子
- `source_leaves_json`：信息源叶子
- `body_style`：模板正文样式名（如 "FIT_WDT_正文"，不传则自动检测）

向用户报告输出文件路径和填充统计。
```

---

### Step E：子 Agent 配置

#### E1. `agents/subagents/prd_fill.py`

参照 `agents/subagents/req_parse.py` 的结构：

```python
"""
prd-fill SubAgent — PRD 模板填充：多信息源 → 模板匹配 → .docx 生成。

策略: 绑定 parse_docx_leaf_flatten / skeleton_guidance / leaf_match_fill / write_prd_docx
     遵循 skills/req-analysis-to-prd/SKILL.md 的定义顺序执行。
"""

from __future__ import annotations

import sys
from deepagents.middleware.subagents import SubAgent
from utils.paths import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))


def build_prd_fill_subagent(middleware=None) -> SubAgent:
    from tools.leaf_flattener import parse_docx_leaf_flatten
    from tools.skeleton_guidance import skeleton_guidance
    from tools.leaf_match_fill import leaf_match_fill
    from tools.docx_writer import write_prd_docx

    skills_dir = "/skills/req-analysis-to-prd/"

    return SubAgent(
        name="prd-fill",
        description=(
            "将需求文档填充到 PRD 模板，生成产品需求规格说明书。"
            "处理：批量解析 .docx 文件、对比骨架生成填充策略、逐章节匹配填充、回写生成最终 PRD。"
            "当用户要求生成 PRD、填充模板、或将需求文档转为产品需求规格说明书时使用此子 Agent。"
        ),
        system_prompt=(
            "你是 PRD 模板填充专家，严格按照 req-analysis-to-prd 技能执行：\n"
            "1. 对每个上传的 .docx 调用 parse_docx_leaf_flatten（模板用 id_prefix='t'，信息源用不同前缀）\n"
            "2. 从叶子列表中提取骨架视图，调用 skeleton_guidance 生成全局填充指导\n"
            "3. 调用 leaf_match_fill 将信息源叶子匹配到模板叶子\n"
            "4. 调用 write_prd_docx 回写生成最终 PRD 文档\n"
            "完成后向主 Agent 报告输出文件路径和填充统计（high/partial/missing/skip 各多少）。\n"
            "所有输出使用中文。"
        ),
        tools=[parse_docx_leaf_flatten, skeleton_guidance, leaf_match_fill, write_prd_docx],
        skills=[skills_dir],
        middleware=middleware or [],
    )
```

---

### Step F：集成注册

#### F1. 修改 `tools/__init__.py`

在现有导出后追加：

```python
from .leaf_flattener import parse_docx_leaf_flatten
from .skeleton_guidance import skeleton_guidance
from .leaf_match_fill import leaf_match_fill
from .docx_writer import write_prd_docx
```

#### F2. 修改 `agents/deep_agent.py`

在 `create_deepmind_agent` 函数中，于 `req_parse_subagent` 定义之后追加：

```python
# ── 子 Agent ───────────────────────────────────────────────────────
from agents.subagents.req_parse import build_req_parse_subagent
from agents.subagents.prd_fill import build_prd_fill_subagent    # ← 新增

req_parse_subagent = build_req_parse_subagent(middleware=config.middleware)
prd_fill_subagent = build_prd_fill_subagent(middleware=config.middleware)  # ← 新增
```

然后将 `subagents=[req_parse_subagent]` 改为：

```python
subagents=[req_parse_subagent, prd_fill_subagent],
```

以及最后的 print 日志追加：

```python
print(f"  子 Agent 'prd-fill': [parse_docx_leaf_flatten, skeleton_guidance, leaf_match_fill, write_prd_docx]")
```

#### F3. 修改 `deepMind.toml`

在 `[ui.display.tools]` 段追加：

```toml
"parse_docx_leaf_flatten" = "📑 批量解析文档叶子"
"skeleton_guidance"       = "🧭 骨架对比指导"
"leaf_match_fill"         = "🔗 叶子匹配填充"
"write_prd_docx"          = "📝 生成 PRD 文档"
```

在 `[ui.display.nodes]` 段追加：

```toml
"prd-fill" = "📝 PRD 模板填充"
```

---

## 依赖关系图（关键）

```
extract_outline (tools/word_parser.py — 已有)
  ↑
leaf_flattener (tools/leaf_flattener.py — 新建 Step B1)
  ├─ 输出: leaves[] + stats
  ├─ leaves_to_skeleton_view() → skeleton 文本
  │     ↑
  ├─ skeleton_guidance (tools/skeleton_guidance.py — 新建 Step C1)
  │     └─ 输出: guidance{terminology, scope_constraints, fill_strategy}
  ├─ leaf_match_fill (tools/leaf_match_fill.py — 新建 Step C2)
  │     └─ 输入: template_leaves + source_leaves + guidance
  │     └─ 输出: match_results[] (每个叶子: match_level + confidence + source_refs)
  └─ docx_writer (tools/docx_writer.py — 新建 Step B2)
        └─ 输入: template_path + match_results + template_leaves + source_leaves
        └─ 输出: 最终 PRD .docx 文件路径
```

## 测试方法

### 单元测试

```python
# tests/test_prd_fill.py
import pytest
from tools.leaf_flattener import flatten_outline, leaves_to_skeleton_view
from tools.word_parser import extract_outline

def test_flatten_outline():
    outline = extract_outline("docs/sample.docx")
    leaves = flatten_outline(outline, source_label="test", id_prefix="s")
    assert len(leaves) > 0
    assert all("id" in l and "path" in l for l in leaves)

def test_skeleton_view():
    leaves = [...]  # 构造测试数据
    skeleton = leaves_to_skeleton_view(leaves)
    assert isinstance(skeleton, str)
    assert "- " in skeleton
```

### 集成测试

准备 3 个测试 .docx 文件放在 `docs/req/` 目录下（可从 CodeMind 共享目录获取）：
- `输入1-用户需求.docx`
- `输入2-需求分析报告.docx`
- `输入3_产品需求规格说明书新模板.docx`

然后通过 Chainlit UI 上传这三个文件并输入：
> "根据用户需求文档和需求分析报告，将信息填充到产品需求规格说明书模板中，生成完整的 PRD 文档"

预期 Agent 行为：
1. 主 Agent 识别意图 → 委托给 `prd-fill` 子 Agent
2. 子 Agent 依次调用 4 个工具
3. 最终输出 PRD .docx 文件路径

---

## 注意事项

1. **叶子 ID 前缀隔离**：多文档时必须用不同 `id_prefix`（t/a/b/c），否则 LLM 在匹配阶段无法区分 source_refs 来源。
2. **正文样式**：`write_prd_docx` 的 `body_style` 参数需与模板实际样式名匹配（如 `FIT_WDT_正文`），否则正文格式丢失。不传则自动检测。
3. **LLM 超时**：leaf_match_fill 单组 LLM 调用可能耗时较长（大量叶子），建议 LLM timeout ≥ 120s。
4. **并发控制**：leaf_match_fill 使用 `asyncio.Semaphore(3)`，超大模板（>100 叶子）可适当降低并发。
5. **错误重试**：四个工具目前没有内置重试。如果 LLM 调用失败，对应步骤返回 error 字段，由 Agent 自行判断是否重试。
6. **编码规范**：所有文件路径必须通过 `utils/paths.DataPaths` 获取，禁止硬编码。
