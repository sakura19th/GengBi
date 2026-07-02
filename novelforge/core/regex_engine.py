"""正则脚本引擎。

兼容 SillyTavern 正则脚本格式，修正 flag 映射并明确替换字符串语法转换。

核心功能：
- 解析 ST 格式 findRegex（``/pattern/flags``）
- flag 映射：``g`` 不映射任何 Python flag（Python 默认全局替换），
  ``i`` → IGNORECASE，``m`` → MULTILINE，``s`` → DOTALL，``u`` → UNICODE，
  ``y`` 忽略并记录日志
- 使用 ``regex`` 库（非 ``re``）编译，支持 ``\\p{Unicode}`` 与 lookbehind
- 替换字符串语法转换：``$1`` → ``\\1``，``$<name>`` → ``\\g<name>``，``{{match}}`` → ``\\g<0>``
- trimStrings 裁剪
- 替换后宏替换（substituteRegex 配置允许时）
- depth 过滤（minDepth/maxDepth，depth=0 为最新消息）
- 正则编译错误捕获，跳过脚本、ERROR 日志、不影响其他脚本
"""
from __future__ import annotations

import concurrent.futures
import logging
from typing import Any

import regex

from novelforge.models import (
    PLACEMENT_AI_OUTPUT,
    PLACEMENT_USER_INPUT,
    PLACEMENT_WORLD_INFO,
    RegexScript,
)

logger = logging.getLogger(__name__)

# 模块级共享线程池：用于执行用户定义正则替换并施加超时保护。
# CompiledRegexScript 是按脚本实例化的，使用模块级共享 executor 避免每次调用
# 都创建/销毁线程池的开销。max_workers=4 以容纳因超时仍后台运行的线程，
# 防止被灾难性回溯的长任务耗尽工作线程。
_REGEX_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# placement 常量别名（便于阅读）
PLACEMENT_USER_INPUT_VAL: int = PLACEMENT_USER_INPUT  # 1
PLACEMENT_AI_OUTPUT_VAL: int = PLACEMENT_AI_OUTPUT  # 2
PLACEMENT_WORLD_INFO_VAL: int = PLACEMENT_WORLD_INFO  # 5

# flag 字符到 regex 库 flag 的映射
# 注意：g 不映射任何 Python flag（Python re.sub/regex.sub 默认全局替换）
_FLAG_MAP: dict[str, int] = {
    "i": regex.IGNORECASE,
    "m": regex.MULTILINE,
    "s": regex.DOTALL,
    "u": regex.UNICODE,
}

# 需要忽略并记录日志的 flag
_IGNORED_FLAGS: frozenset[str] = frozenset({"y"})


def _finditer_with_timeout(
    pattern: "regex.Pattern[str]",
    text: str,
    timeout: float = 5.0,
) -> list[tuple[int, int]]:
    """带超时保护的 ``finditer``，防止用户输入的灾难性回溯正则卡死 UI。

    与 ``CompiledRegexScript._sub_with_timeout`` 一致，使用模块级共享线程池
    执行匹配并施加超时。超时则放弃等待、返回已收集的匹配位置并记录警告。

    注意：Python 线程无法被强制终止，超时后的工作线程仍会在后台继续运行。
    模块级 executor 的 max_workers 取较大值以容纳此类残留线程。

    Args:
        pattern: 已编译的正则模式
        text: 待匹配文本
        timeout: 超时秒数（默认 5 秒）

    Returns:
        匹配位置 (start, end) 列表；超时则返回部分结果
    """

    def _collect() -> list[tuple[int, int]]:
        return [(m.start(), m.end()) for m in pattern.finditer(text)]

    try:
        future = _REGEX_EXECUTOR.submit(_collect)
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.warning(
            "正则匹配超时（可能存在灾难性回溯），返回部分结果: %s",
            pattern.pattern[:100],
        )
        return []


def parse_find_regex(find_regex: str) -> tuple[str, str]:
    """解析 ST 格式的 findRegex 字符串。

    ST 格式为 ``/pattern/flags``，其中 pattern 可包含转义的 ``\\/``。
    若字符串不以 ``/`` 开头，视为无 flag 的纯 pattern。

    Args:
        find_regex: ST 格式的 findRegex 字符串

    Returns:
        (pattern, flags_string) 元组。flags_string 为空字符串表示无 flag。

    Examples::
        >>> parse_find_regex("/foo/gi")
        ('foo', 'gi')
        >>> parse_find_regex("/foo/g")
        ('foo', 'g')
        >>> parse_find_regex("foo")
        ('foo', '')
    """
    if not find_regex:
        return "", ""

    # 必须以 / 开头才能解析为 /pattern/flags 格式
    if not find_regex.startswith("/"):
        return find_regex, ""

    # 从第二个字符开始查找最后一个未转义的 /
    # 简化处理：找最后一个 /，因为 pattern 内部的 / 应该已被转义为 \/
    last_slash = find_regex.rfind("/")
    if last_slash <= 0:
        # 只有开头的 /，无闭合 /
        return find_regex[1:], ""

    pattern = find_regex[1:last_slash]
    flags = find_regex[last_slash + 1:]
    return pattern, flags


def compile_flags(flags_string: str) -> int:
    """将 ST flag 字符串编译为 regex 库的 flag 整数。

    flag 映射规则（关键修正点）：
    - ``g``：不映射任何 Python flag（Python 默认全局替换）
    - ``i`` → IGNORECASE
    - ``m`` → MULTILINE
    - ``s`` → DOTALL
    - ``u`` → UNICODE
    - ``y``：忽略并记录日志

    Args:
        flags_string: flag 字符串（如 "gi"）

    Returns:
        regex 库的 flag 整数（可按位或）
    """
    result = 0
    for flag_char in flags_string:
        if flag_char == "g":
            # g 不映射任何 Python flag
            continue
        if flag_char in _IGNORED_FLAGS:
            logger.info("正则 flag %r 被忽略（不支持）", flag_char)
            continue
        mapped = _FLAG_MAP.get(flag_char)
        if mapped is not None:
            result |= mapped
        else:
            logger.warning("未识别的正则 flag %r，已忽略", flag_char)
    return result


def convert_replace_string(replace_string: str) -> str:
    """将 ST 替换字符串转换为 Python regex 替换字符串语法。

    转换规则：
    - ``$1``、``$2`` ... → ``\\1``、``\\2`` ...
    - ``$<name>`` → ``\\g<name>``
    - ``{{match}}`` → ``\\g<0>``

    注意：``$&`` 在 ST 中也表示整个匹配，转换为 ``\\g<0>``。

    Args:
        replace_string: ST 格式的替换字符串

    Returns:
        Python regex 格式的替换字符串
    """
    if not replace_string:
        return replace_string

    result = replace_string

    # {{match}} → \g<0>（先处理，避免后续 $ 转换影响）
    result = result.replace("{{match}}", "\\g<0>")

    # $& → \g<0>（ST 也支持 $& 表示整个匹配）
    result = result.replace("$&", "\\g<0>")

    # $<name> → \g<name>（命名捕获组引用）
    # 需在 $1/$2 之前处理，避免 $< 被误判
    import re as _re
    result = _re.sub(
        r"\$<([a-zA-Z_][a-zA-Z0-9_]*)>",
        r"\\g<\1>",
        result,
    )

    # $1, $2, ... → \1, \2, ...（数字引用）
    # $10 也应正确处理（两位数）
    result = _re.sub(
        r"\$(\d+)",
        r"\\\1",
        result,
    )

    return result


def apply_trim_strings(text: str, trim_strings: list[str]) -> str:
    """对文本应用 trimStrings 裁剪。

    trimStrings 是 ST 正则的字段，用于在替换前后裁剪指定的字符串。
    本函数移除文本中所有出现的 trim_strings 子串。

    Args:
        text: 待裁剪文本
        trim_strings: 需要裁剪的字符串列表

    Returns:
        裁剪后的文本
    """
    if not trim_strings:
        return text
    result = text
    for trim_str in trim_strings:
        if trim_str:
            result = result.replace(trim_str, "")
    return result


class CompiledRegexScript:
    """预编译的正则脚本。

    在构造时编译 findRegex，捕获编译错误。
    编译失败的脚本 ``is_valid`` 为 False，应用时会被跳过。
    """

    def __init__(self, script: RegexScript) -> None:
        """初始化并编译正则脚本。

        Args:
            script: 原始 RegexScript 对象
        """
        self.script: RegexScript = script
        self.is_valid: bool = False
        self.error_message: str = ""
        self._pattern: regex.Pattern | None = None
        self._converted_replace: str = ""

        try:
            pattern_str, flags_str = parse_find_regex(script.findRegex)
            if not pattern_str:
                self.error_message = "findRegex 的 pattern 为空"
                logger.error(
                    "正则脚本 %s（%s）pattern 为空，已跳过",
                    script.id, script.scriptName,
                )
                return

            flags = compile_flags(flags_str)
            self._pattern = regex.compile(pattern_str, flags)
            self._converted_replace = convert_replace_string(
                script.replaceString
            )
            self.is_valid = True
        except regex.error as e:
            self.error_message = f"正则编译失败: {e}"
            logger.error(
                "正则脚本 %s（%s）编译失败: %s（findRegex=%r）",
                script.id, script.scriptName, e, script.findRegex,
            )
        except Exception as e:
            self.error_message = f"正则脚本初始化异常: {e}"
            logger.error(
                "正则脚本 %s（%s）初始化异常: %s",
                script.id, script.scriptName, e,
                exc_info=True,
            )

    def _sub_with_timeout(
        self,
        text: str,
        timeout: float = 5.0,
    ) -> str:
        """带超时保护的正则替换。

        用户定义的正则可能存在灾难性回溯（catastrophic backtracking），
        导致 ``_pattern.sub`` 长时间阻塞甚至卡死。使用模块级共享线程池
        执行替换并施加超时：超时则放弃等待、返回原文并记录警告。

        注意：Python 线程无法被强制终止，超时后的工作线程仍会在后台
        继续运行直至结束或进程退出（与 template_engine 的限制一致）。
        模块级 executor 的 max_workers 取较大值以容纳此类残留线程，
        避免工作线程被耗尽。

        Args:
            text: 待替换文本
            timeout: 超时秒数（默认 5 秒）

        Returns:
            替换后的文本；超时则返回原文
        """
        try:
            future = _REGEX_EXECUTOR.submit(
                self._pattern.sub, self._converted_replace, text
            )
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "正则替换超时（可能存在灾难性回溯），返回原文: %s",
                self._pattern.pattern[:100],
            )
            return text

    def apply(
        self,
        text: str,
        macro_substitute_fn: Any | None = None,
    ) -> str:
        """应用正则脚本到文本。

        执行流程：
        1. trimStrings 前置裁剪
        2. 正则替换
        3. trimStrings 后置裁剪（同一列表，前后都裁剪）
        4. 若 substituteRegex 启用，对结果做宏替换

        Args:
            text: 待处理文本
            macro_substitute_fn: 宏替换函数（substituteRegex 启用时调用），
                签名 ``fn(text) -> str``；None 时不做宏替换

        Returns:
            处理后的文本（脚本无效时返回原文本）
        """
        if not self.is_valid or self._pattern is None:
            return text

        if not text:
            return text

        # trimStrings 前置裁剪
        result = apply_trim_strings(text, self.script.trimStrings)

        # 正则替换（带超时保护，防止灾难性回溯卡死）
        try:
            result = self._sub_with_timeout(result)
        except regex.error as e:
            logger.error(
                "正则脚本 %s（%s）替换失败: %s",
                self.script.id, self.script.scriptName, e,
            )
            return text

        # trimStrings 后置裁剪
        result = apply_trim_strings(result, self.script.trimStrings)

        # substituteRegex 启用时做宏替换
        sub_flag = self.script.substituteRegex
        sub_enabled = bool(sub_flag) if isinstance(sub_flag, (bool, int)) else False
        if sub_enabled and macro_substitute_fn is not None:
            try:
                result = macro_substitute_fn(result)
            except Exception as e:
                logger.warning(
                    "正则脚本 %s 宏替换失败: %s",
                    self.script.id, e,
                )

        return result


class RegexEngine:
    """正则脚本引擎。

    管理多个正则脚本的编译与应用，支持按 placement 过滤、depth 过滤。

    Usage::

        engine = RegexEngine()
        # 编译脚本列表
        engine.compile_scripts(scripts)
        # 应用 USER_INPUT 正则到提示词 content
        processed = engine.apply_to_text(
            text, placement=PLACEMENT_USER_INPUT, depth=0,
        )
    """

    def __init__(self) -> None:
        """初始化正则引擎。"""
        # 按执行顺序排列的已编译脚本列表
        self._compiled: list[CompiledRegexScript] = []
        # 编译失败的脚本 id 集合（用于 UI 显示错误图标）
        self._failed_ids: set[str] = set()

    def compile_scripts(self, scripts: list[RegexScript]) -> None:
        """编译正则脚本列表（替换原有编译缓存）。

        编译失败的脚本会被跳过并记录，不影响其他脚本。

        Args:
            scripts: 待编译的正则脚本列表（应已按执行顺序排列）
        """
        self._compiled = []
        self._failed_ids = set()
        for script in scripts:
            compiled = CompiledRegexScript(script)
            if compiled.is_valid:
                self._compiled.append(compiled)
            else:
                self._failed_ids.add(script.id)

        logger.info(
            "正则引擎已编译 %d 个脚本（%d 个失败）",
            len(self._compiled), len(self._failed_ids),
        )

    @property
    def failed_script_ids(self) -> set[str]:
        """返回编译失败的脚本 ID 集合。"""
        return set(self._failed_ids)

    def apply_to_text(
        self,
        text: str,
        placement: int,
        depth: int = 0,
        macro_substitute_fn: Any | None = None,
    ) -> str:
        """对文本应用指定 placement 的所有正则脚本。

        脚本按编译时的顺序依次执行（前一个的输出是后一个的输入）。
        depth 过滤：仅对 depth 在 [minDepth, maxDepth] 范围内的脚本应用
        （minDepth=maxDepth=0 表示不过滤）。

        Args:
            text: 待处理文本
            placement: 应用时机（USER_INPUT=1/AI_OUTPUT=2/WORLD_INFO=5）
            depth: 当前消息的深度（0 为最新消息）
            macro_substitute_fn: 宏替换函数

        Returns:
            处理后的文本
        """
        if not text:
            return text

        result = text
        for compiled in self._compiled:
            script = compiled.script

            # placement 过滤
            if placement not in script.placement:
                continue

            # depth 过滤（minDepth=maxDepth=0 表示不过滤）
            if script.minDepth != 0 or script.maxDepth != 0:
                # depth=0 为最新消息
                if depth < script.minDepth or depth > script.maxDepth:
                    continue

            result = compiled.apply(result, macro_substitute_fn)

        return result

    def apply_to_messages(
        self,
        messages: list[dict[str, Any]],
        placement: int,
        macro_substitute_fn: Any | None = None,
    ) -> list[dict[str, Any]]:
        """对 messages 数组应用正则脚本。

        对每条消息的 content 应用对应 placement 的正则。
        depth 按消息在数组中的位置计算（最后一条为 depth=0，倒数第二为 depth=1，...）。

        Args:
            messages: messages 数组
            placement: 应用时机
            macro_substitute_fn: 宏替换函数

        Returns:
            处理后的 messages 数组（新列表，不修改原数组）
        """
        if not messages:
            return list(messages)

        result: list[dict[str, Any]] = []
        total = len(messages)
        for i, msg in enumerate(messages):
            # depth: 最后一条为 0，倒数第二为 1
            depth = total - 1 - i
            new_msg = dict(msg)
            content = new_msg.get("content", "")
            if isinstance(content, str):
                new_msg["content"] = self.apply_to_text(
                    content, placement, depth, macro_substitute_fn
                )
            result.append(new_msg)
        return result

    def apply_single_script(
        self,
        script: RegexScript,
        text: str,
        macro_substitute_fn: Any | None = None,
    ) -> tuple[str, list[tuple[int, int]]]:
        """应用单个正则脚本到文本（用于正则测试对话框）。

        Args:
            script: 待测试的正则脚本
            text: 测试文本
            macro_substitute_fn: 宏替换函数

        Returns:
            (替换后文本, 匹配位置列表) 元组。
            匹配位置列表为 (start, end) 元组列表，用于 UI 高亮。
        """
        compiled = CompiledRegexScript(script)
        if not compiled.is_valid:
            return text, []

        # 收集匹配位置（在替换前）—— 带超时保护，防止灾难性回溯卡死 UI
        pattern_str, flags_str = parse_find_regex(script.findRegex)
        matches: list[tuple[int, int]] = []
        try:
            flags = compile_flags(flags_str)
            pattern = regex.compile(pattern_str, flags)
            matches = _finditer_with_timeout(pattern, text)
        except regex.error:
            pass

        # 应用替换
        result = compiled.apply(text, macro_substitute_fn)
        return result, matches

    def clear(self) -> None:
        """清空已编译的脚本。"""
        self._compiled = []
        self._failed_ids = set()


def strip_html_tags(text: str) -> str:
    """移除文本中的所有 HTML/XML 标签，保留纯文本内容。

    用于将正则后处理产生的 HTML（如 TGbreak 预设的卡片/折叠块）
    转换为适合 QPlainTextEdit 显示的纯文本。

    处理规则：
    - 移除所有 ``<tag ...>`` 和 ``</tag>`` 标签
    - 保留 ``<style>``、``<script>`` 标签内的内容会被整体移除
    - 将 ``<br>``、``<br/>`` 转为换行
    - 将 ``&nbsp;`` 转为空格
    - 将 ``&lt;``、``&gt;``、``&amp;`` 转为对应字符

    Args:
        text: 含 HTML/XML 标签的文本

    Returns:
        纯文本内容
    """
    if not text:
        return text

    import re

    result = text

    # 移除 <style>...</style> 和 <script>...</script> 块（含内容）
    result = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", result, flags=re.IGNORECASE)
    result = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", result, flags=re.IGNORECASE)

    # HTML 实体转换
    result = result.replace("&nbsp;", " ")
    result = result.replace("&lt;", "<")
    result = result.replace("&gt;", ">")
    result = result.replace("&amp;", "&")
    result = result.replace("&quot;", '"')
    result = result.replace("&#39;", "'")

    # <br> 转换为换行
    result = re.sub(r"<br\s*/?>", "\n", result, flags=re.IGNORECASE)

    # </p>、</div> 转换为换行
    result = re.sub(r"</(?:p|div|li|h[1-6])>", "\n", result, flags=re.IGNORECASE)

    # 移除所有剩余 HTML/XML 标签
    # 要求 < 后跟可选 / 和字母（真正的 HTML 标签），避免误伤数学表达式如 x < 5
    result = re.sub(r"</?[a-zA-Z][^>]*>", "", result)

    # 清理多余空行（连续 3+ 换行压缩为 2 个）
    result = re.sub(r"\n{3,}", "\n\n", result)

    # 去除行首尾多余空白（保留行内空格）
    lines = [line.rstrip() for line in result.split("\n")]
    result = "\n".join(lines)

    # ===== 残留标签碎片清理 =====
    # TGbreak 等预设的 trimStrings 会剥离 < 和 >，导致标签结构被破坏，
    # strip_html_tags 的 <[^>]+> 模式无法匹配这些碎片，需额外清理。

    # 步骤 A：移除半残标签（< 被剥离，> 保留）
    # 匹配 /?tagname> 模式，如 ai_last_output>、/cliche>
    result = re.sub(r"/?[a-zA-Z_]\w*>", "", result)

    # 步骤 B：移除 HTML 注释碎片
    # <!-- 变 !--，--> 变 --，匹配 !--...-- 模式
    result = re.sub(r"!--.*?--", "", result, flags=re.DOTALL)

    # 步骤 C：移除配对标签碎片（< 和 > 均被剥离，只剩标签名）
    # 仅当 word 和 /word 同时出现时才移除，避免误伤单独出现的英文单词
    slash_tags = set(re.findall(r"/([a-zA-Z_][a-zA-Z0-9_]*)", result))
    for tag in slash_tags:
        # 检查不带 / 的 tag 是否也存在（排除 /word 紧跟在另一个 word 后的情况）
        if re.search(
            rf"(?<![/a-zA-Z0-9_]){re.escape(tag)}(?![a-zA-Z0-9_])", result
        ):
            # 替换模式须与搜索模式使用一致的 lookbehind，
            # 否则会误伤更长单词的子串（如 "into" 中的 "to"）
            result = re.sub(
                rf"(?<![/a-zA-Z0-9_])/?{re.escape(tag)}(?![a-zA-Z0-9_])",
                "",
                result,
            )

    # 再次清理多余空行
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result.strip()
