"""
工作流节点 — 查询拆解 → SQL 生成 → 执行 → 渲染 / 子查询推进。

LLM 调用使用 llm 模块。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

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


# ═══════════════════════════════════════════════════════════════════════════════
# 查询拆解
# ═══════════════════════════════════════════════════════════════════════════════

_QUERY_SPLIT_PROMPT: ChatPromptTemplate | None = None


def _get_query_split_prompt() -> ChatPromptTemplate:
    global _QUERY_SPLIT_PROMPT
    if _QUERY_SPLIT_PROMPT is None:
        tmpl = _load_prompt("query_split.j2")
        _QUERY_SPLIT_PROMPT = ChatPromptTemplate.from_messages([
            ("system", tmpl),
            ("human", "{user_input}"),
        ])
    return _QUERY_SPLIT_PROMPT


async def parse_query(state: GraphQueryState, *, schema: str = "") -> dict[str, Any]:
    """查询拆解 — LLM 判定意图并拆解为子查询。"""
    user_input = state["user_input"]

    prompt = _get_query_split_prompt()
    llm = get_llm("default", temperature=0)

    chain = prompt | llm | StrOutputParser()
    raw = await chain.ainvoke({
        "user_input": user_input,
        "schema": schema,
        "agent_scope": "需求管理 — 产品、需求模型、需求条目、产品需求、零部件、测试用例等",
        "history_sub_queries": "",
    })

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r'\{[^}]+\}', raw, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group())
            except json.JSONDecodeError:
                result = {"intent": "domain_query", "queries": [user_input]}
        else:
            result = {"intent": "domain_query", "queries": [user_input]}

    intent = result.get("intent", "domain_query")
    queries = [q.strip() for q in result.get("queries", [user_input]) if q.strip()] or [user_input]

    print(f"  → 拆解: intent={intent}, 子查询 {len(queries)} 条")
    for i, q in enumerate(queries):
        print(f"     [{i+1}] {q}")

    return {
        "intent": intent,
        "sub_queries": queries,
        "sub_query_index": 0,
        "current_query": queries[0],
        "query_results": [],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SQL 生成
# ═══════════════════════════════════════════════════════════════════════════════

async def sql_generate(state: GraphQueryState, *, schema: str) -> dict[str, Any]:
    """SQL 生成 — LLM 生成 SELECT 语句。"""
    current_query = state.get("current_query") or state["user_input"]
    last_error = state.get("last_error", "")
    retry_count = state.get("retry_count", 0)

    tmpl = _load_prompt("text2sql.j2")
    system_text = tmpl.format(schema=schema)

    # 注入前序结果（多步查询时）
    prev_results = state.get("query_results", [])
    if prev_results:
        parts: list[str] = []
        for step_no, batch in enumerate(prev_results, 1):
            sample = [{k: v for k, v in row.items()} for row in batch[:3]]
            parts.append(
                f"步骤{step_no}结果（共 {len(batch)} 条）：\n"
                + json.dumps(sample, ensure_ascii=False, indent=2)
            )
        system_text += "\n\n前序查询结果：\n" + "\n\n".join(parts)

    messages = [SystemMessage(content=system_text)]

    if retry_count > 0 and last_error:
        messages.append(HumanMessage(content=(
            f"## 上次生成的 SQL 执行失败（第 {retry_count} 次重试）\n\n"
            f"**错误信息**: {last_error}\n\n"
            f"请分析错误原因，修正后重新生成 SQL。原始查询: {current_query}"
        )))
    else:
        messages.append(HumanMessage(content=current_query))

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

    print(f"  → SQL: {sql[:200]}{'...' if len(sql) > 200 else ''}")
    return {"sql": sql, "error": ""}


# ═══════════════════════════════════════════════════════════════════════════════
# SQL 执行
# ═══════════════════════════════════════════════════════════════════════════════

def execute_sql(state: GraphQueryState) -> dict[str, Any]:
    """SQL 执行 — 直接执行并返回结果。"""
    sql = state.get("sql", "").strip()
    if not sql:
        return {"query_result": [], "error": state.get("error", "SQL 为空")}

    engine = get_engine()
    try:
        with engine.connect() as conn:
            raw = conn.exec_driver_sql(sql)
            keys = list(raw.keys())
            rows = [dict(zip(keys, row)) for row in raw.fetchall()]
        print(f"  → SQL 返回 {len(rows)} 条记录")
        return {"query_result": rows, "error": ""}
    except Exception as exc:
        error_msg = str(exc)
        return {
            "query_result": [],
            "error": error_msg,
            "retry_count": state.get("retry_count", 0) + 1,
            "last_error": error_msg,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 子查询推进
# ═══════════════════════════════════════════════════════════════════════════════

def advance_query(state: GraphQueryState) -> dict[str, Any]:
    """推进到下一个子查询，累积当前结果。"""
    idx = state.get("sub_query_index", 0) + 1
    sub_queries: list[str] = state.get("sub_queries", [])
    accumulated = list(state.get("query_results", []))
    accumulated.append(list(state.get("query_result", [])))

    next_query = sub_queries[idx] if idx < len(sub_queries) else ""
    print(f"  → 子查询 [{idx}/{len(sub_queries)}]: {next_query}")

    return {
        "sub_query_index": idx,
        "current_query": next_query,
        "query_results": accumulated,
        "query_result": [],
        "sql": "",
        "error": "",
        "retry_count": 0,
        "last_error": "",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 结果渲染
# ═══════════════════════════════════════════════════════════════════════════════

async def render(state: GraphQueryState) -> dict[str, Any]:
    """结果渲染 — 合并多步结果，格式化为 Markdown 表格。"""
    all_rows: list[dict[str, Any]] = []
    for batch in state.get("query_results", []):
        all_rows.extend(batch)
    all_rows.extend(state.get("query_result", []))

    error = state.get("error", "")
    user_input = state["user_input"]
    sql = state.get("sql", "")

    if error:
        return {"answer": f"查询失败: {error}\n\nSQL: `{sql}`"}

    if not all_rows:
        return {"answer": f"查询「{user_input}」未返回匹配记录。"}

    cols = list(all_rows[0].keys())
    header = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---" for _ in cols]) + "|"
    body_lines = []
    for row in all_rows[:50]:
        body_lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")

    answer = (
        f"## 查询结果\n\n**{user_input}** — 共 {len(all_rows)} 条\n\n"
        + "\n".join([header, sep] + body_lines)
    )
    if len(all_rows) > 50:
        answer += f"\n\n*... 共 {len(all_rows)} 条，仅展示前 50 条*"

    return {"answer": answer}
