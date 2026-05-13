"""
GraphQueryState — 工作流 State TypedDict.
"""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class GraphQueryState(TypedDict, total=False):
    user_input: str
    intent: str
    parse_info: dict[str, Any]
    sub_queries: list[str]
    sub_query_index: int
    current_query: str
    query_results: list[list[dict[str, Any]]]
    sql: str
    query_result: list[dict[str, Any]]
    answer: str
    error: str
    retry_count: int
    last_error: str


def initial_state(user_input: str) -> GraphQueryState:
    return GraphQueryState(
        user_input=user_input,
        intent="",
        parse_info={},
        sub_queries=[],
        sub_query_index=0,
        current_query="",
        query_results=[],
        sql="",
        query_result=[],
        answer="",
        error="",
        retry_count=0,
        last_error="",
    )
