"""章节服务：拆分、合并、删除等操作，含 undo 支持。

提供章节级别的操作，每个操作返回一个可撤销的 ``ChapterOperation`` 记录，
调用方可用 ``undo_operation`` 恢复。

操作类型：
- split: 在指定位置拆分章节，原章节 continuations 归属前半
- merge: 合并当前章节与下一章，两章 continuations 都归属合并后的章节
- delete: 删除章节（含文件、SQLite、swipe），index 重排
- rename: 重命名章节
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from novelforge.models import Chapter, Continuation
from novelforge.services.importer import TxtImporter
from novelforge.services.storage_service import StorageService, _generate_id

logger = logging.getLogger(__name__)


@dataclass
class ChapterOperation:
    """章节操作记录（用于 undo）。

    Attributes:
        action: 操作类型（split/merge/delete/rename）
        project_id: 项目 ID
        before: 操作前的章节快照列表（深拷贝）
        after: 操作后的章节 ID 列表（用于知道哪些是新创建的）
        description: 操作描述
        extra: 额外数据（如删除时的文件内容备份）
    """

    action: str
    project_id: str
    before: list[dict[str, Any]] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class ChapterService:
    """章节操作服务。

    提供章节的拆分、合并、删除、重命名等操作，每个操作支持 undo。

    Usage::

        service = ChapterService(storage_service)
        op = service.split_chapter(chapter, position=500)
        # undo
        service.undo_operation(op)
    """

    def __init__(self, storage_service: StorageService) -> None:
        """初始化章节服务。

        Args:
            storage_service: 存储服务实例
        """
        self.storage = storage_service
        self.importer = TxtImporter(storage_service)

    def _snapshot_chapter(self, chapter: Chapter) -> dict[str, Any]:
        """创建章节快照（含正文和续写）。"""
        return chapter.model_dump(mode="json")

    def split_chapter(
        self,
        chapter: Chapter,
        position: int,
    ) -> tuple[ChapterOperation, Chapter, Chapter]:
        """在指定位置拆分章节。

        - 原章节 continuations 归属拆分后的前一个章节
        - 后续章节 index 自动重排

        Args:
            chapter: 待拆分章节
            position: 字符偏移量

        Returns:
            (操作记录, 前半章节, 后半章节) 元组
        """
        # 保存操作前快照
        before_snapshot = self._snapshot_chapter(chapter)

        # 执行拆分
        front, back = self.importer.split_at_position(chapter, position)

        # 保存前半（更新原章节）
        self.storage.save_chapter(front)

        # 保存后半（新章节）
        self.storage.save_chapter(back)

        # 重排后续章节 index
        self._reindex_after(chapter.project_id, chapter.index + 1)

        op = ChapterOperation(
            action="split",
            project_id=chapter.project_id,
            before=[before_snapshot],
            after=[front.id, back.id],
            description=f"拆分章节「{chapter.title}」",
            extra={"position": position},
        )
        logger.info("拆分章节: %s -> %s + %s", chapter.id, front.id, back.id)
        return op, front, back

    def merge_chapter_with_next(
        self,
        chapter: Chapter,
    ) -> tuple[ChapterOperation, Chapter]:
        """合并当前章节与下一章。

        - 两章的 continuations 都归属合并后的章节
        - 合并后章节 index 不变，后续章节 index 重排

        Args:
            chapter: 当前章节

        Returns:
            (操作记录, 合并后的章节) 元组

        Raises:
            ValueError: 没有下一章
        """
        # 获取下一章
        all_chapters = self.storage.list_chapters(chapter.project_id)
        all_chapters.sort(key=lambda c: c.index)
        next_chapter = None
        for c in all_chapters:
            if c.index > chapter.index:
                if next_chapter is None or c.index < next_chapter.index:
                    next_chapter = c

        if next_chapter is None:
            raise ValueError("没有下一章可合并")

        # 加载完整数据（含正文和续写）
        full_current = self.storage.load_chapter(chapter.id)
        full_next = self.storage.load_chapter(next_chapter.id)
        if full_current is None or full_next is None:
            raise ValueError("加载章节数据失败")

        # 保存操作前快照
        before_snapshots = [
            self._snapshot_chapter(full_current),
            self._snapshot_chapter(full_next),
        ]

        # 合并正文
        merged_content = full_current.content
        if full_next.content:
            if merged_content:
                merged_content += "\n\n" + full_next.content
            else:
                merged_content = full_next.content

        # 合并 continuations
        merged_conts = list(full_current.continuations) + list(full_next.continuations)

        merged = Chapter(
            id=full_current.id,
            project_id=full_current.project_id,
            index=full_current.index,
            title=full_current.title,
            content=merged_content,
            word_count=len(merged_content),
            continuations=merged_conts,
            metadata=full_current.metadata,
            created_at=full_current.created_at,
            updated_at=datetime.now(),
        )

        # 保存合并后的章节
        self.storage.save_chapter(merged)

        # 删除被合并的下一章（不删除其文件，因为内容已合并）
        self.storage.delete_chapter(full_next.id)

        # 重排后续章节 index
        self._reindex_after(merged.project_id, merged.index + 1)

        op = ChapterOperation(
            action="merge",
            project_id=chapter.project_id,
            before=before_snapshots,
            after=[merged.id],
            description=f"合并章节「{full_current.title}」与「{full_next.title}」",
            extra={"deleted_chapter_id": full_next.id},
        )
        logger.info("合并章节: %s + %s -> %s", full_current.id, full_next.id, merged.id)
        return op, merged

    def delete_chapter(
        self,
        chapter: Chapter,
    ) -> ChapterOperation:
        """删除章节及其所有续写版本。

        - 删除文件、SQLite 记录、关联 swipe
        - 后续章节 index 自动重排
        - 支持 undo（恢复文件和记录）

        Args:
            chapter: 待删除章节

        Returns:
            操作记录
        """
        # 加载完整数据用于 undo
        full_chapter = self.storage.load_chapter(chapter.id)
        if full_chapter is None:
            raise ValueError("章节不存在")

        before_snapshot = self._snapshot_chapter(full_chapter)

        # 删除
        self.storage.delete_chapter(chapter.id)

        # 重排 index
        self._reindex_after(chapter.project_id, chapter.index)

        op = ChapterOperation(
            action="delete",
            project_id=chapter.project_id,
            before=[before_snapshot],
            after=[],
            description=f"删除章节「{chapter.title}」",
        )
        logger.info("删除章节: %s", chapter.id)
        return op

    def rename_chapter(
        self,
        chapter: Chapter,
        new_title: str,
    ) -> ChapterOperation:
        """重命名章节。

        Args:
            chapter: 章节
            new_title: 新标题

        Returns:
            操作记录
        """
        before_snapshot = self._snapshot_chapter(chapter)
        old_title = chapter.title
        chapter.title = new_title
        self.storage.save_chapter(chapter)

        op = ChapterOperation(
            action="rename",
            project_id=chapter.project_id,
            before=[before_snapshot],
            after=[chapter.id],
            description=f"重命名章节「{old_title}」→「{new_title}」",
            extra={"old_title": old_title},
        )
        return op

    def undo_operation(self, op: ChapterOperation) -> None:
        """撤销操作。

        Args:
            op: 操作记录
        """
        if op.action == "split":
            # undo split: 恢复原章节，删除后半章节
            self._undo_split(op)
        elif op.action == "merge":
            # undo merge: 恢复两个章节
            self._undo_merge(op)
        elif op.action == "delete":
            # undo delete: 恢复章节
            self._undo_delete(op)
        elif op.action == "rename":
            # undo rename: 恢复原标题
            self._undo_rename(op)

        # 重排 index
        self._reindex_all(op.project_id)
        logger.info("撤销操作: %s", op.description)

    def _undo_split(self, op: ChapterOperation) -> None:
        """撤销拆分：恢复原章节，删除后半。"""
        if not op.before:
            return
        original = Chapter.model_validate(op.before[0])
        # 删除后半章节
        if len(op.after) >= 2:
            back_id = op.after[1]
            self.storage.delete_chapter(back_id)
        # 恢复原章节
        self.storage.save_chapter(original)

    def _undo_merge(self, op: ChapterOperation) -> None:
        """撤销合并：恢复两个章节。"""
        if len(op.before) < 2:
            return
        ch1 = Chapter.model_validate(op.before[0])
        ch2 = Chapter.model_validate(op.before[1])
        self.storage.save_chapter(ch1)
        self.storage.save_chapter(ch2)

    def _undo_delete(self, op: ChapterOperation) -> None:
        """撤销删除：恢复章节。"""
        if not op.before:
            return
        chapter = Chapter.model_validate(op.before[0])
        self.storage.save_chapter(chapter)

    def _undo_rename(self, op: ChapterOperation) -> None:
        """撤销重命名：恢复原标题。"""
        if not op.before:
            return
        original = Chapter.model_validate(op.before[0])
        self.storage.save_chapter(original)

    def _reindex_after(self, project_id: str, start_index: int) -> None:
        """重排指定 index 之后的章节序号（确保连续）。

        使用 update_chapter_index 只更新 SQL 的 index 列，
        不触碰正文文件，避免 list_chapters 返回空 content 覆盖正文。
        """
        chapters = self.storage.list_chapters(project_id)
        chapters.sort(key=lambda c: c.index)
        # 重新分配 index
        for new_idx, ch in enumerate(chapters):
            if ch.index != new_idx:
                ch.index = new_idx
                self.storage.update_chapter_index(ch.id, new_idx)

    def _reindex_all(self, project_id: str) -> None:
        """重排项目所有章节的 index。"""
        self._reindex_after(project_id, 0)

    # ===== Swipe 操作 =====

    def accept_continuation(
        self,
        chapter: Chapter,
        continuation: Continuation,
    ) -> Chapter:
        """接受续写版本。

        - 标记当前 swipe is_accepted=True，其他设为 False
        - swipe content 追加到章节正文末尾
        - 章节保存到文件系统

        Args:
            chapter: 章节
            continuation: 待接受的续写版本

        Returns:
            更新后的章节
        """
        # 取消其他 swipe 的 is_accepted
        for c in chapter.continuations:
            c.is_accepted = (c.id == continuation.id)
        # 标记当前为已接受
        continuation.is_accepted = True

        # 追加到章节正文
        if chapter.content and continuation.content:
            chapter.content = chapter.content + "\n\n" + continuation.content
        elif continuation.content:
            chapter.content = continuation.content
        chapter.word_count = len(chapter.content)

        # 保存
        self.storage.save_chapter(chapter)
        for c in chapter.continuations:
            self.storage.save_continuation(c, chapter.id)

        logger.info("接受续写: chapter=%s, swipe=%s", chapter.id, continuation.id)
        return chapter

    def delete_continuation(
        self,
        chapter: Chapter,
        continuation: Continuation,
    ) -> Chapter:
        """删除续写版本。

        若为已接受版本，先取消接受。

        Args:
            chapter: 章节
            continuation: 待删除的续写版本

        Returns:
            更新后的章节
        """
        # 从列表中移除
        chapter.continuations = [
            c for c in chapter.continuations if c.id != continuation.id
        ]
        # 删除存储记录
        self.storage.delete_continuation(continuation.id)
        logger.info("删除续写: chapter=%s, swipe=%s", chapter.id, continuation.id)
        return chapter
