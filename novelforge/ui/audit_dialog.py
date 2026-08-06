"""AuditDialog：单章续写审计报告对话框。

流式展示审计报告输出，完成后文本可编辑，用户可采纳或取消。

流程：
1. AuditWorker.chunk_received → dialog.append_chunk（流式中只读）
2. AuditWorker.finished → dialog.finish_streaming（设为可编辑，启用采纳按钮）
3. 用户点击"采纳" → emit accepted(编辑后文本) + self.accept()
4. 用户点击"取消" → emit cancelled() + self.reject()

流式中也可点取消，由 MainWindow 连接 cancelled 信号触发 worker.stop()。

细纲场景额外支持"修改"功能（enable_revision=True）：
- finish_streaming 后启用"修改"按钮
- 用户点击"修改" → 弹多行输入框获取反馈意见 → emit revision_requested(当前文本, 反馈)
- MainWindow 启动 AuditWorker 修订，chunk 回填对话框
- start_revision_streaming 切回流式状态（备份原文本、清空、只读）
- 修订完成后 finish_streaming 恢复可编辑（可再次修改或采纳）
- 修订失败 fail_revision 恢复原文本
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class AuditDialog(QDialog):
    """单章续写审计报告对话框。

    流式输出审计报告，完成后可编辑，采纳后传回编辑后文本。

    Signals:
        accepted(str): 用户采纳，传回编辑后的审计报告文本
        cancelled(): 用户取消（含流式中取消）
        revision_requested(str, str): 用户要求根据反馈修改（当前文本, 反馈意见）；
            仅 enable_revision=True 时可用
    """

    accepted_text = Signal(str)
    cancelled = Signal()
    revision_requested = Signal(str, str)

    def __init__(self, parent=None, enable_revision: bool = False) -> None:
        """初始化审计对话框。

        Args:
            parent: 父窗口
            enable_revision: 是否启用"修改"按钮（细纲场景 True，其他审计场景 False）
        """
        super().__init__(parent)
        self.setWindowTitle("续写审计")
        self.resize(720, 560)

        self._is_streaming = True
        self._revision_backup: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 状态标签
        self._status_label = QLabel("审计中...")
        layout.addWidget(self._status_label)

        # 文本区（流式中只读，完成后可编辑）
        self._text_edit = QPlainTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setPlaceholderText("审计报告将流式输出在此处...")
        layout.addWidget(self._text_edit, 1)

        # 底部按钮
        btn_layout = QHBoxLayout()

        # "修改"按钮（仅细纲场景创建）
        self._revise_btn: QPushButton | None = None
        if enable_revision:
            self._revise_btn = QPushButton("修改")
            self._revise_btn.setEnabled(False)
            self._revise_btn.setToolTip(
                "点击后输入修改意见，AI 将根据意见修改当前内容"
            )
            self._revise_btn.clicked.connect(self._on_revise_clicked)
            btn_layout.addWidget(self._revise_btn)

        btn_layout.addStretch(1)

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_layout.addWidget(self._cancel_btn)

        self._accept_btn = QPushButton("采纳")
        self._accept_btn.setEnabled(False)
        self._accept_btn.setObjectName("primaryBtn")
        self._accept_btn.clicked.connect(self._on_accept_clicked)
        btn_layout.addWidget(self._accept_btn)

        layout.addLayout(btn_layout)

    def append_chunk(self, text: str) -> None:
        """流式追加文本（保持只读）。"""
        cursor = self._text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        # 自动滚动到底部
        self._text_edit.setTextCursor(cursor)
        bar = self._text_edit.verticalScrollBar()
        bar.setValue(bar.maximum())

    def finish_streaming(self, full_text: str) -> None:
        """流式完成：设为可编辑，启用采纳按钮，更新状态。"""
        self._is_streaming = False
        self._text_edit.setReadOnly(False)
        self._text_edit.setPlainText(full_text)
        self._accept_btn.setEnabled(True)
        if self._revise_btn is not None:
            self._revise_btn.setEnabled(True)
        self._status_label.setText("审计完成，可编辑后采纳")

    def fail(self, error_msg: str) -> None:
        """审计失败：显示错误，禁用采纳按钮。"""
        self._is_streaming = False
        self._status_label.setText(f"审计失败: {error_msg}")
        self._accept_btn.setEnabled(False)
        if self._revise_btn is not None:
            self._revise_btn.setEnabled(False)
        self._text_edit.setReadOnly(False)

    def start_revision_streaming(self) -> None:
        """切回流式状态，准备接收修订后的内容。

        备份当前文本（修订失败时可恢复），清空文本区，设为只读，
        禁用所有操作按钮，状态显示"修改中"。
        """
        self._revision_backup = self._text_edit.toPlainText()
        self._is_streaming = True
        self._text_edit.setReadOnly(True)
        self._text_edit.setPlainText("")
        self._accept_btn.setEnabled(False)
        if self._revise_btn is not None:
            self._revise_btn.setEnabled(False)
        self._status_label.setText("根据反馈修改中...")

    def fail_revision(self, error_msg: str) -> None:
        """修订失败：恢复原文本，恢复可编辑状态。

        与 ``fail`` 不同：修订失败不禁用按钮，恢复修订前文本，
        用户可重新修改或直接采纳原文本。
        """
        self._is_streaming = False
        self._text_edit.setReadOnly(False)
        self._text_edit.setPlainText(self._revision_backup)
        self._accept_btn.setEnabled(True)
        if self._revise_btn is not None:
            self._revise_btn.setEnabled(True)
        self._status_label.setText(
            f"修改失败: {error_msg}，可重新修改或采纳原内容"
        )

    def get_edited_text(self) -> str:
        """获取编辑后的审计报告文本。"""
        return self._text_edit.toPlainText()

    def _on_accept_clicked(self) -> None:
        """采纳按钮：emit accepted_text 并关闭对话框。"""
        text = self.get_edited_text()
        self.accepted_text.emit(text)
        self.accept()

    def _on_cancel_clicked(self) -> None:
        """取消按钮：emit cancelled 并关闭对话框。"""
        self.cancelled.emit()
        self.reject()

    def _on_revise_clicked(self) -> None:
        """修改按钮：弹出多行输入框获取反馈意见，emit revision_requested。"""
        feedback, ok = QInputDialog.getMultiLineText(
            self,
            "修改内容",
            "请输入修改意见（AI 将根据意见修改当前内容）：",
            "",
        )
        if not ok or not feedback.strip():
            return
        current_text = self.get_edited_text()
        self.revision_requested.emit(current_text, feedback.strip())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """拦截 Esc 键，按取消处理（确保 worker.stop 被触发）。"""
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel_clicked()
        else:
            super().keyPressEvent(event)
