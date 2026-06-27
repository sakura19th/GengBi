"""Agent 多阶段续写数据模型。

定义多轮 Agent 续写流程的结构化产物与运行配置。
对齐 novel-continuation-agent skill 的 6 阶段工作流。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# CritiqueIssue category 合法值（对齐 spec 阶段⑤验证维度）
VALID_CRITIQUE_CATEGORIES: frozenset[str] = frozenset(
    {"consistency", "style", "structure", "engagement"}
)

# CritiqueIssue severity 合法值
VALID_CRITIQUE_SEVERITIES: frozenset[str] = frozenset(
    {"critical", "major", "minor"}
)


class StorySnapshot(BaseModel):
    """前文分析产物（阶段①②）。

    字段说明：
    - ``structure_position``：结构定位（开头/发展/高潮/结尾）
    - ``tone``：基调
    - ``core_conflict_status``：核心冲突状态
    - ``stakes``：利害关系
    - ``active_characters``：活跃人物（含 name/status/motivation）
    - ``plot_threads``：活跃剧情线
    - ``unresolved_promises``：未兑现承诺
    - ``foreshadowing_tracker``：伏笔追踪（契诃夫之枪）
    - ``world_state``：世界状态
    - ``style_profile``：风格指纹
    """

    model_config = ConfigDict(populate_by_name=True)

    structure_position: str = ""
    tone: str = ""
    core_conflict_status: str = ""
    stakes: str = ""
    active_characters: list[dict[str, Any]] = Field(default_factory=list)
    plot_threads: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_promises: list[dict[str, Any]] = Field(default_factory=list)
    foreshadowing_tracker: list[dict[str, Any]] = Field(default_factory=list)
    world_state: str = ""
    style_profile: str = ""


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


class AgentArtifacts(BaseModel):
    """Agent 流程产物快照。

    字段说明：
    - ``snapshot``：前文分析产物
    - ``outline``：大纲
    - ``critique``：首次验证报告
    - ``final_critique``：最终验证报告
    - ``revision_rounds``：修订轮次
    - ``phase_logs``：各阶段日志
    """

    model_config = ConfigDict(populate_by_name=True)

    snapshot: StorySnapshot | None = None
    outline: Outline | None = None
    critique: CritiqueReport | None = None
    final_critique: CritiqueReport | None = None
    revision_rounds: int = 0
    phase_logs: list[dict[str, Any]] = Field(default_factory=list)


class AgentRunConfig(BaseModel):
    """Agent 运行配置。

    字段说明：
    - ``phases``：阶段开关
    - ``checkpoints``：暂停点开关
    - ``max_revise_rounds``：最大修订轮次
    - ``per_phase_overrides``：每阶段参数覆盖（model/temperature）
    """

    model_config = ConfigDict(populate_by_name=True)

    phases: dict[str, bool] = Field(
        default_factory=lambda: {
            "analysis": True,
            "outline": True,
            "writing": True,
            "verify": True,
            "revise": True,
        }
    )
    checkpoints: dict[str, bool] = Field(
        default_factory=lambda: {
            "after_outline": False,
            "after_verify": False,
        }
    )
    max_revise_rounds: int = 1
    per_phase_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
