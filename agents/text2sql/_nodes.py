"""
工作流节点 — 对应 CodeMind engine/workflow/graph_query/_nodes_*.py.

每个节点是纯 async function，不继承 BaseNode。
LLM 调用使用 llm 模块（默认走 text2sql role）。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from llm import get_llm
from models.reqmgmt import get_engine

from .state import GraphQueryState

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def _load_prompt(filename: str) -> str:
    p = _PROMPTS_DIR / filename
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


# ── SQL 清洗（从 CodeMind Text2SQLSkill._clean 移植）────────────────────

def _clean_sql(raw: str) -> str:
    """从 LLM 原始输出中提取第一条完整的 SELECT 语句。"""
    raw = raw.strip()
    select_idx = raw.upper().find("SELECT")
    if select_idx > 0:
        raw = raw[select_idx:]
    if raw.startswith("```"):
        raw = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("```")).strip()
    semi_idx = raw.find(";")
    if semi_idx != -1:
        raw = raw[: semi_idx + 1].strip()
    return raw


# ── Nodes ─────────────────────────────────────────────────────────────────

async def parse_query(state: GraphQueryState) -> dict[str, Any]:
    """查询解析节点 — 在 reqmgmt 上下文中始终为 domain_query。"""
    return {"intent": "domain_query"}


async def sql_generate(state: GraphQueryState, *, schema: str) -> dict[str, Any]:
    """SQL 生成节点 — LLM 生成 SELECT 语句。"""
    user_input = state["user_input"]
    tmpl = _load_prompt("text2sql.j2")
    system_text = tmpl.format(schema=schema) if tmpl else (
        f"你是 SQLite 查询生成器。只输出一条 SELECT … ;\n\n"
        f"【数据库结构】\n{schema}"
    )

    messages = [SystemMessage(content=system_text), HumanMessage(content=user_input)]

    try:
        llm = get_llm("text2sql", temperature=0)
    except (ValueError, Exception):
        llm = get_llm("default", temperature=0)

    response = await llm.ainvoke(messages)
    raw = response.content if hasattr(response, "content") else str(response)
    sql = _clean_sql(raw)

    if not sql.upper().lstrip().startswith("SELECT"):
        logger.warning("[sql_generate] 非 SELECT 输出: %s", raw[:120])
        return {"sql": "", "error": f"LLM 未生成有效 SQL: {raw[:200]}"}

    return {"sql": sql, "error": ""}


def execute_sql(state: GraphQueryState) -> dict[str, Any]:
    """SQL 执行节点 — 直接执行并返回结果。"""
    sql = state.get("sql", "").strip()
    if not sql:
        return {"query_result": [], "error": state.get("error", "SQL 为空")}

    engine = get_engine()
    try:
        with engine.connect() as conn:
            raw = conn.exec_driver_sql(sql)
            keys = list(raw.keys())
            rows = [dict(zip(keys, row)) for row in raw.fetchall()]
        return {"query_result": rows, "error": ""}
    except Exception as exc:
        return {"query_result": [], "error": str(exc)}


async def render(state: GraphQueryState) -> dict[str, Any]:
    """结果渲染节点 — 格式化为 Markdown 表格。"""
    rows = state.get("query_result", [])
    error = state.get("error", "")
    user_input = state["user_input"]

    if error:
        return {"answer": f"查询失败: {error}\n\nSQL: `{state.get('sql', '')}`"}

    if not rows:
        return {"answer": f"查询「{user_input}」未返回匹配记录。"}

    cols = list(rows[0].keys())
    header = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---" for _ in cols]) + "|"
    body = []
    for row in rows[:50]:
        body.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")

    answer = (
        f"## 查询结果\n\n**{user_input}** — 共 {len(rows)} 条\n\n"
        + "\n".join([header, sep] + body)
    )
    if len(rows) > 50:
        answer += f"\n\n*... 共 {len(rows)} 条，仅展示前 50 条*"

    return {"answer": answer}
