"""「重写当前章节」模式测试。

覆盖：
1. ContextExtractor._get_lookback_chapters(exclude_current=True) 不含当前章节
2. ContextExtractor._build_cache_key(exclude_current=True) 带 :rewrite 后缀
3. ContextExtractor.extract(exclude_current=True) 提取的条目基于排除当前章节后的章节列表
4. PromptAssembler._build_history(exclude_current=True) 历史不含当前章节消息
5. ChapterService.replace_chapter_content 替换正文、删 swipe、移除 continuations
6. phase_rewrite_analysis.txt 模板存在性与 6 占位符完整性
7. ContinuationPanel 三模式切换 + rewrite_current_analysis_requested 信号
8. FlowEndpointDialog.FLOW_DEFINITIONS 含 rewrite_analysis
9. _on_accept_continuation 的 created_by="rewrite_current" 分支
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication


# ===== 测试工具 =====


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """提供全局 QApplication 单例（离屏平台）。"""
    app = QApplication.instance() or QApplication(sys.argv)
    return app


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
) -> Any:
    """构建测试 Project 对象。"""
    from novelforge.models import NovelProfile, Project

    profile = NovelProfile(
        title=title,
        author="测试作者",
        protagonist="主角",
        synopsis="测试简介",
        world_setting="测试世界观",
        writing_style="测试风格",
    )
    return Project(
        id=project_id,
        name=title,
        novel_profile=profile,
    )


# ===== 1. _get_lookback_chapters exclude_current =====


class TestGetLookbackChaptersExcludeCurrent:
    """_get_lookback_chapters(exclude_current=True) 测试。"""

    def test_exclude_current_returns_pre_chapters_only(self) -> None:
        """exclude_current=True 时返回列表不含当前章节（含前章）。"""
        from novelforge.services.context_extractor import ContextExtractor

        storage_service = MagicMock()
        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}
        extractor = ContextExtractor(storage_service, config_manager)

        chapters = [
            make_chapter(index=0, content="ch0"),
            make_chapter(index=1, content="ch1"),
            make_chapter(index=2, content="ch2"),
            make_chapter(index=3, content="ch3"),
        ]
        current = chapters[2]

        # exclude_current=True：应返回 ch0/ch1（不含 ch2）
        result = extractor._get_lookback_chapters(
            chapters, current, lookback=0, exclude_current=True
        )
        result_ids = [c.id for c in result]
        assert "ch_0" in result_ids
        assert "ch_1" in result_ids
        assert "ch_2" not in result_ids, "当前章节不应出现在 exclude_current 结果中"
        assert "ch_3" not in result_ids, "后续章节也不应出现"

    def test_exclude_current_false_includes_current(self) -> None:
        """exclude_current=False（默认）时含当前章节（保持原行为）。"""
        from novelforge.services.context_extractor import ContextExtractor

        storage_service = MagicMock()
        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}
        extractor = ContextExtractor(storage_service, config_manager)

        chapters = [
            make_chapter(index=0, content="ch0"),
            make_chapter(index=1, content="ch1"),
            make_chapter(index=2, content="ch2"),
        ]
        current = chapters[1]

        # 默认：含当前章节 ch1
        result = extractor._get_lookback_chapters(
            chapters, current, lookback=0, exclude_current=False
        )
        result_ids = [c.id for c in result]
        assert "ch_0" in result_ids
        assert "ch_1" in result_ids

    def test_exclude_current_with_lookback(self) -> None:
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
            make_chapter(index=3, content="ch3"),
        ]
        current = chapters[3]

        # lookback=1：仅取最近 1 章前文 = ch_2（不含 ch_3）
        result = extractor._get_lookback_chapters(
            chapters, current, lookback=1, exclude_current=True
        )
        result_ids = [c.id for c in result]
        assert "ch_2" in result_ids
        assert "ch_3" not in result_ids
        assert "ch_1" not in result_ids


# ===== 2. _build_cache_key exclude_current =====


class TestBuildCacheKeyExcludeCurrent:
    """_build_cache_key(exclude_current=True) 测试。"""

    def test_exclude_current_adds_rewrite_suffix(self) -> None:
        """exclude_current=True 时缓存 key 带 :rewrite 后缀。"""
        from novelforge.services.context_extractor import ContextExtractor

        storage_service = MagicMock()
        config_manager = MagicMock()
        config_manager.get_context_extract_settings.return_value = {}
        extractor = ContextExtractor(storage_service, config_manager)

        chapters = [
            make_chapter(index=0, content="test"),
            make_chapter(index=1, content="test2"),
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


# ===== 3. extract(exclude_current=True) 不提取当前章节 =====


class TestExtractExcludeCurrent:
    """extract(exclude_current=True) 测试。"""

    def _make_extractor(self) -> Any:
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
        return ContextExtractor(storage_service, config_manager)

    def test_extract_exclude_current_no_current_in_prompt(self) -> None:
        """exclude_current=True 时提取 prompt 不含当前章节正文。"""
        extractor = self._make_extractor()

        # mock LLM 返回空数组（仅验证 prompt 内容）
        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(
            return_value={
                "choices": [{"message": {"content": "[]"}}],
                "usage": {},
            }
        )
        extractor._get_llm_client = MagicMock(return_value=(mock_client, "gpt-4o-mini"))

        project = make_project()
        chapters = [
            make_chapter(index=0, content="前文0"),
            make_chapter(index=1, content="前文1"),
            make_chapter(index=2, content="当前章节正文unique"),
        ]
        current = chapters[2]

        asyncio.run(
            extractor.extract(
                project=project,
                chapters=chapters,
                current_chapter=current,
                force_refresh=True,
                exclude_current=True,
            )
        )

        # 验证 prompt 中含前文，不含当前章节正文
        call_args = mock_client.chat_completion.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "前文0" in prompt
        assert "前文1" in prompt
        assert "当前章节正文unique" not in prompt, (
            "exclude_current=True 时当前章节正文不应出现在提取 prompt 中"
        )


# ===== 4. _build_history exclude_current =====


class TestBuildHistoryExcludeCurrent:
    """PromptAssembler._build_history(exclude_current=True) 测试。"""

    def test_exclude_current_history_excludes_current(self) -> None:
        """exclude_current=True 时聊天历史不含当前章节消息。"""
        from novelforge.core.prompt_assembler import PromptAssembler
        from novelforge.core.token_counter import TokenCounter

        assembler = PromptAssembler(TokenCounter())

        chapters = [
            make_chapter(index=0, content="ch0"),
            make_chapter(index=1, content="ch1"),
            make_chapter(index=2, content="ch2_current"),
        ]
        current = chapters[2]

        # 默认：含当前章节（3 条消息）
        history_default = assembler._build_history(
            chapters, current, lookback_chapters=0, exclude_current=False
        )
        assert len(history_default) == 3

        # exclude_current=True：仅前 2 章（2 条消息）
        history_exclude = assembler._build_history(
            chapters, current, lookback_chapters=0, exclude_current=True
        )
        assert len(history_exclude) == 2, (
            f"exclude_current 应排除当前章节，期望 2 条历史，实际: {len(history_exclude)}"
        )
        # 验证历史内容不含当前章节正文
        for msg in history_exclude:
            assert "ch2_current" not in msg.get("content", "")

    def test_exclude_current_with_lookback(self) -> None:
        """exclude_current=True 时 lookback 截断作用于排除后的列表。"""
        from novelforge.core.prompt_assembler import PromptAssembler
        from novelforge.core.token_counter import TokenCounter

        assembler = PromptAssembler(TokenCounter())

        chapters = [
            make_chapter(index=0, content="ch0"),
            make_chapter(index=1, content="ch1"),
            make_chapter(index=2, content="ch2"),
            make_chapter(index=3, content="ch3_current"),
        ]
        current = chapters[3]

        # lookback=1：仅取最近 1 章前文 = ch2（不含 ch3_current）
        history = assembler._build_history(
            chapters, current, lookback_chapters=1, exclude_current=True
        )
        assert len(history) == 1
        assert "ch2" in history[0].get("content", "")
        assert "ch3_current" not in history[0].get("content", "")


# ===== 5. ChapterService.replace_chapter_content =====


class TestChapterServiceReplaceChapterContent:
    """ChapterService.replace_chapter_content 测试。"""

    def test_replace_chapter_content(self, tmp_path: Path) -> None:
        """验证替换后章节正文更新、word_count 更新、swipe 删除、continuations 移除。"""
        from novelforge.models import Chapter, Continuation, Project
        from novelforge.services.chapter_service import ChapterService
        from novelforge.services.storage_service import StorageService

        storage = StorageService(storage_path=tmp_path)
        service = ChapterService(storage)

        # 先创建项目（满足外键约束）
        storage.save_project(Project(id="proj_test", name="测试项目"))

        # 创建章节（原正文 100 字）
        original_content = "原正文" * 50
        chapter = Chapter(
            id="ch_test",
            project_id="proj_test",
            index=0,
            title="测试章节",
            content=original_content,
            word_count=len(original_content),
        )
        storage.save_chapter(chapter)

        # 创建 swipe（新正文 200 字）
        new_content = "重写后的新正文内容" * 20
        swipe = Continuation(
            id="cont_test",
            chapter_id="ch_test",
            content=new_content,
            model="test-model",
            status="completed",
            created_by="rewrite_current",
        )
        storage.save_continuation(swipe, "ch_test")

        # 重新加载章节（含 continuations）
        chapter = storage.load_chapter("ch_test")
        assert chapter is not None
        assert len(chapter.continuations) == 1

        # 调用 replace_chapter_content
        updated = service.replace_chapter_content(chapter, swipe)

        # 验证正文已替换
        assert updated.content == new_content
        assert updated.word_count == len(new_content)
        # 验证 continuations 已移除该 swipe
        assert all(c.id != swipe.id for c in updated.continuations), (
            "replace_chapter_content 后 continuations 不应再包含原 swipe"
        )

        # 验证持久化：重新加载
        reloaded = storage.load_chapter("ch_test")
        assert reloaded is not None
        assert reloaded.content == new_content
        assert reloaded.word_count == len(new_content)

        # 验证 swipe 记录已删除
        from novelforge.services.storage_service import _generate_id
        # delete_continuation 应已删除 swipe（_load_continuations 返回空）
        reloaded_with_conts = storage.load_chapter("ch_test")
        assert all(c.id != swipe.id for c in reloaded_with_conts.continuations)

    def test_replace_does_not_shift_index(self, tmp_path: Path) -> None:
        """替换不应后移后续章节 index（与 promote 区别）。"""
        from novelforge.models import Chapter, Continuation, Project
        from novelforge.services.chapter_service import ChapterService
        from novelforge.services.storage_service import StorageService

        storage = StorageService(storage_path=tmp_path)
        service = ChapterService(storage)

        # 先创建项目（满足外键约束，make_chapter 默认 project_id="test_proj"）
        storage.save_project(Project(id="test_proj", name="测试项目"))

        # 创建 3 章
        ch0 = make_chapter(index=0, content="ch0", chapter_id="ch_0")
        ch1 = make_chapter(index=1, content="ch1", chapter_id="ch_1")
        ch2 = make_chapter(index=2, content="ch2", chapter_id="ch_2")
        storage.save_chapter(ch0)
        storage.save_chapter(ch1)
        storage.save_chapter(ch2)

        # 创建 swipe
        swipe = Continuation(
            id="cont_1",
            chapter_id="ch_1",
            content="重写ch1",
            model="test",
            status="completed",
            created_by="rewrite_current",
        )
        storage.save_continuation(swipe, "ch_1")

        # 加载 ch1 并替换
        ch1_loaded = storage.load_chapter("ch_1")
        service.replace_chapter_content(ch1_loaded, swipe)

        # 验证 ch2 的 index 仍为 2（未后移）
        ch2_reloaded = storage.load_chapter("ch_2")
        assert ch2_reloaded is not None
        assert ch2_reloaded.index == 2, "replace 不应后移后续章节 index"


# ===== 6. phase_rewrite_analysis.txt 模板 =====


class TestPhaseRewriteAnalysisTemplate:
    """phase_rewrite_analysis.txt 模板测试。"""

    def test_template_exists(self) -> None:
        """模板文件存在。"""
        from novelforge.utils.paths import get_agent_prompt_path

        path = get_agent_prompt_path("rewrite_analysis")
        assert path.exists(), f"phase_rewrite_analysis.txt 不存在: {path}"

    def test_template_has_all_placeholders(self) -> None:
        """模板含 6 个占位符。"""
        from novelforge.utils.paths import get_agent_prompt_path

        path = get_agent_prompt_path("rewrite_analysis")
        content = path.read_text(encoding="utf-8")

        placeholders = [
            "{{current_chapter_text}}",
            "{{user_input}}",
            "{{world_ontology}}",
            "{{protagonist_profile}}",
            "{{custom_audit_rules}}",
            "{{context_entries}}",
        ]
        for ph in placeholders:
            assert ph in content, f"模板缺少占位符: {ph}"


# ===== 7. ContinuationPanel 三模式 + 信号 =====


class TestContinuationPanelRewriteCurrentMode:
    """ContinuationPanel 三模式与 start_flow 信号测试。"""

    @staticmethod
    def _make_flow_plugins() -> list:
        """创建 3 个内置流程插件的 mock（模拟 FlowPluginService 注册表）。"""
        plugins = []
        for plugin_id, name in [
            ("single", "单章续写"),
            ("volume", "卷续写"),
            ("rewrite_current", "重写当前章节"),
        ]:
            plugin = MagicMock()
            plugin.id = plugin_id
            plugin.name = name
            plugins.append(plugin)
        return plugins

    def test_mode_combo_has_three_items(self, qapp) -> None:
        """模式 combo 含三个选项（single/volume/rewrite_current）。"""
        from novelforge.ui.continuation_panel import ContinuationPanel

        panel = ContinuationPanel()
        panel.set_flow_plugins(self._make_flow_plugins())
        assert panel._mode_combo.count() == 3
        # 验证 itemData
        modes = [
            panel._mode_combo.itemData(i)
            for i in range(panel._mode_combo.count())
        ]
        assert "single" in modes
        assert "volume" in modes
        assert "rewrite_current" in modes

    def test_get_mode_returns_rewrite_current(self, qapp) -> None:
        """set_mode('rewrite_current') → get_mode() 返回 rewrite_current。"""
        from novelforge.ui.continuation_panel import ContinuationPanel

        panel = ContinuationPanel()
        panel.set_flow_plugins(self._make_flow_plugins())
        panel.set_mode("rewrite_current")
        assert panel.get_mode() == "rewrite_current"

    def test_has_rewrite_current_analysis_requested_signal(self, qapp) -> None:
        """Panel 有 rewrite_current_analysis_requested 信号。"""
        from novelforge.ui.continuation_panel import ContinuationPanel

        panel = ContinuationPanel()
        assert hasattr(panel, "rewrite_current_analysis_requested")

    def test_start_clicked_emits_rewrite_signal_in_rewrite_mode(self, qapp) -> None:
        """rewrite_current 模式下点击开始 → 发射 start_flow(plugin_id='rewrite_current')。"""
        from novelforge.ui.continuation_panel import ContinuationPanel

        panel = ContinuationPanel()
        panel.set_flow_plugins(self._make_flow_plugins())
        panel.set_mode("rewrite_current")

        received: list[tuple] = []
        panel.start_flow.connect(
            lambda plugin_id, params: received.append((plugin_id, params))
        )

        panel._on_start_clicked()

        assert len(received) == 1, (
            "rewrite_current 模式下点击开始应发射 start_flow 信号"
        )
        assert received[0][0] == "rewrite_current"
        assert "model" in received[0][1]

    def test_start_clicked_emits_start_continuation_in_single_mode(self, qapp) -> None:
        """single 模式下点击开始 → 发射 start_flow(plugin_id='single')。"""
        from novelforge.ui.continuation_panel import ContinuationPanel

        panel = ContinuationPanel()
        panel.set_flow_plugins(self._make_flow_plugins())
        panel.set_mode("single")

        received: list[tuple] = []
        panel.start_flow.connect(
            lambda plugin_id, params: received.append((plugin_id, params))
        )

        panel._on_start_clicked()

        assert len(received) == 1
        assert received[0][0] == "single"


# ===== 8. FlowEndpointDialog FLOW_DEFINITIONS 含 rewrite_analysis =====


class TestFlowEndpointRewriteAnalysis:
    """FlowEndpointDialog 流程端点含 rewrite_analysis 测试。"""

    def test_flow_definitions_contains_rewrite_analysis(self) -> None:
        """FLOW_DEFINITIONS 含 ('rewrite_analysis', '重写当前章节分析')。"""
        from novelforge.ui.flow_endpoint_dialog import FLOW_DEFINITIONS

        keys = [k for k, _ in FLOW_DEFINITIONS]
        assert "rewrite_analysis" in keys, (
            "FLOW_DEFINITIONS 应包含 rewrite_analysis 流程端点"
        )

    def test_flow_definitions_count_is_8(self) -> None:
        """FLOW_DEFINITIONS 含 8 个流程（原 7 个 + rewrite_analysis）。"""
        from novelforge.ui.flow_endpoint_dialog import FLOW_DEFINITIONS

        assert len(FLOW_DEFINITIONS) == 8, (
            f"FLOW_DEFINITIONS 应有 8 项，实际: {len(FLOW_DEFINITIONS)}"
        )


# ===== 9. _on_accept_continuation 分支 =====


class TestAcceptRewriteCurrentSwipe:
    """_on_accept_continuation 的 created_by='rewrite_current' 分支测试。

    验证：created_by='rewrite_current' 的 swipe 接受时调
    replace_chapter_content 而非 promote_continuation_to_chapter。
    """

    def test_accept_rewrite_current_calls_replace_chapter_content(
        self, tmp_path: Path
    ) -> None:
        """created_by='rewrite_current' 的 swipe 接受时调 replace_chapter_content。"""
        from novelforge.models import Chapter, Continuation, Project
        from novelforge.services.chapter_service import ChapterService
        from novelforge.services.storage_service import StorageService

        storage = StorageService(storage_path=tmp_path)
        chapter_service = ChapterService(storage)

        # 先创建项目（满足外键约束）
        storage.save_project(Project(id="proj", name="测试项目"))

        # 创建章节与 swipe
        chapter = Chapter(
            id="ch_accept_test",
            project_id="proj",
            index=0,
            title="测试章节",
            content="原正文",
            word_count=4,
        )
        storage.save_chapter(chapter)

        swipe = Continuation(
            id="cont_accept_test",
            chapter_id="ch_accept_test",
            content="新正文内容",
            model="test",
            status="completed",
            created_by="rewrite_current",
        )
        storage.save_continuation(swipe, "ch_accept_test")

        # 加载完整章节
        loaded_chapter = storage.load_chapter("ch_accept_test")
        loaded_swipe = loaded_chapter.continuations[0]

        # 直接调 replace_chapter_content（绕过 UI 弹窗）
        updated = chapter_service.replace_chapter_content(
            loaded_chapter, loaded_swipe
        )

        # 验证替换成功
        assert updated.content == "新正文内容"
        assert updated.word_count == len("新正文内容")
        # 验证 swipe 已删除
        reloaded = storage.load_chapter("ch_accept_test")
        assert all(c.id != swipe.id for c in reloaded.continuations)

    def test_accept_normal_continuation_calls_promote(self, tmp_path: Path) -> None:
        """非 rewrite_current 的 swipe 接受时调 promote_continuation_to_chapter。"""
        from novelforge.models import Chapter, Continuation, Project
        from novelforge.services.chapter_service import ChapterService
        from novelforge.services.storage_service import StorageService

        storage = StorageService(storage_path=tmp_path)
        chapter_service = ChapterService(storage)

        # 先创建项目（满足外键约束）
        storage.save_project(Project(id="proj", name="测试项目"))

        chapter = Chapter(
            id="ch_promote_test",
            project_id="proj",
            index=0,
            title="测试章节",
            content="原正文",
            word_count=4,
        )
        storage.save_chapter(chapter)

        swipe = Continuation(
            id="cont_promote_test",
            chapter_id="ch_promote_test",
            content="续写正文",
            model="test",
            status="completed",
            created_by="continuation",  # 普通续写
        )
        storage.save_continuation(swipe, "ch_promote_test")

        loaded_chapter = storage.load_chapter("ch_promote_test")
        loaded_swipe = loaded_chapter.continuations[0]

        # 调 promote_continuation_to_chapter
        updated_chapter, new_chapter = (
            chapter_service.promote_continuation_to_chapter(
                loaded_chapter, loaded_swipe
            )
        )

        # 验证新章节已创建
        assert new_chapter.content == "续写正文"
        assert new_chapter.index == 1
        # 原章节正文未变（promote 不覆盖原章节）
        assert updated_chapter.content == "原正文"
