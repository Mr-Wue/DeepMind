"""
条件路由 — 对应 CodeMind engine/workflow/graph_query/routing.py.
"""

from __future__ import annotations

from typing import Any, Literal

from .state import GraphQueryState


class GraphQueryRouter:
    """领域数据查询工作流路由器。"""

    @staticmethod
    def after_parse(state: GraphQueryState) -> Literal["sql_generate", "render"]:
        if state.get("intent") == "domain_query":
            return "sql_generate"
        return "render"

    @staticmethod
    def after_sql_generate(state: GraphQueryState) -> Literal["execute_sql", "render"]:
        if state.get("sql", "").strip():
            return "execute_sql"
        return "render"
