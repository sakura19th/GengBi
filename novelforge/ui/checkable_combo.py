"""可勾选多选下拉框。

基于 QComboBox + QStandardItemModel，点击项切换勾选而不关闭弹层；
关闭时用自定义绘制显示摘要文本，箭头走与普通 Combo 相同的主题 QSS。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QWidget,
)


class CheckableComboBox(QComboBox):
    """支持多选勾选的下拉框。

    Signals:
        selection_changed(list): 勾选数据列表变化（itemData 列表）
    """

    selection_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化可勾选下拉框。"""
        super().__init__(parent)
        self._placeholder = "（未选择）"
        self._display_text = self._placeholder
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        # 非编辑模式，避免 QLineEdit 盖住右侧下拉区域
        self.setEditable(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.view().viewport().installEventFilter(self)
        self._update_display()

    def set_placeholder(self, text: str) -> None:
        """设置未选择时的占位文案。"""
        self._placeholder = text
        self._update_display()

    def clear_items(self) -> None:
        """清空全部选项。"""
        self._model.clear()
        self._update_display()

    def add_check_item(
        self, text: str, data: object = None, checked: bool = False
    ) -> None:
        """添加可勾选选项。

        Args:
            text: 显示文本
            data: itemData（通常为 ID）
            checked: 是否默认勾选
        """
        item = QStandardItem(text)
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setData(data, Qt.ItemDataRole.UserRole)
        item.setCheckState(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        )
        self._model.appendRow(item)

    def checked_data(self) -> list:
        """返回已勾选项的 UserRole 数据列表（保持列表顺序）。"""
        result: list = []
        for i in range(self._model.rowCount()):
            item = self._model.item(i)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def set_checked_data(self, values: list) -> None:
        """按数据列表设置勾选状态（不在列表中的取消勾选）。"""
        wanted = set(values)
        self._model.blockSignals(True)
        for i in range(self._model.rowCount()):
            item = self._model.item(i)
            if item is None:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(
                Qt.CheckState.Checked
                if data in wanted
                else Qt.CheckState.Unchecked
            )
        self._model.blockSignals(False)
        self._update_display()

    def paintEvent(self, event) -> None:  # noqa: ANN001, ARG002
        """绘制 combo：摘要文本 + 主题 QSS 下拉箭头。"""
        painter = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        opt.currentText = self._display_text

        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)

        # 标签限制在编辑区，避免盖住右侧箭头
        edit_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            opt,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        painter.save()
        painter.setClipRect(edit_rect)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt)
        painter.restore()

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        """拦截弹层内点击：切换勾选并阻止默认关闭。"""
        if obj is self.view().viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            pos = (
                event.position().toPoint()
                if hasattr(event, "position")
                else event.pos()
            )
            index = self.view().indexAt(pos)
            if index.isValid():
                self._toggle_index(index.row())
                return True
        return super().eventFilter(obj, event)

    def hidePopup(self) -> None:
        """关闭弹层时刷新摘要显示。"""
        super().hidePopup()
        self._update_display()

    def _toggle_index(self, row: int) -> None:
        """切换指定行勾选状态并通知。"""
        item = self._model.item(row)
        if item is None:
            return
        if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return
        if item.checkState() == Qt.CheckState.Checked:
            item.setCheckState(Qt.CheckState.Unchecked)
        else:
            item.setCheckState(Qt.CheckState.Checked)
        self._update_display()
        self.selection_changed.emit(self.checked_data())

    def _update_display(self) -> None:
        """根据勾选结果更新关闭态摘要文本。"""
        checked: list[str] = []
        for i in range(self._model.rowCount()):
            item = self._model.item(i)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                checked.append(item.text())
        if not checked:
            self._display_text = self._placeholder
        elif len(checked) == 1:
            self._display_text = checked[0]
        else:
            self._display_text = f"已选 {len(checked)} 本"
        self.update()
