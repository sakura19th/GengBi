"""用户输入历史（条数可配置、去重、截断、面板回填）测试。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from novelforge.core.config import ConfigManager, get_default_config


def test_default_history_settings() -> None:
    cont = get_default_config()["continuation"]
    assert cont.get("user_input_history_size") == 5
    assert cont.get("user_input_history") == []


def test_push_history_dedupe_and_order(tmp_path: Path) -> None:
    cm = ConfigManager(tmp_path / "config.json")
    assert cm.push_user_input_history("第一条") == ["第一条"]
    assert cm.push_user_input_history("第二条") == ["第二条", "第一条"]
    # 与首条相同不重复插入
    assert cm.push_user_input_history("第二条") == ["第二条", "第一条"]
    # 已有非首条相同内容：移除后置顶
    assert cm.push_user_input_history("第一条") == ["第一条", "第二条"]


def test_push_history_empty_ignored(tmp_path: Path) -> None:
    cm = ConfigManager(tmp_path / "config.json")
    cm.push_user_input_history("有内容")
    assert cm.push_user_input_history("") == ["有内容"]
    assert cm.push_user_input_history("   ") == ["有内容"]


def test_push_history_trim_to_size(tmp_path: Path) -> None:
    cm = ConfigManager(tmp_path / "config.json")
    cm.set_user_input_history_size(3)
    for i in range(5):
        cm.push_user_input_history(f"item-{i}")
    hist = cm.get_user_input_history()
    assert hist == ["item-4", "item-3", "item-2"]
    assert len(hist) == 3


def test_set_history_size_trims_existing(tmp_path: Path) -> None:
    cm = ConfigManager(tmp_path / "config.json")
    for i in range(5):
        cm.push_user_input_history(f"h{i}")
    assert len(cm.get_user_input_history()) == 5
    cm.set_user_input_history_size(2)
    assert cm.get_user_input_history_size() == 2
    assert cm.get_user_input_history() == ["h4", "h3"]


def test_history_size_clamped(tmp_path: Path) -> None:
    cm = ConfigManager(tmp_path / "config.json")
    cm.set_user_input_history_size(0)
    assert cm.get_user_input_history_size() == 1
    cm.set_user_input_history_size(99)
    assert cm.get_user_input_history_size() == 30


def test_continuation_panel_history_ui(qapp) -> None:
    """面板 set_user_input_history 填充下拉；选中回填输入框。"""
    from PySide6.QtCore import Qt

    from novelforge.ui.continuation_panel import ContinuationPanel

    panel = ContinuationPanel()
    panel.set_user_input_history([])
    assert not panel._user_input_history_combo.isEnabled()
    assert panel._user_input_history_combo.itemText(0) == "暂无历史"

    panel.set_user_input_history(["完整指令甲", "很长" * 30])
    combo = panel._user_input_history_combo
    assert combo.isEnabled()
    assert combo.count() == 3  # 占位 + 2 条
    # 激活第 1 条历史（index=1）
    panel._on_user_input_history_activated(1)
    assert panel.get_user_input() == "完整指令甲"
    # 回填后复位到占位项
    assert combo.currentIndex() == 0
    # 全文在 UserRole / tooltip
    full = combo.itemData(1, Qt.ItemDataRole.UserRole)
    assert full == "完整指令甲"
    panel.deleteLater()


@pytest.fixture
def qapp():
    """确保 QApplication 单例。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app
