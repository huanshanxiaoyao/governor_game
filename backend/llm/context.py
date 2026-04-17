# backend/llm/context.py
"""LLM 调用上下文，传给 LLMClient 用于日志记录。"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMContext:
    """携带调用上下文，LLMClient 据此写 LLMCallLog。

    call_source 必填（用 llm.call_sources 常量）；
    其余字段可选，无游戏上下文时留 None。
    """
    call_source: str
    game_id:     Optional[int] = None
    season:      Optional[int] = None
    user_id:     Optional[int] = None
