"""路径与资源工具函数。

提供资源文件路径解析等通用工具，以及敏感文件权限收紧。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# PyInstaller 打包后，资源被解压到 sys._MEIPASS 临时目录；
# 源码运行时则定位到包目录。两种模式都指向 novelforge/resources/。
if getattr(sys, "frozen", False):
    PACKAGE_ROOT: Path = Path(sys._MEIPASS) / "novelforge"  # type: ignore[attr-defined]
else:
    PACKAGE_ROOT = Path(__file__).resolve().parent.parent

# 资源目录（novelforge/resources/）
RESOURCES_DIR: Path = PACKAGE_ROOT / "resources"


def get_resource_path(*parts: str) -> Path:
    """获取资源文件路径。

    Args:
        *parts: 资源文件的相对路径部分（如 "defaults", "default_preset.json"）

    Returns:
        资源文件的绝对路径
    """
    return RESOURCES_DIR.joinpath(*parts)


def get_theme_path(theme_name: str) -> Path:
    """获取主题样式表文件路径。

    Args:
        theme_name: 主题名称（dark/light）

    Returns:
        QSS 文件路径
    """
    return get_resource_path("themes", f"{theme_name}.qss")


def get_default_preset_path() -> Path:
    """获取默认预设文件路径。"""
    return get_resource_path("defaults", "default_preset.json")


def get_extract_prompt_path() -> Path:
    """获取默认上下文提取提示词路径。"""
    return get_resource_path("defaults", "extract_prompt.txt")


def get_extract_merge_prompt_path() -> Path:
    """获取上下文提取【信息汇总】环节提示词路径。

    多批次提取后，由该模板指导 LLM 合并去重为最终 ContextEntry 列表。
    """
    return get_resource_path("defaults", "extract_merge_prompt.txt")


def get_extract_ontology_prompt_path() -> Path:
    """获取底层世界观提取提示词路径。

    全文拆分分析提取 WorldOntology 7 大维度参数化描述，
    支持 token 拆分 + 增量更新（含 ``{{accumulated_ontology}}`` 占位符）。
    """
    return get_resource_path("defaults", "extract_ontology_prompt.txt")


def get_extract_ontology_merge_prompt_path() -> Path:
    """获取底层世界观合并提示词路径。

    多批次提取后，由该模板指导 LLM 合并为统一的 WorldOntology JSON 对象。
    """
    return get_resource_path("defaults", "extract_ontology_merge_prompt.txt")


def get_extract_protagonist_prompt_path() -> Path:
    """获取主角形象提取提示词路径。

    提取 ProtagonistProfile 8 大维度心理学档案，
    支持 token 拆分 + 增量更新（含 ``{{accumulated_protagonist}}`` 占位符）。
    """
    return get_resource_path("defaults", "extract_protagonist_prompt.txt")


def get_extract_protagonist_merge_prompt_path() -> Path:
    """获取主角形象合并提示词路径。

    多批次提取后，由该模板指导 LLM 合并为统一的 ProtagonistProfile JSON 对象。
    """
    return get_resource_path("defaults", "extract_protagonist_merge_prompt.txt")


def get_default_regex_scripts_path() -> Path:
    """获取默认正则脚本文件路径。"""
    return get_resource_path("defaults", "default_regex_scripts.json")


def get_agent_prompt_path(phase: str) -> Path:
    """获取 Agent 阶段提示词路径。

    Args:
        phase: 阶段名（analysis/outline/verify/revise/single_audit）

    Returns:
        提示词文件路径
    """
    return get_resource_path("defaults", "agent", f"phase_{phase}.txt")


# 卷级多章节续写支持的阶段名集合
_VOLUME_PROMPT_PHASES = frozenset(
    {"deep_analysis", "deep_analysis_merge", "volume_outline", "outline_audit", "outline_final", "chapter_outline"}
)


def get_volume_prompt_path(phase: str) -> Path:
    """获取卷级多章节续写阶段提示词路径。

    与 get_agent_prompt_path 镜像，但限定为卷续写流程的阶段名。

    Args:
        phase: 阶段名，取值 deep_analysis/volume_outline/outline_audit/outline_final/chapter_outline

    Returns:
        提示词文件路径（resources/defaults/agent/phase_{phase}.txt）

    Raises:
        ValueError: phase 不在支持的取值范围内
    """
    if phase not in _VOLUME_PROMPT_PHASES:
        valid = "/".join(sorted(_VOLUME_PROMPT_PHASES))
        raise ValueError(
            f"非法的卷续写阶段名: {phase!r}，支持取值: {valid}"
        )
    return get_resource_path("defaults", "agent", f"phase_{phase}.txt")


def load_text_resource(path: Path) -> str:
    """加载文本资源文件内容。

    Args:
        path: 文件路径

    Returns:
        文件文本内容
    """
    return path.read_text(encoding="utf-8")


def secure_file(path: Path) -> None:
    """收紧文件权限为所有者可读写（0o600）。

    用于敏感文件（config.json、.machine_id、novelforge.db、日志、导出密钥）
    写入后的纵深防御：阻止同机其他用户读取加密 salt 与 API Key 密文，
    降低 passphrase 非密钥场景下的离线解密风险。

    - Linux/macOS：``os.chmod(path, 0o600)`` 实际生效。
    - Windows：``os.chmod`` 基本为 no-op（不影响 ACL），不会破坏现有行为。
    - 任何 ``OSError`` 静默忽略（权限收紧是最佳努力，不阻断主流程）。

    Args:
        path: 待收紧权限的文件路径
    """
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        # 收紧失败不影响主流程（如只读文件系统、Windows ACL 限制）
        logger.debug("收紧文件权限失败 %s: %s", path, e)
