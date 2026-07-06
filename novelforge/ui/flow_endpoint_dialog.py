"""流程端点配置对话框。

列出全部 8 个 LLM 流程，允许用户为每个流程选择使用的 API 端点。
默认使用端点管理中的默认端点（首项），也可选择其它已配置端点。
配置持久化到 ``config["flow_endpoints"]``（``{flow_key: endpoint_id}``），
由 ``ConfigManager.get_flow_endpoint(flow_key)`` 解析（未配置或端点被删则回退默认端点）。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from novelforge.core.config import ConfigManager
from novelforge.ui.helpers import select_combo_by_id

# 流程清单：(flow_key, 显示名)
FLOW_DEFINITIONS: list[tuple[str, str]] = [
    ("single_continuation", "单章续写"),
    ("volume_continuation", "卷续写"),
    ("single_audit", "单章审计"),
    ("rewrite_analysis", "重写当前章节分析"),
    ("context_extraction", "上下文提取"),
    ("ontology_extraction", "世界观底层提取"),
    ("protagonist_extraction", "主角形象提取"),
    ("custom_rule_parsing", "自定义设定解析"),
]


class FlowEndpointDialog(QDialog):
    """流程端点配置对话框。

    为每个流程提供一个端点下拉框，首项为「默认端点（{名称}）」，
    其余项为已配置的端点。保存时收集所有下拉的 currentData() 写入
    ``config["flow_endpoints"]``。

    Usage::

        dialog = FlowEndpointDialog(config_manager, parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # 配置已保存
            pass
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        parent: QWidget | None = None,
    ) -> None:
        """初始化流程端点配置对话框。

        Args:
            config_manager: 配置管理器（用于读取/保存流程端点映射与端点列表）
            parent: 父控件
        """
        super().__init__(parent)
        self._config_manager = config_manager
        self._combos: dict[str, QComboBox] = {}

        self.setWindowTitle("流程端点配置")
        self.setMinimumWidth(460)

        self._setup_ui()
        self._load_data()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)

        # 说明标签
        hint = QLabel(
            "为每个流程选择使用的 API 端点。「默认端点」使用端点管理中的默认选项；\n"
            "也可选择其它已配置端点。流程指定即生效（续写/卷续写面板仍可临时覆盖）。"
        )
        hint.setObjectName("metaText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()

        endpoints = self._config_manager.get_endpoints()
        default_ep = self._config_manager.get_default_endpoint()
        default_name = default_ep.get("name", default_ep.get("id", "未配置")) if default_ep else "未配置"
        default_label = f"默认端点（{default_name}）"

        for flow_key, flow_name in FLOW_DEFINITIONS:
            combo = QComboBox()
            # 首项：默认端点（itemData="" 表示回退默认）
            combo.addItem(default_label, "")
            # 其余项：所有端点
            for ep in endpoints:
                name = ep.get("name", ep.get("id", ""))
                combo.addItem(name, ep.get("id", ""))
            self._combos[flow_key] = combo
            form.addRow(f"{flow_name}:", combo)

        layout.addLayout(form)

        # 按钮区
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_data(self) -> None:
        """加载已保存的流程端点映射并选中对应项。"""
        mapping = self._config_manager.get_flow_endpoints()
        for flow_key, combo in self._combos.items():
            saved_id = mapping.get(flow_key, "")
            select_combo_by_id(combo, saved_id)

    def _on_accept(self) -> None:
        """确认保存：收集所有下拉值并持久化。"""
        mapping: dict[str, str] = {}
        for flow_key, combo in self._combos.items():
            data = combo.currentData()
            mapping[flow_key] = data if isinstance(data, str) else ""
        self._config_manager.set_flow_endpoints(mapping)
        self.accept()
