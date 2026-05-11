"""
Entity storage tool — batch upsert entities via SQLAlchemy ORM.

Replaces entity_store.py. Uses models/reqmgmt.py ORM for proper FK handling
and full table support (11 tables vs the old 3).
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

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

    result = _store_batch(entities)
    return json.dumps(result, ensure_ascii=False, indent=2)
