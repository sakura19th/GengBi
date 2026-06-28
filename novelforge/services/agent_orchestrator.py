"""AgentOrchestrator：多阶段 Agent 续写编排器。

镜像 ContinuationWorker 的 QThread+asyncio 模式，在独立 QThread 中运行
asyncio 事件循环，按 AgentRunConfig.phases 顺序执行 5 个阶段：
analysis → outline → writing → verify → revise。

特性：
- 信号：phase_started/phase_finished/chunk_received/reasoning_received/
  checkpoint_reached/finished/error/auth_error/token_count
- 线程安全停止：threading.Event + asyncio.Task.cancel 双重中断
- 暂停点（checkpoint）：after_outline/after_verify，UI 线程通过 resume() 恢复
- 优雅降级：JSON 解析失败重试一次（温度归零），再失败跳过该阶段
- 大纲注入：格式化为 Markdown，通过 ContextEntry 或 fallback prepend 注入
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime
from typing import Any

from PySide6.QtCore import QThread, Signal

from novelforge.core.json_utils import parse_json_response
from novelforge.core.prompt_assembler import PromptAssembler
from novelforge.models import (
    AgentArtifacts,
    AgentRunConfig,
    Chapter,
    ContextEntry,
    Continuation,
    CritiqueReport,
    Outline,
    StorySnapshot,
    WritingPreset,
)
from novelforge.services.llm_client import (
    APIError,
    AuthError,
    LLMClient,
    LLMError,
    RateLimitError,
)
from novelforge.services.storage_service import _generate_id
from novelforge.core.post_processor import post_process_content
from novelforge.utils.paths import get_agent_prompt_path, load_text_resource

logger = logging.getLogger(__name__)


class AgentOrchestrator(QThread):
    """多阶段 Agent 续写编排器。

    在独立 QThread 中运行 asyncio 事件循环，按 config.phases 顺序执行
    5 个阶段。每阶段开始 emit phase_started，结束 emit phase_finished。
    写作阶段流式输出 chunk_received/reasoning_received/token_count。
    暂停点 emit checkpoint_reached，UI 线程调 resume() 恢复。

    Signals:
        phase_started(str): 阶段名（analysis/outline/writing/verify/revise）
        phase_finished(str, object): 阶段名, 产物对象
        chunk_received(str): 写作阶段正文增量
        reasoning_received(str): 推理内容增量
        checkpoint_reached(str, object): 检查点名, 产物对象
        finished(object): Continuation 对象
        error(str): 错误信息
        auth_error(): 认证失败
        token_count(int): 已接收字符数
    """

    phase_started = Signal(str)
    phase_finished = Signal(str, object)
    chunk_received = Signal(str)
    reasoning_received = Signal(str)
    checkpoint_reached = Signal(str, object)
    finished = Signal(object)
    error = Signal(str)
    auth_error = Signal()
    token_count = Signal(int)
    prompt_debug_requested = Signal(str, str)

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        parameters: dict[str, Any],
        preset: WritingPreset,
        chapters: list[Chapter],
        current_chapter: Chapter,
        context_entries: list[ContextEntry],
        config: AgentRunConfig,
        user_input: str = "",
        novel_profile: Any = None,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
        regex_engine: Any | None = None,
        template_engine: Any | None = None,
        regex_script_ids: list[str] | None = None,
        preset_id: str = "",
        preset_snapshot: dict[str, Any] | None = None,
        chapter_id: str = "",
        parent=None,
    ) -> None:
        """初始化 Agent 编排器。

        Args:
            base_url: API 基础 URL
            api_key: API Key
            model: 模型名
            parameters: 生成参数（temperature/max_tokens 等）
            preset: 写作预设
            chapters: 所有章节列表
            current_chapter: 当前续写章节
            context_entries: 上下文条目列表
            config: Agent 运行配置（阶段开关/暂停点/修订轮次）
            user_input: 用户续写指令
            novel_profile: 小说档案
            project_id: 项目 ID
            chapter_metadata: 章节元数据
            regex_engine: 正则引擎（AI_OUTPUT 后处理）
            template_engine: 模板引擎（接收后渲染）
            regex_script_ids: 正则脚本 ID 列表快照
            preset_id: 预设 ID
            preset_snapshot: 预设内容快照
            chapter_id: 章节 ID
            parent: 父 QObject
        """
        super().__init__(parent)
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.parameters = parameters
        self.preset = preset
        self.chapters = chapters
        self.current_chapter = current_chapter
        self.context_entries = context_entries
        self.config = config
        self.user_input = user_input
        self.novel_profile = novel_profile
        self.project_id = project_id
        self.chapter_metadata = chapter_metadata or {}
        self.regex_engine = regex_engine
        self.template_engine = template_engine
        self.regex_script_ids = regex_script_ids or []
        self.preset_id = preset_id
        self.preset_snapshot = preset_snapshot or {}
        self.chapter_id = chapter_id

        # Find current chapter position for lookback computation
        self._current_chapter_index = -1
        for i, ch in enumerate(self.chapters):
            if ch.id == current_chapter.id:
                self._current_chapter_index = i
                break

        # 线程安全停止
        self._stop_event = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

        # 暂停点恢复（在 run() 的事件循环中创建）
        self._resume_event: asyncio.Event | None = None
        self._checkpoint_payload: Any = None

        # LLM 客户端（在 _async_run 中创建，可注入用于测试）
        self._client: Any = None

        # 写作阶段 messages 快照（供 _record_history）
        self._writing_messages: list[dict[str, Any]] = []
        self._writing_model: str = ""

        # 章节文本缓存（analysis 与 outline 阶段共用，避免重复拼接）
        self._chapters_text: str = ""

        # 提示词组装器
        self._prompt_assembler = PromptAssembler(
            regex_engine=regex_engine,
            template_engine=template_engine,
        )

        # 调试模式（UI 线程设置，开启后每次 LLM 调用前弹窗确认）
        self.debug_mode: bool = False
        self._debug_confirmed: asyncio.Event | None = None
        self._debug_confirmed_result: bool = False

    def stop(self) -> None:
        """请求停止 Agent 流程（线程安全）。

        设置停止事件并取消 asyncio 任务。
        """
        self._stop_event.set()
        if self._task and self._loop:
            self._loop.call_soon_threadsafe(self._task.cancel)

    def resume(self, payload: Any = None) -> None:
        """UI 线程调用，恢复暂停的 agent 流程。

        Args:
            payload: checkpoint 产物（如编辑后的大纲）
        """
        self._checkpoint_payload = payload
        if self._loop and self._resume_event:
            self._loop.call_soon_threadsafe(self._resume_event.set)

    def confirm_debug_prompt(self, confirmed: bool) -> None:
        """UI 线程调用，确认调试提示词弹窗。

        Args:
            confirmed: True=发送，False=取消
        """
        self._debug_confirmed_result = confirmed
        if self._loop and self._debug_confirmed:
            self._loop.call_soon_threadsafe(self._debug_confirmed.set)

    async def _maybe_debug_prompt(
        self, messages: list[dict[str, Any]], phase_name: str
    ) -> bool:
        """调试模式下弹窗确认提示词。

        若 debug_mode 为 False，直接返回 True。
        若为 True，emit prompt_debug_requested 信号，等待 UI 线程确认。

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
        self._debug_confirmed.clear()
        self._debug_confirmed_result = False
        messages_json = json.dumps(messages, ensure_ascii=False, indent=2)
        self.prompt_debug_requested.emit(phase_name, messages_json)
        await self._debug_confirmed.wait()
        return self._debug_confirmed_result

    def get_writing_messages(self) -> list[dict[str, Any]]:
        """获取写作阶段的 messages 快照（供历史日志记录）。

        Returns:
            写作阶段发送给 LLM 的 messages 列表副本
        """
        return list(self._writing_messages)

    def get_writing_model(self) -> str:
        """获取写作阶段使用的模型名（供历史日志记录）。

        Returns:
            模型名
        """
        return self._writing_model

    def run(self) -> None:
        """线程入口：创建独立事件循环并执行 Agent 流程。"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._task = self._loop.create_task(self._async_run())
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            logger.info("Agent 任务被取消")
        except Exception as e:
            logger.error("Agent 线程异常: %s", e, exc_info=True)
            self.error.emit(str(e))
        finally:
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
            self._loop.close()
            self._loop = None
            logger.debug("Agent 线程事件循环已关闭")

    async def _async_run(self) -> None:
        """异步执行多阶段 Agent 流程。"""
        self._loop = asyncio.get_running_loop()
        self._resume_event = asyncio.Event()
        self._debug_confirmed = asyncio.Event()

        if self._client is None:
            self._client = LLMClient(self.base_url, self.api_key)

        artifacts = AgentArtifacts()
        final_content = ""
        reasoning_content = ""

        # 预构建章节文本缓存（analysis 与 outline 阶段共用，避免重复拼接）
        self._chapters_text = self._build_chapters_text()

        try:
            # ===== 阶段 1：前文分析 =====
            if self.config.phases.get("analysis"):
                self.phase_started.emit("analysis")
                snapshot = await self._run_analysis()
                artifacts.snapshot = snapshot
                self.phase_finished.emit("analysis", snapshot)

            # ===== 阶段 2：大纲规划 =====
            if self.config.phases.get("outline"):
                self.phase_started.emit("outline")
                outline = await self._run_outline(artifacts.snapshot)
                artifacts.outline = outline
                self.phase_finished.emit("outline", outline)

                # 暂停点 after_outline
                if self.config.checkpoints.get("after_outline") and outline is not None:
                    self.checkpoint_reached.emit("after_outline", outline)
                    edited = await self._wait_for_resume("after_outline")
                    if edited is not None and isinstance(edited, Outline):
                        artifacts.outline = edited

            # ===== 阶段 3：续写 =====
            if self.config.phases.get("writing"):
                self.phase_started.emit("writing")
                final_content, reasoning_content, messages = (
                    await self._run_writing(artifacts.outline)
                )
                self._writing_messages = messages
                self._writing_model = self.model
                self.phase_finished.emit("writing", final_content)

            # ===== 阶段 4：验证 =====
            if self.config.phases.get("verify"):
                self.phase_started.emit("verify")
                critique = await self._run_verify(
                    artifacts.snapshot, artifacts.outline, final_content
                )
                artifacts.critique = critique
                artifacts.final_critique = critique
                self.phase_finished.emit("verify", critique)

                # 暂停点 after_verify
                if (
                    self.config.checkpoints.get("after_verify")
                    and critique is not None
                ):
                    self.checkpoint_reached.emit("after_verify", critique)
                    await self._wait_for_resume("after_verify")

            # ===== 阶段 5：修订循环 =====
            rounds = 0
            current_critique = artifacts.critique
            while (
                self.config.phases.get("revise")
                and current_critique is not None
                and not current_critique.passed
                and rounds < self.config.max_revise_rounds
            ):
                rounds += 1
                self.phase_started.emit("revise")
                guidance = await self._run_revise(
                    final_content, current_critique, artifacts.outline
                )
                final_content, reasoning_content, messages = (
                    await self._run_writing(artifacts.outline, guidance, original_content=final_content)
                )
                self._writing_messages = messages
                new_critique = await self._run_verify(
                    artifacts.snapshot, artifacts.outline, final_content
                )
                current_critique = new_critique
                artifacts.final_critique = new_critique
                artifacts.revision_rounds = rounds
                self.phase_finished.emit("revise", new_critique)

            # ===== 构建 Continuation =====
            continuation = Continuation(
                id=_generate_id("sw_"),
                created_at=datetime.now(),
                content=final_content,
                model=self.model,
                is_accepted=False,
                status="completed",
                created_by="agent",
                parameters_snapshot=dict(self.parameters),
                preset_id=self.preset_id,
                preset_snapshot=self.preset_snapshot,
                regex_script_ids_snapshot=list(self.regex_script_ids),
                extracted_context_snapshot=list(self.context_entries),
                prompt_snapshot=self._writing_messages,
                reasoning_content=reasoning_content or None,
                agent_artifacts=artifacts,
            )
            self.finished.emit(continuation)

        except asyncio.CancelledError:
            logger.info("Agent 任务被取消")
            raise
        except AuthError:
            self.auth_error.emit()
        except (RateLimitError, APIError) as e:
            self.error.emit(str(e))
        except Exception as e:
            logger.error("Agent 异常: %s", e, exc_info=True)
            self.error.emit(str(e))

    async def _run_analysis(self) -> StorySnapshot | None:
        """阶段①：前文分析，产出 StorySnapshot。

        失败/解析失败重试一次（温度归零），再失败返回 None。
        """
        template = self._load_template("analysis")
        chapters_text = self._chapters_text
        macros = {
            "{{title}}": self._get_profile_field("title"),
            "{{author}}": self._get_profile_field("author"),
            "{{protagonist}}": self._get_profile_field("protagonist"),
            "{{synopsis}}": self._get_profile_field("synopsis"),
            "{{world_setting}}": self._get_profile_field("world_setting"),
            "{{writing_style}}": self._get_profile_field("writing_style"),
            "{{chapters_text}}": chapters_text,
        }
        system_prompt = self._apply_macros(template, macros)
        messages = [{"role": "system", "content": system_prompt}]

        # 调试模式确认
        if not await self._maybe_debug_prompt(messages, "前文分析"):
            return None

        for attempt in range(2):
            temperature = 0.3 if attempt == 0 else 0.0
            try:
                response = await self._client.chat_completion(
                    messages, self.model, temperature=temperature, max_tokens=3000
                )
                content = response["choices"][0]["message"]["content"]
                data = parse_json_response(content)
                return StorySnapshot.model_validate(data)
            except AuthError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Analysis 失败 (attempt %d): %s", attempt + 1, e)
                continue
        return None

    async def _run_outline(
        self, snapshot: StorySnapshot | None
    ) -> Outline | None:
        """阶段③：大纲规划，产出 Outline。

        若 snapshot 为 None，{{snapshot}} 替换为提示文本。
        失败重试一次，再失败返回 None。
        """
        template = self._load_template("outline")
        chapters_text = self._build_lookback_chapters_text()
        if snapshot is not None:
            snapshot_text = json.dumps(
                snapshot.model_dump(), ensure_ascii=False, indent=2
            )
        else:
            snapshot_text = "（无前文分析，参考上下文条目）"
        macros = {
            "{{snapshot}}": snapshot_text,
            "{{chapters_text}}": chapters_text,
            "{{user_input}}": self.user_input or "",
        }
        system_prompt = self._apply_macros(template, macros)
        messages = [{"role": "system", "content": system_prompt}]

        # 调试模式确认
        if not await self._maybe_debug_prompt(messages, "大纲规划"):
            return None

        for attempt in range(2):
            temperature = 0.3 if attempt == 0 else 0.0
            try:
                response = await self._client.chat_completion(
                    messages, self.model, temperature=temperature, max_tokens=3000
                )
                content = response["choices"][0]["message"]["content"]
                data = parse_json_response(content)
                return Outline.model_validate(data)
            except AuthError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Outline 失败 (attempt %d): %s", attempt + 1, e)
                continue
        return None

    async def _run_writing(
    self,
    outline: Outline | None,
    revision_guidance: dict | None = None,
    original_content: str = "",
) -> tuple[str, str, list[dict[str, Any]]]:
        """阶段④：续写，流式输出。

        将 outline 格式化为 Markdown，通过 ContextEntry 或 fallback 注入。
        revision_guidance 非空时追加为 user 消息（修订重写）。

        Returns:
            (final_content, reasoning_content, messages) 三元组
        """
        # 格式化大纲并创建合成 ContextEntry
        if outline is not None:
            outline_markdown = self._format_outline(outline)
            outline_entry = ContextEntry(
                uid="agent_outline",
                category="plot_state",
                content=outline_markdown,
                position="before",
                role="system",
                order=0,
            )
            all_entries = [outline_entry] + list(self.context_entries)
        else:
            outline_markdown = ""
            all_entries = list(self.context_entries)

        # 检测 preset 是否有 worldInfoBefore marker
        has_marker = self._has_world_info_before_marker()

        # 组装提示词
        result = self._prompt_assembler.assemble(
            preset=self.preset,
            chapters=self.chapters,
            current_chapter=self.current_chapter,
            context_entries=all_entries,
            model=self.model,
            max_context=self.parameters.get("max_context", 9999999),
            max_tokens=self.parameters.get("max_tokens", 2000),
            target_words=self.parameters.get("target_words", 2000),
            novel_profile=self.novel_profile,
            project_id=self.project_id,
            chapter_metadata=self.chapter_metadata,
            user_input=self.user_input,
            lookback_chapters=self.parameters.get("lookback_chapters", 0),
        )
        messages = list(result.messages)

        # Fallback：无 marker 时 prepend 大纲为 system 消息
        if outline is not None and not has_marker:
            messages.insert(0, {"role": "system", "content": outline_markdown})

        # 修订指导追加为 user 消息
        if revision_guidance is not None:
            guidance_text = json.dumps(
                revision_guidance, ensure_ascii=False, indent=2
            )
            if original_content:
                messages.append(
                    {
                        "role": "user",
                        "content": f"以下是当前已生成的内容，请根据修订指导重写：\n\n{original_content}\n\n修订指导：\n{guidance_text}",
                    }
                )
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": f"请根据以下修订指导重写续写内容：\n\n{guidance_text}",
                    }
                )

        # 调试模式确认
        if not await self._maybe_debug_prompt(messages, "续写写作"):
            return "", "", []

        # 创建 async stop 事件（轮询 threading.Event）
        async_stop = asyncio.Event()

        def check_stop() -> None:
            if self._stop_event.is_set():
                async_stop.set()
            else:
                self._loop.call_later(0.1, check_stop)

        check_stop()

        # 流式调用
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        char_count = 0

        async for chunk in self._client.stream_chat_completion(
            messages=messages,
            model=self.model,
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

        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)

        # 后处理（正则 → 模板 → HTML 剥离）
        final_content = self._post_process(content)

        return final_content, reasoning, messages

    async def _run_verify(
        self,
        snapshot: StorySnapshot | None,
        outline: Outline | None,
        written_text: str,
    ) -> CritiqueReport | None:
        """阶段⑤：验证，产出 CritiqueReport。

        失败重试一次，再失败返回 None（视为通过，不阻塞）。
        """
        template = self._load_template("verify")
        if snapshot is not None:
            snapshot_text = json.dumps(
                snapshot.model_dump(), ensure_ascii=False, indent=2
            )
        else:
            snapshot_text = "（无前文分析）"
        if outline is not None:
            outline_text = json.dumps(
                outline.model_dump(), ensure_ascii=False, indent=2
            )
        else:
            outline_text = "（无大纲）"
        macros = {
            "{{snapshot}}": snapshot_text,
            "{{outline}}": outline_text,
            "{{written_text}}": written_text,
            "{{previous_chapters_text}}": self._build_lookback_chapters_text(),
        }
        system_prompt = self._apply_macros(template, macros)
        messages = [{"role": "system", "content": system_prompt}]

        # 调试模式确认
        if not await self._maybe_debug_prompt(messages, "质量验证"):
            return None

        for attempt in range(2):
            temperature = 0.2 if attempt == 0 else 0.0
            try:
                response = await self._client.chat_completion(
                    messages, self.model, temperature=temperature, max_tokens=3000
                )
                content = response["choices"][0]["message"]["content"]
                data = parse_json_response(content)
                return CritiqueReport.model_validate(data)
            except AuthError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Verify 失败 (attempt %d): %s", attempt + 1, e)
                continue
        return None

    async def _run_revise(
        self,
        written_text: str,
        critique: CritiqueReport | None,
        outline: Outline | None,
    ) -> dict:
        """产出修订指导（non-stream JSON，宽松 dict）。

        失败重试一次，再失败返回空 dict。
        """
        template = self._load_template("revise")
        if critique is not None:
            critique_text = json.dumps(
                critique.model_dump(), ensure_ascii=False, indent=2
            )
        else:
            critique_text = "（无评审报告）"
        if outline is not None:
            outline_text = json.dumps(
                outline.model_dump(), ensure_ascii=False, indent=2
            )
        else:
            outline_text = "（无大纲）"
        macros = {
            "{{written_text}}": written_text,
            "{{critique}}": critique_text,
            "{{outline}}": outline_text,
        }
        system_prompt = self._apply_macros(template, macros)
        messages = [{"role": "system", "content": system_prompt}]

        # 调试模式确认
        if not await self._maybe_debug_prompt(messages, "修订指导"):
            return {}

        for attempt in range(2):
            temperature = 0.3 if attempt == 0 else 0.0
            try:
                response = await self._client.chat_completion(
                    messages, self.model, temperature=temperature, max_tokens=3000
                )
                content = response["choices"][0]["message"]["content"]
                return parse_json_response(content)
            except AuthError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Revise 失败 (attempt %d): %s", attempt + 1, e)
                continue
        return {}

    async def _wait_for_resume(self, checkpoint_name: str) -> Any:
        """暂停等待用户操作，返回 checkpoint_payload。

        每 0.5s 轮询 _stop_event，被停止时抛 CancelledError。
        """
        # 如果 resume 在等待前已被调用，直接返回已有 payload（避免竞态丢失）
        if self._checkpoint_payload is not None:
            payload = self._checkpoint_payload
            self._checkpoint_payload = None
            return payload
        # 清理可能残留的事件状态（上次 early-return 可能遗留 stale set）
        self._resume_event.clear()
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._resume_event.wait(), timeout=0.5)
                self._resume_event.clear()
                payload = self._checkpoint_payload
                self._checkpoint_payload = None
                return payload
            except asyncio.TimeoutError:
                continue
        raise asyncio.CancelledError("用户在暂停点取消")

    def _apply_macros(self, template: str, macros: dict[str, str]) -> str:
        """用 str.replace 替换模板占位符。"""
        result = template
        for placeholder, value in macros.items():
            result = result.replace(placeholder, value)
        return result

    def _load_template(self, phase: str) -> str:
        """加载阶段提示词模板。"""
        path = get_agent_prompt_path(phase)
        return load_text_resource(path)

    def _build_chapters_text(self) -> str:
        """拼接章节文本：`## {标题}\n\n{正文}`。"""
        parts: list[str] = []
        for ch in self.chapters:
            parts.append(f"## {ch.title}\n\n{ch.content}")
        return "\n\n".join(parts)

    def _build_lookback_chapters_text(self, max_chapters: int = 10) -> str:
        """Build text from chapters before current chapter (lookback, inclusive).

        Takes chapters[max(0, idx-9):idx+1] (10 chapters including current).
        Falls back to all chapters if current chapter not found.

        Args:
            max_chapters: Maximum number of chapters to include (default 10)

        Returns:
            Joined chapter text in "## {title}\\n\\n{content}" format
        """
        if self._current_chapter_index >= 0:
            start = max(0, self._current_chapter_index - max_chapters + 1)
            lookback = self.chapters[start:self._current_chapter_index + 1]
        else:
            lookback = self.chapters
        parts = [f"## {ch.title}\n\n{ch.content}" for ch in lookback]
        return "\n\n".join(parts)

    def _format_outline(self, outline: Outline) -> str:
        """将 Outline 格式化为 Markdown 场景列表。"""
        parts = ["# 续写大纲"]
        parts.append(f"## 续写目标\n{outline.continuation_goals}")
        parts.append(f"## 伏笔计划\n{outline.foreshadowing_plan}")
        parts.append("## 场景列表")
        for i, scene in enumerate(outline.scenes, 1):
            parts.append(f"### 场景 {i}")
            parts.append(f"- 目的：{scene.purpose}")
            parts.append(f"- 视角：{scene.pov}")
            parts.append(f"- 类型：{scene.scene_type}")
            parts.append(f"- 目标：{scene.goal}")
            parts.append(f"- 冲突：{scene.conflict}")
            parts.append(f"- 结果：{scene.outcome}")
            parts.append(f"- 价值转变：{scene.value_shift}")
            parts.append(f"- 伏笔：{scene.foreshadowing}")
            parts.append(f"- 出场钩子：{scene.exit_hook}")
        return "\n".join(parts)

    def _has_world_info_before_marker(self) -> bool:
        """检测 preset 是否有 worldInfoBefore marker。"""
        for prompt in self.preset.prompts:
            if prompt.marker == "worldInfoBefore":
                return True
        return False

    def _post_process(self, content: str) -> str:
        """后处理：正则 AI_OUTPUT → 模板 render_post_receive → HTML 剥离。"""
        return post_process_content(
            content,
            regex_engine=self.regex_engine,
            template_engine=self.template_engine,
            project_id=self.project_id,
            chapter_metadata=self.chapter_metadata,
        )

    def _get_profile_field(self, field_name: str) -> str:
        """从 novel_profile 获取字段值（getattr 模式）。"""
        profile = self.novel_profile
        if profile is None:
            return ""
        if isinstance(profile, dict):
            return str(profile.get(field_name, "") or "")
        return str(getattr(profile, field_name, "") or "")
