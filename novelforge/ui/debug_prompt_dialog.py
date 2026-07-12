"""调试提示词预览对话框。

调试模式开启后，每次发送提示词给 LLM 前弹窗显示阶段名与完整提示词内容，
用户确认后发送，取消则跳过该次调用。对话框额外提供端点/模型下拉，
允许在运行时覆盖当前 LLM 调用使用的端点/模型，方便对比不同模型/端点的输出效果。
"""
from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
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
    可选端点/模型下拉用于运行时覆盖（默认选中当前值，端点切换时按
    enabled_models → models → [default_model] 回退链填充模型）。

    Attributes:
        confirmed: 用户是否确认发送（默认 False）
        selected_endpoint: 确认时选中的 endpoint dict
        selected_model: 确认时选中的模型名
    """

    def __init__(
        self,
        phase_name: str,
        messages: list[dict[str, Any]],
        endpoints: list[dict] | None = None,
        current_endpoint_id: str = "",
        current_model: str = "",
        parent=None,
    ) -> None:
        """初始化调试提示词对话框。

        Args:
            phase_name: 阶段名（用于窗口标题）
            messages: 即将发送的 messages 列表
            endpoints: 可选端点列表（含 id/name/base_url/models 等），用于覆盖下拉
            current_endpoint_id: 当前使用的端点 ID（下拉默认选中）
            current_model: 当前使用的模型名（下拉默认选中）
            parent: 父窗口
        """
        super().__init__(parent)
        self.setWindowTitle(f"调试模式 - {phase_name}")
        self.confirmed = False
        self.selected_endpoint: dict | None = None
        self.selected_model: str = ""
        self._current_model = current_model
        self.resize(700, 540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 提示标签
        hint = QLabel(f"阶段：{phase_name}　即将发送以下提示词，确认后发送：")
        hint.setObjectName("metaText")
        layout.addWidget(hint)

        # 端点/模型覆盖区
        override_layout = QHBoxLayout()
        override_layout.addWidget(QLabel("端点："))
        self._endpoint_combo = QComboBox()
        self._endpoint_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        # 填充端点下拉
        default_ep_idx = 0
        if endpoints:
            for idx, ep in enumerate(endpoints):
                name = ep.get("name", ep.get("id", ""))
                self._endpoint_combo.addItem(name, ep)
                if ep.get("id", "") == current_endpoint_id:
                    default_ep_idx = idx
        self._endpoint_combo.setCurrentIndex(default_ep_idx)
        self._endpoint_combo.currentIndexChanged.connect(self._on_endpoint_changed)
        override_layout.addWidget(self._endpoint_combo, 1)

        override_layout.addWidget(QLabel("模型："))
        self._model_combo = QComboBox()
        self._model_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        override_layout.addWidget(self._model_combo, 1)
        layout.addLayout(override_layout)

        # 初始填充模型下拉（基于默认选中的端点）
        self._populate_models(self._endpoint_combo.currentData(), current_model)

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

    def _populate_models(self, endpoint: dict | None, prefer_model: str = "") -> None:
        """按回退链填充模型下拉并优先选中 prefer_model。

        回退链：enabled_models → models → [default_model]
        """
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        if endpoint is None:
            self._model_combo.blockSignals(False)
            return
        enabled = endpoint.get("enabled_models") or []
        all_models = endpoint.get("models") or []
        default_model = endpoint.get("default_model", "")
        models_to_show = enabled or all_models or ([default_model] if default_model else [])
        for m in sorted(models_to_show):
            self._model_combo.addItem(m)
        # 优先选中 prefer_model，否则首个
        idx = self._model_combo.findText(prefer_model) if prefer_model else -1
        if idx < 0:
            idx = 0
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        self._model_combo.blockSignals(False)

    def _on_endpoint_changed(self, _index: int) -> None:
        """端点切换：按新端点的模型列表重新填充模型下拉。

        优先选中当前模型（若新端点支持），否则首个。
        """
        endpoint = self._endpoint_combo.currentData()
        self._populate_models(endpoint, self._current_model)

    def get_selected_endpoint(self) -> dict | None:
        """返回当前选中的 endpoint dict。"""
        return self._endpoint_combo.currentData()

    def get_selected_model(self) -> str:
        """返回当前选中的模型名。"""
        return self._model_combo.currentText()

    def _on_send(self) -> None:
        """发送按钮：记录确认结果与选中端点/模型并关闭对话框。"""
        self.confirmed = True
        self.selected_endpoint = self._endpoint_combo.currentData()
        self.selected_model = self._model_combo.currentText()
        self.close()

    def _on_cancel(self) -> None:
        """取消按钮：confirmed 保持 False 并关闭对话框。"""
        self.confirmed = False
        self.close()
