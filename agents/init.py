"""
DeepMind 全局初始化 — 数据库 + 记忆 + Backend + 中间件。

记忆相关的所有细节（Store/Checkpointer 创建、setup、seed、cleanup）
封装在 memory 包中，agents/init.py 只需调用 init_memory() 一行搞定。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv


class MiddlewareList(list):
    """支持 .add() 的中间件列表，下游可追加自定义中间件。"""

    def add(self, m: Any) -> "MiddlewareList":
        self.append(m)
        return self


@dataclass
class DeepMindConfig:
    """下游 Agent 工厂使用的结构化配置。"""

    store: Any                                        # AsyncSqliteStore 实例（来自 MemoryContext）
    checkpointer: Any                                 # AsyncSqliteSaver 实例（来自 MemoryContext）
    backend: Any                                      # CompositeBackend
    middleware: MiddlewareList = field(default_factory=MiddlewareList)
    memory_ctx: Any = field(default=None)             # MemoryContext（cleanup 时使用，不暴露细节）


async def init_deepmind() -> DeepMindConfig:
    """初始化数据库 + 记忆 + Backend + 中间件。

    记忆初始化由 memory.init_memory() 一站式完成：
      - 创建 Store + Checkpointer（SQLite async context manager）
      - setup()（初始化 SQLite 表）
      - seed（写入种子记忆文件）
    """
    load_dotenv(_PROJECT_ROOT / ".env")

    # 数据库
    from models.reqmgmt import init_db

    init_db()
    print("[DeepMind] 数据库初始化完成")

    # 中间件
    from middleware.logging_middleware import InvocationLoggingHandler

    middleware = MiddlewareList([InvocationLoggingHandler.as_middleware()])

    # 记忆（一站式: Store + Checkpointer + setup + seed）
    from memory import init_memory, create_memory_backend

    memory_ctx = await init_memory()
    backend = create_memory_backend()

    return DeepMindConfig(
        store=memory_ctx.store,
        checkpointer=memory_ctx.checkpointer,
        backend=backend,
        middleware=middleware,
        memory_ctx=memory_ctx,
    )


async def cleanup_deepmind(config: DeepMindConfig) -> None:
    """关闭 SQLite 连接（进程退出时调用）。"""
    from memory import cleanup_memory
    if config.memory_ctx:
        await cleanup_memory(config.memory_ctx)

