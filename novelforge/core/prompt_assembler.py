"""提示词组装管线。

对齐 SillyTavern 三阶段组装流程：**排序 → 深度注入 → Token 裁剪**，
并扩展 worldInfoBefore/After marker 注入与 at_depth ContextEntry 注入。

阶段说明：
1. **排序**：按 ``prompt_order[0].order`` 排列启用的提示，marker 保留占位。
   ABSOLUTE 提示从 RELATIVE 序列中移除，改为按深度注入历史。
2. **深度注入**：对 ``injection_position == ABSOLUTE(1)`` 的提示，按
   ``injection_depth`` 从小到大、同深度按 ``injection_order`` 从大到小、
   同优先级按 role（system→user→assistant）排序，splice 插入历史数组。
   depth=N 表示插入到倒数第 N 条历史消息之前；depth=0 表示插入到最后一条之后。
3. **Token 裁剪**：总预算 = ``max_context - max_tokens - 系统提示 - 注入提示``，
   历史从新到旧填充，预算不足停止。注入提示视为不可裁剪项。
   单章超限从末尾截取（保留最新内容），至少保留当前章节。

worldInfoBefore/After marker 注入规则：
- 所有条目内容（无论 role）用 ``\\n`` 拼接为单一字符串
- 作为单条 system role 消息注入到 marker 位置
- 无条目时跳过 marker（不插入空消息）

at_depth ContextEntry 注入：
- 复用 Prompt 深度注入规则（depth 升序、同 depth 按 order 升序）
- 不参与 worldInfoBefore/After marker 拼接

chatHistory 消息 role：
- 每章作为一条消息，role 统一为 user
- content 格式为 ``{章节标题}\\n{章节正文}``
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from novelforge.core.macros import MacroContext, MacroEngine
from novelforge.core.token_counter import TokenCounter
from novelforge.models import (
    ContextEntry,
    Prompt,
    WritingPreset,
)
from novelforge.models.regex import (
    PLACEMENT_AI_OUTPUT,
    PLACEMENT_USER_INPUT,
    PLACEMENT_WORLD_INFO,
)

logger = logging.getLogger(__name__)

# injection_position 取值
INJECTION_RELATIVE: int = 0
INJECTION_ABSOLUTE: int = 1

# role 优先级映射（system → user → assistant，数字越小越优先）
_ROLE_PRIORITY: dict[str, int] = {"system": 0, "user": 1, "assistant": 2}

# 单章截断后 token 不足此值时警告
SINGLE_CHAPTER_MIN_TOKENS: int = 500

# 渲染后容忍超限比例（10%）
RENDER_TOLERANCE_RATIO: float = 0.1


@dataclass
class AssembleResult:
    """提示词组装结果。

    Attributes:
        messages: 最终的 messages 数组（发送给 LLM）
        token_usage: token 使用情况明细
        warnings: 警告信息列表（用于状态栏显示）
        count_mode: token 计数方式 (is_exact, description)
        pre_render_tokens: 渲染前（宏替换前）的 token 总数
        post_render_tokens: 渲染后（宏替换后）的 token 总数
        history_chapter_count: 实际纳入历史的章节数
        current_chapter_truncated: 当前章节是否被截断
    """

    messages: list[dict[str, Any]] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    count_mode: tuple[bool, str] = (False, "估算")
    pre_render_tokens: int = 0
    post_render_tokens: int = 0
    history_chapter_count: int = 0
    current_chapter_truncated: bool = False


def _role_sort_key(role: str) -> int:
    """获取 role 排序 key（system→user→assistant，未知 role 排最后）。"""
    return _ROLE_PRIORITY.get(role, 99)


def _build_history_message(chapter: Any) -> dict[str, Any]:
    """构建章节历史消息。

    每章作为一条 user 消息，content 格式为 ``{章节标题}\\n{章节正文}``。

    Args:
        chapter: Chapter 对象或字典

    Returns:
        消息字典
    """
    if isinstance(chapter, dict):
        title = chapter.get("title", "")
        content = chapter.get("content", "")
    else:
        title = getattr(chapter, "title", "")
        content = getattr(chapter, "content", "")
    return {"role": "user", "content": f"{title}\n{content}"}


def _sort_absolute_prompts(prompts: list[Prompt]) -> list[Prompt]:
    """对 ABSOLUTE 提示排序。

    排序规则：
    1. injection_depth 从小到大
    2. 同深度按 injection_order 从大到小
    3. 同优先级按 role（system→user→assistant）

    Args:
        prompts: ABSOLUTE 提示列表

    Returns:
        排序后的列表
    """
    return sorted(
        prompts,
        key=lambda p: (
            p.injection_depth,
            -p.injection_order,
            _role_sort_key(p.role),
        ),
    )


def _sort_at_depth_entries(entries: list[ContextEntry]) -> list[ContextEntry]:
    """对 at_depth ContextEntry 排序。

    排序规则：
    1. depth 从小到大
    2. 同 depth 按 order 从小到大（统一升序）

    Args:
        entries: at_depth 条目列表

    Returns:
        排序后的列表
    """
    return sorted(entries, key=lambda e: (e.depth, e.order))


def _splice_injections_by_depth(
    history: list[dict[str, Any]],
    injections: list[tuple[int, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """按深度将注入消息 splice 插入历史数组。

    depth 语义：
    - depth=0：插入到最后一条历史消息之后（append）
    - depth=N（N>0）：插入到倒数第 N 条历史消息之前

    多个注入按排序顺序依次插入同一深度位置。
    注入按 depth 升序处理，避免索引漂移。

    Args:
        history: 历史消息数组（不会被修改）
        injections: (depth, message) 元组列表（需已排序）

    Returns:
        插入注入后的新消息数组
    """
    if not injections:
        return list(history)

    result = list(history)
    original_len = len(history)

    # 按 depth 升序分组
    grouped: dict[int, list[dict[str, Any]]] = {}
    for depth, msg in injections:
        grouped.setdefault(depth, []).append(msg)

    for depth in sorted(grouped.keys()):
        batch = grouped[depth]
        if depth <= 0:
            # depth=0：插入到末尾（最后一条历史消息之后）
            insert_idx = len(result)
        else:
            # depth=N：插入到倒数第 N 条历史消息之前
            # 原始历史中倒数第 N 条的索引 = original_len - N
            insert_idx = max(0, original_len - depth)
        # 逐个插入（保持 batch 内顺序）
        for i, msg in enumerate(batch):
            result.insert(insert_idx + i, msg)

    return result


class PromptAssembler:
    """提示词组装器。

    执行三阶段组装（排序 → 深度注入 → Token 裁剪），
    并处理 worldInfoBefore/After marker 注入与 at_depth ContextEntry 注入。

    Usage::

        assembler = PromptAssembler(token_counter, macro_engine)
        result = assembler.assemble(
            preset=preset,
            chapters=chapters,
            current_chapter=current_chapter,
            context_entries=entries,
            model="gpt-4o",
            max_context=32000,
            max_tokens=2000,
            target_words=2000,
            novel_profile=profile,
        )
        messages = result.messages
    """

    def __init__(
        self,
        token_counter: TokenCounter | None = None,
        macro_engine: MacroEngine | None = None,
        regex_engine: Any | None = None,
        template_engine: Any | None = None,
    ) -> None:
        """初始化组装器。

        Args:
            token_counter: token 计数器（None 时内部创建）
            macro_engine: 宏替换引擎（None 时内部创建）
            regex_engine: 正则引擎（None 时不应用正则）
            template_engine: 模板引擎（None 时不应用 Jinja2 渲染）
        """
        self.token_counter = token_counter or TokenCounter()
        self.macro_engine = macro_engine or MacroEngine()
        # M3 新增：正则引擎与模板引擎（可选）
        self.regex_engine = regex_engine
        self.template_engine = template_engine

    def _process_content(
        self,
        content: str,
        macro_context: MacroContext,
        placement: str,
        render_context: dict[str, Any],
        apply_regex: bool = True,
        content_label: str = "提示词",
    ) -> str:
        """处理 content（宏替换 → Jinja2 渲染 → 正则应用）。

        统一处理提示词与 ContextEntry 的 content。执行顺序（不递归）：
        1. 宏替换（{{macro}} 形式）
        2. Jinja2 渲染（{% %}/{{ }} 形式）
        3. 正则应用（按 placement 指定的位置）

        Args:
            content: 原始 content
            macro_context: 宏替换上下文
            placement: 正则应用位置（PLACEMENT_USER_INPUT / PLACEMENT_WORLD_INFO）
            render_context: 模板渲染上下文（局部变量，线程安全）
            apply_regex: 是否应用正则（False 时跳过正则步骤）
            content_label: 渲染错误日志中的内容标签（用于区分提示词/上下文条目）

        Returns:
            处理后的 content
        """
        if not content:
            return content

        # 1. 宏替换
        result = self.macro_engine.substitute(content, macro_context)

        # 2. Jinja2 渲染
        if self.template_engine is not None:
            macro_sub_fn = lambda text: self.macro_engine.substitute(text, macro_context)
            rendered, error = self.template_engine.render_pre_send(
                result,
                **render_context,
                macro_substitute_fn=macro_sub_fn,
            )
            if error:
                logger.warning("%s模板渲染错误: %s", content_label, error)
            result = rendered

        # 3. 正则应用（按 placement）
        if apply_regex and self.regex_engine is not None:
            macro_sub_fn = lambda text: self.macro_engine.substitute(text, macro_context)
            result = self.regex_engine.apply_to_text(
                result,
                placement=placement,
                macro_substitute_fn=macro_sub_fn,
            )

        return result

    def _process_context_entry_content(
        self,
        content: str,
        macro_context: MacroContext,
        render_context: dict[str, Any],
    ) -> str:
        """处理 ContextEntry content（宏替换 → Jinja2 渲染 → WORLD_INFO 正则）。

        薄包装：委托给统一的 _process_content，固定使用 PLACEMENT_WORLD_INFO
        且始终应用正则（与历史行为一致）。

        Args:
            content: 原始 content
            macro_context: 宏替换上下文
            render_context: 模板渲染上下文（局部变量，线程安全）

        Returns:
            处理后的 content
        """
        return self._process_content(
            content,
            macro_context,
            PLACEMENT_WORLD_INFO,
            render_context,
            apply_regex=True,
            content_label="上下文条目",
        )

    def assemble(
        self,
        preset: WritingPreset,
        chapters: list[Any],
        current_chapter: Any,
        context_entries: list[ContextEntry] | None = None,
        model: str = "",
        max_context: int = 32000,
        max_tokens: int = 2000,
        target_words: int = 2000,
        novel_profile: Any = None,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
        user_input: str = "",
        lookback_chapters: int = 0,
    ) -> AssembleResult:
        """组装提示词 messages。

        Args:
            preset: 写作预设
            chapters: 所有章节列表（按 index 排序）
            current_chapter: 当前续写章节
            context_entries: 上下文条目列表（worldInfoBefore/After/at_depth）
            model: 模型名（决定 tokenizer）
            max_context: 最大上下文 token 数
            max_tokens: 最大生成 token 数
            target_words: 目标续写字数（用于宏替换）
            novel_profile: 小说档案（dict 或 NovelProfile 对象）
            project_id: 项目 ID（用于模板渲染上下文）
            chapter_metadata: 章节元数据（用于模板渲染上下文）
            user_input: 用户续写指令
            lookback_chapters: 回溯章节数（0=全部前文，>0=仅取最近 N 章）

        Returns:
            AssembleResult 对象
        """
        result = AssembleResult()
        result.count_mode = self.token_counter.get_count_mode(model)
        context_entries = context_entries or []

        # 构建宏替换上下文
        macro_context = self._build_macro_context(
            current_chapter, novel_profile, target_words,
            project_id=project_id, chapter_metadata=chapter_metadata,
        )

        # M3: 构建模板渲染上下文（局部变量，线程安全；供 _process_content 使用）
        render_context = {
            "project_id": project_id,
            "chapter_metadata": chapter_metadata,
            "chapters": chapters,
            "current_chapter": current_chapter,
            "novel_profile": novel_profile,
            "context_entries": context_entries,
        }

        # M3: 对 ContextEntry content 应用 WORLD_INFO 正则
        if self.regex_engine is not None or self.template_engine is not None:
            for entry in context_entries:
                if entry.content:
                    entry.content = self._process_context_entry_content(
                        entry.content, macro_context, render_context
                    )

        # ===== 阶段 1：排序 =====
        front_prompts, back_prompts, absolute_prompts = self._stage1_sort(preset)
        logger.debug(
            "阶段1排序: front=%d, back=%d, absolute=%d",
            len(front_prompts), len(back_prompts), len(absolute_prompts),
        )

        # ===== 阶段 2：构建历史 + 深度注入 =====
        history_messages = self._build_history(
            chapters, current_chapter, lookback_chapters
        )
        at_depth_entries = [e for e in context_entries if e.position == "at_depth"]
        # 排序后 splice 插入
        # M3: 传入 macro_context 以处理 Prompt content（宏替换 → Jinja2 → 正则）
        sorted_injection_msgs = self._sort_and_merge_injections(
            absolute_prompts, at_depth_entries, macro_context, render_context
        )

        # ===== 阶段 3：Token 裁剪 =====
        # 计算系统提示和注入提示的 token 占用
        system_messages = self._build_relative_messages(
            front_prompts + back_prompts, macro_context, render_context
        )
        system_tokens = self.token_counter.count_messages(system_messages, model)
        injection_tokens = self._count_injection_tokens(
            absolute_prompts, at_depth_entries, model
        )

        # 计算 user_input 的 token 占用（非空时追加为最后一条 user 消息）
        user_input_tokens = 0
        if user_input:
            user_input_tokens = self.token_counter.count_messages(
                [{"role": "user", "content": user_input}], model
            )

        budget = (
            max_context - max_tokens - system_tokens
            - injection_tokens - user_input_tokens
        )
        if budget < 0:
            budget = 0
            result.warnings.append(
                "系统提示与注入提示已超出 Token 预算，历史将被大幅裁剪"
            )

        # 保证当前章节最低 token 预算，必要时自动降低 max_tokens
        min_context_budget = SINGLE_CHAPTER_MIN_TOKENS
        if budget < min_context_budget:
            reduced_max_tokens = (
                max_context - system_tokens - injection_tokens
                - user_input_tokens - min_context_budget
            )
            if reduced_max_tokens > 0:
                result.warnings.append(
                    f"max_tokens 已从 {max_tokens} 自动降低至 {reduced_max_tokens} "
                    f"以保证当前章节最低 {min_context_budget} token 上下文"
                )
                max_tokens = reduced_max_tokens
                budget = min_context_budget
            else:
                result.warnings.append(
                    f"Token 预算严重不足：max_context({max_context}) 不足以容纳"
                    f"系统提示({system_tokens}) + 注入({injection_tokens}) + "
                    f"最低上下文({min_context_budget})，续写质量将严重下降"
                )
                budget = max(
                    0, max_context - system_tokens - injection_tokens - user_input_tokens
                )

        # 裁剪历史（注入提示不可裁剪，仅裁剪历史消息）
        trimmed_history, trim_warnings, current_truncated = self._trim_history(
            history_messages, budget, model
        )
        result.warnings.extend(trim_warnings)
        result.current_chapter_truncated = current_truncated
        result.history_chapter_count = len(trimmed_history)

        # 重新 splice 注入到裁剪后的历史
        final_history = _splice_injections_by_depth(
            trimmed_history, sorted_injection_msgs
        )

        # ===== 阶段 4：Marker 注入（worldInfoBefore/After）=====
        world_info_before_msg = self._build_world_info_message(
            context_entries, "before"
        )
        world_info_after_msg = self._build_world_info_message(
            context_entries, "after"
        )

        # ===== 组装最终 messages =====
        messages: list[dict[str, Any]] = []

        for prompt in front_prompts:
            if prompt.marker == "worldInfoBefore":
                if world_info_before_msg is not None:
                    messages.append(world_info_before_msg)
                # 无条目时跳过 marker
                continue
            if prompt.marker == "chatHistory":
                # chatHistory marker：插入历史 + 注入
                messages.extend(final_history)
                continue
            if prompt.marker == "worldInfoAfter":
                # worldInfoAfter 在 front 中出现时也处理
                if world_info_after_msg is not None:
                    messages.append(world_info_after_msg)
                continue
            # 普通 RELATIVE 提示（M3: 宏替换 → Jinja2 → 正则）
            content = self._process_content(prompt.content, macro_context, PLACEMENT_USER_INPUT, render_context)
            if content or prompt.system_prompt:
                messages.append({"role": prompt.role, "content": content})

        for prompt in back_prompts:
            if prompt.marker == "worldInfoBefore":
                if world_info_before_msg is not None:
                    messages.append(world_info_before_msg)
                continue
            if prompt.marker == "chatHistory":
                messages.extend(final_history)
                continue
            if prompt.marker == "worldInfoAfter":
                if world_info_after_msg is not None:
                    messages.append(world_info_after_msg)
                continue
            # 普通 RELATIVE 提示（M3: 宏替换 → Jinja2 → 正则）
            content = self._process_content(prompt.content, macro_context, PLACEMENT_USER_INPUT, render_context)
            if content or prompt.system_prompt:
                messages.append({"role": prompt.role, "content": content})

        # 如果没有 chatHistory marker，仍需插入历史
        if not any(p.marker == "chatHistory" for p in front_prompts + back_prompts):
            messages.extend(final_history)

        # 用户输入指令注入（非空时追加为最后一条 user 消息）
        if user_input:
            messages.append({"role": "user", "content": user_input})

        # 计算 token 使用情况
        result.messages = messages
        result.pre_render_tokens = self.token_counter.count_messages(messages, model)

        # 宏替换后的 token（M2 阶段宏替换已在上面执行，此处记录差值）
        result.post_render_tokens = result.pre_render_tokens
        result.token_usage = {
            "max_context": max_context,
            "max_tokens": max_tokens,
            "system_tokens": system_tokens,
            "injection_tokens": injection_tokens,
            "history_budget": budget,
            "total_used": result.pre_render_tokens,
        }

        # 检查渲染后是否超限（容忍 10%）
        render_limit = int(max_context * (1 + RENDER_TOLERANCE_RATIO))
        if result.post_render_tokens > max_context:
            if result.post_render_tokens > render_limit:
                result.warnings.append(
                    f"渲染后 token ({result.post_render_tokens}) 超出 max_context "
                    f"({max_context}) 的 10% 容忍范围"
                )
            else:
                result.warnings.append(
                    f"渲染后 token ({result.post_render_tokens}) 略超 max_context "
                    f"({max_context})，在 10% 容忍范围内"
                )

        # 历史不足 1 章警告
        if result.history_chapter_count < 1:
            result.warnings.append(
                "上下文严重不足，建议增大 max_context 或减小 max_tokens"
            )

        return result

    def _build_macro_context(
        self,
        current_chapter: Any,
        novel_profile: Any,
        target_words: int,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
    ) -> MacroContext:
        """构建宏替换上下文。"""
        # 获取章节标题和序号
        if isinstance(current_chapter, dict):
            chapter_title = current_chapter.get("title", "")
            chapter_index = current_chapter.get("index", 0) + 1
        else:
            chapter_title = getattr(current_chapter, "title", "")
            chapter_index = getattr(current_chapter, "index", 0) + 1

        # 从 template_engine 获取变量操作函数（用于 ST 风格 setvar/getvar 宏）
        variable_funcs: dict[str, Any] = {}
        if self.template_engine is not None:
            try:
                from novelforge.core.variable_store import VariableStore
                var_store = VariableStore()
                variable_funcs = var_store.make_template_context(
                    project_id=project_id,
                    chapter_metadata=chapter_metadata or {},
                )
            except Exception as e:
                logger.debug("获取变量函数失败（ST 宏 setvar/getvar 将不可用）: %s", e)

        return MacroContext.from_novel_profile(
            novel_profile=novel_profile,
            chapter_title=chapter_title,
            chapter_index=chapter_index,
            target_words=target_words,
            variable_funcs=variable_funcs,
        )

    def _stage1_sort(
        self, preset: WritingPreset
    ) -> tuple[list[Prompt], list[Prompt], list[Prompt]]:
        """阶段1：按 prompt_order 排序提示。

        将启用的提示分为：
        - front_prompts: chatHistory marker 之前的提示（含 worldInfoBefore marker）
        - back_prompts: chatHistory marker 之后的提示（含 worldInfoAfter marker）
        - absolute_prompts: ABSOLUTE 提示（从 RELATIVE 序列中移除）

        Returns:
            (front, back, absolute) 元组
        """
        # 获取全局 prompt_order
        order_entries: list[tuple[str, bool]] = []
        for group in preset.prompt_order:
            if group.character_id == 100000:
                for entry in group.order:
                    order_entries.append((entry.identifier, entry.enabled))
                break

        # 构建 identifier → Prompt 映射
        prompt_map: dict[str, Prompt] = {p.identifier: p for p in preset.prompts}

        front: list[Prompt] = []
        back: list[Prompt] = []
        absolute: list[Prompt] = []
        found_chat_history = False

        for identifier, enabled in order_entries:
            prompt = prompt_map.get(identifier)
            if prompt is None:
                logger.debug("prompt_order 中的 identifier %s 不存在，跳过", identifier)
                continue

            # 同步 enabled 状态（prompt_order 优先）
            prompt.enabled = enabled

            if not enabled:
                continue

            # ABSOLUTE 提示从 RELATIVE 序列中移除
            if prompt.injection_position == INJECTION_ABSOLUTE:
                absolute.append(prompt)
                continue

            # chatHistory marker 分隔前后
            if prompt.marker == "chatHistory":
                found_chat_history = True
                front.append(prompt)  # chatHistory 也加入 front，后续处理
                continue

            if found_chat_history:
                back.append(prompt)
            else:
                front.append(prompt)

        # 如果没有 chatHistory marker，所有 RELATIVE 提示都在 front
        return front, back, absolute

    def _build_history(
        self,
        chapters: list[Any],
        current_chapter: Any,
        lookback_chapters: int = 0,
    ) -> list[dict[str, Any]]:
        """构建章节历史消息。

        每章作为一条 user 消息，content 格式为 ``{章节标题}\\n{章节正文}``。
        历史按章节顺序排列（旧→新），当前章节在最后。

        ``lookback_chapters`` 控制纳入历史的章节数：
        - 0（默认）：全部前文（从第 0 章到当前章节含）
        - >0：仅取最近 N 章（含当前章节），从末尾保留

        Args:
            chapters: 所有章节列表
            current_chapter: 当前续写章节
            lookback_chapters: 回溯章节数（0=全部前文，>0=仅取最近 N 章）

        Returns:
            历史消息列表
        """
        # 获取当前章节 ID
        if isinstance(current_chapter, dict):
            current_id = current_chapter.get("id", "")
        else:
            current_id = getattr(current_chapter, "id", "")

        # 按 index 排序
        def get_index(ch: Any) -> int:
            if isinstance(ch, dict):
                return ch.get("index", 0)
            return getattr(ch, "index", 0)

        sorted_chapters = sorted(chapters, key=get_index)

        # 找到当前章节位置，取到当前章节为止（含）
        history: list[dict[str, Any]] = []
        for ch in sorted_chapters:
            ch_id = ch.get("id", "") if isinstance(ch, dict) else getattr(ch, "id", "")
            history.append(_build_history_message(ch))
            if ch_id == current_id:
                break

        # 如果当前章节不在列表中，单独追加
        if current_id and not any(
            (ch.get("id", "") if isinstance(ch, dict) else getattr(ch, "id", ""))
            == current_id
            for ch in sorted_chapters
        ):
            history.append(_build_history_message(current_chapter))

        # 按 lookback 限制章节数（从末尾保留最近 N 章，0=全部）
        if lookback_chapters > 0 and len(history) > lookback_chapters:
            history = history[-lookback_chapters:]

        return history

    def _build_relative_messages(
        self,
        prompts: list[Prompt],
        macro_context: MacroContext,
        render_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """构建 RELATIVE 提示的消息列表（用于 token 计数）。

        Args:
            prompts: RELATIVE 提示列表
            macro_context: 宏替换上下文
            render_context: 模板渲染上下文（局部变量，线程安全）

        Returns:
            消息列表
        """
        messages: list[dict[str, Any]] = []
        for prompt in prompts:
            if prompt.marker:
                continue  # marker 不计入系统提示 token
            content = self._process_content(
                prompt.content, macro_context, PLACEMENT_USER_INPUT, render_context
            )
            if content or prompt.system_prompt:
                messages.append({"role": prompt.role, "content": content})
        return messages

    def _build_injection_messages(
        self,
        prompts: list[Prompt],
        macro_context: MacroContext,
        render_context: dict[str, Any],
    ) -> list[tuple[Prompt, dict[str, Any]]]:
        """构建 ABSOLUTE 提示的注入消息。

        Args:
            prompts: ABSOLUTE 提示列表
            macro_context: 宏替换上下文
            render_context: 模板渲染上下文（局部变量，线程安全）

        Returns:
            (Prompt, message) 元组列表
        """
        result: list[tuple[Prompt, dict[str, Any]]] = []
        for prompt in prompts:
            content = self._process_content(
                prompt.content, macro_context, PLACEMENT_USER_INPUT, render_context
            )
            if not content:
                continue  # 空内容不注入
            result.append((prompt, {"role": prompt.role, "content": content}))
        return result

    def _build_at_depth_messages(
        self,
        entries: list[ContextEntry],
    ) -> list[tuple[ContextEntry, dict[str, Any]]]:
        """构建 at_depth ContextEntry 的注入消息。

        Args:
            entries: at_depth 条目列表

        Returns:
            (ContextEntry, message) 元组列表
        """
        result: list[tuple[ContextEntry, dict[str, Any]]] = []
        for entry in entries:
            if not entry.content:
                continue
            result.append(
                (entry, {"role": entry.role, "content": entry.content})
            )
        return result

    def _sort_and_merge_injections(
        self,
        absolute_prompts: list[Prompt],
        at_depth_entries: list[ContextEntry],
        macro_context: MacroContext | None = None,
        render_context: dict[str, Any] | None = None,
    ) -> list[tuple[int, dict[str, Any]]]:
        """排序并合并 Prompt 注入与 at_depth ContextEntry 注入。

        排序规则（统一）：
        1. depth 从小到大
        2. 同 depth：Prompt 按 injection_order 从大到小，
           ContextEntry 按 order 从小到大
        3. 同优先级按 role（system→user→assistant）

        由于 Prompt 和 ContextEntry 的 order 字段语义不同（一个降序一个升序），
        分别排序后按 depth 合并。

        Args:
            absolute_prompts: ABSOLUTE 提示列表
            at_depth_entries: at_depth 条目列表
            macro_context: 宏替换上下文（M3: 用于处理 Prompt content）
            render_context: 模板渲染上下文（局部变量，线程安全）

        Returns:
            (depth, message) 元组列表（已排序）
        """
        # 排序 Prompt 注入
        sorted_prompts = _sort_absolute_prompts(absolute_prompts)
        # 排序 at_depth 条目
        sorted_entries = _sort_at_depth_entries(at_depth_entries)

        # M3: 处理 Prompt content 的辅助函数
        def process_prompt_content(prompt: Prompt) -> str:
            if macro_context is not None:
                return self._process_content(
                    prompt.content, macro_context, PLACEMENT_USER_INPUT, render_context or {}
                )
            return prompt.content

        # 按 depth 合并
        result: list[tuple[int, dict[str, Any]]] = []
        i = j = 0
        while i < len(sorted_prompts) and j < len(sorted_entries):
            p = sorted_prompts[i]
            e = sorted_entries[j]
            if p.injection_depth < e.depth:
                content = process_prompt_content(p)
                if content:
                    result.append(
                        (p.injection_depth, {"role": p.role, "content": content})
                    )
                i += 1
            elif p.injection_depth > e.depth:
                if e.content:
                    result.append((e.depth, {"role": e.role, "content": e.content}))
                j += 1
            else:
                # 同 depth：Prompt 的 injection_order 从大到小，
                # ContextEntry 的 order 从小到大
                # 这里简化处理：Prompt 优先（因为 injection_order 默认 100，
                # ContextEntry 的 order 默认也是 100，但语义不同）
                # 实际上 ST 中两者是独立注入的，我们按 Prompt 先、Entry 后处理
                content = process_prompt_content(p)
                if content:
                    result.append(
                        (p.injection_depth, {"role": p.role, "content": content})
                    )
                i += 1

        while i < len(sorted_prompts):
            p = sorted_prompts[i]
            content = process_prompt_content(p)
            if content:
                result.append(
                    (p.injection_depth, {"role": p.role, "content": content})
                )
            i += 1

        while j < len(sorted_entries):
            e = sorted_entries[j]
            if e.content:
                result.append((e.depth, {"role": e.role, "content": e.content}))
            j += 1

        return result

    def _count_injection_tokens(
        self,
        absolute_prompts: list[Prompt],
        at_depth_entries: list[ContextEntry],
        model: str,
    ) -> int:
        """计算注入提示的 token 总数。

        Args:
            absolute_prompts: ABSOLUTE 提示列表
            at_depth_entries: at_depth 条目列表
            model: 模型名

        Returns:
            token 总数
        """
        messages: list[dict[str, Any]] = []
        for p in absolute_prompts:
            if p.content:
                messages.append({"role": p.role, "content": p.content})
        for e in at_depth_entries:
            if e.content:
                messages.append({"role": e.role, "content": e.content})
        return self.token_counter.count_messages(messages, model)

    def _trim_history(
        self,
        history: list[dict[str, Any]],
        budget: int,
        model: str,
    ) -> tuple[list[dict[str, Any]], list[str], bool]:
        """Token 裁剪历史消息。

        从新到旧填充，预算不足停止。至少保留当前章节（最后一条）。
        单章超限从末尾截取（保留最新内容）。

        Args:
            history: 历史消息列表（旧→新，最后一条是当前章节）
            budget: token 预算
            model: 模型名

        Returns:
            (裁剪后历史, 警告列表, 当前章节是否被截断) 元组
        """
        if not history:
            return [], [], False

        warnings: list[str] = []
        current_chapter_truncated = False

        # 计算每条消息的 token 数
        msg_tokens = [
            self.token_counter.count_messages([msg], model) for msg in history
        ]

        # 从新到旧填充
        trimmed: list[dict[str, Any]] = []
        used_tokens = 0

        # 必须保留当前章节（最后一条）
        current_msg = history[-1]
        current_tokens = msg_tokens[-1]

        if current_tokens > budget:
            # 当前章节单独超限，从末尾截取（保留最新内容）
            current_chapter_truncated = True
            truncated_msg, truncated_tokens = self._truncate_message(
                current_msg, budget, model
            )
            trimmed.append(truncated_msg)
            used_tokens = truncated_tokens

            if truncated_tokens < SINGLE_CHAPTER_MIN_TOKENS:
                warnings.append(
                    f"当前章节截断后仅 {truncated_tokens} token（< {SINGLE_CHAPTER_MIN_TOKENS}），"
                    "建议增大 max_context 或减小 max_tokens"
                )
            warnings.append(
                f"当前章节过长，已截取末尾内容作为上下文"
            )
        else:
            trimmed.append(current_msg)
            used_tokens = current_tokens

        # 从新到旧添加更早的章节
        for i in range(len(history) - 2, -1, -1):
            msg = history[i]
            tokens = msg_tokens[i]
            if used_tokens + tokens <= budget:
                trimmed.insert(0, msg)
                used_tokens += tokens
            else:
                # 预算不足，停止
                break

        if len(trimmed) < 1:
            warnings.append(
                "Token 裁剪导致历史不足 1 章，已至少保留当前章节"
            )

        return trimmed, warnings, current_chapter_truncated

    def _truncate_message(
        self,
        msg: dict[str, Any],
        budget: int,
        model: str,
    ) -> tuple[dict[str, Any], int]:
        """截断消息内容以适应 token 预算。

        从末尾截取（保留最新内容），逐步缩减直到符合预算。

        Args:
            msg: 原始消息
            budget: token 预算
            model: 模型名

        Returns:
            (截断后消息, 截断后 token 数) 元组
        """
        content = msg.get("content", "")
        if not content:
            return msg, 0

        # 二分查找合适的截断长度
        lo, hi = 0, len(content)
        best_content = content[-1:] if content else ""
        best_tokens = self.token_counter.count_messages(
            [{"role": msg["role"], "content": best_content}], model
        )

        while lo <= hi:
            mid = (lo + hi) // 2
            if mid == 0:
                mid = 1
            truncated = content[-mid:]
            tokens = self.token_counter.count_messages(
                [{"role": msg["role"], "content": truncated}], model
            )
            if tokens <= budget:
                best_content = truncated
                best_tokens = tokens
                lo = mid + 1
            else:
                hi = mid - 1

        return {"role": msg["role"], "content": best_content}, best_tokens

    def _build_world_info_message(
        self,
        entries: list[ContextEntry],
        position: str,
    ) -> dict[str, Any] | None:
        """构建 worldInfoBefore/After marker 的注入消息。

        所有条目内容（无论 role）用 ``\\n`` 拼接为单一字符串，
        作为单条 system role 消息。无条目时返回 None（跳过 marker）。

        Args:
            entries: 所有上下文条目
            position: "before" 或 "after"

        Returns:
            消息字典，无条目时返回 None
        """
        # 过滤出指定 position 的条目（排除 at_depth）
        filtered = [
            e for e in entries
            if e.position == position and e.content
        ]
        if not filtered:
            return None

        # 按 order 升序排序
        filtered.sort(key=lambda e: e.order)

        # 用 \n 拼接所有内容
        combined = "\n".join(e.content for e in filtered)

        return {"role": "system", "content": combined}
