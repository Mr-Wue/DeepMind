"""
Default LLM adapter — general-purpose chat / planning / rendering.

Env vars: LLM_MODEL_ID, LLM_API_KEY, LLM_BASE_URL, LLM_TIMEOUT (default 180).
"""

from __future__ import annotations

import os
from typing import ClassVar

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from .base import BaseLLM


def _build_extra_body(model: str) -> dict:
    """Build provider-specific extra_body to disable chain-of-thought."""
    m = (model or "").lower()
    if any(kw in m for kw in ("qwen", "qwq")):
        return {"enable_thinking": False}
    if "deepseek" in m:
        return {"thinking": {"type": "disabled"}}
    return {}


class DefaultLLM(BaseLLM):
    """General-purpose LLM — role="default"."""

    role: ClassVar[str] = "default"

    def _make(self, temperature: float) -> BaseChatModel:
        model = os.getenv("LLM_MODEL_ID")
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL")
        timeout = int(os.getenv("LLM_TIMEOUT", "180"))

        if not all([model, base_url]):
            raise ValueError(
                "DefaultLLM: set LLM_MODEL_ID / LLM_API_KEY / LLM_BASE_URL in .env"
            )

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=1,
            temperature=temperature,
            max_tokens=8192,
            streaming=True,
            extra_body=_build_extra_body(model),
        )
