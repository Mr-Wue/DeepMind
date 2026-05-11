"""
Text2SQL LLM adapter — SQL generation (can point to a dedicated local model).

Env vars (preferred):  TEXT2SQL_MODEL_ID, TEXT2SQL_API_KEY, TEXT2SQL_BASE_URL
Fallback:              LLM_MODEL_ID, LLM_API_KEY, LLM_BASE_URL
"""

from __future__ import annotations

import os
from typing import ClassVar

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from .base import BaseLLM


class Text2SQLLLM(BaseLLM):
    """Text2SQL role — TEXT2SQL_* env vars with fallback to LLM_*."""

    role: ClassVar[str] = "text2sql"

    def _make(self, temperature: float) -> BaseChatModel:
        model = os.getenv("TEXT2SQL_MODEL_ID") or os.getenv("LLM_MODEL_ID")
        api_key = os.getenv("TEXT2SQL_API_KEY") or os.getenv("LLM_API_KEY")
        base_url = os.getenv("TEXT2SQL_BASE_URL") or os.getenv("LLM_BASE_URL")
        timeout = int(os.getenv("TEXT2SQL_TIMEOUT") or os.getenv("LLM_TIMEOUT", "180"))

        if not all([model, base_url]):
            raise ValueError(
                "Text2SQLLLM: set TEXT2SQL_MODEL_ID / TEXT2SQL_API_KEY / TEXT2SQL_BASE_URL "
                "(or the corresponding LLM_* vars as fallback)"
            )

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=1,
            temperature=temperature,
            max_tokens=2048,
            streaming=False,
        )
