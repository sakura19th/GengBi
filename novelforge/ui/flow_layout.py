"""流式布局（QFlowLayout）与容器（FlowContainer）。

按添加顺序从左到右排列控件，超出可用宽度时自动换到下一行。
类似 CSS 的 flex-wrap 行为，用于解决按钮在窄屏下被截断/溢出的问题。

基于 Qt 官方 flowlayout 示例（https://doc.qt.io/qt-6/qtwidgets-layouts-flowlayout-example.html）
适配 PySide6。

嵌进 QVBoxLayout 时请使用 FlowContainer（addWidget），不要裸 addLayout(QFlowLayout)：
父布局只有通过 height-for-width 的 QWidget 才会为换行预留正确高度。

Usage::

    host = FlowContainer()
    host.flow_layout.setSpacing(4)
    host.flow_layout.addWidget(btn1)
    host.flow_layout.addWidget(btn2)
    parent_layout.addWidget(host)
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
        self._last_height: int = -1

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
        """设置布局几何区域；高度变化时通知父控件重算。"""
        super().setGeometry(rect)
        h = self._do_layout(rect, test_only=False)
        if h != self._last_height:
            self._last_height = h
            parent = self.parentWidget()
            if parent is not None:
                parent.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802
        """返回建议尺寸（按当前几何宽度估算多行高度）。"""
        width = self.geometry().width()
        if width <= 0:
            parent = self.parentWidget()
            if parent is not None and parent.width() > 0:
                width = parent.width()
            else:
                width = 200
        h = self.heightForWidth(width)
        ms = self.minimumSize()
        return QSize(max(ms.width(), 0), max(h, ms.height()))

    def minimumSize(self) -> QSize:  # noqa: N802
        """返回最小尺寸（单控件最大宽高 + 边距）。"""
        size = QSize()
        for item in self._item_list:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
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
        return self._smart_spacing(Qt.Orientation.Horizontal)

    def verticalSpacing(self) -> int:  # noqa: N802
        """返回垂直间距。"""
        if self._v_spacing >= 0:
            return self._v_spacing
        return self._smart_spacing(Qt.Orientation.Vertical)

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
        h_space = self.horizontalSpacing()
        v_space = self.verticalSpacing()
        if h_space < 0:
            h_space = 0
        if v_space < 0:
            v_space = 0

        for item in self._item_list:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
            # 同行时考虑控件间的水平间距
            next_x = x + item.sizeHint().width() + h_space
            if (
                next_x - h_space > effective_rect.right()
                and line_height > 0
            ):
                # 当前行放不下，换行
                x = effective_rect.x()
                y = y + line_height + v_space
                next_x = x + item.sizeHint().width() + h_space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        if line_height == 0 and not self._item_list:
            return m.top() + m.bottom()
        return y + line_height - rect.y() + m.bottom()

    def _smart_spacing(self, orientation: Qt.Orientation) -> int:
        """根据父控件计算智能间距。

        Args:
            orientation: 水平或垂直

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
                orientation,
            )
        return parent.spacing()


class FlowContainer(QWidget):
    """承载 QFlowLayout 的容器，保证 height-for-width 对父 QVBoxLayout 生效。

    用法：向 ``flow_layout`` 添加子控件，再把本容器 ``addWidget`` 到父布局。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化流式容器。"""
        super().__init__(parent)
        self._flow = QFlowLayout(self)
        # 横向扩展、纵向按内容，避免被 VBox 压成单行
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )

    @property
    def flow_layout(self) -> QFlowLayout:
        """内部流式布局。"""
        return self._flow

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        """转发：依赖宽度计算高度。"""
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        """转发：按宽度计算流式高度。"""
        return self._flow.heightForWidth(width)

    def sizeHint(self) -> QSize:  # noqa: N802
        """按当前宽度返回多行建议尺寸。"""
        w = self.width() if self.width() > 0 else 200
        h = self.heightForWidth(w)
        ms = self.minimumSizeHint()
        return QSize(max(ms.width(), w if self.width() > 0 else ms.width()), max(h, ms.height()))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """转发布局最小尺寸。"""
        return self._flow.minimumSize()
