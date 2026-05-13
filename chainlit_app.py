"""
DeepMind Chainlit 入口 — 基于 DeepAgent 的全功能 Chat UI。

支持：Token 流式输出、步骤显示、工具调用可视化、子 Agent 可视化、TodoList 可视化、会话持久化。

启动（任选其一）::

    chainlit run chainlit_app.py -w
    python chainlit_app.py -w

浏览器访问 ``http://127.0.0.1:8000``（端口见 ``.chainlit/settings.toml``）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chainlit as cl

from agents.init import init_deepmind
from agents.deep_agent import DeepMindContext, create_deepmind_agent
from ui.interrupt import handle_interrupts
from ui.streaming import (
    EVT_CHAIN_START,
    EVT_CHAIN_END,
    EVT_TOOL_START,
    EVT_TOOL_END,
    EVT_LLM_STREAM,
    EVT_LLM_END,
    WELCOME_MESSAGE,
    get_display_name,
    get_agent_name,
    is_main_agent_stream,
    is_mcp_internal_event,
    is_todo_tool_event,
    should_skip_as_step,
    extract_tool_input,
    extract_tool_output,
    extract_todos_from_tool_input,
    format_todo_checklist,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 全局初始化
# ═══════════════════════════════════════════════════════════════════════════════


@cl.on_chat_start
async def on_chat_start():
    """会话开始 → 初始化 DeepAgent。"""
    config = await init_deepmind()
    agent = create_deepmind_agent(config)

    cl.user_session.set("agent", agent)
    cl.user_session.set("config", config)

    await cl.Message(content=WELCOME_MESSAGE).send()


# ═══════════════════════════════════════════════════════════════════════════════
# 消息处理 — astream_events v2 全功能流式
# ═══════════════════════════════════════════════════════════════════════════════


@cl.on_message
async def on_message(message: cl.Message):
    """用户消息 → DeepAgent astream_events v2 流式处理。

    事件流遍历逻辑：
    - on_chat_model_stream (agent节点) → msg.stream_token()  Token级流式
    - on_tool_start (write_todos)       → todo_msg 更新 checklist
    - on_tool_start (其他工具)          → cl.Step(name=工具) + 显示输入
    - on_tool_end   (write_todos)       → todo_msg 更新 checklist
    - on_tool_end   (其他工具)          → 更新 Step 显示输出
    - on_chain_start (子Agent节点)      → cl.Step(name=子Agent)
    - on_chain_end  (子Agent节点)       → 更新 Step
    """
    agent = cl.user_session.get("agent")
    thread_id = cl.context.session.id

    # ── 构建输入（含上传文件路径）───────────────────────────────────
    content = message.content
    if message.elements:
        file_paths = []
        for elem in message.elements:
            path = getattr(elem, "path", None)
            if path:
                file_paths.append(str(path))
        if file_paths:
            paths_str = "\n".join(f"- {p}" for p in file_paths)
            content = f"{content}\n\n📎 用户上传的文件（真实磁盘路径）：\n{paths_str}"

    input_data = {
        "messages": [{"role": "user", "content": content}],
    }
    run_config = {
        "configurable": {"thread_id": thread_id},
    }
    context = DeepMindContext(user_id="default")

    # ── UI 元素 ──────────────────────────────────────────────────────
    msg = cl.Message(content="")
    # run_id → Step 映射（工具 Step + 节点 Step）
    active_steps: dict[str, cl.Step] = {}
    # 当前活跃的子 Agent Step（工具 Step 嵌套在其下）
    current_subagent_step: cl.Step | None = None
    current_subagent_name: str = ""
    # TodoList checklist 消息（每次 write_todos 调用时更新）
    todo_msg: cl.Message | None = None

    await msg.send()

    try:
        # ── 事件流遍历 ───────────────────────────────────────────────
        async for event in agent.astream_events(
            input_data, config=run_config, version="v2", context=context,
        ):
            kind = event["event"]
            agent_name = get_agent_name(event)

            # ── 1. LLM Token 流式输出（主 Agent 回复）──
            if kind == EVT_LLM_STREAM and is_main_agent_stream(event):
                chunk = event["data"]["chunk"]
                if hasattr(chunk, "content") and chunk.content:
                    await msg.stream_token(chunk.content)

            # ── 1b. LLM 调用结束 → 兜底（流式未产生 token 时用完整响应填充）──
            elif kind == EVT_LLM_END and is_main_agent_stream(event):
                output = event.get("data", {}).get("output", {})
                content = ""
                if hasattr(output, "content") and output.content:
                    content = output.content
                elif hasattr(output, "generations") and output.generations:
                    gen = output.generations[0][0]
                    content = str(getattr(gen, "text", ""))
                if content and not msg.content:
                    msg.content = content

            # ── 2a. write_todos 工具调用开始 → 渲染 checklist ──
            elif kind == EVT_TOOL_START and is_todo_tool_event(event):
                todos = extract_todos_from_tool_input(event)
                checklist = format_todo_checklist(todos)
                if checklist:
                    if todo_msg is None:
                        # 第一次创建 todo，发送新消息
                        todo_msg = cl.Message(content=checklist, author="📋 任务规划")
                        await todo_msg.send()
                    else:
                        # 后续更新 todo，刷新已有消息
                        todo_msg.content = checklist
                        await todo_msg.update()

            # ── 2b. write_todos 工具调用结束 → checklist 已在 TOOL_START 中更新 ──
            elif kind == EVT_TOOL_END and is_todo_tool_event(event):
                pass  # checklist 已在 TOOL_START 中渲染完成

            # ── 2c. 其他工具调用开始 → 创建 Step ──
            elif kind == EVT_TOOL_START and not should_skip_as_step(event):
                display_name = get_display_name(event)
                tool_input = extract_tool_input(event)
                run_id = event.get("run_id", "")
                parent_id = current_subagent_step.id if current_subagent_step else None

                step = cl.Step(name=display_name, parent_id=parent_id)
                step.input = tool_input
                await step.send()
                active_steps[run_id] = step

            # ── 3. 其他工具调用结束 → 更新 Step 输出 ──
            elif kind == EVT_TOOL_END and not should_skip_as_step(event):
                run_id = event.get("run_id", "")
                tool_output = extract_tool_output(event)

                if run_id in active_steps:
                    step = active_steps[run_id]
                    step.output = tool_output
                    await step.update()
                    del active_steps[run_id]

            # ── 4. 链/节点开始 → 创建 Step ──
            elif kind == EVT_CHAIN_START:
                node = event.get("metadata", {}).get("langgraph_node", "")
                run_id = event.get("run_id", "")

                # 子 Agent 开始 → 创建父 Step（后续工具 Step 嵌套在下面）
                if agent_name and agent_name != current_subagent_name:
                    display_name = get_display_name(event) or agent_name
                    step = cl.Step(name=display_name)
                    await step.send()
                    active_steps[f"agent_{agent_name}"] = step
                    current_subagent_step = step
                    current_subagent_name = agent_name

                # 工具执行节点
                if node == "tools":
                    display_name = get_display_name(event) or "🔧 工具执行"
                    parent_id = current_subagent_step.id if current_subagent_step else None
                    step = cl.Step(name=display_name, type="tool", parent_id=parent_id)
                    await step.send()
                    active_steps[f"node_{run_id}"] = step

            # ── 5. 链/节点结束 → 更新 Step ──
            elif kind == EVT_CHAIN_END:
                node = event.get("metadata", {}).get("langgraph_node", "")
                run_id = event.get("run_id", "")

                # 工具节点结束
                key = f"node_{run_id}"
                if key in active_steps and node == "tools":
                    step = active_steps[key]
                    output_data = event.get("data", {}).get("output", "")
                    if output_data:
                        step.output = str(output_data)[:500]
                    await step.update()
                    del active_steps[key]

                # 子 Agent 结束（model 节点结束表示该 Agent 的工作完成）
                if agent_name and node == "model":
                    key = f"agent_{agent_name}"
                    if key in active_steps:
                        step = active_steps[key]
                        await step.update()
                        del active_steps[key]
                        current_subagent_step = None
                        current_subagent_name = ""

        # ── 最终确认消息 ──
        await msg.update()

        # ── 中断处理 ───────────────────────────────────────────────────
        await handle_interrupts(agent, run_config, context, msg)

    except Exception as e:
        # ── 错误处理 ──
        await msg.update()
        await cl.ErrorMessage(
            content=f"❌ 处理出错：{e}",
            author="DeepMind",
        ).send()


# ═══════════════════════════════════════════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════════════════════════════════════════


def _run_chainlit() -> None:
    """用 subprocess 调起 chainlit run，支持 python chainlit_app.py 直接启动。"""
    cmd = [sys.executable, "-m", "chainlit", "run", str(Path(__file__).resolve())]
    cmd.extend(sys.argv[1:])
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    _run_chainlit()