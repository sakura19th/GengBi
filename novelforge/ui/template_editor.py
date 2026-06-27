"""模板编辑器 UI。

提供 Jinja2 模板的可视化管理界面：
- 作用域选择（global/project/chapter）
- 变量列表显示与新建
- 模板编辑区（支持 Jinja2 语法）
- 渲染预览区（实时或点击测试渲染）
- 可用函数列表显示
- 管理器窗口位置和尺寸持久化
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novelforge.core.template_engine import WHITELIST_FUNCTION_NAMES, TemplateEngine
from novelforge.core.variable_store import (
    SCOPE_CACHE,
    SCOPE_CHAPTER,
    SCOPE_GLOBAL,
    SCOPE_PROJECT,
    VALID_SCOPES,
    VariableStore,
)
from novelforge.ui.persistent_dialog import PersistentDialog

logger = logging.getLogger(__name__)

# 窗口默认尺寸
DEFAULT_WINDOW_WIDTH = 1100
DEFAULT_WINDOW_HEIGHT = 700

# 作用域显示名映射
_SCOPE_DISPLAY_NAMES: dict[str, str] = {
    SCOPE_GLOBAL: "全局 (global)",
    SCOPE_PROJECT: "项目 (project)",
    SCOPE_CHAPTER: "章节 (chapter)",
    SCOPE_CACHE: "缓存 (cache，不持久化)",
}

# 可用函数说明
_FUNCTION_DESCRIPTIONS: dict[str, str] = {
    "getvar": "getvar(name, scope='chapter') - 读取变量",
    "setvar": "setvar(name, value, scope='chapter') - 设置变量",
    "hasvar": "hasvar(name, scope='chapter') - 判断变量是否存在",
    "delvar": "delvar(name, scope='chapter') - 删除变量",
    "get_chapter": "get_chapter(index) - 获取指定序号章节",
    "get_chapters": "get_chapters() - 获取所有章节列表",
    "get_current_chapter": "get_current_chapter() - 获取当前章节",
    "get_chapter_count": "get_chapter_count() - 获取章节总数",
    "get_book": "get_book() - 获取小说标题",
    "get_protagonist": "get_protagonist() - 获取主角姓名",
    "get_novel_profile": "get_novel_profile() - 获取小说档案",
    "get_writing_style": "get_writing_style() - 获取写作风格",
    "get_context_entries": "get_context_entries() - 获取上下文条目",
    "regex_apply": "regex_apply(text, placement=1) - 应用正则",
    "substitute_macros": "substitute_macros(text) - 宏替换",
    "now": "now(format='%Y-%m-%d %H:%M:%S') - 当前时间",
    "word_count": "word_count(text) - 计算字数",
    "truncate": "truncate(text, length=200) - 截断文本",
}


class TemplateEditor(PersistentDialog):
    """模板编辑器对话框。

    提供变量管理与模板编辑、预览功能。

    Signals:
        variables_changed(): 变量变更
    """

    variables_changed = Signal()

    def __init__(
        self,
        variable_store: VariableStore | None = None,
        template_engine: TemplateEngine | None = None,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """初始化模板编辑器。

        Args:
            variable_store: 变量存储（None 时内部创建）
            template_engine: 模板引擎（None 时内部创建）
            project_id: 当前项目 ID
            chapter_metadata: 当前章节元数据
            parent: 父控件
        """
        super().__init__(parent)
        self._settings_key = "TemplateEditor"
        self.variable_store = variable_store or VariableStore()
        self.template_engine = template_engine or TemplateEngine(
            variable_store=self.variable_store
        )
        self.project_id = project_id
        self.chapter_metadata = chapter_metadata or {}
        self._current_scope: str = SCOPE_GLOBAL

        self.setWindowTitle("模板编辑器")
        self.setMinimumSize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)

        self._setup_ui()
        self._setup_connections()
        self._refresh_variable_list()
        self._refresh_function_list()
        self._restore_window_state()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)

        # 顶部工具栏
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("作用域:"))

        self._scope_combo = QComboBox()
        for scope in [SCOPE_GLOBAL, SCOPE_PROJECT, SCOPE_CHAPTER, SCOPE_CACHE]:
            self._scope_combo.addItem(_SCOPE_DISPLAY_NAMES.get(scope, scope), scope)
        toolbar.addWidget(self._scope_combo)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 主区域：三栏分割
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左栏：变量列表
        left_group = QGroupBox("变量列表")
        left_layout = QVBoxLayout(left_group)

        self._variable_list = QListWidget()
        left_layout.addWidget(self._variable_list)

        # 变量操作按钮
        var_btn_layout = QHBoxLayout()
        self._add_var_btn = QPushButton("新建变量")
        self._edit_var_btn = QPushButton("编辑")
        self._delete_var_btn = QPushButton("删除")
        var_btn_layout.addWidget(self._add_var_btn)
        var_btn_layout.addWidget(self._edit_var_btn)
        var_btn_layout.addWidget(self._delete_var_btn)
        left_layout.addLayout(var_btn_layout)

        splitter.addWidget(left_group)

        # 中栏：模板编辑区
        center_group = QGroupBox("模板编辑区（支持 Jinja2 语法）")
        center_layout = QVBoxLayout(center_group)

        self._template_edit = QPlainTextEdit()
        self._template_edit.setPlaceholderText(
            "在此输入 Jinja2 模板...\n"
            "示例: {{ getvar('mood', scope='chapter') }}\n"
            "示例: {{ get_book() }} - 第 {{ get_current_chapter().index + 1 }} 章\n"
            "示例: {% set chapters = get_chapters() %}"
        )
        center_layout.addWidget(self._template_edit)

        # 模板操作按钮
        tpl_btn_layout = QHBoxLayout()
        self._render_btn = QPushButton("测试渲染")
        self._clear_btn = QPushButton("清空")
        tpl_btn_layout.addWidget(self._render_btn)
        tpl_btn_layout.addWidget(self._clear_btn)
        tpl_btn_layout.addStretch()
        center_layout.addLayout(tpl_btn_layout)

        splitter.addWidget(center_group)

        # 右栏：预览 + 函数列表
        right_group = QGroupBox("预览与函数")
        right_layout = QVBoxLayout(right_group)

        # 预览区
        right_layout.addWidget(QLabel("渲染预览:"))
        self._preview_display = QTextEdit()
        self._preview_display.setReadOnly(True)
        right_layout.addWidget(self._preview_display)

        # 函数列表
        right_layout.addWidget(QLabel("可用函数:"))
        self._function_list = QListWidget()
        self._function_list.setMaximumHeight(180)
        right_layout.addWidget(self._function_list)

        splitter.addWidget(right_group)

        splitter.setSizes([250, 450, 400])
        layout.addWidget(splitter)

        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _setup_connections(self) -> None:
        """连接信号。"""
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        self._add_var_btn.clicked.connect(self._on_add_variable)
        self._edit_var_btn.clicked.connect(self._on_edit_variable)
        self._delete_var_btn.clicked.connect(self._on_delete_variable)
        self._render_btn.clicked.connect(self._on_render_template)
        self._clear_btn.clicked.connect(self._template_edit.clear)
        self._function_list.itemDoubleClicked.connect(self._on_function_double_clicked)

    # ===== 作用域切换 =====

    def _on_scope_changed(self, index: int) -> None:
        """作用域切换。"""
        if index < 0:
            return
        self._current_scope = self._scope_combo.itemData(index)
        self._refresh_variable_list()

    # ===== 变量列表 =====

    def _refresh_variable_list(self) -> None:
        """刷新变量列表。"""
        self._variable_list.clear()
        try:
            variables = self.variable_store.list_vars(
                scope=self._current_scope,
                project_id=self.project_id,
                chapter_metadata=self.chapter_metadata,
            )
        except Exception as e:
            logger.error("加载变量失败: %s", e)
            variables = {}

        for name, value in variables.items():
            # 显示名称和值的摘要
            value_str = str(value)
            if len(value_str) > 50:
                value_str = value_str[:50] + "..."
            label = f"{name} = {value_str}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._variable_list.addItem(item)

    def _on_add_variable(self) -> None:
        """新建变量。"""
        name, ok = QInputDialog.getText(self, "新建变量", "变量名:")
        if not ok or not name.strip():
            return

        value, ok = QInputDialog.getText(self, "新建变量", "变量值:")
        if not ok:
            return

        try:
            self.variable_store.setvar(
                name=name.strip(),
                value=value,
                scope=self._current_scope,
                project_id=self.project_id,
                chapter_metadata=self.chapter_metadata,
            )
            self._refresh_variable_list()
            self.variables_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"新建变量失败: {e}")

    def _on_edit_variable(self) -> None:
        """编辑变量。"""
        item = self._variable_list.currentItem()
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if not name:
            return

        current_value = self.variable_store.getvar(
            name=name,
            scope=self._current_scope,
            project_id=self.project_id,
            chapter_metadata=self.chapter_metadata,
            default="",
        )

        value, ok = QInputDialog.getText(
            self, "编辑变量", f"变量值 ({name}):", text=str(current_value)
        )
        if not ok:
            return

        try:
            self.variable_store.setvar(
                name=name,
                value=value,
                scope=self._current_scope,
                project_id=self.project_id,
                chapter_metadata=self.chapter_metadata,
            )
            self._refresh_variable_list()
            self.variables_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"编辑变量失败: {e}")

    def _on_delete_variable(self) -> None:
        """删除变量。"""
        item = self._variable_list.currentItem()
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if not name:
            return

        reply = QMessageBox.question(
            self, "删除变量",
            f"确定删除变量「{name}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self.variable_store.delvar(
            name=name,
            scope=self._current_scope,
            project_id=self.project_id,
            chapter_metadata=self.chapter_metadata,
        ):
            self._refresh_variable_list()
            self.variables_changed.emit()

    # ===== 函数列表 =====

    def _refresh_function_list(self) -> None:
        """刷新可用函数列表。"""
        self._function_list.clear()
        for func_name in sorted(WHITELIST_FUNCTION_NAMES):
            desc = _FUNCTION_DESCRIPTIONS.get(func_name, func_name)
            item = QListWidgetItem(desc)
            item.setData(Qt.ItemDataRole.UserRole, func_name)
            self._function_list.addItem(item)

    def _on_function_double_clicked(self, item: QListWidgetItem) -> None:
        """双击函数列表项，插入到模板编辑区。"""
        func_name = item.data(Qt.ItemDataRole.UserRole)
        if func_name:
            # 插入函数调用模板
            snippet = f"{{{{ {func_name}() }}}}"
            self._template_edit.insertPlainText(snippet)

    # ===== 模板渲染 =====

    def _on_render_template(self) -> None:
        """测试渲染模板。"""
        template_str = self._template_edit.toPlainText()
        if not template_str.strip():
            self._preview_display.setPlainText("（模板为空）")
            return

        try:
            # 使用模板引擎渲染
            rendered, error = self.template_engine.render_template(
                template_str,
                context={},
            )
            if error:
                self._preview_display.setPlainText(
                    f"[渲染错误] {error}\n\n原始模板:\n{template_str}"
                )
            else:
                self._preview_display.setPlainText(rendered)
        except Exception as e:
            self._preview_display.setPlainText(
                f"[渲染异常] {e}\n\n原始模板:\n{template_str}"
            )

    # ===== 公共接口 =====

    def set_project_id(self, project_id: str) -> None:
        """设置当前项目 ID。"""
        self.project_id = project_id
        self._refresh_variable_list()

    def set_chapter_metadata(self, metadata: dict[str, Any]) -> None:
        """设置当前章节元数据。"""
        self.chapter_metadata = metadata
        self._refresh_variable_list()

    def get_template_text(self) -> str:
        """获取模板编辑区文本。"""
        return self._template_edit.toPlainText()

    def set_template_text(self, text: str) -> None:
        """设置模板编辑区文本。"""
        self._template_edit.setPlainText(text)
