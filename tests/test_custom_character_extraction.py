"""自定义角色形象提取测试。

镜像 ``test_protagonist_extraction.py`` 的测试结构，覆盖自定义角色提取链路：

覆盖：
1. _filter_custom_character_dimensions 维度过滤与非 dict 值替换（委托 _filter_protagonist_dimensions）
2. _parse_custom_character_response JSON 解析 + markdown fence 去除 + 字段过滤
3. CUSTOM_CHARACTER_DIMENSIONS 常量含 8 大维度（= PROTAGONIST_DIMENSIONS）
4. CUSTOM_CHARACTER_* 常量与 protagonist 常量对齐
5. _build_custom_character_cache_key 格式含 project_id/chapter_id/character_name
6. extract_custom_character_streaming 单批次正常返回并保存到 custom_character: 独立缓存
7. extract_custom_character_streaming 多批次合并
8. extract_custom_character_streaming 缓存往返（load_cached_custom_character）
9. extract_custom_character_streaming 回调被调用
10. extract_custom_character_streaming LLM 失败返回 (None, 失败消息)
11. 自定义角色与主角缓存解耦（不同角色名/不同前缀互不覆盖）
12. UI 面板自定义角色按钮存在且信号正确发射
"""
from __future__ import annotations

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
    """构建 ContextExtractor 实例（带 mock StorageService 与 ConfigManager）。

    用于测试自定义角色提取链路。
    """
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


def _make_custom_character_project() -> tuple[Any, list[Any]]:
    """构建测试用 Project + Chapters（用于自定义角色提取）。"""
    from novelforge.models import Chapter, NovelProfile, Project

    profile = NovelProfile(
        title="测试小说",
        author="测试作者",
        protagonist="主角",
        synopsis="测试简介",
        world_setting="测试世界观",
        writing_style="测试风格",
    )
    project = Project(id="test_custom_proj", name="测试小说", novel_profile=profile)
    chapters = [
        Chapter(
            id="ch_0",
            project_id="test_custom_proj",
            index=0,
            title="第1章",
            content="章节内容",
            word_count=4,
        )
    ]
    return project, chapters


class _StreamChunk:
    """模拟 stream_chat_completion 产出的 chunk。"""

    def __init__(self, content: str, finish_reason: str | None = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason


class _CustomCharacterLLMClient:
    """模拟 LLM 客户端，支持 stream_chat_completion 成功/失败场景。

    按调用顺序依次返回 stream_responses/stream_errors 中的预设响应。
    """

    def __init__(self) -> None:
        self.stream_responses: list[list[_StreamChunk]] = []
        self.stream_errors: list[Exception | None] = []
        self._idx = 0
        self.stream_call_count = 0

    def add_stream_response(self, content: str) -> None:
        """追加一次成功的流式响应（单 chunk，content 为传入字符串）。"""
        self.stream_responses.append([_StreamChunk(content)])
        self.stream_errors.append(None)

    def add_stream_error(self, error: Exception) -> None:
        """追加一次抛错的流式响应（迭代开始即抛出）。"""
        self.stream_responses.append([])
        self.stream_errors.append(error)

    async def stream_chat_completion(self, **kwargs: Any) -> Any:
        """模拟 stream_chat_completion：按顺序返回预设响应或抛错。"""
        self.stream_call_count += 1
        idx = self._idx
        self._idx += 1
        if idx < len(self.stream_errors) and self.stream_errors[idx] is not None:
            raise self.stream_errors[idx]
        if idx < len(self.stream_responses):
            for chunk in self.stream_responses[idx]:
                yield chunk


# ===== 1. _filter_custom_character_dimensions 测试 =====


class TestFilterCustomCharacterDimensions:
    """_filter_custom_character_dimensions 维度过滤测试。"""

    def test_filter_custom_character_dimensions(self) -> None:
        """测试 8 维度被保留、非 dict 值替换为空 dict、额外字段被丢弃。"""
        from novelforge.services.context_extractor import (
            CUSTOM_CHARACTER_DIMENSIONS,
            _filter_custom_character_dimensions,
        )

        data: dict[str, Any] = {
            "basic_anchors": {"name": "林婉", "age": 25},
            "personality_system": {"big_five": {"O": 8, "C": 7}},
            "motivation_system": "这不是 dict 应被替换",  # 非 dict → 空 dict
            "emotion_defense": {"defense": "denial"},
            "behavior_fingerprint": None,  # None → 空 dict
            "relationship_coordinate": {"power": "subordinate"},
            "growth_arc": {"stage": "denial"},
            "ooc_redlines": {"forbidden": ["lie"]},
            # 额外字段应被丢弃
            "extra_field": {"should": "be dropped"},
            "another_extra": 123,
        }

        result = _filter_custom_character_dimensions(data)

        # 8 维度全部存在
        assert set(result.keys()) == set(CUSTOM_CHARACTER_DIMENSIONS)
        assert len(result) == 8

        # dict 值原样保留
        assert result["basic_anchors"] == {"name": "林婉", "age": 25}
        assert result["personality_system"] == {"big_five": {"O": 8, "C": 7}}
        assert result["emotion_defense"] == {"defense": "denial"}
        assert result["relationship_coordinate"] == {"power": "subordinate"}
        assert result["growth_arc"] == {"stage": "denial"}
        assert result["ooc_redlines"] == {"forbidden": ["lie"]}

        # 非 dict 值替换为空 dict
        assert result["motivation_system"] == {}
        assert result["behavior_fingerprint"] == {}

        # 额外字段被丢弃
        assert "extra_field" not in result
        assert "another_extra" not in result

    def test_filter_custom_character_dimensions_empty_input(self) -> None:
        """测试空字典输入返回 8 维度全空 dict。"""
        from novelforge.services.context_extractor import (
            CUSTOM_CHARACTER_DIMENSIONS,
            _filter_custom_character_dimensions,
        )

        result = _filter_custom_character_dimensions({})
        assert set(result.keys()) == set(CUSTOM_CHARACTER_DIMENSIONS)
        assert all(v == {} for v in result.values())

    def test_filter_custom_character_delegates_to_protagonist(self) -> None:
        """测试 _filter_custom_character_dimensions 委托给 _filter_protagonist_dimensions。"""
        from novelforge.services.context_extractor import (
            _filter_custom_character_dimensions,
            _filter_protagonist_dimensions,
        )

        data = {
            "basic_anchors": {"name": "test"},
            "extra": "should be dropped",
        }
        # 两个函数应返回相同结果
        assert _filter_custom_character_dimensions(data) == _filter_protagonist_dimensions(data)


# ===== 2. _parse_custom_character_response 测试 =====


class TestParseCustomCharacterResponse:
    """_parse_custom_character_response JSON 解析测试。"""

    def test_parse_custom_character_response(self) -> None:
        """测试 JSON 解析 + markdown fence 去除 + 字段过滤。"""
        from novelforge.services.context_extractor import (
            CUSTOM_CHARACTER_DIMENSIONS,
            _parse_custom_character_response,
        )

        # --- 场景 1：直接 JSON 对象 ---
        content_obj = json.dumps({
            "basic_anchors": {"name": "苏沐"},
            "personality_system": {"mbti": "INTJ"},
            "growth_arc": {"stage": "struggle"},
            "extra_field": "should be dropped",
        })
        result = _parse_custom_character_response(content_obj)
        assert set(result.keys()) == set(CUSTOM_CHARACTER_DIMENSIONS)
        assert result["basic_anchors"] == {"name": "苏沐"}
        assert result["personality_system"] == {"mbti": "INTJ"}
        assert result["growth_arc"] == {"stage": "struggle"}
        assert "extra_field" not in result
        # 未提供的维度为空 dict
        assert result["motivation_system"] == {}
        assert result["ooc_redlines"] == {}

        # --- 场景 2：带 ```json markdown fence ---
        content_fenced = (
            "```json\n"
            + json.dumps({
                "basic_anchors": {"name": "陆展"},
                "ooc_redlines": {"forbidden": ["kill"]},
            })
            + "\n```"
        )
        result_fenced = _parse_custom_character_response(content_fenced)
        assert result_fenced["basic_anchors"] == {"name": "陆展"}
        assert result_fenced["ooc_redlines"] == {"forbidden": ["kill"]}
        assert set(result_fenced.keys()) == set(CUSTOM_CHARACTER_DIMENSIONS)

        # --- 场景 3：带普通 ``` fence ---
        content_plain_fence = (
            "```\n"
            + json.dumps({"growth_arc": {"stage": "epiphany"}})
            + "\n```"
        )
        result_plain = _parse_custom_character_response(content_plain_fence)
        assert result_plain["growth_arc"] == {"stage": "epiphany"}
        assert set(result_plain.keys()) == set(CUSTOM_CHARACTER_DIMENSIONS)

    def test_parse_custom_character_response_invalid_raises(self) -> None:
        """测试无效 JSON 抛出 JSONDecodeError。"""
        from novelforge.services.context_extractor import (
            _parse_custom_character_response,
        )

        with pytest.raises(json.JSONDecodeError):
            _parse_custom_character_response("not a json at all")

    def test_parse_custom_character_delegates_to_protagonist(self) -> None:
        """测试 _parse_custom_character_response 委托给 _parse_protagonist_response。"""
        from novelforge.services.context_extractor import (
            _parse_custom_character_response,
            _parse_protagonist_response,
        )

        content = json.dumps({"basic_anchors": {"name": "test"}})
        assert _parse_custom_character_response(content) == _parse_protagonist_response(content)


# ===== 3. CUSTOM_CHARACTER_DIMENSIONS 常量测试 =====


class TestCustomCharacterDimensionsConstant:
    """CUSTOM_CHARACTER_DIMENSIONS 常量测试。"""

    def test_custom_character_dimensions_constant(self) -> None:
        """测试 CUSTOM_CHARACTER_DIMENSIONS 含 8 大预期维度（= PROTAGONIST_DIMENSIONS）。"""
        from novelforge.services.context_extractor import (
            CUSTOM_CHARACTER_DIMENSIONS,
            PROTAGONIST_DIMENSIONS,
        )

        expected = (
            "basic_anchors",
            "personality_system",
            "motivation_system",
            "emotion_defense",
            "behavior_fingerprint",
            "relationship_coordinate",
            "growth_arc",
            "ooc_redlines",
        )

        assert isinstance(CUSTOM_CHARACTER_DIMENSIONS, tuple)
        assert len(CUSTOM_CHARACTER_DIMENSIONS) == 8
        assert CUSTOM_CHARACTER_DIMENSIONS == expected
        # 自定义角色维度应与主角维度完全一致
        assert CUSTOM_CHARACTER_DIMENSIONS == PROTAGONIST_DIMENSIONS
        # 确保顺序一致
        for i, dim in enumerate(expected):
            assert CUSTOM_CHARACTER_DIMENSIONS[i] == dim


# ===== 4. CUSTOM_CHARACTER_* 常量对齐测试 =====


class TestCustomCharacterConstantsAlignment:
    """自定义角色常量与主角常量对齐测试（镜像设计决策）。"""

    def test_constants_alignment(self) -> None:
        """测试自定义角色提取常量与主角提取常量对齐。"""
        from novelforge.services.context_extractor import (
            CUSTOM_CHARACTER_CACHE_KEY_PREFIX,
            CUSTOM_CHARACTER_EXTRACT_MAX_TOKENS,
            CUSTOM_CHARACTER_EXTRACT_TEMPERATURE,
            CUSTOM_CHARACTER_EXTRACT_TEMPERATURE_RETRY,
            CUSTOM_CHARACTER_MERGE_TEMPERATURE,
            CUSTOM_CHARACTER_MERGE_TEMPERATURE_RETRY,
            PROTAGONIST_CACHE_KEY_PREFIX,
            PROTAGONIST_EXTRACT_MAX_TOKENS,
            PROTAGONIST_EXTRACT_TEMPERATURE,
            PROTAGONIST_EXTRACT_TEMPERATURE_RETRY,
            PROTAGONIST_MERGE_TEMPERATURE,
            PROTAGONIST_MERGE_TEMPERATURE_RETRY,
        )

        # 缓存 key 前缀必须不同（解耦）
        assert CUSTOM_CHARACTER_CACHE_KEY_PREFIX == "custom_character"
        assert CUSTOM_CHARACTER_CACHE_KEY_PREFIX != PROTAGONIST_CACHE_KEY_PREFIX

        # max_tokens 对齐
        assert CUSTOM_CHARACTER_EXTRACT_MAX_TOKENS == PROTAGONIST_EXTRACT_MAX_TOKENS == 16000

        # 温度对齐
        assert CUSTOM_CHARACTER_EXTRACT_TEMPERATURE == PROTAGONIST_EXTRACT_TEMPERATURE
        assert (
            CUSTOM_CHARACTER_EXTRACT_TEMPERATURE_RETRY
            == PROTAGONIST_EXTRACT_TEMPERATURE_RETRY
        )
        assert CUSTOM_CHARACTER_MERGE_TEMPERATURE == PROTAGONIST_MERGE_TEMPERATURE
        assert (
            CUSTOM_CHARACTER_MERGE_TEMPERATURE_RETRY
            == PROTAGONIST_MERGE_TEMPERATURE_RETRY
        )


# ===== 5. _build_custom_character_cache_key 测试 =====


class TestBuildCustomCharacterCacheKey:
    """_build_custom_character_cache_key 缓存 key 构建测试。"""

    def test_build_cache_key_format(self) -> None:
        """测试缓存 key 格式：custom_character:{project_id}:{chapter_id}:{character_name}。"""
        extractor = _make_extractor()

        cache_key = extractor._build_custom_character_cache_key(
            "proj_001", "chap_002", "林婉"
        )
        assert cache_key == "custom_character:proj_001:chap_002:林婉"
        assert cache_key.startswith("custom_character:")

    def test_build_cache_key_different_characters_decoupled(self) -> None:
        """测试同章节不同角色名的缓存 key 互不相同（互不覆盖）。"""
        extractor = _make_extractor()

        key1 = extractor._build_custom_character_cache_key("p1", "c1", "林婉")
        key2 = extractor._build_custom_character_cache_key("p1", "c1", "苏沐")
        key3 = extractor._build_custom_character_cache_key("p1", "c1", "陆展")

        assert len({key1, key2, key3}) == 3
        assert key1 != key2
        assert key2 != key3
        assert key1 != key3

    def test_build_cache_key_different_from_protagonist(self) -> None:
        """测试自定义角色缓存 key 与主角缓存 key 前缀不同（解耦）。"""
        from novelforge.services.context_extractor import (
            CUSTOM_CHARACTER_CACHE_KEY_PREFIX,
            PROTAGONIST_CACHE_KEY_PREFIX,
        )

        extractor = _make_extractor()

        custom_key = extractor._build_custom_character_cache_key("p1", "c1", "林婉")
        # 主角 key 格式为 protagonist:{project_id}:{chapter_id}
        protagonist_key = f"{PROTAGONIST_CACHE_KEY_PREFIX}:p1:c1"

        # 前缀不同
        assert custom_key.startswith(f"{CUSTOM_CHARACTER_CACHE_KEY_PREFIX}:")
        assert protagonist_key.startswith(f"{PROTAGONIST_CACHE_KEY_PREFIX}:")
        assert not custom_key.startswith(f"{PROTAGONIST_CACHE_KEY_PREFIX}:")


# ===== 6. extract_custom_character_streaming 单批次测试 =====


class TestExtractCustomCharacterStreaming:
    """extract_custom_character_streaming 公共方法测试。"""

    def test_extract_custom_character_streaming_single_batch(self) -> None:
        """单批次正常返回 (ProtagonistProfile, status)，并保存到 custom_character: 独立缓存。"""
        import asyncio

        from novelforge.models import ProtagonistProfile

        extractor = _make_extractor()
        client = _CustomCharacterLLMClient()
        profile_json = json.dumps(
            {"basic_anchors": {"name": "林婉"}}, ensure_ascii=False
        )
        client.add_stream_response(profile_json)
        extractor._get_llm_client = lambda flow_key="": (client, "gpt-4o-mini")  # type: ignore[assignment]

        project, chapters = _make_custom_character_project()
        current_chapter = chapters[0]

        profile, status = asyncio.run(
            extractor.extract_custom_character_streaming(
                project=project,
                chapters=chapters,
                current_chapter=current_chapter,
                character_name="林婉",
            )
        )

        assert profile is not None
        assert isinstance(profile, ProtagonistProfile)
        assert profile.basic_anchors == {"name": "林婉"}
        assert "完成" in status
        # 验证保存到独立缓存（key 含 custom_character: 前缀）
        extractor.storage_service.storage.set_cache.assert_called_once()
        call_args = extractor.storage_service.storage.set_cache.call_args
        cache_key = call_args.args[0]
        assert cache_key.startswith("custom_character:")
        # key 必须含角色名
        assert "林婉" in cache_key
        saved_data = call_args.args[1]
        assert "protagonist_profile" in saved_data
        # 保存的角色名也应正确
        assert saved_data.get("character_name") == "林婉"

    def test_extract_custom_character_streaming_multi_batch_merge(self) -> None:
        """小 token_limit 触发多批次合并。"""
        import asyncio

        from novelforge.models import Chapter, NovelProfile, Project

        extractor = _make_extractor()
        client = _CustomCharacterLLMClient()
        # 3 个批次各一次响应
        for i in range(3):
            client.add_stream_response(
                json.dumps(
                    {"basic_anchors": {"name": f"林婉{i}"}}, ensure_ascii=False
                )
            )
        # 合并环节（_run_custom_character_merge）也需一次响应
        client.add_stream_response(
            json.dumps(
                {"basic_anchors": {"name": "合并后林婉"}}, ensure_ascii=False
            )
        )
        extractor._get_llm_client = lambda flow_key="": (client, "gpt-4o-mini")  # type: ignore[assignment]

        profile = NovelProfile(
            title="测试",
            author="作者",
            protagonist="主角",
            synopsis="简介",
            world_setting="世界观",
            writing_style="风格",
        )
        project = Project(id="multi_custom_proj", name="测试", novel_profile=profile)
        chapters = [
            Chapter(
                id=f"ch_{i}",
                project_id="multi_custom_proj",
                index=i,
                title=f"第{i + 1}章",
                content=f"内容{i}" * 100,
                word_count=200,
            )
            for i in range(3)
        ]
        current_chapter = chapters[-1]

        prof, status = asyncio.run(
            extractor.extract_custom_character_streaming(
                project=project,
                chapters=chapters,
                current_chapter=current_chapter,
                character_name="林婉",
                token_limit=10,  # 小 limit 触发拆分
            )
        )

        assert prof is not None
        assert "完成" in status
        # 应有多批次调用（>= 3）+ 合并环节调用
        assert client.stream_call_count >= 3

    def test_extract_custom_character_streaming_load_cached_roundtrip(self) -> None:
        """提取后 load_cached_custom_character 能加载到保存的 profile。"""
        import asyncio

        extractor = _make_extractor()
        client = _CustomCharacterLLMClient()
        profile_json = json.dumps(
            {"basic_anchors": {"name": "苏沐"}, "growth_arc": {"stage": "denial"}},
            ensure_ascii=False,
        )
        client.add_stream_response(profile_json)
        extractor._get_llm_client = lambda flow_key="": (client, "gpt-4o-mini")  # type: ignore[assignment]

        project, chapters = _make_custom_character_project()
        current_chapter = chapters[0]

        # 第一次：提取并保存
        profile, _ = asyncio.run(
            extractor.extract_custom_character_streaming(
                project=project,
                chapters=chapters,
                current_chapter=current_chapter,
                character_name="苏沐",
            )
        )
        assert profile is not None

        # 模拟从存储读回：把 set_cache 收到的数据回填给 get_cache
        saved_call = extractor.storage_service.storage.set_cache.call_args
        saved_data = saved_call.args[1]
        extractor.storage_service.storage.get_cache = AsyncMock(return_value=saved_data)

        # 通过 load_cached_custom_character 加载
        loaded = asyncio.run(
            extractor.load_cached_custom_character(
                project.id, current_chapter.id, "苏沐"
            )
        )
        assert loaded is not None
        assert "protagonist_profile" in loaded
        assert loaded["protagonist_profile"]["basic_anchors"] == {"name": "苏沐"}
        # 角色名应被持久化
        assert loaded.get("character_name") == "苏沐"

    def test_extract_custom_character_streaming_callbacks_invoked(self) -> None:
        """on_chunk 回调在流式提取中被调用。"""
        import asyncio

        extractor = _make_extractor()
        client = _CustomCharacterLLMClient()
        profile_json = json.dumps(
            {"basic_anchors": {"name": "陆展"}}, ensure_ascii=False
        )
        client.add_stream_response(profile_json)
        extractor._get_llm_client = lambda flow_key="": (client, "gpt-4o-mini")  # type: ignore[assignment]

        project, chapters = _make_custom_character_project()
        current_chapter = chapters[0]

        chunks_received: list[str] = []
        batches_received: list[tuple[int, int]] = []

        def on_chunk(text: str) -> None:
            chunks_received.append(text)

        def on_batch_complete(idx: int, total: int) -> None:
            batches_received.append((idx, total))

        profile, _ = asyncio.run(
            extractor.extract_custom_character_streaming(
                project=project,
                chapters=chapters,
                current_chapter=current_chapter,
                character_name="陆展",
                on_chunk=on_chunk,
                on_batch_complete=on_batch_complete,
            )
        )

        assert profile is not None
        # on_chunk 在流式 chunk 接收时被调用（至少 1 次）
        assert len(chunks_received) >= 1

    def test_extract_custom_character_streaming_failure_returns_none(self) -> None:
        """LLM 2 次重试均失败 → 返回 (None, 失败消息)。"""
        import asyncio

        from novelforge.services.llm_client import LLMError

        extractor = _make_extractor()
        client = _CustomCharacterLLMClient()
        # 2 次失败（首次 + 重试 = 2 次）
        client.add_stream_error(LLMError("首次失败"))
        client.add_stream_error(LLMError("重试也失败"))
        extractor._get_llm_client = lambda flow_key="": (client, "gpt-4o-mini")  # type: ignore[assignment]

        project, chapters = _make_custom_character_project()
        current_chapter = chapters[0]

        profile, status = asyncio.run(
            extractor.extract_custom_character_streaming(
                project=project,
                chapters=chapters,
                current_chapter=current_chapter,
                character_name="陆展",
            )
        )

        assert profile is None
        assert "失败" in status
        # 2 次重试均调用
        assert client.stream_call_count == 2


# ===== 7. 自定义角色与主角缓存解耦测试 =====


class TestCustomCharacterCacheDecoupling:
    """自定义角色与主角缓存解耦测试。"""

    def test_custom_character_cache_does_not_overwrite_protagonist(self) -> None:
        """同章节提取主角和自定义角色后，两者写入不同的缓存 key。"""
        import asyncio

        extractor = _make_extractor()

        # --- 自定义角色提取 ---
        custom_client = _CustomCharacterLLMClient()
        custom_client.add_stream_response(
            json.dumps({"basic_anchors": {"name": "林婉"}}, ensure_ascii=False)
        )
        extractor._get_llm_client = lambda flow_key="": (custom_client, "gpt-4o-mini")  # type: ignore[assignment]

        project, chapters = _make_custom_character_project()
        current_chapter = chapters[0]

        custom_profile, _ = asyncio.run(
            extractor.extract_custom_character_streaming(
                project=project,
                chapters=chapters,
                current_chapter=current_chapter,
                character_name="林婉",
            )
        )
        assert custom_profile is not None

        # 收集自定义角色写入的缓存 key
        custom_cache_keys = [
            call.args[0]
            for call in extractor.storage_service.storage.set_cache.call_args_list
        ]
        # 全部应以 custom_character: 开头
        assert all(k.startswith("custom_character:") for k in custom_cache_keys)
        # 不应有 protagonist: 前缀
        assert not any(k.startswith("protagonist:") for k in custom_cache_keys)

    def test_different_character_names_independent_cache(self) -> None:
        """同章节提取两个不同自定义角色，写入两个不同缓存 key。"""
        import asyncio

        extractor = _make_extractor()

        for name in ["林婉", "苏沐"]:
            client = _CustomCharacterLLMClient()
            client.add_stream_response(
                json.dumps(
                    {"basic_anchors": {"name": name}}, ensure_ascii=False
                )
            )
            extractor._get_llm_client = lambda flow_key="", _c=client: (_c, "gpt-4o-mini")  # type: ignore[assignment]

            project, chapters = _make_custom_character_project()
            current_chapter = chapters[0]

            profile, _ = asyncio.run(
                extractor.extract_custom_character_streaming(
                    project=project,
                    chapters=chapters,
                    current_chapter=current_chapter,
                    character_name=name,
                )
            )
            assert profile is not None

        # 两次提取应写入两个不同的缓存 key
        all_cache_keys = [
            call.args[0]
            for call in extractor.storage_service.storage.set_cache.call_args_list
        ]
        custom_keys = [k for k in all_cache_keys if k.startswith("custom_character:")]
        # 至少 2 个独立 key
        assert len(set(custom_keys)) >= 2
        # 两个 key 都应含对应角色名
        assert any("林婉" in k for k in custom_keys)
        assert any("苏沐" in k for k in custom_keys)


# ===== 8. UI 面板自定义角色按钮测试 =====


class TestContextPanelCustomCharacterButtons:
    """ContextPreviewPanel 自定义角色按钮 UI 测试。"""

    def test_custom_character_buttons_exist(self) -> None:
        """提取/查看自定义角色按钮存在且 objectName 正确。"""
        from PySide6.QtWidgets import QApplication

        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        app = QApplication.instance() or QApplication([])
        panel = ContextPreviewPanel()

        assert panel._extract_custom_character_btn.text() == "提取自定义角色"
        assert panel._extract_custom_character_btn.objectName() == "primaryBtn"
        assert panel._view_custom_character_btn.text() == "查看自定义角色"
        assert panel._view_custom_character_btn.objectName() == "secondaryBtn"

    def test_extract_custom_character_clicked_emits_signal(self) -> None:
        """点击提取按钮发射 extract_custom_character_requested 信号。"""
        from PySide6.QtTest import QSignalSpy
        from PySide6.QtWidgets import QApplication

        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        app = QApplication.instance() or QApplication([])
        panel = ContextPreviewPanel()
        spy = QSignalSpy(panel.extract_custom_character_requested)

        panel._on_extract_custom_character_clicked()

        assert spy.count() == 1

    def test_view_custom_character_clicked_emits_signal(self) -> None:
        """点击查看按钮发射 view_custom_character_requested 信号。"""
        from PySide6.QtTest import QSignalSpy
        from PySide6.QtWidgets import QApplication

        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        app = QApplication.instance() or QApplication([])
        panel = ContextPreviewPanel()
        spy = QSignalSpy(panel.view_custom_character_requested)

        panel._on_view_custom_character_clicked()

        assert spy.count() == 1

    def test_start_custom_character_extraction_disables_buttons(self) -> None:
        """start_custom_character_extraction 禁用提取按钮。"""
        from PySide6.QtWidgets import QApplication

        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        app = QApplication.instance() or QApplication([])
        panel = ContextPreviewPanel()

        panel.start_custom_character_extraction()

        assert panel._extract_custom_character_btn.isEnabled() is False

    def test_finish_custom_character_extraction_enables_buttons(self) -> None:
        """finish_custom_character_extraction 恢复提取按钮可用。"""
        from PySide6.QtWidgets import QApplication

        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        app = QApplication.instance() or QApplication([])
        panel = ContextPreviewPanel()

        panel.start_custom_character_extraction()
        assert panel._extract_custom_character_btn.isEnabled() is False

        panel.finish_custom_character_extraction("completed")
        assert panel._extract_custom_character_btn.isEnabled() is True


# ===== 9. flow_key 注册测试 =====


class TestCustomCharacterFlowKeyRegistration:
    """自定义角色提取 flow_key 注册测试。"""

    def test_flow_key_in_flow_definitions(self) -> None:
        """flow_endpoint_dialog 中 FLOW_DEFINITIONS 含 custom_character_extraction。"""
        from novelforge.ui.flow_endpoint_dialog import FLOW_DEFINITIONS

        flow_keys = [fk for fk, _ in FLOW_DEFINITIONS]
        assert "custom_character_extraction" in flow_keys

    def test_flow_key_default_jailbreak_low(self) -> None:
        """config.FLOW_DEFAULT_JAILBREAKS 中 custom_character_extraction 默认为 low。"""
        from novelforge.core.config import FLOW_DEFAULT_JAILBREAKS

        assert FLOW_DEFAULT_JAILBREAKS.get("custom_character_extraction") == "low"

    def test_jailbreak_template_file_exists(self) -> None:
        """jb_custom_character_extraction.txt 越狱模板文件存在。"""
        from novelforge.utils.paths import get_resource_path

        jb_path = get_resource_path(
            "defaults", "jailbreaks", "jb_custom_character_extraction.txt"
        )
        assert jb_path.exists(), f"越狱模板文件不存在: {jb_path}"

    def test_prompt_template_files_exist(self) -> None:
        """extract_custom_character_prompt.txt / merge_prompt.txt 提示词模板文件存在。"""
        from novelforge.utils.paths import (
            get_extract_custom_character_merge_prompt_path,
            get_extract_custom_character_prompt_path,
        )

        prompt_path = get_extract_custom_character_prompt_path()
        merge_path = get_extract_custom_character_merge_prompt_path()
        assert prompt_path.exists(), f"提示词模板文件不存在: {prompt_path}"
        assert merge_path.exists(), f"合并提示词模板文件不存在: {merge_path}"

    def test_prompt_template_contains_character_name_placeholder(self) -> None:
        """提示词模板含 {{character_name}} 占位符。"""
        from novelforge.utils.paths import (
            get_extract_custom_character_merge_prompt_path,
            get_extract_custom_character_prompt_path,
        )

        prompt_text = get_extract_custom_character_prompt_path().read_text(
            encoding="utf-8"
        )
        merge_text = get_extract_custom_character_merge_prompt_path().read_text(
            encoding="utf-8"
        )
        assert "{{character_name}}" in prompt_text, "提取模板缺 {{character_name}} 占位符"
        assert "{{character_name}}" in merge_text, "合并模板缺 {{character_name}} 占位符"


# ===== 10. Chapter.custom_characters 字段测试 =====


class TestChapterCustomCharactersField:
    """Chapter.custom_characters 字段测试。"""

    def test_custom_characters_default_empty_dict(self) -> None:
        """Chapter.custom_characters 默认为空 dict。"""
        from novelforge.models import Chapter

        chapter = Chapter(
            id="ch_test",
            project_id="p_test",
            index=0,
            title="测试章节",
            content="内容",
            word_count=2,
        )
        assert chapter.custom_characters == {}
        assert isinstance(chapter.custom_characters, dict)

    def test_custom_characters_can_hold_multiple_profiles(self) -> None:
        """custom_characters 可存储多个角色的 ProtagonistProfile。"""
        from novelforge.models import Chapter, ProtagonistProfile

        chapter = Chapter(
            id="ch_test",
            project_id="p_test",
            index=0,
            title="测试章节",
            content="内容",
            word_count=2,
        )

        profile1 = ProtagonistProfile(basic_anchors={"name": "林婉"})
        profile2 = ProtagonistProfile(basic_anchors={"name": "苏沐"})
        chapter.custom_characters = {"林婉": profile1, "苏沐": profile2}

        assert len(chapter.custom_characters) == 2
        assert chapter.custom_characters["林婉"].basic_anchors == {"name": "林婉"}
        assert chapter.custom_characters["苏沐"].basic_anchors == {"name": "苏沐"}

    def test_custom_characters_roundtrip(self) -> None:
        """custom_characters 序列化/反序列化往返不丢字段。"""
        from novelforge.models import Chapter, ProtagonistProfile

        original = Chapter(
            id="ch_test",
            project_id="p_test",
            index=0,
            title="测试章节",
            content="内容",
            word_count=2,
        )
        original.custom_characters = {
            "林婉": ProtagonistProfile(
                basic_anchors={"name": "林婉"},
                growth_arc={"stage": "denial"},
            ),
        }

        dumped = original.model_dump(mode="json")
        restored = Chapter.model_validate(dumped)

        assert "林婉" in restored.custom_characters
        assert restored.custom_characters["林婉"].basic_anchors == {"name": "林婉"}
        assert restored.custom_characters["林婉"].growth_arc == {"stage": "denial"}


if __name__ == "__main__":
    # 直接运行时执行所有测试
    pytest.main([__file__, "-v", "--tb=short"])
