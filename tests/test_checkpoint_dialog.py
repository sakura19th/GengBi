"""CheckpointDialog 测试。

覆盖以下检查点：
1. 简单模式（after_deep_analysis）编辑按钮回调：get_result 返回 ("edit", None)
2. 简单模式接受按钮回调：get_result 返回 ("accept", payload)
3. 简单模式取消按钮回调：get_result 返回 ("cancel", None)
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
from PySide6.QtWidgets import QApplication

from novelforge.ui.checkpoint_dialog import CheckpointDialog


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """提供全局 QApplication 单例（离屏平台）。"""
    app = QApplication.instance() or QApplication(sys.argv)
    return app


class TestCheckpointDialogSimpleMode:
    """CheckpointDialog 简单模式（卷续写检查点）回调测试。"""

    def test_default_action_is_cancel(self, qapp) -> None:
        """默认 action 为 cancel。"""
        dialog = CheckpointDialog(
            checkpoint_name="after_deep_analysis",
            payload={"sample": "data"},
        )
        assert dialog.get_result() == ("cancel", None)

    def test_on_edit_returns_edit_and_none(self, qapp) -> None:
        """after_deep_analysis 模式下调用 _on_edit 后 get_result 为 ("edit", None)。"""
        dialog = CheckpointDialog(
            checkpoint_name="after_deep_analysis",
            payload={"sample": "data"},
        )
        dialog._on_edit()
        assert dialog.get_result() == ("edit", None)

    def test_on_accept_returns_accept_and_payload(self, qapp) -> None:
        """after_deep_analysis 模式下调用 _on_accept 后 get_result 为 ("accept", payload)。"""
        payload = {"sample": "data"}
        dialog = CheckpointDialog(
            checkpoint_name="after_volume_outline",
            payload=payload,
        )
        dialog._on_accept()
        action, result_payload = dialog.get_result()
        assert action == "accept"
        assert result_payload == payload

    def test_on_cancel_returns_cancel_and_none(self, qapp) -> None:
        """after_audit 模式下调用 _on_cancel 后 get_result 为 ("cancel", None)。"""
        dialog = CheckpointDialog(
            checkpoint_name="after_audit",
            payload={"sample": "data"},
        )
        dialog._on_cancel()
        assert dialog.get_result() == ("cancel", None)
