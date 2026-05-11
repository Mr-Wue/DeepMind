-- CodeMind 需求管理库（SQLite）
-- 领域：reqmgmt — 需求管理AI验证
-- 与 base/entity/reqmgmt/ 下的 ORM 实体一一对应
--
-- 实体关系链路：
--   Product → RequirementModel(RM-001/RM-002) → IR/PR
--   IR ←→ PR（覆盖关系）
--   PR ←→ Part（涉及关系）
--   Part ←→ TC（测试关系）
--   TC ←→ TS（测试执行）
--
-- 表名小写 + 下划线，主键统一为 TEXT 类型。

-- ============================================================
-- 实体表（7 张）
-- ============================================================

CREATE TABLE IF NOT EXISTS products (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS requirement_models (
    id          TEXT PRIMARY KEY,
    name        TEXT,        -- 例 "RM-001 用户需求" / "RM-002 产品需求"
    type        TEXT,        -- "user_requirement" | "product_requirement"
    product_id  TEXT REFERENCES products (id),
    description TEXT
);

CREATE TABLE IF NOT EXISTS requirement_items (
    id          TEXT PRIMARY KEY,
    name        TEXT,        -- 例 "IR-1"
    title       TEXT,        -- 需求标题
    description TEXT,        -- 需求详情
    priority    TEXT,        -- 优先级：高/中/低
    status      TEXT,        -- 状态：已实现/未实现/实现中
    rm_id       TEXT REFERENCES requirement_models (id),
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS product_requirements (
    id          TEXT PRIMARY KEY,
    name        TEXT,        -- 例 "PR-1"
    title       TEXT,        -- 产品需求标题
    description TEXT,        -- 产品需求说明
    type        TEXT,        -- 类型：文档/标题/正文
    parent_id   TEXT REFERENCES product_requirements (id),  -- 父节点（层级树）
    sort_order  INTEGER,     -- 排序序号
    rm_id       TEXT REFERENCES requirement_models (id),
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS parts (
    id          TEXT PRIMARY KEY,
    name        TEXT,        -- 例 "Part001 上传组件"
    description TEXT
);

CREATE TABLE IF NOT EXISTS test_cases (
    id          TEXT PRIMARY KEY,
    name        TEXT,        -- 例 "TC-001"
    description TEXT,        -- 测试描述
    test_type   TEXT         -- 测试类型：功能测试/性能测试/安全测试/可靠性测试等
);

CREATE TABLE IF NOT EXISTS test_items (
    id                  TEXT PRIMARY KEY,
    name                TEXT,        -- 例 "TS-001"
    verification_status TEXT,        -- "未验证" | "验证中" | "验证通过"
    tester              TEXT,        -- 测试人员
    test_date           TEXT,        -- 测试日期
    remark              TEXT         -- 备注
);

-- ============================================================
-- 关系表（4 张）— 多对多关联
-- ============================================================

-- IR ←→ PR 覆盖关系（用户需求被哪些产品需求覆盖）
CREATE TABLE IF NOT EXISTS ir_pr_links (
    id    TEXT PRIMARY KEY,
    ir_id TEXT NOT NULL REFERENCES requirement_items (id),
    pr_id TEXT NOT NULL REFERENCES product_requirements (id)
);

-- PR ←→ Part 涉及关系（产品需求涉及哪些零部件）
CREATE TABLE IF NOT EXISTS pr_part_links (
    id      TEXT PRIMARY KEY,
    pr_id   TEXT NOT NULL REFERENCES product_requirements (id),
    part_id TEXT NOT NULL REFERENCES parts (id)
);

-- Part ←→ TC 测试关系（零部件对应哪些测试用例）
CREATE TABLE IF NOT EXISTS part_tc_links (
    id      TEXT PRIMARY KEY,
    part_id TEXT NOT NULL REFERENCES parts (id),
    tc_id   TEXT NOT NULL REFERENCES test_cases (id)
);

-- TC ←→ TS 测试执行关系（测试用例对应哪些测试执行记录）
CREATE TABLE IF NOT EXISTS tc_ts_links (
    id    TEXT PRIMARY KEY,
    tc_id TEXT NOT NULL REFERENCES test_cases (id),
    ts_id TEXT NOT NULL REFERENCES test_items (id)
);
