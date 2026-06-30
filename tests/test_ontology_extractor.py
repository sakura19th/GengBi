"""OntologyExtractor 底层世界观提取测试。

覆盖：
1. _split_chapters_by_token_limit 单批次（token_limit=0 或超大限制）
2. _split_chapters_by_token_limit 多批次（小 token_limit 按章节边界拆分）
3. _build_ontology_prompt 8 个占位符全部替换
4. _merge_ontology_fields 序列化长度启发式合并
5. _merge_ontology_fields 空字段取另一侧
6. _save_ontology_to_worldbook 拆 7 维度为 ContextEntry 并返回 worldbook_id
7. extract_ontology_streaming 单批次提取返回 WorldOntology
8. extract_ontology_streaming 多批次触发【信息汇总】合并
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

from novelforge.models import ContextEntry, WorldOntology
from novelforge.services.llm_client import LLMError
from novelforge.services.ontology_extractor import (
    ONTOLOGY_DIMENSIONS,
    OntologyExtractor,
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


def _make_ontology_json() -> str:
    """构建含 7 维度的有效 WorldOntology JSON 字符串。"""
    return json.dumps(
        {
            "existential_topology": {"being_hierarchy": "三界"},
            "causal_architecture": {"causal_directionality": "前向"},
            "spatio_temporal_ontology": {"time_ontology": "线性"},
            "information_epistemology": {"truth_mechanics": "经验验证"},
            "axiological_foundation": {"morality_source": "契约"},
            "becoming_dynamics": {"change_ontology": "循环"},
            "narrative_ontology": {"ending_logic": "开放式"},
        },
        ensure_ascii=False,
    )


# ===== OntologyExtractor 测试 =====


class TestOntologyExtractor:
    """OntologyExtractor 底层世界观提取测试。"""

    def _make_extractor(self) -> OntologyExtractor:
        """构建 OntologyExtractor 实例（带 mock 依赖）。"""
        storage_service = MagicMock()
        config_manager = MagicMock()
        config_manager.get_default_endpoint.return_value = {
            "id": "ep1",
            "base_url": "https://api.test.com/v1",
            "default_model": "gpt-4o-mini",
        }
        config_manager.decrypt_api_key.return_value = "sk-test"
        token_counter = MagicMock()
        return OntologyExtractor(storage_service, config_manager, token_counter)

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

    def test_build_ontology_prompt_placeholders(self) -> None:
        """验证 8 个占位符均被替换（title/author/protagonist/synopsis/
        world_setting/writing_style/accumulated_ontology/chapters_text）。"""
        extractor = self._make_extractor()
        project = make_project(title="我的小说", author="作者甲", protagonist="张三")
        chapters_text = "## 第一章\n\n这是章节内容。"
        accumulated = {"existential_topology": {"being_hierarchy": "三界"}}

        prompt = extractor._build_ontology_prompt(
            project, chapters_text, accumulated
        )

        # 8 个占位符应全部被替换
        assert "{{title}}" not in prompt
        assert "{{author}}" not in prompt
        assert "{{protagonist}}" not in prompt
        assert "{{synopsis}}" not in prompt
        assert "{{world_setting}}" not in prompt
        assert "{{writing_style}}" not in prompt
        assert "{{accumulated_ontology}}" not in prompt
        assert "{{chapters_text}}" not in prompt
        # 填充值应出现在 prompt 中
        assert "我的小说" in prompt
        assert "作者甲" in prompt
        assert "张三" in prompt
        assert "测试简介" in prompt
        assert "测试世界观" in prompt
        assert "测试风格" in prompt
        assert "这是章节内容。" in prompt
        # accumulated_ontology 被序列化为 JSON 注入
        assert "三界" in prompt

    def test_merge_ontology_fields_length_heuristic(self) -> None:
        """验证字段级合并使用序列化长度启发式（新值更长或相等时取新值，
        旧值更长时保留旧值）。"""
        extractor = self._make_extractor()

        # 新值更长 → 取新值
        accumulated = {
            "existential_topology": {"being_hierarchy": "短"},
        }
        new_batch = {
            "existential_topology": {"being_hierarchy": "这是一个更长的存在层级描述"},
        }
        result = extractor._merge_ontology_fields(accumulated, new_batch)
        assert (
            result["existential_topology"]["being_hierarchy"]
            == "这是一个更长的存在层级描述"
        )

        # 旧值更长 → 保留旧值
        accumulated2 = {
            "causal_architecture": {
                "causal_directionality": "这是一个很长的旧因果方向描述"
            },
        }
        new_batch2 = {"causal_architecture": {"causal_directionality": "短"}}
        result2 = extractor._merge_ontology_fields(accumulated2, new_batch2)
        assert (
            result2["causal_architecture"]["causal_directionality"]
            == "这是一个很长的旧因果方向描述"
        )

    def test_merge_ontology_fields_empty_handling(self) -> None:
        """验证空字段取另一侧的值，双侧均空返回空 dict。"""
        extractor = self._make_extractor()

        # 旧空、新非空 → 取新
        accumulated = {"existential_topology": {}}
        new_batch = {"existential_topology": {"being_hierarchy": "三界"}}
        result = extractor._merge_ontology_fields(accumulated, new_batch)
        assert result["existential_topology"] == {"being_hierarchy": "三界"}

        # 旧非空、新空 → 取旧
        accumulated2 = {"causal_architecture": {"causal_directionality": "前向"}}
        new_batch2 = {"causal_architecture": {}}
        result2 = extractor._merge_ontology_fields(accumulated2, new_batch2)
        assert result2["causal_architecture"] == {"causal_directionality": "前向"}

        # 双侧均空 → 空 dict
        accumulated3 = {"spatio_temporal_ontology": {}}
        new_batch3 = {"spatio_temporal_ontology": {}}
        result3 = extractor._merge_ontology_fields(accumulated3, new_batch3)
        assert result3["spatio_temporal_ontology"] == {}

    def test_save_ontology_to_worldbook(self) -> None:
        """验证 7 维度被拆分为 ContextEntry 对象并返回非空 worldbook_id。"""
        extractor = self._make_extractor()
        project = make_project()
        # project.worldbook_id 初始为空
        assert project.worldbook_id == ""

        # 构造含 7 维度非空值的 WorldOntology
        dims = {dim: {"key": "value"} for dim in ONTOLOGY_DIMENSIONS}
        ontology = WorldOntology(**dims)

        # 捕获 ContextEntry 构造调用（wraps 保持真实构造行为）
        with patch(
            "novelforge.services.ontology_extractor.ContextEntry",
            wraps=ContextEntry,
        ) as mock_entry:
            worldbook_id = extractor._save_ontology_to_worldbook(project, ontology)

        # worldbook_id 非空且已绑定到 project
        assert worldbook_id
        assert project.worldbook_id == worldbook_id
        # 7 维度均非空 → 构造 7 个 ContextEntry
        assert mock_entry.call_count == 7
        # 验证 ContextEntry 构造参数
        for call in mock_entry.call_args_list:
            kwargs = call.kwargs
            assert kwargs["category"] == "plot_state"
            assert kwargs["uid"].startswith(worldbook_id)
            assert kwargs["position"] == "before"
            assert kwargs["role"] == "system"
            assert kwargs["depth"] == 4

    def test_extract_ontology_streaming_single_batch(self) -> None:
        """单批次提取返回 WorldOntology。"""
        extractor = self._make_extractor()

        ontology_json = _make_ontology_json()
        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(
            return_value={
                "choices": [{"message": {"content": ontology_json}}],
                "usage": {"total_tokens": 100},
            }
        )
        extractor._get_llm_client = MagicMock(
            return_value=(mock_client, "gpt-4o-mini")
        )

        chapters = [make_chapter(index=0, content="章节内容")]
        ontology, status = asyncio.run(
            extractor.extract_ontology_streaming(
                project=make_project(),
                chapters=chapters,
                token_limit=0,
            )
        )

        assert ontology is not None
        assert isinstance(ontology, WorldOntology)
        assert ontology.existential_topology == {"being_hierarchy": "三界"}
        assert ontology.source_chapter_range == (0, 0)
        assert "完成" in status
        # 单批次仅 1 次 LLM 调用，不触发汇总
        assert mock_client.chat_completion.call_count == 1

    def test_extract_ontology_streaming_multi_batch_merge(self) -> None:
        """多批次提取触发【信息汇总】合并环节。"""
        extractor = self._make_extractor()
        # 每章 100 tokens，token_limit=10 → 每章独占一批（3 批）
        extractor.token_counter.count = MagicMock(return_value=100)

        batch_resp = json.dumps(
            {
                "existential_topology": {"being_hierarchy": "三界"},
                "narrative_ontology": {"ending_logic": "开放式"},
            },
            ensure_ascii=False,
        )
        merge_resp = _make_ontology_json()

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

        ontology, status = asyncio.run(
            extractor.extract_ontology_streaming(
                project=make_project(),
                chapters=chapters,
                token_limit=10,
            )
        )

        assert ontology is not None
        assert isinstance(ontology, WorldOntology)
        # 3 批 + 1 次汇总 = 4 次 LLM 调用
        assert mock_client.chat_completion.call_count == 4
        # 汇总结果被采纳
        assert ontology.causal_architecture == {"causal_directionality": "前向"}
        assert ontology.narrative_ontology == {"ending_logic": "开放式"}
        assert "完成" in status

    def test_extract_batch_retry_on_llm_error(self) -> None:
        """单批次首次 LLM 调用失败，第二次成功 → 提取成功，call_count==2。"""
        extractor = self._make_extractor()

        ontology_json = _make_ontology_json()
        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(
            side_effect=[
                LLMError("首次调用网络错误"),
                {
                    "choices": [{"message": {"content": ontology_json}}],
                    "usage": {"total_tokens": 100},
                },
            ]
        )
        extractor._get_llm_client = MagicMock(
            return_value=(mock_client, "gpt-4o-mini")
        )

        chapters = [make_chapter(index=0, content="章节内容")]
        ontology, status = asyncio.run(
            extractor.extract_ontology_streaming(
                project=make_project(),
                chapters=chapters,
                token_limit=0,
            )
        )

        assert ontology is not None
        assert isinstance(ontology, WorldOntology)
        assert ontology.existential_topology == {"being_hierarchy": "三界"}
        assert "完成" in status
        # 首次失败 + 第二次成功 = 2 次 LLM 调用
        assert mock_client.chat_completion.call_count == 2

    def test_extract_batch_retry_on_json_parse_failure(self) -> None:
        """单批次首次返回非法 JSON，第二次返回有效 JSON → 提取成功，call_count==2。"""
        extractor = self._make_extractor()

        ontology_json = _make_ontology_json()
        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(
            side_effect=[
                {"choices": [{"message": {"content": "这不是合法JSON"}}], "usage": {}},
                {"choices": [{"message": {"content": ontology_json}}], "usage": {}},
            ]
        )
        extractor._get_llm_client = MagicMock(
            return_value=(mock_client, "gpt-4o-mini")
        )

        chapters = [make_chapter(index=0, content="章节内容")]
        ontology, status = asyncio.run(
            extractor.extract_ontology_streaming(
                project=make_project(),
                chapters=chapters,
                token_limit=0,
            )
        )

        assert ontology is not None
        assert isinstance(ontology, WorldOntology)
        assert ontology.existential_topology == {"being_hierarchy": "三界"}
        assert "完成" in status
        # 首次 JSON 解析失败 + 第二次成功 = 2 次 LLM 调用
        assert mock_client.chat_completion.call_count == 2

    def test_extract_batch_retry_exhausted_fails(self) -> None:
        """单批次 2 次均抛 LLMError → 返回 None + 错误消息含批次信息，call_count==2。"""
        extractor = self._make_extractor()

        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(
            side_effect=[
                LLMError("第一次失败"),
                LLMError("第二次失败"),
            ]
        )
        extractor._get_llm_client = MagicMock(
            return_value=(mock_client, "gpt-4o-mini")
        )

        chapters = [make_chapter(index=0, content="章节内容")]
        ontology, status = asyncio.run(
            extractor.extract_ontology_streaming(
                project=make_project(),
                chapters=chapters,
                token_limit=0,
            )
        )

        assert ontology is None
        assert "批次 1/1" in status
        assert "LLM 调用失败" in status
        # 2 次均失败 = 2 次 LLM 调用
        assert mock_client.chat_completion.call_count == 2


if __name__ == "__main__":
    # 直接运行时执行所有测试
    pytest.main([__file__, "-v", "--tb=short"])
