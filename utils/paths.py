"""
共享路径工具 — 与 CodeMind 共用数据目录，支持 DATA_DIR 环境变量。

- 未设置 ``DATA_DIR``：默认 ``<工程根>/../data``（与 CodeMind 同级共享）
- ``DATA_DIR=.`` 或 ``./``：``<工程根>/data``
- 其他相对路径：相对工程根解析；绝对路径直接使用

首次导入时从工程根 ``.env`` 加载环境变量。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_STANDARD_SUBDIRS = ("logs", "output", "memory", "files")


def _ensure_data_layout(root: Path) -> None:
    root = root.resolve()
    if not root.exists():
        logger.info("数据根目录不存在，将自动创建: %s", root)
        root.mkdir(parents=True, exist_ok=True)
    for name in _STANDARD_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_data_root() -> Path:
    """返回数据根目录绝对路径（缓存一次，进程内不变）。"""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    raw = os.environ.get("DATA_DIR", "").strip()
    if not raw:
        root = (PROJECT_ROOT.parent / "data").resolve()
    elif raw in (".", "./"):
        root = (PROJECT_ROOT / "data").resolve()
    else:
        p = Path(raw).expanduser()
        root = p.resolve() if p.is_absolute() else (PROJECT_ROOT / p).resolve()
    _ensure_data_layout(root)
    return root


class DataPaths:
    """全局数据路径入口；默认单例 ``data_paths``。"""

    __slots__ = ("_base",)

    def __init__(self, base: Path | None = None) -> None:
        self._base = base

    @property
    def root(self) -> Path:
        if self._base is not None:
            r = self._base.resolve()
            _ensure_data_layout(r)
            return r
        return get_data_root()

    def subpath(self, *parts: str | Path) -> Path:
        p = self.root
        for x in parts:
            p = p / x
        return p

    def logs(self) -> Path:
        return self.subpath("logs")

    def output(self) -> Path:
        return self.subpath("output")

    def files_dir(self) -> Path:
        return self.subpath("files")

    # ── 与 CodeMind 共享的路径 ──

    def shared_docs_dir(self) -> Path:
        """共享文档目录：``<工程根>/docs``（与 CodeMind 同结构）"""
        return PROJECT_ROOT / "docs"

    def shared_req_dir(self) -> Path:
        """共享需求文档目录：``<工程根>/docs/req``"""
        return self.shared_docs_dir() / "req"

    def test_db(self) -> Path:
        """测试用 SQLite 数据库"""
        return self.subpath("test_entities.db")

    def reqmgmt_db(self) -> Path:
        """需求管理数据库（entity_store 写入 + Text2SQL 查询 共用）"""
        return self.subpath("reqmgmt", "reqmgmt.db")


data_paths = DataPaths()
