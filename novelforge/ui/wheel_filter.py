"""滚轮事件过滤器：未聚焦控件不响应滚轮，转发给父级 QScrollArea。

安装到 QComboBox/QSpinBox/QDoubleSpinBox 上，使鼠标滚轮仅在控件已聚焦时
改变值；未聚焦时滚轮事件转发给父级 QAbstractScrollArea 的 viewport 滚动页面。
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea


class WheelEventFilter(QObject):
    """滚轮事件过滤器。

    安装到 QComboBox/QSpinBox 的 viewport() 或控件本身。当控件未聚焦时，
    拦截 QWheelEvent 并转发给父级 QAbstractScrollArea 的 viewport，使页面
    滚动而非改变控件值。控件聚焦时放行，正常响应滚轮。
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """拦截滚轮事件：未聚焦时转发给父级滚动区域。

        Args:
            obj: 被监控的对象
            event: 事件对象

        Returns:
            True=过滤（阻止默认处理），False=放行
        """
        if event.type() == QEvent.Type.Wheel:
            if not obj.hasFocus():
                # 未聚焦：查找父级 QAbstractScrollArea 并转发滚轮事件
                parent = obj.parent()
                while parent is not None:
                    if isinstance(parent, QAbstractScrollArea):
                        QCoreApplication.sendEvent(parent.viewport(), event)
                        return True  # 过滤掉 obj 的默认处理
                    parent = parent.parent()
                # 无父级滚动区域，吞掉事件（不改变值）
                return True
            else:
                # 聚焦时放行，让控件正常处理
                return False
        return False
