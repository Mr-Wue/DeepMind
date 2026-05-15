"""
ContextMonitorMiddleware — 每次 LLM 调用前打印上下文统计。

帮助排查：上下文是否过长、注意力分散、子 Agent/工具划分是否合理。
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately


# ── 最近消息摘要（类型 + 前 N 字符 + 工具调用） ──────────────────────────


def _msg_summary(msg, max_chars: int = 120) -> dict[str, Any]:
    content = getattr(msg, "content", "")
    text = content if isinstance(content, str) else str(type(content).__name__)
    tc_names = [
        tc.get("name", "?")
        for tc in (getattr(msg, "tool_calls", None) or [])
    ]
    return {
        "type": type(msg).__name__,
        "chars": len(text),
        "preview": text[:max_chars].replace("\n", " "),
        "tool_calls": tc_names,
    }


# ── Middleware ─────────────────────────────────────────────────────────────


class ContextMonitorMiddleware(AgentMiddleware):
    """每次 LLM 调用前输出上下文统计快照。

    输出：
    - 消息数 + Token 估算
    - 类型分布（Human / AI / Tool / System）
    - 可用工具列表
    - 子 Agent (task) 调用次数
    - 是否已触发上下文压缩
    - 最近 N 条消息摘要
    """

    def __init__(self, max_preview: int = 6, dump_on_compact: bool = True):
        self.max_preview = max_preview
        self.dump_on_compact = dump_on_compact
        self._call_count = 0

    # ── 核心快照逻辑 ────────────────────────────────────────────────────

    def _snapshot(self, request: ModelRequest) -> dict[str, Any]:
        msgs = request.messages
        sm = request.system_message

        # 类型分布
        type_counts: dict[str, int] = {}
        for m in msgs:
            t = type(m).__name__
            type_counts[t] = type_counts.get(t, 0) + 1

        # Token 估算（含 system prompt）
        all_msgs = [sm, *msgs] if sm else list(msgs)
        total_tokens = count_tokens_approximately(all_msgs)

        # Tool 输出总字符（上下文膨胀主因之一）
        tool_output_chars = sum(
            len(getattr(m, "content", "") or "")
            for m in msgs
            if isinstance(m, ToolMessage)
        )

        # 子 Agent (task) 调用次数
        task_calls = sum(
            1 for m in msgs
            if isinstance(m, AIMessage)
            and getattr(m, "tool_calls", None)
            and any(tc.get("name") == "task" for tc in m.tool_calls)
        )

        # 上下文压缩状态
        summ = request.state.get("_summarization_event")
        is_compacted = summ is not None
        compact_cutoff = summ.get("cutoff_index") if is_compacted else None

        # 可用工具
        tools_list = [
            t.name if hasattr(t, "name") else str(t)
            for t in request.tools
        ]

        # 最近消息预览
        recent = [_msg_summary(m) for m in msgs[-self.max_preview:]]

        # System prompt 统计
        sp_text = sm.text if sm else ""
        sp_chars = len(sp_text)

        return {
            "msg_count": len(msgs),
            "type_counts": type_counts,
            "total_tokens": total_tokens,
            "system_prompt_chars": sp_chars,
            "tool_output_chars": tool_output_chars,
            "task_calls": task_calls,
            "is_compacted": is_compacted,
            "compact_cutoff": compact_cutoff,
            "tools_count": len(tools_list),
            "tools_list": tools_list,
            "recent_messages": recent,
        }

    # ── 控制台输出 ───────────────────────────────────────────────────────

    @staticmethod
    def _print_snapshot(snap: dict[str, Any], call_no: int) -> None:
        SEP = "━" * 60
        print(f"\n{SEP}")
        print(f"[CTX] LLM 调用 #{call_no} 前上下文快照")
        print(f"  消息: {snap['msg_count']} 条  |  "
              f"估算 Token: {snap['total_tokens']:,}  |  "
              f"已压缩: {snap['is_compacted']}")
        if snap["compact_cutoff"]:
            print(f"  压缩截断点: 消息索引 {snap['compact_cutoff']}")
        print(f"  System prompt: {snap['system_prompt_chars']:,} chars")
        print(f"  类型分布: {snap['type_counts']}")
        print(f"  Tool 输出累计: {snap['tool_output_chars']:,} chars  |  "
              f"task() 调用: {snap['task_calls']}")
        print(f"  可用工具 ({snap['tools_count']}): {', '.join(snap['tools_list'])}")
        print(f"  最近 {len(snap['recent_messages'])} 条消息:")
        for r in snap["recent_messages"]:
            tc_str = f" → {r['tool_calls']}" if r["tool_calls"] else ""
            print(f"    [{r['type']}]{tc_str} ({r['chars']:,} chars)")
            if r["preview"]:
                print(f"      {r['preview'][:130]}")
        print(f"{'─' * 60}")

    # ── 可选：完整 dump 到文件 ──────────────────────────────────────────

    def _maybe_dump(self, snap: dict[str, Any]) -> None:
        if not self.dump_on_compact:
            return
        if not snap["is_compacted"]:
            return
        from utils.paths import data_paths

        ts = time.strftime("%Y%m%d_%H%M%S")
        dump_path = data_paths.logs_dir() / f"ctx_snapshot_{ts}.json"
        dump_path.write_text(
            json.dumps(snap, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[CTX] 完整快照已保存 → {dump_path}")

    # ── Hook ─────────────────────────────────────────────────────────────

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        self._call_count += 1
        snap = self._snapshot(request)
        self._print_snapshot(snap, self._call_count)
        self._maybe_dump(snap)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        self._call_count += 1
        snap = self._snapshot(request)
        self._print_snapshot(snap, self._call_count)
        self._maybe_dump(snap)
        return await handler(request)
