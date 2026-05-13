"""
Memory Backend — 构建 Agent 虚拟文件系统的 CompositeBackend。

职责单一：只负责 Backend 路由构建，不涉及 Store/Checkpointer 创建或种子数据。

Agent 虚拟文件系统:

  /                   ← FilesystemBackend(PROJECT_ROOT, virtual_mode=True)
  │                     读磁盘项目文件，写入不走磁盘（virtual_mode 安全约束）
  ├── docs/           ← 可读 D:/ai/DeepMind/docs/
  ├── skills/         ← 可读 D:/ai/DeepMind/skills/
  └── ...
  /memories/          ← StoreBackend(namespace=user_id)
  │                     Agent 读写 .md 文件语义，底层存 LangGraph Store
  ├── profile.md      ← 长期记忆：用户画像
  └── knowledge.md    ← 长期记忆：积累知识

扩展性:
  新增 route 只需在 create_memory_backend() 的 routes dict 中加一行::

      routes = {
          "/memories/":  StoreBackend(...),     # 长期记忆
          "/threads/":   SomeOtherBackend(...), # 未来: 临时记忆
      }

Store / Checkpointer 的创建和生命周期管理见 memory.init 模块。
"""

from __future__ import annotations

from pathlib import Path

from deepagents.backends import CompositeBackend, StoreBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
from memory.config import get_default_user_id

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_memory_backend() -> BackendProtocol:
    """构建 CompositeBackend。

    - /           → FilesystemBackend(PROJECT_ROOT, virtual_mode=True)  项目文件
    - /memories/  → StoreBackend(namespace=user_id)                     长期记忆
    """
    return CompositeBackend(
        default=FilesystemBackend(root_dir=str(_PROJECT_ROOT), virtual_mode=True),
        routes={
            "/memories/": StoreBackend(
                namespace=lambda rt: ("user", getattr(rt.context, "user_id", None) or get_default_user_id()),
            ),
        },
    )