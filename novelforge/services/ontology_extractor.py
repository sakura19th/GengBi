"""世界观底层提取器。

实现底层世界观元描述（World Ontology Layer）的提取：
- 取小说全文按 token 限制拆分批次
- 每批次调用 LLM 提取 7 大维度参数化描述
- 多批次时通过【信息汇总】环节语义整合为最终 WorldOntology
- 提取结果固化为 Project.world_ontology，并拆分为 7 个 ContextEntry 存入项目世界书

三大核心机制（镜像 ContextExtractor）：
1. 按 token 自动拆分：超 token_limit 时按章节边界拆分批次
2. 增量更新：每批次提取时携带前一批的 accumulated WorldOntology 作为参考
3. 合并功能：batch_count > 1 时调用 _run_ontology_merge 语义整合
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
from datetime import datetime
from typing import Any, Callable

from novelforge.core.config import ConfigManager
from novelforge.core.json_utils import strip_markdown_fences
from novelforge.core.token_counter import TokenCounter
from novelforge.models import Chapter, ContextEntry, Project
from novelforge.models.ontology import WorldOntology
from novelforge.services.llm_client import (
    APIError,
    AuthError,
    LLMClient,
    LLMError,
    RateLimitError,
)
from novelforge.services.storage_service import StorageService, _generate_id
from novelforge.utils.paths import (
    get_extract_ontology_merge_prompt_path,
    get_extract_ontology_prompt_path,
)

logger = logging.getLogger(__name__)

# 提取请求的 max_tokens（足够返回 7 维度 JSON）
ONTOLOGY_EXTRACT_MAX_TOKENS = 8000

# 提取温度（低温保证稳定输出）
ONTOLOGY_EXTRACT_TEMPERATURE = 0.2

# 提取温度（重试，温度归零确保稳定）
ONTOLOGY_EXTRACT_TEMPERATURE_RETRY = 0.0

# 合并温度（首次尝试）
ONTOLOGY_MERGE_TEMPERATURE = 0.2

# 合并温度（重试，温度归零确保稳定）
ONTOLOGY_MERGE_TEMPERATURE_RETRY = 0.0

# WorldOntology 7 大维度字段名
ONTOLOGY_DIMENSIONS: tuple[str, ...] = (
    "existential_topology",
    "causal_architecture",
    "spatio_temporal_ontology",
    "information_epistemology",
    "axiological_foundation",
    "becoming_dynamics",
    "narrative_ontology",
)

# 7 大维度中文标签（用于世界书条目 comment 字段）
_DIMENSION_LABELS: dict[str, str] = {
    "existential_topology": "存在拓扑",
    "causal_architecture": "因果架构",
    "spatio_temporal_ontology": "时空本体论",
    "information_epistemology": "信息与认识论",
    "axiological_foundation": "价值论基础",
    "becoming_dynamics": "生成动力学",
    "narrative_ontology": "叙事本体论",
}


def _hash_content(content: str) -> str:
    """计算文本内容的 MD5 哈希（用于 token 缓存 key）。

    Args:
        content: 文本内容

    Returns:
        32 位 hex 哈希字符串
    """
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _safe_serialize(value: Any) -> str:
    """安全序列化值为字符串（用于长度比较）。

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


def _filter_dimensions(data: dict[str, Any]) -> dict[str, Any]:
    """过滤字典，仅保留 7 大维度字段，非 dict 值替换为空 dict。

    Args:
        data: 原始字典

    Returns:
        仅含 7 大维度字段的字典
    """
    result: dict[str, Any] = {}
    for dim in ONTOLOGY_DIMENSIONS:
        val = data.get(dim)
        if isinstance(val, dict):
            result[dim] = val
        else:
            result[dim] = {}
    return result


def _parse_ontology_response(content: str) -> dict[str, Any]:
    """解析 LLM 提取响应为 WorldOntology 字典。

    尝试直接 ``json.loads``，失败时去除 markdown 代码块标记后重试。
    仅保留 7 大维度字段，忽略其他字段。

    Args:
        content: LLM 返回的文本内容

    Returns:
        包含 7 大维度字段的字典

    Raises:
        json.JSONDecodeError: 解析失败
    """
    content = content.strip()
    # 第一次尝试：直接解析
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return _filter_dimensions(data)
        raise json.JSONDecodeError(
            f"期望 JSON 对象，实际类型: {type(data).__name__}", content, 0
        )
    except json.JSONDecodeError:
        pass

    # 第二次尝试：去除 markdown 代码块标记
    cleaned = strip_markdown_fences(content)
    data = json.loads(cleaned)
    if isinstance(data, dict):
        return _filter_dimensions(data)
    raise json.JSONDecodeError(
        f"期望 JSON 对象，实际类型: {type(data).__name__}", content, 0
    )


class OntologyExtractor:
    """世界观底层提取器。

    全文拆分分析，提取 WorldOntology 7 大维度参数化描述。
    提取流程：全文按 token 拆分批次 → 逐批增量提取 → 合并 → 存储为世界书条目绑定到项目。

    三大核心机制（镜像 ContextExtractor）：
    1. 按 tokens 自动拆分：超 token_limit 时按章节边界拆分批次
    2. 增量更新：每批次提取时携带前一批的 accumulated WorldOntology 作为参考
    3. 合并功能：batch_count > 1 时调用 _run_ontology_merge 语义整合
    """

    def __init__(
        self,
        storage_service: StorageService,
        config_manager: ConfigManager,
        token_counter: TokenCounter,
    ) -> None:
        """初始化世界观底层提取器。

        Args:
            storage_service: 存储服务（用于项目保存）
            config_manager: 配置管理器（用于获取 API 端点）
            token_counter: token 计数器（用于按 token 拆分）
        """
        self.storage_service = storage_service
        self.config_manager = config_manager
        self.token_counter = token_counter
        # 提示词模板缓存（首次使用时加载，OSError 时回退最小模板）
        self._ontology_prompt_template: str | None = None
        self._ontology_merge_prompt_template: str | None = None
        # token 计数缓存：(chapter_id, content_hash) -> token_count
        # 避免同一章节内容在多次调用时重复计数（tiktoken 加载开销大）
        self._token_cache: dict[tuple[str, str], int] = {}

    # ===== 模板加载 =====

    def _load_ontology_prompt_template(self) -> str:
        """加载底层世界观提取提示词模板（带缓存）。

        Returns:
            提示词模板字符串
        """
        if self._ontology_prompt_template is None:
            path = get_extract_ontology_prompt_path()
            try:
                self._ontology_prompt_template = path.read_text(encoding="utf-8")
            except OSError as e:
                logger.error("加载底层世界观提取提示词模板失败: %s", e)
                # 回退最小可用模板
                self._ontology_prompt_template = (
                    "请分析以下小说全文，提取底层世界观元描述（World Ontology Layer）。\n\n"
                    "# 小说档案\n标题：{{title}}\n作者：{{author}}\n"
                    "主角：{{protagonist}}\n简介：{{synopsis}}\n"
                    "世界观设定：{{world_setting}}\n写作风格：{{writing_style}}\n\n"
                    "# 前序批次累积的世界观认知（增量上下文）\n"
                    "{{accumulated_ontology}}\n\n"
                    "# 本批次章节内容\n{{chapters_text}}\n\n"
                    "请输出严格 JSON 对象，含 7 大维度字段："
                    "existential_topology/causal_architecture/"
                    "spatio_temporal_ontology/information_epistemology/"
                    "axiological_foundation/becoming_dynamics/narrative_ontology。"
                    "只输出 JSON，不要 markdown 代码块。"
                )
        return self._ontology_prompt_template

    def _load_ontology_merge_prompt_template(self) -> str:
        """加载底层世界观合并提示词模板（带缓存）。

        Returns:
            合并提示词模板字符串
        """
        if self._ontology_merge_prompt_template is None:
            path = get_extract_ontology_merge_prompt_path()
            try:
                self._ontology_merge_prompt_template = path.read_text(
                    encoding="utf-8"
                )
            except OSError as e:
                logger.error("加载底层世界观合并提示词模板失败: %s", e)
                # 回退最小可用模板
                self._ontology_merge_prompt_template = (
                    "请将以下多批次提取的 WorldOntology 合并为统一的 JSON 对象。\n\n"
                    "# 待合并的多批次提取结果\n{{entries_blocks}}\n\n"
                    "合并任务：消除跨块重复、冲突消解（以较新批次为准）、"
                    "补全与连贯、抽象纯度（禁止专有名词）。"
                    "输出严格 JSON 对象，含 7 大维度字段。"
                    "只输出 JSON，不要 markdown 代码块。"
                )
        return self._ontology_merge_prompt_template

    # ===== Prompt 构建 =====

    def _build_chapters_text(self, chapters: list[Chapter]) -> str:
        """拼接章节文本为 Markdown 格式。

        Args:
            chapters: 本批次章节列表

        Returns:
            形如 ``## {title}\n\n{content}`` 的拼接文本
        """
        parts: list[str] = []
        for ch in chapters:
            parts.append(f"## {ch.title}\n\n{ch.content}")
        return "\n\n".join(parts)

    def _build_ontology_prompt(
        self,
        project: Project,
        chapters_text: str,
        accumulated_ontology: dict | None = None,
    ) -> str:
        """构建底层世界观提取提示词。

        用宏替换填充 ``{{title}}``/``{{author}}``/``{{protagonist}}``
        /``{{synopsis}}``/``{{world_setting}}``/``{{writing_style}}``
        /``{{accumulated_ontology}}``/``{{chapters_text}}``。

        首批提取（accumulated_ontology=None 或空 dict）注入"（首批提取，无前序参考）"，
        后续批次注入累积 WorldOntology JSON。

        Args:
            project: 项目对象
            chapters_text: 本批次章节文本
            accumulated_ontology: 前序批次累积的 WorldOntology 字典（None 表示首批）

        Returns:
            填充后的提示词
        """
        template = self._load_ontology_prompt_template()

        # 获取小说档案
        profile = project.novel_profile
        title = getattr(profile, "title", "") if profile else ""
        author = getattr(profile, "author", "") if profile else ""
        protagonist = getattr(profile, "protagonist", "") if profile else ""
        synopsis = getattr(profile, "synopsis", "") if profile else ""
        world_setting = getattr(profile, "world_setting", "") if profile else ""
        writing_style = getattr(profile, "writing_style", "") if profile else ""

        # 累积 ontology 文本（首批注入提示文字）
        if not accumulated_ontology:
            accumulated_text = "（首批提取，无前序参考）"
        else:
            try:
                accumulated_text = json.dumps(
                    accumulated_ontology, ensure_ascii=False, indent=2
                )
            except (TypeError, ValueError) as e:
                logger.warning("序列化累积 ontology 失败，使用提示文字: %s", e)
                accumulated_text = "（前序累积内容序列化失败，请独立分析）"

        # 宏替换（简单字符串替换，避免依赖 MacroEngine 的 MacroContext）
        replacements = {
            "{{title}}": title,
            "{{author}}": author,
            "{{protagonist}}": protagonist,
            "{{synopsis}}": synopsis,
            "{{world_setting}}": world_setting,
            "{{writing_style}}": writing_style,
            "{{accumulated_ontology}}": accumulated_text,
            "{{chapters_text}}": chapters_text,
        }
        prompt = template
        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, value)

        return prompt

    # ===== Token 拆分 =====

    def _get_lookback_chapters(
        self,
        chapters: list[Chapter],
        current_chapter: Chapter | None,
        lookback: int,
    ) -> list[Chapter]:
        """获取前 N 章正文（含当前章节）。

        镜像 ContextExtractor._get_lookback_chapters 逻辑（不含 exclude_current）。

        Args:
            chapters: 项目所有章节（按 index 排序）
            current_chapter: 当前续写章节（None 时返回全部章节，兼容旧调用）
            lookback: 回溯章节数（0 或负数=全部前文）

        Returns:
            待提取的章节列表（旧→新，最后一条为当前章节）
        """
        # current_chapter 为 None 或 lookback <= 0：返回全部章节（兼容旧调用）
        if current_chapter is None or lookback <= 0:
            return sorted(chapters, key=lambda c: c.index)

        sorted_chapters = sorted(chapters, key=lambda c: c.index)
        current_idx = -1
        for i, ch in enumerate(sorted_chapters):
            if ch.id == current_chapter.id:
                current_idx = i
                break

        if current_idx == -1:
            # 当前章节不在列表中：返回最后 N 章
            return sorted_chapters[-lookback:] if lookback > 0 else sorted_chapters

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
            cache_key = (ch.id, _hash_content(ch.content or ""))
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

    # ===== LLM 客户端 =====

    def _get_llm_client(self, flow_key: str = "") -> tuple[LLMClient, str] | None:
        """获取 LLM 客户端与默认模型。

        从配置中读取流程端点（flow_key 非空时）或默认端点，解密 API Key，创建 LLMClient。
        端点的 reasoning_effort 一并传入 LLMClient。

        Args:
            flow_key: 流程键（如 "ontology_extraction"），空串用默认端点

        Returns:
            (LLMClient, model) 元组，配置缺失返回 None
        """
        if flow_key:
            ep = self.config_manager.get_flow_endpoint(flow_key)
        else:
            ep = self.config_manager.get_default_endpoint()
        if not ep:
            logger.error("未配置 API 端点，无法提取底层世界观")
            return None

        api_key = self.config_manager.decrypt_api_key(ep.get("id", ""))
        if not api_key:
            logger.error("API Key 无效，无法提取底层世界观")
            return None

        base_url = ep.get("base_url", "")
        if not base_url:
            logger.error("API base_url 为空，无法提取底层世界观")
            return None

        reasoning_effort = ep.get("reasoning_effort", "") or ""
        extra_payload = ep.get("extra_payload") or {}
        extra_headers = ep.get("extra_headers") or {}
        client = LLMClient(
            base_url=base_url,
            api_key=api_key,
            timeout=300,
            reasoning_effort=reasoning_effort,
            extra_payload=extra_payload,
            extra_headers=extra_headers,
        )
        model = self.config_manager.get_flow_model(flow_key)
        return (client, model)

    # ===== 字段级合并（增量更新核心） =====

    def _merge_ontology_fields(
        self, accumulated: dict, new_batch: dict
    ) -> dict:
        """程序化字段级合并（增量更新核心）。

        对 WorldOntology 7 大维度逐字段合并：
        - 空字段：取另一侧
        - 非空：取更完整描述（按序列化长度启发式）
        - 冲突：新批次优先（后文更能反映世界全貌）

        Args:
            accumulated: 已累积的 WorldOntology 字典
            new_batch: 本批次提取的 WorldOntology 字典

        Returns:
            合并后的 WorldOntology 字典（含 7 大维度字段）
        """
        result: dict[str, Any] = {}
        for dim in ONTOLOGY_DIMENSIONS:
            old_val = accumulated.get(dim) or {}
            new_val = new_batch.get(dim) or {}
            # 强制 dict 类型
            if not isinstance(old_val, dict):
                old_val = {}
            if not isinstance(new_val, dict):
                new_val = {}

            if not old_val and not new_val:
                result[dim] = {}
            elif not old_val:
                result[dim] = new_val
            elif not new_val:
                result[dim] = old_val
            else:
                # 双侧非空：逐子字段合并
                # 启发式：按序列化长度比较，新值更长或相等时取新值
                # （冲突时新批次优先，新值更完整时也取新值）
                merged_dim = dict(old_val)
                for k, v in new_val.items():
                    if k not in merged_dim or not merged_dim[k]:
                        # 旧字段空 → 取新值
                        merged_dim[k] = v
                    else:
                        # 双侧均有：按序列化长度比较
                        old_str = _safe_serialize(merged_dim[k])
                        new_str = _safe_serialize(v)
                        if len(new_str) >= len(old_str):
                            # 新值更长或相等 → 取新值（冲突时新批次优先）
                            merged_dim[k] = v
                        # else: 旧值更长更完整，保留旧值
                result[dim] = merged_dim
        return result

    # ===== 【信息汇总】合并 =====

    def _build_entries_blocks(
        self,
        batch_results: list[dict],
        batch_ranges: list[tuple[int, int]],
    ) -> str:
        """构造合并提示词的 ``{{entries_blocks}}`` 占位符内容。

        每批 JSON 前加 ``## 批次 i/N（章节 X-Y）`` 标题，块间空行分隔。

        Args:
            batch_results: 各批次独立提取的 WorldOntology 字典列表
            batch_ranges: 各批次章节区间列表（与 batch_results 一一对应）

        Returns:
            拼接后的多批次文本块
        """
        total = len(batch_results)
        parts: list[str] = []
        for idx, (ontology, (start, end)) in enumerate(
            zip(batch_results, batch_ranges)
        ):
            block_json = json.dumps(ontology, ensure_ascii=False, indent=2)
            parts.append(
                f"## 批次 {idx + 1}/{total}（章节 {start}-{end}）\n\n"
                f"```json\n{block_json}\n```"
            )
        return "\n\n".join(parts)

    async def _run_ontology_merge(
        self,
        batch_results: list[dict],
        accumulated_ontology: dict,
        llm_client: LLMClient,
        model: str,
        on_chunk: Callable[[str], None] | None,
        stop_event: threading.Event | None,
        batch_ranges: list[tuple[int, int]] | None = None,
        jailbreak_text: str = "",
    ) -> dict | None:
        """合并多批次 WorldOntology 结果（【信息汇总】环节）。

        构造合并提示词（``extract_ontology_merge_prompt.txt``），调用 LLM，
        解析响应为 WorldOntology 字典。2 次重试（温度 0.2/0.0），
        失败返回 accumulated_ontology（降级）。

        Args:
            batch_results: 各批次独立提取的 WorldOntology 字典列表
            accumulated_ontology: 程序化合并的累积 ontology（降级时返回）
            llm_client: LLM 客户端
            model: 模型名
            on_chunk: 流式 chunk 回调（用于推送汇总分隔标记）
            stop_event: 取消事件
            batch_ranges: 各批次章节区间列表（None 时用占位区间）

        Returns:
            合并后的 WorldOntology 字典，失败返回 accumulated_ontology（降级）
        """
        # 过滤空批次
        non_empty_indices = [i for i, r in enumerate(batch_results) if r]
        if not non_empty_indices:
            logger.warning("底层世界观汇总：所有批次均为空，返回累积结果")
            return accumulated_ontology

        # 构造用于合并的批次列表（仅非空批次）
        if batch_ranges is None:
            # 无区间信息时用占位（批次序号 1..N）
            merge_results = [batch_results[i] for i in non_empty_indices]
            merge_ranges = [(i + 1, i + 1) for i in non_empty_indices]
        else:
            merge_results = [batch_results[i] for i in non_empty_indices]
            merge_ranges = [batch_ranges[i] for i in non_empty_indices]

        template = self._load_ontology_merge_prompt_template()
        entries_blocks = self._build_entries_blocks(merge_results, merge_ranges)
        prompt = template.replace("{{entries_blocks}}", entries_blocks)
        messages = []
        if jailbreak_text:
            messages.append({"role": "system", "content": jailbreak_text})
        messages.append({"role": "user", "content": prompt})

        # 流式分隔标记
        if on_chunk is not None:
            separator = "\n\n--- 信息汇总 ---\n\n"
            try:
                on_chunk(separator)
            except Exception as e:
                logger.warning("on_chunk 回调异常: %s", e)

        # 取消信号检查
        if stop_event is not None and stop_event.is_set():
            return accumulated_ontology

        # 2 次重试：温度 0.2 / 0.0
        temperatures = [ONTOLOGY_MERGE_TEMPERATURE, ONTOLOGY_MERGE_TEMPERATURE_RETRY]
        last_error: str = ""
        for attempt, temperature in enumerate(temperatures):
            # 取消信号检查
            if stop_event is not None and stop_event.is_set():
                return accumulated_ontology

            try:
                # 流式调用：逐 chunk 接收并推送 UI
                content_parts: list[str] = []
                async for chunk in llm_client.stream_chat_completion(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=ONTOLOGY_EXTRACT_MAX_TOKENS,
                    stop_event=stop_event,
                ):
                    if stop_event is not None and stop_event.is_set():
                        return accumulated_ontology
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

                # 解析 JSON
                merged_ontology = _parse_ontology_response(content)
                logger.info(
                    "底层世界观汇总成功: %d 批次合并 (第 %d 次尝试, 温度=%.1f)",
                    len(merge_results), attempt + 1, temperature,
                )
                return merged_ontology

            except asyncio.CancelledError:
                logger.info("底层世界观汇总被取消")
                return accumulated_ontology
            except asyncio.TimeoutError as e:
                last_error = f"超时: {e}"
                logger.warning(
                    "底层世界观汇总第 %d 次尝试超时: %s", attempt + 1, e
                )
                continue
            except (AuthError, RateLimitError, APIError, LLMError) as e:
                last_error = str(e)
                logger.warning(
                    "底层世界观汇总第 %d 次尝试 LLM 调用失败: %s",
                    attempt + 1, e,
                )
                continue
            except json.JSONDecodeError as e:
                last_error = f"JSON 解析失败: {e}"
                logger.warning(
                    "底层世界观汇总第 %d 次尝试 JSON 解析失败: %s",
                    attempt + 1, e,
                )
                continue
            except Exception as e:
                last_error = f"异常: {e}"
                logger.error(
                    "底层世界观汇总第 %d 次尝试异常: %s",
                    attempt + 1, e, exc_info=True,
                )
                continue

        # 全部重试失败 → 降级返回累积结果
        logger.warning(
            "底层世界观汇总 2 次重试均失败，降级返回累积结果: %s", last_error
        )
        return accumulated_ontology

    # ===== 世界书存储 =====

    def _save_ontology_to_worldbook(
        self, project: Project, ontology: WorldOntology
    ) -> str:
        """保存 WorldOntology 为世界书条目，返回 worldbook_id。

        - 若 ``project.worldbook_id`` 为空，创建新世界书命名为 "{项目名}-世界观底层"
        - 拆分 7 维度为 7 个 ContextEntry（category="plot_state"，
          content=维度 JSON 字符串）
        - 当前实现简化：仅生成 worldbook_id 与构造内存条目，
          实际世界书持久化由后续 UI 层接入 WorldBookService 完成后落地

        Args:
            project: 项目对象
            ontology: 底层世界观元描述

        Returns:
            worldbook_id（项目世界书 ID）
        """
        worldbook_id = project.worldbook_id
        if not worldbook_id:
            worldbook_id = _generate_id("wb_")
            project.worldbook_id = worldbook_id
            logger.info(
                "为项目 %s 创建世界书: id=%s, name=%s-世界观底层",
                project.id, worldbook_id, project.name,
            )

        # 拆分 7 维度为 7 个 ContextEntry（category="plot_state"）
        entries: list[ContextEntry] = []
        for dim in ONTOLOGY_DIMENSIONS:
            dim_value = getattr(ontology, dim, {}) or {}
            if not dim_value:
                # 跳过空维度
                continue
            try:
                content = json.dumps(dim_value, ensure_ascii=False)
            except (TypeError, ValueError) as e:
                logger.warning("维度 %s 序列化失败，跳过: %s", dim, e)
                continue
            entry = ContextEntry(
                uid=f"{worldbook_id}_{dim}",
                category="plot_state",
                key=[dim],
                comment=_DIMENSION_LABELS.get(dim, dim),
                content=content,
                order=100,
                position="before",
                depth=4,
                role="system",
                source_chapter_range=ontology.source_chapter_range,
                extracted_at=ontology.extracted_at,
            )
            entries.append(entry)

        logger.info(
            "已生成世界观底层世界书条目: worldbook_id=%s, %d 维度",
            worldbook_id, len(entries),
        )
        # 注：实际世界书持久化由 WorldBookService 接入后落地
        # 当前实现仅返回 worldbook_id，条目在内存构造完成供后续调用方使用
        return worldbook_id

    # ===== 主流程：流式提取 =====

    async def extract_ontology_streaming(
        self,
        project: Project,
        chapters: list[Chapter],
        token_limit: int = 0,
        on_chunk: Callable[[str], None] | None = None,
        on_batch_complete: Callable[[int, int], None] | None = None,
        stop_event: threading.Event | None = None,
        jailbreak_text: str = "",
        current_chapter: Chapter | None = None,
        lookback: int = 0,
    ) -> tuple[WorldOntology | None, str]:
        """流式提取世界观底层。

        流程：
        1. 按 lookback 过滤章节（current_chapter 为 None 或 lookback<=0 时取全部）
        2. 按 token_limit 拆分章节为多批次
        3. 逐批：构建含 ``{{accumulated_ontology}}`` 的提示词
           （首批注入"（首批提取，无前序参考）"），调用 LLM，解析 JSON → dict，
           通过 ``_merge_ontology_fields`` 合并到 accumulated_ontology
        4. 若 batch_count > 1：调用 ``_run_ontology_merge`` 语义整合
        5. 构建 WorldOntology（含 extracted_at 与 source_chapter_range）
        6. 保存到 ``Project.world_ontology`` + 世界书条目
        7. 返回 (WorldOntology, 状态消息)

        Args:
            project: 项目对象
            chapters: 待分析的章节列表（按 index 排序）
            token_limit: 每批 token 上限（0=不限制）
            on_chunk: 状态/进度文本回调（批次分隔标记、汇总内容等）
            on_batch_complete: 批次完成回调（当前批次序号, 总批次数）
            stop_event: 取消事件（threading.Event 跨线程安全）
            jailbreak_text: 破限文本（作为 system 消息前置到 messages 开头）
            current_chapter: 当前续写章节（None 时取全部章节，兼容旧调用）
            lookback: 回溯章节数（0=全部前文，>0=取前 N 章含当前章节）

        Returns:
            (WorldOntology | None, 状态消息) 元组：
            - 成功：(WorldOntology, 成功消息)
            - 失败：(None, 错误消息)
            - 取消：(None, "用户取消提取")
        """
        # 0 章时跳过提取
        if not chapters:
            logger.info("无章节可提取底层世界观")
            return None, "无章节可提取"

        # 获取 LLM 客户端
        client_info = self._get_llm_client("ontology_extraction")
        if client_info is None:
            return None, "未配置 API 端点或 API Key 无效"
        client, model = client_info

        try:
            # 按 lookback 过滤章节（current_chapter 为 None 或 lookback<=0 时取全部）
            sorted_chapters = self._get_lookback_chapters(
                chapters, current_chapter, lookback
            )

            # 按 token 限制拆分批次
            batches = self._split_chapters_by_token_limit(
                sorted_chapters, token_limit, model
            )
            batch_count = len(batches)
            logger.info(
                "底层世界观提取: %d 章过滤为 %d 章 (lookback=%d), 拆分为 %d 批 (token_limit=%d)",
                len(chapters), len(sorted_chapters), lookback, batch_count, token_limit,
            )

            # 逐批调用 LLM，每批携带前批累积的 WorldOntology
            accumulated_ontology: dict[str, Any] = {}
            batch_results: list[dict[str, Any]] = []  # 各批次独立结果，供汇总环节使用
            batch_ranges: list[tuple[int, int]] = []  # 各批次章节区间

            for batch_idx, batch_chapters in enumerate(batches):
                # 取消信号检查
                if stop_event is not None and stop_event.is_set():
                    logger.info("底层世界观提取被取消（批次 %d/%d 前）",
                                batch_idx + 1, batch_count)
                    return None, "用户取消提取"

                batch_start = min(ch.index for ch in batch_chapters)
                batch_end = max(ch.index for ch in batch_chapters)
                batch_ranges.append((batch_start, batch_end))

                # 多批次时插入分隔标记
                if batch_count > 1 and on_chunk is not None:
                    separator = (
                        f"\n\n--- 批次 {batch_idx + 1}/{batch_count} "
                        f"(章节 {batch_start}-{batch_end}) ---\n\n"
                    )
                    try:
                        on_chunk(separator)
                    except Exception as e:
                        logger.warning("on_chunk 回调异常: %s", e)

                # 构建提示词（首批 accumulated=None → 注入"（首批提取，无前序参考）"）
                chapters_text = self._build_chapters_text(batch_chapters)
                try:
                    prompt = self._build_ontology_prompt(
                        project, chapters_text,
                        accumulated_ontology=accumulated_ontology or None,
                    )
                except Exception as e:
                    logger.error("构建底层世界观提示词失败: %s", e, exc_info=True)
                    return None, f"构建提示词失败: {e}"

                # 调用 LLM（2 次尝试：温度 0.2 / 0.0，仅 2 次均失败才中止整体提取）
                messages = []
                if jailbreak_text:
                    messages.append({"role": "system", "content": jailbreak_text})
                messages.append({"role": "user", "content": prompt})
                extract_temperatures = [
                    ONTOLOGY_EXTRACT_TEMPERATURE,
                    ONTOLOGY_EXTRACT_TEMPERATURE_RETRY,
                ]
                batch_ontology: dict[str, Any] | None = None
                last_error = ""

                for attempt, temperature in enumerate(extract_temperatures):
                    # 取消信号检查
                    if stop_event is not None and stop_event.is_set():
                        return None, "用户取消提取"
                    try:
                        # 流式调用：逐 chunk 接收并推送 UI
                        content_parts: list[str] = []
                        async for chunk in client.stream_chat_completion(
                            messages=messages,
                            model=model,
                            temperature=temperature,
                            max_tokens=ONTOLOGY_EXTRACT_MAX_TOKENS,
                            stop_event=stop_event,
                        ):
                            if stop_event is not None and stop_event.is_set():
                                return None, "用户取消提取"
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

                        # 解析 JSON → dict（仅保留 7 大维度字段）
                        batch_ontology = _parse_ontology_response(content)
                        break  # 成功，退出重试循环
                    except asyncio.CancelledError:
                        logger.info(
                            "底层世界观提取被取消（批次 %d/%d 第 %d 次尝试）",
                            batch_idx + 1, batch_count, attempt + 1,
                        )
                        return None, "用户取消提取"
                    except json.JSONDecodeError as e:
                        last_error = (
                            f"批次 {batch_idx + 1}/{batch_count} JSON 解析失败: {e}"
                        )
                        logger.warning(
                            "批次 %d/%d 第 %d 次尝试 JSON 解析失败: %s, content=%s",
                            batch_idx + 1, batch_count, attempt + 1, e, content[:200],
                        )
                        if attempt < len(extract_temperatures) - 1:
                            continue
                        return None, last_error
                    except asyncio.TimeoutError as e:
                        last_error = f"批次 {batch_idx + 1}/{batch_count} 超时: {e}"
                        logger.warning(
                            "批次 %d/%d 第 %d 次尝试超时: %s",
                            batch_idx + 1, batch_count, attempt + 1, e,
                        )
                        if attempt < len(extract_temperatures) - 1:
                            continue
                        return None, last_error
                    except (AuthError, RateLimitError, APIError, LLMError) as e:
                        last_error = f"批次 {batch_idx + 1}/{batch_count} LLM 调用失败: {e}"
                        logger.warning(
                            "批次 %d/%d 第 %d 次尝试 LLM 调用失败: %s",
                            batch_idx + 1, batch_count, attempt + 1, e,
                        )
                        if attempt < len(extract_temperatures) - 1:
                            continue
                        return None, last_error
                    except Exception as e:
                        last_error = f"批次 {batch_idx + 1}/{batch_count} 提取异常: {e}"
                        logger.error(
                            "批次 %d/%d 第 %d 次尝试异常: %s",
                            batch_idx + 1, batch_count, attempt + 1, e, exc_info=True,
                        )
                        if attempt < len(extract_temperatures) - 1:
                            continue
                        return None, last_error

                # 记录本批次独立结果（供汇总环节使用）
                # 循环仅 break（成功）或 return（失败）退出，此处 batch_ontology 必非 None
                assert batch_ontology is not None
                batch_results.append(batch_ontology)

                # 增量合并到 accumulated_ontology（核心：增量更新）
                accumulated_ontology = self._merge_ontology_fields(
                    accumulated_ontology, batch_ontology
                )

                logger.info(
                    "批次 %d/%d 完成: 章节 %d-%d (累计 %d 维度非空)",
                    batch_idx + 1, batch_count,
                    batch_start, batch_end,
                    sum(1 for v in accumulated_ontology.values() if v),
                )

                # 增量更新 UI（批次完成回调）
                if on_batch_complete is not None:
                    try:
                        on_batch_complete(batch_idx + 1, batch_count)
                    except Exception as e:
                        logger.warning("on_batch_complete 回调异常: %s", e)

            # 【信息汇总】环节：batch_count > 1 时调用 _run_ontology_merge 语义整合
            merged = False
            if batch_count > 1:
                merged_ontology = await self._run_ontology_merge(
                    batch_results=batch_results,
                    accumulated_ontology=accumulated_ontology,
                    llm_client=client,
                    model=model,
                    on_chunk=on_chunk,
                    stop_event=stop_event,
                    batch_ranges=batch_ranges,
                    jailbreak_text=jailbreak_text,
                )
                if merged_ontology is not None:
                    accumulated_ontology = merged_ontology
                    merged = True
                    logger.info("底层世界观【信息汇总】环节完成")
                else:
                    # 汇总失败：降级使用累积结果（_run_ontology_merge 内部已降级处理）
                    logger.warning("底层世界观【信息汇总】环节失败，降级使用累积结果")

            # 取消信号检查
            if stop_event is not None and stop_event.is_set():
                return None, "用户取消提取"

            # 构建 WorldOntology 对象
            all_chapter_indices = [ch.index for ch in sorted_chapters]
            source_chapter_range = (
                (min(all_chapter_indices), max(all_chapter_indices))
                if all_chapter_indices else None
            )
            ontology = WorldOntology(
                **accumulated_ontology,
                extracted_at=datetime.now(),
                source_chapter_range=source_chapter_range,
            )

            # 保存到 Project.world_ontology + 世界书条目
            try:
                project.world_ontology = ontology
                worldbook_id = self._save_ontology_to_worldbook(project, ontology)
                project.worldbook_id = worldbook_id
                project.updated_at = datetime.now()
                # 协程内直接 await 异步存储层，绕过同步包装避免重入死锁
                await self.storage_service.storage.save_project(project.model_dump(mode="json"))
                logger.info(
                    "底层世界观已固化到项目 %s: worldbook_id=%s",
                    project.id, worldbook_id,
                )
            except Exception as e:
                logger.error("保存底层世界观到项目失败: %s", e, exc_info=True)
                # 保存失败仍返回 ontology（已在内存中构造完成）
                return ontology, f"提取完成但保存失败: {e}"

            # 构建状态消息
            if batch_count == 1:
                status_msg = "提取完成（1 批次）"
            elif merged:
                status_msg = f"提取完成（{batch_count} 批次，已合并）"
            else:
                status_msg = f"提取完成（{batch_count} 批次，合并失败降级使用累积结果）"

            logger.info("底层世界观提取完成: %s", status_msg)
            return ontology, status_msg
        finally:
            # 释放 aiohttp session，避免 Unclosed client session 警告
            try:
                await client.close()
            except Exception as e:
                logger.warning("关闭 LLMClient 失败: %s", e)
