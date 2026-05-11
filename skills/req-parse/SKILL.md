---
name: req-parse
description: Parse requirement documents (.docx) to extract products, requirement models, and requirement items for database storage. Use when the user uploads or references a Word requirement document.
allowed-tools: read_docx, entity_store
---

## When to use

- User uploads a requirement specification document (Word/.docx)
- User asks to extract requirements from a document
- User wants to store parsed requirements into the database

## Steps

### 1. Read the document

Use `read_docx` to extract the document content as structured markdown.
Note the file path from the user's message or from the filesystem.

### 2. Extract entities following these mapping rules

From the markdown content, identify entities according to the following rules:

**products (产品)**
- The document title (first `# heading`) maps to a product
- Product `id` format: `PROD-001` (auto-increment)
- Product `name`: the document title text
- Product `description`: overview paragraphs immediately after the title, plus any list items before the first `## heading`

**requirement_models (需求模型 / RM)**
- Each `## heading` (H2) section maps to a requirement_model
- RM `id` format: `RM-001`, `RM-002`, etc.
- RM `name`: `{id} {heading text}`
- RM `type`: `"user_requirement"` (default)
- RM `product_id`: the id of the product extracted above
- RM `description`: the first body paragraph under the H2 heading (before any H3)

**requirement_items (用户需求项 / IR)**
- Each `### heading` (H3) subsection maps to a requirement_item
- IR `id` format: `IR-001`, `IR-002`, etc. (sequential across the whole document)
- IR `name`: same as `id`
- IR `title`: the H3 heading text
- IR `description`: all body paragraphs under the H3 heading, joined with newlines
- IR `priority`: `"中"` (default)
- IR `status`: `"未实现"` (default)
- IR `rm_id`: the id of the parent requirement_model (the H2 that contains this H3)

**Skip these**
- Empty paragraphs
- Paragraphs that are just category labels (e.g., "功能要求", "非功能性需求") — skip them, do not create entities from them

### 3. Store entities

Call `entity_store` with `operation="store"` and the entities as a JSON array in `entities_json`.
Each entity object MUST have a `_type` field set to one of: `"products"`, `"requirement_models"`, `"requirement_items"`.

Example entity format:
```json
[
  {"_type": "products", "id": "PROD-001", "name": "XX平台需求规格说明书", "description": "..."},
  {"_type": "requirement_models", "id": "RM-001", "name": "RM-001 用户管理", "type": "user_requirement", "product_id": "PROD-001", "description": "..."},
  {"_type": "requirement_items", "id": "IR-001", "name": "IR-001", "title": "用户注册登录", "description": "...", "priority": "中", "status": "未实现", "rm_id": "RM-001"}
]
```

### 4. Confirm and report

After storing, call `entity_store` with `operation="stats"` to get counts.
Report the summary to the user: how many products, requirement_models, and requirement_items were stored.
