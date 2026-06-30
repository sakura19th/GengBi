"""主角形象心理学档案模型（Protagonist Profile）。

参考 AI角色一致性创作提示词_心理学驱动版.md，
8 大维度构建主角的完整心理学档案，确保后续续写中主角形象一致性。
融合弗洛伊德人格结构理论、埃里克森心理社会发展阶段、依恋理论、
马斯洛需求层次、施瓦茨价值观理论、阿德勒生活方式、大五人格模型、心理防御机制。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProtagonistProfile(BaseModel):
    """主角形象心理学档案。

    基于人格心理学、依恋理论、动机理论及叙事心理学构建，
    用于确保主角在任何情境下的行为、语言、决策都符合其人格结构。
    所有字段默认空字典，向后兼容。

    8 大维度（参考 AI角色一致性创作提示词_心理学驱动版.md）：
    1. basic_anchors（角色基础锚点）：姓名/年龄/性别身份/职业社会角色/当前人生阶段/外貌标志性特征
    2. personality_system（人格操作系统）：认知风格/人格结构/大五人格/依恋类型/核心自我叙事
    3. motivation_system（动力与动机系统）：核心恐惧/核心渴望/动机优先级/马斯洛需求/价值观光谱/阿德勒生活方式
    4. emotion_defense（情感与防御机制）：情感调节模式/心理防御机制/创伤类型/压力下退行表现
    5. behavior_fingerprint（行为指纹与身体语言）：身体语言系统/日常仪式/物质消费观/语言指纹
    6. relationship_coordinate（关系坐标系）：权力动态/关系角色扮演表/边界感
    7. growth_arc（变化轨迹与弧光）：弧光阶段/未解决埃里克森危机/成长转折点
    8. ooc_redlines（OOC红线与强制约束）：绝对不能做的事/必须出现的标志性行为/决策模式校验优先级

    跟随章节缓存（仅反映至当前章节状态的主角形象，随章节进展演变），
    不存到 Project（不是全局固化的静态档案）。
    """

    model_config = ConfigDict(populate_by_name=True)

    # 1. 角色基础锚点
    basic_anchors: dict[str, Any] = Field(default_factory=dict)
    # 含：姓名/年龄/性别身份/职业社会角色/当前人生阶段（埃里克森）/外貌标志性特征（3个不可变更点）

    # 2. 人格操作系统（核心层）
    personality_system: dict[str, Any] = Field(default_factory=dict)
    # 含：认知风格（实感/直觉、思维/情感、二元/多元）/人格结构（本我/超我/自我）/大五人格（OCEAN 1-10分）/依恋类型/核心自我叙事

    # 3. 动力与动机系统（动力层）
    motivation_system: dict[str, Any] = Field(default_factory=dict)
    # 含：核心恐惧/核心渴望/动机优先级（降序）/马斯洛当前最迫切需求/价值观光谱（施瓦茨前三）/阿德勒生活方式

    # 4. 情感与防御机制
    emotion_defense: dict[str, Any] = Field(default_factory=dict)
    # 含：情感调节模式/常用心理防御机制（主要/次要/压力下退行）/创伤类型（背叛/遗弃/羞辱/侵入）+触发场景+应激反应/压力下退行表现

    # 5. 行为指纹与身体语言（行为层）
    behavior_fingerprint: dict[str, Any] = Field(default_factory=dict)
    # 含：身体语言系统（紧张/撒谎/愤怒/悲伤/心动/放松时）/日常仪式习惯/物质消费观/语言指纹（口头禅/称呼差异/知识影响/情绪变化）

    # 6. 关系坐标系（关系层）
    relationship_coordinate: dict[str, Any] = Field(default_factory=dict)
    # 含：权力动态模式（上位/下位/平等/摇摆）/关系角色扮演表（对权威/平辈/弱者/亲密伴侣/敌人的面具vs真实感受vs潜在冲突）/边界感（硬/软/弹性）

    # 7. 变化轨迹与弧光（变化层）
    growth_arc: dict[str, Any] = Field(default_factory=dict)
    # 含：当前弧光阶段（否认/触发/挣扎/顿悟/践行）/未解决埃里克森危机/成长转折点（触发事件/旧模式最后使用/新选择代价）

    # 8. OOC红线与强制约束
    ooc_redlines: dict[str, Any] = Field(default_factory=dict)
    # 含：绝对不能做的事（列表）/必须出现的标志性行为台词（列表）/决策模式校验优先级

    # 提取元数据
    extracted_at: datetime | None = None
    source_chapter_range: tuple[int, int] | None = None  # 提取来源章节区间（闭区间）
