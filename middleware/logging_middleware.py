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
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

_PFX = "[LOG]"
_SEP_LIGHT = "─" * 56
_SEP_HEAVY = "━" * 56


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def _p(line: str = "") -> None:
    """Print with log prefix."""
    print(f"{_PFX} {line}" if line else "")


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

    def __init__(self, log_dir: str = "data/logs") -> None:
        super().__init__()
        self._log_dir = Path(log_dir)
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

        # Collect tool results
        self._record["tool_executions"] = list(self._tool_runs.values())
        self._record["elapsed_ms"] = round(elapsed, 1)
        self._record["llm_turns_count"] = len(self._record["llm_calls"])
        self._record["tool_executions_count"] = len(self._tool_runs)

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
    def as_middleware(cls, log_dir: str = "data/logs") -> AgentMiddleware:
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
                tid = ""
                cfg = getattr(runtime, "config", {}) or {}
                if isinstance(cfg, dict):
                    tid = cfg.get("configurable", {}).get("thread_id", "")
                handler.on_chain_start(
                    {}, {"user_input": user_input},
                    run_id=UUID(int=0), metadata={"thread_id": tid},
                )
                return None

            async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
                return self.before_agent(state, runtime)

            def after_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
                # Gather tool messages + final response
                tool_outputs = {}
                for msg in state.get("messages", []):
                    if isinstance(msg, ToolMessage):
                        tool_outputs[getattr(msg, "tool_call_id", "")] = str(getattr(msg, "content", ""))[:500]
                # Update tool runs
                for r in handler._tool_runs.values():
                    for tc_id, out in tool_outputs.items():
                        if not r["output"]:
                            r["output"] = out
                            break
                # Final response
                answer = ""
                for msg in reversed(state.get("messages", [])):
                    if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                        answer = str(getattr(msg, "content", ""))
                        break
                handler.on_chain_end({"answer": answer, "messages": state.get("messages", [])}, run_id=UUID(int=0))
                return None

            async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
                return self.after_agent(state, runtime)

        return _Bridge()


# backward-compat alias
InvocationLoggingMiddleware = InvocationLoggingHandler.as_middleware

