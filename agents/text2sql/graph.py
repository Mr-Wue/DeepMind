"""
工作流组装 — 对应 CodeMind engine/workflow/graph_query/graph.py.

流程: START → parse_query → sql_generate → execute_sql → render / advance_query
                              ↓ 失败重试          ↓ 有更多子查询
                              ↓                  ↓ advance_query → sql_generate
"""

from __future__ import annotations

import functools

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from ._nodes import advance_query, execute_sql, parse_query, render, sql_generate
from .routing import GraphQueryRouter
from .state import GraphQueryState


class GraphQueryWorkflow:
    """领域数据查询 Workflow。

    持有编译后的 CompiledGraph，schema 通过闭包注入到 sql_generate / parse_query 节点。
    """

    def __init__(self, schema: str = "", *, checkpointer: MemorySaver | None = None) -> None:
        self._schema = schema
        self._checkpointer = checkpointer or MemorySaver()
        self.compiled = self._build()

    def _build(self):
        router = GraphQueryRouter()
        builder = StateGraph(GraphQueryState)

        builder.add_node("parse_query", functools.partial(parse_query, schema=self._schema))
        builder.add_node("sql_generate", functools.partial(sql_generate, schema=self._schema))
        builder.add_node("execute_sql", execute_sql)
        builder.add_node("advance_query", advance_query)
        builder.add_node("render", render)

        builder.add_edge(START, "parse_query")
        builder.add_conditional_edges("parse_query", router.after_parse)
        builder.add_conditional_edges("sql_generate", router.after_sql_generate)
        builder.add_conditional_edges("execute_sql", router.after_execute)
        builder.add_edge("advance_query", "sql_generate")
        builder.add_edge("render", END)

        return builder.compile(checkpointer=self._checkpointer)
