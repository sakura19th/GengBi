"""流式布局（QFlowLayout）。

按添加顺序从左到右排列控件，超出可用宽度时自动换到下一行。
类似 CSS 的 flex-wrap 行为，用于解决按钮在窄屏下被截断/溢出的问题。

基于 Qt 官方 flowlayout 示例（https://doc.qt.io/qt-6/qtwidgets-layouts-flowlayout-example.html）
适配 PySide6。

Usage::

    layout = QFlowLayout()
    layout.setSpacing(4)
    layout.addWidget(btn1)
    layout.addWidget(btn2)
    parent.setLayout(layout)
"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget


class QFlowLayout(QLayout):
    """流式布局：控件从左到右排列，宽度不足时自动换行。

    Args:
        parent: 父控件（可选）
        margin: 布局外边距（默认 0）
        h_spacing: 水平间距（默认 -1，表示使用默认值）
        v_spacing: 垂直间距（默认 -1，表示使用默认值）
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        margin: int = 0,
        h_spacing: int = -1,
        v_spacing: int = -1,
    ) -> None:
        """初始化流式布局。"""
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._item_list: List[QLayout.Item] = []

    # ===== QLayout 必需接口 =====

    def addItem(self, item: QLayout.Item) -> None:  # noqa: N802
        """添加布局项。"""
        self._item_list.append(item)

    def count(self) -> int:
        """返回布局项数量。"""
        return len(self._item_list)

    def itemAt(self, index: int) -> QLayout.Item | None:  # noqa: N802
        """返回指定索引的布局项。"""
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index: int) -> QLayout.Item | None:  # noqa: N802
        """移除并返回指定索引的布局项。"""
        if 0 <= index < len(self._item_list):
            return self._item_list.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        """返回扩展方向（流式布局不扩展）。"""
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        """是否依赖宽度计算高度（流式布局是）。"""
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        """根据给定宽度计算所需高度。"""
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        """设置布局几何区域。"""
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        """返回建议尺寸。"""
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        """返回最小尺寸。"""
        size = QSize()
        for item in self._item_list:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    # ===== 间距 =====

    def setSpacing(self, spacing: int) -> None:  # noqa: N802
        """同时设置水平和垂直间距。"""
        self._h_spacing = spacing
        self._v_spacing = spacing

    def horizontalSpacing(self) -> int:  # noqa: N802
        """返回水平间距。"""
        if self._h_spacing >= 0:
            return self._h_spacing
        return self._smart_spacing(QSizePolicy.Policy.HorizontalSpacing)

    def verticalSpacing(self) -> int:  # noqa: N802
        """返回垂直间距。"""
        if self._v_spacing >= 0:
            return self._v_spacing
        return self._smart_spacing(QSizePolicy.Policy.VerticalSpacing)

    # ===== 内部实现 =====

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        """执行布局计算。

        Args:
            rect: 可用区域
            test_only: True 时仅计算高度不实际移动控件

        Returns:
            布局所需总高度
        """
        m = self.contentsMargins()
        effective_rect = rect.adjusted(
            m.left(), m.top(), -m.right(), -m.bottom()
        )
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0

        for item in self._item_list:
            wid = item.widget()
            if wid is not None:
                # 同行时考虑控件间的水平间距
                next_x = x + item.sizeHint().width() + self.horizontalSpacing()
                if next_x - self.horizontalSpacing() > effective_rect.right() and line_height > 0:
                    # 当前行放不下，换行
                    x = effective_rect.x()
                    y = y + line_height + self.verticalSpacing()
                    next_x = x + item.sizeHint().width() + self.horizontalSpacing()
                    line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y() + m.bottom()

    def _smart_spacing(self, pm: QSizePolicy.Policy) -> int:
        """根据父控件计算智能间距。

        Args:
            pm: 策略类型（水平/垂直间距）

        Returns:
            间距值，无法确定时返回 -1
        """
        parent = self.parent()
        if parent is None:
            return -1
        if parent.isWidgetType():
            w = parent
            return w.style().layoutSpacing(
                QSizePolicy.ControlType.PushButton,
                QSizePolicy.ControlType.PushButton,
                pm,
            )
        return parent.spacing()
