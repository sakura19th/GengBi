"""内容后处理模块。

统一 Agent 续写与单次续写的后处理流水线：
正则替换 → 模板后处理 → HTML 剥离。

此前该流水线在 ``AgentOrchestrator._post_process`` 与
``ContinuationWorker.run`` 中各有一份重复实现，本模块抽取为单一来源。
"""
from __future__ import annotations

import logging
from typing import Any

from novelforge.core.regex_engine import strip_html_tags
from novelforge.models.regex import PLACEMENT_AI_OUTPUT

logger = logging.getLogger(__name__)


def post_process_content(
    content: str,
    regex_engine: Any | None = None,
    template_engine: Any | None = None,
    project_id: str = "",
    chapter_metadata: dict[str, Any] | None = None,
) -> str:
    """执行后处理流水线：正则替换 → 模板后处理 → HTML 剥离。

    执行顺序与原 ``AgentOrchestrator._post_process`` / ``ContinuationWorker``
    内联实现保持一致：

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

    Returns:
        处理后的文本；若中途出错则返回最近一次成功处理的结果
    """
    # 延迟导入 _contains_html 以避免与 continuation_worker 的循环依赖
    # （continuation_worker 会在顶部导入本模块的 post_process_content）
    from novelforge.services.continuation_worker import _contains_html

    final_content = content

    # 1. 应用 AI_OUTPUT 正则替换
    if regex_engine is not None and final_content:
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
    if final_content and _contains_html(final_content):
        try:
            final_content = strip_html_tags(final_content)
        except Exception as e:
            logger.warning("HTML 标签剥离失败: %s", e)

    return final_content
