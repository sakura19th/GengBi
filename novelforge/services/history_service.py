"""续写历史日志服务。

记录每次续写操作（含中断、失败）到 SQLite ``history_log`` 表，
支持按项目、章节、时间筛选查询，以及详情查看、删除、清空。

历史日志字段：
- ``id``: 日志 ID
- ``project_id``: 项目 ID
- ``chapter_id``: 章节 ID
- ``swipe_id``: 续写版本 ID（可能为空，如续写失败时）
- ``started_at``: 开始时间 ISO 字符串
- ``finished_at``: 结束时间 ISO 字符串
- ``status``: 状态（completed/interrupted/failed）
- ``model``: 使用的模型名
- ``parameters``: 生成参数（dict）
- ``prompt_messages``: 提示词消息数组（list[dict]）
- ``output_text``: 输出文本
- ``error_message``: 错误信息（失败时）
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from novelforge.services.storage_service import StorageService
from novelforge.utils.ids import generate_id

logger = logging.getLogger(__name__)


def _generate_history_id(prefix: str = "hist_") -> str:
    """生成历史日志 ID（12 位 hex）。

    保留为薄包装以兼容既有调用点；新代码应直接使用
    :func:`novelforge.utils.ids.generate_id`。
    """
    return generate_id(prefix)


class HistoryService:
    """续写历史日志服务。

    封装 ``StorageService`` 的历史日志操作，提供高层 API。

    Usage::

        service = HistoryService(storage_service)
        hist_id = service.log_continuation(
            project_id="proj_xxx",
            chapter_id="chap_xxx",
            swipe_id="cont_xxx",
            started_at="2026-06-22T10:00:00",
            finished_at="2026-06-22T10:01:30",
            status="completed",
            model="gpt-4o",
            parameters={"temperature": 0.8},
            prompt_messages=[{"role": "user", "content": "..."}],
            output_text="...",
        )
        logs = service.list_history(project_id="proj_xxx")
    """

    def __init__(self, storage_service: StorageService) -> None:
        """初始化历史日志服务。

        Args:
            storage_service: 存储服务实例
        """
        self.storage_service = storage_service

    def log_continuation(
        self,
        project_id: str,
        chapter_id: str,
        swipe_id: str,
        started_at: str,
        finished_at: str,
        status: str,
        model: str,
        parameters: dict[str, Any],
        prompt_messages: list[dict[str, Any]],
        output_text: str,
        error_message: str = "",
    ) -> str:
        """记录一次续写操作到历史日志。

        Args:
            project_id: 项目 ID
            chapter_id: 章节 ID
            swipe_id: 续写版本 ID（可为空）
            started_at: 开始时间 ISO 字符串
            finished_at: 结束时间 ISO 字符串
            status: 状态（completed/interrupted/failed）
            model: 模型名
            parameters: 生成参数
            prompt_messages: 提示词消息数组
            output_text: 输出文本
            error_message: 错误信息（失败时）

        Returns:
            历史日志 ID
        """
        # 校验 status 取值，未知状态统一记录为 'failed'
        VALID_STATUSES = {"completed", "interrupted", "failed"}
        if status not in VALID_STATUSES:
            logger.warning("未知的历史状态 %r，记录为 'failed'", status)
            status = "failed"
        history_id = _generate_history_id()
        data = {
            "id": history_id,
            "project_id": project_id,
            "chapter_id": chapter_id,
            "swipe_id": swipe_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": status,
            "model": model,
            "parameters": parameters,
            "prompt_messages": prompt_messages,
            "output_text": output_text,
            "error_message": error_message,
        }
        try:
            self.storage_service.save_history_log(data)
            logger.debug(
                "记录历史日志: %s (project=%s, chapter=%s, status=%s)",
                history_id,
                project_id,
                chapter_id,
                status,
            )
        except Exception as e:
            logger.error("记录历史日志失败: %s", e, exc_info=True)
            # 不抛出，避免阻塞续写流程
        return history_id

    def list_history(
        self,
        project_id: str | None = None,
        chapter_id: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """按条件查询历史日志。

        Args:
            project_id: 项目 ID 筛选（None 不筛选）
            chapter_id: 章节 ID 筛选（None 不筛选）
            start_time: 开始时间 ISO 字符串筛选（None 不筛选）
            end_time: 结束时间 ISO 字符串筛选（None 不筛选）
            limit: 返回条数上限

        Returns:
            日志条目字典列表（按 started_at 降序）
        """
        filters: dict[str, Any] = {"limit": limit}
        if project_id:
            filters["project_id"] = project_id
        if chapter_id:
            filters["chapter_id"] = chapter_id
        if start_time:
            filters["start_time"] = start_time
        if end_time:
            filters["end_time"] = end_time
        try:
            return self.storage_service.list_history_logs(filters)
        except Exception as e:
            logger.error("查询历史日志失败: %s", e, exc_info=True)
            return []

    def get_history_detail(self, history_id: str) -> dict[str, Any] | None:
        """获取单条历史日志详情。

        Args:
            history_id: 日志 ID

        Returns:
            日志条目字典（含完整 prompt_messages 和 output_text），不存在时返回 None
        """
        try:
            return self.storage_service.get_history_log(history_id)
        except Exception as e:
            logger.error("获取历史日志详情失败: %s", e, exc_info=True)
            return None

    def delete_history(self, history_id: str) -> None:
        """删除单条历史日志。

        Args:
            history_id: 日志 ID
        """
        try:
            self.storage_service.delete_history_log(history_id)
            logger.info("删除历史日志: %s", history_id)
        except Exception as e:
            logger.error("删除历史日志失败: %s", e, exc_info=True)

    def clear_history(self, project_id: str | None = None) -> None:
        """清空历史日志。

        Args:
            project_id: 指定项目 ID 时只清空该项目日志，None 时清空全部
        """
        try:
            self.storage_service.clear_history_logs(project_id)
            if project_id:
                logger.info("清空项目 %s 的历史日志", project_id)
            else:
                logger.info("清空所有历史日志")
        except Exception as e:
            logger.error("清空历史日志失败: %s", e, exc_info=True)

    @staticmethod
    def now_iso() -> str:
        """返回当前时间的 ISO 字符串（用于 started_at/finished_at）。"""
        return datetime.now().isoformat()
