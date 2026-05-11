"""
scenario_test.py — deepagents SubAgent pattern for document parsing + Text2SQL.

Architecture:
  Main Agent
  ├── tools: [query_reqmgmt]
  ├── memory: [AGENTS.md]
  ├── middleware: [InvocationLoggingHandler.as_middleware()]
  └── subagents:
      └── "req-parse"
          ├── tools: [parse_docx_outline, extract_entities, store_entities]
          └── skills: [skills/req-parse]

The main agent CANNOT call parse_docx_outline directly — it must delegate to
the req-parse subagent via the task() tool. This ensures tool-skill binding.

Usage:
  python -X utf8 scenario_test.py
  python -X utf8 scenario_test.py --docx path/to/file.docx
  python -X utf8 scenario_test.py --synthetic
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import textwrap
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Environment & paths
# ═══════════════════════════════════════════════════════════════════════════════

def load_dotenv() -> None:
    from dotenv import load_dotenv as _load
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print("[WARN] .env not found.")
        return
    _load(env_path)
    masked = os.getenv("LLM_API_KEY", "")
    display = masked[:8] + "..." if len(masked) > 8 else "(empty)"
    print(f"[OK] Loaded .env  (model={os.getenv('LLM_MODEL_ID', '?')}, key={display})")


def find_docx() -> str | None:
    from utils.paths import data_paths
    req_dir = data_paths.shared_req_dir()
    if not req_dir.exists():
        return None
    for f in sorted(req_dir.iterdir()):
        if f.suffix.lower() == ".docx" and not f.name.startswith("~"):
            return str(f)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Synthetic test document
# ═══════════════════════════════════════════════════════════════════════════════

def create_test_docx() -> str:
    from docx import Document
    doc = Document()
    for name, size in [("Heading 1", 16), ("Heading 2", 14), ("Heading 3", 13)]:
        try:
            style = doc.styles[name]
            style.font.size = doc.shared.Pt(size)
            style.font.bold = True
        except Exception:
            pass

    doc.add_heading("XX电商平台用户需求规格说明书", level=1)
    doc.add_paragraph(
        "本文档描述了XX电商平台V3.0版本的核心用户需求，涵盖用户管理、订单系统、"
        "商品管理三大核心模块。系统建设目标：打造高性能、高可用的电商平台，支持日均百万级订单处理。"
    )

    doc.add_heading("用户管理模块", level=2)
    doc.add_paragraph("用户管理模块负责终端用户的注册、登录、权限控制、个人信息管理等功能。")

    doc.add_heading("用户注册与登录", level=3)
    doc.add_paragraph("系统应支持手机号+验证码注册方式。性能要求：注册接口响应时间小于500ms，支持1000并发。")

    doc.add_heading("角色与权限管理", level=3)
    doc.add_paragraph(
        "系统应支持基于RBAC的角色权限控制，权限粒度需达到按钮级别。"
        "默认提供管理员、运营、普通用户三种角色。"
    )

    doc.add_heading("订单系统模块", level=2)
    doc.add_paragraph("订单系统负责用户下单、支付、物流追踪、退换货等全生命周期管理。")

    doc.add_heading("购物车与订单创建", level=3)
    doc.add_paragraph("下单接口响应时间小于200ms，支持5000并发。超卖场景需通过分布式锁保障库存一致性。")

    doc.add_heading("订单支付", level=3)
    doc.add_paragraph("支持微信支付、支付宝、银行卡三种支付方式。支付超时时间15分钟。")

    doc.add_heading("商品管理模块", level=2)
    doc.add_paragraph("商品管理模块包括商品发布、SKU管理、库存管理、商品搜索等功能。")

    doc.add_heading("SKU与库存管理", level=3)
    doc.add_paragraph(
        "每个商品可配置多个SKU，每个SKU独立管理库存和价格。"
        "库存变更需记录日志，支持低库存预警（阈值默认10件）。"
    )

    out_path = PROJECT_ROOT / "data" / "test_req.docx"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    print(f"[OK] Generated test document: {out_path}  ({out_path.stat().st_size} bytes)")
    return str(out_path)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Model & agent
# ═══════════════════════════════════════════════════════════════════════════════

def build_model():
    from langchain_openai import ChatOpenAI

    model_id = os.getenv("LLM_MODEL_ID", "deepseek-v4-flash")
    api_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    timeout = int(os.getenv("LLM_TIMEOUT", "180"))

    if not api_key:
        print("[WARN] LLM_API_KEY not set — agent will fail at runtime")

    extra_body = {}
    if "deepseek" in model_id.lower():
        extra_body = {"thinking": {"type": "disabled"}}

    return ChatOpenAI(
        model=model_id,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=1,
        temperature=0.0,
        max_tokens=8192,
        extra_body=extra_body,
    )


def build_agent():
    from deepagents import create_deep_agent
    from deepagents.middleware.subagents import SubAgent
    from langgraph.checkpoint.memory import MemorySaver

    from middleware.logging_middleware import InvocationLoggingHandler
    from models.reqmgmt import init_db
    from tools import parse_docx_outline, extract_entities, store_entities
    from tools.sql_query import create_sql_query_tool

    init_db()
    print("[OK] Database initialized (SQLAlchemy ORM)")

    model = build_model()
    checkpointer = MemorySaver()
    query_reqmgmt = create_sql_query_tool()

    skills_dir = str(PROJECT_ROOT / "skills" / "req-parse")
    memory_file = str(PROJECT_ROOT / "memory" / "AGENTS.md")

    # ── SubAgent: req-parse — owns document parsing tools + skill ──
    req_parse_subagent = SubAgent(
        name="req-parse",
        description=(
            "Parse requirement documents (.docx files) and extract structured entities. "
            "Handles: reading Word documents, extracting products/requirement_models/"
            "requirement_items from heading structure, and storing them to the database. "
            "Use this when the user asks to parse, extract, or store requirements from a document."
        ),
        system_prompt=(
            "You are a document parsing specialist. Follow the req-parse skill precisely:\n"
            "1. Call parse_docx_outline to get the structured outline from the .docx file\n"
            "2. Call extract_entities with the llm_structure to classify sections into entity types\n"
            "3. Call store_entities to persist all extracted entities to the database\n"
            "Report the final counts (products, models, items) to the main agent."
        ),
        tools=[parse_docx_outline, extract_entities, store_entities],
        skills=[skills_dir],
    )

    # ── Main agent: general-purpose tools only, delegates doc parsing ──
    agent = create_deep_agent(
        model=model,
        tools=[query_reqmgmt],
        system_prompt=(
            "You are a requirements management assistant.\n\n"
            "## Capabilities\n"
            "- **Document parsing**: Delegate to the 'req-parse' subagent via the task tool. "
            "Always use this for any document parsing/extraction/storage tasks.\n"
            "- **Database queries**: Use the query_reqmgmt tool for natural language SQL queries.\n\n"
            "## Working style\n"
            "- When the user asks to parse a document, ALWAYS delegate to the req-parse subagent\n"
            "- When the user asks database questions, use query_reqmgmt directly\n"
            "- Be precise about counts and relationships"
        ),
        middleware=[InvocationLoggingHandler.as_middleware()],
        subagents=[req_parse_subagent],
        memory=[memory_file],
        checkpointer=checkpointer,
    )

    print(f"[OK] Agent created (model={model.model_name})")
    print(f"       Main tools: [query_reqmgmt]")
    print(f"       SubAgent 'req-parse' tools: [parse_docx_outline, extract_entities, store_entities]")
    print(f"       Skill: skills/req-parse/")
    return agent, checkpointer


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Test runner
# ═══════════════════════════════════════════════════════════════════════════════

def _make_log_filter():
    """Return a stdout wrapper that suppresses [LOG] lines."""
    _real_stdout = sys.stdout
    return type("_LogFilter", (object,), {
        "write": lambda self, s: _real_stdout.write(s) if "[LOG]" not in str(s) else None,
        "flush": lambda self: _real_stdout.flush(),
        "__getattr__": lambda self, k: getattr(_real_stdout, k),
    })()


async def run_turn(agent, thread_id: str, user_message: str, turn_label: str, max_steps: int = 30) -> dict:
    config = {"configurable": {"thread_id": thread_id}}

    print(f"\n{'=' * 60}")
    print(f"  {turn_label}")
    print(f"  User: {user_message[:120]}{'...' if len(user_message) > 120 else ''}")
    print(f"{'=' * 60}")

    all_messages: list = []
    tool_calls_found: list[str] = []
    response_text = ""
    step = 0
    turn_start = time.time()

    # Suppress [LOG] spam during streaming
    sys.stdout = _make_log_filter()

    print(f"\n  [Streaming — max {max_steps} steps]\n")

    input_data = {"messages": [{"role": "user", "content": user_message}]}

    try:
        async for chunk in agent.astream(input_data, config=config, stream_mode="updates"):
            step += 1
            if step > max_steps:
                print(f"  [HALT] Max steps ({max_steps}) reached")
                break

            ts = time.strftime("%H:%M:%S")
            elapsed = time.time() - turn_start

            for node_name, node_output in chunk.items():
                if node_output is None:
                    continue
                if "messages" not in node_output:
                    continue

                msgs = node_output["messages"]
                if hasattr(msgs, "value"):
                    msgs = msgs.value
                if not isinstance(msgs, (list, tuple)):
                    msgs = [msgs]

                show_header = False
                for msg in msgs:
                    if msg not in all_messages:
                        if not show_header:
                            print(f"  [{ts}] Step {step} (+{elapsed:.1f}s) | Node: {node_name}")
                            show_header = True
                        all_messages.append(msg)
                        msg_type = getattr(msg, "type", "?")

                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                name = tc.get("name", "unknown")
                                args = tc.get("args", {})
                                tool_calls_found.append(name)
                                args_str = json.dumps(args, ensure_ascii=False, indent=2)
                                if len(args_str) > 500:
                                    args_str = args_str[:500] + "\n    ..."
                                print(f"      [TOOL CALL] {name}")
                                print(f"      Args:\n{textwrap.indent(args_str, '        ')}")

                        elif msg_type == "ai":
                            content = str(getattr(msg, "content", ""))
                            if content:
                                if len(content) > 400:
                                    content = content[:400] + "..."
                                print(f"      [AI] {content}")

                        elif msg_type == "tool":
                            content = str(getattr(msg, "content", ""))
                            tool_name = getattr(msg, "name", "?")
                            if len(content) > 400:
                                content = content[:400] + "..."
                            print(f"      [TOOL RESULT] {tool_name}: {content}")

                print()
    except Exception as e:
        print(f"\n  [ERROR] Turn failed after {time.time() - turn_start:.1f}s: {e}")
        import traceback
        traceback.print_exc()
        return {"response": f"ERROR: {e}", "tools": tool_calls_found}
    finally:
        sys.stdout = sys.__stdout__  # Restore real stdout

    elapsed_total = time.time() - turn_start
    print(f"  [DONE] Turn completed in {elapsed_total:.1f}s ({step} steps)")

    for msg in reversed(all_messages):
        if getattr(msg, "type", "") == "ai":
            content = str(getattr(msg, "content", ""))
            if content:
                response_text = content
                break

    print(f"  Tools called: {tool_calls_found if tool_calls_found else '(none)'}")
    print(f"\n  Final response preview:\n{textwrap.indent(response_text[:800], '    ')}")
    if len(response_text) > 800:
        print(f"    ... (truncated, total {len(response_text)} chars)")

    return {"response": response_text, "tools": tool_calls_found}


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Verification
# ═══════════════════════════════════════════════════════════════════════════════

def verify_database() -> dict:
    import sqlite3
    from utils.paths import data_paths

    db_path = data_paths.reqmgmt_db()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    products = [dict(r) for r in conn.execute("SELECT * FROM products").fetchall()]
    models = [dict(r) for r in conn.execute("SELECT * FROM requirement_models").fetchall()]
    items = [dict(r) for r in conn.execute("SELECT * FROM requirement_items").fetchall()]

    # FK integrity check
    orphans = conn.execute("""
        SELECT ir.id, ir.rm_id FROM requirement_items ir
        LEFT JOIN requirement_models rm ON ir.rm_id = rm.id
        WHERE rm.id IS NULL AND ir.rm_id IS NOT NULL AND ir.rm_id != ''
    """).fetchall()

    conn.close()

    return {
        "product_count": len(products),
        "model_count": len(models),
        "item_count": len(items),
        "products": products,
        "models": models,
        "items": items,
        "orphan_items": len(orphans),
    }


def verify_subagent_used(tool_calls_by_turn: dict[str, list[str]]) -> dict:
    """Check that main agent delegated to sub-agent via 'task' tool."""
    checks = {}
    for turn_label, tools in tool_calls_by_turn.items():
        checks[f"{turn_label}_used_task"] = "task" in tools
    return checks


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Main
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="DeepMind — deepagents SubAgent validation")
    parser.add_argument("--docx", help="Path to a .docx file to parse")
    parser.add_argument("--synthetic", action="store_true", help="Generate a synthetic test document")
    args = parser.parse_args()

    import logging
    logging.disable(logging.CRITICAL)

    print("=" * 60)
    print("  DeepMind — deepagents SubAgent Pattern")
    print("  Main: [query_reqmgmt]  |  SubAgent 'req-parse': [parse_docx_outline, extract_entities, store_entities]")
    print("=" * 60)

    load_dotenv()

    # ── Document selection ──
    if args.docx:
        docx_path = args.docx
        if not Path(docx_path).exists():
            print(f"[FAIL] Document not found: {docx_path}")
            sys.exit(1)
        print(f"[OK] Using specified document: {docx_path}")
    elif args.synthetic:
        docx_path = create_test_docx()
    else:
        docx_path = find_docx()
        if docx_path:
            print(f"[OK] Using shared document: {docx_path}")
        else:
            print("[INFO] No shared docs found, generating synthetic test doc.")
            docx_path = create_test_docx()

    # ── Agent ──
    agent, checkpointer = build_agent()
    thread_id = f"test-{int(time.time())}"

    # ── Turn 1: Parse & Store (→ SubAgent 'req-parse') ──
    turn1_msg = (
        f"请解析需求文档 `{docx_path}`，提取其中的产品(product)、"
        f"需求模型(requirement_model)和用户需求项(requirement_item)，然后存储到数据库。"
        f"完成后报告入库结果。"
    )
    turn1 = await run_turn(agent, thread_id, turn1_msg, "Turn 1: Parse & Store (→ SubAgent 'req-parse')")

    # ── Turn 2: Query & Filter (→ query_reqmgmt) ──
    turn2_msg = (
        "使用 query_reqmgmt 工具查询："
        "数据库里一共有多少个产品(product)？"
        "列出所有需求模型(requirement_model)的名称和类型。"
        "每个需求模型下各有多少需求项(requirement_item)？按数量从多到少排列。"
        "另外，把标题或描述中包含'性能'关键词的需求项筛选出来。"
    )
    turn2 = await run_turn(agent, thread_id, turn2_msg, "Turn 2: Query & Filter (→ query_reqmgmt)")

    # ── Turn 3: Contextual follow-up (cross-turn memory) ──
    turn3_msg = (
        "回到我们在第一轮解析的那个文档，其中'订单系统模块'下面有几个需求项？"
        "列出它们的具体内容。"
    )
    turn3 = await run_turn(agent, thread_id, turn3_msg, "Turn 3: Contextual Follow-up (Cross-turn)")

    # ── Turn 4: Idempotency — re-parse same document, counts should be unchanged ──
    turn4_msg = (
        f"请再次解析同一个文档 `{docx_path}`，提取实体并存储到数据库。"
        f"存储使用幂等逻辑（INSERT OR REPLACE），入库数量应和第一次相同。"
    )
    turn4 = await run_turn(agent, thread_id, turn4_msg, "Turn 4: Idempotency (→ SubAgent 'req-parse')")

    # ── Verification ──
    print(f"\n{'=' * 60}")
    print("  Verification Summary")
    print(f"{'=' * 60}")

    tool_calls_by_turn = {
        "turn1": turn1["tools"],
        "turn2": turn2["tools"],
        "turn3": turn3["tools"],
        "turn4": turn4["tools"],
    }

    # DB ground truth
    db_state = verify_database()
    print(f"\n  [Database Ground Truth]")
    print(f"    products:           {db_state['product_count']}")
    print(f"    requirement_models: {db_state['model_count']}")
    print(f"    requirement_items:  {db_state['item_count']}")
    print(f"    orphan_items (FK):  {db_state['orphan_items']}")
    for m in db_state["models"]:
        print(f"      {m['name']}")
    for it in db_state["items"]:
        print(f"      {it['name']}: {it['title']}")

    # SubAgent delegation check
    subagent_checks = verify_subagent_used(tool_calls_by_turn)
    print(f"\n  [SubAgent Delegation]")
    for check, passed in subagent_checks.items():
        print(f"    {check}: {'PASS' if passed else 'FAIL'}")

    # Tool calls summary
    print(f"\n  [Tool Calls]")
    for turn_label, tools in tool_calls_by_turn.items():
        print(f"    {turn_label}: {tools}")

    # ── Pass/fail criteria ──
    all_pass = True

    criteria = [
        (db_state["product_count"] >= 1, "At least 1 product extracted"),
        (db_state["model_count"] >= 2, "At least 2 requirement_models extracted"),
        (db_state["item_count"] >= 5, "At least 5 requirement_items extracted"),
        (db_state["orphan_items"] == 0, "No orphan items (FK integrity)"),
        (subagent_checks.get("turn1_used_task", False), "Turn 1 delegated to SubAgent (task tool)"),
        (subagent_checks.get("turn4_used_task", False), "Turn 4 delegated to SubAgent (task tool)"),
        ("query_reqmgmt" in tool_calls_by_turn.get("turn2", []), "Turn 2 used query_reqmgmt"),
    ]

    print(f"\n  [Pass/Fail Criteria]")
    for passed, desc in criteria:
        mark = "PASS" if passed else "FAIL"
        print(f"    {mark}: {desc}")
        if not passed:
            all_pass = False

    print(f"\n  {'=' * 60}")
    if all_pass:
        print("  ALL CHECKS PASSED")
    else:
        print("  SOME CHECKS FAILED — review above")
    print(f"  {'=' * 60}")

    if not all_pass:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
