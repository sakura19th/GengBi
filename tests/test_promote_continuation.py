"""续写提升为章节模型测试。

覆盖 ChapterService.promote_continuation_to_chapter：
- 提升后新章节 index=原+1，content=续写内容
- 后续章节 index 后移 1
- 原续写记录从存储删除
- 原章节正文不变
- delete_continuation 删除续写记录
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from novelforge.models import Chapter, Continuation, Project
from novelforge.services.chapter_service import ChapterService
from novelforge.services.storage_service import StorageService


@pytest.fixture
def temp_storage(tmp_path: Path) -> StorageService:
    """提供临时存储服务。"""
    return StorageService(storage_path=tmp_path)


@pytest.fixture
def chapter_service(temp_storage: StorageService) -> ChapterService:
    """提供章节服务。"""
    return ChapterService(temp_storage)


def _make_project_with_chapters(
    storage: StorageService, n_chapters: int = 2
) -> tuple[Project, list[Chapter]]:
    """创建项目 + N 个章节，返回 (project, chapters)。"""
    project = Project(id="proj_test", name="测试项目")
    storage.save_project(project)
    chapters = []
    for i in range(n_chapters):
        ch = Chapter(
            id=f"ch_{i}",
            project_id="proj_test",
            index=i,
            title=f"第{i + 1}章",
            content=f"章节{i}正文",
        )
        storage.save_chapter(ch)
        chapters.append(ch)
    return project, chapters


def _make_continuation(content: str = "续写正文") -> Continuation:
    """创建续写。"""
    return Continuation(id="cont_1", content=content, model="m")


# ===== promote_continuation_to_chapter =====


def test_promote_creates_new_chapter_at_next_index(
    temp_storage: StorageService, chapter_service: ChapterService
) -> None:
    """提升后续写变为新章节，index=原+1，content=续写内容。"""
    _, chapters = _make_project_with_chapters(temp_storage, n_chapters=1)
    chapter = temp_storage.load_chapter(chapters[0].id)
    cont = _make_continuation("续写内容X")
    temp_storage.save_continuation(cont, chapter.id)

    chapter = temp_storage.load_chapter(chapter.id)
    cont = chapter.continuations[0]
    _, new_chapter = chapter_service.promote_continuation_to_chapter(chapter, cont)

    assert new_chapter.index == chapter.index + 1
    assert new_chapter.content == "续写内容X"
    assert new_chapter.project_id == chapter.project_id
    assert new_chapter.word_count == len("续写内容X")


def test_promote_shifts_later_chapters(
    temp_storage: StorageService, chapter_service: ChapterService
) -> None:
    """提升后当前章节之后的章节 index 后移 1。"""
    _, chapters = _make_project_with_chapters(temp_storage, n_chapters=3)
    # chapters: ch_0(idx0), ch_1(idx1), ch_2(idx2)
    chapter = temp_storage.load_chapter(chapters[0].id)
    cont = _make_continuation()
    temp_storage.save_continuation(cont, chapter.id)

    chapter = temp_storage.load_chapter(chapter.id)
    cont = chapter.continuations[0]
    chapter_service.promote_continuation_to_chapter(chapter, cont)

    # ch_1 应从 idx1 后移到 idx2，ch_2 从 idx2 后移到 idx3
    ch1 = temp_storage.load_chapter(chapters[1].id)
    ch2 = temp_storage.load_chapter(chapters[2].id)
    assert ch1.index == 2
    assert ch2.index == 3


def test_promote_deletes_continuation(
    temp_storage: StorageService, chapter_service: ChapterService
) -> None:
    """提升后原续写记录从存储删除。"""
    _, chapters = _make_project_with_chapters(temp_storage, n_chapters=1)
    chapter = temp_storage.load_chapter(chapters[0].id)
    cont = _make_continuation()
    temp_storage.save_continuation(cont, chapter.id)

    chapter = temp_storage.load_chapter(chapter.id)
    cont_id = chapter.continuations[0].id
    chapter_service.promote_continuation_to_chapter(chapter, chapter.continuations[0])

    # 存储中续写已删除
    remaining = temp_storage.list_continuations(chapter.id)
    assert all(c.id != cont_id for c in remaining)


def test_promote_chapter_content_unchanged(
    temp_storage: StorageService, chapter_service: ChapterService
) -> None:
    """提升后原章节正文不变。"""
    _, chapters = _make_project_with_chapters(temp_storage, n_chapters=1)
    chapter = temp_storage.load_chapter(chapters[0].id)
    original_content = chapter.content
    cont = _make_continuation("续写内容Y")
    temp_storage.save_continuation(cont, chapter.id)

    chapter = temp_storage.load_chapter(chapter.id)
    cont = chapter.continuations[0]
    updated_chapter, _ = chapter_service.promote_continuation_to_chapter(chapter, cont)

    assert updated_chapter.content == original_content


def test_promote_removes_continuation_from_chapter_list(
    temp_storage: StorageService, chapter_service: ChapterService
) -> None:
    """提升后 chapter.continuations 不再包含该续写。"""
    _, chapters = _make_project_with_chapters(temp_storage, n_chapters=1)
    chapter = temp_storage.load_chapter(chapters[0].id)
    cont = _make_continuation()
    temp_storage.save_continuation(cont, chapter.id)

    chapter = temp_storage.load_chapter(chapter.id)
    cont = chapter.continuations[0]
    updated_chapter, _ = chapter_service.promote_continuation_to_chapter(chapter, cont)

    assert all(c.id != cont.id for c in updated_chapter.continuations)


# ===== delete_continuation =====


def test_delete_continuation_removes_record(
    temp_storage: StorageService, chapter_service: ChapterService
) -> None:
    """删除续写后 list_continuations 不含该 id。"""
    _, chapters = _make_project_with_chapters(temp_storage, n_chapters=1)
    chapter = temp_storage.load_chapter(chapters[0].id)
    cont = _make_continuation()
    temp_storage.save_continuation(cont, chapter.id)

    chapter = temp_storage.load_chapter(chapter.id)
    cont = chapter.continuations[0]
    chapter_service.delete_continuation(chapter, cont)

    remaining = temp_storage.list_continuations(chapter.id)
    assert all(c.id != cont.id for c in remaining)
