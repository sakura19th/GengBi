"""工具子包：通用工具函数。"""
from __future__ import annotations

from novelforge.utils.paths import (
    RESOURCES_DIR,
    get_default_preset_path,
    get_default_regex_scripts_path,
    get_extract_prompt_path,
    get_resource_path,
    get_theme_path,
    load_text_resource,
)

__all__ = [
    "RESOURCES_DIR",
    "get_resource_path",
    "get_theme_path",
    "get_default_preset_path",
    "get_extract_prompt_path",
    "get_default_regex_scripts_path",
    "load_text_resource",
]
