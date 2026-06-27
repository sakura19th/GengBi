"""上下文条目数据模型。

定义 ``ContextEntry``，用于自动上下文提取与 ST 世界书导入。
字段语义参见 spec.md「自动上下文提取」一节。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ContextEntry 合法分类
VALID_CATEGORIES: frozenset[str] = frozenset(
    {"characters", "locations", "events", "style", "plot_state",
     "relationships", "atmosphere", "foreshadowing"}
)

# ContextEntry 合法 position 值（本工具自定义，表示注入位置）
VALID_POSITIONS: frozenset[str] = frozenset({"before", "after", "at_depth"})

# ContextEntry 合法 role 值（仅 at_depth 注入时生效）
VALID_ROLES: frozenset[str] = frozenset({"system", "user", "assistant"})


class ContextEntry(BaseModel):
    """上下文提取条目（类世界书格式）。

    字段说明：
    - ``uid``：唯一标识
    - ``category``：分类（characters/locations/events/style/plot_state）
    - ``key``：关键词数组，用于 UI 显示和搜索过滤
    - ``content``：详细内容（提取时最长 200 字）
    - ``order``：排序权重，默认 100，**统一升序**（数字越小越优先）
    - ``position``：注入位置（before/after/at_depth）
      - before → worldInfoBefore marker
      - after → worldInfoAfter marker
      - at_depth → 按深度注入历史数组
    - ``depth``：仅 at_depth 时有效，默认 4
    - ``role``：消息角色，默认 system；仅 at_depth 注入时生效，
      worldInfoBefore/After marker 注入时固定为 system
    - ``source_chapter_range``：提取来源章节区间（闭区间，如 (0, 4)）；
      导入 ST 世界书时为 None
    - ``extracted_at``：提取时间
    - ``_raw_st_fields``：未识别字段

    注意：不包含 ``probability`` 字段（本工具 ContextEntry 始终注入，无概率激活）。
    """

    model_config = ConfigDict(populate_by_name=True)

    uid: str
    category: str = "characters"
    key: list[str] = Field(default_factory=list)
    comment: str = ""
    content: str = ""
    order: int = 100
    position: str = "before"
    depth: int = 4
    role: str = "system"
    source_chapter_range: tuple[int, int] | None = None
    extracted_at: datetime | None = None
    raw_st_fields: dict = Field(default_factory=dict, alias="_raw_st_fields")

    @field_validator("category")
    @classmethod
    def validate_category(cls, v):
        # 允许空字符串（None/""），默认值 "characters" 已在 VALID_CATEGORIES 中
        if v and v not in VALID_CATEGORIES:
            raise ValueError(f"无效的 category: {v}，有效值: {VALID_CATEGORIES}")
        return v

    @field_validator("position")
    @classmethod
    def validate_position(cls, v):
        # 允许空字符串，默认值 "before" 已在 VALID_POSITIONS 中
        if v and v not in VALID_POSITIONS:
            raise ValueError(f"无效的 position: {v}，有效值: {VALID_POSITIONS}")
        return v

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        # 允许空字符串，默认值 "system" 已在 VALID_ROLES 中
        if v and v not in VALID_ROLES:
            raise ValueError(f"无效的 role: {v}，有效值: {VALID_ROLES}")
        return v
