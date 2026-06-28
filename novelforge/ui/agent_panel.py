"""AgentPanel：多阶段 Agent 续写流程配置与监控面板。

包含：
- 阶段开关复选框（分析/大纲/写作/验证/修订，写作锁定开启）
- 暂停点复选框（大纲后/验证后）与最大修订轮次 SpinBox
- 阶段进度指示器（5 个 QLabel 横向排列，高亮当前阶段）
- 产物查看器（大纲预览可编辑、评审报告只读折叠组）

Signals:
    config_changed(object): AgentRunConfig 对象，任意配置变更时发射
    resume(object): checkpoint payload（编辑后的大纲等），用户确认继续时发射
    cancel_checkpoint(): 用户取消暂停时发射
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from novelforge.models import AgentRunConfig, CritiqueReport, Outline
from novelforge.ui.helpers import set_label_state
from novelforge.ui.wheel_filter import WheelEventFilter
from novelforge.utils.outline_serializer import (
    format_critique,
    format_outline,
    parse_outline,
)

logger = logging.getLogger(__name__)

# 阶段顺序（与 AgentRunConfig.phases 默认顺序一致）
PHASE_ORDER: list[str] = ["analysis", "outline", "writing", "verify", "revise"]

# 阶段中文标签
PHASE_LABELS: dict[str, str] = {
    "analysis": "分析",
    "outline": "大纲",
    "writing": "写作",
    "verify": "验证",
    "revise": "修订",
}

# 进度指示器状态对象名（由全局 QSS 接管样式）
_OBJ_PHASE_CURRENT = "phaseCurrent"
_OBJ_PHASE_COMPLETED = "phaseCompleted"
_OBJ_PHASE_PENDING = "phasePending"


class AgentPanel(QWidget):
    """多阶段 Agent 续写流程配置与监控面板。

    提供阶段开关、暂停点配置、阶段进度指示与产物查看。
    配置变更时构建 AgentRunConfig 并发射 config_changed 信号。

    Signals:
        config_changed(object): AgentRunConfig 对象
        resume(object): checkpoint payload（编辑后的大纲等）
        cancel_checkpoint(): 取消暂停
        continue_requested(str): 用户在检查点选择编辑后，点击"继续"按钮时发射，
            携带检查点名（after_outline）
    """

    config_changed = Signal(object)
    resume = Signal(object)
    cancel_checkpoint = Signal()
    continue_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        """初始化 Agent 面板。

        Args:
            parent: 父窗口
        """
        super().__init__(parent)
        self._current_outline: Outline | None = None
        self._current_critique: CritiqueReport | None = None
        self._current_checkpoint_name: str = ""

        self._setup_ui()
        self._setup_connections()
        self._update_phase_availability()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # ===== 阶段开关 =====
        self._phase_group = QGroupBox("阶段开关")
        phase_layout = QVBoxLayout(self._phase_group)
        self._phase_checks: dict[str, QCheckBox] = {}
        for phase in PHASE_ORDER:
            check = QCheckBox(PHASE_LABELS[phase])
            check.setChecked(True)
            if phase == "writing":
                # 写作是核心阶段，锁定开启
                check.setEnabled(False)
            phase_layout.addWidget(check)
            self._phase_checks[phase] = check
        layout.addWidget(self._phase_group)

        # ===== 暂停点与修订 =====
        self._checkpoint_group = QGroupBox("暂停点与修订")
        checkpoint_form = QFormLayout(self._checkpoint_group)

        self._after_outline_check = QCheckBox("大纲生成后暂停")
        self._after_outline_check.setChecked(False)
        checkpoint_form.addRow("大纲后暂停:", self._after_outline_check)

        self._after_verify_check = QCheckBox("验证完成后暂停")
        self._after_verify_check.setChecked(False)
        checkpoint_form.addRow("验证后暂停:", self._after_verify_check)

        self._max_revise_spin = QSpinBox()
        self._max_revise_spin.setRange(1, 3)
        self._max_revise_spin.setValue(1)
        checkpoint_form.addRow("最大修订轮次:", self._max_revise_spin)

        layout.addWidget(self._checkpoint_group)

        # ===== 阶段进度 =====
        self._progress_group = QGroupBox("阶段进度")
        progress_layout = QHBoxLayout(self._progress_group)
        self._phase_labels: dict[str, QLabel] = {}
        for phase in PHASE_ORDER:
            label = QLabel(PHASE_LABELS[phase])
            label.setObjectName(_OBJ_PHASE_PENDING)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            progress_layout.addWidget(label)
            self._phase_labels[phase] = label
        layout.addWidget(self._progress_group)

        # ===== 产物查看 =====
        self._artifacts_group = QGroupBox("产物查看")
        artifacts_layout = QVBoxLayout(self._artifacts_group)

        # 大纲预览（可编辑）
        outline_label = QLabel("大纲预览（可编辑）:")
        artifacts_layout.addWidget(outline_label)
        self._outline_edit = QPlainTextEdit()
        self._outline_edit.setPlaceholderText("大纲生成后将显示在此处，可编辑...")
        self._outline_edit.setMinimumHeight(120)
        artifacts_layout.addWidget(self._outline_edit)

        # 评审报告（折叠组，只读）
        self._critique_group = QGroupBox("评审报告")
        self._critique_group.setCheckable(True)
        self._critique_group.setChecked(False)
        critique_layout = QVBoxLayout(self._critique_group)
        self._critique_edit = QPlainTextEdit()
        self._critique_edit.setReadOnly(True)
        self._critique_edit.setPlaceholderText("评审报告将显示在此处...")
        self._critique_edit.setMinimumHeight(100)
        critique_layout.addWidget(self._critique_edit)
        artifacts_layout.addWidget(self._critique_group)

        # 继续按钮：检查点选择"编辑"后显示，用户在面板编辑产物后点击恢复
        self._continue_btn = QPushButton("继续")
        self._continue_btn.setObjectName("primaryBtn")
        self._continue_btn.hide()
        self._continue_btn.clicked.connect(self._on_continue_clicked)
        artifacts_layout.addWidget(self._continue_btn)

        layout.addWidget(self._artifacts_group, 1)

        # 安装滚轮事件过滤器
        self._wheel_filter = WheelEventFilter(self)
        self._max_revise_spin.installEventFilter(self._wheel_filter)

    def _setup_connections(self) -> None:
        """连接信号。"""
        # 阶段开关变更
        for phase, check in self._phase_checks.items():
            if phase != "writing":  # writing 锁定，不会触发
                check.toggled.connect(self._on_config_changed)
        # 暂停点变更
        self._after_outline_check.toggled.connect(self._on_config_changed)
        self._after_verify_check.toggled.connect(self._on_config_changed)
        # 修订轮次变更
        self._max_revise_spin.valueChanged.connect(self._on_config_changed)

    # ------------------------------------------------------------------
    # 配置变更处理
    # ------------------------------------------------------------------

    def _on_config_changed(self, *args: Any) -> None:
        """配置变更回调：更新联动状态并发射 config_changed 信号。"""
        self._update_phase_availability()
        self.config_changed.emit(self.get_config())

    def _update_phase_availability(self) -> None:
        """根据阶段开关更新联动控件的可用状态。

        - 修订阶段未勾选 → max_revise_rounds SpinBox 禁用
        - 验证阶段未勾选 → 验证后暂停禁用
        - 大纲阶段未勾选 → 大纲后暂停禁用
        """
        revise_enabled = self._phase_checks["revise"].isChecked()
        verify_enabled = self._phase_checks["verify"].isChecked()
        outline_enabled = self._phase_checks["outline"].isChecked()

        self._max_revise_spin.setEnabled(revise_enabled)
        self._after_verify_check.setEnabled(verify_enabled)
        self._after_outline_check.setEnabled(outline_enabled)

    # ------------------------------------------------------------------
    # 配置读写
    # ------------------------------------------------------------------

    def get_config(self) -> AgentRunConfig:
        """从 UI 控件读取当前配置并构建 AgentRunConfig。

        Returns:
            当前 AgentRunConfig 对象
        """
        phases = {
            phase: check.isChecked()
            for phase, check in self._phase_checks.items()
        }
        checkpoints = {
            "after_outline": self._after_outline_check.isChecked(),
            "after_verify": self._after_verify_check.isChecked(),
        }
        return AgentRunConfig(
            phases=phases,
            checkpoints=checkpoints,
            max_revise_rounds=self._max_revise_spin.value(),
            per_phase_overrides={},
        )

    # ------------------------------------------------------------------
    # 阶段进度
    # ------------------------------------------------------------------

    def update_phase_progress(
        self, current_phase: str, completed_phases: list[str]
    ) -> None:
        """更新阶段进度指示器。

        Args:
            current_phase: 当前阶段名（analysis/outline/writing/verify/revise），
                空字符串表示无当前阶段（全部完成或未开始）
            completed_phases: 已完成阶段名列表
        """
        completed_set = set(completed_phases)
        for phase in PHASE_ORDER:
            label = self._phase_labels[phase]
            if phase == current_phase:
                set_label_state(label, PHASE_LABELS[phase], _OBJ_PHASE_CURRENT)
            elif phase in completed_set:
                set_label_state(label, f"✓ {PHASE_LABELS[phase]}", _OBJ_PHASE_COMPLETED)
            else:
                set_label_state(label, PHASE_LABELS[phase], _OBJ_PHASE_PENDING)

    # ------------------------------------------------------------------
    # 产物查看
    # ------------------------------------------------------------------

    def update_outline(self, outline: Outline) -> None:
        """更新大纲预览。

        Args:
            outline: 大纲对象
        """
        self._current_outline = outline
        self._outline_edit.setPlainText(format_outline(outline))

    def update_critique(self, critique: CritiqueReport) -> None:
        """更新评审报告。

        Args:
            critique: 评审报告对象
        """
        self._current_critique = critique
        self._critique_edit.setPlainText(format_critique(critique))
        # 有评审报告时自动展开折叠组
        self._critique_group.setChecked(True)

    def get_edited_outline(self) -> Outline | None:
        """获取用户编辑后的大纲。

        解析大纲编辑器文本回 Outline 对象，解析失败返回 None。

        Returns:
            编辑后的 Outline 对象，解析失败或编辑器为空时返回 None
        """
        text = self._outline_edit.toPlainText()
        if not text.strip():
            return None
        return parse_outline(text)

    # ------------------------------------------------------------------
    # 检查点继续按钮
    # ------------------------------------------------------------------

    def show_continue_button(self, checkpoint_name: str) -> None:
        """显示继续按钮。

        用户在检查点对话框选择"编辑"后由主窗口调用：关闭对话框让用户在面板中
        编辑大纲，编辑完成后点击"继续"按钮恢复 orchestrator。

        Args:
            checkpoint_name: 检查点名（after_outline）
        """
        self._current_checkpoint_name = checkpoint_name
        self._continue_btn.show()

    def hide_continue_button(self) -> None:
        """隐藏继续按钮并清空当前检查点名。"""
        self._continue_btn.hide()
        self._current_checkpoint_name = ""

    def _on_continue_clicked(self) -> None:
        """继续按钮点击：发射 continue_requested 信号携带检查点名。

        主窗口接收后从面板读取编辑后的大纲并 resume orchestrator。
        """
        self.continue_requested.emit(self._current_checkpoint_name)

    # ------------------------------------------------------------------
    # 重置
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """重置面板：清空产物查看器，重置进度指示器。"""
        self._current_outline = None
        self._current_critique = None
        self._outline_edit.clear()
        self._critique_edit.clear()
        self._critique_group.setChecked(False)
        # 隐藏继续按钮
        self.hide_continue_button()
        self.update_phase_progress("", [])
