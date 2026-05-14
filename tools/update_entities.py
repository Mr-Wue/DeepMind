"""
Entity update tools — partial update existing entities + schema introspection.

``update_entities``
    通过 ORM 部分更新已有实体。只更新指定字段，未传字段保持原值。
    写入前通过 ``langgraph.types.interrupt()`` 暂停等待用户确认。

``get_db_schema``
    返回完整表结构，供 Agent 在构造更新数据前了解字段名。

Usage::

    from tools.update_entities import update_entities, get_db_schema

    agent = create_deep_agent(
        model=model,
        tools=[..., update_entities, get_db_schema],
    )
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool
from langgraph.types import interrupt

from models.reqmgmt import (
    REQMGMT_ENTITY_CLASSES,
    build_schema_text,
    get_session,
)

# ── 从 ORM 元数据自动生成类型注册表 ──────────────────────────────────────────

_TYPE_MAP: dict[str, type] = {}
_TYPE_CN: dict[str, str] = {}


def _init_registry() -> None:
    """从 REQMGMT_ENTITY_CLASSES 的 __tablename__ 和 TABLE 生成映射。

    TABLE 格式如 ``"IR (用户需求项)"`` — 括号前是缩写/英文名，括号内是中文名。
    """
    for cls in REQMGMT_ENTITY_CLASSES:
        tn = cls.__tablename__
        table_label = getattr(cls, "TABLE", "")

        # 解析 TABLE："IR (用户需求项)" → abbr="IR", cn="用户需求项"
        if "(" in table_label and table_label.endswith(")"):
            abbr, cn = table_label.split("(", 1)
            abbr = abbr.strip()
            cn = cn.rstrip(")")
        else:
            abbr = table_label
            cn = table_label

        # 所有映射到同一个 cls 的 key
        keys = [
            tn,                      # "requirement_items"
            tn.rstrip("s"),          # "requirement_item" (去复数)
            abbr,                    # "IR"
            abbr.lower(),            # "ir"
        ]
        for k in keys:
            k = k.strip()
            if k and k not in _TYPE_MAP:
                _TYPE_MAP[k] = cls
                _TYPE_CN[k] = cn


_init_registry()


# ── 核心逻辑 ──────────────────────────────────────────────────────────────────


def _update_batch(entities: list[dict[str, Any]]) -> dict[str, Any]:
    """部分更新实体：逐条 fetch → setattr → commit。

    不会新增实体 — id 不存在时记为 not_found。
    """
    session = get_session()
    updated = 0
    not_found = 0
    errors: list[str] = []

    try:
        for ent in entities:
            etype = ent.pop("_type", "")
            entity_id = ent.pop("id", "")
            cls = _TYPE_MAP.get(etype)

            if cls is None:
                errors.append(f"未知类型 '{etype}'")
                continue

            if not entity_id:
                errors.append(f"缺少 id（类型={etype}）")
                continue

            obj = session.get(cls, entity_id)
            if obj is None:
                not_found += 1
                errors.append(f"{cls.__tablename__} id={entity_id}: 记录不存在")
                continue

            # 只更新指定的字段
            for key, value in ent.items():
                if hasattr(cls, key):
                    setattr(obj, key, value)
                else:
                    errors.append(
                        f"{cls.__tablename__} id={entity_id}: 未知字段 '{key}'"
                    )

            updated += 1

        session.commit()

        result: dict[str, Any] = {
            "success": len(errors) == 0,
            "total": len(entities),
            "updated": updated,
            "not_found": not_found,
        }
        if errors:
            result["errors"] = errors
        return result
    except Exception as e:
        session.rollback()
        return {"success": False, "total": len(entities), "updated": 0, "error": str(e)}
    finally:
        session.close()


# ── 工具定义 ──────────────────────────────────────────────────────────────────


@tool
def update_entities(entities_json: str) -> str:
    """修改数据库中已有的实体，支持批量、部分更新。

    只更新你指定的字段，未传字段保持原值不变。不会新增实体
    （id 必须已存在，否则跳过并报告 not_found）。

    典型使用流程：
    1. 先用 query_reqmgmt 查询定位要修改的记录，获取其 id
    2. 如有必要，用 get_db_schema 确认可更新的字段名
    3. 构造 JSON 数组，调用本工具

    Args:
        entities_json: JSON 数组字符串，每个元素必须包含：
            - _type: 实体类型。支持表名（如 "requirement_items"）、
              缩写（如 "IR", "RM", "PR", "TC", "TS"）、单数形式（如 "product"）。
              不确定时可以先调用 get_db_schema 查看所有表的准确名称。
            - id: 实体唯一标识（必填，用于定位记录）
            - 其他字段：只传你要修改的字段
              例如修改 IR 的描述和优先级：
              {"_type": "IR", "id": "IR-001", "description": "新描述", "priority": "高"}

    Returns:
        JSON 字符串，包含 success、updated、not_found 等统计信息。
        写入前会弹出确认卡片，取消则返回 cancelled=true。
    """
    try:
        entities: list[dict[str, Any]] = json.loads(entities_json)
    except json.JSONDecodeError as e:
        return json.dumps({"success": False, "error": f"JSON 解析失败: {e}"}, ensure_ascii=False)

    if not isinstance(entities, list):
        return json.dumps({"success": False, "error": "entities_json 必须是 JSON 数组"}, ensure_ascii=False)

    if not entities:
        return json.dumps({"success": True, "total": 0, "updated": 0, "message": "空列表，无需操作"})

    # ── 统计摘要 ──
    type_counts: dict[str, int] = {}
    for e in entities:
        t = e.get("_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    summary_parts = [f"{_TYPE_CN.get(t, t)} {c} 条" for t, c in type_counts.items()]
    summary = "、".join(summary_parts)
    print(f"[update_entities] 更新 {len(entities)} 个实体: {type_counts}")

    # ── 用户确认 ──
    response = interrupt({
        "action": "update_entities",
        "total": len(entities),
        "summary": summary,
        "by_type": type_counts,
        "message": f"即将修改数据库: {summary}。是否确认？",
    })

    if isinstance(response, dict) and response.get("decision") == "reject":
        return json.dumps({
            "success": False,
            "message": "用户取消了修改操作",
            "cancelled": True,
            "total": len(entities),
            "by_type": type_counts,
        }, ensure_ascii=False, indent=2)

    # ── 执行更新 ──
    result = _update_batch(entities)
    print(f"[update_entities] 完成: updated={result.get('updated', 0)}, "
          f"success={result.get('success', False)}")
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def get_db_schema() -> str:
    """获取需求管理数据库的完整表结构。

    返回所有表的名称、字段列表和外键关系。当你需要修改数据但不确
    定某个表有哪些字段、或字段的准确名称时，先调用此工具了解结构。

    Returns:
        格式化的表结构文本，每行一张表：
        表名(字段1, 字段2→外键引用表, ...)  -- 说明
    """
    return build_schema_text()
