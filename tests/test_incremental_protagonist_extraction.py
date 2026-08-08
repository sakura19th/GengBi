"""主角形象增量更新提取测试。

覆盖：
1. find_protagonist_base 基准查找：命中 / 未命中 / 跳过当前章 / copied_from 档案
2. extract_protagonist_streaming 增量：delta 章节选择
3. initial_accumulated 基准档案注入首批提示词（{{accumulated_protagonist}}）
4. 结果落盘缓存元数据 incremental_base_index
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from novelforge.models import ProtagonistProfile


# ===== 测试工具 =====


def _make_extractor() -> Any:
    """构建 ContextExtractor 实例（带 mock StorageService 与 ConfigManager）。"""
    from novelforge.services.context_extractor import ContextExtractor

    storage_service = MagicMock()
    storage_service.storage = MagicMock()
    storage_service.storage.get_cache = AsyncMock(return_value=None)
    storage_service.storage.set_cache = AsyncMock(return_value=None)

    config_manager = MagicMock()
    config_manager.get_context_extract_settings.return_value = {
        "cache_enabled": True,
        "cache_ttl_hours": 24,
        "extractor_prompt_override": None,
        "lookback_chapters": 5,
    }
    config_manager.get_flow_model.return_value = "gpt-4o-mini"
    config_manager.get_default_endpoint.return_value = {
        "id": "ep1",
        "base_url": "https://api.test.com/v1",
        "default_model": "gpt-4o-mini",
    }
    config_manager.decrypt_api_key.return_value = "sk-test"

    return ContextExtractor(storage_service, config_manager)


def _make_project_with_chapters(chapter_count: int) -> tuple[Any, list[Any]]:
    """构建测试用 Project + 指定数量的 Chapters。"""
    from novelforge.models import Chapter, NovelProfile, Project

    profile = NovelProfile(
        title="测试小说",
        author="测试作者",
        protagonist="主角",
        synopsis="测试简介",
        world_setting="测试世界观",
        writing_style="测试风格",
    )
    project = Project(
        id="inc_protagonist_proj", name="测试小说", novel_profile=profile
    )
    chapters = [
        Chapter(
            id=f"ch_{i}",
            project_id="inc_protagonist_proj",
            index=i,
            title=f"第{i + 1}章",
            content=f"第{i + 1}章正文内容" * 10,
            word_count=70,
        )
        for i in range(chapter_count)
    ]
    return project, chapters


class _StreamChunk:
    """模拟 stream_chat_completion 产出的 chunk。"""

    def __init__(self, content: str, finish_reason: str | None = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason


class _ProtagonistLLMClient:
    """模拟 LLM 客户端：记录每次 prompt，按序返回预设响应。"""

    def __init__(self) -> None:
        self.stream_responses: list[list[_StreamChunk]] = []
        self.prompts: list[str] = []
        self._idx = 0
        self.stream_call_count = 0

    def add_stream_response(self, content: str) -> None:
        """追加一次成功的流式响应。"""
        self.stream_responses.append([_StreamChunk(content)])

    async def stream_chat_completion(self, **kwargs: Any) -> Any:
        """模拟 stream_chat_completion：记录 prompt，按序返回预设响应。"""
        self.stream_call_count += 1
        messages = kwargs.get("messages", [])
        if messages:
            self.prompts.append(messages[-1].get("content", ""))
        idx = self._idx
        self._idx += 1
        if idx < len(self.stream_responses):
            for chunk in self.stream_responses[idx]:
                yield chunk


def _profile_json(name: str = "主角") -> str:
    """构造 ProtagonistProfile JSON。"""
    return json.dumps(
        {"basic_anchors": {"name": name}}, ensure_ascii=False
    )


# ===== 1. find_protagonist_base 基准查找 =====


class TestFindProtagonistBase:
    """find_protagonist_base 基准查找测试。"""

    def test_find_protagonist_base_hit(self) -> None:
        """最近的含主角档案的前章作为基准。"""
        extractor = _make_extractor()
        _, chapters = _make_project_with_chapters(4)
        chapters[1].protagonist_profile = ProtagonistProfile(
            basic_anchors={"name": "主角"}
        )
        current_chapter = chapters[3]

        result = extractor.find_protagonist_base(
            chapters=chapters,
            current_chapter=current_chapter,
        )

        assert result is not None
        base_chapter, base_profile = result
        assert base_chapter.id == "ch_1"
        assert base_profile.basic_anchors == {"name": "主角"}

    def test_find_protagonist_base_miss(self) -> None:
        """前章均无主角档案时返回 None。"""
        extractor = _make_extractor()
        _, chapters = _make_project_with_chapters(3)
        current_chapter = chapters[2]

        result = extractor.find_protagonist_base(
            chapters=chapters,
            current_chapter=current_chapter,
        )

        assert result is None

    def test_find_protagonist_base_excludes_current(self) -> None:
        """当前章自身作为基准返回 None。"""
        extractor = _make_extractor()
        _, chapters = _make_project_with_chapters(3)
        # 仅当前章有档案
        chapters[2].protagonist_profile = ProtagonistProfile(
            basic_anchors={"name": "主角"}
        )
        current_chapter = chapters[2]

        result = extractor.find_protagonist_base(
            chapters=chapters,
            current_chapter=current_chapter,
        )

        assert result is None

    def test_find_protagonist_base_skips_none_chapters(self) -> None:
        """中间章节无档案时跳过，继续往前找。"""
        extractor = _make_extractor()
        _, chapters = _make_project_with_chapters(4)
        chapters[2].protagonist_profile = None  # 无档案
        chapters[1].protagonist_profile = ProtagonistProfile(
            basic_anchors={"name": "早期主角"}
        )
        current_chapter = chapters[3]

        result = extractor.find_protagonist_base(
            chapters=chapters,
            current_chapter=current_chapter,
        )

        assert result is not None
        base_chapter, _ = result
        assert base_chapter.id == "ch_1"


# ===== 2. extract_protagonist_streaming 增量 =====


class TestIncrementalProtagonistExtraction:
    """主角形象增量提取测试。"""

    def test_extract_protagonist_incremental_delta_and_seed(self) -> None:
        """delta 章节 + 基准档案注入首批提示词 + 缓存元数据。"""
        from novelforge.services.context_extractor import ContextExtractor

        extractor = _make_extractor()
        client = _ProtagonistLLMClient()
        client.add_stream_response(_profile_json("增量后主角"))
        extractor._get_llm_client = lambda flow_key="": (client, "gpt-4o-mini")  # type: ignore[assignment]

        project, chapters = _make_project_with_chapters(5)
        current_chapter = chapters[4]
        base_profile = ProtagonistProfile(basic_anchors={"name": "基准主角"})

        profile, status = asyncio.run(
            extractor.extract_protagonist_streaming(
                project=project,
                chapters=chapters,
                current_chapter=current_chapter,
                incremental_base=base_profile,
                incremental_base_index=1,  # 基准 = ch_1
            )
        )

        assert profile is not None
        assert "完成" in status
        # 仅 delta 章节（ch_2..ch_4）被用于构建 prompt
        assert len(client.prompts) == 1
        assert "第2章" not in client.prompts[0]
        assert "第3章" in client.prompts[0]
        assert "第5章" in client.prompts[0]
        # 基准档案被注入提示词（accumulated_protagonist）
        assert "基准主角" in client.prompts[0]

        # 缓存元数据含 incremental_base_index
        call_args = extractor.storage_service.storage.set_cache.call_args
        saved_data = call_args.args[1]
        assert saved_data.get("incremental_base_index") == 1
        assert call_args.args[0].startswith("protagonist:")

    def test_extract_protagonist_incremental_current_not_after_base(self) -> None:
        """当前章节不晚于基准章节时直接返回基准档案，不调 LLM。"""
        extractor = _make_extractor()
        client = _ProtagonistLLMClient()
        extractor._get_llm_client = lambda flow_key="": (client, "gpt-4o-mini")  # type: ignore[assignment]

        _, chapters = _make_project_with_chapters(3)
        current_chapter = chapters[1]
        base_profile = ProtagonistProfile(basic_anchors={"name": "基准主角"})

        profile, status = asyncio.run(
            extractor.extract_protagonist_streaming(
                project=None,
                chapters=chapters,
                current_chapter=current_chapter,
                incremental_base=base_profile,
                incremental_base_index=2,
            )
        )

        assert profile is not None
        assert profile.basic_anchors == {"name": "基准主角"}
        assert "未执行提取" in status
        assert client.stream_call_count == 0

    def test_extract_protagonist_incremental_multi_batch(self) -> None:
        """小 token_limit 触发多批次拆分，每个提取批次均携带基准档案。"""
        extractor = _make_extractor()
        client = _ProtagonistLLMClient()
        # 3 个提取批次（3 章 delta 各拆 1 批）+ 1 次主角合并
        for i in range(3):
            client.add_stream_response(_profile_json(f"批次{i}主角"))
        client.add_stream_response(_profile_json("合并后主角"))
        extractor._get_llm_client = lambda flow_key="": (client, "gpt-4o-mini")  # type: ignore[assignment]

        project, chapters = _make_project_with_chapters(5)
        current_chapter = chapters[4]
        base_profile = ProtagonistProfile(basic_anchors={"name": "基准主角"})

        profile, status = asyncio.run(
            extractor.extract_protagonist_streaming(
                project=project,
                chapters=chapters,
                current_chapter=current_chapter,
                token_limit=10,
                incremental_base=base_profile,
                incremental_base_index=1,
            )
        )

        assert profile is not None
        assert "完成" in status
        # 首批 prompt 携带基准档案（initial_accumulated 种子）
        assert "基准主角" in client.prompts[0]
        # 后续批次携带程序化合并后的前批次累积结果
        assert "批次0主角" in client.prompts[1]
        assert "批次1主角" in client.prompts[2]
        assert client.stream_call_count >= 4


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
