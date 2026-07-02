"""ID 生成工具。

提供统一的带前缀唯一 ID 生成函数，替代各服务中重复的 _generate_id 实现。
同时提供 ``validate_id`` 用于校验由外部数据（导入 zip / ST JSON）流入文件路径的 ID，
防止路径穿越攻击。
"""
from __future__ import annotations

import re
import uuid

# 合法 ID 字符集：仅允许字母、数字、下划线、连字符
# 拒绝路径分隔符 / \、点号组合 ..、前导点、空字节、控制字符等
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def generate_id(prefix: str = "") -> str:
    """生成带前缀的唯一 ID。

    Args:
        prefix: ID 前缀（如 "chapter_"、"preset_" 等）

    Returns:
        形如 ``{prefix}{12位hex}`` 的唯一标识符
    """
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def validate_id(value: str, field_name: str = "id") -> str:
    """校验 ID 是否安全可用作文件路径组成部分。

    拒绝包含路径分隔符（``/`` ``\\``）、``..``、空字节、控制字符或为空的 ID，
    防止恶意 ID（如 ``../config``）造成路径穿越。

    合法字符集为 ``[A-Za-z0-9_\\-]``，覆盖所有内置 ID 前缀
    （``proj_``/``ep_``/``ch_``/``wb_``/``nf_regex_`` + uuid hex）。

    Args:
        value: 待校验的 ID 字符串
        field_name: 字段名（用于错误信息，默认 "id"）

    Returns:
        校验通过的原值

    Raises:
        ValueError: ID 为空、含非法字符或含路径穿越片段
    """
    if not value or not _ID_RE.match(value):
        raise ValueError(
            f"非法 {field_name}: {value!r}（仅允许字母数字下划线连字符，且不能为空）"
        )
    if ".." in value or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"非法 {field_name}: {value!r}（含路径字符）")
    return value
