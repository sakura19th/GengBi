"""宏替换引擎。

提供 ``{{macro}}`` 形式的宏替换功能，用于在提示词中插入动态内容。

内置宏：
- ``{{book}}``：小说标题
- ``{{protagonist}}``：主角姓名
- ``{{chapter_title}}``：当前章节标题
- ``{{chapter_index}}``：当前章节序号（从 1 开始）
- ``{{target_words}}``：目标续写字数
- ``{{author}}``：作者
- ``{{synopsis}}``：小说简介
- ``{{world_setting}}``：世界观设定
- ``{{writing_style}}``：写作风格
- ``{{user}}``：用户名（ST 兼容，默认 "User"）
- ``{{char}}``：角色名（ST 兼容，默认 "Assistant"）

ST 风格宏（兼容 SillyTavern）：
- ``{{setvar::name::value}}``：设置变量，返回空字符串
- ``{{getvar::name}}``：获取变量值
- ``{{// comment}}``：注释，替换为空字符串

宏替换结果缓存（key = text + context hash）。
"""
from __future__ import annotations

import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 宏匹配正则：{{name}} 或 {{ name }}（允许空白）
_MACRO_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

# ST 风格宏模式
# {{setvar::name::value}} — value 可含 :: 和任意字符（非贪婪到 }}
_SETVAR_PATTERN = re.compile(r"\{\{setvar::([^:}]+)::([\s\S]*?)\}\}")
# {{getvar::name}}
_GETVAR_PATTERN = re.compile(r"\{\{getvar::([^}]+)\}\}")
# {{// comment}} — 注释
_COMMENT_PATTERN = re.compile(r"\{\{//[\s\S]*?\}\}")

# 内置宏名称集合
BUILTIN_MACROS: frozenset[str] = frozenset({
    "book", "protagonist", "chapter_title", "chapter_index",
    "target_words", "author", "synopsis", "world_setting",
    "writing_style", "user", "char",
})


@dataclass
class MacroContext:
    """宏替换上下文。

    封装宏替换所需的所有数据。所有字段均为可选，缺失时对应宏替换为空字符串。

    Attributes:
        book: 小说标题
        author: 作者
        protagonist: 主角姓名
        synopsis: 小说简介
        world_setting: 世界观设定
        writing_style: 写作风格
        chapter_title: 当前章节标题
        chapter_index: 当前章节序号（从 1 开始，0 表示未设置）
        target_words: 目标续写字数
        user: 用户名（ST 兼容，默认 "User"）
        char: 角色名（ST 兼容，默认 "Assistant"）
        extra: 额外的自定义宏（key 为宏名，value 为字符串值）
        variable_funcs: 变量操作函数字典（含 getvar/setvar），用于 ST 风格宏
    """

    book: str = ""
    author: str = ""
    protagonist: str = ""
    synopsis: str = ""
    world_setting: str = ""
    writing_style: str = ""
    chapter_title: str = ""
    chapter_index: int = 0
    target_words: int = 0
    user: str = "User"
    char: str = "Assistant"
    extra: dict[str, str] = field(default_factory=dict)
    variable_funcs: dict[str, Callable] = field(default_factory=dict)

    @classmethod
    def from_novel_profile(
        cls,
        novel_profile: dict[str, Any] | Any,
        chapter_title: str = "",
        chapter_index: int = 0,
        target_words: int = 0,
        user: str = "User",
        char: str = "Assistant",
        variable_funcs: dict[str, Callable] | None = None,
    ) -> "MacroContext":
        """从 NovelProfile（dict 或对象）构建上下文。

        Args:
            novel_profile: 小说档案（dict 或 NovelProfile 对象）
            chapter_title: 当前章节标题
            chapter_index: 当前章节序号（从 1 开始）
            target_words: 目标续写字数
            user: 用户名（ST 兼容）
            char: 角色名（ST 兼容）
            variable_funcs: 变量操作函数字典（含 getvar/setvar）

        Returns:
            MacroContext 对象
        """
        # 兼容 dict 与 pydantic 对象
        if isinstance(novel_profile, dict):
            get_field = lambda key, default="": novel_profile.get(key, default)  # noqa: E731
        else:
            get_field = lambda key, default="": getattr(novel_profile, key, default)  # noqa: E731

        return cls(
            book=get_field("title", ""),
            author=get_field("author", ""),
            protagonist=get_field("protagonist", ""),
            synopsis=get_field("synopsis", ""),
            world_setting=get_field("world_setting", ""),
            writing_style=get_field("writing_style", ""),
            chapter_title=chapter_title,
            chapter_index=chapter_index,
            target_words=target_words,
            user=user,
            char=char,
            variable_funcs=variable_funcs or {},
        )

    def get_macro_value(self, name: str) -> str:
        """获取宏对应的值。

        Args:
            name: 宏名（不含 ``{{}}``）

        Returns:
            宏值字符串，未知宏返回空字符串
        """
        if name == "book":
            return self.book
        if name == "author":
            return self.author
        if name == "protagonist":
            return self.protagonist
        if name == "synopsis":
            return self.synopsis
        if name == "world_setting":
            return self.world_setting
        if name == "writing_style":
            return self.writing_style
        if name == "chapter_title":
            return self.chapter_title
        if name == "chapter_index":
            return str(self.chapter_index) if self.chapter_index > 0 else ""
        if name == "target_words":
            return str(self.target_words) if self.target_words > 0 else ""
        if name == "user":
            return self.user
        if name == "char":
            return self.char
        # 自定义宏
        return self.extra.get(name, "")

    def context_hash(self) -> str:
        """生成上下文的哈希（用于缓存 key）。"""
        import hashlib
        parts = [
            self.book, self.author, self.protagonist, self.synopsis,
            self.world_setting, self.writing_style, self.chapter_title,
            str(self.chapter_index), str(self.target_words),
            self.user, self.char,
            json_sorted(self.extra),
            str(len(self.variable_funcs)),
        ]
        h = hashlib.md5("|".join(parts).encode("utf-8"))
        return h.hexdigest()


def json_sorted(d: dict[str, str]) -> str:
    """稳定序列化字典（按 key 排序）。"""
    import json
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


class MacroEngine:
    """宏替换引擎。

    提供 ``substitute(text, context)`` 方法，将 ``{{macro}}`` 替换为上下文中的值。
    同时支持 ST 风格宏：``{{setvar::name::value}}``、``{{getvar::name}}``、``{{// comment}}``。
    替换结果缓存（key = text + context hash）。

    Usage::

        engine = MacroEngine()
        context = MacroContext(book="我的小说", chapter_title="第一章")
        result = engine.substitute("当前小说: {{book}}, 章节: {{chapter_title}}", context)
        # "当前小说: 我的小说, 章节: 第一章"
    """

    def __init__(self, cache_size: int = 512) -> None:
        """初始化宏替换引擎。

        Args:
            cache_size: 缓存大小（LRU，按插入顺序淘汰）
        """
        self._cache_size = cache_size
        self._cache: OrderedDict[str, str] = OrderedDict()

    def substitute(self, text: str, context: MacroContext) -> str:
        """执行宏替换。

        替换顺序：
        1. ST 注释 ``{{// comment}}`` → 空字符串
        2. ST setvar ``{{setvar::name::value}}`` → 空字符串（副作用：设置变量）
        3. ST getvar ``{{getvar::name}}`` → 变量值
        4. 简单宏 ``{{name}}`` → 上下文值

        Args:
            text: 待替换文本
            context: 宏上下文

        Returns:
            替换后的文本（未知宏保留原样）
        """
        if not text or "{{" not in text:
            return text

        # 检测是否包含副作用宏（setvar/getvar）——
        # 此类宏具有副作用（设置/读取变量），缓存命中会跳过副作用执行，
        # 导致重复调用时变量不再被设置/读取，因此不参与缓存。
        has_side_effects = (
            _SETVAR_PATTERN.search(text) is not None
            or _GETVAR_PATTERN.search(text) is not None
        )

        cache_key = f"{hash(text)}:{context.context_hash()}"
        if not has_side_effects and cache_key in self._cache:
            # LRU：命中后移到队尾，标记为最近使用
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        result = text

        # 1. 替换注释 {{// comment}}
        result = _COMMENT_PATTERN.sub("", result)

        # 2. 替换 setvar {{setvar::name::value}}
        def replace_setvar(match: re.Match[str]) -> str:
            name = match.group(1).strip()
            value = match.group(2)
            setvar_fn = context.variable_funcs.get("setvar")
            if setvar_fn is not None:
                try:
                    setvar_fn(name, value)
                except Exception as e:
                    logger.warning("setvar 失败（name=%s）: %s", name, e)
            # setvar 返回空字符串（ST 行为）
            return ""

        result = _SETVAR_PATTERN.sub(replace_setvar, result)

        # 3. 替换 getvar {{getvar::name}}
        def replace_getvar(match: re.Match[str]) -> str:
            name = match.group(1).strip()
            getvar_fn = context.variable_funcs.get("getvar")
            if getvar_fn is not None:
                try:
                    value = getvar_fn(name)
                    return str(value) if value is not None else ""
                except Exception as e:
                    logger.warning("getvar 失败（name=%s）: %s", name, e)
                    return ""
            return ""

        result = _GETVAR_PATTERN.sub(replace_getvar, result)

        # 4. 替换简单宏 {{name}}
        def replace_match(match: re.Match[str]) -> str:
            name = match.group(1)
            value = context.get_macro_value(name)
            if value:
                return value
            # 未知宏保留原样（不替换）
            return match.group(0)

        result = _MACRO_PATTERN.sub(replace_match, result)

        # 仅缓存不含副作用宏的结果（LRU 淘汰）
        if not has_side_effects:
            if len(self._cache) >= self._cache_size:
                # 淘汰最久未使用的项（队首）
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            self._cache[cache_key] = result

        return result

    def clear_cache(self) -> None:
        """清空缓存。"""
        self._cache.clear()

    @staticmethod
    def extract_macros(text: str) -> list[str]:
        """提取文本中出现的所有宏名（去重，保持顺序）。

        Args:
            text: 待扫描文本

        Returns:
            宏名列表
        """
        seen: set[str] = set()
        result: list[str] = []
        for match in _MACRO_PATTERN.finditer(text):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                result.append(name)
        return result
