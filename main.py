"""
DeepMind 一键启动入口。

    python main.py

同时启动：
  - FastAPI 后端 (port 8000) — LangGraph Agent + AG-UI 端点
  - Next.js 前端 (port 3000)   — CopilotKit Chat UI

前后端均以子进程运行，主进程退出后子进程自动终止。Ctrl+C 停止所有服务。
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _npm() -> str:
    return "npm.cmd" if sys.platform == "win32" else "npm"


# ── FastAPI ────────────────────────────────────────────────────────────────

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="DeepMind")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    """在 uvicorn event loop 中初始化 Agent，避免跨 event loop 问题。"""
    from agents.init import init_deepmind
    from agents.deep_agent import create_deepmind_agent
    from ag_ui_langgraph import add_langgraph_fastapi_endpoint
    from copilotkit import LangGraphAGUIAgent

    config = await init_deepmind()
    agent = create_deepmind_agent(config)

    # 将 memory_ctx 挂到 app.state 上，防止 startup 结束后被垃圾回收导致 SQLite 连接关闭
    app.state.memory_ctx = config.memory_ctx

    add_langgraph_fastapi_endpoint(
        app=app,
        agent=LangGraphAGUIAgent(
            name="deepmind",
            description="需求管理智能助手",
            graph=agent,
        ),
        path="/copilotkit",
    )
    print("[DeepMind] Agent 初始化完成 → /copilotkit")


@app.on_event("shutdown")
async def shutdown():
    """关闭 SQLite 连接。"""
    from agents.init import cleanup_deepmind
    from agents.init import DeepMindConfig

    # 从 app.state 取出 memory_ctx 构造临时 config 用于清理
    if hasattr(app.state, "memory_ctx") and app.state.memory_ctx:
        await cleanup_deepmind(DeepMindConfig(
            store=None,
            checkpointer=None,
            backend=None,
            memory_ctx=app.state.memory_ctx,
        ))
        print("[DeepMind] SQLite 连接已关闭")


@app.get("/health")
def health():
    return {"status": "ok"}


# ── 子进程管理 ─────────────────────────────────────────────────────────────

def _terminate_proc(proc: subprocess.Popen, name: str) -> None:
    """终止一个子进程，先温和再强制。"""
    if proc.poll() is not None:
        return
    print(f"\n[DeepMind] 停止 {name} (pid={proc.pid})…")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print(f"[DeepMind] {name} 未响应，强制终止…")
        proc.kill()
        proc.wait()


def main():
    procs: list[tuple[subprocess.Popen, str]] = []

    def _cleanup():
        for p, name in reversed(procs):
            _terminate_proc(p, name)

    atexit.register(_cleanup)

    def _on_signal(sig, frame):
        del sig, frame
        _cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    # 启动后端子进程 — 使用 uvicorn.run() 程序化调用，保持与原代码一致
    backend = subprocess.Popen(
        [
            sys.executable, "-c",
            "from main import app; "
            "import uvicorn; "
            "uvicorn.run(app, host='127.0.0.1', port=8000, log_level='info')",
        ],
        cwd=str(ROOT),
    )
    procs.append((backend, "后端"))

    # 启动前端子进程（Next.js dev server）
    frontend = subprocess.Popen(
        [_npm(), "run", "dev"],
        cwd=str(ROOT / "frontend"),
    )
    procs.append((frontend, "前端"))

    print("=" * 50)
    print("  DeepMind 已启动")
    print("  前端 : http://127.0.0.1:3000")
    print("  后端 : http://127.0.0.1:8000")
    print("  Ctrl+C 停止所有服务")
    print("=" * 50)

    # 等待任一子进程退出或 KeyboardInterrupt
    try:
        while True:
            for p, name in procs:
                if p.poll() is not None:
                    print(f"\n[DeepMind] {name} 意外退出 (code={p.returncode})")
                    _cleanup()
                    sys.exit(1)
            time.sleep(0.5)
    except KeyboardInterrupt:
        _cleanup()


if __name__ == "__main__":
    main()
