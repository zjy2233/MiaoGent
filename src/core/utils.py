"""Shared utility functions."""

from typing import Any


def content_str(content: str | list[dict[str, Any] | str]) -> str:
    """Extract text content from LLM message content (str or list)."""
    if isinstance(content, str):
        return content
    texts: list[str] = []
    for part in content:
        if isinstance(part, str):
            texts.append(part)
        elif isinstance(part, dict):
            texts.append(part.get("text", ""))
    return "".join(texts)
