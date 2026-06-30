"""预设管理器 UI。

提供写作预设的可视化管理界面：
- 预设列表（新建、导入、导出、编辑、删除）
- 提示词列表拖拽排序（prompt_order）
- 每个提示启用/禁用复选框
- marker 提示显示 [marker] 不可删除
- system_prompt 提示不可删除可禁用
- 提示编辑器含全部字段
- 管理器窗口位置和尺寸持久化

Signals:
    preset_changed(str): 当前选中的预设 ID 变化
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novelforge.models import (
    GLOBAL_CHARACTER_ID,
    Prompt,
    PromptOrderEntry,
    PromptOrderGroup,
    WritingPreset,
)
from novelforge.services.preset_service import PresetService
from novelforge.ui.helpers import select_combo_by_id
from novelforge.ui.persistent_dialog import PersistentDialog

logger = logging.getLogger(__name__)

# 窗口默认尺寸
DEFAULT_WINDOW_WIDTH = 900
DEFAULT_WINDOW_HEIGHT = 600


class PromptListWidget(QListWidget):
    """支持拖拽排序的提示词列表控件。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)


class PresetManager(PersistentDialog):
    """预设管理器对话框。

    提供预设的增删改查、导入导出、提示词排序与编辑功能。

    Signals:
        preset_changed(str): 当前选中的预设 ID 变化
    """

    preset_changed = Signal(str)

    def __init__(
        self,
        preset_service: PresetService,
        parent: QWidget | None = None,
        regex_service: Any = None,
    ) -> None:
        """初始化预设管理器。

        Args:
            preset_service: 预设服务实例
            parent: 父控件
            regex_service: 正则服务实例（可选，用于导入预设时同步正则脚本）
        """
        super().__init__(parent)
        self._settings_key = "PresetManager"
        self.preset_service = preset_service
        self.regex_service = regex_service
        self._current_preset: WritingPreset | None = None
        self._suppress_selection_signal = False

        self.setWindowTitle("预设管理器")
        self.setMinimumSize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)

        self._setup_ui()
        self._setup_connections()
        self._refresh_preset_list()
        self._restore_window_state()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)

        # 顶部工具栏
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("预设:"))

        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(200)
        toolbar.addWidget(self._preset_combo)

        self._new_btn = QPushButton("新建")
        self._import_btn = QPushButton("导入 ST 预设")
        self._export_btn = QPushButton("导出")
        self._delete_btn = QPushButton("删除")
        self._duplicate_btn = QPushButton("复制")
        self._toggle_preset_btn = QPushButton("禁用预设")
        self._reset_default_btn = QPushButton("恢复默认预设")
        toolbar.addWidget(self._new_btn)
        toolbar.addWidget(self._import_btn)
        toolbar.addWidget(self._export_btn)
        toolbar.addWidget(self._duplicate_btn)
        toolbar.addWidget(self._delete_btn)
        toolbar.addWidget(self._toggle_preset_btn)
        toolbar.addWidget(self._reset_default_btn)
        toolbar.addStretch()

        layout.addLayout(toolbar)

        # 主区域：左右分割
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：提示词列表
        left_group = QGroupBox("提示词列表（拖拽排序）")
        left_layout = QVBoxLayout(left_group)

        # 提示词列表
        self._prompt_list = PromptListWidget()
        left_layout.addWidget(self._prompt_list)

        # 提示词操作按钮
        prompt_btn_layout = QHBoxLayout()
        self._add_prompt_btn = QPushButton("添加提示")
        self._remove_prompt_btn = QPushButton("删除提示")
        self._move_up_btn = QPushButton("上移")
        self._move_down_btn = QPushButton("下移")
        prompt_btn_layout.addWidget(self._add_prompt_btn)
        prompt_btn_layout.addWidget(self._remove_prompt_btn)
        prompt_btn_layout.addWidget(self._move_up_btn)
        prompt_btn_layout.addWidget(self._move_down_btn)
        left_layout.addLayout(prompt_btn_layout)

        splitter.addWidget(left_group)

        # 右侧：提示编辑器
        right_group = QGroupBox("提示编辑器")
        right_layout = QVBoxLayout(right_group)

        form = QFormLayout()

        # identifier（只读）
        self._identifier_edit = QLineEdit()
        self._identifier_edit.setReadOnly(True)
        form.addRow("Identifier:", self._identifier_edit)

        # name
        self._name_edit = QLineEdit()
        form.addRow("名称:", self._name_edit)

        # role
        self._role_combo = QComboBox()
        self._role_combo.addItems(["system", "user", "assistant"])
        form.addRow("角色:", self._role_combo)

        # content
        self._content_edit = QPlainTextEdit()
        self._content_edit.setMinimumHeight(100)
        form.addRow("内容:", self._content_edit)

        # system_prompt（只读复选框）
        self._system_prompt_check = QCheckBox("系统提示（不可删除）")
        self._system_prompt_check.setEnabled(False)
        form.addRow("", self._system_prompt_check)

        # marker（只读）
        self._marker_edit = QLineEdit()
        self._marker_edit.setReadOnly(True)
        form.addRow("Marker:", self._marker_edit)

        # position
        self._position_combo = QComboBox()
        self._position_combo.addItems(["start", "end"])
        form.addRow("Position:", self._position_combo)

        # injection_position
        self._injection_position_combo = QComboBox()
        self._injection_position_combo.addItem("RELATIVE（相对排序）", 0)
        self._injection_position_combo.addItem("ABSOLUTE（深度注入）", 1)
        form.addRow("注入位置:", self._injection_position_combo)

        # injection_depth
        self._injection_depth_spin = QSpinBox()
        self._injection_depth_spin.setRange(0, 100)
        self._injection_depth_spin.setValue(4)
        form.addRow("注入深度:", self._injection_depth_spin)

        # injection_order
        self._injection_order_spin = QSpinBox()
        self._injection_order_spin.setRange(0, 1000)
        self._injection_order_spin.setValue(100)
        form.addRow("注入顺序:", self._injection_order_spin)

        # forbid_overrides
        self._forbid_overrides_check = QCheckBox("禁止覆盖")
        form.addRow("", self._forbid_overrides_check)

        # extension（JSON 文本编辑）
        self._extension_edit = QLineEdit()
        self._extension_edit.setPlaceholderText("{}")
        form.addRow("扩展（JSON）:", self._extension_edit)

        right_layout.addLayout(form)

        # 保存按钮
        self._save_prompt_btn = QPushButton("保存提示")
        right_layout.addWidget(self._save_prompt_btn)

        splitter.addWidget(right_group)
        splitter.setSizes([400, 500])

        layout.addWidget(splitter)

        # 底部生成参数
        params_group = QGroupBox("生成参数")
        params_form = QFormLayout(params_group)

        self._temperature_spin = QSpinBox()
        self._temperature_spin.setRange(0, 200)
        self._temperature_spin.setValue(80)
        self._temperature_spin.setSuffix(" (×0.01)")
        params_form.addRow("温度:", self._temperature_spin)

        self._max_tokens_spin = QSpinBox()
        self._max_tokens_spin.setRange(100, 1000000)
        self._max_tokens_spin.setMinimumWidth(120)
        self._max_tokens_spin.setValue(2000)
        params_form.addRow("最大 Token:", self._max_tokens_spin)

        self._max_context_spin = QSpinBox()
        self._max_context_spin.setRange(1000, 9999999)
        self._max_context_spin.setValue(9999999)
        params_form.addRow("最大上下文:", self._max_context_spin)

        self._top_p_spin = QSpinBox()
        self._top_p_spin.setRange(0, 100)
        self._top_p_spin.setValue(99)
        self._top_p_spin.setSuffix(" (×0.01)")
        params_form.addRow("Top P:", self._top_p_spin)

        self._top_k_spin = QSpinBox()
        self._top_k_spin.setRange(0, 1000)
        self._top_k_spin.setValue(0)
        params_form.addRow("Top K:", self._top_k_spin)

        self._reasoning_effort_combo = QComboBox()
        self._reasoning_effort_combo.addItems(["auto", "low", "medium", "high", "max"])
        params_form.addRow("推理强度:", self._reasoning_effort_combo)

        self._save_params_btn = QPushButton("保存参数")
        params_form.addRow("", self._save_params_btn)

        layout.addWidget(params_group)

        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _setup_connections(self) -> None:
        """连接信号。"""
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        self._new_btn.clicked.connect(self._on_new_preset)
        self._import_btn.clicked.connect(self._on_import_preset)
        self._export_btn.clicked.connect(self._on_export_preset)
        self._delete_btn.clicked.connect(self._on_delete_preset)
        self._duplicate_btn.clicked.connect(self._on_duplicate_preset)

        self._prompt_list.currentRowChanged.connect(self._on_prompt_selected)
        self._prompt_list.model().rowsMoved.connect(self._on_prompts_reordered)
        self._prompt_list.itemChanged.connect(self._on_prompt_check_changed)
        self._add_prompt_btn.clicked.connect(self._on_add_prompt)
        self._remove_prompt_btn.clicked.connect(self._on_remove_prompt)
        self._move_up_btn.clicked.connect(self._on_move_up)
        self._move_down_btn.clicked.connect(self._on_move_down)

        self._save_prompt_btn.clicked.connect(self._on_save_prompt)
        self._save_params_btn.clicked.connect(self._on_save_params)

        self._toggle_preset_btn.clicked.connect(self._on_toggle_preset_enabled)
        self._reset_default_btn.clicked.connect(self._on_reset_default_preset)

    # ===== 预设列表 =====

    def _refresh_preset_list(self) -> None:
        """刷新预设列表。"""
        self._suppress_selection_signal = True
        try:
            current_id = ""
            if self._current_preset:
                current_id = self._current_preset.id

            self._preset_combo.clear()
            presets = self.preset_service.list_presets()
            for preset in presets:
                self._preset_combo.addItem(preset.name, preset.id)
                if preset.id == current_id:
                    self._preset_combo.setCurrentIndex(
                        self._preset_combo.count() - 1
                    )

            # 如果没有选中的，选第一个
            if self._preset_combo.count() > 0 and not current_id:
                self._preset_combo.setCurrentIndex(0)
        finally:
            self._suppress_selection_signal = False

        if self._preset_combo.count() > 0:
            self._load_preset(self._preset_combo.currentData())

    def _on_preset_selected(self, index: int) -> None:
        """预设被选中。"""
        if self._suppress_selection_signal:
            return
        if index < 0:
            return
        preset_id = self._preset_combo.itemData(index)
        if preset_id:
            self._load_preset(preset_id)

    def _load_preset(self, preset_id: str) -> None:
        """加载指定预设。"""
        preset = self.preset_service.load_preset(preset_id)
        if preset is None:
            QMessageBox.warning(self, "错误", f"加载预设失败: {preset_id}")
            return

        self._current_preset = preset
        self._refresh_prompt_list()
        self._refresh_params()
        self._update_toggle_preset_btn_text()
        self.preset_changed.emit(preset_id)

    def _refresh_prompt_list(self) -> None:
        """刷新提示词列表。"""
        self._prompt_list.blockSignals(True)
        try:
            self._prompt_list.clear()
            if not self._current_preset:
                return

            # 获取全局 prompt_order
            order_entries = self._get_global_order()

            # 构建 identifier → Prompt 映射
            prompt_map = {p.identifier: p for p in self._current_preset.prompts}

            for entry in order_entries:
                prompt = prompt_map.get(entry.identifier)
                if prompt is None:
                    continue

                # 显示文本
                label = prompt.name or prompt.identifier
                if prompt.marker:
                    label = f"[marker] {label}"
                elif prompt.system_prompt:
                    label = f"[系统] {label}"
                if not entry.enabled:
                    label = f"[禁用] {label}"

                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, prompt.identifier)
                item.setData(Qt.ItemDataRole.UserRole + 1, entry.enabled)
                # 复选框开关
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked
                )
                self._prompt_list.addItem(item)
        finally:
            self._prompt_list.blockSignals(False)

    def _get_global_order(self) -> list[PromptOrderEntry]:
        """获取全局 prompt_order。"""
        if not self._current_preset or not self._current_preset.prompt_order:
            return []
        for group in self._current_preset.prompt_order:
            if group.character_id == GLOBAL_CHARACTER_ID:
                return list(group.order)
        return []

    def _refresh_params(self) -> None:
        """刷新生成参数。"""
        if not self._current_preset:
            return
        params = self._current_preset.generation_params
        temp = params.get("temperature", 0.8)
        if isinstance(temp, (int, float)):
            self._temperature_spin.setValue(int(temp * 100))
        self._max_tokens_spin.setValue(params.get("max_tokens", 2000))
        self._max_context_spin.setValue(params.get("max_context", 32000))
        top_p = params.get("top_p", 1.0)
        if isinstance(top_p, (int, float)):
            self._top_p_spin.setValue(int(top_p * 100))
        self._top_k_spin.setValue(params.get("top_k", 0))
        reasoning = params.get("reasoning_effort", "high")
        if reasoning not in ("auto", "low", "medium", "high", "max"):
            reasoning = "high"
        self._reasoning_effort_combo.setCurrentText(reasoning)

    # ===== 预设操作 =====

    def _on_new_preset(self) -> None:
        """新建预设。"""
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "新建预设", "预设名称:")
        if not ok or not name.strip():
            return

        try:
            preset = self.preset_service.create_preset(name.strip())
            self._refresh_preset_list()
            # 选中新创建的预设
            select_combo_by_id(self._preset_combo, preset.id)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"新建预设失败: {e}")

    def _on_import_preset(self) -> None:
        """导入 ST 预设。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择 ST 预设 JSON", "", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            preset, regex_scripts_data = self.preset_service.import_from_st_json(file_path)
            # 导入正则脚本到 preset 作用域
            regex_count = 0
            if regex_scripts_data and self.regex_service is not None:
                from novelforge.services.regex_service import _parse_regex_script_from_st
                for script_data in regex_scripts_data:
                    try:
                        script = _parse_regex_script_from_st(script_data)
                        if not script.id:
                            from novelforge.services.regex_service import _generate_regex_id
                            script.id = _generate_regex_id()
                        self.regex_service.add_script(
                            script, scope="preset", preset_id=preset.id
                        )
                        regex_count += 1
                    except Exception as e:
                        logger.warning("导入正则脚本失败: %s", e)
            self._refresh_preset_list()
            msg = f"已导入预设: {preset.name}\n提示数: {len(preset.prompts)}"
            if regex_count:
                msg += f"\n正则脚本: {regex_count} 条（已导入到 preset 作用域）"
            QMessageBox.information(self, "导入成功", msg)
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))

    def _on_export_preset(self) -> None:
        """导出当前预设。"""
        if not self._current_preset:
            QMessageBox.warning(self, "提示", "请先选择预设")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出预设",
            f"{self._current_preset.name}.json",
            "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            self.preset_service.export_to_st_json(
                self._current_preset, file_path
            )
            QMessageBox.information(self, "导出成功", f"已导出到: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_delete_preset(self) -> None:
        """删除当前预设。"""
        if not self._current_preset:
            return
        if self._current_preset.id == "default":
            QMessageBox.warning(self, "提示", "不允许删除默认预设")
            return

        reply = QMessageBox.question(
            self, "删除预设",
            f"确定删除预设「{self._current_preset.name}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self.preset_service.delete_preset(self._current_preset.id):
            self._current_preset = None
            self._refresh_preset_list()

    def _on_duplicate_preset(self) -> None:
        """复制当前预设。"""
        if not self._current_preset:
            return
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            self, "复制预设", "新预设名称:",
            text=f"{self._current_preset.name}_副本"
        )
        if not ok or not name.strip():
            return

        try:
            import uuid
            new_id = "preset_" + uuid.uuid4().hex[:12]
            new_preset = WritingPreset(
                id=new_id,
                name=name.strip(),
                prompts=[
                    Prompt(
                        identifier=p.identifier,
                        name=p.name,
                        role=p.role,
                        content=p.content,
                        system_prompt=p.system_prompt,
                        marker=p.marker,
                        position=p.position,
                        injection_position=p.injection_position,
                        injection_depth=p.injection_depth,
                        injection_order=p.injection_order,
                        forbid_overrides=p.forbid_overrides,
                        extension=dict(p.extension),
                        enabled=p.enabled,
                    )
                    for p in self._current_preset.prompts
                ],
                prompt_order=[
                    PromptOrderGroup(
                        character_id=GLOBAL_CHARACTER_ID,
                        order=[
                            PromptOrderEntry(
                                identifier=e.identifier, enabled=e.enabled
                            )
                            for e in self._current_preset.prompt_order[0].order
                        ],
                    )
                ],
                generation_params=dict(self._current_preset.generation_params),
            )
            self.preset_service.save_preset(new_preset)
            self._refresh_preset_list()
            select_combo_by_id(self._preset_combo, new_id)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"复制预设失败: {e}")

    # ===== 提示词操作 =====

    def _on_prompt_selected(self, row: int) -> None:
        """提示词被选中，加载到编辑器。"""
        if row < 0 or not self._current_preset:
            return

        item = self._prompt_list.item(row)
        if item is None:
            return

        identifier = item.data(Qt.ItemDataRole.UserRole)
        prompt_map = {p.identifier: p for p in self._current_preset.prompts}
        prompt = prompt_map.get(identifier)
        if prompt is None:
            return

        self._identifier_edit.setText(prompt.identifier)
        self._name_edit.setText(prompt.name)
        self._role_combo.setCurrentText(prompt.role)
        self._content_edit.setPlainText(prompt.content)
        self._system_prompt_check.setChecked(prompt.system_prompt)
        self._marker_edit.setText(prompt.marker or "")
        self._position_combo.setCurrentText(prompt.position)
        self._injection_position_combo.setCurrentIndex(prompt.injection_position)
        self._injection_depth_spin.setValue(prompt.injection_depth)
        self._injection_order_spin.setValue(prompt.injection_order)
        self._forbid_overrides_check.setChecked(prompt.forbid_overrides)

        import json
        self._extension_edit.setText(
            json.dumps(prompt.extension, ensure_ascii=False)
            if prompt.extension else ""
        )

        # marker 和 system_prompt 提示不可删除
        is_marker = bool(prompt.marker)
        is_system = prompt.system_prompt
        self._remove_prompt_btn.setEnabled(not is_marker and not is_system)

    def _on_prompts_reordered(self) -> None:
        """提示词列表拖拽排序后更新 prompt_order。"""
        if not self._current_preset:
            return

        new_order: list[tuple[str, bool]] = []
        for i in range(self._prompt_list.count()):
            item = self._prompt_list.item(i)
            if item is None:
                continue
            identifier = item.data(Qt.ItemDataRole.UserRole)
            enabled = item.data(Qt.ItemDataRole.UserRole + 1)
            new_order.append((identifier, bool(enabled)))

        self.preset_service.reorder_prompts(self._current_preset, new_order)
        self.preset_service.save_preset(self._current_preset)
        self.preset_changed.emit(self._current_preset.id)

    def _on_add_prompt(self) -> None:
        """添加新提示。"""
        if not self._current_preset:
            return
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "添加提示", "提示名称:")
        if not ok or not name.strip():
            return

        prompt = self.preset_service.add_prompt_to_preset(
            self._current_preset, name.strip()
        )
        self.preset_service.save_preset(self._current_preset)
        self._refresh_prompt_list()
        # 选中新添加的提示
        for i in range(self._prompt_list.count()):
            item = self._prompt_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == prompt.identifier:
                self._prompt_list.setCurrentRow(i)
                break

    def _on_remove_prompt(self) -> None:
        """删除当前选中的提示。"""
        if not self._current_preset:
            return
        row = self._prompt_list.currentRow()
        if row < 0:
            return

        item = self._prompt_list.item(row)
        if item is None:
            return
        identifier = item.data(Qt.ItemDataRole.UserRole)

        # 检查是否可删除
        prompt_map = {p.identifier: p for p in self._current_preset.prompts}
        prompt = prompt_map.get(identifier)
        if prompt and (prompt.marker or prompt.system_prompt):
            QMessageBox.warning(
                self, "提示",
                "marker 提示和系统提示不允许删除"
            )
            return

        reply = QMessageBox.question(
            self, "删除提示",
            f"确定删除提示「{prompt.name if prompt else identifier}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self.preset_service.remove_prompt_from_preset(
            self._current_preset, identifier
        ):
            self.preset_service.save_preset(self._current_preset)
            self._refresh_prompt_list()

    def _on_move_up(self) -> None:
        """上移选中提示。"""
        row = self._prompt_list.currentRow()
        if row <= 0:
            return
        # 交换当前项与上一项
        current_item = self._prompt_list.takeItem(row)
        self._prompt_list.insertItem(row - 1, current_item)
        self._prompt_list.setCurrentRow(row - 1)
        self._on_prompts_reordered()

    def _on_move_down(self) -> None:
        """下移选中提示。"""
        row = self._prompt_list.currentRow()
        if row < 0 or row >= self._prompt_list.count() - 1:
            return
        current_item = self._prompt_list.takeItem(row)
        self._prompt_list.insertItem(row + 1, current_item)
        self._prompt_list.setCurrentRow(row + 1)
        self._on_prompts_reordered()

    def _on_save_prompt(self) -> None:
        """保存提示编辑。"""
        if not self._current_preset:
            return

        identifier = self._identifier_edit.text()
        if not identifier:
            return

        # 查找并更新提示
        for prompt in self._current_preset.prompts:
            if prompt.identifier == identifier:
                prompt.name = self._name_edit.text()
                prompt.role = self._role_combo.currentText()
                prompt.content = self._content_edit.toPlainText()
                prompt.position = self._position_combo.currentText()
                prompt.injection_position = (
                    self._injection_position_combo.currentData()
                )
                prompt.injection_depth = self._injection_depth_spin.value()
                prompt.injection_order = self._injection_order_spin.value()
                prompt.forbid_overrides = self._forbid_overrides_check.isChecked()

                # 解析 extension JSON
                import json
                ext_text = self._extension_edit.text().strip()
                if ext_text:
                    try:
                        prompt.extension = json.loads(ext_text)
                    except json.JSONDecodeError:
                        QMessageBox.warning(
                            self, "提示", "扩展字段 JSON 格式错误，已忽略"
                        )
                else:
                    prompt.extension = {}
                break

        self.preset_service.save_preset(self._current_preset)
        self._refresh_prompt_list()
        # 保持选中
        for i in range(self._prompt_list.count()):
            item = self._prompt_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == identifier:
                self._prompt_list.setCurrentRow(i)
                break

    def _on_save_params(self) -> None:
        """保存生成参数。"""
        if not self._current_preset:
            return

        self._current_preset.generation_params["temperature"] = (
            self._temperature_spin.value() / 100.0
        )
        self._current_preset.generation_params["max_tokens"] = (
            self._max_tokens_spin.value()
        )
        self._current_preset.generation_params["max_context"] = (
            self._max_context_spin.value()
        )
        self._current_preset.generation_params["top_p"] = (
            self._top_p_spin.value() / 100.0
        )
        self._current_preset.generation_params["top_k"] = (
            self._top_k_spin.value()
        )
        self._current_preset.generation_params["reasoning_effort"] = (
            self._reasoning_effort_combo.currentText()
        )

        self.preset_service.save_preset(self._current_preset)
        QMessageBox.information(self, "成功", "生成参数已保存")

    # ===== 提示词与预设开关 =====

    def _on_prompt_check_changed(self, item: QListWidgetItem) -> None:
        """提示词复选框状态变化。"""
        if not self._current_preset:
            return
        identifier = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        # 防止 set_prompt_enabled 修改对象后刷新触发递归
        self._prompt_list.blockSignals(True)
        try:
            self.preset_service.set_prompt_enabled(
                self._current_preset, identifier, enabled
            )
            self.preset_service.save_preset(self._current_preset)
            # 更新 item 数据与标签
            item.setData(Qt.ItemDataRole.UserRole + 1, enabled)
            prompt_map = {p.identifier: p for p in self._current_preset.prompts}
            prompt = prompt_map.get(identifier)
            if prompt:
                label = prompt.name or prompt.identifier
                if prompt.marker:
                    label = f"[marker] {label}"
                elif prompt.system_prompt:
                    label = f"[系统] {label}"
                if not enabled:
                    label = f"[禁用] {label}"
                item.setText(label)
        finally:
            self._prompt_list.blockSignals(False)
        self.preset_changed.emit(self._current_preset.id)

    def _on_toggle_preset_enabled(self) -> None:
        """切换当前预设的启用/禁用状态。"""
        if not self._current_preset:
            return
        if self._current_preset.id == "default":
            QMessageBox.warning(self, "提示", "默认预设不允许禁用")
            return
        new_state = not self._current_preset.enabled
        if self.preset_service.set_preset_enabled(
            self._current_preset.id, new_state
        ):
            self._current_preset.enabled = new_state
            self._update_toggle_preset_btn_text()
            self.preset_changed.emit(self._current_preset.id)
        else:
            QMessageBox.warning(self, "错误", "切换预设状态失败")

    def _update_toggle_preset_btn_text(self) -> None:
        """更新启用/禁用按钮文本与状态。"""
        if not self._current_preset:
            self._toggle_preset_btn.setEnabled(False)
            self._toggle_preset_btn.setText("禁用预设")
            return
        if self._current_preset.id == "default":
            self._toggle_preset_btn.setEnabled(False)
            self._toggle_preset_btn.setText("默认预设（始终启用）")
            return
        self._toggle_preset_btn.setEnabled(True)
        if self._current_preset.enabled:
            self._toggle_preset_btn.setText("禁用预设")
        else:
            self._toggle_preset_btn.setText("启用预设")

    def _on_reset_default_preset(self) -> None:
        """将本地默认预设重置为内置最新版本。

        内置默认预设升级后，本地 ``~/.novelforge/presets/default.json``
        仍保留旧版本，需用户主动点击此按钮同步到最新版本。
        """
        reply = QMessageBox.question(
            self, "恢复默认预设",
            "将本地默认预设重置为内置最新版本。\n"
            "这会覆盖您对默认预设的所有自定义修改（如提示词内容、生成参数、"
            "开关状态等），且不可撤销。\n\n确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self._current_preset = self.preset_service.reset_default_preset()
            self._refresh_preset_list()
            self._refresh_prompt_list()
            self._refresh_params()
            self.preset_changed.emit("default")
            QMessageBox.information(
                self, "完成",
                f"默认预设已恢复为内置最新版本。\n提示数: {len(self._current_preset.prompts)}",
            )
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "错误", f"恢复默认预设失败: {e}")

    # ===== 公共接口 =====

    def get_current_preset_id(self) -> str | None:
        """获取当前选中的预设 ID。"""
        if self._current_preset:
            return self._current_preset.id
        return None

    def select_preset(self, preset_id: str) -> None:
        """选中指定预设。"""
        for i in range(self._preset_combo.count()):
            if self._preset_combo.itemData(i) == preset_id:
                self._preset_combo.setCurrentIndex(i)
                return
