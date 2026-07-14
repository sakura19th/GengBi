"""ContinuationWorker：QThread 与 asyncio 桥接。

在 QThread 子类的 ``run()`` 中创建独立 asyncio 事件循环，
执行 LLM 流式调用，通过 Qt 信号将 chunk 实时推送到 UI 线程。

特性：
- 信号：``chunk_received(str)``、``reasoning_received(str)``、
  ``finished(Continuation)``、``error(str)``、``token_count(int)``
- 线程安全停止：``threading.Event`` + ``asyncio.Task.cancel()`` 双重中断
- 流式中断时 ≥100 字文本作为 swipe（status=interrupted），<100 字丢弃
- QThread 退出时 asyncio 事件循环正确关闭（loop.close()、aiohttp session 清理）

M2 续写流程（基于 PromptAssembler 组装提示词）：
- 调用 PromptAssembler 组装 messages（三阶段：排序 → 注入 → 裁剪）
- 宏替换在组装阶段完成，Jinja2/正则在 M3 接入
- 状态栏显示 token 预算使用情况
- 兼容 M1 的简单 messages 组装（assemble_simple_messages 保留）
"""
from __future__ import annotations

import aiohttp
import asyncio
import json
import logging
import threading
from datetime import datetime
from typing import Any

from PySide6.QtCore import QThread, Signal

from novelforge.models import Chapter, Continuation
from novelforge.services.llm_client import (
    APIError,
    AuthError,
    LLMClient,
    LLMError,
    RateLimitError,
    StreamResult,
)
from novelforge.services.storage_service import _generate_id
from novelforge.core.post_processor import post_process_content

logger = logging.getLogger(__name__)

# 默认 system 提示词
DEFAULT_SYSTEM_PROMPT = (
    "你是一个专业的小说续写助手。请根据提供的前文章节内容，"
    "自然地续写故事。保持人物性格一致、文风统一、情节连贯。"
    "直接输出续写内容，不要添加任何解释说明。"
)

# 流式中断保留的最小字符数
MIN_INTERRUPT_CHARS = 100

# HTML 标签特征检测模式
# 匹配 < 后跟字母、/ 或 !（真实 HTML 标签），或 /?word>（半残标签碎片）
import re as _re

_HTML_PATTERN = _re.compile(r"<[a-zA-Z/!]|/?[a-zA-Z_]\w*>")


def _contains_html(text: str) -> bool:
    """检测文本是否含 HTML 标签特征。

    匹配真实 HTML 标签（``<div>``、``</p>``、``<!--``）和半残标签碎片
    （``cliche>``、``ai_last_output>``，由 trimStrings 剥离 ``<`` 后产生）。
    不匹配数学表达式如 ``x < 5``（``<`` 后跟空格）。

    Args:
        text: 待检测文本

    Returns:
        含 HTML 特征返回 True，纯文本返回 False
    """
    return bool(_HTML_PATTERN.search(text))


def assemble_simple_messages(
    chapters: list[Chapter],
    current_chapter: Chapter,
    lookback: int = 5,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> list[dict[str, Any]]:
    """组装 M1 简单提示词 messages。

    - 取当前章节前 N 章（默认 5）作为历史
    - 每章作为一条 user 消息，content 格式为 ``{章节标题}\\n{章节正文}``
    - 加一条 system 消息作为写作指导

    Args:
        chapters: 项目所有章节（按 index 排序）
        current_chapter: 当前续写章节
        lookback: 回溯章节数
        system_prompt: 系统提示词

    Returns:
        messages 列表
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt}
    ]

    # 找到当前章节在列表中的位置
    sorted_chapters = sorted(chapters, key=lambda c: c.index)
    current_idx = -1
    for i, ch in enumerate(sorted_chapters):
        if ch.id == current_chapter.id:
            current_idx = i
            break

    if current_idx == -1:
        # 当前章节不在列表中，只发送当前章节
        messages.append({
            "role": "user",
            "content": f"{current_chapter.title}\n{current_chapter.content}",
        })
        return messages

    # lookback <= 0 表示全部前文：返回从第 0 章到当前章节
    if lookback <= 0:
        history = sorted_chapters[: current_idx + 1]
    else:
        # 取前 lookback 章（含当前章节）
        start = max(0, current_idx - lookback + 1)
        history = sorted_chapters[start : current_idx + 1]

    for ch in history:
        messages.append({
            "role": "user",
            "content": f"{ch.title}\n{ch.content}",
        })

    return messages


class ContinuationWorker(QThread):
    """续写工作线程。

    在独立 QThread 中运行 asyncio 事件循环，执行 LLM 流式调用。
    通过 Qt 信号将结果推送到 UI 线程。

    Signals:
        chunk_received(str): 正文增量 chunk
        reasoning_received(str): 推理内容增量 chunk
        finished(Continuation): 流式完成（含中断），返回完整 Continuation
        error(str): 错误信息
        token_count(int): token 计数（已接收字符数）
        rate_limit_warning(str): 限流提示
        auth_error(): 认证失败，需跳转设置
    """

    chunk_received = Signal(str)
    reasoning_received = Signal(str)
    finished = Signal(object)  # Continuation 对象
    error = Signal(str)
    token_count = Signal(int)
    rate_limit_warning = Signal(str)
    auth_error = Signal()
    # M2 新增：token 预算信息（用于状态栏显示）
    token_budget_info = Signal(dict)
    # 调试模式：phase_name, messages_json, current_endpoint_id, current_model
    prompt_debug_requested = Signal(str, str, str, str)

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        parameters: dict[str, Any],
        chapter_id: str,
        created_by: str = "continuation",
        preset_id: str = "",
        preset_snapshot: dict[str, Any] | None = None,
        token_budget: dict[str, int] | None = None,
        regex_engine: Any | None = None,
        template_engine: Any | None = None,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
        regex_script_ids: list[str] | None = None,
        extracted_context_snapshot: list[dict[str, Any]] | None = None,
        parent_continuation_id: str | None = None,
        endpoint_id: str = "",
        debug_mode: bool = False,
        extra_payload: dict | None = None,
        extra_headers: dict | None = None,
        parent=None,
    ) -> None:
        """初始化续写工作线程。

        Args:
            base_url: API 基础 URL
            api_key: API Key
            model: 模型名
            messages: 提示词 messages 列表（已由 PromptAssembler 组装）
            parameters: 生成参数（temperature/max_tokens/target_words 等）
            chapter_id: 所属章节 ID
            created_by: 创建方式（continuation/rewrite）
            preset_id: 使用的预设 ID（用于 swipe 快照）
            preset_snapshot: 预设内容快照（用于 swipe 快照）
            token_budget: token 预算信息（用于状态栏显示）
            regex_engine: 正则引擎（M3: 用于 AI_OUTPUT 正则后处理）
            template_engine: 模板引擎（M3: 用于接收后模板渲染）
            project_id: 项目 ID（用于模板渲染上下文）
            chapter_metadata: 章节元数据（用于模板渲染上下文）
            regex_script_ids: 正则脚本 ID 列表快照
            extracted_context_snapshot: 提取的上下文条目快照（M4: 存入 swipe）
            parent_continuation_id: 链式续写父续写 id（None=章节直接子节点）
            endpoint_id: 当前端点 ID（调试模式覆盖回传时供 dialog 默认选中）
            debug_mode: 是否开启调试模式（开启后每次 LLM 调用前弹窗确认）
            extra_payload: 自定义请求体字段（deep merge 到 payload）
            extra_headers: 自定义 HTTP 头（update 到 headers）
            parent: 父 QObject
        """
        super().__init__(parent)
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.messages = messages
        self.parameters = parameters
        # 自定义请求扩展（透传给 LLMClient）
        self.extra_payload: dict = extra_payload or {}
        self.extra_headers: dict = extra_headers or {}
        self.chapter_id = chapter_id
        self.created_by = created_by
        self.preset_id = preset_id
        self.preset_snapshot = preset_snapshot or {}
        self.token_budget = token_budget or {}
        # M3 新增：正则与模板引擎
        self.regex_engine = regex_engine
        self.template_engine = template_engine
        self.project_id = project_id
        self.chapter_metadata = chapter_metadata or {}
        self.regex_script_ids = regex_script_ids or []
        # M4 新增：提取的上下文条目快照
        self.extracted_context_snapshot: list[dict[str, Any]] = (
            extracted_context_snapshot if extracted_context_snapshot is not None else []
        )
        # 链式续写：父续写 id（None=章节直接子节点）
        self.parent_continuation_id = parent_continuation_id

        # 线程安全停止标志
        self._stop_event = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

        # 调试模式（UI 线程设置，开启后每次 LLM 调用前弹窗确认）
        self._endpoint_id = endpoint_id
        self.debug_mode = debug_mode
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
                reasoning_effort=self.parameters.get("reasoning_effort", ""),
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
        """线程入口：创建独立事件循环并执行流式调用。

        QThread 的 ``run()`` 在新线程中执行，
        在此创建独立 asyncio 事件循环，避免与主线程的 Qt 事件循环冲突。
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._task = self._loop.create_task(self._async_run())
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            logger.info("续写任务被取消")
        except Exception as e:
            logger.error("续写线程异常: %s", e, exc_info=True)
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
                logger.warning("续写线程清理未完成任务失败: %s", e)
            # 关闭主 LLM 客户端，释放 aiohttp ClientSession
            if self._client is not None:
                try:
                    if not self._loop.is_closed():
                        self._loop.run_until_complete(self._client.close())
                except Exception as e:
                    logger.warning("续写线程关闭主 LLM 客户端失败: %s", e)
            # 关闭调试覆盖端点的缓存 client
            for dbg_client in self._debug_clients.values():
                try:
                    if not self._loop.is_closed():
                        self._loop.run_until_complete(dbg_client.close())
                except Exception as e:
                    logger.warning("续写线程关闭调试 LLM 客户端失败: %s", e)
            self._debug_clients.clear()
            # 关闭事件循环
            self._loop.close()
            self._loop = None
            logger.debug("续写线程事件循环已关闭")

    async def _async_run(self) -> None:
        """异步执行流式调用。"""
        # 发送 token 预算信息到 UI
        if self.token_budget:
            self.token_budget_info.emit(self.token_budget)

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
        phase_name = {
            "continuation": "续写",
            "rewrite_current": "重写生成",
            "audit_rewrite": "修正",
        }.get(self.created_by, "续写")
        if not await self._maybe_debug_prompt(self.messages, phase_name):
            # 用户取消：emit error 重置 UI（非 popup，仅 inline 提示）
            self.error.emit("用户取消调试")
            return

        # 创建主 client（供 _effective_client 无覆盖时返回）
        if self._client is None:
            self._client = LLMClient(
                self.base_url,
                self.api_key,
                reasoning_effort=self.parameters.get("reasoning_effort", ""),
                extra_payload=self.extra_payload,
                extra_headers=self.extra_headers,
            )
        client = self._effective_client()

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        char_count = 0
        result = StreamResult(status="completed")

        try:
            async for chunk in client.stream_chat_completion(
                messages=self.messages,
                model=self._effective_model(),
                temperature=self.parameters.get("temperature", 0.8),
                max_tokens=self.parameters.get("max_tokens"),
                top_p=self.parameters.get("top_p", 1.0),
                frequency_penalty=self.parameters.get("frequency_penalty", 0.0),
                presence_penalty=self.parameters.get("presence_penalty", 0.0),
                stop_event=async_stop,
            ):
                if chunk.content:
                    content_parts.append(chunk.content)
                    char_count += len(chunk.content)
                    self.chunk_received.emit(chunk.content)
                    self.token_count.emit(char_count)

                if chunk.reasoning_content:
                    reasoning_parts.append(chunk.reasoning_content)
                    self.reasoning_received.emit(chunk.reasoning_content)

                if chunk.finish_reason:
                    result.finish_reason = chunk.finish_reason
                    if chunk.finish_reason == "done":
                        result.status = "completed"
                    else:
                        result.status = "completed"

            result.content = "".join(content_parts)
            result.reasoning_content = "".join(reasoning_parts)

        except asyncio.CancelledError:
            # 用户停止
            result.content = "".join(content_parts)
            result.reasoning_content = "".join(reasoning_parts)
            result.status = "interrupted"
            result.interrupt_reason = "user_stopped"
            logger.info("用户停止续写，已接收 %d 字", len(result.content))

        except AuthError:
            self.auth_error.emit()
            return

        except RateLimitError as e:
            # 仅发送 rate_limit_warning，避免 UI 弹出两个错误框
            self.rate_limit_warning.emit(f"API 限流，已重试 {client.MAX_RETRIES} 次")
            result.content = "".join(content_parts)
            result.status = "failed"
            result.interrupt_reason = "api_error"
            logger.warning("续写被限流：%s", e)
            return

        except APIError as e:
            result.content = "".join(content_parts)
            result.status = "failed"
            result.interrupt_reason = "api_error"
            self.error.emit(e.body or str(e))
            return

        except (LLMError, aiohttp.ClientError, asyncio.TimeoutError, ConnectionError, OSError) as e:
            # 仅捕获预期的运行时/网络错误，编程错误（KeyError、
            # AttributeError、TypeError、ValueError 等）向上传播，避免掩盖真实 bug
            result.content = "".join(content_parts)
            result.status = "interrupted"
            result.interrupt_reason = "network_error"
            logger.error("续写网络错误: %s", e)
            # 网络错误时，如果有足够内容，仍作为 interrupted swipe
            if len(result.content) < MIN_INTERRUPT_CHARS:
                self.error.emit(f"网络错误: {e}")
                return

        # 处理中断情况：≥100 字保留为 swipe，<100 字丢弃
        if result.status == "interrupted" and len(result.content) < MIN_INTERRUPT_CHARS:
            logger.info("中断文本不足 %d 字（%d 字），丢弃", MIN_INTERRUPT_CHARS, len(result.content))
            self.error.emit(f"续写中断，已接收内容不足 {MIN_INTERRUPT_CHARS} 字，已丢弃")
            return

        # M3: 对结果应用 AI_OUTPUT 正则和接收后模板渲染，再剥离 HTML
        # 流水线抽取至 post_process_content（与 AgentOrchestrator 共用）

        # 在后处理剥离标签之前，提取 <novelforge_title> 章节标题
        title_match = _re.search(
            r"<novelforge_title>\s*(.*?)\s*</novelforge_title>",
            result.content, _re.DOTALL,
        )
        generated_title = title_match.group(1).strip() if title_match else ""

        final_content = post_process_content(
            result.content,
            regex_engine=self.regex_engine,
            template_engine=self.template_engine,
            project_id=self.project_id,
            chapter_metadata=self.chapter_metadata,
        )

        # 构建 Continuation 对象
        continuation = Continuation(
            id=_generate_id("sw_"),
            created_at=datetime.now(),
            content=final_content,
            model=self._effective_model(),
            is_accepted=False,
            parent_id=self.parent_continuation_id,
            status=result.status,
            created_by=self.created_by,
            parameters_snapshot=dict(self.parameters),
            preset_id=self.preset_id,
            preset_snapshot=self.preset_snapshot,
            regex_script_ids_snapshot=list(self.regex_script_ids),
            extracted_context_snapshot=list(self.extracted_context_snapshot),
            prompt_snapshot=list(self.messages),
            reasoning_content=result.reasoning_content if result.reasoning_content else None,
            generated_title=generated_title,
        )

        self.finished.emit(continuation)
