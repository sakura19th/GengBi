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
from novelforge.ui.helpers import parse_token_limit

logger = logging.getLogger(__name__)


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

    def run(self) -> None:
        """在独立事件循环中获取模型列表。"""
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            client = LLMClient(self._base_url, self._api_key, timeout=30.0)
            models = loop.run_until_complete(client.fetch_models())
            self.models_fetched.emit(models)
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

        # 模型选择区
        model_layout = QHBoxLayout()
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setMinimumWidth(200)
        model_layout.addWidget(self._model_combo)

        self._fetch_models_btn = QPushButton("获取模型列表")
        self._fetch_models_btn.clicked.connect(self._on_fetch_models)
        model_layout.addWidget(self._fetch_models_btn)

        form.addRow("默认模型:", model_layout)

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
        self._model_combo.addItem(self._endpoint.get("default_model", ""))
        # 解密 API Key（用于显示和获取模型列表）
        endpoint_id = self._endpoint.get("id", "")
        if endpoint_id:
            self._decrypted_key = self._config_manager.decrypt_api_key(endpoint_id)
        self._default_check.setChecked(
            self._config_manager.get("default_endpoint_id", "") == endpoint_id
        )
        # 若已有 base_url 和解密的 API key，自动拉取模型列表
        if self._endpoint.get("base_url", "").strip() and self._decrypted_key:
            self._on_fetch_models()

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

        self._fetch_models_btn.setEnabled(False)
        self._fetch_models_btn.setText("获取中...")

        self._model_fetch_worker = ModelFetchWorker(base_url, api_key, self)
        self._model_fetch_worker.models_fetched.connect(self._on_models_fetched)
        self._model_fetch_worker.error.connect(self._on_fetch_error)
        self._model_fetch_worker.start()

    def _on_models_fetched(self, models: list) -> None:
        """模型列表获取成功。"""
        self._fetch_models_btn.setEnabled(True)
        self._fetch_models_btn.setText("获取模型列表")

        current = self._model_combo.currentText()
        self._model_combo.clear()
        for m in models:
            self._model_combo.addItem(m)
        if current:
            idx = self._model_combo.findText(current)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
            else:
                self._model_combo.insertItem(0, current)
                self._model_combo.setCurrentIndex(0)

        if not models:
            QMessageBox.information(self, "提示", "未获取到模型列表")

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

        # API Key：如果用户修改了输入框（不是占位符），则用新值
        key_text = self._key_edit.text().strip()
        api_key = ""
        if key_text and key_text != "******":
            api_key = key_text
        elif self._decrypted_key:
            api_key = self._decrypted_key

        self._result = {
            "id": self._endpoint.get("id", f"ep_{os.urandom(4).hex()}"),
            "name": name,
            "base_url": base_url,
            "api_key": api_key,  # 明文，由 ConfigManager 加密
            "default_model": self._model_combo.currentText().strip(),
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
        api_layout.addWidget(self._endpoint_list, 0, 0, 4, 1)

        # 按钮栏
        self._add_btn = QPushButton("新建")
        self._edit_btn = QPushButton("编辑")
        self._delete_btn = QPushButton("删除")
        self._set_default_btn = QPushButton("设为默认")

        api_layout.addWidget(self._add_btn, 0, 1)
        api_layout.addWidget(self._edit_btn, 1, 1)
        api_layout.addWidget(self._delete_btn, 2, 1)
        api_layout.addWidget(self._set_default_btn, 3, 1)

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

        # 上下文提取配置组
        extract_group = QGroupBox("上下文提取")
        extract_form = QFormLayout(extract_group)

        self._extractor_model_edit = QLineEdit()
        self._extractor_model_edit.setPlaceholderText("留空=使用端点默认模型")
        extract_settings = self._config_manager.get_context_extract_settings()
        self._extractor_model_edit.setText(extract_settings.get("extractor_model", ""))
        extract_form.addRow("提取模型:", self._extractor_model_edit)

        # Token 拆分限制（默认值，UI 面板可按次覆盖）
        self._token_limit_combo = QComboBox()
        self._token_limit_combo.addItems(["不限制", "50k", "100k", "250k", "500k"])
        token_limit = extract_settings.get("token_limit", 0)
        if token_limit <= 0:
            self._token_limit_combo.setCurrentText("不限制")
        else:
            self._token_limit_combo.setCurrentText(f"{token_limit // 1000}k")
        self._token_limit_combo.setToolTip(
            "选中章节超出 token 限制时，自动按章节拆分成多次请求。\n"
            "此为默认值，上下文预览面板可按次覆盖。"
        )
        extract_form.addRow("Token 拆分:", self._token_limit_combo)

        layout.addWidget(extract_group)

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
        self._edit_btn.clicked.connect(self._on_edit_endpoint)
        self._delete_btn.clicked.connect(self._on_delete_endpoint)
        self._set_default_btn.clicked.connect(self._on_set_default)
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
                f"默认模型: {ep.get('default_model', '未设置')}"
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

        # 保存上下文提取配置
        extract_settings = self._config_manager.get_context_extract_settings()
        extract_settings["extractor_model"] = self._extractor_model_edit.text().strip()
        # Token 限制
        extract_settings["token_limit"] = parse_token_limit(
            self._token_limit_combo.currentText()
        )
        self._config_manager.config["context_extract"] = extract_settings

        self._config_manager.save()
        self.accept()
