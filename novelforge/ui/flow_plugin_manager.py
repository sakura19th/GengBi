"""流程控制插件管理器。

提供插件的列表浏览、详情查看、导入导出与删除功能。
继承 :class:`PersistentDialog` 持久化窗口几何大小，非模态独立窗口。

设计参考 :class:`novelforge.ui.preset_manager.PresetManager` 与
:class:`novelforge.ui.worldbook_manager.WorldBookManager`。

Signals:
    plugin_changed(): 插件列表变更（导入/删除后），通知 MainWindow 刷新面板下拉
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novelforge.models import FlowPlugin
from novelforge.services.flow_plugin_service import FlowPluginService
from novelforge.ui.persistent_dialog import PersistentDialog

logger = logging.getLogger(__name__)


class FlowPluginManager(PersistentDialog):
    """流程控制插件管理器对话框。

    Usage::

        manager = FlowPluginManager(flow_plugin_service, parent)
        manager.plugin_changed.connect(refresh_callback)
        manager.show()
    """

    plugin_changed = Signal()

    def __init__(
        self,
        flow_plugin_service: FlowPluginService,
        parent: QWidget | None = None,
    ) -> None:
        """初始化插件管理器。

        Args:
            flow_plugin_service: 流程插件服务
            parent: 父窗口
        """
        super().__init__(parent)
        self._settings_key = "FlowPluginManager"
        self._service = flow_plugin_service
        self._current_plugin: FlowPlugin | None = None

        self.setWindowTitle("流程插件管理器")
        self.setMinimumSize(720, 480)

        self._setup_ui()
        self._setup_connections()
        self._refresh_list()
        self._restore_window_state()

    # ===== UI 构建 =====

    def _setup_ui(self) -> None:
        """构建 UI 布局。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 主区域：左右分栏（列表 | 详情）
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._list_widget = QListWidget()
        self._list_widget.setMinimumWidth(200)
        self._detail_edit = QTextEdit()
        self._detail_edit.setReadOnly(True)
        self._detail_edit.setMinimumWidth(360)
        splitter.addWidget(self._list_widget)
        splitter.addWidget(self._detail_edit)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        # 按钮区
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)
        self._import_btn = QPushButton("导入")
        self._export_btn = QPushButton("导出")
        self._delete_btn = QPushButton("删除")
        self._close_btn = QPushButton("关闭")
        for btn in (
            self._import_btn, self._export_btn,
            self._delete_btn, self._close_btn,
        ):
            btn.setMinimumWidth(72)
        btn_layout.addWidget(self._import_btn)
        btn_layout.addWidget(self._export_btn)
        btn_layout.addWidget(self._delete_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self._close_btn)
        layout.addLayout(btn_layout)

    def _setup_connections(self) -> None:
        """连接信号。"""
        self._list_widget.currentItemChanged.connect(self._on_selection_changed)
        self._import_btn.clicked.connect(self._on_import)
        self._export_btn.clicked.connect(self._on_export)
        self._delete_btn.clicked.connect(self._on_delete)
        self._close_btn.clicked.connect(self.accept)

    # ===== 数据刷新 =====

    def _refresh_list(self) -> None:
        """刷新插件列表（保留选中项）。"""
        prev_id = self._current_plugin.id if self._current_plugin else ""
        self._list_widget.clear()
        plugins = self._service.list_plugins()
        # 内置在前，自定义按 ID 排序
        plugins.sort(key=lambda p: (not p.builtin, p.id))
        restore_row = 0
        for i, plugin in enumerate(plugins):
            tag = "[内置]" if plugin.builtin else "[自定义]"
            label = f"{plugin.name} {tag}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, plugin.id)
            self._list_widget.addItem(item)
            if plugin.id == prev_id:
                restore_row = i
        if self._list_widget.count() > 0:
            self._list_widget.setCurrentRow(restore_row)
        else:
            self._current_plugin = None
            self._detail_edit.clear()
        # 按钮状态
        self._update_button_states()

    def _update_button_states(self) -> None:
        """根据当前选中项更新按钮可用状态。"""
        has_selection = self._current_plugin is not None
        is_builtin = self._current_plugin.builtin if self._current_plugin else False
        self._export_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection and not is_builtin)

    def _on_selection_changed(
        self, current: QListWidgetItem, previous: QListWidgetItem
    ) -> None:
        """选中项变化时加载详情。"""
        if current is None:
            self._current_plugin = None
            self._detail_edit.clear()
            self._update_button_states()
            return
        plugin_id = current.data(Qt.ItemDataRole.UserRole)
        plugin = self._service.load_plugin(plugin_id)
        if plugin is None:
            self._current_plugin = None
            self._detail_edit.clear()
            self._update_button_states()
            return
        self._current_plugin = plugin
        self._detail_edit.setPlainText(self._format_plugin_detail(plugin))
        self._update_button_states()

    @staticmethod
    def _format_plugin_detail(plugin: FlowPlugin) -> str:
        """格式化插件详情为纯文本展示。"""
        lines = [
            f"ID: {plugin.id}",
            f"名称: {plugin.name}",
            f"描述: {plugin.description}",
            f"版本: {plugin.version}",
            f"作者: {plugin.author}",
            f"内置: {'是' if plugin.builtin else '否'}",
            f"UI 模式: {plugin.ui_mode}",
            f"接受模式: {plugin.accept_mode}",
            "",
            f"阶段列表（{len(plugin.stages)} 个）:",
        ]
        for i, stage in enumerate(plugin.stages, 1):
            lines.append(f"  {i}. {stage.name}（id={stage.id}, agent={stage.agent}）")
            if stage.flow_key:
                lines.append(f"     flow_key: {stage.flow_key}")
            if stage.created_by:
                lines.append(f"     created_by: {stage.created_by}")
            if stage.params:
                lines.append(f"     params: {stage.params}")
            if stage.input_from:
                lines.append(f"     input_from: {stage.input_from}")
        return "\n".join(lines)

    # ===== 导入导出删除 =====

    def _on_import(self) -> None:
        """导入插件 JSON 文件。"""
        from PySide6.QtWidgets import QFileDialog

        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择流程插件 JSON", "", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not file_path:
            return
        try:
            from pathlib import Path

            plugin = self._service.import_plugin(Path(file_path))
            if plugin is None:
                QMessageBox.critical(self, "导入失败", "插件文件校验失败，请检查格式")
                return
            self._refresh_list()
            self.plugin_changed.emit()
            QMessageBox.information(
                self, "导入成功", f"已导入插件: {plugin.name}（ID: {plugin.id}）"
            )
        except Exception as e:
            logger.error("导入流程插件失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "导入失败", str(e))

    def _on_export(self) -> None:
        """导出当前选中插件为 JSON 文件。"""
        if self._current_plugin is None:
            return
        from PySide6.QtWidgets import QFileDialog

        plugin = self._current_plugin
        suggested = f"{plugin.id}.json"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出流程插件", suggested, "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not file_path:
            return
        try:
            from pathlib import Path

            if self._service.export_plugin(plugin.id, Path(file_path)):
                QMessageBox.information(self, "导出成功", f"已导出到: {file_path}")
            else:
                QMessageBox.critical(self, "导出失败", "插件不存在")
        except Exception as e:
            logger.error("导出流程插件失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_delete(self) -> None:
        """删除当前选中的自定义插件（内置不可删）。"""
        if self._current_plugin is None:
            return
        plugin = self._current_plugin
        if plugin.builtin:
            QMessageBox.warning(self, "无法删除", "内置插件不可删除")
            return
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除插件「{plugin.name}」？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if self._service.delete_plugin(plugin.id):
                self._refresh_list()
                self.plugin_changed.emit()
                self._set_status(f"已删除插件: {plugin.name}")
            else:
                QMessageBox.warning(self, "删除失败", "插件不存在或为内置插件")
        except Exception as e:
            logger.error("删除流程插件失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "删除失败", str(e))

    def _set_status(self, msg: str) -> None:
        """简单状态提示（写入窗口标题栏后缀）。"""
        self.setWindowTitle(f"流程插件管理器 — {msg}")
