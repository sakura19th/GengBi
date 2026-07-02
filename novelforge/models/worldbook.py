"""全局世界书数据模型。

定义 ``WorldBook``，用于全局世界书的文件系统持久化。
复用 ``ContextEntry`` 作为条目模型，对齐 SillyTavern 世界书格式。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from novelforge.models.context import ContextEntry


class WorldBook(BaseModel):
    """全局世界书。

    字段说明：
    - ``id``：唯一标识
    - ``name``：世界书名称
    - ``entries``：条目列表（复用 ContextEntry）
    - ``enabled``：是否启用
    - ``created_at``：创建时间
    - ``updated_at``：更新时间
    - ``_raw_st_fields``：导入 ST 世界书时未识别的顶层字段
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    entries: list[ContextEntry] = Field(default_factory=list)
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw_st_fields: dict = Field(default_factory=dict, alias="_raw_st_fields")

    @field_validator("id")
    @classmethod
    def _validate_path_id(cls, v: str) -> str:
        """防御性校验：拒绝含路径字符的 ID，防止导入恶意数据时路径穿越。"""
        if v and ("/" in v or "\\" in v or ".." in v or "\x00" in v):
            raise ValueError(f"非法 ID（含路径字符）: {v!r}")
        return v
