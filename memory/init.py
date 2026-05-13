"""
Memory 初始化 — Store + Checkpointer 的创建、生命周期管理和种子数据写入。

封装 AsyncSqliteStore / AsyncSqliteSaver 的 async context manager 模式，
对外只暴露 MemoryContext（包含可直接使用的 store/checkpointer 实例）
和 init_memory() / cleanup_memory() 两个函数。

下游（agents/init.py）只需::

    ctx = await init_memory()       # 一行搞定 store + checkpointer + setup + seed
    agent = create_deep_agent(
        store=ctx.store,
        checkpointer=ctx.checkpointer,
        ...
    )
    # 进程退出时:
    await cleanup_memory(ctx)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepagents.backends.utils import create_file_data
from memory.config import get_default_user_id, get_long_term_files
from utils.paths import data_paths

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════════════════
# Store / Checkpointer 工厂
# ═══════════════════════════════════════════════════════════════════════════════


def _create_store_cm():
    """创建 AsyncSqliteStore 的 async context manager。

    数据库文件: {data_dir}/memory/deepmind_store.db
    """
    from langgraph.store.sqlite import AsyncSqliteStore

    db_path = data_paths.store_db()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return AsyncSqliteStore.from_conn_string(str(db_path))


def _create_checkpointer_cm():
    """创建 AsyncSqliteSaver 的 async context manager。

    数据库文件: {data_dir}/memory/deepmind_checkpoints.db
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = data_paths.checkpoint_db()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return AsyncSqliteSaver.from_conn_string(str(db_path))


# ═══════════════════════════════════════════════════════════════════════════════
# 种子数据
# ═══════════════════════════════════════════════════════════════════════════════


async def _seed_memory_files(store) -> None:
    """首次运行时，将模板 .md 内容写入 Store（已存在则跳过）。"""
    user_id = get_default_user_id()
    namespace = ("user", user_id)
    templates_dir = _PROJECT_ROOT / "memory" / "long_term"

    existing = await store.asearch(namespace)
    existing_keys = {item.key for item in existing}

    for filename in get_long_term_files():
        key = f"/memories/{filename}"
        if key in existing_keys:
            continue
        src = templates_dir / filename
        content = src.read_text(encoding="utf-8") if src.exists() else ""
        await store.aput(namespace, key, create_file_data(content))


# ═══════════════════════════════════════════════════════════════════════════════
# MemoryContext — 生命周期封装
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class MemoryContext:
    """记忆基础设施的运行时上下文。

    - store / checkpointer: 可直接使用的 LangGraph 实例
    - store_cm / checkpointer_cm: 内部使用的 async context manager（cleanup 时关闭连接）

    下游只需访问 store / checkpointer，不需要关心 context manager 细节。
    """

    store: Any                                        # AsyncSqliteStore 实例
    checkpointer: Any                                 # AsyncSqliteSaver 实例
    store_cm: Any = field(default=None)               # 内部: Store 的 async context manager
    checkpointer_cm: Any = field(default=None)        # 内部: Checkpointer 的 async context manager


async def init_memory() -> MemoryContext:
    """一站式初始化记忆基础设施。

    1. 创建 AsyncSqliteStore + AsyncSqliteSaver（async context manager）
    2. 进入上下文 + setup()（初始化 SQLite 表）
    3. 种子数据写入（首次运行时写入 profile.md / knowledge.md）

    Returns:
        MemoryContext — 包含可直接使用的 store / checkpointer 实例
    """
    store_cm = _create_store_cm()
    checkpointer_cm = _create_checkpointer_cm()

    # 进入 async context manager
    store = await store_cm.__aenter__()
    checkpointer = await checkpointer_cm.__aenter__()

    # 初始化 SQLite 表（idempotent）
    await store.setup()
    await checkpointer.setup()

    print(f"[DeepMind] SQLite 数据库初始化完成")
    print(f"  Store:       {data_paths.store_db()}")
    print(f"  Checkpointer: {data_paths.checkpoint_db()}")

    # 种子数据
    await _seed_memory_files(store)

    print(f"[DeepMind] 记忆 Store 就绪 ({type(store).__name__})")
    print(f"[DeepMind] Checkpointer 就绪 ({type(checkpointer).__name__})")

    return MemoryContext(
        store=store,
        checkpointer=checkpointer,
        store_cm=store_cm,
        checkpointer_cm=checkpointer_cm,
    )


async def cleanup_memory(ctx: MemoryContext) -> None:
    """关闭 SQLite 连接（进程退出时调用）。

    开发环境下可省略（进程退出时 SQLite 自动清理）。
    """
    if ctx.store_cm and ctx.checkpointer_cm:
        await ctx.store_cm.__aexit__(None, None, None)
        await ctx.checkpointer_cm.__aexit__(None, None, None)
        print("[DeepMind] SQLite 连接已关闭")