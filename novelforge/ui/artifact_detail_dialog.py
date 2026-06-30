"""ArtifactDetailDialog：章节阶段产物完整内容查看对话框。

只读展示单个阶段产物（细纲/初稿/审计/修改正文）的完整文本，不截断。
"""
from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from novelforge.utils.outline_serializer import format_critique, format_outline


class ArtifactDetailDialog(QDialog):
    """阶段产物完整内容查看对话框。

    构造时根据 stage_type 格式化内容：
    - outline：format_outline(outline) 完整输出
    - draft/revise：正文原文（不截断），revise 阶段追加 guidance JSON
    - audit：format_critique(critique) 完整输出
    """

    def __init__(
        self,
        title: str,
        stage_type: str = "",
        content: str = "",
        critique=None,
        guidance: dict | None = None,
        outline=None,
        parent=None,
    ) -> None:
        """初始化产物详情对话框。

        Args:
            title: 窗口标题（如"第3章 · 审计②"）
            stage_type: 阶段类型（"outline"/"draft"/"audit"/"revise"）
            content: 正文文本（draft/revise 阶段）
            critique: 审计报告（audit 阶段）
            guidance: 修订指导 dict（revise 阶段）
            outline: 细纲对象（outline 阶段）
            parent: 父窗口
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(800, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 只读文本区
        text_edit = QPlainTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(self._format_content(stage_type, content, critique, guidance, outline))
        layout.addWidget(text_edit, 1)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    @staticmethod
    def _format_content(
        stage_type: str,
        content: str,
        critique,
        guidance: dict | None,
        outline,
    ) -> str:
        """根据阶段类型格式化展示内容。"""
        if stage_type == "outline":
            if outline is not None:
                return format_outline(outline)
            return "（无细纲）"
        if stage_type == "audit":
            if critique is not None:
                return format_critique(critique)
            return "（无审计报告）"
        if stage_type == "revise":
            parts: list[str] = []
            if guidance is not None:
                parts.append("【修订指导】")
                parts.append(json.dumps(guidance, ensure_ascii=False, indent=2))
                parts.append("")
            parts.append("【修改后正文】")
            parts.append(content or "（无）")
            return "\n".join(parts)
        # draft 或其他
        return content or "（无）"
