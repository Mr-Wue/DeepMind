"""
req-parse SubAgent — 文档解析：.docx → 实体提取 → 入库。

策略: 绑定 parse_docx_outline / extract_entities / store_entities 三个工具，
     遵循 skills/req-parse/SKILL.md 的定义顺序执行。
"""

from __future__ import annotations

import sys

from utils.paths import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))

from deepagents.middleware.subagents import SubAgent


def build_req_parse_subagent(middleware=None) -> SubAgent:
    """构建 req-parse 子 Agent。

    Args:
        middleware: 中间件列表，如 [InvocationLoggingHandler.as_middleware()]
    """
    from tools import extract_entities, parse_docx_outline, store_entities

    skills_dir = "/skills/req-parse/"

    return SubAgent(
        name="req-parse",
        description=(
            "解析需求文档（.docx 文件），提取结构化实体并入库。"
            "处理：读取 Word 文档、从标题结构中提取产品/需求模型/需求项、存入数据库。"
            "当用户要求解析、提取或存储需求文档时使用此子 Agent。"
        ),
        system_prompt=(
            "⚠️ 行为方针：诚实 > 完成、透明 > 推进、确认 > 猜测。"
            "你是文档解析专家，严格按照 req-parse 技能执行：\n"
            "store_entities 工具已有展示数据并interrupt确认,无需再次确认\n"
            "1. 调用 parse_docx_outline 获取 .docx 文件的结构化大纲\n"
            "2. 调用 extract_entities 使用 llm_structure 将段落分类为实体类型\n"
            "3. 调用 store_entities 将所有提取的实体持久化到数据库\n"
            "完成后向主 Agent 报告最终统计：产品数、需求模型数、需求项数。\n"
            "除用户主动要求，否则不进行其他额外的操作。\n"
            "所有输出使用中文。"
        ),
        tools=[parse_docx_outline, extract_entities, store_entities],
        skills=[skills_dir],
        middleware=middleware or [],
    )
