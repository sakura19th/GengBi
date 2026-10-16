"""无界面的文本续写：复用桌面端的拆章、预设、提示词与后处理。"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from novelforge.core.config import ConfigManager
from novelforge.core.post_processor import post_process_content
from novelforge.core.prompt_assembler import AssembleResult, PromptAssembler
from novelforge.core.regex_engine import RegexEngine
from novelforge.core.template_engine import TemplateEngine
from novelforge.core.variable_store import VariableStore
from novelforge.models import WritingPreset
from novelforge.models.preset import GLOBAL_CHARACTER_ID
from novelforge.models.regex import RegexScript
from novelforge.services.importer import DEFAULT_CHAPTER_PATTERN, TxtImporter
from novelforge.services.llm_client import LLMClient
from novelforge.services.preset_service import PresetService
from novelforge.utils.ids import validate_id
from novelforge.utils.paths import (
    get_default_preset_path,
    get_default_regex_scripts_path,
)


def read_json(path: Path) -> Any:
    """读取 UTF-8 JSON，兼容 Windows 编辑器写入的 BOM。"""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_config(path: Path | None = None) -> ConfigManager:
    """读取桌面配置，不创建配置、不迁移或回写用户数据。"""
    manager = ConfigManager(path)
    if path is not None or manager.config_path.exists():
        data = read_json(manager.config_path)
        if not isinstance(data, dict):
            raise ValueError("配置 JSON 顶层必须是对象")
        for key in ("continuation", "network", "data", "flow_endpoints", "flow_models"):
            if key in data and not isinstance(data[key], dict):
                raise ValueError(f"配置项 {key} 必须是对象")
        endpoints = data.get("api_endpoints", [])
        if not isinstance(endpoints, list) or any(
            not isinstance(endpoint, dict) for endpoint in endpoints
        ):
            raise ValueError("配置项 api_endpoints 必须是端点对象列表")
        from novelforge.services.model_prompts import validate_model_prompts
        for endpoint in endpoints:
            validate_model_prompts(endpoint.get("model_prompts", {}))
        manager.config = manager._merge_defaults(data)
    return manager


@dataclass(frozen=True)
class Connection:
    """一次调用使用的连接信息；密钥不参与对象的文本表示。"""

    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    reasoning_effort: str = ""
    proxy: str | None = None
    merge_system_messages: bool | str = False
    extra_payload: dict[str, Any] = field(default_factory=dict, repr=False)
    extra_headers: dict[str, str] = field(default_factory=dict, repr=False)
    timeout: float = 300.0
    model_prompts: dict[str, str] = field(default_factory=dict, repr=False)

    def prompt_summary(self) -> dict:
        from novelforge.services.model_prompts import prompt_summary
        return prompt_summary(self.model_prompts, self.extra_payload.get("model", self.model))

    def client(self) -> LLMClient:
        """创建与桌面端行为一致的 API 客户端。"""
        from novelforge.cli_diagnostics import register_secret
        register_secret(self.api_key)
        for value in self.extra_headers.values():
            register_secret(value)
        return LLMClient(
            base_url=self.base_url,
            api_key=self.api_key,
            reasoning_effort=self.reasoning_effort,
            proxy=self.proxy,
            merge_system_messages=self.merge_system_messages,
            extra_payload=self.extra_payload,
            extra_headers=self.extra_headers,
            model_prompts=self.model_prompts,
            timeout=self.timeout,
        )


def resolve_connection(
    config: ConfigManager,
    *,
    endpoint_id: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    reasoning_effort: str | None = None,
    proxy: str | None = None,
    timeout: float = 300.0,
    decrypt: bool = True,
    flow_key: str = "single_continuation",
) -> Connection:
    """解析一次性覆盖；自定义 URL 不继承保存端点的密钥或 HTTP 头。"""
    if endpoint_id is not None and base_url is not None:
        raise ValueError("不能同时指定端点 ID 和自定义 API 地址")
    if endpoint_id is not None:
        endpoint = config.get_endpoint(endpoint_id)
        if endpoint is None:
            raise ValueError(f"API 端点不存在: {endpoint_id}")
    elif base_url is not None:
        endpoint = {}
    else:
        endpoint = config.get_flow_endpoint(flow_key) or {}
    resolved_model = model
    if resolved_model is None:
        resolved_model = (
            config.get_flow_model(flow_key)
            if endpoint_id is None and base_url is None
            else endpoint.get("default_model", "")
        )
    if api_key is None:
        api_key = ""
        if decrypt and endpoint.get("api_key_encrypted"):
            api_key = config.decrypt_api_key(endpoint["id"])
            if not api_key:
                raise ValueError("无法解密端点密钥，请在本机重新配置或通过环境变量提供")
    network = config.get_network_settings()
    if proxy is None:
        proxy = network.get("http_proxy", "") if network.get("proxy_enabled") else ""
    resolved_url = base_url if base_url is not None else endpoint.get("base_url", "")
    if not isinstance(resolved_url, str) or not isinstance(resolved_model, str):
        raise TypeError("API 地址和模型名称必须是字符串")
    return Connection(
        base_url=resolved_url,
        model=resolved_model or "",
        api_key=api_key,
        reasoning_effort=(
            reasoning_effort
            if reasoning_effort is not None
            else endpoint.get("reasoning_effort", "")
        ),
        proxy=proxy or None,
        timeout=timeout,
        merge_system_messages=endpoint.get("merge_system_messages", False),
        extra_payload=endpoint.get("extra_payload") or {},
        extra_headers=endpoint.get("extra_headers") or {},
        model_prompts=endpoint.get("model_prompts", {}),
    )


@dataclass(frozen=True)
class ContinuationOptions:
    """文本输入与一次续写的可选覆盖；None 表示使用已保存的默认值。"""

    title: str = "全文"
    instruction: str = ""
    preset_id: str = "default"
    preset_file: Path | None = None
    jailbreak: str | None = None
    temperature: float | None = None
    target_words: int | None = None
    lookback: int | None = None
    max_tokens: int | None = None
    max_context: int | None = None
    apply_regex: bool = True
    worldbook_ids: tuple[str, ...] | None = None
    exclude_current: bool = False


@dataclass
class WritingContext:
    """完整项目的组装输入；保留章节实体和已加载的资料。"""

    project: Any
    chapters: list[Any]
    current_chapter: Any
    entries: list[Any] = field(default_factory=list)
    lookback: dict[str, Any] = field(default_factory=dict)


def _override_jailbreak(preset: WritingPreset, level: str | None) -> None:
    """仅调整本次加载的预设模块，同时覆盖组装器优先读取的排序开关。"""
    if level is None:
        return
    levels = {"low", "mid", "high", "cot"}
    if level not in levels | {"off"}:
        raise ValueError("破限等级必须为 off、low、mid、high 或 cot")
    selected = f"nf_jb_{level}"
    group = next(
        (g for g in preset.prompt_order if g.character_id == GLOBAL_CHARACTER_ID),
        None,
    )
    if level != "off" and (
        not any(p.identifier == selected for p in preset.prompts)
        or group is None
        or not any(e.identifier == selected for e in group.order)
    ):
        raise ValueError(
            f"预设缺少破限模块 {selected} 或其全局排序项；请使用包含该模块的预设"
        )
    controlled = {f"nf_jb_{item}" for item in levels}
    if level == "off":
        controlled.add("nf_jb_prefill")
    for prompt in preset.prompts:
        if prompt.identifier in controlled:
            prompt.enabled = prompt.identifier == selected
    if group is not None:
        for entry in group.order:
            if entry.identifier in controlled:
                entry.enabled = entry.identifier == selected


def _number(
    value: Any, label: str, minimum: float, maximum: float, *, integer: bool = False
) -> Any:
    """验证包含配置文件值在内的数值，拒绝 NaN 和隐式类型转换。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
        or (integer and not isinstance(value, int))
    ):
        raise ValueError(
            f"{label} 必须是 {minimum:g} 到 {maximum:g} 之间的{'整数' if integer else '数字'}"
        )
    return value


@dataclass
class PreparedContinuation:
    """已组装请求，在 prepare_continuation 上下文中使用。"""

    assembled: AssembleResult
    parameters: dict[str, Any]
    regex_engine: RegexEngine
    template_engine: TemplateEngine
    metadata: dict[str, Any]
    apply_regex: bool
    preset: WritingPreset
    project_id: str = "cli"
    raw_content: str = ""
    context_entries: list[Any] = field(default_factory=list)

    async def generate(self, connection: Connection, *, stream: bool = True) -> str:
        """完整接收并处理正文；中断或不完整响应不作为成功结果返回。"""
        from novelforge.cli_diagnostics import (
            phase,
            register_secret,
            require_model_text,
        )

        register_secret(connection.api_key)
        phase("模型生成", model=connection.model)
        client = connection.client()
        try:
            content, finish = await client.complete_text(
                messages=self.assembled.messages,
                model=connection.model,
                stream=stream,
                **self.parameters,
            )
        finally:
            from novelforge.cli_diagnostics import preserve_failure
            with preserve_failure():
                await client.close()
        require_model_text(content, finish)
        self.raw_content = content
        result = post_process_content(
            content,
            regex_engine=self.regex_engine,
            template_engine=self.template_engine,
            project_id=self.project_id,
            chapter_metadata=self.metadata,
            apply_regex=self.apply_regex,
        ).strip()
        require_model_text(result, finish, "后处理正文")
        return result


@contextmanager
def prepare_continuation(
    text: str,
    config: ConfigManager,
    options: ContinuationOptions,
    *,
    model: str = "",
    context: WritingContext | None = None,
) -> Iterator[PreparedContinuation]:
    """在独立临时目录渲染预设，不修改桌面项目、预设或全局变量。"""
    if not text.strip():
        raise ValueError("输入文本不能为空")
    storage = config.get_storage_path()
    with TemporaryDirectory(prefix="gengbi-cli-") as temporary:
        scratch = Path(temporary)
        embedded_scripts: list[dict[str, Any]] = []
        if options.preset_file is not None:
            data = read_json(options.preset_file)
            if isinstance(data, dict) and "generation_params" in data:
                preset = WritingPreset.model_validate(data)
            else:
                preset, embedded_scripts = PresetService(scratch).import_from_st_json(
                    options.preset_file
                )
        else:
            validate_id(options.preset_id)
            path = storage / "presets" / f"{options.preset_id}.json"
            if options.preset_id == "default" and not path.exists():
                path = get_default_preset_path()
            preset = WritingPreset.model_validate(read_json(path))
        if not preset.prompts:
            raise ValueError("预设必须包含提示词")
        _override_jailbreak(preset, options.jailbreak)
        defaults = config.get_continuation_settings()
        generation = preset.generation_params
        temperature = _number(
            options.temperature
            if options.temperature is not None
            else defaults.get("default_temperature", 0.8),
            "温度",
            0,
            2,
        )
        target = _number(
            options.target_words
            if options.target_words is not None
            else defaults.get("default_target_words", 2000),
            "目标字数",
            100,
            50000,
            integer=True,
        )
        lookback = _number(
            options.lookback
            if options.lookback is not None
            else defaults.get("default_lookback_chapters", 5),
            "回溯章节数",
            0,
            99999,
            integer=True,
        )
        max_tokens = _number(
            options.max_tokens
            if options.max_tokens is not None
            else generation.get("max_tokens", 2000),
            "输出 Token 上限",
            1,
            10000000,
            integer=True,
        )
        max_context = _number(
            options.max_context
            if options.max_context is not None
            else generation.get("max_context", 32000),
            "上下文 Token 上限",
            1,
            10000000,
            integer=True,
        )
        if max_context <= max_tokens:
            raise ValueError("上下文 Token 上限必须大于输出 Token 上限")
        parameters = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": _number(generation.get("top_p", 1.0), "top_p", 0, 1),
            "frequency_penalty": _number(
                generation.get("frequency_penalty", 0.0), "frequency_penalty", -2, 2
            ),
            "presence_penalty": _number(
                generation.get("presence_penalty", 0.0), "presence_penalty", -2, 2
            ),
        }
        regex_engine = RegexEngine()
        global_regex = storage / "regex" / "global.json"
        scripts = read_json(
            global_regex if global_regex.exists() else get_default_regex_scripts_path()
        )
        if options.preset_file is None:
            preset_regex = storage / "regex" / f"preset_{options.preset_id}.json"
            if preset_regex.exists():
                scripts.extend(read_json(preset_regex))
        if context is not None:
            project_regex = storage / "projects" / context.project.id / "regex.json"
            if project_regex.exists():
                scripts.extend(read_json(project_regex))
        regex_engine.compile_scripts(
            [RegexScript.model_validate(item) for item in [*scripts, *embedded_scripts]]
        )
        chapters = (
            context.chapters
            if context is not None
            else TxtImporter.split_text(
                text,
                "cli",
                DEFAULT_CHAPTER_PATTERN,
                False,
                options.title,
            )
        )
        current = context.current_chapter if context is not None else chapters[-1]
        metadata = current.metadata
        project_id = context.project.id if context is not None else "cli"
        book_ids = options.worldbook_ids
        if book_ids is None:
            book_ids = tuple(config.get_selected_worldbook_ids())
        entries: list[Any] = []
        seen: set[str] = set()
        if book_ids:
            from novelforge.services.worldbook_service import WorldBookService

            books = WorldBookService(storage)
            for identifier in book_ids:
                book = books.load_worldbook(identifier)
                if book is None:
                    raise ValueError(
                        f"世界书不存在：{identifier}；请运行 worldbooks list 查询"
                    )
                if book.enabled:
                    for entry in book.entries:
                        if entry.enabled and entry.uid not in seen:
                            entries.append(entry)
                            seen.add(entry.uid)
        if context is not None:
            entries.extend(
                e for e in context.entries if e.enabled and e.uid not in seen
            )
        extra: dict[str, Any] = {}
        if context is not None:
            extra = {
                "world_ontology": context.project.world_ontology,
                "style_profile": context.project.style_profile,
                "custom_audit_rules": context.project.custom_audit_rules,
                "protagonist_profile": current.protagonist_profile,
                "custom_characters": current.custom_characters,
                **context.lookback,
            }
        template_engine = TemplateEngine(VariableStore(scratch))
        try:
            assembled = PromptAssembler(
                regex_engine=regex_engine,
                template_engine=template_engine,
            ).assemble(
                preset=preset,
                chapters=chapters,
                current_chapter=current,
                context_entries=entries,
                model=model,
                max_context=max_context,
                max_tokens=max_tokens,
                target_words=target,
                lookback_chapters=lookback,
                novel_profile=context.project.novel_profile
                if context
                else {"title": options.title},
                project_id=project_id,
                chapter_metadata=metadata,
                user_input=options.instruction,
                exclude_current=options.exclude_current,
                **extra,
            )
            yield PreparedContinuation(
                assembled,
                parameters,
                regex_engine,
                template_engine,
                metadata,
                options.apply_regex,
                preset,
                project_id,
                context_entries=entries,
            )
        finally:
            from novelforge.cli_diagnostics import preserve_failure
            with preserve_failure():
                template_engine.shutdown()
