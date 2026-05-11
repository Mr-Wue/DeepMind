# CodeMind 系统架构 - Mermaid 分层架构图

## 架构总览

```mermaid
graph TB
    %% ===== 第一层：表现层 =====
    subgraph L1["Presentation 表现层"]
        direction LR
        UI[Web界面]
    end

    %% ===== 第二层：接入层 =====
    subgraph L2["Entry 接入层"]
        direction LR
        Router[Router路由]
    end

    %% ===== 第三层：Agent层 =====
    subgraph L3["Agent 多智能体层"]
        direction LR
        A1[GraphQueryAgent<br/>代码查询]
        A2[GenericAgent<br/>ReAct调用]
        A3[AutonomousAgent<br/>规划执行]
    end

    %% ===== 第四层：编排层 =====
    subgraph L4["Orchestration 编排层"]
        direction LR
        StepExec[StepExecutor<br/>步骤执行器]
        LangGraph[LangGraph<br/>工作流引擎]
        CapRegistry[CapabilityRegistry<br/>能力注册中心]
    end

    %% ===== 第五层：能力单元层 =====
    subgraph L5["Capability 能力单元层"]
        direction LR
        subgraph Skills["Skill 技能"]
            S1[Planner<br/>规划]
            S2[Render<br/>渲染]
            S3[Text2SQL<br/>SQL生成]
            S4[ImageParse<br/>图片解析]
        end
        subgraph Tools["Tool 工具"]
            T1[WebSearch<br/>搜索]
            T2[FileParser<br/>解析]
            T3[GraphExplorer<br/>图探索]
        end
    end

    %% ===== 第六层：总线层 =====
    subgraph L6["Bus 总线层"]
        direction LR
        DBus[DataBus<br/>数据总线]
        EBus[EventBus<br/>事件总线]
        VBus[ViewBus<br/>视图总线]
        Scope[RequestScope<br/>请求上下文]
    end

    %% ===== 第七层：记忆层 =====
    subgraph L7["Memory 记忆层"]
        direction LR
        UMem[用户记忆]
        SMem[会话记忆]
        RCtx[请求上下文]
    end

    %% ===== 第八层：数据层 =====
    subgraph L8["Storage 数据层"]
        direction LR
        Vector[向量存储]
        CodeDB[代码库]
        Meta[元数据]
    end

    %% ===== 连接关系 - 只保留主要流向 =====
    UI --> Router
    Router --> A1
    Router --> A2
    Router --> A3
    A1 --> LangGraph
    A2 --> CapRegistry
    A3 --> StepExec
    StepExec --> CapRegistry
    CapRegistry --> Skills
    CapRegistry --> Tools
    A1 --> DBus
    A2 --> DBus
    A3 --> DBus
    DBus -.-> EBus
    DBus -.-> VBus
    UMem -.-> A3
    SMem -.-> A1
    Vector -.-> S3
    CodeDB -.-> A1
```

## 层次说明

| 层 | 职责 | 核心组件 |
|---|---|---|
| **表现层** | 用户交互、结果展示 | Web界面 |
| **接入层** | 请求路由、场景分发 | Router |
| **Agent层** | 三种智能体并行执行 | GraphQuery/Generic/Autonomous |
| **编排层** | 步骤调度、能力注册 | StepExecutor/LangGraph/CapRegistry |
| **能力层** | Skill/Tool能力单元 | Planner/Render/Text2SQL/... |
| **总线层** | 数据传递、事件传播 | DataBus/EventBus/ViewBus |
| **记忆层** | 用户/会话记忆管理 | UserMemory/SessionMemory |
| **数据层** | 持久化存储 | VectorDB/CodebaseDB |

## 总线机制

```mermaid
graph LR
    subgraph DataBus["DataBus 数据总线"]
        direction TB
        E[entity 完整实体]
        V[view 截断展示]
        L[llm 原始输出]
        S[step 步骤记录]
    end
    
    E -.-> V
    V -.-> L
    L -.-> S
```

```mermaid
graph LR
    subgraph EventBus["EventBus 事件总线"]
        Pub[Publisher 发布者] --> Queue[事件队列]
        Queue --> Sub1[LogListener]
        Queue --> Sub2[MemoryListener]
        Queue --> Sub3[ProgressListener]
    end