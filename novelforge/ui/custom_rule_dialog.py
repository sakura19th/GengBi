"""自定义设定/审计必查项对话框。

提供两个对话框：
- ``CustomRuleInputDialog``：用户输入自定义设定文本（支持变量占位符）
- ``CustomRulesViewDialog``：查看已新增的自定义设定列表，可删除

参考 ``OntologyExtractor`` 的查看对话框模式（QDialog + QListWidget + 删除回调）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class CustomRuleInputDialog(QDialog):
    """自定义设定输入对话框。

    用户在多行输入框中输入自定义设定文本（可使用 ``{{book}}``/``{{protagonist}}``
    等变量占位符），确定后由 MainWindow 调 AI 结构化为 ``CustomAuditRule``。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新增自定义设定")
        self.setMinimumWidth(480)
        self.setMinimumHeight(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hint = QLabel(
            "请输入自定义设定内容。AI 将结合世界观底层与上下文结构化为审计必查项。\n"
            "可使用 {{book}}/{{protagonist}} 等变量占位符（跟随小说全局走）。"
        )
        hint.setObjectName("metaText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._input_edit = QPlainTextEdit()
        self._input_edit.setMinimumHeight(120)
        self._input_edit.setPlaceholderText(
            "例如：主角在白天禁用超自然能力；本卷不得出现火器；反派必须活到第三卷..."
        )
        layout.addWidget(self._input_edit, 1)

        button_row = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_row.accepted.connect(self.accept)
        button_row.rejected.connect(self.reject)
        layout.addWidget(button_row)

    def get_input(self) -> str:
        """返回用户输入文本（去除首尾空白）。"""
        return self._input_edit.toPlainText().strip()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """Ctrl+Enter 确认。"""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            self.accept()
            return
        super().keyPressEvent(event)


class CustomRulesViewDialog(QDialog):
    """自定义设定查看对话框。

    显示已新增的自定义设定列表（severity + title + requirement），支持选中删除。
    删除操作通过 ``on_delete`` 回调交由 MainWindow 持久化，删除后刷新列表。
    """

    def __init__(
        self,
        rules: list[Any],
        on_delete: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("查看自定义设定")
        self.setMinimumWidth(560)
        self.setMinimumHeight(420)

        self._rules: list[Any] = list(rules) if rules else []
        self._on_delete = on_delete

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QLabel(f"共 {len(self._rules)} 条自定义设定（违反 severity=critical 的规则审计一票否决）")
        header.setObjectName("panelTitle")
        header.setWordWrap(True)
        layout.addWidget(header)

        self._list_widget = QListWidget()
        self._list_widget.setWordWrap(True)
        layout.addWidget(self._list_widget, 1)

        self._populate_list()

        btn_row = QDialogButtonBox()
        self._delete_btn = QPushButton("删除选中")
        self._delete_btn.setObjectName("dangerBtn")
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        btn_row.addButton(self._delete_btn, QDialogButtonBox.ButtonRole.ActionRole)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        btn_row.addButton(close_btn, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(btn_row)

    def _populate_list(self) -> None:
        """填充列表。"""
        self._list_widget.clear()
        for rule in self._rules:
            r = self._to_dict(rule)
            rule_id = r.get("id", "")
            severity = r.get("severity", "critical")
            title = r.get("title", "未命名")
            requirement = r.get("requirement", "")
            audit_criteria = r.get("audit_criteria", "")
            text = (
                f"[{severity.upper()}] {title}\n"
                f"  要求：{requirement}\n"
                f"  审计向：{audit_criteria}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, rule_id)
            self._list_widget.addItem(item)

    @staticmethod
    def _to_dict(rule: Any) -> dict[str, Any]:
        """将 CustomAuditRule 或 dict 转为 dict。"""
        if hasattr(rule, "model_dump"):
            return rule.model_dump(mode="json")
        if isinstance(rule, dict):
            return rule
        return {}

    def _on_delete_clicked(self) -> None:
        """删除选中的自定义设定。"""
        item = self._list_widget.currentItem()
        if item is None:
            return
        rule_id = item.data(Qt.ItemDataRole.UserRole)
        if not rule_id:
            return
        # 从内存列表移除
        self._rules = [
            r for r in self._rules if self._to_dict(r).get("id") != rule_id
        ]
        # 回调 MainWindow 持久化
        if self._on_delete is not None:
            try:
                self._on_delete(rule_id)
            except Exception as e:
                logger.error("删除自定义设定失败: %s", e, exc_info=True)
        # 刷新列表
        self._populate_list()
