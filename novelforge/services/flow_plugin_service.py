"""流程控制插件服务。

负责流程插件的加载、保存、删除、导入导出，以及内置插件的首启复制。

存储路径：``~/.novelforge/flow_plugins/{plugin_id}.json``

主要功能：
- 内置插件首启复制（single/volume/rewrite_current 三种模式）
- 插件 CRUD 操作（基于 BaseJsonService + .bak 备份）
- 导入插件 JSON：校验后强制 builtin=False，ID 冲突追加 ``_imported`` 后缀
- 导出插件 JSON：单个 JSON 文件，可分享给其他用户

设计参见 spec.md「流程控制插件系统」一节。
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from novelforge.core.storage import get_default_storage_path, load_json_with_recovery
from novelforge.models import FlowPlugin
from novelforge.services._base_json_service import BaseJsonService
from novelforge.utils.paths import get_default_flow_plugin_path

logger = logging.getLogger(__name__)

# 内置插件 ID 列表（与原续写模式字符串一致以保兼容）
_BUILTIN_PLUGIN_IDS: tuple[str, ...] = ("single", "volume", "rewrite_current")


class FlowPluginService(BaseJsonService[FlowPlugin]):
    """流程控制插件 CRUD 服务。

    继承 :class:`BaseJsonService` 获得统一的 load/save/delete/list_ids 实现，
    额外保留内置插件保护、首启复制、导入导出等业务逻辑。

    Usage::

        service = FlowPluginService()
        plugin = service.load_plugin("single")
        custom = service.import_plugin(Path("/path/to/my_flow.json"))
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        """初始化流程插件服务。

        Args:
            storage_path: 存储根目录，默认 ``~/.novelforge``
        """
        self.storage_path: Path = storage_path or get_default_storage_path()
        self.plugins_dir: Path = self.storage_path / "flow_plugins"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        # 委托基类处理通用的 load/save/delete/list_ids
        super().__init__(
            directory=self.plugins_dir,
            model_class=FlowPlugin,
            model_dump_kwargs={
                "mode": "json",
                "by_alias": True,
                "exclude_none": False,
            },
        )
        self._ensure_builtin_plugins()

    def _ensure_builtin_plugins(self) -> None:
        """首次启动时将内置插件 JSON 复制到用户目录，已有旧版时按版本升级。

        内置插件是三种续写模式的声明式描述，ID 与原模式字符串一致。
        用户目录已有同名文件时：若已安装版本为 builtin 且资源版本更高，覆盖升级；
        否则跳过（允许用户自定义修改内置插件）。
        """
        for plugin_id in _BUILTIN_PLUGIN_IDS:
            target = self.plugins_dir / f"{plugin_id}.json"
            src = get_default_flow_plugin_path(plugin_id)
            if not src.exists():
                logger.warning("内置插件资源不存在: %s", src)
                continue
            if not target.exists():
                try:
                    shutil.copy2(src, target)
                except OSError as e:
                    logger.error("复制内置插件失败 %s: %s", plugin_id, e)
                continue
            # 已存在：检查版本升级
            if self._should_upgrade_builtin(target, src):
                try:
                    shutil.copy2(src, target)
                    logger.info("内置插件 %s 已升级到资源版本", plugin_id)
                except OSError as e:
                    logger.error("升级内置插件失败 %s: %s", plugin_id, e)

    def _should_upgrade_builtin(self, target: Path, src: Path) -> bool:
        """判断是否应将用户目录的内置插件升级到资源版本。

        条件：已安装 builtin=True 且资源 version > 已安装 version。

        Args:
            target: 用户目录插件路径
            src: 资源目录插件路径

        Returns:
            是否应升级
        """
        try:
            installed_data, err = load_json_with_recovery(target)
            if err is not None or installed_data is None:
                return False
            if not installed_data.get("builtin", False):
                return False
            src_text = src.read_text(encoding="utf-8")
            src_data = json.loads(src_text)
            installed_ver = self._parse_version(installed_data.get("version", "0"))
            src_ver = self._parse_version(src_data.get("version", "0"))
            return src_ver > installed_ver
        except Exception as e:
            logger.warning("检查内置插件版本失败 %s: %s", target, e)
            return False

    @staticmethod
    def _parse_version(s: str) -> tuple[int, int]:
        """解析版本字符串为可比较的元组。

        Args:
            s: 版本字符串（如 "1.0"、"2.0"）

        Returns:
            (major, minor) 元组，解析失败返回 (0, 0)
        """
        try:
            parts = s.strip().split(".")
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else 0
            return (major, minor)
        except (ValueError, IndexError):
            return (0, 0)

    # ===== 单插件 CRUD =====

    def load_plugin(self, plugin_id: str) -> FlowPlugin | None:
        """加载单个流程插件。

        Args:
            plugin_id: 插件 ID

        Returns:
            FlowPlugin 对象；文件不存在、损坏或校验失败时返回 None
        """
        return self.load(self.plugins_dir / f"{plugin_id}.json")

    def save_plugin(self, plugin: FlowPlugin) -> None:
        """保存流程插件（写入前 .bak 备份）。

        Args:
            plugin: 待保存的插件对象
        """
        self.save(self.plugins_dir / f"{plugin.id}.json", plugin)

    def delete_plugin(self, plugin_id: str) -> bool:
        """删除流程插件（内置插件不可删除）。

        Args:
            plugin_id: 插件 ID

        Returns:
            是否删除成功（内置插件返回 False）
        """
        plugin = self.load_plugin(plugin_id)
        if plugin and plugin.builtin:
            logger.warning("内置插件不可删除: %s", plugin_id)
            return False
        return self.delete(self.plugins_dir / f"{plugin_id}.json")

    # ===== 列表 =====

    def list_plugins(self) -> list[FlowPlugin]:
        """列出所有流程插件（加载失败的项目跳过）。

        Returns:
            FlowPlugin 列表（无排序保证）
        """
        return [
            p
            for p in (self.load_plugin(pid) for pid in self.list_ids())
            if p is not None
        ]

    def list_plugin_ids_sorted(self) -> list[str]:
        """列出所有插件 ID（内置在前，自定义按 ID 排序）。

        Returns:
            插件 ID 列表
        """
        plugins = self.list_plugins()
        builtin = [p.id for p in plugins if p.builtin]
        custom = sorted(p.id for p in plugins if not p.builtin)
        return builtin + custom

    # ===== 导入导出 =====

    def export_plugin(self, plugin_id: str, dest_path: Path) -> bool:
        """导出插件为单个 JSON 文件。

        Args:
            plugin_id: 插件 ID
            dest_path: 目标文件路径

        Returns:
            是否导出成功（插件不存在返回 False）
        """
        plugin = self.load_plugin(plugin_id)
        if not plugin:
            logger.warning("导出失败，插件不存在: %s", plugin_id)
            return False
        data = plugin.model_dump(mode="json", by_alias=True)
        dest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True

    def import_plugin(
        self, src_path: Path, overwrite: bool = False
    ) -> FlowPlugin | None:
        """从 JSON 文件导入插件。

        校验通过后强制 ``builtin=False``（导入的插件不能伪装为内置）。
        ID 冲突时 ``overwrite=False`` 追加 ``_imported`` 后缀，
        ``overwrite=True`` 直接覆盖。

        Args:
            src_path: 源 JSON 文件路径
            overwrite: ID 冲突时是否覆盖

        Returns:
            导入后的 FlowPlugin 对象；校验失败返回 None
        """
        data, error = load_json_with_recovery(src_path)
        if error is not None or data is None:
            logger.error("导入插件失败，文件读取/解析错误 %s: %s", src_path, error)
            return None
        try:
            plugin = FlowPlugin.model_validate(data)
        except ValueError as e:
            logger.error("导入插件校验失败 %s: %s", src_path, e)
            return None
        # 导入的插件强制为非内置
        plugin.builtin = False
        # ID 冲突处理
        if self.load_plugin(plugin.id) is not None and not overwrite:
            plugin.id = f"{plugin.id}_imported"
            logger.info("插件 ID 冲突，重命名为: %s", plugin.id)
        self.save_plugin(plugin)
        return plugin
