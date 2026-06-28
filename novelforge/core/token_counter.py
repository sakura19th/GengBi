"""Token 计数器。

按模型族选择 tokenizer：
- OpenAI 模型（gpt-3.5/gpt-4/gpt-4o/gpt-5 等）使用 tiktoken
  - gpt-4o / gpt-5 / o1 / o3 系列用 ``o200k_base``
  - 其他 OpenAI 模型用 ``cl100k_base``
- 非 OpenAI 模型用字符数估算 fallback：
  - 中文占比 > 70%：``len(text) * 0.6``
  - 中文占比 < 30%：``len(text) / 4``
  - 混合：``len(text) * 0.5``

token 计数结果缓存（key = text hash + tokenizer 名）。
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

# CJK Unicode 范围正则（用于估算中文占比）
_CJK_PATTERN = re.compile(
    "[\u4e00-\u9fff"  # CJK 统一汉字
    "\u3400-\u4dbf"   # CJK 扩展 A
    "\uf900-\ufaff"   # CJK 兼容汉字
    "\u3040-\u309f"   # 平假名
    "\u30a0-\u30ff"   # 片假名
    "\uac00-\ud7af"   # 韩文音节
    "]+"
)

# OpenAI 模型名匹配正则
_OPENAI_MODEL_PATTERN = re.compile(
    r"^(gpt-3\.5|gpt-4|gpt-4o|gpt-4-turbo|gpt-5|o1|o3|o4|chatgpt)",
    re.IGNORECASE,
)

# 使用 o200k_base tokenizer 的模型前缀
_O200K_MODEL_PREFIXES: tuple[str, ...] = (
    "gpt-4o", "gpt-5", "o1", "o3", "o4", "chatgpt-4o",
)


def estimate_chinese_ratio(text: str) -> float:
    """估算文本中中文字符占比。

    统计 CJK 字符数（含日韩）占总字符数的比例。

    Args:
        text: 待分析文本

    Returns:
        中文占比（0.0 ~ 1.0），空文本返回 0.0
    """
    if not text:
        return 0.0
    cjk_count = 0
    for match in _CJK_PATTERN.finditer(text):
        cjk_count += len(match.group(0))
    return cjk_count / len(text)


def estimate_tokens_by_chars(text: str) -> int:
    """按字符数估算 token 数（非 OpenAI 模型 fallback）。

    规则：
    - 中文占比 > 70%：``len(text) * 0.6``
    - 中文占比 < 30%：``len(text) / 4``
    - 混合：``len(text) * 0.5``

    Args:
        text: 待估算文本

    Returns:
        估算的 token 数（至少为 0）
    """
    if not text:
        return 0
    ratio = estimate_chinese_ratio(text)
    if ratio > 0.7:
        tokens = len(text) * 0.6
    elif ratio < 0.3:
        tokens = len(text) / 4
    else:
        tokens = len(text) * 0.5
    return max(0, int(round(tokens)))


def is_openai_model(model: str) -> bool:
    """判断模型名是否属于 OpenAI 系列。

    Args:
        model: 模型名

    Returns:
        是否为 OpenAI 模型
    """
    if not model:
        return False
    return bool(_OPENAI_MODEL_PATTERN.match(model))


def count_text_tokens(text: str) -> int:
    """计算文本的 token 数（模型无关，使用字符数估算）。

    供卷级深度分析按 token 切分章节使用，不依赖具体模型 tokenizer。

    Args:
        text: 待计算文本

    Returns:
        估算的 token 数
    """
    return estimate_tokens_by_chars(text)


def get_tokenizer_name_for_model(model: str) -> str | None:
    """获取 OpenAI 模型对应的 tokenizer 名。

    Args:
        model: 模型名

    Returns:
        tokenizer 名（``cl100k_base`` / ``o200k_base``），非 OpenAI 模型返回 None
    """
    if not is_openai_model(model):
        return None
    model_lower = model.lower()
    for prefix in _O200K_MODEL_PREFIXES:
        if model_lower.startswith(prefix):
            return "o200k_base"
    return "cl100k_base"


class TokenCounter:
    """Token 计数器。

    按模型族选择 tokenizer，非 OpenAI 模型使用字符数估算 fallback。
    token 计数结果缓存（key = text hash + tokenizer 名）。

    Usage::

        counter = TokenCounter()
        tokens = counter.count("你好世界", model="gpt-4o")
        tokens = counter.count_messages(messages, model="gpt-4")
        is_exact, desc = counter.get_count_mode(model)
    """

    def __init__(self, cache_size: int = 1024) -> None:
        """初始化 token 计数器。

        Args:
            cache_size: 缓存大小（LRU 淘汰）
        """
        self._cache_size = cache_size
        self._cache: OrderedDict[str, int] = OrderedDict()
        # tiktoken 编码器缓存（按 tokenizer 名缓存）
        self._encoders: dict[str, Any] = {}
        self._tiktoken_available: bool | None = None

    def _get_encoder(self, tokenizer_name: str) -> Any | None:
        """获取 tiktoken 编码器（缓存）。

        Args:
            tokenizer_name: tokenizer 名（cl100k_base / o200k_base）

        Returns:
            tiktoken Encoding 对象，不可用时返回 None
        """
        if tokenizer_name in self._encoders:
            return self._encoders[tokenizer_name]

        if self._tiktoken_available is None:
            try:
                import tiktoken  # noqa: F401
                self._tiktoken_available = True
            except ImportError:
                self._tiktoken_available = False
                logger.warning(
                    "tiktoken 未安装，OpenAI 模型 token 计数将使用估算"
                )

        if not self._tiktoken_available:
            return None

        try:
            import tiktoken
            encoder = tiktoken.get_encoding(tokenizer_name)
            self._encoders[tokenizer_name] = encoder
            return encoder
        except Exception as e:
            logger.warning(
                "加载 tiktoken tokenizer %s 失败: %s，使用估算",
                tokenizer_name, e,
            )
            return None

    def count(self, text: str, model: str = "") -> int:
        """计算文本的 token 数。

        Args:
            text: 待计算文本
            model: 模型名（决定使用哪个 tokenizer）

        Returns:
            token 数
        """
        if not text:
            return 0

        tokenizer_name = get_tokenizer_name_for_model(model)
        if tokenizer_name is None:
            # 非 OpenAI 模型，使用估算
            return estimate_tokens_by_chars(text)

        encoder = self._get_encoder(tokenizer_name)
        if encoder is None:
            # tiktoken 不可用，使用估算
            return estimate_tokens_by_chars(text)

        # 缓存查找
        cache_key = self._make_cache_key(text, tokenizer_name)
        if cache_key in self._cache:
            # LRU：命中后移到队尾，标记为最近使用
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        # 精确计数
        try:
            tokens = len(encoder.encode(text))
        except Exception as e:
            logger.warning("tiktoken 编码失败，使用估算: %s", e)
            return estimate_tokens_by_chars(text)

        # 缓存结果
        self._set_cache(cache_key, tokens)
        return tokens

    def count_messages(
        self, messages: list[dict[str, Any]], model: str = ""
    ) -> int:
        """计算 messages 数组的 token 数。

        估算规则（对齐 OpenAI 官方规则）：
        - 每条消息 token = 4（``<|im_start|>role\n`` + ``<|im_end|>\n``）
        - content token 数 + role 名 token 数
        - 整体追加 3（``<|im_start|>assistant<|im_end|>`` 引导回复）

        Args:
            messages: messages 数组
            model: 模型名

        Returns:
            token 数
        """
        if not messages:
            return 0

        tokenizer_name = get_tokenizer_name_for_model(model)
        use_exact = tokenizer_name is not None and self._get_encoder(tokenizer_name) is not None

        total = 0
        for msg in messages:
            if use_exact:
                # 对齐 OpenAI 官方估算：每条消息 4 token 开销
                total += 4
                role = msg.get("role", "")
                content = msg.get("content", "")
                if isinstance(content, str):
                    total += self.count(content, model)
                elif isinstance(content, list):
                    # 多模态消息，按文本部分累加
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            total += self.count(part.get("text", ""), model)
                total += self.count(role, model)
                # name 字段（如有）
                name = msg.get("name")
                if name:
                    total += self.count(name, model) + 1
            else:
                # 估算模式：仅累加 content
                content = msg.get("content", "")
                if isinstance(content, str):
                    total += estimate_tokens_by_chars(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            total += estimate_tokens_by_chars(part.get("text", ""))

        if use_exact:
            # 整体追加 3 token（assistant 引导）
            total += 3

        return total

    def get_count_mode(self, model: str) -> tuple[bool, str]:
        """获取指定模型的 token 计数方式。

        Args:
            model: 模型名

        Returns:
            (是否精确, 描述文本) 元组
            - 精确：(True, "精确（tiktoken）")
            - 估算：(False, "估算（非 OpenAI 模型）" 或 "估算（tiktoken 不可用）")
        """
        tokenizer_name = get_tokenizer_name_for_model(model)
        if tokenizer_name is None:
            return False, "估算（非 OpenAI 模型）"
        if self._get_encoder(tokenizer_name) is None:
            return False, "估算（tiktoken 不可用）"
        return True, f"精确（{tokenizer_name}）"

    def _make_cache_key(self, text: str, tokenizer_name: str) -> str:
        """生成缓存 key（text hash + tokenizer 名）。"""
        text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
        return f"{tokenizer_name}:{text_hash}"

    def _set_cache(self, key: str, value: int) -> None:
        """设置缓存（LRU 淘汰）。"""
        if len(self._cache) >= self._cache_size:
            # 淘汰最久未使用的项（队首）
            # 由于命中时会 move_to_end，队首即为最久未访问的 LRU 项
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        self._cache[key] = value

    def clear_cache(self) -> None:
        """清空缓存。"""
        self._cache.clear()
