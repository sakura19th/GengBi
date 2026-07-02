"""TXT 导入与章节拆分服务。

实现 TXT 文件导入，使用正则表达式匹配章节标题并拆分。

性能要求：100 万字 TXT 在 3 秒内完成拆分。
使用 ``re.finditer`` 批量匹配所有标题位置，然后按位置切片，
避免逐行扫描的开销。

拆分规则：
- 默认正则：``^第[一二三四五六七八九十百千零\\d]+[章节回卷]``
- 无匹配时整文作为单章，标题为"全文"或文件名
- 空章节（连续两个标题）保留，content 为空字符串
- 章节正文不包含标题行（除非 ``include_title_in_content=True``）
"""
from __future__ import annotations

import concurrent.futures
import logging
import re
import time
from pathlib import Path
from typing import Iterator

from novelforge.models import Chapter, Project
from novelforge.services.storage_service import StorageService, _generate_id

logger = logging.getLogger(__name__)

# 默认章节标题正则
DEFAULT_CHAPTER_PATTERN = r"^第[一二三四五六七八九十百千零\d]+[章节回卷]"

# 章节标题匹配超时秒数（防止用户输入的灾难性回溯正则卡死导入流程）
_CHAPTER_MATCH_TIMEOUT: float = 5.0

# 模块级共享线程池：用于施加超时保护的正则匹配
_CHAPTER_MATCH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def _finditer_chapters_with_timeout(
    compiled_regex: "re.Pattern[str]", text: str, timeout: float = _CHAPTER_MATCH_TIMEOUT
) -> list["re.Match[str]"] | None:
    """带超时保护的章节标题 ``finditer``。

    用户可自定义章节标题正则，存在灾难性回溯风险（如 ``(a+)+$``），
    在百万字文本上可能卡死导入流程。使用线程池施加超时保护。

    Args:
        compiled_regex: 已编译的章节标题正则
        text: 全文文本
        timeout: 超时秒数

    Returns:
        匹配列表；超时返回 None（调用方降级用默认正则重试）
    """

    def _collect() -> list["re.Match[str]"]:
        return list(compiled_regex.finditer(text))

    try:
        future = _CHAPTER_MATCH_EXECUTOR.submit(_collect)
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.warning(
            "章节标题正则匹配超时（可能存在灾难性回溯），降级使用默认正则: %s",
            compiled_regex.pattern[:100],
        )
        return None


class ImportResult:
    """导入结果。

    Attributes:
        project: 创建的项目
        chapters: 拆分后的章节列表
        elapsed_seconds: 拆分耗时（秒）
        total_chars: 总字符数
        message: 状态提示消息
    """

    def __init__(
        self,
        project: Project,
        chapters: list[Chapter],
        elapsed_seconds: float,
        total_chars: int,
        message: str = "",
    ) -> None:
        self.project = project
        self.chapters = chapters
        self.elapsed_seconds = elapsed_seconds
        self.total_chars = total_chars
        self.message = message


class TxtImporter:
    """TXT 文件导入器。

    读取 TXT 文件，按章节标题正则拆分，创建项目并保存到存储。

    Usage::

        importer = TxtImporter(storage_service)
        result = importer.import_file("/path/to/novel.txt", project_name="我的小说")
    """

    def __init__(self, storage_service: StorageService) -> None:
        """初始化导入器。

        Args:
            storage_service: 存储服务实例
        """
        self.storage = storage_service

    def import_file(
        self,
        file_path: str | Path,
        project_name: str = "",
        pattern: str = DEFAULT_CHAPTER_PATTERN,
        include_title_in_content: bool = False,
        encoding: str = "utf-8",
    ) -> ImportResult:
        """导入 TXT 文件并拆分章节。

        Args:
            file_path: TXT 文件路径
            project_name: 项目名称（为空时用文件名）
            pattern: 章节标题正则
            include_title_in_content: 章节正文是否包含标题行
            encoding: 文件编码

        Returns:
            ImportResult 导入结果
        """
        file_path = Path(file_path)
        start_time = time.time()

        # 读取文件（先尝试指定编码，失败后尝试常见中文编码 GBK/GB18030/Big5/UTF-16）
        text = None
        successful_encoding = None
        # 备选编码列表：中文小说常用 GBK/GB2312，Big5 用于繁体中文
        fallback_encodings = [encoding, "gbk", "gb18030", "big5", "utf-16"]
        for enc in fallback_encodings:
            try:
                text = file_path.read_text(encoding=enc)
                successful_encoding = enc
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            raise ValueError(
                f"无法解码文件 {file_path}，尝试的编码: UTF-8, GBK, GB18030, Big5。请手动指定文件编码。"
            )

        # 使用备用编码成功时记录警告
        if successful_encoding != encoding:
            logger.warning(
                "文件 %s 使用 %s 编码而非 %s 解码成功",
                file_path,
                successful_encoding,
                encoding,
            )

        total_chars = len(text)

        # 项目名称默认用文件名（不含扩展名）
        if not project_name:
            project_name = file_path.stem

        # 创建项目
        project = self.storage.create_project(
            name=project_name,
            source_file=str(file_path),
        )

        # 拆分章节
        chapters = self._split_text(
            text=text,
            project_id=project.id,
            pattern=pattern,
            include_title_in_content=include_title_in_content,
            fallback_title=project_name,
        )

        # 保存章节
        for chapter in chapters:
            self.storage.save_chapter(chapter)

        elapsed = time.time() - start_time

        # 生成状态提示
        if len(chapters) == 1 and chapters[0].title in ("全文", project_name):
            message = f"未检测到章节标题，已作为单章导入（{total_chars} 字）"
        else:
            message = f"成功导入 {len(chapters)} 章（{total_chars} 字，耗时 {elapsed:.2f}s）"

        logger.info(
            "导入完成: project=%s, chapters=%d, chars=%d, elapsed=%.2fs",
            project.id,
            len(chapters),
            total_chars,
            elapsed,
        )

        return ImportResult(
            project=project,
            chapters=chapters,
            elapsed_seconds=elapsed,
            total_chars=total_chars,
            message=message,
        )

    def _split_text(
        self,
        text: str,
        project_id: str,
        pattern: str,
        include_title_in_content: bool,
        fallback_title: str,
    ) -> list[Chapter]:
        """拆分文本为章节列表。

        使用 ``re.finditer`` 批量匹配所有标题位置，按位置切片。
        性能：O(n) 时间复杂度，n 为文本长度。

        Args:
            text: 全文文本
            project_id: 项目 ID
            pattern: 章节标题正则
            include_title_in_content: 正文是否包含标题行
            fallback_title: 无匹配时的默认标题

        Returns:
            章节列表
        """
        # 编译正则（MULTILINE 使 ^ 匹配每行开头）
        try:
            regex = re.compile(pattern, re.MULTILINE)
        except re.error as e:
            logger.error("章节标题正则编译失败: %s, 使用默认正则", e)
            regex = re.compile(DEFAULT_CHAPTER_PATTERN, re.MULTILINE)

        # 批量匹配所有标题位置（带超时保护，防止灾难性回溯卡死导入）
        matches = _finditer_chapters_with_timeout(regex, text)
        if matches is None:
            # 超时：降级用默认正则重新匹配
            regex = re.compile(DEFAULT_CHAPTER_PATTERN, re.MULTILINE)
            matches = _finditer_chapters_with_timeout(regex, text) or []

        if not matches:
            # 无匹配，整文作为单章
            logger.info("未检测到章节标题，整文作为单章")
            chapter = Chapter(
                id=_generate_id("ch_"),
                project_id=project_id,
                index=0,
                title=fallback_title,
                content=text,
                word_count=len(text),
            )
            return [chapter]

        chapters: list[Chapter] = []
        now = None  # 使用默认值

        for i, match in enumerate(matches):
            # 标题行：从 match.start() 到该行末尾
            title_start = match.start()
            # 找到标题行的末尾（下一个换行符）
            line_end = text.find("\n", match.end())
            if line_end == -1:
                line_end = len(text)
            title = text[title_start:line_end].strip()

            # 章节正文范围
            if include_title_in_content:
                content_start = title_start
            else:
                content_start = line_end + 1 if line_end < len(text) else len(text)

            # 正文结束位置：下一个标题的开始，或文本末尾
            if i + 1 < len(matches):
                content_end = matches[i + 1].start()
            else:
                content_end = len(text)

            content = text[content_start:content_end].strip()

            chapter = Chapter(
                id=_generate_id("ch_"),
                project_id=project_id,
                index=i,
                title=title,
                content=content,
                word_count=len(content),
            )
            chapters.append(chapter)

        logger.info("拆分完成: %d 章", len(chapters))
        return chapters

    def split_at_position(
        self,
        chapter: Chapter,
        position: int,
    ) -> tuple[Chapter, Chapter]:
        """在指定位置拆分章节（供 ChapterService 调用）。

        Args:
            chapter: 原章节
            position: 字符偏移量（在 content 中的位置）

        Returns:
            (前半章节, 后半章节) 元组
        """
        content = chapter.content
        position = max(0, min(position, len(content)))

        # 尝试在最近的换行符处拆分，避免拆断句子
        if position < len(content):
            # 向后找换行符
            newline_after = content.find("\n", position)
            if newline_after != -1 and newline_after - position < 100:
                split_pos = newline_after + 1
            else:
                split_pos = position
        else:
            split_pos = position

        front_content = content[:split_pos].rstrip()
        back_content = content[split_pos:].strip()

        front = Chapter(
            id=chapter.id,  # 前半保留原 ID
            project_id=chapter.project_id,
            index=chapter.index,
            title=chapter.title,
            content=front_content,
            word_count=len(front_content),
            continuations=chapter.continuations,  # 原章节 continuations 归属前半
            metadata=chapter.metadata,
            created_at=chapter.created_at,
            updated_at=chapter.updated_at,
        )

        back = Chapter(
            id=_generate_id("ch_"),
            project_id=chapter.project_id,
            index=chapter.index + 1,
            title=f"{chapter.title}（续）",
            content=back_content,
            word_count=len(back_content),
        )

        return front, back
