"""上下文增量更新提取测试。

覆盖：
1. find_latest_context_base 基准查找：命中 / 未命中 / 空条目跳过 / 当前章不进基准
2. _build_incremental_prompt 占位符填充（含 {{existing_entries}} 基准条目序列化）
3. _extract_common 增量 delta 章节选择与缓存保存元数据（incremental_base_index）
4. extract_streaming 增量单批提取（prompt 仅含 delta 章节、基准确认）
5. extract_streaming 增量多批次合并
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


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
        id="incremental_proj", name="测试小说", novel_profile=profile
    )
    chapters = [
        Chapter(
            id=f"ch_{i}",
            project_id="incremental_proj",
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


class _IncrementalLLMClient:
    """模拟 LLM 客户端：记录每次 prompt，按序返回预设响应。"""

    def __init__(self) -> None:
        self.stream_responses: list[list[_StreamChunk]] = []
        self.stream_errors: list[Exception | None] = []
        self.prompts: list[str] = []
        self._idx = 0
        self.stream_call_count = 0

    def add_stream_response(self, content: str) -> None:
        """追加一次成功的流式响应（单 chunk）。"""
        self.stream_responses.append([_StreamChunk(content)])
        self.stream_errors.append(None)

    async def stream_chat_completion(self, **kwargs: Any) -> Any:
        """模拟 stream_chat_completion：记录 prompt，按序返回预设响应或抛错。"""
        self.stream_call_count += 1
        messages = kwargs.get("messages", [])
        if messages:
            self.prompts.append(messages[-1].get("content", ""))
        idx = self._idx
        self._idx += 1
        if idx < len(self.stream_errors) and self.stream_errors[idx] is not None:
            raise self.stream_errors[idx]
        if idx < len(self.stream_responses):
            for chunk in self.stream_responses[idx]:
                yield chunk


def _entry_dict(uid: str, category: str = "characters") -> dict[str, Any]:
    """构造最小 ContextEntry dict。"""
    return {
        "uid": uid,
        "category": category,
        "key": [uid],
        "comment": "",
        "content": f"{uid} 内容",
        "order": 100,
        "position": "before",
        "depth": 4,
        "role": "system",
    }


# ===== 1. find_latest_context_base 基准查找 =====


class TestFindLatestContextBase:
    """find_latest_context_base 基准查找测试。"""

    def test_find_latest_context_base_hit(self) -> None:
        """最近前章有缓存条目时返回该章与条目。"""
        from novelforge.models import ContextEntry

        extractor = _make_extractor()
        _, chapters = _make_project_with_chapters(4)
        current_chapter = chapters[3]

        # ch_1 缓存含条目（其余无缓存）
        cache_key_1 = extractor._build_cache_key("incremental_proj", "ch_1")
        saved = {
            "entries": [ContextEntry.model_validate(_entry_dict("char_9"))],
            "chapters_hash": "abc",
        }
        extractor.storage_service.storage.get_cache = AsyncMock(
            side_effect=lambda key: saved if key == cache_key_1 else None
        )

        result = asyncio.run(
            extractor.find_latest_context_base(
                project_id="incremental_proj",
                chapters=chapters,
                current_chapter=current_chapter,
            )
        )

        assert result is not None
        base_chapter, base_entries = result
        assert base_chapter.id == "ch_1"
        assert base_entries[0].uid == "char_9"

    def test_find_latest_context_base_miss(self) -> None:
        """前章均无缓存时返回 None。"""
        extractor = _make_extractor()
        _, chapters = _make_project_with_chapters(3)
        current_chapter = chapters[2]

        result = asyncio.run(
            extractor.find_latest_context_base(
                project_id="incremental_proj",
                chapters=chapters,
                current_chapter=current_chapter,
            )
        )

        assert result is None

    def test_find_latest_context_base_skips_empty_entries(self) -> None:
        """前章缓存 entries 为空时跳过，继续往前找。"""
        extractor = _make_extractor()
        _, chapters = _make_project_with_chapters(4)
        current_chapter = chapters[3]

        # ch_2 缓存 entries 为空（视为无有效提取），ch_1 有条目
        cache_key_2 = extractor._build_cache_key("incremental_proj", "ch_2")
        cache_key_1 = extractor._build_cache_key("incremental_proj", "ch_1")

        def _get_cache(key: str) -> dict | None:
            if key == cache_key_2:
                return {"entries": [], "chapters_hash": "abc"}
            if key == cache_key_1:
                return {
                    "entries": [_entry_dict("loc_3", "locations")],
                    "chapters_hash": "def",
                }
            return None

        extractor.storage_service.storage.get_cache = AsyncMock(
            side_effect=_get_cache
        )

        result = asyncio.run(
            extractor.find_latest_context_base(
                project_id="incremental_proj",
                chapters=chapters,
                current_chapter=current_chapter,
            )
        )

        assert result is not None
        base_chapter, base_entries = result
        assert base_chapter.id == "ch_1"
        assert base_entries[0].uid == "loc_3"

    def test_find_latest_context_base_excludes_current(self) -> None:
        """当前章节自身不作为基准（从前一章开始往前扫）。"""
        extractor = _make_extractor()
        _, chapters = _make_project_with_chapters(3)
        current_chapter = chapters[2]

        # 仅当前章（ch_2）有缓存 → 不应命中（基准不含当前章）
        cache_key_2 = extractor._build_cache_key("incremental_proj", "ch_2")
        extractor.storage_service.storage.get_cache = AsyncMock(
            side_effect=lambda key: {
                "entries": [_entry_dict("char_1")],
                "chapters_hash": "abc",
            }
            if key == cache_key_2
            else None
        )

        result = asyncio.run(
            extractor.find_latest_context_base(
                project_id="incremental_proj",
                chapters=chapters,
                current_chapter=current_chapter,
            )
        )

        assert result is None


# ===== 2. _build_incremental_prompt 占位符填充 =====


class TestBuildIncrementalPrompt:
    """_build_incremental_prompt 占位符填充测试。"""

    def test_build_incremental_prompt_placeholders(self) -> None:
        """全部占位符被填充，含 {{existing_entries}} 基准条目序列化。"""
        from novelforge.models import ContextEntry

        extractor = _make_extractor()
        project, chapters = _make_project_with_chapters(3)
        base_entries = [
            ContextEntry.model_validate(_entry_dict("char_1")),
            ContextEntry.model_validate(_entry_dict("loc_2", "locations")),
        ]
        chapters_text = chapters[1].title + "\n" + chapters[1].content
        config: dict[str, Any] = {"extractor_prompt_override": None}

        prompt = extractor._build_incremental_prompt(
            project=project,
            chapters_text=chapters_text,
            existing_entries=base_entries,
            config=config,
        )

        # 标题/作者/主角等档案占位符已替换
        assert "测试小说" in prompt
        assert "测试作者" in prompt
        # 章节文本已替换
        assert chapters[1].title in prompt
        # 基准条目序列化已注入
        assert "char_1" in prompt
        assert "loc_2" in prompt
        # 无未替换占位符
        assert "{{" not in prompt and "}}" not in prompt

    def test_build_incremental_prompt_override(self) -> None:
        """extractor_prompt_override 非空时使用覆盖模板。"""
        extractor = _make_extractor()
        project, chapters = _make_project_with_chapters(2)
        override = "增量模板：{{existing_entries}} | {{chapters_text}}"
        config: dict[str, Any] = {"extractor_prompt_override": override}

        prompt = extractor._build_incremental_prompt(
            project=project,
            chapters_text="章节内容",
            existing_entries=[],
            config=config,
        )

        assert prompt.startswith("增量模板：")
        assert "章节内容" in prompt

    def test_build_incremental_prompt_no_project(self) -> None:
        """project 为 None 时档案字段为空串，不崩溃。"""
        extractor = _make_extractor()
        config: dict[str, Any] = {"extractor_prompt_override": None}

        prompt = extractor._build_incremental_prompt(
            project=None,
            chapters_text="章节内容",
            existing_entries=[],
            config=config,
        )

        assert "章节内容" in prompt
        assert "{{" not in prompt and "}}" not in prompt


# ===== 3. 增量 delta 章节选择与缓存元数据 =====


class TestIncrementalDeltaChapters:
    """增量模式 delta 章节选择与缓存保存测试。"""

    def test_extract_streaming_incremental_delta_and_metadata(self) -> None:
        """delta 章节 = 基准之后到当前章；缓存元数据记录 incremental_base_index。"""
        from novelforge.models import ContextEntry

        extractor = _make_extractor()
        client = _IncrementalLLMClient()
        client.add_stream_response(json.dumps([_entry_dict("new_1")]))
        extractor._get_llm_client = lambda flow_key="": (client, "gpt-4o-mini")  # type: ignore[assignment]

        project, chapters = _make_project_with_chapters(5)
        current_chapter = chapters[4]
        base_entries = [ContextEntry.model_validate(_entry_dict("char_1"))]

        result = asyncio.run(
            extractor.extract_streaming(
                project=project,
                chapters=chapters,
                current_chapter=current_chapter,
                incremental_base=base_entries,
                incremental_base_index=1,  # 基准 = ch_1
            )
        )

        assert result.status == "completed"
        # 仅 delta 章节（ch_2..ch_4）被用于构建 prompt
        assert len(client.prompts) == 1
        assert "第2章" not in client.prompts[0]
        assert "第3章" in client.prompts[0]
        assert "第4章" in client.prompts[0]
        assert "第5章" in client.prompts[0]
        # 基准条目被注入 prompt（existing_entries）
        assert "char_1" in client.prompts[0]

        # 缓存元数据含 incremental_base_index
        call_args = extractor.storage_service.storage.set_cache.call_args
        saved_data = call_args.args[1]
        assert saved_data.get("incremental_base_index") == 1
        # 保存的 entries 为提取结果
        assert len(saved_data["entries"]) == 1

    def test_extract_current_not_after_base_returns_base(self) -> None:
        """当前章节不晚于基准章节时直接返回基准条目，不调 LLM。"""
        from novelforge.models import ContextEntry

        extractor = _make_extractor()
        client = _IncrementalLLMClient()
        extractor._get_llm_client = lambda flow_key="": (client, "gpt-4o-mini")  # type: ignore[assignment]

        _, chapters = _make_project_with_chapters(3)
        current_chapter = chapters[1]
        base_entries = [ContextEntry.model_validate(_entry_dict("char_1"))]

        result = asyncio.run(
            extractor.extract_streaming(
                project=None,
                chapters=chapters,
                current_chapter=current_chapter,
                incremental_base=base_entries,
                incremental_base_index=2,  # 基准 index 晚于当前章
            )
        )

        assert result.status == "completed"
        assert result.entries[0].uid == "char_1"
        assert client.stream_call_count == 0  # 未调用 LLM


# ===== 4. 增量多批次合并 =====


class TestIncrementalMultiBatch:
    """增量模式多批次提取与合并测试。"""

    def test_extract_streaming_incremental_multi_batch_merge(self) -> None:
        """小 token_limit 触发多批次拆分，且每批均携带基准条目。"""
        extractor = _make_extractor()
        client = _IncrementalLLMClient()
        # 3 个提取批次各一次响应（3 章 delta 各拆 1 批）
        for i in range(3):
            client.add_stream_response(json.dumps([_entry_dict(f"batch_{i}_uid")]))
        # 合并环节（_run_merge_entries）需一次响应
        client.add_stream_response(json.dumps([_entry_dict("merged_uid")]))
        extractor._get_llm_client = lambda flow_key="": (client, "gpt-4o-mini")  # type: ignore[assignment]

        from novelforge.models import ContextEntry

        project, chapters = _make_project_with_chapters(5)
        current_chapter = chapters[4]
        base_entries = [ContextEntry.model_validate(_entry_dict("base_uid"))]

        result = asyncio.run(
            extractor.extract_streaming(
                project=project,
                chapters=chapters,
                current_chapter=current_chapter,
                token_limit_override=10,  # 小 limit 触发拆分
                incremental_base=base_entries,
                incremental_base_index=1,
            )
        )

        assert result.status == "completed"
        # 提取批次 prompt（前 3 个）均携带基准条目（existing_entries）
        for prompt in client.prompts[:3]:
            assert "base_uid" in prompt
        # 至少 3 个提取批次 + 1 次合并
        assert client.stream_call_count >= 4
        # 合并后条目为最终结果
        assert result.entries[0].uid == "merged_uid"
        assert result.batch_count >= 3
        assert result.merged is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
