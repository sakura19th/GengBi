"""VolumeOrchestrator：卷级多章节续写编排器。

镜像 AgentOrchestrator 的 QThread+asyncio 模式，在独立 QThread 中运行
asyncio 事件循环，完成卷级多章节续写流程：

1. 前文深度分析（DeepAnalysis）
2. 卷大纲生成（VolumeOutline，注入 DeepAnalysis）
3. 大纲审计轮（OutlineAuditReport，可选）
4. 逐章循环：单章细纲 → 写作 → 验证 → 修订

特性：
- 信号：phase_started/phase_finished/chapter_started/chapter_finished/
  chunk_received/reasoning_received/checkpoint_reached/finished/error/
  auth_error/token_count
- 线程安全停止：threading.Event + asyncio.Task.cancel 双重中断
- 暂停点（checkpoint）：after_deep_analysis/after_volume_outline/after_audit
- 优雅降级：JSON 解析失败重试一次（温度归零），再失败跳过该阶段
- 大纲注入：格式化为 Markdown，通过 ContextEntry 或 fallback prepend 注入
- 独立类，不继承 AgentOrchestrator（通过组合持有 PromptAssembler，
  复用 post_process_content），单章写作/验证/修订逻辑在本类内重新实现
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
from novelforge.core.post_processor import post_process_content
from novelforge.core.prompt_assembler import PromptAssembler
from novelforge.core.token_counter import count_text_tokens
from novelforge.models import (
    Chapter,
    ChapterArtifacts,
    ChapterStageArtifact,
    ContextEntry,
    Continuation,
    CritiqueReport,
    DeepAnalysis,
    Outline,
    OutlineAuditReport,
    ProtagonistProfile,
    VolumeArtifacts,
    VolumeOutline,
    VolumeRunConfig,
    WorldOntology,
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
from novelforge.utils.outline_serializer import format_outline
from novelforge.utils.paths import (
    get_agent_prompt_path,
    get_volume_prompt_path,
    load_text_resource,
)

logger = logging.getLogger(__name__)


# 分析深度对应的 max_tokens 默认档位
# exhaustive 用一个足够大的值近似"不设上限"（chat_completion 总会下发 max_tokens）
_ANALYSIS_MAX_TOKENS: dict[str, int] = {
    "light": 8000,
    "standard": 20000,
    "thorough": 50000,
    "exhaustive": 200000,
}


class VolumeOrchestrator(QThread):
    """卷级多章节续写编排器。

    在独立 QThread 中运行 asyncio 事件循环，按 VolumeRunConfig 执行卷级
    流程：前文深度分析 → 卷大纲 → 大纲审计 → 逐章循环。卷级阶段开始
    emit phase_started，结束 emit phase_finished；逐章循环中每章开始
    emit chapter_started，结束 emit chapter_finished。写作阶段流式输出
    chunk_received/reasoning_received/token_count。暂停点 emit
    checkpoint_reached，UI 线程调 resume() 恢复。

    Signals:
        phase_started(str): 卷级阶段名（deep_analysis/volume_outline/outline_audit）
        phase_finished(str, object): 卷级阶段名, 产物对象
        chapter_started(int): 章节序号
        chapter_finished(int, object): 章节序号, ChapterArtifacts
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
    chapter_started = Signal(int)
    chapter_finished = Signal(int, object)
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
        config: VolumeRunConfig,
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
        world_ontology: WorldOntology | None = None,
        protagonist_profile: ProtagonistProfile | None = None,
        parent=None,
    ) -> None:
        """初始化卷级编排器。

        Args:
            base_url: API 基础 URL
            api_key: API Key
            model: 模型名
            parameters: 生成参数（temperature/max_tokens 等）
            preset: 写作预设
            chapters: 所有章节列表（逐章循环中会追加新章节）
            current_chapter: 当前续写章节
            context_entries: 上下文条目列表
            config: 卷级运行配置（章节数/分析深度/暂停点等）
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
            world_ontology: 底层世界观元描述（从 Project 读取，全文固化）
            protagonist_profile: 主角形象档案（从当前章节缓存读取，反映至当前章节状态）
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
        # Find current chapter position in chapters list for lookback computation
        self._current_chapter_index = -1
        for i, ch in enumerate(self.chapters):
            if ch.id == current_chapter.id:
                self._current_chapter_index = i
                break
        # 记录项目原始章节数（含插入点后章节），供动态前文窗口构造有效序列时
        # 跳过插入点后原章节（self.chapters 在中间续写时含全文末尾章节）
        self._original_chapter_count = len(self.chapters)
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
        # 底层世界观元描述（全文提取一次固化，注入各阶段提示词）
        self.world_ontology = world_ontology
        # 主角形象档案（跟随章节缓存，反映至当前章节状态，注入各阶段提示词）
        self.protagonist_profile = protagonist_profile

        # 线程安全停止
        self._stop_event = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

        # 暂停点恢复（在 run() 的事件循环中创建）
        self._resume_event: asyncio.Event | None = None
        self._checkpoint_payload: Any = None

        # LLM 客户端（在 _async_run 中创建，可注入用于测试）
        self._client: Any = None

        # 写作阶段 messages 快照（供历史日志记录，取最后一章写作阶段）
        self._writing_messages: list[dict[str, Any]] = []
        self._writing_model: str = ""

        # 章节文本缓存（深度分析与卷大纲阶段共用，避免重复拼接）
        self._chapters_text: str = ""
        # 续写点前文文本缓存（深度分析实质性剧情分析/卷大纲使用）
        self._lookback_chapters_text: str = ""
        # 用户输入的审计重点提示（before_audit 检查点设置，多轮审计共享）
        self._audit_focus: str = ""

        # 提示词组装器（组合持有，与 AgentOrchestrator 一致）
        self._prompt_assembler = PromptAssembler(
            regex_engine=regex_engine,
            template_engine=template_engine,
        )

        # 调试模式（UI 线程设置，开启后每次 LLM 调用前弹窗确认）
        self.debug_mode: bool = False
        self._debug_confirmed: asyncio.Event | None = None
        self._debug_confirmed_result: bool = False

    def stop(self) -> None:
        """请求停止卷级流程（线程安全）。

        设置停止事件并取消 asyncio 任务。
        """
        self._stop_event.set()
        if self._task and self._loop:
            self._loop.call_soon_threadsafe(self._task.cancel)

    def resume(self, payload: Any = None) -> None:
        """UI 线程调用，恢复暂停的卷级流程。

        Args:
            payload: checkpoint 产物（如编辑后的 DeepAnalysis/VolumeOutline）
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
        """获取最后一章写作阶段的 messages 快照（供历史日志记录）。

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
        """线程入口：创建独立事件循环并执行卷级流程。"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._task = self._loop.create_task(self._async_run())
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            logger.info("卷级任务被取消")
        except Exception as e:
            logger.error("卷级线程异常: %s", e, exc_info=True)
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
            logger.debug("卷级线程事件循环已关闭")

    async def _async_run(self) -> None:
        """异步执行卷级多章节续写流程。"""
        self._loop = asyncio.get_running_loop()
        self._resume_event = asyncio.Event()
        self._debug_confirmed = asyncio.Event()
        # 清理可能残留的暂停点状态（上次运行遗留的 stale payload/event），
        # 避免 _wait_for_resume 因 stale payload 提前返回
        self._checkpoint_payload = None
        self._resume_event.clear()

        if self._client is None:
            self._client = LLMClient(self.base_url, self.api_key)

        artifacts = VolumeArtifacts()

        # 预构建章节文本缓存（深度分析与卷大纲阶段共用）
        self._chapters_text = self._build_chapters_text()
        # 续写点前文文本缓存（深度分析实质性剧情分析/卷大纲使用）
        self._lookback_chapters_text = self._build_lookback_chapters_text()

        try:
            # ===== 阶段 1：前文深度分析 =====
            self.phase_started.emit("deep_analysis")
            deep_analysis = await self._run_deep_analysis()
            artifacts.deep_analysis = deep_analysis
            self.phase_finished.emit("deep_analysis", deep_analysis)

            # 暂停点 after_deep_analysis
            if self.config.checkpoints.get("after_deep_analysis"):
                self.checkpoint_reached.emit("after_deep_analysis", deep_analysis)
                edited = await self._wait_for_resume("after_deep_analysis")
                if edited is not None and isinstance(edited, DeepAnalysis):
                    artifacts.deep_analysis = edited
                    deep_analysis = edited

            # ===== 阶段 2：卷大纲生成 =====
            self.phase_started.emit("volume_outline")
            volume_outline = await self._run_volume_outline(deep_analysis)
            if volume_outline is None:
                # 卷大纲失败为致命错误，终止流程
                self.phase_finished.emit("volume_outline", None)
                self.error.emit("卷大纲生成失败，流程终止")
                return
            artifacts.volume_outline = volume_outline
            self.phase_finished.emit("volume_outline", volume_outline)

            # 暂停点 after_volume_outline
            if self.config.checkpoints.get("after_volume_outline"):
                self.checkpoint_reached.emit(
                    "after_volume_outline", volume_outline
                )
                edited = await self._wait_for_resume("after_volume_outline")
                if edited is not None and isinstance(edited, VolumeOutline):
                    artifacts.volume_outline = edited
                    volume_outline = edited

            # ===== 阶段 3：大纲审计（可选，多轮循环）=====
            final_outline = volume_outline
            final_audit_report: OutlineAuditReport | None = None
            if self.config.enable_outline_audit:
                # 检查点 before_audit：用户输入需着重审计的部分
                if self.config.checkpoints.get("before_audit", True):
                    self.checkpoint_reached.emit("before_audit", volume_outline)
                    audit_focus = await self._wait_for_resume("before_audit")
                    # resume payload 为字符串（用户输入的审计重点），
                    # None/非字符串表示无重点
                    self._audit_focus = (
                        audit_focus if isinstance(audit_focus, str) else ""
                    )
                self.phase_started.emit("outline_audit")
                audit_reports: list[OutlineAuditReport] = []
                current_outline_to_audit = volume_outline
                for round_idx in range(self.config.audit_rounds):
                    audit_report = await self._run_outline_audit(
                        current_outline_to_audit, deep_analysis, round_idx
                    )
                    if audit_report is None:
                        break
                    audit_reports.append(audit_report)
                    if audit_report.revised_outline is not None:
                        current_outline_to_audit = audit_report.revised_outline
                # 取最后一轮审计报告
                final_audit_report = audit_reports[-1] if audit_reports else None
                artifacts.audit_reports = audit_reports
                artifacts.audit_report = final_audit_report  # 向后兼容
                # 审计失败降级：用原大纲作为 final_outline
                if final_audit_report is not None and final_audit_report.revised_outline is not None:
                    final_outline = final_audit_report.revised_outline
                artifacts.final_outline = final_outline
                self.phase_finished.emit("outline_audit", final_audit_report)

                # 暂停点 after_audit（显示最后一轮修订大纲，用户可编辑）
                if self.config.checkpoints.get("after_audit"):
                    self.checkpoint_reached.emit("after_audit", final_outline)
                    edited = await self._wait_for_resume("after_audit")
                    if edited is not None and isinstance(edited, VolumeOutline):
                        final_outline = edited
                        artifacts.final_outline = edited

                # ===== 阶段 3.5：终稿大纲生成 =====
                # 输入：最后一轮审计结果 + 原大纲 + 前10章前文 + 推进速度
                if final_audit_report is not None:
                    self.phase_started.emit("outline_final")
                    final_outline_result = await self._run_outline_final(
                        volume_outline, final_audit_report, deep_analysis
                    )
                    if final_outline_result is not None:
                        final_outline = final_outline_result
                        artifacts.final_outline = final_outline
                    self.phase_finished.emit("outline_final", final_outline)
            else:
                artifacts.final_outline = final_outline

            # ===== 阶段 4：逐章循环 =====
            previous_chapters_text = ""
            previous_chapter_text = ""
            chapter_count = self.config.chapter_count
            for i in range(chapter_count):
                if self._stop_event.is_set():
                    logger.info("逐章循环在第 %d 章被停止", i)
                    break

                self.chapter_started.emit(i)
                chapter_plan = (
                    final_outline.chapters[i]
                    if i < len(final_outline.chapters)
                    else None
                )

                # 动态前文窗口：含本卷新生成章节，每章生成前重新计算
                dynamic_lookback = self._build_dynamic_lookback_text()

                # 阶段产物序列（细纲/初稿/审计①/修改正文①/审计②/...）
                stages: list[ChapterStageArtifact] = []

                # 单章细纲
                outline = await self._run_chapter_outline(
                    final_outline,
                    chapter_plan,
                    previous_chapters_text,
                    previous_chapter_text,
                    dynamic_lookback,
                )
                stages.append(ChapterStageArtifact(
                    stage_type="outline", round_index=0, outline=outline,
                ))

                # 写作（初稿）
                content, reasoning, messages = await self._run_chapter_writing(
                    outline,
                    chapter_plan,
                    previous_chapters_text,
                    previous_chapter_text,
                    lookback_chapters_text=dynamic_lookback,
                )
                self._writing_messages = messages
                self._writing_model = self.model
                stages.append(ChapterStageArtifact(
                    stage_type="draft", round_index=0, content=content,
                ))

                # 验证 + 强制修改 + 自动修订循环
                rounds = 0
                critique = None
                final_critique = None
                audit_round = 0  # 审计轮次计数（1,2,3...）
                if self.config.enable_chapter_verify:
                    critique = await self._run_chapter_verify(
                        deep_analysis, outline, content,
                        lookback_chapters_text=dynamic_lookback,
                    )
                    final_critique = critique
                    audit_round += 1
                    stages.append(ChapterStageArtifact(
                        stage_type="audit", round_index=audit_round, critique=critique,
                    ))

                    # 强制第1轮修改（无论审计①是否通过）+ 自动修订循环
                    if self.config.enable_chapter_revise:
                        while (
                            rounds < self.config.max_revise_rounds_per_chapter
                            and not self._stop_event.is_set()
                            and (rounds == 0 or (critique is not None and not critique.passed))
                        ):
                            rounds += 1
                            guidance = await self._run_chapter_revise(
                                content, critique, outline,
                                lookback_chapters_text=dynamic_lookback,
                            )
                            content, reasoning, messages = (
                                await self._run_chapter_writing(
                                    outline,
                                    chapter_plan,
                                    previous_chapters_text,
                                    previous_chapter_text,
                                    guidance,
                                    original_content=content,
                                    lookback_chapters_text=dynamic_lookback,
                                )
                            )
                            self._writing_messages = messages
                            stages.append(ChapterStageArtifact(
                                stage_type="revise", round_index=rounds,
                                guidance=guidance, content=content,
                            ))
                            critique = await self._run_chapter_verify(
                                deep_analysis, outline, content,
                                lookback_chapters_text=dynamic_lookback,
                            )
                            final_critique = critique
                            audit_round += 1
                            stages.append(ChapterStageArtifact(
                                stage_type="audit", round_index=audit_round,
                                critique=critique,
                            ))

                # after_chapter 暂停点（用户确认循环）
                if self.config.checkpoints.get("after_chapter", False):
                    user_approved = False
                    while not user_approved and not self._stop_event.is_set():
                        # 触发暂停点，等待用户确认
                        payload = await self._wait_for_resume_with_chapter(i, content)
                        action = payload.get("action", "approve")
                        if action == "approve":
                            user_approved = True
                        elif action == "cancel":
                            raise asyncio.CancelledError("用户在每章确认点取消")
                        else:  # reject
                            feedback = payload.get("feedback", "")
                            # 将用户反馈转化为修订指导
                            user_guidance = {
                                "revision_strategy": "用户确认不通过，按反馈重写",
                                "key_changes": [{
                                    "issue_ref": "用户反馈",
                                    "revision_action": feedback,
                                    "target_section": "全文",
                                }],
                                "preserve_elements": "",
                            }
                            rounds += 1
                            guidance = await self._run_chapter_revise(
                                content, None, outline,
                                lookback_chapters_text=dynamic_lookback,
                                user_guidance=user_guidance,
                            )
                            content, reasoning, messages = await self._run_chapter_writing(
                                outline, chapter_plan,
                                previous_chapters_text, previous_chapter_text,
                                guidance, original_content=content,
                                lookback_chapters_text=dynamic_lookback,
                            )
                            self._writing_messages = messages
                            stages.append(ChapterStageArtifact(
                                stage_type="revise", round_index=rounds,
                                guidance=guidance, content=content,
                            ))
                            # 重新验证 + 自动修订循环
                            if self.config.enable_chapter_verify:
                                critique = await self._run_chapter_verify(
                                    deep_analysis, outline, content,
                                    lookback_chapters_text=dynamic_lookback,
                                )
                                final_critique = critique
                                audit_round += 1
                                stages.append(ChapterStageArtifact(
                                    stage_type="audit", round_index=audit_round,
                                    critique=critique,
                                ))
                                while (
                                    self.config.enable_chapter_revise
                                    and critique is not None
                                    and not critique.passed
                                    and rounds < self.config.max_revise_rounds_per_chapter
                                    and not self._stop_event.is_set()
                                ):
                                    rounds += 1
                                    guidance = await self._run_chapter_revise(
                                        content, critique, outline,
                                        lookback_chapters_text=dynamic_lookback,
                                    )
                                    content, reasoning, messages = (
                                        await self._run_chapter_writing(
                                            outline, chapter_plan,
                                            previous_chapters_text, previous_chapter_text,
                                            guidance, original_content=content,
                                            lookback_chapters_text=dynamic_lookback,
                                        )
                                    )
                                    self._writing_messages = messages
                                    stages.append(ChapterStageArtifact(
                                        stage_type="revise", round_index=rounds,
                                        guidance=guidance, content=content,
                                    ))
                                    critique = await self._run_chapter_verify(
                                        deep_analysis, outline, content,
                                        lookback_chapters_text=dynamic_lookback,
                                    )
                                    final_critique = critique
                                    audit_round += 1
                                    stages.append(ChapterStageArtifact(
                                        stage_type="audit", round_index=audit_round,
                                        critique=critique,
                                    ))
                            # 循环结束，再次触发 after_chapter 暂停点（外层 while 继续）

                # 追加 ChapterArtifacts
                chapter_artifacts = ChapterArtifacts(
                    chapter_index=i,
                    outline=outline,
                    critique=critique,
                    final_critique=final_critique,
                    revision_rounds=rounds,
                    content=content,
                    stages=stages,
                )
                artifacts.chapter_artifacts.append(chapter_artifacts)

                # 将该章正文作为新 Chapter 追加到 chapters 列表供下一章前文
                new_chapter = Chapter(
                    id=_generate_id("ch_"),
                    project_id=self.project_id,
                    index=len(self.chapters),
                    title=(
                        chapter_plan.title
                        if chapter_plan is not None
                        else f"第{len(self.chapters) + 1}章"
                    ),
                    content=content,
                    word_count=len(content),
                )
                self.chapters.append(new_chapter)

                # 更新 previous_chapters_text（本卷已生成章节正文）
                if previous_chapters_text:
                    previous_chapters_text += "\n\n" + content
                else:
                    previous_chapters_text = content

                # 更新 previous_chapter_text（紧邻上一章正文，供下一章紧密衔接）
                previous_chapter_text = content

                self.chapter_finished.emit(i, chapter_artifacts)

            # ===== 拼接 N 章正文 + 构建 Continuation =====
            full_content = "\n\n".join(
                ca.content for ca in artifacts.chapter_artifacts if ca.content
            )
            continuation = Continuation(
                id=_generate_id("sw_"),
                created_at=datetime.now(),
                content=full_content,
                model=self.model,
                is_accepted=False,
                status="completed",
                created_by="volume",
                parameters_snapshot=dict(self.parameters),
                preset_id=self.preset_id,
                preset_snapshot=self.preset_snapshot,
                regex_script_ids_snapshot=list(self.regex_script_ids),
                extracted_context_snapshot=list(self.context_entries),
                prompt_snapshot=self._writing_messages,
                reasoning_content=reasoning or None,
                volume_artifacts=artifacts,
            )
            self.finished.emit(continuation)

        except asyncio.CancelledError:
            logger.info("卷级任务被取消")
            raise
        except AuthError:
            self.auth_error.emit()
        except (RateLimitError, APIError) as e:
            self.error.emit(str(e))
        except Exception as e:
            logger.error("卷级流程异常: %s", e, exc_info=True)
            self.error.emit(str(e))

    async def _run_deep_analysis(self) -> DeepAnalysis | None:
        """阶段①：前文深度分析，产出 DeepAnalysis。

        前文章节文本全量注入（不裁剪，用户有 1M 上下文）。
        max_tokens 按深度调整：light=8000 / standard=20000 / thorough=50000 /
        exhaustive=不设上限（200000 近似）。
        失败/解析失败重试一次（温度归零），再失败返回 None（降级，不阻塞）。

        支持按 token 切分与增量更新：
        - ``analysis_chunk_tokens`` 为 0 时不切分，全量发送一次调用
        - ``analysis_chunk_tokens`` > 0 时按章节边界累积 token 切分，逐块调用，
          每块携带已有分析内容（增量补充），最终合并为完整 DeepAnalysis
        """
        template = self._load_volume_template("deep_analysis")
        depth = self.config.analysis_depth
        max_entries = self.config.max_analysis_entries
        max_tokens = _ANALYSIS_MAX_TOKENS.get(depth, 8000)
        chunk_tokens = self.config.analysis_chunk_tokens

        # 切分章节：基于 lookback chapters（插入点之前），用户要求只分析插入点之前
        # （lookback chapters = chapters[0..current_chapter_index]，含当前章）
        lookback_chapters = (
            self.chapters[:self._current_chapter_index + 1]
            if self._current_chapter_index >= 0
            else self.chapters
        )
        chapter_chunks = self._split_chapters_by_tokens(
            lookback_chapters, chunk_tokens
        )

        if len(chapter_chunks) <= 1:
            # 不切分：单次调用发送 lookback chapters_text
            return await self._run_deep_analysis_single(
                template, depth, max_entries, max_tokens, self._lookback_chapters_text
            )

        # 切分：逐块调用，增量携带已有分析
        accumulated: DeepAnalysis | None = None
        for i, chunk in enumerate(chapter_chunks):
            chunk_text = "\n\n".join(
                f"## {ch.title}\n\n{ch.content}" for ch in chunk
            )
            # 增量：携带已有分析内容
            existing_analysis_text = ""
            if accumulated is not None:
                existing_analysis_text = json.dumps(
                    accumulated.model_dump(),
                    ensure_ascii=False,
                    indent=2,
                )

            result = await self._run_deep_analysis_single(
                template,
                depth,
                max_entries,
                max_tokens,
                chunk_text,
                existing_analysis=existing_analysis_text,
                chunk_info=f"（第 {i + 1}/{len(chapter_chunks)} 块）",
            )
            if result is not None:
                accumulated = self._merge_deep_analysis(accumulated, result)

        # 【信息汇总】环节：切分模式下，程序化合并后追加一次 LLM 整合润色
        if accumulated is not None:
            accumulated = await self._run_deep_analysis_merge(
                accumulated, depth, max_tokens
            )

        return accumulated

    async def _run_deep_analysis_single(
        self,
        template: str,
        depth: str,
        max_entries: int,
        max_tokens: int,
        chapters_text: str,
        existing_analysis: str = "",
        chunk_info: str = "",
    ) -> DeepAnalysis | None:
        """单次深度分析调用（提取自 _run_deep_analysis，支持增量与切分信息）。

        Args:
            template: 提示词模板
            depth: 分析深度
            max_entries: 条目上限
            max_tokens: LLM max_tokens
            chapters_text: 本块章节文本
            existing_analysis: 已有分析内容 JSON（增量补充参考，空串表示无）
            chunk_info: 切分块信息（如"（第 1/3 块）"，空串表示无）

        Returns:
            DeepAnalysis 对象，失败返回 None
        """
        macros = {
            "{{title}}": self._get_profile_field("title"),
            "{{author}}": self._get_profile_field("author"),
            "{{protagonist}}": self._get_profile_field("protagonist"),
            "{{synopsis}}": self._get_profile_field("synopsis"),
            "{{world_setting}}": self._get_profile_field("world_setting"),
            "{{writing_style}}": self._get_profile_field("writing_style"),
            "{{world_ontology}}": self._format_world_ontology(),
            "{{protagonist_profile}}": self._format_protagonist_profile(),
            "{{chapters_text}}": chapters_text,
            "{{analysis_depth}}": depth,
            "{{max_analysis_entries}}": str(max_entries),
        }
        system_prompt = self._apply_macros(template, macros)
        if existing_analysis:
            system_prompt += (
                f"\n\n# 已有分析内容（增量补充参考）\n{existing_analysis}"
            )
        if chunk_info:
            system_prompt += f"\n\n{chunk_info}"
        messages = [{"role": "system", "content": system_prompt}]

        # 调试模式确认
        if not await self._maybe_debug_prompt(messages, "深度分析"):
            return None

        for attempt in range(2):
            temperature = 0.3 if attempt == 0 else 0.0
            try:
                response = await self._client.chat_completion(
                    messages,
                    self.model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                content = response["choices"][0]["message"]["content"]
                data = parse_json_response(content)
                return DeepAnalysis.model_validate(data)
            except AuthError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "DeepAnalysis 失败 (attempt %d): %s", attempt + 1, e
                )
                continue
        return None

    async def _run_deep_analysis_merge(
        self,
        accumulated: DeepAnalysis,
        depth: str,
        max_tokens: int,
    ) -> DeepAnalysis:
        """【信息汇总】环节：对程序化合并后的 DeepAnalysis 做 LLM 语义整合润色。

        切分模式下，``_merge_deep_analysis`` 仅做字段级合并（字符串取非空、列表去重），
        本方法追加一次 LLM 调用，消除跨块重复、消解冲突、补全遗漏，产出连贯最终报告。

        失败降级：返回原 ``accumulated``（不阻塞卷续写流程）。

        Args:
            accumulated: 程序化合并后的 DeepAnalysis
            depth: 分析深度等级（light/standard/thorough/exhaustive）
            max_tokens: LLM max_tokens（按深度复用 _ANALYSIS_MAX_TOKENS）

        Returns:
            整合润色后的 DeepAnalysis，失败时返回原 accumulated
        """
        template = self._load_volume_template("deep_analysis_merge")
        deep_analysis_text = json.dumps(
            accumulated.model_dump(), ensure_ascii=False, indent=2
        )
        macros = {
            "{{title}}": self._get_profile_field("title"),
            "{{author}}": self._get_profile_field("author"),
            "{{protagonist}}": self._get_profile_field("protagonist"),
            "{{synopsis}}": self._get_profile_field("synopsis"),
            "{{world_setting}}": self._get_profile_field("world_setting"),
            "{{writing_style}}": self._get_profile_field("writing_style"),
            "{{world_ontology}}": self._format_world_ontology(),
            "{{protagonist_profile}}": self._format_protagonist_profile(),
            "{{deep_analysis}}": deep_analysis_text,
        }
        system_prompt = self._apply_macros(template, macros)
        messages = [{"role": "system", "content": system_prompt}]

        # 调试模式确认
        if not await self._maybe_debug_prompt(messages, "深度分析汇总"):
            return accumulated

        for attempt in range(2):
            temperature = 0.2 if attempt == 0 else 0.0
            try:
                response = await self._client.chat_completion(
                    messages,
                    self.model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                content = response["choices"][0]["message"]["content"]
                data = parse_json_response(content)
                merged = DeepAnalysis.model_validate(data)
                logger.info(
                    "DeepAnalysis 信息汇总完成 (attempt %d)", attempt + 1
                )
                return merged
            except AuthError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "DeepAnalysis 信息汇总失败 (attempt %d): %s，降级使用程序化合并结果",
                    attempt + 1, e,
                )
                continue
        return accumulated

    def _split_chapters_by_tokens(
        self, chapters: list[Chapter], chunk_tokens: int
    ) -> list[list[Chapter]]:
        """按章节边界累积 token，达到阈值切分（不跨章切分）。

        Args:
            chapters: 章节列表
            chunk_tokens: 每块 token 上限（>0）

        Returns:
            章节块的列表，每块是一个章节子列表
        """
        if chunk_tokens <= 0:
            return [list(chapters)]
        chunks: list[list[Chapter]] = []
        current: list[Chapter] = []
        current_tokens = 0
        for ch in chapters:
            ch_tokens = count_text_tokens(ch.content or "")
            if current and current_tokens + ch_tokens > chunk_tokens:
                chunks.append(current)
                current = [ch]
                current_tokens = ch_tokens
            else:
                current.append(ch)
                current_tokens += ch_tokens
        if current:
            chunks.append(current)
        return chunks

    def _merge_deep_analysis(
        self, base: DeepAnalysis | None, new: DeepAnalysis
    ) -> DeepAnalysis:
        """合并两个 DeepAnalysis：非空字段覆盖空字段，列表字段拼接去重。

        Args:
            base: 已有分析（None 时直接返回 new）
            new: 新块分析

        Returns:
            合并后的 DeepAnalysis
        """
        if base is None:
            return new
        result = base.model_copy(deep=True)
        # 字符串字段：base 为空则用 new
        for field in (
            "structure_position",
            "tone",
            "core_conflict_status",
            "stakes",
            "world_state",
            "plot_arrangement_analysis",
            "chapter_structure_pattern",
            "tension_curve_pattern",
            "hook_patterns",
            "style_analysis",
            "dialogue_analysis",
            "pacing_analysis",
        ):
            if not getattr(result, field) and getattr(new, field):
                setattr(result, field, getattr(new, field))
        # 列表字段：拼接（简单去重按 JSON 序列化）
        for field in (
            "active_characters",
            "plot_threads",
            "unresolved_promises",
            "character_arc_patterns",
            "foreshadowing_inventory",
            "common_tropes",
            "settings_database",
            "recurring_elements",
            "key_phrases",
        ):
            existing = getattr(result, field) or []
            new_vals = getattr(new, field) or []
            seen = {
                json.dumps(x, ensure_ascii=False, sort_keys=True)
                for x in existing
            }
            for x in new_vals:
                key = json.dumps(x, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    existing.append(x)
                    seen.add(key)
            setattr(result, field, existing)
        return result

    async def _run_volume_outline(
        self, deep_analysis: DeepAnalysis | None
    ) -> VolumeOutline | None:
        """阶段②：卷大纲生成，产出 VolumeOutline。

        注入 DeepAnalysis 的 JSON 序列化（若为 None 则空字符串）。
        校验 chapters 长度 == chapter_count，若超长则截断，若不足则返回 None。
        失败重试一次（温度归零），再失败 emit error 终止。
        """
        template = self._load_volume_template("volume_outline")
        chapters_text = self._lookback_chapters_text
        if deep_analysis is not None:
            deep_analysis_text = json.dumps(
                deep_analysis.model_dump(), ensure_ascii=False, indent=2
            )
        else:
            deep_analysis_text = ""
        chapter_count = self.config.chapter_count
        target_words = self.config.target_words_per_chapter
        macros = {
            "{{world_ontology}}": self._format_world_ontology(),
            "{{protagonist_profile}}": self._format_protagonist_profile(),
            "{{deep_analysis}}": deep_analysis_text,
            "{{chapters_text}}": chapters_text,
            "{{user_input}}": self.user_input or "",
            "{{chapter_count}}": str(chapter_count),
            "{{target_words_per_chapter}}": str(target_words),
            "{{pacing_speed}}": self.config.pacing_speed,
            "{{context_entries}}": self._build_context_entries_text(),
        }
        system_prompt = self._apply_macros(template, macros)
        messages = [{"role": "system", "content": system_prompt}]

        # 调试模式确认
        if not await self._maybe_debug_prompt(messages, "卷大纲"):
            return None

        for attempt in range(2):
            temperature = 0.3 if attempt == 0 else 0.0
            content = ""
            try:
                response = await self._client.chat_completion(
                    messages,
                    self.model,
                    temperature=temperature,
                    max_tokens=8000,
                )
                content = response["choices"][0]["message"]["content"]
                data = parse_json_response(content)
                outline = VolumeOutline.model_validate(data)
                # 校验 chapters 长度
                if len(outline.chapters) > chapter_count:
                    outline.chapters = outline.chapters[:chapter_count]
                elif len(outline.chapters) < chapter_count:
                    raise ValueError(
                        f"章节数不足: 期望 {chapter_count}, 实际 {len(outline.chapters)}"
                    )
                outline.chapter_count = chapter_count
                return outline
            except AuthError:
                raise
            except RateLimitError:
                raise
            except APIError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("VolumeOutline 失败 (attempt %d): %s", attempt + 1, e)
                # 补充 LLM 上次输出到上下文，让它在下次重试时针对性修正
                messages.append({"role": "assistant", "content": content})
                # 反馈错误信息给 LLM
                messages.append({
                    "role": "user",
                    "content": (
                        f"上次输出校验失败：{e}。请修正上述问题，严格按 JSON Schema "
                        "重新输出完整的 VolumeOutline JSON 对象。特别注意：plot_role "
                        "必须为 起/承/转/合/高潮/过渡 中的单一值，严禁组合拼接"
                        "（如\"承转\"无效，应输出\"承\"或\"转\"）。"
                    ),
                })
                continue
        return None

    async def _run_outline_audit(
        self,
        volume_outline: VolumeOutline,
        deep_analysis: DeepAnalysis | None = None,
        round_idx: int = 0,
    ) -> OutlineAuditReport | None:
        """阶段③：大纲审计，产出 OutlineAuditReport（含 revised_outline 终稿）。

        仅当 enable_outline_audit=True 时由 _async_run 调用。
        注入最近 10 章前文正文与 DeepAnalysis JSON，供一致性审计参考。
        支持多轮审计：每轮审计上一轮修订版（round_idx 为当前轮次，0-based）。
        失败降级：返回 None，由 _async_run 用原大纲作为 final_outline。
        """
        template = self._load_volume_template("outline_audit")
        outline_text = json.dumps(
            volume_outline.model_dump(), ensure_ascii=False, indent=2
        )
        dimensions = ", ".join(self.config.audit_dimensions)
        # 最近 10 章正文（不足 10 章时取全部），格式镜像 _build_chapters_text
        if self._current_chapter_index >= 0:
            start = max(0, self._current_chapter_index - 9)
            recent_chapters = self.chapters[start:self._current_chapter_index + 1]
        else:
            recent_chapters = self.chapters[-10:] if self.chapters else []
        previous_chapters_text = "\n\n".join(
            f"## {ch.title}\n\n{ch.content}" for ch in recent_chapters
        )
        if deep_analysis is not None:
            deep_analysis_text = json.dumps(
                deep_analysis.model_dump(), ensure_ascii=False, indent=2
            )
        else:
            deep_analysis_text = ""
        macros = {
            "{{world_ontology}}": self._format_world_ontology(),
            "{{protagonist_profile}}": self._format_protagonist_profile(),
            "{{volume_outline}}": outline_text,
            "{{audit_dimensions}}": dimensions,
            "{{previous_chapters_text}}": previous_chapters_text,
            "{{deep_analysis}}": deep_analysis_text,
            "{{round_idx}}": str(round_idx + 1),
            "{{total_rounds}}": str(self.config.audit_rounds),
            "{{audit_focus}}": self._audit_focus or "（用户未指定重点）",
        }
        system_prompt = self._apply_macros(template, macros)
        messages = [{"role": "system", "content": system_prompt}]

        # 调试模式确认
        phase_name = f"大纲审计(第{round_idx + 1}轮)"
        if not await self._maybe_debug_prompt(messages, phase_name):
            return None

        for attempt in range(2):
            temperature = 0.3 if attempt == 0 else 0.0
            content = ""
            try:
                response = await self._client.chat_completion(
                    messages,
                    self.model,
                    temperature=temperature,
                    max_tokens=8000,
                )
                content = response["choices"][0]["message"]["content"]
                data = parse_json_response(content)
                return OutlineAuditReport.model_validate(data)
            except AuthError:
                raise
            except RateLimitError:
                raise
            except APIError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("OutlineAudit 失败 (attempt %d): %s", attempt + 1, e)
                # 补充 LLM 上次输出到上下文，让它在下次重试时针对性修正
                messages.append({"role": "assistant", "content": content})
                # 反馈错误信息给 LLM
                messages.append({
                    "role": "user",
                    "content": (
                        f"上次输出校验失败：{e}。请修正上述问题，严格按 JSON Schema "
                        "重新输出完整的 OutlineAuditReport JSON 对象（含 revised_outline）。"
                        "特别注意：revised_outline.chapters[*].plot_role 必须为 "
                        "起/承/转/合/高潮/过渡 中的单一值，严禁组合拼接。"
                    ),
                })
                continue
        return None

    async def _run_outline_final(
        self,
        original_outline: VolumeOutline,
        final_audit_report: OutlineAuditReport | None,
        deep_analysis: DeepAnalysis | None,
    ) -> VolumeOutline | None:
        """阶段③.5：终稿大纲生成。

        输入：最后一轮审计结果 + 原大纲 + 前10章前文 + 推进速度。
        产出：终稿 VolumeOutline。
        失败降级：返回 None，由 _async_run 保持审计后的 final_outline。
        """
        template = self._load_volume_template("outline_final")
        original_text = json.dumps(
            original_outline.model_dump(), ensure_ascii=False, indent=2
        )
        audit_text = (
            json.dumps(
                final_audit_report.model_dump(), ensure_ascii=False, indent=2
            )
            if final_audit_report is not None
            else ""
        )
        lookback_10_text = self._build_lookback_10_chapters_text()
        if deep_analysis is not None:
            deep_analysis_text = json.dumps(
                deep_analysis.model_dump(), ensure_ascii=False, indent=2
            )
        else:
            deep_analysis_text = ""
        macros = {
            "{{original_outline}}": original_text,
            "{{audit_report}}": audit_text,
            "{{previous_chapters_text}}": lookback_10_text,
            "{{deep_analysis}}": deep_analysis_text,
            "{{pacing_speed}}": self.config.pacing_speed,
            "{{world_ontology}}": self._format_world_ontology(),
            "{{protagonist_profile}}": self._format_protagonist_profile(),
        }
        system_prompt = self._apply_macros(template, macros)
        messages = [{"role": "system", "content": system_prompt}]

        # 调试模式确认
        if not await self._maybe_debug_prompt(messages, "终稿大纲"):
            return None

        for attempt in range(2):
            temperature = 0.3 if attempt == 0 else 0.0
            content = ""
            try:
                response = await self._client.chat_completion(
                    messages,
                    self.model,
                    temperature=temperature,
                    max_tokens=8000,
                )
                content = response["choices"][0]["message"]["content"]
                data = parse_json_response(content)
                outline = VolumeOutline.model_validate(data)
                # 校验 chapters 长度
                chapter_count = self.config.chapter_count
                if len(outline.chapters) > chapter_count:
                    outline.chapters = outline.chapters[:chapter_count]
                elif len(outline.chapters) < chapter_count:
                    raise ValueError(
                        f"章节数不足: 期望 {chapter_count}, 实际 {len(outline.chapters)}"
                    )
                outline.chapter_count = chapter_count
                return outline
            except AuthError:
                raise
            except RateLimitError:
                raise
            except APIError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("OutlineFinal 失败 (attempt %d): %s", attempt + 1, e)
                # 补充 LLM 上次输出到上下文，让它在下次重试时针对性修正
                messages.append({"role": "assistant", "content": content})
                # 反馈错误信息给 LLM
                messages.append({
                    "role": "user",
                    "content": (
                        f"上次输出校验失败：{e}。请修正上述问题，严格按 JSON Schema "
                        "重新输出完整的 VolumeOutline JSON 对象。特别注意：plot_role "
                        "必须为 起/承/转/合/高潮/过渡 中的单一值，严禁组合拼接。"
                    ),
                })
                continue
        return None

    async def _run_chapter_outline(
        self,
        volume_outline: VolumeOutline,
        chapter_plan: Any | None,
        previous_chapters_text: str,
        previous_chapter_text: str = "",
        lookback_chapters_text: str = "",
    ) -> Outline | None:
        """阶段④-细纲：单章场景级细纲，产出 Outline（3-7 个 Scene）。

        注入紧邻上一章正文（previous_chapter_text）用于紧密衔接。
        失败降级返回 None（该章直接基于 ChapterPlan 写作，无场景级细纲）。
        """
        template = self._load_volume_template("chapter_outline")
        outline_text = json.dumps(
            volume_outline.model_dump(), ensure_ascii=False, indent=2
        )
        if chapter_plan is not None:
            plan_text = json.dumps(
                chapter_plan.model_dump(), ensure_ascii=False, indent=2
            )
        else:
            plan_text = ""
        macros = {
            "{{protagonist_profile}}": self._format_protagonist_profile(),
            "{{world_ontology}}": self._format_world_ontology(),
            "{{volume_outline}}": outline_text,
            "{{chapter_plan}}": plan_text,
            "{{lookback_chapters_text}}": lookback_chapters_text,
            "{{previous_chapters_text}}": previous_chapters_text,
            "{{previous_chapter_text}}": previous_chapter_text,
            "{{user_input}}": self.user_input or "",
            "{{pacing_speed}}": self.config.pacing_speed,
            "{{context_entries}}": self._build_context_entries_text(),
        }
        system_prompt = self._apply_macros(template, macros)
        messages = [{"role": "system", "content": system_prompt}]

        # 调试模式确认
        if not await self._maybe_debug_prompt(messages, "章节细纲"):
            return None

        for attempt in range(2):
            temperature = 0.3 if attempt == 0 else 0.0
            try:
                response = await self._client.chat_completion(
                    messages,
                    self.model,
                    temperature=temperature,
                    max_tokens=3000,
                )
                content = response["choices"][0]["message"]["content"]
                data = parse_json_response(content)
                return Outline.model_validate(data)
            except AuthError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("ChapterOutline 失败 (attempt %d): %s", attempt + 1, e)
                continue
        return None

    async def _run_chapter_writing(
        self,
        outline: Outline | None,
        chapter_plan: Any | None,
        previous_chapters_text: str,
        previous_chapter_text: str = "",
        revision_guidance: dict | None = None,
        original_content: str = "",
        lookback_chapters_text: str = "",
    ) -> tuple[str, str, list[dict[str, Any]]]:
        """阶段④-写作：单章流式写作。

        复用 AgentOrchestrator._run_writing 的模式（在本类内重新实现）：
        - 单章细纲格式化为 Markdown（用 format_outline）
        - 合成 ContextEntry（position=before）注入 prompt_assembler
        - worldInfoBefore marker 不存在时 fallback：直接 prepend system 消息
        - 上一章正文（previous_chapter_text）prepend 为 system 消息用于紧密衔接
        - user_input 用本章 ChapterPlan 派生的生成要求覆盖（chapter_plan 为 None 时回退 self.user_input）
        - prompt_assembler.assemble() 组装 messages
        - stream_chat_completion 流式调用，emit chunk_received/reasoning_received/token_count
        - post_process_content 后处理

        Args:
            outline: 单章细纲（可能为 None）
            chapter_plan: 当前章 ChapterPlan（用于 target_words 覆盖与生成要求派生）
            previous_chapters_text: 本卷已生成章节正文拼接
            previous_chapter_text: 紧邻上一章完整正文（用于紧密衔接，可能为空）
            revision_guidance: 修订指导（非空时追加为 user 消息）
            original_content: 当前已生成内容（修订时作为重写参考，空串表示无）

        Returns:
            (content, reasoning_content, messages) 三元组
        """
        # 格式化大纲并创建合成 ContextEntry
        if outline is not None:
            outline_markdown = format_outline(outline)
            outline_entry = ContextEntry(
                uid="volume_chapter_outline",
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

        # 每章目标字数：优先取 chapter_plan.target_words，否则取配置
        target_words = self.config.target_words_per_chapter
        if chapter_plan is not None and chapter_plan.target_words:
            target_words = chapter_plan.target_words

        # 本章生成要求：优先用 chapter_plan 派生（优先级高于卷级 user_input），
        # chapter_plan 为 None 时回退 self.user_input
        if chapter_plan is not None:
            per_chapter_input = (
                f"本章生成要求：\n标题：{chapter_plan.title}\n"
                f"摘要：{chapter_plan.summary}\n"
                f"剧情角色：{chapter_plan.plot_role}\n"
                f"关键事件：{'；'.join(chapter_plan.key_events)}\n"
                f"章节钩子：{chapter_plan.chapter_hook}"
            )
        else:
            per_chapter_input = self.user_input

        # 组装提示词（使用有效章节序列，跳过插入点后原章节）
        effective_chapters = self._get_effective_chapters()
        result = self._prompt_assembler.assemble(
            preset=self.preset,
            chapters=effective_chapters,
            current_chapter=self.current_chapter,
            context_entries=all_entries,
            model=self.model,
            max_context=self.parameters.get("max_context", 9999999),
            max_tokens=self.parameters.get("max_tokens", 2000),
            target_words=target_words,
            novel_profile=self.novel_profile,
            project_id=self.project_id,
            chapter_metadata=self.chapter_metadata,
            user_input=per_chapter_input,
            skip_history=True,
            world_ontology=self.world_ontology,
            protagonist_profile=self.protagonist_profile,
        )
        messages = list(result.messages)

        # 上一章正文注入（用于紧密衔接，插入到最后一条消息之前）
        if previous_chapter_text:
            insert_pos = max(0, len(messages) - 1)
            messages.insert(
                insert_pos,
                {
                    "role": "system",
                    "content": f"# 上一章正文（用于衔接）\n{previous_chapter_text}",
                },
            )

        # 动态前文窗口注入（含本卷已生成章节，供写作参考风格与剧情）
        if lookback_chapters_text:
            messages.insert(0, {
                "role": "system",
                "content": f"# 最近 10 章正文参考（含本卷已生成）\n{lookback_chapters_text}",
            })

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
        if not await self._maybe_debug_prompt(messages, "章节写作"):
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

    async def _run_chapter_verify(
        self,
        deep_analysis: DeepAnalysis | None,
        outline: Outline | None,
        written_text: str,
        lookback_chapters_text: str = "",
    ) -> CritiqueReport | None:
        """阶段④-验证：单章验证，产出 CritiqueReport。

        复用 AgentOrchestrator._run_verify 的模式，加载 phase_verify.txt
        （用 get_agent_prompt_path）。
        snapshot 可从 DeepAnalysis 构造简化版或 None。
        失败重试一次，再失败返回 None（视为通过，不阻塞）。
        """
        template = self._load_agent_template("verify")
        # 从 DeepAnalysis 构造简化 snapshot 文本（或 None）
        if deep_analysis is not None:
            snapshot_text = json.dumps(
                deep_analysis.model_dump(), ensure_ascii=False, indent=2
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
            "{{world_ontology}}": self._format_world_ontology(),
            "{{protagonist_profile}}": self._format_protagonist_profile(),
            "{{snapshot}}": snapshot_text,
            "{{outline}}": outline_text,
            "{{written_text}}": written_text,
            "{{previous_chapters_text}}": lookback_chapters_text,
        }
        system_prompt = self._apply_macros(template, macros)
        messages = [{"role": "system", "content": system_prompt}]

        # 调试模式确认
        if not await self._maybe_debug_prompt(messages, "章节验证"):
            return None

        for attempt in range(2):
            temperature = 0.2 if attempt == 0 else 0.0
            try:
                response = await self._client.chat_completion(
                    messages,
                    self.model,
                    temperature=temperature,
                    max_tokens=3000,
                )
                content = response["choices"][0]["message"]["content"]
                data = parse_json_response(content)
                return CritiqueReport.model_validate(data)
            except AuthError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("ChapterVerify 失败 (attempt %d): %s", attempt + 1, e)
                continue
        return None

    async def _run_chapter_revise(
        self,
        written_text: str,
        critique: CritiqueReport | None,
        outline: Outline | None,
        lookback_chapters_text: str = "",
        user_guidance: dict | None = None,
    ) -> dict:
        """阶段④-修订：产出修订指导（non-stream JSON，宽松 dict）。

        复用 AgentOrchestrator._run_revise 的模式，加载 phase_revise.txt
        （用 get_agent_prompt_path）。
        失败重试一次，再失败返回空 dict。

        Args:
            user_guidance: 用户确认不通过时直接提供的修订指导。非 None 时跳过 LLM
                revise 调用，直接返回该 dict（节省一次调用）。
        """
        if user_guidance is not None:
            return user_guidance
        template = self._load_agent_template("revise")
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
            "{{protagonist_profile}}": self._format_protagonist_profile(),
            "{{world_ontology}}": self._format_world_ontology(),
            "{{written_text}}": written_text,
            "{{critique}}": critique_text,
            "{{outline}}": outline_text,
            "{{previous_chapters_text}}": lookback_chapters_text,
            "{{pacing_speed}}": self.config.pacing_speed,
        }
        system_prompt = self._apply_macros(template, macros)
        messages = [{"role": "system", "content": system_prompt}]

        # 调试模式确认
        if not await self._maybe_debug_prompt(messages, "章节修订"):
            return {}

        for attempt in range(2):
            temperature = 0.3 if attempt == 0 else 0.0
            try:
                response = await self._client.chat_completion(
                    messages,
                    self.model,
                    temperature=temperature,
                    max_tokens=3000,
                )
                content = response["choices"][0]["message"]["content"]
                return parse_json_response(content)
            except AuthError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("ChapterRevise 失败 (attempt %d): %s", attempt + 1, e)
                continue
        return {}

    async def _wait_for_resume(self, checkpoint_name: str) -> Any:
        """暂停等待用户操作，返回 checkpoint_payload。

        每 0.5s 轮询 _stop_event，被停止时抛 CancelledError。
        镜像 AgentOrchestrator._wait_for_resume。

        注意：不检查 _checkpoint_payload 的"提前返回"路径——该路径会因
        上次运行残留的 stale payload 触发立即返回，导致用户编辑内容被忽略。
        payload 仅在 resume() 设置 _resume_event 后才读取，确保读取的是
        当前次用户操作的结果。
        """
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

    async def _wait_for_resume_with_chapter(
        self, chapter_index: int, content: str
    ) -> dict:
        """触发 after_chapter 检查点，等待用户确认。

        与 _wait_for_resume 类似，但先 emit checkpoint_reached 携带章节正文，
        供 UI 弹出 ChapterConfirmDialog。返回值为用户操作 dict：
        {"action": "approve"/"reject"/"cancel", "feedback": "..."}.
        """
        self.checkpoint_reached.emit("after_chapter", {
            "chapter_index": chapter_index,
            "content": content,
        })
        payload = await self._wait_for_resume("after_chapter")
        if payload is None:
            return {"action": "approve"}
        return payload

    def _apply_macros(self, template: str, macros: dict[str, str]) -> str:
        """用 str.replace 替换模板占位符（不用 MacroEngine/Jinja2）。

        镜像 AgentOrchestrator._apply_macros。
        """
        result = template
        for placeholder, value in macros.items():
            result = result.replace(placeholder, value)
        return result

    def _load_volume_template(self, phase: str) -> str:
        """加载卷级阶段提示词模板。"""
        path = get_volume_prompt_path(phase)
        return load_text_resource(path)

    def _load_agent_template(self, phase: str) -> str:
        """加载单次 Agent 阶段提示词模板（verify/revise 复用）。"""
        path = get_agent_prompt_path(phase)
        return load_text_resource(path)

    def _build_chapters_text(self) -> str:
        """拼接章节文本：`## {标题}\\n\\n{正文}`。

        镜像 AgentOrchestrator._build_chapters_text。
        """
        parts: list[str] = []
        for ch in self.chapters:
            parts.append(f"## {ch.title}\n\n{ch.content}")
        return "\n\n".join(parts)

    def _build_lookback_chapters_text(self) -> str:
        """Build text from chapters[0] to current chapter (inclusive).

        If current chapter not found in list, falls back to all chapters.
        """
        if self._current_chapter_index >= 0:
            lookback = self.chapters[:self._current_chapter_index + 1]
        else:
            lookback = self.chapters
        parts = [f"## {ch.title}\n\n{ch.content}" for ch in lookback]
        return "\n\n".join(parts)

    def _build_lookback_10_chapters_text(self) -> str:
        """构建插入点前 10 章正文文本（含当前章）。

        与 AgentOrchestrator._build_lookback_chapters_text(max_chapters=10) 一致：
        返回 chapters[max(0, idx-9):idx+1] 拼接。
        用于细纲生成与终稿大纲生成的前文参考。
        """
        if self._current_chapter_index >= 0:
            start = max(0, self._current_chapter_index - 9)
            lookback = self.chapters[start:self._current_chapter_index + 1]
        else:
            lookback = self.chapters[-10:] if self.chapters else []
        parts = [f"## {ch.title}\n\n{ch.content}" for ch in lookback]
        return "\n\n".join(parts)

    def _get_effective_chapters(self) -> list:
        """构造有效章节序列：插入点前章节 + 本卷已生成章节。

        从中间续写时 self.chapters = [插入点前 | 插入点后原章节 | 本卷已生成]，
        其中"插入点后原章节"不应作为前文参考。本方法返回
        chapters[0:_current_chapter_index+1] + chapters[_original_chapter_count:]，
        跳过插入点后原章节，供动态前文窗口与 prompt_assembler.assemble 复用。
        """
        if self._current_chapter_index >= 0:
            pre = self.chapters[: self._current_chapter_index + 1]
        else:
            pre = list(self.chapters)
        generated = self.chapters[self._original_chapter_count:]
        return pre + generated

    def _build_dynamic_lookback_text(self, window: int = 10) -> str:
        """构建动态前文窗口：插入点前章节 + 本卷已生成章节，取末尾 window 章。

        与 _build_lookback_10_chapters_text() 的区别：随本卷新生成章节动态滑动
        （含本卷已生成章节），且通过 _get_effective_chapters 跳过插入点后原章节，
        避免从中间续写时误将全文末尾章节当作前文。
        """
        effective = self._get_effective_chapters()
        if not effective:
            return ""
        start = max(0, len(effective) - window)
        lookback = effective[start:]
        parts = [f"## {ch.title}\n\n{ch.content}" for ch in lookback]
        return "\n\n".join(parts)

    def _build_context_entries_text(self) -> str:
        """格式化上下文条目为可读 Markdown 文本（按 category 分组，order 升序）。

        空列表返回空字符串。用于卷大纲与章节细纲阶段注入 {{context_entries}} 占位符。
        """
        if not self.context_entries:
            return ""
        # 按 category 分组
        grouped: dict[str, list[ContextEntry]] = {}
        for entry in self.context_entries:
            if not entry.content:
                continue
            grouped.setdefault(entry.category or "other", []).append(entry)
        if not grouped:
            return ""
        lines: list[str] = ["# 上下文条目（自动提取的人物/地点/事件/风格/伏笔等）"]
        category_labels = {
            "characters": "人物",
            "locations": "地点",
            "events": "事件",
            "style": "风格",
            "plot_state": "剧情状态",
            "relationships": "关系",
            "atmosphere": "氛围",
            "foreshadowing": "伏笔",
            "other": "其他",
        }
        for category in sorted(grouped.keys()):
            entries = sorted(grouped[category], key=lambda e: e.order)
            label = category_labels.get(category, category)
            lines.append(f"## {label}")
            for entry in entries:
                keys = f"（{','.join(entry.key)}）" if entry.key else ""
                lines.append(f"- {entry.content}{keys}")
        return "\n".join(lines)

    def _format_world_ontology(self) -> str:
        """格式化 WorldOntology 为提示词注入文本。

        Returns:
            格式化 JSON 字符串；无世界观底层时返回占位提示文字。
        """
        if not self.world_ontology:
            return "（无世界观底层元描述，请基于已有前文自行推断世界规则）"
        try:
            return json.dumps(
                self.world_ontology.model_dump(mode="json"),
                ensure_ascii=False, indent=2,
            )
        except Exception as e:
            logger.warning("WorldOntology 序列化失败: %s", e)
            return "（世界观底层序列化失败，请基于已有前文自行推断世界规则）"

    def _format_protagonist_profile(self) -> str:
        """格式化 ProtagonistProfile 为提示词注入文本。

        Returns:
            格式化 JSON 字符串；无主角形象档案时返回占位提示文字。
        """
        if not self.protagonist_profile:
            return "（无主角形象档案，请基于已有前文自行推断主角性格）"
        try:
            return json.dumps(
                self.protagonist_profile.model_dump(mode="json"),
                ensure_ascii=False, indent=2,
            )
        except Exception as e:
            logger.warning("ProtagonistProfile 序列化失败: %s", e)
            return "（主角形象档案序列化失败，请基于已有前文自行推断主角性格）"

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
        """从 novel_profile 获取字段值（getattr 模式）。

        镜像 AgentOrchestrator._get_profile_field。
        """
        profile = self.novel_profile
        if profile is None:
            return ""
        if isinstance(profile, dict):
            return str(profile.get(field_name, "") or "")
        return str(getattr(profile, field_name, "") or "")
