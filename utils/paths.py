"""
路径工具 — 从 deepMind.toml 读取 data_dir，统一管理所有磁盘路径。

首次导入时加载 TOML，后续全部从缓存读取。
"""

from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_STANDARD_SUBDIRS = ("logs", "output", "memory", "files")

# ── TOML 配置加载 ──────────────────────────────────────────────────────────


if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # pyright: ignore[reportMissingImports]


@lru_cache(maxsize=1)
def _load_toml() -> dict:
    path = PROJECT_ROOT / "deepMind.toml"
    if path.exists():
        return tomllib.loads(path.read_text(encoding="utf-8"))
    return {}


def _resolve_data_dir(raw: str) -> Path:
    """将 TOML 中的 data.dir 字符串解析为绝对路径。"""
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (PROJECT_ROOT / p).resolve()


def _ensure_layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in _STANDARD_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)


# ── 公开接口 ──────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_data_root() -> Path:
    """数据根目录（磁盘绝对路径）。

    优先级: TOML data.dir > 环境变量 DATA_DIR > 默认 ../data
    """
    toml = _load_toml()
    raw = toml.get("data", {}).get("dir", "").strip()
    if not raw:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        raw = os.environ.get("DATA_DIR", "").strip()
    if not raw:
        raw = "../data"
    root = _resolve_data_dir(raw)
    _ensure_layout(root)
    return root


class DataPaths:
    """磁盘路径入口，默认单例 ``data_paths``。"""

    __slots__ = ("_base",)

    def __init__(self, base: Path | None = None) -> None:
        self._base = base

    @property
    def root(self) -> Path:
        if self._base is not None:
            _ensure_layout(self._base.resolve())
            return self._base.resolve()
        return get_data_root()

    def subpath(self, *parts: str | Path) -> Path:
        p = self.root
        for x in parts:
            p = p / x
        return p

    def logs_dir(self) -> Path:
        return self.subpath("logs")

    def output_dir(self) -> Path:
        return self.subpath("output")

    def files_dir(self) -> Path:
        return self.subpath("files")

    def memory_dir(self, user_id: str = "") -> Path:
        """长期记忆磁盘目录: {data}/memory/{user_id}/"""
        p = self.subpath("memory")
        if user_id:
            p = p / user_id
        return p

    def threads_dir(self, thread_id: str = "") -> Path:
        """临时记忆磁盘目录: {data}/threads/{thread_id}/"""
        p = self.subpath("threads")
        if thread_id:
            p = p / thread_id
        return p

    def reqmgmt_db(self) -> Path:
        return self.subpath("reqmgmt", "reqmgmt.db")

    def upload_files_dir(self) -> Path:
        """上传文件暂存目录（在 PROJECT_ROOT 下，供 deepagents 虚拟文件系统访问）。"""
        p = PROJECT_ROOT / "data" / "files"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def shared_docs_dir(self) -> Path:
        return PROJECT_ROOT / "docs"

    def shared_req_dir(self) -> Path:
        return self.shared_docs_dir() / "req"

    def store_db(self) -> Path:
        """长期记忆 Store SQLite: {data}/memory/deepmind_store.db"""
        return self.memory_dir() / "deepmind_store.db"

    def checkpoint_db(self) -> Path:
        """线程 Checkpoint SQLite: {data}/memory/deepmind_checkpoints.db"""
        return self.memory_dir() / "deepmind_checkpoints.db"

    def test_db(self) -> Path:
        return self.subpath("test_entities.db")


data_paths = DataPaths()
