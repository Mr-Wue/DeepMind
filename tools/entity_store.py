"""
Thin entity store — SQLite CRUD for parsed requirement entities.
No ORM, no domain abstraction. Just the 3 tables we need for validation.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from utils.paths import data_paths

_DB_PATH = data_paths.reqmgmt_db()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS requirement_models (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT DEFAULT 'user_requirement',
    product_id TEXT,
    description TEXT DEFAULT '',
    FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS requirement_items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    title TEXT DEFAULT '',
    description TEXT DEFAULT '',
    priority TEXT DEFAULT '中',
    status TEXT DEFAULT '未实现',
    rm_id TEXT,
    FOREIGN KEY (rm_id) REFERENCES requirement_models(id)
);
"""


def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Initialize database schema (idempotent)."""
    conn = _get_conn()
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


@tool
def entity_store(operation: str, entities_json: str = "", query_type: str = "", keyword: str = "") -> str:
    """Store extracted entities to or query entities from the database.

    Args:
        operation: 'store' to save entities, 'query' to retrieve, 'stats' for counts.
        entities_json: JSON array of entity objects. Required for 'store'.
            Each entity must have: _type (one of: products, requirement_models, requirement_items),
            id, name, and other type-specific fields.
        query_type: Entity type to query: 'products', 'requirement_models', 'requirement_items'.
        keyword: Optional keyword to filter by (searches name, title, description).
    """
    conn = _get_conn()
    try:
        if operation == "store":
            return _do_store(conn, entities_json)
        elif operation == "query":
            return _do_query(conn, query_type, keyword)
        elif operation == "stats":
            return _do_stats(conn)
        else:
            return f"Error: unknown operation '{operation}'. Use 'store', 'query', or 'stats'."
    finally:
        conn.close()


def _do_store(conn: sqlite3.Connection, entities_json: str) -> str:
    if not entities_json:
        return "Error: entities_json is required for 'store' operation."

    try:
        entities: list[dict[str, Any]] = json.loads(entities_json)
    except json.JSONDecodeError as e:
        return f"Error: invalid JSON: {e}"

    if not isinstance(entities, list):
        return "Error: entities_json must be a JSON array."

    type_table_map = {
        "products": "products",
        "product": "products",
        "requirement_models": "requirement_models",
        "requirement_model": "requirement_models",
        "RM": "requirement_models",
        "requirement_items": "requirement_items",
        "requirement_item": "requirement_items",
        "IR": "requirement_items",
    }

    # Separate entities by type (topological order: products → models → items)
    products = []
    models = []
    items = []
    unknown = []

    for ent in entities:
        etype = ent.pop("_type", "")
        table = type_table_map.get(etype, "")
        if table == "products":
            products.append(ent)
        elif table == "requirement_models":
            models.append(ent)
        elif table == "requirement_items":
            items.append(ent)
        else:
            unknown.append((etype, ent))

    inserted = 0
    errors: list[str] = []

    # Insert products first
    for ent in products:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO products (id, name, description) VALUES (?, ?, ?)",
                (ent.get("id", ""), ent.get("name", ""), ent.get("description", "")),
            )
            inserted += 1
        except Exception as e:
            errors.append(f"product {ent.get('id', '?')}: {e}")

    # Then requirement_models
    for ent in models:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO requirement_models (id, name, type, product_id, description) VALUES (?, ?, ?, ?, ?)",
                (ent.get("id", ""), ent.get("name", ""), ent.get("type", "user_requirement"),
                 ent.get("product_id", ""), ent.get("description", "")),
            )
            inserted += 1
        except Exception as e:
            errors.append(f"requirement_model {ent.get('id', '?')}: {e}")

    # Then requirement_items
    for ent in items:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO requirement_items (id, name, title, description, priority, status, rm_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ent.get("id", ""), ent.get("name", ""), ent.get("title", ""),
                 ent.get("description", ""), ent.get("priority", "中"),
                 ent.get("status", "未实现"), ent.get("rm_id", ent.get("model_id", ""))),
            )
            inserted += 1
        except Exception as e:
            errors.append(f"requirement_item {ent.get('id', '?')}: {e}")

    conn.commit()

    result = {
        "success": len(errors) == 0,
        "total": len(entities),
        "inserted": inserted,
        "by_type": {
            "products": len(products),
            "requirement_models": len(models),
            "requirement_items": len(items),
        },
    }
    if unknown:
        result["unknown_types"] = [t for t, _ in unknown]
    if errors:
        result["errors"] = errors

    return json.dumps(result, ensure_ascii=False, indent=2)


def _do_query(conn: sqlite3.Connection, query_type: str, keyword: str = "") -> str:
    type_table_map = {
        "products": "products",
        "product": "products",
        "requirement_models": "requirement_models",
        "requirement_model": "requirement_models",
        "requirement_items": "requirement_items",
        "requirement_item": "requirement_items",
    }

    table = type_table_map.get(query_type, "")
    if not table:
        return f"Error: unknown query_type '{query_type}'. Use one of: products, requirement_models, requirement_items."

    if keyword:
        if table == "products":
            rows = conn.execute(
                "SELECT * FROM products WHERE name LIKE ? OR description LIKE ?",
                (f"%{keyword}%", f"%{keyword}%"),
            ).fetchall()
        elif table == "requirement_models":
            rows = conn.execute(
                "SELECT * FROM requirement_models WHERE name LIKE ? OR description LIKE ?",
                (f"%{keyword}%", f"%{keyword}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM requirement_items WHERE name LIKE ? OR title LIKE ? OR description LIKE ?",
                (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"),
            ).fetchall()
    else:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()

    results = [dict(row) for row in rows]
    return json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2)


def _do_stats(conn: sqlite3.Connection) -> str:
    product_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    model_count = conn.execute("SELECT COUNT(*) FROM requirement_models").fetchone()[0]
    item_count = conn.execute("SELECT COUNT(*) FROM requirement_items").fetchone()[0]

    # Per-model item counts
    model_stats = conn.execute(
        "SELECT rm.name, COUNT(ri.id) as item_count "
        "FROM requirement_models rm LEFT JOIN requirement_items ri ON ri.rm_id = rm.id "
        "GROUP BY rm.id ORDER BY item_count DESC"
    ).fetchall()

    return json.dumps({
        "products": product_count,
        "requirement_models": model_count,
        "requirement_items": item_count,
        "by_model": [{"model": row[0], "item_count": row[1]} for row in model_stats],
    }, ensure_ascii=False, indent=2)
