"""卷级多章节续写数据模型。

定义卷级多章节 Agent 续写流程的结构化产物与运行配置。
覆盖前文深度分析、卷大纲、大纲审计、章节产物聚合与卷运行配置。
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from novelforge.models.agent import CritiqueReport, Outline

# 分析深度合法值（对齐 max_analysis_entries 自动档位）
VALID_ANALYSIS_DEPTHS: frozenset[str] = frozenset(
    {"light", "standard", "thorough", "exhaustive"}
)

# 默认大纲审计维度（12 维度：原 8 维度 + 主角一致性 + 世界观一致性 + 用户指令遵从性 + 自定义设定遵从性）
DEFAULT_AUDIT_DIMENSIONS: list[str] = [
    "consistency",
    "pacing",
    "engagement",
    "structure",
    "coherence",
    "foreshadowing",
    "characters",
    "style",
    "protagonist_consistency",  # 主角一致性（一票否决：score ≤ 4 时整体 passed=false）
    "worldview_consistency",  # 世界观一致性（严格给分：违反底层世界观元规则时 score ≤ 3）
    "user_directive_compliance",  # 用户指令遵从性（严格给分：未满足 required_elements 任一项时 score ≤ 3）
    "custom_rules_compliance",  # 自定义设定遵从性（一票否决：违反 severity=critical 的规则时 passed=false）
]

# ChapterPlan.plot_role 合法值（起承转合 + 高潮 + 过渡）
VALID_PLOT_ROLES: frozenset[str] = frozenset(
    {"起", "承", "转", "合", "高潮", "过渡"}
)

# 稳定顺序的合法值展示字符串（用于错误消息，避免 frozenset repr 顺序不确定）
_VALID_PLOT_ROLES_DISPLAY = "起/承/转/合/高潮/过渡"

# 模块级 logger（用于 plot_role 容错归一化时记录 warning）
_logger = logging.getLogger(__name__)

# 推进速度合法值（slow=缓速/medium=中速/fast=快速）
VALID_PACING_SPEEDS: frozenset[str] = frozenset(
    {"slow", "medium", "fast"}
)


class DeepAnalysis(BaseModel):
    """前文深度分析产物。

    字段说明（故事状态）：
    - ``structure_position``：结构定位
    - ``tone``：基调
    - ``core_conflict_status``：核心冲突状态
    - ``stakes``：利害关系
    - ``active_characters``：活跃人物
    - ``plot_threads``：活跃剧情线
    - ``unresolved_promises``：未兑现承诺
    - ``world_state``：世界状态

    字段说明（深度分析）：
    - ``plot_arrangement_analysis``：布局分析
    - ``chapter_structure_pattern``：章节结构模式
    - ``tension_curve_pattern``：张力曲线模式
    - ``hook_patterns``：钩子模式
    - ``style_analysis``：风格分析
    - ``dialogue_analysis``：对白分析
    - ``pacing_analysis``：节奏分析
    - ``character_arc_patterns``：人物弧光模式

    字段说明（结构化清单）：
    - ``foreshadowing_inventory``：伏笔清单
    - ``common_tropes``：常用桥段
    - ``settings_database``：场景设定库
    - ``recurring_elements``：复现元素
    - ``key_phrases``：关键短语
    - ``user_directive_analysis``：用户剧情输出需求解析（含 required_elements/emphasized_elements/interpretation/conflicts 四子字段）
    """

    model_config = ConfigDict(populate_by_name=True)

    # 故事状态
    structure_position: str = ""
    tone: str = ""
    core_conflict_status: str = ""
    stakes: str = ""
    active_characters: list[dict[str, Any]] = Field(default_factory=list)
    plot_threads: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_promises: list[dict[str, Any]] = Field(default_factory=list)
    world_state: str = ""

    # 深度分析
    plot_arrangement_analysis: str = ""
    chapter_structure_pattern: str = ""
    tension_curve_pattern: str = ""
    hook_patterns: str = ""
    style_analysis: str = ""
    dialogue_analysis: str = ""
    pacing_analysis: str = ""
    character_arc_patterns: list[dict[str, Any]] = Field(default_factory=list)

    # 结构化清单
    foreshadowing_inventory: list[dict[str, Any]] = Field(default_factory=list)
    common_tropes: list[dict[str, Any]] = Field(default_factory=list)
    settings_database: list[dict[str, Any]] = Field(default_factory=list)
    recurring_elements: list[dict[str, Any]] = Field(default_factory=list)
    key_phrases: list[dict[str, Any]] = Field(default_factory=list)

    # 用户剧情输出需求解析（结构化）
    user_directive_analysis: dict[str, Any] = Field(default_factory=dict)


class ChapterPlan(BaseModel):
    """卷大纲中的单章节计划。

    字段说明：
    - ``index``：章节序号
    - ``title``：章节标题
    - ``summary``：章节摘要
    - ``plot_role``：剧情角色（起/承/转/合/高潮/过渡）
    - ``key_events``：关键事件
    - ``characters_involved``：涉及人物
    - ``foreshadowing``：伏笔
    - ``chapter_hook``：章节钩子
    - ``target_words``：目标字数
    """

    model_config = ConfigDict(populate_by_name=True)

    index: int = 0
    title: str = ""
    summary: str = ""
    plot_role: str = ""
    key_events: list[str] = Field(default_factory=list)
    characters_involved: list[str] = Field(default_factory=list)
    foreshadowing: str = ""
    chapter_hook: str = ""
    target_words: int = 2000

    @field_validator("plot_role")
    @classmethod
    def validate_plot_role(cls, v):
        # 允许空字符串（默认值）
        if not v:
            return v
        # 去除前后空白，避免 LLM 偶发输出带空格的值被误判
        v = v.strip()
        if not v:
            return v
        # 精确匹配
        if v in VALID_PLOT_ROLES:
            return v
        # 容错归一化：对组合值（如"承转"、"起承"）尝试拆分取首个合法值
        # 先匹配 2 字符合法值（高潮/过渡），避免被单字符拆分误匹配
        for role in ("高潮", "过渡"):
            if v.startswith(role):
                _logger.warning(
                    "plot_role 容错归一化: %r -> %r（取首个合法值）", v, role
                )
                return role
        # 再匹配单字符合法值
        for ch in v:
            if ch in VALID_PLOT_ROLES:
                _logger.warning(
                    "plot_role 容错归一化: %r -> %r（取首个合法值）", v, ch
                )
                return ch
        # 完全不匹配，抛错（错误消息用稳定顺序）
        raise ValueError(
            f"无效的 plot_role: {v}，有效值: {_VALID_PLOT_ROLES_DISPLAY}"
        )


class VolumeOutline(BaseModel):
    """卷大纲。

    字段说明：
    - ``volume_title``：卷标题
    - ``volume_goals``：卷目标
    - ``plot_arrangement_analysis``：布局分析
    - ``pacing_plan``：节奏计划
    - ``foreshadowing_plan``：伏笔计划
    - ``chapter_count``：章节数
    - ``chapters``：章节计划列表
    """

    model_config = ConfigDict(populate_by_name=True)

    volume_title: str = ""
    volume_goals: str = ""
    plot_arrangement_analysis: str = ""
    pacing_plan: str = ""
    foreshadowing_plan: str = ""
    chapter_count: int = 0
    chapters: list[ChapterPlan] = Field(default_factory=list)


class AuditDimension(BaseModel):
    """大纲审计单维度结果。

    字段说明：
    - ``dimension``：维度名称
    - ``score``：分数（1-10）
    - ``issues``：问题列表
    - ``suggestions``：建议列表
    """

    model_config = ConfigDict(populate_by_name=True)

    dimension: str = ""
    score: int = 0
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class OutlineAuditReport(BaseModel):
    """大纲审计报告。

    字段说明：
    - ``dimensions``：各维度审计结果
    - ``overall_assessment``：总体评估
    - ``passed``：是否通过
    - ``revised_outline``：修订后的卷大纲
    """

    model_config = ConfigDict(populate_by_name=True)

    dimensions: list[AuditDimension] = Field(default_factory=list)
    overall_assessment: str = ""
    passed: bool = False
    revised_outline: VolumeOutline | None = None


class ChapterStageArtifact(BaseModel):
    """单章节单阶段产物。

    按阶段次序记录每一步产物，供 UI 按次序展示与完整内容查看。

    字段说明：
    - ``stage_type``：阶段类型（"outline"=细纲 / "draft"=初稿 / "audit"=审计 / "revise"=修改正文）
    - ``round_index``：轮次索引（outline/draft=0；audit/revise 按轮次 1,2,3...）
    - ``content``：正文（draft/revise 阶段保存重写后正文）
    - ``critique``：审计报告（audit 阶段）
    - ``guidance``：修订指导 dict（revise 阶段）
    - ``outline``：细纲对象（outline 阶段）
    """

    model_config = ConfigDict(populate_by_name=True)

    stage_type: str = ""
    round_index: int = 0
    content: str = ""
    critique: CritiqueReport | None = None
    guidance: dict[str, Any] | None = None
    outline: Outline | None = None


class ChapterArtifacts(BaseModel):
    """单章节产物聚合。

    字段说明：
    - ``chapter_index``：章节序号
    - ``outline``：章节大纲
    - ``critique``：首次验证报告
    - ``final_critique``：最终验证报告
    - ``revision_rounds``：修订轮次
    - ``content``：章节正文（最终版）
    - ``stages``：完整阶段产物序列（细纲/初稿/审计①/修改正文①/审计②/...），向后兼容默认空列表
    """

    model_config = ConfigDict(populate_by_name=True)

    chapter_index: int = 0
    outline: Outline | None = None
    critique: CritiqueReport | None = None
    final_critique: CritiqueReport | None = None
    revision_rounds: int = 0
    content: str = ""
    stages: list[ChapterStageArtifact] = Field(default_factory=list)


class VolumeArtifacts(BaseModel):
    """卷级流程产物聚合。

    字段说明：
    - ``deep_analysis``：前文深度分析产物
    - ``volume_outline``：卷大纲
    - ``audit_report``：大纲审计报告（最后一轮，向后兼容）
    - ``audit_reports``：多轮审计报告列表
    - ``final_outline``：最终卷大纲
    - ``chapter_artifacts``：各章节产物列表
    - ``phase_logs``：各阶段日志
    """

    model_config = ConfigDict(populate_by_name=True)

    deep_analysis: DeepAnalysis | None = None
    volume_outline: VolumeOutline | None = None
    audit_report: OutlineAuditReport | None = None
    audit_reports: list[OutlineAuditReport] = Field(default_factory=list)
    final_outline: VolumeOutline | None = None
    chapter_artifacts: list[ChapterArtifacts] = Field(default_factory=list)
    phase_logs: list[str] = Field(default_factory=list)


class VolumeRunConfig(BaseModel):
    """卷级 Agent 运行配置。

    字段说明：
    - ``chapter_count``：卷内章节数（2-20）
    - ``target_words_per_chapter``：每章目标字数
    - ``analysis_depth``：分析深度（light/standard/thorough/exhaustive）
    - ``max_analysis_entries``：分析条目上限（0 表示按深度自动）
    - ``analysis_chunk_tokens``：深度分析切分 token 上限（0=不切分全量发送，>0=按该 token 数切分）
    - ``analysis_chunk_strategy``：切分策略（sequential=按章节顺序切分）
    - ``enable_outline_audit``：是否启用大纲审计
    - ``audit_dimensions``：审计维度列表
    - ``audit_rounds``：大纲审计轮次（1-3）
    - ``pacing_speed``：推进速度（slow/medium/fast）
    - ``enable_chapter_verify``：是否启用章节验证
    - ``enable_chapter_revise``：是否启用章节修订
    - ``max_revise_rounds_per_chapter``：每章最大修订轮次
    - ``checkpoints``：暂停点开关
    """

    model_config = ConfigDict(populate_by_name=True)

    chapter_count: int = 5
    target_words_per_chapter: int = 2000
    analysis_depth: str = "standard"
    max_analysis_entries: int = 0
    analysis_chunk_tokens: int = 0  # 0=不切分全量发送, >0=按该 token 数切分
    analysis_chunk_strategy: str = "sequential"  # sequential=按章节顺序切分
    enable_outline_audit: bool = True
    audit_dimensions: list[str] = Field(
        default_factory=lambda: list(DEFAULT_AUDIT_DIMENSIONS)
    )
    audit_rounds: int = 1
    pacing_speed: str = "medium"
    enable_chapter_verify: bool = True
    enable_chapter_revise: bool = True
    max_revise_rounds_per_chapter: int = 1
    checkpoints: dict[str, bool] = Field(
        default_factory=lambda: {
            "after_deep_analysis": False,
            "after_volume_outline": False,
            "before_audit": True,
            "after_audit": False,
            "after_chapter": False,
        }
    )

    @field_validator("chapter_count")
    @classmethod
    def validate_chapter_count(cls, v):
        if not (2 <= v <= 20):
            raise ValueError(f"无效的 chapter_count: {v}，有效范围 2-20")
        return v

    @field_validator("analysis_depth")
    @classmethod
    def validate_analysis_depth(cls, v):
        if v not in VALID_ANALYSIS_DEPTHS:
            raise ValueError(
                f"无效的 analysis_depth: {v}，有效值: {VALID_ANALYSIS_DEPTHS}"
            )
        return v

    @field_validator("audit_rounds")
    @classmethod
    def validate_audit_rounds(cls, v):
        if not (1 <= v <= 3):
            raise ValueError(f"无效的 audit_rounds: {v}，有效范围 1-3")
        return v

    @field_validator("pacing_speed")
    @classmethod
    def validate_pacing_speed(cls, v):
        if v not in VALID_PACING_SPEEDS:
            raise ValueError(
                f"无效的 pacing_speed: {v}，有效值: {VALID_PACING_SPEEDS}"
            )
        return v
