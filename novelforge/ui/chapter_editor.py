"""章节预览与编辑组件。

提供章节正文的预览（只读）和编辑两种模式：
- 预览模式：QPlainTextEdit 只读
- 编辑模式：可修改，5 秒空闲自动保存
- undo/redo（Ctrl+Z / Ctrl+Y，使用 QPlainTextEdit 内置功能）
- 字数统计显示
- 流式输出时锁定编辑（只读 + "续写中"提示）
- 右键菜单"在此处拆分"

信号：
- ``content_changed(str)``: 内容变更（用于自动保存状态提示）
- ``saved()``: 已保存
- ``split_requested(int)``: 请求在指定位置拆分
- ``word_count_changed(int)``: 字数变更
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QContextMenuEvent, QKeySequence
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from novelforge.ui.helpers import set_label_state

logger = logging.getLogger(__name__)

# 自动保存延迟（毫秒）
AUTOSAVE_DELAY_MS = 5000


class ChapterEditor(QWidget):
    """章节预览/编辑组件。

    支持预览/编辑模式切换、自动保存、undo/redo、字数统计。

    Signals:
        content_changed(str): 内容变更
        saved(): 已保存到存储
        split_requested(int): 请求在指定字符位置拆分
        word_count_changed(int): 字数变更
    """

    content_changed = Signal(str)
    saved = Signal()
    split_requested = Signal(int)
    word_count_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        """初始化章节编辑器。"""
        super().__init__(parent)
        self._chapter_id: str | None = None
        self._project_id: str | None = None
        self._is_edit_mode = False
        self._is_streaming = False
        self._has_unsaved_changes = False
        self._original_content = ""

        self._setup_ui()
        self._setup_connections()

        # 自动保存定时器
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._do_autosave)

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 工具栏
        toolbar = QFrame()
        toolbar.setFrameShape(QFrame.Shape.StyledPanel)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 4, 8, 4)

        self._title_label = QLabel("未选择章节")
        self._title_label.setObjectName("panelTitle")
        toolbar_layout.addWidget(self._title_label)

        toolbar_layout.addStretch()

        # 字数标签
        self._word_count_label = QLabel("字数: 0")
        toolbar_layout.addWidget(self._word_count_label)

        # 保存状态标签
        self._save_status_label = QLabel("就绪")
        self._save_status_label.setObjectName("textSecondary")
        toolbar_layout.addWidget(self._save_status_label)

        # 编辑/预览切换按钮
        self._edit_btn = QPushButton("编辑")
        self._edit_btn.setCheckable(True)
        toolbar_layout.addWidget(self._edit_btn)

        layout.addWidget(toolbar)

        # 编辑器（使用 QPlainTextEdit 提升大文本性能）
        self._editor = QPlainTextEdit()
        self._editor.setPlaceholderText("选择章节后在此预览或编辑正文...")
        self._editor.setReadOnly(True)  # 默认只读（预览模式）
        layout.addWidget(self._editor)

    def _setup_connections(self) -> None:
        """连接信号。"""
        self._edit_btn.toggled.connect(self._on_edit_toggled)
        self._editor.textChanged.connect(self._on_text_changed)

    def _set_save_status(self, text: str, state: str) -> None:
        """更新保存状态标签文本与状态色（对象名驱动，由全局 QSS 接管）。

        Args:
            text: 状态文字
            state: 状态对象名（textSecondary/textWarning/textInfo/textSuccess）
        """
        set_label_state(self._save_status_label, text, state)

    # ===== 模式切换 =====

    def _on_edit_toggled(self, checked: bool) -> None:
        """编辑/预览模式切换。"""
        if self._is_streaming:
            # 流式输出时不允许切换
            self._edit_btn.setChecked(False)
            return
        self._is_edit_mode = checked
        self._editor.setReadOnly(not checked)
        self._edit_btn.setText("完成编辑" if checked else "编辑")
        logger.debug("切换模式: %s", "编辑" if checked else "预览")

    def set_edit_mode(self, enabled: bool) -> None:
        """设置编辑模式。"""
        self._edit_btn.setChecked(enabled)

    # ===== 章节加载 =====

    def load_chapter(
        self,
        chapter_id: str,
        project_id: str,
        title: str,
        content: str,
    ) -> None:
        """加载章节内容到编辑器。

        Args:
            chapter_id: 章节 ID
            project_id: 项目 ID
            title: 章节标题
            content: 章节正文
        """
        # 如果有未保存的修改，先保存
        if self._has_unsaved_changes:
            self._do_autosave()

        self._chapter_id = chapter_id
        self._project_id = project_id
        self._original_content = content
        self._has_unsaved_changes = False

        # 阻断信号，避免触发 textChanged
        self._editor.blockSignals(True)
        self._editor.setPlainText(content)
        self._editor.blockSignals(False)

        self._title_label.setText(title)
        self._update_word_count()
        self._set_save_status("已保存", "textSecondary")

    def clear(self) -> None:
        """清空编辑器。"""
        if self._has_unsaved_changes:
            self._do_autosave()
        self._chapter_id = None
        self._project_id = None
        self._original_content = ""
        self._has_unsaved_changes = False
        self._editor.blockSignals(True)
        self._editor.clear()
        self._editor.blockSignals(False)
        self._title_label.setText("未选择章节")
        self._word_count_label.setText("字数: 0")

    # ===== 内容变更与自动保存 =====

    def _on_text_changed(self) -> None:
        """文本变更处理。"""
        if not self._is_edit_mode or self._is_streaming:
            return

        self._has_unsaved_changes = True
        self._set_save_status("未保存修改", "textWarning")
        self._update_word_count()

        # 重置自动保存定时器
        self._autosave_timer.start(AUTOSAVE_DELAY_MS)

        content = self._editor.toPlainText()
        self.content_changed.emit(content)

    def _do_autosave(self) -> None:
        """执行自动保存。"""
        if not self._has_unsaved_changes or not self._chapter_id:
            return

        self._has_unsaved_changes = False
        self._original_content = self._editor.toPlainText()
        self._set_save_status("已保存", "textSecondary")
        self.saved.emit()
        logger.debug("章节自动保存: %s", self._chapter_id)

    def save_now(self) -> None:
        """立即保存（Ctrl+S 触发）。"""
        self._autosave_timer.stop()
        self._do_autosave()

    def _update_word_count(self) -> None:
        """更新字数统计。"""
        text = self._editor.toPlainText()
        count = len(text)
        self._word_count_label.setText(f"字数: {count}")
        self.word_count_changed.emit(count)

    # ===== 流式输出锁定 =====

    def set_streaming_locked(self, locked: bool) -> None:
        """流式输出时锁定编辑。

        Args:
            locked: True 锁定（只读 + "续写中"提示），False 解锁
        """
        self._is_streaming = locked
        if locked:
            self._editor.setReadOnly(True)
            self._edit_btn.setEnabled(False)
            self._set_save_status("续写中...", "textInfo")
        else:
            self._edit_btn.setEnabled(True)
            self._editor.setReadOnly(not self._is_edit_mode)
            if self._has_unsaved_changes:
                self._set_save_status("未保存修改", "textWarning")
            else:
                self._set_save_status("已保存", "textSecondary")

    # ===== undo/redo =====

    def undo(self) -> None:
        """撤销。"""
        if self._editor.document().isUndoAvailable():
            self._editor.undo()

    def redo(self) -> None:
        """重做。"""
        if self._editor.document().isRedoAvailable():
            self._editor.redo()

    # ===== 右键菜单：拆分 =====

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """右键菜单：包含"在此处拆分"选项。"""
        if not self._is_edit_mode or not self._chapter_id:
            super().contextMenuEvent(event)
            return

        menu = self._editor.createStandardContextMenu()

        menu.addSeparator()
        split_action = QAction("在此处拆分", menu)
        split_action.triggered.connect(self._on_split_here)
        menu.addAction(split_action)

        menu.exec(event.globalPos())

    def _on_split_here(self) -> None:
        """在光标位置拆分章节。"""
        cursor = self._editor.textCursor()
        position = cursor.position()
        logger.info("请求拆分: position=%d", position)
        self.split_requested.emit(position)

    # ===== 属性访问 =====

    @property
    def content(self) -> str:
        """当前编辑器内容。"""
        return self._editor.toPlainText()

    @property
    def chapter_id(self) -> str | None:
        """当前章节 ID。"""
        return self._chapter_id

    @property
    def has_unsaved_changes(self) -> bool:
        """是否有未保存的修改。"""
        return self._has_unsaved_changes

    def append_content(self, text: str) -> None:
        """追加内容到编辑器末尾（接受续写时调用）。

        Args:
            text: 待追加的文本
        """
        if not text:
            return
        current = self._editor.toPlainText()
        if current:
            new_content = current + "\n\n" + text
        else:
            new_content = text
        self._editor.blockSignals(True)
        self._editor.setPlainText(new_content)
        self._editor.blockSignals(False)
        self._has_unsaved_changes = True
        self._do_autosave()
