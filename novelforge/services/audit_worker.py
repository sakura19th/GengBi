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

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 3000,
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
            parent: 父 QObject
        """
        super().__init__(parent)
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.messages = messages
        self.temperature = temperature
        self.max_tokens = max_tokens

        # 线程安全停止标志
        self._stop_event = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

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
                if pending:
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
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

        client = LLMClient(self.base_url, self.api_key)

        content_parts: list[str] = []
        char_count = 0
        status = "completed"

        try:
            async for chunk in client.stream_chat_completion(
                messages=self.messages,
                model=self.model,
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
