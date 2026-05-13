"""
Memory 配置读取 — 从 deepMind.toml 解析用户 ID、长期记忆路径等。

纯配置，无 LangGraph 依赖。下游模块（backends, init, agents）直接 import。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # pyright: ignore[reportMissingImports]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_toml() -> dict[str, Any]:
    """加载 deepMind.toml，缓存由 tomllib 保证（文件不变则结果不变）。"""
    path = _PROJECT_ROOT / "deepMind.toml"
    if not path.exists():
        raise FileNotFoundError(f"deepMind.toml not found at {path}")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def get_default_user_id() -> str:
    """TOML [user].default_id"""
    return _load_toml()["user"]["default_id"]


def get_long_term_memory_paths() -> list[str]:
    """Agent 虚拟路径列表 → create_deep_agent(memory=...)

    例: ['/memories/profile.md', '/memories/knowledge.md']
    """
    toml = _load_toml()
    vdir = toml["memory"]["long_term"]["virtual_dir"]
    files = toml["memory"]["long_term"]["files"]
    return [f"{vdir}{f}" for f in files]


def get_long_term_files() -> list[str]:
    """长期记忆文件名列表（不含虚拟路径前缀）。

    例: ['profile.md', 'knowledge.md']
    """
    return _load_toml()["memory"]["long_term"]["files"]


def get_long_term_virtual_dir() -> str:
    """长期记忆虚拟目录前缀。

    例: '/memories/'
    """
    return _load_toml()["memory"]["long_term"]["virtual_dir"]