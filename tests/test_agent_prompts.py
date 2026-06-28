"""Agent 阶段提示词模板测试。

验证：
- 4 个阶段模板文件存在且可读
- 各模板包含预期占位符
- 宏替换（str.replace）正确填充所有占位符
- 模板包含 JSON 输出约束指导
- E2E：合成 preset + 合成章节，mock LLM 跑全流程
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from novelforge.models import (
    AgentRunConfig,
    Chapter,
    ContextEntry,
    Continuation,
    Prompt,
    PromptOrderEntry,
    PromptOrderGroup,
    WritingPreset,
)
from novelforge.services.agent_orchestrator import AgentOrchestrator
from novelforge.services.llm_client import StreamChunk
from novelforge.utils.paths import get_agent_prompt_path, load_text_resource


# ===== 模板存在性 =====


def test_phase_analysis_template_exists() -> None:
    """分析阶段模板文件存在且可读。"""
    path = get_agent_prompt_path("analysis")
    assert path.exists(), f"模板文件不存在: {path}"
    content = load_text_resource(path)
    assert content, "模板内容为空"
    assert "分析" in content


def test_phase_outline_template_exists() -> None:
    """大纲阶段模板文件存在且可读。"""
    path = get_agent_prompt_path("outline")
    assert path.exists(), f"模板文件不存在: {path}"
    content = load_text_resource(path)
    assert content, "模板内容为空"
    assert "大纲" in content


def test_phase_verify_template_exists() -> None:
    """验证阶段模板文件存在且可读。"""
    path = get_agent_prompt_path("verify")
    assert path.exists(), f"模板文件不存在: {path}"
    content = load_text_resource(path)
    assert content, "模板内容为空"
    assert "验证" in content or "评审" in content


def test_phase_revise_template_exists() -> None:
    """修订阶段模板文件存在且可读。"""
    path = get_agent_prompt_path("revise")
    assert path.exists(), f"模板文件不存在: {path}"
    content = load_text_resource(path)
    assert content, "模板内容为空"
    assert "修订" in content


# ===== 占位符存在性 =====


def test_phase_analysis_placeholders() -> None:
    """分析模板包含 7 个预期占位符。"""
    content = load_text_resource(get_agent_prompt_path("analysis"))
    expected = [
        "{{title}}",
        "{{author}}",
        "{{protagonist}}",
        "{{synopsis}}",
        "{{world_setting}}",
        "{{writing_style}}",
        "{{chapters_text}}",
    ]
    for ph in expected:
        assert ph in content, f"分析模板缺少占位符: {ph}"


def test_phase_outline_placeholders() -> None:
    """大纲模板包含 3 个预期占位符。"""
    content = load_text_resource(get_agent_prompt_path("outline"))
    expected = ["{{snapshot}}", "{{chapters_text}}", "{{user_input}}"]
    for ph in expected:
        assert ph in content, f"大纲模板缺少占位符: {ph}"


def test_phase_verify_placeholders() -> None:
    """验证模板包含 4 个预期占位符。"""
    content = load_text_resource(get_agent_prompt_path("verify"))
    expected = ["{{snapshot}}", "{{outline}}", "{{written_text}}", "{{previous_chapters_text}}"]
    for ph in expected:
        assert ph in content, f"验证模板缺少占位符: {ph}"


def test_phase_revise_placeholders() -> None:
    """修订模板包含 5 个预期占位符。"""
    content = load_text_resource(get_agent_prompt_path("revise"))
    expected = [
        "{{written_text}}",
        "{{critique}}",
        "{{outline}}",
        "{{previous_chapters_text}}",
        "{{pacing_speed}}",
    ]
    for ph in expected:
        assert ph in content, f"修订模板缺少占位符: {ph}"


# ===== 宏替换正确性 =====


def _apply_macros(template: str, macros: dict[str, str]) -> str:
    """镜像 AgentOrchestrator._apply_macros 的 str.replace 逻辑。"""
    result = template
    for placeholder, value in macros.items():
        result = result.replace(placeholder, value)
    return result


def _assert_no_placeholders(text: str) -> None:
    """断言文本中无残留 {{...}} 占位符。"""
    residue = re.findall(r"\{\{[^}]+\}\}", text)
    assert not residue, f"存在未替换的占位符: {residue}"


def test_macro_replacement_analysis() -> None:
    """用合成数据替换分析模板占位符，验证无残留。"""
    template = load_text_resource(get_agent_prompt_path("analysis"))
    macros = {
        "{{title}}": "星辰大海",
        "{{author}}": "张三",
        "{{protagonist}}": "李四",
        "{{synopsis}}": "一段星际冒险的故事",
        "{{world_setting}}": "公元 3024 年的银河帝国",
        "{{writing_style}}": "冷峻、短句、悬念密集",
        "{{chapters_text}}": "## 第一章\n\n飞船起飞了。",
    }
    result = _apply_macros(template, macros)
    _assert_no_placeholders(result)
    # 验证中文内容正确替换
    assert "星辰大海" in result
    assert "张三" in result
    assert "李四" in result
    assert "公元 3024 年的银河帝国" in result
    assert "飞船起飞了" in result


def test_macro_replacement_outline() -> None:
    """用合成数据替换大纲模板占位符，验证无残留。"""
    template = load_text_resource(get_agent_prompt_path("outline"))
    macros = {
        "{{snapshot}}": '{"tone": "紧张"}',
        "{{chapters_text}}": "## 第一章\n\n前文内容。",
        "{{user_input}}": "请续写主角与反派的对决",
    }
    result = _apply_macros(template, macros)
    _assert_no_placeholders(result)
    assert '"tone": "紧张"' in result
    assert "前文内容" in result
    assert "请续写主角与反派的对决" in result


def test_macro_replacement_verify() -> None:
    """用合成数据替换验证模板占位符，验证无残留。"""
    template = load_text_resource(get_agent_prompt_path("verify"))
    macros = {
        "{{snapshot}}": '{"tone": "紧张"}',
        "{{outline}}": '{"continuation_goals": "升级冲突"}',
        "{{written_text}}": "主角拔剑而出。",
        "{{previous_chapters_text}}": "前一章正文内容。",
    }
    result = _apply_macros(template, macros)
    _assert_no_placeholders(result)
    assert '"continuation_goals": "升级冲突"' in result
    assert "主角拔剑而出" in result
    assert "前一章正文内容" in result


def test_macro_replacement_revise() -> None:
    """用合成数据替换修订模板占位符，验证无残留。"""
    template = load_text_resource(get_agent_prompt_path("revise"))
    macros = {
        "{{written_text}}": "主角拔剑而出。",
        "{{critique}}": '{"passed": false}',
        "{{outline}}": '{"continuation_goals": "升级冲突"}',
        "{{previous_chapters_text}}": "## 第一章\n\n前文参考。",
        "{{pacing_speed}}": "medium",
    }
    result = _apply_macros(template, macros)
    _assert_no_placeholders(result)
    assert "主角拔剑而出" in result
    assert '"passed": false' in result
    assert "前文参考" in result
    assert "medium" in result


def test_macro_replacement_empty_values() -> None:
    """空字符串替换不报错，且无残留占位符。"""
    for phase in ("analysis", "outline", "verify", "revise"):
        template = load_text_resource(get_agent_prompt_path(phase))
        # 找出模板中所有占位符，全部替换为空字符串
        placeholders = re.findall(r"\{\{[^}]+\}\}", template)
        macros = {ph: "" for ph in placeholders}
        result = _apply_macros(template, macros)
        _assert_no_placeholders(result)


def test_templates_contain_json_constraint() -> None:
    """各模板含 JSON 输出约束文字。"""
    for phase in ("analysis", "outline", "verify", "revise"):
        content = load_text_resource(get_agent_prompt_path(phase))
        assert "JSON" in content, f"{phase} 模板缺少 JSON 约束文字"
        # 至少包含一种约束指导
        assert (
            "严格" in content
            or "代码块" in content
            or "markdown" in content.lower()
        ), f"{phase} 模板缺少 JSON 输出格式指导"


def test_get_agent_prompt_path() -> None:
    """路径函数返回正确路径。"""
    for phase in ("analysis", "outline", "verify", "revise"):
        path = get_agent_prompt_path(phase)
        assert path.name == f"phase_{phase}.txt"
        assert path.parent.name == "agent"
        assert path.exists()


# ===== E2E 测试 =====


# --- 合成 JSON fixtures ---

SNAPSHOT_JSON = json.dumps(
    {
        "structure_position": "发展",
        "tone": "紧张悬疑",
        "core_conflict_status": "白热化",
        "stakes": "主角的性命",
        "active_characters": [
            {"name": "林晨", "status": "受伤", "motivation": "复仇"}
        ],
        "plot_threads": [
            {"name": "主线", "status": "推进中", "priority": "high"}
        ],
        "unresolved_promises": [
            {"description": "归还宝物", "setup_chapter": "第一章"}
        ],
        "foreshadowing_tracker": [
            {"item": "墙上的剑", "status": "planted"}
        ],
        "world_state": "战乱年代",
        "style_profile": "冷峻、短句",
    },
    ensure_ascii=False,
)

OUTLINE_JSON = json.dumps(
    {
        "continuation_goals": "推动主角与反派正面冲突",
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
                "exit_hook": "远方传来号角",
            },
        ],
    },
    ensure_ascii=False,
)

CRITIQUE_PASSED_JSON = json.dumps(
    {"summary": "续写质量良好，通过验证", "issues": [], "passed": True},
    ensure_ascii=False,
)


class _FakeLLMClient:
    """模拟 LLM 客户端，按顺序返回预设响应。"""

    def __init__(self) -> None:
        self.chat_responses: list[str] = []
        self.stream_responses: list[list[StreamChunk]] = []
        self._chat_idx = 0
        self._stream_idx = 0

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
        if self._stream_idx < len(self.stream_responses):
            chunks = self.stream_responses[self._stream_idx]
        else:
            chunks = []
        self._stream_idx += 1
        for chunk in chunks:
            yield chunk


def _build_synthetic_preset() -> WritingPreset:
    """构建合成 WritingPreset（含 worldInfoBefore marker）。"""
    preset = WritingPreset(id="test_e2e", name="测试预设")
    preset.prompts = [
        Prompt(
            identifier="worldInfoBefore",
            name="World Info Before",
            marker="worldInfoBefore",
            role="system",
            content="",
            position="start",
        ),
        Prompt(
            identifier="chatHistory",
            name="Chat History",
            marker="chatHistory",
            role="system",
            content="",
            position="start",
        ),
        Prompt(
            identifier="main",
            name="主提示",
            role="system",
            content="你是小说续写助手，请根据前文续写。",
            enabled=True,
            system_prompt=True,
            position="start",
        ),
    ]
    preset.prompt_order = [
        PromptOrderGroup(
            character_id=100000,
            order=[
                PromptOrderEntry(identifier="worldInfoBefore", enabled=True),
                PromptOrderEntry(identifier="main", enabled=True),
                PromptOrderEntry(identifier="chatHistory", enabled=True),
            ],
        )
    ]
    return preset


def _build_synthetic_chapters() -> tuple[list[Chapter], Chapter]:
    """构建合成章节（2 章），返回 (chapters, current_chapter)。"""
    ch1 = Chapter(
        id="ch1",
        project_id="p1",
        index=0,
        title="第一章 风起",
        content="林晨站在城墙上，望着远方的烽火。战乱已持续三年，"
        "他手中的剑早已卷刃。一阵寒风吹过，带来血腥的气息。",
        word_count=50,
    )
    ch2 = Chapter(
        id="ch2",
        project_id="p1",
        index=1,
        title="第二章 剑鸣",
        content="剑在鞘中轻鸣，仿佛感应到了主人的杀意。林晨握紧剑柄，"
        "他知道，今夜注定有人要倒下。",
        word_count=40,
    )
    return [ch1, ch2], ch2


def _build_synthetic_context_entries() -> list[ContextEntry]:
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
            content="宝物：传说中的寒霜剑，能斩断一切。",
            position="before",
            role="system",
            order=1,
        ),
    ]


def test_agent_e2e_full_flow() -> None:
    """E2E: 合成 preset + 合成章节，mock LLM 跑全流程。

    验证：
    - AgentOrchestrator 能用合成 WritingPreset 运行
    - 各阶段产物正确生成
    - finished 信号发射含 agent_artifacts 的 Continuation
    - content 非空
    """
    # 1. 构建合成 WritingPreset（含 worldInfoBefore marker）
    preset = _build_synthetic_preset()

    # 2. 构建合成章节
    chapters, current_chapter = _build_synthetic_chapters()

    # 3. 构建合成 context_entries
    context_entries = _build_synthetic_context_entries()

    # 4. 构建 AgentRunConfig（全阶段开启，无暂停点）
    config = AgentRunConfig()

    # 5. 构建 AgentOrchestrator
    orchestrator = AgentOrchestrator(
        base_url="http://test",
        api_key="test-key",
        model="test-model",
        parameters={"temperature": 0.8, "max_tokens": 2000},
        preset=preset,
        chapters=chapters,
        current_chapter=current_chapter,
        context_entries=context_entries,
        config=config,
        user_input="请续写林晨与反派的决战",
        project_id="p1",
        chapter_id="ch2",
    )

    # 6. mock LLMClient（chat_completion 返回预设 JSON，stream 返回 chunk）
    fake = _FakeLLMClient()
    fake.add_chat_response(SNAPSHOT_JSON)  # analysis
    fake.add_chat_response(OUTLINE_JSON)  # outline
    fake.add_stream_response(
        [StreamChunk(content="林晨拔剑出鞘，寒光划破夜色。")]
    )  # writing
    fake.add_chat_response(CRITIQUE_PASSED_JSON)  # verify
    orchestrator._client = fake

    # 7. 运行 _async_run
    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    asyncio.run(orchestrator._async_run())

    # 8. 断言 Continuation 含 agent_artifacts，content 非空
    assert len(finished_results) == 1
    cont = finished_results[0]

    # content 非空
    assert cont.content, "续写内容为空"
    assert cont.content == "林晨拔剑出鞘，寒光划破夜色。"

    # created_by 标记为 agent
    assert cont.created_by == "agent"

    # agent_artifacts 存在且各阶段产物正确
    assert cont.agent_artifacts is not None
    artifacts = cont.agent_artifacts
    assert artifacts.snapshot is not None
    assert artifacts.snapshot.tone == "紧张悬疑"
    assert artifacts.snapshot.stakes == "主角的性命"
    assert artifacts.outline is not None
    assert len(artifacts.outline.scenes) == 2
    assert artifacts.outline.scenes[0].purpose == "对峙"
    assert artifacts.critique is not None
    assert artifacts.critique.passed is True
    assert artifacts.revision_rounds == 0  # critique 通过，无需修订

    # prompt_snapshot 非空（写作阶段 messages）
    assert cont.prompt_snapshot, "prompt_snapshot 为空"


def test_agent_e2e_minimal_phases() -> None:
    """E2E: 仅开启 writing 阶段，验证最小组合也能运行。"""
    preset = _build_synthetic_preset()
    chapters, current_chapter = _build_synthetic_chapters()
    context_entries = _build_synthetic_context_entries()

    config = AgentRunConfig()
    config.phases["analysis"] = False
    config.phases["outline"] = False
    config.phases["verify"] = False
    config.phases["revise"] = False

    orchestrator = AgentOrchestrator(
        base_url="http://test",
        api_key="test-key",
        model="test-model",
        parameters={"temperature": 0.8, "max_tokens": 2000},
        preset=preset,
        chapters=chapters,
        current_chapter=current_chapter,
        context_entries=context_entries,
        config=config,
        user_input="",
        project_id="p1",
        chapter_id="ch2",
    )

    fake = _FakeLLMClient()
    fake.add_stream_response([StreamChunk(content="最小流程续写内容。")])
    orchestrator._client = fake

    finished_results: list[Continuation] = []
    orchestrator.finished.connect(lambda cont: finished_results.append(cont))

    asyncio.run(orchestrator._async_run())

    assert len(finished_results) == 1
    cont = finished_results[0]
    assert cont.content == "最小流程续写内容。"
    assert cont.created_by == "agent"
    assert cont.agent_artifacts is not None
    # 仅 writing 阶段，其他产物为 None
    assert cont.agent_artifacts.snapshot is None
    assert cont.agent_artifacts.outline is None
    assert cont.agent_artifacts.critique is None
