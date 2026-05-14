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
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chainlit as cl

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

from agents.init import init_deepmind
from agents.deep_agent import DeepMindContext, create_deepmind_agent
from ui.interrupt import handle_interrupts
from ui.streaming import (
    WELCOME_MESSAGE,
    process_event_stream,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 文件上传处理
# ═══════════════════════════════════════════════════════════════════════════════


def _get_element_name(element, fallback_path: str) -> str:
    """获取元素的原始文件名，回退到路径 basename。"""
    return getattr(element, "name", "") or Path(fallback_path).name


async def _notify_file_too_large(filename: str) -> None:
    """通知用户文件过大被跳过。"""
    max_mb = MAX_FILE_SIZE // (1024 * 1024)
    await cl.Message(
        content=f"⚠️ 文件 `{filename}` 超过 {max_mb}MB 限制，已跳过。",
        author="System",
    ).send()


async def _process_uploaded_elements(elements: list, thread_id: str) -> list[str]:
    """处理 Chainlit 上传文件：拷贝到持久目录，保留原始文件名。

    返回虚拟路径（相对于项目根目录，以 / 开头），兼容 deepagents 虚拟文件系统。
    """
    from utils.paths import data_paths, PROJECT_ROOT

    # 必须用 upload_files_dir()（PROJECT_ROOT 下），而非 files_dir()（可能在外部 data_dir）
    # 否则 deepagents 虚拟文件系统（根为 PROJECT_ROOT）无法访问
    files_dir = data_paths.upload_files_dir()

    session_id = f"{thread_id}_{str(uuid.uuid4())[:8]}"
    session_dir = files_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    file_paths: list[str] = []
    for el in elements:
        path = getattr(el, "path", "")
        if not path:
            continue

        try:
            file_size = Path(path).stat().st_size
            if file_size > MAX_FILE_SIZE:
                original_name = _get_element_name(el, path)
                await _notify_file_too_large(original_name)
                continue
        except OSError:
            continue

        original_name = _get_element_name(el, path)
        dest_path = session_dir / original_name
        shutil.copy2(path, dest_path)
        # 转为虚拟路径（deepagents FilesystemMiddleware 要求所有路径以 / 开头）
        virtual_path = "/" + str(dest_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        file_paths.append(virtual_path)

    return file_paths


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
        file_paths = await _process_uploaded_elements(message.elements, thread_id)
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
        todo_msg, current_subagent_step, current_subagent_name = await process_event_stream(
            agent.astream_events(
                input_data, config=run_config, version="v2", context=context,
            ),
            msg,
            todo_msg,
            active_steps,
            current_subagent_step,
            current_subagent_name,
        )

        # ── 最终确认消息 ──
        await msg.update()

        # ── 中断处理 ───────────────────────────────────────────────────
        todo_msg, current_subagent_step, current_subagent_name = await handle_interrupts(
            agent, run_config, context, msg,
            todo_msg, active_steps, current_subagent_step, current_subagent_name,
        )

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