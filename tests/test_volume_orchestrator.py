"""VolumeOrchestrator 测试。

覆盖以下用例：
1. 深度分析成功产出 DeepAnalysis
2. 深度分析失败降级（返回 None，流程继续）
3. 卷大纲成功产出 VolumeOutline（chapters 长度 == chapter_count）
4. 卷大纲失败 emit error
5. 审计成功产出 OutlineAuditReport（含 revised_outline）
6. 审计失败降级用原大纲
7. 逐章循环（N=2，每章细纲→写作→验证→修订）
8. 验证未通过时修订循环（rounds < max）
9. 暂停点（after_deep_analysis）触发 checkpoint_reached
10. 停止机制（stop() 后逐章循环中断）

由于 pytest_asyncio 未安装，使用 asyncio.run() 执行异步逻辑。
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

# 设置离屏平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from novelforge.models import (
    Chapter,
    ContextEntry,
    Continuation,
    DeepAnalysis,
    Outline,
    OutlineAuditReport,
    Prompt,
    PromptOrderEntry,
    PromptOrderGroup,
    VolumeOutline,
    VolumeRunConfig,
    WritingPreset,
    ChapterStageArtifact,
)
from novelforge.services.llm_client import StreamChunk
from novelforge.services.volume_orchestrator import VolumeOrchestrator


# ===== JSON fixtures =====

DEEP_ANALYSIS_JSON = json.dumps(
    {
        "structure_position": "发展",
        "tone": "紧张",
        "core_conflict_status": "白热化",
        "stakes": "生死",
        "active_characters": [
            {"name": "主角", "status": "受伤", "motivation": "复仇"}
        ],
        "plot_threads": [{"name": "主线", "status": "推进中", "priority": "high"}],
        "unresolved_promises": [
            {"description": "承诺", "setup_chapter": "第1章"}
        ],
        "world_state": "战乱",
        "plot_arrangement_analysis": "按场景切分",
        "chapter_structure_pattern": "起承转合",
        "tension_curve_pattern": "逐步升级",
        "hook_patterns": "悬念钩子",
        "style_analysis": "冷峻白话",
        "dialogue_analysis": "口语对白",
        "pacing_analysis": "快节奏",
        "character_arc_patterns": [
            {"arc": "主角复仇弧光", "pattern": "从信念坚定到信念动摇"}
        ],
        "foreshadowing_inventory": [
            {"item": "宝物", "status": "planted", "setup_chapter": "1", "importance": "high"}
        ],
        "common_tropes": [{"trope": "复仇", "description": "血海深仇", "frequency": "高"}],
        "settings_database": [
            {"category": "地点", "name": "江湖", "description": "武林世界"}
        ],
        "recurring_elements": [{"element_type": "意象", "image": "残月"}],
        "key_phrases": [{"phrase": "血债血偿"}],
    },
    ensure_ascii=False,
)


def make_volume_outline_json(chapter_count: int = 2) -> str:
    """构造 chapters 长度 == chapter_count 的 VolumeOutline JSON。"""
    chapters = []
    for i in range(chapter_count):
        chapters.append(
            {
                "index": i + 1,
                "title": f"卷章{i + 1}",
                "summary": f"第{i + 1}章摘要",
                "plot_role": "起" if i == 0 else "合",
                "key_events": [f"事件{i + 1}"],
                "characters_involved": ["主角"],
                "foreshadowing": "埋设伏笔",
                "chapter_hook": "钩子",
                "target_words": 2000,
            }
        )
    return json.dumps(
        {
            "volume_title": "测试卷",
            "volume_goals": "推进剧情",
            "plot_arrangement_analysis": "起承转合",
            "pacing_plan": "逐步升级",
            "foreshadowing_plan": "回收伏笔",
            "chapter_count": chapter_count,
            "chapters": chapters,
        },
        ensure_ascii=False,
    )


def make_outline_json() -> str:
    """单章细纲 Outline JSON。"""
    return json.dumps(
        {
            "continuation_goals": "推动冲突升级",
            "foreshadowing_plan": "埋设宝物失效伏笔",
            "scenes": [
                {
                    "purpose": "对峙",
                    "pov": "主角",
                    "scene_type": "对话",
                    "goal": "逼反派露出破绽",
                    "conflict": "价值观冲突",
                    "outcome": "主角负伤",
                    "value_shift": "信念动摇",
                    "foreshadowing": "宝物裂纹",
                    "exit_hook": "援军到达",
                }
            ],
        },
        ensure_ascii=False,
    )


def make_audit_report_json(chapter_count: int = 2) -> str:
    """OutlineAuditReport JSON（含 revised_outline）。"""
    return json.dumps(
        {
            "dimensions": [
                {
                    "dimension": "consistency",
                    "score": 8,
                    "issues": ["小问题"],
                    "suggestions": ["微调"],
                }
            ],
            "overall_assessment": "通过",
            "passed": True,
            "revised_outline": json.loads(make_volume_outline_json(chapter_count)),
        },
        ensure_ascii=False,
    )


CRITIQUE_PASSED_JSON = json.dumps(
    {"summary": "通过", "issues": [], "passed": True},
    ensure_ascii=False,
)

CRITIQUE_FAILED_JSON = json.dumps(
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

REVISE_JSON = json.dumps(
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


# ===== Fake LLM Client =====


class FakeLLMClient:
    """模拟 LLM 客户端，按顺序返回预设响应。"""

    def __init__(self) -> None:
        self.chat_responses: list[str] = []
        self.stream_responses: list[list[StreamChunk]] = []
        self._chat_idx = 0
        self._stream_idx = 0
        self.last_stream_messages: list[dict[str, Any]] = []
        self.chat_call_count = 0
        self.stream_call_count = 0
        # 每次 chat_completion 调用的 messages 快照（供测试断言宏注入）
        self.chat_messages_history: list[list[dict[str, Any]]] = []

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
        self.chat_messages_history.append(list(messages))
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
        self.last_stream_messages = list(messages)
        if self._stream_idx < len(self.stream_responses):
            chunks = self.stream_responses[self._stream_idx]
        else:
            chunks = []
        self._stream_idx += 1
        for chunk in chunks:
            yield chunk


# ===== Helper functions =====


def make_chapter(
    index: int = 0,
    title: str = "",
    content: str = "",
    chapter_id: str = "",
) -> Chapter:
    """构建测试 Chapter。"""
    c = content or "前文章节正文内容。"
    return Chapter(
        id=chapter_id or f"ch_{index}",
        project_id="test_proj",
        index=index,
        title=title or f"第{index + 1}章",
        content=c,
        word_count=len(c),
    )


def make_preset(with_marker: bool = True) -> WritingPreset:
    """构建测试 WritingPreset。"""
    prompts = [
        Prompt(
            identifier="main",
            name="Main",
            role="system",
            content="You are a novel writer.",
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

    order = [
        PromptOrderEntry(identifier=p.identifier, enabled=True) for p in prompts
    ]

    return WritingPreset(
        id="test_preset",
        name="Test Preset",
        prompts=prompts,
        prompt_order=[PromptOrderGroup(character_id=100000, order=order)],
    )


def make_orchestrator(
    config: VolumeRunConfig | None = None,
    context_entries: list[ContextEntry] | None = None,
    preset: WritingPreset | None = None,
    chapters: list[Chapter] | None = None,
    current_chapter: Chapter | None = None,
    user_input: str = "",
    with_marker: bool = True,
) -> VolumeOrchestrator:
    """构建测试 VolumeOrchestrator。"""
    if config is None:
        config = VolumeRunConfig(chapter_count=2)
    if context_entries is None:
        context_entries = []
    if preset is None:
        preset = make_preset(with_marker=with_marker)
    if chapters is None:
        current_chapter = current_chapter or make_chapter()
        chapters = [current_chapter]
    elif current_chapter is None:
        current_chapter = chapters[0]

    return VolumeOrchestrator(
        base_url="http://test",
        api_key="test-key",
        model="test-model",
        parameters={"temperature": 0.8, "max_tokens": 2000},
        preset=preset,
        chapters=chapters,
        current_chapter=current_chapter,
        context_entries=context_entries,
        config=config,
        user_input=user_input,
    )


def run_async(coro):
    """同步运行协程。"""
    return asyncio.run(coro)


# ===== 1. 深度分析成功产出 DeepAnalysis =====


def test_deep_analysis_success() -> None:
    """深度分析成功产出 DeepAnalysis（关闭审计与验证，聚焦阶段①）。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    assert cont.volume_artifacts.deep_analysis is not None
    assert cont.volume_artifacts.deep_analysis.tone == "紧张"
    assert cont.created_by == "volume"


# ===== 2. 深度分析失败降级（返回 None，流程继续）=====


def test_deep_analysis_degradation() -> None:
    """深度分析两次都失败，返回 None，流程继续（卷大纲基于空 deep_analysis）。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    # 深度分析两次都失败
    fake.add_chat_response("乱码1")
    fake.add_chat_response("乱码2")
    fake.add_chat_response(make_volume_outline_json(2))
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    assert cont.volume_artifacts.deep_analysis is None
    # 卷大纲仍生成
    assert cont.volume_artifacts.volume_outline is not None


# ===== 3. 卷大纲成功产出 VolumeOutline（chapters 长度 == chapter_count）=====


def test_volume_outline_success() -> None:
    """卷大纲成功产出，chapters 长度 == chapter_count。"""
    config = VolumeRunConfig(chapter_count=3)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(3))
    # 逐章：细纲 + 写作（3 章）
    for _ in range(3):
        fake.add_chat_response(make_outline_json())
        fake.add_stream_response([StreamChunk(content="章节正文")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    outline = cont.volume_artifacts.volume_outline
    assert outline is not None
    assert len(outline.chapters) == 3
    assert outline.chapter_count == 3


# ===== 4. 卷大纲失败 emit error =====


def test_volume_outline_failure_emits_error() -> None:
    """卷大纲两次都失败，emit error 终止流程。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    # 卷大纲两次都失败
    fake.add_chat_response("乱码1")
    fake.add_chat_response("乱码2")
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    error_results: list[str] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))
    orchestrator.error.connect(lambda msg: error_results.append(msg))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 0
    assert len(error_results) == 1
    assert "卷大纲" in error_results[0]


# ===== 4b. 卷大纲章节数不足 emit error =====


def test_volume_outline_error_feedback_retry() -> None:
    """卷大纲第一次校验失败，错误反馈给 LLM 后第二次重试成功。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    # 第一次卷大纲响应：plot_role 非法（触发 ValidationError）
    bad_outline = json.loads(make_volume_outline_json(2))
    bad_outline["chapters"][0]["plot_role"] = "无效角色"
    fake.add_chat_response(json.dumps(bad_outline, ensure_ascii=False))
    # 第二次卷大纲响应：合法（重试成功）
    fake.add_chat_response(make_volume_outline_json(2))
    # 逐章：细纲 + 写作（2 章）
    for _ in range(2):
        fake.add_chat_response(make_outline_json())
        fake.add_stream_response([StreamChunk(content="章节正文")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))
    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    outline = cont.volume_artifacts.volume_outline
    assert outline is not None
    assert len(outline.chapters) == 2
    # 验证第二次卷大纲调用时 messages 含 assistant（LLM 上次输出）+ user 反馈消息
    # chat_messages_history: [0]=深度分析, [1]=卷大纲第一次, [2]=卷大纲第二次
    second_outline_messages = fake.chat_messages_history[2]
    assistant_msgs = [
        m for m in second_outline_messages if m.get("role") == "assistant"
    ]
    user_feedbacks = [
        m for m in second_outline_messages if m.get("role") == "user"
    ]
    assert len(assistant_msgs) >= 1
    assert len(user_feedbacks) >= 1
    feedback_content = user_feedbacks[-1]["content"]
    assert "上次输出校验失败" in feedback_content
    assert "plot_role" in feedback_content


def test_volume_outline_chapter_count_short_triggers_retry() -> None:
    """卷大纲章节数不足触发反馈重试，第二次返回正确章节数后成功。"""
    config = VolumeRunConfig(chapter_count=3)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    # 第一次卷大纲响应：只返回 2 章（期望 3 章，触发 ValueError 重试）
    fake.add_chat_response(make_volume_outline_json(2))
    # 第二次卷大纲响应：返回 3 章（重试成功）
    fake.add_chat_response(make_volume_outline_json(3))
    # 逐章：细纲 + 写作（3 章）
    for _ in range(3):
        fake.add_chat_response(make_outline_json())
        fake.add_stream_response([StreamChunk(content="章节正文")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))
    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    outline = cont.volume_artifacts.volume_outline
    assert outline is not None
    assert len(outline.chapters) == 3
    # 验证第二次卷大纲调用时 messages 含 assistant + user 反馈消息
    second_outline_messages = fake.chat_messages_history[2]
    user_feedbacks = [
        m for m in second_outline_messages if m.get("role") == "user"
    ]
    assert len(user_feedbacks) >= 1
    assert "章节数不足" in user_feedbacks[-1]["content"]


def test_volume_outline_chapter_count_mismatch() -> None:
    """卷大纲两次都章节数不足，最终 emit error 终止流程。"""
    config = VolumeRunConfig(chapter_count=3)
    config.enable_outline_audit = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    # 两次卷大纲都只返回 2 章（期望 3 章，两次重试都失败）
    fake.add_chat_response(make_volume_outline_json(2))
    fake.add_chat_response(make_volume_outline_json(2))
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    error_results: list[str] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))
    orchestrator.error.connect(lambda msg: error_results.append(msg))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 0
    assert len(error_results) == 1


# ===== 5. 审计成功产出 OutlineAuditReport（含 revised_outline）=====


def test_outline_audit_success() -> None:
    """审计成功产出 OutlineAuditReport，final_outline 取 revised_outline。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = True
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    config.checkpoints["before_audit"] = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    fake.add_chat_response(make_audit_report_json(2))
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    assert cont.volume_artifacts.audit_report is not None
    assert cont.volume_artifacts.audit_report.revised_outline is not None
    assert cont.volume_artifacts.final_outline is not None
    # final_outline 应取 revised_outline
    assert (
        cont.volume_artifacts.final_outline
        == cont.volume_artifacts.audit_report.revised_outline
    )


# ===== 6. 审计失败降级用原大纲 =====


def test_outline_audit_degradation() -> None:
    """审计两次都失败，降级用原大纲作为 final_outline。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = True
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    config.checkpoints["before_audit"] = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 审计两次都失败
    fake.add_chat_response("乱码1")
    fake.add_chat_response("乱码2")
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    assert cont.volume_artifacts.audit_report is None
    # final_outline 应为原 volume_outline
    assert cont.volume_artifacts.final_outline is not None
    assert (
        cont.volume_artifacts.final_outline == cont.volume_artifacts.volume_outline
    )


# ===== 7. 逐章循环（N=2，每章细纲→写作→验证→修订）=====


def test_chapter_loop_n2() -> None:
    """逐章循环 N=2，每章细纲→写作→验证通过，无修订（关闭修订开关）。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = True
    config.enable_chapter_revise = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 第1章：细纲 + 写作 + 验证通过
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    # 第2章：细纲 + 写作 + 验证通过
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    chapter_finished_results: list[tuple[int, Any]] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))
    orchestrator.chapter_finished.connect(
        lambda idx, ca: chapter_finished_results.append((idx, ca))
    )

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    assert len(cont.volume_artifacts.chapter_artifacts) == 2
    assert len(chapter_finished_results) == 2
    # 验证两章正文拼接
    assert "第一章正文" in cont.content
    assert "第二章正文" in cont.content
    # 无修订
    for ca in cont.volume_artifacts.chapter_artifacts:
        assert ca.revision_rounds == 0
        assert ca.final_critique is not None
        assert ca.final_critique.passed is True


# ===== 8. 验证未通过时修订循环（rounds < max）=====


def test_revise_loop() -> None:
    """第1章验证未通过，修订一轮后通过。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = True
    config.enable_chapter_revise = True
    config.max_revise_rounds_per_chapter = 1
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 第1章：细纲 + 写作(初稿) + 验证失败 + 修订指导 + 写作(修订稿) + 验证通过
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="初稿")])
    fake.add_chat_response(CRITIQUE_FAILED_JSON)
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="修订稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    # 第2章：细纲 + 写作 + 验证通过
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    assert len(cont.volume_artifacts.chapter_artifacts) == 2
    ca = cont.volume_artifacts.chapter_artifacts[0]
    assert ca.revision_rounds == 1
    assert ca.content == "修订稿"
    assert ca.final_critique is not None
    assert ca.final_critique.passed is True


# ===== 8b. 修订循环上限：critique 始终不通过 =====


def test_revise_loop_max_rounds() -> None:
    """critique 始终不通过，rounds 不超过 max。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = True
    config.enable_chapter_revise = True
    config.max_revise_rounds_per_chapter = 2
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 第1章：细纲 + 写作(初稿) + 验证失败
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="初稿")])
    fake.add_chat_response(CRITIQUE_FAILED_JSON)
    # 第1轮修订：指导 + 写作 + 验证失败
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="修订1")])
    fake.add_chat_response(CRITIQUE_FAILED_JSON)
    # 第2轮修订：指导 + 写作 + 验证失败
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="修订2")])
    fake.add_chat_response(CRITIQUE_FAILED_JSON)
    # 第2章：细纲 + 写作 + 验证通过
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    ca = cont.volume_artifacts.chapter_artifacts[0]
    assert ca.revision_rounds == 2
    assert ca.content == "修订2"
    assert ca.final_critique is not None
    assert ca.final_critique.passed is False


# ===== 9. 暂停点（after_deep_analysis）触发 checkpoint_reached =====


def test_checkpoint_after_deep_analysis() -> None:
    """暂停点 after_deep_analysis：emit checkpoint_reached，resume 后流程继续。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    config.checkpoints["after_deep_analysis"] = True
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    async def run() -> None:
        checkpoint_event = asyncio.Event()
        orchestrator.checkpoint_reached.connect(
            lambda name, payload: checkpoint_event.set()
        )

        task = asyncio.create_task(orchestrator._async_run())
        await asyncio.wait_for(checkpoint_event.wait(), timeout=5.0)

        # 暂停点触发，传回编辑后的 DeepAnalysis
        edited = DeepAnalysis(tone="编辑后的基调")
        orchestrator.resume(edited)

        await task

    run_async(run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts.deep_analysis is not None
    assert cont.volume_artifacts.deep_analysis.tone == "编辑后的基调"


# ===== 9b. 暂停点停止：暂停期间调 stop() =====


def test_checkpoint_stop() -> None:
    """暂停期间调 stop()，验证任务取消，无 finished。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    config.checkpoints["after_deep_analysis"] = True
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    async def run() -> None:
        checkpoint_event = asyncio.Event()
        orchestrator.checkpoint_reached.connect(
            lambda name, payload: checkpoint_event.set()
        )

        task = asyncio.create_task(orchestrator._async_run())
        orchestrator._task = task  # 供 stop() 取消
        await asyncio.wait_for(checkpoint_event.wait(), timeout=5.0)

        orchestrator.stop()

        with pytest.raises(asyncio.CancelledError):
            await task

    run_async(run())

    assert len(finished_results) == 0


# ===== 10. 停止机制（stop() 后逐章循环中断）=====


def test_stop_during_chapter_loop() -> None:
    """逐章循环期间调 stop()，循环中断，已生成章节保留。

    通过在 chapter_started 信号回调中触发 stop() 模拟循环中断。
    """
    config = VolumeRunConfig(chapter_count=3)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(3))
    # 第1章正常完成
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    # 第2章细纲（写作前会被停止）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    # 在第2章 chapter_started 时设置 stop_event
    def on_chapter_started(idx: int) -> None:
        if idx == 1:
            orchestrator._stop_event.set()

    orchestrator.chapter_started.connect(on_chapter_started)

    run_async(orchestrator._async_run())

    # 第2章 chapter_started 触发了 stop_event，但第2章的细纲/写作已开始
    # 第3章不会开始（循环在 i=2 检查 stop_event 被中断）
    # 注意：第2章可能完成也可能在写作流式过程中被中断，取决于时序
    # 关键断言：第3章未开始，且如果有 finished 则只含前两章
    if finished_results:
        cont = finished_results[0]
        # 至多 2 章产物（第3章未开始）
        assert len(cont.volume_artifacts.chapter_artifacts) <= 2


# ===== 11. worldInfoBefore marker 不存在：fallback 直接 prepend system 消息 =====


def test_writing_marker_absent_fallback() -> None:
    """worldInfoBefore marker 不存在时，fallback 直接 prepend system 消息。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    orchestrator = make_orchestrator(config=config, with_marker=False)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # 验证第一条消息是 system 消息且包含大纲 Markdown（fallback prepend）
    messages = fake.last_stream_messages
    assert len(messages) > 0
    assert messages[0]["role"] == "system"
    assert "# 续写大纲" in messages[0]["content"]


# ===== 12. worldInfoBefore marker 存在：大纲通过 ContextEntry 注入 =====


def test_writing_marker_exists() -> None:
    """worldInfoBefore marker 存在时，大纲通过 ContextEntry 注入到 messages。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    orchestrator = make_orchestrator(config=config, with_marker=True)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # 验证大纲 Markdown 出现在 messages 中（通过 worldInfoBefore marker 注入）
    messages = fake.last_stream_messages
    found = any("# 续写大纲" in m.get("content", "") for m in messages)
    assert found, "大纲 Markdown 应通过 worldInfoBefore marker 注入到 messages"


# ===== 13. 章节追加到 chapters 列表（供下一章前文）=====


def test_chapters_appended() -> None:
    """逐章循环中，每章正文作为新 Chapter 追加到 chapters 列表。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    initial_chapter = make_chapter()
    orchestrator = make_orchestrator(
        config=config, chapters=[initial_chapter], current_chapter=initial_chapter
    )
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # 初始 1 章 + 2 章新追加 = 3 章
    assert len(orchestrator.chapters) == 3
    assert orchestrator.chapters[1].content == "第一章正文"
    assert orchestrator.chapters[2].content == "第二章正文"


# ===== 14. get_writing_messages / get_writing_model =====


def test_get_writing_messages_and_model() -> None:
    """get_writing_messages 返回最后一章写作阶段 messages，model 正确。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    messages = orchestrator.get_writing_messages()
    assert len(messages) > 0
    assert orchestrator.get_writing_model() == "test-model"


# ===== 15. 审计阶段注入前文参考与深度分析（Task 7）=====


def test_outline_audit_injects_previous_chapters_and_deep_analysis() -> None:
    """审计阶段 system prompt 包含 previous_chapters_text 与 deep_analysis JSON。

    chat 调用顺序：deep_analysis(0) → volume_outline(1) → outline_audit(2)。
    验证 audit 调用的 system prompt 含初始章节正文与 DeepAnalysis 序列化内容。
    """
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = True
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    config.checkpoints["before_audit"] = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    fake.add_chat_response(make_audit_report_json(2))
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # audit 调用是第 3 个 chat_completion（索引 2）
    assert len(fake.chat_messages_history) >= 3
    audit_messages = fake.chat_messages_history[2]
    assert audit_messages[0]["role"] == "system"
    audit_prompt = audit_messages[0]["content"]
    # previous_chapters_text：初始章节正文（make_chapter 默认内容）
    assert "前文章节正文内容" in audit_prompt
    # deep_analysis：DeepAnalysis 序列化后的 tone 字段值
    assert "紧张" in audit_prompt
    # 确认无残留占位符
    assert "{{previous_chapters_text}}" not in audit_prompt
    assert "{{deep_analysis}}" not in audit_prompt


# ===== 16. 章节衔接：第 2 章注入第 1 章正文与第 2 章 ChapterPlan（Task 8）=====


def test_chapter_continuity_chapter2_injects_chapter1_and_plan() -> None:
    """生成第 2 章时，写作 messages 含第 1 章正文与第 2 章 ChapterPlan 派生要求。

    - 第 2 章写作阶段（最后一次 stream 调用）的 messages 应含：
      - 上一章正文 system 消息（含"第一章正文"）
      - 本章生成要求（含第 2 章 ChapterPlan 的标题"卷章2"与摘要"第2章摘要"）
    - 第 2 章细纲阶段（第 4 个 chat 调用，索引 3）system prompt 应含
      previous_chapter_text（"第一章正文"）与 chapter_plan（"卷章2"）。
    """
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 第1章：细纲 + 写作
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    # 第2章：细纲 + 写作
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # ===== 验证第 2 章细纲调用（chat 索引 3）=====
    # chat 顺序：deep_analysis(0) → volume_outline(1) → ch1_outline(2) → ch2_outline(3)
    assert len(fake.chat_messages_history) >= 4
    ch2_outline_messages = fake.chat_messages_history[3]
    ch2_outline_prompt = ch2_outline_messages[0]["content"]
    # previous_chapter_text：第 1 章正文
    assert "第一章正文" in ch2_outline_prompt
    # chapter_plan：第 2 章 ChapterPlan 的标题
    assert "卷章2" in ch2_outline_prompt
    # 无残留占位符
    assert "{{previous_chapter_text}}" not in ch2_outline_prompt

    # ===== 验证第 2 章写作调用（最后一次 stream）=====
    writing_messages = fake.last_stream_messages
    # 拼接所有 message 内容用于断言
    all_content = "\n".join(m.get("content", "") for m in writing_messages)
    # 上一章正文 system 消息含第 1 章正文
    assert "第一章正文" in all_content
    # 本章生成要求含第 2 章 ChapterPlan 的标题与摘要
    assert "卷章2" in all_content
    assert "第2章摘要" in all_content
    # 不应包含卷级 user_input 作为本章生成要求主体（user_input 默认空字符串）
    # 验证存在上一章正文 system 消息
    has_prev_chapter_sys = any(
        m.get("role") == "system"
        and "上一章正文" in m.get("content", "")
        and "第一章正文" in m.get("content", "")
        for m in writing_messages
    )
    assert has_prev_chapter_sys, "写作 messages 应含上一章正文 system 消息"


# ===== 17. 深度分析按 token 切分：多块调用 + 增量携带 + 合并（Task 3）=====


def test_deep_analysis_chunked_incremental() -> None:
    """深度分析按 token 切分：多块调用 + 增量携带 + 合并。

    - analysis_chunk_tokens=10 使 2 章切分为 2 块
    - 第1块返回 tone="紧张" 的 DeepAnalysis
    - 第2块返回 structure_position="中段" 的 DeepAnalysis
    - 验证深度分析阶段调用 chat_completion >=2 次
    - 验证合并后 tone=="紧张" 且 structure_position=="中段"
    - 验证第2块调用 messages 含第1块的分析内容（增量携带）
    """
    config = VolumeRunConfig(chapter_count=2)
    config.analysis_chunk_tokens = 10
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False

    # 构建足够长的章节内容以确保切分（中文约 len*0.6 token）
    long_content = "这是一个很长的章节正文内容用于测试切分功能，内容需要足够长才能触发切分。"
    ch1 = make_chapter(index=0, content=long_content, title="第一章")
    ch2 = make_chapter(index=1, content=long_content, title="第二章")
    # current_chapter=ch2，切分判断基于全量 chapters（2 章），10 tokens 阈值切分为 2 块
    orchestrator = make_orchestrator(
        config=config, chapters=[ch1, ch2], current_chapter=ch2
    )

    fake = FakeLLMClient()
    # 深度分析第1块：tone="紧张"
    fake.add_chat_response(
        json.dumps({"tone": "紧张"}, ensure_ascii=False)
    )
    # 深度分析第2块：structure_position="中段"
    fake.add_chat_response(
        json.dumps({"structure_position": "中段"}, ensure_ascii=False)
    )
    # 【信息汇总】环节：返回整合后的 DeepAnalysis（两字段均存在）
    fake.add_chat_response(
        json.dumps(
            {"tone": "紧张", "structure_position": "中段"},
            ensure_ascii=False,
        )
    )
    # 卷大纲
    fake.add_chat_response(make_volume_outline_json(2))
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    da = cont.volume_artifacts.deep_analysis
    assert da is not None
    # 汇总后两个字段都存在（来自【信息汇总】LLM 整合结果）
    assert da.tone == "紧张"
    assert da.structure_position == "中段"

    # chat 调用顺序：da_chunk1(0) → da_chunk2(1) → da_merge(2) → volume_outline(3) → ch1_outline(4) → ch2_outline(5)
    # 验证深度分析阶段调用了 >=3 次 chat_completion（含信息汇总）
    assert len(fake.chat_messages_history) >= 4
    # 第2块（索引1）的 messages 应含第1块的分析内容（增量携带）
    chunk2_messages = fake.chat_messages_history[1]
    chunk2_prompt = chunk2_messages[0]["content"]
    assert "已有分析内容" in chunk2_prompt
    assert "紧张" in chunk2_prompt  # 第1块的 tone 值被携带
    # 信息汇总（索引2）的 messages 应含程序化合并后的 DeepAnalysis
    merge_messages = fake.chat_messages_history[2]
    merge_prompt = merge_messages[0]["content"]
    assert "紧张" in merge_prompt  # 合并后的 deep_analysis JSON 被注入
    assert "中段" in merge_prompt


# ===== 18. _current_chapter_index 计算（Task 5）=====


def test_current_chapter_index_computation() -> None:
    """_current_chapter_index 在 current_chapter 在列表中时正确计算，不在时为 -1。"""
    # 构造 3 章，current_chapter 为第 2 章
    ch1 = make_chapter(index=0, chapter_id="ch_a")
    ch2 = make_chapter(index=1, chapter_id="ch_b")
    ch3 = make_chapter(index=2, chapter_id="ch_c")
    orchestrator = make_orchestrator(
        chapters=[ch1, ch2, ch3], current_chapter=ch2
    )
    assert orchestrator._current_chapter_index == 1

    # current_chapter 为第 1 章
    orchestrator2 = make_orchestrator(
        chapters=[ch1, ch2, ch3], current_chapter=ch1
    )
    assert orchestrator2._current_chapter_index == 0

    # current_chapter 为第 3 章
    orchestrator3 = make_orchestrator(
        chapters=[ch1, ch2, ch3], current_chapter=ch3
    )
    assert orchestrator3._current_chapter_index == 2

    # current_chapter 不在 chapters 列表中（id 不匹配）
    other_chapter = make_chapter(index=99, chapter_id="nonexistent")
    orchestrator4 = make_orchestrator(
        chapters=[ch1, ch2, ch3], current_chapter=other_chapter
    )
    assert orchestrator4._current_chapter_index == -1


# ===== 19. _build_lookback_chapters_text 仅包含至当前章（Task 5）=====


def test_build_lookback_chapters_text() -> None:
    """lookback 文本仅包含从首章至当前章（含）。"""
    # 构造 3 章，current_chapter 为第 2 章（index=1）
    ch1 = make_chapter(
        index=0, chapter_id="ch_a", title="第一章", content="第一章正文AAA"
    )
    ch2 = make_chapter(
        index=1, chapter_id="ch_b", title="第二章", content="第二章正文BBB"
    )
    ch3 = make_chapter(
        index=2, chapter_id="ch_c", title="第三章", content="第三章正文CCC"
    )
    orchestrator = make_orchestrator(
        chapters=[ch1, ch2, ch3], current_chapter=ch2
    )

    lookback_text = orchestrator._build_lookback_chapters_text()
    # 应含第一章与第二章，不含第三章
    assert "第一章正文AAA" in lookback_text
    assert "第二章正文BBB" in lookback_text
    assert "第三章正文CCC" not in lookback_text
    # 全文缓存应含全部 3 章
    full_text = orchestrator._build_chapters_text()
    assert "第一章正文AAA" in full_text
    assert "第二章正文BBB" in full_text
    assert "第三章正文CCC" in full_text

    # current_chapter 为第 1 章（index=0）：lookback 只含第 1 章
    orchestrator2 = make_orchestrator(
        chapters=[ch1, ch2, ch3], current_chapter=ch1
    )
    lookback_text2 = orchestrator2._build_lookback_chapters_text()
    assert "第一章正文AAA" in lookback_text2
    assert "第二章正文BBB" not in lookback_text2
    assert "第三章正文CCC" not in lookback_text2

    # current_chapter 不在列表中：fallback 为全量
    other_chapter = make_chapter(index=99, chapter_id="nonexistent")
    orchestrator3 = make_orchestrator(
        chapters=[ch1, ch2, ch3], current_chapter=other_chapter
    )
    lookback_text3 = orchestrator3._build_lookback_chapters_text()
    assert "第一章正文AAA" in lookback_text3
    assert "第二章正文BBB" in lookback_text3
    assert "第三章正文CCC" in lookback_text3


# ===== 20. 深度分析宏含 chapters_text（单占位符，全量注入）=====


def test_deep_analysis_macros_include_chapters_text() -> None:
    """_run_deep_analysis_single 调用的 system prompt 含 chapters_text 文本。

    构造 3 章，current_chapter 为第 3 章。深度分析基于 lookback chapters
    （chapters[0..current_chapter_index]，含当前章），内容短小不切分，
    不切分分支发送 lookback chapters_text。
    验证深度分析阶段 chat 调用的 system prompt 含全部 3 章内容（每章 1 次）。
    """
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    ch1 = make_chapter(
        index=0, chapter_id="ch_a", title="第一章", content="AAA_FIRST"
    )
    ch2 = make_chapter(
        index=1, chapter_id="ch_b", title="第二章", content="BBB_SECOND"
    )
    ch3 = make_chapter(
        index=2, chapter_id="ch_c", title="第三章", content="CCC_THIRD"
    )
    orchestrator = make_orchestrator(
        config=config, chapters=[ch1, ch2, ch3], current_chapter=ch3
    )
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # 深度分析是第 1 个 chat 调用（索引 0）
    assert len(fake.chat_messages_history) >= 1
    da_messages = fake.chat_messages_history[0]
    da_prompt = da_messages[0]["content"]
    # 模板只有一个 {{chapters_text}} 占位符，注入 lookback chapters_text（3 章）
    # 每章内容出现 1 次
    assert "AAA_FIRST" in da_prompt
    assert "BBB_SECOND" in da_prompt
    assert "CCC_THIRD" in da_prompt
    assert "# 章节文本" in da_prompt
    # 单占位符注入，每章内容出现 1 次
    assert da_prompt.count("CCC_THIRD") == 1
    assert da_prompt.count("AAA_FIRST") == 1
    assert da_prompt.count("BBB_SECOND") == 1
    # 无残留占位符
    assert "{{chapters_text}}" not in da_prompt
    assert "{{full_chapters_text}}" not in da_prompt
    assert "{{lookback_chapters_text}}" not in da_prompt


# ===== 21. 卷大纲使用 lookback 文本（Task 5）=====


def test_volume_outline_uses_lookback_text() -> None:
    """_run_volume_outline 调用的 system prompt 含 lookback 文本，不含当前章之后章节。

    构造 3 章，current_chapter 为第 2 章（index=1），使 lookback 只含前 2 章。
    验证卷大纲阶段（第 2 个 chat 调用）的 system prompt 含前 2 章正文，
    不含第 3 章正文（lookback 截断点）。
    """
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    ch1 = make_chapter(
        index=0, chapter_id="ch_a", title="第一章", content="AAA_LOOKBACK"
    )
    ch2 = make_chapter(
        index=1, chapter_id="ch_b", title="第二章", content="BBB_LOOKBACK"
    )
    ch3 = make_chapter(
        index=2, chapter_id="ch_c", title="第三章", content="CCC_BEYOND"
    )
    orchestrator = make_orchestrator(
        config=config, chapters=[ch1, ch2, ch3], current_chapter=ch2
    )
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 逐章：细纲 + 写作（2 章）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # 卷大纲是第 2 个 chat 调用（索引 1）
    assert len(fake.chat_messages_history) >= 2
    outline_messages = fake.chat_messages_history[1]
    outline_prompt = outline_messages[0]["content"]
    # lookback 文本含第 1、2 章
    assert "AAA_LOOKBACK" in outline_prompt
    assert "BBB_LOOKBACK" in outline_prompt
    # 第 3 章在 current_chapter 之后，不应注入卷大纲
    assert "CCC_BEYOND" not in outline_prompt
    # 段落标签应为 # 续写点前文内容
    assert "# 续写点前文内容" in outline_prompt
    # 无残留占位符
    assert "{{chapters_text}}" not in outline_prompt


# ===== 11. 动态前文窗口（含本卷已生成章节）=====


def test_dynamic_lookback_includes_generated_chapters() -> None:
    """动态前文：第 2 章 verify prompt 含第 1 章本卷已生成正文。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = True
    config.enable_chapter_revise = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 第 1 章：细纲 + 写作 + 验证（通过）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="VOL_CH1_UNIQUE_MARK")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    # 第 2 章：细纲 + 写作 + 验证（通过）
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="VOL_CH2_UNIQUE_MARK")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # chat_messages_history 顺序：
    # [0]=deep_analysis, [1]=volume_outline,
    # [2]=ch1_outline, [3]=ch1_verify, [4]=ch2_outline, [5]=ch2_verify
    assert len(fake.chat_messages_history) >= 6
    ch2_verify_messages = fake.chat_messages_history[5]
    ch2_verify_prompt = ch2_verify_messages[0]["content"]
    # 第 2 章 verify 的前文应含第 1 章本卷已生成正文（动态前文）
    assert "VOL_CH1_UNIQUE_MARK" in ch2_verify_prompt
    # 标签应为"最近 10 章正文参考（含本卷已生成）"
    assert "最近 10 章正文参考（含本卷已生成）" in ch2_verify_prompt


def test_volume_writing_skips_chat_history() -> None:
    """卷模式写作阶段跳过聊天历史：前文仅由"最近 10 章正文参考"系统消息提供，不在 chat history 重复。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    # 预置 1 章，正文含唯一标记
    pre_chapter = make_chapter(
        index=0, chapter_id="pre_0",
        title="前文第1章", content="PRE_CHAPTER_UNIQUE_BODY_MARK",
    )
    orchestrator = make_orchestrator(
        config=config,
        chapters=[pre_chapter],
        current_chapter=pre_chapter,
    )
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 第 1 章：细纲 + 写作
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="VOL_CH1_UNIQUE_MARK")])
    # 第 2 章：细纲 + 写作
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="VOL_CH2_UNIQUE_MARK")])
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # last_stream_messages 为最后一次 stream 调用（第 2 章写作）的 messages
    writing_messages = fake.last_stream_messages
    assert writing_messages, "应有写作阶段 stream 调用"

    # 预置章节正文应出现在"最近 10 章正文参考"系统消息中（前文由系统消息提供）
    all_content = "\n".join(m.get("content", "") for m in writing_messages)
    assert "PRE_CHAPTER_UNIQUE_BODY_MARK" in all_content, "预置章节应在系统消息中提供前文"
    assert "最近 10 章正文参考（含本卷已生成）" in all_content, "应含系统消息标签"

    # 预置章节正文不应作为独立 user 消息出现（chat history 应为空，skip_history=True）
    user_msgs_with_pre = [
        m for m in writing_messages
        if m.get("role") == "user" and "PRE_CHAPTER_UNIQUE_BODY_MARK" in m.get("content", "")
    ]
    assert not user_msgs_with_pre, "chat history 不应包含预置章节正文（skip_history=True）"


def test_dynamic_lookback_window_size() -> None:
    """动态前文窗口恰好 10 章：15 章前文 + 生成第 6 章，lookback=chapters[10:20]。"""
    config = VolumeRunConfig(chapter_count=6)
    config.enable_outline_audit = False
    config.enable_chapter_verify = True
    config.enable_chapter_revise = False
    # 预置 15 章，current_chapter 为第 15 章（index=14）
    pre_chapters = [
        make_chapter(
            index=i, chapter_id=f"pre_{i}",
            title=f"前文第{i+1}章", content=f"PRE_MARK_{i:02d}",
        )
        for i in range(15)
    ]
    orchestrator = make_orchestrator(
        config=config, chapters=pre_chapters, current_chapter=pre_chapters[-1],
    )
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(6))
    # 6 章：每章细纲 + 写作 + 验证（通过）
    for _ in range(6):
        fake.add_chat_response(make_outline_json())
        fake.add_stream_response([StreamChunk(content=f"VOL_MARK_{_}")])
        fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # 第 6 章 verify 是最后一个 chat 调用
    ch6_verify_messages = fake.chat_messages_history[-1]
    ch6_verify_prompt = ch6_verify_messages[0]["content"]
    # 生成第 6 章时 self.chapters 长度 = 15+5=20，lookback=chapters[10:20]
    # 含索引 10-14（前文第 11-15 章）+ 索引 15-19（本卷第 1-5 章）
    assert "PRE_MARK_10" in ch6_verify_prompt
    assert "PRE_MARK_14" in ch6_verify_prompt
    assert "VOL_MARK_0" in ch6_verify_prompt
    assert "VOL_MARK_4" in ch6_verify_prompt
    # 不含索引 9（窗口外）
    assert "PRE_MARK_09" not in ch6_verify_prompt
    assert "PRE_MARK_00" not in ch6_verify_prompt


# ===== 12. 每章后暂停点（after_chapter）=====


def test_after_chapter_checkpoint_approved() -> None:
    """after_chapter 开启：每章后触发暂停点，approve 后继续下一章。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    config.checkpoints["after_chapter"] = True
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    async def run() -> None:
        checkpoint_count = 0
        checkpoint_event = asyncio.Event()

        def on_checkpoint(name, payload):
            nonlocal checkpoint_count
            if name == "after_chapter":
                checkpoint_count += 1
                checkpoint_event.set()

        orchestrator.checkpoint_reached.connect(on_checkpoint)

        task = asyncio.create_task(orchestrator._async_run())
        # 第 1 章后暂停
        await asyncio.wait_for(checkpoint_event.wait(), timeout=5.0)
        checkpoint_event.clear()
        assert checkpoint_count == 1
        orchestrator.resume({"action": "approve"})
        # 第 2 章后暂停
        await asyncio.wait_for(checkpoint_event.wait(), timeout=5.0)
        assert checkpoint_count == 2
        orchestrator.resume({"action": "approve"})
        await task

    run_async(run())

    assert len(finished_results) == 1
    assert finished_results[0].volume_artifacts is not None


def test_after_chapter_checkpoint_rejected_then_approved() -> None:
    """after_chapter reject 后重写，再次暂停 approve 通过。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    config.checkpoints["after_chapter"] = True
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 第 1 章：细纲 + 写作
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章初稿")])
    # reject 后：_run_chapter_revise(user_guidance) 短路无 LLM 调用，
    # _run_chapter_writing 流式重写（无 verify，跳过验证环节）
    fake.add_stream_response([StreamChunk(content="第一章重写稿")])
    # 第 2 章：细纲 + 写作
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    async def run() -> None:
        checkpoint_count = 0
        checkpoint_event = asyncio.Event()

        def on_checkpoint(name, payload):
            nonlocal checkpoint_count
            if name == "after_chapter":
                checkpoint_count += 1
                checkpoint_event.set()

        orchestrator.checkpoint_reached.connect(on_checkpoint)

        task = asyncio.create_task(orchestrator._async_run())
        # 第 1 章第一次暂停：reject 触发重写
        await asyncio.wait_for(checkpoint_event.wait(), timeout=5.0)
        checkpoint_event.clear()
        assert checkpoint_count == 1
        orchestrator.resume({"action": "reject", "feedback": "加强冲突"})
        # 第 1 章第二次暂停：approve 通过，进入第 2 章
        await asyncio.wait_for(checkpoint_event.wait(), timeout=5.0)
        checkpoint_event.clear()
        assert checkpoint_count == 2
        orchestrator.resume({"action": "approve"})
        # 第 2 章暂停：approve 通过
        await asyncio.wait_for(checkpoint_event.wait(), timeout=5.0)
        assert checkpoint_count == 3
        orchestrator.resume({"action": "approve"})
        await task

    run_async(run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.volume_artifacts is not None
    # 第 1 章最终正文为重写稿（reject 后重写覆盖初稿）
    assert "重写稿" in cont.content


def test_after_chapter_disabled_no_checkpoint() -> None:
    """after_chapter 未开启：逐章循环无 checkpoint_reached emit。"""
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = False
    config.enable_chapter_revise = False
    # after_chapter 默认 False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第一章正文")])
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第二章正文")])
    orchestrator._client = fake

    checkpoints: list[tuple[str, object]] = []
    orchestrator.checkpoint_reached.connect(lambda n, p: checkpoints.append((n, p)))

    run_async(orchestrator._async_run())

    # 无任何 after_chapter 检查点触发
    after_chapter_count = sum(1 for n, _ in checkpoints if n == "after_chapter")
    assert after_chapter_count == 0


# ===== 13. 文风审计维度 =====


def test_style_audit_dimension() -> None:
    """文风审计维度：DEFAULT_AUDIT_DIMENSIONS 含 style，审计模板含 style 定义。"""
    from novelforge.models.volume import DEFAULT_AUDIT_DIMENSIONS
    from novelforge.utils.paths import get_volume_prompt_path, load_text_resource

    assert "style" in DEFAULT_AUDIT_DIMENSIONS
    assert len(DEFAULT_AUDIT_DIMENSIONS) == 10

    audit_template = load_text_resource(get_volume_prompt_path("outline_audit"))
    assert "style" in audit_template
    assert "文风" in audit_template


# ===== 14. 强制修改流程：stages 捕获 =====


def test_forced_revise_flow_stages_captured() -> None:
    """强制修改流程：审计①通过后仍触发 1 轮修改，stages 含 5 阶段。

    流程：outline → draft → audit①(passed=True) → revise① → audit②
    断言：revision_rounds==1，content 为修订稿（非初稿），stages 次序与类型正确。
    """
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = True
    config.enable_chapter_revise = True
    config.max_revise_rounds_per_chapter = 1
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 第 1 章：细纲 + 写作(初稿) + 验证(通过) + 强制修改(修订指导 + 修订稿 + 验证通过)
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="初稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="修订稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    # 第 2 章：细纲 + 写作 + 验证(通过) + 强制修改(修订指导 + 修订稿 + 验证通过)
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第2章初稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="第2章修订稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    artifacts = cont.volume_artifacts
    assert artifacts is not None
    assert len(artifacts.chapter_artifacts) == 2
    ca = artifacts.chapter_artifacts[0]

    # 强制修改触发 1 轮，content 为修订稿
    assert ca.revision_rounds == 1
    assert ca.content == "修订稿"
    assert ca.final_critique is not None
    assert ca.final_critique.passed is True

    # stages 次序：outline, draft, audit①, revise①, audit②
    assert len(ca.stages) == 5
    expected_types = ["outline", "draft", "audit", "revise", "audit"]
    assert [s.stage_type for s in ca.stages] == expected_types
    # audit① round_index=1（passed=True），audit② round_index=2
    assert ca.stages[2].round_index == 1
    assert ca.stages[2].critique is not None
    assert ca.stages[2].critique.passed is True
    assert ca.stages[3].round_index == 1
    assert ca.stages[3].content == "修订稿"
    assert ca.stages[4].round_index == 2
    assert ca.stages[4].critique is not None
    assert ca.stages[4].critique.passed is True


def test_forced_revise_max_2_auto_loop() -> None:
    """max=2 时：审计①通过→强制修改→审计②失败→自动修改→审计③失败→退出。

    断言：revision_rounds==2，stages 含 2 个 revise + 3 个 audit。
    """
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = True
    config.enable_chapter_revise = True
    config.max_revise_rounds_per_chapter = 2
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 第 1 章：细纲 + 写作(初稿) + 验证(通过)
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="初稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    # 强制修改轮1：修订指导 + 修订稿 + 验证(失败)
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="修订1")])
    fake.add_chat_response(CRITIQUE_FAILED_JSON)
    # 自动修改轮2：修订指导 + 修订稿 + 验证(失败)
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="修订2")])
    fake.add_chat_response(CRITIQUE_FAILED_JSON)
    # 第 2 章：细纲 + 写作 + 验证(通过) + 强制修改(修订指导 + 修订稿 + 验证通过)
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第2章初稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="第2章修订稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    ca = cont.volume_artifacts.chapter_artifacts[0]
    assert ca.revision_rounds == 2
    assert ca.content == "修订2"
    assert ca.final_critique is not None
    assert ca.final_critique.passed is False

    # stages: outline, draft, audit①, revise①, audit②, revise②, audit③
    assert len(ca.stages) == 7
    expected_types = ["outline", "draft", "audit", "revise", "audit", "revise", "audit"]
    assert [s.stage_type for s in ca.stages] == expected_types
    # 3 个 audit 的 round_index 依次为 1, 2, 3
    audits = [s for s in ca.stages if s.stage_type == "audit"]
    assert len(audits) == 3
    assert [s.round_index for s in audits] == [1, 2, 3]
    # 2 个 revise 的 round_index 依次为 1, 2
    revises = [s for s in ca.stages if s.stage_type == "revise"]
    assert len(revises) == 2
    assert [s.round_index for s in revises] == [1, 2]


# ===== 15. 动态前文排除插入点后章节 =====


def test_dynamic_lookback_excludes_post_insertion_chapters() -> None:
    """从中间续写时，动态前文窗口排除插入点后原章节。

    构造 20 章项目从第 10 章续写（current_chapter_index=9，
    _original_chapter_count=20），生成 2 章。断言第 2 章 verify 的
    前文窗口含 ch1..ch10 + 第 1 章生成内容，不含 ch11..ch20 标记。
    """
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = True
    config.enable_chapter_revise = False
    # 预置 20 章，current_chapter 为第 10 章（index=9）
    pre_chapters = [
        make_chapter(
            index=i, chapter_id=f"ch_{i}",
            title=f"第{i + 1}章",
            content=f"PRE_MARK_{i:02d}" if i < 10 else f"POST_MARK_{i:02d}",
        )
        for i in range(20)
    ]
    orchestrator = make_orchestrator(
        config=config, chapters=pre_chapters, current_chapter=pre_chapters[9],
    )
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 2 章：每章细纲 + 写作 + 验证（通过）
    for _ in range(2):
        fake.add_chat_response(make_outline_json())
        fake.add_stream_response([StreamChunk(content=f"VOL_MARK_{_}")])
        fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # 第 2 章 verify 是最后一个 chat 调用
    ch2_verify_messages = fake.chat_messages_history[-1]
    ch2_verify_prompt = ch2_verify_messages[0]["content"]
    # 应含插入点前章节（PRE_MARK_00..PRE_MARK_09）与第 1 章生成内容（VOL_MARK_0）
    assert "PRE_MARK_09" in ch2_verify_prompt
    assert "VOL_MARK_0" in ch2_verify_prompt
    # 不应含插入点后原章节（POST_MARK_10..POST_MARK_19）
    assert "POST_MARK_10" not in ch2_verify_prompt
    assert "POST_MARK_19" not in ch2_verify_prompt


# ===== 16. after_chapter reject 的 stages 捕获 =====


def test_stages_capture_after_chapter_reject() -> None:
    """after_chapter reject 后 stages 含 reject 的 revise+audit，round_index 续接。

    流程：outline → draft → audit①(passed) → revise①(forced) → audit②(passed)
          → after_chapter reject → revise②(用户反馈) → audit③(passed) → approve
    断言：stages 含 7 阶段，reject 的 revise round_index=2（续接非重置）。
    """
    config = VolumeRunConfig(chapter_count=2)
    config.enable_outline_audit = False
    config.enable_chapter_verify = True
    config.enable_chapter_revise = True
    config.max_revise_rounds_per_chapter = 1
    config.checkpoints["after_chapter"] = True
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(DEEP_ANALYSIS_JSON)
    fake.add_chat_response(make_volume_outline_json(2))
    # 第 1 章：细纲 + 写作(初稿) + 验证(通过) + 强制修改(修订指导 + 修订稿 + 验证通过)
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="初稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="修订稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    # reject 后：_run_chapter_revise(user_guidance) 短路无 LLM 调用，
    # _run_chapter_writing 流式重写 + 验证(通过)
    fake.add_stream_response([StreamChunk(content="重写稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    # 第 2 章：细纲 + 写作 + 验证(通过) + 强制修改(修订指导 + 修订稿 + 验证通过)
    fake.add_chat_response(make_outline_json())
    fake.add_stream_response([StreamChunk(content="第2章初稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="第2章修订稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    async def run() -> None:
        checkpoint_count = 0
        checkpoint_event = asyncio.Event()

        def on_checkpoint(name, payload):
            nonlocal checkpoint_count
            if name == "after_chapter":
                checkpoint_count += 1
                checkpoint_event.set()

        orchestrator.checkpoint_reached.connect(on_checkpoint)

        task = asyncio.create_task(orchestrator._async_run())
        # 第 1 章第 1 次暂停：reject
        await asyncio.wait_for(checkpoint_event.wait(), timeout=5.0)
        checkpoint_event.clear()
        assert checkpoint_count == 1
        orchestrator.resume({"action": "reject", "feedback": "加强冲突"})
        # 第 1 章第 2 次暂停：approve
        await asyncio.wait_for(checkpoint_event.wait(), timeout=5.0)
        checkpoint_event.clear()
        assert checkpoint_count == 2
        orchestrator.resume({"action": "approve"})
        # 第 2 章暂停：approve
        await asyncio.wait_for(checkpoint_event.wait(), timeout=5.0)
        checkpoint_event.clear()
        assert checkpoint_count == 3
        orchestrator.resume({"action": "approve"})
        await task

    run_async(run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    ca = cont.volume_artifacts.chapter_artifacts[0]
    # reject 后重写，最终 content 为重写稿
    assert ca.content == "重写稿"
    assert ca.revision_rounds == 2  # 强制1 + reject1

    # stages: outline, draft, audit①, revise①, audit②, revise②, audit③
    assert len(ca.stages) == 7
    expected_types = ["outline", "draft", "audit", "revise", "audit", "revise", "audit"]
    assert [s.stage_type for s in ca.stages] == expected_types
    # reject 的 revise round_index=2（续接强制修改的 round_index=1）
    reject_revise = ca.stages[5]
    assert reject_revise.stage_type == "revise"
    assert reject_revise.round_index == 2
    assert reject_revise.content == "重写稿"
    # reject 后的 audit round_index=3
    reject_audit = ca.stages[6]
    assert reject_audit.stage_type == "audit"
    assert reject_audit.round_index == 3
