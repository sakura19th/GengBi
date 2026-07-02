"""续写流程共享数据模型。

定义大纲、场景、验证报告等结构化产物，供卷级多章节续写流程使用。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# CritiqueIssue category 合法值（对齐 phase_outline_audit.txt 的 13 维度 + phase_verify.txt 的 16 维度）
VALID_CRITIQUE_CATEGORIES: frozenset[str] = frozenset(
    {"consistency", "pacing", "engagement", "structure", "coherence",
     "foreshadowing", "characters", "style",
     "protagonist_consistency",  # 主角一致性（一票否决：出现 critical 级别问题时 passed=false）
     "worldview_consistency",  # 世界观一致性（严格给分：违反底层世界观元规则时 score ≤ 3）
     "user_directive_compliance",  # 用户指令遵从性（严格给分：未满足 required_elements 任一项时 score ≤ 3）
     "custom_rules_compliance",  # 自定义设定遵从性（一票否决：违反 severity=critical 的规则时 passed=false）
     "outline_alignment",  # 大纲一致性（一票否决：未覆盖 ChapterPlan 关键事件时 critical）
     "detail_outline_alignment",  # 细纲一致性（一票否决：偏离细纲场景规划时 critical）
     "chapter_transition",  # 章节衔接（一票否决：与前一章结尾断裂时 critical）
     "rigid_ai_text"}  # 刻板AI文本禁令（严格禁止AI套路文本：出现3处以上典型AI痕迹判major，5处以上或整段为AI套路文本判critical，passed=false）
)

# CritiqueIssue severity 合法值
VALID_CRITIQUE_SEVERITIES: frozenset[str] = frozenset(
    {"critical", "major", "minor"}
)


class Scene(BaseModel):
    """大纲中的单个场景（阶段③）。

    字段说明：
    - ``purpose``：场景目的
    - ``pov``：视角
    - ``scene_type``：场景类型（对话/动作/描写/内心等）
    - ``goal``：场景目标
    - ``conflict``：冲突
    - ``outcome``：结果
    - ``value_shift``：价值转变
    - ``foreshadowing``：伏笔
    - ``exit_hook``：出场钩子
    """

    model_config = ConfigDict(populate_by_name=True)

    purpose: str = ""
    pov: str = ""
    scene_type: str = ""
    goal: str = ""
    conflict: str = ""
    outcome: str = ""
    value_shift: str = ""
    foreshadowing: str = ""
    exit_hook: str = ""


class Outline(BaseModel):
    """续写大纲（阶段③）。

    字段说明：
    - ``continuation_goals``：续写目标
    - ``foreshadowing_plan``：伏笔计划
    - ``scenes``：场景列表（3-7 个）
    """

    model_config = ConfigDict(populate_by_name=True)

    continuation_goals: str = ""
    foreshadowing_plan: str = ""
    scenes: list[Scene] = Field(default_factory=list)


class CritiqueIssue(BaseModel):
    """验证发现的问题（阶段⑤）。

    字段说明：
    - ``category``：类别（consistency/style/structure/engagement）
    - ``severity``：严重度（critical/major/minor）
    - ``location``：问题位置
    - ``description``：问题描述
    - ``suggestion``：修改建议
    """

    model_config = ConfigDict(populate_by_name=True)

    category: str = ""
    severity: str = ""
    location: str = ""
    description: str = ""
    suggestion: str = ""

    @field_validator("category")
    @classmethod
    def validate_category(cls, v):
        # 允许空字符串（默认值），非空时必须为合法枚举
        if v and v not in VALID_CRITIQUE_CATEGORIES:
            raise ValueError(f"无效的 category: {v}，有效值: {VALID_CRITIQUE_CATEGORIES}")
        return v

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v):
        # 允许空字符串（默认值），非空时必须为合法枚举
        if v and v not in VALID_CRITIQUE_SEVERITIES:
            raise ValueError(f"无效的 severity: {v}，有效值: {VALID_CRITIQUE_SEVERITIES}")
        return v


class CritiqueReport(BaseModel):
    """验证报告（阶段⑤）。

    字段说明：
    - ``summary``：总结
    - ``issues``：问题列表
    - ``passed``：是否通过（无 critical/major 问题）
    """

    model_config = ConfigDict(populate_by_name=True)

    summary: str = ""
    issues: list[CritiqueIssue] = Field(default_factory=list)
    passed: bool = False

