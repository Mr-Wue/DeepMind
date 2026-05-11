"""
Test ReqMgmtText2SQLAgent — requirement_items queries.

Usage::

    python tests/text2sql_reqmgmt_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class Text2SQLReqmgmt_test:
    """Test natural language queries against the reqmgmt requirement_items table."""

    def __init__(self) -> None:
        from agents.text2sql_agent import ReqMgmtText2SQLAgent
        from middleware.logging_middleware import InvocationLoggingHandler
        self._handler = InvocationLoggingHandler(log_dir="data/logs")
        self._agent = ReqMgmtText2SQLAgent()
        self._passed = 0
        self._failed = 0

    # ── helpers ──────────────────────────────────────────────────────────

    def _ok(self, label: str, extra: str = "") -> None:
        self._passed += 1
        msg = f"  [PASS] {label}"
        if extra:
            msg += f"  ({extra})"
        print(msg)

    def _fail(self, label: str, reason: str = "") -> None:
        self._failed += 1
        msg = f"  [FAIL] {label}"
        if reason:
            msg += f" — {reason}"
        print(msg)

    async def _query(self, q: str) -> dict:
        return await self._agent.query(q, callbacks=[self._handler])

    async def _assert_rows(self, label: str, result: dict, *,
                           min_rows: int = 0, exactly: int | None = None) -> None:
        rows = result.get("query_result", [])
        error = result.get("error", "")
        if error:
            self._fail(label, f"SQL error: {error[:120]}")
            return
        n = len(rows)
        if exactly is not None and n != exactly:
            self._fail(label, f"expected {exactly} rows, got {n}")
        elif n < min_rows:
            self._fail(label, f"expected >= {min_rows} rows, got {n}")
        else:
            self._ok(label, f"{n} rows")
        if rows:
            print(f"         columns: {list(rows[0].keys())}")

    async def _assert_sql(self, label: str, result: dict) -> None:
        sql = result.get("sql", "")
        if sql.strip().upper().startswith("SELECT"):
            self._ok(label, sql[:100])
        else:
            self._fail(label, "no valid SQL")

    # ── tests (queries match actual DB content) ──────────────────────────

    async def test_01_select_all(self) -> None:
        """SELECT * — 全表"""
        print("\n── test_01: 全表查询 ──")
        r = await self._query("列出所有用户需求项(requirement_items)")
        await self._assert_rows("全表", r, min_rows=16)
        await self._assert_sql("SQL", r)

    async def test_02_filter_by_priority(self) -> None:
        """WHERE priority = '中'"""
        print("\n── test_02: 按优先级筛选 ──")
        r = await self._query("查询优先级为'中'的需求项，列出标题(title)和优先级(priority)")
        await self._assert_rows("priority=中", r, min_rows=16)
        await self._assert_sql("SQL", r)

    async def test_03_filter_by_status(self) -> None:
        """WHERE status = '未实现'"""
        print("\n── test_03: 按状态筛选 ──")
        r = await self._query("查询状态为'未实现'的需求项")
        await self._assert_rows("status=未实现", r, min_rows=16)
        await self._assert_sql("SQL", r)

    async def test_04_count_by_model(self) -> None:
        """GROUP BY rm_id — 按需求模型统计"""
        print("\n── test_04: 按需求模型统计 ──")
        r = await self._query(
            "统计每个需求模型(requirement_model)下有多少条需求项(requirement_item)，"
            "按数量从多到少排列，显示模型名称和数量"
        )
        await self._assert_rows("GROUP BY", r, min_rows=3)
        await self._assert_sql("SQL", r)

    async def test_05_keyword_like(self) -> None:
        """description LIKE '%性能%'"""
        print("\n── test_05: 关键词模糊搜索 ──")
        r = await self._query("查询标题或描述中包含'性能'的需求项")
        await self._assert_rows("LIKE '%性能%'", r, min_rows=1)
        await self._assert_sql("SQL", r)

    async def test_06_multi_condition(self) -> None:
        """WHERE priority='中' AND status='未实现'"""
        print("\n── test_06: 多字段组合 ──")
        r = await self._query(
            "查询优先级为'中'且状态为'未实现'的需求项，列出id、标题、优先级、状态"
        )
        await self._assert_rows("AND组合", r, min_rows=16)
        await self._assert_sql("SQL", r)

    async def test_07_count_total(self) -> None:
        """SELECT COUNT(*)"""
        print("\n── test_07: 总数统计 ──")
        r = await self._query("requirement_items 表里一共有多少条记录？")
        await self._assert_rows("COUNT", r, min_rows=1)
        await self._assert_sql("SQL", r)

    async def test_08_join_model(self) -> None:
        """JOIN requirement_models"""
        print("\n── test_08: JOIN 关联查询 ──")
        r = await self._query(
            "列出所有需求项的标题(title)，同时显示它所属的需求模型名称"
            "(即 JOIN requirement_models 取 name)"
        )
        await self._assert_rows("JOIN", r, min_rows=16)
        await self._assert_sql("SQL", r)

    async def test_09_distinct(self) -> None:
        """SELECT DISTINCT priority"""
        print("\n── test_09: 去重 ──")
        r = await self._query(
            "requirement_items 表中有哪些不同的优先级(priority)值？去重列出"
        )
        await self._assert_rows("DISTINCT", r, min_rows=1)
        await self._assert_sql("SQL", r)

    async def test_10_answer_format(self) -> None:
        """markdown 表格输出"""
        print("\n── test_10: 返回格式 ──")
        r = await self._query("查询所有需求项")
        answer = r.get("answer", "")
        if "|" in answer and "---" in answer:
            self._ok("markdown 表格", f"{len(answer)} chars")
        else:
            self._fail("markdown 表格", f"unexpected: {answer[:100]}")

    # ── runner ───────────────────────────────────────────────────────────

    async def _run_all(self) -> None:
        print("=" * 60)
        print("  Text2SQLReqmgmt_test — requirement_items")
        print("=" * 60)

        for name in sorted(dir(self)):
            if name.startswith("test_") and name.endswith(("select_all", "filter_by_priority",
                    "filter_by_status", "count_by_model", "keyword_like",
                    "multi_condition", "count_total", "join_model", "distinct",
                    "answer_format")):
                try:
                    await getattr(self, name)()
                except Exception as exc:
                    self._fail(name, str(exc)[:200])

        print(f"\n{'=' * 60}")
        print(f"  Results: {self._passed} passed, {self._failed} failed")
        print(f"{'=' * 60}")
        if self._failed:
            sys.exit(1)


def main() -> None:
    asyncio.run(Text2SQLReqmgmt_test()._run_all())


if __name__ == "__main__":
    main()
