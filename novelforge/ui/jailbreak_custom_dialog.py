"""自定义破限文本编辑对话框。

供 FlowEndpointDialog 在用户选择「自定义」破限等级时，编辑该流程的
自定义破限文本。文本将作为 system 消息前置到流程 messages 数组开头。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)


class JailbreakCustomDialog(QDialog):
    """自定义破限文本编辑对话框。

    含一个 ``QPlainTextEdit`` 多行编辑区 + 确定/取消按钮。

    Usage::

        dialog = JailbreakCustomDialog(flow_name, initial_text, parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            text = dialog.get_text()
    """

    def __init__(
        self,
        flow_name: str,
        initial_text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """初始化自定义破限编辑对话框。

        Args:
            flow_name: 流程显示名（用于窗口标题）
            initial_text: 初始文本
            parent: 父控件
        """
        super().__init__(parent)
        self._flow_name = flow_name

        self.setWindowTitle(f"自定义破限 — {flow_name}")
        self.setMinimumWidth(520)
        self.setMinimumHeight(360)

        layout = QVBoxLayout(self)

        hint = QLabel(
            "在此编辑自定义破限文本。保存后该文本将作为 system 消息前置到\n"
            "此流程的 messages 开头，覆盖等级模板。留空则回退到等级模板。"
        )
        hint.setObjectName("metaText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._editor = QPlainTextEdit()
        self._editor.setPlainText(initial_text)
        self._editor.setPlaceholderText("输入自定义破限文本…")
        layout.addWidget(self._editor, 1)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_text(self) -> str:
        """返回编辑后的破限文本。"""
        return self._editor.toPlainText()
