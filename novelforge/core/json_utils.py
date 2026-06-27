"""JSON 解析工具函数。

提供 markdown fence 去除和宽松 JSON 解析，供 context_extractor 和 agent_orchestrator 共用。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# JSON 输入大小上限（50MB），防止解析超大文本导致内存溢出
MAX_JSON_SIZE = 50 * 1024 * 1024


def strip_markdown_fences(text: str) -> str:
    """去除 markdown 代码块标记（```json ... ```）。

    Args:
        text: 原始文本

    Returns:
        去除标记后的文本
    """
    text = text.strip()
    # 去除开头的 ```json 或 ``` 标记
    if text.startswith("```"):
        # 找到第一个换行符
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        else:
            text = text[3:]
    # 去除结尾的 ``` 标记
    if text.endswith("```"):
        text = text[: -3]
    return text.strip()


def parse_json_response(text: str) -> Any:
    """宽松解析 LLM 返回的 JSON 文本。

    先去除 markdown fence，再 json.loads。
    若失败，尝试提取第一个 { 到最后一个 } 之间的子串再解析。

    Args:
        text: LLM 返回的文本

    Returns:
        解析后的 Python 对象（dict 或 list）

    Raises:
        json.JSONDecodeError: 解析失败
    """
    # 检查输入长度，防止解析超大文本导致内存溢出
    if len(text) > MAX_JSON_SIZE:
        size_mb = len(text) / (1024 * 1024)
        limit_mb = MAX_JSON_SIZE // (1024 * 1024)
        raise ValueError(
            f"JSON 文本过大（{size_mb:.1f}MB），超过上限 {limit_mb}MB"
        )
    cleaned = strip_markdown_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 宽松模式：提取第一个 { 或 [ 到最后一个 } 或 ]
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = cleaned.find(start_char)
        end = cleaned.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("无法解析 JSON", cleaned, 0)
