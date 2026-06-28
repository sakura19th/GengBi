"""CheckpointDialog：卷续写流程暂停点交互对话框。

支持简单暂停模式（卷续写 after_deep_analysis/after_volume_outline/after_audit）：
仅提示信息 + 接受/编辑/取消（编辑在面板中进行，对话框不解析产物）。
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class CheckpointDialog(QDialog):
    """卷续写流程暂停点交互对话框。

    根据 checkpoint_name 进入简单暂停模式，用户操作后通过 get_result()
    获取 (action, payload) 元组。

    Signals:
        result_ready(str, object): action 与 payload，与 get_result() 返回值一致
    """

    result_ready = Signal(str, object)

    def __init__(
        self, checkpoint_name: str, payload: Any, parent=None
    ) -> None:
        """初始化检查点对话框。

        Args:
            checkpoint_name: 检查点名称，支持：
                "after_deep_analysis"/"after_volume_outline"/"after_audit"
                （卷续写简单暂停）
            payload: 检查点产物（DeepAnalysis/VolumeOutline/OutlineAuditReport 对象）
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

        if self._checkpoint_name in (
            "after_deep_analysis",
            "after_volume_outline",
            "after_audit",
        ):
            # 卷续写检查点：简单模式（不显示产物，编辑在面板中进行）
            self._setup_simple_mode(layout)
        else:
            # 未知检查点：显示提示并仅提供关闭按钮
            self.setWindowTitle("未知检查点")
            self.resize(400, 200)
            label = QLabel(f"未知的检查点类型：{self._checkpoint_name}")
            layout.addWidget(label)
            btn = QPushButton("关闭")
            btn.clicked.connect(self.reject)
            layout.addWidget(btn)

    def _setup_simple_mode(self, layout: QVBoxLayout) -> None:
        """构建简单暂停模式 UI：提示信息 + 接受/编辑/取消（无编辑区）。

        用于卷续写检查点（after_deep_analysis/after_volume_outline/after_audit）：
        产物已在 VolumePanel 中展示并可编辑，对话框只负责让用户选择操作。
        编辑 = 关闭对话框，用户在面板中编辑后点击"继续"按钮恢复。
        """
        self.setWindowTitle("卷续写检查点")
        self.resize(400, 200)

        hint = QLabel(
            "已到达暂停点。您可以在面板中查看和编辑产物。\n\n请选择操作："
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 按钮栏
        btn_layout = QHBoxLayout()

        btn_accept = QPushButton("接受")
        btn_accept.clicked.connect(self._on_accept)

        btn_edit = QPushButton("编辑")
        btn_edit.clicked.connect(self._on_edit)

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self._on_cancel)

        btn_layout.addWidget(btn_accept)
        btn_layout.addWidget(btn_edit)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)

        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    # 按钮回调
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        """简单模式：接受原产物继续。"""
        self._action = "accept"
        self._result_payload = self._original_payload
        self.result_ready.emit(self._action, self._result_payload)
        self.accept()

    def _on_edit(self) -> None:
        """简单模式：关闭对话框让用户在面板中编辑，不解析产物。

        返回 action="edit"，payload=None。调用方应显示面板中的"继续"按钮，
        用户在面板编辑后点击继续时再由调用方解析编辑后的产物并 resume。
        """
        self._action = "edit"
        self._result_payload = None
        self.result_ready.emit(self._action, self._result_payload)
        self.accept()

    def _on_cancel(self) -> None:
        """简单模式：取消整个卷续写流程。"""
        self._action = "cancel"
        self._result_payload = None
        self.result_ready.emit(self._action, self._result_payload)
        self.reject()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_result(self) -> tuple[str, Any]:
        """获取用户操作结果。

        Returns:
            (action, payload) 元组：
            - 简单模式（卷续写）：action 为 "accept"/"edit"/"cancel"
              payload 为原产物（accept 时）或 None（edit/cancel 时）
        """
        return self._action, self._result_payload
