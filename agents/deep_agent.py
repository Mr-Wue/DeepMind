"""
DeepMind 根 Agent — 从 DeepMindConfig 组装所有组件，返回 CompiledStateGraph。

Usage:
    from agents.init import init_deepmind
    from agents.deep_agent import DeepMindContext, create_deepmind_agent

    config = await init_deepmind()
    # config.middleware.add(CustomMiddleware())  ← 下游可追加

    agent = create_deepmind_agent(config)

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "解析需求文档并入库"}]},
        config={"configurable": {"thread_id": "session-1"}},
        context=DeepMindContext(user_id="default"),
    )
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from deepagents import create_deep_agent
from utils.paths import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════════════════════
# Context
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class DeepMindContext:
    """Agent 运行时上下文。user_id 用于 StoreBackend 的用户隔离。

    调用时通过顶层 context 参数传入：
        agent.invoke(..., context=DeepMindContext(user_id="xxx"))
    """

    user_id: str


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 构建
# ═══════════════════════════════════════════════════════════════════════════════


def create_deepmind_agent(config=None):
    """构建 Agent，自包含初始化。

    Args:
        config: 可选，init_deepmind() 的结构化配置。不传则自动初始化。
    """
    if config is None:
        raise TypeError(
            "config 不能为 None — init_deepmind() 现在是 async 函数，"
            "请先调用 `config = await init_deepmind()` 再传入。"
        )

    from tools.sql_query import create_sql_query_tool
    from tools.web_search import create_web_search_tool
    from tools.update_entities import update_entities, get_db_schema

    # ── LLM ─────────────────────────────────────────────────────────────
    from llm.base import get_llm

    model = get_llm("default", temperature=0.0)

    # ── 工具 ───────────────────────────────────────────────────────────
    query_reqmgmt = create_sql_query_tool()
    web_search = create_web_search_tool()

    # ── 系统提示词 ─────────────────────────────────────────────────────
    system_prompt = (PROJECT_ROOT / "prompts" / "system.md").read_text(encoding="utf-8")

    # ── 子 Agent ───────────────────────────────────────────────────────
    from agents.subagents.req_parse import build_req_parse_subagent

    req_parse_subagent = build_req_parse_subagent(middleware=config.middleware)

    # ── 组装 ───────────────────────────────────────────────────────────
    from memory import get_long_term_memory_paths

    agent = create_deep_agent(
        model=model,
        tools=[query_reqmgmt, web_search, update_entities, get_db_schema],
        memory=get_long_term_memory_paths(),
        backend=config.backend,
        store=config.store,
        system_prompt=system_prompt,
        middleware=config.middleware,
        subagents=[req_parse_subagent],
        checkpointer=config.checkpointer,
        context_schema=DeepMindContext,
    )

    print(f"[DeepMind] Agent 创建完成  (model={model.model_name})")
    print(f"  长期记忆: {get_long_term_memory_paths()}")
    print(f"  主工具:   [query_reqmgmt, web_search, update_entities, get_db_schema]")
    print(f"  子 Agent 'req-parse': [parse_docx_outline, extract_entities, store_entities]")
    return agent
