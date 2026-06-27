"""ST 世界书导入。

将 SillyTavern 世界书 JSON 转换为 ``ContextEntry`` 列表。

支持两种 ``entries`` 格式：
- ``entries: {dict}``：key 为 entry id，value 为 entry 对象（ST 标准格式）
- ``entries: [list]``：直接为 entry 对象列表

字段映射规则：
- ``uid``：原 ``uid`` 或生成
- ``category``：从原 ``comment`` 或 ``key`` 推断
  （含"人物"/"character"→characters，"地点"/"location"→locations，
  "事件"/"event"→events，"风格"/"style"→style，其他→plot_state）
- ``key``：原 ``key`` 字段（数组）
- ``comment``：原 ``comment``
- ``content``：原 ``content``
- ``order``：原 ``order``（默认 100）
- ``position``：数字→字符串转换（0→before, 1→after, 2→at_depth）
- ``depth``：原 ``depth``（默认 4）
- ``role``：原 ``role``（默认 system）
- ``source_chapter_range``：None（导入条目标记）
- 忽略 ``probability`` 字段
- 未识别字段保留在 ``_raw_st_fields``
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from novelforge.models import ContextEntry
from novelforge.models.context import VALID_POSITIONS, VALID_ROLES
from novelforge.services.storage_service import _generate_id

logger = logging.getLogger(__name__)

# ST position 数字 → 赓笔 字符串映射
# ST position 枚举（world-info.js）：before=0, after=1, ANTop=2, ANBottom=3,
# atDepth=4, EMTop=5, EMBottom=6, outlet=7。
# 赓笔简化为 3 种 position 语义，归并策略：
# - before/ANTop/EMTop → before（worldInfoBefore marker）
# - after/ANBottom/EMBottom/outlet → after（worldInfoAfter marker）
# - atDepth → at_depth（按深度注入历史数组）
POSITION_MAP: dict[int, str] = {
    0: "before",
    1: "after",
    2: "before",      # ANTop
    3: "after",       # ANBottom
    4: "at_depth",    # atDepth
    5: "before",      # EMTop
    6: "after",       # EMBottom
    7: "after",        # outlet（兜底）
}

# category 推断关键词映射
CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("人物", "characters"),
    ("character", "characters"),
    ("地点", "locations"),
    ("location", "locations"),
    ("事件", "events"),
    ("event", "events"),
    ("风格", "style"),
    ("style", "style"),
]

# 默认 category
DEFAULT_CATEGORY = "plot_state"

# 已知的 ST entry 字段名集合（用于分离未识别字段）
KNOWN_ST_FIELDS: frozenset[str] = frozenset(
    {
        "uid",
        "key",
        "keysecondary",
        "comment",
        "content",
        "constant",
        "vectorized",
        "selective",
        "order",
        "position",
        "disable",
        "excludeRecursion",
        "preventRecursion",
        "delayUntilRecursion",
        "probability",
        "useGroupScoring",
        "group",
        "groupOverride",
        "groupWeight",
        "scanDepth",
        "caseSensitive",
        "matchWholeWords",
        "useGroupScoring",
        "automationId",
        "role",
        "depth",
        "extensions",
        "displayIndex",
        "matchPersonaDescription",
        "matchCharacterDescription",
        "matchCharacterPersonality",
        "matchCharacterDepthPrompt",
        "id",  # 列表格式可能用 id
    }
)


def _infer_category(comment: str, keys: list[str]) -> str:
    """从 comment 或 key 推断 category。

    Args:
        comment: 原 comment 字段
        keys: 原 key 字段（数组）

    Returns:
        category 字符串
    """
    # 合并 comment 与 keys 用于匹配
    text = (comment or "").lower()
    for k in keys:
        if k:
            text += " " + k.lower()

    for keyword, category in CATEGORY_KEYWORDS:
        if keyword.lower() in text:
            return category

    return DEFAULT_CATEGORY


def _normalize_position(position: Any) -> str:
    """将 ST position 字段转换为赓笔 position 字符串。

    Args:
        position: 原始 position 值（数字或字符串）

    Returns:
        position 字符串（before/after/at_depth）
    """
    if position is None:
        return "before"

    # 数字类型
    if isinstance(position, (int, float)):
        return POSITION_MAP.get(int(position), "before")

    # 字符串类型
    if isinstance(position, str):
        # 直接是合法值
        if position in VALID_POSITIONS:
            return position
        # 尝试解析为数字
        try:
            num = int(position)
            return POSITION_MAP.get(num, "before")
        except ValueError:
            pass

    return "before"


def _normalize_role(role: Any) -> str:
    """规范化 role 字段。

    Args:
        role: 原始 role 值

    Returns:
        role 字符串（system/user/assistant）
    """
    if role is None:
        return "system"
    if isinstance(role, str) and role in VALID_ROLES:
        return role
    # ST 中 role 可能是数字（0=system, 1=user, 2=assistant）
    if isinstance(role, (int, float)):
        role_map = {0: "system", 1: "user", 2: "assistant"}
        return role_map.get(int(role), "system")
    return "system"


def _convert_entry(raw: dict[str, Any], fallback_id: str = "") -> ContextEntry:
    """转换单个 ST entry 字典为 ContextEntry。

    Args:
        raw: ST entry 字典
        fallback_id: 当 raw 无 uid 时使用的回退 ID

    Returns:
        ContextEntry 对象
    """
    # uid
    uid = raw.get("uid") or raw.get("id") or fallback_id or _generate_id("ctx_")
    uid = str(uid)

    # key（数组）
    key = raw.get("key", [])
    if not isinstance(key, list):
        key = [str(key)] if key else []
    else:
        key = [str(k) for k in key if k is not None]

    # comment
    comment = str(raw.get("comment", ""))

    # content
    content = str(raw.get("content", ""))

    # category（从 comment 或 key 推断）
    category = _infer_category(comment, key)

    # order（默认 100）
    order = raw.get("order", 100)
    try:
        order = int(order)
    except (TypeError, ValueError):
        order = 100

    # position（数字→字符串）
    position = _normalize_position(raw.get("position"))

    # depth（默认 4）
    depth = raw.get("depth", 4)
    try:
        depth = int(depth)
    except (TypeError, ValueError):
        depth = 4

    # role（默认 system）
    role = _normalize_role(raw.get("role"))

    # 收集未识别字段（排除 probability 与已知字段）
    raw_st_fields: dict[str, Any] = {}
    for k, v in raw.items():
        if k not in KNOWN_ST_FIELDS and k != "probability":
            raw_st_fields[k] = v

    return ContextEntry(
        uid=uid,
        category=category,
        key=key,
        comment=comment,
        content=content,
        order=order,
        position=position,
        depth=depth,
        role=role,
        source_chapter_range=None,  # 导入条目标记
        extracted_at=None,
        raw_st_fields=raw_st_fields,
    )


def import_worldbook(file_path: str | Path) -> list[ContextEntry]:
    """导入 ST 世界书 JSON 文件。

    支持两种 ``entries`` 格式：
    - ``entries: {dict}``：key 为 entry id，value 为 entry 对象（ST 标准格式）
    - ``entries: [list]``：直接为 entry 对象列表

    Args:
        file_path: 世界书 JSON 文件路径

    Returns:
        ContextEntry 列表

    Raises:
        FileNotFoundError: 文件不存在
        json.JSONDecodeError: JSON 解析失败
        ValueError: 文件格式不合法
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"世界书文件不存在: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("读取世界书文件失败 %s: %s", path, e)
        raise

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("世界书 JSON 解析失败 %s: %s", path, e)
        raise

    if not isinstance(data, dict):
        raise ValueError(
            f"世界书格式不合法，期望顶层为 dict，实际: {type(data).__name__}"
        )

    entries_field = data.get("entries")
    if entries_field is None:
        logger.warning("世界书 %s 无 entries 字段", path)
        return []

    entries: list[ContextEntry] = []

    if isinstance(entries_field, dict):
        # ST 标准格式：entries 为 dict，key 为 entry id
        for entry_id, entry_data in entries_field.items():
            if not isinstance(entry_data, dict):
                logger.warning(
                    "世界书 entry %s 不是 dict，跳过: %s",
                    entry_id,
                    type(entry_data).__name__,
                )
                continue
            # 如果 entry_data 没有 uid/id，用 dict key 作为回退
            fallback = entry_id if not (entry_data.get("uid") or entry_data.get("id")) else ""
            try:
                entries.append(_convert_entry(entry_data, fallback_id=fallback))
            except Exception as e:
                logger.error("转换世界书 entry %s 失败: %s", entry_id, e)
    elif isinstance(entries_field, list):
        # 列表格式：entries 直接为 entry 对象列表
        for i, entry_data in enumerate(entries_field):
            if not isinstance(entry_data, dict):
                logger.warning(
                    "世界书 entry[%d] 不是 dict，跳过: %s",
                    i,
                    type(entry_data).__name__,
                )
                continue
            try:
                entries.append(_convert_entry(entry_data, fallback_id=f""))
            except Exception as e:
                logger.error("转换世界书 entry[%d] 失败: %s", i, e)
    else:
        raise ValueError(
            f"世界书 entries 字段格式不合法，期望 dict 或 list，实际: "
            f"{type(entries_field).__name__}"
        )

    logger.info("从 %s 导入 %d 条世界书条目", path.name, len(entries))
    return entries
