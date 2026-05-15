"""
Graph 工厂 — LangGraph Server 入口。

langgraph dev 启动时导入此模块，调用 ``graph()`` 获取 CompiledStateGraph。
使用懒加载单例 + asyncio.Lock 确保 init_deepmind() 只执行一次。
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.graph.state import CompiledStateGraph

_graph: CompiledStateGraph | None = None
_lock = asyncio.Lock()


async def graph(config: dict[str, Any] | None = None) -> CompiledStateGraph:
    """LangGraph Server 入口 — 懒加载并缓存 Agent 实例。

    首次调用执行 init_deepmind()（数据库初始化 + Store/Checkpointer + Middleware），
    后续调用直接返回缓存的 CompiledStateGraph。
    """
    global _graph, _lock

    if _graph is not None:
        return _graph

    async with _lock:
        if _graph is not None:
            return _graph

        from agents.init import init_deepmind
        from agents.deep_agent import create_deepmind_agent

        deepmind_config = await init_deepmind()
        _graph = create_deepmind_agent(deepmind_config)
        return _graph
