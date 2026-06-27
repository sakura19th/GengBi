"""写作预设服务。

负责预设的加载、保存、导入导出（兼容 SillyTavern 预设 JSON 格式）。

存储路径：``~/.novelforge/presets/{preset_id}.json``

主要功能：
- 加载默认预设（``resources/defaults/default_preset.json``）
- 预设的 CRUD 操作（基于文件系统 + .bak 备份）
- 导入 ST 预设 JSON：解析 prompts/prompt_order/generation_params，
  ``character_id == 100000`` 视为全局顺序，取第一个匹配项
- 导出 ST 预设 JSON：未识别字段（``_raw_st_fields``）原样写回
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from novelforge.core.storage import (
    atomic_write_file,
    backup_file,
    get_default_storage_path,
    get_preset_file_path,
)
from novelforge.models import (
    GLOBAL_CHARACTER_ID,
    Prompt,
    PromptOrderEntry,
    PromptOrderGroup,
    WritingPreset,
)
from novelforge.services._base_json_service import BaseJsonService
from novelforge.utils.ids import generate_id
from novelforge.utils.paths import get_default_preset_path

logger = logging.getLogger(__name__)

# 已知 Prompt 字段名集合，用于识别 ST 预设中的未知字段
_KNOWN_PROMPT_FIELDS: frozenset[str] = frozenset({
    "identifier", "name", "role", "content", "system_prompt", "marker",
    "position", "injection_position", "injection_depth", "injection_order",
    "forbid_overrides", "extension", "enabled",
})

# 已知 WritingPreset 顶层字段名集合
_KNOWN_PRESET_FIELDS: frozenset[str] = frozenset({
    "id", "name", "prompts", "prompt_order", "generation_params",
})


def _extract_regex_scripts_from_extensions(
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """从 ST 预设的 extensions 字段提取正则脚本。

    支持三个来源（按优先级，按 id 去重）：
    1. ``extensions.regex_scripts`` — ST 内置正则
    2. ``extensions.SPreset.RegexBinding.regexes`` — SPreset 插件正则
    3. ``extensions.tavern_helper.scripts`` — TavernHelper 插件 JS 脚本（忽略，仅记录日志）

    Args:
        data: ST 预设 JSON 顶层字典

    Returns:
        去重后的正则脚本字典列表
    """
    extensions = data.get("extensions", {}) or {}
    if not isinstance(extensions, dict):
        return []

    seen_ids: set[str] = set()
    result: list[dict[str, Any]] = []

    # 来源 1：ST 内置 regex_scripts
    for script in extensions.get("regex_scripts", []) or []:
        if not isinstance(script, dict):
            continue
        script_id = script.get("id", "")
        if script_id and script_id in seen_ids:
            continue
        if script_id:
            seen_ids.add(script_id)
        result.append(script)

    # 来源 2：SPreset 插件 RegexBinding.regexes
    spreset = extensions.get("SPreset", {}) or {}
    if isinstance(spreset, dict):
        regex_binding = spreset.get("RegexBinding", {}) or {}
        if isinstance(regex_binding, dict):
            for script in regex_binding.get("regexes", []) or []:
                if not isinstance(script, dict):
                    continue
                script_id = script.get("id", "")
                if script_id and script_id in seen_ids:
                    continue
                if script_id:
                    seen_ids.add(script_id)
                result.append(script)

    # 来源 3：tavern_helper.scripts（JS 脚本，无法在 Python 执行，仅记录日志）
    tavern_helper = extensions.get("tavern_helper", {}) or {}
    if isinstance(tavern_helper, dict):
        js_scripts = tavern_helper.get("scripts", []) or []
        if js_scripts:
            logger.info(
                "忽略 tavern_helper.scripts 中的 %d 条 JS 脚本（无法在 Python 执行）",
                len(js_scripts),
            )

    return result


def _generate_preset_id(prefix: str = "preset_") -> str:
    """生成预设 ID（12 位 hex）。

    保留为薄包装以兼容既有调用点；新代码应直接使用
    :func:`novelforge.utils.ids.generate_id`。
    """
    return generate_id(prefix)


def _parse_marker_from_st(data: dict[str, Any]) -> str | None:
    """从 ST Prompt 字典解析 marker 字段。

    ST 标准格式中 marker 为字符串（如 "worldInfoBefore"），
    但部分预设（如 TGbreak）使用布尔值：``true`` 表示该 prompt 是 marker，
    此时用 ``identifier`` 字段作为 marker 名。

    Args:
        data: ST Prompt 字典

    Returns:
        marker 名称字符串，或 None
    """
    marker = data.get("marker")
    if isinstance(marker, str):
        return marker
    if marker is True:
        # 布尔 true：用 identifier 作为 marker 名
        return data.get("identifier", "") or None
    # False / None / 其他类型
    return None


def _parse_prompt_from_st(data: dict[str, Any]) -> Prompt:
    """从 ST 预设 JSON 解析单条 Prompt。

    未识别字段保留在 ``raw_st_fields`` 中，导出时原样写回。

    Args:
        data: ST Prompt 字典

    Returns:
        Prompt 对象
    """
    raw_st_fields: dict[str, Any] = {}
    for key, value in data.items():
        if key not in _KNOWN_PROMPT_FIELDS:
            raw_st_fields[key] = value

    return Prompt(
        identifier=data.get("identifier", ""),
        name=data.get("name", ""),
        role=data.get("role", "system"),
        content=data.get("content", ""),
        system_prompt=bool(data.get("system_prompt", False)),
        marker=_parse_marker_from_st(data),
        position=data.get("position", "start"),
        injection_position=int(data.get("injection_position", 0)),
        injection_depth=int(data.get("injection_depth", 4)),
        injection_order=int(data.get("injection_order", 100)),
        forbid_overrides=bool(data.get("forbid_overrides", False)),
        extension=data.get("extension", {}) or {},
        enabled=bool(data.get("enabled", True)),
        raw_st_fields=raw_st_fields,
    )


def _parse_preset_order_from_st(
    prompt_order_data: list[dict[str, Any]],
) -> list[PromptOrderGroup]:
    """解析 ST 预设的 prompt_order 字段。

    - 优先取 ``character_id == 100000`` 的全局分组
    - 若无全局分组，则取第一个分组（统一映射为 100000），记录 INFO 日志
    - 兼容 TGbreak 等使用非标准 character_id（如 100001）的预设

    Args:
        prompt_order_data: ST prompt_order 数组

    Returns:
        PromptOrderGroup 列表（通常只含一个全局分组）
    """
    result: list[PromptOrderGroup] = []
    has_global = False
    fallback_entry: dict[str, Any] | None = None

    for entry in prompt_order_data:
        char_id = entry.get("character_id", 0)
        if char_id == GLOBAL_CHARACTER_ID:
            if has_global:
                logger.info(
                    "已存在全局 prompt_order，忽略后续匹配项（character_id=%s）",
                    char_id,
                )
                continue
        else:
            # 非 100000 的分组作为 fallback 候选
            if fallback_entry is None:
                fallback_entry = entry
            logger.info(
                "记录非全局 prompt_order 条目作为 fallback（character_id=%s）",
                char_id,
            )
            continue

        order_list = entry.get("order", []) or []
        order_entries: list[PromptOrderEntry] = []
        for item in order_list:
            order_entries.append(
                PromptOrderEntry(
                    identifier=item.get("identifier", ""),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        result.append(
            PromptOrderGroup(
                character_id=GLOBAL_CHARACTER_ID,
                order=order_entries,
            )
        )
        has_global = True

    # 无全局分组时，使用 fallback（第一个非 100000 分组）
    if not result and fallback_entry is not None:
        logger.info(
            "无全局 prompt_order，使用 fallback 分组（character_id=%s）",
            fallback_entry.get("character_id", 0),
        )
        order_list = fallback_entry.get("order", []) or []
        order_entries: list[PromptOrderEntry] = []
        for item in order_list:
            order_entries.append(
                PromptOrderEntry(
                    identifier=item.get("identifier", ""),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        result.append(
            PromptOrderGroup(
                character_id=GLOBAL_CHARACTER_ID,
                order=order_entries,
            )
        )

    if not result:
        # 无任何分组，返回空分组（保持 character_id=100000 约定）
        result.append(PromptOrderGroup(character_id=GLOBAL_CHARACTER_ID, order=[]))

    return result


class PresetService(BaseJsonService[WritingPreset]):
    """写作预设服务。

    提供预设的加载、保存、新建、删除、导入导出功能。

    继承 :class:`BaseJsonService` 获得统一的 load/save/delete/list_ids 实现，
    额外保留默认预设保护、排序等业务逻辑。

    Usage::

        service = PresetService()
        preset = service.load_preset("default")
        new_preset = service.import_from_st_json("/path/to/st_preset.json")
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        """初始化预设服务。

        Args:
            storage_path: 存储根目录，默认 ``~/.novelforge``
        """
        self.storage_path: Path = storage_path or get_default_storage_path()
        self.presets_dir: Path = self.storage_path / "presets"
        self.presets_dir.mkdir(parents=True, exist_ok=True)
        # 委托基类处理通用的 load/save/delete/list_ids
        super().__init__(
            directory=self.presets_dir,
            model_class=WritingPreset,
            model_dump_kwargs={
                "mode": "json",
                "by_alias": True,
                "exclude_none": False,
            },
        )

    # ===== 默认预设 =====

    def load_default_preset(self) -> WritingPreset:
        """加载内置默认预设。

        Returns:
            默认 WritingPreset 对象
        """
        default_path = get_default_preset_path()
        try:
            data = json.loads(default_path.read_text(encoding="utf-8"))
            return WritingPreset.model_validate(data)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error("加载默认预设失败 %s: %s", default_path, e)
            # 返回内存中的最小默认预设
            return self._build_minimal_default_preset()

    @staticmethod
    def _build_minimal_default_preset() -> WritingPreset:
        """构建内存中的最小默认预设（资源文件加载失败时使用）。"""
        return WritingPreset(
            id="default",
            name="默认写作预设",
            prompts=[
                Prompt(
                    identifier="main",
                    name="主提示",
                    role="system",
                    content="你是一位专业的小说续写助手。请根据提供的小说档案、前文章节和上下文信息，续写小说内容。",
                    system_prompt=True,
                ),
                Prompt(
                    identifier="worldInfoBefore",
                    name="世界书（前）",
                    role="system",
                    marker="worldInfoBefore",
                ),
                Prompt(
                    identifier="chatHistory",
                    name="章节历史",
                    role="system",
                    marker="chatHistory",
                ),
                Prompt(
                    identifier="worldInfoAfter",
                    name="世界书（后）",
                    role="system",
                    marker="worldInfoAfter",
                    position="end",
                ),
            ],
            prompt_order=[
                PromptOrderGroup(
                    character_id=GLOBAL_CHARACTER_ID,
                    order=[
                        PromptOrderEntry(identifier="main", enabled=True),
                        PromptOrderEntry(identifier="worldInfoBefore", enabled=True),
                        PromptOrderEntry(identifier="chatHistory", enabled=True),
                        PromptOrderEntry(identifier="worldInfoAfter", enabled=True),
                    ],
                )
            ],
            generation_params={
                "temperature": 0.8,
                "max_tokens": 2000,
                "top_p": 0.95,
                "frequency_penalty": 0.0,
                "presence_penalty": 0.0,
                "max_context": 32000,
            },
        )

    def ensure_default_preset_exists(self) -> WritingPreset:
        """确保默认预设存在于文件系统（不存在则写入）。

        Returns:
            默认 WritingPreset 对象
        """
        default_path = get_preset_file_path(self.storage_path, "default")
        if not default_path.exists():
            preset = self.load_default_preset()
            self.save_preset(preset)
            logger.info("已写入默认预设到 %s", default_path)
            return preset
        return self.load_preset("default") or self.load_default_preset()

    # ===== 预设 CRUD =====

    def load_preset(self, preset_id: str) -> WritingPreset | None:
        """加载指定 ID 的预设。

        Args:
            preset_id: 预设 ID

        Returns:
            WritingPreset 对象，不存在时返回 None
        """
        preset_path = get_preset_file_path(self.storage_path, preset_id)
        return self.load(preset_path)

    def save_preset(self, preset: WritingPreset) -> None:
        """保存预设到文件系统（写入前 .bak 备份）。

        Args:
            preset: 待保存的预设
        """
        preset_path = get_preset_file_path(self.storage_path, preset.id)
        self.save(preset_path, preset)
        logger.info("已保存预设: %s (%s)", preset.id, preset.name)

    def delete_preset(self, preset_id: str) -> bool:
        """删除预设。

        Args:
            preset_id: 预设 ID

        Returns:
            是否删除成功
        """
        if preset_id == "default":
            logger.warning("不允许删除默认预设")
            return False
        preset_path = get_preset_file_path(self.storage_path, preset_id)
        if not self.delete(preset_path):
            return False
        logger.info("已删除预设: %s", preset_id)
        return True

    def list_presets(self) -> list[WritingPreset]:
        """列出所有预设。

        Returns:
            WritingPreset 列表（默认预设排在首位）
        """
        presets: list[WritingPreset] = []
        # 确保默认预设存在
        self.ensure_default_preset_exists()

        for preset_id in self.list_ids():
            preset = self.load_preset(preset_id)
            if preset is not None:
                presets.append(preset)

        # 默认预设排在首位
        presets.sort(key=lambda p: (p.id != "default", p.name))
        return presets

    def list_preset_ids(self) -> list[str]:
        """列出所有预设 ID。"""
        return [p.id for p in self.list_presets()]

    def create_preset(self, name: str) -> WritingPreset:
        """创建新预设（基于默认预设的 marker 结构）。

        Args:
            name: 预设名称

        Returns:
            新建的 WritingPreset 对象
        """
        preset_id = _generate_preset_id()
        # 复制默认预设结构
        default = self.load_default_preset()
        new_prompts = [
            Prompt(
                identifier=p.identifier,
                name=p.name,
                role=p.role,
                content=p.content,
                system_prompt=p.system_prompt,
                marker=p.marker,
                position=p.position,
                injection_position=p.injection_position,
                injection_depth=p.injection_depth,
                injection_order=p.injection_order,
                forbid_overrides=p.forbid_overrides,
                extension=dict(p.extension),
                enabled=p.enabled,
            )
            for p in default.prompts
        ]
        new_preset = WritingPreset(
            id=preset_id,
            name=name,
            prompts=new_prompts,
            prompt_order=[
                PromptOrderGroup(
                    character_id=GLOBAL_CHARACTER_ID,
                    order=[
                        PromptOrderEntry(identifier=p.identifier, enabled=True)
                        for p in new_prompts
                    ],
                )
            ],
            generation_params=dict(default.generation_params),
        )
        self.save_preset(new_preset)
        return new_preset

    # ===== ST 预设导入导出 =====

    def import_from_st_json(
        self, file_path: str | Path
    ) -> tuple[WritingPreset, list[dict[str, Any]]]:
        """从 ST 预设 JSON 文件导入。

        解析规则：
        - 提取 prompts、prompt_order、生成参数
        - prompt_order 优先取 character_id == 100000，无则取第一个分组
        - 映射 openai_max_context/openai_max_tokens 到 max_context/max_tokens
        - 解析 extensions.regex_scripts 和 extensions.SPreset.RegexBinding.regexes
        - 未识别字段保留在 _raw_st_fields，导出时原样写回
        - position 字段对齐 ST（start/end），原样保留

        Args:
            file_path: ST 预设 JSON 文件路径

        Returns:
            (WritingPreset, 原始正则脚本字典列表) 元组。
            正则脚本字典列表供调用方通过 RegexService 导入。

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: JSON 解析或校验失败
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"预设文件不存在: {file_path}")

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}") from e

        if not isinstance(data, dict):
            raise ValueError("预设 JSON 顶层必须是对象")

        # 解析 prompts
        prompts_data = data.get("prompts", []) or []
        prompts: list[Prompt] = []
        for p_data in prompts_data:
            if not isinstance(p_data, dict):
                continue
            try:
                prompts.append(_parse_prompt_from_st(p_data))
            except (ValueError, TypeError) as e:
                logger.warning("解析 Prompt 失败，跳过: %s", e)

        # 解析 prompt_order
        prompt_order_data = data.get("prompt_order", []) or []
        prompt_order = _parse_preset_order_from_st(prompt_order_data)

        # 解析生成参数（保留所有非已知字段）
        generation_params: dict[str, Any] = {}
        for key in (
            "temperature", "max_tokens", "top_p", "frequency_penalty",
            "presence_penalty", "max_context", "seed", "top_k",
        ):
            if key in data:
                generation_params[key] = data[key]

        # 映射非标准字段名（openai_max_context/openai_max_tokens）
        if "openai_max_context" in data and "max_context" not in generation_params:
            generation_params["max_context"] = data["openai_max_context"]
        if "openai_max_tokens" in data and "max_tokens" not in generation_params:
            generation_params["max_tokens"] = data["openai_max_tokens"]

        # 解析 extensions 中的正则脚本
        regex_scripts_data = _extract_regex_scripts_from_extensions(data)

        # 收集顶层未识别字段
        raw_st_fields: dict[str, Any] = {}
        for key, value in data.items():
            if key not in _KNOWN_PRESET_FIELDS and key not in generation_params:
                raw_st_fields[key] = value

        # 生成预设 ID（使用文件名 stem 或随机 ID）
        preset_id = _generate_preset_id()
        preset_name = data.get("name", file_path.stem)

        preset = WritingPreset(
            id=preset_id,
            name=preset_name,
            prompts=prompts,
            prompt_order=prompt_order,
            generation_params=generation_params,
            raw_st_fields=raw_st_fields,
        )

        self.save_preset(preset)
        logger.info(
            "导入 ST 预设成功: %s（%d 个提示，%d 条正则脚本）",
            preset.name, len(prompts), len(regex_scripts_data),
        )
        return preset, regex_scripts_data

    def export_to_st_json(
        self, preset: WritingPreset, file_path: str | Path
    ) -> None:
        """导出预设为 ST 兼容 JSON 文件。

        未识别字段（``_raw_st_fields``）原样写回。

        Args:
            preset: 待导出的预设
            file_path: 目标文件路径
        """
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # 构建导出数据
        data: dict[str, Any] = {
            "id": preset.id,
            "name": preset.name,
            "prompts": [self._prompt_to_st_dict(p) for p in preset.prompts],
            "prompt_order": [
                {
                    "character_id": g.character_id,
                    "order": [
                        {"identifier": e.identifier, "enabled": e.enabled}
                        for e in g.order
                    ],
                }
                for g in preset.prompt_order
            ],
        }

        # 生成参数
        for key, value in preset.generation_params.items():
            data[key] = value

        # 写回未识别字段
        for key, value in preset.raw_st_fields.items():
            data[key] = value

        content = json.dumps(data, ensure_ascii=False, indent=2)
        atomic_write_file(file_path, content)
        logger.info("导出预设到 %s", file_path)

    @staticmethod
    def _prompt_to_st_dict(prompt: Prompt) -> dict[str, Any]:
        """将 Prompt 转换为 ST 兼容字典（含未识别字段）。"""
        data: dict[str, Any] = {
            "identifier": prompt.identifier,
            "name": prompt.name,
            "role": prompt.role,
            "content": prompt.content,
            "system_prompt": prompt.system_prompt,
            "marker": prompt.marker,
            "position": prompt.position,
            "injection_position": prompt.injection_position,
            "injection_depth": prompt.injection_depth,
            "injection_order": prompt.injection_order,
            "forbid_overrides": prompt.forbid_overrides,
            "extension": prompt.extension,
            "enabled": prompt.enabled,
        }
        # 写回未识别字段
        for key, value in prompt.raw_st_fields.items():
            data[key] = value
        return data

    # ===== 预设编辑辅助 =====

    def add_prompt_to_preset(
        self,
        preset: WritingPreset,
        name: str,
        content: str = "",
        role: str = "system",
        position: str = "start",
        injection_position: int = 0,
        injection_depth: int = 4,
        injection_order: int = 100,
    ) -> Prompt:
        """向预设添加新提示（自动生成 identifier 并追加到 prompt_order）。

        Args:
            preset: 目标预设（会被原地修改）
            name: 提示名称
            content: 提示内容
            role: 角色
            position: 相对位置（start/end）
            injection_position: 0=RELATIVE / 1=ABSOLUTE
            injection_depth: 注入深度（ABSOLUTE 时有效）
            injection_order: 注入顺序权重

        Returns:
            新创建的 Prompt 对象
        """
        # 生成唯一 identifier
        existing_ids = {p.identifier for p in preset.prompts}
        base_id = "custom"
        identifier = base_id
        suffix = 1
        while identifier in existing_ids:
            identifier = f"{base_id}_{suffix}"
            suffix += 1

        prompt = Prompt(
            identifier=identifier,
            name=name,
            role=role,
            content=content,
            position=position,
            injection_position=injection_position,
            injection_depth=injection_depth,
            injection_order=injection_order,
        )
        preset.prompts.append(prompt)

        # 同步追加到 prompt_order 的全局分组
        if preset.prompt_order:
            preset.prompt_order[0].order.append(
                PromptOrderEntry(identifier=identifier, enabled=True)
            )
        else:
            preset.prompt_order.append(
                PromptOrderGroup(
                    character_id=GLOBAL_CHARACTER_ID,
                    order=[PromptOrderEntry(identifier=identifier, enabled=True)],
                )
            )

        return prompt

    def remove_prompt_from_preset(
        self, preset: WritingPreset, identifier: str
    ) -> bool:
        """从预设移除提示（marker 与 system_prompt 提示不允许移除）。

        Args:
            preset: 目标预设（会被原地修改）
            identifier: 提示 identifier

        Returns:
            是否移除成功
        """
        target: Prompt | None = None
        for p in preset.prompts:
            if p.identifier == identifier:
                target = p
                break

        if target is None:
            return False

        if target.marker:
            logger.warning("marker 提示 %s 不允许删除", identifier)
            return False
        if target.system_prompt:
            logger.warning("system_prompt 提示 %s 不允许删除", identifier)
            return False

        preset.prompts = [
            p for p in preset.prompts if p.identifier != identifier
        ]
        for group in preset.prompt_order:
            group.order = [
                e for e in group.order if e.identifier != identifier
            ]
        return True

    def reorder_prompts(
        self, preset: WritingPreset, new_order: list[tuple[str, bool]]
    ) -> None:
        """重新排序预设的提示（更新全局 prompt_order）。

        Args:
            preset: 目标预设（会被原地修改）
            new_order: 新顺序列表，每项为 (identifier, enabled)
        """
        if not preset.prompt_order:
            preset.prompt_order.append(
                PromptOrderGroup(character_id=GLOBAL_CHARACTER_ID, order=[])
            )
        preset.prompt_order[0].order = [
            PromptOrderEntry(identifier=identifier, enabled=enabled)
            for identifier, enabled in new_order
        ]

    def set_prompt_enabled(
        self, preset: WritingPreset, identifier: str, enabled: bool
    ) -> None:
        """切换提示启用状态（同步 prompts 和 prompt_order）。

        Args:
            preset: 目标预设（会被原地修改）
            identifier: 提示 identifier
            enabled: 是否启用
        """
        for p in preset.prompts:
            if p.identifier == identifier:
                p.enabled = enabled
                break
        for group in preset.prompt_order:
            for entry in group.order:
                if entry.identifier == identifier:
                    entry.enabled = enabled

    def set_preset_enabled(self, preset_id: str, enabled: bool) -> bool:
        """切换预设启用状态。

        禁用的预设不会出现在续写面板的预设下拉列表中。

        Args:
            preset_id: 预设 ID
            enabled: 是否启用

        Returns:
            是否成功（预设不存在或为默认预设时返回 False）
        """
        preset = self.load_preset(preset_id)
        if preset is None:
            return False
        if preset.id == "default":
            logger.warning("默认预设不允许禁用")
            return False
        preset.enabled = enabled
        self.save_preset(preset)
        return True
