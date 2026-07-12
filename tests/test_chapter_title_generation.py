"""章节标题生成与编辑测试。

覆盖：
- Continuation.generated_title 字段
- promote_continuation_to_chapter 使用 generated_title
- _build_previous_chapter_titles 构建前章标题列表
- _build_macro_context 注入 previous_chapter_titles
- update_chapter_title 存储方法
- <novelforge_title> 标签正则提取
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from novelforge.core.prompt_assembler import PromptAssembler
from novelforge.core.token_counter import TokenCounter
from novelforge.models import Chapter, Continuation, Project
from novelforge.services.chapter_service import ChapterService
from novelforge.services.storage_service import StorageService


# ===== Fixtures =====


@pytest.fixture
def temp_storage(tmp_path: Path) -> StorageService:
    """提供临时存储服务。"""
    return StorageService(storage_path=tmp_path)


@pytest.fixture
def chapter_service(temp_storage: StorageService) -> ChapterService:
    """提供章节服务。"""
    return ChapterService(temp_storage)


def _make_project_with_chapters(
    storage: StorageService,
    titles: list[str] | None = None,
    n_chapters: int | None = None,
) -> tuple[Project, list[Chapter]]:
    """创建项目 + 章节列表，返回 (project, chapters)。"""
    project = Project(id="proj_test", name="测试项目")
    storage.save_project(project)
    chapters = []
    if titles:
        n = len(titles)
    elif n_chapters:
        n = n_chapters
    else:
        n = 2
    for i in range(n):
        title = titles[i] if titles else f"第{i + 1}章"
        ch = Chapter(
            id=f"ch_{i}",
            project_id="proj_test",
            index=i,
            title=title,
            content=f"章节{i}正文内容",
        )
        storage.save_chapter(ch)
        chapters.append(ch)
    return project, chapters


# ===== Continuation.generated_title 字段测试 =====


def test_continuation_has_generated_title_default() -> None:
    """Continuation.generated_title 默认为空串。"""
    cont = Continuation(id="cont_1", content="正文", model="m")
    assert cont.generated_title == ""


def test_continuation_generated_title_set() -> None:
    """Continuation.generated_title 可设置。"""
    cont = Continuation(
        id="cont_1", content="正文", model="m",
        generated_title="暗流涌动",
    )
    assert cont.generated_title == "暗流涌动"


# ===== promote_continuation_to_chapter 使用 generated_title 测试 =====


def test_promote_uses_generated_title(
    temp_storage: StorageService, chapter_service: ChapterService
) -> None:
    """提升续写时，使用 continuation.generated_title 作为新章节标题。"""
    _, chapters = _make_project_with_chapters(temp_storage, n_chapters=1)
    chapter = temp_storage.load_chapter(chapters[0].id)
    cont = Continuation(
        id="cont_1", content="续写内容", model="m",
        generated_title="风起云涌",
    )
    temp_storage.save_continuation(cont, chapter.id)

    chapter = temp_storage.load_chapter(chapter.id)
    cont = chapter.continuations[0]
    _, new_chapter = chapter_service.promote_continuation_to_chapter(chapter, cont)

    assert new_chapter.title == "风起云涌"


def test_promote_falls_back_to_default_title_when_no_generated_title(
    temp_storage: StorageService, chapter_service: ChapterService
) -> None:
    """无 generated_title 时回退到默认"第N章"。"""
    _, chapters = _make_project_with_chapters(temp_storage, n_chapters=1)
    chapter = temp_storage.load_chapter(chapters[0].id)
    cont = Continuation(id="cont_1", content="续写内容", model="m")
    temp_storage.save_continuation(cont, chapter.id)

    chapter = temp_storage.load_chapter(chapter.id)
    cont = chapter.continuations[0]
    _, new_chapter = chapter_service.promote_continuation_to_chapter(chapter, cont)

    assert new_chapter.title == f"第{new_chapter.index + 1}章"


# ===== _build_previous_chapter_titles 测试 =====


def test_build_previous_chapter_titles_normal(
    temp_storage: StorageService,
) -> None:
    """续写模式：前章标题含当前章节。"""
    titles = ["初入江湖", "暗流涌动", "风起云涌"]
    _, chapters = _make_project_with_chapters(temp_storage, titles=titles)
    assembler = PromptAssembler(TokenCounter())

    result = assembler._build_previous_chapter_titles(
        chapters, chapters[2], exclude_current=False,
    )

    assert "1. 初入江湖" in result
    assert "2. 暗流涌动" in result
    assert "3. 风起云涌" in result


def test_build_previous_chapter_titles_exclude_current(
    temp_storage: StorageService,
) -> None:
    """重写模式：前章标题不含当前章节。"""
    titles = ["初入江湖", "暗流涌动", "风起云涌"]
    _, chapters = _make_project_with_chapters(temp_storage, titles=titles)
    assembler = PromptAssembler(TokenCounter())

    result = assembler._build_previous_chapter_titles(
        chapters, chapters[2], exclude_current=True,
    )

    assert "1. 初入江湖" in result
    assert "2. 暗流涌动" in result
    assert "风起云涌" not in result


def test_build_previous_chapter_titles_empty() -> None:
    """无章节时返回空串。"""
    assembler = PromptAssembler(TokenCounter())
    result = assembler._build_previous_chapter_titles([], None)
    assert result == ""


def test_build_previous_chapter_titles_max_limit(
    temp_storage: StorageService,
) -> None:
    """超过 max_titles 时只取最后 N 章。"""
    titles = [f"章{i}" for i in range(25)]
    _, chapters = _make_project_with_chapters(temp_storage, titles=titles)
    assembler = PromptAssembler(TokenCounter())

    result = assembler._build_previous_chapter_titles(
        chapters, chapters[-1], exclude_current=False, max_titles=5,
    )

    lines = result.split("\n")
    assert len(lines) == 5
    # 最后 5 章：章20~章24
    assert "章24" in lines[-1]


# ===== _build_macro_context 注入 previous_chapter_titles 测试 =====


def test_macro_context_injects_previous_chapter_titles(
    temp_storage: StorageService,
) -> None:
    """_build_macro_context 将 previous_chapter_titles 注入 ctx.extra。"""
    _, chapters = _make_project_with_chapters(
        temp_storage, titles=["初入江湖", "暗流涌动"]
    )
    assembler = PromptAssembler(TokenCounter())

    ctx = assembler._build_macro_context(
        current_chapter=chapters[1],
        novel_profile=None,
        target_words=2000,
        previous_chapter_titles="1. 初入江湖\n2. 暗流涌动",
    )

    assert "previous_chapter_titles" in ctx.extra
    assert "初入江湖" in ctx.extra["previous_chapter_titles"]


def test_macro_context_previous_chapter_titles_fallback() -> None:
    """空 previous_chapter_titles 注入占位文本。"""
    assembler = PromptAssembler(TokenCounter())

    ctx = assembler._build_macro_context(
        current_chapter=None,
        novel_profile=None,
        target_words=2000,
        previous_chapter_titles="",
    )

    assert "previous_chapter_titles" in ctx.extra
    assert "无前文章节" in ctx.extra["previous_chapter_titles"]


# ===== <novelforge_title> 标签正则提取测试 =====


def test_extract_title_from_novelforge_title_tag() -> None:
    """从 <novelforge_title> 标签提取标题。"""
    content = (
        "<novelforge_thinking>分析...</novelforge_thinking>\n"
        "<novelforge_title>暗流涌动</novelforge_title>\n"
        "<novelforge_chapter>正文内容</novelforge_chapter>"
    )
    match = re.search(
        r"<novelforge_title>\s*(.*?)\s*</novelforge_title>",
        content, re.DOTALL,
    )
    assert match is not None
    assert match.group(1).strip() == "暗流涌动"


def test_extract_title_no_tag() -> None:
    """无 <novelforge_title> 标签时返回空。"""
    content = "<novelforge_chapter>正文内容</novelforge_chapter>"
    match = re.search(
        r"<novelforge_title>\s*(.*?)\s*</novelforge_title>",
        content, re.DOTALL,
    )
    assert match is None


def test_extract_title_multiline() -> None:
    """标题跨行时正确提取。"""
    content = (
        "<novelforge_title>\n  风起云涌  \n</novelforge_title>\n"
        "<novelforge_chapter>正文</novelforge_chapter>"
    )
    match = re.search(
        r"<novelforge_title>\s*(.*?)\s*</novelforge_title>",
        content, re.DOTALL,
    )
    assert match is not None
    assert match.group(1).strip() == "风起云涌"


# ===== update_chapter_title 存储方法测试 =====


def test_update_chapter_title(temp_storage: StorageService) -> None:
    """update_chapter_title 只更新标题列，不影响正文。"""
    _, chapters = _make_project_with_chapters(temp_storage, n_chapters=1)
    ch = chapters[0]
    original_content = ch.content

    temp_storage.update_chapter_title(ch.id, "新标题")

    reloaded = temp_storage.load_chapter(ch.id)
    assert reloaded is not None
    assert reloaded.title == "新标题"
    # 正文不变
    assert reloaded.content == original_content


def test_update_chapter_title_does_not_overwrite_content(
    temp_storage: StorageService,
) -> None:
    """update_chapter_title 不用空 content 覆盖正文文件。"""
    _, chapters = _make_project_with_chapters(temp_storage, n_chapters=1)
    ch = chapters[0]
    original_content = ch.content

    # 模拟 list_chapters 返回 content="" 的场景
    meta_chapters = temp_storage.list_chapters("proj_test")
    assert meta_chapters[0].content == ""  # list_chapters 不加载正文

    # 用 update_chapter_title 更新标题
    temp_storage.update_chapter_title(ch.id, "重命名标题")

    # 正文文件未被覆盖
    reloaded = temp_storage.load_chapter(ch.id)
    assert reloaded is not None
    assert reloaded.content == original_content
    assert reloaded.title == "重命名标题"
