"""
Invocation logging — LangChain BaseCallbackHandler + AgentMiddleware bridge.

- Standalone LangGraph graphs: pass via config={"callbacks": [handler]}
- deepagents: wrap via AgentMiddleware bridge (or pass in config)

Logs to data/logs/<thread_id>_<timestamp>.json + console summary.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.messages import AIMessage, ToolMessage
from langchain_core.callbacks import BaseCallbackHandler
from langgraph.config import get_config
from langgraph.runtime import Runtime
from utils.paths import data_paths

logger = logging.getLogger(__name__)

_PFX = "[LOG]"
_SEP_LIGHT = "─" * 56
_SEP_HEAVY = "━" * 56


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def _p(line: str = "") -> None:
    """Print with log prefix. 安全写入 stdout，忽略编码错误。"""
    if not line:
        print()
        return
    try:
        print(f"{_PFX} {line}")
    except UnicodeEncodeError:
        print(f"{_PFX} {line.encode('ascii', errors='replace').decode('ascii')}")


# ═══════════════════════════════════════════════════════════════════════════════
# BaseCallbackHandler — works with any LangGraph/LangChain invoke
# ═══════════════════════════════════════════════════════════════════════════════

class InvocationLoggingHandler(BaseCallbackHandler):
    """Log every langgraph invocation to file + console.

    Usage — standalone graph::

        agent = ReqMgmtText2SQLAgent()
        handler = InvocationLoggingHandler(log_dir="data/logs")
        result = await agent.query("...", callbacks=[handler])

    Usage — deepagents::

        agent = create_deep_agent(
            model=model, tools=[...],
            middleware=[InvocationLoggingHandler.as_middleware()],
        )
    """

    def __init__(self, log_dir: str | None = None) -> None:
        super().__init__()
        self._log_dir = Path(log_dir) if log_dir else data_paths.logs_dir()
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._start: float = 0.0
        self._record: dict[str, Any] = {}
        self._tool_runs: dict[UUID, dict[str, Any]] = {}

    # ── chain = top-level graph invoke ────────────────────────────────────

    def on_chain_start(self, serialized: dict[str, Any], inputs: dict[str, Any],
                       *, run_id: UUID, parent_run_id: UUID | None = None,
                       tags: list[str] | None = None,
                       metadata: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._start = time.monotonic()
        self._tool_runs = {}

        # Extract user_input from graph state
        user_input = ""
        if isinstance(inputs, dict):
            user_input = str(inputs.get("user_input", inputs.get("input", "")))
        if not user_input and "messages" in (inputs or {}):
            msgs = inputs["messages"]
            if msgs:
                last = msgs[-1]
                user_input = str(getattr(last, "content", "")) if hasattr(last, "content") else str(last)

        # Extract thread_id from metadata
        thread_id = ""
        if metadata and isinstance(metadata, dict):
            thread_id = metadata.get("thread_id", "")

        self._record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "thread_id": thread_id,
            "user_input": user_input[:2000],
            "llm_calls": [],
            "tool_executions": [],
            "error": None,
        }

        _p(f"{_SEP_HEAVY}")
        _p(f"[{_ts()}] Invocation start  (thread={thread_id or '?'})")
        _p(f"  Input: {user_input[:150]}{'…' if len(user_input) > 150 else ''}")
        _p(_SEP_HEAVY)

    def on_chain_end(self, outputs: Any, *, run_id: UUID,
                     parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        elapsed = (time.monotonic() - self._start) * 1000

        # outputs can be str or dict depending on LangChain version / graph type
        out_dict: dict[str, Any] = outputs if isinstance(outputs, dict) else {}

        # Collect tool results from callback trackers + state collection
        state_executions = self._record.get("tool_executions", [])
        callback_executions = list(self._tool_runs.values())
        # Merge: callback trackers take priority, state appends
        merged_executions = callback_executions + [e for e in state_executions if e not in callback_executions]
        self._record["tool_executions"] = merged_executions
        self._record["elapsed_ms"] = round(elapsed, 1)
        self._record["llm_turns_count"] = len(self._record["llm_calls"])
        self._record["tool_executions_count"] = len(merged_executions)

        # Final response
        self._record["final_response_preview"] = str(out_dict.get("answer", ""))[:500]

        # Error
        error = out_dict.get("error", "")
        if error:
            self._record["error"] = str(error)[:500]

        # ── Write JSON ──
        tid = self._record["thread_id"] or "unknown"
        ts_file = time.strftime("%Y%m%d_%H%M%S")
        log_path = self._log_dir / f"{tid}_{ts_file}.json"
        log_path.write_text(json.dumps(self._record, ensure_ascii=False, indent=2),
                            encoding="utf-8")

        # ── Console ──
        tool_names = [t["name"] for t in self._record["tool_executions"]]
        _p()
        _p(_SEP_LIGHT)
        _p(f"[{_ts()}] Invocation complete  ({elapsed:.0f}ms)")
        _p(f"  LLM calls:    {self._record['llm_turns_count']}")
        _p(f"  Tools called: {tool_names if tool_names else '(none)'}")
        _p(f"  Log:          {log_path.name}")
        if error:
            _p(f"  Error:        {error[:120]}")
        answer = self._record["final_response_preview"]
        if answer:
            preview = answer[:250].replace("\n", "\n    ")
            _p(f"  Response: {preview}{'…' if len(answer) > 250 else ''}")
        _p(_SEP_LIGHT)

    def on_chain_error(self, error: BaseException, *, run_id: UUID,
                       parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        self._record["error"] = str(error)[:500]
        _p()
        _p(f"[{_ts()}] Error: {error}")

    # ── LLM calls ─────────────────────────────────────────────────────────

    def on_chat_model_start(self, serialized: dict[str, Any],
                            messages: list[list[Any]], *, run_id: UUID,
                            parent_run_id: UUID | None = None,
                            tags: list[str] | None = None,
                            metadata: dict[str, Any] | None = None,
                            **kwargs: Any) -> None:
        self._record.setdefault("llm_calls", []).append({
            "run_id": str(run_id),
            "messages_count": sum(len(batch) for batch in messages),
            "content_preview": "",
            "content_length": 0,
            "tool_calls_requested": [],
        })

    def on_chat_model_end(self, response: Any, *, run_id: UUID,
                          parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        # Update the last LLM call record
        for entry in reversed(self._record.get("llm_calls", [])):
            if entry.get("run_id") == str(run_id):
                msg = getattr(response, "generations", [[None]])[0][0]
                if msg is None and hasattr(response, "message"):
                    msg = response.message
                if msg is not None:
                    content = str(getattr(msg, "content", ""))
                    entry["content_preview"] = content[:300]
                    entry["content_length"] = len(content)
                    tcs = getattr(msg, "tool_calls", None) or []
                    entry["tool_calls_requested"] = [tc.get("name", "?") for tc in tcs]

                    tc_str = f" → tools: {entry['tool_calls_requested']}" if entry["tool_calls_requested"] else ""
                    _p()
                    _p(f"[{_ts()}] LLM #{len(self._record['llm_calls'])}{tc_str}")
                    if content.strip():
                        preview = content[:200].replace("\n", " ")
                        _p(f"  {preview}{'…' if len(content) > 200 else ''}")
                break

    # ── Tool calls ────────────────────────────────────────────────────────

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, *,
                      run_id: UUID, parent_run_id: UUID | None = None,
                      tags: list[str] | None = None,
                      metadata: dict[str, Any] | None = None,
                      inputs: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._tool_runs[run_id] = {
            "name": serialized.get("name", "?"),
            "input": str(inputs if inputs else input_str)[:500],
            "output": "",
        }

    def on_tool_end(self, output: Any, *, run_id: UUID,
                    parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        if run_id in self._tool_runs:
            self._tool_runs[run_id]["output"] = str(output)[:500]

    # ── bridge: as AgentMiddleware for deepagents ─────────────────────────

    @classmethod
    def as_middleware(cls, log_dir: str | None = None) -> AgentMiddleware:
        """Return an AgentMiddleware that delegates to this handler.

        Use when you want the same logging for deepagents invocations::

            agent = create_deep_agent(
                middleware=[InvocationLoggingHandler.as_middleware()],
            )
        """
        handler = cls(log_dir=log_dir)

        class _Bridge(AgentMiddleware):
            def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
                user_input = ""
                msgs = state.get("messages", [])
                for m in reversed(msgs):
                    if hasattr(m, "type") and m.type == "human":
                        user_input = str(getattr(m, "content", ""))
                        break
                config = get_config()
                tid = config.get("configurable", {}).get("thread_id", "")
                handler.on_chain_start(
                    {}, {"user_input": user_input},
                    run_id=UUID(int=0), metadata={"thread_id": tid},
                )
                return None

            async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
                return self.before_agent(state, runtime)

            def after_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
                self._collect_from_state(state)
                answer = ""
                # 优先找最后一条无 tool_calls 的 AIMessage（LLM 最终回复）
                for msg in reversed(state.get("messages", [])):
                    if isinstance(msg, AIMessage):
                        tc = getattr(msg, "tool_calls", None) or []
                        if not tc and getattr(msg, "content", ""):
                            answer = str(getattr(msg, "content", ""))
                            break
                # 回退：取最后一条有内容的 ToolMessage（工具直接产出）
                if not answer:
                    for msg in reversed(state.get("messages", [])):
                        if isinstance(msg, ToolMessage) and getattr(msg, "content", ""):
                            answer = str(getattr(msg, "content", ""))[:2000]
                            break
                handler.on_chain_end({"answer": answer, "messages": state.get("messages", [])}, run_id=UUID(int=0))
                return None

            async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
                return self.after_agent(state, runtime)

            @staticmethod
            def _collect_from_state(state: AgentState) -> None:
                """从 state.messages 中提取 LLM 调用和工具执行记录。"""
                msgs = state.get("messages", [])
                seen_tc_ids: set[str] = set()

                for i, msg in enumerate(msgs):
                    msg_type = type(msg).__name__
                    if isinstance(msg, AIMessage):
                        tc_list = getattr(msg, "tool_calls", None) or []
                        tc_names = [tc.get("name", "?") for tc in tc_list]
                        handler._record.setdefault("llm_calls", []).append({
                            "run_id": str(getattr(msg, "id", "")),
                            "messages_count": 1,
                            "content_preview": str(getattr(msg, "content", ""))[:300],
                            "content_length": len(str(getattr(msg, "content", ""))),
                            "tool_calls_requested": tc_names,
                        })
                        # Collect tool results matching this AIMessage's tool_calls
                        for tc in tc_list:
                            tc_id = tc.get("id", "")
                            seen_tc_ids.add(tc_id)
                            output = ""
                            for j in range(i + 1, len(msgs)):
                                fm = msgs[j]
                                if hasattr(fm, "tool_call_id") and getattr(fm, "tool_call_id", "") == tc_id:
                                    output = str(getattr(fm, "content", ""))[:500]
                                    break
                            handler._record.setdefault("tool_executions", []).append({
                                "name": tc.get("name", "?"),
                                "input": json.dumps(tc.get("args", {}) if isinstance(tc, dict) else {}, ensure_ascii=False)[:500],
                                "output": output[:500],
                            })
                    elif hasattr(msg, "tool_call_id") and getattr(msg, "tool_call_id", "") not in seen_tc_ids:
                        handler._record.setdefault("tool_executions", []).append({
                            "name": getattr(msg, "name", "?"),
                            "input": "",
                            "output": str(getattr(msg, "content", ""))[:500],
                        })


        return _Bridge()


# backward-compat alias
InvocationLoggingMiddleware = InvocationLoggingHandler.as_middleware

