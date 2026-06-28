"""VolumePanel：卷级多章节 Agent 续写流程配置与监控面板。

包含：
- 卷续写配置（预设选择、章节数/分析深度/条目上限/每章字数提示、深度分析 token 切分 ComboBox）
- 审计设置（审计开关 + 7 维度勾选组）
- 逐章设置（逐章验证/逐章修订/每章最大修订轮次）
- 暂停点复选框（深度分析后/卷大纲后/审计后）
- 顶部固定整卷进度条（QProgressBar#volumeProgressBar，24px 高，按深度分析 10%/卷大纲 20%/审计 30%/逐章 30-100% 加权计算）
- 两层进度指示器（卷阶段 4 标签 + 当前章节 第 i/N 章 4 步标签，均同步更新顶部进度条）
- 产物查看器（深度分析预览可编辑、卷大纲预览可编辑、各章产物折叠列表、当前章节正文流式区 QPlainTextEdit 实时承接写作流式增量）

卷模式由 ContinuationPanel.output_panel_visibility_requested(bool) 信号联动隐藏主窗口右侧续写输出面板，空间并入本面板以扩大产物查看区。

Signals:
    config_changed(object): VolumeRunConfig 对象，任意配置变更时发射
    resume(object): checkpoint payload（编辑后的深度分析/卷大纲等），用户确认继续时发射
    cancel_checkpoint(): 用户取消暂停时发射
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from novelforge.models import (
    DEFAULT_AUDIT_DIMENSIONS,
    ChapterArtifacts,
    ChapterPlan,
    DeepAnalysis,
    OutlineAuditReport,
    VALID_ANALYSIS_DEPTHS,
    VolumeOutline,
    VolumeRunConfig,
)
from novelforge.ui.helpers import parse_token_limit, select_combo_by_id, set_label_state
from novelforge.ui.wheel_filter import WheelEventFilter
from novelforge.utils.outline_serializer import format_critique, format_outline

logger = logging.getLogger(__name__)

# 卷阶段顺序（深度分析 → 卷大纲 → 审计 → 逐章）
VOLUME_PHASES: list[str] = [
    "deep_analysis",
    "volume_outline",
    "audit",
    "chapter",
]

# 卷阶段中文标签
VOLUME_PHASE_LABELS: dict[str, str] = {
    "deep_analysis": "深度分析",
    "volume_outline": "卷大纲",
    "audit": "审计",
    "chapter": "逐章",
}

# 章节步骤顺序（细纲 → 写作 → 验证 → 修订）
CHAPTER_STEPS: list[str] = ["outline", "writing", "verify", "revise"]

# 章节步骤中文标签
CHAPTER_STEP_LABELS: dict[str, str] = {
    "outline": "细纲",
    "writing": "写作",
    "verify": "验证",
    "revise": "修订",
}

# 分析深度中文标签与说明（key 与 VALID_ANALYSIS_DEPTHS 对齐）
# 显示文本（下拉项） / 每类清单条目上限 / max_tokens 说明
_ANALYSIS_DEPTH_LABELS: dict[str, str] = {
    "light": "浅度",
    "standard": "标准",
    "thorough": "深度",
    "exhaustive": "详尽",
}

_ANALYSIS_DEPTH_DESCRIPTIONS: dict[str, str] = {
    "light": "每类≤5条，max_tokens 8000，快速分析",
    "standard": "每类≤15条，max_tokens 20000，常规卷续写",
    "thorough": "每类≤30条，max_tokens 50000，复杂前文",
    "exhaustive": "条目不限，max_tokens 不设上限，1M 上下文模型",
}

# 审计维度中文标签（key 与 DEFAULT_AUDIT_DIMENSIONS 对齐）
_AUDIT_DIMENSION_LABELS: dict[str, str] = {
    "consistency": "一致性",
    "pacing": "节奏",
    "engagement": "吸引力",
    "structure": "结构",
    "coherence": "连贯性",
    "foreshadowing": "伏笔",
    "characters": "人物",
}

# 进度指示器状态对象名（由全局 QSS 接管样式，镜像 AgentPanel）
_OBJ_PHASE_CURRENT = "phaseCurrent"
_OBJ_PHASE_COMPLETED = "phaseCompleted"
_OBJ_PHASE_PENDING = "phasePending"


# ----------------------------------------------------------------------
# 深度分析 / 卷大纲 的格式化与宽松解析（outline_serializer 未覆盖此类模型）
# ----------------------------------------------------------------------


def _format_json(obj: Any) -> str:
    """将列表/字典对象格式化为紧凑 JSON 文本，空值返回空串。"""
    if not obj:
        return ""
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(obj)


def _format_deep_analysis(da: DeepAnalysis) -> str:
    """将 DeepAnalysis 转为可读 Markdown 文本。"""
    lines: list[str] = ["# 前文深度分析"]
    lines.append("## 故事状态")
    lines.append(f"- 结构定位：{da.structure_position}")
    lines.append(f"- 基调：{da.tone}")
    lines.append(f"- 核心冲突状态：{da.core_conflict_status}")
    lines.append(f"- 利害关系：{da.stakes}")
    lines.append(f"- 活跃人物：{_format_json(da.active_characters)}")
    lines.append(f"- 活跃剧情线：{_format_json(da.plot_threads)}")
    lines.append(f"- 未兑现承诺：{_format_json(da.unresolved_promises)}")
    lines.append(f"- 世界状态：{da.world_state}")
    lines.append("## 深度分析")
    lines.append(f"- 布局分析：{da.plot_arrangement_analysis}")
    lines.append(f"- 章节结构模式：{da.chapter_structure_pattern}")
    lines.append(f"- 张力曲线模式：{da.tension_curve_pattern}")
    lines.append(f"- 钩子模式：{da.hook_patterns}")
    lines.append(f"- 风格分析：{da.style_analysis}")
    lines.append(f"- 对白分析：{da.dialogue_analysis}")
    lines.append(f"- 节奏分析：{da.pacing_analysis}")
    lines.append(f"- 人物弧光模式：{_format_json(da.character_arc_patterns)}")
    lines.append("## 结构化清单")
    lines.append(f"- 伏笔清单：{_format_json(da.foreshadowing_inventory)}")
    lines.append(f"- 常用桥段：{_format_json(da.common_tropes)}")
    lines.append(f"- 场景设定库：{_format_json(da.settings_database)}")
    lines.append(f"- 复现元素：{_format_json(da.recurring_elements)}")
    lines.append(f"- 关键短语：{_format_json(da.key_phrases)}")
    return "\n".join(lines)


def _format_volume_outline(vo: VolumeOutline) -> str:
    """将 VolumeOutline 转为可读 Markdown 文本。"""
    lines: list[str] = ["# 卷大纲"]
    lines.append("## 卷信息")
    lines.append(f"- 卷标题：{vo.volume_title}")
    lines.append(f"- 卷目标：{vo.volume_goals}")
    lines.append(f"- 布局分析：{vo.plot_arrangement_analysis}")
    lines.append(f"- 节奏计划：{vo.pacing_plan}")
    lines.append(f"- 伏笔计划：{vo.foreshadowing_plan}")
    lines.append(f"- 章节数：{vo.chapter_count}")
    lines.append("## 章节计划")
    for ch in vo.chapters:
        lines.append(f"### 第 {ch.index} 章：{ch.title}")
        lines.append(f"- 摘要：{ch.summary}")
        lines.append(f"- 剧情角色：{ch.plot_role}")
        lines.append(
            f"- 关键事件：{'；'.join(ch.key_events) if ch.key_events else ''}"
        )
        lines.append(
            f"- 涉及人物：{'；'.join(ch.characters_involved) if ch.characters_involved else ''}"
        )
        lines.append(f"- 伏笔：{ch.foreshadowing}")
        lines.append(f"- 章节钩子：{ch.chapter_hook}")
        lines.append(f"- 目标字数：{ch.target_words}")
    return "\n".join(lines)


def _format_audit_report(report: OutlineAuditReport, round_idx: int = 0) -> str:
    """将 OutlineAuditReport 转为可读 Markdown 文本（含轮次信息）。

    Args:
        report: 大纲审计报告
        round_idx: 审计轮次（0 基，用于标题显示）
    """
    lines: list[str] = [f"# 审计报告（第 {round_idx + 1} 轮）"]
    lines.append(f"- 总体评估：{report.overall_assessment}")
    lines.append(f"- 是否通过：{'通过' if report.passed else '不通过'}")
    lines.append("## 各维度审计")
    for dim in report.dimensions:
        lines.append(f"### {dim.dimension}（得分：{dim.score}/10）")
        if dim.issues:
            lines.append("- 问题：")
            for issue in dim.issues:
                lines.append(f"  - {issue}")
        if dim.suggestions:
            lines.append("- 建议：")
            for s in dim.suggestions:
                lines.append(f"  - {s}")
    return "\n".join(lines)


def _extract_field(text: str, field_name: str) -> str:
    """从 Markdown 文本中提取 "- field_name：value" 字段值（宽松，支持多行）。

    值延续至下一个行首 "- "、"## "、"# " 或文本结尾。
    """
    pattern = rf"- {re.escape(field_name)}：(.*?)(?=\n- |\n## |\n# |\Z)"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _parse_json_list(text: str) -> list[Any]:
    """宽松解析 JSON 列表，失败返回空列表。"""
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def parse_deep_analysis(text: str) -> DeepAnalysis | None:
    """从编辑后的文本解析回 DeepAnalysis（宽松解析，失败返回 None）。"""
    if not text.strip():
        return None
    try:
        return DeepAnalysis(
            structure_position=_extract_field(text, "结构定位"),
            tone=_extract_field(text, "基调"),
            core_conflict_status=_extract_field(text, "核心冲突状态"),
            stakes=_extract_field(text, "利害关系"),
            active_characters=_parse_json_list(_extract_field(text, "活跃人物")),
            plot_threads=_parse_json_list(_extract_field(text, "活跃剧情线")),
            unresolved_promises=_parse_json_list(_extract_field(text, "未兑现承诺")),
            world_state=_extract_field(text, "世界状态"),
            plot_arrangement_analysis=_extract_field(text, "布局分析"),
            chapter_structure_pattern=_extract_field(text, "章节结构模式"),
            tension_curve_pattern=_extract_field(text, "张力曲线模式"),
            hook_patterns=_extract_field(text, "钩子模式"),
            style_analysis=_extract_field(text, "风格分析"),
            dialogue_analysis=_extract_field(text, "对白分析"),
            pacing_analysis=_extract_field(text, "节奏分析"),
            character_arc_patterns=_parse_json_list(_extract_field(text, "人物弧光模式")),
            foreshadowing_inventory=_parse_json_list(_extract_field(text, "伏笔清单")),
            common_tropes=_parse_json_list(_extract_field(text, "常用桥段")),
            settings_database=_parse_json_list(_extract_field(text, "场景设定库")),
            recurring_elements=_parse_json_list(_extract_field(text, "复现元素")),
            key_phrases=_parse_json_list(_extract_field(text, "关键短语")),
        )
    except Exception:
        logger.debug("解析 DeepAnalysis 失败", exc_info=True)
        return None


def _parse_int(text: str, default: int = 0) -> int:
    """宽松解析整数，失败返回默认值。"""
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return default


def parse_volume_outline(text: str) -> VolumeOutline | None:
    """从编辑后的文本解析回 VolumeOutline（宽松解析，失败返回 None）。"""
    if not text.strip():
        return None
    try:
        chapter_count = _parse_int(_extract_field(text, "章节数"), 0)
        chapters: list[ChapterPlan] = []
        # 按 "### 第 N 章：标题" 切分章节块
        chapter_blocks = re.split(r"(?=### 第 \d+ 章：)", text)
        for block in chapter_blocks:
            if not block.strip().startswith("### 第"):
                continue
            ch = _parse_chapter_plan_block(block)
            if ch is not None:
                chapters.append(ch)
        return VolumeOutline(
            volume_title=_extract_field(text, "卷标题"),
            volume_goals=_extract_field(text, "卷目标"),
            plot_arrangement_analysis=_extract_field(text, "布局分析"),
            pacing_plan=_extract_field(text, "节奏计划"),
            foreshadowing_plan=_extract_field(text, "伏笔计划"),
            chapter_count=chapter_count,
            chapters=chapters,
        )
    except Exception:
        logger.debug("解析 VolumeOutline 失败", exc_info=True)
        return None


def _parse_chapter_plan_block(block: str) -> ChapterPlan | None:
    """从单个章节块解析 ChapterPlan 对象。

    Args:
        block: 以 "### 第 N 章：标题" 开头的文本块

    Returns:
        ChapterPlan 对象，解析失败返回 None
    """
    try:
        # 提取 "### 第 N 章：标题"
        header = re.match(r"### 第 (\d+) 章：(.*)", block)
        if not header:
            return None
        index = int(header.group(1))
        title = header.group(2).strip()
        return ChapterPlan(
            index=index,
            title=title,
            summary=_extract_field(block, "摘要"),
            plot_role=_extract_field(block, "剧情角色"),
            key_events=[
                s.strip()
                for s in _extract_field(block, "关键事件").split("；")
                if s.strip()
            ],
            characters_involved=[
                s.strip()
                for s in _extract_field(block, "涉及人物").split("；")
                if s.strip()
            ],
            foreshadowing=_extract_field(block, "伏笔"),
            chapter_hook=_extract_field(block, "章节钩子"),
            target_words=_parse_int(_extract_field(block, "目标字数"), 2000),
        )
    except Exception:
        return None


class VolumePanel(QWidget):
    """卷级多章节 Agent 续写流程配置与监控面板。

    提供卷续写配置、审计与逐章设置、暂停点、两层进度指示与产物查看。
    配置变更时构建 VolumeRunConfig 并发射 config_changed 信号。

    Signals:
        config_changed(object): VolumeRunConfig 对象
        resume(object): checkpoint payload（编辑后的深度分析/卷大纲等）
        cancel_checkpoint(): 取消暂停
        continue_requested(str): 用户在检查点选择编辑后，点击"继续"按钮时发射，
            携带检查点名（after_deep_analysis/after_volume_outline/after_audit）
    """

    config_changed = Signal(object)
    resume = Signal(object)
    cancel_checkpoint = Signal()
    continue_requested = Signal(str, object)

    def __init__(self, parent=None) -> None:
        """初始化卷续写面板。

        Args:
            parent: 父窗口
        """
        super().__init__(parent)
        self._current_deep_analysis: DeepAnalysis | None = None
        self._current_volume_outline: VolumeOutline | None = None
        self._current_audit_report: OutlineAuditReport | None = None
        self._audit_reports: list[OutlineAuditReport] = []
        self._chapter_groups: dict[int, QGroupBox] = {}
        self._current_checkpoint_name: str = ""
        # 回填配置时阻塞 config_changed 信号（避免回填触发保存循环）
        self._loading_config: bool = False

        self._setup_ui()
        self._setup_connections()
        self._update_linkage_state()
        self._update_depth_hint()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """构建 UI。

        整体用 QScrollArea 包裹，避免窄高面板内容挤压；配置组并入暂停点；
        审计与逐章设置横排节省竖向空间；产物查看器用 QTabWidget 切换
        深度分析/卷大纲/各章产物/当前章节正文，避免多个大控件同时竖向排列。
        顶部固定整卷进度条（不随滚动）。
        """
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 顶部整卷进度条（不滚动，始终可见）
        self._volume_progress_bar = QProgressBar()
        self._volume_progress_bar.setObjectName("volumeProgressBar")
        self._volume_progress_bar.setRange(0, 100)
        self._volume_progress_bar.setValue(0)
        self._volume_progress_bar.setTextVisible(True)
        self._volume_progress_bar.setFixedHeight(24)
        self._volume_progress_bar.setFormat("准备中...")
        outer.addWidget(self._volume_progress_bar)

        # 继续按钮：检查点选择"编辑"后显示，用户在面板编辑产物后点击恢复
        self._continue_btn = QPushButton("继续")
        self._continue_btn.setObjectName("primaryBtn")
        self._continue_btn.hide()
        self._continue_btn.clicked.connect(self._on_continue_clicked)
        outer.addWidget(self._continue_btn)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        layout.addWidget(self._build_config_group())
        layout.addWidget(self._build_audit_chapter_group())
        layout.addWidget(self._build_progress_group())
        layout.addWidget(self._build_artifacts_group(), 1)
        layout.addStretch(0)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        # 安装滚轮事件过滤器
        self._wheel_filter = WheelEventFilter(self)
        for combo in (self._preset_combo, self._analysis_depth_combo, self._analysis_chunk_tokens_combo, self._pacing_speed_combo):
            combo.installEventFilter(self._wheel_filter)
        for spin in (self._chapter_count_spin, self._max_entries_spin, self._max_revise_spin, self._target_words_spin, self._audit_rounds_spin):
            spin.installEventFilter(self._wheel_filter)

    def _build_config_group(self) -> QGroupBox:
        """构建卷续写配置组（章节数/分析深度/条目上限/每章字数 + 暂停点）。"""
        group = QGroupBox("卷续写配置")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        # 预设行：预设选择（卷模式独立预设）
        preset_row = QGridLayout()
        preset_row.setHorizontalSpacing(8)
        preset_row.setVerticalSpacing(4)
        preset_row.addWidget(QLabel("预设:"), 0, 0)
        self._preset_combo = QComboBox()
        self._preset_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._preset_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._preset_combo.addItem("默认预设", "default")
        preset_row.addWidget(self._preset_combo, 0, 1)
        preset_row.setColumnStretch(0, 0)
        preset_row.setColumnStretch(1, 1)
        layout.addLayout(preset_row)

        # 第一行：章节数 + 分析深度
        row1 = QGridLayout()
        row1.setHorizontalSpacing(8)
        row1.setVerticalSpacing(4)
        row1.addWidget(QLabel("章节数:"), 0, 0)
        self._chapter_count_spin = QSpinBox()
        self._chapter_count_spin.setRange(2, 20)
        self._chapter_count_spin.setValue(5)
        row1.addWidget(self._chapter_count_spin, 0, 1)
        row1.addWidget(QLabel("分析深度:"), 0, 2)
        self._analysis_depth_combo = QComboBox()
        # 适配内容宽度，确保下拉框能完整显示当前项文字
        self._analysis_depth_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._analysis_depth_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        for depth in VALID_ANALYSIS_DEPTHS:
            # 下拉项仅显示简短标签，详细说明由下方 _analysis_depth_hint 行展示
            self._analysis_depth_combo.addItem(
                _ANALYSIS_DEPTH_LABELS[depth], depth
            )
        # 默认 standard
        for i in range(self._analysis_depth_combo.count()):
            if self._analysis_depth_combo.itemData(i) == "standard":
                self._analysis_depth_combo.setCurrentIndex(i)
                break
        row1.addWidget(self._analysis_depth_combo, 0, 3)
        # 列拉伸：下拉框列(3)占剩余空间，标签/输入框列不拉伸
        row1.setColumnStretch(0, 0)
        row1.setColumnStretch(1, 0)
        row1.setColumnStretch(2, 0)
        row1.setColumnStretch(3, 1)
        layout.addLayout(row1)

        # 分析深度说明行（跟随选中项变化，提示当前档位含义）
        self._analysis_depth_hint = QLabel("")
        self._analysis_depth_hint.setObjectName("metaText")
        self._analysis_depth_hint.setWordWrap(True)
        layout.addWidget(self._analysis_depth_hint)

        # 第二行：条目上限 + 每章字数
        row2 = QGridLayout()
        row2.setHorizontalSpacing(8)
        row2.setVerticalSpacing(4)
        row2.addWidget(QLabel("条目上限:"), 0, 0)
        self._max_entries_spin = QSpinBox()
        self._max_entries_spin.setRange(0, 100)
        self._max_entries_spin.setValue(0)
        self._max_entries_spin.setToolTip(
            "分析条目上限；0 表示按分析深度自动确定"
        )
        row2.addWidget(self._max_entries_spin, 0, 1)
        row2.addWidget(QLabel("每章字数:"), 0, 2)
        self._target_words_spin = QSpinBox()
        self._target_words_spin.setRange(500, 20000)
        self._target_words_spin.setValue(2000)
        self._target_words_spin.setSingleStep(500)
        self._target_words_spin.setToolTip("每章目标字数（500-20000）")
        row2.addWidget(self._target_words_spin, 0, 3)
        # 列拉伸：与 row1 对齐，只读标签列(3)占剩余空间
        row2.setColumnStretch(0, 0)
        row2.setColumnStretch(1, 0)
        row2.setColumnStretch(2, 0)
        row2.setColumnStretch(3, 1)
        layout.addLayout(row2)

        # 第三行：分析切分 tokens（不限制=0 全量发送，>0=按该 token 数切分）
        row3 = QGridLayout()
        row3.setHorizontalSpacing(8)
        row3.setVerticalSpacing(4)
        row3.addWidget(QLabel("切分 tokens:"), 0, 0)
        self._analysis_chunk_tokens_combo = QComboBox()
        self._analysis_chunk_tokens_combo.addItems(
            ["不限制", "50k", "100k", "250k", "500k"]
        )
        # 默认"100k"（index 2），启用 token 切分与增量更新
        self._analysis_chunk_tokens_combo.setCurrentIndex(2)
        self._analysis_chunk_tokens_combo.setMinimumWidth(100)
        self._analysis_chunk_tokens_combo.setToolTip(
            "不限制=全量发送（前文过长可能超上下文），"
            "50k/100k/250k/500k=按该 token 数切分前文章节逐块分析并增量合并（默认 100k）"
        )
        row3.addWidget(self._analysis_chunk_tokens_combo, 0, 1)
        row3.setColumnStretch(0, 0)
        row3.setColumnStretch(1, 1)
        layout.addLayout(row3)

        # 第四行：推进速度 + 审计轮次
        row4 = QGridLayout()
        row4.setHorizontalSpacing(8)
        row4.setVerticalSpacing(4)
        row4.addWidget(QLabel("推进速度:"), 0, 0)
        self._pacing_speed_combo = QComboBox()
        self._pacing_speed_combo.addItem("缓速", "slow")
        self._pacing_speed_combo.addItem("中速", "medium")
        self._pacing_speed_combo.addItem("快速", "fast")
        # 默认中速（index 1）
        self._pacing_speed_combo.setCurrentIndex(1)
        self._pacing_speed_combo.setToolTip(
            "缓速：同一场景 1-2 章\n中速：一个场景半章左右\n快速：按目前设定"
        )
        row4.addWidget(self._pacing_speed_combo, 0, 1)
        row4.addWidget(QLabel("审计轮次:"), 0, 2)
        self._audit_rounds_spin = QSpinBox()
        self._audit_rounds_spin.setRange(1, 3)
        self._audit_rounds_spin.setValue(1)
        self._audit_rounds_spin.setToolTip("大纲审计轮次（1-3）")
        row4.addWidget(self._audit_rounds_spin, 0, 3)
        row4.setColumnStretch(0, 0)
        row4.setColumnStretch(1, 0)
        row4.setColumnStretch(2, 0)
        row4.setColumnStretch(3, 1)
        layout.addLayout(row4)

        # 暂停点（并入配置组底部，避免单独成组浪费空间）
        checkpoint_label = QLabel("暂停点:")
        checkpoint_label.setObjectName("textSecondary")
        layout.addWidget(checkpoint_label)
        checkpoint_row = QHBoxLayout()
        checkpoint_row.setSpacing(8)
        self._after_deep_analysis_check = QCheckBox("分析后")
        self._after_deep_analysis_check.setChecked(False)
        self._after_volume_outline_check = QCheckBox("大纲后")
        self._after_volume_outline_check.setChecked(False)
        self._before_audit_check = QCheckBox("审计前")
        self._before_audit_check.setChecked(True)
        self._after_audit_check = QCheckBox("审计后")
        self._after_audit_check.setChecked(False)
        checkpoint_row.addWidget(self._after_deep_analysis_check)
        checkpoint_row.addWidget(self._after_volume_outline_check)
        checkpoint_row.addWidget(self._before_audit_check)
        checkpoint_row.addWidget(self._after_audit_check)
        checkpoint_row.addStretch(1)
        layout.addLayout(checkpoint_row)

        return group

    def _build_audit_chapter_group(self) -> QGroupBox:
        """构建审计与逐章设置组（横排节省竖向空间）。"""
        group = QGroupBox("审计与逐章设置")
        outer = QVBoxLayout(group)
        outer.setSpacing(6)

        # 审计开关 + 逐章验证/修订开关横排
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(12)
        self._audit_check = QCheckBox("启用大纲审计")
        self._audit_check.setChecked(True)
        self._chapter_verify_check = QCheckBox("逐章验证")
        self._chapter_verify_check.setChecked(True)
        self._chapter_revise_check = QCheckBox("逐章修订")
        self._chapter_revise_check.setChecked(True)
        toggle_row.addWidget(self._audit_check)
        toggle_row.addWidget(self._chapter_verify_check)
        toggle_row.addWidget(self._chapter_revise_check)
        toggle_row.addStretch(1)
        outer.addLayout(toggle_row)

        # 审计维度勾选组（7 维度，4 列网格）
        dims_widget = QWidget()
        dims_layout = QGridLayout(dims_widget)
        dims_layout.setContentsMargins(20, 0, 0, 0)
        dims_layout.setHorizontalSpacing(8)
        dims_layout.setVerticalSpacing(2)
        self._audit_dim_checks: dict[str, QCheckBox] = {}
        for i, key in enumerate(DEFAULT_AUDIT_DIMENSIONS):
            check = QCheckBox(_AUDIT_DIMENSION_LABELS[key])
            check.setChecked(True)
            dims_layout.addWidget(check, i // 4, i % 4)
            self._audit_dim_checks[key] = check
        outer.addWidget(dims_widget)

        # 每章最大修订轮次（单独一行，靠右）
        revise_row = QHBoxLayout()
        revise_row.addWidget(QLabel("每章最大修订轮次:"))
        self._max_revise_spin = QSpinBox()
        self._max_revise_spin.setRange(1, 3)
        self._max_revise_spin.setValue(1)
        revise_row.addWidget(self._max_revise_spin)
        revise_row.addStretch(1)
        outer.addLayout(revise_row)

        return group

    def _build_progress_group(self) -> QGroupBox:
        """构建两层进度指示器组。"""
        group = QGroupBox("进度")
        layout = QVBoxLayout(group)

        # 卷阶段进度（4 标签横向）
        volume_phase_row = QHBoxLayout()
        self._volume_phase_labels: dict[str, QLabel] = {}
        for phase in VOLUME_PHASES:
            label = QLabel(VOLUME_PHASE_LABELS[phase])
            label.setObjectName(_OBJ_PHASE_PENDING)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            volume_phase_row.addWidget(label)
            self._volume_phase_labels[phase] = label
        layout.addLayout(volume_phase_row)

        # 当前章节进度（第 i/N 章 + 4 步标签横向）
        chapter_row = QHBoxLayout()
        self._chapter_index_label = QLabel("章节进度：未开始")
        self._chapter_index_label.setObjectName("textSecondary")
        chapter_row.addWidget(self._chapter_index_label)
        self._chapter_step_labels: dict[str, QLabel] = {}
        for step in CHAPTER_STEPS:
            label = QLabel(CHAPTER_STEP_LABELS[step])
            label.setObjectName(_OBJ_PHASE_PENDING)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chapter_row.addWidget(label)
            self._chapter_step_labels[step] = label
        layout.addLayout(chapter_row)

        return group

    def _build_artifacts_group(self) -> QGroupBox:
        """构建产物查看组（QTabWidget 切换深度分析/卷大纲/各章产物）。

        用 Tab 切换避免三个大控件同时竖向排列挤压配置区。
        """
        group = QGroupBox("产物查看")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._artifacts_tabs = QTabWidget()

        # Tab 1：深度分析预览（可编辑）
        da_tab = QWidget()
        da_layout = QVBoxLayout(da_tab)
        da_layout.setContentsMargins(0, 0, 0, 0)
        da_hint = QLabel("可编辑，暂停点恢复时读取此处内容")
        da_hint.setObjectName("metaText")
        da_layout.addWidget(da_hint)
        self._deep_analysis_edit = QPlainTextEdit()
        self._deep_analysis_edit.setPlaceholderText(
            "深度分析生成后将显示在此处，可编辑..."
        )
        self._deep_analysis_edit.setMinimumHeight(200)
        da_layout.addWidget(self._deep_analysis_edit)
        self._artifacts_tabs.addTab(da_tab, "深度分析")

        # Tab 2：卷大纲预览（可编辑）
        vo_tab = QWidget()
        vo_layout = QVBoxLayout(vo_tab)
        vo_layout.setContentsMargins(0, 0, 0, 0)
        vo_hint = QLabel("可编辑，审计后更新为终稿")
        vo_hint.setObjectName("metaText")
        vo_layout.addWidget(vo_hint)
        self._volume_outline_edit = QPlainTextEdit()
        self._volume_outline_edit.setPlaceholderText(
            "卷大纲生成后将显示在此处，可编辑..."
        )
        self._volume_outline_edit.setMinimumHeight(200)
        vo_layout.addWidget(self._volume_outline_edit)

        # 审计重点输入区（before_audit 检查点触发时显示，默认隐藏）
        self._audit_focus_group = QGroupBox("审计重点（before_audit 检查点）")
        self._audit_focus_group.setVisible(False)
        af_layout = QVBoxLayout(self._audit_focus_group)
        af_layout.setContentsMargins(4, 4, 4, 4)
        af_hint = QLabel(
            "请在上方卷大纲中查看后，输入需着重审计的部分"
            "（如\"第3章人物动机不一致\"、\"伏笔回收过快\"等），"
            "将随审计提示词一并发送给审计 AI。\n留空则按各维度均衡审计。"
        )
        af_hint.setWordWrap(True)
        af_hint.setObjectName("metaText")
        af_layout.addWidget(af_hint)
        self._audit_focus_edit = QPlainTextEdit()
        self._audit_focus_edit.setMinimumHeight(80)
        self._audit_focus_edit.setPlaceholderText(
            "例如：第3章主角动机转变过于突兀；伏笔回收节奏过快；..."
        )
        af_layout.addWidget(self._audit_focus_edit)
        af_btn_layout = QHBoxLayout()
        self._audit_focus_start_btn = QPushButton("开始审计")
        self._audit_focus_start_btn.clicked.connect(self._on_audit_focus_start)
        self._audit_focus_cancel_btn = QPushButton("取消续写")
        self._audit_focus_cancel_btn.clicked.connect(self._on_audit_focus_cancel)
        af_btn_layout.addWidget(self._audit_focus_start_btn)
        af_btn_layout.addStretch()
        af_btn_layout.addWidget(self._audit_focus_cancel_btn)
        af_layout.addLayout(af_btn_layout)
        vo_layout.addWidget(self._audit_focus_group)

        self._artifacts_tabs.addTab(vo_tab, "卷大纲")

        # Tab 3：审计报告（上半审计维度/分数/问题/建议，下半修订版大纲预览，均只读）
        ar_tab = QWidget()
        ar_layout = QVBoxLayout(ar_tab)
        ar_layout.setContentsMargins(0, 0, 0, 0)
        ar_layout.setSpacing(4)
        ar_report_label = QLabel("审计报告内容（维度/分数/问题/建议）")
        ar_report_label.setObjectName("metaText")
        ar_layout.addWidget(ar_report_label)
        self._audit_report_edit = QPlainTextEdit()
        self._audit_report_edit.setPlaceholderText(
            "大纲审计完成后将显示在此处（只读）..."
        )
        self._audit_report_edit.setReadOnly(True)
        self._audit_report_edit.setMinimumHeight(120)
        ar_layout.addWidget(self._audit_report_edit)
        ar_revised_label = QLabel("修订版大纲预览（只读，终稿生成后卷大纲 Tab 更新为终稿）")
        ar_revised_label.setObjectName("metaText")
        ar_layout.addWidget(ar_revised_label)
        self._revised_outline_edit = QPlainTextEdit()
        self._revised_outline_edit.setPlaceholderText(
            "审计修订后的大纲将显示在此处（只读）..."
        )
        self._revised_outline_edit.setReadOnly(True)
        self._revised_outline_edit.setMinimumHeight(120)
        ar_layout.addWidget(self._revised_outline_edit)
        self._artifacts_tabs.addTab(ar_tab, "审计报告")

        # Tab 4：各章产物折叠列表（QScrollArea 内动态添加可折叠 QGroupBox）
        ch_tab = QWidget()
        ch_layout = QVBoxLayout(ch_tab)
        ch_layout.setContentsMargins(0, 0, 0, 0)
        self._chapters_scroll = QScrollArea()
        self._chapters_scroll.setWidgetResizable(True)
        self._chapters_scroll_content = QWidget()
        self._chapters_scroll_layout = QVBoxLayout(self._chapters_scroll_content)
        self._chapters_scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._chapters_scroll_layout.setSpacing(4)
        self._chapters_scroll_layout.addStretch(1)
        self._chapters_scroll.setWidget(self._chapters_scroll_content)
        ch_layout.addWidget(self._chapters_scroll)
        self._artifacts_tabs.addTab(ch_tab, "各章产物")

        # Tab 4：当前章节正文流式区（写作流式增量直接显示在此处，
        # 不再路由到右侧续写输出面板，避免与单次/Agent 模式输出冲突）
        self._chapter_stream_tab = QWidget()
        cs_layout = QVBoxLayout(self._chapter_stream_tab)
        cs_layout.setContentsMargins(0, 0, 0, 0)
        self._chapter_stream_edit = QPlainTextEdit()
        self._chapter_stream_edit.setObjectName("volumeChapterStream")
        self._chapter_stream_edit.setReadOnly(True)
        self._chapter_stream_edit.setPlaceholderText(
            "章节正文生成后将流式显示在此处..."
        )
        self._chapter_stream_edit.setMinimumHeight(200)
        cs_layout.addWidget(self._chapter_stream_edit)
        self._artifacts_tabs.addTab(self._chapter_stream_tab, "当前章节正文")

        layout.addWidget(self._artifacts_tabs)
        return group

    def _setup_connections(self) -> None:
        """连接信号。"""
        # 卷续写配置
        self._preset_combo.currentIndexChanged.connect(self._on_config_changed)
        self._chapter_count_spin.valueChanged.connect(self._on_config_changed)
        self._analysis_depth_combo.currentIndexChanged.connect(
            self._on_config_changed
        )
        self._max_entries_spin.valueChanged.connect(self._on_config_changed)
        self._analysis_chunk_tokens_combo.currentIndexChanged.connect(
            self._on_config_changed
        )
        self._target_words_spin.valueChanged.connect(self._on_config_changed)
        self._pacing_speed_combo.currentIndexChanged.connect(self._on_config_changed)
        self._audit_rounds_spin.valueChanged.connect(self._on_config_changed)

        # 审计与逐章设置
        self._audit_check.toggled.connect(self._on_config_changed)
        for check in self._audit_dim_checks.values():
            check.toggled.connect(self._on_config_changed)
        self._chapter_verify_check.toggled.connect(self._on_config_changed)
        self._chapter_revise_check.toggled.connect(self._on_config_changed)
        self._max_revise_spin.valueChanged.connect(self._on_config_changed)

        # 暂停点
        self._after_deep_analysis_check.toggled.connect(self._on_config_changed)
        self._after_volume_outline_check.toggled.connect(self._on_config_changed)
        self._before_audit_check.toggled.connect(self._on_config_changed)
        self._after_audit_check.toggled.connect(self._on_config_changed)

    # ------------------------------------------------------------------
    # 配置变更处理
    # ------------------------------------------------------------------

    def _on_config_changed(self, *args: Any) -> None:
        """配置变更回调：更新联动状态并发射 config_changed 信号。"""
        if self._loading_config:
            return
        self._update_linkage_state()
        self._update_depth_hint()
        self.config_changed.emit(self.get_config())

    def _update_depth_hint(self) -> None:
        """根据当前选中的分析深度更新说明文本。"""
        depth = self._analysis_depth_combo.currentData()
        desc = _ANALYSIS_DEPTH_DESCRIPTIONS.get(depth, "")
        label = _ANALYSIS_DEPTH_LABELS.get(depth, "")
        if desc:
            self._analysis_depth_hint.setText(f"{label}：{desc}")
        else:
            self._analysis_depth_hint.setText("")

    def _update_linkage_state(self) -> None:
        """根据开关更新联动控件的可用状态。

        - 审计关闭 → 审计维度勾选组禁用
        - 逐章验证关闭 → 逐章修订禁用
        - 逐章修订关闭 → 每章最大修订轮次禁用
        """
        audit_enabled = self._audit_check.isChecked()
        verify_enabled = self._chapter_verify_check.isChecked()
        revise_enabled = (
            self._chapter_revise_check.isChecked() and verify_enabled
        )

        for check in self._audit_dim_checks.values():
            check.setEnabled(audit_enabled)

        self._chapter_revise_check.setEnabled(verify_enabled)
        self._max_revise_spin.setEnabled(revise_enabled)

    # ------------------------------------------------------------------
    # 配置读写
    # ------------------------------------------------------------------

    def get_config(self) -> VolumeRunConfig:
        """从 UI 控件读取当前配置并构建 VolumeRunConfig。

        Returns:
            当前 VolumeRunConfig 对象
        """
        audit_dims = [
            key
            for key in DEFAULT_AUDIT_DIMENSIONS
            if self._audit_dim_checks[key].isChecked()
        ]
        checkpoints = {
            "after_deep_analysis": self._after_deep_analysis_check.isChecked(),
            "after_volume_outline": self._after_volume_outline_check.isChecked(),
            "before_audit": self._before_audit_check.isChecked(),
            "after_audit": self._after_audit_check.isChecked(),
        }
        return VolumeRunConfig(
            chapter_count=self._chapter_count_spin.value(),
            target_words_per_chapter=self._target_words_spin.value(),
            analysis_depth=self._analysis_depth_combo.currentData(),
            max_analysis_entries=self._max_entries_spin.value(),
            analysis_chunk_tokens=parse_token_limit(self._analysis_chunk_tokens_combo.currentText()),
            enable_outline_audit=self._audit_check.isChecked(),
            audit_dimensions=audit_dims,
            audit_rounds=self._audit_rounds_spin.value(),
            pacing_speed=self._pacing_speed_combo.currentData(),
            enable_chapter_verify=self._chapter_verify_check.isChecked(),
            enable_chapter_revise=self._chapter_revise_check.isChecked(),
            max_revise_rounds_per_chapter=self._max_revise_spin.value(),
            checkpoints=checkpoints,
        )

    def set_config(self, config: VolumeRunConfig) -> None:
        """从 VolumeRunConfig 回填到 UI 控件（阻塞信号避免触发保存循环）。

        Args:
            config: 要应用的卷续写配置
        """
        self._loading_config = True
        try:
            self._chapter_count_spin.setValue(config.chapter_count)
            self._target_words_spin.setValue(config.target_words_per_chapter)
            # analysis_depth：按 currentData 匹配
            for i in range(self._analysis_depth_combo.count()):
                if self._analysis_depth_combo.itemData(i) == config.analysis_depth:
                    self._analysis_depth_combo.setCurrentIndex(i)
                    break
            self._max_entries_spin.setValue(config.max_analysis_entries)
            # analysis_chunk_tokens：数值反查文本
            token_text = self._token_value_to_text(config.analysis_chunk_tokens)
            idx = self._analysis_chunk_tokens_combo.findText(token_text)
            if idx >= 0:
                self._analysis_chunk_tokens_combo.setCurrentIndex(idx)
            # pacing_speed：按 currentData 匹配
            for i in range(self._pacing_speed_combo.count()):
                if self._pacing_speed_combo.itemData(i) == config.pacing_speed:
                    self._pacing_speed_combo.setCurrentIndex(i)
                    break
            self._audit_rounds_spin.setValue(config.audit_rounds)
            self._audit_check.setChecked(config.enable_outline_audit)
            # 审计维度
            for key, check in self._audit_dim_checks.items():
                check.setChecked(key in config.audit_dimensions)
            self._chapter_verify_check.setChecked(config.enable_chapter_verify)
            self._chapter_revise_check.setChecked(config.enable_chapter_revise)
            self._max_revise_spin.setValue(config.max_revise_rounds_per_chapter)
            # 检查点
            cps = config.checkpoints
            self._after_deep_analysis_check.setChecked(cps.get("after_deep_analysis", False))
            self._after_volume_outline_check.setChecked(cps.get("after_volume_outline", False))
            self._before_audit_check.setChecked(cps.get("before_audit", True))
            self._after_audit_check.setChecked(cps.get("after_audit", False))
            # 联动状态与提示更新
            self._update_linkage_state()
            self._update_depth_hint()
        finally:
            self._loading_config = False

    def _token_value_to_text(self, value: int) -> str:
        """将 analysis_chunk_tokens 数值反查为下拉框文本。"""
        mapping = {0: "不限制", 50000: "50k", 100000: "100k", 250000: "250k", 500000: "500k"}
        return mapping.get(value, "不限制")

    # ------------------------------------------------------------------
    # 预设管理
    # ------------------------------------------------------------------

    def set_presets(self, presets: list[dict], default_id: str = "default") -> None:
        """设置预设列表。

        Args:
            presets: 预设列表，每项为 {"id": str, "name": str}
            default_id: 默认选中的预设 ID
        """
        self._preset_combo.clear()
        for preset in presets:
            self._preset_combo.addItem(
                preset.get("name", preset.get("id", "")),
                preset.get("id", ""),
            )
        select_combo_by_id(self._preset_combo, default_id)

    def get_selected_preset_id(self) -> str:
        """获取选中的预设 ID。

        Returns:
            当前选中预设 ID，未选中时返回 "default"
        """
        data = self._preset_combo.currentData()
        return data if data else "default"

    # ------------------------------------------------------------------
    # 进度指示
    # ------------------------------------------------------------------

    def update_volume_progress(self, percent: int, text: str) -> None:
        """更新整卷进度条。

        Args:
            percent: 进度百分比（0-100）
            text: 进度条显示文本
        """
        self._volume_progress_bar.setValue(percent)
        self._volume_progress_bar.setFormat(text)

    def update_volume_phase_progress(
        self, current_phase: str, completed_phases: list[str]
    ) -> None:
        """更新卷阶段进度指示器。

        Args:
            current_phase: 当前卷阶段名
                （deep_analysis/volume_outline/audit/chapter），
                空字符串表示无当前阶段（全部完成或未开始）
            completed_phases: 已完成卷阶段名列表
        """
        completed_set = set(completed_phases)
        for phase in VOLUME_PHASES:
            label = self._volume_phase_labels[phase]
            if phase == current_phase:
                set_label_state(
                    label, VOLUME_PHASE_LABELS[phase], _OBJ_PHASE_CURRENT
                )
            elif phase in completed_set:
                set_label_state(
                    label,
                    f"✓ {VOLUME_PHASE_LABELS[phase]}",
                    _OBJ_PHASE_COMPLETED,
                )
            else:
                set_label_state(
                    label, VOLUME_PHASE_LABELS[phase], _OBJ_PHASE_PENDING
                )

        # 计算整卷进度条百分比与文本
        # 权重：deep_analysis 10% / volume_outline 20% / audit 30% / chapter 30-100%
        percent = 0
        if "deep_analysis" in completed_set:
            percent = max(percent, 10)
        if "volume_outline" in completed_set:
            percent = max(percent, 20)
        if "audit" in completed_set:
            percent = max(percent, 30)
        if current_phase == "chapter":
            # 逐章阶段基线 30%，章节级加权由 update_chapter_progress 单独更新
            percent = max(percent, 30)
            text = f"逐章写作中... {percent}%"
        elif current_phase in VOLUME_PHASE_LABELS:
            text = f"{VOLUME_PHASE_LABELS[current_phase]}中... {percent}%"
        else:
            # current_phase 为空（阶段完成回调或未开始）
            if percent == 0:
                text = "准备中..."
            else:
                text = f"已完成 {percent}%"
        self.update_volume_progress(percent, text)

    def update_chapter_progress(
        self,
        chapter_index: int,
        total: int,
        current_step: str,
        completed_steps: list[str],
    ) -> None:
        """更新当前章节进度指示器。

        Args:
            chapter_index: 当前章节序号（1 基），<=0 表示未开始
            total: 卷总章节数
            current_step: 当前步骤名（outline/writing/verify/revise），
                空字符串表示无当前步骤
            completed_steps: 已完成步骤名列表
        """
        if total <= 0 or chapter_index <= 0:
            self._chapter_index_label.setText("章节进度：未开始")
            for step in CHAPTER_STEPS:
                set_label_state(
                    self._chapter_step_labels[step],
                    CHAPTER_STEP_LABELS[step],
                    _OBJ_PHASE_PENDING,
                )
            return

        self._chapter_index_label.setText(f"第 {chapter_index}/{total} 章")
        completed_set = set(completed_steps)
        for step in CHAPTER_STEPS:
            label = self._chapter_step_labels[step]
            if step == current_step:
                set_label_state(
                    label, CHAPTER_STEP_LABELS[step], _OBJ_PHASE_CURRENT
                )
            elif step in completed_set:
                set_label_state(
                    label,
                    f"✓ {CHAPTER_STEP_LABELS[step]}",
                    _OBJ_PHASE_COMPLETED,
                )
            else:
                set_label_state(
                    label, CHAPTER_STEP_LABELS[step], _OBJ_PHASE_PENDING
                )

        # 同步整卷进度条：30% 基线 + 章节加权 70%
        all_steps_done = all(s in completed_set for s in CHAPTER_STEPS)
        if current_step:
            # 章节进行中（某步骤正在执行）：基于已完成的上一章
            percent = 30 + int((chapter_index - 1) / total * 70)
            step_label = CHAPTER_STEP_LABELS.get(current_step, current_step)
        elif all_steps_done:
            # 章节完成（所有步骤已完成）
            percent = 30 + int(chapter_index / total * 70)
            step_label = "完成"
        else:
            # 章节刚开始（无当前步骤，无已完成步骤）：按进行中算
            percent = 30 + int((chapter_index - 1) / total * 70)
            step_label = "进行中"
        text = f"第 {chapter_index}/{total} 章 {step_label} {percent}%"
        self.update_volume_progress(percent, text)

    # ------------------------------------------------------------------
    # 流式输出（当前章节正文）
    # ------------------------------------------------------------------

    def append_chapter_chunk(self, text: str) -> None:
        """追加写作流式增量到当前章节正文流式区。

        使用 moveCursor(End) + insertPlainText 实现真正的流式追加，
        避免 appendPlainText 引入额外换行。

        Args:
            text: 流式增量文本
        """
        cursor = self._chapter_stream_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self._chapter_stream_edit.setTextCursor(cursor)
        self._chapter_stream_edit.ensureCursorVisible()

    def start_chapter_streaming(self, chapter_index: int) -> None:
        """开始新章节流式：清空流式区，切到流式 tab，标注章节。

        Args:
            chapter_index: 章节序号（0 基）
        """
        self._chapter_stream_edit.clear()
        self._chapter_stream_edit.setPlaceholderText(
            f"第 {chapter_index + 1} 章正文生成中..."
        )
        # 切到流式 tab
        idx = self._artifacts_tabs.indexOf(self._chapter_stream_tab)
        if idx >= 0:
            self._artifacts_tabs.setCurrentIndex(idx)

    def reset_streaming(self) -> None:
        """清空流式区。"""
        self._chapter_stream_edit.clear()

    def set_full_volume_content(self, content: str) -> None:
        """完成后在流式区显示完整卷正文。

        Args:
            content: 完整卷正文
        """
        self._chapter_stream_edit.setPlainText(content)

    # ------------------------------------------------------------------
    # 产物查看
    # ------------------------------------------------------------------

    def update_deep_analysis(self, deep_analysis: DeepAnalysis) -> None:
        """更新深度分析预览。

        Args:
            deep_analysis: 前文深度分析产物
        """
        self._current_deep_analysis = deep_analysis
        self._deep_analysis_edit.setPlainText(_format_deep_analysis(deep_analysis))

    def update_volume_outline(self, outline: VolumeOutline) -> None:
        """更新卷大纲预览。

        Args:
            outline: 卷大纲对象
        """
        self._current_volume_outline = outline
        self._volume_outline_edit.setPlainText(_format_volume_outline(outline))

    def update_final_outline(self, outline: VolumeOutline) -> None:
        """审计后更新卷大纲预览为终稿。

        Args:
            outline: 审计修订后的最终卷大纲
        """
        self._current_volume_outline = outline
        self._volume_outline_edit.setPlainText(_format_volume_outline(outline))

    def update_audit_report(self, report: OutlineAuditReport, round_idx: int = 0) -> None:
        """更新审计报告预览（含维度/分数/问题/建议 + 修订版大纲）。

        Args:
            report: 大纲审计报告
            round_idx: 审计轮次（0 基，用于标题显示）
        """
        self._current_audit_report = report
        self._audit_report_edit.setPlainText(_format_audit_report(report, round_idx))
        if report.revised_outline is not None:
            self._revised_outline_edit.setPlainText(
                _format_volume_outline(report.revised_outline)
            )

    def add_chapter_artifacts(
        self, chapter_index: int, artifacts: ChapterArtifacts, title: str = ""
    ) -> None:
        """动态添加单章产物到折叠列表。

        Args:
            chapter_index: 章节序号（1 基）
            artifacts: 单章产物聚合
            title: 章节标题（可选，来自卷大纲 ChapterPlan.title）
        """
        # 已存在则先移除旧的
        old = self._chapter_groups.pop(chapter_index, None)
        if old is not None:
            old.setParent(None)
            old.deleteLater()

        group_title = (
            f"第 {chapter_index} 章: {title}" if title else f"第 {chapter_index} 章"
        )
        chapter_group = QGroupBox(group_title)
        chapter_group.setCheckable(True)
        chapter_group.setChecked(False)  # 默认折叠

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 4)
        content_layout.setSpacing(4)

        # 细纲摘要
        content_layout.addWidget(QLabel("细纲摘要:"))
        outline_edit = QPlainTextEdit()
        outline_edit.setReadOnly(True)
        outline_edit.setMinimumHeight(60)
        outline_edit.setMaximumHeight(120)
        if artifacts.outline is not None:
            outline_edit.setPlainText(format_outline(artifacts.outline))
        else:
            outline_edit.setPlainText("（无）")
        content_layout.addWidget(outline_edit)

        # 评审摘要（优先最终评审）
        content_layout.addWidget(QLabel("评审摘要:"))
        critique_edit = QPlainTextEdit()
        critique_edit.setReadOnly(True)
        critique_edit.setMinimumHeight(60)
        critique_edit.setMaximumHeight(120)
        critique = artifacts.final_critique or artifacts.critique
        if critique is not None:
            critique_edit.setPlainText(format_critique(critique))
        else:
            critique_edit.setPlainText("（无）")
        content_layout.addWidget(critique_edit)

        # 正文摘要（截断避免过长）
        content_layout.addWidget(QLabel("正文摘要:"))
        content_edit = QPlainTextEdit()
        content_edit.setReadOnly(True)
        content_edit.setMinimumHeight(60)
        content_edit.setMaximumHeight(160)
        body = artifacts.content or ""
        if len(body) > 1000:
            content_edit.setPlainText(body[:1000] + "\n……（已截断）")
        else:
            content_edit.setPlainText(body if body else "（无）")
        content_layout.addWidget(content_edit)

        if artifacts.revision_rounds > 0:
            content_layout.addWidget(
                QLabel(f"修订轮次：{artifacts.revision_rounds}")
            )

        chapter_group_layout = QVBoxLayout(chapter_group)
        chapter_group_layout.setContentsMargins(4, 4, 4, 4)
        chapter_group_layout.addWidget(content)
        # 折叠联动：勾选=展开，取消勾选=折叠
        chapter_group.toggled.connect(
            lambda checked, w=content: w.setVisible(checked)
        )
        content.setVisible(False)  # 默认折叠

        # 插入到 stretch 之前
        self._chapters_scroll_layout.insertWidget(
            self._chapters_scroll_layout.count() - 1, chapter_group
        )
        self._chapter_groups[chapter_index] = chapter_group

    # ------------------------------------------------------------------
    # 编辑回读
    # ------------------------------------------------------------------

    def get_edited_deep_analysis(self) -> DeepAnalysis | None:
        """获取用户编辑后的深度分析。

        解析深度分析编辑器文本回 DeepAnalysis 对象，解析失败返回 None。

        Returns:
            编辑后的 DeepAnalysis 对象，解析失败或编辑器为空时返回 None
        """
        text = self._deep_analysis_edit.toPlainText()
        if not text.strip():
            return None
        return parse_deep_analysis(text)

    def get_edited_volume_outline(self) -> VolumeOutline | None:
        """获取用户编辑后的卷大纲。

        解析卷大纲编辑器文本回 VolumeOutline 对象，解析失败返回 None。

        Returns:
            编辑后的 VolumeOutline 对象，解析失败或编辑器为空时返回 None
        """
        text = self._volume_outline_edit.toPlainText()
        if not text.strip():
            return None
        return parse_volume_outline(text)

    # ------------------------------------------------------------------
    # 检查点继续按钮
    # ------------------------------------------------------------------

    def show_continue_button(self, checkpoint_name: str) -> None:
        """显示继续按钮，并切换到对应产物标签页。

        用户在检查点对话框选择"编辑"后由主窗口调用：关闭对话框让用户在面板中
        编辑产物，编辑完成后点击"继续"按钮恢复 orchestrator。

        Args:
            checkpoint_name: 检查点名
                （after_deep_analysis/after_volume_outline/after_audit）
        """
        self._current_checkpoint_name = checkpoint_name
        self._continue_btn.show()
        # 切换到对应产物标签页，方便用户编辑
        if checkpoint_name == "after_deep_analysis":
            self._artifacts_tabs.setCurrentIndex(0)  # 深度分析 tab
        elif checkpoint_name == "after_volume_outline":
            self._artifacts_tabs.setCurrentIndex(1)  # 卷大纲 tab
        elif checkpoint_name == "after_audit":
            self._artifacts_tabs.setCurrentIndex(2)  # 审计报告 tab

    def hide_continue_button(self) -> None:
        """隐藏继续按钮并清空当前检查点名。"""
        self._continue_btn.hide()
        self._current_checkpoint_name = ""

    def switch_to_tab(self, phase: str) -> None:
        """切换产物查看 Tab 到对应阶段。

        Args:
            phase: 阶段名（deep_analysis/volume_outline/outline_audit/outline_final）
        """
        if not hasattr(self, "_artifacts_tabs"):
            return
        if phase in ("deep_analysis",):
            self._artifacts_tabs.setCurrentIndex(0)
        elif phase in ("volume_outline", "outline_final"):
            self._artifacts_tabs.setCurrentIndex(1)
        elif phase == "outline_audit":
            self._artifacts_tabs.setCurrentIndex(2)  # 审计报告 tab

    def _on_continue_clicked(self) -> None:
        """继续按钮点击：发射 continue_requested 信号携带检查点名。

        主窗口接收后从面板读取编辑后的产物并 resume orchestrator。
        """
        self.continue_requested.emit(self._current_checkpoint_name, None)

    # ------------------------------------------------------------------
    # 审计重点输入区（before_audit 检查点内嵌输入）
    # ------------------------------------------------------------------

    def show_audit_focus_input(self) -> None:
        """显示审计重点输入区并切到卷大纲 Tab（before_audit 检查点触发）。"""
        self._artifacts_tabs.setCurrentIndex(1)  # 卷大纲 tab
        self._audit_focus_group.setVisible(True)
        self._audit_focus_edit.clear()
        self._audit_focus_edit.setFocus()

    def hide_audit_focus_input(self) -> None:
        """隐藏审计重点输入区。"""
        self._audit_focus_group.setVisible(False)

    def _on_audit_focus_start(self) -> None:
        """用户点击"开始审计"：发射 continue_requested 信号携带用户输入。"""
        text = self._audit_focus_edit.toPlainText().strip()
        self.hide_audit_focus_input()
        self.continue_requested.emit("before_audit", text)

    def _on_audit_focus_cancel(self) -> None:
        """用户点击"取消续写"：发射 cancel_checkpoint 信号。"""
        self.hide_audit_focus_input()
        self.cancel_checkpoint.emit()

    # ------------------------------------------------------------------
    # 重置
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """重置面板：清空产物查看器，重置进度指示器。"""
        self._current_deep_analysis = None
        self._current_volume_outline = None
        self._current_audit_report = None
        self._audit_reports.clear()

        self._deep_analysis_edit.clear()
        self._volume_outline_edit.clear()
        self._audit_report_edit.clear()
        self._revised_outline_edit.clear()
        self._chapter_stream_edit.clear()

        # 清空各章产物折叠列表
        for group in list(self._chapter_groups.values()):
            group.setParent(None)
            group.deleteLater()
        self._chapter_groups.clear()

        # 隐藏继续按钮
        self.hide_continue_button()

        # 重置进度
        self.update_volume_phase_progress("", [])
        self.update_chapter_progress(0, 0, "", [])
