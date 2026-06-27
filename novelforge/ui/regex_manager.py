"""正则管理器 UI。

提供正则脚本的可视化管理界面：
- 脚本列表（按作用域分组，显示执行顺序编号）
- 脚本编辑（名称、findRegex、replaceString、trimStrings、placement、depth、substituteRegex）
- 启用/禁用复选框
- 正则单独测试对话框（输入文本，显示匹配高亮和替换结果）
- 管理器窗口位置和尺寸持久化

执行顺序：GLOBAL(0) → PRESET(2) → SCOPED(1)
（对齐 ST ``SCRIPT_TYPES`` 对象插入序，非数值大小）

Signals:
    regex_changed(): 正则脚本变更
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
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

from novelforge.core.regex_engine import RegexEngine
from novelforge.models import (
    PLACEMENT_AI_OUTPUT,
    PLACEMENT_USER_INPUT,
    PLACEMENT_WORLD_INFO,
    RegexScript,
)
from novelforge.services.regex_service import (
    SCOPE_GLOBAL,
    SCOPE_PRESET,
    SCOPE_SCOPED,
    RegexService,
)
from novelforge.ui.persistent_dialog import PersistentDialog

logger = logging.getLogger(__name__)

# 窗口默认尺寸
DEFAULT_WINDOW_WIDTH = 1000
DEFAULT_WINDOW_HEIGHT = 650

# placement 选项
_PLACEMENT_OPTIONS: list[tuple[str, int]] = [
    ("USER_INPUT (1)", PLACEMENT_USER_INPUT),
    ("AI_OUTPUT (2)", PLACEMENT_AI_OUTPUT),
    ("WORLD_INFO (5)", PLACEMENT_WORLD_INFO),
]


class RegexTestDialog(QDialog):
    """正则测试对话框。

    输入测试文本，应用当前选中的单个正则脚本，
    显示匹配高亮和替换结果。
    """

    def __init__(self, script: RegexScript, parent: QWidget | None = None) -> None:
        """初始化测试对话框。

        Args:
            script: 待测试的正则脚本
            parent: 父控件
        """
        super().__init__(parent)
        self.script = script
        self.setWindowTitle(f"测试正则: {script.scriptName}")
        self.setMinimumSize(600, 500)

        self._setup_ui()
        self._do_test()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)

        # 脚本信息
        info_group = QGroupBox("脚本信息")
        info_layout = QFormLayout(info_group)
        info_layout.addRow("名称:", QLabel(self.script.scriptName))
        info_layout.addRow("findRegex:", QLabel(self.script.findRegex))
        info_layout.addRow("replaceString:", QLabel(self.script.replaceString))
        if self.script.trimStrings:
            info_layout.addRow("trimStrings:", QLabel(str(self.script.trimStrings)))
        layout.addWidget(info_group)

        # 输入文本
        input_group = QGroupBox("输入文本")
        input_layout = QVBoxLayout(input_group)
        self._input_edit = QPlainTextEdit()
        self._input_edit.setPlaceholderText("在此输入测试文本...")
        self._input_edit.setPlainText("测试文本示例")
        input_layout.addWidget(self._input_edit)

        self._test_btn = QPushButton("测试")
        input_layout.addWidget(self._test_btn)
        layout.addWidget(input_group)

        # 匹配高亮显示
        match_group = QGroupBox("匹配高亮（黄色背景）")
        match_layout = QVBoxLayout(match_group)
        self._match_display = QTextEdit()
        self._match_display.setReadOnly(True)
        match_layout.addWidget(self._match_display)
        layout.addWidget(match_group)

        # 替换结果
        result_group = QGroupBox("替换结果")
        result_layout = QVBoxLayout(result_group)
        self._result_display = QPlainTextEdit()
        self._result_display.setReadOnly(True)
        result_layout.addWidget(self._result_display)
        layout.addWidget(result_group)

        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._test_btn.clicked.connect(self._do_test)

    def _do_test(self) -> None:
        """执行测试。"""
        text = self._input_edit.toPlainText()
        engine = RegexEngine()
        result, matches = engine.apply_single_script(self.script, text)

        # 显示匹配高亮
        self._match_display.clear()
        cursor = self._match_display.textCursor()
        cursor.insertText(text)

        # 高亮匹配项
        highlight_format = QTextCharFormat()
        highlight_format.setBackground(QColor("#FFFF00"))

        for start, end in matches:
            cursor = self._match_display.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(highlight_format)

        # 显示替换结果
        self._result_display.setPlainText(result)

        # 如果脚本无效，显示错误
        from novelforge.core.regex_engine import CompiledRegexScript
        compiled = CompiledRegexScript(self.script)
        if not compiled.is_valid:
            self._result_display.setPlainText(
                f"[脚本错误] {compiled.error_message}\n\n原始文本:\n{text}"
            )


class RegexManager(PersistentDialog):
    """正则管理器对话框。

    提供正则脚本的增删改查、导入导出、测试功能。
    脚本按作用域分组显示，含执行顺序编号。

    Signals:
        regex_changed(): 正则脚本变更
    """

    regex_changed = Signal()

    def __init__(
        self,
        regex_service: RegexService,
        project_id: str = "",
        preset_id: str = "default",
        parent: QWidget | None = None,
    ) -> None:
        """初始化正则管理器。

        Args:
            regex_service: 正则服务实例
            project_id: 当前项目 ID（用于 scoped 脚本）
            preset_id: 当前预设 ID（用于 preset 脚本）
            parent: 父控件
        """
        super().__init__(parent)
        self._settings_key = "RegexManager"
        self.regex_service = regex_service
        self.project_id = project_id
        self.preset_id = preset_id
        self._current_script: RegexScript | None = None
        self._current_scope: str = SCOPE_GLOBAL

        self.setWindowTitle("正则管理器")
        self.setMinimumSize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)

        self._setup_ui()
        self._setup_connections()
        self._refresh_script_list()
        self._restore_window_state()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)

        # 顶部工具栏
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("作用域:"))

        self._scope_combo = QComboBox()
        self._scope_combo.addItem("全局 (GLOBAL)", SCOPE_GLOBAL)
        self._scope_combo.addItem("项目 (SCOPED)", SCOPE_SCOPED)
        self._scope_combo.addItem("预设 (PRESET)", SCOPE_PRESET)
        toolbar.addWidget(self._scope_combo)

        self._new_btn = QPushButton("新建")
        self._import_btn = QPushButton("导入 ST 正则")
        self._export_btn = QPushButton("导出")
        self._delete_btn = QPushButton("删除")
        toolbar.addWidget(self._new_btn)
        toolbar.addWidget(self._import_btn)
        toolbar.addWidget(self._export_btn)
        toolbar.addWidget(self._delete_btn)
        toolbar.addStretch()

        # 执行顺序说明
        order_label = QLabel("执行顺序: GLOBAL → PRESET → SCOPED")
        order_label.setObjectName("metaText")
        toolbar.addWidget(order_label)

        layout.addLayout(toolbar)

        # 主区域：左右分割
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：脚本列表
        left_group = QGroupBox("脚本列表")
        left_layout = QVBoxLayout(left_group)

        self._script_list = QListWidget()
        left_layout.addWidget(self._script_list)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self._test_btn = QPushButton("测试")
        self._move_up_btn = QPushButton("上移")
        self._move_down_btn = QPushButton("下移")
        btn_layout.addWidget(self._test_btn)
        btn_layout.addWidget(self._move_up_btn)
        btn_layout.addWidget(self._move_down_btn)
        left_layout.addLayout(btn_layout)

        splitter.addWidget(left_group)

        # 右侧：脚本编辑器
        right_group = QGroupBox("脚本编辑器")
        right_layout = QVBoxLayout(right_group)

        form = QFormLayout()

        # ID（只读）
        self._id_edit = QLineEdit()
        self._id_edit.setReadOnly(True)
        form.addRow("ID:", self._id_edit)

        # 名称
        self._name_edit = QLineEdit()
        form.addRow("名称:", self._name_edit)

        # findRegex
        self._find_regex_edit = QLineEdit()
        self._find_regex_edit.setPlaceholderText("/pattern/flags（如 /foo/gi）")
        form.addRow("findRegex:", self._find_regex_edit)

        # replaceString
        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText("支持 $1, $<name>, {{match}}")
        form.addRow("replaceString:", self._replace_edit)

        # trimStrings
        self._trim_edit = QLineEdit()
        self._trim_edit.setPlaceholderText('逗号分隔，如 "abc,def"')
        form.addRow("trimStrings:", self._trim_edit)

        # placement
        placement_layout = QHBoxLayout()
        self._placement_checks: dict[int, QCheckBox] = {}
        for label, value in _PLACEMENT_OPTIONS:
            check = QCheckBox(label)
            self._placement_checks[value] = check
            placement_layout.addWidget(check)
        placement_layout.addStretch()
        form.addRow("placement:", placement_layout)

        # minDepth / maxDepth
        depth_layout = QHBoxLayout()
        self._min_depth_spin = QSpinBox()
        self._min_depth_spin.setRange(0, 9999)
        self._min_depth_spin.setValue(0)
        depth_layout.addWidget(QLabel("min:"))
        depth_layout.addWidget(self._min_depth_spin)
        self._max_depth_spin = QSpinBox()
        self._max_depth_spin.setRange(0, 9999)
        self._max_depth_spin.setValue(0)
        depth_layout.addWidget(QLabel("max:"))
        depth_layout.addWidget(self._max_depth_spin)
        form.addRow("depth 范围:", depth_layout)

        # 复选框选项
        options_layout = QHBoxLayout()
        self._disabled_check = QCheckBox("禁用")
        self._markdown_only_check = QCheckBox("仅 Markdown")
        self._prompt_only_check = QCheckBox("仅提示词")
        self._run_on_edit_check = QCheckBox("编辑时运行")
        self._substitute_regex_check = QCheckBox("替换后宏替换")
        self._markup_safety_check = QCheckBox("标记安全")
        options_layout.addWidget(self._disabled_check)
        options_layout.addWidget(self._markdown_only_check)
        options_layout.addWidget(self._prompt_only_check)
        options_layout.addWidget(self._run_on_edit_check)
        options_layout.addWidget(self._substitute_regex_check)
        options_layout.addWidget(self._markup_safety_check)
        options_layout.addStretch()
        form.addRow("选项:", options_layout)

        right_layout.addLayout(form)

        # 保存按钮
        self._save_btn = QPushButton("保存脚本")
        right_layout.addWidget(self._save_btn)

        splitter.addWidget(right_group)
        splitter.setSizes([400, 600])

        layout.addWidget(splitter)

        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _setup_connections(self) -> None:
        """连接信号。"""
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        self._script_list.currentRowChanged.connect(self._on_script_selected)
        self._new_btn.clicked.connect(self._on_new_script)
        self._import_btn.clicked.connect(self._on_import_script)
        self._export_btn.clicked.connect(self._on_export_script)
        self._delete_btn.clicked.connect(self._on_delete_script)
        self._test_btn.clicked.connect(self._on_test_script)
        self._move_up_btn.clicked.connect(self._on_move_up)
        self._move_down_btn.clicked.connect(self._on_move_down)
        self._save_btn.clicked.connect(self._on_save_script)

    # ===== 作用域切换 =====

    def _on_scope_changed(self, index: int) -> None:
        """作用域切换。"""
        if index < 0:
            return
        self._current_scope = self._scope_combo.itemData(index)
        self._refresh_script_list()

    def _get_current_scope_params(self) -> dict[str, str]:
        """获取当前作用域的参数。"""
        return {
            "scope": self._current_scope,
            "project_id": self.project_id if self._current_scope == SCOPE_SCOPED else "",
            "preset_id": self.preset_id if self._current_scope == SCOPE_PRESET else "",
        }

    # ===== 脚本列表 =====

    def _refresh_script_list(self) -> None:
        """刷新脚本列表（含执行顺序编号）。"""
        self._script_list.clear()
        params = self._get_current_scope_params()

        try:
            scripts = self.regex_service.load_scripts(**params)
        except Exception as e:
            logger.error("加载正则脚本失败: %s", e)
            scripts = []

        # 显示脚本列表
        for i, script in enumerate(scripts, 1):
            label_parts = [f"#{i}", script.scriptName or script.id]
            if script.disabled:
                label_parts.append("[禁用]")
            if not script.findRegex:
                label_parts.append("[空]")
            label = " ".join(label_parts)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, script.id)
            self._script_list.addItem(item)

        # 同时显示其他作用域的脚本（灰色，仅显示顺序）
        self._append_other_scope_scripts()

        if self._script_list.count() > 0:
            self._script_list.setCurrentRow(0)

    def _append_other_scope_scripts(self) -> None:
        """追加显示其他作用域的脚本（用于展示完整执行顺序）。"""
        # 获取所有作用域的脚本（按执行顺序）
        ordered = self.regex_service.get_ordered_scripts(
            project_id=self.project_id,
            preset_id=self.preset_id,
            include_disabled=True,
        )

        # 添加分隔项
        if ordered and self._script_list.count() > 0:
            sep_item = QListWidgetItem("── 全局执行顺序 ──")
            sep_item.setFlags(Qt.ItemFlag.NoItemFlags)
            sep_item.setForeground(QColor("gray"))
            self._script_list.addItem(sep_item)

        scope_names = {
            SCOPE_GLOBAL: "GLOBAL",
            SCOPE_PRESET: "PRESET",
            SCOPE_SCOPED: "SCOPED",
        }
        for i, (script, scope) in enumerate(ordered, 1):
            scope_name = scope_names.get(scope, scope)
            label_parts = [
                f"#{i}", f"[{scope_name}]",
                script.scriptName or script.id,
            ]
            if script.disabled:
                label_parts.append("[禁用]")
            label = " ".join(label_parts)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, script.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, scope)
            # 其他作用域的脚本不可选中编辑
            if scope != self._current_scope:
                item.setForeground(QColor("gray"))
            self._script_list.addItem(item)

    def _on_script_selected(self, row: int) -> None:
        """脚本被选中，加载到编辑器。"""
        if row < 0:
            return
        item = self._script_list.item(row)
        if item is None:
            return

        # 检查是否为分隔项
        if item.flags() & Qt.ItemFlag.NoItemFlags:
            return

        script_id = item.data(Qt.ItemDataRole.UserRole)
        if not script_id:
            return

        # 从当前作用域加载脚本
        params = self._get_current_scope_params()
        scripts = self.regex_service.load_scripts(**params)
        for script in scripts:
            if script.id == script_id:
                self._current_script = script
                self._load_script_to_editor(script)
                return

    def _load_script_to_editor(self, script: RegexScript) -> None:
        """加载脚本到编辑器。"""
        self._id_edit.setText(script.id)
        self._name_edit.setText(script.scriptName)
        self._find_regex_edit.setText(script.findRegex)
        self._replace_edit.setText(script.replaceString)
        self._trim_edit.setText(",".join(script.trimStrings))

        # placement 复选框
        for value, check in self._placement_checks.items():
            check.setChecked(value in script.placement)

        # depth
        self._min_depth_spin.setValue(script.minDepth)
        self._max_depth_spin.setValue(script.maxDepth)

        # 选项
        self._disabled_check.setChecked(script.disabled)
        self._markdown_only_check.setChecked(script.markdownOnly)
        self._prompt_only_check.setChecked(script.promptOnly)
        self._run_on_edit_check.setChecked(script.runOnEdit)
        self._substitute_regex_check.setChecked(bool(script.substituteRegex))
        self._markup_safety_check.setChecked(script.markupSafety)

    # ===== 脚本操作 =====

    def _on_new_script(self) -> None:
        """新建脚本。"""
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "新建正则脚本", "脚本名称:")
        if not ok or not name.strip():
            return

        try:
            params = self._get_current_scope_params()
            script = self.regex_service.create_script(
                name=name.strip(),
                scope=params["scope"],
                project_id=params["project_id"],
                preset_id=params["preset_id"],
            )
            self._refresh_script_list()
            self.regex_changed.emit()
            # 选中新创建的脚本
            self._select_script_by_id(script.id)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"新建脚本失败: {e}")

    def _on_import_script(self) -> None:
        """导入 ST 正则。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择 ST 正则 JSON", "", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            params = self._get_current_scope_params()
            scripts = self.regex_service.import_from_st_json(
                file_path,
                scope=params["scope"],
                project_id=params["project_id"],
                preset_id=params["preset_id"],
            )
            self._refresh_script_list()
            self.regex_changed.emit()
            QMessageBox.information(
                self, "导入成功",
                f"已导入 {len(scripts)} 个正则脚本"
            )
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))

    def _on_export_script(self) -> None:
        """导出当前作用域的正则。"""
        params = self._get_current_scope_params()
        scripts = self.regex_service.load_scripts(**params)
        if not scripts:
            QMessageBox.warning(self, "提示", "当前作用域无脚本可导出")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出正则",
            f"regex_{params['scope']}.json",
            "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            self.regex_service.export_to_st_json(scripts, file_path)
            QMessageBox.information(self, "导出成功", f"已导出到: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_delete_script(self) -> None:
        """删除当前脚本。"""
        if not self._current_script:
            QMessageBox.warning(self, "提示", "请先选择脚本")
            return

        reply = QMessageBox.question(
            self, "删除脚本",
            f"确定删除脚本「{self._current_script.scriptName}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        params = self._get_current_scope_params()
        if self.regex_service.delete_script(
            self._current_script.id, **params
        ):
            self._current_script = None
            self._refresh_script_list()
            self.regex_changed.emit()

    def _on_test_script(self) -> None:
        """测试当前脚本。"""
        if not self._current_script:
            QMessageBox.warning(self, "提示", "请先选择脚本")
            return

        # 先保存当前编辑（不写入文件，仅用于测试）
        script = self._collect_script_from_editor(self._current_script.id)
        if script is None:
            return

        dialog = RegexTestDialog(script, self)
        dialog.exec()

    def _on_move_up(self) -> None:
        """上移脚本。"""
        row = self._script_list.currentRow()
        if row <= 0:
            return
        self._swap_scripts(row, row - 1)

    def _on_move_down(self) -> None:
        """下移脚本。"""
        row = self._script_list.currentRow()
        if row < 0 or row >= self._script_list.count() - 1:
            return
        self._swap_scripts(row, row + 1)

    def _swap_scripts(self, row1: int, row2: int) -> None:
        """交换两个脚本的位置。"""
        params = self._get_current_scope_params()
        scripts = self.regex_service.load_scripts(**params)
        if row1 >= len(scripts) or row2 >= len(scripts):
            return
        scripts[row1], scripts[row2] = scripts[row2], scripts[row1]
        self.regex_service.save_scripts(scripts, **params)
        self._refresh_script_list()
        self._script_list.setCurrentRow(row2)
        self.regex_changed.emit()

    def _on_save_script(self) -> None:
        """保存脚本编辑。"""
        if not self._current_script:
            QMessageBox.warning(self, "提示", "请先选择脚本")
            return

        script = self._collect_script_from_editor(self._current_script.id)
        if script is None:
            return

        params = self._get_current_scope_params()
        if self.regex_service.update_script(script, **params):
            self._current_script = script
            self._refresh_script_list()
            self._select_script_by_id(script.id)
            self.regex_changed.emit()
            QMessageBox.information(self, "成功", "脚本已保存")
        else:
            QMessageBox.warning(self, "错误", "保存失败：找不到对应脚本")

    def _collect_script_from_editor(self, script_id: str) -> RegexScript | None:
        """从编辑器收集脚本数据。

        Args:
            script_id: 脚本 ID

        Returns:
            RegexScript 对象，校验失败时返回 None
        """
        # 收集 placement
        placement: list[int] = []
        for value, check in self._placement_checks.items():
            if check.isChecked():
                placement.append(value)

        # 收集 trimStrings
        trim_text = self._trim_edit.text().strip()
        trim_strings = [s for s in trim_text.split(",") if s] if trim_text else []

        try:
            return RegexScript(
                id=script_id,
                scriptName=self._name_edit.text().strip(),
                findRegex=self._find_regex_edit.text(),
                replaceString=self._replace_edit.text(),
                trimStrings=trim_strings,
                placement=placement,
                disabled=self._disabled_check.isChecked(),
                markdownOnly=self._markdown_only_check.isChecked(),
                promptOnly=self._prompt_only_check.isChecked(),
                runOnEdit=self._run_on_edit_check.isChecked(),
                substituteRegex=self._substitute_regex_check.isChecked(),
                minDepth=self._min_depth_spin.value(),
                maxDepth=self._max_depth_spin.value(),
                markupSafety=self._markup_safety_check.isChecked(),
            )
        except Exception as e:
            QMessageBox.warning(self, "错误", f"脚本数据无效: {e}")
            return None

    def _select_script_by_id(self, script_id: str) -> None:
        """选中指定 ID 的脚本。"""
        for i in range(self._script_list.count()):
            item = self._script_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == script_id:
                self._script_list.setCurrentRow(i)
                return

    # ===== 公共接口 =====

    def set_project_id(self, project_id: str) -> None:
        """设置当前项目 ID。"""
        self.project_id = project_id
        self._refresh_script_list()

    def set_preset_id(self, preset_id: str) -> None:
        """设置当前预设 ID。"""
        self.preset_id = preset_id
        self._refresh_script_list()
