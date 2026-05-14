"""
LangGraph astream_events → Chainlit UI 操作映射。

将 DeepAgent 的细粒度事件流转换为 Chainlit 可消费的结构，
支持：Token 流式、步骤显示、工具调用可视化、子 Agent 可视化、TodoList 可视化。

显示名配置来自 deepMind.toml 的 [ui.display] 段，
新增工具/子 Agent 时只需改 TOML，无需改代码。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import logging
from typing import Any

import chainlit as cl

from utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 事件类型常量
# ═══════════════════════════════════════════════════════════════════════════════

EVT_CHAIN_START = "on_chain_start"
EVT_CHAIN_END   = "on_chain_end"
EVT_TOOL_START  = "on_tool_start"
EVT_TOOL_END    = "on_tool_end"
EVT_LLM_STREAM  = "on_chat_model_stream"
EVT_LLM_END     = "on_chat_model_end"


# ═══════════════════════════════════════════════════════════════════════════════
# Todo 工具常量
# ═══════════════════════════════════════════════════════════════════════════════

TODO_TOOL_NAME = "write_todos"


# ═══════════════════════════════════════════════════════════════════════════════
# TOML 配置加载
# ═══════════════════════════════════════════════════════════════════════════════


def _load_display_config() -> tuple[dict[str, str], dict[str, str], str]:
    """从 deepMind.toml 加载 UI 显示配置。

    Returns:
        (node_display, tool_display, welcome_message)
    """
    try:
        # Python 3.11+ 使用 tomllib
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    config_path = PROJECT_ROOT / "deepMind.toml"
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)

    ui = cfg.get("ui", {})
    display = ui.get("display", {})
    theme = ui.get("theme", {})

    node_display = display.get("nodes", {})
    tool_display = display.get("tools", {})
    welcome_message = theme.get("welcome_message", "👋 欢迎使用 DeepMind！")

    return node_display, tool_display, welcome_message


# 模块级加载（只在 import 时执行一次）
NODE_DISPLAY, TOOL_DISPLAY, WELCOME_MESSAGE = _load_display_config()


# ═══════════════════════════════════════════════════════════════════════════════
# 显示名解析
# ═══════════════════════════════════════════════════════════════════════════════


def get_display_name(event: dict) -> str:
    """从事件中提取可读的显示名称。

    查找优先级：
    1. TOML 中配置的工具名 → TOOL_DISPLAY
    2. TOML 中配置的 Agent 名（lc_agent_name） → NODE_DISPLAY
    3. TOML 中配置的节点名（langgraph_node） → NODE_DISPLAY
    4. 原始名称 fallback
    """
    name = event.get("name", "")
    metadata = event.get("metadata", {})
    node = metadata.get("langgraph_node", "")
    agent = metadata.get("lc_agent_name", "")

    if name in TOOL_DISPLAY:
        return TOOL_DISPLAY[name]
    if agent in NODE_DISPLAY:
        return NODE_DISPLAY[agent]
    if node in NODE_DISPLAY:
        return NODE_DISPLAY[node]
    return name or node or "步骤"


def is_main_agent_stream(event: dict) -> bool:
    """判断事件是否来自主 Agent 的 LLM 流（用于 token 级流式输出）。

    深 Agent 的 LLM 调用发生在 ``model`` 节点（或 ``agent`` 节点，兼容旧版本）。
    """
    node = event.get("metadata", {}).get("langgraph_node", "")
    return (
        event["event"] == EVT_LLM_STREAM
        and node in ("agent", "model")
    )


def is_tool_event(event: dict) -> bool:
    """判断事件是否是工具调用相关。"""
    return event["event"] in (EVT_TOOL_START, EVT_TOOL_END)


def get_agent_name(event: dict) -> str:
    """从事件 metadata 中提取 Agent 名称（lc_agent_name）。

    主 Agent 返回空字符串，子 Agent 返回其名称如 "req-parse"。
    """
    return event.get("metadata", {}).get("lc_agent_name", "")


def is_subagent_event(event: dict) -> bool:
    """判断事件是否来自子 Agent（通过 lc_agent_name 识别）。"""
    return bool(get_agent_name(event))


def extract_tool_input(event: dict) -> str:
    """从 on_tool_start 事件提取工具输入（格式化显示）。"""
    input_data = event.get("data", {}).get("input", {})
    if isinstance(input_data, dict):
        # 精简显示，截断过长内容
        return str(input_data)[:500]
    return str(input_data)[:500]


# ═══════════════════════════════════════════════════════════════════════════════
# 事件过滤
# ═══════════════════════════════════════════════════════════════════════════════

# MCP 内部工具名 → 不在 UI 中显示
_MCP_INTERNAL_TOOLS = frozenset({"webSearchPro", "webSearchStd", "webSearchSogou", "webSearchQuark"})


def is_mcp_internal_event(event: dict) -> bool:
    """判断是否为 MCP 底层工具事件（不应在 UI 显示 Step）。"""
    return event.get("name", "") in _MCP_INTERNAL_TOOLS


def is_todo_tool_event(event: dict) -> bool:
    """判断是否为 write_todos 工具调用事件（需特殊渲染为 checklist）。"""
    return event.get("name", "") == TODO_TOOL_NAME


def should_skip_as_step(event: dict) -> bool:
    """判断工具事件是否应跳过普通 Step 渲染（todo 和 MCP 内部工具）。"""
    return is_todo_tool_event(event) or is_mcp_internal_event(event)


# ═══════════════════════════════════════════════════════════════════════════════
# Todo 提取与格式化
# ═══════════════════════════════════════════════════════════════════════════════


def extract_todos_from_tool_input(event: dict) -> list[dict]:
    """从 on_tool_start 事件中提取 write_todos 的 todo 列表。

    write_todos 工具的 input 格式为: {"todos": [{"content": "...", "status": "..."}]}
    """
    input_data = event.get("data", {}).get("input", {})
    if isinstance(input_data, dict):
        return input_data.get("todos", [])
    return []


def format_todo_checklist(todos: list[dict]) -> str:
    """将 todo 列表格式化为 Markdown checklist，用于 UI 显示。

    根据 status 显示不同 emoji:
    - completed  → ✅
    - in_progress → 🔄
    - pending    → ⏳
    """
    if not todos:
        return ""

    status_emoji = {
        "completed": "✅",
        "in_progress": "🔄",
        "pending": "⏳",
    }

    lines = ["📋 **任务规划：**"]
    for todo in todos:
        content = todo.get("content", "")
        status = todo.get("status", "pending")
        emoji = status_emoji.get(status, "⏳")
        lines.append(f"- {emoji} {content}")

    return "\n".join(lines)


def extract_tool_output(event: dict) -> str:
    """从 on_tool_end 事件提取工具输出（格式化显示）。

    支持 ToolMessage 对象（有 .content）、字符串、列表等类型。
    """
    output = event.get("data", {}).get("output", "")
    if hasattr(output, "content"):
        content = output.content
        # content 可能是 str 或 list[dict]
        if isinstance(content, str):
            return content[:1000]
        return str(content)[:1000]
    return str(output)[:1000]


# ═══════════════════════════════════════════════════════════════════════════════
# 共享事件流处理
# ═══════════════════════════════════════════════════════════════════════════════


async def process_event_stream(
    event_stream,
    msg: cl.Message,
    todo_msg: cl.Message | None,
    active_steps: dict[str, cl.Step],
    current_subagent_step: cl.Step | None,
    current_subagent_name: str,
) -> tuple[cl.Message | None, cl.Step | None, str]:
    """处理 Agent 事件流，更新 UI 状态。

    同时用于首次执行和中断恢复（Command(resume=...)），
    确保两种场景下事件处理逻辑完全一致。

    Returns:
        (todo_msg, current_subagent_step, current_subagent_name) — 可能被更新的状态。
    """
    async for event in event_stream:
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
                    todo_msg = cl.Message(content=checklist, author="📋 任务规划")
                    await todo_msg.send()
                else:
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

    return todo_msg, current_subagent_step, current_subagent_name