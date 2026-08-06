"""流程端点配置对话框。

列出全部 10 个 LLM 流程，允许用户为每个流程选择使用的 API 端点。
默认使用端点管理中的默认端点（首项），也可选择其它已配置端点。
配置持久化到 ``config["flow_endpoints"]``（``{flow_key: endpoint_id}``），
由 ``ConfigManager.get_flow_endpoint(flow_key)`` 解析（未配置或端点被删则回退默认端点）。

另为 8 个非正文流程（除 single/volume continuation 外）提供破限配置：
每流程选破限等级（关闭/低/中/高/自定义），自定义可编辑文本。配置持久化到
``config["flow_endpoints"]`` 与 ``config["flow_jailbreaks"]``/``flow_jailbreaks_custom``。
正文流程的破限由预设管理器勾选 ``nf_jb_*`` 模块控制，不在此对话框配置。
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from novelforge.core.config import ConfigManager
from novelforge.ui.helpers import select_combo_by_id
from novelforge.ui.jailbreak_custom_dialog import JailbreakCustomDialog

# 流程清单：(flow_key, 显示名)
FLOW_DEFINITIONS: list[tuple[str, str]] = [
    ("single_continuation", "单章续写"),
    ("volume_continuation", "卷续写"),
    ("single_audit", "单章审计"),
    ("rewrite_analysis", "重写当前章节分析"),
    ("context_extraction", "上下文提取"),
    ("ontology_extraction", "世界观底层提取"),
    ("protagonist_extraction", "主角形象提取"),
    ("custom_character_extraction", "自定义角色提取"),
    ("style_extraction", "文风档案提取"),
    ("custom_rule_parsing", "自定义设定解析"),
    ("writing_element_analysis", "写作要素分析"),
    ("writing_element_refinement", "写作要素深化"),
    ("planned_writing_outline", "规划写作细纲生成"),
]

# 正文流程（破限由预设控制，不在本对话框配置破限）
MAIN_FLOWS: set[str] = {"single_continuation", "volume_continuation"}

# 破限等级下拉项：(显示名, level 值)
JAILBREAK_LEVEL_ITEMS: list[tuple[str, str]] = [
    ("关闭", "off"),
    ("低", "low"),
    ("中", "mid"),
    ("高", "high"),
    ("自定义", "custom"),
]


class FlowEndpointDialog(QDialog):
    """流程端点配置对话框。

    为每个流程提供一个端点下拉框，首项为「默认端点（{名称}）」，
    其余项为已配置的端点。保存时收集所有下拉的 currentData() 写入
    ``config["flow_endpoints"]``。

    另为 8 个非正文流程提供破限等级下拉 + 自定义编辑按钮，写入
    ``config["flow_jailbreaks"]`` 与 ``config["flow_jailbreaks_custom"]``。

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
        self._endpoint_combos: dict[str, QComboBox] = {}
        self._model_combos: dict[str, QComboBox] = {}
        self._jb_combos: dict[str, QComboBox] = {}
        self._jb_buttons: dict[str, QPushButton] = {}
        # 暂存自定义文本（未保存前在内存，确认时一并写盘）
        self._custom_texts: dict[str, str] = {}
        # 已保存的流程模型映射（加载时填充，供 _populate_model_combo 选中）
        self._saved_model_ids: dict[str, str] = {}

        self.setWindowTitle("流程端点配置")
        self.setMinimumWidth(520)

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

        endpoints = self._config_manager.get_endpoints()
        default_ep = self._config_manager.get_default_endpoint()
        default_name = default_ep.get("name", default_ep.get("id", "未配置")) if default_ep else "未配置"
        default_label = f"默认端点（{default_name}）"

        # 端点与模型（可折叠，默认展开）
        ep_group = QGroupBox("端点与模型")
        ep_content = QWidget()
        endpoint_form = QFormLayout(ep_content)
        for flow_key, flow_name in FLOW_DEFINITIONS:
            ep_combo = QComboBox()
            # 首项：默认端点（itemData="" 表示回退默认）
            ep_combo.addItem(default_label, "")
            # 其余项：所有端点
            for ep in endpoints:
                name = ep.get("name", ep.get("id", ""))
                ep_combo.addItem(name, ep.get("id", ""))
            self._endpoint_combos[flow_key] = ep_combo

            # 模型下拉：首项「默认模型」itemData=""，端点切换时动态填充
            model_combo = QComboBox()
            model_combo.setMinimumWidth(180)
            model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            model_combo.addItem("默认模型", "")
            self._model_combos[flow_key] = model_combo

            # 端点切换时重新填充模型下拉
            ep_combo.currentIndexChanged.connect(
                lambda _idx, k=flow_key: self._on_flow_endpoint_changed(k)
            )

            row = QHBoxLayout()
            row.addWidget(ep_combo, 1)
            row.addWidget(model_combo, 1)
            endpoint_form.addRow(f"{flow_name}:", row)

        ep_group_layout = QVBoxLayout(ep_group)
        ep_group_layout.setContentsMargins(4, 4, 4, 4)
        ep_group_layout.addWidget(ep_content)
        self._bind_collapsible_section(ep_group, ep_content, expanded=True)
        layout.addWidget(ep_group)

        # 破限配置（可折叠，默认折叠；仅非正文流程）
        jb_group = QGroupBox("破限配置（非正文流程）")
        jb_content = QWidget()
        jb_layout = QFormLayout(jb_content)

        jb_hint = QLabel(
            "为非正文流程选择破限等级。等级文本作为 system 消息前置到此流程 messages 开头。\n"
            "「自定义」可编辑专属文本；正文流程的破限在预设管理器勾选 nf_jb_* 模块控制。"
        )
        jb_hint.setObjectName("metaText")
        jb_hint.setWordWrap(True)
        jb_layout.addRow(jb_hint)

        for flow_key, flow_name in FLOW_DEFINITIONS:
            if flow_key in MAIN_FLOWS:
                continue
            # 破限等级下拉
            jb_combo = QComboBox()
            for display, level in JAILBREAK_LEVEL_ITEMS:
                jb_combo.addItem(display, level)
            self._jb_combos[flow_key] = jb_combo

            # 自定义编辑按钮
            edit_btn = QPushButton("编辑自定义")
            edit_btn.setEnabled(False)
            edit_btn.clicked.connect(lambda _checked, k=flow_key: self._edit_custom(k))
            self._jb_buttons[flow_key] = edit_btn

            # 等级变化时启/禁用按钮
            jb_combo.currentIndexChanged.connect(
                lambda _idx, k=flow_key: self._on_jb_level_changed(k)
            )

            row = QHBoxLayout()
            row.addWidget(jb_combo, 1)
            row.addWidget(edit_btn)
            jb_layout.addRow(f"{flow_name}:", row)

        jb_group_layout = QVBoxLayout(jb_group)
        jb_group_layout.setContentsMargins(4, 4, 4, 4)
        jb_group_layout.addWidget(jb_content)
        self._bind_collapsible_section(jb_group, jb_content, expanded=False)
        layout.addWidget(jb_group)

        # 按钮区
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _bind_collapsible_section(
        self,
        group: QGroupBox,
        content: QWidget,
        *,
        expanded: bool,
    ) -> None:
        """将 GroupBox 绑为可勾选折叠段，折叠时外框随内容收缩。

        Args:
            group: 外层分组框
            content: 可显隐的内容容器
            expanded: 初始是否展开
        """
        group.setCheckable(True)
        group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        group.toggled.connect(
            lambda checked, g=group, c=content: self._on_section_toggled(g, c, checked)
        )
        group.setChecked(expanded)
        # setChecked 在已是目标态时可能不发 toggled，显式同步一次
        self._on_section_toggled(group, content, expanded)

    def _on_section_toggled(
        self,
        group: QGroupBox,
        content: QWidget,
        checked: bool,
    ) -> None:
        """展开/折叠分段：折叠压到标题栏；对话框高度收紧到内容，宽度仍可拖。"""
        content.setVisible(checked)
        gl = group.layout()
        if checked:
            if gl is not None:
                gl.setContentsMargins(4, 4, 4, 4)
            group.setMinimumHeight(0)
            group.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
        else:
            if gl is not None:
                gl.setContentsMargins(0, 0, 0, 0)
            # 标题栏高度；不用 minimumSizeHint（隐藏后常仍偏大）
            title_h = max(group.fontMetrics().height() + 20, 28)
            group.setFixedHeight(title_h)
        self._fit_dialog_height()
        # 等布局结算后再收一次，避免 sizeHint 仍是展开态
        QTimer.singleShot(0, self._fit_dialog_height)

    def _fit_dialog_height(self) -> None:
        """按内容把对话框高度收到最小，保留当前宽度（不锁宽）。"""
        lay = self.layout()
        if lay is None:
            return
        w = max(self.width(), self.minimumWidth())
        # 先解除高度锁，才能算出真实内容高度
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        lay.invalidate()
        lay.activate()
        h = lay.totalSizeHint().height()
        h = max(h, lay.totalMinimumSize().height())
        self.resize(w, h)
        # 高度锁到内容，避免大窗外框留白；宽度不 setFixedWidth
        self.setFixedHeight(h)

    def _load_data(self) -> None:
        """加载已保存的流程端点/模型映射与破限配置并选中对应项。"""
        # 端点
        mapping = self._config_manager.get_flow_endpoints()
        for flow_key, combo in self._endpoint_combos.items():
            saved_id = mapping.get(flow_key, "")
            select_combo_by_id(combo, saved_id)

        # 模型映射（暂存供 _populate_model_combo 选中）
        self._saved_model_ids = dict(self._config_manager.get_flow_models())
        # 对每个流程根据当前选中端点填充模型下拉并选中已保存模型
        for flow_key, model_combo in self._model_combos.items():
            ep_combo = self._endpoint_combos[flow_key]
            ep_id = ep_combo.currentData() if ep_combo.count() else ""
            self._populate_model_combo(flow_key, ep_id)

        # 破限等级与自定义文本
        jb_mapping = self._config_manager.get_flow_jailbreaks()
        for flow_key, combo in self._jb_combos.items():
            saved_level = jb_mapping.get(flow_key) or self._config_manager.get_flow_jailbreak(flow_key)
            select_combo_by_id(combo, saved_level)
            # 预载自定义文本到暂存
            self._custom_texts[flow_key] = self._config_manager.get_flow_jailbreak_custom(flow_key)
            self._on_jb_level_changed(flow_key)

    def _on_flow_endpoint_changed(self, flow_key: str) -> None:
        """流程端点切换时重新填充对应模型下拉。

        保留已保存模型选中状态（若新端点中存在），否则回退默认项。
        """
        ep_combo = self._endpoint_combos[flow_key]
        ep_id = ep_combo.currentData() if ep_combo.count() else ""
        self._populate_model_combo(flow_key, ep_id)

    def _populate_model_combo(self, flow_key: str, endpoint_id: str) -> None:
        """根据端点 ID 填充流程的模型下拉。

        取端点（空 id 用默认端点），用回退链 enabled_models → models → [default_model]
        填充模型项；首项「默认模型（{default_model}）」itemData="" 表示回退端点默认模型。
        选中已保存模型（``_saved_model_ids[flow_key]``），找不到则选默认项。

        Args:
            flow_key: 流程标识
            endpoint_id: 端点 ID，空串表示默认端点
        """
        model_combo = self._model_combos[flow_key]
        ep = (
            self._config_manager.get_endpoint(endpoint_id)
            if endpoint_id
            else self._config_manager.get_default_endpoint()
        )
        default_model = ep.get("default_model", "") if ep else ""
        enabled = ep.get("enabled_models") or [] if ep else []
        all_models = ep.get("models") or [] if ep else []
        # 回退链：enabled_models → models → [default_model]（旧端点兼容）
        models_to_show = enabled or all_models or ([default_model] if default_model else [])

        model_combo.blockSignals(True)
        model_combo.clear()
        # 首项：默认模型（itemData="" 表示回退端点 default_model）
        default_label = f"默认模型（{default_model}）" if default_model else "默认模型"
        model_combo.addItem(default_label, "")
        # 其余项：模型列表按名称排序
        for m in sorted(models_to_show):
            if m and m != default_model:
                model_combo.addItem(m, m)
        # 选中已保存模型，找不到则选默认项
        saved_model = self._saved_model_ids.get(flow_key, "")
        target_idx = 0
        if saved_model:
            for i in range(model_combo.count()):
                if model_combo.itemData(i) == saved_model:
                    target_idx = i
                    break
        model_combo.setCurrentIndex(target_idx)
        model_combo.blockSignals(False)

    def _on_jb_level_changed(self, flow_key: str) -> None:
        """破限等级变化时启/禁用自定义编辑按钮。"""
        combo = self._jb_combos[flow_key]
        level = combo.currentData()
        self._jb_buttons[flow_key].setEnabled(level == "custom")

    def _edit_custom(self, flow_key: str) -> None:
        """打开自定义破限文本编辑对话框。"""
        flow_name = dict(FLOW_DEFINITIONS).get(flow_key, flow_key)
        initial = self._custom_texts.get(flow_key, "")
        dialog = JailbreakCustomDialog(flow_name, initial, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._custom_texts[flow_key] = dialog.get_text()

    def _on_accept(self) -> None:
        """确认保存：收集所有下拉值并持久化。"""
        # 端点映射
        endpoint_mapping: dict[str, str] = {}
        for flow_key, combo in self._endpoint_combos.items():
            data = combo.currentData()
            endpoint_mapping[flow_key] = data if isinstance(data, str) else ""
        self._config_manager.set_flow_endpoints(endpoint_mapping)

        # 模型映射（空串=用端点 default_model）
        model_mapping: dict[str, str] = {}
        for flow_key, combo in self._model_combos.items():
            data = combo.currentData()
            model_mapping[flow_key] = data if isinstance(data, str) else ""
        self._config_manager.set_flow_models(model_mapping)

        # 破限等级映射
        jb_mapping: dict[str, str] = {}
        for flow_key, combo in self._jb_combos.items():
            data = combo.currentData()
            jb_mapping[flow_key] = data if isinstance(data, str) else "off"
        self._config_manager.set_flow_jailbreaks(jb_mapping)

        # 破限自定义文本映射
        jb_custom_mapping: dict[str, str] = {}
        for flow_key, text in self._custom_texts.items():
            jb_custom_mapping[flow_key] = text
        self._config_manager.set_flow_jailbreaks_custom(jb_custom_mapping)

        self.accept()
