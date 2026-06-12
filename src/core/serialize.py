"""Serialize LLM inputs/outputs for tracing spans."""

from __future__ import annotations

import json
from typing import Any


def _serialize_llm_input(input_data: Any, limit: int = 8192) -> str:
    """Serialize LLM messages array to JSON string, truncated to limit chars."""
    try:
        if isinstance(input_data, dict):
            msgs = input_data.get("messages", [])
        elif isinstance(input_data, list):
            msgs = input_data
        else:
            msgs = []
        if msgs and isinstance(msgs, list) and len(msgs) > 0 and isinstance(msgs[0], list):
            msgs = msgs[0]
        serializable: list[dict] = []
        for m in msgs:
            if hasattr(m, "type") and hasattr(m, "content"):
                content = m.content
                if isinstance(content, list):
                    content = "".join(
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                serializable.append({"type": m.type, "content": str(content)[:2000]})
            elif isinstance(m, dict):
                serializable.append(m)
            else:
                serializable.append({"type": "unknown", "content": str(m)[:2000]})
        result = json.dumps(serializable, ensure_ascii=False, default=str)
        return result if len(result) <= limit else result[:limit]
    except Exception:
        return str(input_data)[:limit]


def _serialize_llm_output(output: Any, limit: int = 4096) -> str:
    """Serialize LLM output to JSON, truncated to limit chars."""
    try:
        content = None
        if hasattr(output, "content"):
            content = output.content
        elif isinstance(output, dict):
            content = output.get("content")
        if content is None:
            return str(output)[:limit]
        if isinstance(content, str):
            return json.dumps({"content": content[:limit]}, ensure_ascii=False)
        if isinstance(content, list):
            text = "".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
            return json.dumps({"content": text[:limit]}, ensure_ascii=False)
        return json.dumps({"content": str(content)[:limit]}, ensure_ascii=False)
    except Exception:
        return str(output)[:limit]


def _short_repr(x: Any, limit: int = 200) -> str:
    if isinstance(x, (dict, list)):
        s = json.dumps(x, ensure_ascii=False).replace("\n", " ").strip()
    elif isinstance(x, bytes):
        s = x.decode("utf-8", errors="replace").replace("\n", " ").strip()
    else:
        s = str(x).replace("\n", " ").strip()
    if len(s) > limit:
        s = s[:limit] + "..."
    return s
