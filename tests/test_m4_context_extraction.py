"""M4 里程碑自动上下文提取测试。

覆盖：
1. ContextEntry 模型字段
2. ST 世界书导入（position 转换、probability 忽略、source_chapter_range=None）
3. 提取提示词模板填充
4. JSON 解析与修复（去除 markdown 标记）
5. content 截断 200 字
6. 章节数不足 N 时取所有可用
7. 0 章时跳过提取
8. 缓存命中/未命中
9. force_refresh 跳过缓存
10. 提取失败处理
11. 非流式 LLM 调用
12. ExtractResult 数据类
13. 章节哈希计算
14. ContextPreviewPanel UI
15. ExtractionDialog UI
16. ContinuationWorker extracted_context_snapshot 参数
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

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
    extract_config: dict | None = None,
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
        extract_config=extract_config,
    )


class _StreamChunk:
    """模拟 stream_chat_completion 产出的 chunk。"""
    def __init__(self, content: str, finish_reason: str | None = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason


# ===== 1. ContextEntry 模型字段测试 =====


class TestContextEntryModel:
    """ContextEntry 模型字段测试。"""

    def test_context_entry_default_values(self) -> None:
        """测试 ContextEntry 默认值。"""
        from novelforge.models import ContextEntry

        entry = ContextEntry(uid="test_1")
        assert entry.uid == "test_1"
        assert entry.category == "characters"
        assert entry.key == []
        assert entry.comment == ""
        assert entry.content == ""
        assert entry.order == 100
        assert entry.position == "before"
        assert entry.depth == 4
        assert entry.role == "system"
        assert entry.source_chapter_range is None
        assert entry.extracted_at is None
        assert entry.raw_st_fields == {}

    def test_context_entry_with_all_fields(self) -> None:
        """测试 ContextEntry 设置所有字段。"""
        from novelforge.models import ContextEntry

        now = datetime.now()
        entry = ContextEntry(
            uid="char_1",
            category="characters",
            key=["主角", "hero"],
            comment="主角信息",
            content="主角是位勇敢的战士",
            order=50,
            position="at_depth",
            depth=2,
            role="system",
            source_chapter_range=(0, 4),
            extracted_at=now,
        )
        assert entry.uid == "char_1"
        assert entry.category == "characters"
        assert entry.key == ["主角", "hero"]
        assert entry.comment == "主角信息"
        assert entry.content == "主角是位勇敢的战士"
        assert entry.order == 50
        assert entry.position == "at_depth"
        assert entry.depth == 2
        assert entry.source_chapter_range == (0, 4)
        assert entry.extracted_at == now

    def test_context_entry_raw_st_fields_alias(self) -> None:
        """测试 ContextEntry 通过 _raw_st_fields 别名设置。"""
        from novelforge.models import ContextEntry

        entry = ContextEntry(
            uid="test_2",
            _raw_st_fields={"custom_field": "value"},
        )
        assert entry.raw_st_fields == {"custom_field": "value"}

    def test_context_entry_no_probability_field(self) -> None:
        """测试 ContextEntry 不包含 probability 字段。"""
        from novelforge.models import ContextEntry

        entry = ContextEntry(uid="test_3")
        # probability 字段不应存在于模型中
        assert not hasattr(entry, "probability")

    def test_valid_categories_constant(self) -> None:
        """测试 VALID_CATEGORIES 常量。"""
        from novelforge.models import VALID_CATEGORIES

        assert "characters" in VALID_CATEGORIES
        assert "locations" in VALID_CATEGORIES
        assert "events" in VALID_CATEGORIES
        assert "style" in VALID_CATEGORIES
        assert "plot_state" in VALID_CATEGORIES

    def test_valid_positions_constant(self) -> None:
        """测试 VALID_POSITIONS 常量。"""
        from novelforge.models import VALID_POSITIONS

        assert "before" in VALID_POSITIONS
        assert "after" in VALID_POSITIONS
        assert "at_depth" in VALID_POSITIONS


# ===== 2. ST 世界书导入测试 =====


class TestWorldbookImporter:
    """ST 世界书导入测试。"""

    def test_import_worldbook_dict_format(self, tmp_path: Path) -> None:
        """测试导入 dict 格式的世界书。"""
        from novelforge.services.worldbook_importer import import_worldbook

        worldbook = {
            "entries": {
                "0": {
                    "uid": 0,
                    "key": ["主角"],
                    "comment": "人物信息",
                    "content": "主角是位战士",
                    "order": 100,
                    "position": 0,
                    "depth": 4,
                    "role": "system",
                    "probability": 100,
                }
            }
        }
        path = tmp_path / "worldbook.json"
        path.write_text(json.dumps(worldbook, ensure_ascii=False), encoding="utf-8")

        entries = import_worldbook(path)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.uid == "0"
        assert entry.category == "characters"  # comment 含"人物"
        assert entry.content == "主角是位战士"
        assert entry.position == "before"  # 0 → before
        assert entry.source_chapter_range is None  # 导入条目

    def test_import_worldbook_list_format(self, tmp_path: Path) -> None:
        """测试导入 list 格式的世界书。"""
        from novelforge.services.worldbook_importer import import_worldbook

        worldbook = {
            "entries": [
                {
                    "uid": "char_1",
                    "key": ["hero"],
                    "comment": "character info",
                    "content": "Hero is brave",
                    "position": 0,
                },
                {
                    "uid": "loc_1",
                    "key": ["castle"],
                    "comment": "location info",
                    "content": "A big castle",
                    "position": 1,
                },
            ]
        }
        path = tmp_path / "worldbook.json"
        path.write_text(json.dumps(worldbook, ensure_ascii=False), encoding="utf-8")

        entries = import_worldbook(path)
        assert len(entries) == 2
        assert entries[0].category == "characters"  # comment 含 "character"
        assert entries[1].category == "locations"  # comment 含 "location"
        assert entries[0].position == "before"
        assert entries[1].position == "after"

    def test_import_worldbook_position_conversion(self, tmp_path: Path) -> None:
        """测试 position 数字→字符串转换（ST 8 种 position 归并到赓笔 3 种）。

        ST position 枚举：before=0, after=1, ANTop=2, ANBottom=3,
        atDepth=4, EMTop=5, EMBottom=6, outlet=7。
        赓笔归并策略：
        - 0/2/5 → before
        - 1/3/6/7 → after
        - 4 → at_depth
        """
        from novelforge.services.worldbook_importer import import_worldbook

        worldbook = {
            "entries": [
                {"uid": "e1", "content": "c1", "position": 0},   # before
                {"uid": "e2", "content": "c2", "position": 1},   # after
                {"uid": "e3", "content": "c3", "position": 2},   # ANTop → before
                {"uid": "e4", "content": "c4", "position": 3},   # ANBottom → after
                {"uid": "e5", "content": "c5", "position": 4},   # atDepth → at_depth
                {"uid": "e6", "content": "c6", "position": 5},   # EMTop → before
                {"uid": "e7", "content": "c7", "position": 6},   # EMBottom → after
                {"uid": "e8", "content": "c8", "position": 7},   # outlet → after
                {"uid": "e9", "content": "c9", "position": None},  # 默认 before
            ]
        }
        path = tmp_path / "worldbook.json"
        path.write_text(json.dumps(worldbook), encoding="utf-8")

        entries = import_worldbook(path)
        assert entries[0].position == "before"   # 0
        assert entries[1].position == "after"     # 1
        assert entries[2].position == "before"    # 2 ANTop
        assert entries[3].position == "after"     # 3 ANBottom
        assert entries[4].position == "at_depth"  # 4 atDepth
        assert entries[5].position == "before"    # 5 EMTop
        assert entries[6].position == "after"     # 6 EMBottom
        assert entries[7].position == "after"     # 7 outlet
        assert entries[8].position == "before"    # None 默认

    def test_import_worldbook_probability_ignored(self, tmp_path: Path) -> None:
        """测试 probability 字段被忽略。"""
        from novelforge.services.worldbook_importer import import_worldbook

        worldbook = {
            "entries": [
                {
                    "uid": "e1",
                    "content": "c1",
                    "probability": 50,
                    "position": 0,
                }
            ]
        }
        path = tmp_path / "worldbook.json"
        path.write_text(json.dumps(worldbook), encoding="utf-8")

        entries = import_worldbook(path)
        assert len(entries) == 1
        # probability 不应出现在 raw_st_fields 中
        assert "probability" not in entries[0].raw_st_fields

    def test_import_worldbook_source_chapter_range_none(self, tmp_path: Path) -> None:
        """测试导入条目的 source_chapter_range 为 None。"""
        from novelforge.services.worldbook_importer import import_worldbook

        worldbook = {
            "entries": [
                {"uid": "e1", "content": "c1", "position": 0}
            ]
        }
        path = tmp_path / "worldbook.json"
        path.write_text(json.dumps(worldbook), encoding="utf-8")

        entries = import_worldbook(path)
        assert all(e.source_chapter_range is None for e in entries)

    def test_import_worldbook_category_inference(self, tmp_path: Path) -> None:
        """测试 category 推断（人物/地点/事件/风格/其他）。"""
        from novelforge.services.worldbook_importer import import_worldbook

        worldbook = {
            "entries": [
                {"uid": "e1", "comment": "人物信息", "content": "c1"},
                {"uid": "e2", "comment": "character", "content": "c2"},
                {"uid": "e3", "comment": "地点描述", "content": "c3"},
                {"uid": "e4", "comment": "location", "content": "c4"},
                {"uid": "e5", "comment": "事件记录", "content": "c5"},
                {"uid": "e6", "comment": "event", "content": "c6"},
                {"uid": "e7", "comment": "风格说明", "content": "c7"},
                {"uid": "e8", "comment": "style", "content": "c8"},
                {"uid": "e9", "comment": "其他", "content": "c9"},
            ]
        }
        path = tmp_path / "worldbook.json"
        path.write_text(json.dumps(worldbook, ensure_ascii=False), encoding="utf-8")

        entries = import_worldbook(path)
        assert entries[0].category == "characters"
        assert entries[1].category == "characters"
        assert entries[2].category == "locations"
        assert entries[3].category == "locations"
        assert entries[4].category == "events"
        assert entries[5].category == "events"
        assert entries[6].category == "style"
        assert entries[7].category == "style"
        assert entries[8].category == "plot_state"  # 默认

    def test_import_worldbook_unknown_fields_preserved(self, tmp_path: Path) -> None:
        """测试未识别字段保留在 _raw_st_fields。"""
        from novelforge.services.worldbook_importer import import_worldbook

        worldbook = {
            "entries": [
                {
                    "uid": "e1",
                    "content": "c1",
                    "position": 0,
                    "customField": "custom_value",
                    "anotherUnknown": 42,
                }
            ]
        }
        path = tmp_path / "worldbook.json"
        path.write_text(json.dumps(worldbook), encoding="utf-8")

        entries = import_worldbook(path)
        assert "customField" in entries[0].raw_st_fields
        assert entries[0].raw_st_fields["customField"] == "custom_value"
        assert "anotherUnknown" in entries[0].raw_st_fields

    def test_import_worldbook_empty_entries(self, tmp_path: Path) -> None:
        """测试空 entries 字段。"""
        from novelforge.services.worldbook_importer import import_worldbook

        worldbook = {"entries": {}}
        path = tmp_path / "worldbook.json"
        path.write_text(json.dumps(worldbook), encoding="utf-8")

        entries = import_worldbook(path)
        assert entries == []

    def test_import_worldbook_no_entries_field(self, tmp_path: Path) -> None:
        """测试无 entries 字段。"""
        from novelforge.services.worldbook_importer import import_worldbook

        worldbook = {"name": "test"}
        path = tmp_path / "worldbook.json"
        path.write_text(json.dumps(worldbook), encoding="utf-8")

        entries = import_worldbook(path)
        assert entries == []

    def test_import_worldbook_file_not_found(self) -> None:
        """测试文件不存在。"""
        from novelforge.services.worldbook_importer import import_worldbook

        with pytest.raises(FileNotFoundError):
            import_worldbook("/nonexistent/path/worldbook.json")

    def test_import_worldbook_invalid_json(self, tmp_path: Path) -> None:
        """测试无效 JSON。"""
        from novelforge.services.worldbook_importer import import_worldbook

        path = tmp_path / "worldbook.json"
        path.write_text("not a json", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            import_worldbook(path)

    def test_import_worldbook_invalid_format(self, tmp_path: Path) -> None:
        """测试顶层非 dict 格式。"""
        from novelforge.services.worldbook_importer import import_worldbook

        path = tmp_path / "worldbook.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")

        with pytest.raises(ValueError):
            import_worldbook(path)

    def test_import_worldbook_default_order(self, tmp_path: Path) -> None:
        """测试默认 order 为 100。"""
        from novelforge.services.worldbook_importer import import_worldbook

        worldbook = {
            "entries": [
                {"uid": "e1", "content": "c1", "position": 0}
            ]
        }
        path = tmp_path / "worldbook.json"
        path.write_text(json.dumps(worldbook), encoding="utf-8")

        entries = import_worldbook(path)
        assert entries[0].order == 100

    def test_import_worldbook_default_depth(self, tmp_path: Path) -> None:
        """测试默认 depth 为 4。"""
        from novelforge.services.worldbook_importer import import_worldbook

        worldbook = {
            "entries": [
                {"uid": "e1", "content": "c1", "position": 2}
            ]
        }
        path = tmp_path / "worldbook.json"
        path.write_text(json.dumps(worldbook), encoding="utf-8")

        entries = import_worldbook(path)
        assert entries[0].depth == 4


# ===== 3. 提取提示词模板填充测试 =====


class TestPromptTemplateFilling:
    """提取提示词模板填充测试。"""

    def test_default_prompt_template_loaded(self) -> None:
        """测试默认提示词模板加载。"""
        from novelforge.services.context_extractor import ContextExtractor
        from novelforge.utils.paths import get_extract_prompt_path

        # 直接读取文件验证占位符存在
        template = get_extract_prompt_path().read_text(encoding="utf-8")
        assert "{{title}}" in template
        assert "{{author}}" in template
        assert "{{protagonist}}" in template
        assert "{{synopsis}}" in template
        assert "{{world_setting}}" in template
        assert "{{writing_style}}" in template
        assert "{{chapters_text}}" in template

    def test_build_prompt_fills_placeholders(self) -> None:
        """测试提示词模板填充。"""
        from novelforge.services.context_extractor import ContextExtractor

        # 创建 mock storage_service 和 config_manager
        storage_service = MagicMock()
        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}

        extractor = ContextExtractor(storage_service, config_manager)

        project = make_project(title="我的小说", author="作者甲", protagonist="张三")
        chapters = [make_chapter(index=0, title="第一章", content="第一章内容")]

        prompt = extractor._build_prompt(project, chapters, {})
        assert "我的小说" in prompt
        assert "作者甲" in prompt
        assert "张三" in prompt
        assert "测试简介" in prompt
        assert "测试世界观" in prompt
        assert "测试风格" in prompt
        assert "第一章内容" in prompt
        # 占位符应被全部替换
        assert "{{title}}" not in prompt
        assert "{{author}}" not in prompt
        assert "{{chapters_text}}" not in prompt

    def test_build_prompt_with_override(self) -> None:
        """测试使用 extractor_prompt_override。"""
        from novelforge.services.context_extractor import ContextExtractor

        storage_service = MagicMock()
        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}

        extractor = ContextExtractor(storage_service, config_manager)
        project = make_project()
        chapters = [make_chapter(index=0, content="内容")]

        override = "自定义提示词：{{title}} - {{chapters_text}}"
        prompt = extractor._build_prompt(project, chapters, {"extractor_prompt_override": override})
        assert "自定义提示词" in prompt
        assert "测试小说" in prompt
        assert "内容" in prompt

    def test_build_prompt_multiple_chapters(self) -> None:
        """测试多章节文本拼接。"""
        from novelforge.services.context_extractor import ContextExtractor

        storage_service = MagicMock()
        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}

        extractor = ContextExtractor(storage_service, config_manager)
        project = make_project()
        chapters = [
            make_chapter(index=0, title="第一章", content="内容一"),
            make_chapter(index=1, title="第二章", content="内容二"),
        ]

        prompt = extractor._build_prompt(project, chapters, {})
        assert "第一章" in prompt
        assert "内容一" in prompt
        assert "第二章" in prompt
        assert "内容二" in prompt


# ===== 4. JSON 解析与修复测试 =====


class TestJsonParsing:
    """JSON 解析与修复测试。"""

    def test_parse_valid_json_array(self) -> None:
        """测试解析有效 JSON 数组。"""
        from novelforge.services.context_extractor import _parse_extract_response

        content = '[{"uid": "1", "category": "characters", "content": "test"}]'
        result = _parse_extract_response(content)
        assert len(result) == 1
        assert result[0]["uid"] == "1"

    def test_parse_json_with_markdown_fences(self) -> None:
        """测试解析带 markdown 代码块标记的 JSON。"""
        from novelforge.services.context_extractor import _parse_extract_response

        content = '```json\n[{"uid": "1", "content": "test"}]\n```'
        result = _parse_extract_response(content)
        assert len(result) == 1
        assert result[0]["uid"] == "1"

    def test_parse_json_with_plain_fences(self) -> None:
        """测试解析带普通代码块标记的 JSON。"""
        from novelforge.services.context_extractor import _parse_extract_response

        content = '```\n[{"uid": "1", "content": "test"}]\n```'
        result = _parse_extract_response(content)
        assert len(result) == 1

    def test_parse_single_object_wrapped_to_list(self) -> None:
        """测试单个 JSON 对象包装为列表。"""
        from novelforge.services.context_extractor import _parse_extract_response

        content = '{"uid": "1", "content": "test"}'
        result = _parse_extract_response(content)
        assert len(result) == 1
        assert isinstance(result, list)

    def test_parse_invalid_json_raises(self) -> None:
        """测试无效 JSON 抛出异常。"""
        from novelforge.services.context_extractor import _parse_extract_response

        content = "not a json at all"
        with pytest.raises(json.JSONDecodeError):
            _parse_extract_response(content)

    def test_strip_markdown_fences(self) -> None:
        """测试去除 markdown 代码块标记。"""
        from novelforge.services.context_extractor import _strip_markdown_fences

        assert _strip_markdown_fences("```json\n{}\n```") == "{}"
        assert _strip_markdown_fences("```\n{}\n```") == "{}"
        assert _strip_markdown_fences("{}") == "{}"
        assert _strip_markdown_fences("  ```json\n{}\n```  ") == "{}"


# ===== 5. content 截断 200 字测试 =====


class TestContentTruncation:
    """content 截断测试。"""

    def test_content_truncated_to_max(self) -> None:
        """测试 content 超过 MAX_CONTENT_LENGTH 时截断。"""
        from novelforge.services.context_extractor import (
            MAX_CONTENT_LENGTH,
            _validate_and_normalize_entry,
        )

        long_content = "a" * (MAX_CONTENT_LENGTH + 100)
        raw = {
            "uid": "1",
            "category": "characters",
            "content": long_content,
        }
        entry = _validate_and_normalize_entry(raw, None, datetime.now())
        assert entry is not None
        assert len(entry.content) == MAX_CONTENT_LENGTH

    def test_content_not_truncated_when_short(self) -> None:
        """测试 content 不超过 200 字时不截断。"""
        from novelforge.services.context_extractor import _validate_and_normalize_entry

        short_content = "短内容"
        raw = {
            "uid": "1",
            "category": "characters",
            "content": short_content,
        }
        entry = _validate_and_normalize_entry(raw, None, datetime.now())
        assert entry is not None
        assert entry.content == short_content

    def test_content_exactly_200_not_truncated(self) -> None:
        """测试 content 正好 200 字时不截断。"""
        from novelforge.services.context_extractor import (
            MAX_CONTENT_LENGTH,
            _validate_and_normalize_entry,
        )

        content = "b" * MAX_CONTENT_LENGTH
        raw = {"uid": "1", "category": "characters", "content": content}
        entry = _validate_and_normalize_entry(raw, None, datetime.now())
        assert entry is not None
        assert len(entry.content) == MAX_CONTENT_LENGTH

    def test_validate_entry_missing_uid_returns_none(self) -> None:
        """测试缺少 uid 返回 None。"""
        from novelforge.services.context_extractor import _validate_and_normalize_entry

        raw = {"category": "characters", "content": "test"}
        entry = _validate_and_normalize_entry(raw, None, datetime.now())
        assert entry is None

    def test_validate_entry_invalid_category_uses_default(self) -> None:
        """测试非法 category 使用默认值。"""
        from novelforge.services.context_extractor import _validate_and_normalize_entry

        raw = {
            "uid": "1",
            "category": "invalid_category",
            "content": "test",
        }
        entry = _validate_and_normalize_entry(raw, None, datetime.now())
        assert entry is not None
        assert entry.category == "characters"

    def test_validate_entry_invalid_position_uses_default(self) -> None:
        """测试非法 position 使用默认值。"""
        from novelforge.services.context_extractor import _validate_and_normalize_entry

        raw = {
            "uid": "1",
            "category": "characters",
            "content": "test",
            "position": "invalid",
        }
        entry = _validate_and_normalize_entry(raw, None, datetime.now())
        assert entry is not None
        assert entry.position == "before"

    def test_validate_entry_sets_source_chapter_range(self) -> None:
        """测试设置 source_chapter_range。"""
        from novelforge.services.context_extractor import _validate_and_normalize_entry

        raw = {"uid": "1", "category": "characters", "content": "test"}
        entry = _validate_and_normalize_entry(raw, (0, 4), datetime.now())
        assert entry is not None
        assert entry.source_chapter_range == (0, 4)
        assert entry.extracted_at is not None


# ===== 6. 章节哈希计算测试 =====


class TestChaptersHash:
    """章节哈希计算测试。"""

    def test_hash_chapter_content_deterministic(self) -> None:
        """测试章节内容哈希确定性。"""
        from novelforge.services.context_extractor import _hash_chapter_content

        h1 = _hash_chapter_content("test content")
        h2 = _hash_chapter_content("test content")
        assert h1 == h2

    def test_hash_chapter_content_different(self) -> None:
        """测试不同内容哈希不同。"""
        from novelforge.services.context_extractor import _hash_chapter_content

        h1 = _hash_chapter_content("content1")
        h2 = _hash_chapter_content("content2")
        assert h1 != h2

    def test_compute_chapters_hash_empty(self) -> None:
        """测试空章节列表哈希。"""
        from novelforge.services.context_extractor import _compute_chapters_hash

        h = _compute_chapters_hash([])
        assert h == "empty"

    def test_compute_chapters_hash_multiple(self) -> None:
        """测试多章节组合哈希。"""
        from novelforge.services.context_extractor import _compute_chapters_hash

        chapters1 = [
            make_chapter(index=0, content="content1"),
            make_chapter(index=1, content="content2"),
        ]
        chapters2 = [
            make_chapter(index=0, content="content1"),
            make_chapter(index=1, content="content2"),
        ]
        chapters3 = [
            make_chapter(index=0, content="content1"),
            make_chapter(index=1, content="different"),
        ]
        assert _compute_chapters_hash(chapters1) == _compute_chapters_hash(chapters2)
        assert _compute_chapters_hash(chapters1) != _compute_chapters_hash(chapters3)

    def test_cache_key_format(self) -> None:
        """测试缓存 key 格式。"""
        from novelforge.services.context_extractor import ContextExtractor

        storage_service = MagicMock()
        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}

        extractor = ContextExtractor(storage_service, config_manager)
        chapters = [make_chapter(index=0, content="test")]
        key = extractor._build_cache_key("proj_123", chapters)
        assert key.startswith("ctx_extract:proj_123:")


# ===== 7. ContextExtractor 提取流程测试 =====


class TestContextExtractor:
    """ContextExtractor 提取流程测试。"""

    def _make_extractor(self, storage_service: Any = None, config_manager: Any = None) -> Any:
        """构建 ContextExtractor 实例（带 mock）。"""
        from novelforge.services.context_extractor import ContextExtractor

        if storage_service is None:
            storage_service = MagicMock()
            storage_service.storage = MagicMock()
            storage_service.storage.get_cache = AsyncMock(return_value=None)
            storage_service.storage.set_cache = AsyncMock(return_value=None)

        if config_manager is None:
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

    def test_extract_zero_chapters_skipped(self) -> None:
        """测试 0 章时跳过提取。"""
        extractor = self._make_extractor()

        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=[],
                current_chapter=None,
            )
        )
        assert result.status == "skipped"
        assert result.entries == []
        assert "无前文" in result.error

    def test_extract_fewer_chapters_uses_all(self) -> None:
        """测试章节数不足 N 时取所有可用章节。"""
        extractor = self._make_extractor()

        # mock LLM client
        mock_client = MagicMock()

        async def _mock_stream(**kwargs):
            yield _StreamChunk("[]")

        mock_client.stream_chat_completion = MagicMock(side_effect=_mock_stream)
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        chapters = [make_chapter(index=0, content="内容1")]
        current = chapters[0]

        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=current,
            )
        )
        assert result.status == "completed"
        # 1 次 8 维度提取（主角形象已解耦为独立链路）
        assert mock_client.stream_chat_completion.call_count == 1

    def test_extract_cache_hit(self) -> None:
        """测试缓存命中。"""
        from novelforge.models import ContextEntry
        from novelforge.services.context_extractor import _compute_chapters_hash

        storage_service = MagicMock()
        storage_service.storage = MagicMock()
        chapters = [make_chapter(index=0, content="内容")]
        chapters_hash = _compute_chapters_hash(chapters)
        cached_data = {
            "entries": [
                ContextEntry(
                    uid="cached_1", category="characters", content="cached"
                ).model_dump(mode="json")
            ],
            "chapters_hash": chapters_hash,
            "extracted_at": "2024-01-01T00:00:00",
            "elapsed_seconds": 1.0,
            "token_usage": {},
            "lookback": 5,
            "batch_count": 1,
        }
        storage_service.storage.get_cache = AsyncMock(return_value=cached_data)
        storage_service.storage.set_cache = AsyncMock(return_value=None)

        extractor = self._make_extractor(storage_service=storage_service)

        # mock LLM client（不应被调用）
        mock_client = MagicMock()
        mock_client.stream_chat_completion = MagicMock()
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=chapters[0],
            )
        )
        assert result.status == "completed"
        assert result.from_cache is True
        assert len(result.entries) == 1
        assert result.entries[0].uid == "cached_1"
        # LLM 不应被调用
        mock_client.stream_chat_completion.assert_not_called()

    def test_extract_force_refresh_skips_cache(self) -> None:
        """测试 force_refresh 跳过缓存。"""
        from novelforge.models import ContextEntry

        storage_service = MagicMock()
        storage_service.storage = MagicMock()
        cached_entries = [
            ContextEntry(uid="cached_1", content="cached").model_dump(mode="json")
        ]
        storage_service.storage.get_cache = AsyncMock(return_value=cached_entries)
        storage_service.storage.set_cache = AsyncMock(return_value=None)

        extractor = self._make_extractor(storage_service=storage_service)

        mock_client = MagicMock()

        async def _mock_stream(**kwargs):
            yield _StreamChunk("[]")

        mock_client.stream_chat_completion = MagicMock(side_effect=_mock_stream)
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        chapters = [make_chapter(index=0, content="内容")]
        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=chapters[0],
                force_refresh=True,
            )
        )
        assert result.status == "completed"
        assert result.from_cache is False
        # 缓存不应被读取
        storage_service.storage.get_cache.assert_not_called()
        # LLM 应被调用：1 次 8 维度提取（主角形象已解耦为独立链路）
        assert mock_client.stream_chat_completion.call_count == 1

    def test_extract_llm_failure(self) -> None:
        """测试 LLM 调用失败。"""
        from novelforge.services.llm_client import LLMError

        extractor = self._make_extractor()

        mock_client = MagicMock()
        mock_client.stream_chat_completion = MagicMock(side_effect=LLMError("API error"))
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        chapters = [make_chapter(index=0, content="内容")]
        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=chapters[0],
            )
        )
        assert result.status == "failed"
        assert "API error" in result.error

    def test_extract_no_endpoint_configured(self) -> None:
        """测试未配置 API 端点。"""
        extractor = self._make_extractor()
        extractor._get_llm_client = MagicMock(return_value=None)

        chapters = [make_chapter(index=0, content="内容")]
        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=chapters[0],
            )
        )
        assert result.status == "failed"
        assert "API" in result.error or "端点" in result.error

    def test_extract_invalid_json_response(self) -> None:
        """测试 LLM 返回无效 JSON。"""
        extractor = self._make_extractor()

        mock_client = MagicMock()

        async def _mock_stream(**kwargs):
            yield _StreamChunk("not a json")

        mock_client.stream_chat_completion = MagicMock(side_effect=_mock_stream)
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        chapters = [make_chapter(index=0, content="内容")]
        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=chapters[0],
            )
        )
        assert result.status == "failed"
        assert "JSON" in result.error or "解析" in result.error

    def test_extract_parses_valid_entries(self) -> None:
        """测试解析有效的提取结果。"""
        extractor = self._make_extractor()

        response_content = json.dumps([
            {
                "uid": "char_1",
                "category": "characters",
                "key": ["主角"],
                "comment": "主角信息",
                "content": "主角是位战士",
                "order": 50,
                "position": "before",
                "depth": 4,
                "role": "system",
            },
            {
                "uid": "loc_1",
                "category": "locations",
                "content": "城堡",
                "position": "after",
            },
        ])
        mock_client = MagicMock()

        async def _mock_stream(**kwargs):
            yield _StreamChunk(response_content)

        mock_client.stream_chat_completion = MagicMock(side_effect=_mock_stream)
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        chapters = [make_chapter(index=0, content="内容")]
        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=chapters[0],
                force_refresh=True,
            )
        )
        assert result.status == "completed"
        assert len(result.entries) == 2
        assert result.entries[0].uid == "char_1"
        assert result.entries[0].category == "characters"
        assert result.entries[1].uid == "loc_1"
        assert result.entries[1].position == "after"
        # 验证 source_chapter_range 被设置
        assert all(e.source_chapter_range == (0, 0) for e in result.entries)
        # 流式路径 stream_chat_completion 的 StreamChunk 不携带 usage，token_usage 为空
        assert result.token_usage == {}

    def test_extract_batch_retry_on_llm_error(self) -> None:
        """单批次首次 LLM 调用失败，第二次成功 → status 非 failed，call_count==2。"""
        from novelforge.services.llm_client import LLMError

        extractor = self._make_extractor()

        response_content = json.dumps([
            {
                "uid": "char_1",
                "category": "characters",
                "key": ["主角"],
                "comment": "主角信息",
                "content": "主角是位战士",
                "order": 50,
                "position": "before",
                "depth": 4,
                "role": "system",
            },
        ])
        mock_client = MagicMock()
        responses = [
            LLMError("首次调用网络错误"),
            response_content,
            # 主角形象提取响应（合法 dict，第 1 次尝试即成功）
            '{"basic_anchors": {}}',
        ]

        async def _mock_stream(**kwargs):
            idx = mock_client.stream_chat_completion.call_count - 1
            item = responses[min(idx, len(responses) - 1)]
            if isinstance(item, BaseException):
                raise item
            yield _StreamChunk(item)

        mock_client.stream_chat_completion = MagicMock(side_effect=_mock_stream)
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        chapters = [make_chapter(index=0, content="内容")]
        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=chapters[0],
                force_refresh=True,
            )
        )
        assert result.status != "failed"
        assert len(result.entries) == 1
        assert result.entries[0].uid == "char_1"
        # 8 维度首次失败+第二次成功（2 次）= 2 次 LLM 调用（主角形象已解耦为独立链路）
        assert mock_client.stream_chat_completion.call_count == 2

    def test_extract_batch_retry_exhausted_fails(self) -> None:
        """单批次 2 次均抛 LLMError → status failed，call_count==2。"""
        from novelforge.services.llm_client import LLMError

        extractor = self._make_extractor()

        mock_client = MagicMock()
        mock_client.stream_chat_completion = MagicMock(
            side_effect=[
                LLMError("第一次失败"),
                LLMError("第二次失败"),
            ]
        )
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        chapters = [make_chapter(index=0, content="内容")]
        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=chapters[0],
                force_refresh=True,
            )
        )
        assert result.status == "failed"
        # 2 次均失败 = 2 次 LLM 调用
        assert mock_client.stream_chat_completion.call_count == 2

    def test_extract_lookback_chapters_override(self) -> None:
        """测试 lookback_chapters 配置覆盖。"""
        extractor = self._make_extractor()

        # mock LLM
        mock_client = MagicMock()

        async def _mock_stream(**kwargs):
            yield _StreamChunk("[]")

        mock_client.stream_chat_completion = MagicMock(side_effect=_mock_stream)
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        # 项目级覆盖 lookback_chapters=2
        project = make_project(extract_config={"lookback_chapters": 2})
        chapters = [
            make_chapter(index=0, content="内容0"),
            make_chapter(index=1, content="内容1"),
            make_chapter(index=2, content="内容2"),
            make_chapter(index=3, content="内容3"),
        ]
        current = chapters[3]

        result = asyncio.run(
            extractor.extract(
                project=project,
                chapters=chapters,
                current_chapter=current,
                force_refresh=True,
            )
        )
        assert result.status == "completed"
        # 验证 prompt 中只包含最后 2 章内容
        call_args = mock_client.stream_chat_completion.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "内容2" in prompt
        assert "内容3" in prompt
        assert "内容0" not in prompt
        assert "内容1" not in prompt

    def test_extract_saves_cache_on_success(self) -> None:
        """测试提取成功后保存缓存。"""
        storage_service = MagicMock()
        storage_service.storage = MagicMock()
        storage_service.storage.get_cache = AsyncMock(return_value=None)
        storage_service.storage.set_cache = AsyncMock(return_value=None)

        extractor = self._make_extractor(storage_service=storage_service)

        mock_client = MagicMock()

        async def _mock_stream(**kwargs):
            yield _StreamChunk('[{"uid": "1", "content": "test"}]')

        mock_client.stream_chat_completion = MagicMock(side_effect=_mock_stream)
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        chapters = [make_chapter(index=0, content="内容")]
        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=chapters[0],
            )
        )
        assert result.status == "completed"
        # 验证保存了缓存
        storage_service.storage.set_cache.assert_called_once()
        call_args = storage_service.storage.set_cache.call_args
        assert call_args.kwargs.get("category") == "context_extract"

    def test_extract_cancel(self) -> None:
        """测试取消提取。"""
        import asyncio as asyncio_mod

        from novelforge.services.llm_client import LLMError

        extractor = self._make_extractor()

        mock_client = MagicMock()
        mock_client.stream_chat_completion = MagicMock(
            side_effect=asyncio_mod.CancelledError("cancelled")
        )
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        chapters = [make_chapter(index=0, content="内容")]
        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=chapters[0],
            )
        )
        assert result.status == "failed"
        assert "取消" in result.error

    # ===== 7.1 【信息汇总】环节测试 =====

    def test_multi_batch_triggers_merge(self) -> None:
        """测试多批次独立提取 + 【信息汇总】环节被触发。

        3 章长内容 + token_limit=10 → 每章一批（3 批），
        第 4 次 LLM 调用为汇总，返回去重合并后的 3 条条目。
        """
        extractor = self._make_extractor()

        batch1_resp = json.dumps([{"uid": "char_a", "category": "characters", "content": "主角A", "key": ["A"], "position": "before"}])
        batch2_resp = json.dumps([{"uid": "char_b", "category": "characters", "content": "配角B", "key": ["B"], "position": "before"}])
        batch3_resp = json.dumps([{"uid": "loc_c", "category": "locations", "content": "地点C", "key": ["C"], "position": "after"}])
        merge_resp = json.dumps([
            {"uid": "char_a", "category": "characters", "content": "主角A", "key": ["A"], "position": "before", "order": 100},
            {"uid": "char_b", "category": "characters", "content": "配角B", "key": ["B"], "position": "before", "order": 200},
            {"uid": "loc_c", "category": "locations", "content": "地点C", "key": ["C"], "position": "after", "order": 100},
        ])

        mock_client = MagicMock()
        responses = [batch1_resp, batch2_resp, batch3_resp, merge_resp]

        async def _mock_stream(**kwargs):
            idx = mock_client.stream_chat_completion.call_count - 1
            content = responses[min(idx, len(responses) - 1)]
            yield _StreamChunk(content)

        mock_client.stream_chat_completion = MagicMock(side_effect=_mock_stream)
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        long_content = "这是一段测试用的章节内容用于触发多批次拆分。" * 3
        chapters = [
            make_chapter(index=0, content=long_content),
            make_chapter(index=1, content=long_content),
            make_chapter(index=2, content=long_content),
        ]

        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=None,
                force_refresh=True,
                lookback_override=0,
                token_limit_override=10,
            )
        )

        assert result.status == "completed"
        assert result.merged is True
        assert result.batch_count == 3
        assert len(result.entries) == 3
        # 3 批 8 维度 + 1 次汇总 = 4 次 LLM 调用（主角形象已解耦为独立链路）
        assert mock_client.stream_chat_completion.call_count == 4
        # 验证第 4 次调用（汇总）的 prompt 含批次标记
        merge_call = mock_client.stream_chat_completion.call_args_list[3]
        merge_prompt = merge_call.kwargs["messages"][0]["content"]
        assert "## 批次 1/3" in merge_prompt
        assert "## 批次 2/3" in merge_prompt
        assert "## 批次 3/3" in merge_prompt

    def test_single_batch_no_merge(self) -> None:
        """测试单批次不触发【信息汇总】环节。

        2 章 + token_limit=0（不限制）→ 单批次，仅 1 次 LLM 调用。
        """
        extractor = self._make_extractor()

        response_content = json.dumps([
            {"uid": "char_1", "category": "characters", "content": "主角", "position": "before"},
            {"uid": "loc_1", "category": "locations", "content": "地点", "position": "after"},
        ])
        mock_client = MagicMock()

        async def _mock_stream(**kwargs):
            yield _StreamChunk(response_content)

        mock_client.stream_chat_completion = MagicMock(side_effect=_mock_stream)
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        chapters = [
            make_chapter(index=0, content="内容1"),
            make_chapter(index=1, content="内容2"),
        ]

        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=None,
                force_refresh=True,
                lookback_override=0,
                token_limit_override=0,
            )
        )

        assert result.status == "completed"
        assert result.merged is False
        assert result.batch_count == 1
        assert len(result.entries) == 2
        # 1 次 8 维度提取（主角形象已解耦为独立链路）
        assert mock_client.stream_chat_completion.call_count == 1

    def test_merge_failure_degrades_to_all_entries(self) -> None:
        """测试【信息汇总】失败时降级使用 best-effort uid 替换合并结果。

        3 批次提取（含重复 uid 验证降级合并），第 4 次汇总 LLM 抛 LLMError。
        降级路径：final_entries = all_entries（uid 替换合并后的累计列表）。
        """
        from novelforge.services.llm_client import LLMError

        extractor = self._make_extractor()

        batch1_resp = json.dumps([{"uid": "dup_1", "category": "characters", "content": "主角A", "position": "before"}])
        batch2_resp = json.dumps([{"uid": "dup_1", "category": "characters", "content": "主角A更新", "position": "before"}])
        batch3_resp = json.dumps([{"uid": "loc_2", "category": "locations", "content": "地点B", "position": "after"}])

        mock_client = MagicMock()
        responses = [batch1_resp, batch2_resp, batch3_resp, LLMError("merge failed")]

        async def _mock_stream(**kwargs):
            idx = mock_client.stream_chat_completion.call_count - 1
            item = responses[min(idx, len(responses) - 1)]
            if isinstance(item, BaseException):
                raise item
            yield _StreamChunk(item)

        mock_client.stream_chat_completion = MagicMock(side_effect=_mock_stream)
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        long_content = "这是一段测试用的章节内容用于触发多批次拆分。" * 3
        chapters = [
            make_chapter(index=0, content=long_content),
            make_chapter(index=1, content=long_content),
            make_chapter(index=2, content=long_content),
        ]

        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=None,
                force_refresh=True,
                lookback_override=0,
                token_limit_override=10,
            )
        )

        assert result.status == "completed"
        assert result.merged is False
        assert result.batch_count == 3
        # best-effort uid 替换合并：dup_1 被更新（2 次），loc_2 新增 → 2 条
        assert len(result.entries) == 2
        # 3 批 8 维度 + 1 次汇总失败 = 4 次 LLM 调用（主角形象已解耦为独立链路）
        assert mock_client.stream_chat_completion.call_count == 4

    def test_extract_result_merged_field(self) -> None:
        """测试 ExtractResult.merged 字段默认值与赋值。"""
        from novelforge.services.context_extractor import ExtractResult

        result_default = ExtractResult()
        assert result_default.merged is False
        assert result_default.batch_count == 1

        result_merged = ExtractResult(batch_count=3, merged=True)
        assert result_merged.merged is True
        assert result_merged.batch_count == 3

        assert "merged" in ExtractResult.__dataclass_fields__
        assert "batch_count" in ExtractResult.__dataclass_fields__


# ===== 8. ExtractResult 数据类测试 =====


class TestExtractResult:
    """ExtractResult 数据类测试。"""

    def test_extract_result_default_values(self) -> None:
        """测试 ExtractResult 默认值。"""
        from novelforge.services.context_extractor import ExtractResult

        result = ExtractResult()
        assert result.entries == []
        assert result.status == "completed"
        assert result.error == ""
        assert result.elapsed_seconds == 0.0
        assert result.token_usage == {}
        assert result.from_cache is False

    def test_extract_result_with_values(self) -> None:
        """测试 ExtractResult 设置值。"""
        from novelforge.models import ContextEntry
        from novelforge.services.context_extractor import ExtractResult

        entry = ContextEntry(uid="1", content="test")
        result = ExtractResult(
            entries=[entry],
            status="completed",
            elapsed_seconds=1.5,
            token_usage={"total_tokens": 100},
            from_cache=True,
        )
        assert len(result.entries) == 1
        assert result.elapsed_seconds == 1.5
        assert result.token_usage["total_tokens"] == 100
        assert result.from_cache is True

    def test_extract_result_protagonist_default_values(self) -> None:
        """测试 ExtractResult 主角形象字段默认值。"""
        from novelforge.services.context_extractor import ExtractResult

        result = ExtractResult()
        assert result.protagonist_profile is None
        assert result.protagonist_batch_count == 1
        assert result.protagonist_merged is False

    def test_extract_result_with_protagonist_profile(self) -> None:
        """测试 ExtractResult 携带 ProtagonistProfile。"""
        from novelforge.models import ProtagonistProfile
        from novelforge.services.context_extractor import ExtractResult

        profile = ProtagonistProfile(
            basic_anchors={"name": "主角"},
            motivation_system={"core_fear": "失败"},
        )
        result = ExtractResult(
            entries=[],
            protagonist_profile=profile,
            protagonist_batch_count=3,
            protagonist_merged=True,
        )
        assert result.protagonist_profile is not None
        assert result.protagonist_profile.basic_anchors["name"] == "主角"
        assert result.protagonist_batch_count == 3
        assert result.protagonist_merged is True


# ===== 9. 非流式 LLM 调用测试 =====


class TestLLMClientChatCompletion:
    """非流式 LLM 调用测试。"""

    def test_chat_completion_method_exists(self) -> None:
        """测试 chat_completion 方法存在。"""
        from novelforge.services.llm_client import LLMClient

        client = LLMClient(base_url="https://api.test.com/v1", api_key="sk-test")
        assert hasattr(client, "chat_completion")
        assert callable(client.chat_completion)

    def test_chat_completion_signature(self) -> None:
        """测试 chat_completion 方法签名。"""
        import inspect

        from novelforge.services.llm_client import LLMClient

        sig = inspect.signature(LLMClient.chat_completion)
        params = sig.parameters
        assert "messages" in params
        assert "model" in params
        assert "temperature" in params
        assert "max_tokens" in params
        assert "top_p" in params
        # 验证默认值
        assert params["temperature"].default == 0.2
        assert params["max_tokens"].default == 2000
        assert params["top_p"].default == 1.0

    def test_chat_completion_payload_stream_false(self) -> None:
        """测试 chat_completion 的 payload 中 stream 为 false。"""
        # 通过检查源码验证
        import inspect

        from novelforge.services.llm_client import LLMClient

        source = inspect.getsource(LLMClient.chat_completion)
        assert '"stream": False' in source


# ===== 10. ContinuationWorker extracted_context_snapshot 测试 =====


class TestContinuationWorkerSnapshot:
    """ContinuationWorker extracted_context_snapshot 参数测试。"""

    def test_worker_accepts_extracted_context_snapshot(self) -> None:
        """测试 ContinuationWorker 接受 extracted_context_snapshot 参数。"""
        import inspect

        from novelforge.services.continuation_worker import ContinuationWorker

        sig = inspect.signature(ContinuationWorker.__init__)
        assert "extracted_context_snapshot" in sig.parameters

    def test_worker_default_extracted_context_snapshot_empty(self) -> None:
        """测试默认 extracted_context_snapshot 为空列表。"""
        from novelforge.services.continuation_worker import ContinuationWorker

        worker = ContinuationWorker(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            model="gpt-4o",
            messages=[],
            parameters={},
            chapter_id="ch1",
        )
        assert worker.extracted_context_snapshot == []

    def test_worker_with_extracted_context_snapshot(self) -> None:
        """测试 ContinuationWorker 接受非空 extracted_context_snapshot。"""
        from novelforge.services.continuation_worker import ContinuationWorker

        snapshot = [
            {"uid": "1", "category": "characters", "content": "test"}
        ]
        worker = ContinuationWorker(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            model="gpt-4o",
            messages=[],
            parameters={},
            chapter_id="ch1",
            extracted_context_snapshot=snapshot,
        )
        assert worker.extracted_context_snapshot == snapshot


# ===== 11. UI 组件测试 =====


class TestUIComponents:
    """UI 组件测试（需要 QApplication）。"""

    @pytest.fixture(autouse=True)
    def _setup_qapp(self) -> None:
        """确保 QApplication 实例存在。"""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication([])

    def test_context_preview_panel_creation(self) -> None:
        """测试 ContextPreviewPanel 创建。"""
        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        panel = ContextPreviewPanel()
        assert panel.is_extracting is False
        assert panel.get_entries() == []

    def test_context_preview_panel_set_entries(self) -> None:
        """测试 ContextPreviewPanel 设置条目。"""
        from novelforge.models import ContextEntry
        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        panel = ContextPreviewPanel()
        entries = [
            ContextEntry(uid="1", category="characters", content="test1"),
            ContextEntry(uid="2", category="locations", content="test2"),
        ]
        panel.set_entries(entries)
        assert len(panel.get_all_entries()) == 2
        assert len(panel.get_entries()) == 2

    def test_context_preview_panel_start_extraction(self) -> None:
        """测试 ContextPreviewPanel 开始提取状态。"""
        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        panel = ContextPreviewPanel()
        panel.start_extraction()
        assert panel.is_extracting is True

    def test_context_preview_panel_finish_extraction(self) -> None:
        """测试 ContextPreviewPanel 完成提取。"""
        from novelforge.models import ContextEntry
        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        panel = ContextPreviewPanel()
        panel.start_extraction()
        entries = [ContextEntry(uid="1", content="test")]
        panel.finish_extraction(entries, elapsed_seconds=1.0)
        assert panel.is_extracting is False
        assert len(panel.get_entries()) == 1

    def test_context_preview_panel_fail_extraction(self) -> None:
        """测试 ContextPreviewPanel 提取失败。"""
        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        panel = ContextPreviewPanel()
        panel.start_extraction()
        panel.fail_extraction("test error")
        assert panel.is_extracting is False

    def test_context_preview_panel_cancel_extraction(self) -> None:
        """测试 ContextPreviewPanel 取消提取。"""
        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        panel = ContextPreviewPanel()
        panel.start_extraction()
        panel.cancel_extraction()
        assert panel.is_extracting is False

    def test_extraction_dialog_creation_failed(self) -> None:
        """测试 ExtractionDialog 失败模式创建。"""
        from novelforge.ui.extraction_dialog import ExtractionDialog

        dialog = ExtractionDialog(mode="failed", error="test error")
        assert dialog.windowTitle() == "上下文提取失败"

    def test_extraction_dialog_creation_cancelled(self) -> None:
        """测试 ExtractionDialog 取消模式创建。"""
        from novelforge.ui.extraction_dialog import ExtractionDialog

        dialog = ExtractionDialog(mode="cancelled")
        assert dialog.windowTitle() == "上下文提取已取消"

    def test_extraction_dialog_result_constants(self) -> None:
        """测试 ExtractionDialog 返回值常量。"""
        from novelforge.ui.extraction_dialog import ExtractionDialog

        assert ExtractionDialog.RESULT_RETRY == 1
        assert ExtractionDialog.RESULT_SKIP == 2
        assert ExtractionDialog.RESULT_CANCEL == 3

    def test_extraction_dialog_default_result_cancel(self) -> None:
        """测试 ExtractionDialog 默认结果为 cancel。"""
        from novelforge.ui.extraction_dialog import ExtractionDialog

        dialog = ExtractionDialog()
        assert dialog.result_code() == ExtractionDialog.RESULT_CANCEL

    def test_continuation_panel_has_context_preview(self) -> None:
        """测试 ContinuationPanel 包含 context_preview_panel 属性。"""
        from novelforge.ui.continuation_panel import ContinuationPanel
        from novelforge.ui.context_preview_panel import ContextPreviewPanel

        panel = ContinuationPanel()
        assert hasattr(panel, "context_preview_panel")
        assert isinstance(panel.context_preview_panel, ContextPreviewPanel)

    def test_continuation_panel_has_extract_context_requested_signal(self) -> None:
        """测试 ContinuationPanel 有 extract_context_requested 信号。"""
        from novelforge.ui.continuation_panel import ContinuationPanel

        panel = ContinuationPanel()
        assert hasattr(panel, "extract_context_requested")


# ===== 12. 集成测试 =====


class TestIntegration:
    """集成测试。"""

    def test_full_extraction_flow_with_mock(self) -> None:
        """测试完整提取流程（mock LLM）。"""
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
            "lookback_chapters": 3,
        }
        config_manager.get_flow_model.return_value = "gpt-4o-mini"
        config_manager.get_default_endpoint.return_value = {
            "id": "ep1",
            "base_url": "https://api.test.com/v1",
            "default_model": "gpt-4o-mini",
        }
        config_manager.decrypt_api_key.return_value = "sk-test"

        extractor = ContextExtractor(storage_service, config_manager)

        # mock LLM 返回完整 JSON
        response_content = json.dumps([
            {"uid": "char_1", "category": "characters", "content": "主角信息", "position": "before"},
            {"uid": "loc_1", "category": "locations", "content": "地点信息", "position": "after"},
            {"uid": "evt_1", "category": "events", "content": "事件信息", "position": "at_depth", "depth": 2},
        ])
        mock_client = MagicMock()

        async def _mock_stream(**kwargs):
            yield _StreamChunk(response_content)

        mock_client.stream_chat_completion = MagicMock(side_effect=_mock_stream)
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        chapters = [
            make_chapter(index=0, content="第一章内容"),
            make_chapter(index=1, content="第二章内容"),
            make_chapter(index=2, content="第三章内容"),
        ]
        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=chapters[-1],
                force_refresh=True,
            )
        )

        assert result.status == "completed"
        assert len(result.entries) == 3
        assert result.entries[0].category == "characters"
        assert result.entries[1].category == "locations"
        assert result.entries[2].category == "events"
        assert result.entries[2].position == "at_depth"
        assert result.entries[2].depth == 2
        # source_chapter_range 应为 (0, 2)
        assert all(e.source_chapter_range == (0, 2) for e in result.entries)
        # 流式路径 stream_chat_completion 的 StreamChunk 不携带 usage，token_usage 为空
        assert result.token_usage == {}
        # 缓存应被保存
        storage_service.storage.set_cache.assert_called_once()

    def test_extraction_with_markdown_fenced_json(self) -> None:
        """测试 LLM 返回带 markdown 标记的 JSON。"""
        from novelforge.services.context_extractor import ContextExtractor

        storage_service = MagicMock()
        storage_service.storage = MagicMock()
        storage_service.storage.get_cache = AsyncMock(return_value=None)
        storage_service.storage.set_cache = AsyncMock(return_value=None)

        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}
        config_manager.get_default_endpoint.return_value = {
            "id": "ep1",
            "base_url": "https://api.test.com/v1",
            "default_model": "gpt-4o-mini",
        }
        config_manager.decrypt_api_key.return_value = "sk-test"

        extractor = ContextExtractor(storage_service, config_manager)

        # LLM 返回带 markdown 标记的 JSON
        response_content = '```json\n[{"uid": "1", "category": "characters", "content": "test"}]\n```'
        mock_client = MagicMock()

        async def _mock_stream(**kwargs):
            yield _StreamChunk(response_content)

        mock_client.stream_chat_completion = MagicMock(side_effect=_mock_stream)
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        chapters = [make_chapter(index=0, content="内容")]
        result = asyncio.run(
            extractor.extract(
                project=make_project(),
                chapters=chapters,
                current_chapter=chapters[0],
                force_refresh=True,
            )
        )
        assert result.status == "completed"
        assert len(result.entries) == 1
        assert result.entries[0].uid == "1"


# ===== 13. exclude_current 参数测试（重写当前章节模式） =====


class TestContextExtractorExcludeCurrent:
    """ContextExtractor exclude_current 参数测试（重写当前章节模式）。

    重写模式下「前文提取」不得包含当前章节（当前章节是待重写对象，不是前文）。
    """

    def test_build_cache_key_exclude_current_suffix(self) -> None:
        """exclude_current=True 时缓存 key 带 :rewrite 后缀。"""
        from novelforge.services.context_extractor import ContextExtractor

        storage_service = MagicMock()
        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}
        extractor = ContextExtractor(storage_service, config_manager)

        chapters = [
            make_chapter(index=0, content="ch0"),
            make_chapter(index=1, content="ch1"),
        ]
        # 排除当前章节的 key 应以 :rewrite 结尾
        key_exclude = extractor._build_cache_key(
            "proj_123", chapters, exclude_current=True
        )
        assert key_exclude.endswith(":rewrite"), (
            f"重写模式 key 应带 :rewrite 后缀，实际: {key_exclude}"
        )

        # 默认（含当前章节）的 key 不应带 :rewrite 后缀
        key_normal = extractor._build_cache_key(
            "proj_123", chapters, exclude_current=False
        )
        assert not key_normal.endswith(":rewrite"), (
            f"续写模式 key 不应带 :rewrite 后缀，实际: {key_normal}"
        )

        # 两个 key 应不同（避免互相覆盖）
        assert key_exclude != key_normal

    def test_get_lookback_chapters_exclude_current(self) -> None:
        """exclude_current=True 时 _get_lookback_chapters 不含当前章节。"""
        from novelforge.services.context_extractor import ContextExtractor

        storage_service = MagicMock()
        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}
        extractor = ContextExtractor(storage_service, config_manager)

        chapters = [
            make_chapter(index=0, content="前文0"),
            make_chapter(index=1, content="前文1"),
            make_chapter(index=2, content="当前章节"),
            make_chapter(index=3, content="后续章节"),
        ]
        current = chapters[2]

        # exclude_current=True：应返回 ch0/ch1（不含 ch2 当前章节）
        result = extractor._get_lookback_chapters(
            chapters, current, lookback=0, exclude_current=True
        )
        result_ids = [c.id for c in result]
        assert "ch_0" in result_ids
        assert "ch_1" in result_ids
        assert "ch_2" not in result_ids, "当前章节不应出现在 exclude_current 结果中"
        assert "ch_3" not in result_ids, "后续章节也不应出现"

    def test_get_lookback_chapters_exclude_current_with_lookback(self) -> None:
        """exclude_current=True 时 lookback 截断作用于排除后的列表。"""
        from novelforge.services.context_extractor import ContextExtractor

        storage_service = MagicMock()
        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}
        extractor = ContextExtractor(storage_service, config_manager)

        chapters = [
            make_chapter(index=0, content="ch0"),
            make_chapter(index=1, content="ch1"),
            make_chapter(index=2, content="ch2"),
            make_chapter(index=3, content="ch3_current"),
        ]
        current = chapters[3]

        # lookback=1：仅取最近 1 章前文 = ch2（不含 ch3_current）
        result = extractor._get_lookback_chapters(
            chapters, current, lookback=1, exclude_current=True
        )
        result_ids = [c.id for c in result]
        assert "ch_2" in result_ids
        assert "ch_3" not in result_ids
        assert "ch_1" not in result_ids


if __name__ == "__main__":
    # 直接运行时执行所有测试
    pytest.main([__file__, "-v", "--tb=short"])
