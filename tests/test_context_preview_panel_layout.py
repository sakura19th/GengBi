"""上下文预览面板布局重构测试。

覆盖：
1. 6 个功能按钮存在、文本/objectName 正确
2. _FeatureActionDialog 返回动作键
3. 各功能按钮点击后的动作分发（mock _show_feature_dialog）
4. 3 个增量信号在对应动作下被发射
5. _set_feature_buttons_enabled 统一禁用/恢复
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from novelforge.ui.context_preview_panel import _FeatureActionDialog


@pytest.fixture(scope="module")
def qapp() -> Any:
    """模块级 QApplication 单例。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _make_panel(qapp: Any) -> Any:
    """构建 ContextPreviewPanel。"""
    from novelforge.ui.context_preview_panel import ContextPreviewPanel

    return ContextPreviewPanel()


# ===== 1. 6 个功能按钮存在 =====


class TestFeatureButtonsExist:
    """功能按钮存在性测试。"""

    def test_six_feature_buttons(self, qapp: Any) -> None:
        """_feature_buttons 含 6 个功能按钮。"""
        panel = _make_panel(qapp)

        assert set(panel._feature_buttons.keys()) == {
            "context",
            "ontology",
            "protagonist",
            "custom_character",
            "style",
            "custom_rule",
        }

    def test_feature_button_texts(self, qapp: Any) -> None:
        """各功能按钮文本正确。"""
        panel = _make_panel(qapp)

        assert panel._context_btn.text() == "上下文"
        assert panel._ontology_btn.text() == "世界观底层"
        assert panel._protagonist_btn.text() == "主角形象"
        assert panel._custom_character_btn.text() == "自定义角色"
        assert panel._style_btn.text() == "文风档案"
        assert panel._custom_rule_btn.text() == "自定义设定"

    def test_feature_buttons_object_name(self, qapp: Any) -> None:
        """各功能按钮 objectName 均为 primaryBtn。"""
        panel = _make_panel(qapp)

        for btn in panel._feature_buttons.values():
            assert btn.objectName() == "primaryBtn"

    def test_old_individual_buttons_removed(self, qapp: Any) -> None:
        """旧版独立提取/查看按钮已移除。"""
        panel = _make_panel(qapp)

        assert not hasattr(panel, "_extract_custom_character_btn")
        assert not hasattr(panel, "_view_custom_character_btn")
        assert not hasattr(panel, "_extract_protagonist_btn")
        assert not hasattr(panel, "_view_protagonist_btn")


# ===== 2. _FeatureActionDialog 返回动作键 =====


class TestFeatureActionDialog:
    """功能操作选择对话框测试。"""

    def test_dialog_action_buttons_click(self, qapp: Any) -> None:
        """直接调用 _on_action_clicked 记录动作键。"""
        dialog = _FeatureActionDialog(
            "自定义角色",
            [("extract", "提取（全量）"), ("incremental", "增量更新")],
        )

        dialog._on_action_clicked("incremental")
        assert dialog.get_selected_action() == "incremental"

    def test_dialog_default_selection_none(self, qapp: Any) -> None:
        """未选择直接关闭时返回 None。"""
        dialog = _FeatureActionDialog("上下文", [("extract", "提取")])
        assert dialog.get_selected_action() is None


# ===== 3. 动作分发与信号发射 =====


class TestFeatureActionDispatch:
    """功能按钮点击后的动作分发测试（mock 对话框）。"""

    def test_context_incremental_emits_signal(self, qapp: Any) -> None:
        """上下文·增量更新 → incremental_context_requested。"""
        from PySide6.QtTest import QSignalSpy

        panel = _make_panel(qapp)
        spy = QSignalSpy(panel.incremental_context_requested)

        with patch.object(panel, "_show_feature_dialog", return_value="incremental"):
            panel._on_context_clicked()

        assert spy.count() == 1

    def test_context_extract_emits_signal(self, qapp: Any) -> None:
        """上下文·提取 → extract_requested。"""
        from PySide6.QtTest import QSignalSpy

        panel = _make_panel(qapp)
        spy = QSignalSpy(panel.extract_requested)

        with patch.object(panel, "_show_feature_dialog", return_value="extract"):
            panel._on_context_clicked()

        assert spy.count() == 1

    def test_context_copy_emits_signal(self, qapp: Any) -> None:
        """上下文·复制到章节 → copy_to_chapter_requested。"""
        from PySide6.QtTest import QSignalSpy

        panel = _make_panel(qapp)
        spy = QSignalSpy(panel.copy_to_chapter_requested)

        with patch.object(panel, "_show_feature_dialog", return_value="copy"):
            panel._on_context_clicked()

        assert spy.count() == 1

    def test_protagonist_incremental_emits_signal(self, qapp: Any) -> None:
        """主角形象·增量更新 → incremental_protagonist_requested。"""
        from PySide6.QtTest import QSignalSpy

        panel = _make_panel(qapp)
        spy = QSignalSpy(panel.incremental_protagonist_requested)

        with patch.object(panel, "_show_feature_dialog", return_value="incremental"):
            panel._on_protagonist_clicked()

        assert spy.count() == 1

    def test_protagonist_view_emits_signal(self, qapp: Any) -> None:
        """主角形象·查看 → view_protagonist_requested。"""
        from PySide6.QtTest import QSignalSpy

        panel = _make_panel(qapp)
        spy = QSignalSpy(panel.view_protagonist_requested)

        with patch.object(panel, "_show_feature_dialog", return_value="view"):
            panel._on_protagonist_clicked()

        assert spy.count() == 1

    def test_custom_character_incremental_emits_signal(self, qapp: Any) -> None:
        """自定义角色·增量更新 → incremental_custom_character_requested。"""
        from PySide6.QtTest import QSignalSpy

        panel = _make_panel(qapp)
        spy = QSignalSpy(panel.incremental_custom_character_requested)

        with patch.object(panel, "_show_feature_dialog", return_value="incremental"):
            panel._on_custom_character_clicked()

        assert spy.count() == 1

    def test_custom_character_view_emits_signal(self, qapp: Any) -> None:
        """自定义角色·查看 → view_custom_character_requested。"""
        from PySide6.QtTest import QSignalSpy

        panel = _make_panel(qapp)
        spy = QSignalSpy(panel.view_custom_character_requested)

        with patch.object(panel, "_show_feature_dialog", return_value="view"):
            panel._on_custom_character_clicked()

        assert spy.count() == 1

    def test_ontology_extract_emits_signal(self, qapp: Any) -> None:
        """世界观底层·提取 → extract_ontology_requested。"""
        from PySide6.QtTest import QSignalSpy

        panel = _make_panel(qapp)
        spy = QSignalSpy(panel.extract_ontology_requested)

        with patch.object(panel, "_show_feature_dialog", return_value="extract"):
            panel._on_ontology_clicked()

        assert spy.count() == 1

    def test_style_view_emits_signal(self, qapp: Any) -> None:
        """文风档案·查看 → view_style_requested。"""
        from PySide6.QtTest import QSignalSpy

        panel = _make_panel(qapp)
        spy = QSignalSpy(panel.view_style_requested)

        with patch.object(panel, "_show_feature_dialog", return_value="view"):
            panel._on_style_clicked()

        assert spy.count() == 1

    def test_custom_rule_add_emits_signal(self, qapp: Any) -> None:
        """自定义设定·新增 → add_custom_rule_requested。"""
        from PySide6.QtTest import QSignalSpy

        panel = _make_panel(qapp)
        spy = QSignalSpy(panel.add_custom_rule_requested)

        with patch.object(panel, "_show_feature_dialog", return_value="add"):
            panel._on_custom_rule_clicked()

        assert spy.count() == 1

    def test_no_action_emits_nothing(self, qapp: Any) -> None:
        """未选择动作直接关闭时无信号发射。"""
        from PySide6.QtTest import QSignalSpy

        panel = _make_panel(qapp)
        spy = QSignalSpy(panel.extract_requested)

        with patch.object(panel, "_show_feature_dialog", return_value=None):
            panel._on_context_clicked()

        assert spy.count() == 0


# ===== 4. _set_feature_buttons_enabled 统一控制 =====


class TestFeatureButtonsEnabled:
    """功能按钮统一启用/禁用测试。"""

    def test_disable_all(self, qapp: Any) -> None:
        """禁用所有功能按钮与条目管理按钮。"""
        panel = _make_panel(qapp)

        panel._set_feature_buttons_enabled(False)

        for btn in panel._feature_buttons.values():
            assert btn.isEnabled() is False
        assert panel._add_btn.isEnabled() is False
        assert panel._clear_btn.isEnabled() is False
        assert panel._view_prompt_btn.isEnabled() is False

    def test_enable_all(self, qapp: Any) -> None:
        """恢复所有按钮可用。"""
        panel = _make_panel(qapp)

        panel._set_feature_buttons_enabled(False)
        panel._set_feature_buttons_enabled(True)

        for btn in panel._feature_buttons.values():
            assert btn.isEnabled() is True
        assert panel._add_btn.isEnabled() is True
        assert panel._clear_btn.isEnabled() is True
        assert panel._view_prompt_btn.isEnabled() is True

    def test_start_extraction_disables(self, qapp: Any) -> None:
        """start_extraction 禁用所有功能按钮。"""
        panel = _make_panel(qapp)

        panel.start_extraction()

        for btn in panel._feature_buttons.values():
            assert btn.isEnabled() is False

    def test_finish_extraction_enables(self, qapp: Any) -> None:
        """finish_extraction 恢复所有功能按钮。"""
        panel = _make_panel(qapp)

        panel.start_extraction()
        panel.finish_extraction([])

        for btn in panel._feature_buttons.values():
            assert btn.isEnabled() is True

    def test_fail_extraction_enables(self, qapp: Any) -> None:
        """fail_extraction 恢复所有功能按钮。"""
        panel = _make_panel(qapp)

        panel.start_extraction()
        panel.fail_extraction("测试失败")

        for btn in panel._feature_buttons.values():
            assert btn.isEnabled() is True

    def test_cancel_extraction_enables(self, qapp: Any) -> None:
        """cancel_extraction 恢复所有功能按钮。"""
        panel = _make_panel(qapp)

        panel.start_extraction()
        panel.cancel_extraction()

        for btn in panel._feature_buttons.values():
            assert btn.isEnabled() is True

    def test_restore_extraction_state_disables(self, qapp: Any) -> None:
        """restore_extraction_state 禁用所有功能按钮。"""
        panel = _make_panel(qapp)

        panel.restore_extraction_state("已缓冲文本", is_ontology=True)

        for btn in panel._feature_buttons.values():
            assert btn.isEnabled() is False


# ===== 5. get_lookback_config 供增量信号使用 =====


class TestLookbackConfig:
    """get_lookback_config 测试（增量信号携带的 dict）。"""

    def test_lookback_config_contains_keys(self, qapp: Any) -> None:
        """config 含 lookback 与 token_limit。"""
        panel = _make_panel(qapp)

        config = panel.get_lookback_config()

        assert "lookback" in config
        assert "token_limit" in config


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
