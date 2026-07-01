"""思考强度（reasoning_effort）注入测试。

覆盖：
- LLMClient 的 reasoning_effort 属性存储与 payload 注入条件
- MainWindow._resolve_reasoning_effort 的预设级优先/端点级回退/均空逻辑
"""
from __future__ import annotations

from novelforge.models.preset import WritingPreset
from novelforge.services.llm_client import LLMClient


# ===== LLMClient reasoning_effort 属性与注入条件 =====


def test_llm_client_stores_reasoning_effort() -> None:
    """LLMClient 正确存储 reasoning_effort 参数。"""
    client = LLMClient("https://api.example.com/v1", "sk-test", reasoning_effort="high")
    assert client.reasoning_effort == "high"


def test_llm_client_stores_empty_reasoning_effort() -> None:
    """reasoning_effort 为 None 时存储为空串。"""
    client = LLMClient("https://api.example.com/v1", "sk-test")
    assert client.reasoning_effort == ""


def test_llm_client_payload_injects_reasoning_effort() -> None:
    """reasoning_effort="high" 时满足注入条件。"""
    client = LLMClient("https://api.example.com/v1", "sk-test", reasoning_effort="high")
    # 镜像 llm_client.py 中的注入条件
    should_inject = (
        client.reasoning_effort
        and client.reasoning_effort.lower() not in {"", "none", "off"}
    )
    assert should_inject is True
    assert client.reasoning_effort == "high"


def test_llm_client_payload_skips_empty_reasoning_effort() -> None:
    """空串不满足注入条件。"""
    client = LLMClient("https://api.example.com/v1", "sk-test", reasoning_effort="")
    should_inject = (
        client.reasoning_effort
        and client.reasoning_effort.lower() not in {"", "none", "off"}
    )
    assert not should_inject


def test_llm_client_payload_skips_none_off() -> None:
    """none/off（大小写不敏感）不满足注入条件。"""
    for val in ("none", "off", "None", "OFF", "None", "off"):
        client = LLMClient("https://api.example.com/v1", "sk-test", reasoning_effort=val)
        should_inject = (
            client.reasoning_effort
            and client.reasoning_effort.lower() not in {"", "none", "off"}
        )
        assert not should_inject, f"值 {val!r} 不应注入"


def test_llm_client_payload_injects_max() -> None:
    """max（DeepSeek V4）满足注入条件。"""
    client = LLMClient("https://api.example.com/v1", "sk-test", reasoning_effort="max")
    should_inject = (
        client.reasoning_effort
        and client.reasoning_effort.lower() not in {"", "none", "off"}
    )
    assert should_inject is True


# ===== _resolve_reasoning_effort 解析逻辑 =====


def _make_main_window_stub():
    """创建仅含 _resolve_reasoning_effort 方法的 MainWindow 桩实例。"""
    from novelforge.ui.main_window import MainWindow

    win = MainWindow.__new__(MainWindow)
    return win


def test_resolve_reasoning_effort_preset_priority() -> None:
    """预设级 reasoning_effort 优先于端点级。"""
    win = _make_main_window_stub()
    preset = WritingPreset(
        id="test-preset",
        name="test",
        generation_params={"reasoning_effort": "max"},
    )
    endpoint = {"reasoning_effort": "low"}
    result = win._resolve_reasoning_effort(endpoint, preset)
    assert result == "max"


def test_resolve_reasoning_effort_preset_none_off_ignored() -> None:
    """预设 reasoning_effort 为 none/off 时忽略，回退端点级。"""
    win = _make_main_window_stub()
    for val in ("none", "off"):
        preset = WritingPreset(
            id="test-preset",
            name="test",
            generation_params={"reasoning_effort": val},
        )
        endpoint = {"reasoning_effort": "high"}
        result = win._resolve_reasoning_effort(endpoint, preset)
        assert result == "high", f"预设值 {val!r} 应被忽略，回退端点 high"


def test_resolve_reasoning_effort_endpoint_fallback() -> None:
    """预设 reasoning_effort 为空时用端点级。"""
    win = _make_main_window_stub()
    preset = WritingPreset(
        id="test-preset",
        name="test",
        generation_params={"reasoning_effort": ""},
    )
    endpoint = {"reasoning_effort": "medium"}
    result = win._resolve_reasoning_effort(endpoint, preset)
    assert result == "medium"


def test_resolve_reasoning_effort_no_preset() -> None:
    """无预设时用端点级。"""
    win = _make_main_window_stub()
    endpoint = {"reasoning_effort": "low"}
    result = win._resolve_reasoning_effort(endpoint, None)
    assert result == "low"


def test_resolve_reasoning_effort_both_empty() -> None:
    """预设与端点均空时返回空串（不发送）。"""
    win = _make_main_window_stub()
    preset = WritingPreset(
        id="test-preset",
        name="test",
        generation_params={"reasoning_effort": ""},
    )
    endpoint = {"reasoning_effort": ""}
    result = win._resolve_reasoning_effort(endpoint, preset)
    assert result == ""


def test_resolve_reasoning_effort_endpoint_missing_field() -> None:
    """端点无 reasoning_effort 字段时返回空串。"""
    win = _make_main_window_stub()
    endpoint = {}
    result = win._resolve_reasoning_effort(endpoint, None)
    assert result == ""
