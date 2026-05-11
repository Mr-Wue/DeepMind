"""
Word document parser — extract structured outline from .docx files.

Pure code, no LLM. Migrated from CodeMind tools/file_parser/word_parser.py.
Stripped of BaseFileParser / ParsedContent / register_parser dependencies.

Provides:
  - extract_outline: .docx → structured heading tree with paragraphs
  - outline_to_markdown: outline → markdown text
  - build_structure_for_llm: outline → flattened sections for LLM classification
  - parse_docx_outline (tool): @tool wrapper for deepagents
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _is_heading_style(style: str, heading_styles: tuple[str, ...]) -> bool:
    return any(style.startswith(h) for h in heading_styles)


def _heading_level(style: str) -> int:
    try:
        return int(style.split()[-1])
    except (ValueError, IndexError):
        return 2


def _make_slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9一-鿿]", "-", text)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        return "prod-doc"
    return f"prod-{slug[:40].lower()}" if not slug.startswith("prod-") else slug[:50].lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Core: .docx → structured outline tree
# ═══════════════════════════════════════════════════════════════════════════════


def extract_outline(
    file_path: str,
    *,
    heading_styles: tuple[str, ...] = ("Heading",),
) -> dict[str, Any]:
    """Extract structured heading tree from a Word document.

    Args:
        file_path: Path to .docx file.
        heading_styles: Style name prefixes to treat as headings.
                        Pass ("Heading", "FAI") for custom heading styles.

    Returns:
        {
            "title": "Document title",
            "sections": [
                {"level": 2, "heading": "Section A", "paragraphs": ["..."], "children": [
                    {"level": 3, "heading": "Subsection", "paragraphs": ["..."], "children": []},
                ]},
            ],
            "list_items": ["list item 1", ...],
            "stats": {"h2_count": 4, "h3_count": 12, "para_count": 30},
        }
    """
    from docx import Document

    doc = Document(file_path)

    # Phase 1: collect all paragraphs with styles
    entries: list[dict[str, Any]] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = para.style.name if para.style else "Normal"
        entries.append({"style": style, "text": text})

    # Phase 2: identify top-level metadata (before first heading)
    title = ""
    top_list: list[str] = []
    body_start = 0

    for i, entry in enumerate(entries):
        style = entry["style"]
        if _is_heading_style(style, heading_styles):
            body_start = i
            break
        if i == 0:
            title = entry["text"]
        elif style == "List Paragraph":
            top_list.append(entry["text"])

    # Phase 3: build heading tree
    sections: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []

    _body_headings = [
        _heading_level(e["style"])
        for e in entries[body_start:]
        if _is_heading_style(e["style"], heading_styles)
    ]
    root_level = min(_body_headings) if _body_headings else 2

    def _section(level: int, heading: str) -> dict[str, Any]:
        return {"level": level, "heading": heading, "paragraphs": [], "children": []}

    for entry in entries[body_start:]:
        style = entry["style"]
        text = entry["text"]

        if _is_heading_style(style, heading_styles):
            level = _heading_level(style)
            sec = _section(level, text)

            while stack and stack[-1]["level"] >= level:
                stack.pop()

            if level == root_level:
                sections.append(sec)
                stack = [sec]
            elif stack:
                stack[-1]["children"].append(sec)
                while stack and stack[-1]["level"] >= level:
                    stack.pop()
                stack.append(sec)
            else:
                sections.append(sec)
                stack = [sec]
        else:
            if stack:
                stack[-1]["paragraphs"].append(text)
            else:
                sections.append(_section(2, "未分类"))

    # Phase 4: statistics
    def _accurate_count(secs):
        h2, h3, para = 0, 0, 0
        for s in secs:
            if s["level"] == 2:
                h2 += 1
            elif s["level"] == 3:
                h3 += 1
            para += len(s["paragraphs"])
            ch2, ch3, cp = _accurate_count(s["children"])
            h2 += ch2
            h3 += ch3
            para += cp
        return h2, h3, para

    h2, h3, para = _accurate_count(sections)

    return {
        "title": title,
        "sections": sections,
        "list_items": top_list + [e["text"] for e in entries[body_start:] if e["style"] == "List Paragraph"],
        "stats": {"h2_count": h2, "h3_count": h3, "para_count": para},
    }


def outline_to_markdown(outline: dict[str, Any]) -> str:
    """Render outline tree as markdown for LLM consumption."""
    lines: list[str] = []

    lines.append(f"# {outline['title']}\n")

    if outline.get("list_items"):
        lines.append("## 文档概述\n")
        for item in outline["list_items"]:
            lines.append(f"- {item}")
        lines.append("")

    def _render(sec: dict[str, Any]) -> None:
        prefix = "#" * min(sec["level"] + 1, 4)
        lines.append(f"{prefix} {sec['heading']}")
        if sec["paragraphs"]:
            lines.append("")
            for para in sec["paragraphs"]:
                lines.append(para)
            lines.append("")
        for child in sec["children"]:
            _render(child)

    for sec in outline["sections"]:
        _render(sec)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# LLM-ready structure builders
# ═══════════════════════════════════════════════════════════════════════════════

_BODY_TRUNCATE_CHARS = 500


def _strip_leaf_paragraphs(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip body paragraphs from leaf nodes, preserving only structure skeleton."""
    result: list[dict[str, Any]] = []
    for sec in sections:
        s = dict(sec)
        if s["children"]:
            s["children"] = _strip_leaf_paragraphs(s["children"])
        else:
            n = len(s.get("paragraphs", []))
            if n:
                s["paragraphs"] = [f"（{n} 段正文，已剥离）"]
        result.append(s)
    return result


def _flatten_section_tree(root: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a section tree into a list with stable IDs and parent_id references."""
    result: list[dict[str, Any]] = []
    _counter = [0]

    def _walk(node: dict[str, Any], parent_id: str | None) -> None:
        _counter[0] += 1
        sid = f"s{_counter[0]}"
        result.append({
            "id": sid,
            "heading": node["heading"],
            "level": node["level"],
            "paragraphs": node.get("paragraphs", []),
            "parent_id": parent_id,
        })
        for child in node.get("children", []):
            _walk(child, sid)

    _walk(root, None)
    return result


def group_by_top_level_sections(outline: dict[str, Any]) -> list[dict[str, Any]]:
    """Split outline into groups by top-level sections."""
    groups: list[dict[str, Any]] = []
    for sec in outline["sections"]:
        groups.append({
            "heading": sec["heading"],
            "level": sec["level"],
            "paragraphs": sec.get("paragraphs", []),
            "children": sec.get("children", []),
        })
    return groups


def build_structure_for_llm(outline: dict[str, Any]) -> dict[str, Any]:
    """Build LLM-ready structure: strip leaf bodies, group, flatten.

    Each group gets a flat list of sections with stable IDs and parent_ids.
    LLM returns entities keyed by section_id — no nested structure to maintain.

    Returns:
        {
            "title": "...",
            "overview": ["...", "..."],
            "groups": [
                {"heading": "...",
                 "sections": [{"id":"s1","heading":"...","level":2,"paragraphs":[...],"parent_id":None}, ...]},
            ],
            "paragraphs_lookup": {"s2": ["original para 1", ...], ...}
        }
    """
    groups_out: list[dict[str, Any]] = []
    paragraphs_lookup: dict[str, list[str]] = {}

    for g in group_by_top_level_sections(outline):
        stripped_tree = {
            "heading": g["heading"],
            "level": g["level"],
            "paragraphs": g["paragraphs"],
            "children": _strip_leaf_paragraphs(g["children"]),
        }
        full_tree = {
            "heading": g["heading"],
            "level": g["level"],
            "paragraphs": g["paragraphs"],
            "children": g["children"],
        }
        groups_out.append({
            "heading": g["heading"],
            "sections": _flatten_section_tree(stripped_tree),
        })
        for s in _flatten_section_tree(full_tree):
            if s["paragraphs"]:
                paragraphs_lookup[s["id"]] = s["paragraphs"]

    return {
        "title": outline.get("title", ""),
        "overview": outline.get("list_items", []),
        "groups": groups_out,
        "paragraphs_lookup": paragraphs_lookup,
    }


def outline_to_flat_requirements(outline: dict[str, Any]) -> list[dict[str, Any]]:
    """Rule-based entity extraction (skeleton, no LLM).

    H2 → RequirementModel, H3 → RequirementItem.
    IDs and FKs are code-generated for consistency.
    """
    items: list[dict[str, Any]] = []

    title = outline.get("title", "").strip()
    product_id = _make_slug(title) if title else "prod-doc"
    items.append({
        "_type": "products",
        "id": product_id,
        "name": title,
        "description": "；".join(outline.get("list_items", [])),
    })

    rm_counter = 0
    ir_counter = 0

    def _process(secs: list[dict], rm_id: str = "") -> None:
        nonlocal rm_counter, ir_counter
        for sec in secs:
            if sec["level"] == 2:
                rm_counter += 1
                rid = f"RM-{rm_counter:03d}"
                items.append({
                    "_type": "requirement_models",
                    "id": rid,
                    "name": f"{rid} {sec['heading']}",
                    "type": "user_requirement",
                    "product_id": product_id,
                    "description": sec["paragraphs"][0] if sec["paragraphs"] else "",
                })
                _process(sec["children"], rid)
            elif sec["level"] == 3:
                ir_counter += 1
                iid = f"IR-{ir_counter}"
                items.append({
                    "_type": "requirement_items",
                    "id": iid,
                    "name": iid,
                    "title": sec["heading"],
                    "description": "\n".join(sec["paragraphs"]),
                    "priority": "中",
                    "status": "未实现",
                    "rm_id": rm_id,
                })
                _process(sec["children"], rm_id)

    _process(outline["sections"])
    return items


# ═══════════════════════════════════════════════════════════════════════════════
# deepagents @tool
# ═══════════════════════════════════════════════════════════════════════════════


@tool
def parse_docx_outline(file_path: str, heading_styles: str = "Heading") -> str:
    """Parse a .docx file and return its structured outline as JSON.

    The outline contains the full heading hierarchy, body paragraphs, and
    a pre-built 'llm_structure' for entity extraction.

    Use this before extract_entities to understand the document structure.

    Args:
        file_path: Absolute or relative path to the .docx file.
        heading_styles: Comma-separated heading style prefixes.
                        Default \"Heading\". Use \"Heading,FAI\" for custom styles.
    """
    path = Path(file_path)
    if not path.exists():
        return json.dumps({"error": f"File not found: {file_path}"}, ensure_ascii=False)

    styles = tuple(h.strip() for h in heading_styles.split(",") if h.strip()) or ("Heading",)
    outline = extract_outline(str(path), heading_styles=styles)
    llm_structure = build_structure_for_llm(outline)

    return json.dumps({
        "title": outline["title"],
        "stats": outline["stats"],
        "sections": outline["sections"],
        "llm_structure": llm_structure,
    }, ensure_ascii=False, indent=2)
