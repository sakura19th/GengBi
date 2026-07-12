"""设置对话框。

包含 API 连接配置：
- API 端点管理（增删改）
- API Key 输入（加密存储，UI 显示 sk-****，需点击显示才可见）
- 默认端点选择
- 从 API 获取模型列表（GET /models），解析 data[].id，失败不阻塞保存
- 外观配置入口（字体设置、历史日志清理）

使用 ``ConfigManager`` 管理配置，``CryptoManager`` 加密 API Key。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from novelforge.core.config import ConfigManager
from novelforge.services.async_runner import AsyncLoopRunner
from novelforge.services.llm_client import LLMClient, LLMError
from novelforge.ui.helpers import parse_token_limit, select_combo_by_id

logger = logging.getLogger(__name__)

# 思考强度选项：(显示文本, 发送值)。「不发送」=空串，表示不写入 payload
REASONING_EFFORT_OPTIONS: list[tuple[str, str]] = [
    ("不发送", ""),
    ("auto", "auto"),
    ("minimal", "minimal"),
    ("low", "low"),
    ("medium", "medium"),
    ("high", "high"),
    ("max", "max"),
]


class ModelFetchWorker(QThread):
    """获取模型列表的工作线程。

    Signals:
        models_fetched(list): 模型 ID 列表
        error(str): 错误信息
    """

    models_fetched = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        base_url: str,
        api_key: str,
        parent=None,
    ) -> None:
        """初始化模型获取线程。"""
        super().__init__(parent)
        self._base_url = base_url
        self._api_key = api_key
        self._stop = False

    def stop(self) -> None:
        """请求停止（非阻塞）。

        设置 ``_stop`` 标志。由于 ``run()`` 使用
        ``loop.run_until_complete`` 阻塞等待 HTTP 响应，此标志不会立即
        中断进行中的请求；线程会在请求完成后自行退出，并通过
        ``finished`` 信号触发 ``deleteLater`` 自清理。
        """
        self._stop = True

    def run(self) -> None:
        """在独立事件循环中获取模型列表。"""
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            client = LLMClient(self._base_url, self._api_key, timeout=30.0)
            try:
                models = loop.run_until_complete(client.fetch_models())
                self.models_fetched.emit(models)
            finally:
                # 关闭 aiohttp ClientSession，避免未关闭连接警告
                loop.run_until_complete(client.close())
        except Exception as e:
            logger.error("获取模型列表失败: %s", e)
            self.error.emit(str(e))
        finally:
            loop.close()


def mask_api_key(key: str) -> str:
    """API Key 脱敏显示。

    Args:
        key: 原始 API Key

    Returns:
        脱敏后的字符串（如 ``sk-****``）
    """
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:3] + "****" + key[-4:]


class EndpointEditDialog(QDialog):
    """API 端点编辑对话框。

    提供端点名称、Base URL、API Key、默认模型的编辑表单。
    支持"获取模型列表"按钮，从 API 拉取可用模型。

    Signals:
        accepted(dict): 确认保存，返回端点数据字典
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        endpoint: dict[str, Any] | None = None,
        parent=None,
    ) -> None:
        """初始化端点编辑对话框。

        Args:
            config_manager: 配置管理器
            endpoint: 待编辑的端点（None 表示新建）
        """
        super().__init__(parent)
        self._config_manager = config_manager
        self._endpoint = endpoint or {}
        self._api_key_visible = False
        self._decrypted_key = ""
        self._model_fetch_worker: ModelFetchWorker | None = None

        self.setWindowTitle("编辑端点" if endpoint else "新建端点")
        self.setMinimumWidth(480)

        self._setup_ui()
        self._load_data()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)

        form = QFormLayout()

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("如：OpenAI 官方")
        form.addRow("名称:", self._name_edit)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("Base URL:", self._url_edit)

        # API Key 输入区
        key_layout = QHBoxLayout()
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("sk-...")
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.setText("******")  # 占位，表示已有加密的 key
        key_layout.addWidget(self._key_edit)

        self._toggle_key_btn = QPushButton("显示")
        self._toggle_key_btn.setCheckable(True)
        self._toggle_key_btn.clicked.connect(self._on_toggle_key_visible)
        key_layout.addWidget(self._toggle_key_btn)

        self._clear_key_btn = QPushButton("清除")
        self._clear_key_btn.clicked.connect(self._on_clear_key)
        key_layout.addWidget(self._clear_key_btn)

        form.addRow("API Key:", key_layout)

        # 模型选择区：可勾选列表（多选启用模型）+ 获取/全选/全不选 + 自定义录入
        model_group = QGroupBox("可用模型")
        model_layout = QVBoxLayout(model_group)
        model_layout.setContentsMargins(2, 2, 2, 2)
        model_layout.setSpacing(2)

        self._model_list = QListWidget()
        self._model_list.setMinimumHeight(120)
        self._model_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        model_layout.addWidget(self._model_list)

        # 按钮行：获取模型列表 / 全选 / 全不选
        btn_row = QHBoxLayout()
        self._fetch_models_btn = QPushButton("获取模型列表")
        self._fetch_models_btn.clicked.connect(self._on_fetch_models)
        btn_row.addWidget(self._fetch_models_btn)

        self._select_all_btn = QPushButton("全选")
        self._select_all_btn.clicked.connect(self._on_select_all_models)
        btn_row.addWidget(self._select_all_btn)

        self._deselect_all_btn = QPushButton("全不选")
        self._deselect_all_btn.clicked.connect(self._on_deselect_all_models)
        btn_row.addWidget(self._deselect_all_btn)
        btn_row.addStretch()
        model_layout.addLayout(btn_row)

        # 自定义模型录入（保留手动添加能力，避免回归）
        custom_row = QHBoxLayout()
        self._custom_model_edit = QLineEdit()
        self._custom_model_edit.setPlaceholderText("手动添加模型名（如自定义端点无 /models）")
        custom_row.addWidget(self._custom_model_edit)
        self._add_custom_btn = QPushButton("添加")
        self._add_custom_btn.clicked.connect(self._on_add_custom_model)
        custom_row.addWidget(self._add_custom_btn)
        model_layout.addLayout(custom_row)

        form.addRow("可用模型:", model_group)

        # 思考强度（reasoning_effort）：OpenAI o 系列/DeepSeek V4 等兼容网关支持
        self._reasoning_effort_combo = QComboBox()
        for label, value in REASONING_EFFORT_OPTIONS:
            self._reasoning_effort_combo.addItem(label, value)
        self._reasoning_effort_combo.setToolTip(
            "思考强度（reasoning_effort），覆盖 OpenAI o 系列"
            "（minimal/low/medium/high）、DeepSeek V4（max）等 OpenAI 兼容网关；\n"
            "「不发送」表示不写入该字段，由模型默认行为决定。\n"
            "不支持该字段的网关会忽略它，无副作用。"
        )
        form.addRow("思考强度:", self._reasoning_effort_combo)

        self._default_check = QCheckBox("设为默认端点")
        form.addRow("", self._default_check)

        layout.addLayout(form)

        # 按钮
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_data(self) -> None:
        """加载端点数据到表单。"""
        if not self._endpoint:
            return
        self._name_edit.setText(self._endpoint.get("name", ""))
        self._url_edit.setText(self._endpoint.get("base_url", ""))
        # 模型列表：全部已获取 models（回退到 default_model）；勾选状态由 enabled_models 决定
        saved_models = self._endpoint.get("models") or []
        default_model = self._endpoint.get("default_model", "")
        enabled_models = self._endpoint.get("enabled_models") or []
        if not saved_models and default_model:
            saved_models = [default_model]
        # 勾选规则：enabled_models 非空→勾选其中的；为空→全部勾选（旧端点兼容）
        checked_set = set(enabled_models) if enabled_models else set(saved_models)
        for m in sorted(saved_models):
            self._add_model_list_item(m, checked=m in checked_set)
        # 解密 API Key（用于显示和获取模型列表）
        endpoint_id = self._endpoint.get("id", "")
        if endpoint_id:
            self._decrypted_key = self._config_manager.decrypt_api_key(endpoint_id)
        self._default_check.setChecked(
            self._config_manager.get("default_endpoint_id", "") == endpoint_id
        )
        # 思考强度：按保存值选中对应项
        select_combo_by_id(
            self._reasoning_effort_combo, self._endpoint.get("reasoning_effort", "")
        )
        # 若已有 base_url 和解密的 API key，自动拉取模型列表
        if self._endpoint.get("base_url", "").strip() and self._decrypted_key:
            self._on_fetch_models()

    def closeEvent(self, event) -> None:
        """关闭对话框时清理模型获取线程，避免回调到已销毁的 UI。"""
        worker = self._model_fetch_worker
        if worker is not None:
            try:
                still_running = worker.isRunning()
            except RuntimeError:
                # finished→deleteLater 已删除 C++ 对象，Python wrapper 失效
                still_running = False
            if still_running:
                try:
                    worker.models_fetched.disconnect()
                    worker.error.disconnect()
                except (RuntimeError, TypeError):
                    pass  # 信号可能已断开
                try:
                    worker.stop()
                    worker.wait(2000)
                except (RuntimeError, AttributeError):
                    pass
                # worker parent=None + finished→deleteLater，可安全在后台完成并自清理
        super().closeEvent(event)

    def _on_toggle_key_visible(self, checked: bool) -> None:
        """切换 API Key 显示。"""
        self._api_key_visible = checked
        if checked:
            self._key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self._toggle_key_btn.setText("隐藏")
            # 显示解密后的 key（如果有）
            if self._decrypted_key and self._key_edit.text() == "******":
                self._key_edit.setText(self._decrypted_key)
        else:
            self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._toggle_key_btn.setText("显示")

    def _on_clear_key(self) -> None:
        """清除 API Key 输入。"""
        self._key_edit.clear()
        self._decrypted_key = ""

    def _on_fetch_models(self) -> None:
        """获取模型列表。"""
        base_url = self._url_edit.text().strip()
        if not base_url:
            QMessageBox.warning(self, "提示", "请先输入 Base URL")
            return

        # 获取 API Key：优先用输入框的值，否则用解密的值
        api_key = self._key_edit.text().strip()
        if not api_key or api_key == "******":
            api_key = self._decrypted_key
        if not api_key:
            QMessageBox.warning(self, "提示", "请先输入 API Key")
            return

        # 停止前一个 worker（若仍在运行）
        prev = self._model_fetch_worker
        if prev is not None:
            try:
                if prev.isRunning():
                    prev.stop()
                    prev.wait(2000)
            except (RuntimeError, AttributeError):
                pass

        self._fetch_models_btn.setEnabled(False)
        self._fetch_models_btn.setText("获取中...")

        # parent=None：解耦对话框生命周期，避免对话框关闭时 QThread 被强制销毁
        self._model_fetch_worker = ModelFetchWorker(base_url, api_key, None)
        self._model_fetch_worker.models_fetched.connect(self._on_models_fetched)
        self._model_fetch_worker.error.connect(self._on_fetch_error)
        # 线程结束后自动清理（fire-and-forget，不随对话框销毁）
        self._model_fetch_worker.finished.connect(self._model_fetch_worker.deleteLater)
        self._model_fetch_worker.start()

    def _on_models_fetched(self, models: list) -> None:
        """模型列表获取成功。"""
        self._fetch_models_btn.setEnabled(True)
        self._fetch_models_btn.setText("获取模型列表")

        # 记录现有勾选状态（拉取前已勾选的模型保留勾选）
        checked_set = {
            self._model_list.item(i).text()
            for i in range(self._model_list.count())
            if self._model_list.item(i).checkState() == Qt.CheckState.Checked
        }
        self._model_list.clear()
        for m in sorted(models):
            # 已勾选的保留勾选；新模型默认勾选（拉取即可用）
            self._add_model_list_item(m, checked=True)
        # 恢复旧勾选状态（取消之前未勾选的）
        if checked_set:
            for i in range(self._model_list.count()):
                it = self._model_list.item(i)
                if it.text() not in checked_set:
                    it.setCheckState(Qt.CheckState.Unchecked)

        if not models:
            QMessageBox.information(self, "提示", "未获取到模型列表")

    def _add_model_list_item(self, name: str, checked: bool = True) -> None:
        """向模型列表追加一个可勾选项。"""
        name = (name or "").strip()
        if not name:
            return
        # 去重
        for i in range(self._model_list.count()):
            if self._model_list.item(i).text() == name:
                return
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._model_list.addItem(item)

    def _on_select_all_models(self) -> None:
        """全选模型。"""
        for i in range(self._model_list.count()):
            self._model_list.item(i).setCheckState(Qt.CheckState.Checked)

    def _on_deselect_all_models(self) -> None:
        """全不选模型。"""
        for i in range(self._model_list.count()):
            self._model_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _on_add_custom_model(self) -> None:
        """手动添加自定义模型名（默认勾选）。"""
        name = self._custom_model_edit.text().strip()
        if not name:
            return
        self._add_model_list_item(name, checked=True)
        self._custom_model_edit.clear()

    def _on_fetch_error(self, error: str) -> None:
        """模型列表获取失败。"""
        self._fetch_models_btn.setEnabled(True)
        self._fetch_models_btn.setText("获取模型列表")
        QMessageBox.warning(
            self,
            "获取失败",
            f"获取模型列表失败：{error}\n\n不影响端点保存。",
        )

    def _on_accept(self) -> None:
        """确认保存。"""
        name = self._name_edit.text().strip()
        base_url = self._url_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入端点名称")
            return
        if not base_url:
            QMessageBox.warning(self, "提示", "请输入 Base URL")
            return

        # 校验 Base URL 格式：必须是 http:// 或 https:// 开头
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            QMessageBox.warning(
                self,
                "提示",
                "Base URL 必须以 http:// 或 https:// 开头\n例: https://api.openai.com/v1",
            )
            return
        base_url = base_url.rstrip("/")

        # API Key：如果用户修改了输入框（不是占位符），则用新值
        key_text = self._key_edit.text().strip()
        api_key = ""
        if key_text and key_text != "******":
            api_key = key_text
        elif self._decrypted_key:
            api_key = self._decrypted_key

        # 收集模型列表：全部 item = models；勾选项 = enabled_models
        # default_model 自动取首个已启用（sorted），供后台流程回退
        models: list[str] = []
        enabled_models: list[str] = []
        for i in range(self._model_list.count()):
            m = self._model_list.item(i).text().strip()
            if m and m not in models:
                models.append(m)
            if self._model_list.item(i).checkState() == Qt.CheckState.Checked and m and m not in enabled_models:
                enabled_models.append(m)
        default_model = sorted(enabled_models)[0] if enabled_models else ""

        self._result = {
            "id": self._endpoint.get("id", f"ep_{os.urandom(4).hex()}"),
            "name": name,
            "base_url": base_url,
            "api_key": api_key,  # 明文，由 ConfigManager 加密
            "default_model": default_model,
            "models": models,
            "enabled_models": enabled_models,
            "reasoning_effort": self._reasoning_effort_combo.currentData(),
            "is_default": self._default_check.isChecked(),
        }
        self.accept()

    def get_result(self) -> dict[str, Any]:
        """获取编辑结果。"""
        return getattr(self, "_result", {})


class SettingsDialog(QDialog):
    """设置对话框。

    包含 API 端点管理、外观配置等设置页。

    Usage::

        dialog = SettingsDialog(config_manager, parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # 配置已保存
            pass
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        parent=None,
        storage_service: Any = None,
        history_service: Any = None,
    ) -> None:
        """初始化设置对话框。

        Args:
            config_manager: 配置管理器
            parent: 父控件
            storage_service: 存储服务（可选，用于历史日志清理）
            history_service: 历史日志服务（可选，用于历史日志清理）
        """
        super().__init__(parent)
        self._config_manager = config_manager
        self._storage_service = storage_service
        self._history_service = history_service

        self.setWindowTitle("设置")
        self.setMinimumSize(640, 560)

        self._setup_ui()
        self._load_endpoints()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)

        # API 端点管理组
        api_group = QGroupBox("API 端点管理")
        api_layout = QGridLayout(api_group)

        # 端点列表
        self._endpoint_list = QListWidget()
        api_layout.addWidget(self._endpoint_list, 0, 0, 5, 1)

        # 按钮栏
        self._add_btn = QPushButton("新建")
        self._edit_btn = QPushButton("编辑")
        self._delete_btn = QPushButton("删除")
        self._set_default_btn = QPushButton("设为默认")
        self._duplicate_btn = QPushButton("复制")

        api_layout.addWidget(self._add_btn, 0, 1)
        api_layout.addWidget(self._edit_btn, 1, 1)
        api_layout.addWidget(self._delete_btn, 2, 1)
        api_layout.addWidget(self._set_default_btn, 3, 1)
        api_layout.addWidget(self._duplicate_btn, 4, 1)

        layout.addWidget(api_group)

        # 续写默认配置组
        cont_group = QGroupBox("续写默认配置")
        cont_form = QFormLayout(cont_group)

        self._lookback_spin = QSpinBox()
        self._lookback_spin.setRange(0, 99999)
        self._lookback_spin.setSpecialValueText("全部前文")
        self._lookback_spin.setValue(
            self._config_manager.get_continuation_settings().get(
                "default_lookback_chapters", 5
            )
        )
        cont_form.addRow("回溯章节数:", self._lookback_spin)

        self._temp_spin = QDoubleSpinBox()
        self._temp_spin.setRange(0.0, 2.0)
        self._temp_spin.setSingleStep(0.1)
        self._temp_spin.setValue(
            self._config_manager.get_continuation_settings().get(
                "default_temperature", 0.8
            )
        )
        cont_form.addRow("温度:", self._temp_spin)

        layout.addWidget(cont_group)

        # M5: 外观与维护组
        appearance_group = QGroupBox("外观与维护")
        appearance_layout = QFormLayout(appearance_group)

        # 当前字体信息显示
        appearance = self._config_manager.get_appearance()
        font_family = appearance.get("font_family", "默认")
        font_size = appearance.get("font_size", 14)
        line_height = appearance.get("line_height", 1.6)
        self._font_info_label = QLabel(
            f"{font_family} {font_size}pt, 行距 {line_height:.2f}"
        )
        appearance_layout.addRow("当前字体:", self._font_info_label)

        self._font_settings_btn = QPushButton("打开字体设置...")
        self._font_settings_btn.clicked.connect(self._on_open_font_settings)
        appearance_layout.addRow("", self._font_settings_btn)

        # 历史日志清理
        self._clear_history_btn = QPushButton("清空续写历史日志...")
        self._clear_history_btn.clicked.connect(self._on_clear_history)
        appearance_layout.addRow("历史日志:", self._clear_history_btn)

        layout.addWidget(appearance_group)

        # 按钮区
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # 连接信号
        self._add_btn.clicked.connect(self._on_add_endpoint)
        # 用 lambda 包裹：避免 clicked(bool) 把 False 传给 item 参数
        # （False is not None，但 not False 为 True，会导致提前 return）
        self._edit_btn.clicked.connect(lambda: self._on_edit_endpoint())
        self._delete_btn.clicked.connect(self._on_delete_endpoint)
        self._set_default_btn.clicked.connect(self._on_set_default)
        self._duplicate_btn.clicked.connect(self._on_duplicate_endpoint)
        self._endpoint_list.itemDoubleClicked.connect(self._on_edit_endpoint)

    def _load_endpoints(self) -> None:
        """加载端点列表。"""
        self._endpoint_list.clear()
        default_id = self._config_manager.get("default_endpoint_id", "")
        for ep in self._config_manager.get_endpoints():
            name = ep.get("name", ep.get("id", ""))
            is_default = ep.get("id") == default_id
            label = f"{'★ ' if is_default else '  '}{name}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, ep)
            item.setToolTip(
                f"名称: {name}\n"
                f"URL: {ep.get('base_url', '')}\n"
                f"API Key: {mask_api_key(ep.get('api_key_encrypted', ''))}\n"
                f"已启用模型: {len(ep.get('enabled_models', []) or [])} 个"
                f"（默认: {ep.get('default_model', '未设置')}）\n"
                f"思考强度: {ep.get('reasoning_effort', '') or '不发送'}"
            )
            self._endpoint_list.addItem(item)

    def _on_add_endpoint(self) -> None:
        """新建端点。"""
        dialog = EndpointEditDialog(self._config_manager, None, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            result = dialog.get_result()
            self._config_manager.add_endpoint(result)
            if result.get("is_default"):
                self._config_manager.set_default_endpoint(result["id"])
            self._load_endpoints()

    def _on_edit_endpoint(self, item=None) -> None:
        """编辑端点。"""
        if item is None:
            item = self._endpoint_list.currentItem()
        if not item:
            return
        ep = item.data(Qt.ItemDataRole.UserRole)
        dialog = EndpointEditDialog(self._config_manager, ep, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            result = dialog.get_result()
            endpoint_id = result.pop("id", ep.get("id"))
            is_default = result.pop("is_default", False)
            self._config_manager.update_endpoint(endpoint_id, result)
            if is_default:
                self._config_manager.set_default_endpoint(endpoint_id)
            self._load_endpoints()

    def _on_delete_endpoint(self) -> None:
        """删除端点。"""
        item = self._endpoint_list.currentItem()
        if not item:
            return
        ep = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self,
            "删除端点",
            f"确定删除端点「{ep.get('name', ep.get('id'))}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._config_manager.remove_endpoint(ep.get("id"))
            self._load_endpoints()

    def _on_set_default(self) -> None:
        """设为默认端点。"""
        item = self._endpoint_list.currentItem()
        if not item:
            return
        ep = item.data(Qt.ItemDataRole.UserRole)
        self._config_manager.set_default_endpoint(ep.get("id"))
        self._load_endpoints()

    def _on_duplicate_endpoint(self) -> None:
        """复制当前选中的端点配置。"""
        item = self._endpoint_list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先选择一个端点。")
            return
        ep = item.data(Qt.ItemDataRole.UserRole)
        if not ep:
            return

        from PySide6.QtWidgets import QInputDialog

        default_name = f"{ep.get('name', '')} 副本"
        name, ok = QInputDialog.getText(
            self, "复制端点", "新端点名称:", text=default_name,
        )
        if not ok or not name.strip():
            return

        # 深拷贝端点数据，生成新 ID，复用 api_key_encrypted 密文
        new_ep = {
            "id": f"ep_{os.urandom(4).hex()}",
            "name": name.strip(),
            "base_url": ep.get("base_url", ""),
            "api_key_encrypted": ep.get("api_key_encrypted", ""),
            "default_model": ep.get("default_model", ""),
            "models": list(ep.get("models", [])),
            "enabled_models": list(ep.get("enabled_models", [])),
            "reasoning_effort": ep.get("reasoning_effort", ""),
            "is_default": False,
        }
        try:
            self._config_manager.add_endpoint(new_ep)
            self._load_endpoints()
            # 选中新复制的端点
            for i in range(self._endpoint_list.count()):
                it = self._endpoint_list.item(i)
                if it.data(Qt.ItemDataRole.UserRole).get("id") == new_ep["id"]:
                    self._endpoint_list.setCurrentRow(i)
                    break
        except Exception as e:
            QMessageBox.critical(self, "错误", f"复制端点失败: {e}")

    def _on_open_font_settings(self) -> None:
        """打开字体设置对话框。"""
        from novelforge.ui.font_settings import FontSettingsDialog

        dialog = FontSettingsDialog(self._config_manager, self)
        if dialog.exec() == FontSettingsDialog.DialogCode.Accepted:
            # 刷新字体信息显示
            appearance = self._config_manager.get_appearance()
            font_family = appearance.get("font_family", "默认")
            font_size = appearance.get("font_size", 14)
            line_height = appearance.get("line_height", 1.6)
            self._font_info_label.setText(
                f"{font_family} {font_size}pt, 行距 {line_height:.2f}"
            )

    def _on_clear_history(self) -> None:
        """清空续写历史日志。"""
        if self._history_service is None:
            QMessageBox.information(
                self, "提示", "历史日志服务未初始化，请通过工具菜单清空。"
            )
            return

        reply = QMessageBox.question(
            self,
            "清空历史日志",
            "确定清空所有续写历史日志？\n\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self._history_service.clear_history()
            QMessageBox.information(self, "成功", "已清空所有续写历史日志")
        except Exception as e:
            logger.error("清空历史日志失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "失败", f"清空历史日志失败: {e}")

    def _on_accept(self) -> None:
        """确认保存。"""
        # 保存续写配置
        cont_settings = self._config_manager.get_continuation_settings()
        cont_settings["default_lookback_chapters"] = self._lookback_spin.value()
        cont_settings["default_temperature"] = self._temp_spin.value()
        self._config_manager.config["continuation"] = cont_settings

        self._config_manager.save()
        self.accept()
