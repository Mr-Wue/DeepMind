# CodeMind 记忆系统改进计划

参考 [arch-insights-for-codemind.md](arch-insights-for-codemind.md) 和 [nanobot](D:\ai\nanobot) 源码。

## Context

CodeMind 当前的问题：
1. 所有记忆混在 `InMemoryStore` + RAG 向量检索中，LLM 无法区分"用户偏好"和"对话上下文"
2. 提示词硬编码在 Skill 的 Python 类中，修改需要改代码
3. `@tool_method` 装饰器缺少依赖声明，Maven 工具依赖 `mvn` 命令只在运行时才发现
4. 没有机制区分模板占位符和真实记忆内容

改进目标：参考 nanobot 的轻量设计模式，用最少代码改动实现三权分立的记忆体系 + Jinja2 提示词模板 + Tool 依赖声明。

## Phase A — 立即落地（<100 行代码）

### A1. 记忆文件三权分立（SOUL / USER / MEMORY）

**文件清单：**

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `data/soul.md` | AI 行为准则（手动维护） |
| 新建 | `data/user.md` | 用户画像模板（手动填写） |
| 新建 | `data/memory.md` | 长期记忆（由 WriteUserMemorySkill 增量更新），初始为模板 |
| 新建 | `engine/workflow/skills/system_prompt_builder.py` | build_system_prompt() + is_template_placeholder() |
| 修改 | `utils/paths.py` | DataPaths 新增 data() 方法 |
| 修改 | `engine/workflow/graph_query/state.py` | 新增 system_context 字段 |
| 修改 | `engine/workflow/graph_query/runner.py` | initial_state 时构建 system_context |

注入顺序：SOUL → USER → MEMORY（仅非模板时）→ RAG 上下文

### A2. Tool 依赖声明

**文件清单：**

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `base/entity/codebase/tool_method.py` | tool_method() 新增 requires 参数 |
| 修改 | `engine/workflow/tools/auto_codebase_tools.py` | _register_tool_methods() 加依赖检查 |

## Phase B — 下一个大版本

### B1. Jinja2 提示词模板系统

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `utils/prompt_templates.py` | Jinja2 渲染引擎（30 行） |
| 新建 | `prompts/*.j2` (6-8 个) | 从 Skill 中提取的提示词模板 |
| 修改 | 各 Skill 文件 | prompt 属性改用 render_template() |
| 修改 | `requirements.txt` | 加 jinja2 |

## Phase C — 远期储备

- C1: Dream 长期记忆机制（参考 nanobot `agent/memory.py` Dream 类）
- C2: 行级 Git 年龄标注（参考 nanobot `utils/gitstore.py`，需 dulwich）

## 关键对照表

| CodeMind 改动 | 参考 nanobot | 看什么 |
|-------------|-------------|--------|
| `data/soul.md`（新建） | `templates/SOUL.md` | 默认模板结构 |
| `data/user.md`（新建） | `templates/USER.md` | 默认模板结构 |
| `data/memory.md`（新建） | `templates/memory/MEMORY.md` | 默认模板结构 |
| `system_prompt_builder.py`（新建） | `agent/context.py:build_system_prompt()` | 注入逻辑 |
| `utils/prompt_templates.py`（新建） | `utils/prompt_templates.py` | 渲染函数 |
| `tool_method.py`（修改） | `agent/skills.py:_check_requirements()` | requires 解析 |
