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
        _handler_singleton = InvocationLoggingHandler()
    return _agent_singleton, _handler_singleton


def create_sql_query_tool():
    """Create a deepagents-compatible tool for reqmgmt database queries."""

    @tool
    async def query_reqmgmt(query: str) -> str:
        """使用自然语言查询需求管理数据库（只读）。

        此工具只能执行 SELECT 查询，不能进行增删改操作。
        请勿尝试 INSERT、UPDATE、DELETE 或 DROP 等写操作。

        当用户询问以下内容时使用此工具：
        - 产品（Products）：名称、描述等
        - 需求模型（RM）：用户需求类型或产品需求类型
        - 用户需求项（IR）：标题、优先级、状态等
        - 产品需求项（PR）：具有父子层级关系的结构化需求
        - 统计信息：数量、分组、聚合

        数据库中包含类似"XX电商平台"的产品，每个产品下有类似"用户管理模块"
        的需求模型，每个模型下有包含标题、描述、优先级、状态等字段的需求项。

        Args:
            query: 中文或英文的自然语言查询。
                   例如："列出所有产品"、"用户管理模块下有多少需求项"、
                   "查询优先级为高的需求项"

        Returns:
            格式化的 Markdown 表格查询结果。
        """
        agent, handler = _get_agent_and_handler()
        result = await agent.query(query, callbacks=[handler])
        answer = result.get("answer", "查询未返回结果")
        sql = result.get("sql", "")
        if sql:
            answer += f"\n\n<details>\n<summary>🔍 生成的 SQL</summary>\n\n```sql\n{sql}\n```\n</details>"
        return answer

    return query_reqmgmt
