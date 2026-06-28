"""CheckpointDialog 测试。

覆盖以下检查点：
1. 大纲模式（after_outline）编辑按钮回调：get_result 返回 ("edit", None)
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

from novelforge.models import Outline, Scene
from novelforge.ui.checkpoint_dialog import CheckpointDialog


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """提供全局 QApplication 单例（离屏平台）。"""
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def make_outline() -> Outline:
    """构建测试用 Outline（含 1 个场景）。"""
    return Outline(
        continuation_goals="推进主角的成长弧光",
        foreshadowing_plan="埋设神秘信物的伏笔",
        scenes=[
            Scene(
                purpose="揭示真相",
                pov="主角",
                scene_type="对话",
                goal="获取关键情报",
                conflict="信任危机",
                outcome="主角决定独自行动",
                value_shift="从依赖到独立",
                foreshadowing="信物在月光下闪烁",
                exit_hook="主角推门走入夜色",
            )
        ],
    )


class TestCheckpointDialogEditAction:
    """CheckpointDialog 大纲模式编辑按钮回调测试。"""

    def test_on_edit_returns_edit_and_none(self, qapp) -> None:
        """after_outline 模式下调用 _on_edit 后 get_result 为 ("edit", None)。"""
        outline = make_outline()
        dialog = CheckpointDialog(
            checkpoint_name="after_outline",
            payload=outline,
        )

        # 默认 action 为 cancel
        assert dialog.get_result() == ("cancel", None)

        # 触发编辑按钮回调
        dialog._on_edit()
        assert dialog.get_result() == ("edit", None)
