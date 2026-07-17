"""世界书选择面板。

嵌入续写配置区的轻量控件，提供世界书多选下拉框。
类似预设下拉框的对应物，用于续写时选择全局世界书；选中至少一本即启用。

Signals:
    worldbook_changed(list): 选中的世界书 ID 列表变化
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QSizePolicy, QWidget

from novelforge.ui.checkable_combo import CheckableComboBox

logger = logging.getLogger(__name__)


class WorldBookPanel(QWidget):
    """世界书选择面板（嵌入续写配置区）。

    提供可多选世界书下拉框；选中至少一本即视为启用。
    """

    worldbook_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化世界书选择面板。"""
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(2)

        self._worldbook_combo = CheckableComboBox()
        self._worldbook_combo.set_placeholder("（未选择）")
        self._worldbook_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._worldbook_combo.selection_changed.connect(self._on_selection_changed)
        layout.addRow("世界书:", self._worldbook_combo)

    def set_worldbooks(
        self,
        worldbooks: list[dict[str, Any]],
        default_ids: list[str] | None = None,
    ) -> None:
        """设置世界书列表。

        Args:
            worldbooks: 世界书字典列表，每项含 id/name/enabled
            default_ids: 默认勾选的世界书 ID 列表
        """
        available = {wb["id"] for wb in worldbooks}
        valid_defaults = [i for i in (default_ids or []) if i in available]
        default_set = set(valid_defaults)

        self._worldbook_combo.clear_items()
        for wb in worldbooks:
            wb_id = wb["id"]
            self._worldbook_combo.add_check_item(
                wb.get("name", wb_id),
                wb_id,
                checked=wb_id in default_set,
            )
        # 再按 default 顺序对齐勾选（add 时已勾选，此处保证与 valid_defaults 一致）
        self._worldbook_combo.set_checked_data(valid_defaults)
        self.worldbook_changed.emit(self.get_selected_worldbook_ids())

    def get_selected_worldbook_ids(self) -> list[str]:
        """获取选中的世界书 ID 列表（按列表顺序）。"""
        return [str(x) for x in self._worldbook_combo.checked_data() if x]

    def is_enabled(self) -> bool:
        """是否启用世界书（选中至少一本）。"""
        return bool(self.get_selected_worldbook_ids())

    def _on_selection_changed(self, _ids: list) -> None:
        """勾选变化时向外转发。"""
        self.worldbook_changed.emit(self.get_selected_worldbook_ids())
