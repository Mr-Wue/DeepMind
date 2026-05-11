"""
Tool wrapper for ReqMgmtText2SQLAgent — deepagents-compatible.

Lazily initializes the Text2SQL agent + logging handler (singletons) and
exposes a ``query_reqmgmt`` tool for natural language database queries.

Usage in deepagents::

    from tools.sql_query import create_sql_query_tool

    agent = create_deep_agent(
        model=model,
        tools=[read_docx, store_entities, create_sql_query_tool()],
    )
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from utils.paths import data_paths

_agent_singleton: Any = None
_handler_singleton: Any = None


def _get_agent_and_handler() -> tuple[Any, Any]:
    global _agent_singleton, _handler_singleton
    if _agent_singleton is None:
        from agents.text2sql_agent import ReqMgmtText2SQLAgent
        from middleware.logging_middleware import InvocationLoggingHandler
        _agent_singleton = ReqMgmtText2SQLAgent(db_path=str(data_paths.reqmgmt_db()))
        _handler_singleton = InvocationLoggingHandler(log_dir="data/logs")
    return _agent_singleton, _handler_singleton


def create_sql_query_tool():
    """Create a deepagents-compatible tool for reqmgmt database queries."""

    @tool
    async def query_reqmgmt(query: str) -> str:
        """Query the requirements management database using natural language.

        Use this tool when the user asks about:
        - Products (产品): names, descriptions
        - Requirement Models / RM (需求模型): user_requirement or product_requirement types
        - Requirement Items / IR (用户需求项): titles, priorities, statuses
        - Product Requirements / PR (产品需求项): structured requirements with parent-child hierarchy
        - Statistics: counts, groupings, aggregations

        The database contains products like "XX电商平台", requirement_models
        like "用户管理模块" under each product, and requirement_items under
        each model with fields like title, description, priority, status.

        Args:
            query: Natural language query in Chinese or English.
                   E.g. "列出所有产品", "用户管理模块下有多少需求项",
                   "查询优先级为高的需求项"

        Returns:
            Formatted markdown table with query results.
        """
        agent, handler = _get_agent_and_handler()
        result = await agent.query(query, callbacks=[handler])
        return result.get("answer", "查询未返回结果")

    return query_reqmgmt
