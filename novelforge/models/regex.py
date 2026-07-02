"""正则脚本数据模型。

定义 ``RegexScript``，兼容 SillyTavern 正则脚本 JSON 格式。
字段语义参见 spec.md「正则脚本引擎（ST 兼容）」一节。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

# placement 取值常量（对齐 ST）
PLACEMENT_USER_INPUT: int = 1
PLACEMENT_AI_OUTPUT: int = 2
PLACEMENT_WORLD_INFO: int = 5

# 本工具识别的 placement 合法值集合
VALID_PLACEMENTS: frozenset[int] = frozenset(
    {PLACEMENT_USER_INPUT, PLACEMENT_AI_OUTPUT, PLACEMENT_WORLD_INFO}
)


class RegexScript(BaseModel):
    """正则脚本。

    字段对齐 ST RegexScript 结构：
    - ``findRegex``：``/pattern/flags`` 格式
    - ``replaceString``：替换字符串（``$1``/``$<name>``/``{{match}}`` 语法）
    - ``trimStrings``：替换前后裁剪指定字符串
    - ``placement``：应用时机列表（USER_INPUT=1/AI_OUTPUT=2/WORLD_INFO=5）
    - ``minDepth``/``maxDepth``：历史消息深度过滤范围（depth=0 为最新消息）
    - ``substituteRegex``：允许替换后宏替换
    - ``markupSafety``：标记安全模式
    - ``_raw_st_fields``：未识别字段
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    scriptName: str = ""
    findRegex: str = ""
    replaceString: str = ""
    trimStrings: list[str] = Field(default_factory=list)
    placement: list[int] = Field(default_factory=list)
    disabled: bool = False
    markdownOnly: bool = False
    promptOnly: bool = False
    runOnEdit: bool = False
    substituteRegex: bool | int = False
    minDepth: int = 0
    maxDepth: int = 0
    markupSafety: bool = False
    raw_st_fields: dict = Field(default_factory=dict, alias="_raw_st_fields")

    @field_validator("placement")
    @classmethod
    def validate_placement(cls, v):
        # 校验每个 placement 取值是否在合法集合内
        for p in v:
            if p not in VALID_PLACEMENTS:
                raise ValueError(f"无效的 placement: {p}，有效值: {VALID_PLACEMENTS}")
        return v

    @field_validator("id")
    @classmethod
    def _validate_path_id(cls, v: str) -> str:
        """防御性校验：拒绝含路径字符的 ID，防止导入恶意数据时路径穿越。"""
        if v and ("/" in v or "\\" in v or ".." in v or "\x00" in v):
            raise ValueError(f"非法 ID（含路径字符）: {v!r}")
        return v
