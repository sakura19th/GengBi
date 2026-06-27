"""CheckpointDialog：Agent 续写流程暂停点交互对话框。

支持两种模式：
- 大纲暂停（after_outline）：可编辑大纲文本，接受/编辑后继续/取消
- 验证暂停（after_verify）：只读评审报告，接受/修订/重写
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from novelforge.utils.outline_serializer import (
    format_critique,
    format_outline,
    parse_outline,
)


class CheckpointDialog(QDialog):
    """Agent 续写流程暂停点交互对话框。

    根据 checkpoint_name 切换大纲暂停模式或验证暂停模式，
    用户操作后通过 get_result() 获取 (action, payload) 元组。

    Signals:
        result_ready(str, object): action 与 payload，与 get_result() 返回值一致
    """

    result_ready = Signal(str, object)

    def __init__(
        self, checkpoint_name: str, payload: Any, parent=None
    ) -> None:
        """初始化检查点对话框。

        Args:
            checkpoint_name: 检查点名称，"after_outline" 或 "after_verify"
            payload: 检查点产物，Outline 或 CritiqueReport 对象
            parent: 父窗口
        """
        super().__init__(parent)
        self._checkpoint_name = checkpoint_name
        self._original_payload = payload
        self._action: str = "cancel"
        self._result_payload: Any = None

        self._setup_ui()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """根据 checkpoint_name 构建对应模式的 UI。"""
        layout = QVBoxLayout(self)

        if self._checkpoint_name == "after_outline":
            self._setup_outline_mode(layout)
        elif self._checkpoint_name == "after_verify":
            self._setup_verify_mode(layout)
        else:
            # 未知检查点：显示提示并仅提供关闭按钮
            self.setWindowTitle("未知检查点")
            self.resize(400, 200)
            label = QLabel(f"未知的检查点类型：{self._checkpoint_name}")
            layout.addWidget(label)
            btn = QPushButton("关闭")
            btn.clicked.connect(self.reject)
            layout.addWidget(btn)

    def _setup_outline_mode(self, layout: QVBoxLayout) -> None:
        """构建大纲暂停模式 UI。"""
        self.setWindowTitle("大纲检查点 - 可编辑后继续")
        self.resize(600, 500)

        # 提示标签
        hint = QLabel("以下是 Agent 生成的大纲，您可以编辑后继续，或直接接受原大纲。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 可编辑文本区域
        self._text_edit = QPlainTextEdit()
        self._text_edit.setPlainText(
            format_outline(self._original_payload)
        )
        layout.addWidget(self._text_edit)

        # 按钮栏
        btn_layout = QHBoxLayout()

        btn_accept = QPushButton("接受大纲")
        btn_accept.clicked.connect(self._on_accept)

        btn_edit_continue = QPushButton("编辑后继续")
        btn_edit_continue.clicked.connect(self._on_edit_continue)

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self._on_cancel)

        btn_layout.addWidget(btn_accept)
        btn_layout.addWidget(btn_edit_continue)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)

        layout.addLayout(btn_layout)

    def _setup_verify_mode(self, layout: QVBoxLayout) -> None:
        """构建验证暂停模式 UI。"""
        self.setWindowTitle("验证检查点 - 评审报告")
        self.resize(500, 400)

        # 提示标签
        hint = QLabel("以下是验证阶段的评审报告，请选择后续操作。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 只读文本区域
        self._text_edit = QPlainTextEdit()
        self._text_edit.setPlainText(
            format_critique(self._original_payload)
        )
        self._text_edit.setReadOnly(True)
        layout.addWidget(self._text_edit)

        # 按钮栏
        btn_layout = QHBoxLayout()

        btn_accept = QPushButton("接受当前结果")
        btn_accept.clicked.connect(self._on_accept_result)

        btn_revise = QPushButton("修订")
        btn_revise.clicked.connect(self._on_revise)

        btn_rewrite = QPushButton("重写")
        btn_rewrite.clicked.connect(self._on_rewrite)

        btn_layout.addWidget(btn_accept)
        btn_layout.addWidget(btn_revise)
        btn_layout.addWidget(btn_rewrite)
        btn_layout.addStretch()

        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    # 按钮回调
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        """大纲模式：接受原大纲继续。"""
        self._action = "accept"
        self._result_payload = self._original_payload
        self.result_ready.emit(self._action, self._result_payload)
        self.accept()

    def _on_edit_continue(self) -> None:
        """大纲模式：使用编辑后的文本解析回 Outline 继续。"""
        edited_text = self._text_edit.toPlainText()
        parsed = parse_outline(edited_text)
        self._action = "edit_continue"
        # 解析失败时回退到原大纲
        self._result_payload = parsed if parsed is not None else self._original_payload
        self.result_ready.emit(self._action, self._result_payload)
        self.accept()

    def _on_cancel(self) -> None:
        """大纲模式：取消整个 agent 流程。"""
        self._action = "cancel"
        self._result_payload = None
        self.result_ready.emit(self._action, self._result_payload)
        self.reject()

    def _on_accept_result(self) -> None:
        """验证模式：接受当前写作结果，不修订。"""
        self._action = "accept"
        self._result_payload = None
        self.result_ready.emit(self._action, self._result_payload)
        self.accept()

    def _on_revise(self) -> None:
        """验证模式：进入修订循环。"""
        self._action = "revise"
        self._result_payload = None
        self.result_ready.emit(self._action, self._result_payload)
        self.accept()

    def _on_rewrite(self) -> None:
        """验证模式：重跑写作阶段。"""
        self._action = "rewrite"
        self._result_payload = None
        self.result_ready.emit(self._action, self._result_payload)
        self.accept()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_result(self) -> tuple[str, Any]:
        """获取用户操作结果。

        Returns:
            (action, payload) 元组：
            - 大纲模式：action 为 "accept"/"edit_continue"/"cancel"
              payload 为 Outline 对象（cancel 时为 None）
            - 验证模式：action 为 "accept"/"revise"/"rewrite"
              payload 为 None
        """
        return self._action, self._result_payload
