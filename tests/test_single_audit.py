"""单章续写审计与修正功能测试。

覆盖：
- phase_single_audit.txt 模板存在性与占位符完整性
- AuditWorker 流式输出与停止
- AuditDialog 流式追加/完成/采纳/取消
- MainWindow 审计→采纳→修正流程

运行方式：
    python -m pytest tests/test_single_audit.py -v
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 确保项目根目录在 sys.path 中
import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from novelforge.services.audit_worker import AuditWorker
from novelforge.services.llm_client import StreamChunk
from novelforge.utils.paths import get_agent_prompt_path, load_text_resource


# ===== 模板测试 =====


class TestSingleAuditTemplate:
    """审计提示词模板测试。"""

    def test_single_audit_template_exists(self) -> None:
        """模板文件应存在。"""
        path = get_agent_prompt_path("single_audit")
        assert path.exists(), f"模板文件不存在: {path}"

    def test_single_audit_template_placeholders(self) -> None:
        """模板应含 4 个占位符，不含卷级专用占位符。"""
        path = get_agent_prompt_path("single_audit")
        content = load_text_resource(path)

        # 必须含的占位符
        assert "{{written_text}}" in content
        assert "{{user_input}}" in content
        assert "{{world_ontology}}" in content
        assert "{{protagonist_profile}}" in content

        # 不应含卷级专用占位符
        assert "{{snapshot}}" not in content
        assert "{{outline}}" not in content
        assert "{{previous_chapters_text}}" not in content

    def test_single_audit_template_dimensions(self) -> None:
        """模板应聚焦 5 个维度，不含 pacing/engagement 等。"""
        path = get_agent_prompt_path("single_audit")
        content = load_text_resource(path)

        # 必须含的 5 维度
        assert "consistency" in content
        assert "protagonist_consistency" in content
        assert "worldview_consistency" in content
        assert "style" in content
        assert "coherence" in content

        # 不应含卷级专用维度
        assert "## 2. pacing" not in content
        assert "## 3. engagement" not in content
        assert "## 4. structure" not in content
        assert "## 6. foreshadowing" not in content
        assert "## 7. characters" not in content

    def test_single_audit_template_output_format(self) -> None:
        """模板应含 summary/issues/passed 输出格式说明。"""
        path = get_agent_prompt_path("single_audit")
        content = load_text_resource(path)

        assert "summary" in content
        assert "issues" in content
        assert "passed" in content
        assert "【主角一致性审计】" in content
        assert "【世界观一致性审计】" in content


class TestAuditRewriteTemplate:
    """审计后修改提示词模板测试（新统一流程）。"""

    def test_audit_rewrite_template_exists(self) -> None:
        """模板文件应存在。"""
        path = get_agent_prompt_path("audit_rewrite")
        assert path.exists(), f"模板文件不存在: {path}"

    def test_audit_rewrite_template_placeholders(self) -> None:
        """模板应含 9 个占位符，不含 revision_guidance。"""
        path = get_agent_prompt_path("audit_rewrite")
        content = load_text_resource(path)

        # 必须含的占位符
        assert "{{original_content}}" in content
        assert "{{critique}}" in content
        assert "{{world_ontology}}" in content
        assert "{{protagonist_profile}}" in content
        assert "{{custom_audit_rules}}" in content
        assert "{{previous_chapters_text}}" in content
        assert "{{chapter_plan}}" in content
        assert "{{outline}}" in content
        assert "{{pacing_speed}}" in content
        assert "{{target_words}}" in content

        # 不应含 revision_guidance（新流程审计报告即修改意见）
        assert "{{revision_guidance}}" not in content

    def test_audit_rewrite_template_focus(self) -> None:
        """模板应聚焦修改意见，含严格输出要求。"""
        path = get_agent_prompt_path("audit_rewrite")
        content = load_text_resource(path)

        # 聚焦修改意见
        assert "修改意见" in content
        assert "审计结果与修改意见" in content
        # 严格输出要求
        assert "重写完整正文" in content
        assert "严禁续写或追加" in content
        assert "直接输出正文" in content


# ===== AuditWorker 测试 =====


class TestAuditWorker:
    """审计 worker 测试。"""

    def test_audit_worker_init(self) -> None:
        """AuditWorker 应正确存储参数。"""
        worker = AuditWorker(
            base_url="http://test",
            api_key="key",
            model="gpt-4",
            messages=[{"role": "system", "content": "test"}],
            temperature=0.3,
            max_tokens=2000,
        )
        assert worker.base_url == "http://test"
        assert worker.api_key == "key"
        assert worker.model == "gpt-4"
        assert worker.temperature == 0.3
        assert worker.max_tokens == 2000
        assert len(worker.messages) == 1

    def test_audit_worker_signals(self) -> None:
        """AuditWorker 应定义所需信号。"""
        worker = AuditWorker(
            base_url="http://test",
            api_key="key",
            model="gpt-4",
            messages=[],
        )
        # 验证信号存在
        assert hasattr(worker, "chunk_received")
        assert hasattr(worker, "finished")
        assert hasattr(worker, "error")
        assert hasattr(worker, "token_count")
        assert hasattr(worker, "rate_limit_warning")
        assert hasattr(worker, "auth_error")

    def test_audit_worker_stop(self) -> None:
        """stop() 应设置停止事件。"""
        worker = AuditWorker(
            base_url="http://test",
            api_key="key",
            model="gpt-4",
            messages=[],
        )
        assert not worker._stop_event.is_set()
        worker.stop()
        assert worker._stop_event.is_set()


# ===== AuditDialog 测试 =====


@pytest.fixture
def qapp():
    """提供 Qt 应用实例。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestAuditDialog:
    """审计对话框测试。"""

    def test_audit_dialog_init(self, qapp) -> None:
        """AuditDialog 应正确初始化。"""
        from novelforge.ui.audit_dialog import AuditDialog
        dialog = AuditDialog()
        assert dialog.windowTitle() == "续写审计"
        # 流式中应只读
        assert dialog._text_edit.isReadOnly()
        # 采纳按钮初始应禁用
        assert not dialog._accept_btn.isEnabled()
        dialog.deleteLater()

    def test_audit_dialog_append_chunk(self, qapp) -> None:
        """append_chunk 应累积文本。"""
        from novelforge.ui.audit_dialog import AuditDialog
        dialog = AuditDialog()
        dialog.append_chunk("Hello ")
        dialog.append_chunk("World")
        assert dialog.get_edited_text() == "Hello World"
        dialog.deleteLater()

    def test_audit_dialog_finish_streaming(self, qapp) -> None:
        """finish_streaming 应设为可编辑并启用采纳按钮。"""
        from novelforge.ui.audit_dialog import AuditDialog
        dialog = AuditDialog()
        dialog.append_chunk("partial")
        dialog.finish_streaming("full report")
        # 应可编辑
        assert not dialog._text_edit.isReadOnly()
        # 采纳按钮应启用
        assert dialog._accept_btn.isEnabled()
        # 文本应为完整报告
        assert dialog.get_edited_text() == "full report"
        dialog.deleteLater()

    def test_audit_dialog_accept_signal(self, qapp) -> None:
        """点击采纳应 emit accepted_text 信号。"""
        from novelforge.ui.audit_dialog import AuditDialog
        dialog = AuditDialog()
        dialog.finish_streaming("report content")
        received: list[str] = []
        dialog.accepted_text.connect(lambda text: received.append(text))
        dialog._on_accept_clicked()
        assert len(received) == 1
        assert received[0] == "report content"
        dialog.deleteLater()

    def test_audit_dialog_cancel_signal(self, qapp) -> None:
        """点击取消应 emit cancelled 信号。"""
        from novelforge.ui.audit_dialog import AuditDialog
        dialog = AuditDialog()
        received: list[bool] = []
        dialog.cancelled.connect(lambda: received.append(True))
        dialog._on_cancel_clicked()
        assert len(received) == 1
        dialog.deleteLater()

    def test_audit_dialog_fail(self, qapp) -> None:
        """fail() 应禁用采纳按钮。"""
        from novelforge.ui.audit_dialog import AuditDialog
        dialog = AuditDialog()
        dialog.fail("network error")
        assert not dialog._accept_btn.isEnabled()
        assert "network error" in dialog._status_label.text()
        dialog.deleteLater()


# ===== 辅助方法测试 =====


class TestFormatHelpers:
    """MainWindow 格式化辅助方法测试。"""

    def test_format_world_ontology_none(self) -> None:
        """None 应返回占位文本。"""
        from novelforge.ui.main_window import MainWindow
        result = MainWindow._format_world_ontology(None)
        assert result == "（无世界观底层）"

    def test_format_world_ontology_dict(self) -> None:
        """dict 应序列化为 JSON。"""
        from novelforge.ui.main_window import MainWindow
        result = MainWindow._format_world_ontology({"dim": "value"})
        assert '"dim"' in result
        assert '"value"' in result

    def test_format_protagonist_profile_none(self) -> None:
        """None 应返回占位文本。"""
        from novelforge.ui.main_window import MainWindow
        result = MainWindow._format_protagonist_profile(None)
        assert result == "（无主角形象档案）"

    def test_format_protagonist_profile_dict(self) -> None:
        """dict 应序列化为 JSON。"""
        from novelforge.ui.main_window import MainWindow
        result = MainWindow._format_protagonist_profile({"name": "林风"})
        assert '"name"' in result
        assert '"林风"' in result
