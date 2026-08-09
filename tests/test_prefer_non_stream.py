"""全局 prefer_non_stream 与非流式超时策略测试。

覆盖：
- 默认配置 prefer_non_stream=False
- ConfigManager.is_prefer_non_stream 读写
- LLMClient 生成超时 total=None + sock_read
- complete_text 按 stream 分支调用 chat/stream
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from novelforge.core.config import ConfigManager, get_default_config
from novelforge.services.llm_client import LLMClient, StreamChunk


def test_default_config_prefer_non_stream_false() -> None:
    """默认关闭非流式，保持原有流式体验。"""
    cont = get_default_config()["continuation"]
    assert cont.get("prefer_non_stream") is False


def test_is_prefer_non_stream_default(tmp_path: Path) -> None:
    cm = ConfigManager(tmp_path / "config.json")
    assert cm.is_prefer_non_stream() is False


def test_is_prefer_non_stream_roundtrip(tmp_path: Path) -> None:
    cm = ConfigManager(tmp_path / "config.json")
    cont = cm.get_continuation_settings()
    cont["prefer_non_stream"] = True
    cm.config["continuation"] = cont
    cm.save()

    cm2 = ConfigManager(tmp_path / "config.json")
    cm2.load()
    assert cm2.is_prefer_non_stream() is True


def test_llm_client_request_timeout_no_total() -> None:
    """生成请求超时：total=None，sock_read=构造 timeout，避免长生成被 total 误杀。"""
    client = LLMClient(
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout=300,
    )
    assert client.request_timeout.total is None
    assert client.request_timeout.sock_read == 300
    assert client.stream_timeout is client.request_timeout
    # 短请求仍有 total
    assert client.timeout.total == 300


@pytest.mark.asyncio
async def test_complete_text_stream_path_uses_stream() -> None:
    """stream=True 时走 stream_chat_completion 并聚合 chunk。"""
    client = LLMClient(base_url="https://example.com/v1", api_key="k")
    chunks = [
        StreamChunk(content="你好"),
        StreamChunk(content="世界", finish_reason="stop"),
    ]

    async def _gen(**_kwargs):
        for c in chunks:
            yield c

    received: list[str] = []
    with patch.object(client, "stream_chat_completion", side_effect=_gen) as mock_stream:
        with patch.object(client, "chat_completion", new_callable=AsyncMock) as mock_chat:
            text, finish = await client.complete_text(
                messages=[{"role": "user", "content": "hi"}],
                model="gpt-4",
                stream=True,
                on_chunk=received.append,
            )
    assert text == "你好世界"
    assert finish == "stop"
    assert received == ["你好", "世界"]
    mock_stream.assert_called_once()
    mock_chat.assert_not_called()


@pytest.mark.asyncio
async def test_complete_text_non_stream_path_uses_chat() -> None:
    """stream=False 时走 chat_completion，完成后一次性 on_chunk。"""
    client = LLMClient(base_url="https://example.com/v1", api_key="k")
    fake_resp = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "全文一次返回"},
                "finish_reason": "stop",
            }
        ]
    }
    received: list[str] = []
    with patch.object(
        client, "chat_completion", new_callable=AsyncMock, return_value=fake_resp
    ) as mock_chat:
        with patch.object(client, "stream_chat_completion") as mock_stream:
            text, finish = await client.complete_text(
                messages=[{"role": "user", "content": "hi"}],
                model="gpt-4",
                stream=False,
                on_chunk=received.append,
            )
    assert text == "全文一次返回"
    assert finish == "stop"
    assert received == ["全文一次返回"]
    mock_chat.assert_called_once()
    mock_stream.assert_not_called()


def test_main_window_workers_pass_prefer_non_stream() -> None:
    """静态扫描：MainWindow 创建 worker 时透传 prefer_non_stream。"""
    src = (
        PROJECT_ROOT / "novelforge" / "ui" / "main_window.py"
    ).read_text(encoding="utf-8")
    # 至少 ContinuationWorker / AuditWorker / VolumeOrchestrator 构造点
    assert "prefer_non_stream=self.config_manager.is_prefer_non_stream()" in src
    assert src.count("prefer_non_stream=self.config_manager.is_prefer_non_stream()") >= 3
