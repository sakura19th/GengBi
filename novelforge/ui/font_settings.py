"""字体设置对话框。

提供正文字体、字号、行距的调整界面：
- 字体下拉框（QFontComboBox）
- 字号 SpinBox（8-32）
- 行距 DoubleSpinBox（1.0-3.0）
- 预览区（QPlainTextEdit 显示示例文本）
- 确定/取消按钮

应用后立即生效，持久化到 ``ConfigManager.set_appearance``。

Usage::

    dialog = FontSettingsDialog(config_manager, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        # 字体已应用并持久化
        pass
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QFontComboBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from novelforge.core.config import ConfigManager

logger = logging.getLogger(__name__)

# 字号范围
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 32

# 行距范围
MIN_LINE_HEIGHT = 1.0
MAX_LINE_HEIGHT = 3.0

# 预览示例文本
PREVIEW_TEXT = """第一章 示例预览

这是一段示例文本，用于预览字体设置效果。

字体、字号、行距的调整会实时反映在此预览区中。
点击"确定"后设置将立即应用到章节编辑器，并持久化到配置文件。

—— 赓笔 字体设置"""


class FontSettingsDialog(QDialog):
    """字体设置对话框。

    提供字体、字号、行距调整界面，含实时预览。

    Args:
        config_manager: 配置管理器（用于读取/持久化外观配置）
        parent: 父控件
    """

    def __init__(self, config_manager: ConfigManager, parent=None) -> None:
        """初始化字体设置对话框。"""
        super().__init__(parent)
        self._config_manager = config_manager

        self.setWindowTitle("字体设置")
        self.setMinimumSize(480, 420)

        self._setup_ui()
        self._load_settings()
        self._update_preview()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)

        # 字体设置组
        font_group = QGroupBox("字体设置")
        font_form = QFormLayout(font_group)

        self._font_combo = QFontComboBox()
        self._font_combo.currentFontChanged.connect(self._on_font_changed)
        font_form.addRow("字体:", self._font_combo)

        self._size_spin = QSpinBox()
        self._size_spin.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self._size_spin.setValue(14)
        self._size_spin.setSuffix(" pt")
        self._size_spin.valueChanged.connect(self._on_size_changed)
        font_form.addRow("字号:", self._size_spin)

        self._line_height_spin = QDoubleSpinBox()
        self._line_height_spin.setRange(MIN_LINE_HEIGHT, MAX_LINE_HEIGHT)
        self._line_height_spin.setSingleStep(0.1)
        self._line_height_spin.setValue(1.6)
        self._line_height_spin.setSuffix(" 倍")
        self._line_height_spin.valueChanged.connect(self._on_line_height_changed)
        font_form.addRow("行距:", self._line_height_spin)

        layout.addWidget(font_group)

        # 预览区
        preview_group = QGroupBox("预览")
        preview_layout = QVBoxLayout(preview_group)
        self._preview = QPlainTextEdit()
        self._preview.setPlainText(PREVIEW_TEXT)
        self._preview.setReadOnly(True)
        preview_layout.addWidget(self._preview)
        layout.addWidget(preview_group)

        # 按钮区
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_settings(self) -> None:
        """从配置加载当前字体设置。"""
        appearance = self._config_manager.get_appearance()
        font_family = appearance.get("font_family", "")
        font_size = appearance.get("font_size", 14)
        line_height = appearance.get("line_height", 1.6)

        if font_family:
            font = QFont(font_family)
            self._font_combo.setCurrentFont(font)
        self._size_spin.setValue(int(font_size))
        self._line_height_spin.setValue(float(line_height))

    def _on_font_changed(self, font: QFont) -> None:
        """字体变更：更新预览。"""
        self._update_preview()

    def _on_size_changed(self, _value: int) -> None:
        """字号变更：更新预览。"""
        self._update_preview()

    def _on_line_height_changed(self, _value: float) -> None:
        """行距变更：更新预览。"""
        self._update_preview()

    def _update_preview(self) -> None:
        """更新预览区字体。"""
        font = self._font_combo.currentFont()
        font.setPointSize(self._size_spin.value())
        self._preview.setFont(font)
        # QTextDocument 设置行距（通过 HTML/CSS 或 QTextBlockFormat）
        # QPlainTextEdit 使用 QTextDocument，行距通过 blockFormat 的 lineHeight
        cursor = self._preview.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        from PySide6.QtGui import QTextBlockFormat

        fmt = QTextBlockFormat()
        fmt.setLineHeight(
            float(self._line_height_spin.value() * 100),
            QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
        )
        cursor.mergeBlockFormat(fmt)

    def _on_accept(self) -> None:
        """确认保存：持久化到配置。"""
        appearance = self._config_manager.get_appearance()
        appearance["font_family"] = self._font_combo.currentFont().family()
        appearance["font_size"] = self._size_spin.value()
        appearance["line_height"] = self._line_height_spin.value()
        self._config_manager.set_appearance(appearance)
        logger.info(
            "字体设置已保存: family=%s, size=%d, line_height=%.2f",
            appearance["font_family"],
            appearance["font_size"],
            appearance["line_height"],
        )
        self.accept()

    def get_settings(self) -> dict:
        """获取当前对话框中的字体设置（不保存）。

        Returns:
            含 font_family/font_size/line_height 的字典
        """
        return {
            "font_family": self._font_combo.currentFont().family(),
            "font_size": self._size_spin.value(),
            "line_height": self._line_height_spin.value(),
        }


def apply_font_to_editor(editor, appearance: dict) -> None:
    """将外观配置中的字体设置应用到 QPlainTextEdit 编辑器。

    Args:
        editor: QPlainTextEdit 实例（或含 ``_editor`` 属性的控件）
        appearance: 外观配置字典（含 font_family/font_size/line_height）
    """
    from PySide6.QtGui import QFont, QTextBlockFormat, QTextCursor

    font_family = appearance.get("font_family", "")
    font_size = int(appearance.get("font_size", 14))
    if font_size < 1:
        font_size = 14  # 非法值回退到默认
    line_height = float(appearance.get("line_height", 1.6))

    # 支持 ChapterEditor（含 _editor）或直接 QPlainTextEdit
    plain_edit = getattr(editor, "_editor", editor)
    if plain_edit is None:
        return

    font = QFont(font_family) if font_family else QFont()
    font.setPointSize(font_size)
    plain_edit.setFont(font)

    # 设置行距
    cursor = plain_edit.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    fmt = QTextBlockFormat()
    fmt.setLineHeight(
        float(line_height * 100),
        QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
    )
    cursor.mergeBlockFormat(fmt)
