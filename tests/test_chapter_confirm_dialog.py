"""ChapterConfirmDialog 测试。

覆盖 approve/reject/cancel 三种用户操作。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from novelforge.ui.chapter_confirm_dialog import ChapterConfirmDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_chapter_confirm_dialog_approve(qapp) -> None:
    """点通过：返回 ("approve", "")。"""
    dialog = ChapterConfirmDialog(0, "章节正文内容", parent=None)
    dialog._on_approve()
    action, feedback = dialog.get_result()
    assert action == "approve"
    assert feedback == ""


def test_chapter_confirm_dialog_reject(qapp) -> None:
    """输入反馈点提交：返回 ("reject", "调整内容")。"""
    dialog = ChapterConfirmDialog(2, "章节正文", parent=None)
    dialog._on_reject_toggle()  # 展开反馈区
    dialog._feedback_edit.setPlainText("请加强冲突描写")
    dialog._on_submit_reject()
    action, feedback = dialog.get_result()
    assert action == "reject"
    assert feedback == "请加强冲突描写"


def test_chapter_confirm_dialog_cancel(qapp) -> None:
    """点取消：返回 ("cancel", "")。"""
    dialog = ChapterConfirmDialog(1, "章节正文", parent=None)
    dialog._on_cancel()
    action, feedback = dialog.get_result()
    assert action == "cancel"
    assert feedback == ""
