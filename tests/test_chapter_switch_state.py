"""章节切换状态保留机制测试。

覆盖：
- 状态缓冲字段初值
- 续写 chunk 路由（按章缓冲 + UI 守卫）
- 续写完成归档（swipe 存入发起章节 + 状态清理）
- 续写出错清理
- 上下文提取 chunk 路由
- 世界观提取项目级状态跟踪
- 审计发起章节跟踪与取消清理
- 卷续写发起章节跟踪与完成清理
- _on_chapter_selected 续写流式态恢复

运行方式：
    python -m pytest tests/test_chapter_switch_state.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ===== 辅助：构造轻量 MainWindow（绕过 __init__）=====


def _make_window():
    """构造一个绕过 __init__ 的 MainWindow，仅设置被测方法所需属性。

    用 MainWindow.__new__ 跳过重型 __init__（需 ConfigManager/StorageService 等），
    手动设置 7 个状态缓冲字段与被测方法访问的 mock 依赖。
    """
    from novelforge.ui.main_window import MainWindow

    win = MainWindow.__new__(MainWindow)

    # 7 个状态缓冲字段（初值与 __init__ 一致）
    win._extract_stream_text_by_chapter = {}
    win._ontology_extracting = False
    win._ontology_stream_text = ""
    win._continuation_chapter_id = None
    win._continuation_stream_text_by_chapter = {}
    win._audit_chapter_id = None
    win._volume_chapter_id = None

    # 其它被测方法访问的属性
    win._current_chapter = None
    win._current_context_entries = []
    win._context_entries_by_chapter = {}
    win._extracting_chapter_id = None
    win._continuation_worker = None
    win._audit_worker = None
    win._audit_dialog = None
    win._audit_original_swipe = None
    win._volume_orchestrator = None
    win._current_chapters = []
    win._continuation_prompt_messages = []
    win._continuation_model = ""
    win._continuation_parameters = {}
    win._continuation_started_at = ""

    # mock 依赖对象
    win.continuation_panel = MagicMock()
    win.continuation_panel.context_preview_panel = MagicMock()
    win.chapter_editor = MagicMock()
    win.chapter_list = MagicMock()
    win.storage_service = MagicMock()
    win.chapter_service = MagicMock()
    win.chapter_service.storage = MagicMock()
    win.history_service = MagicMock()

    # 内部辅助方法 stub
    win._set_status_message = MagicMock()
    win._record_history = MagicMock()
    win._update_chapter_in_list = MagicMock()
    win._refresh_chapter_list = MagicMock()
    win._load_context_entries_for_chapter = MagicMock()

    return win


def _make_chapter(chapter_id: str = "c1", index: int = 0, continuations=None):
    """构造一个轻量 chapter 对象（用 SimpleNamespace 模拟 Chapter）。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=chapter_id,
        project_id="p1",
        index=index,
        title=f"测试章节{index}",
        content="章节正文",
        continuations=continuations or [],
        metadata={},
    )


def _make_continuation(content: str = "续写内容", status: str = "completed"):
    """构造一个轻量 continuation 对象。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        id="swipe_1",
        content=content,
        status=status,
    )


# ===== 状态缓冲字段初值测试 =====


class TestStateBufferFields:
    """状态缓冲字段初值测试。"""

    def test_init_state_buffer_fields(self) -> None:
        """_make_window 构造后 7 个字段初值正确（镜像 MainWindow.__init__）。"""
        win = _make_window()
        assert win._extract_stream_text_by_chapter == {}
        assert win._ontology_extracting is False
        assert win._ontology_stream_text == ""
        assert win._continuation_chapter_id is None
        assert win._continuation_stream_text_by_chapter == {}
        assert win._audit_chapter_id is None
        assert win._volume_chapter_id is None

    def test_init_fields_match_source(self) -> None:
        """验证 MainWindow.__init__ 源码中确实声明了 7 个字段。"""
        import inspect

        from novelforge.ui.main_window import MainWindow

        source = inspect.getsource(MainWindow.__init__)
        assert "_extract_stream_text_by_chapter" in source
        assert "_ontology_extracting" in source
        assert "_ontology_stream_text" in source
        assert "_continuation_chapter_id" in source
        assert "_continuation_stream_text_by_chapter" in source
        assert "_audit_chapter_id" in source
        assert "_volume_chapter_id" in source


# ===== 续写 chunk 路由测试 =====


class TestContinuationChunkRouting:
    """_on_continuation_chunk_received 路由测试。"""

    def test_chunk_routes_to_buffer_and_ui_when_on_origin(self) -> None:
        """用户停留在发起章节：缓冲追加 + UI 更新。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        win._continuation_stream_text_by_chapter["c1"] = ""
        win._current_chapter = _make_chapter("c1")

        win._on_continuation_chunk_received("chunk1")

        assert win._continuation_stream_text_by_chapter["c1"] == "chunk1"
        win.continuation_panel.append_chunk.assert_called_once_with("chunk1")

    def test_chunk_routes_to_buffer_only_when_switched_away(self) -> None:
        """用户切到其它章节：缓冲追加 + UI 不更新。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        win._continuation_stream_text_by_chapter["c1"] = ""
        win._current_chapter = _make_chapter("c2")  # 切到 c2

        win._on_continuation_chunk_received("chunk1")

        # 缓冲仍追加到发起章节 c1
        assert win._continuation_stream_text_by_chapter["c1"] == "chunk1"
        # UI 不更新（append_chunk 不被调用）
        win.continuation_panel.append_chunk.assert_not_called()

    def test_chunk_dropped_when_no_chapter_id(self) -> None:
        """无发起章节 id（_continuation_chapter_id=None）：直接 return。"""
        win = _make_window()
        win._continuation_chapter_id = None
        win._current_chapter = _make_chapter("c1")

        win._on_continuation_chunk_received("chunk1")

        # 缓冲与 UI 均无变化
        assert win._continuation_stream_text_by_chapter == {}
        win.continuation_panel.append_chunk.assert_not_called()

    def test_chunk_accumulates_in_buffer(self) -> None:
        """多个 chunk 应累积到缓冲。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        win._continuation_stream_text_by_chapter["c1"] = ""
        win._current_chapter = _make_chapter("c2")  # 切走，仅缓冲

        win._on_continuation_chunk_received("Hello ")
        win._on_continuation_chunk_received("World")

        assert win._continuation_stream_text_by_chapter["c1"] == "Hello World"


# ===== 续写完成归档测试 =====


class TestContinuationFinishedArchiving:
    """_on_continuation_finished 归档测试。"""

    def test_swipe_saved_to_origin_chapter_when_switched_away(self) -> None:
        """用户切走：swipe 仍存入发起章节，UI 不刷新。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        win._continuation_stream_text_by_chapter["c1"] = "buffered text"
        win._current_chapter = _make_chapter("c2")  # 切到 c2

        continuation = _make_continuation(content="新续写", status="completed")
        win._on_continuation_finished(continuation)

        # swipe 存入发起章节 c1
        win.chapter_service.storage.save_continuation.assert_called_once_with(
            continuation, "c1"
        )
        # UI 不刷新（stop_streaming 不被调用）
        win.continuation_panel.stop_streaming.assert_not_called()
        # set_current_swipe 不被调用（不在发起章节）
        win.continuation_panel.set_current_swipe.assert_not_called()

    def test_swipe_saved_and_ui_refreshed_when_on_origin(self) -> None:
        """用户停留在发起章节：swipe 存入 + UI 刷新。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        win._continuation_stream_text_by_chapter["c1"] = "buffered"
        origin_chapter = _make_chapter("c1")
        win._current_chapter = origin_chapter
        # load_chapter 返回带 continuations 的章节
        loaded_chapter = _make_chapter("c1", continuations=[continuation_obj := _make_continuation()])
        win.storage_service.load_chapter.return_value = loaded_chapter

        continuation = _make_continuation(content="新续写", status="completed")
        win._on_continuation_finished(continuation)

        # swipe 存入发起章节
        win.chapter_service.storage.save_continuation.assert_called_once_with(
            continuation, "c1"
        )
        # UI 刷新
        win.continuation_panel.stop_streaming.assert_called_once()
        win.chapter_editor.set_streaming_locked.assert_called_once_with(False)
        win.continuation_panel.set_current_swipe.assert_called_once()

    def test_cleanup_after_finish(self) -> None:
        """完成后：_continuation_chapter_id 归 None，缓冲 pop。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        win._continuation_stream_text_by_chapter["c1"] = "buffered"
        win._current_chapter = _make_chapter("c1")

        continuation = _make_continuation()
        win._on_continuation_finished(continuation)

        assert win._continuation_chapter_id is None
        assert "c1" not in win._continuation_stream_text_by_chapter

    def test_history_recorded_after_finish(self) -> None:
        """完成后应记录历史日志。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        win._continuation_stream_text_by_chapter["c1"] = ""
        win._current_chapter = _make_chapter("c1")

        continuation = _make_continuation(content="内容", status="completed")
        win._on_continuation_finished(continuation)

        win._record_history.assert_called_once_with(
            swipe_id=continuation.id,
            status="completed",
            output_text="内容",
            error_message="",
        )


# ===== 续写出错清理测试 =====


class TestContinuationErrorCleanup:
    """_on_continuation_error 清理测试。"""

    def test_error_clears_state_when_on_origin(self) -> None:
        """用户停留在发起章节：UI 显示错误 + 清理状态。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        win._continuation_stream_text_by_chapter["c1"] = "buffered"
        win._current_chapter = _make_chapter("c1")

        win._on_continuation_error("网络错误")

        # UI 刷新
        win.continuation_panel.stop_streaming.assert_called_once()
        win.chapter_editor.set_streaming_locked.assert_called_once_with(False)
        win.continuation_panel.show_error.assert_called_once_with("网络错误")
        # 状态清理
        assert win._continuation_chapter_id is None
        assert "c1" not in win._continuation_stream_text_by_chapter

    def test_error_clears_state_when_switched_away(self) -> None:
        """用户切走：UI 不刷新 + 状态仍清理。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        win._continuation_stream_text_by_chapter["c1"] = "buffered"
        win._current_chapter = _make_chapter("c2")

        win._on_continuation_error("网络错误")

        # UI 不刷新
        win.continuation_panel.stop_streaming.assert_not_called()
        win.continuation_panel.show_error.assert_not_called()
        # 状态仍清理
        assert win._continuation_chapter_id is None
        assert "c1" not in win._continuation_stream_text_by_chapter


# ===== 上下文提取 chunk 路由测试 =====


class TestExtractChunkRouting:
    """_on_extract_chunk_received 路由测试。"""

    def test_extract_chunk_routes_to_buffer(self) -> None:
        """chunk 总是缓冲到发起章节。"""
        win = _make_window()
        win._extracting_chapter_id = "c1"
        win._current_chapter = _make_chapter("c1")

        win._on_extract_chunk_received("chunk1")

        assert win._extract_stream_text_by_chapter["c1"] == "chunk1"
        win.continuation_panel.context_preview_panel.update_extraction_progress.assert_called_once_with(
            "chunk1"
        )

    def test_extract_chunk_buffer_only_when_switched_away(self) -> None:
        """用户切走：缓冲追加 + UI 不更新。"""
        win = _make_window()
        win._extracting_chapter_id = "c1"
        win._current_chapter = _make_chapter("c2")

        win._on_extract_chunk_received("chunk1")

        assert win._extract_stream_text_by_chapter["c1"] == "chunk1"
        win.continuation_panel.context_preview_panel.update_extraction_progress.assert_not_called()

    def test_extract_chunk_dropped_when_no_chapter_id(self) -> None:
        """无发起章节 id：直接 return。"""
        win = _make_window()
        win._extracting_chapter_id = None
        win._current_chapter = _make_chapter("c1")

        win._on_extract_chunk_received("chunk1")

        assert win._extract_stream_text_by_chapter == {}
        win.continuation_panel.context_preview_panel.update_extraction_progress.assert_not_called()


# ===== 世界观提取项目级状态测试 =====


class TestOntologyStateTracking:
    """_on_ontology_chunk_received 项目级状态测试。"""

    def test_ontology_chunk_accumulates_to_single_buffer(self) -> None:
        """世界观 chunk 累积到项目级单字符串（非按章缓冲）。"""
        win = _make_window()
        win._ontology_stream_text = ""
        # 面板处于提取态
        win.continuation_panel.context_preview_panel._is_extracting = True

        win._on_ontology_chunk_received("chunk1")
        win._on_ontology_chunk_received("chunk2")

        assert win._ontology_stream_text == "chunk1chunk2"
        assert win.continuation_panel.context_preview_panel.update_ontology_progress.call_count == 2

    def test_ontology_chunk_ui_guarded_when_panel_not_extracting(self) -> None:
        """面板不处于提取态（用户切走）：缓冲仍累积 + UI 不更新。"""
        win = _make_window()
        win._ontology_stream_text = ""
        # 面板不处于提取态（用户切走后 _is_extracting 被重置）
        win.continuation_panel.context_preview_panel._is_extracting = False

        win._on_ontology_chunk_received("chunk1")

        # 缓冲仍累积（项目级，后台继续）
        assert win._ontology_stream_text == "chunk1"
        # UI 不更新
        win.continuation_panel.context_preview_panel.update_ontology_progress.assert_not_called()


# ===== 审计发起章节跟踪测试 =====


class TestAuditChapterIdTracking:
    """审计发起章节跟踪与清理测试。"""

    def test_audit_cancelled_clears_id(self) -> None:
        """_on_audit_cancelled 应清理 _audit_chapter_id。"""
        win = _make_window()
        win._audit_chapter_id = "c1"
        win._audit_worker = MagicMock()

        win._on_audit_cancelled()

        # worker 停止
        win._audit_worker.stop.assert_called_once()
        # 发起章节标记清理
        assert win._audit_chapter_id is None

    def test_audit_cancelled_with_no_worker(self) -> None:
        """_audit_worker 为 None 时 _on_audit_cancelled 不应报错。"""
        win = _make_window()
        win._audit_chapter_id = "c1"
        win._audit_worker = None

        win._on_audit_cancelled()

        assert win._audit_chapter_id is None


# ===== 卷续写发起章节跟踪测试 =====


class TestVolumeChapterIdTracking:
    """_on_volume_continuation_finished 清理测试。"""

    def test_volume_finished_clears_chapter_id(self) -> None:
        """卷续写完成应清理 _volume_chapter_id 与 _volume_orchestrator。"""
        win = _make_window()
        win._volume_chapter_id = "c1"
        win._volume_orchestrator = MagicMock()
        win._volume_orchestrator.get_writing_model.return_value = "model"
        win._volume_orchestrator.get_writing_messages.return_value = []
        # origin chapter 加载成功
        origin = _make_chapter("c1", index=5)
        win.storage_service.load_chapter.return_value = origin
        win._current_chapter = origin

        # 构造无 volume_artifacts 的 continuation（跳过章节创建分支）
        continuation = _make_continuation(content="卷正文", status="completed")
        continuation.volume_artifacts = None
        win._on_volume_continuation_finished(continuation)

        # 清理
        assert win._volume_chapter_id is None
        assert win._volume_orchestrator is None

    def test_volume_finished_records_history(self) -> None:
        """卷续写完成应记录历史日志。"""
        win = _make_window()
        win._volume_chapter_id = "c1"
        win._volume_orchestrator = MagicMock()
        win._volume_orchestrator.get_writing_model.return_value = "model"
        win._volume_orchestrator.get_writing_messages.return_value = []
        origin = _make_chapter("c1", index=5)
        win.storage_service.load_chapter.return_value = origin
        win._current_chapter = origin

        continuation = _make_continuation(content="卷正文", status="completed")
        continuation.volume_artifacts = None
        win._on_volume_continuation_finished(continuation)

        win._record_history.assert_called_once_with(
            swipe_id=continuation.id,
            status="completed",
            output_text="卷正文",
            error_message="",
        )


# ===== _on_chapter_selected 续写流式态恢复测试 =====


class TestChapterSelectedRestore:
    """_on_chapter_selected 续写流式态恢复测试。"""

    def test_restore_streaming_state_when_returning_to_origin(self) -> None:
        """切回续写发起章节：调 restore_streaming_state + set_streaming_locked。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        win._continuation_stream_text_by_chapter["c1"] = "已接收的续写文本"
        # 加载 c1 章节返回
        chapter = _make_chapter("c1")
        win.storage_service.load_chapter.return_value = chapter
        win._current_chapter = None  # 模拟从其它章节切回

        win._on_chapter_selected("c1")

        # 应调 restore_streaming_state
        win.continuation_panel.restore_streaming_state.assert_called_once_with(
            "已接收的续写文本"
        )
        # 应锁定编辑器
        win.chapter_editor.set_streaming_locked.assert_called_once_with(True)
        # 不应调 set_current_swipe / clear_output
        win.continuation_panel.set_current_swipe.assert_not_called()
        win.continuation_panel.clear_output.assert_not_called()

    def test_no_restore_when_switching_to_other_chapter(self) -> None:
        """切到非发起章节：走原 set_current_swipe / clear_output 分支。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        win._continuation_stream_text_by_chapter["c1"] = "buffered"
        # 加载 c2 章节返回（无 continuations）
        chapter = _make_chapter("c2")
        win.storage_service.load_chapter.return_value = chapter
        win._current_chapter = None

        win._on_chapter_selected("c2")

        # 不调 restore_streaming_state
        win.continuation_panel.restore_streaming_state.assert_not_called()
        # chapter_editor.set_streaming_locked 不被调用（非 True）
        win.chapter_editor.set_streaming_locked.assert_not_called()
        # 走 clear_output 分支（chapter.continuations 为空）
        win.continuation_panel.clear_output.assert_called_once()

    def test_set_current_swipe_when_chapter_has_continuations(self) -> None:
        """切到有 continuations 的非发起章节：走 set_current_swipe 分支。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        win._continuation_stream_text_by_chapter["c1"] = "buffered"
        # c2 有 continuations
        swipe = _make_continuation(content="c2 的旧续写")
        chapter = _make_chapter("c2", continuations=[swipe])
        win.storage_service.load_chapter.return_value = chapter
        win._current_chapter = None

        win._on_chapter_selected("c2")

        # 不调 restore_streaming_state
        win.continuation_panel.restore_streaming_state.assert_not_called()
        # 走 set_current_swipe 分支
        win.continuation_panel.set_current_swipe.assert_called_once_with(
            swipe, [swipe]
        )
        win.continuation_panel.clear_output.assert_not_called()

    def test_no_restore_when_buffer_empty(self) -> None:
        """_continuation_chapter_id 匹配但缓冲不存在：走原分支。"""
        win = _make_window()
        win._continuation_chapter_id = "c1"
        # 注意：不设置 _continuation_stream_text_by_chapter["c1"]
        chapter = _make_chapter("c1")
        win.storage_service.load_chapter.return_value = chapter
        win._current_chapter = None

        win._on_chapter_selected("c1")

        # 不调 restore_streaming_state（缓冲不存在）
        win.continuation_panel.restore_streaming_state.assert_not_called()
        # 走 clear_output 分支（chapter.continuations 为空）
        win.continuation_panel.clear_output.assert_called_once()

    def test_no_restore_when_no_continuation_in_progress(self) -> None:
        """无续写进行中（_continuation_chapter_id=None）：走原分支。"""
        win = _make_window()
        win._continuation_chapter_id = None
        chapter = _make_chapter("c1")
        win.storage_service.load_chapter.return_value = chapter
        win._current_chapter = None

        win._on_chapter_selected("c1")

        win.continuation_panel.restore_streaming_state.assert_not_called()
        win.continuation_panel.clear_output.assert_called_once()
