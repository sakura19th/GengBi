"""HTTP 代理设置（network 配置组）测试。

覆盖：
- ConfigManager.get_default_config() 含 network 分组且默认值正确
- ConfigManager.get_network_settings / set_network_settings 往返
- 旧配置（无 network 字段）get_network_settings 返回默认值不崩溃
- LLMClient(proxy=...) 构造后 self.proxy 正确（含 None/空串/空白串/带空白）
- SettingsDialog「网络代理」分组 UI 存在（QCheckBox + QLineEdit）
- SettingsDialog 保存后 config_manager.get_network_settings 反映 UI 输入
- ModelFetchWorker 接收并保存 proxy 参数
- MainWindow._get_network_proxy 读取逻辑（开关 on/off × URL 空/非空组合）
- MainWindow 7 处 worker 实例化点静态扫描，确保每个构造调用透传 proxy（回归防护）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit

from novelforge.core.config import ConfigManager, get_default_config
from novelforge.services.llm_client import LLMClient
from novelforge.ui.settings_dialog import ModelFetchWorker, SettingsDialog


# ===== 配置层测试 =====


def test_default_config_contains_network_group() -> None:
    """get_default_config() 含 network 分组且默认 proxy_enabled=False/http_proxy=""。"""
    cfg = get_default_config()
    assert "network" in cfg
    network = cfg["network"]
    assert network["proxy_enabled"] is False
    assert network["http_proxy"] == ""


def test_get_network_settings_default(tmp_path: Path) -> None:
    """新配置的 get_network_settings() 返回默认值。"""
    cm = ConfigManager(tmp_path / "config.json")
    network = cm.get_network_settings()
    assert network == {"proxy_enabled": False, "http_proxy": ""}


def test_set_network_settings_roundtrip(tmp_path: Path) -> None:
    """set 后 get 一致，且持久化到磁盘。"""
    cm = ConfigManager(tmp_path / "config.json")
    cm.set_network_settings({
        "proxy_enabled": True,
        "http_proxy": "http://127.0.0.1:7890",
    })
    # 同实例 get 一致
    network = cm.get_network_settings()
    assert network["proxy_enabled"] is True
    assert network["http_proxy"] == "http://127.0.0.1:7890"

    # 重新加载配置文件验证持久化
    cm2 = ConfigManager(tmp_path / "config.json")
    cm2.load()
    network2 = cm2.get_network_settings()
    assert network2["proxy_enabled"] is True
    assert network2["http_proxy"] == "http://127.0.0.1:7890"


def test_get_network_settings_missing_field(tmp_path: Path) -> None:
    """旧配置（无 network 字段）get_network_settings() 返回默认值不崩溃。"""
    config_path = tmp_path / "config.json"
    # 手动写一个不含 network 字段的配置文件
    import json

    config_path.write_text(
        json.dumps({"endpoints": [], "default_endpoint_id": ""}),
        encoding="utf-8",
    )
    cm = ConfigManager(config_path)
    cm.load()
    network = cm.get_network_settings()
    assert network == {"proxy_enabled": False, "http_proxy": ""}


def test_set_network_settings_disabled_with_url(tmp_path: Path) -> None:
    """proxy_enabled=False 但 http_proxy 非空时仍正确保存（开关独立于 URL）。"""
    cm = ConfigManager(tmp_path / "config.json")
    cm.set_network_settings({
        "proxy_enabled": False,
        "http_proxy": "http://127.0.0.1:7890",
    })
    network = cm.get_network_settings()
    assert network["proxy_enabled"] is False
    assert network["http_proxy"] == "http://127.0.0.1:7890"


# ===== LLMClient proxy 参数测试 =====


def test_llm_client_proxy_set() -> None:
    """LLMClient(proxy=...) 构造后 self.proxy 正确设置。"""
    client = LLMClient(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        proxy="http://127.0.0.1:7890",
    )
    assert client.proxy == "http://127.0.0.1:7890"


def test_llm_client_proxy_none() -> None:
    """LLMClient(proxy=None) 构造后 self.proxy 为 None。"""
    client = LLMClient(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        proxy=None,
    )
    assert client.proxy is None


def test_llm_client_proxy_empty_string() -> None:
    """LLMClient(proxy="") 构造后 self.proxy 为 None（空串视为无代理）。"""
    client = LLMClient(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        proxy="",
    )
    assert client.proxy is None


def test_llm_client_proxy_whitespace_only() -> None:
    """LLMClient(proxy="   ") 构造后 self.proxy 为 None（纯空白视为无代理）。"""
    client = LLMClient(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        proxy="   ",
    )
    assert client.proxy is None


def test_llm_client_proxy_stripped() -> None:
    """LLMClient(proxy="  http://host:port  ") 构造后 self.proxy 去除首尾空白。"""
    client = LLMClient(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        proxy="  http://127.0.0.1:7890  ",
    )
    assert client.proxy == "http://127.0.0.1:7890"


def test_llm_client_proxy_default_none() -> None:
    """LLMClient 不传 proxy 时 self.proxy 为 None。"""
    client = LLMClient(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
    )
    assert client.proxy is None


# ===== ModelFetchWorker proxy 参数测试 =====


def test_model_fetch_worker_proxy_default_none() -> None:
    """ModelFetchWorker 不传 proxy 时 _proxy 为 None。"""
    worker = ModelFetchWorker("https://api.example.com/v1", "sk-test")
    assert worker._proxy is None


def test_model_fetch_worker_proxy_set() -> None:
    """ModelFetchWorker 传入 proxy 时 _proxy 正确保存。"""
    worker = ModelFetchWorker(
        "https://api.example.com/v1",
        "sk-test",
        proxy="http://127.0.0.1:7890",
    )
    assert worker._proxy == "http://127.0.0.1:7890"


# ===== SettingsDialog UI 测试 =====


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """提供全局 QApplication 单例（离屏平台）。"""
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def test_settings_dialog_has_network_proxy_group(tmp_path: Path, qapp: QApplication) -> None:
    """SettingsDialog 含「网络代理」分组（QCheckBox + QLineEdit）。"""
    cm = ConfigManager(tmp_path / "config.json")
    dialog = SettingsDialog(cm)
    # QCheckBox 与 QLineEdit 应存在于 dialog
    checkboxes = dialog.findChildren(QCheckBox)
    line_edits = dialog.findChildren(QLineEdit)
    assert any("代理" in cb.text() for cb in checkboxes), "缺少「启用 HTTP 代理」复选框"
    assert any(le.placeholderText().startswith("http://") for le in line_edits), (
        "缺少代理 URL 输入框"
    )


def test_settings_dialog_loads_network_settings(tmp_path: Path, qapp: QApplication) -> None:
    """SettingsDialog 加载时从 config_manager 读取网络代理配置。"""
    cm = ConfigManager(tmp_path / "config.json")
    cm.set_network_settings({
        "proxy_enabled": True,
        "http_proxy": "http://127.0.0.1:7890",
    })
    dialog = SettingsDialog(cm)
    assert dialog._proxy_check.isChecked() is True
    assert dialog._proxy_edit.text() == "http://127.0.0.1:7890"
    assert dialog._proxy_edit.isEnabled() is True  # 开关联动


def test_settings_dialog_disabled_proxy_disables_edit(
    tmp_path: Path, qapp: QApplication
) -> None:
    """proxy_enabled=False 时 URL 输入框禁用。"""
    cm = ConfigManager(tmp_path / "config.json")
    # 默认 proxy_enabled=False
    dialog = SettingsDialog(cm)
    assert dialog._proxy_check.isChecked() is False
    assert dialog._proxy_edit.isEnabled() is False


def test_settings_dialog_save_network_settings(
    tmp_path: Path, qapp: QApplication
) -> None:
    """SettingsDialog 保存后 config_manager.get_network_settings 反映 UI 输入。"""
    cm = ConfigManager(tmp_path / "config.json")
    dialog = SettingsDialog(cm)
    # 模拟用户勾选并输入代理 URL
    dialog._proxy_check.setChecked(True)
    dialog._proxy_edit.setText("http://user:pass@proxy.example.com:8080")
    # 触发保存
    dialog._on_accept()
    # 验证配置已持久化
    network = cm.get_network_settings()
    assert network["proxy_enabled"] is True
    assert network["http_proxy"] == "http://user:pass@proxy.example.com:8080"


def test_settings_dialog_toggle_enables_edit(tmp_path: Path, qapp: QApplication) -> None:
    """勾选开关后 URL 输入框启用，取消勾选后禁用。"""
    cm = ConfigManager(tmp_path / "config.json")
    dialog = SettingsDialog(cm)
    # 初始禁用
    assert dialog._proxy_edit.isEnabled() is False
    # 勾选后启用
    dialog._proxy_check.setChecked(True)
    assert dialog._proxy_edit.isEnabled() is True
    # 取消勾选后禁用
    dialog._proxy_check.setChecked(False)
    assert dialog._proxy_edit.isEnabled() is False


def test_settings_dialog_save_strips_whitespace(tmp_path: Path, qapp: QApplication) -> None:
    """保存时对代理 URL 去除首尾空白。"""
    cm = ConfigManager(tmp_path / "config.json")
    dialog = SettingsDialog(cm)
    dialog._proxy_check.setChecked(True)
    dialog._proxy_edit.setText("  http://127.0.0.1:7890  ")
    dialog._on_accept()
    network = cm.get_network_settings()
    assert network["http_proxy"] == "http://127.0.0.1:7890"


# ===== MainWindow _get_network_proxy 与 worker 接线测试 =====


class _FakeConfigManager:
    """仅实现 get_network_settings 的桩 ConfigManager。"""

    def __init__(self, network: dict) -> None:
        self._network = network

    def get_network_settings(self) -> dict:
        return self._network


def _make_main_window_stub(network: dict):
    """创建仅含 config_manager 的 MainWindow 桩实例（不经 __init__，避免 UI 依赖）。"""
    from novelforge.ui.main_window import MainWindow

    win = MainWindow.__new__(MainWindow)
    win.config_manager = _FakeConfigManager(network)
    return win


def test_get_network_proxy_enabled_with_url() -> None:
    """proxy_enabled=True 且 http_proxy 非空时返回代理 URL。"""
    win = _make_main_window_stub(
        {"proxy_enabled": True, "http_proxy": "http://127.0.0.1:7890"}
    )
    assert win._get_network_proxy() == "http://127.0.0.1:7890"


def test_get_network_proxy_disabled() -> None:
    """proxy_enabled=False 时返回 None（即使 http_proxy 非空）。"""
    win = _make_main_window_stub(
        {"proxy_enabled": False, "http_proxy": "http://127.0.0.1:7890"}
    )
    assert win._get_network_proxy() is None


def test_get_network_proxy_enabled_empty_url() -> None:
    """proxy_enabled=True 但 http_proxy 为空串时返回 None。"""
    win = _make_main_window_stub({"proxy_enabled": True, "http_proxy": ""})
    assert win._get_network_proxy() is None


def test_get_network_proxy_both_off() -> None:
    """proxy_enabled=False 且 http_proxy 为空时返回 None。"""
    win = _make_main_window_stub({"proxy_enabled": False, "http_proxy": ""})
    assert win._get_network_proxy() is None


def test_all_worker_instantiations_pass_proxy() -> None:
    """回归测试：main_window.py 中每个 worker 构造调用都必须传 proxy=self._get_network_proxy()。

    历史 bug：7 处 worker 实例化点中 5 处漏传 proxy 参数，导致用户配置的
    HTTP 代理在单章续写/卷续写/单章审计/审计后修正/重写生成流程被静默忽略。
    本测试静态扫描源码，确保每个 worker 构造调用的参数块含 proxy 透传，
    防止未来新增/修改 worker 实例化点时再次漏传。
    """
    import re
    from collections import Counter

    main_window_path = PROJECT_ROOT / "novelforge" / "ui" / "main_window.py"
    source = main_window_path.read_text(encoding="utf-8")

    # 匹配 worker 构造调用：名字后紧跟 ( （class 定义是 : ，类型注解无 ( ）
    pattern = re.compile(r"\b(ContinuationWorker|AuditWorker|VolumeOrchestrator)\(")

    call_sites: list[tuple[str, str]] = []  # (worker_name, argument_block)
    for m in pattern.finditer(source):
        name = m.group(1)
        # 从 ( 开始，按括号深度匹配到对应 )
        open_idx = m.end() - 1  # 指向 (
        depth = 0
        i = open_idx
        while i < len(source):
            ch = source[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block = source[open_idx : i + 1]
        call_sites.append((name, block))

    # 必须找到 8 个 worker 构造调用（3 ContinuationWorker + 4 AuditWorker + 1 VolumeOrchestrator）
    assert len(call_sites) == 8, (
        f"预期 8 个 worker 构造调用，实际找到 {len(call_sites)} 个；"
        f"若新增 worker 实例化点请同步传 proxy 并更新本断言"
    )

    name_counts = Counter(name for name, _ in call_sites)
    assert name_counts == {
        "ContinuationWorker": 3,
        "AuditWorker": 4,
        "VolumeOrchestrator": 1,
    }, f"worker 构造调用分布异常：{name_counts}"

    # 每个构造调用的参数块都必须含 proxy 透传
    missing = [
        name for name, block in call_sites
        if "proxy=self._get_network_proxy()" not in block
    ]
    assert missing == [], (
        f"以下 worker 构造调用未传 proxy=self._get_network_proxy()：{missing}；"
        f"这会导致用户配置的 HTTP 代理在对应流程被静默忽略"
    )
