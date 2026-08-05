"""功能 2「同步注入回溯上下文」测试脚本。

验证内容：
1. ``PromptAssembler._build_lookback_context_message`` 各种边界与格式
2. ``PromptAssembler.assemble`` 注入回溯上下文 system 消息的位置
3. 回溯上下文与 worldInfoBefore/After、chatHistory 的并存关系

运行方式：
    python -m pytest tests/test_lookback_context_injection.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from novelforge.core.macros import MacroEngine
from novelforge.core.prompt_assembler import (
    INJECTION_RELATIVE,
    PromptAssembler,
)
from novelforge.core.token_counter import TokenCounter
from novelforge.models import (
    GLOBAL_CHARACTER_ID,
    Chapter,
    ContextEntry,
    NovelProfile,
    Prompt,
    PromptOrderEntry,
    PromptOrderGroup,
    WritingPreset,
)


# ===== 测试工具函数 =====


def make_preset_with_markers() -> WritingPreset:
    """构建含 main/worldInfoBefore/chatHistory/worldInfoAfter 的预设。"""
    return WritingPreset(
        id="test_preset",
        name="测试预设",
        prompts=[
            Prompt(
                identifier="main",
                name="主提示",
                role="system",
                content="你是一位小说续写助手。",
                system_prompt=True,
                marker=None,
                position="start",
                injection_position=INJECTION_RELATIVE,
            ),
            Prompt(
                identifier="worldInfoBefore",
                name="世界书（前）",
                role="system",
                marker="worldInfoBefore",
                position="start",
                injection_position=INJECTION_RELATIVE,
            ),
            Prompt(
                identifier="chatHistory",
                name="章节历史",
                role="system",
                marker="chatHistory",
                position="start",
                injection_position=INJECTION_RELATIVE,
            ),
            Prompt(
                identifier="worldInfoAfter",
                name="世界书（后）",
                role="system",
                marker="worldInfoAfter",
                position="end",
                injection_position=INJECTION_RELATIVE,
            ),
        ],
        prompt_order=[
            PromptOrderGroup(
                character_id=GLOBAL_CHARACTER_ID,
                order=[
                    PromptOrderEntry(identifier="main", enabled=True),
                    PromptOrderEntry(identifier="worldInfoBefore", enabled=True),
                    PromptOrderEntry(identifier="chatHistory", enabled=True),
                    PromptOrderEntry(identifier="worldInfoAfter", enabled=True),
                ],
            )
        ],
        generation_params={
            "temperature": 0.8,
            "max_tokens": 2000,
            "max_context": 32000,
            "top_p": 0.95,
        },
    )


def make_preset_without_worldinfobefore() -> WritingPreset:
    """构建不含 worldInfoBefore marker 的预设（回溯上下文应注入 chatHistory 前）。"""
    return WritingPreset(
        id="test_preset_no_wib",
        name="测试预设（无 worldInfoBefore）",
        prompts=[
            Prompt(
                identifier="main",
                name="主提示",
                role="system",
                content="你是一位小说续写助手。",
                system_prompt=True,
                marker=None,
                position="start",
                injection_position=INJECTION_RELATIVE,
            ),
            Prompt(
                identifier="chatHistory",
                name="章节历史",
                role="system",
                marker="chatHistory",
                position="start",
                injection_position=INJECTION_RELATIVE,
            ),
        ],
        prompt_order=[
            PromptOrderGroup(
                character_id=GLOBAL_CHARACTER_ID,
                order=[
                    PromptOrderEntry(identifier="main", enabled=True),
                    PromptOrderEntry(identifier="chatHistory", enabled=True),
                ],
            )
        ],
        generation_params={
            "temperature": 0.8,
            "max_tokens": 2000,
            "max_context": 32000,
        },
    )


def make_chapters(count: int = 5) -> list[Chapter]:
    """构建测试章节列表。"""
    chapters: list[Chapter] = []
    for i in range(count):
        chapters.append(
            Chapter(
                id=f"ch_{i:02d}",
                project_id="test_project",
                index=i,
                title=f"第{i + 1}章 测试章节",
                content=f"这是第{i + 1}章的正文内容。",
            )
        )
    return chapters


def make_lookback_entries() -> list[ContextEntry]:
    """构建回溯章节的上下文条目（before + after 各 1 条）。"""
    return [
        ContextEntry(
            uid="lb_before_1",
            category="characters",
            key=["主角"],
            content="主角张三，性格坚毅。",
            order=100,
            position="before",
        ),
        ContextEntry(
            uid="lb_after_1",
            category="plot_state",
            key=["剧情"],
            content="当前剧情：寻找宝物。",
            order=100,
            position="after",
        ),
    ]


def make_novel_profile() -> NovelProfile:
    """构建测试小说档案。"""
    return NovelProfile(
        title="测试小说",
        author="测试作者",
        protagonist="张三",
        synopsis="武侠冒险故事。",
        world_setting="古代武侠世界。",
        writing_style="古风武侠",
    )


def make_assembler() -> PromptAssembler:
    """构建 PromptAssembler 实例。"""
    return PromptAssembler(TokenCounter(), MacroEngine())


def find_lookback_message(messages: list[dict]) -> dict | None:
    """从 messages 中查找回溯上下文 system 消息。"""
    for m in messages:
        if m.get("role") == "system" and "前章上下文参考" in m.get("content", ""):
            return m
    return None


# ===== _build_lookback_context_message 单元测试 =====


class TestBuildLookbackContextMessage:
    """``_build_lookback_context_message`` 方法行为。"""

    __test__ = True

    def setup_method(self) -> None:
        self.assembler = make_assembler()

    def test_none_entries_returns_none(self) -> None:
        """entries=None 返回 None。"""
        assert self.assembler._build_lookback_context_message(None, 99) is None

    def test_empty_entries_returns_none(self) -> None:
        """空列表返回 None。"""
        assert self.assembler._build_lookback_context_message([], 99) is None

    def test_all_disabled_returns_none(self) -> None:
        """全部 enabled=False 返回 None。"""
        entries = [
            ContextEntry(uid="ctx_1", content="测试", enabled=False, position="before"),
            ContextEntry(uid="ctx_2", content="测试2", enabled=False, position="after"),
        ]
        assert self.assembler._build_lookback_context_message(entries, 99) is None

    def test_all_at_depth_returns_none(self) -> None:
        """全是 at_depth 位置返回 None（不纳入此消息）。"""
        entries = [
            ContextEntry(uid="ctx_1", content="测试", position="at_depth", depth=2),
        ]
        assert self.assembler._build_lookback_context_message(entries, 99) is None

    def test_empty_content_returns_none(self) -> None:
        """content 为空字符串的条目被过滤。"""
        entries = [
            ContextEntry(uid="ctx_1", content="", position="before"),
        ]
        assert self.assembler._build_lookback_context_message(entries, 99) is None

    def test_basic_message_structure(self) -> None:
        """基本消息结构：role=system + 标题行 + 分组 + 条目。"""
        entries = make_lookback_entries()
        msg = self.assembler._build_lookback_context_message(entries, 95)
        assert msg is not None
        assert msg["role"] == "system"
        content = msg["content"]
        # 标题含来源章节 index
        assert "# 前章上下文参考（第95章提取）" in content
        # 按 category 分组（中文标签）
        assert "## 人物" in content
        assert "## 剧情状态" in content
        # 条目内容
        assert "主角张三" in content
        assert "寻找宝物" in content

    def test_source_index_none_uses_default_label(self) -> None:
        """source_index=None 时标题用「前文」。"""
        entries = make_lookback_entries()
        msg = self.assembler._build_lookback_context_message(entries, None)
        assert msg is not None
        assert "# 前章上下文参考（前文提取）" in msg["content"]

    def test_only_before_entries(self) -> None:
        """仅 before 条目也能构建消息。"""
        entries = [
            ContextEntry(
                uid="lb_before_1",
                category="characters",
                key=["主角"],
                content="主角张三。",
                order=100,
                position="before",
            ),
        ]
        msg = self.assembler._build_lookback_context_message(entries, 90)
        assert msg is not None
        assert "## 人物" in msg["content"]

    def test_order_sorting_within_category(self) -> None:
        """同 category 内按 order 升序排列。"""
        entries = [
            ContextEntry(
                uid="ctx_b",
                category="characters",
                content="B条目",
                order=200,
                position="before",
            ),
            ContextEntry(
                uid="ctx_a",
                category="characters",
                content="A条目",
                order=100,
                position="before",
            ),
        ]
        msg = self.assembler._build_lookback_context_message(entries, 90)
        assert msg is not None
        content = msg["content"]
        # A 条目（order=100）应在 B 条目（order=200）之前
        assert content.index("A条目") < content.index("B条目")

    def test_at_depth_filtered_out(self) -> None:
        """at_depth 条目被过滤，不进入回溯消息。"""
        entries = [
            ContextEntry(
                uid="ctx_before",
                category="characters",
                content="前文条目",
                position="before",
            ),
            ContextEntry(
                uid="ctx_depth",
                category="events",
                content="深度条目",
                position="at_depth",
                depth=2,
            ),
        ]
        msg = self.assembler._build_lookback_context_message(entries, 90)
        assert msg is not None
        assert "前文条目" in msg["content"]
        assert "深度条目" not in msg["content"]

    def test_disabled_filtered_out(self) -> None:
        """禁用条目被过滤。"""
        entries = [
            ContextEntry(
                uid="ctx_enabled",
                category="characters",
                content="启用条目",
                position="before",
                enabled=True,
            ),
            ContextEntry(
                uid="ctx_disabled",
                category="characters",
                content="禁用条目",
                position="before",
                enabled=False,
            ),
        ]
        msg = self.assembler._build_lookback_context_message(entries, 90)
        assert msg is not None
        assert "启用条目" in msg["content"]
        assert "禁用条目" not in msg["content"]


# ===== assemble 注入位置测试 =====


class TestAssembleLookbackInjection:
    """``assemble`` 方法注入回溯上下文的位置。"""

    __test__ = True

    def setup_method(self) -> None:
        self.assembler = make_assembler()
        self.preset = make_preset_with_markers()
        self.chapters = make_chapters(5)
        self.current_chapter = self.chapters[-1]
        self.novel_profile = make_novel_profile()
        self.lookback_entries = make_lookback_entries()

    def test_no_lookback_entries_no_injection(self) -> None:
        """未传 lookback_context_entries 时 messages 中不应有回溯消息。"""
        result = self.assembler.assemble(
            preset=self.preset,
            chapters=self.chapters,
            current_chapter=self.current_chapter,
            context_entries=[],
            model="gpt-4o",
            novel_profile=self.novel_profile,
        )
        assert find_lookback_message(result.messages) is None

    def test_inject_after_worldinfobefore(self) -> None:
        """有 worldInfoBefore marker 时，回溯消息注入其后。"""
        # 当前章 context_entries 为空，确保 worldInfoBefore 不出现
        result = self.assembler.assemble(
            preset=self.preset,
            chapters=self.chapters,
            current_chapter=self.current_chapter,
            context_entries=[],
            model="gpt-4o",
            novel_profile=self.novel_profile,
            lookback_context_entries=self.lookback_entries,
            lookback_context_source_index=3,
        )
        lb_msg = find_lookback_message(result.messages)
        assert lb_msg is not None
        assert "# 前章上下文参考（第3章提取）" in lb_msg["content"]

    def test_inject_with_worldinfobefore_entries_present(self) -> None:
        """当前章有 before 条目时，worldInfoBefore 消息与回溯消息并存。"""
        current_entries = [
            ContextEntry(
                uid="cur_before",
                category="locations",
                content="当前章地点：京城",
                position="before",
            ),
        ]
        result = self.assembler.assemble(
            preset=self.preset,
            chapters=self.chapters,
            current_chapter=self.current_chapter,
            context_entries=current_entries,
            model="gpt-4o",
            novel_profile=self.novel_profile,
            lookback_context_entries=self.lookback_entries,
            lookback_context_source_index=3,
        )
        # 两条 system 消息都应存在
        messages = result.messages
        # 当前章 worldInfoBefore 消息
        wib_msgs = [
            m for m in messages
            if m.get("role") == "system" and "京城" in m.get("content", "")
        ]
        assert len(wib_msgs) == 1
        # 回溯消息
        lb_msg = find_lookback_message(messages)
        assert lb_msg is not None
        # 回溯消息应在 worldInfoBefore 之后
        assert messages.index(lb_msg) > messages.index(wib_msgs[0])

    def test_inject_before_chathistory_without_worldinfobefore(self) -> None:
        """无 worldInfoBefore marker 时，回溯消息注入 chatHistory 之前。"""
        preset = make_preset_without_worldinfobefore()
        result = self.assembler.assemble(
            preset=preset,
            chapters=self.chapters,
            current_chapter=self.current_chapter,
            context_entries=[],
            model="gpt-4o",
            novel_profile=self.novel_profile,
            lookback_context_entries=self.lookback_entries,
            lookback_context_source_index=2,
        )
        lb_msg = find_lookback_message(result.messages)
        assert lb_msg is not None
        assert "# 前章上下文参考（第2章提取）" in lb_msg["content"]
        # 找到第一条历史消息（user role 含「测试章节」）
        history_msgs = [
            m for m in result.messages
            if m.get("role") == "user" and "测试章节" in m.get("content", "")
        ]
        assert len(history_msgs) > 0
        # 回溯消息应在第一条历史消息之前
        assert result.messages.index(lb_msg) < result.messages.index(history_msgs[0])

    def test_empty_lookback_entries_no_injection(self) -> None:
        """lookback_context_entries 为空列表时不注入。"""
        result = self.assembler.assemble(
            preset=self.preset,
            chapters=self.chapters,
            current_chapter=self.current_chapter,
            context_entries=[],
            model="gpt-4o",
            novel_profile=self.novel_profile,
            lookback_context_entries=[],
            lookback_context_source_index=3,
        )
        assert find_lookback_message(result.messages) is None

    def test_all_disabled_lookback_no_injection(self) -> None:
        """回溯条目全部禁用时不注入。"""
        disabled_entries = [
            ContextEntry(
                uid="lb_disabled",
                content="禁用条目",
                position="before",
                enabled=False,
            ),
        ]
        result = self.assembler.assemble(
            preset=self.preset,
            chapters=self.chapters,
            current_chapter=self.current_chapter,
            context_entries=[],
            model="gpt-4o",
            novel_profile=self.novel_profile,
            lookback_context_entries=disabled_entries,
            lookback_context_source_index=3,
        )
        assert find_lookback_message(result.messages) is None

    def test_lookback_message_is_system_role(self) -> None:
        """注入的回溯消息 role 为 system。"""
        result = self.assembler.assemble(
            preset=self.preset,
            chapters=self.chapters,
            current_chapter=self.current_chapter,
            context_entries=[],
            model="gpt-4o",
            novel_profile=self.novel_profile,
            lookback_context_entries=self.lookback_entries,
            lookback_context_source_index=3,
        )
        lb_msg = find_lookback_message(result.messages)
        assert lb_msg is not None
        assert lb_msg["role"] == "system"

    def test_lookback_injected_only_once(self) -> None:
        """回溯消息只注入一次（不重复）。"""
        result = self.assembler.assemble(
            preset=self.preset,
            chapters=self.chapters,
            current_chapter=self.current_chapter,
            context_entries=[],
            model="gpt-4o",
            novel_profile=self.novel_profile,
            lookback_context_entries=self.lookback_entries,
            lookback_context_source_index=3,
        )
        lb_msgs = [
            m for m in result.messages
            if m.get("role") == "system" and "前章上下文参考" in m.get("content", "")
        ]
        assert len(lb_msgs) == 1


# ===== 端到端集成：回溯上下文与当前章条目并存 =====


class TestLookbackAndCurrentCoexist:
    """回溯上下文与当前章 before/after 条目共存场景。"""

    __test__ = True

    def test_all_three_messages_present(self) -> None:
        """当前章 before/after + 回溯消息三者并存。"""
        assembler = make_assembler()
        preset = make_preset_with_markers()
        chapters = make_chapters(5)
        current_chapter = chapters[-1]
        novel_profile = make_novel_profile()

        current_entries = [
            ContextEntry(
                uid="cur_before",
                category="characters",
                content="当前章主角信息",
                position="before",
            ),
            ContextEntry(
                uid="cur_after",
                category="style",
                content="当前章风格",
                position="after",
            ),
        ]
        lookback_entries = [
            ContextEntry(
                uid="lb_before",
                category="locations",
                content="回溯章地点",
                position="before",
            ),
        ]

        result = assembler.assemble(
            preset=preset,
            chapters=chapters,
            current_chapter=current_chapter,
            context_entries=current_entries,
            model="gpt-4o",
            novel_profile=novel_profile,
            lookback_context_entries=lookback_entries,
            lookback_context_source_index=3,
        )

        messages = result.messages
        # 当前章 before 消息
        assert any("当前章主角信息" in m.get("content", "") for m in messages)
        # 当前章 after 消息
        assert any("当前章风格" in m.get("content", "") for m in messages)
        # 回溯消息
        lb_msg = find_lookback_message(messages)
        assert lb_msg is not None
        assert "回溯章地点" in lb_msg["content"]
