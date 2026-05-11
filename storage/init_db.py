"""
Initialize reqmgmt database — stripped from CodeMind storage/init_db.py.

No DomainManager, codebase, chromadb, or meta dependencies.
Pure sqlite3 + schema.sql.

Usage:
    python storage/init_db.py                      # default path
    python storage/init_db.py --db path/to/db.db   # custom path
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "storage" / "reqmgmt" / "schema.sql"

from utils.paths import data_paths
DEFAULT_DB = data_paths.reqmgmt_db()


def init_reqmgmt(db_path: str | Path = "") -> Path:
    """Apply reqmgmt DDL to SQLite database. Idempotent (CREATE TABLE IF NOT EXISTS).

    Args:
        db_path: Target database path. Defaults to data/reqmgmt/reqmgmt.db.

    Returns:
        Resolved database path.
    """
    target = Path(db_path) if db_path else DEFAULT_DB
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    ddl = SCHEMA_FILE.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(target))
    conn.executescript(ddl)
    conn.commit()
    conn.close()

    print(f"[reqmgmt] schema applied ({len(ddl.splitlines())} lines) -> {target}")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize reqmgmt database")
    parser.add_argument("--db", default="", help="Target database path")
    args = parser.parse_args()
    init_reqmgmt(args.db)
