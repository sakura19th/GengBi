"""卷级多章节续写端到端（E2E）测试。

用合成 preset + 合成测试小说，mock LLMClient 的 chat_completion 与
stream_chat_completion，跑 VolumeOrchestrator 全流程（N=3）。

覆盖：
1. 全流程：深度分析 → 卷大纲 → 大纲审计 → 逐章循环（细纲→写作→验证→修订）
   - DeepAnalysis 产出（mock 返回合法 JSON）
   - VolumeOutline 产出（chapters 长度 == 3）
   - OutlineAuditReport 产出（含 revised_outline）
   - 3 章 ChapterArtifacts 累积
   - 最终 Continuation 含 volume_artifacts
   - phase_logs 字段存在
2. 降级场景：mock 深度分析返回非法 JSON，验证流程继续（无 DeepAnalysis 注入）

mock 顺序对齐 VolumeOrchestrator._async_run 的调用序列：
- deep_analysis（1 次 chat，失败重试 1 次）
- volume_outline（1 次 chat，失败重试 1 次）
- outline_audit（1 次 chat，失败重试 1 次，按 audit_rounds 循环）
- outline_final（1 次 chat，失败重试 1 次，终稿大纲生成）
- 每章：chapter_outline（chat）+ writing（stream）+ verify（chat）+ revise 循环（可选）
"""
from __future__ import annotations

import asyncio
import json
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

from novelforge.models import (
    Chapter,
    ContextEntry,
    Continuation,
    DeepAnalysis,
    Prompt,
    PromptOrderEntry,
    PromptOrderGroup,
    VolumeOutline,
    VolumeRunConfig,
    WritingPreset,
)
from novelforge.services.llm_client import StreamChunk
from novelforge.services.volume_orchestrator import VolumeOrchestrator


# ===== JSON fixtures =====


def _deep_analysis_json() -> str:
    """构造合法 DeepAnalysis JSON。"""
    return json.dumps(
        {
            "structure_position": "发展",
            "tone": "紧张悬疑",
            "core_conflict_status": "白热化",
            "stakes": "主角生死",
            "active_characters": [
                {"name": "林晨", "status": "受伤", "motivation": "复仇"}
            ],
            "plot_threads": [
                {"name": "主线", "status": "推进中", "priority": "high"}
            ],
            "unresolved_promises": [
                {"description": "归还宝物", "setup_chapter": "第一章"}
            ],
            "world_state": "战乱年代",
            "plot_arrangement_analysis": "按场景切分，每章起承转合",
            "chapter_structure_pattern": "开头设悬念，中段铺冲突，结尾留钩子",
            "tension_curve_pattern": "逐步升级，章末小高潮",
            "hook_patterns": "悬念+反转钩子交替",
            "style_analysis": "冷峻白话，短句为主",
            "dialogue_analysis": "对话占比约 30%，口语为主",
            "pacing_analysis": "快节奏，少铺垫",
            "character_arc_patterns": [
                {"name": "主角", "arc_trajectory": "成长", "value_shift": "从弱到强"}
            ],
            "foreshadowing_inventory": [
                {
                    "item": "寒霜剑",
                    "status": "planted",
                    "setup_chapter": "1",
                    "importance": "high",
                }
            ],
            "common_tropes": [
                {"trope": "复仇", "description": "血海深仇", "frequency": "高"}
            ],
            "settings_database": [
                {"category": "地点", "name": "江湖", "description": "武林世界"}
            ],
            "recurring_elements": [
                {"element_type": "意象", "image": "残月"}
            ],
            "key_phrases": [{"phrase": "血债血偿", "context": "复仇语境"}],
        },
        ensure_ascii=False,
    )


def _volume_outline_json(chapter_count: int = 3) -> str:
    """构造 chapters 长度 == chapter_count 的 VolumeOutline JSON。"""
    chapters = []
    for i in range(chapter_count):
        chapters.append(
            {
                "index": i + 1,
                "title": f"卷章{i + 1}",
                "summary": f"第{i + 1}章摘要",
                "plot_role": "起" if i == 0 else ("合" if i == chapter_count - 1 else "承"),
                "key_events": [f"事件{i + 1}"],
                "characters_involved": ["林晨"],
                "foreshadowing": "埋设伏笔",
                "chapter_hook": f"第{i + 1}章钩子",
                "target_words": 2000,
            }
        )
    return json.dumps(
        {
            "volume_title": "测试卷",
            "volume_goals": "推进主线",
            "plot_arrangement_analysis": "起承转合布局",
            "pacing_plan": "逐步升级至卷高潮",
            "foreshadowing_plan": "回收寒霜剑伏笔",
            "chapter_count": chapter_count,
            "chapters": chapters,
        },
        ensure_ascii=False,
    )


def _outline_json() -> str:
    """单章场景级细纲 Outline JSON（含 2 个 Scene）。"""
    return json.dumps(
        {
            "continuation_goals": "推动冲突升级",
            "foreshadowing_plan": "埋设宝物失效伏笔",
            "scenes": [
                {
                    "purpose": "对峙",
                    "pov": "主角第三人称限知",
                    "scene_type": "对话",
                    "goal": "逼反派露出破绽",
                    "conflict": "价值观冲突",
                    "outcome": "主角负伤",
                    "value_shift": "信念动摇",
                    "foreshadowing": "宝物裂纹",
                    "exit_hook": "援军到达",
                },
                {
                    "purpose": "撤退",
                    "pov": "主角第三人称限知",
                    "scene_type": "动作",
                    "goal": "安全撤离",
                    "conflict": "追兵围堵",
                    "outcome": "成功脱险",
                    "value_shift": "从绝望到希望",
                    "foreshadowing": "无",
                    "exit_hook": "远方号角",
                },
            ],
        },
        ensure_ascii=False,
    )


def _audit_report_json(chapter_count: int = 3) -> str:
    """OutlineAuditReport JSON（含 revised_outline 完整 VolumeOutline）。"""
    return json.dumps(
        {
            "dimensions": [
                {
                    "dimension": "consistency",
                    "score": 8,
                    "issues": ["小问题"],
                    "suggestions": ["微调"],
                },
                {
                    "dimension": "pacing",
                    "score": 7,
                    "issues": [],
                    "suggestions": [],
                },
            ],
            "overall_assessment": "通过，已微调",
            "passed": True,
            "revised_outline": json.loads(_volume_outline_json(chapter_count)),
        },
        ensure_ascii=False,
    )


def _critique_passed_json() -> str:
    """验证通过 JSON。"""
    return json.dumps(
        {"summary": "续写质量良好，通过验证", "issues": [], "passed": True},
        ensure_ascii=False,
    )


# ===== Fake LLM Client =====


class FakeLLMClient:
    """模拟 LLM 客户端，按顺序返回预设响应。

    chat_completion 与 stream_chat_completion 各维护独立队列，
    按调用顺序依次返回。队列耗尽时返回空内容。
    """

    def __init__(self) -> None:
        self.chat_responses: list[str] = []
        self.stream_responses: list[list[StreamChunk]] = []
        self._chat_idx = 0
        self._stream_idx = 0
        self.chat_call_count = 0
        self.stream_call_count = 0
        self.last_chat_messages: list[dict[str, Any]] = []

    def add_chat_response(self, content: str) -> None:
        self.chat_responses.append(content)

    def add_stream_response(self, chunks: list[StreamChunk]) -> None:
        self.stream_responses.append(chunks)

    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        top_p: float = 1.0,
        stop_event: Any = None,
    ) -> dict:
        self.chat_call_count += 1
        self.last_chat_messages = list(messages)
        if self._chat_idx < len(self.chat_responses):
            content = self.chat_responses[self._chat_idx]
        else:
            content = ""
        self._chat_idx += 1
        return {"choices": [{"message": {"content": content}}]}

    async def stream_chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.8,
        max_tokens: int | None = None,
        top_p: float = 1.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        stop_event: Any = None,
    ):
        self.stream_call_count += 1
        if self._stream_idx < len(self.stream_responses):
            chunks = self.stream_responses[self._stream_idx]
        else:
            chunks = []
        self._stream_idx += 1
        for chunk in chunks:
            yield chunk


# ===== 测试夹具构建 =====


def _make_chapter(
    index: int = 0,
    title: str = "",
    content: str = "",
    chapter_id: str = "",
) -> Chapter:
    """构建测试 Chapter（前文章节）。"""
    c = content or "林晨站在城墙上，望着远方的烽火。战乱已持续三年。"
    return Chapter(
        id=chapter_id or f"ch_{index}",
        project_id="e2e_proj",
        index=index,
        title=title or f"第{index + 1}章",
        content=c,
        word_count=len(c),
    )


def _make_preset(with_marker: bool = True) -> WritingPreset:
    """构建合成 WritingPreset（含 worldInfoBefore marker）。"""
    prompts = [
        Prompt(
            identifier="main",
            name="Main",
            role="system",
            content="你是小说续写助手，请根据前文续写。",
            system_prompt=True,
            position="start",
        ),
    ]
    if with_marker:
        prompts.append(
            Prompt(
                identifier="worldInfoBefore",
                name="World Info Before",
                marker="worldInfoBefore",
                role="system",
                position="start",
            )
        )
    prompts.append(
        Prompt(
            identifier="chatHistory",
            name="Chat History",
            marker="chatHistory",
            role="system",
            position="start",
        )
    )
    order = [PromptOrderEntry(identifier=p.identifier, enabled=True) for p in prompts]
    return WritingPreset(
        id="e2e_preset",
        name="E2E Preset",
        prompts=prompts,
        prompt_order=[PromptOrderGroup(character_id=100000, order=order)],
    )


def _make_context_entries() -> list[ContextEntry]:
    """构建合成上下文条目（2 条）。"""
    return [
        ContextEntry(
            uid="ctx1",
            category="characters",
            content="林晨：主角，剑客，性格冷峻，背负血海深仇。",
            position="before",
            role="system",
            order=0,
        ),
        ContextEntry(
            uid="ctx2",
            category="foreshadowing",
            content="寒霜剑：传说中能斩断一切的宝物。",
            position="before",
            role="system",
            order=1,
        ),
    ]


def _make_orchestrator(
    config: VolumeRunConfig,
    chapters: list[Chapter] | None = None,
    current_chapter: Chapter | None = None,
    user_input: str = "请续写林晨与反派的决战卷",
) -> VolumeOrchestrator:
    """构建测试 VolumeOrchestrator。"""
    if chapters is None:
        current_chapter = current_chapter or _make_chapter()
        chapters = [current_chapter]
    elif current_chapter is None:
        current_chapter = chapters[0]

    return VolumeOrchestrator(
        base_url="http://test",
        api_key="test-key",
        model="test-model",
        parameters={"temperature": 0.8, "max_tokens": 2000},
        preset=_make_preset(),
        chapters=chapters,
        current_chapter=current_chapter,
        context_entries=_make_context_entries(),
        config=config,
        user_input=user_input,
        project_id="e2e_proj",
        chapter_id="ch_0",
    )


def _run(coro):
    """同步运行协程。"""
    return asyncio.run(coro)


# ===== 1. 全流程 E2E 测试（N=3，全阶段开启）=====


def test_volume_e2e_full_flow_n3() -> None:
    """E2E: N=3 全流程，mock LLM 跑卷续写。

    验证：
    - DeepAnalysis 产出（mock 返回合法 JSON）
    - VolumeOutline 产出（chapters 长度 == 3）
    - OutlineAuditReport 产出（含 revised_outline）
    - 3 章 ChapterArtifacts 累积
    - 最终 Continuation 含 volume_artifacts
    - phase_logs 字段存在（list）
    - created_by == "volume"
    """
    # 1. 构建 VolumeRunConfig：N=3，全阶段开启（修订关闭以测试无修订基础流程）
    config = VolumeRunConfig(chapter_count=3)
    config.checkpoints["before_audit"] = False
    config.enable_chapter_revise = False
    assert config.enable_outline_audit is True
    assert config.enable_chapter_verify is True
    assert config.enable_chapter_revise is False

    # 2. 构建 VolumeOrchestrator
    orchestrator = _make_orchestrator(config=config)

    # 3. mock LLMClient，按 _async_run 调用序列注入响应
    fake = FakeLLMClient()
    # 阶段①：深度分析（1 次 chat）
    fake.add_chat_response(_deep_analysis_json())
    # 阶段②：卷大纲（1 次 chat，返回 3 章）
    fake.add_chat_response(_volume_outline_json(3))
    # 阶段③：大纲审计（1 次 chat，含 revised_outline）
    fake.add_chat_response(_audit_report_json(3))
    # 阶段③.5：终稿大纲生成（1 次 chat，返回 3 章终稿大纲）
    fake.add_chat_response(_volume_outline_json(3))
    # 阶段④：逐章循环（3 章，每章细纲 chat + 写作 stream + 验证 chat 通过）
    for i in range(3):
        fake.add_chat_response(_outline_json())  # 单章细纲
        fake.add_stream_response(
            [StreamChunk(content=f"第{i + 1}章正文内容。")]
        )  # 写作
        fake.add_chat_response(_critique_passed_json())  # 验证通过
    orchestrator._client = fake

    # 4. 连接 finished 信号收集结果
    finished_results: list[Continuation] = []
    phase_started_log: list[str] = []
    phase_finished_log: list[tuple[str, Any]] = []
    chapter_started_log: list[int] = []
    chapter_finished_log: list[tuple[int, Any]] = []
    error_results: list[str] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))
    orchestrator.phase_started.connect(lambda name: phase_started_log.append(name))
    orchestrator.phase_finished.connect(
        lambda name, obj: phase_finished_log.append((name, obj))
    )
    orchestrator.chapter_started.connect(lambda idx: chapter_started_log.append(idx))
    orchestrator.chapter_finished.connect(
        lambda idx, ca: chapter_finished_log.append((idx, ca))
    )
    orchestrator.error.connect(lambda msg: error_results.append(msg))

    # 5. 运行 _async_run
    _run(orchestrator._async_run())

    # 6. 断言：无错误
    assert not error_results, f"流程报错: {error_results}"

    # 7. 断言：finished 发射一次，Continuation 含 volume_artifacts
    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    artifacts = cont.volume_artifacts
    assert cont.created_by == "volume"
    assert cont.content, "续写内容为空"

    # 8. 断言：DeepAnalysis 产出
    assert artifacts.deep_analysis is not None
    assert isinstance(artifacts.deep_analysis, DeepAnalysis)
    assert artifacts.deep_analysis.tone == "紧张悬疑"
    assert artifacts.deep_analysis.stakes == "主角生死"
    assert artifacts.deep_analysis.plot_arrangement_analysis == "按场景切分，每章起承转合"

    # 9. 断言：VolumeOutline 产出，chapters 长度 == 3
    assert artifacts.volume_outline is not None
    assert isinstance(artifacts.volume_outline, VolumeOutline)
    assert len(artifacts.volume_outline.chapters) == 3
    assert artifacts.volume_outline.chapter_count == 3
    assert artifacts.volume_outline.volume_title == "测试卷"

    # 10. 断言：OutlineAuditReport 产出（含 revised_outline）
    assert artifacts.audit_report is not None
    assert artifacts.audit_report.revised_outline is not None
    assert artifacts.audit_report.passed is True
    # final_outline 应取 revised_outline
    assert artifacts.final_outline is not None
    assert artifacts.final_outline == artifacts.audit_report.revised_outline

    # 11. 断言：3 章 ChapterArtifacts 累积
    assert len(artifacts.chapter_artifacts) == 3
    for idx, ca in enumerate(artifacts.chapter_artifacts):
        assert ca.chapter_index == idx
        assert ca.content == f"第{idx + 1}章正文内容。"
        # 验证通过，无修订
        assert ca.revision_rounds == 0
        assert ca.final_critique is not None
        assert ca.final_critique.passed is True

    # 12. 断言：phase_logs 字段存在（list）
    assert isinstance(artifacts.phase_logs, list)

    # 13. 断言：phase_started/phase_finished 记录各阶段
    # 卷级阶段：deep_analysis / volume_outline / outline_audit
    assert "deep_analysis" in phase_started_log
    assert "volume_outline" in phase_started_log
    assert "outline_audit" in phase_started_log
    # phase_finished 各阶段产物非 None（审计阶段产物为 OutlineAuditReport）
    phase_names_finished = [name for name, _ in phase_finished_log]
    assert "deep_analysis" in phase_names_finished
    assert "volume_outline" in phase_names_finished
    assert "outline_audit" in phase_names_finished

    # 14. 断言：chapter_started/chapter_finished 记录 3 章
    assert chapter_started_log == [0, 1, 2]
    assert len(chapter_finished_log) == 3
    assert [idx for idx, _ in chapter_finished_log] == [0, 1, 2]

    # 15. 断言：3 章正文均出现在拼接后的 content 中
    for i in range(3):
        assert f"第{i + 1}章正文内容" in cont.content

    # 16. 断言：prompt_snapshot 非空（最后一章写作阶段 messages）
    assert cont.prompt_snapshot, "prompt_snapshot 为空"


# ===== 2. 降级场景：深度分析返回非法 JSON =====


def test_volume_e2e_deep_analysis_degradation() -> None:
    """降级场景：mock 深度分析返回非法 JSON，验证流程继续（无 DeepAnalysis 注入）。

    深度分析两次都返回非法 JSON（触发重试 + 降级返回 None），
    卷大纲阶段以空 deep_analysis 继续规划，逐章循环正常完成。
    """
    config = VolumeRunConfig(chapter_count=3)
    config.checkpoints["before_audit"] = False
    config.enable_chapter_revise = False
    orchestrator = _make_orchestrator(config=config)

    fake = FakeLLMClient()
    # 阶段①：深度分析两次都失败（非法 JSON → 重试 → 仍非法 → 降级 None）
    fake.add_chat_response("这不是合法 JSON {乱码")
    fake.add_chat_response("{still not valid json")
    # 阶段②：卷大纲（1 次 chat，返回 3 章；deep_analysis 注入为空字符串）
    fake.add_chat_response(_volume_outline_json(3))
    # 阶段③：大纲审计（1 次 chat）
    fake.add_chat_response(_audit_report_json(3))
    # 阶段③.5：终稿大纲生成（1 次 chat）
    fake.add_chat_response(_volume_outline_json(3))
    # 阶段④：逐章循环（3 章）
    for i in range(3):
        fake.add_chat_response(_outline_json())
        fake.add_stream_response(
            [StreamChunk(content=f"降级第{i + 1}章正文。")]
        )
        fake.add_chat_response(_critique_passed_json())
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    error_results: list[str] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))
    orchestrator.error.connect(lambda msg: error_results.append(msg))

    _run(orchestrator._async_run())

    # 无错误（深度分析降级不阻塞流程）
    assert not error_results, f"降级场景不应报错: {error_results}"
    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    artifacts = cont.volume_artifacts

    # DeepAnalysis 降级为 None
    assert artifacts.deep_analysis is None
    # 卷大纲仍生成
    assert artifacts.volume_outline is not None
    assert len(artifacts.volume_outline.chapters) == 3
    # 审计仍产出
    assert artifacts.audit_report is not None
    # 3 章产物累积
    assert len(artifacts.chapter_artifacts) == 3
    for idx, ca in enumerate(artifacts.chapter_artifacts):
        assert ca.content == f"降级第{idx + 1}章正文。"
    # content 拼接 3 章
    for i in range(3):
        assert f"降级第{i + 1}章正文" in cont.content


# ===== 3. 全流程 + 修订循环（验证 mock 覆盖写作/验证/修订）=====


def test_volume_e2e_with_revise_loop() -> None:
    """E2E: N=3，第1章验证未通过触发修订一轮后通过，验证 mock 覆盖修订链路。

    mock 覆盖：深度分析/卷大纲/审计/单章细纲/写作/验证/修订 全部 LLM 调用。
    """
    config = VolumeRunConfig(chapter_count=3)
    config.max_revise_rounds_per_chapter = 1
    config.checkpoints["before_audit"] = False
    orchestrator = _make_orchestrator(config=config)

    fake = FakeLLMClient()
    # 阶段①：深度分析
    fake.add_chat_response(_deep_analysis_json())
    # 阶段②：卷大纲（3 章）
    fake.add_chat_response(_volume_outline_json(3))
    # 阶段③：审计
    fake.add_chat_response(_audit_report_json(3))
    # 阶段③.5：终稿大纲生成（1 次 chat）
    fake.add_chat_response(_volume_outline_json(3))
    # 第1章：细纲 + 写作(初稿) + 验证失败 + 修订指导 + 写作(修订稿) + 验证通过
    fake.add_chat_response(_outline_json())
    fake.add_stream_response([StreamChunk(content="初稿")])
    fake.add_chat_response(
        json.dumps(
            {
                "summary": "不通过",
                "issues": [
                    {
                        "category": "consistency",
                        "severity": "major",
                        "location": "第1段",
                        "description": "不一致",
                        "suggestion": "修改",
                    }
                ],
                "passed": False,
            },
            ensure_ascii=False,
        )
    )
    fake.add_chat_response(
        json.dumps(
            {
                "revision_strategy": "修正一致性",
                "key_changes": [
                    {
                        "issue_ref": "consistency",
                        "revision_action": "修改时间线",
                        "target_section": "第1段",
                    }
                ],
                "preserve_elements": "对话",
            },
            ensure_ascii=False,
        )
    )
    fake.add_stream_response([StreamChunk(content="修订稿")])
    fake.add_chat_response(_critique_passed_json())
    # 第2章、第3章：细纲 + 写作(初稿) + 验证通过 + 强制修改(修订指导+修订稿+验证通过)
    for i in range(2, 4):
        fake.add_chat_response(_outline_json())
        fake.add_stream_response([StreamChunk(content=f"第{i}章初稿")])
        fake.add_chat_response(_critique_passed_json())
        # 强制修改：审计①通过后仍触发 1 轮修改
        fake.add_chat_response(
            json.dumps(
                {
                    "revision_strategy": "润色提升",
                    "key_changes": [],
                    "preserve_elements": "",
                },
                ensure_ascii=False,
            )
        )
        fake.add_stream_response([StreamChunk(content=f"第{i}章正文")])
        fake.add_chat_response(_critique_passed_json())
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    _run(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    artifacts = cont.volume_artifacts
    assert artifacts is not None
    assert len(artifacts.chapter_artifacts) == 3

    # 第1章经过修订（审计失败触发）
    ca1 = artifacts.chapter_artifacts[0]
    assert ca1.revision_rounds == 1
    assert ca1.content == "修订稿"
    assert ca1.final_critique is not None
    assert ca1.final_critique.passed is True

    # 第2、3章强制修改（审计通过后仍触发 1 轮修改）
    for idx, ca in enumerate(artifacts.chapter_artifacts[1:], start=2):
        assert ca.revision_rounds == 1
        assert ca.content == f"第{idx}章正文"
        assert ca.final_critique is not None
        assert ca.final_critique.passed is True

    # content 拼接：修订稿 + 第2章 + 第3章
    assert "修订稿" in cont.content
    assert "第2章正文" in cont.content
    assert "第3章正文" in cont.content
