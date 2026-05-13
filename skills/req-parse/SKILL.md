---
name: req-parse
description: 解析需求文档（.docx），提取产品、需求模型、需求项并存入数据库。当用户上传或引用 Word 需求文档时使用。
allowed-tools: parse_docx_outline, extract_entities, store_entities
---

## 适用场景

- 用户上传需求规格文档（Word/.docx）
- 用户要求从文档中提取需求
- 用户希望将解析后的需求存入数据库

## 工作步骤

### 1. 解析文档结构

使用 `parse_docx_outline` 从 .docx 文件中提取结构化标题树。
传入用户消息中的文件路径。

返回内容：
- `title`：文档标题
- `sections`：完整标题层级及段落
- `stats`：标题/段落统计
- `llm_structure`：为实体提取预处理的扁平化节列表（含 ID）

### 2. 提取实体

使用步骤 1 返回的 `llm_structure` JSON 调用 `extract_entities`。
该工具通过 LLM 分类将每个节映射到正确的实体类型（产品、需求模型、需求项）。

工具负责：
- 节 → 实体类型分类
- ID 生成（PROD-001、RM-001、IR-001 等）
- 外键关系建立（RM→Product、IR→RM）
- 原始段落还原

### 3. 存储实体

使用步骤 2 返回的 `entities` JSON 数组调用 `store_entities`。

⚠️ **执行前会暂停等待用户确认** — 此时向用户展示即将入库的实体摘要（产品数、模型数、需求项数），等待用户批准后再实际写入。

通过 ORM schema 将所有实体写入数据库。

存储具有幂等性 — 重复存储相同实体不会产生重复数据。

### 4. 确认结果

向用户报告汇总：存入了多少产品、需求模型、需求项。使用步骤 2 的统计数据。
