"""流程破限文本提供器。

加载 ``resources/defaults/jailbreaks/jb_{flow}.txt`` 模板文件，按
``### LOW ###`` / ``### MID ###`` / ``### HIGH ###`` 标记分段，返回对应
等级的破限文本。供非正文流程（审计/提取/解析等）在调用 LLM 前作为
system 消息前置到 messages 数组开头。

正文流程（single/volume continuation）不走本服务，其破限由预设
``prompt_order`` 中的 ``nf_jb_*`` 模块控制。
"""
from __future__ import annotations

import logging
import re

from novelforge.utils.paths import get_resource_path

logger = logging.getLogger(__name__)

# 破限等级取值
LEVEL_OFF: str = "off"
LEVEL_LOW: str = "low"
LEVEL_MID: str = "mid"
LEVEL_HIGH: str = "high"

# 合法等级（off 返回空串）
_VALID_LEVELS: tuple[str, ...] = (LEVEL_OFF, LEVEL_LOW, LEVEL_MID, LEVEL_HIGH)

# 段落标记正则：匹配行首 ### LEVEL ###
_SECTION_PATTERN = re.compile(r"^###\s*(\w+)\s*###\s*$", re.MULTILINE)


class JailbreakProvider:
    """流程破限文本提供器。

    加载 ``resources/defaults/jailbreaks/jb_{flow}.txt``，按
    ``### LOW/MID/HIGH ###`` 标记分段，返回对应等级文本。文件内容缓存。
    """

    def __init__(self) -> None:
        # flow_key -> 原始文件内容（缺失则不在缓存中）
        self._cache: dict[str, str] = {}

    def get_jailbreak(self, flow_key: str, level: str) -> str:
        """返回等级对应的破限文本；off 或无文件/无对应段返回空串。

        Args:
            flow_key: 流程标识（如 ``context_extraction``）
            level: 破限等级（``off``/``low``/``mid``/``high``）

        Returns:
            破限文本；off、文件缺失或对应等级段不存在时返回 ``""``
        """
        if level not in _VALID_LEVELS:
            logger.warning("未知破限等级 %r，忽略", level)
            return ""
        if level == LEVEL_OFF:
            return ""

        raw = self._load_raw(flow_key)
        if not raw:
            return ""
        return self._extract_section(raw, level)

    def _load_raw(self, flow_key: str) -> str:
        """加载并缓存 jb_{flow}.txt 原始内容。

        Args:
            flow_key: 流程标识

        Returns:
            文件原始文本；文件不存在返回 ``""``
        """
        if flow_key in self._cache:
            return self._cache[flow_key]

        path = get_resource_path("defaults", "jailbreaks", f"jb_{flow_key}.txt")
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug("破限模板不存在: %s", path)
            text = ""
        except Exception as e:
            logger.warning("读取破限模板失败 %s: %s", path, e)
            text = ""
        self._cache[flow_key] = text
        return text

    @staticmethod
    def _extract_section(raw: str, level: str) -> str:
        """从原始文本中提取指定等级段落。

        段落由 ``### LEVEL ###`` 标记分隔，返回标记到下一标记（或文件尾）
        之间的文本，去除首尾空白。

        Args:
            raw: 文件原始文本
            level: 等级名（``low``/``mid``/``high``，大写匹配标记）

        Returns:
            对应段落文本；未找到返回 ``""``
        """
        level_upper = level.upper()
        matches = list(_SECTION_PATTERN.finditer(raw))
        for i, m in enumerate(matches):
            if m.group(1).upper() == level_upper:
                start = m.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
                return raw[start:end].strip()
        return ""
