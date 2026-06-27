"""上下文提取失败/取消处理对话框。

当上下文提取失败或用户主动取消时弹出，提供三选项：
- 重试：重新调用 ContextExtractor.extract
- 跳过提取继续续写：用空 entries 继续续写流程
- 取消续写：终止本次续写

返回值（通过 ``exec()`` 后用 ``result()`` 获取）：
- ``ExtractionDialog.RESULT_RETRY = 1``
- ``ExtractionDialog.RESULT_SKIP = 2``
- ``ExtractionDialog.RESULT_CANCEL = 3``
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ExtractionDialog(QDialog):
    """上下文提取失败/取消处理对话框。

    提供三选项：重试 / 跳过提取继续续写 / 取消续写。

    Usage::

        dialog = ExtractionDialog(parent, mode="failed", error="网络错误")
        if dialog.exec() == ExtractionDialog.RESULT_RETRY:
            # 重试提取
            ...
        elif dialog.exec() == ExtractionDialog.RESULT_SKIP:
            # 跳过提取继续续写
            ...
        else:
            # 取消续写
            ...
    """

    # 返回值常量
    RESULT_RETRY = 1
    RESULT_SKIP = 2
    RESULT_CANCEL = 3

    def __init__(
        self,
        parent: QWidget | None = None,
        mode: str = "failed",
        error: str = "",
    ) -> None:
        """初始化对话框。

        Args:
            parent: 父控件
            mode: 模式（"failed"=提取失败 / "cancelled"=用户取消）
            error: 错误信息（mode=failed 时显示）
        """
        super().__init__(parent)
        self._result_code = self.RESULT_CANCEL

        if mode == "cancelled":
            self.setWindowTitle("上下文提取已取消")
        else:
            self.setWindowTitle("上下文提取失败")

        self.setMinimumWidth(400)
        self._setup_ui(mode, error)

    def _setup_ui(self, mode: str, error: str) -> None:
        """构建 UI。

        Args:
            mode: 模式
            error: 错误信息
        """
        layout = QVBoxLayout(self)

        # 提示文本
        if mode == "cancelled":
            message = "上下文提取已被用户取消。"
        else:
            message = "上下文提取失败。"
            if error:
                message += f"\n\n错误信息：{error}"
        message += "\n\n请选择后续操作："

        label = QLabel(message)
        label.setWordWrap(True)
        layout.addWidget(label)

        # 三选项按钮
        btn_layout = QHBoxLayout()

        retry_btn = QPushButton("重试提取")
        retry_btn.setToolTip("重新调用 LLM 提取上下文")
        retry_btn.clicked.connect(self._on_retry)
        btn_layout.addWidget(retry_btn)

        skip_btn = QPushButton("跳过提取继续续写")
        skip_btn.setToolTip("使用空上下文条目继续续写流程")
        skip_btn.clicked.connect(self._on_skip)
        btn_layout.addWidget(skip_btn)

        cancel_btn = QPushButton("取消续写")
        cancel_btn.setToolTip("终止本次续写")
        cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

    def _on_retry(self) -> None:
        """重试按钮。"""
        self._result_code = self.RESULT_RETRY
        self.accept()

    def _on_skip(self) -> None:
        """跳过按钮。"""
        self._result_code = self.RESULT_SKIP
        self.accept()

    def _on_cancel(self) -> None:
        """取消按钮。"""
        self._result_code = self.RESULT_CANCEL
        self.reject()

    def result_code(self) -> int:
        """获取用户选择的结果代码。

        Returns:
            RESULT_RETRY / RESULT_SKIP / RESULT_CANCEL
        """
        return self._result_code
