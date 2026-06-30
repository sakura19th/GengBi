"""卷续写与相关面板 UI 测试。

覆盖以下检查点：
1. VolumePanel.set_presets/get_selected_preset_id 预设选择
2. parse_token_limit 与 VolumePanel token 切分下拉框联动 get_config
3. ContinuationPanel 用户输入框高度约束（maxHeight 80 / minHeight 60）
4. VolumePanel.show_continue_button/hide_continue_button 显隐与产物 tab 切换
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
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QPushButton

from novelforge.ui.continuation_panel import ContinuationPanel
from novelforge.ui.helpers import select_combo_by_id
from novelforge.ui.volume_panel import VolumePanel
from novelforge.models import (
    ChapterArtifacts,
    ChapterStageArtifact,
    CritiqueReport,
    Outline,
    VolumeRunConfig,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """提供全局 QApplication 单例（离屏平台）。"""
    app = QApplication.instance() or QApplication(sys.argv)
    return app


@pytest.fixture
def volume_panel(qapp) -> VolumePanel:
    """创建 VolumePanel 实例。"""
    return VolumePanel()


@pytest.fixture
def continuation_panel(qapp) -> ContinuationPanel:
    """创建 ContinuationPanel 实例。"""
    return ContinuationPanel()


# ===== 1. VolumePanel 预设管理 =====


class TestVolumePanelPresets:
    """VolumePanel.set_presets/get_selected_preset_id 测试。"""

    def test_set_presets_selects_default(self, volume_panel) -> None:
        """set_presets 后默认预设被选中。"""
        volume_panel.set_presets(
            [{"id": "default", "name": "默认预设"}, {"id": "custom", "name": "自定义"}],
            "default",
        )
        assert volume_panel.get_selected_preset_id() == "default"

    def test_set_presets_then_change_selection(self, volume_panel) -> None:
        """切换预设后 get_selected_preset_id 返回新值。"""
        volume_panel.set_presets(
            [{"id": "default", "name": "默认预设"}, {"id": "custom", "name": "自定义"}],
            "default",
        )
        assert volume_panel.get_selected_preset_id() == "default"

        # 切换选中到 custom
        select_combo_by_id(volume_panel._preset_combo, "custom")
        assert volume_panel.get_selected_preset_id() == "custom"

    def test_get_selected_preset_id_empty_returns_default(self, volume_panel) -> None:
        """无预设时 get_selected_preset_id 返回 "default"。"""
        volume_panel.set_presets([], "default")
        assert volume_panel.get_selected_preset_id() == "default"


# ===== 2. parse_token_limit 与 token 切分下拉框联动 =====


class TestVolumePanelTokenLimit:
    """VolumePanel token 切分下拉框与 get_config().analysis_chunk_tokens 联动测试。"""

    def test_no_limit_token_is_zero(self, volume_panel) -> None:
        """选择"不限制"时 analysis_chunk_tokens 为 0。"""
        volume_panel._analysis_chunk_tokens_combo.setCurrentText("不限制")
        config = volume_panel.get_config()
        assert config.analysis_chunk_tokens == 0

    def test_50k_token_is_50000(self, volume_panel) -> None:
        """选择"50k"时 analysis_chunk_tokens 为 50000。"""
        volume_panel._analysis_chunk_tokens_combo.setCurrentText("50k")
        config = volume_panel.get_config()
        assert config.analysis_chunk_tokens == 50000

    def test_100k_token_is_100000(self, volume_panel) -> None:
        """选择"100k"时 analysis_chunk_tokens 为 100000。"""
        volume_panel._analysis_chunk_tokens_combo.setCurrentText("100k")
        config = volume_panel.get_config()
        assert config.analysis_chunk_tokens == 100000


# ===== 2b. VolumePanel.set_config 回填 =====


class TestVolumePanelSetConfig:
    """VolumePanel.set_config 从 VolumeRunConfig 回填到 UI 测试。"""

    def test_set_config_restores_ui(self, volume_panel) -> None:
        """set_config 后 UI 控件反映传入的 VolumeRunConfig 值。"""
        config = VolumeRunConfig(
            chapter_count=7,
            target_words_per_chapter=3000,
            analysis_depth="thorough",
            max_analysis_entries=20,
            analysis_chunk_tokens=250000,
            pacing_speed="fast",
            audit_rounds=2,
            enable_outline_audit=False,
            audit_dimensions=["consistency", "pacing"],
            enable_chapter_verify=False,
            enable_chapter_revise=False,
            max_revise_rounds_per_chapter=2,
            checkpoints={
                "after_deep_analysis": True,
                "after_volume_outline": True,
                "before_audit": False,
                "after_audit": True,
            },
        )
        volume_panel.set_config(config)

        assert volume_panel._chapter_count_spin.value() == 7
        assert volume_panel._target_words_spin.value() == 3000
        assert volume_panel._analysis_depth_combo.currentData() == "thorough"
        assert volume_panel._max_entries_spin.value() == 20
        assert volume_panel._analysis_chunk_tokens_combo.currentText() == "250k"
        assert volume_panel._pacing_speed_combo.currentData() == "fast"
        assert volume_panel._audit_rounds_spin.value() == 2
        assert volume_panel._audit_check.isChecked() is False
        # 审计维度
        assert volume_panel._audit_dim_checks["consistency"].isChecked()
        assert volume_panel._audit_dim_checks["pacing"].isChecked()
        assert not volume_panel._audit_dim_checks["engagement"].isChecked()
        assert volume_panel._chapter_verify_check.isChecked() is False
        assert volume_panel._chapter_revise_check.isChecked() is False
        assert volume_panel._max_revise_spin.value() == 2
        # 检查点
        assert volume_panel._after_deep_analysis_check.isChecked() is True
        assert volume_panel._after_volume_outline_check.isChecked() is True
        assert volume_panel._before_audit_check.isChecked() is False
        assert volume_panel._after_audit_check.isChecked() is True

    def test_set_config_roundtrip_via_get_config(self, volume_panel) -> None:
        """set_config 后 get_config 返回等价配置（roundtrip）。"""
        config = VolumeRunConfig(
            chapter_count=10,
            target_words_per_chapter=5000,
            analysis_depth="exhaustive",
            max_analysis_entries=50,
            analysis_chunk_tokens=500000,
            pacing_speed="slow",
            audit_rounds=3,
            enable_outline_audit=True,
            audit_dimensions=["structure", "coherence", "characters"],
            enable_chapter_verify=True,
            enable_chapter_revise=True,
            max_revise_rounds_per_chapter=3,
            checkpoints={
                "after_deep_analysis": False,
                "after_volume_outline": False,
                "before_audit": True,
                "after_audit": False,
                "after_chapter": False,
            },
        )
        volume_panel.set_config(config)
        restored = volume_panel.get_config()
        assert restored.chapter_count == 10
        assert restored.target_words_per_chapter == 5000
        assert restored.analysis_depth == "exhaustive"
        assert restored.max_analysis_entries == 50
        assert restored.analysis_chunk_tokens == 500000
        assert restored.pacing_speed == "slow"
        assert restored.audit_rounds == 3
        assert restored.enable_outline_audit is True
        assert restored.audit_dimensions == ["structure", "coherence", "characters"]
        assert restored.enable_chapter_verify is True
        assert restored.enable_chapter_revise is True
        assert restored.max_revise_rounds_per_chapter == 3
        assert restored.checkpoints == {
            "after_deep_analysis": False,
            "after_volume_outline": False,
            "before_audit": True,
            "after_audit": False,
            "after_chapter": False,
        }

    def test_set_config_does_not_emit_config_changed(self, volume_panel) -> None:
        """set_config 过程中不发射 config_changed 信号（避免保存循环）。"""
        emitted: list = []
        volume_panel.config_changed.connect(lambda cfg: emitted.append(cfg))

        config = VolumeRunConfig(chapter_count=8, pacing_speed="fast")
        volume_panel.set_config(config)

        assert len(emitted) == 0


# ===== 2c. VolumePanel before_audit 审计重点内嵌输入 =====


class TestVolumePanelAuditFocus:
    """VolumePanel before_audit 审计重点内嵌输入区测试。"""

    def test_show_audit_focus_input_visible_and_switch_tab(self, volume_panel) -> None:
        """show_audit_focus_input 后输入区可见且切到卷大纲 Tab。"""
        # offscreen 平台下 isVisible() 要求顶层窗口已 show，
        # 改用 isVisibleTo(parent) 检查对父级的可见性
        parent = volume_panel._audit_focus_group.parentWidget()
        # 初始隐藏
        assert not volume_panel._audit_focus_group.isVisibleTo(parent)
        # 切到其他 Tab
        volume_panel._artifacts_tabs.setCurrentIndex(0)

        volume_panel.show_audit_focus_input()

        assert volume_panel._audit_focus_group.isVisibleTo(parent)
        assert volume_panel._artifacts_tabs.currentIndex() == 1  # 卷大纲 tab

    def test_audit_focus_start_emits_signal_with_text(self, volume_panel) -> None:
        """点击"开始审计"发射 continue_requested 信号携带用户输入文本。"""
        emitted: list = []
        volume_panel.continue_requested.connect(
            lambda name, payload: emitted.append((name, payload))
        )

        volume_panel.show_audit_focus_input()
        volume_panel._audit_focus_edit.setPlainText("第3章人物动机不一致")

        # 模拟点击"开始审计"按钮
        volume_panel._audit_focus_start_btn.click()

        assert len(emitted) == 1
        name, payload = emitted[0]
        assert name == "before_audit"
        assert payload == "第3章人物动机不一致"
        # 输入区已隐藏
        parent = volume_panel._audit_focus_group.parentWidget()
        assert not volume_panel._audit_focus_group.isVisibleTo(parent)

    def test_audit_focus_cancel_emits_signal(self, volume_panel) -> None:
        """点击"取消续写"发射 cancel_checkpoint 信号。"""
        cancel_emitted: list = []
        volume_panel.cancel_checkpoint.connect(lambda: cancel_emitted.append(True))

        volume_panel.show_audit_focus_input()
        # 模拟点击"取消续写"按钮
        volume_panel._audit_focus_cancel_btn.click()

        assert len(cancel_emitted) == 1
        # 输入区已隐藏
        parent = volume_panel._audit_focus_group.parentWidget()
        assert not volume_panel._audit_focus_group.isVisibleTo(parent)

    def test_audit_focus_start_strips_whitespace(self, volume_panel) -> None:
        """开始审计时对用户输入做 strip。"""
        emitted: list = []
        volume_panel.continue_requested.connect(
            lambda name, payload: emitted.append((name, payload))
        )

        volume_panel.show_audit_focus_input()
        volume_panel._audit_focus_edit.setPlainText("  带空格的输入  \n")

        volume_panel._audit_focus_start_btn.click()

        assert emitted[0][1] == "带空格的输入"


# ===== 3. 用户输入框高度约束 =====


class TestContinuationPanelInputHeight:
    """ContinuationPanel 用户输入框高度约束测试。"""

    def test_user_input_min_height_is_36(self, continuation_panel) -> None:
        """用户输入框 minHeight 为 36（拖动底线）。"""
        assert continuation_panel._user_input_edit.minimumHeight() == 36

    def test_user_input_no_max_height_constraint(self, continuation_panel) -> None:
        """用户输入框无 maxHeight 约束（由 QSplitter 控制高度，可拖动调整）。"""
        # QWIDGETSIZE_MAX 是 Qt 内部宏常量，值为 16777215
        assert continuation_panel._user_input_edit.maximumHeight() == 16777215


# ===== 4. VolumePanel 继续/隐藏继续按钮 =====


class TestVolumePanelContinueButton:
    """VolumePanel.show_continue_button/hide_continue_button 测试。"""

    def test_show_continue_button_after_deep_analysis(
        self, volume_panel
    ) -> None:
        """after_deep_analysis 时显示继续按钮并切到深度分析 tab(index 0)。"""
        volume_panel.show_continue_button("after_deep_analysis")
        # 面板未作为顶层窗口显示，isVisible 受祖先链影响，用 isHidden 反映显式 show/hide
        assert not volume_panel._continue_btn.isHidden()
        assert volume_panel._artifacts_tabs.currentIndex() == 0

    def test_show_continue_button_after_volume_outline(
        self, volume_panel
    ) -> None:
        """after_volume_outline 时显示继续按钮并切到卷大纲 tab(index 1)。"""
        volume_panel.show_continue_button("after_volume_outline")
        assert not volume_panel._continue_btn.isHidden()
        assert volume_panel._artifacts_tabs.currentIndex() == 1

    def test_show_continue_button_after_audit(self, volume_panel) -> None:
        """after_audit 时显示继续按钮并切到审计报告 tab(index 2)。"""
        volume_panel.show_continue_button("after_audit")
        assert not volume_panel._continue_btn.isHidden()
        assert volume_panel._artifacts_tabs.currentIndex() == 2

    def test_switch_to_tab_outline_final(self, volume_panel) -> None:
        """switch_to_tab('outline_final') 切到卷大纲 tab(index 1)。"""
        volume_panel.switch_to_tab("outline_final")
        assert volume_panel._artifacts_tabs.currentIndex() == 1

    def test_switch_to_tab_outline_audit(self, volume_panel) -> None:
        """switch_to_tab('outline_audit') 切到审计报告 tab(index 2)。"""
        volume_panel.switch_to_tab("outline_audit")
        assert volume_panel._artifacts_tabs.currentIndex() == 2

    def test_hide_continue_button(self, volume_panel) -> None:
        """hide_continue_button 隐藏继续按钮。"""
        volume_panel.show_continue_button("after_deep_analysis")
        assert not volume_panel._continue_btn.isHidden()

        volume_panel.hide_continue_button()
        assert volume_panel._continue_btn.isHidden()


# ===== 5. add_chapter_artifacts 阶段产物展示 =====


class TestAddChapterArtifactsStages:
    """add_chapter_artifacts 阶段次序展示测试。"""

    def test_add_chapter_artifacts_with_stages(self, volume_panel) -> None:
        """stages 非空时按阶段次序生成"查看完整内容"按钮，标签依次为细纲/初稿/审计①/修改正文①/审计②。"""
        outline = Outline(continuation_goals="目标", foreshadowing_plan="无", scenes=[])
        critique1 = CritiqueReport(summary="通过", issues=[], passed=True)
        critique2 = CritiqueReport(summary="通过2", issues=[], passed=True)
        stages = [
            ChapterStageArtifact(stage_type="outline", round_index=0, outline=outline),
            ChapterStageArtifact(stage_type="draft", round_index=0, content="初稿"),
            ChapterStageArtifact(stage_type="audit", round_index=1, critique=critique1),
            ChapterStageArtifact(
                stage_type="revise", round_index=1,
                guidance={"revision_strategy": "润色"}, content="修订稿",
            ),
            ChapterStageArtifact(stage_type="audit", round_index=2, critique=critique2),
        ]
        artifacts = ChapterArtifacts(
            chapter_index=0, content="修订稿", revision_rounds=1, stages=stages,
        )

        volume_panel.add_chapter_artifacts(1, artifacts, "测试章")

        # 找到刚添加的 QGroupBox
        groups = volume_panel.findChildren(QGroupBox)
        target_group = None
        for g in groups:
            if "第 1 章" in g.title():
                target_group = g
                break
        assert target_group is not None, "未找到第 1 章 QGroupBox"

        # 验证有 5 个"查看完整内容"按钮
        buttons = target_group.findChildren(QPushButton)
        view_buttons = [b for b in buttons if b.text() == "查看完整内容"]
        assert len(view_buttons) == 5

        # 验证阶段标签依次为：细纲/初稿/审计①/修改正文①/审计②
        labels = target_group.findChildren(QLabel)
        stage_labels = [l.text() for l in labels if l.text().startswith("阶段 ")]
        assert len(stage_labels) == 5
        assert "细纲" in stage_labels[0]
        assert "初稿" in stage_labels[1]
        assert "审计①" in stage_labels[2]
        assert "修改正文①" in stage_labels[3]
        assert "审计②" in stage_labels[4]

    def test_add_chapter_artifacts_legacy_fallback(self, volume_panel) -> None:
        """stages 为空时回退到三块摘要布局（细纲摘要/评审摘要/正文摘要）。"""
        outline = Outline(continuation_goals="目标", foreshadowing_plan="无", scenes=[])
        critique = CritiqueReport(summary="通过", issues=[], passed=True)
        artifacts = ChapterArtifacts(
            chapter_index=0,
            outline=outline,
            critique=critique,
            final_critique=critique,
            content="正文内容",
            revision_rounds=0,
            stages=[],  # 空 stages 触发 legacy 回退
        )

        volume_panel.add_chapter_artifacts(1, artifacts, "旧数据章")

        # 找到 QGroupBox
        groups = volume_panel.findChildren(QGroupBox)
        target_group = None
        for g in groups:
            if "第 1 章" in g.title():
                target_group = g
                break
        assert target_group is not None

        # 验证三块摘要 QLabel 存在
        labels = target_group.findChildren(QLabel)
        label_texts = [l.text() for l in labels]
        assert any("细纲摘要" in t for t in label_texts)
        assert any("评审摘要" in t for t in label_texts)
        assert any("正文摘要" in t for t in label_texts)
        # 不应有"查看完整内容"按钮
        buttons = target_group.findChildren(QPushButton)
        view_buttons = [b for b in buttons if b.text() == "查看完整内容"]
        assert len(view_buttons) == 0
