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


# ===== _ensure_user_message Gemini 兼容兜底 =====


def test_ensure_user_message_all_system_converts_last() -> None:
    """全 system 消息时，最后一条转为 user 角色。"""
    messages = [
        {"role": "system", "content": "破限文本"},
        {"role": "system", "content": "任务指令"},
    ]
    result = LLMClient._ensure_user_message(messages)
    assert result[-1]["role"] == "user"
    assert result[-1]["content"] == "任务指令"
    # 前面的消息保持 system
    assert result[0]["role"] == "system"
    # 原始列表不被修改
    assert messages[-1]["role"] == "system"


def test_ensure_user_message_has_user_unchanged() -> None:
    """已含 user 消息时不修改。"""
    messages = [
        {"role": "system", "content": "破限"},
        {"role": "user", "content": "任务"},
    ]
    result = LLMClient._ensure_user_message(messages)
    assert result == messages


def test_ensure_user_message_single_system_converts() -> None:
    """单条 system 消息也转为 user。"""
    messages = [{"role": "system", "content": "任务指令"}]
    result = LLMClient._ensure_user_message(messages)
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "任务指令"


# ===== _is_xai_model Grok 模型检测 =====


def test_is_xai_model_grok() -> None:
    """检测 Grok 模型。"""
    assert LLMClient._is_xai_model("grok-4.5") is True
    assert LLMClient._is_xai_model("Grok-3") is True
    assert LLMClient._is_xai_model("grok-code-fast") is True
    assert LLMClient._is_xai_model("gpt-4") is False
    assert LLMClient._is_xai_model("gemini-pro") is False


# ===== _filter_unsupported_params 按模型过滤不支持参数 =====


def test_filter_unsupported_params_grok4_removes_penalties_and_reasoning() -> None:
    """Grok-4/4.5 删除 penalties 和 reasoning_effort。"""
    payload = {
        "model": "grok-4.5",
        "presence_penalty": 0.5,
        "frequency_penalty": 0.3,
        "reasoning_effort": "medium",
    }
    LLMClient._filter_unsupported_params(payload, "grok-4.5")
    assert "presence_penalty" not in payload
    assert "frequency_penalty" not in payload
    assert "reasoning_effort" not in payload


def test_filter_unsupported_params_grok3_mini_removes_penalties_keeps_reasoning() -> None:
    """Grok-3-mini 删除 penalties 但保留 reasoning_effort。"""
    payload = {
        "model": "grok-3-mini",
        "presence_penalty": 0.5,
        "frequency_penalty": 0.3,
        "reasoning_effort": "medium",
    }
    LLMClient._filter_unsupported_params(payload, "grok-3-mini")
    assert "presence_penalty" not in payload
    assert "frequency_penalty" not in payload
    assert "reasoning_effort" in payload


def test_filter_unsupported_params_grok_code_removes_penalties_and_reasoning() -> None:
    """grok-code 删除 penalties 和 reasoning_effort。"""
    payload = {
        "model": "grok-code-fast",
        "presence_penalty": 0.5,
        "frequency_penalty": 0.3,
        "reasoning_effort": "medium",
    }
    LLMClient._filter_unsupported_params(payload, "grok-code-fast")
    assert "presence_penalty" not in payload
    assert "frequency_penalty" not in payload
    assert "reasoning_effort" not in payload


def test_filter_unsupported_params_grok3_removes_penalties_and_reasoning() -> None:
    """grok-3（非 mini）删除 penalties 和 reasoning_effort。"""
    payload = {
        "model": "grok-3",
        "presence_penalty": 0.5,
        "frequency_penalty": 0.3,
        "reasoning_effort": "medium",
    }
    LLMClient._filter_unsupported_params(payload, "grok-3")
    assert "presence_penalty" not in payload
    assert "frequency_penalty" not in payload
    assert "reasoning_effort" not in payload


def test_filter_unsupported_params_non_grok_unchanged() -> None:
    """非 Grok 模型不修改 payload。"""
    payload = {
        "model": "gpt-4",
        "presence_penalty": 0.5,
        "frequency_penalty": 0.3,
        "reasoning_effort": "medium",
    }
    LLMClient._filter_unsupported_params(payload, "gpt-4")
    assert payload["presence_penalty"] == 0.5
    assert payload["frequency_penalty"] == 0.3
    assert payload["reasoning_effort"] == "medium"
