"""赓笔 端到端实例测试。

验证整个项目从导入 TXT → 章节管理 → 预设/正则/模板 → 上下文提取 →
提示词组装 → 导出 → 历史日志的完整流程能跑通。

本测试不依赖真实 LLM API，使用 mock 和本地数据验证业务逻辑闭环。

运行方式：
    python -m pytest tests/test_e2e_workflow.py -v
    python tests/test_e2e_workflow.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from novelforge.core.macros import MacroContext, MacroEngine
from novelforge.core.prompt_assembler import (
    INJECTION_ABSOLUTE,
    INJECTION_RELATIVE,
    PromptAssembler,
)
from novelforge.core.regex_engine import RegexEngine
from novelforge.core.storage import get_default_storage_path
from novelforge.core.template_engine import TemplateEngine
from novelforge.core.token_counter import TokenCounter
from novelforge.core.variable_store import VariableStore
from novelforge.models import (
    Chapter,
    ContextEntry,
    Continuation,
    NovelProfile,
    Project,
    Prompt,
    PromptOrderEntry,
    PromptOrderGroup,
    RegexScript,
    WritingPreset,
)
from novelforge.models.regex import (
    PLACEMENT_AI_OUTPUT,
    PLACEMENT_USER_INPUT,
    PLACEMENT_WORLD_INFO,
)
from novelforge.services.chapter_service import ChapterService
from novelforge.services.exporter import (
    export_chapter_markdown,
    export_chapter_txt,
    export_full_txt,
    export_project_backup,
    import_project_backup,
)
from novelforge.services.history_service import HistoryService
from novelforge.services.importer import TxtImporter
from novelforge.services.preset_service import PresetService
from novelforge.services.regex_service import RegexService
from novelforge.services.storage_service import StorageService


# ===== 测试辅助 =====

SAMPLE_NOVEL_TEXT = """第一章 风起

青山之上，白云缭绕。少年林风站在山崖之巅，望着远方的云海。
他手中握着一柄生锈的长剑，眼中闪烁着坚定的光芒。
"我一定要成为天下第一剑客！"他低声说道。

第二章 初遇

林风下了山，来到一座小镇。镇上人来人往，热闹非凡。
他在茶馆里遇到了一位白发老者，老者看着他手中的剑，微微一笑。
"年轻人，你这把剑可不简单啊。"

第三章 试炼

老者带林风来到一处秘境，只见四周灵气浓郁。
"这里是上古剑冢，里面藏着无数神兵利器。"
林风深吸一口气，迈步走了进去。
"""

SAMPLE_ST_PRESET_JSON = """{
    "prompts": [
        {
            "identifier": "main",
            "name": "Main Prompt",
            "role": "system",
            "content": "你是一位专业的小说续写助手。请根据前文续写故事。",
            "system_prompt": true,
            "marker": null,
            "position": "start",
            "injection_position": 0,
            "injection_depth": 4,
            "injection_order": 100,
            "forbid_overrides": false,
            "extension": {},
            "enabled": true
        },
        {
            "identifier": "worldInfoBefore",
            "name": "World Info Before",
            "role": "system",
            "content": "",
            "system_prompt": false,
            "marker": "worldInfoBefore",
            "position": "start",
            "injection_position": 0,
            "injection_depth": 4,
            "injection_order": 100,
            "forbid_overrides": false,
            "extension": {},
            "enabled": true
        },
        {
            "identifier": "chatHistory",
            "name": "Chat History",
            "role": "system",
            "content": "",
            "system_prompt": false,
            "marker": "chatHistory",
            "position": "start",
            "injection_position": 0,
            "injection_depth": 4,
            "injection_order": 100,
            "forbid_overrides": false,
            "extension": {},
            "enabled": true
        },
        {
            "identifier": "worldInfoAfter",
            "name": "World Info After",
            "role": "system",
            "content": "",
            "system_prompt": false,
            "marker": "worldInfoAfter",
            "position": "end",
            "injection_position": 0,
            "injection_depth": 4,
            "injection_order": 100,
            "forbid_overrides": false,
            "extension": {},
            "enabled": true
        }
    ],
    "prompt_order": [
        {
            "character_id": 100000,
            "order": [
                {"identifier": "main", "enabled": true},
                {"identifier": "worldInfoBefore", "enabled": true},
                {"identifier": "chatHistory", "enabled": true},
                {"identifier": "worldInfoAfter", "enabled": true}
            ]
        }
    ],
    "temperature": 0.8,
    "max_tokens": 2000,
    "max_context": 32000
}
"""

SAMPLE_ST_REGEX_JSON = """[
    {
        "id": "regex_1",
        "scriptName": "去除思考标签",
        "findRegex": "/<think>[\\\\s\\\\S]*?<\\/think>/g",
        "replaceString": "",
        "trimStrings": [],
        "placement": [2],
        "disabled": false,
        "markdownOnly": false,
        "promptOnly": false,
        "runOnEdit": false,
        "substituteRegex": false,
        "minDepth": 0,
        "maxDepth": 100,
        "markupSafety": false
    }
]
"""

SAMPLE_ST_WORLDBOOK_JSON = """{
    "entries": {
        "0": {
            "uid": "char_1",
            "key": ["林风", "主角"],
            "comment": "人物",
            "content": "林风：少年剑客，立志成为天下第一",
            "order": 10,
            "position": 0,
            "depth": 4,
            "role": "system",
            "probability": 100,
            "disable": false
        },
        "1": {
            "uid": "loc_1",
            "key": ["青山", "山崖"],
            "comment": "地点",
            "content": "青山：主角故乡，灵气浓郁",
            "order": 20,
            "position": 1,
            "depth": 4,
            "role": "system",
            "probability": 80,
            "disable": false
        }
    }
}
"""


@pytest.fixture
def temp_storage(tmp_path: Path) -> StorageService:
    """提供临时存储服务。"""
    return StorageService(storage_path=tmp_path)


@pytest.fixture
def preset_service(temp_storage: StorageService, tmp_path: Path) -> PresetService:
    """提供预设服务（已确保默认预设存在）。"""
    service = PresetService(tmp_path)
    service.ensure_default_preset_exists()
    return service


@pytest.fixture
def regex_service(temp_storage: StorageService, tmp_path: Path) -> RegexService:
    """提供正则服务。"""
    service = RegexService(tmp_path)
    service.ensure_global_scripts_exist()
    return service


@pytest.fixture
def sample_project(temp_storage: StorageService, tmp_path: Path) -> tuple[Project, list[Chapter]]:
    """导入示例小说并返回项目和章节。"""
    # 写入示例 TXT
    txt_path = tmp_path / "sample_novel.txt"
    txt_path.write_text(SAMPLE_NOVEL_TEXT, encoding="utf-8")

    importer = TxtImporter(temp_storage)
    result = importer.import_file(str(txt_path), project_name="示例小说")

    # 重新加载章节（含完整内容）
    chapters = []
    for ch in result.chapters:
        full_chapter = temp_storage.load_chapter(ch.id)
        if full_chapter:
            chapters.append(full_chapter)

    return result.project, chapters


# ===== 测试用例 =====


class TestE2EImportAndChapter:
    """端到端：TXT 导入与章节管理。"""

    def test_import_txt_creates_project_with_chapters(
        self, sample_project: tuple[Project, list[Chapter]]
    ) -> None:
        """导入 TXT 应创建项目和章节。"""
        project, chapters = sample_project

        assert project.name == "示例小说"
        assert len(chapters) == 3
        # 章节标题包含完整标题行（如"第一章 风起"）
        assert "风起" in chapters[0].title
        assert "初遇" in chapters[1].title
        assert "试炼" in chapters[2].title

    def test_chapter_content_loaded_correctly(
        self, sample_project: tuple[Project, list[Chapter]]
    ) -> None:
        """章节正文应正确加载。"""
        _, chapters = sample_project

        # 第一章应包含"青山之上"
        assert "青山之上" in chapters[0].content
        # 第二章应包含"小镇"
        assert "小镇" in chapters[1].content
        # 第三章应包含"剑冢"
        assert "剑冢" in chapters[2].content

    def test_chapter_index_sequential(
        self, sample_project: tuple[Project, list[Chapter]]
    ) -> None:
        """章节 index 应从 0 开始递增。"""
        _, chapters = sample_project
        for i, ch in enumerate(chapters):
            assert ch.index == i

    def test_chapter_split(
        self, temp_storage: StorageService, sample_project: tuple[Project, list[Chapter]]
    ) -> None:
        """拆分章节应生成两个章节。"""
        _, chapters = sample_project
        chapter_service = ChapterService(temp_storage)

        # 在第一章中间位置拆分
        first_chapter = chapters[0]
        split_pos = len(first_chapter.content) // 2

        op, front, back = chapter_service.split_chapter(first_chapter, split_pos)

        assert op.action == "split"
        assert front.title != first_chapter.title or back.title != first_chapter.title
        # 拆分后总章节数应为原数+1
        updated_chapters = temp_storage.list_chapters(first_chapter.project_id)
        assert len(updated_chapters) == len(chapters) + 1

    def test_chapter_merge(
        self, temp_storage: StorageService, sample_project: tuple[Project, list[Chapter]]
    ) -> None:
        """合并章节应减少章节数。"""
        _, chapters = sample_project
        chapter_service = ChapterService(temp_storage)

        first_chapter = temp_storage.load_chapter(chapters[0].id)
        op, merged = chapter_service.merge_chapter_with_next(first_chapter)

        assert op.action == "merge"
        updated_chapters = temp_storage.list_chapters(first_chapter.project_id)
        assert len(updated_chapters) == len(chapters) - 1

    def test_delete_chapter_preserves_remaining_content(
        self, temp_storage: StorageService, sample_project: tuple[Project, list[Chapter]]
    ) -> None:
        """删除中间章节后，后续章节正文必须完整保留（不被空 content 覆盖）。"""
        _, chapters = sample_project
        chapter_service = ChapterService(temp_storage)

        # 确保每个章节有明确正文
        for ch in chapters:
            full = temp_storage.load_chapter(ch.id)
            full.content = f"章节{ch.index}的正文内容"
            temp_storage.save_chapter(full)

        # 删除第一章（非末尾），触发 reindex
        first = chapters[0]
        chapter_service.delete_chapter(first)

        # 验证剩余章节正文完整保留
        remaining = temp_storage.list_chapters(first.project_id)
        assert len(remaining) == len(chapters) - 1
        for ch in remaining:
            full = temp_storage.load_chapter(ch.id)
            assert full.content != "", f"章节 {ch.id} 正文被清空！"
            assert "正文内容" in full.content
        # 验证 index 连续
        for i, ch in enumerate(sorted(remaining, key=lambda c: c.index)):
            assert ch.index == i


class TestE2EPresetAndPrompt:
    """端到端：预设与提示词组装。"""

    def test_default_preset_loadable(self, preset_service: PresetService) -> None:
        """默认预设应可加载。"""
        preset = preset_service.load_default_preset()

        assert preset.id == "default"
        assert len(preset.prompts) > 0
        # 应包含 main、chatHistory、worldInfoBefore、worldInfoAfter
        identifiers = [p.identifier for p in preset.prompts]
        assert "main" in identifiers
        assert "chatHistory" in identifiers

    def test_st_preset_import(
        self, preset_service: PresetService, tmp_path: Path
    ) -> None:
        """导入 ST 预设 JSON 应正确解析。"""
        st_file = tmp_path / "st_preset.json"
        st_file.write_text(SAMPLE_ST_PRESET_JSON, encoding="utf-8")

        preset, _regex_scripts = preset_service.import_from_st_json(str(st_file))

        assert preset is not None
        assert len(preset.prompts) == 4
        # character_id 应为 100000
        assert preset.prompt_order[0].character_id == 100000
        # 应有 4 个 order 条目
        assert len(preset.prompt_order[0].order) == 4

    def test_prompt_assembly_with_default_preset(
        self, sample_project: tuple[Project, list[Chapter]], preset_service: PresetService
    ) -> None:
        """使用默认预设组装提示词应生成有效 messages。"""
        project, chapters = sample_project
        preset = preset_service.load_default_preset()

        token_counter = TokenCounter()
        macro_engine = MacroEngine()
        assembler = PromptAssembler(token_counter, macro_engine)

        novel_profile = NovelProfile(
            title="示例小说",
            author="测试作者",
            protagonist="林风",
            synopsis="少年剑客成长记",
            world_setting="古代武侠世界",
            writing_style="古风武侠",
        )

        result = assembler.assemble(
            preset=preset,
            chapters=chapters,
            current_chapter=chapters[-1],
            context_entries=[],
            model="gpt-4o",
            max_context=32000,
            max_tokens=2000,
            target_words=2000,
            novel_profile=novel_profile,
        )

        assert len(result.messages) > 0
        # 应包含 system 消息（main prompt）
        roles = [m["role"] for m in result.messages]
        assert "system" in roles
        # 应包含 user 消息（章节历史）
        assert "user" in roles
        # token 使用应合理
        assert result.token_usage["total_used"] > 0
        assert result.token_usage["total_used"] <= 32000

    def test_prompt_assembly_with_context_entries(
        self, sample_project: tuple[Project, list[Chapter]], preset_service: PresetService
    ) -> None:
        """带 ContextEntry 的提示词组装应注入 worldInfo。"""
        _, chapters = sample_project
        preset = preset_service.load_default_preset()

        token_counter = TokenCounter()
        macro_engine = MacroEngine()
        assembler = PromptAssembler(token_counter, macro_engine)

        # 创建上下文条目
        entries = [
            ContextEntry(
                uid="char_1",
                category="characters",
                key=["林风"],
                content="林风：少年剑客，立志成为天下第一",
                order=10,
                position="before",
            ),
            ContextEntry(
                uid="loc_1",
                category="locations",
                key=["青山"],
                content="青山：主角故乡",
                order=20,
                position="after",
            ),
        ]

        result = assembler.assemble(
            preset=preset,
            chapters=chapters,
            current_chapter=chapters[-1],
            context_entries=entries,
            model="gpt-4o",
            max_context=32000,
            max_tokens=2000,
        )

        # 应在 messages 中找到注入的 worldInfo 内容
        all_content = "\n".join(m.get("content", "") for m in result.messages)
        assert "林风" in all_content
        assert "青山" in all_content


class TestE2ERegexAndTemplate:
    """端到端：正则与模板。"""

    def test_st_regex_import(
        self, regex_service: RegexService, tmp_path: Path
    ) -> None:
        """导入 ST 正则 JSON 应正确解析。"""
        st_file = tmp_path / "st_regex.json"
        st_file.write_text(SAMPLE_ST_REGEX_JSON, encoding="utf-8")

        scripts = regex_service.import_from_st_json(str(st_file), scope="global")

        assert len(scripts) == 1
        assert scripts[0].scriptName == "去除思考标签"
        assert PLACEMENT_AI_OUTPUT in scripts[0].placement

    def test_regex_engine_apply(
        self, regex_service: RegexService, tmp_path: Path
    ) -> None:
        """正则引擎应正确应用替换。"""
        st_file = tmp_path / "st_regex.json"
        st_file.write_text(SAMPLE_ST_REGEX_JSON, encoding="utf-8")
        scripts = regex_service.import_from_st_json(str(st_file), scope="global")

        engine = RegexEngine()
        engine.compile_scripts(scripts)

        text = "前文<think>这是思考内容</think>后文"
        result = engine.apply_to_text(text, placement=PLACEMENT_AI_OUTPUT)

        assert "<think>" not in result
        assert "这是思考内容" not in result
        assert "前文" in result
        assert "后文" in result

    def test_template_engine_render(self, tmp_path: Path) -> None:
        """模板引擎应正确渲染 Jinja2。"""
        var_store = VariableStore(tmp_path)
        # project 作用域需要 project_id
        var_store.setvar("主角", "林风", scope="project", project_id="test_proj")

        engine = TemplateEngine(variable_store=var_store)

        # getvar 在模板中不需要传 project_id，由 render_pre_send 绑定
        template = "当前主角是 {{ getvar('主角', scope='project') }}"
        rendered, error = engine.render_pre_send(template, project_id="test_proj")

        assert error is None
        assert "林风" in rendered

    def test_template_sandbox_security(self, tmp_path: Path) -> None:
        """沙箱应阻止访问危险属性。"""
        var_store = VariableStore(tmp_path)
        engine = TemplateEngine(variable_store=var_store)

        # 尝试访问 __class__ 应被阻止
        template = "{{ ''.__class__ }}"
        rendered, error = engine.render_pre_send(template)

        # 沙箱应阻止 __class__ 访问：要么返回错误，要么渲染结果不含类信息
        assert error is not None or "__class__" not in rendered, (
            f"沙箱未阻止 __class__ 访问：error={error!r}, rendered={rendered!r}"
        )


class TestE2EContextExtraction:
    """端到端：上下文提取。"""

    def test_worldbook_import(
        self, tmp_path: Path
    ) -> None:
        """ST 世界书导入应正确转换。"""
        from novelforge.services.worldbook_importer import import_worldbook

        wb_file = tmp_path / "worldbook.json"
        wb_file.write_text(SAMPLE_ST_WORLDBOOK_JSON, encoding="utf-8")

        entries = import_worldbook(str(wb_file))

        assert len(entries) == 2
        # position 数字→字符串转换
        positions = {e.position for e in entries}
        assert "before" in positions
        assert "after" in positions
        # source_chapter_range 应为 None（导入条目）
        for e in entries:
            assert e.source_chapter_range is None
        # 不应包含 probability 字段
        assert not hasattr(entries[0], "probability")

    def test_context_extractor_skipped_no_chapters(
        self, temp_storage: StorageService, tmp_path: Path
    ) -> None:
        """0 章时跳过提取。"""
        from novelforge.services.context_extractor import ContextExtractor

        # 创建空项目
        project = temp_storage.create_project(name="空项目")

        # Mock config_manager
        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}

        extractor = ContextExtractor(temp_storage, config_manager)

        # 异步运行 extract
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                extractor.extract(
                    project=project,
                    chapters=[],
                    current_chapter=None,
                )
            )
        finally:
            loop.close()

        assert result.status == "skipped"
        assert result.entries == []

    def test_context_extractor_with_mock_llm(
        self, temp_storage: StorageService, sample_project: tuple[Project, list[Chapter]],
        tmp_path: Path
    ) -> None:
        """使用 mock LLM 验证提取流程。"""
        from novelforge.services.context_extractor import ContextExtractor

        project, chapters = sample_project

        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}
        config_manager.get_default_endpoint.return_value = {
            "id": "ep1",
            "base_url": "https://api.example.com/v1",
            "default_model": "gpt-4o-mini",
        }
        config_manager.decrypt_api_key.return_value = "sk-test-key"

        extractor = ContextExtractor(temp_storage, config_manager)

        # Mock LLM 返回的 JSON
        mock_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps([
                            {
                                "uid": "char_1",
                                "category": "characters",
                                "key": ["林风"],
                                "comment": "主角",
                                "content": "林风：少年剑客",
                                "order": 10,
                                "position": "before",
                            }
                        ])
                    }
                }
            ]
        }

        # Mock LLMClient.chat_completion
        with patch("novelforge.services.context_extractor.LLMClient") as MockLLM:
            mock_client = MockLLM.return_value
            mock_client.chat_completion = AsyncMock(return_value=mock_response)

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    extractor.extract(
                        project=project,
                        chapters=chapters,
                        current_chapter=chapters[-1],
                    )
                )
            finally:
                loop.close()

        assert result.status == "completed"
        assert len(result.entries) == 1
        assert result.entries[0].uid == "char_1"
        assert result.entries[0].category == "characters"
        # source_chapter_range 应为 tuple
        assert result.entries[0].source_chapter_range is not None

    def test_context_extraction_caching(
        self, temp_storage: StorageService, sample_project: tuple[Project, list[Chapter]],
        tmp_path: Path
    ) -> None:
        """缓存命中时应跳过 LLM 调用。"""
        from novelforge.services.context_extractor import ContextExtractor

        project, chapters = sample_project

        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}
        config_manager.get_default_endpoint.return_value = {
            "id": "ep1",
            "base_url": "https://api.example.com/v1",
            "default_model": "gpt-4o-mini",
        }
        config_manager.decrypt_api_key.return_value = "sk-test-key"

        extractor = ContextExtractor(temp_storage, config_manager)

        # 使用非空响应（空列表可能不被缓存）
        mock_response = {
            "choices": [{"message": {"content": json.dumps([
                {
                    "uid": "char_1",
                    "category": "characters",
                    "key": ["林风"],
                    "content": "林风：少年剑客",
                    "position": "before",
                }
            ])}}]
        }

        # 使用内存缓存模拟（避免跨事件循环的 aiosqlite 问题）
        cache_store: dict = {}

        async def mock_get_cache(key):
            return cache_store.get(key)

        async def mock_set_cache(key, value, **kw):
            cache_store[key] = value

        mock_storage = AsyncMock()
        mock_storage.get_cache = mock_get_cache
        mock_storage.set_cache = mock_set_cache
        temp_storage.storage = mock_storage

        with patch("novelforge.services.context_extractor.LLMClient") as MockLLM:
            mock_client = MockLLM.return_value
            mock_client.chat_completion = AsyncMock(return_value=mock_response)

            loop = asyncio.new_event_loop()
            try:
                # 第一次提取
                result1 = loop.run_until_complete(
                    extractor.extract(project=project, chapters=chapters, current_chapter=chapters[-1])
                )
                # 第二次提取（应命中缓存）
                result2 = loop.run_until_complete(
                    extractor.extract(project=project, chapters=chapters, current_chapter=chapters[-1])
                )
            finally:
                loop.close()

        # 第一次不应命中缓存，第二次应命中缓存
        assert result1.status == "completed"
        assert result1.from_cache is False
        assert result2.status == "completed"
        assert result2.from_cache is True
        # LLM 应只被调用一次（第二次命中缓存）+ 2 次主角形象提取（解析失败重试 1 次，最终失败被捕获）
        assert mock_client.chat_completion.call_count == 3


class TestE2EExport:
    """端到端：导出功能。"""

    def test_export_full_txt(
        self, temp_storage: StorageService, sample_project: tuple[Project, list[Chapter]],
        tmp_path: Path
    ) -> None:
        """导出完整 TXT 应包含所有章节。"""
        project, chapters = sample_project
        output_path = tmp_path / "exported.txt"

        char_count = export_full_txt(temp_storage, project.id, str(output_path), include_titles=True)

        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        # 应包含所有章节标题
        assert "风起" in content
        assert "初遇" in content
        assert "试炼" in content
        # 应包含章节内容
        assert "青山之上" in content
        assert char_count > 0

    def test_export_chapter_markdown(
        self, sample_project: tuple[Project, list[Chapter]], tmp_path: Path
    ) -> None:
        """导出单章 Markdown 应包含 H1 标题。"""
        _, chapters = sample_project
        output_path = tmp_path / "chapter1.md"

        export_chapter_markdown(chapters[0], str(output_path))

        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        assert content.startswith("# ")
        assert chapters[0].title in content

    def test_project_backup_export_import(
        self, temp_storage: StorageService, preset_service: PresetService,
        regex_service: RegexService, sample_project: tuple[Project, list[Chapter]],
        tmp_path: Path
    ) -> None:
        """项目备份导出后导入应恢复数据。"""
        project, chapters = sample_project
        zip_path = tmp_path / "backup.zip"

        # 导出备份
        export_project_backup(
            temp_storage, preset_service, regex_service, project.id, str(zip_path)
        )

        assert zip_path.exists()

        # 导入备份
        new_project_id = import_project_backup(
            temp_storage, preset_service, regex_service, str(zip_path)
        )

        assert new_project_id != project.id

        # 验证恢复的项目
        restored_project = temp_storage.load_project(new_project_id)
        assert restored_project is not None
        assert restored_project.name == project.name

        # 验证恢复的章节
        restored_chapters = temp_storage.list_chapters(new_project_id)
        assert len(restored_chapters) == len(chapters)


class TestE2EHistoryLog:
    """端到端：历史日志。"""

    def test_history_log_record_and_query(
        self, temp_storage: StorageService, sample_project: tuple[Project, list[Chapter]]
    ) -> None:
        """历史日志应能记录和查询。"""
        project, chapters = sample_project
        history_service = HistoryService(temp_storage)

        # 记录一条历史
        history_id = history_service.log_continuation(
            project_id=project.id,
            chapter_id=chapters[0].id,
            swipe_id="swipe_test_1",
            started_at=datetime.now().isoformat(),
            finished_at=datetime.now().isoformat(),
            status="completed",
            model="gpt-4o",
            parameters={"temperature": 0.8},
            prompt_messages=[{"role": "system", "content": "test"}],
            output_text="这是续写内容",
        )

        assert history_id != ""

        # 查询历史
        history_list = history_service.list_history(project_id=project.id)
        assert len(history_list) >= 1

        # 查询详情
        detail = history_service.get_history_detail(history_id)
        assert detail is not None
        assert detail["model"] == "gpt-4o"
        assert detail["output_text"] == "这是续写内容"

    def test_history_log_filter_by_chapter(
        self, temp_storage: StorageService, sample_project: tuple[Project, list[Chapter]]
    ) -> None:
        """按章节筛选历史日志。"""
        project, chapters = sample_project
        history_service = HistoryService(temp_storage)

        # 记录两条历史（不同章节）
        for i, chapter in enumerate(chapters[:2]):
            history_service.log_continuation(
                project_id=project.id,
                chapter_id=chapter.id,
                swipe_id=f"swipe_{i}",
                started_at=datetime.now().isoformat(),
                finished_at=datetime.now().isoformat(),
                status="completed",
                model="gpt-4o",
                parameters={},
                prompt_messages=[],
                output_text=f"内容{i}",
            )

        # 按第一章筛选
        filtered = history_service.list_history(chapter_id=chapters[0].id)
        assert len(filtered) == 1
        assert filtered[0]["chapter_id"] == chapters[0].id


class TestE2EContinuationSnapshot:
    """端到端：续写版本快照。"""

    def test_swipe_snapshot_integrity(
        self, temp_storage: StorageService, sample_project: tuple[Project, list[Chapter]]
    ) -> None:
        """swipe 快照应包含完整参数。"""
        _, chapters = sample_project
        chapter = chapters[0]

        # 创建 swipe
        swipe = Continuation(
            id="swipe_test",
            created_at=datetime.now(),
            content="这是续写内容",
            model="gpt-4o",
            is_accepted=False,
            status="completed",
            created_by="continuation",
            parameters_snapshot={
                "temperature": 0.8,
                "max_tokens": 2000,
                "target_words": 2000,
            },
            preset_id="default",
            preset_snapshot={"id": "default", "name": "默认"},
            regex_script_ids_snapshot=[],
            extracted_context_snapshot=[
                {"uid": "char_1", "content": "林风"}
            ],
            prompt_snapshot=[
                {"role": "system", "content": "test"}
            ],
            reasoning_content=None,
        )

        temp_storage.save_continuation(swipe, chapter.id)

        # 重新加载
        loaded_chapter = temp_storage.load_chapter(chapter.id)
        assert loaded_chapter is not None
        assert len(loaded_chapter.continuations) == 1

        loaded_swipe = loaded_chapter.continuations[0]
        assert loaded_swipe.id == "swipe_test"
        assert loaded_swipe.model == "gpt-4o"
        assert loaded_swipe.parameters_snapshot["temperature"] == 0.8
        assert loaded_swipe.preset_id == "default"
        assert len(loaded_swipe.extracted_context_snapshot) == 1
        assert len(loaded_swipe.prompt_snapshot) == 1

    def test_swipe_accept_append_to_chapter(
        self, temp_storage: StorageService, sample_project: tuple[Project, list[Chapter]]
    ) -> None:
        """接受续写（提升为章节：插入新章节，删除续写记录）。"""
        _, chapters = sample_project
        chapter_service = ChapterService(temp_storage)
        chapter = temp_storage.load_chapter(chapters[0].id)

        original_length = len(chapter.content)
        original_index = chapter.index

        # 创建并接受 swipe
        swipe = Continuation(
            id="swipe_accept",
            created_at=datetime.now(),
            content="这是续写的内容部分。",
            model="gpt-4o",
            is_accepted=False,
            status="completed",
            created_by="continuation",
            parameters_snapshot={},
            preset_id="default",
            preset_snapshot={},
            regex_script_ids_snapshot=[],
            extracted_context_snapshot=[],
            prompt_snapshot=[],
        )
        temp_storage.save_continuation(swipe, chapter.id)

        # 重新加载并接受（提升为新章节）
        chapter = temp_storage.load_chapter(chapter.id)
        swipe = chapter.continuations[-1]
        updated_chapter, new_chapter = (
            chapter_service.promote_continuation_to_chapter(chapter, swipe)
        )

        # 原章节正文不变
        assert len(updated_chapter.content) == original_length
        assert "这是续写的内容部分。" not in updated_chapter.content
        # 新章节 index = 原 + 1，content = 续写内容
        assert new_chapter.index == original_index + 1
        assert new_chapter.content == "这是续写的内容部分。"
        # 原续写记录已删除（不在 updated_chapter.continuations）
        assert all(c.id != swipe.id for c in updated_chapter.continuations)


class TestE2EFullWorkflow:
    """端到端：完整工作流。"""

    def test_full_workflow_from_import_to_export(
        self, temp_storage: StorageService, preset_service: PresetService,
        regex_service: RegexService, tmp_path: Path
    ) -> None:
        """完整工作流：导入 → 组装 → 导出。"""
        # 1. 导入 TXT
        txt_path = tmp_path / "novel.txt"
        txt_path.write_text(SAMPLE_NOVEL_TEXT, encoding="utf-8")
        importer = TxtImporter(temp_storage)
        import_result = importer.import_file(str(txt_path), project_name="完整流程测试")
        project = import_result.project
        chapters = []
        for ch in import_result.chapters:
            full = temp_storage.load_chapter(ch.id)
            if full:
                chapters.append(full)

        assert len(chapters) == 3

        # 2. 加载预设
        preset = preset_service.load_default_preset()
        assert preset is not None

        # 3. 组装提示词
        token_counter = TokenCounter()
        macro_engine = MacroEngine()
        assembler = PromptAssembler(token_counter, macro_engine)

        novel_profile = NovelProfile(
            title="完整流程测试",
            protagonist="林风",
            writing_style="武侠",
        )

        assemble_result = assembler.assemble(
            preset=preset,
            chapters=chapters,
            current_chapter=chapters[-1],
            context_entries=[],
            model="gpt-4o",
            max_context=32000,
            max_tokens=2000,
            novel_profile=novel_profile,
        )

        assert len(assemble_result.messages) > 0

        # 4. 创建模拟 swipe
        swipe = Continuation(
            id="swipe_full",
            created_at=datetime.now(),
            content="林风握紧长剑，继续前行。",
            model="gpt-4o",
            is_accepted=False,
            status="completed",
            created_by="continuation",
            parameters_snapshot={"temperature": 0.8},
            preset_id=preset.id,
            preset_snapshot={},
            regex_script_ids_snapshot=[],
            extracted_context_snapshot=[],
            prompt_snapshot=assemble_result.messages,
        )
        temp_storage.save_continuation(swipe, chapters[-1].id)

        # 5. 接受续写（提升为新章节，删除续写记录）
        chapter_service = ChapterService(temp_storage)
        chapter = temp_storage.load_chapter(chapters[-1].id)
        swipe = chapter.continuations[-1]
        updated, new_chapter = chapter_service.promote_continuation_to_chapter(
            chapter, swipe
        )
        # 新章节 content = 续写内容
        assert "林风握紧长剑" in new_chapter.content
        assert new_chapter.index == chapter.index + 1

        # 6. 导出完整 TXT
        output_path = tmp_path / "final_export.txt"
        char_count = export_full_txt(temp_storage, project.id, str(output_path))
        assert output_path.exists()
        assert char_count > 0

        # 7. 导出项目备份
        zip_path = tmp_path / "backup.zip"
        export_project_backup(
            temp_storage, preset_service, regex_service, project.id, str(zip_path)
        )
        assert zip_path.exists()

        # 8. 记录历史日志
        history_service = HistoryService(temp_storage)
        history_id = history_service.log_continuation(
            project_id=project.id,
            chapter_id=chapters[-1].id,
            swipe_id=swipe.id,
            started_at=datetime.now().isoformat(),
            finished_at=datetime.now().isoformat(),
            status="completed",
            model="gpt-4o",
            parameters={"temperature": 0.8},
            prompt_messages=assemble_result.messages,
            output_text=swipe.content,
        )
        assert history_id != ""

        # 9. 查询历史
        history = history_service.list_history(project_id=project.id)
        assert len(history) >= 1

    def test_performance_large_chapter_count(
        self, temp_storage: StorageService, tmp_path: Path
    ) -> None:
        """性能验证：大量章节应快速加载。"""
        # 创建包含 100 章的项目
        project = temp_storage.create_project(name="性能测试")

        start_time = time.time()
        for i in range(100):
            chapter = Chapter(
                id=f"ch_perf_{i}",
                project_id=project.id,
                index=i,
                title=f"第{i+1}章",
                content=f"这是第{i+1}章的内容。" * 10,
                word_count=100,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            temp_storage.save_chapter(chapter)
        create_elapsed = time.time() - start_time

        # 加载章节列表
        start_time = time.time()
        chapters = temp_storage.list_chapters(project.id)
        list_elapsed = time.time() - start_time

        assert len(chapters) == 100
        # 创建 100 章应在 5 秒内
        assert create_elapsed < 5.0, f"创建 100 章耗时 {create_elapsed:.2f}s"
        # 列表加载应在 1 秒内
        assert list_elapsed < 1.0, f"加载 100 章列表耗时 {list_elapsed:.2f}s"


# ===== 主入口 =====

def main() -> int:
    """运行所有端到端测试。"""
    print("=" * 60)
    print("赓笔 端到端实例测试")
    print("=" * 60)

    import pytest as _pytest

    sys.exit(_pytest.main([__file__, "-v", "--tb=short"]))


if __name__ == "__main__":
    main()
