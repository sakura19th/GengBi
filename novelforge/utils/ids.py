"""ID 生成工具。

提供统一的带前缀唯一 ID 生成函数，替代各服务中重复的 _generate_id 实现。
"""
from __future__ import annotations

import uuid


def generate_id(prefix: str = "") -> str:
    """生成带前缀的唯一 ID。

    Args:
        prefix: ID 前缀（如 "chapter_"、"preset_" 等）

    Returns:
        形如 ``{prefix}{12位hex}`` 的唯一标识符
    """
    return f"{prefix}{uuid.uuid4().hex[:12]}"
