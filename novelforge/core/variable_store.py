"""分层变量存储。

实现 global / project / chapter / cache 四层变量作用域：
- ``global``：全局持久化 JSON（``~/.novelforge/variables/global_variables.json``）
- ``project``：项目级持久化 JSON（``~/.novelforge/projects/{project_id}/variables.json``）
- ``chapter``：章节元数据（``chapter.metadata['variables']``）
- ``cache``：内存（不持久化，进程结束即失效）

统一通过函数式 API 访问：
- ``getvar(name, scope='chapter')``
- ``setvar(name, value, scope='chapter')``
- ``hasvar(name, scope='chapter')``
- ``delvar(name, scope='chapter')``

不支持 ``global.x`` 点号语法，统一用 ``getvar('x', scope='global')``。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from novelforge.core.storage import (
    atomic_write_file,
    get_default_storage_path,
    load_json_with_recovery,
    save_json_with_backup,
)

logger = logging.getLogger(__name__)

# 作用域常量
SCOPE_GLOBAL: str = "global"
SCOPE_PROJECT: str = "project"
SCOPE_CHAPTER: str = "chapter"
SCOPE_CACHE: str = "cache"

# 合法作用域集合
VALID_SCOPES: frozenset[str] = frozenset({
    SCOPE_GLOBAL, SCOPE_PROJECT, SCOPE_CHAPTER, SCOPE_CACHE,
})


class VariableStore:
    """分层变量存储。

    管理 global/project/chapter/cache 四层变量。
    global/project 持久化到 JSON 文件，chapter 持久化到章节元数据（由调用方负责），
    cache 仅存在于内存。

    Usage::

        store = VariableStore()
        # 设置全局变量
        store.setvar("app_version", "1.0", scope="global")
        # 读取项目变量
        val = store.getvar("protagonist", scope="project", project_id="proj_xxx")
        # 读取章节变量
        val = store.getvar("mood", scope="chapter", chapter_metadata=md)
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        """初始化变量存储。

        Args:
            storage_path: 存储根目录，默认 ``~/.novelforge``
        """
        self.storage_path: Path = storage_path or get_default_storage_path()
        self.variables_dir: Path = self.storage_path / "variables"
        self.variables_dir.mkdir(parents=True, exist_ok=True)

        # cache 作用域变量（内存）
        self._cache: dict[str, Any] = {}

        # 内存缓存：global/project 变量（避免频繁读文件）
        self._global_cache: dict[str, Any] | None = None
        self._project_cache: dict[str, dict[str, Any]] = {}

    # ===== 路径计算 =====

    def _get_global_file_path(self) -> Path:
        """全局变量文件路径。"""
        return self.variables_dir / "global_variables.json"

    def _get_project_file_path(self, project_id: str) -> Path:
        """项目变量文件路径。"""
        return self.storage_path / "projects" / project_id / "variables.json"

    # ===== global 作用域 =====

    def _load_global(self) -> dict[str, Any]:
        """加载全局变量（带内存缓存）。"""
        if self._global_cache is not None:
            return self._global_cache

        file_path = self._get_global_file_path()
        data, error = load_json_with_recovery(file_path)
        if error is not None:
            logger.error("加载全局变量失败: %s", error)
            data = {}
        elif data is None:
            data = {}
        if not isinstance(data, dict):
            data = {}

        self._global_cache = data
        return data

    def _save_global(self, data: dict[str, Any]) -> None:
        """保存全局变量。"""
        file_path = self._get_global_file_path()
        save_json_with_backup(file_path, data)
        self._global_cache = dict(data)

    # ===== project 作用域 =====

    def _load_project(self, project_id: str) -> dict[str, Any]:
        """加载项目变量（带内存缓存）。"""
        if project_id in self._project_cache:
            return self._project_cache[project_id]

        file_path = self._get_project_file_path(project_id)
        data, error = load_json_with_recovery(file_path)
        if error is not None:
            logger.error("加载项目变量失败 %s: %s", project_id, error)
            data = {}
        elif data is None:
            data = {}
        if not isinstance(data, dict):
            data = {}

        self._project_cache[project_id] = data
        return data

    def _save_project(self, project_id: str, data: dict[str, Any]) -> None:
        """保存项目变量。"""
        file_path = self._get_project_file_path(project_id)
        # 确保目录存在
        file_path.parent.mkdir(parents=True, exist_ok=True)
        save_json_with_backup(file_path, data)
        self._project_cache[project_id] = dict(data)

    # ===== chapter 作用域 =====

    @staticmethod
    def _get_chapter_vars(chapter_metadata: dict[str, Any]) -> dict[str, Any]:
        """从章节元数据中提取变量字典。

        章节变量存储在 ``chapter.metadata['variables']`` 中。
        """
        if not isinstance(chapter_metadata, dict):
            return {}
        variables = chapter_metadata.get("variables", {})
        if not isinstance(variables, dict):
            return {}
        return variables

    @staticmethod
    def _set_chapter_vars(
        chapter_metadata: dict[str, Any], variables: dict[str, Any]
    ) -> None:
        """将变量字典写回章节元数据。"""
        if not isinstance(chapter_metadata, dict):
            return
        chapter_metadata["variables"] = variables

    # ===== 公共 API =====

    def getvar(
        self,
        name: str,
        scope: str = SCOPE_CHAPTER,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
        default: Any = None,
    ) -> Any:
        """读取指定作用域的变量。

        Args:
            name: 变量名
            scope: 作用域（global/project/chapter/cache）
            project_id: 项目 ID（project 作用域必填）
            chapter_metadata: 章节元数据字典（chapter 作用域必填）
            default: 变量不存在时的默认值

        Returns:
            变量值，不存在时返回 default
        """
        if scope == SCOPE_GLOBAL:
            data = self._load_global()
            return data.get(name, default)
        if scope == SCOPE_PROJECT:
            if not project_id:
                return default
            data = self._load_project(project_id)
            return data.get(name, default)
        if scope == SCOPE_CHAPTER:
            if chapter_metadata is None:
                return default
            data = self._get_chapter_vars(chapter_metadata)
            return data.get(name, default)
        if scope == SCOPE_CACHE:
            return self._cache.get(name, default)
        logger.warning("未知的作用域 %r，返回默认值", scope)
        return default

    def setvar(
        self,
        name: str,
        value: Any,
        scope: str = SCOPE_CHAPTER,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
    ) -> None:
        """设置指定作用域的变量。

        Args:
            name: 变量名
            value: 变量值
            scope: 作用域（global/project/chapter/cache）
            project_id: 项目 ID（project 作用域必填）
            chapter_metadata: 章节元数据字典（chapter 作用域必填，会被原地修改）
        """
        if scope == SCOPE_GLOBAL:
            data = self._load_global()
            data[name] = value
            self._save_global(data)
            return
        if scope == SCOPE_PROJECT:
            if not project_id:
                raise ValueError("project 作用域必须提供 project_id")
            data = self._load_project(project_id)
            data[name] = value
            self._save_project(project_id, data)
            return
        if scope == SCOPE_CHAPTER:
            if chapter_metadata is None:
                raise ValueError("chapter 作用域必须提供 chapter_metadata")
            data = self._get_chapter_vars(chapter_metadata)
            data[name] = value
            self._set_chapter_vars(chapter_metadata, data)
            return
        if scope == SCOPE_CACHE:
            self._cache[name] = value
            return
        raise ValueError(f"未知的作用域: {scope}")

    def hasvar(
        self,
        name: str,
        scope: str = SCOPE_CHAPTER,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
    ) -> bool:
        """判断指定作用域是否存在变量。

        Args:
            name: 变量名
            scope: 作用域
            project_id: 项目 ID
            chapter_metadata: 章节元数据字典

        Returns:
            是否存在
        """
        if scope == SCOPE_GLOBAL:
            return name in self._load_global()
        if scope == SCOPE_PROJECT:
            if not project_id:
                return False
            return name in self._load_project(project_id)
        if scope == SCOPE_CHAPTER:
            if chapter_metadata is None:
                return False
            return name in self._get_chapter_vars(chapter_metadata)
        if scope == SCOPE_CACHE:
            return name in self._cache
        return False

    def delvar(
        self,
        name: str,
        scope: str = SCOPE_CHAPTER,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
    ) -> bool:
        """删除指定作用域的变量。

        Args:
            name: 变量名
            scope: 作用域
            project_id: 项目 ID
            chapter_metadata: 章节元数据字典（会被原地修改）

        Returns:
            是否删除成功（变量不存在时返回 False）
        """
        if scope == SCOPE_GLOBAL:
            data = self._load_global()
            if name in data:
                del data[name]
                self._save_global(data)
                return True
            return False
        if scope == SCOPE_PROJECT:
            if not project_id:
                return False
            data = self._load_project(project_id)
            if name in data:
                del data[name]
                self._save_project(project_id, data)
                return True
            return False
        if scope == SCOPE_CHAPTER:
            if chapter_metadata is None:
                return False
            data = self._get_chapter_vars(chapter_metadata)
            if name in data:
                del data[name]
                self._set_chapter_vars(chapter_metadata, data)
                return True
            return False
        if scope == SCOPE_CACHE:
            if name in self._cache:
                del self._cache[name]
                return True
            return False
        return False

    # ===== 批量操作 =====

    def list_vars(
        self,
        scope: str,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """列出指定作用域的所有变量。

        Args:
            scope: 作用域
            project_id: 项目 ID
            chapter_metadata: 章节元数据字典

        Returns:
            变量名到值的字典
        """
        if scope == SCOPE_GLOBAL:
            return dict(self._load_global())
        if scope == SCOPE_PROJECT:
            if not project_id:
                return {}
            return dict(self._load_project(project_id))
        if scope == SCOPE_CHAPTER:
            if chapter_metadata is None:
                return {}
            return dict(self._get_chapter_vars(chapter_metadata))
        if scope == SCOPE_CACHE:
            return dict(self._cache)
        return {}

    def clear_scope(
        self,
        scope: str,
        project_id: str = "",
    ) -> None:
        """清空指定作用域的所有变量。

        注意：chapter 作用域需由调用方直接操作 metadata，不支持此方法。

        Args:
            scope: 作用域
            project_id: 项目 ID
        """
        if scope == SCOPE_GLOBAL:
            self._save_global({})
        elif scope == SCOPE_PROJECT:
            if project_id:
                self._save_project(project_id, {})
        elif scope == SCOPE_CACHE:
            self._cache.clear()
        else:
            logger.warning("不支持清空作用域: %s", scope)

    def invalidate_cache(
        self,
        scope: str = "",
        project_id: str = "",
    ) -> None:
        """使内存缓存失效（下次访问时重新从文件加载）。

        Args:
            scope: 作用域（空字符串表示所有作用域）
            project_id: 项目 ID
        """
        if not scope or scope == SCOPE_GLOBAL:
            self._global_cache = None
        if not scope or scope == SCOPE_PROJECT:
            if project_id:
                self._project_cache.pop(project_id, None)
            elif not scope:
                self._project_cache.clear()

    # ===== 便捷方法（用于模板引擎绑定） =====

    def make_template_context(
        self,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构建模板引擎可用的变量访问上下文。

        返回一个字典，包含 ``getvar``/``setvar``/``hasvar``/``delvar`` 函数，
        这些函数已绑定默认的 project_id 和 chapter_metadata。

        Args:
            project_id: 项目 ID
            chapter_metadata: 章节元数据

        Returns:
            含变量访问函数的字典
        """
        def getvar(name: str, scope: str = SCOPE_CHAPTER, default: Any = None) -> Any:
            return self.getvar(
                name, scope=scope, project_id=project_id,
                chapter_metadata=chapter_metadata, default=default,
            )

        def setvar(name: str, value: Any, scope: str = SCOPE_CHAPTER) -> None:
            self.setvar(
                name, value, scope=scope,
                project_id=project_id, chapter_metadata=chapter_metadata,
            )

        def hasvar(name: str, scope: str = SCOPE_CHAPTER) -> bool:
            return self.hasvar(
                name, scope=scope,
                project_id=project_id, chapter_metadata=chapter_metadata,
            )

        def delvar(name: str, scope: str = SCOPE_CHAPTER) -> bool:
            return self.delvar(
                name, scope=scope,
                project_id=project_id, chapter_metadata=chapter_metadata,
            )

        return {
            "getvar": getvar,
            "setvar": setvar,
            "hasvar": hasvar,
            "delvar": delvar,
        }
