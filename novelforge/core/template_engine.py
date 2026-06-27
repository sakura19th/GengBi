"""Jinja2 沙箱模板引擎。

使用 ``ImmutableSandboxedEnvironment`` 实现双阶段模板执行。

核心特性：
- ``ImmutableSandboxedEnvironment``（非 ``SandboxedEnvironment``）
- 自定义 ``is_safe_attribute``：拒绝所有以 ``_`` 开头的属性
- 禁用 ``attr`` 过滤器
- 白名单函数：getvar/setvar/hasvar/delvar/get_chapter/get_chapters/
  get_current_chapter/get_chapter_count/get_book/get_protagonist/
  get_novel_profile/get_writing_style/get_context_entries/regex_apply/
  substitute_macros/now/word_count/truncate
- ``recursion_limit=50``，递归超限跳过模板用原始文本
- 子进程/线程执行模板，5 秒超时
- 超时后跳过模板使用原始文本，记录 ERROR 日志

双阶段执行：
- 发送前（render_pre_send）：扫描 {% %}/{{ }}，沙箱执行，替换
- 接收后（render_post_receive）：扫描输出模板，执行（可 setvar），替换
- 最终显示文本不含 Jinja2 语法
- 模板语法错误捕获，跳过渲染用原始文本，ERROR 日志

执行顺序：宏替换 → Jinja2 渲染 → 正则应用（不递归）
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime
from typing import Any, Callable

from jinja2 import (
    TemplateSyntaxError,
    meta,
)
from jinja2.exceptions import SecurityError, TemplateError
from jinja2.sandbox import ImmutableSandboxedEnvironment

from novelforge.core.variable_store import VariableStore

logger = logging.getLogger(__name__)

# 模板渲染超时时间（秒）
TEMPLATE_TIMEOUT_SECONDS: int = 5

# 递归深度限制
RECURSION_LIMIT: int = 50

# 白名单函数名称集合
WHITELIST_FUNCTION_NAMES: frozenset[str] = frozenset({
    "getvar", "setvar", "hasvar", "delvar",
    "get_chapter", "get_chapters", "get_current_chapter", "get_chapter_count",
    "get_book", "get_protagonist", "get_novel_profile", "get_writing_style",
    "get_context_entries", "regex_apply", "substitute_macros",
    "now", "word_count", "truncate",
})

# 模块级默认沙箱环境实例（复用以避免每次调用 _safe_is_safe_attribute 都创建新实例）
# 注意：此实例不挂载自定义 is_safe_attribute，仅用于调用默认的安全属性检查，
# 因此不会与各 TemplateEngine 实例的自定义环境产生递归。
_DEFAULT_ENV: ImmutableSandboxedEnvironment = ImmutableSandboxedEnvironment()


def _safe_is_safe_attribute(obj: Any, value: str, attr: str) -> bool:
    """自定义 is_safe_attribute：拒绝所有以 ``_`` 开头的属性。

    Args:
        obj: 对象
        value: 属性名
        attr: 属性值（未使用）

    Returns:
        是否安全访问
    """
    if value.startswith("_"):
        return False
    # 调用 ImmutableSandboxedEnvironment 的默认检查（复用模块级实例，避免重复创建）
    return _DEFAULT_ENV.is_safe_attribute(obj, value, attr)


def _word_count(text: Any) -> int:
    """计算字数（白名单函数）。

    Args:
        text: 文本

    Returns:
        字数
    """
    if not text:
        return 0
    return len(str(text))


def _truncate(text: Any, length: int = 200, suffix: str = "...") -> str:
    """截断文本（白名单函数）。

    Args:
        text: 文本
        length: 最大长度
        suffix: 截断后缀

    Returns:
        截断后的文本
    """
    if not text:
        return ""
    s = str(text)
    if len(s) <= length:
        return s
    return s[:length] + suffix


def _now(format: str = "%Y-%m-%d %H:%M:%S") -> str:
    """获取当前时间（白名单函数）。

    Args:
        format: 时间格式字符串

    Returns:
        格式化的当前时间字符串
    """
    return datetime.now().strftime(format)


def _has_jinja2_syntax(text: str) -> bool:
    """检查文本是否包含 Jinja2 语法（{% %} 或 {{ }}）。

    Args:
        text: 待检查文本

    Returns:
        是否包含 Jinja2 语法
    """
    if not text:
        return False
    return ("{%" in text) or ("{{" in text)


def _render_template_in_thread(
    env: ImmutableSandboxedEnvironment,
    template_str: str,
    context: dict[str, Any],
) -> str:
    """在线程中渲染模板（模块顶层函数，便于 pickle）。

    Args:
        env: Jinja2 沙箱环境
        template_str: 模板字符串
        context: 渲染上下文

    Returns:
        渲染后的文本

    Raises:
        TemplateError: 模板渲染错误
        Exception: 其他异常
    """
    template = env.from_string(template_str)
    return template.render(**context)


class TemplateEngine:
    """Jinja2 沙箱模板引擎。

    提供双阶段模板执行（发送前 + 接收后），使用 ImmutableSandboxedEnvironment
    保证安全性，支持子进程/线程超时执行。

    Usage::

        engine = TemplateEngine(variable_store=store)
        # 发送前渲染
        rendered = engine.render_pre_send(
            text, project_id="proj_xxx", chapter_metadata=md,
            context_entries=[], chapters=[],
        )
        # 接收后渲染
        final = engine.render_post_receive(
            ai_output, project_id="proj_xxx", chapter_metadata=md,
        )
    """

    def __init__(
        self,
        variable_store: VariableStore | None = None,
        timeout_seconds: int = TEMPLATE_TIMEOUT_SECONDS,
    ) -> None:
        """初始化模板引擎。

        Args:
            variable_store: 变量存储（None 时内部创建）
            timeout_seconds: 模板渲染超时时间（秒）
        """
        self.variable_store: VariableStore = variable_store or VariableStore()
        self.timeout_seconds: int = timeout_seconds
        self._env: ImmutableSandboxedEnvironment = self._create_environment()
        # 线程池（复用以减少创建开销）
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="template-render"
        )

    def _create_environment(self) -> ImmutableSandboxedEnvironment:
        """创建并配置 Jinja2 沙箱环境。

        配置：
        - ImmutableSandboxedEnvironment
        - 自定义 is_safe_attribute（拒绝 _ 开头属性）
        - 禁用 attr 过滤器
        - recursion_limit=50
        """
        env = ImmutableSandboxedEnvironment(
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=False,  # 小说文本不需要 HTML 转义
        )

        # 自定义 is_safe_attribute：拒绝 _ 开头属性
        env.is_safe_attribute = _safe_is_safe_attribute

        # 禁用 attr 过滤器
        def _disabled_attr(*args: Any, **kwargs: Any) -> Any:
            raise SecurityError("attr 过滤器已被禁用")

        env.filters["attr"] = _disabled_attr

        # 设置递归限制
        env.policies["sandbox.recall_limit"] = RECURSION_LIMIT

        return env

    def _build_default_context(
        self,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
        chapters: list[Any] | None = None,
        current_chapter: Any = None,
        novel_profile: Any = None,
        context_entries: list[Any] | None = None,
        macro_substitute_fn: Callable[[str], str] | None = None,
        regex_apply_fn: Callable[[str, int], str] | None = None,
    ) -> dict[str, Any]:
        """构建模板渲染的默认上下文（含白名单函数）。

        Args:
            project_id: 项目 ID
            chapter_metadata: 章节元数据
            chapters: 所有章节列表
            current_chapter: 当前章节
            novel_profile: 小说档案
            context_entries: 上下文条目列表
            macro_substitute_fn: 宏替换函数
            regex_apply_fn: 正则应用函数（签名 fn(text, placement) -> str）

        Returns:
            含白名单函数的上下文字典
        """
        # 从 VariableStore 获取变量访问函数
        var_funcs = self.variable_store.make_template_context(
            project_id=project_id, chapter_metadata=chapter_metadata
        )

        # 构建白名单函数
        def get_chapter(index: int) -> Any:
            """获取指定序号的章节。"""
            if not chapters or index < 0 or index >= len(chapters):
                return None
            return chapters[index]

        def get_chapters() -> list[Any]:
            """获取所有章节列表。"""
            return list(chapters) if chapters else []

        def get_current_chapter() -> Any:
            """获取当前章节。"""
            return current_chapter

        def get_chapter_count() -> int:
            """获取章节总数。"""
            return len(chapters) if chapters else 0

        def get_book() -> str:
            """获取小说标题。"""
            if isinstance(novel_profile, dict):
                return str(novel_profile.get("title", ""))
            return str(getattr(novel_profile, "title", "") if novel_profile else "")

        def get_protagonist() -> str:
            """获取主角姓名。"""
            if isinstance(novel_profile, dict):
                return str(novel_profile.get("protagonist", ""))
            return str(getattr(novel_profile, "protagonist", "") if novel_profile else "")

        def get_novel_profile() -> Any:
            """获取小说档案。"""
            return novel_profile

        def get_writing_style() -> str:
            """获取写作风格。"""
            if isinstance(novel_profile, dict):
                return str(novel_profile.get("writing_style", ""))
            return str(getattr(novel_profile, "writing_style", "") if novel_profile else "")

        def get_context_entries() -> list[Any]:
            """获取上下文条目列表。"""
            return list(context_entries) if context_entries else []

        def regex_apply(text: str, placement: int = 1) -> str:
            """应用正则脚本到文本。"""
            if regex_apply_fn is not None:
                return regex_apply_fn(text, placement)
            return text

        def substitute_macros(text: str) -> str:
            """宏替换（显式二次替换）。"""
            if macro_substitute_fn is not None:
                return macro_substitute_fn(text)
            return text

        # 组装上下文
        context: dict[str, Any] = {
            # 变量访问函数
            "getvar": var_funcs["getvar"],
            "setvar": var_funcs["setvar"],
            "hasvar": var_funcs["hasvar"],
            "delvar": var_funcs["delvar"],
            # 章节相关函数
            "get_chapter": get_chapter,
            "get_chapters": get_chapters,
            "get_current_chapter": get_current_chapter,
            "get_chapter_count": get_chapter_count,
            # 小说档案函数
            "get_book": get_book,
            "get_protagonist": get_protagonist,
            "get_novel_profile": get_novel_profile,
            "get_writing_style": get_writing_style,
            # 上下文条目函数
            "get_context_entries": get_context_entries,
            # 正则与宏函数
            "regex_apply": regex_apply,
            "substitute_macros": substitute_macros,
            # 工具函数
            "now": _now,
            "word_count": _word_count,
            "truncate": _truncate,
        }

        # 仅保留 WHITELIST_FUNCTION_NAMES 中的函数，防止误将未授权函数暴露给模板
        context = {
            name: fn for name, fn in context.items()
            if name in WHITELIST_FUNCTION_NAMES
        }

        return context

    def _render_with_timeout(
        self,
        template_str: str,
        context: dict[str, Any],
    ) -> tuple[str, str | None]:
        """在线程中渲染模板，带超时保护。

        Args:
            template_str: 模板字符串
            context: 渲染上下文

        Returns:
            (渲染结果, 错误信息) 元组。成功时错误信息为 None。
            超时或错误时返回 (原始模板字符串, 错误信息)。
        """
        # 先检查模板语法
        try:
            # 使用 meta.find_undeclared_variables 检查语法
            ast = self._env.parse(template_str)
        except TemplateSyntaxError as e:
            error_msg = f"模板语法错误: {e}"
            logger.error(error_msg)
            return template_str, error_msg
        except Exception as e:
            error_msg = f"模板解析异常: {e}"
            logger.error(error_msg)
            return template_str, error_msg

        # 在线程中渲染，带超时
        try:
            future = self._executor.submit(
                _render_template_in_thread,
                self._env,
                template_str,
                context,
            )
            result = future.result(timeout=self.timeout_seconds)
            return result, None
        except FuturesTimeoutError:
            error_msg = (
                f"模板渲染超时（{self.timeout_seconds}秒），已使用原始文本"
            )
            logger.error(error_msg)
            return template_str, error_msg
        except TemplateSyntaxError as e:
            error_msg = f"模板语法错误: {e}"
            logger.error(error_msg)
            return template_str, error_msg
        except RecursionError:
            error_msg = f"模板递归超限（limit={RECURSION_LIMIT}），已使用原始文本"
            logger.error(error_msg)
            return template_str, error_msg
        except SecurityError as e:
            error_msg = f"模板安全错误: {e}，已使用原始文本"
            logger.error(error_msg)
            return template_str, error_msg
        except TemplateError as e:
            error_msg = f"模板渲染错误: {e}"
            logger.error(error_msg)
            return template_str, error_msg
        except Exception as e:
            error_msg = f"模板渲染异常: {e}"
            logger.error(error_msg, exc_info=True)
            return template_str, error_msg

    # ===== 双阶段模板执行 =====

    def _render(
        self,
        text: str,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
        chapters: list[Any] | None = None,
        current_chapter: Any = None,
        novel_profile: Any = None,
        context_entries: list[Any] | None = None,
        macro_substitute_fn: Callable[[str], str] | None = None,
        regex_apply_fn: Callable[[str, int], str] | None = None,
    ) -> tuple[str, str | None]:
        """双阶段共享的模板渲染实现。

        扫描 ``{% %}`` 和 ``{{ }}`` 块，在沙箱中执行，用执行结果替换原块。
        无 Jinja2 语法时直接返回原文本。
        ``render_pre_send`` 与 ``render_post_receive`` 的实现完全一致，
        仅语义不同（发送前/接收后），故抽取此私有方法消除重复。

        Args:
            text: 待渲染文本
            project_id: 项目 ID
            chapter_metadata: 章节元数据
            chapters: 所有章节列表
            current_chapter: 当前章节
            novel_profile: 小说档案
            context_entries: 上下文条目列表
            macro_substitute_fn: 宏替换函数
            regex_apply_fn: 正则应用函数

        Returns:
            (渲染后文本, 错误信息) 元组。成功时错误信息为 None。
            无 Jinja2 语法时直接返回原文本。
        """
        if not text or not _has_jinja2_syntax(text):
            return text, None

        context = self._build_default_context(
            project_id=project_id,
            chapter_metadata=chapter_metadata,
            chapters=chapters,
            current_chapter=current_chapter,
            novel_profile=novel_profile,
            context_entries=context_entries,
            macro_substitute_fn=macro_substitute_fn,
            regex_apply_fn=regex_apply_fn,
        )

        return self._render_with_timeout(text, context)

    def render_pre_send(
        self,
        text: str,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
        chapters: list[Any] | None = None,
        current_chapter: Any = None,
        novel_profile: Any = None,
        context_entries: list[Any] | None = None,
        macro_substitute_fn: Callable[[str], str] | None = None,
        regex_apply_fn: Callable[[str, int], str] | None = None,
    ) -> tuple[str, str | None]:
        """发送前模板渲染。

        扫描 ``{% %}`` 和 ``{{ }}`` 块，在沙箱中执行，用执行结果替换原块。
        执行顺序：宏替换 → Jinja2 渲染 → 正则应用（不递归）。
        本方法仅负责 Jinja2 渲染，宏替换和正则应用由调用方在外层处理。

        Args:
            text: 待渲染文本
            project_id: 项目 ID
            chapter_metadata: 章节元数据
            chapters: 所有章节列表
            current_chapter: 当前章节
            novel_profile: 小说档案
            context_entries: 上下文条目列表
            macro_substitute_fn: 宏替换函数
            regex_apply_fn: 正则应用函数

        Returns:
            (渲染后文本, 错误信息) 元组。成功时错误信息为 None。
            无 Jinja2 语法时直接返回原文本。
        """
        return self._render(
            text,
            project_id=project_id,
            chapter_metadata=chapter_metadata,
            chapters=chapters,
            current_chapter=current_chapter,
            novel_profile=novel_profile,
            context_entries=context_entries,
            macro_substitute_fn=macro_substitute_fn,
            regex_apply_fn=regex_apply_fn,
        )

    def render_post_receive(
        self,
        text: str,
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
        chapters: list[Any] | None = None,
        current_chapter: Any = None,
        novel_profile: Any = None,
        context_entries: list[Any] | None = None,
        macro_substitute_fn: Callable[[str], str] | None = None,
        regex_apply_fn: Callable[[str, int], str] | None = None,
    ) -> tuple[str, str | None]:
        """接收后模板渲染。

        扫描 AI 输出中的 ``{% %}`` 和 ``{{ }}`` 块，在沙箱中执行
        （可调用 setvar 更新变量），用执行结果替换原块。
        最终显示给用户的文本不含 Jinja2 语法。

        Args:
            text: AI 输出文本
            project_id: 项目 ID
            chapter_metadata: 章节元数据
            chapters: 所有章节列表
            current_chapter: 当前章节
            novel_profile: 小说档案
            context_entries: 上下文条目列表
            macro_substitute_fn: 宏替换函数
            regex_apply_fn: 正则应用函数

        Returns:
            (渲染后文本, 错误信息) 元组。成功时错误信息为 None。
            无 Jinja2 语法时直接返回原文本。
        """
        return self._render(
            text,
            project_id=project_id,
            chapter_metadata=chapter_metadata,
            chapters=chapters,
            current_chapter=current_chapter,
            novel_profile=novel_profile,
            context_entries=context_entries,
            macro_substitute_fn=macro_substitute_fn,
            regex_apply_fn=regex_apply_fn,
        )

    def render_template(
        self,
        template_str: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """渲染单个模板字符串（通用接口）。

        Args:
            template_str: 模板字符串
            context: 额外的渲染上下文（与默认上下文合并）

        Returns:
            (渲染后文本, 错误信息) 元组。
        """
        if not template_str or not _has_jinja2_syntax(template_str):
            return template_str, None

        # 合并默认上下文（空参数）和额外上下文
        default_ctx = self._build_default_context()
        if context:
            default_ctx.update(context)

        return self._render_with_timeout(template_str, default_ctx)

    def render_messages_pre_send(
        self,
        messages: list[dict[str, Any]],
        project_id: str = "",
        chapter_metadata: dict[str, Any] | None = None,
        chapters: list[Any] | None = None,
        current_chapter: Any = None,
        novel_profile: Any = None,
        context_entries: list[Any] | None = None,
        macro_substitute_fn: Callable[[str], str] | None = None,
        regex_apply_fn: Callable[[str, int], str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """对 messages 数组应用发送前模板渲染。

        对每条消息的 content 执行模板渲染。

        Args:
            messages: messages 数组
            其他参数同 render_pre_send

        Returns:
            (渲染后 messages, 错误信息列表) 元组。
            渲染失败的 message 保留原文本。
        """
        if not messages:
            return list(messages), []

        errors: list[str] = []
        result: list[dict[str, Any]] = []
        for msg in messages:
            new_msg = dict(msg)
            content = new_msg.get("content", "")
            if isinstance(content, str) and _has_jinja2_syntax(content):
                rendered, error = self.render_pre_send(
                    content,
                    project_id=project_id,
                    chapter_metadata=chapter_metadata,
                    chapters=chapters,
                    current_chapter=current_chapter,
                    novel_profile=novel_profile,
                    context_entries=context_entries,
                    macro_substitute_fn=macro_substitute_fn,
                    regex_apply_fn=regex_apply_fn,
                )
                new_msg["content"] = rendered
                if error:
                    errors.append(f"消息渲染错误: {error}")
            result.append(new_msg)
        return result, errors

    def shutdown(self) -> None:
        """关闭模板引擎，释放线程池资源。"""
        self._executor.shutdown(wait=False, cancel_futures=True)
        logger.info("模板引擎已关闭")

    @property
    def environment(self) -> ImmutableSandboxedEnvironment:
        """获取 Jinja2 沙箱环境（用于测试）。"""
        return self._env

    @property
    def whitelist_functions(self) -> frozenset[str]:
        """获取白名单函数名集合。"""
        return WHITELIST_FUNCTION_NAMES
