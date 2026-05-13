"""
条件路由 — 对应 CodeMind engine/workflow/graph_query/routing.py.
"""

from __future__ import annotations

from typing import Any, Literal

from .state import GraphQueryState

_MAX_RETRIES = 3


class GraphQueryRouter:
    """领域数据查询工作流路由器。"""

    @staticmethod
    def after_parse(state: GraphQueryState) -> Literal["sql_generate", "render"]:
        intent = state.get("intent", "")
        if intent in ("domain_query", "graph_query"):
            return "sql_generate"
        return "render"

    @staticmethod
    def after_sql_generate(state: GraphQueryState) -> Literal["execute_sql", "render"]:
        if state.get("sql", "").strip():
            return "execute_sql"
        return "render"

    @staticmethod
    def _has_more_sub_queries(state: GraphQueryState) -> bool:
        sub_queries: list = state.get("sub_queries", [])
        idx: int = state.get("sub_query_index", 0)
        return bool(sub_queries) and idx + 1 < len(sub_queries)

    @staticmethod
    def after_execute(state: GraphQueryState) -> Literal["sql_generate", "advance_query", "render"]:
        """SQL 执行后：有错误且未达重试上限 → 重试；有更多子查询 → 推进；否则渲染。"""
        error = state.get("error", "")
        retry = state.get("retry_count", 0)
        if error and retry <= _MAX_RETRIES:
            return "sql_generate"
        if GraphQueryRouter._has_more_sub_queries(state):
            return "advance_query"
        return "render"
