"""
tools/web_search.py

Web 搜索工具工厂 — deepagents 兼容。

从智谱 MCP 服务器搜索互联网，返回 Markdown 格式结果。

Usage::

    from tools.web_search import create_web_search_tool

    web_search = create_web_search_tool()
    agent = create_deep_agent(
        model=model,
        tools=[..., web_search],
    )
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

ENGINE_TOOL_MAP: dict[str, str] = {
    "search_pro":       "webSearchPro",
    "search_std":       "webSearchStd",
    "search_pro_sogou": "webSearchSogou",
    "search_pro_quark": "webSearchQuark",
}


# ── 数据结构 ──────────────────────────────────────────────────────────────────


@dataclass
class WebSearchItem:
    """单条搜索结果。"""
    title: str = ""
    content: str = ""
    link: str = ""
    media: str = ""
    publish_date: str = ""
    icon: str = ""


@dataclass
class WebSearchResult:
    """Web 搜索结果（统一格式）。"""
    items: list[WebSearchItem] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def answer(self) -> str:
        parts: list[str] = []
        for it in self.items:
            header = f"## [{it.title}]({it.link})" if it.title and it.link else (it.title or it.link)
            if it.media:
                header += f" — {it.media}"
            parts.append(header)
            if it.content:
                parts.append(it.content)
        return "\n\n".join(parts)

    @property
    def sources(self) -> list[str]:
        return [it.link for it in self.items if it.link]


# ── 内部客户端（懒加载 MCP 连接）─────────────────────────────────────────────


class _WebSearchClient:
    """Web 搜索 MCP 客户端，封装智谱搜索引擎调用和结果解析。"""

    def __init__(self, search_engine: str = "search_pro") -> None:
        if search_engine not in ENGINE_TOOL_MAP:
            raise ValueError(f"不支持的搜索引擎: {search_engine!r}，可选: {list(ENGINE_TOOL_MAP)}")
        self._search_engine = search_engine
        self._mcp_client: Any = None

    async def search(self, query: str, **kwargs: Any) -> WebSearchResult:
        if not query or not query.strip():
            return WebSearchResult()
        try:
            return await self._do_search(query.strip(), **kwargs)
        except Exception as exc:
            logger.exception("Web 搜索失败: %s", exc)
            return WebSearchResult(raw={"error": str(exc)})

    def close(self) -> None:
        if self._mcp_client:
            try:
                self._mcp_client.close()
            except Exception:
                pass
            self._mcp_client = None

    async def _do_search(self, query: str, **kwargs: Any) -> WebSearchResult:
        tool_name = ENGINE_TOOL_MAP[self._search_engine]
        client = self._get_mcp_client()

        try:
            tools = await client.get_tools()
        except Exception as exc:
            return WebSearchResult(raw={"error": f"获取搜索工具失败: {exc}"})

        tool_map = {t.name: t for t in tools}
        tool = tool_map.get(tool_name)
        if tool is None:
            return WebSearchResult(raw={"error": f"未找到工具 {tool_name}"})

        arguments: dict[str, Any] = {
            "search_query": query[:70],
            "count": kwargs.get("count", 2),
            "count_size": kwargs.get("count_size", "medium"),
        }
        for opt in ("count","count_size", "search_recency_filter", "search_domain_filter"):
            if opt in kwargs:
                arguments[opt] = kwargs[opt]

        try:
            result = await tool.ainvoke(arguments)
            return self._parse_result(result)
        except Exception as exc:
            return WebSearchResult(raw={"error": str(exc)})

    def _get_mcp_client(self) -> Any:
        if self._mcp_client is None:
            from tools.mcp_client import MCPClient, get_zhipu_mcp_config
            cfg = get_zhipu_mcp_config()
            self._mcp_client = MCPClient.from_config(cfg)
        return self._mcp_client

    @staticmethod
    def _parse_result(result: Any) -> WebSearchResult:
        """解析 MCP 双层 JSON 嵌套 → WebSearchResult。"""
        raw = result
        try:
            if isinstance(result, list) and len(result) > 0:
                result = result[0]
            if isinstance(result, dict) and "text" in result:
                text = result["text"]
                while isinstance(text, str):
                    try:
                        text = json.loads(text)
                    except json.JSONDecodeError:
                        break
                result = text
        except Exception:
            return WebSearchResult(raw={"error": "解析搜索结果失败", "raw": raw})

        items_data = result if isinstance(result, list) else []
        items = [
            WebSearchItem(
                title=(r.get("title") or "").strip(),
                content=(r.get("content") or "").strip(),
                link=(r.get("link") or "").strip(),
                media=(r.get("media") or "").strip(),
                publish_date=(r.get("publish_date") or "").strip(),
                icon=(r.get("icon") or "").strip(),
            )
            for r in items_data if isinstance(r, dict)
        ]
        return WebSearchResult(items=items, raw={"raw": raw})


# ── 工厂函数 ──────────────────────────────────────────────────────────────────

_client_singleton: _WebSearchClient | None = None


def _get_client(search_engine: str = "search_pro") -> _WebSearchClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = _WebSearchClient(search_engine=search_engine)
    return _client_singleton


def create_web_search_tool(search_engine: str = "search_pro"):
    """创建 web_search 工具（deepagents 兼容）。

    返回 @tool 装饰的 async 函数，可传入 create_deep_agent(tools=[...])。
    """

    @tool
    async def web_search(
        query: str,
        count: int = 3,
        count_size: str = "medium",
        search_recency_filter: str = "",
        search_domain_filter: str = "",
    ) -> str:
        """Search the internet for real-time information.

        Use this tool when:
        - The user asks about current events, news, or recent information
        - The knowledge needed is beyond your training data cutoff
        - You need to verify facts or find up-to-date documentation

        Args:
            query: Search query string (supports Chinese and English).
            count: Number of results to return (default 5, max 10).
            count_size: medium, high .
            search_recency_filter: Time filter: "week", "month", "year" or "" for no filter.
            search_domain_filter: Domain filter, e.g. "github.com" or "" for all domains.

        Returns:
            Markdown formatted search results with titles, links, and content snippets.
        """
        kwargs: dict = {}
        if count:
            kwargs["count"] = count
        if count_size:
            kwargs["count_size"] = count_size
        if search_recency_filter:
            kwargs["search_recency_filter"] = search_recency_filter
        if search_domain_filter:
            kwargs["search_domain_filter"] = search_domain_filter

        client = _get_client(search_engine)
        result = await client.search(query, **kwargs)
        if result.raw.get("error"):
            return f"搜索出错: {result.raw['error']}"
        if not result.items:
            return "未找到相关结果。"
        return result.answer

    return web_search
