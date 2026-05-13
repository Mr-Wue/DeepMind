"""
UI 中断处理 — LangGraph human-in-the-loop 确认交互。

当工具内部调用 ``langgraph.types.interrupt()`` 时，
展示 Chainlit 确认卡片，用户确认后以 Command(resume=...) 恢复执行。
"""

from __future__ import annotations

from typing import Any

import chainlit as cl
from langgraph.types import Command

from ui.streaming import (
    EVT_LLM_STREAM,
    is_main_agent_stream,
)


async def handle_interrupts(
    agent: Any,
    run_config: dict,
    context: Any,
    msg: cl.Message,
) -> None:
    """检测并处理 LangGraph 中断。

    循环处理多轮中断（同一轮 Agent 执行可能触发多次 interrupt）。
    """
    while True:
        state = await agent.aget_state(run_config)
        if not state.interrupts:
            break

        for interrupt_item in state.interrupts:
            payload = interrupt_item.value
            action = payload.get("action", "")
            decision = await _ask_user(action, payload)
            await _resume(agent, run_config, context, msg, decision)


# ── 确认 UI ──────────────────────────────────────────────────────────────────


async def _ask_user(action: str, payload: dict) -> dict:
    """根据中断类型展示对应的确认 UI，返回 decision dict。"""
    message_text = payload.get("message", "是否确认执行此操作？")

    if action == "store_entities":
        return await _ask_store_entities(message_text, payload)
    else:
        return await _ask_generic(message_text)


async def _ask_store_entities(message_text: str, payload: dict) -> dict:
    """入库确认卡片。"""
    by_type = payload.get("by_type", {})
    detail_lines = [f"- {k}: {v} 个" for k, v in by_type.items()]
    detail_text = "\n".join(detail_lines)

    res = await cl.AskActionMessage(
        content=f"📦 **{message_text}**\n\n{detail_text}",
        actions=[
            cl.Action(name="approve", payload={"decision": "approve"}, label="✅ 确认入库"),
            cl.Action(name="reject", payload={"decision": "reject"}, label="❌ 取消"),
        ],
        timeout=3600,
    ).send()

    if res is not None:
        return res.get("payload", {"decision": "reject"})
    return {"decision": "reject"}


async def _ask_generic(message_text: str) -> dict:
    """通用确认卡片。"""
    res = await cl.AskActionMessage(
        content=f"⚠️ **{message_text}**",
        actions=[
            cl.Action(name="approve", payload={"decision": "approve"}, label="✅ 确认"),
            cl.Action(name="reject", payload={"decision": "reject"}, label="❌ 取消"),
        ],
        timeout=3600,
    ).send()

    if res is not None:
        return res.get("payload", {"decision": "reject"})
    return {"decision": "reject"}


# ── 恢复执行 ─────────────────────────────────────────────────────────────────


async def _resume(
    agent: Any,
    run_config: dict,
    context: Any,
    msg: cl.Message,
    decision: dict,
) -> None:
    """以 Command(resume=decision) 恢复 Agent 执行，流式输出 LLM 回复。"""
    async for event in agent.astream_events(
        Command(resume=decision),
        config=run_config,
        version="v2",
        context=context,
    ):
        kind = event["event"]
        if kind == EVT_LLM_STREAM and is_main_agent_stream(event):
            chunk = event["data"]["chunk"]
            if hasattr(chunk, "content") and chunk.content:
                await msg.stream_token(chunk.content)

    await msg.update()
