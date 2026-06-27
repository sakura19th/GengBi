"""UI 对话框组件。

包含隐私声明对话框等独立对话框组件。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)


# 隐私声明文本
PRIVACY_NOTICE = """赓笔 隐私声明

本工具除调用您配置的 LLM API 外，不发送任何数据到第三方。

数据存储：
- 所有小说内容、项目数据、配置信息均存储在本地（~/.novelforge/）
- 不进行云端同步，不上传任何数据到我们的服务器

API Key 安全：
- API Key 使用 PBKDF2HMAC + Fernet 加密存储，不明文落盘
- 加密密钥基于本机机器 ID 派生，换机器需重新配置或导入密钥

日志记录：
- 崩溃日志不包含小说内容和 API Key
- API Key 在日志中自动脱敏为 sk-****
- 请求体只记录摘要（前 200 字 + 总字数）

LLM API 调用：
- 仅向您选择的 API 端点发送续写请求和上下文数据
- 请求内容包含小说正文片段（用于续写），由您选择的 LLM 服务商处理
- 请确保您信任所配置的 LLM 服务商的隐私政策

本声明仅供参考，不作为接受/拒绝条件。"""


class PrivacyDialog(QDialog):
    """隐私声明对话框（仅展示，无同意/不同意）。"""

    def __init__(self, parent=None) -> None:
        """初始化隐私声明对话框。"""
        super().__init__(parent)
        self.setWindowTitle("隐私声明")
        self.setMinimumSize(520, 420)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # 标题
        title = QLabel("欢迎使用 赓笔")
        title.setObjectName("dialogTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # 声明内容
        notice_text = QTextEdit()
        notice_text.setPlainText(PRIVACY_NOTICE)
        notice_text.setReadOnly(True)
        layout.addWidget(notice_text)

        # 关闭按钮（仅展示，无同意/不同意）
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("primaryBtn")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
