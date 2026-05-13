"""
Entity storage tool — batch upsert entities via SQLAlchemy ORM.

Replaces entity_store.py. Uses models/reqmgmt.py ORM for proper FK handling
and full table support (11 tables vs the old 3).

写入前通过 ``langgraph.types.interrupt()`` 暂停等待用户确认。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool
from langgraph.types import interrupt

from models.reqmgmt import (
    Product,
    RequirementModel,
    RequirementItem,
    ProductRequirement,
    get_session,
)

# _type → ORM class mapping
_TYPE_MAP: dict[str, type] = {
    "products": Product,
    "product": Product,
    "requirement_models": RequirementModel,
    "requirement_model": RequirementModel,
    "RM": RequirementModel,
    "requirement_items": RequirementItem,
    "requirement_item": RequirementItem,
    "IR": RequirementItem,
    "product_requirements": ProductRequirement,
    "product_requirement": ProductRequirement,
    "PR": ProductRequirement,
}


def _store_batch(entities: list[dict[str, Any]]) -> dict[str, Any]:
    """Store entities to database via ORM. Returns stats dict."""
    session = get_session()
    inserted = 0
    errors: list[str] = []

    # Separate by ORM class
    buckets: dict[type, list[dict[str, Any]]] = {}
    unknown: list[tuple[str, dict]] = []

    for ent in entities:
        etype = ent.pop("_type", "")
        cls = _TYPE_MAP.get(etype)
        if cls is not None:
            buckets.setdefault(cls, []).append(ent)
        else:
            unknown.append((etype, ent))

    try:
        for cls, batch in buckets.items():
            for fields in batch:
                try:
                    obj = cls(**{k: v for k, v in fields.items() if hasattr(cls, k)})
                    session.merge(obj)
                    inserted += 1
                except Exception as e:
                    errors.append(f"{cls.__tablename__} {fields.get('id', '?')}: {e}")

        session.commit()

        by_type: dict[str, int] = {}
        for cls, batch in buckets.items():
            by_type[cls.__tablename__] = len(batch)

        result = {
            "success": len(errors) == 0,
            "total": len(entities),
            "inserted": inserted,
            "by_type": by_type,
        }
        if unknown:
            result["unknown_types"] = [t for t, _ in unknown]
        if errors:
            result["errors"] = errors
        return result
    except Exception as e:
        session.rollback()
        return {"success": False, "total": len(entities), "inserted": 0, "error": str(e)}
    finally:
        session.close()


@tool
def store_entities(entities_json: str) -> str:
    """Store extracted entities to the requirements management database.

    Uses INSERT OR REPLACE (via SQLAlchemy merge) — re-storing the same
    entities is idempotent.

    Args:
        entities_json: JSON array of entity objects. Each entity must have:
            - _type: One of "products", "requirement_models", "requirement_items",
                    "product_requirements"
            - id: Unique identifier
            - Other type-specific fields (name, title, description, etc.)

    Returns:
        JSON with success status and counts by entity type.
    """
    try:
        entities: list[dict[str, Any]] = json.loads(entities_json)
    except json.JSONDecodeError as e:
        return json.dumps({"success": False, "error": f"Invalid JSON: {e}"}, ensure_ascii=False)

    if not isinstance(entities, list):
        return json.dumps({"success": False, "error": "entities_json must be a JSON array"}, ensure_ascii=False)

    # ── 统计摘要 ─────────────────────────────────────────────────────
    type_counts: dict[str, int] = {}
    type_names: dict[str, str] = {
        "products": "产品", "product": "产品",
        "requirement_models": "需求模型", "requirement_model": "需求模型", "RM": "需求模型",
        "requirement_items": "需求项", "requirement_item": "需求项", "IR": "需求项",
        "product_requirements": "产品需求", "product_requirement": "产品需求", "PR": "产品需求",
    }
    for e in entities:
        t = e.get("_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    summary_parts = [f"{type_names.get(t, t)} {c} 个" for t, c in type_counts.items()]
    summary = "、".join(summary_parts)
    print(f"[store_entities] 写入 {len(entities)} 个实体: {type_counts}")

    # ── 用户确认 ─────────────────────────────────────────────────────
    response = interrupt({
        "action": "store_entities",
        "total": len(entities),
        "summary": summary,
        "by_type": type_counts,
        "message": f"即将写入数据库: {summary}。是否确认？",
    })

    if isinstance(response, dict) and response.get("decision") == "reject":
        return json.dumps({
            "success": False,
            "message": "用户取消了入库操作",
            "cancelled": True,
            "total": len(entities),
            "by_type": type_counts,
        }, ensure_ascii=False, indent=2)

    # ── 执行入库 ─────────────────────────────────────────────────────
    result = _store_batch(entities)
    print(f"[store_entities] 入库完成: inserted={result.get('inserted', 0)}, "
          f"success={result.get('success', False)}")
    return json.dumps(result, ensure_ascii=False, indent=2)
