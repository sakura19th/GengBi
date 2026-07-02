"""全局世界书服务。

负责全局世界书的加载、保存、导入导出（兼容 SillyTavern 世界书 JSON 格式）。

存储路径：``~/.novelforge/worldbooks/{worldbook_id}.json``

主要功能：
- 世界书的 CRUD 操作（基于文件系统 + .bak 备份）
- 导入 ST 世界书 JSON：复用 ``worldbook_importer.import_worldbook``
- 导出 ST 世界书 JSON：从 ``ContextEntry`` 重建 ST 格式
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from novelforge.core.storage import (
    atomic_write_file,
    get_default_storage_path,
)
from novelforge.models.context import ContextEntry
from novelforge.models.worldbook import WorldBook
from novelforge.services._base_json_service import BaseJsonService
from novelforge.services.storage_service import _generate_id
from novelforge.services.worldbook_importer import import_worldbook
from novelforge.utils.ids import validate_id

logger = logging.getLogger(__name__)

# 赓笔 position 字符串 → ST position 数字 反向映射
# ST position 枚举：before=0, after=1, atDepth=4（赓笔仅保留这 3 种语义）
REVERSE_POSITION_MAP: dict[str, int] = {
    "before": 0,
    "after": 1,
    "at_depth": 4,
}

# 赓笔 role 字符串 → ST role 数字 反向映射
REVERSE_ROLE_MAP: dict[str, int] = {
    "system": 0,
    "user": 1,
    "assistant": 2,
}


class WorldBookService(BaseJsonService[WorldBook]):
    """全局世界书服务。

    提供世界书的加载、保存、新建、删除、导入导出功能。

    继承 :class:`BaseJsonService` 获得统一的 load/save/delete/list_ids 实现，
    额外保留时间戳更新与按名称排序等业务逻辑。

    Usage::

        service = WorldBookService()
        wb = service.create_worldbook("我的世界书")
        imported = service.import_from_st_json("/path/to/st_worldbook.json")
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        """初始化世界书服务。

        Args:
            storage_path: 存储根目录，默认 ``~/.novelforge``
        """
        self.storage_path: Path = storage_path or get_default_storage_path()
        self.worldbooks_dir: Path = self.storage_path / "worldbooks"
        self.worldbooks_dir.mkdir(parents=True, exist_ok=True)
        # 委托基类处理通用的 load/save/delete/list_ids
        super().__init__(directory=self.worldbooks_dir, model_class=WorldBook)

    # ===== 世界书 CRUD =====

    def list_worldbooks(self) -> list[WorldBook]:
        """列出所有世界书。

        Returns:
            WorldBook 列表（按 name 排序）
        """
        worldbooks: list[WorldBook] = []
        for wb_id in self.list_ids():
            wb = self.load_worldbook(wb_id)
            if wb is not None:
                worldbooks.append(wb)
        worldbooks.sort(key=lambda w: w.name)
        return worldbooks

    def load_worldbook(self, wb_id: str) -> WorldBook | None:
        """加载指定 ID 的世界书。

        Args:
            wb_id: 世界书 ID

        Returns:
            WorldBook 对象，不存在时返回 None
        """
        validate_id(wb_id, "worldbook_id")
        wb_path = self.worldbooks_dir / f"{wb_id}.json"
        return self.load(wb_path)

    def save_worldbook(self, wb: WorldBook) -> None:
        """保存世界书到文件系统（写入前 .bak 备份）。

        Args:
            wb: 待保存的世界书
        """
        validate_id(wb.id, "worldbook_id")
        wb.updated_at = datetime.now()
        wb_path = self.worldbooks_dir / f"{wb.id}.json"
        self.save(wb_path, wb)
        logger.info("已保存世界书: %s (%s)", wb.id, wb.name)

    def delete_worldbook(self, wb_id: str) -> bool:
        """删除世界书。

        Args:
            wb_id: 世界书 ID

        Returns:
            是否删除成功
        """
        validate_id(wb_id, "worldbook_id")
        wb_path = self.worldbooks_dir / f"{wb_id}.json"
        if not self.delete(wb_path):
            return False
        logger.info("已删除世界书: %s", wb_id)
        return True

    def create_worldbook(self, name: str) -> WorldBook:
        """创建空世界书。

        Args:
            name: 世界书名称

        Returns:
            新建的 WorldBook 对象
        """
        wb_id = _generate_id("wb_")
        wb = WorldBook(id=wb_id, name=name)
        self.save_worldbook(wb)
        return wb

    # ===== ST 世界书导入导出 =====

    def import_from_st_json(self, file_path: str | Path) -> WorldBook:
        """从 ST 世界书 JSON 文件导入。

        调用 ``worldbook_importer.import_worldbook`` 解析条目，
        世界书名称取文件名 stem。

        Args:
            file_path: ST 世界书 JSON 文件路径

        Returns:
            新建的 WorldBook 对象

        Raises:
            FileNotFoundError: 文件不存在
            json.JSONDecodeError: JSON 解析失败
            ValueError: 文件格式不合法
        """
        file_path = Path(file_path)
        entries = import_worldbook(file_path)
        wb_id = _generate_id("wb_")
        wb = WorldBook(
            id=wb_id,
            name=file_path.stem,
            entries=entries,
        )
        self.save_worldbook(wb)
        logger.info(
            "导入 ST 世界书成功: %s（%d 条条目）",
            wb.name,
            len(entries),
        )
        return wb

    def export_to_st_json(self, wb: WorldBook, file_path: str | Path) -> None:
        """导出世界书为 ST 兼容 JSON 文件。

        每个条目从 ``ContextEntry.raw_st_fields`` 恢复原始 ST 字段，
        再用赓笔字段覆盖（key/comment/content/order/position/depth/role/disable）。

        Args:
            wb: 待导出的世界书
            file_path: 目标文件路径
        """
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        entries_data: dict[str, Any] = {}
        for i, entry in enumerate(wb.entries):
            entries_data[str(i)] = self._entry_to_st_dict(entry)

        data: dict[str, Any] = {"entries": entries_data}
        content = json.dumps(data, ensure_ascii=False, indent=2)
        atomic_write_file(file_path, content)
        logger.info("导出世界书到 %s", file_path)

    @staticmethod
    def _entry_to_st_dict(entry: ContextEntry) -> dict[str, Any]:
        """将 ContextEntry 转换为 ST 兼容字典（含未识别字段）。

        先从 ``raw_st_fields`` 恢复未识别字段，再用赓笔字段覆盖。
        position/role 通过反向映射转回数字；disable 由条目级 enabled
        反向映射（enabled=False → disable=True）。
        """
        # 从 raw_st_fields 恢复未识别字段
        data: dict[str, Any] = dict(entry.raw_st_fields)
        # 用赓笔字段覆盖
        data["uid"] = entry.uid
        data["key"] = entry.key
        data["comment"] = entry.comment
        data["content"] = entry.content
        data["order"] = entry.order
        data["position"] = REVERSE_POSITION_MAP.get(entry.position, 0)
        data["depth"] = entry.depth
        data["role"] = REVERSE_ROLE_MAP.get(entry.role, 0)
        data["disable"] = not entry.enabled
        return data

    def set_worldbook_enabled(self, wb_id: str, enabled: bool) -> bool:
        """切换世界书启用状态。

        Args:
            wb_id: 世界书 ID
            enabled: 是否启用

        Returns:
            是否成功（世界书不存在时返回 False）
        """
        wb = self.load_worldbook(wb_id)
        if wb is None:
            return False
        wb.enabled = enabled
        self.save_worldbook(wb)
        return True

    def set_entry_enabled(
        self, wb: WorldBook, uid: str, enabled: bool
    ) -> bool:
        """切换世界书内单条条目的启用状态并持久化。

        Args:
            wb: 当前世界书（内存对象，调用方持有引用）
            uid: 条目 UID
            enabled: 是否启用

        Returns:
            是否找到并更新成功（uid 不存在时返回 False）
        """
        for entry in wb.entries:
            if entry.uid == uid:
                entry.enabled = enabled
                self.save_worldbook(wb)
                return True
        return False
