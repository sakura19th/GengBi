"""JSON CRUD 服务基类。

为 preset/worldbook 等基于 JSON 文件存储的服务提供统一的
load/save/delete/list 实现，消除跨文件重复代码。

存储模式约定：一个模型对应一个 JSON 文件（``{id}.json``），
写入前 .bak 备份，损坏时从 .bak 恢复。

注意：本基类适用于「一模型一文件」的存储模式。对于将多个模型
序列化为数组存入单文件的服务（如 RegexService 按作用域存储
脚本列表），其 CRUD 模式与本基类不同，不应使用本基类。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from novelforge.core.storage import load_json_with_recovery, save_json_with_backup

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class BaseJsonService(Generic[T]):
    """JSON 文件 CRUD 服务的泛型基类。

    为「一模型一文件」存储模式提供统一的 load/save/delete/list_ids 实现。
    子类继承后，将自身的 ``load_xxx``/``save_xxx``/``delete_xxx``/``list_xxx``
    方法委托给基类的 :meth:`load`/:meth:`save`/:meth:`delete`/:meth:`list_ids`，
    并保留额外的业务逻辑（如默认项保护、排序、时间戳更新等）。

    Usage::

        class MyService(BaseJsonService[MyModel]):
            def __init__(self, storage_path):
                directory = storage_path / "items"
                directory.mkdir(parents=True, exist_ok=True)
                super().__init__(directory=directory, model_class=MyModel)
                self.storage_path = storage_path

            def load_item(self, item_id):
                return self.load(self._directory / f"{item_id}.json")
    """

    def __init__(
        self,
        directory: Path,
        model_class: type[T],
        suffix: str = ".json",
        model_dump_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """初始化 JSON 服务基类。

        Args:
            directory: 存储目录（模型 JSON 文件所在目录）
            model_class: Pydantic 模型类，用于校验加载的数据
            suffix: 文件后缀（默认 ``.json``）
            model_dump_kwargs: ``model_dump`` 调用参数，
                默认 ``{"mode": "json", "by_alias": True}``
        """
        self._directory = directory
        self._model_class = model_class
        self._suffix = suffix
        self._model_dump_kwargs: dict[str, Any] = model_dump_kwargs or {
            "mode": "json",
            "by_alias": True,
        }

    def load(self, file_path: Path) -> T | None:
        """加载单个 JSON 文件并验证为模型。

        文件损坏时由 :func:`load_json_with_recovery` 尝试从 .bak 恢复；
        恢复失败或模型校验失败时返回 None。

        Args:
            file_path: JSON 文件路径

        Returns:
            模型对象；文件不存在、损坏或校验失败时返回 None
        """
        data, error = load_json_with_recovery(file_path)
        if error is not None:
            logger.error("加载 JSON 失败 %s: %s", file_path, error)
            return None
        if data is None:
            return None
        try:
            return self._model_class.model_validate(data)
        except ValueError as e:
            logger.error("数据校验失败 %s: %s", file_path, e)
            return None

    def save(self, file_path: Path, model: T) -> None:
        """保存模型为 JSON 文件（写入前 .bak 备份）。

        Args:
            file_path: 目标文件路径
            model: 待保存的模型对象
        """
        data = model.model_dump(**self._model_dump_kwargs)
        save_json_with_backup(file_path, data)

    def delete(self, file_path: Path) -> bool:
        """删除 JSON 文件及其 .bak 备份。

        Args:
            file_path: 待删除的 JSON 文件路径

        Returns:
            是否删除成功（文件不存在或删除异常时返回 False）
        """
        if not file_path.exists():
            return False
        # 同时删除 .bak 备份
        bak_path = file_path.with_suffix(file_path.suffix + ".bak")
        try:
            file_path.unlink()
            if bak_path.exists():
                bak_path.unlink()
            return True
        except OSError as e:
            logger.error("删除文件失败 %s: %s", file_path, e)
            return False

    def list_ids(self) -> list[str]:
        """列出目录下所有 JSON 文件的 ID（文件 stem，排除 .bak）。

        Returns:
            ID 字符串列表（无排序保证）
        """
        if not self._directory.exists():
            return []
        return [
            f.stem
            for f in self._directory.glob(f"*{self._suffix}")
            if not f.name.endswith(".bak")
        ]
