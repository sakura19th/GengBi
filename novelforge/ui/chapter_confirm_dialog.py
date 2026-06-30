"""ChapterConfirmDialog：每章后暂停点对话框。

显示章节正文供用户确认，用户选择通过/不通过。
不通过时展示调整内容输入框，用户输入反馈后提交重修。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ChapterConfirmDialog(QDialog):
    """每章后暂停点对话框：显示章节正文，用户选择通过/不通过。

    不通过时展示调整内容输入框，用户输入后点"提交并重修"恢复。
    通过 get_result() 获取 (action, feedback) 元组。

    Signals:
        result_ready(str, str): action 与 feedback，与 get_result() 返回值一致
    """

    result_ready = Signal(str, str)

    def __init__(
        self, chapter_index: int, content: str, parent=None
    ) -> None:
        """初始化每章确认对话框。

        Args:
            chapter_index: 章节序号（0 基）
            content: 章节正文
            parent: 父窗口
        """
        super().__init__(parent)
        self._chapter_index = chapter_index
        self._content = content
        self._action: str = "cancel"
        self._feedback: str = ""

        self._setup_ui()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        self.setWindowTitle(f"第 {self._chapter_index + 1} 章 确认")
        self.resize(700, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 提示标签
        hint = QLabel("请审阅本章正文，选择通过或需要调整：")
        hint.setObjectName("textSecondary")
        layout.addWidget(hint)

        # 正文展示区（只读）
        self._content_edit = QPlainTextEdit()
        self._content_edit.setReadOnly(True)
        self._content_edit.setPlainText(self._content)
        self._content_edit.setMinimumHeight(300)
        layout.addWidget(self._content_edit, 1)

        # 调整内容输入区（初始隐藏）
        self._feedback_container = QWidget()
        feedback_layout = QVBoxLayout(self._feedback_container)
        feedback_layout.setContentsMargins(0, 0, 0, 0)
        feedback_layout.setSpacing(6)
        feedback_label = QLabel("需调整的内容（将作为修订指导重写本章）：")
        feedback_label.setObjectName("textSecondary")
        feedback_layout.addWidget(feedback_label)
        self._feedback_edit = QPlainTextEdit()
        self._feedback_edit.setMinimumHeight(80)
        self._feedback_edit.setPlaceholderText("请输入需要调整的内容描述…")
        feedback_layout.addWidget(self._feedback_edit)
        self._feedback_container.setVisible(False)
        layout.addWidget(self._feedback_container)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self._approve_btn = QPushButton("通过")
        self._approve_btn.setObjectName("accentButton")
        self._approve_btn.clicked.connect(self._on_approve)
        btn_row.addWidget(self._approve_btn)

        self._reject_btn = QPushButton("不通过，需要调整")
        self._reject_btn.clicked.connect(self._on_reject_toggle)
        btn_row.addWidget(self._reject_btn)

        self._submit_reject_btn = QPushButton("提交并重修")
        self._submit_reject_btn.setObjectName("accentButton")
        self._submit_reject_btn.clicked.connect(self._on_submit_reject)
        self._submit_reject_btn.setVisible(False)
        btn_row.addWidget(self._submit_reject_btn)

        self._cancel_btn = QPushButton("取消续写")
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)

        layout.addLayout(btn_row)

    def _on_approve(self) -> None:
        """通过：action=approve，关闭对话框。"""
        self._action = "approve"
        self._feedback = ""
        self.result_ready.emit(self._action, self._feedback)
        self.accept()

    def _on_reject_toggle(self) -> None:
        """不通过：展开调整内容输入框，切换按钮。"""
        self._feedback_container.setVisible(True)
        self._submit_reject_btn.setVisible(True)
        self._reject_btn.setVisible(False)
        self._feedback_edit.setFocus()

    def _on_submit_reject(self) -> None:
        """提交重修：action=reject，feedback=输入内容，关闭对话框。"""
        self._action = "reject"
        self._feedback = self._feedback_edit.toPlainText().strip()
        self.result_ready.emit(self._action, self._feedback)
        self.accept()

    def _on_cancel(self) -> None:
        """取消：action=cancel，关闭对话框。"""
        self._action = "cancel"
        self._feedback = ""
        self.result_ready.emit(self._action, self._feedback)
        self.reject()

    def get_result(self) -> tuple[str, str]:
        """返回 (action, feedback)。

        action 为 "approve"/"reject"/"cancel"，
        feedback 为调整内容（approve/cancel 时为空）。
        """
        return self._action, self._feedback
