"""窗口状态持久化对话框基类。

为需要保存/恢复窗口几何大小的 QDialog 子类提供统一的
_restore_window_state / _save_window_state / closeEvent 实现。

子类需在 __init__ 中设置 self._settings_key（用于 QSettings 区分不同对话框），
并在 __init__ 末尾调用 self._restore_window_state()。
"""
from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog


# 应用组织名，与各管理器原有 QSettings("赓笔", "<Dialog>") 调用保持一致
_SETTINGS_ORGANIZATION = "赓笔"


class PersistentDialog(QDialog):
    """带窗口状态持久化的对话框基类。

    子类应设置 self._settings_key（用于 QSettings 区分不同对话框），
    在 __init__ 末尾调用 self._restore_window_state()。
    """

    _settings_key: str = "PersistentDialog"

    def _restore_window_state(self) -> None:
        """从 QSettings 恢复窗口几何大小。"""
        settings = QSettings(_SETTINGS_ORGANIZATION, self._settings_key)
        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def _save_window_state(self) -> None:
        """保存窗口几何大小到 QSettings。"""
        settings = QSettings(_SETTINGS_ORGANIZATION, self._settings_key)
        settings.setValue("geometry", self.saveGeometry())

    def closeEvent(self, event) -> None:
        """窗口关闭时保存窗口状态。"""
        self._save_window_state()
        super().closeEvent(event)
