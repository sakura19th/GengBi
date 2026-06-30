"""世界书管理器 UI。

提供全局世界书的可视化管理界面：
- 世界书列表（新建、导入 ST、导出、复制、删除、启用/禁用）
- 条目列表拖拽排序
- 每个条目编辑器含全部字段（uid/分类/备注/关键词/内容/排序权重/注入位置/深度/角色）
- 管理器窗口位置和尺寸持久化

Signals:
    worldbook_changed(): 世界书集合或状态变化，通知主窗口刷新续写面板
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from novelforge.models import ContextEntry
from novelforge.models.worldbook import WorldBook
from novelforge.services.worldbook_service import WorldBookService
from novelforge.ui.helpers import select_combo_by_id
from novelforge.ui.persistent_dialog import PersistentDialog

logger = logging.getLogger(__name__)

# 窗口默认尺寸
DEFAULT_WINDOW_WIDTH = 900
DEFAULT_WINDOW_HEIGHT = 600


class EntryListWidget(QListWidget):
    """支持拖拽排序的条目列表控件。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)


class WorldBookManager(PersistentDialog):
    """世界书管理器对话框。

    提供世界书的增删改查、导入导出、条目排序与编辑功能。

    Signals:
        worldbook_changed(): 世界书集合或状态变化时发射
    """

    worldbook_changed = Signal()

    def __init__(
        self,
        worldbook_service: WorldBookService,
        parent: QWidget | None = None,
    ) -> None:
        """初始化世界书管理器。

        Args:
            worldbook_service: 世界书服务实例
            parent: 父控件
        """
        super().__init__(parent)
        self._settings_key = "WorldBookManager"
        self.worldbook_service = worldbook_service
        self._current_worldbook: WorldBook | None = None
        self._suppress_selection_signal = False

        self.setWindowTitle("世界书管理器")
        self.setMinimumSize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)

        self._setup_ui()
        self._setup_connections()
        self._refresh_worldbook_list()
        self._restore_window_state()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)

        # 顶部工具栏
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("世界书:"))

        self._worldbook_combo = QComboBox()
        self._worldbook_combo.setMinimumWidth(200)
        toolbar.addWidget(self._worldbook_combo)

        self._new_btn = QPushButton("新建")
        self._import_btn = QPushButton("导入 ST")
        self._export_btn = QPushButton("导出")
        self._duplicate_btn = QPushButton("复制")
        self._delete_btn = QPushButton("删除")
        self._toggle_btn = QPushButton("禁用世界书")
        toolbar.addWidget(self._new_btn)
        toolbar.addWidget(self._import_btn)
        toolbar.addWidget(self._export_btn)
        toolbar.addWidget(self._duplicate_btn)
        toolbar.addWidget(self._delete_btn)
        toolbar.addWidget(self._toggle_btn)
        toolbar.addStretch()

        layout.addLayout(toolbar)

        # 主区域：左右分割
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：条目列表
        left_group = QGroupBox("条目列表（拖拽排序）")
        left_layout = QVBoxLayout(left_group)

        self._entry_list = EntryListWidget()
        left_layout.addWidget(self._entry_list)

        # 条目操作按钮
        entry_btn_layout = QHBoxLayout()
        self._add_entry_btn = QPushButton("添加条目")
        self._delete_entry_btn = QPushButton("删除条目")
        self._move_up_btn = QPushButton("上移")
        self._move_down_btn = QPushButton("下移")
        entry_btn_layout.addWidget(self._add_entry_btn)
        entry_btn_layout.addWidget(self._delete_entry_btn)
        entry_btn_layout.addWidget(self._move_up_btn)
        entry_btn_layout.addWidget(self._move_down_btn)
        left_layout.addLayout(entry_btn_layout)

        splitter.addWidget(left_group)

        # 右侧：条目编辑器
        right_group = QGroupBox("条目编辑器")
        right_layout = QVBoxLayout(right_group)

        form = QFormLayout()

        # UID（只读）
        self._uid_edit = QLineEdit()
        self._uid_edit.setReadOnly(True)
        form.addRow("UID:", self._uid_edit)

        # 分类（只读）
        self._category_edit = QLineEdit()
        self._category_edit.setReadOnly(True)
        form.addRow("分类:", self._category_edit)

        # 备注
        self._comment_edit = QLineEdit()
        form.addRow("备注:", self._comment_edit)

        # 关键词（逗号分隔）
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("多个关键词用英文逗号分隔")
        form.addRow("关键词:", self._key_edit)

        # 内容
        self._content_edit = QPlainTextEdit()
        self._content_edit.setMinimumHeight(150)
        form.addRow("内容:", self._content_edit)

        # 排序权重
        self._order_spin = QSpinBox()
        self._order_spin.setRange(0, 9999)
        self._order_spin.setValue(100)
        form.addRow("排序权重:", self._order_spin)

        # 注入位置
        self._position_combo = QComboBox()
        self._position_combo.addItem("before (worldInfoBefore)", "before")
        self._position_combo.addItem("after (worldInfoAfter)", "after")
        self._position_combo.addItem("at_depth (按深度注入)", "at_depth")
        form.addRow("注入位置:", self._position_combo)

        # 深度
        self._depth_spin = QSpinBox()
        self._depth_spin.setRange(0, 99)
        self._depth_spin.setValue(4)
        form.addRow("深度:", self._depth_spin)

        # 角色
        self._role_combo = QComboBox()
        self._role_combo.addItem("system", "system")
        self._role_combo.addItem("user", "user")
        self._role_combo.addItem("assistant", "assistant")
        form.addRow("角色:", self._role_combo)

        right_layout.addLayout(form)

        # 保存条目按钮
        self._save_entry_btn = QPushButton("保存条目")
        right_layout.addWidget(self._save_entry_btn)

        splitter.addWidget(right_group)
        splitter.setSizes([400, 500])

        layout.addWidget(splitter)

        # 底部按钮
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()

        self._save_worldbook_btn = QPushButton("保存世界书")
        bottom_layout.addWidget(self._save_worldbook_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(close_btn)

        layout.addLayout(bottom_layout)

    def _setup_connections(self) -> None:
        """连接信号。"""
        self._worldbook_combo.currentIndexChanged.connect(
            self._on_worldbook_selected
        )
        self._new_btn.clicked.connect(self._on_new_worldbook)
        self._import_btn.clicked.connect(self._on_import_worldbook)
        self._export_btn.clicked.connect(self._on_export_worldbook)
        self._delete_btn.clicked.connect(self._on_delete_worldbook)
        self._duplicate_btn.clicked.connect(self._on_duplicate_worldbook)
        self._toggle_btn.clicked.connect(self._on_toggle_enabled)

        self._entry_list.currentRowChanged.connect(self._on_entry_selected)
        self._entry_list.model().rowsMoved.connect(self._on_entries_reordered)
        self._entry_list.itemChanged.connect(self._on_entry_check_changed)
        self._add_entry_btn.clicked.connect(self._on_add_entry)
        self._delete_entry_btn.clicked.connect(self._on_delete_entry)
        self._move_up_btn.clicked.connect(self._on_move_up)
        self._move_down_btn.clicked.connect(self._on_move_down)

        self._save_entry_btn.clicked.connect(self._on_save_entry)
        self._save_worldbook_btn.clicked.connect(self._on_save_worldbook)

    # ===== 世界书列表 =====

    def _refresh_worldbook_list(self) -> None:
        """刷新世界书列表。"""
        self._suppress_selection_signal = True
        try:
            current_id = ""
            if self._current_worldbook:
                current_id = self._current_worldbook.id

            self._worldbook_combo.clear()
            worldbooks = self.worldbook_service.list_worldbooks()
            for wb in worldbooks:
                label = wb.name
                if not wb.enabled:
                    label = f"[禁用] {label}"
                self._worldbook_combo.addItem(label, wb.id)
                if wb.id == current_id:
                    self._worldbook_combo.setCurrentIndex(
                        self._worldbook_combo.count() - 1
                    )

            # 如果没有选中的，选第一个
            if self._worldbook_combo.count() > 0 and not current_id:
                self._worldbook_combo.setCurrentIndex(0)
        finally:
            self._suppress_selection_signal = False

        if self._worldbook_combo.count() > 0:
            self._load_worldbook(self._worldbook_combo.currentData())
        else:
            self._current_worldbook = None
            self._refresh_entry_list()
            self._update_toggle_btn_text()

    def _on_worldbook_selected(self, index: int) -> None:
        """世界书下拉框切换。"""
        if self._suppress_selection_signal:
            return
        if index < 0:
            return
        wb_id = self._worldbook_combo.itemData(index)
        if wb_id:
            self._load_worldbook(wb_id)

    def _load_worldbook(self, wb_id: str) -> None:
        """加载指定世界书。"""
        wb = self.worldbook_service.load_worldbook(wb_id)
        if wb is None:
            QMessageBox.warning(self, "错误", f"加载世界书失败: {wb_id}")
            return

        self._current_worldbook = wb
        self._refresh_entry_list()
        self._update_toggle_btn_text()

    def _refresh_entry_list(self) -> None:
        """刷新条目列表。"""
        self._entry_list.blockSignals(True)
        try:
            self._entry_list.clear()
            if not self._current_worldbook:
                return

            for entry in self._current_worldbook.entries:
                label = entry.comment or entry.uid
                if not entry.enabled:
                    label = f"[禁用] {label}"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, entry.uid)
                item.setData(Qt.ItemDataRole.UserRole + 1, entry.enabled)
                # 复选框开关（参照预设）
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked
                )
                self._entry_list.addItem(item)
        finally:
            self._entry_list.blockSignals(False)

    # ===== 世界书操作 =====

    def _on_new_worldbook(self) -> None:
        """新建世界书。"""
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "新建世界书", "世界书名称:")
        if not ok or not name.strip():
            return

        try:
            wb = self.worldbook_service.create_worldbook(name.strip())
            self._refresh_worldbook_list()
            # 选中新创建的世界书
            select_combo_by_id(self._worldbook_combo, wb.id)
            self.worldbook_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"新建世界书失败: {e}")

    def _on_import_worldbook(self) -> None:
        """导入 ST 世界书 JSON。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 ST 世界书 JSON",
            "",
            "SillyTavern 世界书 (*.json);;所有文件 (*)",
        )
        if not file_path:
            return

        try:
            wb = self.worldbook_service.import_from_st_json(file_path)
            self._refresh_worldbook_list()
            select_combo_by_id(self._worldbook_combo, wb.id)
            self.worldbook_changed.emit()
            QMessageBox.information(
                self,
                "导入成功",
                f"已导入世界书: {wb.name}\n条目数: {len(wb.entries)}",
            )
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))

    def _on_export_worldbook(self) -> None:
        """导出当前世界书为 ST JSON。"""
        if not self._current_worldbook:
            QMessageBox.warning(self, "提示", "请先选择世界书")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出世界书",
            f"{self._current_worldbook.name}.json",
            "SillyTavern 世界书 (*.json);;所有文件 (*)",
        )
        if not file_path:
            return

        try:
            self.worldbook_service.export_to_st_json(
                self._current_worldbook, file_path
            )
            QMessageBox.information(self, "导出成功", f"已导出到: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_duplicate_worldbook(self) -> None:
        """复制当前世界书。"""
        if not self._current_worldbook:
            return
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            self,
            "复制世界书",
            "新世界书名称:",
            text=f"{self._current_worldbook.name} 副本",
        )
        if not ok or not name.strip():
            return

        try:
            from novelforge.utils.ids import generate_id

            new_id = generate_id("wb_")
            new_wb = WorldBook(
                id=new_id,
                name=name.strip(),
                entries=[
                    ContextEntry(
                        uid=e.uid,
                        category=e.category,
                        key=list(e.key),
                        comment=e.comment,
                        content=e.content,
                        order=e.order,
                        position=e.position,
                        depth=e.depth,
                        role=e.role,
                        enabled=e.enabled,
                        source_chapter_range=e.source_chapter_range,
                        extracted_at=e.extracted_at,
                        raw_st_fields=dict(e.raw_st_fields),
                    )
                    for e in self._current_worldbook.entries
                ],
                enabled=self._current_worldbook.enabled,
                raw_st_fields=dict(self._current_worldbook.raw_st_fields),
            )
            self.worldbook_service.save_worldbook(new_wb)
            self._refresh_worldbook_list()
            select_combo_by_id(self._worldbook_combo, new_id)
            self.worldbook_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"复制世界书失败: {e}")

    def _on_delete_worldbook(self) -> None:
        """删除当前世界书。"""
        if not self._current_worldbook:
            return

        reply = QMessageBox.question(
            self,
            "删除世界书",
            f"确定删除世界书「{self._current_worldbook.name}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self.worldbook_service.delete_worldbook(self._current_worldbook.id):
            self._current_worldbook = None
            self._refresh_worldbook_list()
            self.worldbook_changed.emit()

    # ===== 世界书启用/禁用 =====

    def _on_toggle_enabled(self) -> None:
        """切换当前世界书的启用/禁用状态。"""
        if not self._current_worldbook:
            return
        new_state = not self._current_worldbook.enabled
        if self.worldbook_service.set_worldbook_enabled(
            self._current_worldbook.id, new_state
        ):
            self._current_worldbook.enabled = new_state
            self._update_toggle_btn_text()
            # 同步下拉框标签
            self._update_combo_label_for(self._current_worldbook.id)
            self.worldbook_changed.emit()
        else:
            QMessageBox.warning(self, "错误", "切换世界书状态失败")

    def _update_combo_label_for(self, wb_id: str) -> None:
        """更新指定世界书在下拉框中的显示文本。"""
        for i in range(self._worldbook_combo.count()):
            if self._worldbook_combo.itemData(i) == wb_id:
                wb = self.worldbook_service.load_worldbook(wb_id)
                if wb is not None:
                    label = wb.name
                    if not wb.enabled:
                        label = f"[禁用] {label}"
                    self._worldbook_combo.setItemText(i, label)
                return

    def _update_toggle_btn_text(self) -> None:
        """更新启用/禁用按钮文本与状态。"""
        if not self._current_worldbook:
            self._toggle_btn.setEnabled(False)
            self._toggle_btn.setText("禁用世界书")
            return
        self._toggle_btn.setEnabled(True)
        if self._current_worldbook.enabled:
            self._toggle_btn.setText("禁用世界书")
        else:
            self._toggle_btn.setText("启用世界书")

    # ===== 条目操作 =====

    def _on_entry_selected(self, row: int) -> None:
        """条目被选中，加载到编辑器。"""
        if row < 0 or not self._current_worldbook:
            return

        item = self._entry_list.item(row)
        if item is None:
            return

        uid = item.data(Qt.ItemDataRole.UserRole)
        entry = self._find_entry(uid)
        if entry is None:
            return

        self._uid_edit.setText(entry.uid)
        self._category_edit.setText(entry.category)
        self._comment_edit.setText(entry.comment)
        self._key_edit.setText(", ".join(entry.key))
        self._content_edit.setPlainText(entry.content)
        self._order_spin.setValue(entry.order)

        pos_idx = self._position_combo.findData(entry.position)
        if pos_idx >= 0:
            self._position_combo.setCurrentIndex(pos_idx)

        self._depth_spin.setValue(entry.depth)

        role_idx = self._role_combo.findData(entry.role)
        if role_idx >= 0:
            self._role_combo.setCurrentIndex(role_idx)

    def _find_entry(self, uid: str) -> ContextEntry | None:
        """根据 UID 在当前世界书中查找条目。"""
        if not self._current_worldbook:
            return None
        for entry in self._current_worldbook.entries:
            if entry.uid == uid:
                return entry
        return None

    def _on_entry_check_changed(self, item: QListWidgetItem) -> None:
        """条目复选框状态变化（参照预设开关）。"""
        if not self._current_worldbook:
            return
        uid = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        # 防止刷新触发递归
        self._entry_list.blockSignals(True)
        try:
            self.worldbook_service.set_entry_enabled(
                self._current_worldbook, uid, enabled
            )
            # 更新镜像数据与标签（[禁用] 前缀）
            item.setData(Qt.ItemDataRole.UserRole + 1, enabled)
            entry = self._find_entry(uid)
            if entry is not None:
                label = entry.comment or entry.uid
                if not enabled:
                    label = f"[禁用] {label}"
                item.setText(label)
        finally:
            self._entry_list.blockSignals(False)
        self.worldbook_changed.emit()

    def _on_save_entry(self) -> None:
        """保存条目编辑。"""
        if not self._current_worldbook:
            return

        uid = self._uid_edit.text()
        if not uid:
            return

        entry = self._find_entry(uid)
        if entry is None:
            return

        entry.comment = self._comment_edit.text()
        entry.key = [
            k.strip() for k in self._key_edit.text().split(",") if k.strip()
        ]
        entry.content = self._content_edit.toPlainText()
        entry.order = self._order_spin.value()
        entry.position = self._position_combo.currentData()
        entry.depth = self._depth_spin.value()
        entry.role = self._role_combo.currentData()

        self.worldbook_service.save_worldbook(self._current_worldbook)
        self._refresh_entry_list()
        # 保持选中
        for i in range(self._entry_list.count()):
            item = self._entry_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == uid:
                self._entry_list.setCurrentRow(i)
                break
        self.worldbook_changed.emit()

    def _on_save_worldbook(self) -> None:
        """保存当前世界书。"""
        if not self._current_worldbook:
            return
        try:
            self.worldbook_service.save_worldbook(self._current_worldbook)
            self.worldbook_changed.emit()
            QMessageBox.information(self, "成功", "世界书已保存")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存世界书失败: {e}")

    def _on_add_entry(self) -> None:
        """添加新条目。"""
        if not self._current_worldbook:
            return
        from novelforge.utils.ids import generate_id

        uid = generate_id("wb_entry_")
        entry = ContextEntry(
            uid=uid,
            category="characters",
            comment="",
            content="",
            order=100,
            position="before",
            depth=4,
            role="system",
        )
        self._current_worldbook.entries.append(entry)
        self.worldbook_service.save_worldbook(self._current_worldbook)
        self._refresh_entry_list()
        # 选中新添加的条目
        for i in range(self._entry_list.count()):
            item = self._entry_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == uid:
                self._entry_list.setCurrentRow(i)
                break
        self.worldbook_changed.emit()

    def _on_delete_entry(self) -> None:
        """删除当前选中的条目。"""
        if not self._current_worldbook:
            return
        row = self._entry_list.currentRow()
        if row < 0:
            return

        item = self._entry_list.item(row)
        if item is None:
            return
        uid = item.data(Qt.ItemDataRole.UserRole)
        entry = self._find_entry(uid)
        label = (entry.comment or uid) if entry else uid

        reply = QMessageBox.question(
            self,
            "删除条目",
            f"确定删除条目「{label}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        entries = self._current_worldbook.entries
        for i, e in enumerate(entries):
            if e.uid == uid:
                del entries[i]
                break
        self.worldbook_service.save_worldbook(self._current_worldbook)
        self._refresh_entry_list()
        self.worldbook_changed.emit()

    def _on_move_up(self) -> None:
        """上移选中条目。"""
        row = self._entry_list.currentRow()
        if row <= 0:
            return
        current_item = self._entry_list.takeItem(row)
        self._entry_list.insertItem(row - 1, current_item)
        self._entry_list.setCurrentRow(row - 1)
        self._on_entries_reordered()

    def _on_move_down(self) -> None:
        """下移选中条目。"""
        row = self._entry_list.currentRow()
        if row < 0 or row >= self._entry_list.count() - 1:
            return
        current_item = self._entry_list.takeItem(row)
        self._entry_list.insertItem(row + 1, current_item)
        self._entry_list.setCurrentRow(row + 1)
        self._on_entries_reordered()

    def _on_entries_reordered(self) -> None:
        """条目列表拖拽排序后，按列表顺序重排 entries 并保存。"""
        if not self._current_worldbook:
            return

        # 构建当前列表中的 uid 顺序
        new_order: list[str] = []
        for i in range(self._entry_list.count()):
            item = self._entry_list.item(i)
            if item is None:
                continue
            new_order.append(item.data(Qt.ItemDataRole.UserRole))

        # 按新顺序重排 entries（保留未在列表中的条目于末尾）
        entries_map = {e.uid: e for e in self._current_worldbook.entries}
        reordered: list[ContextEntry] = []
        consumed: set[str] = set()
        for uid in new_order:
            entry = entries_map.get(uid)
            if entry is not None:
                reordered.append(entry)
                consumed.add(uid)
        for entry in self._current_worldbook.entries:
            if entry.uid not in consumed:
                reordered.append(entry)
        self._current_worldbook.entries = reordered

        self.worldbook_service.save_worldbook(self._current_worldbook)
        self.worldbook_changed.emit()

    # ===== 公共接口 =====

    def get_current_worldbook_id(self) -> str | None:
        """获取当前选中的世界书 ID。"""
        if self._current_worldbook:
            return self._current_worldbook.id
        return None

    def select_worldbook(self, wb_id: str) -> None:
        """选中指定世界书。"""
        for i in range(self._worldbook_combo.count()):
            if self._worldbook_combo.itemData(i) == wb_id:
                self._worldbook_combo.setCurrentIndex(i)
                return
