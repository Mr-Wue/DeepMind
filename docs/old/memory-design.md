# Memory 系统设计规范

> 本文档面向 AI 协作者：描述 CodeMind 记忆系统的目标架构与实现约束。

---

## 一、设计目标

1. **记忆按类型分离**：任务记忆（turn_history）vs 用户记忆（偏好/跨任务上下文）
2. **记忆按类型切换后端**：任务记忆 → Checkpointer；用户记忆 → InMemoryStore（嵌入搜索）
3. **记忆按类型选择策略**：任务记忆按轮数裁剪 + LLM 压缩；用户记忆按 RAG 检索
4. **策略可配置**：不同场景（scene）可注入不同策略对象

> **切换方式**：改代码重启即可，不支持运行时切换。

---

## 二、核心抽象（仅策略层）

存储层直接用 LangGraph 原生能力，不重复封装：

| 记忆类型 | 存储后端 | 策略（需封装） |
|----------|---------|---------------|
| `task` | LangGraph `MemorySaver`（`CompiledStateGraph.aget_state` / `aupdate_state`） | 裁尾阈值、压缩触发条件 |
| `user` | LangGraph `InMemoryStore`（嵌入搜索） | RAG 开关、top_k |

### 2.1 为什么不用 Backend 抽象

LangGraph 的 `MemorySaver` 和 `InMemoryStore` 本身就是成熟的存储抽象，它们的 API 已经足够简单：

```python
# 任务记忆：LangGraph checkpointer 原生支持
await compiled.aget_state(cfg)      # 读
await compiled.aupdate_state(cfg)  # 写

# 用户记忆：InMemoryStore 原生支持嵌入搜索
store.search(namespace, query=...)  # 读
store.put(namespace, key, value)    # 写
```

封一层 `Backend` 只是徒增代码量，没有实际收益。

---

## 三、策略接口

```python
memory/policies/base.py

@dataclass
class MemoryPolicy:
    """策略基类。task/user 各自有子类。"""
    enabled: bool = True
```

### 3.1 TaskMemoryPolicy

```python
memory/policies/task.py

@dataclass
class TaskMemoryPolicy(MemoryPolicy):
    """任务记忆策略。"""

    max_retain_turns: int = 5
    """裁尾阈值。None = 不裁尾。"""

    compression_threshold: int | None = None
    """触发 LLM 压缩的轮数阈值。None = 不压缩。"""
```

### 3.2 UserMemoryPolicy

```python
memory/policies/user.py

@dataclass
class UserMemoryPolicy(MemoryPolicy):
    """用户记忆策略。"""

    rag_enabled: bool = False
    """是否启用 RAG 检索。"""

    rag_top_k: int = 3
    """RAG 检索返回条数。"""
```

---

## 四、请求上下文

```python
memory/request_context.py

@dataclass(frozen=True)
class RequestMemoryContext:
    thread_id: str = ""
    scene_key: str = ""
    # 各类型策略（可按 scene 注入不同策略）
    task_policy: TaskMemoryPolicy | None = None
    user_policy: UserMemoryPolicy | None = None
```

---

## 五、初始化与绑定

```python
# app.py 或启动脚本

# 1. LangGraph 原生组件（按需切换实现类）
checkpointer = MemorySaver()  # 开发环境；生产换 PostgresSaver
store = InMemoryStore(
    index={
        "embed": init_embeddings("openai:text-embedding-3-small"),
        "dims": 1536,
    }
)  # 开发环境；生产换 PostgresStore

# 2. 编译时注入到 graph
graph = builder.compile(checkpointer=checkpointer, store=store)

# 3. 策略按 scene 注入
def make_policy_for_scene(scene_key: str) -> tuple[TaskMemoryPolicy, UserMemoryPolicy]:
    if scene_key == "debug":
        return TaskMemoryPolicy(max_retain_turns=3), UserMemoryPolicy(rag_enabled=False)
    return TaskMemoryPolicy(max_retain_turns=10), UserMemoryPolicy(rag_enabled=True)

# 4. 请求级绑定
task_policy, user_policy = make_policy_for_scene(scene_key)
with bind_request_memory_context(
    thread_id=thread_id,
    scene_key=scene_key,
    task_policy=task_policy,
    user_policy=user_policy,
):
    # 后续节点从 context 获取策略
    ...
```

---

## 六、节点使用示例

### 6.1 任务记忆写入（GraphQueryAgent）

```python
# agents/graph_query_agent.py

async def _invoke(self, ctx: ExecutionContext) -> dict[str, Any]:
    out = await self._runner.arun(ctx.user_input)
    r = get_request_memory_context()

    if r.thread_id and r.task_policy:
        # 策略判断：是否裁尾
        hist = list(out.get("turn_history") or [])
        max_retain = r.task_policy.max_retain_turns
        if max_retain and len(hist) > max_retain:
            hist = hist[-max_retain:]

        # 策略判断：是否压缩
        if r.task_policy.should_compress(len(hist)):
            hist = await self._compress(hist)

        # 直接用 LangGraph checkpointer 写
        cfg = {"configurable": {"thread_id": r.thread_id}}
        await self._runner._workflow.compiled.aupdate_state(cfg, {
            "turn_history": hist,
            "last_completed_turn_id": out.get("current_turn_id"),
        })
    return out
```

### 6.2 任务记忆读取（MemoryCompensateNode）

```python
# engine/workflow/graph_query/_nodes_memory.py

async def execute(self, state: GraphQueryState) -> dict[str, Any]:
    r = get_request_memory_context()
    thread_id = str(r.thread_id or "")

    if thread_id:
        # 直接用 LangGraph checkpointer 读
        cfg = {"configurable": {"thread_id": thread_id}}
        prev = await self._runner._workflow.compiled.aget_state(cfg)
        if prev and prev.values:
            turn_history = prev.values.get("turn_history", [])
            last_completed_turn_id = prev.values.get("last_completed_turn_id", 0)
        else:
            turn_history = []
            last_completed_turn_id = 0
    else:
        turn_history = list(state.get("turn_history") or [])
        last_completed_turn_id = int(state.get("last_completed_turn_id") or 0)

    # ... 后续逻辑不变
```

### 6.3 用户记忆 RAG（MemoryCompensateNode）

```python
async def execute(self, state: GraphQueryState) -> dict[str, Any]:
    r = get_request_memory_context()

    if r.user_policy and r.user_policy.rag_enabled:
        # 通过 runtime.store 访问 InMemoryStore
        namespace = ("user", r.thread_id or "anonymous")
        results = await self._runtime.store.asearch(
            namespace,
            query=state["raw_input"],
            limit=r.user_policy.rag_top_k,
        )
        rag_context = "\n".join(item.value["text"] for item in results)
        # 注入到 memory_context
```

> **注意**：`runtime.store` 需通过 `Runtime` 参数注入到 node，详见 LangGraph 官方文档。

---

## 七、目录结构

```
memory/
  constants.py          # 键名常量（保留）
  request_context.py    # RequestMemoryContext（保留）

  policies/             # 策略层（新增）
    __init__.py
    base.py
    task.py
    user.py

  __init__.py
```

---

## 八、实现约束

1. **存储直接用 LangGraph 原生 API**
   - 任务记忆：`compiled.aget_state` / `aupdate_state`
   - 用户记忆：`runtime.store.search` / `put`

2. **策略只封装决策逻辑**
   - 裁尾/压缩/RAG 的判断在策略类中
   - 实际存储操作调用 LangGraph API

3. **不支持运行时切换后端**
   - 切换方式：改代码 → 重启
   - 如需切换存储后端，替换 `MemorySaver` → `PostgresSaver`，`InMemoryStore` → `PostgresStore`

4. **所有异步方法必须声明 `async def`**

5. **Policy 对象必须可序列化（dataclass）**
   - 便于从配置文件加载
