"""项目管理面板。

提供项目列表、打开、删除、新建等操作。
M1 阶段为单项目管理，但 UI 已支持多项目列表。

Signals:
    project_opened(str): 项目打开（project_id）
    project_deleted(str): 项目删除（project_id）
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from novelforge.models import Project
from novelforge.services.storage_service import StorageService

logger = logging.getLogger(__name__)


class ProjectPanel(QDialog):
    """项目管理对话框。

    显示最近项目列表，支持打开、删除、新建。

    Signals:
        project_opened(str): 项目打开
        project_deleted(str): 项目删除
    """

    project_opened = Signal(str)
    project_deleted = Signal(str)

    def __init__(self, storage_service: StorageService, parent=None) -> None:
        """初始化项目管理面板。

        Args:
            storage_service: 存储服务
        """
        super().__init__(parent)
        self._storage = storage_service
        self._current_project_id: str | None = None

        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 标题
        title = QLabel("项目管理")
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        # 按钮栏
        btn_layout = QHBoxLayout()
        self._new_btn = QPushButton("新建项目")
        self._open_btn = QPushButton("打开")
        self._delete_btn = QPushButton("删除")
        self._open_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        btn_layout.addWidget(self._new_btn)
        btn_layout.addWidget(self._open_btn)
        btn_layout.addWidget(self._delete_btn)
        layout.addLayout(btn_layout)

        # 项目列表
        self._list = QListWidget()
        layout.addWidget(self._list)

        # 提示
        self._hint = QLabel("双击项目打开，右键更多操作")
        self._hint.setObjectName("hintText")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._hint)

        # 连接信号
        self._new_btn.clicked.connect(self._on_new_project)
        self._open_btn.clicked.connect(self._on_open_project)
        self._delete_btn.clicked.connect(self._on_delete_project)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)

    def refresh(self) -> None:
        """刷新项目列表。"""
        self._list.clear()
        try:
            projects = self._storage.list_projects()
        except Exception as e:
            logger.error("加载项目列表失败: %s", e)
            return

        for project in projects:
            item = QListWidgetItem()
            item.setText(project.name or project.id)
            item.setData(Qt.ItemDataRole.UserRole, project.id)
            item.setToolTip(
                f"ID: {project.id}\n"
                f"创建: {project.created_at}\n"
                f"更新: {project.updated_at}\n"
                f"源文件: {project.source_file or '无'}"
            )
            self._list.addItem(item)

        if self._current_project_id:
            # 选中当前项目
            for i in range(self._list.count()):
                item = self._list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == self._current_project_id:
                    self._list.setCurrentItem(item)
                    break

    def set_current_project(self, project_id: str | None) -> None:
        """设置当前项目。"""
        self._current_project_id = project_id
        self.refresh()

    def _on_selection_changed(self) -> None:
        """选中项变更。"""
        has_selection = self._list.currentItem() is not None
        self._open_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection)

    def _on_item_double_clicked(self, item) -> None:
        """双击打开项目。"""
        project_id = item.data(Qt.ItemDataRole.UserRole)
        self._open_project_by_id(project_id)

    def _on_new_project(self) -> None:
        """新建空项目。"""
        from datetime import datetime

        project = self._storage.create_project(
            name=f"新项目_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        )
        self.refresh()
        self.project_opened.emit(project.id)
        logger.info("新建项目: %s", project.id)
        self.accept()

    def _on_open_project(self) -> None:
        """打开选中项目。"""
        item = self._list.currentItem()
        if item:
            project_id = item.data(Qt.ItemDataRole.UserRole)
            self._open_project_by_id(project_id)

    def _open_project_by_id(self, project_id: str) -> None:
        """打开指定项目。"""
        self._current_project_id = project_id
        self.project_opened.emit(project_id)
        logger.info("打开项目: %s", project_id)
        self.accept()

    def _on_delete_project(self) -> None:
        """删除选中项目。"""
        item = self._list.currentItem()
        if not item:
            return

        project_id = item.data(Qt.ItemDataRole.UserRole)
        project_name = item.text()

        reply = QMessageBox.question(
            self,
            "删除项目",
            f"确定删除项目「{project_name}」？\n\n"
            "所有章节、续写版本将被永久删除，此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                self._storage.delete_project(project_id)
                if self._current_project_id == project_id:
                    self._current_project_id = None
                self.refresh()
                self.project_deleted.emit(project_id)
                logger.info("删除项目: %s", project_id)
            except Exception as e:
                QMessageBox.critical(self, "删除失败", f"删除项目失败: {e}")
