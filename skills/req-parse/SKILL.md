---
name: req-parse
description: Parse requirement documents (.docx) to extract products, requirement models, and requirement items for database storage. Use when the user uploads or references a Word requirement document.
allowed-tools: parse_docx_outline, extract_entities, store_entities
---

## When to use

- User uploads a requirement specification document (Word/.docx)
- User asks to extract requirements from a document
- User wants to store parsed requirements into the database

## Steps

### 1. Parse the document structure

Use `parse_docx_outline` to extract the structured heading tree from the .docx file.
Pass the file path from the user's message.

This returns:
- `title`: Document title
- `sections`: Full heading hierarchy with paragraphs
- `stats`: Heading/paragraph counts
- `llm_structure`: Pre-processed structure for entity extraction (flattened sections with IDs)

### 2. Extract entities

Call `extract_entities` with the `llm_structure` JSON from step 1.
This uses LLM classification to map each section to the correct entity type
(products, requirement_models, requirement_items).

The tool handles:
- Section → entity type classification
- ID generation (PROD-001, RM-001, IR-001, etc.)
- Foreign key relationships (RM→Product, IR→RM)
- Original paragraph restoration

### 3. Store entities

Call `store_entities` with the `entities` JSON array from step 2.
This writes all entities to the database using the proper ORM schema.

Storage is idempotent — re-storing the same entities will not create duplicates.

### 4. Confirm

Report the summary to the user: how many products, requirement_models, and
requirement_items were stored. Use the stats from step 2.
