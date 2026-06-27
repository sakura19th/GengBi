"""续写历史日志查看面板。

提供历史日志的查询、详情查看、导出、清空功能：
- 顶部筛选区：项目下拉框、章节下拉框、时间范围（开始/结束日期）、查询按钮
- 中间表格：时间、项目、章节、模型、状态、字数
- 底部详情区：点击表格行显示完整 prompt_messages（JSON 格式化）和 output_text
- 导出按钮：导出选中记录为 JSON
- 清空按钮：清空当前筛选条件下的历史

Usage::

    panel = HistoryPanel(storage_service, history_service, parent)
    panel.exec()
"""
from __future__ import annotations

import json
import logging
from typing import Any

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from novelforge.services.history_service import HistoryService
from novelforge.services.storage_service import StorageService

logger = logging.getLogger(__name__)

# 表格列定义
COLUMNS = [
    ("时间", 160),
    ("项目", 100),
    ("章节", 100),
    ("模型", 120),
    ("状态", 80),
    ("字数", 60),
]


class HistoryPanel(QDialog):
    """续写历史日志查看面板。

    Args:
        storage_service: 存储服务（用于加载项目/章节列表）
        history_service: 历史日志服务
        parent: 父控件
    """

    def __init__(
        self,
        storage_service: StorageService,
        history_service: HistoryService,
        parent=None,
    ) -> None:
        """初始化历史日志面板。"""
        super().__init__(parent)
        self._storage = storage_service
        self._history = history_service
        self._current_logs: list[dict[str, Any]] = []

        self.setWindowTitle("续写历史日志")
        self.setMinimumSize(900, 600)

        self._setup_ui()
        self._load_projects()
        self._refresh()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)

        # ===== 筛选区 =====
        filter_group = QGroupBox("筛选条件")
        filter_layout = QFormLayout(filter_group)

        self._project_combo = QComboBox()
        self._project_combo.addItem("全部项目", "")
        filter_layout.addRow("项目:", self._project_combo)

        self._chapter_combo = QComboBox()
        self._chapter_combo.addItem("全部章节", "")
        filter_layout.addRow("章节:", self._chapter_combo)

        time_layout = QHBoxLayout()
        self._start_date = QDateEdit()
        self._start_date.setCalendarPopup(True)
        self._start_date.setDate(QDate.currentDate().addMonths(-1))
        self._start_date.setDisplayFormat("yyyy-MM-dd")
        time_layout.addWidget(QLabel("从:"))
        time_layout.addWidget(self._start_date)

        self._end_date = QDateEdit()
        self._end_date.setCalendarPopup(True)
        self._end_date.setDate(QDate.currentDate())
        self._end_date.setDisplayFormat("yyyy-MM-dd")
        time_layout.addWidget(QLabel("到:"))
        time_layout.addWidget(self._end_date)

        filter_layout.addRow("时间范围:", time_layout)

        btn_layout = QHBoxLayout()
        self._query_btn = QPushButton("查询")
        self._query_btn.clicked.connect(self._refresh)
        btn_layout.addWidget(self._query_btn)

        self._reset_btn = QPushButton("重置")
        self._reset_btn.clicked.connect(self._on_reset_filter)
        btn_layout.addWidget(self._reset_btn)

        btn_layout.addStretch()

        self._export_btn = QPushButton("导出选中记录")
        self._export_btn.clicked.connect(self._on_export_selected)
        btn_layout.addWidget(self._export_btn)

        self._clear_btn = QPushButton("清空历史")
        self._clear_btn.clicked.connect(self._on_clear_history)
        btn_layout.addWidget(self._clear_btn)

        filter_layout.addRow("", btn_layout)

        layout.addWidget(filter_group)

        # ===== 主体：表格 + 详情区（QSplitter 垂直分割）=====
        splitter = QSplitter(Qt.Orientation.Vertical)

        # 表格
        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        for i, (title, width) in enumerate(COLUMNS):
            self._table.setHorizontalHeaderItem(i, QTableWidgetItem(title))
            self._table.setColumnWidth(i, width)
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)
        splitter.addWidget(self._table)

        # 详情区
        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)

        detail_label = QLabel("详情（点击表格行查看）")
        detail_label.setObjectName("panelTitle")
        detail_layout.addWidget(detail_label)

        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setPlaceholderText("prompt_messages (JSON 格式化)")
        self._prompt_edit.setReadOnly(True)
        self._prompt_edit.setMaximumHeight(180)
        detail_layout.addWidget(QLabel("Prompt Messages:"))
        detail_layout.addWidget(self._prompt_edit)

        self._output_edit = QPlainTextEdit()
        self._output_edit.setPlaceholderText("output_text")
        self._output_edit.setReadOnly(True)
        detail_layout.addWidget(QLabel("Output Text:"))
        detail_layout.addWidget(self._output_edit)

        splitter.addWidget(detail_widget)
        splitter.setSizes([300, 300])

        layout.addWidget(splitter, 1)

        # 状态标签
        self._status_label = QLabel("就绪")
        self._status_label.setObjectName("textSecondary")
        layout.addWidget(self._status_label)

    def _load_projects(self) -> None:
        """加载项目列表到下拉框。"""
        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        self._project_combo.addItem("全部项目", "")
        try:
            projects = self._storage.list_projects()
            for p in projects:
                self._project_combo.addItem(p.name or p.id, p.id)
        except Exception as e:
            logger.error("加载项目列表失败: %s", e)

        self._project_combo.blockSignals(False)
        self._project_combo.currentIndexChanged.connect(self._on_project_changed)

    def _on_project_changed(self, _index: int) -> None:
        """项目变更：刷新章节下拉框。"""
        project_id = self._project_combo.currentData()
        self._chapter_combo.blockSignals(True)
        self._chapter_combo.clear()
        self._chapter_combo.addItem("全部章节", "")
        if project_id:
            try:
                chapters = self._storage.list_chapters(project_id)
                for ch in chapters:
                    label = f"第{ch.index + 1}章 {ch.title}".strip()
                    self._chapter_combo.addItem(label, ch.id)
            except Exception as e:
                logger.error("加载章节列表失败: %s", e)
        self._chapter_combo.blockSignals(False)

    def _on_reset_filter(self) -> None:
        """重置筛选条件。"""
        self._project_combo.setCurrentIndex(0)
        self._chapter_combo.setCurrentIndex(0)
        self._start_date.setDate(QDate.currentDate().addMonths(-1))
        self._end_date.setDate(QDate.currentDate())
        self._refresh()

    def _refresh(self) -> None:
        """按当前筛选条件查询历史日志。"""
        project_id = self._project_combo.currentData() or None
        chapter_id = self._chapter_combo.currentData() or None
        start_date = self._start_date.date()
        end_date = self._end_date.date()
        start_time = f"{start_date.toString('yyyy-MM-dd')}T00:00:00" if start_date.isValid() else None
        end_time = f"{end_date.toString('yyyy-MM-dd')}T23:59:59" if end_date.isValid() else None

        try:
            logs = self._history.list_history(
                project_id=project_id,
                chapter_id=chapter_id,
                start_time=start_time,
                end_time=end_time,
                limit=500,
            )
        except Exception as e:
            logger.error("查询历史日志失败: %s", e)
            logs = []

        self._current_logs = logs
        self._populate_table(logs)
        self._status_label.setText(f"共 {len(logs)} 条记录")

    def _populate_table(self, logs: list[dict[str, Any]]) -> None:
        """填充表格。"""
        self._table.setRowCount(0)
        for row_idx, log in enumerate(logs):
            self._table.insertRow(row_idx)
            # 时间
            time_str = log.get("started_at", "") or ""
            # 截断到 19 字符（yyyy-MM-ddTHH:MM:SS）
            if len(time_str) > 19:
                time_str = time_str[:19]
            self._table.setItem(row_idx, 0, QTableWidgetItem(time_str))
            # 项目
            self._table.setItem(
                row_idx, 1, QTableWidgetItem(log.get("project_id", "") or "")
            )
            # 章节
            self._table.setItem(
                row_idx, 2, QTableWidgetItem(log.get("chapter_id", "") or "")
            )
            # 模型
            self._table.setItem(
                row_idx, 3, QTableWidgetItem(log.get("model", "") or "")
            )
            # 状态
            self._table.setItem(
                row_idx, 4, QTableWidgetItem(log.get("status", "") or "")
            )
            # 字数
            output_text = log.get("output_text", "") or ""
            self._table.setItem(
                row_idx, 5, QTableWidgetItem(str(len(output_text)))
            )

    def _on_table_selection_changed(self) -> None:
        """表格选中行变更：显示详情。"""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._current_logs):
            self._prompt_edit.clear()
            self._output_edit.clear()
            return

        log = self._current_logs[row]
        prompt_messages = log.get("prompt_messages", [])
        try:
            prompt_str = json.dumps(prompt_messages, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as e:
            prompt_str = f"[序列化失败: {e}]"
        self._prompt_edit.setPlainText(prompt_str)
        self._output_edit.setPlainText(log.get("output_text", "") or "")

    def _on_export_selected(self) -> None:
        """导出选中记录为 JSON 文件。"""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._current_logs):
            QMessageBox.information(self, "提示", "请先选择一条记录")
            return

        log = self._current_logs[row]
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出历史日志",
            f"history_{log.get('id', 'record')}.json",
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(log, f, ensure_ascii=False, indent=2)
            self._status_label.setText(f"已导出到: {file_path}")
            logger.info("导出历史日志到 %s", file_path)
        except OSError as e:
            QMessageBox.critical(self, "导出失败", f"写入文件失败: {e}")

    def _on_clear_history(self) -> None:
        """清空当前筛选条件下的历史。"""
        project_id = self._project_combo.currentData() or None
        scope = f"项目 {project_id}" if project_id else "所有项目"
        reply = QMessageBox.question(
            self,
            "清空历史",
            f"确定清空{scope}的历史日志？\n\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self._history.clear_history(project_id=project_id)
            self._refresh()
            self._status_label.setText("已清空历史日志")
        except Exception as e:
            QMessageBox.critical(self, "清空失败", f"清空历史日志失败: {e}")
