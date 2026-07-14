"""AuditWorker：单章续写审计的 QThread + asyncio 桥接。

在 QThread 子类的 ``run()`` 中创建独立 asyncio 事件循环，执行 LLM 流式
调用，通过 Qt 信号将审计报告的增量 chunk 实时推送到 UI 线程。

与 ContinuationWorker 的区别：
- 不做正则后处理（审计报告是 JSON 文本，无需 post_process）
- 不构建 Continuation 对象，``finished`` 信号返回完整文本字符串
- 仅供单章续写审计使用（精简 5 维度模板）

特性：
- 信号：``chunk_received(str)``、``finished(str)``、``error(str)``、
  ``token_count(int)``、``rate_limit_warning(str)``、``auth_error()``
- 线程安全停止：``threading.Event`` + ``asyncio.Task.cancel()`` 双重中断
- QThread 退出时 asyncio 事件循环正确关闭（loop.close()、aiohttp session 清理）
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from PySide6.QtCore import QThread, Signal

from novelforge.services.llm_client import (
    APIError,
    AuthError,
    LLMClient,
    LLMError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

# 流式中断保留的最小字符数（与 ContinuationWorker 一致）
MIN_INTERRUPT_CHARS = 100


class AuditWorker(QThread):
    """单章续写审计工作线程。

    在 QThread 中创建独立 asyncio 事件循环，流式调用 LLM 生成审计报告，
    通过 Qt 信号实时推送 chunk 到 UI 线程。

    Signals:
        chunk_received(str): 审计报告增量文本
        finished(str): 流式完成（含中断），返回完整审计报告文本
        error(str): 错误信息
        token_count(int): token 计数（已接收字符数）
        rate_limit_warning(str): 限流提示
        auth_error(): 认证失败，需跳转设置
    """

    chunk_received = Signal(str)
    finished = Signal(str)
    error = Signal(str)
    token_count = Signal(int)
    rate_limit_warning = Signal(str)
    auth_error = Signal()
    # 调试模式：phase_name, messages_json, current_endpoint_id, current_model
    prompt_debug_requested = Signal(str, str, str, str)

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 3000,
        reasoning_effort: str = "",
        endpoint_id: str = "",
        debug_mode: bool = False,
        phase_name: str = "审计",
        extra_payload: dict | None = None,
        extra_headers: dict | None = None,
        parent=None,
    ) -> None:
        """初始化审计工作线程。

        Args:
            base_url: API 基础 URL
            api_key: API Key
            model: 模型名
            messages: 已组装好的审计提示词 messages
            temperature: 温度（审计报告用低温稳定输出，默认 0.2）
            max_tokens: 最大 token 数（默认 3000）
            reasoning_effort: 思考强度（OpenAI o 系列/DeepSeek V4 等，空串不发送）
            endpoint_id: 当前端点 ID（调试模式覆盖回传时供 dialog 默认选中）
            debug_mode: 是否开启调试模式（开启后每次 LLM 调用前弹窗确认）
            phase_name: 阶段名（用于调试弹窗标题，如「单章审计」「重写需求分析」）
            extra_payload: 自定义请求体字段（deep merge 到 payload）
            extra_headers: 自定义 HTTP 头（update 到 headers）
            parent: 父 QObject
        """
        super().__init__(parent)
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.messages = messages
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort or ""
        # 自定义请求扩展（透传给 LLMClient）
        self.extra_payload: dict = extra_payload or {}
        self.extra_headers: dict = extra_headers or {}

        # 线程安全停止标志
        self._stop_event = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

        # 调试模式（UI 线程设置，开启后每次 LLM 调用前弹窗确认）
        self._endpoint_id = endpoint_id
        self.debug_mode = debug_mode
        self._phase_name = phase_name
        self._client: Any = None
        self._debug_confirmed: asyncio.Event | None = None
        self._debug_confirmed_result: bool = False
        # 调试覆盖：confirm_debug_prompt 写入，_effective_model/_effective_client 读取
        # 每次 _maybe_debug_prompt 开始时清空，保证覆盖仅对紧接的下一次 LLM 调用生效
        self._debug_override_endpoint: dict | None = None
        self._debug_override_model: str = ""
        self._debug_override_api_key: str = ""
        # 调试覆盖端点的 LLMClient 缓存（endpoint_id → client），避免重复创建 aiohttp session
        self._debug_clients: dict[str, Any] = {}

    def confirm_debug_prompt(
        self,
        confirmed: bool,
        endpoint_override: dict | None = None,
        model_override: str = "",
        api_key_override: str = "",
    ) -> None:
        """UI 线程调用，确认调试提示词弹窗。

        Args:
            confirmed: True=发送，False=取消
            endpoint_override: 覆盖端点 dict（含 base_url 等），None=不覆盖
            model_override: 覆盖模型名，空串=不覆盖
            api_key_override: 覆盖端点的解密 API Key（endpoint_override 非 None 时需提供）
        """
        self._debug_confirmed_result = confirmed
        if confirmed:
            self._debug_override_endpoint = endpoint_override
            self._debug_override_model = model_override
            self._debug_override_api_key = api_key_override
        if self._loop and self._debug_confirmed:
            self._loop.call_soon_threadsafe(self._debug_confirmed.set)

    async def _maybe_debug_prompt(
        self, messages: list[dict[str, Any]], phase_name: str
    ) -> bool:
        """调试模式下弹窗确认提示词。

        若 debug_mode 为 False，直接返回 True。
        若为 True，清空覆盖字段 → emit prompt_debug_requested 信号（含当前
        endpoint_id/model 供 dialog 默认选中）→ 等待 UI 线程确认。
        确认后覆盖字段由 confirm_debug_prompt 写入，供紧接的 LLM 调用经
        _effective_model/_effective_client 读取。

        Args:
            messages: 即将发送的 messages 列表
            phase_name: 阶段名（用于弹窗标题）

        Returns:
            True=确认发送，False=取消
        """
        if not self.debug_mode:
            return True
        if self._debug_confirmed is None:
            return True
        # 清空覆盖字段（保证覆盖仅对紧接的下一次 LLM 调用生效）
        self._debug_override_endpoint = None
        self._debug_override_model = ""
        self._debug_override_api_key = ""
        self._debug_confirmed.clear()
        self._debug_confirmed_result = False
        messages_json = json.dumps(messages, ensure_ascii=False, indent=2)
        self.prompt_debug_requested.emit(
            phase_name, messages_json, self._endpoint_id, self.model
        )
        await self._debug_confirmed.wait()
        return self._debug_confirmed_result

    def _effective_model(self) -> str:
        """返回当前 LLM 调用应使用的模型（调试覆盖优先）。

        Returns:
            覆盖模型名（若有）否则构造时传入的 self.model
        """
        if self._debug_override_model:
            return self._debug_override_model
        return self.model

    def _effective_client(self) -> Any:
        """返回当前 LLM 调用应使用的 client（调试覆盖端点优先）。

        覆盖端点时按 endpoint_id 缓存 LLMClient，避免重复创建 aiohttp session；
        无覆盖时返回主 client。

        Returns:
            LLMClient 实例
        """
        if self._debug_override_endpoint is not None:
            ep_id = self._debug_override_endpoint.get("id", "")
            if ep_id and ep_id in self._debug_clients:
                return self._debug_clients[ep_id]
            client = LLMClient(
                self._debug_override_endpoint.get("base_url", ""),
                self._debug_override_api_key,
                reasoning_effort=self.reasoning_effort,
                extra_payload=self._debug_override_endpoint.get("extra_payload") or {},
                extra_headers=self._debug_override_endpoint.get("extra_headers") or {},
            )
            if ep_id:
                self._debug_clients[ep_id] = client
            return client
        return self._client

    def stop(self) -> None:
        """请求停止流式输出（线程安全）。

        设置停止事件并取消 asyncio 任务。
        """
        self._stop_event.set()
        if self._task and self._loop:
            # 在事件循环线程中取消任务
            self._loop.call_soon_threadsafe(self._task.cancel)

    def run(self) -> None:
        """线程入口：创建独立事件循环并执行流式调用。"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._task = self._loop.create_task(self._async_run())
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            logger.info("审计任务被取消")
        except Exception as e:
            logger.error("审计线程异常: %s", e, exc_info=True)
            self.error.emit(str(e))
        finally:
            # 清理未完成任务
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                if pending and not self._loop.is_closed():
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception as e:
                logger.warning("审计线程清理未完成任务失败: %s", e)
            # 关闭主 LLM 客户端，释放 aiohttp ClientSession
            if self._client is not None:
                try:
                    if not self._loop.is_closed():
                        self._loop.run_until_complete(self._client.close())
                except Exception as e:
                    logger.warning("审计线程关闭主 LLM 客户端失败: %s", e)
            # 关闭调试覆盖端点的缓存 client
            for dbg_client in self._debug_clients.values():
                try:
                    if not self._loop.is_closed():
                        self._loop.run_until_complete(dbg_client.close())
                except Exception as e:
                    logger.warning("审计线程关闭调试 LLM 客户端失败: %s", e)
            self._debug_clients.clear()
            # 关闭事件循环
            self._loop.close()
            self._loop = None
            logger.debug("审计线程事件循环已关闭")

    async def _async_run(self) -> None:
        """异步执行流式调用。"""
        # 创建 asyncio Event 用于停止信号
        async_stop = asyncio.Event()

        # 监听 threading.Event，设置 asyncio.Event
        def check_stop() -> None:
            if self._stop_event.is_set():
                async_stop.set()
            else:
                # 每 100ms 检查一次
                self._loop.call_later(0.1, check_stop)

        check_stop()

        # 调试模式：弹窗确认提示词（取消则 emit error 并返回）
        self._debug_confirmed = asyncio.Event()
        if not await self._maybe_debug_prompt(self.messages, self._phase_name):
            # 用户取消：emit error 重置 UI（非 popup，仅 inline 提示）
            self.error.emit("用户取消调试")
            return

        # 创建主 client（供 _effective_client 无覆盖时返回）
        if self._client is None:
            self._client = LLMClient(
                self.base_url,
                self.api_key,
                reasoning_effort=self.reasoning_effort,
                extra_payload=self.extra_payload,
                extra_headers=self.extra_headers,
            )
        client = self._effective_client()

        content_parts: list[str] = []
        char_count = 0
        status = "completed"

        try:
            async for chunk in client.stream_chat_completion(
                messages=self.messages,
                model=self._effective_model(),
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stop_event=async_stop,
            ):
                if chunk.content:
                    content_parts.append(chunk.content)
                    char_count += len(chunk.content)
                    self.chunk_received.emit(chunk.content)
                    self.token_count.emit(char_count)

                if chunk.finish_reason:
                    if chunk.finish_reason == "done":
                        status = "completed"
                    else:
                        status = "completed"

        except asyncio.CancelledError:
            # 用户停止
            status = "interrupted"
            logger.info("用户停止审计，已接收 %d 字", len("".join(content_parts)))

        except AuthError:
            self.auth_error.emit()
            return

        except RateLimitError as e:
            # 仅发送 rate_limit_warning，避免 UI 弹出两个错误框
            self.rate_limit_warning.emit(f"API 限流，已重试 {client.MAX_RETRIES} 次")
            logger.warning("审计被限流：%s", e)
            return

        except APIError as e:
            self.error.emit(e.body or str(e))
            return

        except (LLMError, asyncio.TimeoutError, ConnectionError, OSError) as e:
            # 网络错误
            status = "interrupted"
            logger.error("审计网络错误: %s", e)
            full = "".join(content_parts)
            if len(full) < MIN_INTERRUPT_CHARS:
                self.error.emit(f"网络错误: {e}")
                return

        full_text = "".join(content_parts)

        # 中断时若内容不足，丢弃并报错
        if status == "interrupted" and len(full_text) < MIN_INTERRUPT_CHARS:
            logger.info(
                "审计中断文本不足 %d 字（%d 字），丢弃",
                MIN_INTERRUPT_CHARS,
                len(full_text),
            )
            self.error.emit(
                f"审计中断，已接收内容不足 {MIN_INTERRUPT_CHARS} 字，已丢弃"
            )
            return

        self.finished.emit(full_text)
