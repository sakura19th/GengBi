"""ArtifactDetailDialog 测试。

覆盖各阶段类型（outline/audit/revise/draft）的内容格式化与展示，
以及关闭按钮行为。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QPushButton

from novelforge.models import ChapterStageArtifact, CritiqueReport, Outline
from novelforge.ui.artifact_detail_dialog import ArtifactDetailDialog
from novelforge.utils.outline_serializer import format_critique, format_outline


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """提供全局 QApplication 单例（离屏平台）。"""
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def _get_text_edit(dialog: ArtifactDetailDialog) -> QPlainTextEdit:
    """从对话框中获取 QPlainTextEdit。"""
    edits = dialog.findChildren(QPlainTextEdit)
    assert len(edits) >= 1, "对话框中未找到 QPlainTextEdit"
    return edits[0]


def test_dialog_outline_stage(qapp) -> None:
    """outline 阶段：文本区含 format_outline 输出。"""
    outline = Outline(
        continuation_goals="推动冲突升级",
        foreshadowing_plan="埋设宝物失效伏笔",
        scenes=[],
    )
    dialog = ArtifactDetailDialog(
        title="第 1 章 · 阶段 1：细纲",
        stage_type="outline",
        outline=outline,
    )
    text_edit = _get_text_edit(dialog)
    text = text_edit.toPlainText()
    # 应含 format_outline 的输出
    expected = format_outline(outline)
    assert expected in text or text.strip() == expected.strip()
    assert "推动冲突升级" in text
    assert dialog.windowTitle() == "第 1 章 · 阶段 1：细纲"
    dialog.close()


def test_dialog_audit_stage(qapp) -> None:
    """audit 阶段：文本区含 format_critique 输出。"""
    critique = CritiqueReport(
        summary="续写质量良好，通过验证",
        issues=[],
        passed=True,
    )
    dialog = ArtifactDetailDialog(
        title="第 1 章 · 阶段 3：审计①",
        stage_type="audit",
        critique=critique,
    )
    text_edit = _get_text_edit(dialog)
    text = text_edit.toPlainText()
    expected = format_critique(critique)
    assert expected in text or text.strip() == expected.strip()
    assert "通过验证" in text or "passed" in text.lower() or "True" in text
    dialog.close()


def test_dialog_revise_stage(qapp) -> None:
    """revise 阶段：文本区含【修订指导】和【修改后正文】。"""
    guidance = {
        "revision_strategy": "修正一致性",
        "key_changes": [],
        "preserve_elements": "对话",
    }
    content = "这是修订后的正文内容。"
    dialog = ArtifactDetailDialog(
        title="第 1 章 · 阶段 4：修改正文①",
        stage_type="revise",
        content=content,
        guidance=guidance,
    )
    text_edit = _get_text_edit(dialog)
    text = text_edit.toPlainText()
    assert "【修订指导】" in text
    assert "【修改后正文】" in text
    assert "修正一致性" in text
    assert content in text
    dialog.close()


def test_dialog_draft_stage(qapp) -> None:
    """draft 阶段：文本区为 content 原文。"""
    content = "这是初稿正文。"
    dialog = ArtifactDetailDialog(
        title="第 1 章 · 阶段 2：初稿",
        stage_type="draft",
        content=content,
    )
    text_edit = _get_text_edit(dialog)
    text = text_edit.toPlainText()
    assert text == content
    dialog.close()


def test_dialog_close_button(qapp) -> None:
    """点击关闭按钮后对话框 accept。"""
    dialog = ArtifactDetailDialog(
        title="测试",
        stage_type="draft",
        content="内容",
    )
    # 查找关闭按钮
    buttons = dialog.findChildren(QPushButton)
    close_btn = None
    for btn in buttons:
        if btn.text() == "关闭":
            close_btn = btn
            break
    assert close_btn is not None, "未找到关闭按钮"

    # 点击关闭按钮（不调用 dialog.exec()，直接触发 clicked 信号）
    close_btn.click()
    # dialog 应已被 accept（result == QDialog.Accepted）
    # 由于未 exec，检查 dialog 的 result 是否为 Accepted
    from PySide6.QtWidgets import QDialog
    assert dialog.result() == QDialog.DialogCode.Accepted
