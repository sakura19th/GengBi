"""调试提示词预览对话框。

调试模式开启后，每次发送提示词给 LLM 前弹窗显示阶段名与完整提示词内容，
用户确认后发送，取消则跳过该次调用。
"""
from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class DebugPromptDialog(QDialog):
    """调试提示词预览对话框。

    显示即将发送的 messages JSON 文本，用户点击"发送"或"取消"。

    Attributes:
        confirmed: 用户是否确认发送（默认 False）
    """

    def __init__(
        self,
        phase_name: str,
        messages: list[dict[str, Any]],
        parent=None,
    ) -> None:
        """初始化调试提示词对话框。

        Args:
            phase_name: 阶段名（用于窗口标题）
            messages: 即将发送的 messages 列表
            parent: 父窗口
        """
        super().__init__(parent)
        self.setWindowTitle(f"调试模式 - {phase_name}")
        self.confirmed = False
        self.resize(700, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 提示标签
        hint = QLabel(f"阶段：{phase_name}　即将发送以下提示词，确认后发送：")
        hint.setObjectName("metaText")
        layout.addWidget(hint)

        # messages JSON 文本（只读）
        self._text_edit = QPlainTextEdit()
        self._text_edit.setReadOnly(True)
        messages_json = json.dumps(messages, ensure_ascii=False, indent=2)
        self._text_edit.setPlainText(messages_json)
        layout.addWidget(self._text_edit, 1)

        # 按钮区
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        self._send_btn = QPushButton("发送")
        self._send_btn.setObjectName("primaryBtn")
        self._send_btn.clicked.connect(self._on_send)

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.clicked.connect(self._on_cancel)

        btn_layout.addWidget(self._send_btn)
        btn_layout.addWidget(self._cancel_btn)
        layout.addLayout(btn_layout)

    def _on_send(self) -> None:
        """发送按钮：设置 confirmed 为 True 并关闭对话框。"""
        self.confirmed = True
        self.close()

    def _on_cancel(self) -> None:
        """取消按钮：confirmed 保持 False 并关闭对话框。"""
        self.confirmed = False
        self.close()
