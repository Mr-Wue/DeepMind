"""
Memory 包 — 统一出口。

下游只需::

    from memory import (
        init_memory,       # 一站式: Store + Checkpointer + setup + seed
        cleanup_memory,    # 关闭 SQLite 连接
        MemoryContext,     # 运行时上下文（store, checkpointer）
        create_memory_backend,  # Backend 路由构建
        get_long_term_memory_paths,  # Agent 虚拟路径列表
        get_default_user_id,        # 默认用户 ID
    )
"""

from memory.init import init_memory, cleanup_memory, MemoryContext
from memory.backends import create_memory_backend
from memory.config import get_long_term_memory_paths, get_default_user_id

__all__ = [
    "init_memory",
    "cleanup_memory",
    "MemoryContext",
    "create_memory_backend",
    "get_long_term_memory_paths",
    "get_default_user_id",
]