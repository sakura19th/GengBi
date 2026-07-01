"""上下文提取预览面板。

显示上下文提取结果，支持：
- 提取中 loading 动画（QPropertyAnimation 旋转图标）与"提取中..."文本
- 取消提取按钮
- 提取完成后自动刷新预览
- 按分类分组显示（人物/地点/事件/风格/剧情状态，使用 QGroupBox）
- 每条可展开查看（QTextEdit 只读）
- 编辑按钮：弹出编辑对话框修改 content
- 禁用复选框：本次续写不注入
- 手动添加条目按钮
- 显示提取耗时和 token 消耗

Signals:
    extraction_started(): 提取开始
    extraction_finished(list): 提取完成（传 ContextEntry 列表）
    extraction_failed(str): 提取失败（传错误信息）
    extraction_cancelled(): 提取被取消
    entries_changed(list): 用户编辑/禁用/添加后通知（传当前 entries 列表）
    cancel_requested(): 用户点击取消提取按钮
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import (
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from novelforge.models import ContextEntry
from novelforge.ui.flow_layout import QFlowLayout
from novelforge.ui.helpers import parse_token_limit, set_label_state

logger = logging.getLogger(__name__)

# 分类显示名称映射
CATEGORY_DISPLAY_NAMES: dict[str, str] = {
    "characters": "人物",
    "protagonist_behavior": "主角行为",
    "locations": "地点",
    "events": "事件",
    "style": "风格",
    "plot_state": "剧情状态",
}

# 分类显示顺序
CATEGORY_ORDER: list[str] = [
    "characters",
    "protagonist_behavior",
    "locations",
    "events",
    "style",
    "plot_state",
]

# loading 动画旋转间隔（毫秒）
LOADING_ANIMATION_INTERVAL_MS = 100

# loading 旋转字符序列
LOADING_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class _EntryEditorDialog(QDialog):
    """ContextEntry 编辑对话框。

    允许用户编辑 content、comment、position、depth、role 等字段。
    """

    def __init__(self, entry: ContextEntry, parent: QWidget | None = None) -> None:
        """初始化编辑对话框。

        Args:
            entry: 待编辑的 ContextEntry（不会被修改，编辑后返回新对象）
            parent: 父控件
        """
        super().__init__(parent)
        self.setWindowTitle(f"编辑条目 - {entry.uid}")
        self.setMinimumWidth(500)

        self._entry = entry
        self._setup_ui()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)

        # 表单
        form = QFormLayout()

        self._uid_edit = QLineEdit(self._entry.uid)
        self._uid_edit.setReadOnly(True)
        form.addRow("UID:", self._uid_edit)

        self._category_edit = QLineEdit(self._entry.category)
        self._category_edit.setReadOnly(True)
        form.addRow("分类:", self._category_edit)

        self._comment_edit = QLineEdit(self._entry.comment)
        form.addRow("备注:", self._comment_edit)

        self._content_edit = QPlainTextEdit()
        self._content_edit.setPlainText(self._entry.content)
        self._content_edit.setMinimumHeight(150)
        form.addRow("内容:", self._content_edit)

        self._order_spin = QSpinBox()
        self._order_spin.setRange(0, 9999)
        self._order_spin.setValue(self._entry.order)
        form.addRow("排序权重:", self._order_spin)

        self._position_edit = QLineEdit(self._entry.position)
        form.addRow("注入位置:", self._position_edit)

        self._depth_spin = QSpinBox()
        self._depth_spin.setRange(0, 99)
        self._depth_spin.setValue(self._entry.depth)
        form.addRow("深度:", self._depth_spin)

        self._role_edit = QLineEdit(self._entry.role)
        form.addRow("角色:", self._role_edit)

        layout.addLayout(form)

        # 按钮
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_entry(self) -> ContextEntry:
        """获取编辑后的 ContextEntry。"""
        return ContextEntry(
            uid=self._entry.uid,
            category=self._entry.category,
            key=list(self._entry.key),
            comment=self._comment_edit.text(),
            content=self._content_edit.toPlainText(),
            order=self._order_spin.value(),
            position=self._position_edit.text() or self._entry.position,
            depth=self._depth_spin.value(),
            role=self._role_edit.text() or self._entry.role,
            source_chapter_range=self._entry.source_chapter_range,
            extracted_at=self._entry.extracted_at,
            raw_st_fields=dict(self._entry.raw_st_fields),
        )


class _AddEntryDialog(QDialog):
    """手动添加 ContextEntry 对话框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化添加对话框。"""
        super().__init__(parent)
        self.setWindowTitle("添加上下文条目")
        self.setMinimumWidth(500)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        from PySide6.QtWidgets import QComboBox

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._uid_edit = QLineEdit()
        self._uid_edit.setPlaceholderText("留空将自动生成")
        form.addRow("UID:", self._uid_edit)

        self._category_combo = QComboBox()
        for cat in CATEGORY_ORDER:
            self._category_combo.addItem(CATEGORY_DISPLAY_NAMES[cat], cat)
        form.addRow("分类:", self._category_combo)

        self._comment_edit = QLineEdit()
        form.addRow("备注:", self._comment_edit)

        self._content_edit = QPlainTextEdit()
        self._content_edit.setMinimumHeight(150)
        form.addRow("内容:", self._content_edit)

        self._order_spin = QSpinBox()
        self._order_spin.setRange(0, 9999)
        self._order_spin.setValue(100)
        form.addRow("排序权重:", self._order_spin)

        self._position_combo = QComboBox()
        self._position_combo.addItem("before (worldInfoBefore)", "before")
        self._position_combo.addItem("after (worldInfoAfter)", "after")
        self._position_combo.addItem("at_depth (按深度注入)", "at_depth")
        form.addRow("注入位置:", self._position_combo)

        self._depth_spin = QSpinBox()
        self._depth_spin.setRange(0, 99)
        self._depth_spin.setValue(4)
        form.addRow("深度:", self._depth_spin)

        self._role_combo = QComboBox()
        self._role_combo.addItem("system", "system")
        self._role_combo.addItem("user", "user")
        self._role_combo.addItem("assistant", "assistant")
        form.addRow("角色:", self._role_combo)

        layout.addLayout(form)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_entry(self) -> ContextEntry:
        """获取用户输入的 ContextEntry。"""
        from novelforge.utils.ids import generate_id

        uid = self._uid_edit.text().strip() or generate_id("ctx_")
        return ContextEntry(
            uid=uid,
            category=self._category_combo.currentData(),
            comment=self._comment_edit.text(),
            content=self._content_edit.toPlainText(),
            order=self._order_spin.value(),
            position=self._position_combo.currentData(),
            depth=self._depth_spin.value(),
            role=self._role_combo.currentData(),
            source_chapter_range=None,
            extracted_at=None,
        )


class ContextPreviewPanel(QWidget):
    """上下文提取预览面板。

    显示提取结果，支持编辑、禁用、添加条目，并发出提取相关信号。

    Signals:
        extraction_started(): 提取开始
        extraction_finished(list): 提取完成（传 ContextEntry 列表）
        extraction_failed(str): 提取失败
        extraction_cancelled(): 提取被取消
        entries_changed(list): 用户编辑/禁用/添加后通知
        cancel_requested(): 用户点击取消提取按钮
    """

    extraction_started = Signal()
    extraction_finished = Signal(list)
    extraction_failed = Signal(str)
    extraction_cancelled = Signal()
    entries_changed = Signal(list)
    cancel_requested = Signal()
    extract_requested = Signal(dict)
    view_extract_prompt_requested = Signal()
    extract_ontology_requested = Signal()
    view_ontology_requested = Signal()
    extract_protagonist_requested = Signal()
    view_protagonist_requested = Signal()
    add_custom_rule_requested = Signal()
    view_custom_rules_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化预览面板。"""
        super().__init__(parent)
        self._entries: list[ContextEntry] = []
        self._disabled_uids: set[str] = set()
        self._is_extracting = False
        self._loading_frame_index = 0

        self._setup_ui()
        self._setup_loading_animation()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ===== 标题与状态栏 =====
        header_layout = QHBoxLayout()

        self._title_label = QLabel("上下文提取预览")
        self._title_label.setObjectName("panelTitle")
        header_layout.addWidget(self._title_label)

        header_layout.addStretch()

        self._status_label = QLabel("就绪")
        self._status_label.setObjectName("textSecondary")
        header_layout.addWidget(self._status_label)

        layout.addLayout(header_layout)

        # ===== loading 与取消按钮 =====
        loading_layout = QHBoxLayout()

        self._loading_label = QLabel("")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setVisible(False)
        loading_layout.addWidget(self._loading_label)

        self._loading_text = QLabel("")
        self._loading_text.setObjectName("loadingText")
        self._loading_text.setVisible(False)
        loading_layout.addWidget(self._loading_text)

        loading_layout.addStretch()

        self._cancel_btn = QPushButton("取消提取")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        loading_layout.addWidget(self._cancel_btn)

        layout.addLayout(loading_layout)

        # ===== 流式输出查看区（提取时显示原始 LLM 输出）=====
        self._stream_group = QGroupBox("流式输出（实时）")
        self._stream_group.setCheckable(True)
        self._stream_group.setChecked(False)
        self._stream_group.setVisible(False)
        stream_layout = QVBoxLayout(self._stream_group)

        self._stream_view = QPlainTextEdit()
        self._stream_view.setReadOnly(True)
        self._stream_view.setPlaceholderText("等待流式输出...")
        self._stream_view.setMaximumHeight(200)
        self._stream_view.setObjectName("streamView")
        stream_layout.addWidget(self._stream_view)

        layout.addWidget(self._stream_group)

        # ===== 元数据信息（耗时与 token） =====
        self._meta_label = QLabel("尚未提取")
        self._meta_label.setObjectName("metaText")
        self._meta_label.setWordWrap(True)
        layout.addWidget(self._meta_label)

        # ===== 提取操作行（流式布局，窄屏自动换行） =====
        extract_row = QFlowLayout()
        extract_row.setSpacing(4)

        self._extract_btn = QPushButton("提取上下文")
        self._extract_btn.setObjectName("primaryBtn")
        self._extract_btn.clicked.connect(self._on_extract_clicked)
        extract_row.addWidget(self._extract_btn)

        self._extract_ontology_btn = QPushButton("提取世界观底层")
        self._extract_ontology_btn.setObjectName("primaryBtn")
        self._extract_ontology_btn.setToolTip(
            "全文拆分分析提取 7 维度世界观元描述，固化到项目并绑定世界书"
        )
        self._extract_ontology_btn.clicked.connect(self._on_extract_ontology_clicked)
        extract_row.addWidget(self._extract_ontology_btn)

        self._view_ontology_btn = QPushButton("查看世界观底层")
        self._view_ontology_btn.setObjectName("secondaryBtn")
        self._view_ontology_btn.setToolTip(
            "查看已提取的世界观底层 7 维度元描述"
        )
        self._view_ontology_btn.clicked.connect(self._on_view_ontology_clicked)
        extract_row.addWidget(self._view_ontology_btn)

        self._extract_protagonist_btn = QPushButton("提取主角形象")
        self._extract_protagonist_btn.setObjectName("primaryBtn")
        self._extract_protagonist_btn.setToolTip(
            "全文拆分分析提取 8 维度主角心理学档案，缓存到当前章节"
        )
        self._extract_protagonist_btn.clicked.connect(
            self._on_extract_protagonist_clicked
        )
        extract_row.addWidget(self._extract_protagonist_btn)

        self._view_protagonist_btn = QPushButton("查看主角形象")
        self._view_protagonist_btn.setObjectName("secondaryBtn")
        self._view_protagonist_btn.setToolTip(
            "查看当前章节已提取的主角形象 8 维度档案"
        )
        self._view_protagonist_btn.clicked.connect(
            self._on_view_protagonist_clicked
        )
        extract_row.addWidget(self._view_protagonist_btn)

        self._add_custom_rule_btn = QPushButton("新增自定义设定")
        self._add_custom_rule_btn.setObjectName("primaryBtn")
        self._add_custom_rule_btn.setToolTip(
            "输入自定义设定，AI 结合世界观底层结构化为审计必查项（一票否决）"
        )
        self._add_custom_rule_btn.clicked.connect(self._on_add_custom_rule_clicked)
        extract_row.addWidget(self._add_custom_rule_btn)

        self._view_custom_rules_btn = QPushButton("查看自定义设定")
        self._view_custom_rules_btn.setObjectName("secondaryBtn")
        self._view_custom_rules_btn.setToolTip(
            "查看已新增的自定义设定/审计必查项列表，可删除"
        )
        self._view_custom_rules_btn.clicked.connect(self._on_view_custom_rules_clicked)
        extract_row.addWidget(self._view_custom_rules_btn)

        extract_row.addWidget(QLabel("前文:"))

        self._lookback_combo = QComboBox()
        self._lookback_combo.addItems([
            "全部前文", "最近 3 章", "最近 5 章", "最近 10 章", "最近 20 章",
        ])
        self._lookback_combo.setCurrentText("全部前文")
        self._lookback_combo.setMinimumWidth(100)
        extract_row.addWidget(self._lookback_combo)

        extract_row.addWidget(QLabel("Token:"))

        self._token_limit_combo = QComboBox()
        self._token_limit_combo.addItems(["不限制", "50k", "100k", "250k", "500k"])
        self._token_limit_combo.setCurrentText("不限制")
        self._token_limit_combo.setMinimumWidth(100)
        self._token_limit_combo.setToolTip(
            "选中章节超出 token 限制时，自动按章节拆分成多次请求"
        )
        extract_row.addWidget(self._token_limit_combo)

        layout.addLayout(extract_row)

        # ===== 操作按钮（流式布局，窄屏自动换行） =====
        btn_layout = QFlowLayout()
        btn_layout.setSpacing(4)

        self._add_btn = QPushButton("添加条目")
        self._add_btn.clicked.connect(self._on_add_clicked)
        btn_layout.addWidget(self._add_btn)

        self._clear_btn = QPushButton("清空")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        btn_layout.addWidget(self._clear_btn)

        self._view_prompt_btn = QPushButton("查看提示词")
        self._view_prompt_btn.clicked.connect(self._on_view_prompt_clicked)
        btn_layout.addWidget(self._view_prompt_btn)

        layout.addLayout(btn_layout)

        # ===== 分组显示区（滚动） =====
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)

        self._entries_container = QWidget()
        self._entries_layout = QVBoxLayout(self._entries_container)
        self._entries_layout.setContentsMargins(0, 0, 0, 0)
        self._entries_layout.setSpacing(4)
        self._entries_layout.addStretch()

        self._scroll_area.setWidget(self._entries_container)
        layout.addWidget(self._scroll_area, stretch=1)

        # 分类 QGroupBox 引用（按 category 索引）
        self._category_groups: dict[str, QGroupBox] = {}
        self._category_layouts: dict[str, QVBoxLayout] = {}
        self._category_visible_flags: dict[str, bool] = {}
        self._build_category_groups()

    def _build_category_groups(self) -> None:
        """构建分类 QGroupBox（初始隐藏，有条目时显示）。"""
        # 先移除 stretch
        stretch_item = self._entries_layout.takeAt(self._entries_layout.count() - 1)

        for category in CATEGORY_ORDER:
            group = QGroupBox(CATEGORY_DISPLAY_NAMES[category])
            group.setVisible(False)
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(4, 4, 4, 4)
            group_layout.setSpacing(2)

            self._entries_layout.addWidget(group)
            self._category_groups[category] = group
            self._category_layouts[category] = group_layout
            self._category_visible_flags[category] = False

        # 重新添加 stretch
        if stretch_item is not None:
            self._entries_layout.addStretch()

    def _setup_loading_animation(self) -> None:
        """设置 loading 动画定时器。"""
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(LOADING_ANIMATION_INTERVAL_MS)
        self._loading_timer.timeout.connect(self._update_loading_frame)

    def _update_loading_frame(self) -> None:
        """更新 loading 动画帧。"""
        self._loading_label.setText(LOADING_FRAMES[self._loading_frame_index])
        self._loading_frame_index = (self._loading_frame_index + 1) % len(LOADING_FRAMES)

    def _set_label_state(self, label: QLabel, text: str, state: str) -> None:
        """更新标签文本与状态色（对象名驱动，由全局 QSS 接管）。

        Args:
            label: 目标标签
            text: 文本
            state: 状态对象名（textSecondary/textInfo/textSuccess/textDanger/textWarning/metaText）
        """
        set_label_state(label, text, state)

    # ===== 公开接口 =====

    def start_extraction(self) -> None:
        """开始提取：显示 loading 动画，禁用按钮。"""
        self._is_extracting = True
        self._loading_frame_index = 0
        self._loading_label.setText(LOADING_FRAMES[0])
        self._loading_label.setVisible(True)
        self._loading_text.setText("提取中...")
        self._loading_text.setVisible(True)
        self._cancel_btn.setEnabled(True)
        self._add_btn.setEnabled(False)
        self._clear_btn.setEnabled(False)
        self._set_label_state(self._status_label, "提取中", "textInfo")
        self._loading_timer.start()
        # 显示流式输出查看区并清空内容
        self._stream_view.clear()
        self._stream_view.setPlaceholderText("等待流式输出...")
        self._stream_group.setVisible(True)
        self._stream_group.setChecked(True)
        self._stream_group.setTitle("流式输出（实时接收中...）")
        self.extraction_started.emit()

    def finish_extraction(
        self,
        entries: list[ContextEntry],
        elapsed_seconds: float = 0.0,
        token_usage: dict[str, Any] | None = None,
        from_cache: bool = False,
        batch_count: int = 1,
    ) -> None:
        """完成提取：停止 loading，刷新预览。

        Args:
            entries: 提取的 ContextEntry 列表
            elapsed_seconds: 提取耗时
            token_usage: token 消耗信息
            from_cache: 是否命中缓存
            batch_count: 拆分批次数（1=未拆分）
        """
        self._is_extracting = False
        self._loading_timer.stop()
        self._loading_label.setVisible(False)
        self._loading_text.setVisible(False)
        self._cancel_btn.setEnabled(False)
        self._add_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._set_label_state(self._status_label, "提取完成", "textSuccess")
        # 更新流式输出查看区标题
        self._stream_group.setTitle("流式输出（接收完成）")

        # 更新元数据
        meta_parts: list[str] = []
        meta_parts.append(f"条目数: {len(entries)}")
        if elapsed_seconds > 0:
            meta_parts.append(f"耗时: {elapsed_seconds:.2f}s")
        if from_cache:
            meta_parts.append("来源: 缓存")
        if batch_count > 1:
            meta_parts.append(f"批次: {batch_count}")
        if token_usage:
            prompt_tokens = token_usage.get("prompt_tokens", 0)
            completion_tokens = token_usage.get("completion_tokens", 0)
            total_tokens = token_usage.get("total_tokens", 0)
            if total_tokens:
                meta_parts.append(
                    f"Token: {total_tokens} (输入 {prompt_tokens} + 输出 {completion_tokens})"
                )
        self._set_label_state(self._meta_label, " | ".join(meta_parts), "metaText")

        # 更新条目显示
        self._entries = list(entries)
        self._disabled_uids.clear()
        self._refresh_entries_display()
        self.extraction_finished.emit(list(self._entries))

    def fail_extraction(self, error: str) -> None:
        """提取失败：停止 loading，显示错误。

        Args:
            error: 错误信息
        """
        self._is_extracting = False
        self._loading_timer.stop()
        self._loading_label.setVisible(False)
        self._loading_text.setVisible(False)
        self._cancel_btn.setEnabled(False)
        self._add_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._set_label_state(self._status_label, "提取失败", "textDanger")
        self._set_label_state(self._meta_label, f"错误: {error}", "textDanger")
        # 更新流式输出查看区标题
        self._stream_group.setTitle("流式输出（已中断）")
        self.extraction_failed.emit(error)

    def cancel_extraction(self) -> None:
        """提取被取消：停止 loading，恢复状态。"""
        self._is_extracting = False
        self._loading_timer.stop()
        self._loading_label.setVisible(False)
        self._loading_text.setVisible(False)
        self._cancel_btn.setEnabled(False)
        self._add_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._set_label_state(self._status_label, "已取消", "textWarning")
        self._set_label_state(self._meta_label, "提取已取消", "textWarning")
        # 更新流式输出查看区标题
        self._stream_group.setTitle("流式输出（已中断）")
        self.extraction_cancelled.emit()

    def get_entries(self) -> list[ContextEntry]:
        """获取当前所有启用的条目（排除被禁用的）。

        Returns:
            启用的 ContextEntry 列表
        """
        return [e for e in self._entries if e.uid not in self._disabled_uids]

    def get_all_entries(self) -> list[ContextEntry]:
        """获取所有条目（含禁用的）。

        Returns:
            所有 ContextEntry 列表
        """
        return list(self._entries)

    def set_entries(self, entries: list[ContextEntry]) -> None:
        """设置条目列表（外部设置，如导入世界书后）。

        Args:
            entries: ContextEntry 列表
        """
        self._entries = list(entries)
        self._disabled_uids.clear()
        self._refresh_entries_display()
        self.entries_changed.emit(list(self._entries))

    # ===== 内部方法 =====

    def _refresh_entries_display(self) -> None:
        """刷新条目显示（按分类分组）。"""
        # 清空各分类组的内容
        for category, group_layout in self._category_layouts.items():
            # 移除所有子项（保留 stretch）
            while group_layout.count() > 0:
                item = group_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self._category_groups[category].setVisible(False)
            self._category_visible_flags[category] = False

        # 按分类分组
        grouped: dict[str, list[ContextEntry]] = {cat: [] for cat in CATEGORY_ORDER}
        for entry in self._entries:
            if entry.category in grouped:
                grouped[entry.category].append(entry)
            else:
                # 未知分类归入 plot_state
                grouped["plot_state"].append(entry)

        # 按分类填充
        for category in CATEGORY_ORDER:
            entries = grouped[category]
            if not entries:
                continue
            # 按 order 升序排序
            entries.sort(key=lambda e: e.order)
            group = self._category_groups[category]
            group_layout = self._category_layouts[category]
            for entry in entries:
                widget = self._build_entry_widget(entry)
                group_layout.addWidget(widget)
            group.setVisible(True)
            self._category_visible_flags[category] = True
            # 更新标题（含条目数）
            group.setTitle(f"{CATEGORY_DISPLAY_NAMES[category]} ({len(entries)})")

    def _build_entry_widget(self, entry: ContextEntry) -> QWidget:
        """构建单个条目的显示控件。

        Args:
            entry: ContextEntry 对象

        Returns:
            QWidget 控件
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # 第一行：UID + 备注 + 禁用复选框 + 编辑按钮
        top_layout = QHBoxLayout()

        # 禁用复选框
        disable_check = QCheckBox()
        disable_check.setChecked(entry.uid not in self._disabled_uids)
        disable_check.setToolTip("勾选启用，取消勾选本次续写不注入")
        disable_check.stateChanged.connect(
            lambda state, uid=entry.uid: self._on_disable_toggled(uid, state)
        )
        top_layout.addWidget(disable_check)

        # UID 与备注
        info_text = f"<b>{entry.uid}</b>"
        if entry.comment:
            info_text += f" - {entry.comment}"
        if entry.key:
            info_text += f" <i>[{', '.join(entry.key)}]</i>"
        info_label = QLabel(info_text)
        info_label.setTextFormat(Qt.TextFormat.RichText)
        info_label.setWordWrap(True)
        top_layout.addWidget(info_label, stretch=1)

        # 编辑按钮
        edit_btn = QPushButton("编辑")
        edit_btn.setFixedWidth(60)
        edit_btn.clicked.connect(lambda _, e=entry: self._on_edit_clicked(e))
        top_layout.addWidget(edit_btn)

        # 删除按钮
        del_btn = QPushButton("删除")
        del_btn.setFixedWidth(60)
        del_btn.clicked.connect(lambda _, e=entry: self._on_delete_clicked(e))
        top_layout.addWidget(del_btn)

        layout.addLayout(top_layout)

        # 第二行：内容（可折叠）
        content_label = QLabel(entry.content.replace("\n", "<br>"))
        content_label.setTextFormat(Qt.TextFormat.RichText)
        content_label.setWordWrap(True)
        content_label.setObjectName("entryContent")
        layout.addWidget(content_label)

        # 元数据
        meta_parts: list[str] = []
        meta_parts.append(f"position={entry.position}")
        if entry.position == "at_depth":
            meta_parts.append(f"depth={entry.depth}")
        meta_parts.append(f"order={entry.order}")
        meta_parts.append(f"role={entry.role}")
        if entry.source_chapter_range is not None:
            meta_parts.append(
                f"source=第{entry.source_chapter_range[0]}-{entry.source_chapter_range[1]}章"
            )
        else:
            meta_parts.append("source=导入")
        meta_label = QLabel(" | ".join(meta_parts))
        meta_label.setObjectName("entryMeta")
        layout.addWidget(meta_label)

        return widget

    def _on_disable_toggled(self, uid: str, state: int) -> None:
        """禁用复选框状态变更。

        Args:
            uid: 条目 UID
            state: 复选框状态（0=未勾选=禁用，2=勾选=启用）
        """
        if state == 0:
            self._disabled_uids.add(uid)
        else:
            self._disabled_uids.discard(uid)
        self.entries_changed.emit(self.get_entries())

    def _on_edit_clicked(self, entry: ContextEntry) -> None:
        """编辑按钮点击。

        Args:
            entry: 待编辑的条目
        """
        dialog = _EntryEditorDialog(entry, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_entry = dialog.get_entry()
            # 替换列表中的条目
            for i, e in enumerate(self._entries):
                if e.uid == entry.uid:
                    self._entries[i] = new_entry
                    break
            self._refresh_entries_display()
            self.entries_changed.emit(self.get_entries())

    def _on_delete_clicked(self, entry: ContextEntry) -> None:
        """删除按钮点击。

        Args:
            entry: 待删除的条目
        """
        self._entries = [e for e in self._entries if e.uid != entry.uid]
        self._disabled_uids.discard(entry.uid)
        self._refresh_entries_display()
        self.entries_changed.emit(self.get_entries())

    def _on_add_clicked(self) -> None:
        """添加条目按钮点击。"""
        dialog = _AddEntryDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_entry = dialog.get_entry()
            self._entries.append(new_entry)
            self._refresh_entries_display()
            self.entries_changed.emit(self.get_entries())

    def _on_clear_clicked(self) -> None:
        """清空按钮点击。"""
        self._entries.clear()
        self._disabled_uids.clear()
        self._refresh_entries_display()
        self.entries_changed.emit([])

    def _on_cancel_clicked(self) -> None:
        """取消提取按钮点击。"""
        self.cancel_requested.emit()

    def _on_extract_clicked(self) -> None:
        """提取上下文按钮点击。"""
        config = self.get_lookback_config()
        self.extract_requested.emit(config)

    def _on_extract_ontology_clicked(self) -> None:
        """提取世界观底层按钮点击，发射信号给 MainWindow。"""
        self.extract_ontology_requested.emit()

    def _on_view_ontology_clicked(self) -> None:
        """查看世界观底层按钮点击，发射信号给 MainWindow。"""
        self.view_ontology_requested.emit()

    def _on_extract_protagonist_clicked(self) -> None:
        """提取主角形象按钮点击，发射信号给 MainWindow。"""
        self.extract_protagonist_requested.emit()

    def _on_view_protagonist_clicked(self) -> None:
        """查看主角形象按钮点击，发射信号给 MainWindow。"""
        self.view_protagonist_requested.emit()

    def _on_add_custom_rule_clicked(self) -> None:
        """新增自定义设定按钮点击，发射信号给 MainWindow。"""
        self.add_custom_rule_requested.emit()

    def _on_view_custom_rules_clicked(self) -> None:
        """查看自定义设定按钮点击，发射信号给 MainWindow。"""
        self.view_custom_rules_requested.emit()

    def _on_view_prompt_clicked(self) -> None:
        """查看提示词按钮点击。"""
        self.view_extract_prompt_requested.emit()

    def get_lookback_config(self) -> dict:
        """获取前文章节配置与 token 限制。

        Returns:
            含 ``lookback`` 和 ``token_limit`` 键的字典：
            - lookback: 0=全部前文，正整数=最近 N 章
            - token_limit: 0=不限制，50000/100000/250000/500000
        """
        text = self._lookback_combo.currentText()
        if text == "全部前文":
            lookback = 0
        else:
            import re
            m = re.search(r"(\d+)", text)
            lookback = int(m.group(1)) if m else 5

        token_limit = parse_token_limit(self._token_limit_combo.currentText())

        return {"lookback": lookback, "token_limit": token_limit}

    def update_extraction_progress(self, text: str) -> None:
        """更新提取进度（流式 chunk 到达时调用）。

        Args:
            text: 新接收的 chunk 文本
        """
        if not self._is_extracting:
            return
        # 追加原始 chunk 到流式查看区
        self._stream_view.insertPlainText(text)
        # 自动滚动到底部
        cursor = self._stream_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._stream_view.setTextCursor(cursor)
        # 累积字符数显示进度
        current = self._loading_text.text()
        if "提取中" in current:
            # 解析当前字符数
            import re
            m = re.search(r"(\d+)", current)
            count = int(m.group(1)) if m else 0
            count += len(text)
            self._loading_text.setText(f"提取中... 已接收 {count} 字符")
        else:
            self._loading_text.setText(f"提取中... 已接收 {len(text)} 字符")

    def update_entries_incremental(
        self,
        entries: list,
        batch_idx: int,
        total_batches: int,
    ) -> None:
        """增量更新条目显示（多批次提取时，每批完成后调用）。

        Args:
            entries: 累计的 ContextEntry 列表
            batch_idx: 当前完成的批次序号（从 1 开始）
            total_batches: 总批次数
        """
        if not self._is_extracting:
            return
        # 更新条目显示（累计 entries）
        self._entries = list(entries)
        self._disabled_uids.clear()
        self._refresh_entries_display()
        # 更新 loading 文本
        self._loading_text.setText(
            f"提取中... 批次 {batch_idx}/{total_batches} 已完成 "
            f"({len(entries)} 条)"
        )

    # ===== 世界观底层提取流式接口（复用 _stream_view）=====

    def start_ontology_extraction(self) -> None:
        """开始世界观提取：复用 stream_view 显示流式输出，禁用按钮。"""
        self._is_extracting = True
        self._loading_frame_index = 0
        self._loading_label.setText(LOADING_FRAMES[0])
        self._loading_label.setVisible(True)
        self._loading_text.setText("世界观提取中...")
        self._loading_text.setVisible(True)
        self._cancel_btn.setEnabled(False)
        self._add_btn.setEnabled(False)
        self._clear_btn.setEnabled(False)
        self._extract_btn.setEnabled(False)
        self._extract_ontology_btn.setEnabled(False)
        self._extract_protagonist_btn.setEnabled(False)
        self._add_custom_rule_btn.setEnabled(False)
        self._view_custom_rules_btn.setEnabled(False)
        self._set_label_state(self._status_label, "世界观提取中", "textInfo")
        self._loading_timer.start()
        # 显示流式输出查看区并清空内容
        self._stream_view.clear()
        self._stream_view.setPlaceholderText("等待世界观流式输出...")
        self._stream_group.setVisible(True)
        self._stream_group.setChecked(True)
        self._stream_group.setTitle("世界观流式输出（实时接收中...）")

    def update_ontology_progress(self, text: str) -> None:
        """追加世界观提取 chunk 到 stream_view。

        Args:
            text: 新接收的 chunk 文本
        """
        if not self._is_extracting:
            return
        self._stream_view.insertPlainText(text)
        cursor = self._stream_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._stream_view.setTextCursor(cursor)
        # 累积字符数显示进度
        current = self._loading_text.text()
        if "世界观提取中" in current and "已接收" in current:
            import re
            m = re.search(r"(\d+)", current)
            count = int(m.group(1)) if m else 0
            count += len(text)
            self._loading_text.setText(f"世界观提取中... 已接收 {count} 字符")
        else:
            self._loading_text.setText(f"世界观提取中... 已接收 {len(text)} 字符")

    def update_ontology_batch(self, batch_idx: int, total_batches: int) -> None:
        """更新世界观提取批次进度文本。

        Args:
            batch_idx: 当前完成的批次序号（从 1 开始）
            total_batches: 总批次数
        """
        if not self._is_extracting:
            return
        self._loading_text.setText(
            f"世界观提取中... 批次 {batch_idx}/{total_batches} 已完成"
        )

    def finish_ontology_extraction(self, status: str) -> None:
        """世界观提取完成：停止 loading，更新标题。

        Args:
            status: 完成状态消息
        """
        self._is_extracting = False
        self._loading_timer.stop()
        self._loading_label.setVisible(False)
        self._loading_text.setVisible(False)
        self._add_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._extract_btn.setEnabled(True)
        self._extract_ontology_btn.setEnabled(True)
        self._extract_protagonist_btn.setEnabled(True)
        self._add_custom_rule_btn.setEnabled(True)
        self._view_custom_rules_btn.setEnabled(True)
        self._set_label_state(self._status_label, "世界观提取完成", "textSuccess")
        self._stream_group.setTitle("世界观流式输出（接收完成）")

    def fail_ontology_extraction(self, error: str) -> None:
        """世界观提取失败：停止 loading，显示错误。

        Args:
            error: 错误信息
        """
        self._is_extracting = False
        self._loading_timer.stop()
        self._loading_label.setVisible(False)
        self._loading_text.setVisible(False)
        self._add_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._extract_btn.setEnabled(True)
        self._extract_ontology_btn.setEnabled(True)
        self._extract_protagonist_btn.setEnabled(True)
        self._add_custom_rule_btn.setEnabled(True)
        self._view_custom_rules_btn.setEnabled(True)
        self._set_label_state(self._status_label, "世界观提取失败", "textDanger")
        self._set_label_state(self._meta_label, f"错误: {error}", "textDanger")
        self._stream_group.setTitle("世界观流式输出（已中断）")

    # ===== 主角形象提取流式接口（复用 _stream_view，镜像 ontology）=====

    def start_protagonist_extraction(self) -> None:
        """开始主角形象提取：复用 stream_view 显示流式输出，禁用按钮。"""
        self._is_extracting = True
        self._loading_frame_index = 0
        self._loading_label.setText(LOADING_FRAMES[0])
        self._loading_label.setVisible(True)
        self._loading_text.setText("主角形象提取中...")
        self._loading_text.setVisible(True)
        self._cancel_btn.setEnabled(False)
        self._add_btn.setEnabled(False)
        self._clear_btn.setEnabled(False)
        self._extract_btn.setEnabled(False)
        self._extract_ontology_btn.setEnabled(False)
        self._extract_protagonist_btn.setEnabled(False)
        self._add_custom_rule_btn.setEnabled(False)
        self._view_custom_rules_btn.setEnabled(False)
        self._set_label_state(self._status_label, "主角形象提取中", "textInfo")
        self._loading_timer.start()
        # 显示流式输出查看区并清空内容
        self._stream_view.clear()
        self._stream_view.setPlaceholderText("等待主角形象流式输出...")
        self._stream_group.setVisible(True)
        self._stream_group.setChecked(True)
        self._stream_group.setTitle("主角形象流式输出（实时接收中...）")

    def update_protagonist_progress(self, text: str) -> None:
        """追加主角形象提取 chunk 到 stream_view。

        Args:
            text: 新接收的 chunk 文本
        """
        if not self._is_extracting:
            return
        self._stream_view.insertPlainText(text)
        cursor = self._stream_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._stream_view.setTextCursor(cursor)
        # 累积字符数显示进度
        current = self._loading_text.text()
        if "主角形象提取中" in current and "已接收" in current:
            import re
            m = re.search(r"(\d+)", current)
            count = int(m.group(1)) if m else 0
            count += len(text)
            self._loading_text.setText(f"主角形象提取中... 已接收 {count} 字符")
        else:
            self._loading_text.setText(f"主角形象提取中... 已接收 {len(text)} 字符")

    def update_protagonist_batch(self, batch_idx: int, total_batches: int) -> None:
        """更新主角形象提取批次进度文本。

        Args:
            batch_idx: 当前完成的批次序号（从 1 开始）
            total_batches: 总批次数
        """
        if not self._is_extracting:
            return
        self._loading_text.setText(
            f"主角形象提取中... 批次 {batch_idx}/{total_batches} 已完成"
        )

    def finish_protagonist_extraction(self, status: str) -> None:
        """主角形象提取完成：停止 loading，更新标题。

        Args:
            status: 完成状态消息
        """
        self._is_extracting = False
        self._loading_timer.stop()
        self._loading_label.setVisible(False)
        self._loading_text.setVisible(False)
        self._add_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._extract_btn.setEnabled(True)
        self._extract_ontology_btn.setEnabled(True)
        self._extract_protagonist_btn.setEnabled(True)
        self._add_custom_rule_btn.setEnabled(True)
        self._view_custom_rules_btn.setEnabled(True)
        self._set_label_state(self._status_label, "主角形象提取完成", "textSuccess")
        self._stream_group.setTitle("主角形象流式输出（接收完成）")

    def fail_protagonist_extraction(self, error: str) -> None:
        """主角形象提取失败：停止 loading，显示错误。

        Args:
            error: 错误信息
        """
        self._is_extracting = False
        self._loading_timer.stop()
        self._loading_label.setVisible(False)
        self._loading_text.setVisible(False)
        self._add_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._extract_btn.setEnabled(True)
        self._extract_ontology_btn.setEnabled(True)
        self._extract_protagonist_btn.setEnabled(True)
        self._add_custom_rule_btn.setEnabled(True)
        self._view_custom_rules_btn.setEnabled(True)
        self._set_label_state(self._status_label, "主角形象提取失败", "textDanger")
        self._set_label_state(self._meta_label, f"错误: {error}", "textDanger")
        self._stream_group.setTitle("主角形象流式输出（已中断）")

    # ===== 自定义设定结构化流式接口（复用 _stream_view，镜像 ontology）=====

    def start_custom_rule_parsing(self) -> None:
        """开始自定义设定结构化：复用 stream_view 显示流式输出，禁用按钮。"""
        self._is_extracting = True
        self._loading_frame_index = 0
        self._loading_label.setText(LOADING_FRAMES[0])
        self._loading_label.setVisible(True)
        self._loading_text.setText("自定义设定结构化中...")
        self._loading_text.setVisible(True)
        self._cancel_btn.setEnabled(False)
        self._add_btn.setEnabled(False)
        self._clear_btn.setEnabled(False)
        self._extract_btn.setEnabled(False)
        self._extract_ontology_btn.setEnabled(False)
        self._extract_protagonist_btn.setEnabled(False)
        self._add_custom_rule_btn.setEnabled(False)
        self._view_custom_rules_btn.setEnabled(False)
        self._set_label_state(self._status_label, "自定义设定结构化中", "textInfo")
        self._loading_timer.start()
        # 显示流式输出查看区并清空内容
        self._stream_view.clear()
        self._stream_view.setPlaceholderText("等待自定义设定流式输出...")
        self._stream_group.setVisible(True)
        self._stream_group.setChecked(True)
        self._stream_group.setTitle("自定义设定流式输出（实时接收中...）")

    def update_custom_rule_progress(self, text: str) -> None:
        """追加自定义设定结构化 chunk 到 stream_view。

        Args:
            text: 新接收的 chunk 文本
        """
        if not self._is_extracting:
            return
        self._stream_view.insertPlainText(text)
        cursor = self._stream_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._stream_view.setTextCursor(cursor)
        # 累积字符数显示进度
        current = self._loading_text.text()
        if "自定义设定结构化中" in current and "已接收" in current:
            import re
            m = re.search(r"(\d+)", current)
            count = int(m.group(1)) if m else 0
            count += len(text)
            self._loading_text.setText(f"自定义设定结构化中... 已接收 {count} 字符")
        else:
            self._loading_text.setText(f"自定义设定结构化中... 已接收 {len(text)} 字符")

    def finish_custom_rule_parsing(self, status: str) -> None:
        """自定义设定结构化完成：停止 loading，更新标题。

        Args:
            status: 完成状态消息
        """
        self._is_extracting = False
        self._loading_timer.stop()
        self._loading_label.setVisible(False)
        self._loading_text.setVisible(False)
        self._add_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._extract_btn.setEnabled(True)
        self._extract_ontology_btn.setEnabled(True)
        self._extract_protagonist_btn.setEnabled(True)
        self._add_custom_rule_btn.setEnabled(True)
        self._view_custom_rules_btn.setEnabled(True)
        self._set_label_state(self._status_label, "自定义设定结构化完成", "textSuccess")
        self._stream_group.setTitle("自定义设定流式输出（接收完成）")

    def fail_custom_rule_parsing(self, error: str) -> None:
        """自定义设定结构化失败：停止 loading，显示错误。

        Args:
            error: 错误信息
        """
        self._is_extracting = False
        self._loading_timer.stop()
        self._loading_label.setVisible(False)
        self._loading_text.setVisible(False)
        self._add_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._extract_btn.setEnabled(True)
        self._extract_ontology_btn.setEnabled(True)
        self._extract_protagonist_btn.setEnabled(True)
        self._add_custom_rule_btn.setEnabled(True)
        self._view_custom_rules_btn.setEnabled(True)
        self._set_label_state(self._status_label, "自定义设定结构化失败", "textDanger")
        self._set_label_state(self._meta_label, f"错误: {error}", "textDanger")
        self._stream_group.setTitle("自定义设定流式输出（已中断）")

    def restore_extraction_state(
        self, stream_text: str, is_ontology: bool = False, is_protagonist: bool = False
    ) -> None:
        """恢复章节切换前的提取中状态（切回发起章节时调用）。

        复用现有 _is_extracting / _stream_view / _loading_* 状态，重建"接收中"
        视觉态。update_extraction_progress / update_ontology_progress /
        update_protagonist_progress 的 `if not self._is_extracting: return`
        守卫在恢复后重新放行后续 chunk。

        Args:
            stream_text: 已缓冲的流式输出文本
            is_ontology: True=世界观提取（标题/状态文本不同）
            is_protagonist: True=主角形象提取（标题/状态文本不同）
            （is_ontology 与 is_protagonist 互斥，同时为 True 时以 is_protagonist 为准）
        """
        self._is_extracting = True
        self._loading_frame_index = 0
        self._loading_label.setText(LOADING_FRAMES[0])
        self._loading_label.setVisible(True)
        if is_protagonist:
            title = "主角形象提取中..."
        elif is_ontology:
            title = "世界观提取中..."
        else:
            title = "提取中..."
        self._loading_text.setText(title)
        self._loading_text.setVisible(True)
        self._cancel_btn.setEnabled(not is_ontology and not is_protagonist)
        self._add_btn.setEnabled(False)
        self._clear_btn.setEnabled(False)
        if is_protagonist:
            self._extract_btn.setEnabled(False)
            self._extract_ontology_btn.setEnabled(False)
            self._extract_protagonist_btn.setEnabled(False)
            self._set_label_state(self._status_label, "主角形象提取中", "textInfo")
        elif is_ontology:
            self._extract_btn.setEnabled(False)
            self._extract_ontology_btn.setEnabled(False)
            self._extract_protagonist_btn.setEnabled(False)
            self._set_label_state(self._status_label, "世界观提取中", "textInfo")
        else:
            self._extract_btn.setEnabled(False)
            self._extract_ontology_btn.setEnabled(True)
            self._extract_protagonist_btn.setEnabled(True)
            self._set_label_state(self._status_label, "提取中", "textInfo")
        self._loading_timer.start()
        # 显示流式输出区并回填缓冲文本
        self._stream_view.clear()
        self._stream_view.setPlaceholderText("等待流式输出...")
        self._stream_view.setPlainText(stream_text)
        cursor = self._stream_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._stream_view.setTextCursor(cursor)
        self._stream_group.setVisible(True)
        self._stream_group.setChecked(True)
        if is_protagonist:
            prefix = "主角形象流式输出"
        elif is_ontology:
            prefix = "世界观流式输出"
        else:
            prefix = "流式输出"
        self._stream_group.setTitle(f"{prefix}（实时接收中...）")
        # 更新 loading 文本字符数
        self._loading_text.setText(f"{title} 已接收 {len(stream_text)} 字符")

    def load_entries_for_chapter(
        self,
        entries: list,
        meta: dict | None = None,
    ) -> None:
        """加载章节的已有提取结果（章节切换时调用，非提取完成）。

        Args:
            entries: ContextEntry 列表
            meta: 元数据 dict（含 elapsed_seconds/token_usage/batch_count 等）
        """
        self._is_extracting = False
        self._loading_timer.stop()
        self._loading_label.setVisible(False)
        self._loading_text.setVisible(False)
        self._cancel_btn.setEnabled(False)
        self._add_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._stream_group.setVisible(False)

        # 更新条目显示
        self._entries = list(entries)
        self._disabled_uids.clear()
        self._refresh_entries_display()

        # 更新状态与元数据
        if entries:
            self._set_label_state(self._status_label, "已加载", "textSecondary")
            meta_parts: list[str] = [f"条目数: {len(entries)}"]
            if meta:
                elapsed = meta.get("elapsed_seconds", 0)
                if elapsed > 0:
                    meta_parts.append(f"耗时: {elapsed:.2f}s")
                batch_count = meta.get("batch_count", 1)
                if batch_count > 1:
                    meta_parts.append(f"批次: {batch_count}")
                token_usage = meta.get("token_usage", {})
                if token_usage:
                    total_tokens = token_usage.get("total_tokens", 0)
                    if total_tokens:
                        prompt_tokens = token_usage.get("prompt_tokens", 0)
                        completion_tokens = token_usage.get("completion_tokens", 0)
                        meta_parts.append(
                            f"Token: {total_tokens} "
                            f"(输入 {prompt_tokens} + 输出 {completion_tokens})"
                        )
            self._set_label_state(self._meta_label, " | ".join(meta_parts), "metaText")
        else:
            self._set_label_state(self._status_label, "就绪", "textSecondary")
            self._set_label_state(self._meta_label, "尚未提取", "metaText")

    # ===== 属性 =====

    @property
    def is_extracting(self) -> bool:
        """是否正在提取。"""
        return self._is_extracting
