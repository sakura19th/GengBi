"""正则脚本服务。

负责正则脚本的加载、保存、导入导出（兼容 SillyTavern 正则 JSON 格式），
以及按作用域分组管理与优先级排序。

存储路径约定：
- 全局：``~/.novelforge/regex/global.json``
- 项目级（scoped）：``~/.novelforge/projects/{project_id}/regex.json``
- 预设级（preset）：``~/.novelforge/regex/preset_{preset_id}.json``

脚本执行顺序（对齐 ST ``SCRIPT_TYPES`` 对象插入序，非数值大小）：
``GLOBAL(0) → PRESET(2) → SCOPED(1)``

ST 正则导入规则：
- 解析单个对象或数组
- 保留所有字段，未识别字段存入 ``_raw_st_fields``
- 导入时未识别 placement 值（0=MD_DISPLAY, 3=SLASH_COMMAND, 4=sendAs, 6=REASONING）
  忽略并记录 WARNING 日志
- 本工具仅处理 USER_INPUT(1)、AI_OUTPUT(2)、WORLD_INFO(5) 三种 placement
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from novelforge.core.regex_engine import parse_find_regex
from novelforge.core.storage import (
    atomic_write_file,
    get_default_storage_path,
    load_json_with_recovery,
    save_json_with_backup,
)
from novelforge.models import (
    PLACEMENT_AI_OUTPUT,
    PLACEMENT_USER_INPUT,
    PLACEMENT_WORLD_INFO,
    VALID_PLACEMENTS,
    RegexScript,
)
from novelforge.utils.ids import generate_id

logger = logging.getLogger(__name__)

# 作用域常量
SCOPE_GLOBAL: str = "global"
SCOPE_SCOPED: str = "scoped"
SCOPE_PRESET: str = "preset"

# 已知 RegexScript 字段名集合，用于识别 ST 正则中的未知字段
_KNOWN_REGEX_FIELDS: frozenset[str] = frozenset({
    "id", "scriptName", "findRegex", "replaceString", "trimStrings",
    "placement", "disabled", "markdownOnly", "promptOnly", "runOnEdit",
    "substituteRegex", "minDepth", "maxDepth", "markupSafety",
})

# 作用域执行优先级（数字越小越先执行）
# 对齐 ST SCRIPT_TYPES 对象插入序：GLOBAL(0) → PRESET(2) → SCOPED(1)
_SCOPE_PRIORITY: dict[str, int] = {
    SCOPE_GLOBAL: 0,
    SCOPE_PRESET: 1,
    SCOPE_SCOPED: 2,
}


def _generate_regex_id(prefix: str = "regex_") -> str:
    """生成正则脚本 ID（12 位 hex）。

    保留为薄包装以兼容既有调用点；新代码应直接使用
    :func:`novelforge.utils.ids.generate_id`。
    """
    return generate_id(prefix)


def _parse_regex_script_from_st(data: dict[str, Any]) -> RegexScript:
    """从 ST 正则 JSON 解析单条 RegexScript。

    未识别字段保留在 ``raw_st_fields`` 中，导出时原样写回。
    未识别的 placement 值会被过滤并记录 WARNING 日志。

    Args:
        data: ST RegexScript 字典

    Returns:
        RegexScript 对象
    """
    raw_st_fields: dict[str, Any] = {}
    for key, value in data.items():
        if key not in _KNOWN_REGEX_FIELDS:
            raw_st_fields[key] = value

    # 处理 placement：过滤未识别值并记录 WARNING
    raw_placement = data.get("placement", []) or []
    if not isinstance(raw_placement, list):
        raw_placement = [raw_placement]
    filtered_placement: list[int] = []
    for p in raw_placement:
        try:
            p_int = int(p)
        except (TypeError, ValueError):
            logger.warning("正则脚本 placement 值无效: %r，已忽略", p)
            continue
        if p_int in VALID_PLACEMENTS:
            filtered_placement.append(p_int)
        else:
            logger.warning(
                "正则脚本 placement 值 %d 未识别（本工具仅处理 1/2/5），已忽略",
                p_int,
            )

    # 处理 substituteRegex（可为 bool 或 int）
    sub_regex = data.get("substituteRegex", False)
    if isinstance(sub_regex, (int, bool)):
        sub_regex = bool(sub_regex)
    else:
        sub_regex = False

    return RegexScript(
        id=data.get("id", "") or _generate_regex_id(),
        scriptName=data.get("scriptName", ""),
        findRegex=data.get("findRegex", ""),
        replaceString=data.get("replaceString", ""),
        trimStrings=list(data.get("trimStrings", []) or []),
        placement=filtered_placement,
        disabled=bool(data.get("disabled", False)),
        markdownOnly=bool(data.get("markdownOnly", False)),
        promptOnly=bool(data.get("promptOnly", False)),
        runOnEdit=bool(data.get("runOnEdit", False)),
        substituteRegex=sub_regex,
        minDepth=int(data.get("minDepth") or 0) if data.get("minDepth") is not None else 0,
        maxDepth=int(data.get("maxDepth") or 0) if data.get("maxDepth") is not None else 0,
        markupSafety=bool(data.get("markupSafety", False)),
        raw_st_fields=raw_st_fields,
    )


def _regex_script_to_st_dict(script: RegexScript) -> dict[str, Any]:
    """将 RegexScript 转换为 ST 兼容字典（含未识别字段）。"""
    data: dict[str, Any] = {
        "id": script.id,
        "scriptName": script.scriptName,
        "findRegex": script.findRegex,
        "replaceString": script.replaceString,
        "trimStrings": list(script.trimStrings),
        "placement": list(script.placement),
        "disabled": script.disabled,
        "markdownOnly": script.markdownOnly,
        "promptOnly": script.promptOnly,
        "runOnEdit": script.runOnEdit,
        "substituteRegex": script.substituteRegex,
        "minDepth": script.minDepth,
        "maxDepth": script.maxDepth,
        "markupSafety": script.markupSafety,
    }
    # 写回未识别字段
    for key, value in script.raw_st_fields.items():
        data[key] = value
    return data


class RegexService:
    """正则脚本服务。

    提供正则脚本的加载、保存、新建、删除、导入导出功能，
    支持按作用域（global/scoped/preset）分组管理。

    Usage::

        service = RegexService()
        # 加载全局正则
        scripts = service.load_scripts(scope="global")
        # 导入 ST 正则
        new_scripts = service.import_from_st_json("/path/to/regex.json", scope="global")
        # 获取按优先级排序的所有脚本
        ordered = service.get_ordered_scripts(project_id="proj_xxx", preset_id="default")
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        """初始化正则服务。

        Args:
            storage_path: 存储根目录，默认 ``~/.novelforge``
        """
        self.storage_path: Path = storage_path or get_default_storage_path()
        self.regex_dir: Path = self.storage_path / "regex"
        self.regex_dir.mkdir(parents=True, exist_ok=True)

    # ===== 路径计算 =====

    def _get_scope_file_path(
        self,
        scope: str,
        project_id: str = "",
        preset_id: str = "",
    ) -> Path:
        """获取指定作用域的正则脚本文件路径。

        Args:
            scope: 作用域（global/scoped/preset）
            project_id: 项目 ID（scoped 时必填）
            preset_id: 预设 ID（preset 时必填）

        Returns:
            正则脚本 JSON 文件路径
        """
        if scope == SCOPE_GLOBAL:
            return self.regex_dir / "global.json"
        if scope == SCOPE_SCOPED:
            if not project_id:
                raise ValueError("scoped 作用域必须提供 project_id")
            return self.storage_path / "projects" / project_id / "regex.json"
        if scope == SCOPE_PRESET:
            if not preset_id:
                raise ValueError("preset 作用域必须提供 preset_id")
            return self.regex_dir / f"preset_{preset_id}.json"
        raise ValueError(f"未知的作用域: {scope}")

    # ===== 脚本加载与保存 =====

    def load_scripts(
        self,
        scope: str,
        project_id: str = "",
        preset_id: str = "",
    ) -> list[RegexScript]:
        """加载指定作用域的所有正则脚本。

        Args:
            scope: 作用域（global/scoped/preset）
            project_id: 项目 ID（scoped 时必填）
            preset_id: 预设 ID（preset 时必填）

        Returns:
            RegexScript 列表（文件不存在时返回空列表）
        """
        file_path = self._get_scope_file_path(scope, project_id, preset_id)
        data, error = load_json_with_recovery(file_path)
        if error is not None:
            logger.error("加载正则脚本失败 %s: %s", file_path, error)
            return []
        if data is None:
            return []
        if not isinstance(data, list):
            logger.warning("正则脚本文件 %s 顶层不是数组，已忽略", file_path)
            return []

        scripts: list[RegexScript] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                scripts.append(RegexScript.model_validate(item))
            except Exception as e:
                logger.warning("解析正则脚本失败，跳过: %s", e)
        return scripts

    def save_scripts(
        self,
        scripts: list[RegexScript],
        scope: str,
        project_id: str = "",
        preset_id: str = "",
    ) -> None:
        """保存指定作用域的所有正则脚本（写入前 .bak 备份）。

        Args:
            scripts: 正则脚本列表
            scope: 作用域（global/scoped/preset）
            project_id: 项目 ID（scoped 时必填）
            preset_id: 预设 ID（preset 时必填）
        """
        file_path = self._get_scope_file_path(scope, project_id, preset_id)
        data = [s.model_dump(mode="json", by_alias=True) for s in scripts]
        save_json_with_backup(file_path, data)
        logger.info(
            "已保存正则脚本: %s（%d 条）", file_path.name, len(scripts)
        )

    # ===== 单脚本操作 =====

    def add_script(
        self,
        script: RegexScript,
        scope: str,
        project_id: str = "",
        preset_id: str = "",
    ) -> None:
        """添加单个正则脚本到指定作用域。"""
        scripts = self.load_scripts(scope, project_id, preset_id)
        # 若 id 已存在则覆盖
        scripts = [s for s in scripts if s.id != script.id]
        scripts.append(script)
        self.save_scripts(scripts, scope, project_id, preset_id)

    def update_script(
        self,
        script: RegexScript,
        scope: str,
        project_id: str = "",
        preset_id: str = "",
    ) -> bool:
        """更新指定作用域中的正则脚本。

        Returns:
            是否更新成功（找不到对应 id 时返回 False）
        """
        scripts = self.load_scripts(scope, project_id, preset_id)
        for i, s in enumerate(scripts):
            if s.id == script.id:
                scripts[i] = script
                self.save_scripts(scripts, scope, project_id, preset_id)
                return True
        return False

    def delete_script(
        self,
        script_id: str,
        scope: str,
        project_id: str = "",
        preset_id: str = "",
    ) -> bool:
        """删除指定作用域中的正则脚本。"""
        scripts = self.load_scripts(scope, project_id, preset_id)
        new_scripts = [s for s in scripts if s.id != script_id]
        if len(new_scripts) == len(scripts):
            return False
        self.save_scripts(new_scripts, scope, project_id, preset_id)
        return True

    def create_script(
        self,
        name: str = "新正则脚本",
        find_regex: str = "/pattern/g",
        replace_string: str = "",
        placement: list[int] | None = None,
        scope: str = SCOPE_GLOBAL,
        project_id: str = "",
        preset_id: str = "",
    ) -> RegexScript:
        """创建新的正则脚本并保存。

        Args:
            name: 脚本名称
            find_regex: findRegex 字符串（/pattern/flags 格式）
            replace_string: 替换字符串
            placement: 应用时机列表（默认 [USER_INPUT]）
            scope: 作用域
            project_id: 项目 ID
            preset_id: 预设 ID

        Returns:
            新创建的 RegexScript 对象

        Raises:
            ValueError: find_regex 的 pattern 部分不是合法正则
        """
        if placement is None:
            placement = [PLACEMENT_USER_INPUT]
        # 保存前校验 findRegex 的 pattern 是否为合法正则
        pattern, _flags = parse_find_regex(find_regex)
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"无效的正则表达式: {pattern}") from e
        script = RegexScript(
            id=_generate_regex_id(),
            scriptName=name,
            findRegex=find_regex,
            replaceString=replace_string,
            placement=list(placement),
        )
        self.add_script(script, scope, project_id, preset_id)
        return script

    # ===== 优先级排序 =====

    def get_ordered_scripts(
        self,
        project_id: str = "",
        preset_id: str = "",
        placement: int | None = None,
        include_disabled: bool = False,
    ) -> list[tuple[RegexScript, str]]:
        """获取按执行优先级排序的所有正则脚本。

        执行顺序：GLOBAL(0) → PRESET(2) → SCOPED(1)
        （对齐 ST ``SCRIPT_TYPES`` 对象插入序，非数值大小）

        Args:
            project_id: 项目 ID（用于加载 scoped 脚本）
            preset_id: 预设 ID（用于加载 preset 脚本）
            placement: 过滤指定 placement 的脚本（None 表示不过滤）
            include_disabled: 是否包含禁用的脚本

        Returns:
            (RegexScript, scope) 元组列表，按执行顺序排列
        """
        result: list[tuple[RegexScript, str]] = []

        # GLOBAL 优先级最高
        for script in self.load_scripts(SCOPE_GLOBAL):
            result.append((script, SCOPE_GLOBAL))

        # PRESET 次之
        if preset_id:
            for script in self.load_scripts(SCOPE_PRESET, preset_id=preset_id):
                result.append((script, SCOPE_PRESET))

        # SCOPED 最后
        if project_id:
            for script in self.load_scripts(SCOPE_SCOPED, project_id=project_id):
                result.append((script, SCOPE_SCOPED))

        # 过滤禁用
        if not include_disabled:
            result = [(s, sc) for s, sc in result if not s.disabled]

        # 过滤 placement
        if placement is not None:
            result = [
                (s, sc) for s, sc in result if placement in s.placement
            ]

        return result

    @staticmethod
    def get_scope_priority(scope: str) -> int:
        """获取作用域的执行优先级（数字越小越先执行）。

        GLOBAL(0) → PRESET(1) → SCOPED(2)
        （内部用 0/1/2 表示顺序，对应 ST 的 GLOBAL→PRESET→SCOPED）
        """
        return _SCOPE_PRIORITY.get(scope, 99)

    # ===== ST 正则导入导出 =====

    def import_from_st_json(
        self,
        file_path: str | Path,
        scope: str = SCOPE_GLOBAL,
        project_id: str = "",
        preset_id: str = "",
    ) -> list[RegexScript]:
        """从 ST 正则 JSON 文件导入。

        解析规则：
        - 支持单个对象或数组
        - 保留所有字段，未识别字段存入 ``_raw_st_fields``
        - 未识别 placement 值忽略并记录 WARNING 日志
        - 本工具仅处理 USER_INPUT(1)、AI_OUTPUT(2)、WORLD_INFO(5)

        Args:
            file_path: ST 正则 JSON 文件路径
            scope: 导入到的作用域
            project_id: 项目 ID（scoped 时必填）
            preset_id: 预设 ID（preset 时必填）

        Returns:
            导入后的 RegexScript 列表（已保存到文件系统）

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: JSON 解析失败
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"正则文件不存在: {file_path}")

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}") from e

        # 统一为数组处理
        if isinstance(data, dict):
            data_list = [data]
        elif isinstance(data, list):
            data_list = data
        else:
            raise ValueError("正则 JSON 顶层必须是对象或数组")

        # 解析每条脚本
        new_scripts: list[RegexScript] = []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            try:
                script = _parse_regex_script_from_st(item)
                # 若 id 为空则生成
                if not script.id:
                    script.id = _generate_regex_id()
                new_scripts.append(script)
            except Exception as e:
                logger.warning("解析正则脚本失败，跳过: %s", e)

        # 追加到现有脚本（保留原有脚本）
        existing = self.load_scripts(scope, project_id, preset_id)
        existing_ids = {s.id for s in existing}
        for script in new_scripts:
            # 若 id 冲突则重新生成
            while script.id in existing_ids:
                script.id = _generate_regex_id()
            existing_ids.add(script.id)
            existing.append(script)

        self.save_scripts(existing, scope, project_id, preset_id)
        logger.info(
            "导入 ST 正则成功: %d 条（作用域: %s）", len(new_scripts), scope
        )
        return new_scripts

    def export_to_st_json(
        self,
        scripts: list[RegexScript],
        file_path: str | Path,
    ) -> None:
        """导出正则脚本为 ST 兼容 JSON 文件。

        未识别字段（``_raw_st_fields``）原样写回。

        Args:
            scripts: 待导出的正则脚本列表
            file_path: 目标文件路径
        """
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        data = [_regex_script_to_st_dict(s) for s in scripts]
        content = json.dumps(data, ensure_ascii=False, indent=2)
        atomic_write_file(file_path, content)
        logger.info("导出正则脚本到 %s（%d 条）", file_path, len(scripts))

    def export_scope_to_st_json(
        self,
        file_path: str | Path,
        scope: str = SCOPE_GLOBAL,
        project_id: str = "",
        preset_id: str = "",
    ) -> None:
        """导出指定作用域的正则脚本为 ST 兼容 JSON 文件。"""
        scripts = self.load_scripts(scope, project_id, preset_id)
        self.export_to_st_json(scripts, file_path)

    # ===== 默认正则 =====

    def load_default_scripts(self) -> list[RegexScript]:
        """加载内置默认正则脚本（资源文件，通常为空数组）。"""
        from novelforge.utils.paths import get_default_regex_scripts_path

        default_path = get_default_regex_scripts_path()
        try:
            data = json.loads(default_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            return [
                _parse_regex_script_from_st(item)
                for item in data
                if isinstance(item, dict)
            ]
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("加载默认正则脚本失败 %s: %s", default_path, e)
            return []

    def ensure_global_scripts_exist(self) -> None:
        """确保全局正则文件存在（不存在则创建空数组）。"""
        global_path = self._get_scope_file_path(SCOPE_GLOBAL)
        if not global_path.exists():
            save_json_with_backup(global_path, [])
            logger.info("已创建空的全局正则脚本文件: %s", global_path)
