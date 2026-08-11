"""QFlowLayout / FlowContainer 与提取失败后按钮不重叠回归测试。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget


@pytest.fixture(scope="module")
def qapp() -> Any:
    """模块级 QApplication 单例。"""
    app = QApplication.instance() or QApplication([])
    yield app


class TestQFlowLayoutHeight:
    """流式布局高度计算测试。"""

    def test_height_for_width_wraps(self, qapp: Any) -> None:
        """窄宽度下 heightForWidth 明显大于单行高度。"""
        from novelforge.ui.flow_layout import QFlowLayout

        host = QWidget()
        flow = QFlowLayout(host)
        flow.setSpacing(4)
        for i in range(6):
            btn = QPushButton(f"按钮{i}很长文字")
            btn.setMinimumWidth(100)
            flow.addWidget(btn)

        wide_h = flow.heightForWidth(800)
        narrow_h = flow.heightForWidth(180)
        assert narrow_h > wide_h
        # 单行高度约等于一个按钮；窄宽应至少两行
        one_line = flow.heightForWidth(2000)
        assert narrow_h >= one_line * 2 - 8

    def test_size_hint_uses_multi_line_height(self, qapp: Any) -> None:
        """sizeHint 高度应反映换行后的多行高度。"""
        from novelforge.ui.flow_layout import QFlowLayout

        host = QWidget()
        host.resize(160, 40)
        flow = QFlowLayout(host)
        flow.setSpacing(4)
        for i in range(5):
            btn = QPushButton(f"Item{i}")
            btn.setMinimumWidth(90)
            flow.addWidget(btn)
        # 先设定几何，使 sizeHint 能读到宽度
        flow.setGeometry(host.rect())
        hint = flow.sizeHint()
        assert hint.height() >= flow.heightForWidth(160) - 2


class TestFlowContainer:
    """FlowContainer height-for-width 转发测试。"""

    def test_container_has_height_for_width(self, qapp: Any) -> None:
        """容器声明 hasHeightForWidth 且窄宽更高。"""
        from novelforge.ui.flow_layout import FlowContainer

        host = FlowContainer()
        for i in range(6):
            btn = QPushButton(f"Btn{i}")
            btn.setMinimumWidth(80)
            host.flow_layout.addWidget(btn)

        assert host.hasHeightForWidth() is True
        assert host.heightForWidth(120) > host.heightForWidth(600)

    def test_vbox_reserves_multi_line_space(self, qapp: Any) -> None:
        """嵌进 QVBoxLayout 后容器实际高度应接近 heightForWidth。"""
        from novelforge.ui.flow_layout import FlowContainer

        root = QWidget()
        root.resize(200, 400)
        vbox = QVBoxLayout(root)
        vbox.setContentsMargins(0, 0, 0, 0)

        host = FlowContainer()
        host.flow_layout.setSpacing(4)
        for i in range(6):
            btn = QPushButton(f"功能{i}")
            btn.setMinimumWidth(90)
            host.flow_layout.addWidget(btn)
        below = QPushButton("下一行")
        vbox.addWidget(host)
        vbox.addWidget(below)
        vbox.addStretch()

        root.show()
        qapp.processEvents()

        expected = host.heightForWidth(host.width())
        # 允许少量边距误差
        assert host.height() >= expected - 4
        # 与下一行不重叠
        assert host.geometry().bottom() <= below.geometry().top()


class TestContextPreviewNoOverlapOnFail:
    """ContextPreviewPanel 提取失败后功能行与设置行不重叠。"""

    def test_fail_extraction_no_overlap(self, qapp: Any) -> None:
        """start → fail 后 _feature_host 与 _settings_host 几何不重叠。"""
        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        panel = ContextPreviewPanel()
        panel.resize(320, 700)
        panel.show()
        qapp.processEvents()

        panel.start_extraction()
        qapp.processEvents()
        long_err = "E" * 300 + " 提取失败详细堆栈与 API 返回"
        panel.fail_extraction(long_err)
        qapp.processEvents()
        # 强制再布局一次（部分 offscreen 环境需手动 activate）
        panel._relayout_after_state_change()
        qapp.processEvents()

        feat = panel._feature_host.geometry()
        sett = panel._settings_host.geometry()
        assert feat.height() > 0
        assert sett.height() > 0
        assert feat.bottom() <= sett.top(), (
            f"feature bottom={feat.bottom()} overlaps settings top={sett.top()} "
            f"(feat={feat}, sett={sett})"
        )

        # 按钮已恢复可用
        for btn in panel._feature_buttons.values():
            assert btn.isEnabled() is True

        # 长错误截断，完整内容在 tooltip
        assert "…" in panel._meta_label.text() or len(panel._meta_label.text()) <= 140
        assert long_err in panel._meta_label.toolTip()

    def test_fail_ontology_no_overlap(self, qapp: Any) -> None:
        """世界观提取失败后两行 Flow 仍不重叠。"""
        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        panel = ContextPreviewPanel()
        panel.resize(280, 700)
        panel.show()
        qapp.processEvents()

        panel.start_ontology_extraction()
        qapp.processEvents()
        panel.fail_ontology_extraction("ontology failed: " + ("x" * 200))
        qapp.processEvents()
        panel._relayout_after_state_change()
        qapp.processEvents()

        feat = panel._feature_host.geometry()
        sett = panel._settings_host.geometry()
        assert feat.bottom() <= sett.top()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
