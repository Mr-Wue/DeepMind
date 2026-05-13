"""
Inspect Memory DB — 查看 DeepMind 的 checkpoint 和 store 数据库内容。

利用 LangGraph 官方 SqliteSaver.list() 自动解码 msgpack，
按 parent_checkpoint_id 正序排列，输出可读的对话内容。

Usage:
    python storage/inspect_memory_db.py THREAD_ID

切换模式改下面 MODE 变量即可：
    "checkpoint"  → 查看 thread 的所有 checkpoint（默认）
    "last"        → 只看最后一个 checkpoint
    "store"       → 查看 Store 记忆文件内容
    "reset"       → 清空重建数据库
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.paths import data_paths

# ═══════════════════════════════════════════════════════════════════════════════
# 模式配置 — 改这里切换功能
# ═══════════════════════════════════════════════════════════════════════════════

MODE = "checkpoint"  # "checkpoint" | "last" | "store" | "reset"


# ═══════════════════════════════════════════════════════════════════════════════
# Checkpoint 查询
# ═══════════════════════════════════════════════════════════════════════════════


def _format_message(msg: Any, max_content: int = 300) -> str:
    """格式化单条消息为可读文本。"""
    role = getattr(msg, "type", "?")
    content = str(getattr(msg, "content", ""))
    if len(content) > max_content:
        content = content[:max_content] + "..."

    name = getattr(msg, "name", "")
    tc = getattr(msg, "tool_calls", None)
    extra = ""
    if name:
        extra = f" (name={name})"
    if tc:
        tool_names = [c.get("name", "") for c in tc]
        extra += f" tool_calls=[{', '.join(tool_names)}]"
        for c in tc:
            args_str = json.dumps(c.get("args", {}), ensure_ascii=False)
            if len(args_str) > 200:
                args_str = args_str[:200] + "..."
            extra += f"\n      {c.get('name', '')}: {args_str}"

    return f"[{role}]{extra} {content}"


def inspect_checkpoints(thread_id: str, last_only: bool = False) -> None:
    """查看指定 thread_id 的所有 checkpoint，按 step 正序排列。"""
    db_path = data_paths.checkpoint_db()
    if not db_path.exists():
        print(f"[ERROR] Checkpoint DB not found: {db_path}")
        return

    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(str(db_path))
    saver = SqliteSaver(conn)

    config = {"configurable": {"thread_id": thread_id}}
    tuples = list(saver.list(config))

    if not tuples:
        print(f"[INFO] No checkpoints found for thread_id='{thread_id}'")
        conn.close()
        return

    # list() 返回倒序（最新先），按 step 正序排列
    tuples.sort(key=lambda t: t.metadata.get("step", 0) if t.metadata else 0)

    if last_only:
        tuples = [tuples[-1]]
        print(f"=== Last checkpoint for thread '{thread_id}' ===")
    else:
        print(f"=== All checkpoints for thread '{thread_id}' ({len(tuples)} steps) ===")

    for t in tuples:
        cp = t.checkpoint
        cp_id = cp.get("id", "N/A") if isinstance(cp, dict) else getattr(cp, "id", "N/A")
        cp_ts = cp.get("ts", "N/A") if isinstance(cp, dict) else getattr(cp, "ts", "N/A")
        step = t.metadata.get("step", "?") if t.metadata else "?"
        source = t.metadata.get("source", "?") if t.metadata else "?"

        print(f"\n  Step {step} | source={source} | ts={cp_ts}")
        print(f"  checkpoint_id: {cp_id}")

        cv = cp.get("channel_values", {}) if isinstance(cp, dict) else getattr(cp, "channel_values", {})
        if "messages" in cv:
            msgs = cv["messages"]
            if hasattr(msgs, "value"):
                msgs = msgs.value
            if not isinstance(msgs, (list, tuple)):
                msgs = [msgs]
            print(f"  messages ({len(msgs)}):")
            for msg in msgs:
                print(f"    {_format_message(msg)}")
        else:
            keys = list(cv.keys()) if isinstance(cv, dict) else "?"
            print(f"  channel_values keys: {keys}")

    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Store 查询
# ═══════════════════════════════════════════════════════════════════════════════


async def inspect_store() -> None:
    """查看 Store 中的所有记忆文件内容。"""
    db_path = data_paths.store_db()
    if not db_path.exists():
        print(f"[ERROR] Store DB not found: {db_path}")
        return

    from langgraph.store.sqlite import AsyncSqliteStore

    async with AsyncSqliteStore.from_conn_string(str(db_path)) as store:
        await store.setup()

        namespaces = await store.alist_namespaces(prefix=())
        print(f"=== Store namespaces: {[tuple(ns) for ns in namespaces]} ===")

        for ns in namespaces:
            ns_tuple = tuple(ns)
            items = await store.asearch(ns_tuple, limit=100)
            print(f"\n  Namespace: ({', '.join(ns_tuple)})")
            print(f"  Items ({len(items)}):")
            for item in items:
                value = item.value
                content = value.get("content", str(value)) if isinstance(value, dict) else str(value)
                if len(content) > 500:
                    content = content[:500] + "..."
                print(f"    key={item.key} | created_at={item.created_at} | updated_at={item.updated_at}")
                print(f"    content: {content}")


# ═══════════════════════════════════════════════════════════════════════════════
# Reset
# ═══════════════════════════════════════════════════════════════════════════════


async def reset_memory_db() -> None:
    """清空重建数据库（删除 .db 文件后重建）。"""
    for db_path in [data_paths.store_db(), data_paths.checkpoint_db()]:
        if db_path.exists():
            db_path.unlink()
            print(f"[memory] 已删除: {db_path}")

    from memory import init_memory, cleanup_memory

    ctx = await init_memory()
    print("[memory] 数据库重建完成")
    await cleanup_memory(ctx)


# ═══════════════════════════════════════════════════════════════════════════════
# Main — 传入 thread_id 直接查询
# ═══════════════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    # if MODE == "reset":
    #     asyncio.run(reset_memory_db())
    if MODE == "store":
        asyncio.run(inspect_store())
    elif MODE in ("checkpoint", "last"):
        thread_id = "9be97d80-3358-40b2-8b8c-800f631bb8db"
        if not thread_id:
            print("Usage: python storage/inspect_memory_db.py THREAD_ID")
            sys.exit(1)
        inspect_checkpoints(thread_id, last_only=(MODE == "last"))
    else:
        print(f"[ERROR] Unknown MODE: {MODE}")
        sys.exit(1)