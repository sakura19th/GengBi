"""主角形象/自定义角色形象的复制到章节与前文回溯注入测试。

验证内容：
1. ``ProtagonistProfile.copied_from`` 字段默认值/序列化往返/model_copy 保留
2. ``PromptAssembler._build_lookback_protagonist_message`` 边界与格式
3. ``PromptAssembler._build_lookback_custom_characters_message`` 边界与格式
4. ``PromptAssembler.assemble`` 三类回溯消息并存、注入顺序、独立标记

运行方式：
    python -m pytest tests/test_protagonist_custom_character_copy_and_lookback.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json

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
from novelforge.models.protagonist import ProtagonistProfile


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


def make_chapters(count: int = 3) -> list[Chapter]:
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


def make_novel_profile() -> NovelProfile:
    return NovelProfile(
        title="测试小说",
        author="测试作者",
        protagonist="张三",
        synopsis="武侠冒险故事。",
        world_setting="古代武侠世界。",
        writing_style="古风武侠",
    )


def make_assembler() -> PromptAssembler:
    return PromptAssembler(TokenCounter(), MacroEngine())


def make_profile(name: str = "张三") -> ProtagonistProfile:
    """构建含内容的主角形象档案。"""
    return ProtagonistProfile(
        basic_anchors={"姓名": name, "年龄": 25},
        personality_system={"人格结构": "自我平衡"},
        motivation_system={"核心渴望": "自由"},
        emotion_defense={"防御机制": "合理化"},
        behavior_fingerprint={"口头禅": "果然如此"},
        relationship_coordinate={"权力动态": "平等"},
        growth_arc={"弧光阶段": "践行"},
        ooc_redlines={"绝对不能": ["背叛朋友"]},
        source_chapter_range=(1, 3),
    )


def find_message_by_prefix(messages: list[dict], prefix: str) -> dict | None:
    """从 messages 中查找内容以指定前缀开头的 system 消息。"""
    for m in messages:
        if m.get("role") == "system" and m.get("content", "").startswith(prefix):
            return m
    return None


# ===== ProtagonistProfile.copied_from 字段测试 =====


class TestProtagonistProfileCopiedFrom:
    """``copied_from`` 字段行为。"""

    __test__ = True

    def test_default_is_none(self) -> None:
        """新档案 copied_from 默认 None。"""
        profile = ProtagonistProfile()
        assert profile.copied_from is None

    def test_serialization_roundtrip(self) -> None:
        """序列化/反序列化保留 copied_from。"""
        profile = ProtagonistProfile(copied_from=5, source_chapter_range=(1, 4))
        data = profile.model_dump(mode="json")
        assert data["copied_from"] == 5
        restored = ProtagonistProfile.model_validate(data)
        assert restored.copied_from == 5

    def test_model_copy_preserves_and_updates(self) -> None:
        """model_copy 保留原字段，update 可改 copied_from。"""
        original = make_profile()
        original.copied_from = None
        copied = original.model_copy(update={"copied_from": 7}, deep=True)
        # copied_from 被设为新值
        assert copied.copied_from == 7
        # 其他字段保留（深拷贝独立性）
        assert copied.basic_anchors == original.basic_anchors
        # 原 source_chapter_range 保留
        assert copied.source_chapter_range == (1, 3)
        # 修改副本不影响原档案
        copied.basic_anchors["姓名"] = "李四"
        assert original.basic_anchors.get("姓名") == "张三"

    def test_old_data_without_field_loads_none(self) -> None:
        """旧数据（无 copied_from 键）反序列化为 None（向后兼容）。"""
        data = {"basic_anchors": {}, "personality_system": {}}
        profile = ProtagonistProfile.model_validate(data)
        assert profile.copied_from is None


# ===== _build_lookback_protagonist_message 测试 =====


class TestBuildLookbackProtagonistMessage:
    """``_build_lookback_protagonist_message`` 方法行为。"""

    __test__ = True

    def setup_method(self) -> None:
        self.assembler = make_assembler()

    def test_none_profile_returns_none(self) -> None:
        """profile=None 返回 None。"""
        assert self.assembler._build_lookback_protagonist_message(None, 99) is None

    def test_valid_profile_returns_message(self) -> None:
        """有效档案返回 system 消息。"""
        profile = make_profile()
        msg = self.assembler._build_lookback_protagonist_message(profile, 3)
        assert msg is not None
        assert msg["role"] == "system"
        assert "# 前章主角形象参考（第3章提取）" in msg["content"]
        # 包含 8 维度序列化内容
        assert "basic_anchors" in msg["content"]

    def test_none_source_index_uses_prefix(self) -> None:
        """source_index=None 时标题用「前文」。"""
        profile = make_profile()
        msg = self.assembler._build_lookback_protagonist_message(profile, None)
        assert msg is not None
        assert "# 前章主角形象参考（前文提取）" in msg["content"]


# ===== _build_lookback_custom_characters_message 测试 =====


class TestBuildLookbackCustomCharactersMessage:
    """``_build_lookback_custom_characters_message`` 方法行为。"""

    __test__ = True

    def setup_method(self) -> None:
        self.assembler = make_assembler()

    def test_none_returns_none(self) -> None:
        """characters=None 返回 None。"""
        assert self.assembler._build_lookback_custom_characters_message(None, 99) is None

    def test_empty_dict_returns_none(self) -> None:
        """空 dict 返回 None。"""
        assert self.assembler._build_lookback_custom_characters_message({}, 99) is None

    def test_valid_dict_returns_message_with_roles(self) -> None:
        """有效 dict 返回含多角色分节的消息。"""
        chars = {
            "张三": make_profile("张三"),
            "李四": make_profile("李四"),
        }
        msg = self.assembler._build_lookback_custom_characters_message(chars, 5)
        assert msg is not None
        assert msg["role"] == "system"
        assert "# 前章自定义角色参考（第5章提取）" in msg["content"]
        # 两个角色分节均出现
        assert "【角色：张三】" in msg["content"]
        assert "【角色：李四】" in msg["content"]

    def test_none_source_index_uses_prefix(self) -> None:
        """source_index=None 时标题用「前文」。"""
        chars = {"王五": make_profile("王五")}
        msg = self.assembler._build_lookback_custom_characters_message(chars, None)
        assert msg is not None
        assert "# 前章自定义角色参考（前文提取）" in msg["content"]


# ===== assemble 注入位置与顺序测试 =====


class TestAssembleLookbackInjection:
    """``assemble`` 三类回溯消息注入行为。"""

    __test__ = True

    def setup_method(self) -> None:
        self.assembler = make_assembler()
        self.preset = make_preset_with_markers()
        self.chapters = make_chapters(3)
        self.novel_profile = make_novel_profile()

    def _assemble(
        self,
        lookback_context_entries=None,
        lookback_protagonist_profile=None,
        lookback_custom_characters=None,
        lookback_context_source_index=2,
        lookback_protagonist_source_index=2,
        lookback_custom_characters_source_index=2,
    ):
        return self.assembler.assemble(
            preset=self.preset,
            chapters=self.chapters,
            current_chapter=self.chapters[-1],
            context_entries=[],
            model="",
            max_context=32000,
            max_tokens=2000,
            target_words=2000,
            novel_profile=self.novel_profile,
            project_id="test_project",
            user_input="续写下一章",
            lookback_context_entries=lookback_context_entries,
            lookback_context_source_index=lookback_context_source_index,
            lookback_protagonist_profile=lookback_protagonist_profile,
            lookback_protagonist_source_index=lookback_protagonist_source_index,
            lookback_custom_characters=lookback_custom_characters,
            lookback_custom_characters_source_index=lookback_custom_characters_source_index,
        )

    def test_all_none_no_extra_messages(self) -> None:
        """三类回溯均为 None 时无回溯消息。"""
        result = self._assemble()
        msgs = result.messages
        assert not any("前章" in m.get("content", "") for m in msgs if m.get("role") == "system")

    def test_only_protagonist_injected(self) -> None:
        """仅传主角形象回溯时注入对应消息。"""
        result = self._assemble(
            lookback_protagonist_profile=make_profile(),
        )
        msgs = result.messages
        prot_msg = find_message_by_prefix(msgs, "# 前章主角形象参考")
        assert prot_msg is not None
        # 无上下文/自定义角色回溯消息
        assert find_message_by_prefix(msgs, "# 前章上下文参考") is None
        assert find_message_by_prefix(msgs, "# 前章自定义角色参考") is None

    def test_only_custom_characters_injected(self) -> None:
        """仅传自定义角色回溯时注入对应消息。"""
        result = self._assemble(
            lookback_custom_characters={"赵六": make_profile("赵六")},
        )
        msgs = result.messages
        cc_msg = find_message_by_prefix(msgs, "# 前章自定义角色参考")
        assert cc_msg is not None
        assert find_message_by_prefix(msgs, "# 前章上下文参考") is None
        assert find_message_by_prefix(msgs, "# 前章主角形象参考") is None

    def test_all_three_coexist_in_order(self) -> None:
        """三类回溯并存时按 上下文→主角→自定义角色 顺序注入。"""
        ctx_entries = [
            ContextEntry(
                uid="lb_1",
                category="characters",
                key=["主角"],
                content="前章主角信息。",
                order=100,
                position="before",
            ),
        ]
        result = self._assemble(
            lookback_context_entries=ctx_entries,
            lookback_protagonist_profile=make_profile(),
            lookback_custom_characters={"赵六": make_profile("赵六")},
        )
        msgs = result.messages
        ctx_msg = find_message_by_prefix(msgs, "# 前章上下文参考")
        prot_msg = find_message_by_prefix(msgs, "# 前章主角形象参考")
        cc_msg = find_message_by_prefix(msgs, "# 前章自定义角色参考")
        assert ctx_msg is not None
        assert prot_msg is not None
        assert cc_msg is not None
        # 验证顺序：上下文 < 主角 < 自定义角色
        idx_ctx = msgs.index(ctx_msg)
        idx_prot = msgs.index(prot_msg)
        idx_cc = msgs.index(cc_msg)
        assert idx_ctx < idx_prot < idx_cc

    def test_injected_only_once(self) -> None:
        """回溯消息仅注入一次（lookback_injected 标记保护）。"""
        result = self._assemble(
            lookback_protagonist_profile=make_profile(),
        )
        msgs = result.messages
        prot_msgs = [m for m in msgs if m.get("content", "").startswith("# 前章主角形象参考")]
        assert len(prot_msgs) == 1

    def test_injected_after_worldinfobefore(self) -> None:
        """回溯消息注入到 worldInfoBefore 之后。"""
        result = self._assemble(
            lookback_protagonist_profile=make_profile(),
        )
        msgs = result.messages
        prot_msg = find_message_by_prefix(msgs, "# 前章主角形象参考")
        assert prot_msg is not None
        prot_idx = msgs.index(prot_msg)
        # worldInfoBefore marker 之后无独立 worldInfo 消息（无条目），
        # 回溯消息应出现在 chatHistory（章节正文）之前
        # 找到第一条包含章节正文的 user 消息
        history_idx = None
        for i, m in enumerate(msgs):
            if "正文内容" in m.get("content", ""):
                history_idx = i
                break
        assert history_idx is not None
        assert prot_idx < history_idx


# ===== 复制合并语义测试（model 层，无 Qt） =====


class TestCopyMergeSemantics:
    """复制操作的合并语义（不依赖 Qt，测 model_copy 行为）。"""

    __test__ = True

    def test_protagonist_copy_generates_new_uid_free(self) -> None:
        """主角形象复制无需新 uid（单章单档案），model_copy 独立。"""
        original = make_profile()
        copied = original.model_copy(update={"copied_from": 2}, deep=True)
        assert copied.copied_from == 2
        # 修改副本不影响原档案
        copied.basic_anchors["姓名"] = "改名"
        assert original.basic_anchors.get("姓名") == "张三"

    def test_custom_character_merge_preserves_others(self) -> None:
        """自定义角色复制：目标 dict 同名覆盖、其他角色保留。"""
        target_chars = {
            "李四": make_profile("李四"),
            "王五": make_profile("王五"),
        }
        # 复制「赵六」到目标
        copied_zhao = make_profile("赵六").model_copy(update={"copied_from": 3})
        merged = dict(target_chars)
        merged["赵六"] = copied_zhao
        # 其他角色保留
        assert "李四" in merged and "王五" in merged
        # 新角色加入
        assert "赵六" in merged
        # 原 dict 未被修改
        assert "赵六" not in target_chars

    def test_custom_character_overwrite_same_name(self) -> None:
        """自定义角色复制同名覆盖：目标 dict 同名角色被新档案替换。"""
        target_chars = {"张三": make_profile("旧张三")}
        copied = make_profile("新张三").model_copy(update={"copied_from": 1})
        merged = dict(target_chars)
        merged["张三"] = copied
        # 同名角色被覆盖
        assert merged["张三"].basic_anchors.get("姓名") == "新张三"
        assert merged["张三"].copied_from == 1
