"""卷级多章节续写数据模型测试。

覆盖：
1. VolumeRunConfig chapter_count 校验 [2,20]（边界值 1/21 抛错，2/20 正常）
2. VolumeRunConfig analysis_depth 枚举校验（非法值抛错）
3. VolumeRunConfig 默认 audit_dimensions 含 8 维度
4. VolumeRunConfig 默认 checkpoints 含 5 个 key，before_audit 默认开启，其余关闭
5. DeepAnalysis 默认所有字段为空
6. VolumeOutline 含 chapters: list[ChapterPlan]
7. 旧 Continuation JSON（无 volume_artifacts 字段）能正常反序列化
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from novelforge.models import (
    DEFAULT_AUDIT_DIMENSIONS,
    VALID_ANALYSIS_DEPTHS,
    VALID_PLOT_ROLES,
    ChapterArtifacts,
    ChapterPlan,
    ChapterStageArtifact,
    Continuation,
    CritiqueReport,
    DeepAnalysis,
    Outline,
    VolumeOutline,
    VolumeRunConfig,
)


# ===== 1. VolumeRunConfig chapter_count 校验 =====


def test_volume_run_config_chapter_count_lower_bound_invalid() -> None:
    """chapter_count 小于 2（如 1）应抛 ValueError。"""
    with pytest.raises(ValueError):
        VolumeRunConfig(chapter_count=1)


def test_volume_run_config_chapter_count_upper_bound_invalid() -> None:
    """chapter_count 大于 20（如 21）应抛 ValueError。"""
    with pytest.raises(ValueError):
        VolumeRunConfig(chapter_count=21)


def test_volume_run_config_chapter_count_lower_bound_valid() -> None:
    """chapter_count 等于 2（下界）正常。"""
    config = VolumeRunConfig(chapter_count=2)
    assert config.chapter_count == 2


def test_volume_run_config_chapter_count_upper_bound_valid() -> None:
    """chapter_count 等于 20（上界）正常。"""
    config = VolumeRunConfig(chapter_count=20)
    assert config.chapter_count == 20


def test_volume_run_config_chapter_count_default() -> None:
    """chapter_count 默认值为 5。"""
    config = VolumeRunConfig()
    assert config.chapter_count == 5


# ===== 2. VolumeRunConfig analysis_depth 枚举校验 =====


def test_volume_run_config_analysis_depth_invalid() -> None:
    """analysis_depth 非法值应抛 ValueError。"""
    with pytest.raises(ValueError):
        VolumeRunConfig(analysis_depth="unknown")


def test_volume_run_config_analysis_depth_valid_values() -> None:
    """analysis_depth 合法值均能正常构造。"""
    for depth in VALID_ANALYSIS_DEPTHS:
        config = VolumeRunConfig(analysis_depth=depth)
        assert config.analysis_depth == depth


def test_volume_run_config_analysis_depth_default() -> None:
    """analysis_depth 默认值为 standard。"""
    config = VolumeRunConfig()
    assert config.analysis_depth == "standard"


# ===== 3. VolumeRunConfig 默认 audit_dimensions 含 8 维度 =====


def test_volume_run_config_default_audit_dimensions() -> None:
    """默认 audit_dimensions 含 11 维度且与 DEFAULT_AUDIT_DIMENSIONS 一致。"""
    config = VolumeRunConfig()
    assert len(config.audit_dimensions) == 11
    assert config.audit_dimensions == DEFAULT_AUDIT_DIMENSIONS
    # 确认 11 个维度内容
    assert config.audit_dimensions == [
        "consistency",
        "pacing",
        "engagement",
        "structure",
        "coherence",
        "foreshadowing",
        "characters",
        "style",
        "protagonist_consistency",
        "worldview_consistency",
        "user_directive_compliance",
    ]


def test_volume_run_config_audit_dimensions_independent() -> None:
    """多个实例的 audit_dimensions 互不影响（default_factory 隔离）。"""
    c1 = VolumeRunConfig()
    c2 = VolumeRunConfig()
    c1.audit_dimensions.append("extra")
    # c2 不受影响
    assert len(c2.audit_dimensions) == 11
    assert "extra" not in c2.audit_dimensions


# ===== 4. VolumeRunConfig 默认 checkpoints 含 5 个 key，before_audit 默认开启 =====


def test_volume_run_config_default_checkpoints() -> None:
    """默认 checkpoints 含 5 个 key，before_audit 默认开启，其余关闭。"""
    config = VolumeRunConfig()
    assert len(config.checkpoints) == 5
    assert config.checkpoints == {
        "after_deep_analysis": False,
        "after_volume_outline": False,
        "before_audit": True,
        "after_audit": False,
        "after_chapter": False,
    }
    # before_audit 默认开启，其余关闭
    assert config.checkpoints["before_audit"] is True
    assert not config.checkpoints["after_deep_analysis"]
    assert not config.checkpoints["after_volume_outline"]
    assert not config.checkpoints["after_audit"]


def test_volume_run_config_checkpoints_independent() -> None:
    """多个实例的 checkpoints 互不影响（default_factory 隔离）。"""
    c1 = VolumeRunConfig()
    c2 = VolumeRunConfig()
    c1.checkpoints["after_deep_analysis"] = True
    # c2 不受影响
    assert c2.checkpoints["after_deep_analysis"] is False


def test_volume_run_config_other_defaults() -> None:
    """VolumeRunConfig 其他字段默认值。"""
    config = VolumeRunConfig()
    assert config.target_words_per_chapter == 2000
    assert config.max_analysis_entries == 0
    assert config.enable_outline_audit is True
    assert config.enable_chapter_verify is True
    assert config.enable_chapter_revise is True
    assert config.max_revise_rounds_per_chapter == 1


# ===== 5. DeepAnalysis 默认所有字段为空 =====


def test_deep_analysis_defaults_all_empty() -> None:
    """DeepAnalysis 默认所有字段为空字符串或空列表。"""
    analysis = DeepAnalysis()
    # 故事状态
    assert analysis.structure_position == ""
    assert analysis.tone == ""
    assert analysis.core_conflict_status == ""
    assert analysis.stakes == ""
    assert analysis.active_characters == []
    assert analysis.plot_threads == []
    assert analysis.unresolved_promises == []
    assert analysis.world_state == ""
    # 深度分析
    assert analysis.plot_arrangement_analysis == ""
    assert analysis.chapter_structure_pattern == ""
    assert analysis.tension_curve_pattern == ""
    assert analysis.hook_patterns == ""
    assert analysis.style_analysis == ""
    assert analysis.dialogue_analysis == ""
    assert analysis.pacing_analysis == ""
    assert analysis.character_arc_patterns == []
    # 结构化清单
    assert analysis.foreshadowing_inventory == []
    assert analysis.common_tropes == []
    assert analysis.settings_database == []
    assert analysis.recurring_elements == []
    assert analysis.key_phrases == []


def test_deep_analysis_default_lists_independent() -> None:
    """多个 DeepAnalysis 实例的 list 字段互不影响（default_factory 隔离）。"""
    a1 = DeepAnalysis()
    a2 = DeepAnalysis()
    a1.active_characters.append({"name": "主角"})
    a1.key_phrases.append("关键短语")
    # a2 不受影响
    assert a2.active_characters == []
    assert a2.key_phrases == []


def test_deep_analysis_list_dict_fields() -> None:
    """DeepAnalysis 的 character_arc_patterns 与 key_phrases 接受 dict 列表。"""
    da = DeepAnalysis.model_validate(
        {
            "character_arc_patterns": [
                {"name": "隗辛", "arc_trajectory": "转变", "value_shift": "从善良到冷酷"}
            ],
            "key_phrases": [{"phrase": "我是隗辛", "context": "立人设"}],
        }
    )
    assert isinstance(da.character_arc_patterns, list)
    assert len(da.character_arc_patterns) == 1
    assert isinstance(da.character_arc_patterns[0], dict)
    assert da.character_arc_patterns[0]["name"] == "隗辛"
    assert da.character_arc_patterns[0]["arc_trajectory"] == "转变"
    assert da.character_arc_patterns[0]["value_shift"] == "从善良到冷酷"
    assert isinstance(da.key_phrases, list)
    assert len(da.key_phrases) == 1
    assert isinstance(da.key_phrases[0], dict)
    assert da.key_phrases[0]["phrase"] == "我是隗辛"
    assert da.key_phrases[0]["context"] == "立人设"
    # 默认值
    assert DeepAnalysis().character_arc_patterns == []
    assert DeepAnalysis().key_phrases == []


def test_deep_analysis_user_directive_analysis_defaults() -> None:
    """DeepAnalysis.user_directive_analysis 默认为空 dict。"""
    da = DeepAnalysis()
    assert da.user_directive_analysis == {}
    assert isinstance(da.user_directive_analysis, dict)


def test_deep_analysis_user_directive_analysis_roundtrip() -> None:
    """构造含 user_directive_analysis 的 DeepAnalysis，model_dump → model_validate 往返不丢字段。"""
    uda = {
        "required_elements": ["a"],
        "emphasized_elements": ["b"],
        "interpretation": "c",
        "conflicts": [],
    }
    da = DeepAnalysis(user_directive_analysis=uda)
    assert da.user_directive_analysis == uda
    assert da.user_directive_analysis["required_elements"] == ["a"]
    assert da.user_directive_analysis["emphasized_elements"] == ["b"]
    assert da.user_directive_analysis["interpretation"] == "c"
    assert da.user_directive_analysis["conflicts"] == []

    # model_dump → model_validate 往返
    dumped = da.model_dump()
    assert dumped["user_directive_analysis"] == uda
    restored = DeepAnalysis.model_validate(dumped)
    assert restored.user_directive_analysis == uda
    assert restored.user_directive_analysis["required_elements"] == ["a"]
    assert restored.user_directive_analysis["emphasized_elements"] == ["b"]
    assert restored.user_directive_analysis["interpretation"] == "c"
    assert restored.user_directive_analysis["conflicts"] == []

    # model_dump_json → model_validate_json 往返
    json_str = da.model_dump_json()
    restored_json = DeepAnalysis.model_validate_json(json_str)
    assert restored_json.user_directive_analysis == uda


# ===== 6. VolumeOutline 含 chapters: list[ChapterPlan] =====


def test_volume_outline_default_chapters_empty() -> None:
    """VolumeOutline 默认 chapters 为空列表。"""
    outline = VolumeOutline()
    assert outline.chapters == []
    assert outline.chapter_count == 0
    assert outline.volume_title == ""
    assert outline.volume_goals == ""
    assert outline.plot_arrangement_analysis == ""
    assert outline.pacing_plan == ""
    assert outline.foreshadowing_plan == ""


def test_volume_outline_with_chapter_plans() -> None:
    """VolumeOutline 含 chapters: list[ChapterPlan]。"""
    plan1 = ChapterPlan(
        index=0,
        title="第一章",
        summary="开篇",
        plot_role="起",
        key_events=["主角登场"],
        characters_involved=["主角"],
        foreshadowing="神秘信物",
        chapter_hook="夜半钟声",
        target_words=2500,
    )
    plan2 = ChapterPlan(
        index=1,
        title="第二章",
        summary="发展",
        plot_role="承",
        key_events=["初遇反派"],
    )
    outline = VolumeOutline(
        volume_title="第一卷",
        volume_goals="引入主线",
        plot_arrangement_analysis="起承转合布局",
        pacing_plan="先慢后快",
        foreshadowing_plan="埋下三处伏笔",
        chapter_count=2,
        chapters=[plan1, plan2],
    )
    assert outline.volume_title == "第一卷"
    assert outline.chapter_count == 2
    assert len(outline.chapters) == 2
    assert isinstance(outline.chapters[0], ChapterPlan)
    assert outline.chapters[0].index == 0
    assert outline.chapters[0].title == "第一章"
    assert outline.chapters[0].plot_role == "起"
    assert outline.chapters[0].key_events == ["主角登场"]
    assert outline.chapters[0].target_words == 2500
    assert outline.chapters[1].summary == "发展"
    # 未提供的字段使用默认值
    assert outline.chapters[1].target_words == 2000
    assert outline.chapters[1].chapter_hook == ""


def test_volume_outline_from_dict() -> None:
    """VolumeOutline 能从 dict 构造（含嵌套 ChapterPlan）。"""
    data = {
        "volume_title": "第二卷",
        "volume_goals": "冲突升级",
        "chapter_count": 1,
        "chapters": [
            {
                "index": 0,
                "title": "高潮章",
                "summary": "决战",
                "plot_role": "高潮",
                "key_events": ["最终对决"],
            }
        ],
    }
    outline = VolumeOutline.model_validate(data)
    assert outline.volume_title == "第二卷"
    assert outline.chapter_count == 1
    assert len(outline.chapters) == 1
    assert isinstance(outline.chapters[0], ChapterPlan)
    assert outline.chapters[0].plot_role == "高潮"
    assert outline.chapters[0].key_events == ["最终对决"]


def test_chapter_plan_invalid_plot_role() -> None:
    """ChapterPlan plot_role 非法值应抛 ValueError。"""
    with pytest.raises(ValueError):
        ChapterPlan(plot_role="无效角色")


def test_chapter_plan_plot_role_combo_normalized() -> None:
    """ChapterPlan plot_role 组合值（如"承转"）容错归一化为首个合法值。"""
    # 单字符组合：取首个合法单字符
    assert ChapterPlan(plot_role="承转").plot_role == "承"
    assert ChapterPlan(plot_role="起承").plot_role == "起"
    assert ChapterPlan(plot_role="转合").plot_role == "转"
    # 2 字符合法值优先匹配（避免被单字符拆分误匹配）
    assert ChapterPlan(plot_role="高潮过渡").plot_role == "高潮"
    assert ChapterPlan(plot_role="过渡起").plot_role == "过渡"
    # 精确匹配仍通过
    assert ChapterPlan(plot_role="起").plot_role == "起"
    assert ChapterPlan(plot_role="高潮").plot_role == "高潮"


def test_chapter_plan_plot_role_with_spaces_normalized() -> None:
    """ChapterPlan plot_role 含前后空白的值被 strip 后正确匹配。"""
    # 精确值含前后空白
    assert ChapterPlan(plot_role=" 高潮 ").plot_role == "高潮"
    assert ChapterPlan(plot_role="  承  ").plot_role == "承"
    assert ChapterPlan(plot_role="\t起\n").plot_role == "起"
    # 组合值含空白：strip 后归一化
    assert ChapterPlan(plot_role=" 承转 ").plot_role == "承"
    # 纯空白归一化为空字符串
    assert ChapterPlan(plot_role="   ").plot_role == ""


def test_chapter_plan_empty_plot_role_ok() -> None:
    """ChapterPlan plot_role 默认空字符串合法（向后兼容）。"""
    plan = ChapterPlan()
    assert plan.plot_role == ""


def test_valid_plot_roles_constant() -> None:
    """VALID_PLOT_ROLES 常量含 6 个合法值。"""
    assert VALID_PLOT_ROLES == frozenset({"起", "承", "转", "合", "高潮", "过渡"})
    assert len(VALID_PLOT_ROLES) == 6


# ===== 7. 旧 Continuation JSON（无 volume_artifacts 字段）能正常反序列化 =====


def test_continuation_without_volume_artifacts_field() -> None:
    """旧 Continuation JSON（无 volume_artifacts 字段）能正常反序列化。"""
    data = {
        "id": "cont_legacy",
        "content": "旧版本正文",
        "model": "test-model",
    }
    cont = Continuation.model_validate(data)
    assert cont.id == "cont_legacy"
    assert cont.content == "旧版本正文"
    # volume_artifacts 默认 None（向后兼容）
    assert cont.volume_artifacts is None
    # agent_artifacts 也应默认 None
    assert cont.agent_artifacts is None


def test_continuation_default_volume_artifacts_none() -> None:
    """Continuation 默认 volume_artifacts 为 None。"""
    cont = Continuation(id="cont_1", content="正文", model="m")
    assert cont.volume_artifacts is None


def test_continuation_with_volume_artifacts_roundtrip() -> None:
    """Continuation 含 volume_artifacts 时能正常序列化/反序列化。"""
    from novelforge.models import VolumeArtifacts

    artifacts = VolumeArtifacts(
        deep_analysis=DeepAnalysis(
            structure_position="发展",
            tone="悬疑",
            key_phrases=[{"phrase": "线索", "context": "提示"}, {"phrase": "谜题", "context": "悬念"}],
        ),
        phase_logs=["阶段1完成", "阶段2完成"],
    )
    cont = Continuation(
        id="cont_vol",
        content="卷级正文",
        model="m",
        volume_artifacts=artifacts,
    )

    # model_dump
    dumped = cont.model_dump()
    assert dumped["volume_artifacts"] is not None
    assert dumped["volume_artifacts"]["deep_analysis"]["structure_position"] == "发展"
    assert dumped["volume_artifacts"]["deep_analysis"]["key_phrases"] == [{"phrase": "线索", "context": "提示"}, {"phrase": "谜题", "context": "悬念"}]
    assert dumped["volume_artifacts"]["phase_logs"] == ["阶段1完成", "阶段2完成"]

    # model_validate_json 反序列化
    json_str = cont.model_dump_json()
    restored = Continuation.model_validate_json(json_str)
    assert restored.id == "cont_vol"
    assert restored.volume_artifacts is not None
    assert isinstance(restored.volume_artifacts, VolumeArtifacts)
    assert isinstance(restored.volume_artifacts.deep_analysis, DeepAnalysis)
    assert restored.volume_artifacts.deep_analysis.structure_position == "发展"
    assert restored.volume_artifacts.deep_analysis.key_phrases == [{"phrase": "线索", "context": "提示"}, {"phrase": "谜题", "context": "悬念"}]
    assert restored.volume_artifacts.phase_logs == ["阶段1完成", "阶段2完成"]


def test_continuation_volume_artifacts_none_roundtrip() -> None:
    """Continuation volume_artifacts 为 None 时序列化/反序列化保持 None。"""
    cont = Continuation(id="cont_none", content="x", model="m")
    json_str = cont.model_dump_json()
    restored = Continuation.model_validate_json(json_str)
    assert restored.volume_artifacts is None


# ===== 8. ChapterStageArtifact 模型测试 =====


def test_chapter_stage_artifact_defaults() -> None:
    """ChapterStageArtifact 默认值：所有字段为空/None。"""
    stage = ChapterStageArtifact()
    assert stage.stage_type == ""
    assert stage.round_index == 0
    assert stage.content == ""
    assert stage.critique is None
    assert stage.guidance is None
    assert stage.outline is None


def test_chapter_stage_artifact_construction() -> None:
    """构造完整 ChapterStageArtifact 对象，验证字段赋值。"""
    outline = Outline(continuation_goals="测试目标", foreshadowing_plan="无", scenes=[])
    critique = CritiqueReport(summary="通过", issues=[], passed=True)
    guidance = {"revision_strategy": "润色", "key_changes": [], "preserve_elements": ""}

    stage = ChapterStageArtifact(
        stage_type="revise",
        round_index=1,
        content="修订后正文",
        critique=critique,
        guidance=guidance,
        outline=outline,
    )
    assert stage.stage_type == "revise"
    assert stage.round_index == 1
    assert stage.content == "修订后正文"
    assert stage.critique is not None
    assert stage.critique.passed is True
    assert stage.guidance is not None
    assert stage.guidance["revision_strategy"] == "润色"
    assert stage.outline is not None
    assert stage.outline.continuation_goals == "测试目标"


def test_chapter_artifacts_stages_default_empty() -> None:
    """ChapterArtifacts 默认 stages 为空列表（向后兼容）。"""
    artifacts = ChapterArtifacts(chapter_index=0)
    assert artifacts.stages == []
    assert isinstance(artifacts.stages, list)


def test_chapter_artifacts_stages_roundtrip() -> None:
    """构造含 stages 的 ChapterArtifacts，model_dump → model_validate 往返一致。"""
    stages = [
        ChapterStageArtifact(stage_type="outline", round_index=0),
        ChapterStageArtifact(stage_type="draft", round_index=0, content="初稿"),
        ChapterStageArtifact(
            stage_type="audit",
            round_index=1,
            critique=CritiqueReport(summary="通过", issues=[], passed=True),
        ),
        ChapterStageArtifact(
            stage_type="revise",
            round_index=1,
            guidance={"revision_strategy": "润色"},
            content="修订稿",
        ),
    ]
    artifacts = ChapterArtifacts(
        chapter_index=0,
        content="修订稿",
        revision_rounds=1,
        stages=stages,
    )

    json_str = artifacts.model_dump_json()
    restored = ChapterArtifacts.model_validate_json(json_str)

    assert len(restored.stages) == 4
    assert restored.stages[0].stage_type == "outline"
    assert restored.stages[1].stage_type == "draft"
    assert restored.stages[1].content == "初稿"
    assert restored.stages[2].stage_type == "audit"
    assert restored.stages[2].critique is not None
    assert restored.stages[2].critique.passed is True
    assert restored.stages[3].stage_type == "revise"
    assert restored.stages[3].content == "修订稿"
    assert restored.stages[3].guidance is not None
    assert restored.stages[3].guidance["revision_strategy"] == "润色"
    assert restored.content == "修订稿"
    assert restored.revision_rounds == 1
