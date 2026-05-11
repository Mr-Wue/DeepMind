"""
GraphQueryState — 工作流 State TypedDict.
"""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class GraphQueryState(TypedDict, total=False):
    user_input: str
    intent: str
    sql: str
    query_result: list[dict[str, Any]]
    answer: str
    error: str


def initial_state(user_input: str) -> GraphQueryState:
    return GraphQueryState(
        user_input=user_input,
        intent="",
        sql="",
        query_result=[],
        answer="",
        error="",
    )
