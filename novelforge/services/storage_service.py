"""同步存储服务层。

封装 ``Storage``（异步）为同步接口，供 UI 层直接调用。
内部通过 ``AsyncLoopRunner`` 在后台事件循环中执行异步操作。

同时提供项目、章节、续写版本的便捷操作方法，
封装数据模型与字典之间的转换。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from novelforge.core.storage import (
    Storage,
    atomic_write_file,
    get_chapter_file_path,
    get_default_storage_path,
)
from novelforge.models import Chapter, Continuation, Project
from novelforge.services.async_runner import AsyncLoopRunner
from novelforge.utils.ids import generate_id

logger = logging.getLogger(__name__)


def _generate_id(prefix: str = "") -> str:
    """生成短 ID（12 位 hex）。

    保留为薄包装以兼容既有调用点；新代码应直接使用
    :func:`novelforge.utils.ids.generate_id`。
    """
    return generate_id(prefix)


class StorageService:
    """同步存储服务。

    封装异步 ``Storage`` 为同步接口，提供项目/章节/续写的 CRUD 操作。

    Usage::

        service = StorageService()
        project = service.create_project(name="我的小说")
        chapters = service.list_chapters(project.id)
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        """初始化存储服务，连接数据库。

        Args:
            storage_path: 存储根目录，默认 ``~/.novelforge``
        """
        self.storage_path = storage_path or get_default_storage_path()
        self.storage = Storage(self.storage_path)
        self._runner = AsyncLoopRunner.instance()
        # 在后台事件循环中连接数据库
        self._runner.run(self.storage.connect(), timeout=30)
        logger.info("存储服务已连接: %s", self.storage_path)

    # ===== 项目操作 =====

    def create_project(
        self,
        name: str = "",
        source_file: str = "",
        novel_profile: dict | None = None,
    ) -> Project:
        """创建新项目。

        Args:
            name: 项目名称
            source_file: 源文件路径
            novel_profile: 小说档案

        Returns:
            创建的 Project 对象
        """
        project_id = _generate_id("proj_")
        now = datetime.now()
        project = Project(
            id=project_id,
            name=name,
            created_at=now,
            updated_at=now,
            source_file=source_file,
            novel_profile=novel_profile or {},  # type: ignore[arg-type]
        )
        self._runner.run(self.storage.save_project(project.model_dump(mode="json")))
        logger.info("创建项目: id=%s, name=%s", project_id, name)
        return project

    def save_project(self, project: Project) -> None:
        """保存项目。"""
        project.updated_at = datetime.now()
        self._runner.run(self.storage.save_project(project.model_dump(mode="json")))

    def load_project(self, project_id: str) -> Project | None:
        """加载项目。"""
        data = self._runner.run(self.storage.load_project(project_id))
        if data is None:
            return None
        # world_ontology 从 DB 加载为 dict，还原为 WorldOntology 实例
        wo = data.get("world_ontology")
        if isinstance(wo, dict):
            try:
                from novelforge.models.ontology import WorldOntology
                data["world_ontology"] = WorldOntology.model_validate(wo)
            except Exception:
                logger.warning("world_ontology 还原失败，保留 dict")
        return Project.model_validate(data)

    def list_projects(self) -> list[Project]:
        """列出所有项目（按更新时间降序）。"""
        data_list = self._runner.run(self.storage.list_projects())
        return [Project.model_validate(d) for d in data_list]

    def delete_project(self, project_id: str) -> None:
        """删除项目及其所有章节和续写。"""
        self._runner.run(self.storage.delete_project(project_id))
        logger.info("删除项目: %s", project_id)

    # ===== 章节操作 =====

    def save_chapter(self, chapter: Chapter) -> None:
        """保存章节（元数据写 SQLite，正文写文件系统）。"""
        chapter.updated_at = datetime.now()
        chapter.word_count = len(chapter.content)
        data = chapter.model_dump(mode="json")
        self._runner.run(self.storage.save_chapter(data))

    def update_chapter_index(self, chapter_id: str, new_index: int) -> None:
        """只更新章节的 index 列，不触碰正文文件。

        用于 reindex 场景，避免 save_chapter 用空 content 覆盖正文。
        """
        self._runner.run(
            self.storage.update_chapter_index(chapter_id, new_index)
        )

    def load_chapter(self, chapter_id: str) -> Chapter | None:
        """加载章节（含正文）。"""
        data = self._runner.run(self.storage.load_chapter(chapter_id))
        if data is None:
            return None
        # 加载关联的续写版本
        conts = self._runner.run(self.storage.list_continuations(chapter_id))
        data["continuations"] = conts
        return Chapter.model_validate(data)

    def list_chapters(self, project_id: str) -> list[Chapter]:
        """列出项目的所有章节元数据（不含正文，不含续写）。"""
        data_list = self._runner.run(self.storage.list_chapters(project_id))
        return [Chapter.model_validate(d) for d in data_list]

    def rebuild_chapters_from_disk(self, project_id: str) -> int:
        """从磁盘重建章节 DB 行，返回重建数量。"""
        return self._runner.run(self.storage.rebuild_chapters_from_disk(project_id))

    def load_chapter_contents(self, chapters: list[Chapter]) -> list[Chapter]:
        """批量加载章节正文，返回填充了 content 的章节列表。

        对已有 content 的章节保持不变，对 content 为空的章节批量加载
        正文与续写（单次 ``load_chapters_with_continuations`` 调用，减少跨线程往返）。

        Args:
            chapters: 章节列表（可能含 content 为空的章节）

        Returns:
            填充了 content 的章节列表（新列表，不修改原列表）
        """
        # 需要加载正文的章节 ID（content 为空）
        to_load_ids = [ch.id for ch in chapters if not ch.content]
        if not to_load_ids:
            return list(chapters)
        # 单次批量加载所有需要加载的章节及其续写
        loaded_map = self._runner.run(
            self.storage.load_chapters_with_continuations(to_load_ids)
        )
        result: list[Chapter] = []
        for ch in chapters:
            if ch.content:
                result.append(ch)
            else:
                loaded = loaded_map.get(ch.id)
                result.append(loaded if loaded is not None else ch)
        return result

    def delete_chapter(self, chapter_id: str) -> None:
        """删除章节及其所有续写。"""
        self._runner.run(self.storage.delete_chapter(chapter_id))

    def reorder_chapters(self, project_id: str, chapter_ids: list[str]) -> None:
        """重排章节 index。

        Args:
            project_id: 项目 ID
            chapter_ids: 按新顺序排列的章节 ID 列表
        """
        for new_index, cid in enumerate(chapter_ids):
            chapter = self.load_chapter(cid)
            if chapter and chapter.index != new_index:
                chapter.index = new_index
                self.save_chapter(chapter)

    # ===== 续写版本操作 =====

    def save_continuation(self, continuation: Continuation, chapter_id: str) -> None:
        """保存续写版本。"""
        data = continuation.model_dump(mode="json")
        data["chapter_id"] = chapter_id
        self._runner.run(self.storage.save_continuation(data))

    def list_continuations(self, chapter_id: str) -> list[Continuation]:
        """列出章节的所有续写版本。"""
        data_list = self._runner.run(self.storage.list_continuations(chapter_id))
        return [Continuation.model_validate(d) for d in data_list]

    def delete_continuation(self, continuation_id: str) -> None:
        """删除续写版本。"""
        self._runner.run(self.storage.delete_continuation(continuation_id))

    def update_continuation(self, continuation: Continuation) -> None:
        """更新续写版本（如标记 is_accepted）。"""
        data = continuation.model_dump(mode="json")
        # chapter_id 需要从存储中获取或由调用方提供
        if "chapter_id" not in data:
            # 通过 list 查找（M1 简化实现）
            data["chapter_id"] = ""
        self._runner.run(self.storage.save_continuation(data))

    # ===== 历史日志操作 =====

    def save_history_log(self, data: dict[str, Any]) -> None:
        """保存续写历史日志（INSERT OR REPLACE）。

        Args:
            data: 日志条目字典
        """
        self._runner.run(self.storage.save_history_log(data))

    def list_history_logs(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """按条件查询历史日志。

        Args:
            filters: 筛选条件（project_id/chapter_id/start_time/end_time/limit）

        Returns:
            日志条目字典列表
        """
        return self._runner.run(self.storage.list_history_logs(filters))

    def get_history_log(self, history_id: str) -> dict[str, Any] | None:
        """获取单条历史日志详情。"""
        return self._runner.run(self.storage.get_history_log(history_id))

    def delete_history_log(self, history_id: str) -> None:
        """删除单条历史日志。"""
        self._runner.run(self.storage.delete_history_log(history_id))

    def clear_history_logs(self, project_id: str | None = None) -> None:
        """清空历史日志（可按项目）。"""
        self._runner.run(self.storage.clear_history_logs(project_id))

    # ===== 工具方法 =====

    def get_chapter_file_path(self, project_id: str, chapter_id: str) -> Path:
        """获取章节正文文件路径。"""
        return get_chapter_file_path(self.storage_path, project_id, chapter_id)

    def write_chapter_content(self, project_id: str, chapter_id: str, content: str) -> None:
        """直接写入章节正文文件（不更新 SQLite）。"""
        path = self.get_chapter_file_path(project_id, chapter_id)
        atomic_write_file(path, content)

    def read_chapter_content(self, project_id: str, chapter_id: str) -> str:
        """直接读取章节正文文件。"""
        path = self.get_chapter_file_path(project_id, chapter_id)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def shutdown(self) -> None:
        """关闭存储服务。"""
        self._runner.run(self.storage.close(), timeout=10)
