"""写作预设数据模型。

定义 ``Prompt`` 与 ``WritingPreset``，对齐 SillyTavern 预设 JSON 格式。
字段语义参见 spec.md「写作预设管理（ST 兼容）」一节。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 全局 prompt_order 分组标识，本工具统一使用 100000
GLOBAL_CHARACTER_ID: int = 100000


class Prompt(BaseModel):
    """预设中的单条提示。

    字段对齐 ST Prompt 结构：
    - ``identifier``：唯一标识（如 main、chatHistory、worldInfoBefore）
    - ``role``：消息角色（system/user/assistant）
    - ``position``：相对排列位置（start/end，对齐 ST 语义）
    - ``injection_position``：0=RELATIVE（按 order 排列）/ 1=ABSOLUTE（按深度注入历史）
    - ``injection_depth``：ABSOLUTE 模式下插入到倒数第 N 条历史消息之前，默认 4
    - ``injection_order``：同深度内的排序权重，默认 100
    - ``marker``：非空时为占位符提示（chatHistory/worldInfoBefore/worldInfoAfter）
    - ``system_prompt``：True 表示系统提示，不可删除但可禁用
    - ``_raw_st_fields``：导入 ST 预设时未识别的字段，导出时原样写回
    """

    model_config = ConfigDict(populate_by_name=True)

    identifier: str
    name: str = ""
    role: str = "system"
    content: str = ""
    system_prompt: bool = False
    marker: str | None = None
    position: str = "start"
    injection_position: int = 0
    injection_depth: int = 4
    injection_order: int = 100
    forbid_overrides: bool = False
    extension: dict = Field(default_factory=dict)
    enabled: bool = True
    # ST 未识别字段，Python 属性名为 raw_st_fields，JSON 序列化时用别名 _raw_st_fields
    raw_st_fields: dict = Field(default_factory=dict, alias="_raw_st_fields")

    @field_validator("injection_position")
    @classmethod
    def validate_injection_position(cls, v):
        # 0=RELATIVE / 1=ABSOLUTE，仅允许这两个取值
        if v not in (0, 1):
            raise ValueError(f"injection_position 必须为 0 或 1，当前值: {v}")
        return v


class PromptOrderEntry(BaseModel):
    """prompt_order 中单条排序项。"""

    model_config = ConfigDict(populate_by_name=True)

    identifier: str
    enabled: bool = True


class PromptOrderGroup(BaseModel):
    """prompt_order 分组，character_id=100000 表示全局顺序。"""

    model_config = ConfigDict(populate_by_name=True)

    character_id: int = GLOBAL_CHARACTER_ID
    order: list[PromptOrderEntry] = Field(default_factory=list)


class WritingPreset(BaseModel):
    """写作预设。

    含提示列表、排序配置与生成参数。兼容 ST 预设 JSON 导入导出。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    prompts: list[Prompt] = Field(default_factory=list)
    prompt_order: list[PromptOrderGroup] = Field(default_factory=list)
    generation_params: dict = Field(default_factory=dict)
    enabled: bool = True
    raw_st_fields: dict = Field(default_factory=dict, alias="_raw_st_fields")
