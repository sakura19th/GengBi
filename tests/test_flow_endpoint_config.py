"""流程端点配置（flow_endpoints）测试。

覆盖：
- ConfigManager.get_flow_endpoint：已配置/端点被删/未配置→回退默认端点
- ConfigManager.set_flow_endpoints 持久化 roundtrip
- FlowEndpointDialog 加载/保存 roundtrip（7 行下拉）
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
from PySide6.QtWidgets import QApplication

from novelforge.core.config import ConfigManager
from novelforge.ui.flow_endpoint_dialog import FLOW_DEFINITIONS, FlowEndpointDialog


# ===== ConfigManager flow_endpoints 测试 =====


def _make_config_manager(tmp_path: Path) -> ConfigManager:
    """创建带 2 个端点的 ConfigManager（ep1 为默认）。"""
    cm = ConfigManager(tmp_path / "config.json")
    cm.add_endpoint({"name": "端点A", "base_url": "https://a.example.com/v1", "api_key": "sk-a"})
    cm.add_endpoint({"name": "端点B", "base_url": "https://b.example.com/v1", "api_key": "sk-b"})
    # ep1 为默认端点（add_endpoint 自动设第一个为默认）
    return cm


def test_get_flow_endpoint_unconfigured(tmp_path: Path) -> None:
    """未配置 flow_endpoints 时返回默认端点。"""
    cm = _make_config_manager(tmp_path)
    ep = cm.get_flow_endpoint("single_continuation")
    assert ep is not None
    assert ep["name"] == "端点A"  # 默认端点


def test_get_flow_endpoint_configured(tmp_path: Path) -> None:
    """flow_key 已配置且端点存在→返回该端点。"""
    cm = _make_config_manager(tmp_path)
    endpoints = cm.get_endpoints()
    ep_b_id = endpoints[1]["id"]
    cm.set_flow_endpoints({"context_extraction": ep_b_id})
    ep = cm.get_flow_endpoint("context_extraction")
    assert ep is not None
    assert ep["name"] == "端点B"


def test_get_flow_endpoint_deleted_falls_back(tmp_path: Path) -> None:
    """配置的端点被删→回退默认端点。"""
    cm = _make_config_manager(tmp_path)
    endpoints = cm.get_endpoints()
    ep_b_id = endpoints[1]["id"]
    cm.set_flow_endpoints({"ontology_extraction": ep_b_id})
    # 删除端点 B
    cm.remove_endpoint(ep_b_id)
    ep = cm.get_flow_endpoint("ontology_extraction")
    assert ep is not None
    assert ep["name"] == "端点A"  # 回退默认


def test_get_flow_endpoint_no_endpoints(tmp_path: Path) -> None:
    """无任何端点时返回 None。"""
    cm = ConfigManager(tmp_path / "empty_config.json")
    ep = cm.get_flow_endpoint("single_continuation")
    assert ep is None


def test_set_flow_endpoints_roundtrip(tmp_path: Path) -> None:
    """set 后 get 一致。"""
    cm = _make_config_manager(tmp_path)
    endpoints = cm.get_endpoints()
    ep_a_id = endpoints[0]["id"]
    ep_b_id = endpoints[1]["id"]
    mapping = {
        "single_continuation": ep_a_id,
        "volume_continuation": ep_b_id,
        "single_audit": "",  # 空串=默认端点
        "context_extraction": ep_b_id,
    }
    cm.set_flow_endpoints(mapping)
    result = cm.get_flow_endpoints()
    assert result["single_continuation"] == ep_a_id
    assert result["volume_continuation"] == ep_b_id
    assert result["single_audit"] == ""
    assert result["context_extraction"] == ep_b_id


def test_flow_endpoints_default_empty(tmp_path: Path) -> None:
    """新配置的 flow_endpoints 默认为空 dict。"""
    cm = ConfigManager(tmp_path / "fresh_config.json")
    assert cm.get_flow_endpoints() == {}


# ===== FlowEndpointDialog 加载/保存 roundtrip =====


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """提供全局 QApplication 单例（离屏平台）。"""
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def test_flow_endpoint_dialog_has_7_flows(qapp, tmp_path: Path) -> None:
    """对话框包含 7 个流程下拉框。"""
    cm = _make_config_manager(tmp_path)
    dialog = FlowEndpointDialog(cm)
    assert len(dialog._combos) == 7
    for flow_key, _ in FLOW_DEFINITIONS:
        assert flow_key in dialog._combos


def test_flow_endpoint_dialog_load_save_roundtrip(qapp, tmp_path: Path) -> None:
    """对话框加载已保存配置→修改→保存→重新加载一致。"""
    cm = _make_config_manager(tmp_path)
    endpoints = cm.get_endpoints()
    ep_b_id = endpoints[1]["id"]

    # 预设配置
    cm.set_flow_endpoints({
        "single_continuation": ep_b_id,
        "context_extraction": ep_b_id,
    })

    # 打开对话框验证加载
    dialog1 = FlowEndpointDialog(cm)
    assert dialog1._combos["single_continuation"].currentData() == ep_b_id
    assert dialog1._combos["context_extraction"].currentData() == ep_b_id
    assert dialog1._combos["volume_continuation"].currentData() == ""  # 未配置=默认

    # 修改并保存
    dialog1._combos["volume_continuation"].setCurrentIndex(2)  # 选端点B
    dialog1._on_accept()

    # 验证保存
    mapping = cm.get_flow_endpoints()
    assert mapping["single_continuation"] == ep_b_id
    assert mapping["volume_continuation"] == ep_b_id
    assert mapping["context_extraction"] == ep_b_id

    # 重新打开对话框验证加载一致
    dialog2 = FlowEndpointDialog(cm)
    assert dialog2._combos["single_continuation"].currentData() == ep_b_id
    assert dialog2._combos["volume_continuation"].currentData() == ep_b_id
    assert dialog2._combos["context_extraction"].currentData() == ep_b_id


def test_flow_endpoint_dialog_default_selected(qapp, tmp_path: Path) -> None:
    """未配置时所有下拉默认选中首项（默认端点，itemData=""）。"""
    cm = _make_config_manager(tmp_path)
    dialog = FlowEndpointDialog(cm)
    for flow_key, _ in FLOW_DEFINITIONS:
        combo = dialog._combos[flow_key]
        assert combo.currentData() == "", f"{flow_key} 应默认选中首项（默认端点）"
