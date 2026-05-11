# WriteUserMemorySkill 使用指南

## 功能概述

`WriteUserMemorySkill` 是一个将用户记忆写入 Markdown 文件的 Skill，按 `thread_id` 组织，便于调试和持久化。

**架构设计**：
- **Policy 层**（`UserMemoryPolicy`）：决策层，控制场景级开关（`write_to_file_enabled`）
- **Node 层**（`BaseNode`）：触发层，声明节点是否需要写入（`write_to_memory`），并提供格式化钩子
- **Skill 层**（`WriteUserMemorySkill`）：执行层，纯写入逻辑，不自带开关

## 快速开始

### 1. 场景配置（Policy 层）

在 `config.py` 中配置场景的记忆写入开关：

```python
from memory import UserMemoryPolicy

SCENES: dict[str, dict] = {
    "data_query": {
        "name": "代码库数据查询",
        "agent": "GraphQueryAgent",
        "task_policy": TaskMemoryPolicy(max_retain_turns=10),
        "user_policy": UserMemoryPolicy(
            rag_enabled=True,              # 启用 RAG 检索
            write_to_file_enabled=True,    # 允许节点写入记忆
        ),
    },
    "debug": {
        "name": "调试模式",
        "agent": "GraphQueryAgent",
        "user_policy": UserMemoryPolicy(
            rag_enabled=False,
            write_to_file_enabled=False,   # 关闭所有写入
        ),
    },
}
```

### 2. 节点声明（Node 层）

在自定义节点中声明是否需要写入记忆：

```python
from engine.base.node import BaseNode

class IntentClassifyNode(BaseNode):
    # 声明此节点需要写入记忆
    write_to_memory = True
    
    def execute(self, state):
        # ... 业务逻辑 ...
        return {"intent": "query_class", "confidence": 0.95}
    
    def format_memory_content(self, state, patch):
        """自定义格式化，只写入关键信息。"""
        intent = patch.get("intent", "")
        confidence = patch.get("confidence", "")
        
        parts = []
        if intent:
            parts.append(f"意图: {intent}")
        if confidence:
            parts.append(f"置信度: {confidence}")
        
        return "\n".join(parts)


class RenderNode(BaseNode):
    # 默认 write_to_memory = False，不写入记忆
    def execute(self, state):
        # ... 渲染逻辑 ...
        return {"output": "..."}
```

### 3. 直接使用 Skill（高级用法）

如果需要手动调用 Skill（不推荐，应优先使用 Node 声明）：

```python
from engine.workflow.skills.write_user_memory import WriteUserMemorySkill
from memory.request_context import bind_request_memory_context

# 在请求上下文中使用
async with bind_request_memory_context(
    thread_id="user_session_123",
    scene_key="data_query",
):
    # 注意：需要自己检查 Policy
    from memory.request_context import get_request_memory_context
    ctx = get_request_memory_context()
    
    if ctx.user_policy and ctx.user_policy.should_write():
        write_skill = WriteUserMemorySkill()
        result = await write_skill.write(
            content="用户查询了订单服务的相关信息",
        )
        
        print(f"写入成功: {result.success}")
        print(f"文件路径: {result.file_path}")
```

## 输出文件格式

文件按 `thread_id` 命名，追加写入，格式如下：

```markdown
memory/user_memories/{thread_id}.md

## [2026-04-24 15:30:45] (scene: data_query)

用户查询了订单服务的相关信息

## [2026-04-24 15:31:20] (scene: data_query)

用户进一步查询了订单模型的类结构
```

## API 参考

### UserMemoryPolicy

```python
UserMemoryPolicy(
    enabled: bool = True,
    rag_enabled: bool = False,
    rag_top_k: int = 3,
    write_to_file_enabled: bool = False,
)
```

**方法：**
- `should_write() -> bool`: 判断当前场景是否允许写入（`enabled and write_to_file_enabled`）

### BaseNode

**类属性：**
- `write_to_memory: bool = False`: 声明节点是否需要写入记忆

**方法：**
- `format_memory_content(state, patch) -> str`: 格式化节点关键信息供记忆写入（可覆盖）

### WriteUserMemorySkill

#### 构造函数

```python
WriteUserMemorySkill(
    output_dir: str = "memory/user_memories"
)
```

**参数：**
- `output_dir`: 输出目录路径

#### write() 方法

```python
async def write(
    self,
    content: str,
    *,
    thread_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> WriteUserMemoryResult
```

**参数：**
- `content`: 要写入的记忆内容
- `thread_id`: 线程 ID（可选，不传则从 request context 获取）
- `metadata`: 可选的元数据

**返回值：**
- `WriteUserMemoryResult`: 包含 `success`, `file_path`, `message`

### WriteUserMemoryResult

```python
@dataclass
class WriteUserMemoryResult:
    success: bool = False      # 是否成功
    file_path: str = ""        # 文件路径
    message: str = ""          # 消息说明
```

## 注意事项

1. **职责分离**：
   - Policy 控制"能不能写"（场景级开关）
   - Node 控制"写不写"（节点级声明）和"写什么"（格式化钩子）
   - Skill 负责"怎么写"（纯执行逻辑）

2. **线程安全**：多个并发写入同一文件时，Python 的 `open(..., "a")` 保证追加安全

3. **目录自动创建**：如果输出目录不存在，会自动创建

4. **空内容过滤**：空字符串或纯空白内容不会写入

5. **上下文依赖**：需要在 `bind_request_memory_context` 上下文中使用，以便获取 Policy

6. **默认行为**：节点默认 `write_to_memory=False`，不会自动写入记忆

## 决策流程

```
节点执行成功
    ↓
Node.write_to_memory == True?  ─No─→ 跳过
    ↓ Yes
Policy.should_write()?  ─No─→ 跳过
    ↓ Yes
Node.format_memory_content()  →  "意图: query_class | 置信度: 0.95"
    ↓
Skill.write(content)  →  写入文件
```

## 与 UserMemoryRAGSkill 配合

```python
from engine.workflow.skills import UserMemoryRAGSkill
from memory.request_context import get_request_memory_context

# RAG 检索（由 Policy 控制）
rag_skill = UserMemoryRAGSkill()
rag_result = await rag_skill.recall(query="订单服务")

# 检查是否启用
ctx = get_request_memory_context()
if ctx.user_policy and ctx.user_policy.rag_enabled:
    print(f"检索到上下文: {rag_result.rag_context}")
```

## 测试

运行测试文件验证功能：

```bash
python test/test_write_user_memory.py
```

测试覆盖：
- ✓ 启用写入功能
- ✓ 禁用写入功能
- ✓ 空内容处理
- ✓ 多次写入同一 thread
- ✓ 文件内容验证
