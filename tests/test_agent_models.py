"""Agent 多阶段续写数据模型测试。

覆盖：
1. 各模型能正常实例化（含默认值）
2. StorySnapshot/Outline/CritiqueReport 能从 dict 构造（pydantic 校验）
3. Continuation 无 agent_artifacts 字段时能正常构造（向后兼容）
4. Continuation 含 agent_artifacts 时能正常序列化/反序列化
5. AgentRunConfig 默认 phases 含 5 个阶段，checkpoints 含 2 个暂停点
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
    AgentArtifacts,
    AgentRunConfig,
    Chapter,
    Continuation,
    CritiqueIssue,
    CritiqueReport,
    Outline,
    Scene,
    StorySnapshot,
)


# ===== 1. 各模型默认值实例化 =====


def test_story_snapshot_defaults() -> None:
    """StorySnapshot 默认值实例化。"""
    snap = StorySnapshot()
    assert snap.structure_position == ""
    assert snap.tone == ""
    assert snap.core_conflict_status == ""
    assert snap.stakes == ""
    assert snap.active_characters == []
    assert snap.plot_threads == []
    assert snap.unresolved_promises == []
    assert snap.foreshadowing_tracker == []
    assert snap.world_state == ""
    assert snap.style_profile == ""


def test_scene_defaults() -> None:
    """Scene 默认值实例化。"""
    scene = Scene()
    assert scene.purpose == ""
    assert scene.pov == ""
    assert scene.scene_type == ""
    assert scene.goal == ""
    assert scene.conflict == ""
    assert scene.outcome == ""
    assert scene.value_shift == ""
    assert scene.foreshadowing == ""
    assert scene.exit_hook == ""


def test_outline_defaults() -> None:
    """Outline 默认值实例化。"""
    outline = Outline()
    assert outline.continuation_goals == ""
    assert outline.foreshadowing_plan == ""
    assert outline.scenes == []


def test_critique_issue_defaults() -> None:
    """CritiqueIssue 默认值实例化。"""
    issue = CritiqueIssue()
    assert issue.category == ""
    assert issue.severity == ""
    assert issue.location == ""
    assert issue.description == ""
    assert issue.suggestion == ""


def test_critique_report_defaults() -> None:
    """CritiqueReport 默认值实例化。"""
    report = CritiqueReport()
    assert report.summary == ""
    assert report.issues == []
    assert report.passed is False


def test_agent_artifacts_defaults() -> None:
    """AgentArtifacts 默认值实例化。"""
    artifacts = AgentArtifacts()
    assert artifacts.snapshot is None
    assert artifacts.outline is None
    assert artifacts.critique is None
    assert artifacts.final_critique is None
    assert artifacts.revision_rounds == 0
    assert artifacts.phase_logs == []


def test_agent_run_config_defaults() -> None:
    """AgentRunConfig 默认值实例化。"""
    config = AgentRunConfig()
    assert config.max_revise_rounds == 1
    assert config.per_phase_overrides == {}


# ===== 2. 从 dict 构造（pydantic 校验） =====


def test_story_snapshot_from_dict() -> None:
    """StorySnapshot 能从 dict 构造。"""
    data = {
        "structure_position": "高潮",
        "tone": "紧张",
        "core_conflict_status": "白热化",
        "stakes": "生死",
        "active_characters": [
            {"name": "主角", "status": "受伤", "motivation": "复仇"},
            {"name": "反派", "status": "得意", "motivation": "统治"},
        ],
        "plot_threads": [{"id": "t1", "desc": "主线"}],
        "unresolved_promises": [{"id": "p1", "desc": "承诺归还宝物"}],
        "foreshadowing_tracker": [{"id": "f1", "desc": "墙上的剑"}],
        "world_state": "战乱",
        "style_profile": "冷峻、短句",
    }
    snap = StorySnapshot.model_validate(data)
    assert snap.structure_position == "高潮"
    assert snap.tone == "紧张"
    assert snap.core_conflict_status == "白热化"
    assert snap.stakes == "生死"
    assert len(snap.active_characters) == 2
    assert snap.active_characters[0]["name"] == "主角"
    assert snap.active_characters[1]["motivation"] == "统治"
    assert snap.plot_threads[0]["id"] == "t1"
    assert snap.unresolved_promises[0]["desc"] == "承诺归还宝物"
    assert snap.foreshadowing_tracker[0]["id"] == "f1"
    assert snap.world_state == "战乱"
    assert snap.style_profile == "冷峻、短句"


def test_outline_from_dict() -> None:
    """Outline 能从 dict 构造（含嵌套 Scene）。"""
    data = {
        "continuation_goals": "推动主角与反派正面冲突",
        "foreshadowing_plan": "埋下宝物失效的伏笔",
        "scenes": [
            {
                "purpose": "对峙",
                "pov": "主角",
                "scene_type": "对话",
                "goal": "逼反派露出破绽",
                "conflict": "价值观冲突",
                "outcome": "主角负伤",
                "value_shift": "信念动摇",
                "foreshadowing": "宝物裂纹",
                "exit_hook": "援军到达",
            },
            {
                "purpose": "撤退",
                "pov": "主角",
                "scene_type": "动作",
            },
        ],
    }
    outline = Outline.model_validate(data)
    assert outline.continuation_goals == "推动主角与反派正面冲突"
    assert outline.foreshadowing_plan == "埋下宝物失效的伏笔"
    assert len(outline.scenes) == 2
    assert isinstance(outline.scenes[0], Scene)
    assert outline.scenes[0].purpose == "对峙"
    assert outline.scenes[0].scene_type == "对话"
    assert outline.scenes[0].exit_hook == "援军到达"
    assert outline.scenes[1].purpose == "撤退"
    # 未提供的字段使用默认值
    assert outline.scenes[1].goal == ""
    assert outline.scenes[1].outcome == ""


def test_critique_report_from_dict() -> None:
    """CritiqueReport 能从 dict 构造（含嵌套 CritiqueIssue）。"""
    data = {
        "summary": "整体通过，存在 2 个 minor 问题",
        "issues": [
            {
                "category": "consistency",
                "severity": "minor",
                "location": "第 3 段",
                "description": "时间线前后不一致",
                "suggestion": "修正时间描述",
            },
            {
                "category": "style",
                "severity": "minor",
                "location": "第 5 段",
                "description": "用词重复",
                "suggestion": "替换同义词",
            },
        ],
        "passed": True,
    }
    report = CritiqueReport.model_validate(data)
    assert report.summary == "整体通过，存在 2 个 minor 问题"
    assert len(report.issues) == 2
    assert isinstance(report.issues[0], CritiqueIssue)
    assert report.issues[0].category == "consistency"
    assert report.issues[0].severity == "minor"
    assert report.issues[1].suggestion == "替换同义词"
    assert report.passed is True


# ===== 3. Continuation 向后兼容 =====


def test_continuation_without_agent_artifacts() -> None:
    """Continuation 无 agent_artifacts 字段时能正常构造（向后兼容）。"""
    cont = Continuation(id="cont_1", content="正文内容", model="test-model")
    assert cont.id == "cont_1"
    assert cont.content == "正文内容"
    assert cont.model == "test-model"
    # agent_artifacts 默认 None
    assert cont.agent_artifacts is None


def test_continuation_from_dict_without_agent_artifacts() -> None:
    """Continuation 从不含 agent_artifacts 的 dict 构造（向后兼容）。"""
    data = {
        "id": "cont_2",
        "content": "内容",
        "model": "m",
    }
    cont = Continuation.model_validate(data)
    assert cont.id == "cont_2"
    assert cont.agent_artifacts is None


# ===== 4. Continuation 含 agent_artifacts 序列化/反序列化 =====


def test_continuation_with_agent_artifacts_roundtrip() -> None:
    """Continuation 含 agent_artifacts 时能正常序列化/反序列化。"""
    artifacts = AgentArtifacts(
        snapshot=StorySnapshot(
            structure_position="发展",
            tone="悬疑",
            active_characters=[{"name": "侦探", "status": "调查中"}],
        ),
        outline=Outline(
            continuation_goals="揭示线索",
            scenes=[Scene(purpose="调查现场", pov="侦探", scene_type="描写")],
        ),
        critique=CritiqueReport(
            summary="首次验证",
            issues=[
                CritiqueIssue(
                    category="consistency",
                    severity="major",
                    location="第 2 段",
                    description="人物动机不明",
                    suggestion="补充心理描写",
                )
            ],
            passed=False,
        ),
        final_critique=CritiqueReport(summary="最终验证", passed=True),
        revision_rounds=1,
        phase_logs=[{"phase": "analysis", "ok": True}],
    )
    cont = Continuation(
        id="cont_3",
        content="正文",
        model="m",
        agent_artifacts=artifacts,
    )

    # model_dump
    dumped = cont.model_dump()
    assert dumped["agent_artifacts"] is not None
    assert dumped["agent_artifacts"]["snapshot"]["structure_position"] == "发展"
    assert (
        dumped["agent_artifacts"]["snapshot"]["active_characters"][0]["name"]
        == "侦探"
    )
    assert dumped["agent_artifacts"]["outline"]["scenes"][0]["purpose"] == "调查现场"
    assert dumped["agent_artifacts"]["critique"]["issues"][0]["severity"] == "major"
    assert dumped["agent_artifacts"]["final_critique"]["passed"] is True
    assert dumped["agent_artifacts"]["revision_rounds"] == 1
    assert dumped["agent_artifacts"]["phase_logs"][0]["phase"] == "analysis"

    # model_validate_json 反序列化
    json_str = cont.model_dump_json()
    restored = Continuation.model_validate_json(json_str)
    assert restored.id == "cont_3"
    assert restored.agent_artifacts is not None
    assert isinstance(restored.agent_artifacts, AgentArtifacts)
    assert isinstance(restored.agent_artifacts.snapshot, StorySnapshot)
    assert restored.agent_artifacts.snapshot.structure_position == "发展"
    assert (
        restored.agent_artifacts.snapshot.active_characters[0]["name"] == "侦探"
    )
    assert isinstance(restored.agent_artifacts.outline, Outline)
    assert len(restored.agent_artifacts.outline.scenes) == 1
    assert restored.agent_artifacts.outline.scenes[0].purpose == "调查现场"
    assert isinstance(restored.agent_artifacts.critique, CritiqueReport)
    assert len(restored.agent_artifacts.critique.issues) == 1
    assert restored.agent_artifacts.critique.issues[0].severity == "major"
    assert isinstance(restored.agent_artifacts.final_critique, CritiqueReport)
    assert restored.agent_artifacts.final_critique.passed is True
    assert restored.agent_artifacts.revision_rounds == 1
    assert restored.agent_artifacts.phase_logs[0]["phase"] == "analysis"


def test_continuation_agent_artifacts_none_roundtrip() -> None:
    """Continuation agent_artifacts 为 None 时序列化/反序列化保持 None。"""
    cont = Continuation(id="cont_4", content="x", model="m")
    json_str = cont.model_dump_json()
    restored = Continuation.model_validate_json(json_str)
    assert restored.agent_artifacts is None


# ===== 5. AgentRunConfig 默认阶段与暂停点 =====


def test_agent_run_config_default_phases() -> None:
    """AgentRunConfig 默认 phases 含 5 个阶段。"""
    config = AgentRunConfig()
    assert config.phases == {
        "analysis": True,
        "outline": True,
        "writing": True,
        "verify": True,
        "revise": True,
    }
    assert len(config.phases) == 5
    # 所有阶段默认开启
    assert all(config.phases.values())


def test_agent_run_config_default_checkpoints() -> None:
    """AgentRunConfig 默认 checkpoints 含 2 个暂停点。"""
    config = AgentRunConfig()
    assert config.checkpoints == {
        "after_outline": False,
        "after_verify": False,
    }
    assert len(config.checkpoints) == 2
    # 暂停点默认关闭
    assert not any(config.checkpoints.values())


def test_agent_run_config_independent_defaults() -> None:
    """多个 AgentRunConfig 实例的默认 dict 互不影响（default_factory 隔离）。"""
    c1 = AgentRunConfig()
    c2 = AgentRunConfig()
    c1.phases["analysis"] = False
    c1.checkpoints["after_outline"] = True
    c1.per_phase_overrides["analysis"] = {"temperature": 0.5}
    # c2 不受影响
    assert c2.phases["analysis"] is True
    assert c2.checkpoints["after_outline"] is False
    assert c2.per_phase_overrides == {}


def test_agent_run_config_custom_values() -> None:
    """AgentRunConfig 支持自定义阶段与覆盖参数。"""
    config = AgentRunConfig(
        phases={"analysis": True, "outline": False, "writing": True,
                "verify": True, "revise": False},
        checkpoints={"after_outline": True, "after_verify": False},
        max_revise_rounds=3,
        per_phase_overrides={
            "writing": {"model": "gpt-4", "temperature": 0.8},
            "verify": {"model": "claude-3"},
        },
    )
    assert config.phases["outline"] is False
    assert config.phases["revise"] is False
    assert config.checkpoints["after_outline"] is True
    assert config.max_revise_rounds == 3
    assert config.per_phase_overrides["writing"]["model"] == "gpt-4"
    assert config.per_phase_overrides["writing"]["temperature"] == 0.8
    assert config.per_phase_overrides["verify"]["model"] == "claude-3"
    assert "temperature" not in config.per_phase_overrides["verify"]


# ===== 6. Chapter 集成（确认无循环导入） =====


def test_chapter_with_continuation_agent_artifacts() -> None:
    """Chapter 含带 agent_artifacts 的 Continuation 能正常构造（无循环导入）。"""
    cont = Continuation(
        id="cont_5",
        content="正文",
        model="m",
        agent_artifacts=AgentArtifacts(
            revision_rounds=2,
            phase_logs=[{"phase": "writing", "tokens": 1024}],
        ),
    )
    chapter = Chapter(
        id="ch_1",
        project_id="proj_1",
        index=0,
        continuations=[cont],
    )
    assert len(chapter.continuations) == 1
    assert chapter.continuations[0].agent_artifacts is not None
    assert chapter.continuations[0].agent_artifacts.revision_rounds == 2
    assert chapter.continuations[0].agent_artifacts.phase_logs[0]["tokens"] == 1024
