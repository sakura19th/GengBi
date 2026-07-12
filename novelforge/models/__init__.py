"""数据模型子包：pydantic 数据模型定义。

统一导出所有数据模型，方便外部按 ``from novelforge.models import Project`` 导入。
"""
from __future__ import annotations

from novelforge.models.agent import (
    CritiqueIssue,
    CritiqueReport,
    Outline,
    Scene,
)
from novelforge.models.chapter import Chapter, Continuation
from novelforge.models.context import (
    VALID_CATEGORIES,
    VALID_POSITIONS,
    VALID_ROLES,
    ContextEntry,
)
from novelforge.models.flow_plugin import (
    VALID_ACCEPT_MODES,
    VALID_AGENT_TYPES,
    VALID_UI_MODES,
    FlowPlugin,
    FlowStage,
)
from novelforge.models.ontology import WorldOntology
from novelforge.models.preset import (
    GLOBAL_CHARACTER_ID,
    Prompt,
    PromptOrderEntry,
    PromptOrderGroup,
    WritingPreset,
)
from novelforge.models.project import (
    ChapterSplitRule,
    ManualOverride,
    NovelProfile,
    Project,
)
from novelforge.models.protagonist import ProtagonistProfile
from novelforge.models.regex import (
    PLACEMENT_AI_OUTPUT,
    PLACEMENT_USER_INPUT,
    PLACEMENT_WORLD_INFO,
    VALID_PLACEMENTS,
    RegexScript,
)
from novelforge.models.volume import (
    DEFAULT_AUDIT_DIMENSIONS,
    VALID_ANALYSIS_DEPTHS,
    VALID_PLOT_ROLES,
    AuditDimension,
    ChapterArtifacts,
    ChapterPlan,
    ChapterStageArtifact,
    DeepAnalysis,
    OutlineAuditReport,
    VolumeArtifacts,
    VolumeOutline,
    VolumeRunConfig,
)
from novelforge.models.worldbook import WorldBook

__all__ = [
    # 章节与续写
    "Chapter",
    "Continuation",
    # 续写流程共享模型（大纲/场景/验证报告）
    "Scene",
    "Outline",
    "CritiqueIssue",
    "CritiqueReport",
    # 卷级多章节续写
    "DeepAnalysis",
    "ChapterPlan",
    "VolumeOutline",
    "AuditDimension",
    "OutlineAuditReport",
    "ChapterArtifacts",
    "ChapterStageArtifact",
    "VolumeArtifacts",
    "VolumeRunConfig",
    "VALID_ANALYSIS_DEPTHS",
    "DEFAULT_AUDIT_DIMENSIONS",
    "VALID_PLOT_ROLES",
    # 上下文条目
    "ContextEntry",
    "VALID_CATEGORIES",
    "VALID_POSITIONS",
    "VALID_ROLES",
    # 流程控制插件
    "FlowPlugin",
    "FlowStage",
    "VALID_AGENT_TYPES",
    "VALID_UI_MODES",
    "VALID_ACCEPT_MODES",
    # 底层世界观与主角形象
    "WorldOntology",
    "ProtagonistProfile",
    # 预设
    "Prompt",
    "PromptOrderEntry",
    "PromptOrderGroup",
    "WritingPreset",
    "GLOBAL_CHARACTER_ID",
    # 项目
    "Project",
    "NovelProfile",
    "ChapterSplitRule",
    "ManualOverride",
    # 正则脚本
    "RegexScript",
    "PLACEMENT_USER_INPUT",
    "PLACEMENT_AI_OUTPUT",
    "PLACEMENT_WORLD_INFO",
    "VALID_PLACEMENTS",
    # 全局世界书
    "WorldBook",
]
