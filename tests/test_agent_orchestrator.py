"""AgentOrchestrator 测试。

覆盖 12 个用例：
1. 全流程运行（所有阶段开启，无暂停点）
2. 优雅降级：关闭 analysis
3. 优雅降级：关闭 outline
4. 优雅降级：关闭 verify
5. 修订循环：critique.passed=False，max_revise_rounds=1
6. 修订循环上限：critique 始终不通过
7. 暂停点 after_outline：resume 传回编辑后大纲
8. 暂停点停止：暂停期间调 stop()
9. JSON 解析失败重试
10. JSON 解析彻底失败（降级）
11. worldInfoBefore marker 存在：大纲通过 ContextEntry 注入
12. worldInfoBefore marker 不存在：fallback 直接 prepend system 消息

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
    AgentRunConfig,
    Chapter,
    ContextEntry,
    Continuation,
    Outline,
    Prompt,
    PromptOrderEntry,
    PromptOrderGroup,
    Scene,
    StorySnapshot,
    WritingPreset,
)
from novelforge.services.agent_orchestrator import AgentOrchestrator
from novelforge.services.llm_client import StreamChunk


# ===== JSON fixtures =====

SNAPSHOT_JSON = json.dumps(
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
        "foreshadowing_tracker": [{"item": "宝物", "status": "planted"}],
        "world_state": "战乱",
        "style_profile": "冷峻",
    },
    ensure_ascii=False,
)

OUTLINE_JSON = json.dumps(
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
    config: AgentRunConfig | None = None,
    context_entries: list[ContextEntry] | None = None,
    preset: WritingPreset | None = None,
    chapters: list[Chapter] | None = None,
    current_chapter: Chapter | None = None,
    user_input: str = "",
    with_marker: bool = True,
) -> AgentOrchestrator:
    """构建测试 AgentOrchestrator。"""
    if config is None:
        config = AgentRunConfig()
    if context_entries is None:
        context_entries = []
    if preset is None:
        preset = make_preset(with_marker=with_marker)
    if chapters is None:
        current_chapter = current_chapter or make_chapter()
        chapters = [current_chapter]
    elif current_chapter is None:
        current_chapter = chapters[0]

    return AgentOrchestrator(
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


# ===== 1. 全流程运行 =====


def test_full_flow() -> None:
    """全流程运行（所有阶段开启，无暂停点）。"""
    orchestrator = make_orchestrator()
    fake = FakeLLMClient()
    fake.add_chat_response(SNAPSHOT_JSON)
    fake.add_chat_response(OUTLINE_JSON)
    fake.add_stream_response([StreamChunk(content="续写正文内容")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.content == "续写正文内容"
    assert cont.created_by == "agent"
    assert cont.agent_artifacts is not None
    assert cont.agent_artifacts.snapshot is not None
    assert cont.agent_artifacts.outline is not None
    assert cont.agent_artifacts.critique is not None
    assert cont.agent_artifacts.critique.passed is True
    assert cont.agent_artifacts.revision_rounds == 0


# ===== 2. 优雅降级：关闭 analysis =====


def test_degradation_analysis_off() -> None:
    """关闭 analysis，outline 阶段基于 context_entries 运行。"""
    config = AgentRunConfig()
    config.phases["analysis"] = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(OUTLINE_JSON)
    fake.add_stream_response([StreamChunk(content="续写正文")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.agent_artifacts.snapshot is None
    assert cont.agent_artifacts.outline is not None
    assert cont.content == "续写正文"


# ===== 3. 优雅降级：关闭 outline =====


def test_degradation_outline_off() -> None:
    """关闭 outline，writing 直接基于 context_entries。"""
    config = AgentRunConfig()
    config.phases["outline"] = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(SNAPSHOT_JSON)
    fake.add_stream_response([StreamChunk(content="无大纲续写")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.agent_artifacts.outline is None
    assert cont.content == "无大纲续写"


# ===== 4. 优雅降级：关闭 verify =====


def test_degradation_verify_off() -> None:
    """关闭 verify，写作完直接出稿。"""
    config = AgentRunConfig()
    config.phases["verify"] = False
    config.phases["revise"] = False
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(SNAPSHOT_JSON)
    fake.add_chat_response(OUTLINE_JSON)
    fake.add_stream_response([StreamChunk(content="直接出稿")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.agent_artifacts.critique is None
    assert cont.content == "直接出稿"


# ===== 5. 修订循环：critique.passed=False，max_revise_rounds=1 =====


def test_revise_loop_one_round() -> None:
    """critique 不通过，修订一轮后通过。"""
    config = AgentRunConfig()
    config.max_revise_rounds = 1
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(SNAPSHOT_JSON)
    fake.add_chat_response(OUTLINE_JSON)
    fake.add_stream_response([StreamChunk(content="初稿")])
    fake.add_chat_response(CRITIQUE_FAILED_JSON)
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="修订稿")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.agent_artifacts.revision_rounds == 1
    assert cont.content == "修订稿"
    assert cont.agent_artifacts.final_critique is not None
    assert cont.agent_artifacts.final_critique.passed is True


# ===== 6. 修订循环上限：critique 始终不通过 =====


def test_revise_loop_max_rounds() -> None:
    """critique 始终不通过，rounds 不超过 max。"""
    config = AgentRunConfig()
    config.max_revise_rounds = 2
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(SNAPSHOT_JSON)
    fake.add_chat_response(OUTLINE_JSON)
    # 初稿
    fake.add_stream_response([StreamChunk(content="初稿")])
    fake.add_chat_response(CRITIQUE_FAILED_JSON)
    # 第1轮修订
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="修订1")])
    fake.add_chat_response(CRITIQUE_FAILED_JSON)
    # 第2轮修订
    fake.add_chat_response(REVISE_JSON)
    fake.add_stream_response([StreamChunk(content="修订2")])
    fake.add_chat_response(CRITIQUE_FAILED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.agent_artifacts.revision_rounds == 2
    assert cont.content == "修订2"
    assert cont.agent_artifacts.final_critique is not None
    assert cont.agent_artifacts.final_critique.passed is False


# ===== 7. 暂停点 after_outline：resume 传回编辑后大纲 =====


def test_checkpoint_after_outline_resume() -> None:
    """暂停点 after_outline：emit checkpoint_reached，resume 传回编辑后大纲。"""
    config = AgentRunConfig()
    config.checkpoints["after_outline"] = True
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(SNAPSHOT_JSON)
    fake.add_chat_response(OUTLINE_JSON)
    fake.add_stream_response([StreamChunk(content="使用编辑后大纲续写")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
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

        edited = Outline(continuation_goals="编辑后的目标")
        orchestrator.resume(edited)

        await task

    run_async(run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.agent_artifacts.outline is not None
    assert cont.agent_artifacts.outline.continuation_goals == "编辑后的目标"


# ===== 8. 暂停点停止：暂停期间调 stop() =====


def test_checkpoint_stop() -> None:
    """暂停期间调 stop()，验证任务取消。"""
    config = AgentRunConfig()
    config.checkpoints["after_outline"] = True
    orchestrator = make_orchestrator(config=config)
    fake = FakeLLMClient()
    fake.add_chat_response(SNAPSHOT_JSON)
    fake.add_chat_response(OUTLINE_JSON)
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


# ===== 9. JSON 解析失败重试 =====


def test_json_parse_retry_success() -> None:
    """第一次返回乱码，第二次返回正确 JSON，验证重试成功。"""
    orchestrator = make_orchestrator()
    fake = FakeLLMClient()
    # analysis: 第一次乱码，第二次正确
    fake.add_chat_response("这不是JSON")
    fake.add_chat_response(SNAPSHOT_JSON)
    # outline
    fake.add_chat_response(OUTLINE_JSON)
    fake.add_stream_response([StreamChunk(content="续写正文")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.agent_artifacts.snapshot is not None
    assert cont.agent_artifacts.snapshot.tone == "紧张"


# ===== 10. JSON 解析彻底失败（降级）=====


def test_json_parse_total_failure_degradation() -> None:
    """两次都失败，验证该阶段跳过（降级）。"""
    orchestrator = make_orchestrator()
    fake = FakeLLMClient()
    # analysis: 两次都乱码
    fake.add_chat_response("乱码1")
    fake.add_chat_response("乱码2")
    # outline: 正常
    fake.add_chat_response(OUTLINE_JSON)
    fake.add_stream_response([StreamChunk(content="降级续写")])
    fake.add_chat_response(CRITIQUE_PASSED_JSON)
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    run_async(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.agent_artifacts.snapshot is None
    assert cont.agent_artifacts.outline is not None
    assert cont.content == "降级续写"


# ===== 11. worldInfoBefore marker 存在 =====


def test_world_info_before_marker_exists() -> None:
    """marker 存在：大纲通过 ContextEntry 注入。"""
    config = AgentRunConfig()
    config.phases["verify"] = False
    config.phases["revise"] = False
    orchestrator = make_orchestrator(config=config, with_marker=True)
    fake = FakeLLMClient()
    fake.add_chat_response(SNAPSHOT_JSON)
    fake.add_chat_response(OUTLINE_JSON)
    fake.add_stream_response([StreamChunk(content="marker续写")])
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # 验证大纲 Markdown 出现在 messages 中（通过 worldInfoBefore marker 注入）
    messages = fake.last_stream_messages
    found = any("# 续写大纲" in m.get("content", "") for m in messages)
    assert found, "大纲 Markdown 应通过 worldInfoBefore marker 注入到 messages"


# ===== 12. worldInfoBefore marker 不存在 =====


def test_world_info_before_marker_absent() -> None:
    """marker 不存在：fallback 直接 prepend system 消息。"""
    config = AgentRunConfig()
    config.phases["verify"] = False
    config.phases["revise"] = False
    orchestrator = make_orchestrator(config=config, with_marker=False)
    fake = FakeLLMClient()
    fake.add_chat_response(SNAPSHOT_JSON)
    fake.add_chat_response(OUTLINE_JSON)
    fake.add_stream_response([StreamChunk(content="fallback续写")])
    orchestrator._client = fake

    run_async(orchestrator._async_run())

    # 验证第一条消息是 system 消息且包含大纲 Markdown（fallback prepend）
    messages = fake.last_stream_messages
    assert len(messages) > 0
    assert messages[0]["role"] == "system"
    assert "# 续写大纲" in messages[0]["content"]
