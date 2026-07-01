"""设置对话框端点编辑回归测试。

覆盖以下检查点：
1. 点击"编辑"按钮后 EndpointEditDialog 被打开且加载了选中端点数据
   （回归：修复前 clicked(bool) 把 False 传给 item 参数导致提前 return，对话框从未打开）
2. 双击列表项触发编辑（回归保护，确保 lambda 修改未破坏双击路径）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

from novelforge.core.config import ConfigManager
from novelforge.ui import settings_dialog as sd_module
from novelforge.ui.settings_dialog import SettingsDialog


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """提供全局 QApplication 单例（离屏平台）。"""
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def _make_config_manager(tmp_path: Path) -> ConfigManager:
    """创建带 1 个端点的 ConfigManager。"""
    cm = ConfigManager(tmp_path / "config.json")
    cm.add_endpoint(
        {
            "name": "测试端点",
            "base_url": "https://test.example.com/v1",
            "api_key": "sk-test123",
            "default_model": "gpt-4o",
            "reasoning_effort": "high",
        }
    )
    return cm


class _StubEditDialog:
    """替换 EndpointEditDialog 的 stub，记录调用参数并立即返回 Rejected。

    避免 _on_fetch_models 在 _load_data 中触发真实网络请求阻塞测试。
    """

    calls: list[dict[str, Any]] = []

    def __init__(self, config_manager, endpoint, parent=None) -> None:
        # 记录本次调用传入的 endpoint 字典
        self._endpoint = endpoint or {}
        type(self).calls.append({"endpoint": endpoint})

    def exec(self) -> int:
        # 返回 Rejected，避免 _on_edit_endpoint 进入保存分支
        from PySide6.QtWidgets import QDialog

        return QDialog.DialogCode.Rejected

    def get_result(self) -> dict[str, Any]:
        return {}


@pytest.fixture(autouse=True)
def _reset_stub() -> None:
    """每个测试前清空 stub 调用记录。"""
    _StubEditDialog.calls.clear()
    yield
    _StubEditDialog.calls.clear()


@pytest.fixture
def settings_dialog(qapp, tmp_path: Path, monkeypatch) -> SettingsDialog:
    """创建带 1 个端点的 SettingsDialog，并 stub 掉 EndpointEditDialog。"""
    cm = _make_config_manager(tmp_path)
    # stub EndpointEditDialog 避免真实网络请求（_load_data 会自动 fetch_models）
    monkeypatch.setattr(sd_module, "EndpointEditDialog", _StubEditDialog)
    dialog = SettingsDialog(cm)
    return dialog


class TestEditButton:
    """编辑按钮点击回归测试。"""

    def test_edit_button_opens_dialog_with_selected_endpoint(
        self, settings_dialog
    ) -> None:
        """点击"编辑"按钮后 EndpointEditDialog 被打开且加载选中端点。

        回归：修复前 clicked(bool) 把 False 传给 _on_edit_endpoint 的 item 参数，
        False is not None 跳过取 currentItem，not False 为 True 提前 return，
        EndpointEditDialog 从未构造。
        """
        # 选中列表第一项
        settings_dialog._endpoint_list.setCurrentRow(0)

        # 模拟点击"编辑"按钮（触发 clicked 信号，会传 False 给槽）
        settings_dialog._edit_btn.click()

        # stub 应被调用一次
        assert len(_StubEditDialog.calls) == 1
        # 传入的 endpoint 应是选中端点
        ep = _StubEditDialog.calls[0]["endpoint"]
        assert ep is not None
        assert ep["name"] == "测试端点"
        assert ep["base_url"] == "https://test.example.com/v1"

    def test_edit_via_doubleclick_still_works(self, settings_dialog) -> None:
        """双击列表项同样触发编辑（回归保护，lambda 修改未破坏双击路径）。"""
        # 双击第一项
        item = settings_dialog._endpoint_list.item(0)
        settings_dialog._endpoint_list.itemDoubleClicked.emit(item)

        assert len(_StubEditDialog.calls) == 1
        ep = _StubEditDialog.calls[0]["endpoint"]
        assert ep is not None
        assert ep["name"] == "测试端点"


class _SlowModelFetchWorker(QThread):
    """模拟慢速模型获取线程，run() 中 sleep 模拟网络延迟。"""

    models_fetched = Signal(list)
    error = Signal(str)

    def __init__(self, base_url: str, api_key: str, parent=None) -> None:
        super().__init__(parent)

    def run(self) -> None:
        import time

        time.sleep(0.5)


class TestCloseWhileFetching:
    """对话框关闭时模型获取线程仍在运行的回归测试。"""

    def test_edit_dialog_close_while_fetching_does_not_crash(
        self, qapp, tmp_path: Path, monkeypatch
    ) -> None:
        """编辑已有端点时自动拉取模型，立即关闭对话框不应闪退。

        回归：修复前 worker parent=dialog，对话框关闭时 QThread 被强制销毁
        → QThread: Destroyed while thread is still running → 闪退。
        """
        from novelforge.ui.settings_dialog import EndpointEditDialog

        cm = _make_config_manager(tmp_path)
        endpoint = cm.get_endpoints()[0]

        # stub ModelFetchWorker 为慢速线程
        monkeypatch.setattr(sd_module, "ModelFetchWorker", _SlowModelFetchWorker)

        dialog = EndpointEditDialog(cm, endpoint, None)
        # _load_data 已自动触发 _on_fetch_models，慢速 worker 已 start
        assert dialog._model_fetch_worker is not None
        assert dialog._model_fetch_worker.isRunning()

        # 模拟用户立即点 OK 关闭对话框（不等待 fetch 完成）
        dialog.close()

        # 断言未抛异常且对话框已关闭
        assert not dialog.isVisible()
        # worker parent=None，可安全等待完成（证明未被强制销毁）
        dialog._model_fetch_worker.wait(3000)
        assert not dialog._model_fetch_worker.isRunning()
