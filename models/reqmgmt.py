"""
Minimal reqmgmt ORM models — stripped from CodeMind base/entity/reqmgmt/.

No DomainManager, BaseSkill, ReqMgmtEntity, or Bus dependencies.
Pure SQLAlchemy declarative models with schema-generation support.
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class ReqMgmtBase(DeclarativeBase):
    """Minimal declarative base for reqmgmt models."""
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Node entities
# ═══════════════════════════════════════════════════════════════════════════════

class Product(ReqMgmtBase):
    __tablename__ = "products"

    TABLE: ClassVar[str] = "Product"
    LLM_NODE_NOTE: ClassVar[str] = "产品；字段: id, name(产品名称), description(描述)"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class RequirementModel(ReqMgmtBase):
    __tablename__ = "requirement_models"

    TABLE: ClassVar[str] = "RM (需求模型)"
    LLM_NODE_NOTE: ClassVar[str] = (
        "需求模型；type: user_requirement|product_requirement; "
        "字段: id, name, type, product_id→products, description"
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_id: Mapped[str | None] = mapped_column(String, ForeignKey("products.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class RequirementItem(ReqMgmtBase):
    __tablename__ = "requirement_items"

    TABLE: ClassVar[str] = "IR (用户需求项)"
    LLM_NODE_NOTE: ClassVar[str] = (
        "用户需求项；字段: id, name, title, description, priority, status, "
        "rm_id→requirement_models"
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    rm_id: Mapped[str | None] = mapped_column(String, ForeignKey("requirement_models.id"), nullable=True)


class ProductRequirement(ReqMgmtBase):
    __tablename__ = "product_requirements"

    TABLE: ClassVar[str] = "PR (产品需求项)"
    LLM_NODE_NOTE: ClassVar[str] = (
        "产品需求项；字段: id, name, title, description, "
        "type(文档|标题|正文), parent_id→product_requirements, sort_order, "
        "rm_id→requirement_models"
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String, ForeignKey("product_requirements.id"), nullable=True)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rm_id: Mapped[str | None] = mapped_column(String, ForeignKey("requirement_models.id"), nullable=True)


class Part(ReqMgmtBase):
    __tablename__ = "parts"

    TABLE: ClassVar[str] = "Part (零部件)"
    LLM_NODE_NOTE: ClassVar[str] = "零部件；字段: id, name(名称如Part001), description"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class TestCase(ReqMgmtBase):
    __tablename__ = "test_cases"

    TABLE: ClassVar[str] = "TC (测试用例)"
    LLM_NODE_NOTE: ClassVar[str] = "测试用例；字段: id, name(如TC-001), description, test_type(测试类型)"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_type: Mapped[str | None] = mapped_column(Text, nullable=True)


class TestItem(ReqMgmtBase):
    __tablename__ = "test_items"

    TABLE: ClassVar[str] = "TS (测试项/验证记录)"
    LLM_NODE_NOTE: ClassVar[str] = (
        "测试项/验证记录; 字段: id, name, verification_status(未验证|验证中|验证通过), "
        "tester, test_date, remark"
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    tester: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Edge / relationship tables (多对多关联)
# ═══════════════════════════════════════════════════════════════════════════════

class IRPRLink(ReqMgmtBase):
    """IR ←→ PR 覆盖关系（用户需求被产品需求覆盖）"""

    __tablename__ = "ir_pr_links"

    TABLE: ClassVar[str] = "IR_PR_Link (IR↔PR覆盖关系)"
    LLM_NODE_NOTE: ClassVar[str] = "IR→PR 覆盖关系表；字段: id, ir_id→requirement_items, pr_id→product_requirements"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    ir_id: Mapped[str] = mapped_column(String, ForeignKey("requirement_items.id"))
    pr_id: Mapped[str] = mapped_column(String, ForeignKey("product_requirements.id"))


class PRPartLink(ReqMgmtBase):
    """PR ←→ Part 涉及关系（产品需求涉及零部件）"""

    __tablename__ = "pr_part_links"

    TABLE: ClassVar[str] = "PR_Part_Link (PR↔Part涉及关系)"
    LLM_NODE_NOTE: ClassVar[str] = "PR→Part 涉及关系表；字段: id, pr_id→product_requirements, part_id→parts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pr_id: Mapped[str] = mapped_column(String, ForeignKey("product_requirements.id"))
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"))


class PartTCLink(ReqMgmtBase):
    """Part ←→ TC 测试关系（零部件对应测试用例）"""

    __tablename__ = "part_tc_links"

    TABLE: ClassVar[str] = "Part_TC_Link (Part↔TC测试关系)"
    LLM_NODE_NOTE: ClassVar[str] = "Part→TC 测试关系表；字段: id, part_id→parts, tc_id→test_cases"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"))
    tc_id: Mapped[str] = mapped_column(String, ForeignKey("test_cases.id"))


class TCTestItemLink(ReqMgmtBase):
    """TC ←→ TestItem 测试执行关系（测试用例的测试执行记录）"""

    __tablename__ = "tc_ts_links"

    TABLE: ClassVar[str] = "TC_TS_Link (TC↔TS测试执行关系)"
    LLM_NODE_NOTE: ClassVar[str] = "TC→TS 测试执行关系表；字段: id, tc_id→test_cases, ts_id→test_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tc_id: Mapped[str] = mapped_column(String, ForeignKey("test_cases.id"))
    ts_id: Mapped[str] = mapped_column(String, ForeignKey("test_items.id"))


# ═══════════════════════════════════════════════════════════════════════════════
# Entity registry (for schema generation)
# ═══════════════════════════════════════════════════════════════════════════════

REQMGMT_ENTITY_CLASSES: tuple[type[ReqMgmtBase], ...] = (
    Product,
    RequirementModel,
    RequirementItem,
    ProductRequirement,
    Part,
    TestCase,
    TestItem,
    IRPRLink,
    PRPartLink,
    PartTCLink,
    TCTestItemLink,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Schema text generation (replaces DomainConfig.build_schema_text)
# ═══════════════════════════════════════════════════════════════════════════════

def build_schema_text() -> str:
    """Generate Text2SQL schema description from ORM metadata."""
    note_map: dict[str, str] = {}
    for cls in REQMGMT_ENTITY_CLASSES:
        tn = getattr(cls, "__tablename__", None)
        note = getattr(cls, "LLM_NODE_NOTE", "")
        if tn and note:
            note_map[tn] = note

    lines: list[str] = []
    for table_name, table in sorted(ReqMgmtBase.metadata.tables.items()):
        cols: list[str] = []
        for col in table.columns:
            if col.foreign_keys:
                fk = next(iter(col.foreign_keys))
                ref_table = fk.column.table.name
                cols.append(f"{col.name}→{ref_table}")
            else:
                cols.append(col.name)
        line = f"{table_name}({', '.join(cols)})"
        note = note_map.get(table_name, "")
        if note:
            line += f"  -- {note}"
        lines.append(line)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# DB setup (replaces DomainManager.get_engine + init_database)
# ═══════════════════════════════════════════════════════════════════════════════

_engine = None
_SessionFactory = None


def init_db(db_path: str = "") -> None:
    """Initialize SQLite engine and create tables (idempotent)."""
    global _engine, _SessionFactory
    if _engine is not None:
        return

    from pathlib import Path

    from utils.paths import data_paths

    p = Path(db_path) if db_path else data_paths.reqmgmt_db()
    p.parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(
        f"sqlite:///{p.as_posix()}",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    ReqMgmtBase.metadata.create_all(_engine)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=True)


def get_session() -> Session:
    if _SessionFactory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _SessionFactory()


def get_engine():
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine
