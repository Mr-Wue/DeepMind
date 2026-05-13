"""
tools/mcp_client.py

LangChain MCP (Model Context Protocol) Client 封装。

通过 SSE 协议连接智谱 Web Search MCP 服务器，将 MCP 工具转换为 LangChain BaseTool。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """单个 MCP 服务器配置。"""

    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout: int = 30
    transport: str = "http"


class MCPClient:
    """MCP Client 封装，通过 langchain-mcp-adapters 连接 MCP 服务器。

    用法::

        client = MCPClient.from_config({"mcpServers": {...}})
        tools = await client.get_tools()
        result = await client.acall_tool("webSearchPro", {"search_query": "..."})
        client.close()
    """

    def __init__(self, servers: list[MCPServerConfig], headers: dict[str, str] | None = None) -> None:
        self.servers = servers
        self._global_headers = headers or {}
        self._adapter_clients: list[MultiServerMCPClient] = []
        self._tools_cache: list[BaseTool] | None = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> MCPClient:
        """从标准 MCP 配置 dict 初始化。

        config 格式: {"mcpServers": {"name": {"url": "...", "headers": {...}}}}
        """
        servers: list[MCPServerConfig] = []
        for name, server_cfg in config.get("mcpServers", {}).items():
            url = server_cfg.get("url", "")
            if not url:
                logger.warning("[MCPClient] server %s 缺少 url，跳过", name)
                continue
            servers.append(
                MCPServerConfig(
                    name=name,
                    url=url,
                    headers=server_cfg.get("headers", {}),
                    timeout=server_cfg.get("timeout", 30),
                )
            )
        return cls(servers=servers)

    def _ensure_connected(self) -> None:
        if self._adapter_clients:
            return
        clients: dict[str, dict[str, Any]] = {}
        for server in self.servers:
            client_kwargs: dict[str, Any] = {
                "url": server.url,
                "timeout": server.timeout,
                "transport": server.transport,
            }
            merged_headers = {**self._global_headers, **server.headers}
            if merged_headers:
                client_kwargs["headers"] = merged_headers
            clients[server.name] = client_kwargs
        self._adapter_clients = [MultiServerMCPClient(clients)]

    async def get_tools(self) -> list[BaseTool]:
        self._ensure_connected()
        if self._tools_cache is None:
            self._tools_cache = await self._adapter_clients[0].get_tools()
        return self._tools_cache

    async def acall_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        tools = await self.get_tools()
        tool_map = {t.name: t for t in tools}
        if tool_name not in tool_map:
            available = [t.name for t in tools]
            raise ValueError(f"未找到 MCP 工具: {tool_name!r}，可用: {available}")
        return await tool_map[tool_name].ainvoke(arguments)

    def close(self) -> None:
        for client in self._adapter_clients:
            if hasattr(client, "close"):
                client.close()
        self._adapter_clients.clear()
        self._tools_cache = None

    def __del__(self) -> None:
        if self._adapter_clients:
            self.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 智谱 Web Search MCP 配置
# ═══════════════════════════════════════════════════════════════════════════════


def get_zhipu_mcp_config() -> dict[str, Any]:
    """从 .env 读取 ZHIPU_API_KEY，组装智谱 MCP 标准配置。"""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    key = os.getenv("ZHIPU_API_KEY", "").strip()
    if not key:
        raise ValueError("未配置智谱 MCP：请在 .env 中设置 ZHIPU_API_KEY")
    return {
        "mcpServers": {
            "zhipu-web-search-sse": {
                "url": f"https://open.bigmodel.cn/api/mcp-broker/proxy/web-search/mcp?Authorization={key}"
            }
        }
    }
