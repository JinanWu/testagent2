"""one-shot Published API的固定互動工具授權政策。"""
from __future__ import annotations


ONE_SHOT_PUBLISHED禁止工具: frozenset[tuple[str, str]] = frozenset({
    ("clarify", "clarify@published-v1"),
})
