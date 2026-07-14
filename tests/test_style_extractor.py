"""StyleExtractor 文风档案提取测试。

覆盖：
1. _split_chapters_by_token_limit 单批次（token_limit=0 或超大限制）
2. _split_chapters_by_token_limit 多批次（小 token_limit 按章节边界拆分）
3. _build_style_prompt 8 个占位符全部替换
4. _merge_style_fields 序列化长度启发式合并
5. _merge_style_fields 空字段取另一侧
6. extract_style_streaming 单批次提取返回 StyleProfile
7. extract_style_streaming 多批次触发【信息汇总】合并
8. StyleProfile 模型 9 维度校验
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from novelforge.models.style_profile import StyleProfile
from novelforge.services.llm_client import LLMError
from novelforge.services.style_extractor import (
    STYLE_DIMENSIONS,
    StyleExtractor,
)


# ===== 测试工具 =====


def make_chapter(
    index: int = 0,
    title: str = "",
    content: str = "",
    chapter_id: str = "",
    project_id: str = "test_proj",
) -> Any:
    """构建测试 Chapter 对象。"""
    from novelforge.models import Chapter

    return Chapter(
        id=chapter_id or f"ch_{index}",
        project_id=project_id,
        index=index,
        title=title or f"第{index + 1}章",
        content=content,
        word_count=len(content),
    )


def make_project(
    project_id: str = "test_proj",
    title: str = "测试小说",
    author: str = "测试作者",
    protagonist: str = "主角",
) -> Any:
    """构建测试 Project 对象。"""
    from novelforge.models import NovelProfile, Project

    profile = NovelProfile(
        title=title,
        author=author,
        protagonist=protagonist,
        synopsis="测试简介",
        world_setting="测试世界观",
        writing_style="测试风格",
    )
    return Project(
        id=project_id,
        name=title,
        novel_profile=profile,
    )


def _make_style_json() -> str:
    """构建含 9 维度的有效 StyleProfile JSON 字符串。"""
    return json.dumps(
        {
            "language_texture": {"avg_sentence_length": 18.3, "register_type": "冷峻白描"},
            "narrative_rhythm": {"dialogue_ratio": 0.45, "conflict_density": 2.1},
            "scene_construction": {"sensory_density": 3.2, "visual_ratio": 0.6},
            "character_portrayal": {"compound_tag_ratio": 0.3, "subtext_density": 0.15},
            "emotion_engagement": {"expression_style": "动作外化型", "psychology_density": 1.8},
            "innovation_signature": {"style_fingerprint": "物质性意象密集"},
            "protagonist_supporting_ratio": {"protagonist_presence_ratio": 0.65, "role_structure_type": "主角主导"},
            "perspective_usage": {"perspective_type": "第三人称限制（单角色）", "main_perspective_ratio": 0.9},
            "time_transition": {"timeline_structure": "线性顺叙", "scene_switch_frequency": 1.5},
        },
        ensure_ascii=False,
    )


# ===== StyleExtractor 测试 =====


class TestStyleExtractor:
    """StyleExtractor 文风档案提取测试。"""

    def _make_extractor(self) -> StyleExtractor:
        """构建 StyleExtractor 实例（带 mock 依赖）。"""
        storage_service = MagicMock()
        config_manager = MagicMock()
        config_manager.get_default_endpoint.return_value = {
            "id": "ep1",
            "base_url": "https://api.test.com/v1",
            "default_model": "gpt-4o-mini",
        }
        config_manager.decrypt_api_key.return_value = "sk-test"
        token_counter = MagicMock()
        return StyleExtractor(storage_service, config_manager, token_counter)

    def test_split_chapters_by_token_limit_single_batch(self) -> None:
        """token_limit=0 或超大限制时返回单批次。"""
        extractor = self._make_extractor()
        chapters = [
            make_chapter(index=0, content="内容一"),
            make_chapter(index=1, content="内容二"),
            make_chapter(index=2, content="内容三"),
        ]

        # token_limit=0：不拆分，返回单批次包含全部章节
        batches = extractor._split_chapters_by_token_limit(
            chapters, 0, "gpt-4o-mini"
        )
        assert len(batches) == 1
        assert len(batches[0]) == 3

        # 超大 token_limit：所有章节装进一批
        extractor.token_counter.count = MagicMock(return_value=10)
        batches = extractor._split_chapters_by_token_limit(
            chapters, 100000, "gpt-4o-mini"
        )
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_split_chapters_by_token_limit_multi_batch(self) -> None:
        """小 token_limit 时按章节边界贪心拆分为多批次。"""
        extractor = self._make_extractor()
        # 每章 100 tokens，token_limit=10 → 每章独占一批
        extractor.token_counter.count = MagicMock(return_value=100)
        chapters = [
            make_chapter(index=0, content="内容一"),
            make_chapter(index=1, content="内容二"),
            make_chapter(index=2, content="内容三"),
        ]

        batches = extractor._split_chapters_by_token_limit(
            chapters, 10, "gpt-4o-mini"
        )
        assert len(batches) == 3
        # 每批含 1 章，顺序保持
        assert len(batches[0]) == 1
        assert len(batches[1]) == 1
        assert len(batches[2]) == 1
        assert batches[0][0].index == 0
        assert batches[1][0].index == 1
        assert batches[2][0].index == 2

    def test_build_style_prompt_placeholders(self) -> None:
        """验证 8 个占位符均被替换（title/author/protagonist/synopsis/
        world_setting/writing_style/accumulated_style/chapters_text）。"""
        extractor = self._make_extractor()
        project = make_project(title="我的小说", author="作者甲", protagonist="张三")
        chapters_text = "## 第一章\n\n这是章节内容。"
        accumulated = {"language_texture": {"avg_sentence_length": 15.2}}

        prompt = extractor._build_style_prompt(
            project, chapters_text, accumulated
        )

        # 8 个占位符应全部被替换
        assert "{{title}}" not in prompt
        assert "{{author}}" not in prompt
        assert "{{protagonist}}" not in prompt
        assert "{{synopsis}}" not in prompt
        assert "{{world_setting}}" not in prompt
        assert "{{writing_style}}" not in prompt
        assert "{{accumulated_style}}" not in prompt
        assert "{{chapters_text}}" not in prompt
        # 填充值应出现在 prompt 中
        assert "我的小说" in prompt
        assert "作者甲" in prompt
        assert "张三" in prompt
        assert "测试简介" in prompt
        assert "测试世界观" in prompt
        assert "测试风格" in prompt
        assert "这是章节内容。" in prompt
        # accumulated_style 被序列化为 JSON 注入
        assert "15.2" in prompt

    def test_merge_style_fields_length_heuristic(self) -> None:
        """验证字段级合并使用序列化长度启发式（新值更长或相等时取新值，
        旧值更长时保留旧值）。"""
        extractor = self._make_extractor()

        # 新值更长 → 取新值
        accumulated = {
            "language_texture": {"avg_sentence_length": 15.0},
        }
        new_batch = {
            "language_texture": {"avg_sentence_length": 18.3, "register_type": "冷峻白描"},
        }
        result = extractor._merge_style_fields(accumulated, new_batch)
        assert (
            result["language_texture"]["register_type"] == "冷峻白描"
        )

        # 旧值更长 → 保留旧值
        accumulated2 = {
            "narrative_rhythm": {
                "dialogue_ratio": 0.45,
                "conflict_density": 2.1,
                "description_ratio": 0.3,
            },
        }
        new_batch2 = {"narrative_rhythm": {"dialogue_ratio": 0.5}}
        result2 = extractor._merge_style_fields(accumulated2, new_batch2)
        assert result2["narrative_rhythm"]["conflict_density"] == 2.1

    def test_merge_style_fields_empty_handling(self) -> None:
        """验证空字段取另一侧的值，双侧均空返回空 dict。"""
        extractor = self._make_extractor()

        # 旧空、新非空 → 取新
        accumulated = {"language_texture": {}}
        new_batch = {"language_texture": {"avg_sentence_length": 18.3}}
        result = extractor._merge_style_fields(accumulated, new_batch)
        assert result["language_texture"] == {"avg_sentence_length": 18.3}

        # 旧非空、新空 → 取旧
        accumulated2 = {"narrative_rhythm": {"dialogue_ratio": 0.45}}
        new_batch2 = {"narrative_rhythm": {}}
        result2 = extractor._merge_style_fields(accumulated2, new_batch2)
        assert result2["narrative_rhythm"] == {"dialogue_ratio": 0.45}

        # 双侧均空 → 空 dict
        accumulated3 = {"scene_construction": {}}
        new_batch3 = {"scene_construction": {}}
        result3 = extractor._merge_style_fields(accumulated3, new_batch3)
        assert result3["scene_construction"] == {}

    def test_extract_style_streaming_single_batch(self) -> None:
        """单批次提取返回 StyleProfile。"""
        extractor = self._make_extractor()

        style_json = _make_style_json()
        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(
            return_value={
                "choices": [{"message": {"content": style_json}}],
                "usage": {"total_tokens": 100},
            }
        )
        extractor._get_llm_client = MagicMock(
            return_value=(mock_client, "gpt-4o-mini")
        )

        chapters = [make_chapter(index=0, content="章节内容")]
        style_profile, status = asyncio.run(
            extractor.extract_style_streaming(
                project=make_project(),
                chapters=chapters,
                token_limit=0,
            )
        )

        assert style_profile is not None
        assert isinstance(style_profile, StyleProfile)
        assert style_profile.language_texture == {"avg_sentence_length": 18.3, "register_type": "冷峻白描"}
        assert style_profile.source_chapter_range == (0, 0)
        assert "完成" in status
        # 单批次仅 1 次 LLM 调用，不触发汇总
        assert mock_client.chat_completion.call_count == 1

    def test_extract_style_streaming_multi_batch_merge(self) -> None:
        """多批次提取触发【信息汇总】合并环节。"""
        extractor = self._make_extractor()
        # 每章 100 tokens，token_limit=10 → 每章独占一批（3 批）
        extractor.token_counter.count = MagicMock(return_value=100)

        batch_resp = json.dumps(
            {
                "language_texture": {"avg_sentence_length": 18.3, "register_type": "冷峻白描"},
                "time_transition": {"timeline_structure": "线性顺叙"},
            },
            ensure_ascii=False,
        )
        merge_resp = _make_style_json()

        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(
            side_effect=[
                {"choices": [{"message": {"content": batch_resp}}], "usage": {}},
                {"choices": [{"message": {"content": batch_resp}}], "usage": {}},
                {"choices": [{"message": {"content": batch_resp}}], "usage": {}},
                {"choices": [{"message": {"content": merge_resp}}], "usage": {}},
            ]
        )
        extractor._get_llm_client = MagicMock(
            return_value=(mock_client, "gpt-4o-mini")
        )

        long_content = "这是一段测试用的章节内容用于触发多批次拆分。" * 3
        chapters = [
            make_chapter(index=0, content=long_content),
            make_chapter(index=1, content=long_content),
            make_chapter(index=2, content=long_content),
        ]

        style_profile, status = asyncio.run(
            extractor.extract_style_streaming(
                project=make_project(),
                chapters=chapters,
                token_limit=10,
            )
        )

        assert style_profile is not None
        assert isinstance(style_profile, StyleProfile)
        # 3 批 + 1 次汇总 = 4 次 LLM 调用
        assert mock_client.chat_completion.call_count == 4
        # 汇总结果被采纳
        assert style_profile.narrative_rhythm == {"dialogue_ratio": 0.45, "conflict_density": 2.1}
        assert style_profile.time_transition == {"timeline_structure": "线性顺叙", "scene_switch_frequency": 1.5}
        assert "完成" in status

    def test_style_profile_model_dimensions(self) -> None:
        """验证 StyleProfile 模型含 9 大维度字段，默认空 dict。"""
        sp = StyleProfile()
        assert len(STYLE_DIMENSIONS) == 9
        for dim in STYLE_DIMENSIONS:
            assert hasattr(sp, dim)
            assert getattr(sp, dim) == {}
        # 验证元数据字段
        assert sp.extracted_at is None
        assert sp.source_chapter_range is None

    def test_style_profile_model_with_data(self) -> None:
        """验证 StyleProfile 模型可接受 9 维度数据。"""
        dims = {dim: {"key": "value"} for dim in STYLE_DIMENSIONS}
        sp = StyleProfile(**dims)
        for dim in STYLE_DIMENSIONS:
            assert getattr(sp, dim) == {"key": "value"}
