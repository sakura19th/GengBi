"""上下文提取器。

实现 M4 自动上下文提取：
- 取前 N 章正文（默认 5，可被 ``project.extract_config.lookback_chapters`` 覆盖）
- 章节数不足 N 时取所有可用章节，0 章时跳过提取
- 构造提取提示词（优先用 ``extractor_prompt_override``，为 null 时用默认模板）
- 用宏替换填充模板（``{{title}}``/``{{author}}``/``{{protagonist}}``/
  ``{{synopsis}}``/``{{world_setting}}``/``{{writing_style}}``/``{{chapters_text}}``）
- 调用提取模型（非流式，默认 ``gpt-4o-mini``）
- 解析 JSON → ``list[ContextEntry]``，失败时尝试修复（去除 markdown 代码块标记）
- 校验必填字段（``uid``、``category``、``content``），``content`` 长度截断 200 字
- 实现分段缓存：``key = "ctx_extract:{project_id}:{chapters_hash}"``
- 缓存有效期默认 24 小时（可被 ``cache_ttl_hours`` 覆盖）
- ``force_refresh=True`` 时跳过缓存
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from novelforge.core.config import ConfigManager
from novelforge.core.json_utils import strip_markdown_fences
from novelforge.core.token_counter import TokenCounter
from novelforge.models import Chapter, ContextEntry, Project, ProtagonistProfile
from novelforge.models.context import VALID_CATEGORIES, VALID_POSITIONS, VALID_ROLES
from novelforge.services.llm_client import (
    APIError,
    AuthError,
    LLMClient,
    LLMError,
    RateLimitError,
)
from novelforge.services.storage_service import StorageService, _generate_id
from novelforge.utils.paths import (
    get_extract_merge_prompt_path,
    get_extract_protagonist_merge_prompt_path,
    get_extract_protagonist_prompt_path,
    get_extract_prompt_path,
)

logger = logging.getLogger(__name__)

# 向后兼容别名：旧测试通过 _strip_markdown_fences 导入
_strip_markdown_fences = strip_markdown_fences

# 默认回溯章节数
DEFAULT_LOOKBACK_CHAPTERS = 5

# 默认提取模型
DEFAULT_EXTRACTOR_MODEL = "gpt-4o-mini"

# 默认缓存有效期（小时）
DEFAULT_CACHE_TTL_HOURS = 24

# 提取请求的 max_tokens（足够返回完整 JSON 数组）
EXTRACT_MAX_TOKENS = 5000

# 提取温度（低温保证稳定输出）
EXTRACT_TEMPERATURE = 0.2

# content 字段最大长度
MAX_CONTENT_LENGTH = 500

# 缓存 category
CACHE_CATEGORY = "context_extract"

# 缓存 key 前缀
CACHE_KEY_PREFIX = "ctx_extract"

# ===== 主角形象提取常量（镜像 OntologyExtractor 模式）=====

# 主角形象提取请求的 max_tokens（足够返回 8 维度 JSON）
PROTAGONIST_EXTRACT_MAX_TOKENS = 6000

# 主角形象提取温度（低温保证稳定输出）
PROTAGONIST_EXTRACT_TEMPERATURE = 0.2

# 主角形象合并温度（首次尝试）
PROTAGONIST_MERGE_TEMPERATURE = 0.2

# 主角形象合并温度（重试，温度归零确保稳定）
PROTAGONIST_MERGE_TEMPERATURE_RETRY = 0.0

# ProtagonistProfile 8 大维度字段名
PROTAGONIST_DIMENSIONS: tuple[str, ...] = (
    "basic_anchors",
    "personality_system",
    "motivation_system",
    "emotion_defense",
    "behavior_fingerprint",
    "relationship_coordinate",
    "growth_arc",
    "ooc_redlines",
)


@dataclass
class ExtractResult:
    """上下文提取结果。

    Attributes:
        entries: 提取的 ContextEntry 列表
        status: 完成状态（completed/skipped/failed）
        error: 错误信息（status=failed 时非空）
        elapsed_seconds: 提取耗时（秒）
        token_usage: token 消耗信息
        from_cache: 是否命中缓存
        batch_count: 拆分批次数（1=未拆分，>1=按 token 限制拆分）
        merged: 是否经过【信息汇总】环节（仅 batch_count>1 时为 True）
        protagonist_profile: 主角形象心理学档案（跟随章节缓存，仅反映至当前章节状态）
        protagonist_batch_count: 主角形象提取批次数
        protagonist_merged: 主角形象是否经过合并环节
    """

    entries: list[ContextEntry] = field(default_factory=list)
    status: str = "completed"
    error: str = ""
    elapsed_seconds: float = 0.0
    token_usage: dict[str, Any] = field(default_factory=dict)
    from_cache: bool = False
    batch_count: int = 1
    merged: bool = False
    protagonist_profile: ProtagonistProfile | None = None
    protagonist_batch_count: int = 1
    protagonist_merged: bool = False


def _hash_chapter_content(content: str) -> str:
    """计算章节内容的 MD5 哈希。

    Args:
        content: 章节正文

    Returns:
        32 位 hex 哈希字符串
    """
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _compute_chapters_hash(chapters: list[Chapter]) -> str:
    """计算章节列表的组合哈希。

    将各章节内容的 MD5 哈希拼接后再取 MD5，作为缓存 key 的一部分。

    Args:
        chapters: 章节列表

    Returns:
        组合哈希字符串
    """
    if not chapters:
        return "empty"
    parts = [_hash_chapter_content(ch.content or "") for ch in chapters]
    combined = "|".join(parts)
    return hashlib.md5(combined.encode("utf-8")).hexdigest()


def _parse_extract_response(content: str) -> list[dict[str, Any]]:
    """解析 LLM 提取响应为字典列表。

    尝试直接 ``json.loads``，失败时去除 markdown 代码块标记后重试。

    Args:
        content: LLM 返回的文本内容

    Returns:
        解析后的字典列表

    Raises:
        json.JSONDecodeError: 解析失败
    """
    content = content.strip()
    # 第一次尝试：直接解析
    try:
        data = json.loads(content)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # 单个对象包装为列表
            return [data]
        raise json.JSONDecodeError(
            f"期望 JSON 数组，实际类型: {type(data).__name__}", content, 0
        )
    except json.JSONDecodeError:
        pass

    # 第二次尝试：去除 markdown 代码块标记
    cleaned = strip_markdown_fences(content)
    data = json.loads(cleaned)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise json.JSONDecodeError(
        f"期望 JSON 数组，实际类型: {type(data).__name__}", content, 0
    )


def _filter_protagonist_dimensions(data: dict[str, Any]) -> dict[str, Any]:
    """过滤字典，仅保留 ProtagonistProfile 8 大维度字段，非 dict 值替换为空 dict。

    Args:
        data: 原始字典

    Returns:
        仅含 8 大维度字段的字典
    """
    result: dict[str, Any] = {}
    for dim in PROTAGONIST_DIMENSIONS:
        val = data.get(dim)
        if isinstance(val, dict):
            result[dim] = val
        else:
            result[dim] = {}
    return result


def _safe_serialize_dim(value: Any) -> str:
    """安全序列化值为字符串（用于维度合并长度比较）。

    Args:
        value: 任意值

    Returns:
        JSON 字符串，序列化失败时返回 str(value)
    """
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _parse_protagonist_response(content: str) -> dict[str, Any]:
    """解析 LLM 提取响应为 ProtagonistProfile 字典。

    尝试直接 ``json.loads``，失败时去除 markdown 代码块标记后重试。
    仅保留 8 大维度字段，忽略其他字段。

    Args:
        content: LLM 返回的文本内容

    Returns:
        包含 8 大维度字段的字典

    Raises:
        json.JSONDecodeError: 解析失败
    """
    content = content.strip()
    # 第一次尝试：直接解析
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return _filter_protagonist_dimensions(data)
        raise json.JSONDecodeError(
            f"期望 JSON 对象，实际类型: {type(data).__name__}", content, 0
        )
    except json.JSONDecodeError:
        pass

    # 第二次尝试：去除 markdown 代码块标记
    cleaned = strip_markdown_fences(content)
    data = json.loads(cleaned)
    if isinstance(data, dict):
        return _filter_protagonist_dimensions(data)
    raise json.JSONDecodeError(
        f"期望 JSON 对象，实际类型: {type(data).__name__}", content, 0
    )


def _validate_and_normalize_entry(
    raw: dict[str, Any],
    source_chapter_range: tuple[int, int] | None,
    extracted_at: datetime,
) -> ContextEntry | None:
    """校验并规范化单个 ContextEntry 字典。

    校验必填字段（``uid``、``category``、``content``），``content`` 长度截断 200 字。
    非法 ``category``/``position``/``role`` 时使用默认值。

    Args:
        raw: 原始字典
        source_chapter_range: 来源章节区间
        extracted_at: 提取时间

    Returns:
        ContextEntry 对象，校验失败（缺必填字段）返回 None
    """
    if not isinstance(raw, dict):
        return None

    # 必填字段校验
    uid = raw.get("uid") or raw.get("id")
    if not uid:
        return None
    uid = str(uid)

    category = raw.get("category", "characters")
    if category not in VALID_CATEGORIES:
        logger.warning("非法 category %r，使用默认值 characters", category)
        category = "characters"

    content = raw.get("content", "")
    if not content:
        # content 为空也允许，但截断后仍为空
        content = ""
    else:
        content = str(content)
        # 截断 200 字
        if len(content) > MAX_CONTENT_LENGTH:
            content = content[:MAX_CONTENT_LENGTH]

    # 可选字段
    key = raw.get("key", [])
    if not isinstance(key, list):
        key = [str(key)] if key else []
    else:
        key = [str(k) for k in key]

    comment = str(raw.get("comment", ""))

    order = raw.get("order", 100)
    try:
        order = int(order)
    except (TypeError, ValueError):
        order = 100

    position = raw.get("position", "before")
    if position not in VALID_POSITIONS:
        logger.warning("非法 position %r，使用默认值 before", position)
        position = "before"

    depth = raw.get("depth", 4)
    try:
        depth = int(depth)
    except (TypeError, ValueError):
        depth = 4

    role = raw.get("role", "system")
    if role not in VALID_ROLES:
        logger.warning("非法 role %r，使用默认值 system", role)
        role = "system"

    return ContextEntry(
        uid=uid,
        category=category,
        key=key,
        comment=comment,
        content=content,
        order=order,
        position=position,
        depth=depth,
        role=role,
        source_chapter_range=source_chapter_range,
        extracted_at=extracted_at,
    )


class ContextExtractor:
    """上下文提取器。

    根据前文章节调用 LLM 提取关键信息（人物/地点/事件/风格/剧情状态），
    返回 ``list[ContextEntry]`` 供 PromptAssembler 注入。

    Usage::

        extractor = ContextExtractor(storage_service, config_manager)
        result = await extractor.extract(
            project=project,
            chapters=chapters,
            current_chapter=current_chapter,
        )
        if result.status == "completed":
            entries = result.entries
    """

    def __init__(
        self,
        storage_service: StorageService,
        config_manager: ConfigManager,
        token_counter: TokenCounter | None = None,
    ) -> None:
        """初始化上下文提取器。

        Args:
            storage_service: 存储服务（用于缓存读写）
            config_manager: 配置管理器（用于获取提取配置与 API 端点）
            token_counter: token 计数器（用于按 token 拆分），None 时内部创建
        """
        self.storage_service = storage_service
        self.config_manager = config_manager
        self.token_counter = token_counter or TokenCounter()
        # 取消事件（threading.Event 跨线程安全：cancel() 在 Qt UI 线程触发，
        # extract()/extract_streaming() 在 asyncio 事件循环线程中读取；
        # asyncio.Event 非线程安全，且每次 extract() 内重新创建会丢失
        # cancel() 在 extract() 之前/之间设置的取消信号，故改为 __init__ 中一次性创建）
        self._cancel_event = threading.Event()
        # 默认提取提示词模板
        self._default_prompt_template: str | None = None
        # 【信息汇总】提示词模板
        self._merge_prompt_template: str | None = None
        # 主角形象提取提示词模板
        self._protagonist_prompt_template: str | None = None
        # 主角形象合并提示词模板
        self._protagonist_merge_prompt_template: str | None = None
        # token 计数缓存：(chapter_id, content_hash) -> token_count
        # 避免同一章节内容在多次 extract 调用时重复计数（tiktoken 加载开销大）
        self._token_cache: dict[tuple[str, str], int] = {}

    def _get_extract_config(self, project: Project | None) -> dict[str, Any]:
        """获取提取配置（项目级覆盖优先，否则用全局配置）。

        Args:
            project: 项目对象（可为 None）

        Returns:
            合并后的提取配置字典
        """
        # 全局默认配置
        global_config = self.config_manager.get_context_extract_settings()
        config = dict(global_config)

        # 项目级覆盖
        if project and project.extract_config:
            config.update(project.extract_config)

        return config

    def _load_default_prompt_template(self) -> str:
        """加载默认提取提示词模板（带缓存）。

        Returns:
            提示词模板字符串
        """
        if self._default_prompt_template is None:
            path = get_extract_prompt_path()
            try:
                self._default_prompt_template = path.read_text(encoding="utf-8")
            except OSError as e:
                logger.error("加载默认提取提示词模板失败: %s", e)
                # 返回最小可用模板
                self._default_prompt_template = (
                    "请分析以下小说前文，提取关键信息并以 JSON 数组输出。\n\n"
                    "# 小说档案\n标题：{{title}}\n作者：{{author}}\n"
                    "主角：{{protagonist}}\n简介：{{synopsis}}\n"
                    "世界观设定：{{world_setting}}\n写作风格：{{writing_style}}\n\n"
                    "# 前文章节内容\n{{chapters_text}}\n\n"
                    "请输出 JSON 数组，每个元素含 uid/category/key/comment/content/"
                    "order/position/depth/role 字段。"
                )
        return self._default_prompt_template

    def _build_prompt(
        self,
        project: Project | None,
        chapters: list[Chapter],
        config: dict[str, Any],
    ) -> str:
        """构建提取提示词。

        优先使用 ``extractor_prompt_override``，为 null 时用默认模板。
        用宏替换填充 ``{{title}}``/``{{author}}``/``{{protagonist}}``
        /``{{synopsis}}``/``{{world_setting}}``/``{{writing_style}}``/``{{chapters_text}}``。

        每批次独立全量提取（不再附已有条目）；跨批去重由【信息汇总】环节统一处理。

        Args:
            project: 项目对象
            chapters: 待提取的章节列表
            config: 提取配置

        Returns:
            填充后的提示词
        """
        # 选择模板
        override = config.get("extractor_prompt_override")
        if override and isinstance(override, str) and override.strip():
            template = override
        else:
            template = self._load_default_prompt_template()

        # 获取小说档案
        profile = project.novel_profile if project else None
        title = getattr(profile, "title", "") if profile else ""
        author = getattr(profile, "author", "") if profile else ""
        protagonist = getattr(profile, "protagonist", "") if profile else ""
        synopsis = getattr(profile, "synopsis", "") if profile else ""
        world_setting = getattr(profile, "world_setting", "") if profile else ""
        writing_style = getattr(profile, "writing_style", "") if profile else ""

        # 拼接章节文本
        chapters_text_parts: list[str] = []
        for ch in chapters:
            chapters_text_parts.append(f"## {ch.title}\n\n{ch.content}")
        chapters_text = "\n\n".join(chapters_text_parts)

        # 宏替换（简单字符串替换，避免依赖 MacroEngine 的 MacroContext）
        replacements = {
            "{{title}}": title,
            "{{author}}": author,
            "{{protagonist}}": protagonist,
            "{{synopsis}}": synopsis,
            "{{world_setting}}": world_setting,
            "{{writing_style}}": writing_style,
            "{{chapters_text}}": chapters_text,
        }
        prompt = template
        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, value)

        return prompt

    def _load_merge_prompt_template(self) -> str:
        """加载【信息汇总】提示词模板（带缓存）。

        Returns:
            汇总提示词模板字符串
        """
        if self._merge_prompt_template is None:
            path = get_extract_merge_prompt_path()
            try:
                self._merge_prompt_template = path.read_text(encoding="utf-8")
            except OSError as e:
                logger.error("加载汇总提示词模板失败: %s", e)
                # 返回最小可用模板
                self._merge_prompt_template = (
                    "请将以下多批次提取的 ContextEntry 合并去重为最终 JSON 数组。\n\n"
                    "# 待合并的批次提取结果\n{{entries_blocks}}\n\n"
                    "请输出严格 JSON 数组，每个元素含 uid/category/key/comment/content/"
                    "order/position/depth/role 字段。"
                )
        return self._merge_prompt_template

    def _build_entries_blocks(
        self, batch_results: list[list[ContextEntry]]
    ) -> str:
        """构造汇总提示词的 ``{{entries_blocks}}`` 占位符内容。

        每批 JSON 数组前加 ``## 批次 i/N（章节 X-Y）`` 标题，块间空行分隔。

        Args:
            batch_results: 各批次独立提取的 ContextEntry 列表

        Returns:
            拼接后的多批次文本块
        """
        total = len(batch_results)
        parts: list[str] = []
        for idx, entries in enumerate(batch_results):
            condensed = []
            for e in entries:
                condensed.append({
                    "uid": e.uid,
                    "category": e.category,
                    "key": e.key,
                    "comment": e.comment,
                    "content": (e.content or "")[:MAX_CONTENT_LENGTH],
                    "order": e.order,
                    "position": e.position,
                    "depth": e.depth,
                    "role": e.role,
                })
            block_json = json.dumps(condensed, ensure_ascii=False, indent=2)
            parts.append(f"## 批次 {idx + 1}/{total}\n\n```json\n{block_json}\n```")
        return "\n\n".join(parts)

    async def _run_merge_entries(
        self,
        batch_results: list[list[ContextEntry]],
        config: dict[str, Any],
        client: LLMClient,
        model: str,
        stream: bool,
        on_chunk: Callable[[str], None] | None,
        on_batch_complete: Callable[[list, int, int], None] | None,
        batch_count: int,
        total_token_usage: dict[str, Any],
        log_prefix: str = "",
    ) -> list[ContextEntry] | None:
        """【信息汇总】环节：LLM 合并多批次提取结果为最终 ContextEntry 列表。

        构造汇总提示词（``extract_merge_prompt.txt``），调用 LLM（流式/非流式），
        解析响应为 ``list[ContextEntry]``。失败返回 None（由调用方降级处理）。

        Args:
            batch_results: 各批次独立提取的 ContextEntry 列表
            config: 提取配置
            client: LLM 客户端
            model: 模型名
            stream: 是否流式调用
            on_chunk: 流式 chunk 回调
            on_batch_complete: 流式批次完成回调（汇总完成后以最终列表回调一次）
            batch_count: 总批次数
            total_token_usage: 累计 token_usage（非流式模式下累加汇总调用消耗）
            log_prefix: 日志前缀

        Returns:
            合并后的 ContextEntry 列表，失败返回 None
        """
        # 过滤空批次
        non_empty = [es for es in batch_results if es]
        if not non_empty:
            logger.warning("%s信息汇总：所有批次均为空，跳过汇总", log_prefix)
            return []

        template = self._load_merge_prompt_template()
        entries_blocks = self._build_entries_blocks(batch_results)
        prompt = template.replace("{{entries_blocks}}", entries_blocks)
        messages = [{"role": "user", "content": prompt}]

        # 流式模式：插入汇总分隔标记
        if stream and on_chunk is not None:
            separator = "\n\n--- 信息汇总 ---\n\n"
            try:
                on_chunk(separator)
            except Exception as e:
                logger.warning("on_chunk 回调异常: %s", e)

        # 取消信号检查
        if self._cancel_event.is_set():
            return None

        content_parts: list[str] = []
        response: dict[str, Any] = {}
        merge_retried = False

        while True:
            try:
                if stream:
                    async for chunk in client.stream_chat_completion(
                        messages=messages,
                        model=model,
                        temperature=EXTRACT_TEMPERATURE,
                        max_tokens=EXTRACT_MAX_TOKENS,
                        stop_event=self._cancel_event,
                    ):
                        if chunk.content:
                            content_parts.append(chunk.content)
                            if on_chunk is not None:
                                try:
                                    on_chunk(chunk.content)
                                except Exception as e:
                                    logger.warning("on_chunk 回调异常: %s", e)
                        if chunk.finish_reason:
                            break
                else:
                    response = await client.chat_completion(
                        messages=messages,
                        model=model,
                        temperature=EXTRACT_TEMPERATURE,
                        max_tokens=EXTRACT_MAX_TOKENS,
                        stop_event=self._cancel_event,
                    )
                break
            except asyncio.CancelledError:
                logger.info("%s信息汇总被取消", log_prefix)
                return None
            except asyncio.TimeoutError as e:
                if not merge_retried:
                    logger.warning("%s信息汇总超时，重试中...", log_prefix)
                    merge_retried = True
                    if self._cancel_event.is_set():
                        return None
                    if stream:
                        content_parts.clear()
                    continue
                logger.error("%s信息汇总超时重试失败: %s", log_prefix, e)
                return None
            except (AuthError, RateLimitError, APIError, LLMError) as e:
                logger.error("%s信息汇总 LLM 调用失败: %s", log_prefix, e)
                return None
            except Exception as e:
                logger.error("%s信息汇总异常: %s", log_prefix, e, exc_info=True)
                return None

        # 取消信号检查
        if self._cancel_event.is_set():
            return None

        # 提取内容与 token_usage
        if stream:
            content = "".join(content_parts)
        else:
            batch_usage = response.get("usage", {})
            if isinstance(batch_usage, dict):
                for k, v in batch_usage.items():
                    if isinstance(v, (int, float)):
                        total_token_usage[k] = (
                            total_token_usage.get(k, 0) + v
                        )
            choices = response.get("choices", [])
            if not choices:
                logger.error("%s信息汇总响应无 choices", log_prefix)
                return None
            message = choices[0].get("message", {})
            content = message.get("content", "") or ""

        # 解析响应
        try:
            raw_entries = _parse_extract_response(content)
        except json.JSONDecodeError as e:
            logger.error(
                "%s信息汇总 JSON 解析失败: %s, content=%s",
                log_prefix, e, content[:200],
            )
            return None

        # 校验并规范化（汇总条目 source_chapter_range=None，extracted_at=now）
        merged_at = datetime.now()
        merged_entries: list[ContextEntry] = []
        for raw in raw_entries:
            entry = _validate_and_normalize_entry(raw, None, merged_at)
            if entry is not None:
                merged_entries.append(entry)

        logger.info(
            "%s信息汇总完成: %d 条 (输入 %d 批)",
            log_prefix,
            len(merged_entries),
            batch_count,
        )

        # 流式模式：以最终合并列表回调一次（batch_idx=batch_count 标识汇总完成）
        if stream and on_batch_complete is not None:
            try:
                on_batch_complete(list(merged_entries), batch_count, batch_count)
            except Exception as e:
                logger.warning("on_batch_complete 回调异常: %s", e)

        return merged_entries

    # ===== 主角形象提取（镜像 OntologyExtractor 三大机制）=====

    def _load_protagonist_prompt_template(self) -> str:
        """加载主角形象提取提示词模板（带缓存）。

        Returns:
            提示词模板字符串
        """
        if self._protagonist_prompt_template is None:
            path = get_extract_protagonist_prompt_path()
            try:
                self._protagonist_prompt_template = path.read_text(encoding="utf-8")
            except OSError as e:
                logger.error("加载主角形象提取提示词模板失败: %s", e)
                # 回退最小可用模板
                self._protagonist_prompt_template = (
                    "请分析以下小说前文，提取主角的完整心理学档案。\n\n"
                    "# 小说档案\n标题：{{title}}\n作者：{{author}}\n"
                    "主角：{{protagonist}}\n简介：{{synopsis}}\n"
                    "世界观设定：{{world_setting}}\n写作风格：{{writing_style}}\n\n"
                    "# 前序批次累积的主角形象认知（增量上下文）\n"
                    "{{accumulated_protagonist}}\n\n"
                    "# 本批次章节内容\n{{chapters_text}}\n\n"
                    "请输出严格 JSON 对象，含 8 大维度字段："
                    "basic_anchors/personality_system/motivation_system/"
                    "emotion_defense/behavior_fingerprint/relationship_coordinate/"
                    "growth_arc/ooc_redlines。只输出 JSON，不要 markdown 代码块。"
                )
        return self._protagonist_prompt_template

    def _load_protagonist_merge_prompt_template(self) -> str:
        """加载主角形象合并提示词模板（带缓存）。

        Returns:
            合并提示词模板字符串
        """
        if self._protagonist_merge_prompt_template is None:
            path = get_extract_protagonist_merge_prompt_path()
            try:
                self._protagonist_merge_prompt_template = path.read_text(
                    encoding="utf-8"
                )
            except OSError as e:
                logger.error("加载主角形象合并提示词模板失败: %s", e)
                # 回退最小可用模板
                self._protagonist_merge_prompt_template = (
                    "请将以下多批次提取的 ProtagonistProfile 合并为统一的 JSON 对象。\n\n"
                    "# 待合并的多批次提取结果\n{{entries_blocks}}\n\n"
                    "合并任务：消除跨块重复、冲突消解（弧光以最新为准）、"
                    "补全与连贯、OOC红线完整性。"
                    "输出严格 JSON 对象，含 8 大维度字段。"
                    "只输出 JSON，不要 markdown 代码块。"
                )
        return self._protagonist_merge_prompt_template

    def _build_protagonist_prompt(
        self,
        project: Project | None,
        chapters_text: str,
        accumulated_protagonist: dict | None = None,
    ) -> str:
        """构建主角形象提取提示词。

        用宏替换填充 ``{{title}}``/``{{author}}``/``{{protagonist}}``
        /``{{synopsis}}``/``{{world_setting}}``/``{{writing_style}}``
        /``{{accumulated_protagonist}}``/``{{chapters_text}}``。

        首批提取（accumulated_protagonist=None 或空 dict）注入"（首批提取，无前序参考）"，
        后续批次注入累积 ProtagonistProfile JSON。

        Args:
            project: 项目对象
            chapters_text: 本批次章节文本
            accumulated_protagonist: 前序批次累积的 ProtagonistProfile 字典（None 表示首批）

        Returns:
            填充后的提示词
        """
        template = self._load_protagonist_prompt_template()

        # 获取小说档案
        profile = project.novel_profile if project else None
        title = getattr(profile, "title", "") if profile else ""
        author = getattr(profile, "author", "") if profile else ""
        protagonist = getattr(profile, "protagonist", "") if profile else ""
        synopsis = getattr(profile, "synopsis", "") if profile else ""
        world_setting = getattr(profile, "world_setting", "") if profile else ""
        writing_style = getattr(profile, "writing_style", "") if profile else ""

        # 累积 protagonist 文本（首批注入提示文字）
        if not accumulated_protagonist:
            accumulated_text = "（首批提取，无前序参考）"
        else:
            try:
                accumulated_text = json.dumps(
                    accumulated_protagonist, ensure_ascii=False, indent=2
                )
            except (TypeError, ValueError) as e:
                logger.warning("序列化累积 protagonist 失败，使用提示文字: %s", e)
                accumulated_text = "（前序累积内容序列化失败，请独立分析）"

        # 宏替换
        replacements = {
            "{{title}}": title,
            "{{author}}": author,
            "{{protagonist}}": protagonist,
            "{{synopsis}}": synopsis,
            "{{world_setting}}": world_setting,
            "{{writing_style}}": writing_style,
            "{{accumulated_protagonist}}": accumulated_text,
            "{{chapters_text}}": chapters_text,
        }
        prompt = template
        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, value)

        return prompt

    def _merge_protagonist_fields(
        self, accumulated: dict, new_batch: dict
    ) -> dict:
        """程序化字段级合并（增量更新核心）。

        对 ProtagonistProfile 8 大维度逐字段合并：
        - 空字段：取另一侧
        - 非空：取更完整描述（按序列化长度启发式）
        - 冲突：新批次优先（后文更能反映主角最新状态）
        - **growth_arc 特殊处理**：主角弧光可能演变（弧光阶段推进/大五人格变化/
          依恋类型转变），新批次非空时直接覆盖旧值（不按长度启发式），
          确保反映至当前章节最新状态

        Args:
            accumulated: 已累积的 ProtagonistProfile 字典
            new_batch: 本批次提取的 ProtagonistProfile 字典

        Returns:
            合并后的 ProtagonistProfile 字典（含 8 大维度字段）
        """
        result: dict[str, Any] = {}
        for dim in PROTAGONIST_DIMENSIONS:
            old_val = accumulated.get(dim) or {}
            new_val = new_batch.get(dim) or {}
            # 强制 dict 类型
            if not isinstance(old_val, dict):
                old_val = {}
            if not isinstance(new_val, dict):
                new_val = {}

            # growth_arc 维度特殊处理：弧光演变，新批次非空直接覆盖
            if dim == "growth_arc":
                if new_val:
                    result[dim] = new_val
                else:
                    result[dim] = old_val
                continue

            if not old_val and not new_val:
                result[dim] = {}
            elif not old_val:
                result[dim] = new_val
            elif not new_val:
                result[dim] = old_val
            else:
                # 双侧非空：逐子字段合并
                # 启发式：按序列化长度比较，新值更长或相等时取新值
                merged_dim = dict(old_val)
                for k, v in new_val.items():
                    if k not in merged_dim or not merged_dim[k]:
                        merged_dim[k] = v
                    else:
                        old_str = _safe_serialize_dim(merged_dim[k])
                        new_str = _safe_serialize_dim(v)
                        if len(new_str) >= len(old_str):
                            merged_dim[k] = v
                result[dim] = merged_dim
        return result

    def _build_protagonist_blocks(
        self,
        batch_results: list[dict],
        batch_ranges: list[tuple[int, int]],
    ) -> str:
        """构造主角形象合并提示词的 ``{{entries_blocks}}`` 占位符内容。

        每批 JSON 前加 ``## 批次 i/N（章节 X-Y）`` 标题，块间空行分隔。

        Args:
            batch_results: 各批次独立提取的 ProtagonistProfile 字典列表
            batch_ranges: 各批次章节区间列表（与 batch_results 一一对应）

        Returns:
            拼接后的多批次文本块
        """
        total = len(batch_results)
        parts: list[str] = []
        for idx, (profile, (start, end)) in enumerate(
            zip(batch_results, batch_ranges)
        ):
            block_json = json.dumps(profile, ensure_ascii=False, indent=2)
            parts.append(
                f"## 批次 {idx + 1}/{total}（章节 {start}-{end}）\n\n"
                f"```json\n{block_json}\n```"
            )
        return "\n\n".join(parts)

    async def _run_protagonist_merge(
        self,
        batch_results: list[dict],
        accumulated_protagonist: dict,
        llm_client: LLMClient,
        model: str,
        stream: bool,
        on_chunk: Callable[[str], None] | None,
        on_batch_complete: Callable[[list, int, int], None] | None,
        batch_count: int,
        total_token_usage: dict[str, Any],
        batch_ranges: list[tuple[int, int]] | None = None,
    ) -> dict | None:
        """合并多批次 ProtagonistProfile 结果（主角形象汇总环节）。

        构造合并提示词（``extract_protagonist_merge_prompt.txt``），调用 LLM，
        解析响应为 ProtagonistProfile 字典。2 次重试（温度 0.2/0.0），
        失败返回 accumulated_protagonist（降级）。

        Args:
            batch_results: 各批次独立提取的 ProtagonistProfile 字典列表
            accumulated_protagonist: 程序化合并的累积 protagonist（降级时返回）
            llm_client: LLM 客户端
            model: 模型名
            stream: 是否流式调用
            on_chunk: 流式 chunk 回调
            on_batch_complete: 流式批次完成回调
            batch_count: 总批次数
            total_token_usage: 累计 token_usage
            batch_ranges: 各批次章节区间列表（None 时用占位区间）

        Returns:
            合并后的 ProtagonistProfile 字典，失败返回 accumulated_protagonist（降级）
        """
        # 过滤空批次
        non_empty_indices = [i for i, r in enumerate(batch_results) if r]
        if not non_empty_indices:
            logger.warning("主角形象汇总：所有批次均为空，返回累积结果")
            return accumulated_protagonist

        # 构造用于合并的批次列表（仅非空批次）
        if batch_ranges is None:
            merge_results = [batch_results[i] for i in non_empty_indices]
            merge_ranges = [(i + 1, i + 1) for i in non_empty_indices]
        else:
            merge_results = [batch_results[i] for i in non_empty_indices]
            merge_ranges = [batch_ranges[i] for i in non_empty_indices]

        template = self._load_protagonist_merge_prompt_template()
        entries_blocks = self._build_protagonist_blocks(merge_results, merge_ranges)
        prompt = template.replace("{{entries_blocks}}", entries_blocks)
        messages = [{"role": "user", "content": prompt}]

        # 流式分隔标记
        if stream and on_chunk is not None:
            separator = "\n\n--- 主角形象汇总 ---\n\n"
            try:
                on_chunk(separator)
            except Exception as e:
                logger.warning("on_chunk 回调异常: %s", e)

        # 取消信号检查
        if self._cancel_event.is_set():
            return accumulated_protagonist

        # 2 次重试：温度 0.2 / 0.0
        temperatures = [
            PROTAGONIST_MERGE_TEMPERATURE,
            PROTAGONIST_MERGE_TEMPERATURE_RETRY,
        ]
        last_error: str = ""
        for attempt, temperature in enumerate(temperatures):
            if self._cancel_event.is_set():
                return accumulated_protagonist

            content_parts: list[str] = []
            response: dict[str, Any] = {}
            try:
                if stream:
                    async for chunk in llm_client.stream_chat_completion(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=PROTAGONIST_EXTRACT_MAX_TOKENS,
                        stop_event=self._cancel_event,
                    ):
                        if chunk.content:
                            content_parts.append(chunk.content)
                            if on_chunk is not None:
                                try:
                                    on_chunk(chunk.content)
                                except Exception as e:
                                    logger.warning("on_chunk 回调异常: %s", e)
                        if chunk.finish_reason:
                            break
                    content = "".join(content_parts)
                else:
                    response = await llm_client.chat_completion(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=PROTAGONIST_EXTRACT_MAX_TOKENS,
                        stop_event=self._cancel_event,
                    )
                    batch_usage = response.get("usage", {})
                    if isinstance(batch_usage, dict):
                        for k, v in batch_usage.items():
                            if isinstance(v, (int, float)):
                                total_token_usage[k] = (
                                    total_token_usage.get(k, 0) + v
                                )
                    choices = response.get("choices", [])
                    if not choices:
                        last_error = "LLM 响应无 choices"
                        logger.warning(
                            "主角形象汇总第 %d 次尝试无 choices", attempt + 1
                        )
                        continue
                    message = choices[0].get("message", {})
                    content = message.get("content", "") or ""

                # 解析 JSON
                merged_protagonist = _parse_protagonist_response(content)
                logger.info(
                    "主角形象汇总成功: %d 批次合并 (第 %d 次尝试, 温度=%.1f)",
                    len(merge_results), attempt + 1, temperature,
                )
                return merged_protagonist

            except asyncio.CancelledError:
                logger.info("主角形象汇总被取消")
                return accumulated_protagonist
            except asyncio.TimeoutError as e:
                last_error = f"超时: {e}"
                logger.warning(
                    "主角形象汇总第 %d 次尝试超时: %s", attempt + 1, e
                )
                continue
            except (AuthError, RateLimitError, APIError, LLMError) as e:
                last_error = str(e)
                logger.warning(
                    "主角形象汇总第 %d 次尝试 LLM 调用失败: %s",
                    attempt + 1, e,
                )
                continue
            except json.JSONDecodeError as e:
                last_error = f"JSON 解析失败: {e}"
                logger.warning(
                    "主角形象汇总第 %d 次尝试 JSON 解析失败: %s",
                    attempt + 1, e,
                )
                continue
            except Exception as e:
                last_error = f"异常: {e}"
                logger.error(
                    "主角形象汇总第 %d 次尝试异常: %s",
                    attempt + 1, e, exc_info=True,
                )
                continue

        # 全部重试失败 → 降级返回累积结果
        logger.warning(
            "主角形象汇总 2 次重试均失败，降级返回累积结果: %s", last_error
        )
        return accumulated_protagonist

    async def _extract_protagonist(
        self,
        project: Project | None,
        batches: list[list[Chapter]],
        config: dict[str, Any],
        client: LLMClient,
        model: str,
        stream: bool,
        on_chunk: Callable[[str], None] | None,
        on_batch_complete: Callable[[list, int, int], None] | None,
    ) -> tuple[ProtagonistProfile | None, int, bool]:
        """主角形象提取（完整支持 token 拆分 + 增量更新 + 合并）。

        流程：
        1. 复用 8 维度提取的 batches 划分（不重复拆分计算）
        2. 逐批增量提取 ProtagonistProfile（用 extract_protagonist_prompt.txt）：
           - 第 1 批：独立提取，得 profile_1
           - 第 2 批：携带 profile_1 作为增量上下文（{{accumulated_protagonist}} 占位符），
             提取得 profile_2（可能深化/修正 profile_1，特别注意弧光演变）
           - 第 N 批：携带 accumulated profile_{1..N-1}，提取得 profile_N
           - 维护 accumulated_protagonist 累积变量（_merge_protagonist_fields 程序化合并）
        3. batch_count > 1 时调用 _run_protagonist_merge 合并：
           - 消除跨块重复/冲突消解（弧光以最新为准）/补全连贯/OOC红线完整性检查
           - 失败降级返回 accumulated_protagonist 不阻塞流程
        4. 返回 (ProtagonistProfile, batch_count, merged)

        Args:
            project: 项目对象
            batches: 8 维度提取已计算的批次划分（复用）
            config: 提取配置
            client: LLM 客户端
            model: 模型名
            stream: 是否流式调用
            on_chunk: 流式 chunk 回调
            on_batch_complete: 流式批次完成回调

        Returns:
            (ProtagonistProfile | None, batch_count, merged) 元组：
            - 成功：(ProtagonistProfile, batch_count, merged)
            - 失败：(None, batch_count, False)
        """
        batch_count = len(batches)
        log_prefix = "流式" if stream else ""

        # 逐批调用 LLM，每批携带前批累积的 ProtagonistProfile
        accumulated_protagonist: dict[str, Any] = {}
        batch_results: list[dict[str, Any]] = []  # 各批次独立结果，供汇总环节使用
        batch_ranges: list[tuple[int, int]] = []  # 各批次章节区间

        for batch_idx, batch_chapters in enumerate(batches):
            # 取消信号检查
            if self._cancel_event.is_set():
                logger.info("主角形象提取被取消（批次 %d/%d 前）",
                            batch_idx + 1, batch_count)
                return None, batch_count, False

            batch_start = min(ch.index for ch in batch_chapters)
            batch_end = max(ch.index for ch in batch_chapters)
            batch_ranges.append((batch_start, batch_end))

            # 多批次时插入分隔标记（仅流式模式）
            if stream and batch_count > 1 and on_chunk is not None:
                separator = (
                    f"\n\n--- 主角形象 批次 {batch_idx + 1}/{batch_count} "
                    f"(章节 {batch_start}-{batch_end}) ---\n\n"
                )
                try:
                    on_chunk(separator)
                except Exception as e:
                    logger.warning("on_chunk 回调异常: %s", e)

            # 构建提示词（首批 accumulated=None → 注入"（首批提取，无前序参考）"）
            chapters_text_parts: list[str] = []
            for ch in batch_chapters:
                chapters_text_parts.append(f"## {ch.title}\n\n{ch.content}")
            chapters_text = "\n\n".join(chapters_text_parts)

            try:
                prompt = self._build_protagonist_prompt(
                    project, chapters_text,
                    accumulated_protagonist=accumulated_protagonist or None,
                )
            except Exception as e:
                logger.error("构建主角形象提示词失败: %s", e, exc_info=True)
                return None, batch_count, False

            # 调用 LLM（流式/非流式 + 超时重试 1 次）
            messages = [{"role": "user", "content": prompt}]
            content_parts: list[str] = []
            response: dict[str, Any] = {}
            retried = False

            while True:
                try:
                    if stream:
                        async for chunk in client.stream_chat_completion(
                            messages=messages,
                            model=model,
                            temperature=PROTAGONIST_EXTRACT_TEMPERATURE,
                            max_tokens=PROTAGONIST_EXTRACT_MAX_TOKENS,
                            stop_event=self._cancel_event,
                        ):
                            if chunk.content:
                                content_parts.append(chunk.content)
                                if on_chunk is not None:
                                    try:
                                        on_chunk(chunk.content)
                                    except Exception as e:
                                        logger.warning("on_chunk 回调异常: %s", e)
                            if chunk.finish_reason:
                                break
                        content = "".join(content_parts)
                    else:
                        response = await client.chat_completion(
                            messages=messages,
                            model=model,
                            temperature=PROTAGONIST_EXTRACT_TEMPERATURE,
                            max_tokens=PROTAGONIST_EXTRACT_MAX_TOKENS,
                            stop_event=self._cancel_event,
                        )
                    break
                except asyncio.CancelledError:
                    logger.info("主角形象提取被取消（批次 %d/%d 调用中）",
                                batch_idx + 1, batch_count)
                    return None, batch_count, False
                except asyncio.TimeoutError as e:
                    if not retried:
                        logger.warning(
                            "主角形象批次 %d/%d 超时，重试中...",
                            batch_idx + 1, batch_count,
                        )
                        retried = True
                        if self._cancel_event.is_set():
                            return None, batch_count, False
                        if stream:
                            content_parts.clear()
                        continue
                    logger.error(
                        "主角形象批次 %d/%d 超时重试失败: %s",
                        batch_idx + 1, batch_count, e,
                    )
                    return None, batch_count, False
                except (AuthError, RateLimitError, APIError, LLMError) as e:
                    logger.error("主角形象提取 LLM 调用失败: %s", e)
                    return None, batch_count, False
                except Exception as e:
                    logger.error("主角形象提取异常: %s", e, exc_info=True)
                    return None, batch_count, False

            # 取消信号检查
            if self._cancel_event.is_set():
                return None, batch_count, False

            # 非流式模式提取 content
            if not stream:
                choices = response.get("choices", [])
                if not choices:
                    logger.error(
                        "主角形象批次 %d/%d 响应无 choices",
                        batch_idx + 1, batch_count,
                    )
                    return None, batch_count, False
                message = choices[0].get("message", {})
                content = message.get("content", "") or ""

            # 解析 JSON → dict（仅保留 8 大维度字段）
            try:
                batch_protagonist = _parse_protagonist_response(content)
            except json.JSONDecodeError as e:
                logger.error(
                    "主角形象批次 %d/%d JSON 解析失败: %s, content=%s",
                    batch_idx + 1, batch_count, e, content[:200],
                )
                return None, batch_count, False

            # 记录本批次独立结果（供汇总环节使用）
            batch_results.append(batch_protagonist)

            # 增量合并到 accumulated_protagonist（核心：增量更新）
            accumulated_protagonist = self._merge_protagonist_fields(
                accumulated_protagonist, batch_protagonist
            )

            logger.info(
                "%s主角形象批次 %d/%d 完成: 章节 %d-%d (累计 %d 维度非空)",
                log_prefix,
                batch_idx + 1, batch_count,
                batch_start, batch_end,
                sum(1 for v in accumulated_protagonist.values() if v),
            )

        # 主角形象汇总环节：batch_count > 1 时调用 _run_protagonist_merge 语义整合
        merged = False
        if batch_count > 1:
            merged_protagonist = await self._run_protagonist_merge(
                batch_results=batch_results,
                accumulated_protagonist=accumulated_protagonist,
                llm_client=client,
                model=model,
                stream=stream,
                on_chunk=on_chunk,
                on_batch_complete=on_batch_complete,
                batch_count=batch_count,
                total_token_usage={},  # 主角形象 token_usage 不累加到 8 维度
                batch_ranges=batch_ranges,
            )
            if merged_protagonist is not None:
                accumulated_protagonist = merged_protagonist
                merged = True
                logger.info("主角形象汇总环节完成")
            else:
                logger.warning("主角形象汇总环节失败，降级使用累积结果")

        # 取消信号检查
        if self._cancel_event.is_set():
            return None, batch_count, False

        # 构建 ProtagonistProfile 对象
        all_chapter_indices: list[int] = []
        for batch_chapters in batches:
            for ch in batch_chapters:
                all_chapter_indices.append(ch.index)
        source_chapter_range = (
            (min(all_chapter_indices), max(all_chapter_indices))
            if all_chapter_indices else None
        )
        profile = ProtagonistProfile(
            **accumulated_protagonist,
            extracted_at=datetime.now(),
            source_chapter_range=source_chapter_range,
        )

        logger.info(
            "%s主角形象提取完成: %d 批 (merged=%s)",
            log_prefix, batch_count, merged,
        )
        return profile, batch_count, merged

    def _get_lookback_chapters(
        self,
        chapters: list[Chapter],
        current_chapter: Chapter,
        lookback: int,
    ) -> list[Chapter]:
        """获取前 N 章正文（含当前章节）。

        章节数不足 N 时取所有可用章节。
        lookback <= 0 表示全部前文（从第 0 章到当前章节含）。

        Args:
            chapters: 项目所有章节（按 index 排序）
            current_chapter: 当前续写章节
            lookback: 回溯章节数（0 或负数=全部前文）

        Returns:
            待提取的章节列表（旧→新，最后一条为当前章节）
        """
        sorted_chapters = sorted(chapters, key=lambda c: c.index)
        # 找到当前章节位置
        current_idx = -1
        for i, ch in enumerate(sorted_chapters):
            if ch.id == current_chapter.id:
                current_idx = i
                break

        if current_idx == -1:
            # 当前章节不在列表中
            if lookback <= 0:
                return sorted_chapters  # 全部前文
            return sorted_chapters[-lookback:] if lookback > 0 else []

        # lookback <= 0 表示全部前文：返回从第 0 章到当前章节
        if lookback <= 0:
            return sorted_chapters[: current_idx + 1]

        # 取前 lookback 章（含当前章节）
        start = max(0, current_idx - lookback + 1)
        return sorted_chapters[start : current_idx + 1]

    def _split_chapters_by_token_limit(
        self, chapters: list[Chapter], token_limit: int, model: str
    ) -> list[list[Chapter]]:
        """按 token 限制拆分章节为多个批次。

        - token_limit <= 0：不拆分，返回 ``[chapters]``
        - 按章节顺序贪心装填：当前批次 + 下一章超限时开新批次
        - 单章超限：该章独占一个批次（无法再拆分）

        Args:
            chapters: 待拆分的章节列表（已按 index 排序）
            token_limit: 每批 token 上限
            model: 模型名（决定 tokenizer）

        Returns:
            章节批次列表
        """
        if token_limit <= 0 or not chapters:
            return [chapters]

        batches: list[list[Chapter]] = []
        current_batch: list[Chapter] = []
        current_tokens = 0

        for ch in chapters:
            # token 计数缓存：以 (chapter_id, content_hash) 为 key，
            # 章节内容未变时复用上次计数结果，避免重复加载 tiktoken 编码大文本
            cache_key = (ch.id, _hash_chapter_content(ch.content or ""))
            if cache_key in self._token_cache:
                ch_tokens = self._token_cache[cache_key]
            else:
                ch_tokens = self.token_counter.count(ch.content or "", model)
                self._token_cache[cache_key] = ch_tokens
            # 当前批次非空且加入后超限 → 开新批次
            if current_batch and current_tokens + ch_tokens > token_limit:
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0
            current_batch.append(ch)
            current_tokens += ch_tokens

        if current_batch:
            batches.append(current_batch)

        return batches

    def _build_cache_key(
        self, project_id: str, chapter_id: str
    ) -> str:
        """构建按章节绑定的缓存 key。

        格式：``ctx_extract:{project_id}:{chapter_id}``

        Args:
            project_id: 项目 ID
            chapter_id: 当前章节 ID

        Returns:
            缓存 key
        """
        return f"{CACHE_KEY_PREFIX}:{project_id}:{chapter_id}"

    async def _get_cached_data(
        self, cache_key: str
    ) -> dict[str, Any] | None:
        """从缓存读取提取结果（完整 dict，含 entries + 元数据）。

        Args:
            cache_key: 缓存 key

        Returns:
            缓存 dict（含 entries/chapters_hash/extracted_at 等），未命中返回 None
        """
        try:
            data = await self.storage_service.storage.get_cache(cache_key)
            if data is None:
                return None
            if not isinstance(data, dict):
                # 旧格式缓存（纯 list），忽略
                logger.debug("缓存数据为旧格式，忽略: %s", type(data).__name__)
                return None
            raw_entries = data.get("entries", [])
            if not isinstance(raw_entries, list):
                logger.warning("缓存 entries 格式异常，忽略")
                return None
            entries: list[ContextEntry] = []
            for item in raw_entries:
                try:
                    entries.append(ContextEntry.model_validate(item))
                except Exception as e:
                    logger.warning("缓存条目反序列化失败，跳过: %s", e)
            data["entries"] = entries
            logger.info("命中上下文提取缓存: %s (%d 条)", cache_key, len(entries))
            return data
        except Exception as e:
            logger.warning("读取缓存失败: %s", e)
            return None

    async def _get_cached_entries(
        self, cache_key: str, current_chapters_hash: str
    ) -> dict[str, Any] | None:
        """从缓存读取提取结果，并校验 chapters_hash 是否匹配。

        Args:
            cache_key: 缓存 key
            current_chapters_hash: 当前目标章节的内容哈希

        Returns:
            完整缓存 dict（含 entries/protagonist_profile 等字段，hash 匹配时），
            不匹配或未命中返回 None
        """
        data = await self._get_cached_data(cache_key)
        if data is None:
            return None
        cached_hash = data.get("chapters_hash", "")
        if cached_hash != current_chapters_hash:
            logger.info(
                "缓存 chapters_hash 不匹配（%s != %s），需重新提取",
                cached_hash[:8],
                current_chapters_hash[:8],
            )
            return None
        return data

    async def _save_cached_entries(
        self,
        cache_key: str,
        entries: list[ContextEntry],
        chapters_hash: str,
        ttl_hours: int,
        elapsed_seconds: float = 0.0,
        token_usage: dict[str, Any] | None = None,
        lookback: int = 0,
        batch_count: int = 1,
        merged: bool = False,
        protagonist_profile: ProtagonistProfile | None = None,
        protagonist_batch_count: int = 1,
        protagonist_merged: bool = False,
    ) -> None:
        """保存提取结果到缓存（按章节绑定，含元数据）。

        Args:
            cache_key: 缓存 key
            entries: ContextEntry 列表
            chapters_hash: 来源章节内容哈希（用于失效判断）
            ttl_hours: 缓存有效期（小时）
            elapsed_seconds: 提取耗时
            token_usage: token 消耗信息
            lookback: 使用的 lookback 值
            batch_count: 拆分批次数
            merged: 是否经过【信息汇总】环节
            protagonist_profile: 主角形象档案（跟随章节缓存）
            protagonist_batch_count: 主角形象提取批次数
            protagonist_merged: 主角形象是否经合并环节
        """
        try:
            data = {
                "entries": [e.model_dump(mode="json") for e in entries],
                "chapters_hash": chapters_hash,
                "extracted_at": datetime.now().isoformat(),
                "elapsed_seconds": elapsed_seconds,
                "token_usage": token_usage or {},
                "lookback": lookback,
                "batch_count": batch_count,
                "merged": merged,
                "protagonist_profile": (
                    protagonist_profile.model_dump(mode="json")
                    if protagonist_profile else None
                ),
                "protagonist_batch_count": protagonist_batch_count,
                "protagonist_merged": protagonist_merged,
            }
            await self.storage_service.storage.set_cache(
                cache_key, data, ttl_hours=ttl_hours, category=CACHE_CATEGORY
            )
            logger.info(
                "已保存上下文提取缓存: %s (%d 条, TTL=%dh, 批次=%d, merged=%s, "
                "protagonist=%s)",
                cache_key,
                len(entries),
                ttl_hours,
                batch_count,
                merged,
                "有" if protagonist_profile else "无",
            )
        except Exception as e:
            logger.warning("保存缓存失败: %s", e)

    def _get_llm_client(self) -> tuple[LLMClient, str] | None:
        """获取 LLM 客户端与默认模型。

        从配置中读取默认端点，解密 API Key，创建 LLMClient。

        Returns:
            (LLMClient, model) 元组，配置缺失返回 None
        """
        default_ep = self.config_manager.get_default_endpoint()
        if not default_ep:
            logger.error("未配置默认 API 端点，无法提取上下文")
            return None

        api_key = self.config_manager.decrypt_api_key(default_ep.get("id", ""))
        if not api_key:
            logger.error("API Key 无效，无法提取上下文")
            return None

        base_url = default_ep.get("base_url", "")
        if not base_url:
            logger.error("API base_url 为空，无法提取上下文")
            return None

        client = LLMClient(base_url=base_url, api_key=api_key, timeout=300)
        model = default_ep.get("default_model", "") or DEFAULT_EXTRACTOR_MODEL
        return client, model

    async def _extract_common(
        self,
        project: Project | None,
        chapters: list[Chapter],
        current_chapter: Chapter | None,
        force_refresh: bool = False,
        lookback_override: int | None = None,
        token_limit_override: int | None = None,
        stream: bool = False,
        on_chunk: Callable[[str], None] | None = None,
        on_batch_complete: Callable[[list, int, int], None] | None = None,
    ) -> ExtractResult:
        """统一的上下文提取实现（流式与非流式共享）。

        ``extract`` 与 ``extract_streaming`` 的唯一差异在于 LLM 调用方式：
        - 非流式（``stream=False``）：调用 ``chat_completion`` 一次性获取响应
        - 流式（``stream=True``）：调用 ``stream_chat_completion`` 逐 chunk 接收，
          通过 ``on_chunk`` 回调推送增量文本，通过 ``on_batch_complete`` 增量更新 UI

        Args:
            project: 项目对象（可为 None）
            chapters: 项目所有章节
            current_chapter: 当前续写章节（None 时取最后 N 章）
            force_refresh: 强制刷新（跳过缓存）
            lookback_override: 覆盖 lookback_chapters（0=全部前文，None=用配置默认值）
            token_limit_override: 覆盖 token_limit（0=不限制，None=用配置默认值）
            stream: 是否使用流式调用
            on_chunk: 流式模式下每个 chunk 的回调函数（接收增量文本）
            on_batch_complete: 流式模式下每批完成回调（累计 entries, 批次序号, 总批数）

        Returns:
            ExtractResult 对象
        """
        start_time = time.time()
        config = self._get_extract_config(project)
        if lookback_override is not None:
            lookback = lookback_override
        else:
            lookback = int(
                config.get("lookback_chapters", DEFAULT_LOOKBACK_CHAPTERS)
            )
        # 上界校验：lookback > 0 时若超过章节总数，clamp 至章节总数并告警，
        # 避免 _get_lookback_chapters 因极大 lookback 而加载全部章节、构造超大 prompt。
        # （lookback <= 0 表示"全部前文"的特殊语义，不在此 clamp）
        if lookback > 0 and len(chapters) > 0 and lookback > len(chapters):
            logger.warning(
                "lookback %d 超过章节总数 %d，已 clamp 至 %d",
                lookback, len(chapters), len(chapters),
            )
            lookback = len(chapters)
        if token_limit_override is not None:
            token_limit = token_limit_override
        else:
            token_limit = int(config.get("token_limit", 0))
        extractor_model = config.get("extractor_model", "") or ""
        cache_ttl_hours = int(
            config.get("cache_ttl_hours", DEFAULT_CACHE_TTL_HOURS)
        )
        cache_enabled = bool(config.get("cache_enabled", True))

        # 日志前缀（流式/非流式区分）
        log_prefix = "流式" if stream else ""

        # 检查是否在本次提取开始前已被取消（cancel() 早于提取调用）。
        # 先快照当前状态再 clear()，否则取消信号会被抹掉导致本次提取无法被取消；
        # 若已取消，直接返回 cancelled 结果，避免无谓的 LLM 调用。
        cancelled_before_start = self._cancel_event.is_set()
        self._cancel_event.clear()  # 为本次提取重置取消信号
        if cancelled_before_start:
            logger.info("%s上下文提取在开始前已被取消", log_prefix)
            return ExtractResult(
                entries=[],
                status="failed",
                error="用户取消提取",
                elapsed_seconds=time.time() - start_time,
            )

        # 0 章时跳过提取
        if not chapters:
            logger.info("无前文可提取，跳过上下文提取")
            return ExtractResult(
                entries=[],
                status="skipped",
                error="无前文可提取",
                elapsed_seconds=time.time() - start_time,
            )

        # 获取前 N 章
        if current_chapter is not None:
            target_chapters = self._get_lookback_chapters(
                chapters, current_chapter, lookback
            )
        else:
            # 无当前章节时取最后 N 章
            sorted_chapters = sorted(chapters, key=lambda c: c.index)
            target_chapters = (
                sorted_chapters[-lookback:] if lookback > 0 else sorted_chapters
            )

        if not target_chapters:
            logger.info("无可提取的章节，跳过上下文提取")
            return ExtractResult(
                entries=[],
                status="skipped",
                error="无前文可提取",
                elapsed_seconds=time.time() - start_time,
            )

        extracted_at = datetime.now()

        # 按章节绑定缓存 key
        project_id = project.id if project else "unknown"
        chapter_id = current_chapter.id if current_chapter else "global"
        cache_key = self._build_cache_key(project_id, chapter_id)
        chapters_hash = _compute_chapters_hash(target_chapters)

        # 缓存检查（比对 chapters_hash）
        if cache_enabled and not force_refresh:
            cached = await self._get_cached_entries(cache_key, chapters_hash)
            if cached is not None:
                # 从缓存 dict 提取 entries 与 protagonist_profile
                cached_entries = cached.get("entries", [])
                cached_protagonist_data = cached.get("protagonist_profile")
                cached_protagonist: ProtagonistProfile | None = None
                if cached_protagonist_data:
                    try:
                        cached_protagonist = ProtagonistProfile.model_validate(
                            cached_protagonist_data
                        )
                    except Exception as e:
                        logger.warning("缓存 protagonist_profile 反序列化失败: %s", e)
                return ExtractResult(
                    entries=cached_entries,
                    status="completed",
                    elapsed_seconds=time.time() - start_time,
                    from_cache=True,
                    batch_count=cached.get("batch_count", 1),
                    merged=cached.get("merged", False),
                    protagonist_profile=cached_protagonist,
                    protagonist_batch_count=cached.get(
                        "protagonist_batch_count", 1
                    ),
                    protagonist_merged=cached.get("protagonist_merged", False),
                )

        # 获取 LLM 客户端
        client_info = self._get_llm_client()
        if client_info is None:
            return ExtractResult(
                entries=[],
                status="failed",
                error="未配置 API 端点或 API Key 无效",
                elapsed_seconds=time.time() - start_time,
            )
        client, default_model = client_info
        # 模型选择优先级：用户配置的 extractor_model > 端点 default_model > DEFAULT_EXTRACTOR_MODEL
        if extractor_model:
            model = extractor_model
        else:
            model = default_model or DEFAULT_EXTRACTOR_MODEL

        # 按 token 限制拆分批次
        batches = self._split_chapters_by_token_limit(
            target_chapters, token_limit, model
        )
        batch_count = len(batches)
        logger.info(
            "%s上下文提取: %d 章拆分为 %d 批 (token_limit=%d)",
            log_prefix,
            len(target_chapters),
            batch_count,
            token_limit,
        )

        # 逐批调用 LLM（每批独立全量提取，跨批去重由【信息汇总】环节统一处理）
        all_entries: list[ContextEntry] = []  # best-effort 累计，仅供 UI 增量显示
        entries_by_uid: dict[str, int] = {}  # uid -> all_entries 索引（uid 替换合并，仅用于 UI 显示）
        batch_results: list[list[ContextEntry]] = []  # 各批次独立结果，供汇总环节使用
        total_token_usage: dict[str, Any] = {}

        for batch_idx, batch_chapters in enumerate(batches):
            # 检查取消信号
            if self._cancel_event.is_set():
                return ExtractResult(
                    entries=[],
                    status="failed",
                    error="用户取消提取",
                    elapsed_seconds=time.time() - start_time,
                )

            batch_start = min(ch.index for ch in batch_chapters)
            batch_end = max(ch.index for ch in batch_chapters)
            batch_range: tuple[int, int] = (batch_start, batch_end)

            # 多批次时插入分隔标记（仅流式模式 + on_chunk 回调）
            if stream and batch_count > 1 and on_chunk is not None:
                separator = (
                    f"\n\n--- 批次 {batch_idx + 1}/{batch_count} "
                    f"(章节 {batch_start}-{batch_end}) ---\n\n"
                )
                try:
                    on_chunk(separator)
                except Exception as e:
                    logger.warning("on_chunk 回调异常: %s", e)

            # 构建 prompt（仅含该批章节；每批独立全量提取，不附已有条目）
            try:
                prompt = self._build_prompt(
                    project, batch_chapters, config,
                )
            except Exception as e:
                logger.error("构建提取提示词失败: %s", e, exc_info=True)
                return ExtractResult(
                    entries=[],
                    status="failed",
                    error=f"构建提示词失败: {e}",
                    elapsed_seconds=time.time() - start_time,
                )

            # 调用 LLM（超时重试 1 次）；流式与非流式分支处理
            messages = [{"role": "user", "content": prompt}]
            content_parts: list[str] = []  # 流式模式累积 chunk
            response: dict[str, Any] = {}  # 非流式模式响应
            extract_timeout_retried = False

            while True:
                try:
                    if stream:
                        async for chunk in client.stream_chat_completion(
                            messages=messages,
                            model=model,
                            temperature=EXTRACT_TEMPERATURE,
                            max_tokens=EXTRACT_MAX_TOKENS,
                            stop_event=self._cancel_event,
                        ):
                            if chunk.content:
                                content_parts.append(chunk.content)
                                if on_chunk is not None:
                                    try:
                                        on_chunk(chunk.content)
                                    except Exception as e:
                                        logger.warning("on_chunk 回调异常: %s", e)
                            if chunk.finish_reason:
                                break
                    else:
                        response = await client.chat_completion(
                            messages=messages,
                            model=model,
                            temperature=EXTRACT_TEMPERATURE,
                            max_tokens=EXTRACT_MAX_TOKENS,
                            stop_event=self._cancel_event,
                        )
                    break  # 成功，退出重试循环
                except asyncio.CancelledError:
                    logger.info("上下文提取被取消")
                    return ExtractResult(
                        entries=[],
                        status="failed",
                        error="用户取消提取",
                        elapsed_seconds=time.time() - start_time,
                    )
                except asyncio.TimeoutError as e:
                    if not extract_timeout_retried:
                        logger.warning(
                            "%s批次 %d/%d 超时，重试中...",
                            log_prefix, batch_idx + 1, batch_count,
                        )
                        extract_timeout_retried = True
                        # 超时重试前检查取消信号：用户在首次尝试期间已点击取消，
                        # 不应再发起重试，直接返回 cancelled 结果。
                        if self._cancel_event.is_set():
                            return ExtractResult(
                                entries=[],
                                status="failed",
                                error="用户取消提取",
                                elapsed_seconds=time.time() - start_time,
                            )
                        # 流式模式需清空已累积的 chunk，避免拼接出残缺内容
                        if stream:
                            content_parts.clear()
                        continue
                    # 重试仍失败：保留已有批次结果，返回部分结果
                    logger.error(
                        "%s批次 %d/%d 超时重试失败: %s",
                        log_prefix, batch_idx + 1, batch_count, e,
                    )
                    return ExtractResult(
                        entries=all_entries,
                        status="failed",
                        error=f"批次 {batch_idx + 1}/{batch_count} 超时（已重试），"
                              f"已保留前 {len(all_entries)} 条结果",
                        elapsed_seconds=time.time() - start_time,
                        token_usage=total_token_usage,
                    )
                except (AuthError, RateLimitError, APIError, LLMError) as e:
                    logger.error("上下文提取 LLM 调用失败: %s", e)
                    return ExtractResult(
                        entries=[],
                        status="failed",
                        error=str(e),
                        elapsed_seconds=time.time() - start_time,
                    )
                except Exception as e:
                    logger.error("上下文提取异常: %s", e, exc_info=True)
                    return ExtractResult(
                        entries=[],
                        status="failed",
                        error=f"提取异常: {e}",
                        elapsed_seconds=time.time() - start_time,
                    )

            # 检查取消信号
            if self._cancel_event.is_set():
                return ExtractResult(
                    entries=[],
                    status="failed",
                    error="用户取消提取",
                    elapsed_seconds=time.time() - start_time,
                )

            # 提取内容与 token_usage（流式与非流式路径不同）
            if stream:
                content = "".join(content_parts)
            else:
                # 累加 token_usage
                batch_usage = response.get("usage", {})
                if isinstance(batch_usage, dict):
                    for k, v in batch_usage.items():
                        if isinstance(v, (int, float)):
                            total_token_usage[k] = (
                                total_token_usage.get(k, 0) + v
                            )

                # 解析响应
                choices = response.get("choices", [])
                if not choices:
                    return ExtractResult(
                        entries=[],
                        status="failed",
                        error="LLM 响应无 choices",
                        elapsed_seconds=time.time() - start_time,
                        token_usage=total_token_usage,
                    )

                message = choices[0].get("message", {})
                content = message.get("content", "") or ""

            # 解析响应
            try:
                raw_entries = _parse_extract_response(content)
            except json.JSONDecodeError as e:
                logger.error(
                    "提取响应 JSON 解析失败: %s, content=%s", e, content[:200]
                )
                return ExtractResult(
                    entries=[],
                    status="failed",
                    error=f"JSON 解析失败: {e}",
                    elapsed_seconds=time.time() - start_time,
                    token_usage=total_token_usage,
                )

            # 校验并规范化，收集本批独立结果（uid 替换合并仅用于 UI best-effort 显示）
            batch_entries: list[ContextEntry] = []
            for raw in raw_entries:
                entry = _validate_and_normalize_entry(
                    raw, batch_range, extracted_at
                )
                if entry is not None:
                    batch_entries.append(entry)
                    # 维护 all_entries（uid 替换）仅供 UI 增量显示
                    if entry.uid in entries_by_uid:
                        idx = entries_by_uid[entry.uid]
                        all_entries[idx] = entry
                    else:
                        entries_by_uid[entry.uid] = len(all_entries)
                        all_entries.append(entry)
            batch_results.append(batch_entries)

            logger.info(
                "%s批次 %d/%d 完成: +%d 条 (累计 %d, 章节 %d-%d)",
                log_prefix,
                batch_idx + 1,
                batch_count,
                len(raw_entries),
                len(all_entries),
                batch_start,
                batch_end,
            )

            # 增量更新 UI（仅流式模式 + on_batch_complete 回调）
            if stream and on_batch_complete is not None:
                try:
                    on_batch_complete(
                        list(all_entries), batch_idx + 1, batch_count
                    )
                except Exception as e:
                    logger.warning("on_batch_complete 回调异常: %s", e)

        # 【信息汇总】环节：仅多批次时触发，LLM 合并去重为最终 ContextEntry 列表
        merged = False
        if batch_count > 1:
            merged_entries = await self._run_merge_entries(
                batch_results=batch_results,
                config=config,
                client=client,
                model=model,
                stream=stream,
                on_chunk=on_chunk,
                on_batch_complete=on_batch_complete,
                batch_count=batch_count,
                total_token_usage=total_token_usage,
                log_prefix=log_prefix,
            )
            if merged_entries is not None:
                final_entries = merged_entries
                merged = True
            else:
                # 汇总失败降级：使用 best-effort uid 替换合并的 all_entries
                logger.warning(
                    "%s信息汇总失败，降级使用 best-effort 合并结果 (%d 条)",
                    log_prefix, len(all_entries),
                )
                final_entries = all_entries
        else:
            final_entries = batch_results[0] if batch_results else []

        logger.info(
            "%s上下文提取完成: %d 条 (%d 批, merged=%s, 耗时 %.2fs)",
            log_prefix,
            len(final_entries),
            batch_count,
            merged,
            time.time() - start_time,
        )

        # === 主角形象提取（与 8 维度共用批次划分，三大机制完整支持）===
        # 复用已计算的 batches（不重复拆分），顺序执行独立提示词，
        # 失败不阻塞 8 维度结果
        protagonist_profile: ProtagonistProfile | None = None
        protagonist_batch_count = 1
        protagonist_merged = False
        try:
            protagonist_profile, protagonist_batch_count, protagonist_merged = (
                await self._extract_protagonist(
                    project=project,
                    batches=batches,
                    config=config,
                    client=client,
                    model=model,
                    stream=stream,
                    on_chunk=on_chunk,
                    on_batch_complete=on_batch_complete,
                )
            )
        except Exception as e:
            logger.error(
                "%s主角形象提取失败（不阻塞 8 维度结果）: %s",
                log_prefix, e, exc_info=True,
            )

        # 保存缓存
        if cache_enabled and final_entries:
            await self._save_cached_entries(
                cache_key,
                final_entries,
                chapters_hash,
                cache_ttl_hours,
                elapsed_seconds=time.time() - start_time,
                token_usage=total_token_usage,
                lookback=lookback,
                batch_count=batch_count,
                merged=merged,
                protagonist_profile=protagonist_profile,
                protagonist_batch_count=protagonist_batch_count,
                protagonist_merged=protagonist_merged,
            )

        return ExtractResult(
            entries=final_entries,
            status="completed",
            elapsed_seconds=time.time() - start_time,
            token_usage=total_token_usage,
            batch_count=batch_count,
            merged=merged,
            protagonist_profile=protagonist_profile,
            protagonist_batch_count=protagonist_batch_count,
            protagonist_merged=protagonist_merged,
        )

    async def extract(
        self,
        project: Project | None,
        chapters: list[Chapter],
        current_chapter: Chapter | None,
        force_refresh: bool = False,
        lookback_override: int | None = None,
        token_limit_override: int | None = None,
    ) -> ExtractResult:
        """提取上下文条目（非流式）。

        Args:
            project: 项目对象（可为 None）
            chapters: 项目所有章节
            current_chapter: 当前续写章节（None 时取最后 N 章）
            force_refresh: 强制刷新（跳过缓存）
            lookback_override: 覆盖 lookback_chapters（0=全部前文，None=用配置默认值）
            token_limit_override: 覆盖 token_limit（0=不限制，None=用配置默认值）

        Returns:
            ExtractResult 对象
        """
        return await self._extract_common(
            project=project,
            chapters=chapters,
            current_chapter=current_chapter,
            force_refresh=force_refresh,
            lookback_override=lookback_override,
            token_limit_override=token_limit_override,
            stream=False,
        )

    async def extract_streaming(
        self,
        project: Project | None,
        chapters: list[Chapter],
        current_chapter: Chapter | None,
        force_refresh: bool = False,
        lookback_override: int | None = None,
        on_chunk: Callable[[str], None] | None = None,
        token_limit_override: int | None = None,
        on_batch_complete: Callable[[list, int, int], None] | None = None,
    ) -> ExtractResult:
        """流式提取上下文（不阻塞，通过 on_chunk 回调推送进度）。

        与 :meth:`extract` 功能一致，但使用 ``stream_chat_completion``
        逐 chunk 接收响应，避免长连接超时和 UI 冻结。
        支持 token 拆分：超出限制时按章节拆分多次请求，每批完成后通过
        ``on_batch_complete`` 增量更新 UI。

        Args:
            project: 项目对象（可为 None）
            chapters: 项目所有章节
            current_chapter: 当前续写章节
            force_refresh: 强制刷新（跳过缓存）
            lookback_override: 覆盖 lookback_chapters（0=全部前文，None=用配置默认值）
            on_chunk: 每个 chunk 的回调函数（接收增量文本）
            token_limit_override: 覆盖 token_limit（0=不限制，None=用配置默认值）
            on_batch_complete: 每批完成回调（累计 entries, 批次序号, 总批数）

        Returns:
            ExtractResult 对象
        """
        return await self._extract_common(
            project=project,
            chapters=chapters,
            current_chapter=current_chapter,
            force_refresh=force_refresh,
            lookback_override=lookback_override,
            token_limit_override=token_limit_override,
            stream=True,
            on_chunk=on_chunk,
            on_batch_complete=on_batch_complete,
        )

    def build_prompt_for_preview(
        self,
        project: Project | None,
        chapters: list[Chapter],
        current_chapter: Chapter | None,
        lookback_override: int | None = None,
    ) -> str:
        """构建提取提示词（纯本地，不调用 LLM），供预览使用。

        Args:
            project: 项目对象
            chapters: 项目所有章节
            current_chapter: 当前续写章节
            lookback_override: 覆盖 lookback_chapters（0=全部前文，None=用配置默认值）

        Returns:
            填充后的提示词文本
        """
        config = self._get_extract_config(project)
        if lookback_override is not None:
            lookback = lookback_override
        else:
            lookback = int(
                config.get("lookback_chapters", DEFAULT_LOOKBACK_CHAPTERS)
            )

        if current_chapter is not None:
            target_chapters = self._get_lookback_chapters(
                chapters, current_chapter, lookback
            )
        else:
            sorted_chapters = sorted(chapters, key=lambda c: c.index)
            target_chapters = (
                sorted_chapters[-lookback:] if lookback > 0 else sorted_chapters
            )

        return self._build_prompt(project, target_chapters, config)

    async def load_cached_entries(
        self, project_id: str, chapter_id: str
    ) -> dict[str, Any] | None:
        """加载章节的缓存提取结果（供章节切换时显示，不校验 hash）。

        Args:
            project_id: 项目 ID
            chapter_id: 章节 ID

        Returns:
            缓存 dict（含 entries/elapsed_seconds/token_usage 等），未命中返回 None
        """
        cache_key = self._build_cache_key(project_id, chapter_id)
        return await self._get_cached_data(cache_key)

    async def cancel(self) -> None:
        """取消提取请求。

        设置取消事件，通知正在进行的 LLM 调用中断。
        _cancel_event 在 __init__ 中一次性创建（threading.Event，跨线程安全），
        因此无需判空，也无需在 extract() 内重新创建（避免丢失先于 extract() 设置的取消信号）。
        """
        self._cancel_event.set()
        logger.info("已请求取消上下文提取")
