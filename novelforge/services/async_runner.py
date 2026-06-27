"""后台事件循环运行器。

在后台守护线程中运行一个持久 asyncio 事件循环，
供同步代码（如 UI 事件处理函数）提交协程并阻塞等待结果。

这解决了 ``Storage``（基于 aiosqlite）的异步接口与 PySide6 同步 UI 代码的桥接问题：
- aiosqlite 连接绑定到创建它的事件循环，不能跨循环使用
- ``asyncio.run()`` 每次创建新循环，无法复用连接
- 本模块提供单一持久循环，所有协程在其中串行执行
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AsyncLoopRunner:
    """后台事件循环运行器（单例）。

    在守护线程中运行 ``asyncio`` 事件循环，
    通过 :meth:`run` 将协程提交到该循环并阻塞等待结果。

    Usage::

        runner = AsyncLoopRunner.instance()
        result = runner.run(some_async_func(args))
    """

    _instance: "AsyncLoopRunner | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        """初始化后台事件循环线程。"""
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop, name="AsyncLoopRunner", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5)

    @classmethod
    def instance(cls) -> "AsyncLoopRunner":
        """获取单例实例（线程安全）。"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _run_loop(self) -> None:
        """在后台线程中运行事件循环。"""
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            try:
                # 取消所有未完成任务
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
            except Exception:
                pass
            self._loop.close()
            logger.debug("后台事件循环已关闭")

    def run(self, coro: Coroutine[Any, Any, T], timeout: float = 30.0) -> T:
        """提交协程到后台事件循环，阻塞等待结果。

        Args:
            coro: 待执行的协程
            timeout: 超时秒数（默认 30 秒）

        Returns:
            协程返回值

        Raises:
            TimeoutError: 超时
            Exception: 协程内部抛出的异常
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def shutdown(self) -> None:
        """关闭后台事件循环（应用退出时调用）。"""
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
