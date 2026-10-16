"""内容后处理模块。

统一 Agent 续写与单次续写的后处理流水线：
剥离原生 think → 正则替换 → 模板后处理 → HTML 剥离。

此前该流水线在 ``AgentOrchestrator._post_process`` 与
``ContinuationWorker.run`` 中各有一份重复实现，本模块抽取为单一来源。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from novelforge.core.regex_engine import strip_html_tags
from novelforge.models.regex import PLACEMENT_AI_OUTPUT

logger = logging.getLogger(__name__)

_HTML_PATTERN = re.compile(r"<[a-zA-Z/!]|/?[a-zA-Z_]\w*>")


def contains_html(text: str) -> bool:
    """检测 HTML 标签特征，避免破坏普通文本中的比较符。"""
    return bool(_HTML_PATTERN.search(text))

_THINKING_TAG_RE = re.compile(
    r"<novelforge_thinking>\s*(.*?)\s*</novelforge_thinking>",
    re.DOTALL,
)

# 模型原生思考标签（Qwen / 部分 OpenAI 兼容中转把思维链嵌在正文）
_NATIVE_THINK_RE = re.compile(
    r"<think>\s*(.*?)\s*</think>",
    re.DOTALL | re.IGNORECASE,
)


def extract_thinking_content(text: str) -> str:
    """从 LLM 原文中提取 ``<novelforge_thinking>`` 标签内思维链文本。

    Args:
        text: 后处理前的 LLM 输出

    Returns:
        所有标签内文本拼接；无标签或空内容时返回空串
    """
    if not text:
        return ""
    parts = [
        m.group(1).strip()
        for m in _THINKING_TAG_RE.finditer(text)
        if m.group(1).strip()
    ]
    return "\n\n".join(parts)


def extract_native_think_content(text: str) -> str:
    """从正文中提取模型原生 ``<think>...</think>`` 思维链。

    Args:
        text: LLM 输出（可能含原生 think 标签）

    Returns:
        所有 ``<think>`` 段拼接文本；无则空串
    """
    if not text:
        return ""
    parts = [m.group(1).strip() for m in _NATIVE_THINK_RE.finditer(text) if m.group(1).strip()]
    return "\n\n".join(parts)


def strip_native_think_blocks(text: str) -> str:
    """从正文删除 ``<think>...</think>`` 整段（含内部文本）。

    与 HTML 剥离不同：后者只去标签、留下思考原文。持久化前调用本函数，
    避免模型原生思维链混入章节。``raw_content`` 应保留删除前原文。
    """
    if not text:
        return text
    stripped = _NATIVE_THINK_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", stripped)


def compose_thinking_display(
    api_reasoning: str | None = None,
    xml_thinking: str | None = None,
    *,
    raw_text: str | None = None,
    empty_placeholder: str = "当前段无思维链内容",
) -> str:
    """组装思维链展示区文本（API 推理 + 预设标签 + 原生 think 标签）。

    Args:
        api_reasoning: DeepSeek/xAI 等 ``reasoning_content`` / ``reasoning``
        xml_thinking: ``<novelforge_thinking>`` 内提取的文本
        raw_text: 后处理前原文；当 api 推理为空时用于回退提取 ``<think>``
        empty_placeholder: 皆空时的占位文案

    Returns:
        拼接后的展示文本
    """
    parts: list[str] = []
    api_text = (api_reasoning or "").strip()
    # API 字段为空时，从原文回退提取原生 <think>
    if not api_text and raw_text:
        api_text = extract_native_think_content(raw_text)
    if api_text:
        parts.append(f"【模型思维链】\n{api_text}")
    xml_text = (xml_thinking or "").strip()
    if not xml_text and raw_text:
        xml_text = extract_thinking_content(raw_text)
    if xml_text:
        parts.append(f"【预设思维链】\n{xml_text}")
    if not parts:
        return empty_placeholder
    return "\n\n".join(parts)


def post_process_content(
    content: str,
    regex_engine: Any | None = None,
    template_engine: Any | None = None,
    project_id: str = "",
    chapter_metadata: dict[str, Any] | None = None,
    apply_regex: bool = True,
) -> str:
    """执行后处理流水线：剥离原生 think → 正则替换 → 模板后处理 → HTML 剥离。

    执行顺序与原 ``AgentOrchestrator._post_process`` / ``ContinuationWorker``
    内联实现保持一致，并在正则前删除 ``<think>`` 整段：

    0. 删除 ``<think>...</think>`` 整段（含内部文本，避免混入章节）
    1. 应用 ``PLACEMENT_AI_OUTPUT`` 正则替换（regex_engine 非空时）
    2. 接收后模板渲染（template_engine 非空时）
    3. 检测到 HTML 标签特征时剥离 HTML（避免破坏纯文本输出）

    每一步均用 try/except 包裹并记录日志，单步失败不影响后续步骤。

    Args:
        content: 原始文本
        regex_engine: 正则引擎（AI_OUTPUT 后处理），为 None 时跳过该步
        template_engine: 模板引擎（接收后渲染），为 None 时跳过该步
        project_id: 项目 ID（模板渲染上下文）
        chapter_metadata: 章节元数据（模板渲染上下文）
        apply_regex: 是否应用 AI_OUTPUT 正则（False 时跳过正则，仍做模板与 HTML 剥离）

    Returns:
        处理后的文本；若中途出错则返回最近一次成功处理的结果
    """
    final_content = strip_native_think_blocks(content) if content else content

    # 1. 应用 AI_OUTPUT 正则替换
    if apply_regex and regex_engine is not None and final_content:
        try:
            final_content = regex_engine.apply_to_text(
                final_content, placement=PLACEMENT_AI_OUTPUT
            )
        except Exception as e:
            logger.error("AI_OUTPUT 正则后处理失败: %s", e)

    # 2. 接收后模板渲染
    if template_engine is not None and final_content:
        try:
            rendered, error = template_engine.render_post_receive(
                final_content,
                project_id=project_id,
                chapter_metadata=chapter_metadata,
            )
            if error:
                logger.warning("接收后模板渲染错误: %s", error)
            else:
                final_content = rendered
        except Exception as e:
            logger.error("接收后模板渲染失败: %s", e)

    # 3. HTML 标签剥离
    # 仅当内容含 HTML 标签特征时才执行，避免破坏纯文本输出
    if final_content and contains_html(final_content):
        try:
            final_content = strip_html_tags(final_content)
        except Exception as e:
            logger.warning("HTML 标签剥离失败: %s", e)

    return final_content
