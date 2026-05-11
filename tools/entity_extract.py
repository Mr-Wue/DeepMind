"""
Entity extraction tool — LLM classifies document sections into entity types.

Adapted from CodeMind skills/dataskill/entity_extract.py and
skills/file_parse/document_parse.py + entity_assembly.py.

Uses LLM structured output to classify each section from parse_docx_outline
into the correct entity type (_type), then assembles entities with IDs and FK
relationships deterministically.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_core.tools import tool

from models.reqmgmt import REQMGMT_ENTITY_CLASSES, build_schema_text

logger = logging.getLogger(__name__)

# ── helpers ────────────────────────────────────────────────────────────────


def _extract_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if hasattr(response, "content"):
        return str(response.content)
    return str(response)


def _parse_json_response(content: str) -> Any:
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines)
    return json.loads(content)


# ── FK mapping from ORM metadata ───────────────────────────────────────────


def _build_fk_map() -> dict[str, dict[str, str]]:
    """Build FK map from ORM metadata: {child_table: {fk_col: parent_table}}."""
    fk_map: dict[str, dict[str, str]] = {}
    for cls in REQMGMT_ENTITY_CLASSES:
        tn = getattr(cls, "__tablename__", None)
        if not tn:
            continue
        if not getattr(cls, "LLM_NODE_NOTE", ""):
            continue
        table = getattr(cls, "__table__", None)
        if table is None:
            continue
        for col in table.columns:
            if col.foreign_keys:
                for fk in col.foreign_keys:
                    parent_tn = fk.column.table.name
                    fk_map.setdefault(tn, {})[col.name] = parent_tn
    return fk_map


def _find_root_table(fk_map: dict[str, dict[str, str]]) -> str | None:
    child_tables = set(fk_map.keys())
    parent_tables: set[str] = set()
    for fks in fk_map.values():
        parent_tables.update(fks.values())
    roots = parent_tables - child_tables
    return roots.pop() if len(roots) == 1 else None


def _known_tables(fk_map: dict[str, dict[str, str]]) -> set[str]:
    tables = set(fk_map.keys())
    for fks in fk_map.values():
        tables.update(fks.values())
    return tables


def _entity_type_descriptions() -> dict[str, str]:
    """Extract LLM_NODE_NOTE from entity classes, keyed by __tablename__."""
    result: dict[str, str] = {}
    for cls in REQMGMT_ENTITY_CLASSES:
        tn = getattr(cls, "__tablename__", None)
        note = getattr(cls, "LLM_NODE_NOTE", "")
        if tn and note:
            result[tn] = note
    return result


# ── LLM classification ─────────────────────────────────────────────────────


_CLASSIFY_SYSTEM = """\
You are a document entity classifier. Given a list of sections from a document,
classify each section into the appropriate entity type.

## Entity types

{entity_descriptions}

## Rules

1. For each section, choose the correct _type from the entity types above
2. The document title + overview → products (one product for the whole document)
3. Top-level sections (H2, parent_id=null) → requirement_models
4. Nested sections (H3+, has parent_id) → requirement_items
5. Fill in name, title, description from the section's heading and paragraphs
6. For requirement_items: priority defaults to "中", status defaults to "未实现"
7. Return a JSON array of objects, one per section, each with: section_id, _type, name, title, description

Only classify sections — do NOT generate IDs or foreign keys. Those are added downstream."""


def _build_classify_prompt(sections: list[dict[str, Any]]) -> str:
    sections_json = json.dumps(sections, ensure_ascii=False, indent=2)
    entity_descs = "\n".join(
        f"- {tn}: {desc}" for tn, desc in _entity_type_descriptions().items()
    )
    system = _CLASSIFY_SYSTEM.format(entity_descriptions=entity_descs)
    return system, sections_json


async def _classify_group(
    sections: list[dict[str, Any]],
    heading: str,
    index: int,
    total: int,
    sem: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    """Call LLM to classify one group of sections."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from llm.base import get_llm

    system_text, sections_json = _build_classify_prompt(sections)

    async with sem:
        try:
            llm = get_llm("default", temperature=0.0)
            response = await llm.ainvoke([
                SystemMessage(content=system_text),
                HumanMessage(
                    content=f"Classify the {len(sections)} sections in group {index + 1}/{total} "
                    f"\"{heading}\" and return a JSON array."
                ),
            ])
            content = _extract_text(response)
            parsed = _parse_json_response(content)
            if isinstance(parsed, list):
                return parsed
            return []
        except Exception as exc:
            logger.warning("[EntityExtract] Group %d LLM failed: %s", index, exc)
            return []


# ── Entity assembly (pure code) ────────────────────────────────────────────


def _assemble_entities(
    structure: dict[str, Any],
    groups_data: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    fk_map: dict[str, dict[str, str]],
    root_table: str | None,
    paragraphs_lookup: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Assemble classified sections into entities with IDs and FK relationships."""
    from tools.word_parser import _make_slug

    entities: list[dict[str, Any]] = []
    warnings: list[str] = []
    counters: dict[str, int] = {}

    def _next_id(table_name: str) -> str:
        counters[table_name] = counters.get(table_name, 0) + 1
        prefix = "".join(w[0] for w in table_name.split("_"))
        return f"{prefix}-{counters[table_name]:03d}"

    # Root entity (document → product)
    root_entity: dict[str, Any] | None = None
    if root_table:
        root_entity = {
            "_type": root_table,
            "id": _make_slug(structure.get("title", "")),
            "name": structure.get("title", ""),
            "description": "；".join(structure.get("overview", [])),
        }
        entities.append(root_entity)

    known = _known_tables(fk_map)

    for group_sections, llm_output in groups_data:
        sec_llm_map: dict[str, dict[str, Any]] = {}
        for item in llm_output:
            sid = item.get("section_id", "")
            if sid:
                sec_llm_map[sid] = item

        sec_eid_map: dict[str, str] = {}

        for sec in group_sections:
            sid = sec["id"]
            llm_entity = sec_llm_map.get(sid)
            if llm_entity is None:
                continue

            table = llm_entity.get("_type", "")
            if not table or table not in known:
                warnings.append(f"Section {sid}: _type={table} not in schema")
                continue

            entity: dict[str, Any] = {"_type": table, "id": _next_id(table)}
            for k, v in llm_entity.items():
                if k not in ("_type", "section_id"):
                    entity[k] = v

            # Restore original paragraphs from lookup
            if paragraphs_lookup and sid in paragraphs_lookup:
                sec_paragraphs = sec.get("paragraphs", [])
                if not sec.get("heading", ""):
                    entity["description"] = "\n".join(paragraphs_lookup[sid])
                elif any("已剥离" in p for p in sec_paragraphs):
                    entity["description"] = "\n".join(paragraphs_lookup[sid])

            # FK: parent_id=null → FK to root entity
            parent_id = sec.get("parent_id")
            if parent_id is None:
                if root_entity:
                    for fk_col, ref_table in fk_map.get(table, {}).items():
                        if ref_table == root_entity["_type"]:
                            entity[fk_col] = root_entity["id"]
                            break
            else:
                parent_eid = sec_eid_map.get(parent_id)
                if parent_eid and parent_id in sec_llm_map:
                    parent_type = sec_llm_map[parent_id]["_type"]
                    for fk_col, ref_table in fk_map.get(table, {}).items():
                        if ref_table == parent_type:
                            entity[fk_col] = parent_eid
                            break

            entities.append(entity)
            sec_eid_map[sid] = entity["id"]

    return entities, warnings


def _validate_entities(entities: list[dict[str, Any]], fk_map: dict) -> list[str]:
    """Validate: _type legality, FK references, required fields."""
    warnings: list[str] = []
    all_ids: set[str] = {e["id"] for e in entities if e.get("id")}
    known = _known_tables(fk_map)

    for entity in entities:
        eid = entity.get("id", "?")
        etype = entity.get("_type", "")

        if etype and etype not in known:
            warnings.append(f"{eid}: _type={etype} not in schema")

        for fk_col in fk_map.get(etype, {}):
            fk_val = entity.get(fk_col, "")
            if fk_val and fk_val not in all_ids:
                warnings.append(f"{eid}: {fk_col}={fk_val} references non-existent entity")

        has_name = bool(entity.get("name", "") or entity.get("title", ""))
        if not has_name:
            warnings.append(f"{eid}: missing name/title")

    return warnings


# ═══════════════════════════════════════════════════════════════════════════════
# deepagents @tool
# ═══════════════════════════════════════════════════════════════════════════════


@tool
async def extract_entities(llm_structure_json: str) -> str:
    """Classify document sections into domain entities using LLM.

    Takes the 'llm_structure' output from parse_docx_outline and returns
    structured entities with IDs and FK relationships ready for storage.

    Args:
        llm_structure_json: The 'llm_structure' field from parse_docx_outline output.
                            Must contain: title, overview, groups, paragraphs_lookup.
    """
    try:
        structure = json.loads(llm_structure_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON input: {e}"}, ensure_ascii=False)

    fk_map = _build_fk_map()
    # Documents always represent a product at root level
    root_table = "products"
    groups = structure.get("groups", [])

    if not groups:
        return json.dumps({"error": "No groups found in llm_structure"}, ensure_ascii=False)

    sem = asyncio.Semaphore(3)  # Limit concurrent LLM calls

    tasks = [
        _classify_group(g["sections"], g["heading"], i, len(groups), sem)
        for i, g in enumerate(groups)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    groups_data: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning("[EntityExtract] Group %d failed: %s", i, result)
            groups_data.append((groups[i]["sections"], []))
        else:
            groups_data.append((groups[i]["sections"], result))

    entities, warnings = _assemble_entities(
        structure, groups_data, fk_map, root_table,
        paragraphs_lookup=structure.get("paragraphs_lookup"),
    )
    val_warnings = _validate_entities(entities, fk_map)
    warnings.extend(val_warnings)

    type_counts: dict[str, int] = {}
    for e in entities:
        t = e.get("_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    return json.dumps({
        "entities": entities,
        "stats": {
            "total": len(entities),
            "by_type": type_counts,
            "groups": len(groups),
        },
        "warnings": warnings,
    }, ensure_ascii=False, indent=2)
