"""M5 里程碑完善与打磨测试。

覆盖：
1. 导出完整 TXT（含/不含标题）
2. 导出单章 TXT/Markdown
3. 项目备份 zip 导出/导入（manifest 结构、章节恢复、续写恢复）
4. 历史日志记录与查询（按项目、章节、时间筛选）
5. 历史日志详情查看
6. 历史日志删除与清空
7. 字体设置持久化
8. 主题切换配置持久化
9. Storage history_log 方法
10. StorageService 同步包装方法
11. HistoryPanel UI
12. FontSettingsDialog UI
13. ChapterListWidget 导出菜单
14. PyInstaller 打包配置文件
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

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


def make_continuation(
    cont_id: str = "cont_1",
    content: str = "续写内容",
    model: str = "gpt-4o",
    status: str = "completed",
    is_accepted: bool = False,
) -> Any:
    """构建测试 Continuation 对象。"""
    from novelforge.models import Continuation

    return Continuation(
        id=cont_id,
        content=content,
        model=model,
        status=status,
        is_accepted=is_accepted,
        created_at=datetime.now(),
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
    )
    return Project(
        id=project_id,
        name=title,
        novel_profile=profile,
    )


@pytest.fixture
def storage_service(tmp_path):
    """创建临时存储服务。"""
    from novelforge.services.storage_service import StorageService

    service = StorageService(storage_path=tmp_path)
    yield service
    try:
        service.shutdown()
    except Exception:
        pass


@pytest.fixture
def preset_service(tmp_path):
    """创建临时预设服务。"""
    from novelforge.services.preset_service import PresetService

    return PresetService(storage_path=tmp_path)


@pytest.fixture
def regex_service(tmp_path):
    """创建临时正则服务。"""
    from novelforge.services.regex_service import RegexService

    return RegexService(storage_path=tmp_path)


@pytest.fixture
def history_service(storage_service):
    """创建历史日志服务。"""
    from novelforge.services.history_service import HistoryService

    return HistoryService(storage_service)


@pytest.fixture
def project_with_chapters(storage_service):
    """创建带章节的项目。"""
    project = storage_service.create_project(name="测试小说")
    chapters = []
    for i in range(3):
        ch = make_chapter(
            index=i,
            title=f"第{i + 1}章",
            content=f"这是第{i + 1}章的正文内容，包含一些文字。",
            project_id=project.id,
        )
        storage_service.save_chapter(ch)
        chapters.append(ch)
    return project, chapters


# ===== 1. 导出完整 TXT 测试 =====


class TestExportFullTxt:
    """导出完整 TXT 测试。"""

    def test_export_full_txt_with_titles(
        self, storage_service, project_with_chapters, tmp_path
    ) -> None:
        """测试导出完整 TXT（含标题）。"""
        from novelforge.services.exporter import export_full_txt

        project, chapters = project_with_chapters
        output = tmp_path / "full.txt"
        count = export_full_txt(
            storage_service, project.id, output, include_titles=True
        )

        assert output.exists()
        text = output.read_text(encoding="utf-8")
        # 含标题
        assert "第1章" in text
        assert "第2章" in text
        assert "第3章" in text
        # 含正文
        assert "这是第1章的正文内容" in text
        # 返回字数
        expected = sum(len(c.content) for c in chapters)
        assert count == expected

    def test_export_full_txt_without_titles(
        self, storage_service, project_with_chapters, tmp_path
    ) -> None:
        """测试导出完整 TXT（不含标题）。"""
        from novelforge.services.exporter import export_full_txt

        project, chapters = project_with_chapters
        output = tmp_path / "no_title.txt"
        count = export_full_txt(
            storage_service, project.id, output, include_titles=False
        )

        text = output.read_text(encoding="utf-8")
        # 不含标题（"第N章" 不应作为标题行出现，但正文里没有这个字符串）
        # 注意：正文内容里没有"第N章"，所以可以检查
        assert "这是第1章的正文内容" in text
        assert count == sum(len(c.content) for c in chapters)

    def test_export_full_txt_creates_parent_dir(
        self, storage_service, project_with_chapters, tmp_path
    ) -> None:
        """测试导出时自动创建父目录。"""
        from novelforge.services.exporter import export_full_txt

        project, _ = project_with_chapters
        output = tmp_path / "subdir" / "nested" / "full.txt"
        export_full_txt(storage_service, project.id, output)
        assert output.exists()

    def test_export_full_txt_empty_project(self, storage_service, tmp_path) -> None:
        """测试导出空项目。"""
        from novelforge.services.exporter import export_full_txt

        project = storage_service.create_project(name="空项目")
        output = tmp_path / "empty.txt"
        count = export_full_txt(storage_service, project.id, output)

        assert count == 0
        assert output.exists()
        assert output.read_text(encoding="utf-8") == ""

    def test_export_full_txt_chapter_order(
        self, storage_service, tmp_path
    ) -> None:
        """测试章节按 index 顺序拼接。"""
        from novelforge.services.exporter import export_full_txt

        project = storage_service.create_project(name="顺序测试")
        # 故意乱序保存
        ch2 = make_chapter(index=2, title="第二章", content="CCC", project_id=project.id)
        ch0 = make_chapter(index=0, title="第一章", content="AAA", project_id=project.id)
        ch1 = make_chapter(index=1, title="第二章", content="BBB", project_id=project.id)
        storage_service.save_chapter(ch2)
        storage_service.save_chapter(ch0)
        storage_service.save_chapter(ch1)

        output = tmp_path / "order.txt"
        export_full_txt(storage_service, project.id, output, include_titles=False)

        text = output.read_text(encoding="utf-8")
        # AAA 应在 BBB 之前，BBB 应在 CCC 之前
        assert text.index("AAA") < text.index("BBB") < text.index("CCC")


# ===== 2. 导出单章 TXT/Markdown 测试 =====


class TestExportChapter:
    """导出单章 TXT/Markdown 测试。"""

    def test_export_chapter_txt(self, tmp_path) -> None:
        """测试导出单章 TXT。"""
        from novelforge.services.exporter import export_chapter_txt

        chapter = make_chapter(index=0, title="测试章", content="章节正文内容")
        output = tmp_path / "chapter.txt"
        count = export_chapter_txt(chapter, output)

        assert count == len("章节正文内容")
        text = output.read_text(encoding="utf-8")
        assert text == "章节正文内容"

    def test_export_chapter_markdown(self, tmp_path) -> None:
        """测试导出单章 Markdown（含 H1 标题）。"""
        from novelforge.services.exporter import export_chapter_markdown

        chapter = make_chapter(index=2, title="测试章", content="Markdown 正文")
        output = tmp_path / "chapter.md"
        count = export_chapter_markdown(chapter, output)

        assert count == len("Markdown 正文")
        text = output.read_text(encoding="utf-8")
        # 含 H1 章节标题
        assert text.startswith("# 第3章 测试章")
        assert "Markdown 正文" in text
        # 标题后有空行
        assert "# 第3章 测试章\n\n" in text

    def test_export_chapter_txt_empty_content(self, tmp_path) -> None:
        """测试导出空内容章节。"""
        from novelforge.services.exporter import export_chapter_txt

        chapter = make_chapter(index=0, title="空章", content="")
        output = tmp_path / "empty.txt"
        count = export_chapter_txt(chapter, output)

        assert count == 0
        assert output.read_text(encoding="utf-8") == ""

    def test_export_chapter_markdown_creates_parent_dir(self, tmp_path) -> None:
        """测试导出 Markdown 自动创建父目录。"""
        from novelforge.services.exporter import export_chapter_markdown

        chapter = make_chapter(index=0, content="内容")
        output = tmp_path / "sub" / "chapter.md"
        export_chapter_markdown(chapter, output)
        assert output.exists()


# ===== 3. 项目备份 zip 导出/导入测试 =====


class TestProjectBackup:
    """项目备份 zip 导出/导入测试。"""

    def test_export_project_backup_creates_zip(
        self, storage_service, preset_service, regex_service,
        project_with_chapters, tmp_path
    ) -> None:
        """测试导出项目备份生成 zip。"""
        from novelforge.services.exporter import export_project_backup

        project, chapters = project_with_chapters
        output = tmp_path / "backup.zip"
        manifest_path = export_project_backup(
            storage_service, preset_service, regex_service, project.id, output
        )

        assert manifest_path == "manifest.json"
        assert output.exists()
        assert zipfile.is_zipfile(output)

    def test_export_project_backup_manifest_structure(
        self, storage_service, preset_service, regex_service,
        project_with_chapters, tmp_path
    ) -> None:
        """测试 manifest.json 结构。"""
        from novelforge.services.exporter import export_project_backup

        project, _ = project_with_chapters
        output = tmp_path / "backup.zip"
        export_project_backup(
            storage_service, preset_service, regex_service, project.id, output
        )

        with zipfile.ZipFile(output, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))

        # manifest 结构字段
        assert "project" in manifest
        assert "presets" in manifest
        assert "regex_scripts" in manifest
        assert "exported_at" in manifest
        assert "version" in manifest
        assert manifest["version"] == "1.0"
        # project 字段含 id 和 name
        assert manifest["project"]["id"] == project.id
        assert manifest["project"]["name"] == "测试小说"

    def test_export_project_backup_contains_chapters(
        self, storage_service, preset_service, regex_service,
        project_with_chapters, tmp_path
    ) -> None:
        """测试 zip 包含章节文件。"""
        from novelforge.services.exporter import export_project_backup

        project, chapters = project_with_chapters
        output = tmp_path / "backup.zip"
        export_project_backup(
            storage_service, preset_service, regex_service, project.id, output
        )

        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()
            # 每章有 .txt 和 .meta.json
            for ch in chapters:
                assert f"chapters/{ch.id}.txt" in names
                assert f"chapters/{ch.id}.meta.json" in names
            # project.json
            assert "project.json" in names
            # continuations.json
            assert "continuations.json" in names

    def test_export_project_backup_with_continuations(
        self, storage_service, preset_service, regex_service, tmp_path
    ) -> None:
        """测试 zip 包含续写版本。"""
        from novelforge.services.exporter import export_project_backup

        project = storage_service.create_project(name="续写测试")
        chapter = make_chapter(index=0, title="第一章", content="正文", project_id=project.id)
        storage_service.save_chapter(chapter)

        cont = make_continuation(cont_id="cont_1", content="续写内容", model="gpt-4o")
        storage_service.save_continuation(cont, chapter.id)

        output = tmp_path / "backup.zip"
        export_project_backup(
            storage_service, preset_service, regex_service, project.id, output
        )

        with zipfile.ZipFile(output, "r") as zf:
            conts = json.loads(zf.read("continuations.json"))

        assert len(conts) == 1
        assert conts[0]["id"] == "cont_1"
        assert conts[0]["content"] == "续写内容"
        assert conts[0]["chapter_id"] == chapter.id

    def test_import_project_backup_restores_project(
        self, storage_service, preset_service, regex_service,
        project_with_chapters, tmp_path
    ) -> None:
        """测试导入项目备份恢复项目。"""
        from novelforge.services.exporter import (
            export_project_backup,
            import_project_backup,
        )

        project, chapters = project_with_chapters
        backup_zip = tmp_path / "backup.zip"
        export_project_backup(
            storage_service, preset_service, regex_service, project.id, backup_zip
        )

        # 导入到新项目
        new_project_id = import_project_backup(
            storage_service, preset_service, regex_service, backup_zip
        )

        assert new_project_id != project.id
        # 验证新项目存在
        new_project = storage_service.load_project(new_project_id)
        assert new_project is not None
        assert new_project.name == "测试小说"

    def test_import_project_backup_restores_chapters(
        self, storage_service, preset_service, regex_service,
        project_with_chapters, tmp_path
    ) -> None:
        """测试导入项目备份恢复章节。"""
        from novelforge.services.exporter import (
            export_project_backup,
            import_project_backup,
        )

        project, chapters = project_with_chapters
        backup_zip = tmp_path / "backup.zip"
        export_project_backup(
            storage_service, preset_service, regex_service, project.id, backup_zip
        )

        new_project_id = import_project_backup(
            storage_service, preset_service, regex_service, backup_zip
        )

        # 验证章节数量
        new_chapters = storage_service.list_chapters(new_project_id)
        assert len(new_chapters) == len(chapters)
        # 验证章节顺序与标题
        for i, ch in enumerate(new_chapters):
            assert ch.index == i
            assert ch.title == f"第{i + 1}章"

    def test_import_project_backup_restores_continuations(
        self, storage_service, preset_service, regex_service, tmp_path
    ) -> None:
        """测试导入项目备份恢复续写版本。"""
        from novelforge.services.exporter import (
            export_project_backup,
            import_project_backup,
        )

        project = storage_service.create_project(name="续写导入测试")
        chapter = make_chapter(index=0, title="第一章", content="正文", project_id=project.id)
        storage_service.save_chapter(chapter)
        cont = make_continuation(cont_id="cont_orig", content="续写内容", model="gpt-4o")
        storage_service.save_continuation(cont, chapter.id)

        backup_zip = tmp_path / "backup.zip"
        export_project_backup(
            storage_service, preset_service, regex_service, project.id, backup_zip
        )

        new_project_id = import_project_backup(
            storage_service, preset_service, regex_service, backup_zip
        )

        new_chapters = storage_service.list_chapters(new_project_id)
        assert len(new_chapters) == 1
        # 加载完整章节（含续写）
        full_chapter = storage_service.load_chapter(new_chapters[0].id)
        assert len(full_chapter.continuations) == 1
        assert full_chapter.continuations[0].content == "续写内容"
        assert full_chapter.continuations[0].model == "gpt-4o"

    def test_import_project_backup_invalid_zip(self, storage_service, tmp_path) -> None:
        """测试导入无效 zip 文件。"""
        from novelforge.services.exporter import import_project_backup

        invalid_zip = tmp_path / "invalid.zip"
        invalid_zip.write_bytes(b"not a zip file")

        with pytest.raises((zipfile.BadZipFile, Exception)):
            import_project_backup(
                storage_service, MagicMock(), MagicMock(), invalid_zip
            )

    def test_import_project_backup_nonexistent_file(self, storage_service) -> None:
        """测试导入不存在的文件。"""
        from novelforge.services.exporter import import_project_backup

        with pytest.raises(FileNotFoundError):
            import_project_backup(
                storage_service, MagicMock(), MagicMock(), "/nonexistent/file.zip"
            )


# ===== 4. 历史日志记录与查询测试 =====


class TestHistoryLog:
    """历史日志记录与查询测试。"""

    def test_log_continuation_returns_id(self, history_service) -> None:
        """测试记录历史日志返回 ID。"""
        hist_id = history_service.log_continuation(
            project_id="proj_1",
            chapter_id="chap_1",
            swipe_id="cont_1",
            started_at="2026-06-22T10:00:00",
            finished_at="2026-06-22T10:01:00",
            status="completed",
            model="gpt-4o",
            parameters={"temperature": 0.8},
            prompt_messages=[{"role": "user", "content": "hi"}],
            output_text="输出内容",
        )
        assert hist_id.startswith("hist_")
        assert len(hist_id) > 5

    def test_list_history_by_project(self, history_service) -> None:
        """测试按项目筛选历史日志。"""
        for i in range(3):
            history_service.log_continuation(
                project_id="proj_A",
                chapter_id=f"chap_{i}",
                swipe_id=f"cont_{i}",
                started_at=f"2026-06-22T10:0{i}:00",
                finished_at=f"2026-06-22T10:0{i}:30",
                status="completed",
                model="gpt-4o",
                parameters={},
                prompt_messages=[],
                output_text=f"输出{i}",
            )
        history_service.log_continuation(
            project_id="proj_B",
            chapter_id="chap_x",
            swipe_id="cont_x",
            started_at="2026-06-22T11:00:00",
            finished_at="2026-06-22T11:01:00",
            status="completed",
            model="gpt-4o",
            parameters={},
            prompt_messages=[],
            output_text="项目B输出",
        )

        logs_a = history_service.list_history(project_id="proj_A")
        logs_b = history_service.list_history(project_id="proj_B")
        assert len(logs_a) == 3
        assert len(logs_b) == 1
        assert all(log["project_id"] == "proj_A" for log in logs_a)

    def test_list_history_by_chapter(self, history_service) -> None:
        """测试按章节筛选历史日志。"""
        history_service.log_continuation(
            project_id="proj_1",
            chapter_id="chap_A",
            swipe_id="c1",
            started_at="2026-06-22T10:00:00",
            finished_at="2026-06-22T10:01:00",
            status="completed",
            model="gpt-4o",
            parameters={},
            prompt_messages=[],
            output_text="A1",
        )
        history_service.log_continuation(
            project_id="proj_1",
            chapter_id="chap_B",
            swipe_id="c2",
            started_at="2026-06-22T10:02:00",
            finished_at="2026-06-22T10:03:00",
            status="completed",
            model="gpt-4o",
            parameters={},
            prompt_messages=[],
            output_text="B1",
        )

        logs = history_service.list_history(chapter_id="chap_A")
        assert len(logs) == 1
        assert logs[0]["chapter_id"] == "chap_A"

    def test_list_history_by_time_range(self, history_service) -> None:
        """测试按时间范围筛选。"""
        history_service.log_continuation(
            project_id="proj_1",
            chapter_id="chap_1",
            swipe_id="c1",
            started_at="2026-06-20T10:00:00",
            finished_at="2026-06-20T10:01:00",
            status="completed",
            model="gpt-4o",
            parameters={},
            prompt_messages=[],
            output_text="old",
        )
        history_service.log_continuation(
            project_id="proj_1",
            chapter_id="chap_1",
            swipe_id="c2",
            started_at="2026-06-22T10:00:00",
            finished_at="2026-06-22T10:01:00",
            status="completed",
            model="gpt-4o",
            parameters={},
            prompt_messages=[],
            output_text="new",
        )

        # 查询 6/21 之后
        logs = history_service.list_history(start_time="2026-06-21T00:00:00")
        assert len(logs) == 1
        assert logs[0]["output_text"] == "new"

        # 查询 6/21 之前
        logs = history_service.list_history(end_time="2026-06-21T00:00:00")
        assert len(logs) == 1
        assert logs[0]["output_text"] == "old"

    def test_list_history_limit(self, history_service) -> None:
        """测试 limit 参数。"""
        for i in range(10):
            history_service.log_continuation(
                project_id="proj_1",
                chapter_id="chap_1",
                swipe_id=f"c{i}",
                started_at=f"2026-06-22T10:{i:02d}:00",
                finished_at=f"2026-06-22T10:{i:02d}:30",
                status="completed",
                model="gpt-4o",
                parameters={},
                prompt_messages=[],
                output_text=f"output_{i}",
            )

        logs = history_service.list_history(limit=5)
        assert len(logs) == 5

    def test_list_history_order_desc(self, history_service) -> None:
        """测试历史日志按 started_at 降序。"""
        history_service.log_continuation(
            project_id="proj_1",
            chapter_id="chap_1",
            swipe_id="c1",
            started_at="2026-06-20T10:00:00",
            finished_at="2026-06-20T10:01:00",
            status="completed",
            model="gpt-4o",
            parameters={},
            prompt_messages=[],
            output_text="old",
        )
        history_service.log_continuation(
            project_id="proj_1",
            chapter_id="chap_1",
            swipe_id="c2",
            started_at="2026-06-22T10:00:00",
            finished_at="2026-06-22T10:01:00",
            status="completed",
            model="gpt-4o",
            parameters={},
            prompt_messages=[],
            output_text="new",
        )

        logs = history_service.list_history()
        # 降序：new 在前
        assert logs[0]["output_text"] == "new"
        assert logs[1]["output_text"] == "old"


# ===== 5. 历史日志详情查看测试 =====


class TestHistoryDetail:
    """历史日志详情查看测试。"""

    def test_get_history_detail(self, history_service) -> None:
        """测试获取历史日志详情。"""
        hist_id = history_service.log_continuation(
            project_id="proj_1",
            chapter_id="chap_1",
            swipe_id="cont_1",
            started_at="2026-06-22T10:00:00",
            finished_at="2026-06-22T10:01:00",
            status="completed",
            model="gpt-4o",
            parameters={"temperature": 0.8, "max_tokens": 2000},
            prompt_messages=[
                {"role": "system", "content": "系统提示"},
                {"role": "user", "content": "用户输入"},
            ],
            output_text="续写输出内容",
        )

        detail = history_service.get_history_detail(hist_id)
        assert detail is not None
        assert detail["id"] == hist_id
        assert detail["project_id"] == "proj_1"
        assert detail["chapter_id"] == "chap_1"
        assert detail["swipe_id"] == "cont_1"
        assert detail["status"] == "completed"
        assert detail["model"] == "gpt-4o"
        assert detail["parameters"]["temperature"] == 0.8
        assert detail["parameters"]["max_tokens"] == 2000
        assert len(detail["prompt_messages"]) == 2
        assert detail["prompt_messages"][0]["role"] == "system"
        assert detail["output_text"] == "续写输出内容"

    def test_get_history_detail_nonexistent(self, history_service) -> None:
        """测试获取不存在的历史日志。"""
        detail = history_service.get_history_detail("nonexistent_id")
        assert detail is None

    def test_get_history_detail_with_error(self, history_service) -> None:
        """测试失败续写的历史日志含 error_message。"""
        hist_id = history_service.log_continuation(
            project_id="proj_1",
            chapter_id="chap_1",
            swipe_id="",
            started_at="2026-06-22T10:00:00",
            finished_at="2026-06-22T10:00:05",
            status="failed",
            model="gpt-4o",
            parameters={},
            prompt_messages=[],
            output_text="",
            error_message="API 认证失败",
        )

        detail = history_service.get_history_detail(hist_id)
        assert detail is not None
        assert detail["status"] == "failed"
        assert detail["error_message"] == "API 认证失败"
        assert detail["output_text"] == ""


# ===== 6. 历史日志删除与清空测试 =====


class TestHistoryDelete:
    """历史日志删除与清空测试。"""

    def test_delete_history(self, history_service) -> None:
        """测试删除单条历史日志。"""
        hist_id = history_service.log_continuation(
            project_id="proj_1",
            chapter_id="chap_1",
            swipe_id="c1",
            started_at="2026-06-22T10:00:00",
            finished_at="2026-06-22T10:01:00",
            status="completed",
            model="gpt-4o",
            parameters={},
            prompt_messages=[],
            output_text="内容",
        )

        assert history_service.get_history_detail(hist_id) is not None
        history_service.delete_history(hist_id)
        assert history_service.get_history_detail(hist_id) is None

    def test_clear_history_by_project(self, history_service) -> None:
        """测试按项目清空历史日志。"""
        for i in range(3):
            history_service.log_continuation(
                project_id="proj_A",
                chapter_id=f"chap_{i}",
                swipe_id=f"c{i}",
                started_at=f"2026-06-22T10:0{i}:00",
                finished_at=f"2026-06-22T10:0{i}:30",
                status="completed",
                model="gpt-4o",
                parameters={},
                prompt_messages=[],
                output_text=f"A{i}",
            )
        history_service.log_continuation(
            project_id="proj_B",
            chapter_id="chap_x",
            swipe_id="cx",
            started_at="2026-06-22T11:00:00",
            finished_at="2026-06-22T11:01:00",
            status="completed",
            model="gpt-4o",
            parameters={},
            prompt_messages=[],
            output_text="B",
        )

        history_service.clear_history(project_id="proj_A")
        assert len(history_service.list_history(project_id="proj_A")) == 0
        assert len(history_service.list_history(project_id="proj_B")) == 1

    def test_clear_all_history(self, history_service) -> None:
        """测试清空所有历史日志。"""
        for i in range(3):
            history_service.log_continuation(
                project_id=f"proj_{i}",
                chapter_id=f"chap_{i}",
                swipe_id=f"c{i}",
                started_at="2026-06-22T10:00:00",
                finished_at="2026-06-22T10:01:00",
                status="completed",
                model="gpt-4o",
                parameters={},
                prompt_messages=[],
                output_text=f"output_{i}",
            )

        history_service.clear_history()
        assert len(history_service.list_history()) == 0


# ===== 7. Storage history_log 方法测试 =====


class TestStorageHistoryLog:
    """Storage history_log 方法测试。"""

    def test_storage_save_and_get_history_log(self, storage_service) -> None:
        """测试 Storage.save_history_log 和 get_history_log。"""
        data = {
            "id": "hist_test_1",
            "project_id": "proj_1",
            "chapter_id": "chap_1",
            "swipe_id": "cont_1",
            "started_at": "2026-06-22T10:00:00",
            "finished_at": "2026-06-22T10:01:00",
            "status": "completed",
            "model": "gpt-4o",
            "parameters": {"temperature": 0.8},
            "prompt_messages": [{"role": "user", "content": "hi"}],
            "output_text": "输出",
            "error_message": "",
        }
        storage_service.save_history_log(data)

        result = storage_service.get_history_log("hist_test_1")
        assert result is not None
        assert result["id"] == "hist_test_1"
        assert result["parameters"]["temperature"] == 0.8
        assert result["prompt_messages"][0]["content"] == "hi"

    def test_storage_list_history_logs_with_filters(self, storage_service) -> None:
        """测试 Storage.list_history_logs 筛选。"""
        for i in range(5):
            storage_service.save_history_log({
                "id": f"hist_{i}",
                "project_id": "proj_1" if i < 3 else "proj_2",
                "chapter_id": f"chap_{i}",
                "swipe_id": f"cont_{i}",
                "started_at": f"2026-06-2{i}T10:00:00",
                "finished_at": f"2026-06-2{i}T10:01:00",
                "status": "completed",
                "model": "gpt-4o",
                "parameters": {},
                "prompt_messages": [],
                "output_text": f"output_{i}",
                "error_message": "",
            })

        # 按 project_id 筛选
        logs = storage_service.list_history_logs({"project_id": "proj_1"})
        assert len(logs) == 3

        # 按 limit 筛选
        logs = storage_service.list_history_logs({"limit": 2})
        assert len(logs) == 2

    def test_storage_delete_history_log(self, storage_service) -> None:
        """测试 Storage.delete_history_log。"""
        storage_service.save_history_log({
            "id": "hist_del",
            "project_id": "proj_1",
            "chapter_id": "chap_1",
            "swipe_id": "cont_1",
            "started_at": "2026-06-22T10:00:00",
            "finished_at": "2026-06-22T10:01:00",
            "status": "completed",
            "model": "gpt-4o",
            "parameters": {},
            "prompt_messages": [],
            "output_text": "内容",
            "error_message": "",
        })

        storage_service.delete_history_log("hist_del")
        assert storage_service.get_history_log("hist_del") is None

    def test_storage_clear_history_logs(self, storage_service) -> None:
        """测试 Storage.clear_history_logs。"""
        for i in range(3):
            storage_service.save_history_log({
                "id": f"hist_{i}",
                "project_id": "proj_1",
                "chapter_id": f"chap_{i}",
                "swipe_id": f"cont_{i}",
                "started_at": "2026-06-22T10:00:00",
                "finished_at": "2026-06-22T10:01:00",
                "status": "completed",
                "model": "gpt-4o",
                "parameters": {},
                "prompt_messages": [],
                "output_text": "内容",
                "error_message": "",
            })

        storage_service.clear_history_logs(project_id="proj_1")
        assert len(storage_service.list_history_logs({"project_id": "proj_1"})) == 0


# ===== 8. 字体设置持久化测试 =====


class TestFontSettings:
    """字体设置持久化测试。"""

    def test_font_settings_persistence(self, tmp_path) -> None:
        """测试字体设置持久化到 config。"""
        from novelforge.core.config import ConfigManager

        config_path = tmp_path / "config.json"
        manager = ConfigManager(config_path=config_path)
        manager.load()

        # 修改字体设置
        appearance = manager.get_appearance()
        appearance["font_family"] = "SimSun"
        appearance["font_size"] = 16
        appearance["line_height"] = 1.8
        manager.set_appearance(appearance)

        # 重新加载
        manager2 = ConfigManager(config_path=config_path)
        manager2.load()
        appearance2 = manager2.get_appearance()
        assert appearance2["font_family"] == "SimSun"
        assert appearance2["font_size"] == 16
        assert appearance2["line_height"] == 1.8

    def test_font_settings_dialog_creation(self, tmp_path) -> None:
        """测试 FontSettingsDialog 创建。"""
        from PySide6.QtWidgets import QApplication

        from novelforge.core.config import ConfigManager
        from novelforge.ui.font_settings import FontSettingsDialog

        app = QApplication.instance() or QApplication(sys.argv)
        config_path = tmp_path / "config.json"
        manager = ConfigManager(config_path=config_path)
        manager.load()

        dialog = FontSettingsDialog(manager)
        assert dialog.windowTitle() == "字体设置"
        # 默认值应从配置加载
        appearance = manager.get_appearance()
        assert dialog._size_spin.value() == appearance.get("font_size", 14)

    def test_font_settings_apply_to_editor(self, tmp_path) -> None:
        """测试 apply_font_to_editor 函数。"""
        from PySide6.QtWidgets import QApplication, QPlainTextEdit

        from novelforge.ui.font_settings import apply_font_to_editor

        app = QApplication.instance() or QApplication(sys.argv)
        editor = QPlainTextEdit()
        appearance = {
            "font_family": "SimSun",
            "font_size": 18,
            "line_height": 2.0,
        }
        apply_font_to_editor(editor, appearance)

        font = editor.font()
        assert font.family() == "SimSun"
        assert font.pointSize() == 18


# ===== 9. 主题切换配置持久化测试 =====


class TestThemePersistence:
    """主题切换配置持久化测试。"""

    def test_theme_persistence(self, tmp_path) -> None:
        """测试主题切换持久化。"""
        from novelforge.core.config import ConfigManager

        config_path = tmp_path / "config.json"
        manager = ConfigManager(config_path=config_path)
        manager.load()

        # 切换主题
        appearance = manager.get_appearance()
        appearance["theme"] = "light"
        manager.set_appearance(appearance)

        # 重新加载
        manager2 = ConfigManager(config_path=config_path)
        manager2.load()
        assert manager2.get_appearance().get("theme") == "light"

    def test_theme_default_is_dark(self, tmp_path) -> None:
        """测试默认主题为 dark。"""
        from novelforge.core.config import ConfigManager

        config_path = tmp_path / "config.json"
        manager = ConfigManager(config_path=config_path)
        manager.load()

        assert manager.get_appearance().get("theme") == "dark"


# ===== 10. HistoryPanel UI 测试 =====


class TestHistoryPanelUI:
    """HistoryPanel UI 测试。"""

    def test_history_panel_creation(self, storage_service, history_service) -> None:
        """测试 HistoryPanel 创建。"""
        from PySide6.QtWidgets import QApplication

        from novelforge.ui.history_panel import HistoryPanel

        app = QApplication.instance() or QApplication(sys.argv)
        panel = HistoryPanel(storage_service, history_service)
        assert panel.windowTitle() == "续写历史日志"
        # 表格列数
        assert panel._table.columnCount() == 6

    def test_history_panel_refresh(self, storage_service, history_service) -> None:
        """测试 HistoryPanel 刷新。"""
        from PySide6.QtWidgets import QApplication

        from novelforge.ui.history_panel import HistoryPanel

        app = QApplication.instance() or QApplication(sys.argv)

        # 添加几条日志
        for i in range(3):
            history_service.log_continuation(
                project_id="proj_1",
                chapter_id=f"chap_{i}",
                swipe_id=f"c{i}",
                started_at=f"2026-06-22T10:0{i}:00",
                finished_at=f"2026-06-22T10:0{i}:30",
                status="completed",
                model="gpt-4o",
                parameters={},
                prompt_messages=[],
                output_text=f"output_{i}",
            )

        panel = HistoryPanel(storage_service, history_service)
        assert panel._table.rowCount() == 3
        assert "3 条记录" in panel._status_label.text()


# ===== 11. ChapterListWidget 导出菜单测试 =====


class TestChapterListExportMenu:
    """ChapterListWidget 导出菜单测试。"""

    def test_chapter_list_has_export_menu(self, storage_service) -> None:
        """测试章节列表右键菜单含导出选项。"""
        from PySide6.QtWidgets import QApplication

        from novelforge.ui.chapter_list import ChapterListWidget

        app = QApplication.instance() or QApplication(sys.argv)
        widget = ChapterListWidget(storage_service)
        # 验证 _on_export_chapter 方法存在
        assert hasattr(widget, "_on_export_chapter")
        assert callable(widget._on_export_chapter)


# ===== 12. PyInstaller 打包配置测试 =====


class TestBuildConfig:
    """PyInstaller 打包配置测试。"""

    def test_build_spec_exists(self) -> None:
        """测试 build.spec 文件存在。"""
        spec_path = PROJECT_ROOT / "novelforge" / "resources" / "build.spec"
        assert spec_path.exists()

    def test_build_spec_contains_tiktoken(self) -> None:
        """测试 build.spec 包含 tiktoken hiddenimports。"""
        spec_path = PROJECT_ROOT / "novelforge" / "resources" / "build.spec"
        content = spec_path.read_text(encoding="utf-8")
        assert "tiktoken" in content
        assert "tiktoken_ext.openai_public" in content

    def test_build_py_exists(self) -> None:
        """测试 build.py 文件存在。"""
        build_path = PROJECT_ROOT / "novelforge" / "resources" / "build.py"
        assert build_path.exists()

    def test_build_py_importable(self) -> None:
        """测试 build.py 可被导入。"""
        from novelforge.resources import build

        assert hasattr(build, "main")
        assert callable(build.main)

    def test_resources_is_package(self) -> None:
        """测试 resources 目录是 Python 包。"""
        import novelforge.resources

        assert hasattr(novelforge.resources, "__path__")


# ===== 13. 模块导入测试 =====


class TestModuleImports:
    """模块导入测试。"""

    def test_import_exporter(self) -> None:
        """测试导入 exporter 模块。"""
        from novelforge.services.exporter import (
            export_chapter_markdown,
            export_chapter_txt,
            export_full_txt,
            export_project_backup,
            import_project_backup,
        )
        assert callable(export_full_txt)
        assert callable(export_chapter_txt)
        assert callable(export_chapter_markdown)
        assert callable(export_project_backup)
        assert callable(import_project_backup)

    def test_import_history_service(self) -> None:
        """测试导入 history_service 模块。"""
        from novelforge.services.history_service import HistoryService
        assert callable(HistoryService)

    def test_import_history_panel(self) -> None:
        """测试导入 history_panel 模块。"""
        from novelforge.ui.history_panel import HistoryPanel
        assert callable(HistoryPanel)

    def test_import_font_settings(self) -> None:
        """测试导入 font_settings 模块。"""
        from novelforge.ui.font_settings import FontSettingsDialog
        assert callable(FontSettingsDialog)

    def test_history_service_now_iso(self) -> None:
        """测试 HistoryService.now_iso 返回 ISO 字符串。"""
        from novelforge.services.history_service import HistoryService

        now_str = HistoryService.now_iso()
        # 应可被 fromisoformat 解析
        datetime.fromisoformat(now_str)
