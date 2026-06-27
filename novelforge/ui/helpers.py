"""UI 工具函数。

提供跨 UI 模块复用的标签状态切换、token 限制解析、下拉框按 ID 选中
等通用操作，避免在各面板/对话框中重复实现。
"""
from __future__ import annotations

import re

from PySide6.QtWidgets import QComboBox, QLabel


def set_label_state(label: QLabel, text: str, state: str) -> None:
    """设置标签文本与状态样式（对象名驱动，由全局 QSS 接管）。

    仅当目标状态与当前 objectName 不同时才刷新样式，避免不必要的重算。

    Args:
        label: 目标标签
        text: 文本
        state: 状态对象名（如 textSecondary/textInfo/textSuccess/textDanger/
            textWarning/metaText/phaseCurrent 等）
    """
    label.setText(text)
    if label.objectName() != state:
        label.setObjectName(state)
        # 刷新 QSS 选择器（objectName 变化不会自动重算样式）
        label.style().unpolish(label)
        label.style().polish(label)


def parse_token_limit(text: str) -> int:
    """从 "50k"/"100k" 等文本中解析 token 限制数值。

    "不限制" 等不含数字的文本返回 0（等价于不限制）。

    Args:
        text: 下拉框文本（如 "不限制"/"50k"/"100k"/"250k"/"500k"）

    Returns:
        token 数值（"50k" → 50000），无数字时返回 0
    """
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) * 1000 if match else 0


def select_combo_by_id(combo: QComboBox, target_id: str) -> None:
    """按 itemData 选中下拉框对应项。

    遍历所有条目，找到 itemData 等于 target_id 的项并设为当前项；
    未找到则保持当前选中不变。

    Args:
        combo: 目标下拉框
        target_id: 目标条目 ID
    """
    for i in range(combo.count()):
        if combo.itemData(i) == target_id:
            combo.setCurrentIndex(i)
            return
