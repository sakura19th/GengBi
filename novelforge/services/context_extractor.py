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
from novelforge.models import Chapter, ContextEntry, Project
from novelforge.models.context import VALID_CATEGORIES, VALID_POSITIONS, VALID_ROLES
from novelforge.services.llm_client import (
    APIError,
    AuthError,
    LLMClient,
    LLMError,
    RateLimitError,
)
from novelforge.services.storage_service import StorageService, _generate_id
from novelforge.utils.paths import get_extract_prompt_path

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
    """

    entries: list[ContextEntry] = field(default_factory=list)
    status: str = "completed"
    error: str = ""
    elapsed_seconds: float = 0.0
    token_usage: dict[str, Any] = field(default_factory=dict)
    from_cache: bool = False
    batch_count: int = 1


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
        previous_entries: list[ContextEntry] | None = None,
    ) -> str:
        """构建提取提示词。

        优先使用 ``extractor_prompt_override``，为 null 时用默认模板。
        用宏替换填充 ``{{title}}``/``{{author}}``/``{{protagonist}}``/
        ``{{synopsis}}``/``{{world_setting}}``/``{{writing_style}}``/``{{chapters_text}}``。

        增量更新模式（``previous_entries`` 非空时）：在 prompt 末尾追加已有提取结果，
        指示 LLM 仅输出新增或修改的条目。

        Args:
            project: 项目对象
            chapters: 待提取的章节列表
            config: 提取配置
            previous_entries: 已有提取条目（增量更新模式时传入，首批为 None）

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

        # 增量更新模式：追加已有提取结果
        if previous_entries:
            prompt += self._build_incremental_section(previous_entries)

        return prompt

    def _build_incremental_section(
        self, previous_entries: list[ContextEntry]
    ) -> str:
        """构建增量更新指令段（附在 prompt 末尾）。

        将已有条目精简为 ``uid``/``category``/``content``（content 截断 200 字）的
        JSON 数组，并指示 LLM 仅输出新增或修改的条目。

        Args:
            previous_entries: 已有提取条目

        Returns:
            增量更新指令段文本
        """
        condensed = []
        for e in previous_entries:
            content = (e.content or "")[:200]
            condensed.append({
                "uid": e.uid,
                "category": e.category,
                "content": content,
            })
        previous_json = json.dumps(condensed, ensure_ascii=False, indent=2)

        return (
            "\n\n# 增量更新模式（重要）\n"
            "以下是基于前序章节已提取的条目。请基于本次新增章节内容，"
            "仅输出需要**新增**或**修改**的条目：\n"
            "- 保持已有 uid 表示修改该条目（更新 content 以反映新章节带来的变化）\n"
            "- 使用新 uid 表示新增条目\n"
            "- **无需重复输出未发生变化的条目**\n"
            "- 若本次新增章节未带来任何新信息或变化，可输出空数组 `[]`\n\n"
            f"已有条目：\n```json\n{previous_json}\n```"
        )

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
    ) -> list[ContextEntry] | None:
        """从缓存读取提取结果，并校验 chapters_hash 是否匹配。

        Args:
            cache_key: 缓存 key
            current_chapters_hash: 当前目标章节的内容哈希

        Returns:
            ContextEntry 列表（hash 匹配时），不匹配或未命中返回 None
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
        return data.get("entries", [])

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
            }
            await self.storage_service.storage.set_cache(
                cache_key, data, ttl_hours=ttl_hours, category=CACHE_CATEGORY
            )
            logger.info(
                "已保存上下文提取缓存: %s (%d 条, TTL=%dh, 批次=%d)",
                cache_key,
                len(entries),
                ttl_hours,
                batch_count,
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
                return ExtractResult(
                    entries=cached,
                    status="completed",
                    elapsed_seconds=time.time() - start_time,
                    from_cache=True,
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

        # 逐批调用 LLM
        all_entries: list[ContextEntry] = []
        entries_by_uid: dict[str, int] = {}  # uid -> all_entries 索引（替换式合并）
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

            # 构建 prompt（仅含该批章节；增量模式附上已有条目）
            try:
                prompt = self._build_prompt(
                    project, batch_chapters, config,
                    previous_entries=all_entries if batch_idx > 0 else None,
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

            # 校验并规范化（替换式合并：相同 uid=修改，新 uid=新增）
            for raw in raw_entries:
                entry = _validate_and_normalize_entry(
                    raw, batch_range, extracted_at
                )
                if entry is not None:
                    if entry.uid in entries_by_uid:
                        # 增量更新：替换已有条目
                        idx = entries_by_uid[entry.uid]
                        all_entries[idx] = entry
                        logger.debug("增量更新条目: %s", entry.uid)
                    else:
                        # 新增条目
                        entries_by_uid[entry.uid] = len(all_entries)
                        all_entries.append(entry)

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

        logger.info(
            "%s上下文提取完成: %d 条 (%d 批, 耗时 %.2fs)",
            log_prefix,
            len(all_entries),
            batch_count,
            time.time() - start_time,
        )

        # 保存缓存
        if cache_enabled and all_entries:
            await self._save_cached_entries(
                cache_key,
                all_entries,
                chapters_hash,
                cache_ttl_hours,
                elapsed_seconds=time.time() - start_time,
                token_usage=total_token_usage,
                lookback=lookback,
                batch_count=batch_count,
            )

        return ExtractResult(
            entries=all_entries,
            status="completed",
            elapsed_seconds=time.time() - start_time,
            token_usage=total_token_usage,
            batch_count=batch_count,
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
