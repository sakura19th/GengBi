"""世界书选择面板。

嵌入续写配置区的轻量控件，提供世界书下拉框与启用复选框。
类似预设下拉框的对应物，用于续写时选择全局世界书。

Signals:
    worldbook_changed(str): 选中的世界书 ID 变化
    worldbook_enabled_changed(bool): 启用状态变化
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class WorldBookPanel(QWidget):
    """世界书选择面板（嵌入续写配置区）。

    提供世界书下拉框与启用复选框，续写时读取选中的世界书条目。
    """

    worldbook_changed = Signal(str)
    worldbook_enabled_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化世界书选择面板。"""
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 世界书下拉框
        self._worldbook_combo = QComboBox()
        self._worldbook_combo.addItem("（无）", "")
        layout.addRow("世界书:", self._worldbook_combo)

        # 启用复选框
        self._enabled_check = QCheckBox("启用世界书")
        self._enabled_check.setChecked(False)
        layout.addRow("", self._enabled_check)

    def set_worldbooks(
        self, worldbooks: list[dict[str, Any]], default_id: str = ""
    ) -> None:
        """设置世界书列表。

        Args:
            worldbooks: 世界书字典列表，每项含 id/name/enabled
            default_id: 默认选中的世界书 ID
        """
        self._worldbook_combo.blockSignals(True)
        self._worldbook_combo.clear()
        self._worldbook_combo.addItem("（无）", "")
        default_idx = 0
        for i, wb in enumerate(worldbooks, start=1):
            self._worldbook_combo.addItem(wb.get("name", wb["id"]), wb["id"])
            if wb["id"] == default_id:
                default_idx = i
        self._worldbook_combo.setCurrentIndex(default_idx)
        self._worldbook_combo.blockSignals(False)
        # 触发一次 changed 信号
        self.worldbook_changed.emit(self.get_selected_worldbook_id())

    def get_selected_worldbook_id(self) -> str:
        """获取选中的世界书 ID（未选择时返回空字符串）。"""
        idx = self._worldbook_combo.currentIndex()
        if idx <= 0:
            return ""
        return self._worldbook_combo.itemData(idx) or ""

    def is_enabled(self) -> bool:
        """是否启用世界书。"""
        return self._enabled_check.isChecked() and self.get_selected_worldbook_id()
