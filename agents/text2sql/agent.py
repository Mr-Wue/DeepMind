"""
执行器 — 对应 CodeMind engine/workflow/graph_query/runner.py.

对外暴露唯一入口 ``ReqMgmtText2SQLAgent``，内部持有 GraphQueryWorkflow 单例。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.checkpoint.memory import MemorySaver

from models.reqmgmt import build_schema_text, init_db
from utils.paths import data_paths

from .graph import GraphQueryWorkflow
from .state import GraphQueryState, initial_state

logger = logging.getLogger(__name__)


class ReqMgmtText2SQLAgent:
    """领域数据查询 Agent — reqmgmt Text2SQL 的唯一入口。

    Usage::

        from middleware.logging_middleware import InvocationLoggingHandler

        handler = InvocationLoggingHandler(log_dir="data/logs")
        agent = ReqMgmtText2SQLAgent()
        result = await agent.query("查询所有产品", callbacks=[handler])
    """

    def __init__(
        self,
        db_path: str = "",
        *,
        checkpointer: MemorySaver | None = None,
    ) -> None:
        # ── 初始化数据库 + schema ──
        p = Path(db_path) if db_path else data_paths.reqmgmt_db()
        if not p.is_absolute():
            p = Path.cwd() / p
        self._db_path = str(p)
        init_db(self._db_path)
        schema = build_schema_text()
        logger.info("[ReqMgmtText2SQL] schema loaded, %d chars, db=%s", len(schema), self._db_path)

        # ── 编译工作流（进程内单例） ──
        self._workflow = GraphQueryWorkflow(schema, checkpointer=checkpointer)

    # ── 公开接口 ────────────────────────────────────────────────────────

    async def query(
        self,
        user_input: str,
        *,
        thread_id: str = "default",
        callbacks: list[BaseCallbackHandler] | None = None,
    ) -> dict[str, Any]:
        """自然语言查询，返回完整 state dict（含 answer / sql / query_result）。

        Args:
            user_input: 自然语言查询。
            thread_id: 会话 ID，用于 LangGraph checkpoint 隔离。
            callbacks: LangChain callbacks（如 InvocationLoggingHandler）。
        """
        state = initial_state(user_input)
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id},
            "metadata": {"thread_id": thread_id},
        }
        if callbacks:
            config["callbacks"] = callbacks
        return await self._workflow.compiled.ainvoke(state, config=config)

    def query_sync(
        self,
        user_input: str,
        *,
        thread_id: str = "default",
        callbacks: list[BaseCallbackHandler] | None = None,
    ) -> dict[str, Any]:
        """query() 的同步包装。"""
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.query(user_input, thread_id=thread_id, callbacks=callbacks))
        raise RuntimeError("异步上下文中请使用 await agent.query()")
