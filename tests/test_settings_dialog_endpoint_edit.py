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
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import QApplication

from novelforge.core.config import ConfigManager
from novelforge.ui import settings_dialog as sd_module
from novelforge.ui.continuation_panel import ContinuationPanel
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


class TestEnabledModelsSave:
    """端点编辑：可勾选模型列表多选 enabled_models 持久化测试。"""

    def test_load_and_save_enabled_models(self, qapp, tmp_path: Path) -> None:
        """加载端点时按 enabled_models 勾选；保存时收集勾选项为 enabled_models，
        default_model 自动取首个已启用（sorted）。"""
        from novelforge.ui.settings_dialog import EndpointEditDialog

        cm = ConfigManager(tmp_path / "config.json")
        cm.add_endpoint(
            {
                "name": "测试",
                "base_url": "https://test.example.com/v1",
                # 不提供 api_key，避免 _load_data 自动拉取模型
                "models": ["b-model", "a-model", "c-model"],
                "enabled_models": ["a-model", "c-model"],
                "default_model": "a-model",
            }
        )
        endpoint = cm.get_endpoints()[0]

        dialog = EndpointEditDialog(cm, endpoint, None)

        # 列表应有 3 项（a/b/c sorted），a-model 与 c-model 勾选
        texts = [dialog._model_list.item(i).text() for i in range(dialog._model_list.count())]
        assert texts == ["a-model", "b-model", "c-model"]
        checked = {
            dialog._model_list.item(i).text()
            for i in range(dialog._model_list.count())
            if dialog._model_list.item(i).checkState() == Qt.CheckState.Checked
        }
        assert checked == {"a-model", "c-model"}

        # 用户操作：取消 a-model，勾选 b-model
        for i in range(dialog._model_list.count()):
            it = dialog._model_list.item(i)
            if it.text() == "a-model":
                it.setCheckState(Qt.CheckState.Unchecked)
            elif it.text() == "b-model":
                it.setCheckState(Qt.CheckState.Checked)

        # 触发保存（name/base_url 已由 _load_data 填入，通过校验）
        dialog._on_accept()
        result = dialog.get_result()

        # enabled_models = 勾选项（b-model, c-model）
        assert set(result["enabled_models"]) == {"b-model", "c-model"}
        # models = 全部 item
        assert set(result["models"]) == {"a-model", "b-model", "c-model"}
        # default_model = sorted(enabled)[0] = "b-model"
        assert result["default_model"] == "b-model"

    def test_no_enabled_models_fallback_checks_all(self, qapp, tmp_path: Path) -> None:
        """旧端点无 enabled_models → 加载时全部勾选（兼容）。"""
        from novelforge.ui.settings_dialog import EndpointEditDialog

        cm = ConfigManager(tmp_path / "config.json")
        cm.add_endpoint(
            {
                "name": "旧端点",
                "base_url": "https://test.example.com/v1",
                "models": ["x", "y"],
                "default_model": "x",
            }
        )
        endpoint = cm.get_endpoints()[0]
        dialog = EndpointEditDialog(cm, endpoint, None)

        checked = {
            dialog._model_list.item(i).text()
            for i in range(dialog._model_list.count())
            if dialog._model_list.item(i).checkState() == Qt.CheckState.Checked
        }
        assert checked == {"x", "y"}


class TestContinuationPanelEnabledModels:
    """续写面板：按 enabled_models 填充模型下拉 + 会话记忆。"""

    def test_model_dropdown_shows_only_enabled(self, qapp) -> None:
        """切换到端点时，模型下拉仅含 enabled_models（sorted），选中首个。"""
        panel = ContinuationPanel()
        ep = {
            "id": "ep1",
            "name": "端点1",
            "models": ["z-model", "a-model", "m-model"],
            "enabled_models": ["z-model", "a-model"],
            "default_model": "a-model",
        }
        panel.set_endpoints([ep], default_id="ep1")

        items = [panel._model_combo.itemText(i) for i in range(panel._model_combo.count())]
        assert items == ["a-model", "z-model"]  # sorted，仅 enabled
        assert panel._model_combo.currentText() == "a-model"  # 首个

    def test_session_memory_restores_last_model(self, qapp) -> None:
        """用户手动切换模型后，切到别的端点再切回应恢复上次选择。"""
        panel = ContinuationPanel()
        ep1 = {
            "id": "ep1",
            "name": "端点1",
            "models": ["a", "b", "c"],
            "enabled_models": ["a", "b", "c"],
            "default_model": "a",
        }
        ep2 = {
            "id": "ep2",
            "name": "端点2",
            "models": ["x", "y"],
            "enabled_models": ["x", "y"],
            "default_model": "x",
        }
        panel.set_endpoints([ep1, ep2], default_id="ep1")

        # 在 ep1 手动切换到 "b"（用户操作，触发会话记忆）
        idx = panel._model_combo.findText("b")
        panel._model_combo.setCurrentIndex(idx)
        assert panel._last_model_per_endpoint["ep1"] == "b"

        # 切到 ep2
        panel._endpoint_combo.setCurrentIndex(1)
        assert panel._model_combo.currentText() == "x"

        # 切回 ep1 → 应恢复 "b"
        panel._endpoint_combo.setCurrentIndex(0)
        assert panel._model_combo.currentText() == "b"

    def test_fallback_to_models_when_no_enabled(self, qapp) -> None:
        """旧端点无 enabled_models → 回退到 models 全部。"""
        panel = ContinuationPanel()
        ep = {
            "id": "ep1",
            "name": "旧端点",
            "models": ["m2", "m1"],
            "default_model": "m1",
        }
        panel.set_endpoints([ep], default_id="ep1")
        items = [panel._model_combo.itemText(i) for i in range(panel._model_combo.count())]
        assert items == ["m1", "m2"]
