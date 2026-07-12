"""续写控制面板。

包含：
- 顶部模式切换区（QComboBox 选择 single/volume/rewrite_current 模式）
- 中部垂直 QSplitter：
  - 上半：模式面板区（续写配置区/VolumePanel，按模式显隐），撑满中间空间
  - 下半：用户输入区（QPlainTextEdit），默认小、可拖动把手调整高度
- 底部按钮区（流式布局，开始/停止/重写/接受/对比等）
- 上下文提取预览面板（M4：显示提取结果，支持编辑/禁用/添加）
- 流式输出区（QPlainTextEdit，QTimer 50ms 节流批量更新）
- 光标动画（█ 闪烁）
- 滚动自动跟随（可锁定）

Signals:
    start_continuation(dict): 请求开始续写（参数字典）
    stop_continuation(): 请求停止流式
    rewrite(dict): 请求重写（参数字典）
    accept_continuation(): 接受当前续写
    accept_and_continue(): 接受并继续续写
    edit_then_accept(): 编辑后接受
    compare_swipes(): 请求并排对比
    extract_context_requested(bool): 请求上下文提取（force_refresh 参数）
    swipe_info_requested(str): 请求 MainWindow 在状态栏显示 swipe 元信息
    toast_requested(str): 请求 MainWindow 在状态栏显示临时提示（限速等）
    rewrite_current_analysis_requested(dict): 请求开始「重写当前章节」分析→生成流程
        （rewrite_current 模式下点击「开始续写」/「重写」时发射，参数字典）
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from novelforge.models import Continuation, VolumeRunConfig
from novelforge.ui.context_preview_panel import ContextPreviewPanel
from novelforge.ui.flow_layout import QFlowLayout
from novelforge.ui.helpers import select_combo_by_id
from novelforge.ui.volume_panel import VolumePanel
from novelforge.ui.wheel_filter import WheelEventFilter
from novelforge.ui.worldbook_panel import WorldBookPanel

logger = logging.getLogger(__name__)

# UI 节流间隔（毫秒）
UI_THROTTLE_MS = 50

# 光标闪烁间隔（毫秒）
CURSOR_BLINK_MS = 500

# 高亮颜色 4 色（黄/绿/蓝/红），背景半透明
_HIGHLIGHT_COLORS: dict[str, str] = {
    "黄": "#FFFACD",
    "绿": "#C8FFB4",
    "蓝": "#B4DCFF",
    "红": "#FFB4B4",
}


class ContinuationPanel(QWidget):
    """续写控制面板。

    提供续写配置、上下文提取预览、流式输出显示、操作按钮。

    Signals:
        start_continuation(dict): 请求开始续写
        stop_continuation(): 请求停止流式
        rewrite(dict): 请求重写
        accept_continuation(): 接受当前续写
        accept_and_continue(): 接受并继续续写
        edit_then_accept(): 编辑后接受
        compare_swipes(): 请求并排对比
        extract_context_requested(bool): 请求上下文提取（force_refresh）
        rewrite_current_analysis_requested(dict): 请求开始「重写当前章节」分析→生成流程
    """

    start_continuation = Signal(dict)
    stop_continuation = Signal()
    rewrite = Signal(dict)
    accept_continuation = Signal()
    accept_and_continue = Signal()
    edit_then_accept = Signal()
    compare_swipes = Signal()
    delete_continuation = Signal()
    audit_continuation = Signal()
    # M4 新增：请求上下文提取（force_refresh 参数）
    extract_context_requested = Signal(bool)
    # 查看组装后的续写提示词
    view_prompt_requested = Signal()
    # 模式切换（"single"/"volume"/"rewrite_current"）
    mode_changed = Signal(str)
    # 卷模式切换时请求显隐右侧续写输出面板（visible=True 显示输出面板，
    # visible=False 隐藏输出面板并把空间让给卷控制面板）
    output_panel_visibility_requested = Signal(bool)
    # 请求 MainWindow 在状态栏显示 swipe 元信息（替代已删除的 _swipe_info_label）
    swipe_info_requested = Signal(str)
    # 请求 MainWindow 在状态栏显示临时提示（限速等，3 秒后由 MainWindow 还原）
    toast_requested = Signal(str)
    # 高亮变化通知 MainWindow 持久化 [{start,end,color,note}]
    highlights_changed = Signal(list)
    # 重写当前章节模式：触发「分析→检查点→生成」两步流程
    rewrite_current_analysis_requested = Signal(dict)
    # 统一流程启动信号（携带 plugin_id + params），替代 start_continuation/
    # rewrite_current_analysis_requested 的新统一入口，由 FlowExecutor 执行
    start_flow = Signal(str, dict)

    def __init__(self, parent=None) -> None:
        """初始化续写控制面板。"""
        super().__init__(parent)
        self._is_streaming = False
        self._current_swipe: Continuation | None = None
        self._all_swipes: list[Continuation] = []
        self._chunk_buffer: list[str] = []  # 待刷新的 chunk 缓冲
        # 端点→上次手动选择的模型（会话记忆，不持久化；切换端点时恢复）
        self._last_model_per_endpoint: dict[str, str] = {}

        # Volume 面板（卷续写，由 show_volume_panel 控制显隐）
        self._volume_panel = VolumePanel()

        self._setup_ui()
        self._setup_timers()
        self._setup_connections()
        self._update_button_states()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ===== 模式切换 =====
        mode_group = QGroupBox("续写模式")
        mode_layout = QHBoxLayout(mode_group)
        self._mode_combo = QComboBox()
        # 不再硬编码 3 项，由 set_flow_plugins 动态填充插件注册表
        mode_layout.addWidget(self._mode_combo)
        layout.addWidget(mode_group)

        # ===== 中部 QSplitter：模式面板区（上）+ 用户输入区（下）=====
        # 上半部分撑满中间空间，下半部分默认小、可拖动调整高度
        self._content_splitter = QSplitter(Qt.Orientation.Vertical)
        self._content_splitter.setChildrenCollapsible(False)
        self._content_splitter.setHandleWidth(6)

        # ----- 上半：模式面板容器 -----
        self._mode_content_widget = QWidget()
        mode_content_layout = QVBoxLayout(self._mode_content_widget)
        mode_content_layout.setContentsMargins(0, 0, 0, 0)
        mode_content_layout.setSpacing(4)

        # ===== 续写配置区 =====
        self._config_group = QGroupBox("续写配置")
        config_form = QFormLayout(self._config_group)

        # 预设选择（M2 启用）
        self._preset_combo = QComboBox()
        self._preset_combo.addItem("默认预设", "default")
        config_form.addRow("预设:", self._preset_combo)

        # 端点选择
        self._endpoint_combo = QComboBox()
        self._endpoint_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._endpoint_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        config_form.addRow("API 端点:", self._endpoint_combo)

        # 模型选择（不可编辑，自动从端点填充）
        self._model_combo = QComboBox()
        self._model_combo.setEditable(False)
        self._model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        config_form.addRow("模型:", self._model_combo)

        # 温度
        self._temp_spin = QDoubleSpinBox()
        self._temp_spin.setRange(0.0, 2.0)
        self._temp_spin.setSingleStep(0.1)
        self._temp_spin.setValue(0.8)
        config_form.addRow("温度:", self._temp_spin)

        # 目标字数（单章续写目标字数，注入 {{target_words}} 宏）
        self._target_words_spin = QSpinBox()
        self._target_words_spin.setRange(500, 20000)
        self._target_words_spin.setValue(2000)
        self._target_words_spin.setSingleStep(500)
        self._target_words_spin.setToolTip("单章目标字数（500-20000）")
        config_form.addRow("目标字数:", self._target_words_spin)

        # 回溯章节数（0=全部前文，上限 99999 实际不限制）
        self._lookback_spin = QSpinBox()
        self._lookback_spin.setRange(0, 99999)
        self._lookback_spin.setValue(5)
        self._lookback_spin.setSpecialValueText("全部前文")
        config_form.addRow("回溯章节数:", self._lookback_spin)

        # 世界书选择（全局加载，与预设并列）
        self._worldbook_panel = WorldBookPanel()
        config_form.addRow(self._worldbook_panel)

        mode_content_layout.addWidget(self._config_group, 1)

        # ===== Volume 面板（默认隐藏，由 show_volume_panel 控制显隐）=====
        mode_content_layout.addWidget(self._volume_panel, 1)
        self._volume_panel.hide()

        # ----- 下半：用户输入区（贴底，可拖动调整高度）-----
        self._user_input_group = QGroupBox("用户输入（续写指令）")
        user_input_layout = QVBoxLayout(self._user_input_group)
        user_input_layout.setContentsMargins(2, 2, 2, 2)
        user_input_layout.setSpacing(2)
        self._user_input_edit = QPlainTextEdit()
        self._user_input_edit.setPlaceholderText(
            "输入续写指令或额外要求（可选）...\n如：聚焦主角的心理变化，增加环境描写"
        )
        self._user_input_edit.setMinimumHeight(36)
        user_input_layout.addWidget(self._user_input_edit)

        self._content_splitter.addWidget(self._mode_content_widget)
        self._content_splitter.addWidget(self._user_input_group)
        self._content_splitter.setStretchFactor(0, 1)  # 模式面板撑满中间
        self._content_splitter.setStretchFactor(1, 0)  # 用户输入默认小
        self._content_splitter.setSizes([400, 60])

        layout.addWidget(self._content_splitter, 1)

        # ===== 上下文预览面板（不在本面板布局中，由 MainWindow 放入独立分栏） =====
        self._context_preview_panel = ContextPreviewPanel()
        self._context_preview_panel.setMinimumHeight(80)

        # ===== 续写输出区（不在本面板布局中，由 MainWindow 放入独立分栏） =====
        self._output_edit = QPlainTextEdit()
        self._output_edit.setReadOnly(True)
        self._output_edit.setPlaceholderText("续写输出将显示在此处...")
        # 启用右键菜单高亮（4 色 + 可选备注，持久化到 swipe.highlights）
        self._output_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._output_edit.customContextMenuRequested.connect(self._on_output_context_menu)
        self._current_highlights: list[dict] = []  # 当前 swipe 的高亮列表
        self._auto_scroll_check = QCheckBox("自动滚动跟随")
        self._auto_scroll_check.setChecked(True)

        # ===== 按钮区（流式布局，窄屏自动换行） =====
        btn_layout = QFlowLayout()
        btn_layout.setSpacing(4)

        self._start_btn = QPushButton("开始续写")
        self._start_btn.setObjectName("primaryBtn")
        self._view_prompt_btn = QPushButton("查看提示词")
        self._stop_btn = QPushButton("停止")
        self._stop_btn.setEnabled(False)
        self._rewrite_btn = QPushButton("重写")
        self._rewrite_btn.setEnabled(False)
        self._accept_btn = QPushButton("接受")
        self._accept_btn.setEnabled(False)
        self._accept_continue_btn = QPushButton("接受并继续")
        self._accept_continue_btn.setEnabled(False)
        self._edit_accept_btn = QPushButton("编辑后接受")
        self._edit_accept_btn.setEnabled(False)
        self._delete_btn = QPushButton("删除")
        self._delete_btn.setEnabled(False)
        self._audit_btn = QPushButton("审计")
        self._audit_btn.setEnabled(False)
        self._compare_btn = QPushButton("并排对比")
        self._compare_btn.setEnabled(False)

        # 设置最小宽度保证文字完整，窄屏时流式布局自动换行
        for btn in (
            self._start_btn, self._view_prompt_btn, self._stop_btn, self._rewrite_btn,
            self._accept_btn, self._accept_continue_btn,
            self._edit_accept_btn, self._delete_btn, self._audit_btn, self._compare_btn,
        ):
            btn.setMinimumWidth(80)

        # 按顺序添加，宽屏自然排成接近 4 列，窄屏自动折行
        btn_layout.addWidget(self._start_btn)
        btn_layout.addWidget(self._view_prompt_btn)
        btn_layout.addWidget(self._stop_btn)
        btn_layout.addWidget(self._rewrite_btn)
        btn_layout.addWidget(self._accept_btn)
        btn_layout.addWidget(self._accept_continue_btn)
        btn_layout.addWidget(self._edit_accept_btn)
        btn_layout.addWidget(self._delete_btn)
        btn_layout.addWidget(self._audit_btn)
        btn_layout.addWidget(self._compare_btn)

        layout.addLayout(btn_layout)

        # 安装滚轮事件过滤器：未聚焦时不响应滚轮，转发给父级滚动区域
        self._wheel_filter = WheelEventFilter(self)
        for combo in (self._mode_combo, self._preset_combo, self._endpoint_combo, self._model_combo):
            combo.installEventFilter(self._wheel_filter)
        for spin in (self._temp_spin, self._target_words_spin, self._lookback_spin):
            spin.installEventFilter(self._wheel_filter)

    def _setup_timers(self) -> None:
        """设置定时器。"""
        # 节流刷新定时器
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(UI_THROTTLE_MS)
        self._flush_timer.timeout.connect(self._flush_buffer)

        # 光标闪烁定时器
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(CURSOR_BLINK_MS)
        self._cursor_timer.timeout.connect(self._toggle_cursor)
        self._cursor_visible = True

    def _setup_connections(self) -> None:
        """连接信号。"""
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._view_prompt_btn.clicked.connect(self.view_prompt_requested.emit)
        self._stop_btn.clicked.connect(self.stop_continuation.emit)
        self._rewrite_btn.clicked.connect(self._on_rewrite_clicked)
        self._accept_btn.clicked.connect(self.accept_continuation.emit)
        self._accept_continue_btn.clicked.connect(self.accept_and_continue.emit)
        self._edit_accept_btn.clicked.connect(self.edit_then_accept.emit)
        self._delete_btn.clicked.connect(self.delete_continuation.emit)
        self._audit_btn.clicked.connect(self.audit_continuation.emit)
        self._compare_btn.clicked.connect(self.compare_swipes.emit)
        # 端点切换时自动更新模型
        self._endpoint_combo.currentIndexChanged.connect(self._on_endpoint_changed)
        # 用户手动切换模型时记录会话记忆（程序化填充用 blockSignals 屏蔽）
        self._model_combo.currentIndexChanged.connect(self._on_model_user_changed)
        # 模式切换
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)

    # ===== 端点/模型管理 =====

    def set_endpoints(self, endpoints: list[dict], default_id: str = "") -> None:
        """设置 API 端点列表。

        Args:
            endpoints: 端点列表
            default_id: 默认端点 ID
        """
        self._endpoint_combo.clear()
        for ep in endpoints:
            label = ep.get("name", ep.get("id", ""))
            self._endpoint_combo.addItem(label, ep)
            if ep.get("id") == default_id:
                self._endpoint_combo.setCurrentIndex(self._endpoint_combo.count() - 1)

    def set_models(self, models: list[str]) -> None:
        """填充模型下拉框（按名称排序）。

        仅负责清空 + 填充，不处理选中；选中由调用方（``_on_endpoint_changed``）决定。
        填充期间 ``blockSignals`` 防止触发 ``_on_model_user_changed`` 误记录会话记忆。
        """
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for m in sorted(models):
            self._model_combo.addItem(m)
        self._model_combo.blockSignals(False)

    def _on_endpoint_changed(self, index: int) -> None:
        """端点切换时自动更新模型列表（仅显示该端点已启用模型）。"""
        if index < 0:
            return
        endpoint = self._endpoint_combo.itemData(index)
        if not endpoint:
            return
        enabled = endpoint.get("enabled_models") or []
        all_models = endpoint.get("models") or []
        default_model = endpoint.get("default_model", "")
        # 回退链：enabled_models → models → [default_model]（旧端点兼容）
        models_to_show = enabled or all_models or ([default_model] if default_model else [])
        if not models_to_show:
            self._model_combo.blockSignals(True)
            self._model_combo.clear()
            self._model_combo.blockSignals(False)
            return
        self.set_models(models_to_show)
        # 选中：该端点上次手动选择的模型 → 否则首个
        ep_id = endpoint.get("id", "")
        last = self._last_model_per_endpoint.get(ep_id, "")
        idx = self._model_combo.findText(last) if last else -1
        if idx < 0:
            idx = 0
        self._model_combo.blockSignals(True)
        self._model_combo.setCurrentIndex(idx)
        self._model_combo.blockSignals(False)

    def _on_model_user_changed(self, _index: int) -> None:
        """用户手动切换模型时记录会话记忆（每端点记住上次选择）。"""
        ep = self.get_selected_endpoint()
        if not ep:
            return
        text = self._model_combo.currentText()
        if text:
            self._last_model_per_endpoint[ep.get("id", "")] = text

    # ===== 模式管理 =====

    def _on_mode_changed(self) -> None:
        """模式切换回调：发射 mode_changed 信号。"""
        self.mode_changed.emit(self.get_mode())

    def get_mode(self) -> str:
        """获取当前续写模式（plugin_id，内置为 single/volume/rewrite_current）。"""
        idx = self._mode_combo.currentIndex()
        if idx >= 0:
            data = self._mode_combo.itemData(idx)
            if data:
                return data
        return "single"

    def set_mode(self, mode: str) -> None:
        """设置续写模式。

        Args:
            mode: 模式名 / plugin_id（"single"/"volume"/"rewrite_current" 或自定义插件 ID）
        """
        for i in range(self._mode_combo.count()):
            if self._mode_combo.itemData(i) == mode:
                self._mode_combo.setCurrentIndex(i)
                return

    def set_flow_plugins(self, plugins: list) -> None:
        """设置流程插件列表（替换模式下拉项）。

        由 main_window 在初始化和插件变更时调用，用 FlowPluginService 注册表
        填充模式下拉框。内置插件 ID 与原模式字符串一致以保兼容。

        Args:
            plugins: FlowPlugin 对象列表（内置在前，自定义按序）
        """
        # 记录当前选中项，填充后恢复
        prev_mode = self.get_mode()
        self._mode_combo.blockSignals(True)
        self._mode_combo.clear()
        for plugin in plugins:
            self._mode_combo.addItem(plugin.name, plugin.id)
        # 恢复选中项（若仍存在），否则选第一项
        restored = False
        for i in range(self._mode_combo.count()):
            if self._mode_combo.itemData(i) == prev_mode:
                self._mode_combo.setCurrentIndex(i)
                restored = True
                break
        if not restored and self._mode_combo.count() > 0:
            self._mode_combo.setCurrentIndex(0)
        self._mode_combo.blockSignals(False)
        # 触发一次 mode_changed 以同步 UI 显隐
        self.mode_changed.emit(self.get_mode())

    def show_volume_panel(self, visible: bool) -> None:
        """切换 Volume 面板的显示。

        卷续写模式下显示 volume_panel，并隐藏单次参数区；
        非 volume 模式下仅隐藏 volume_panel（由单次参数区管理其余显隐）。

        卷模式开启时同时请求隐藏右侧续写输出面板（让空间给卷控制面板），
        卷模式关闭时请求恢复右侧续写输出面板。

        Args:
            visible: True 时显示 volume_panel 并隐藏 config_group，
                False 时仅隐藏 volume_panel
        """
        self._volume_panel.setVisible(visible)
        if visible:
            # 卷模式：隐藏单次参数区
            self._config_group.hide()
        else:
            # 单次模式：恢复单次参数区
            self._config_group.show()
        # 卷模式开启→隐藏输出面板(visible=False)；卷模式关闭→显示输出面板(True)
        self.output_panel_visibility_requested.emit(not visible)

    def set_presets(self, presets: list[dict], default_id: str = "default") -> None:
        """设置预设列表。

        Args:
            presets: 预设列表，每项为 {"id": str, "name": str}
            default_id: 默认选中的预设 ID
        """
        current_id = self._preset_combo.currentData()
        self._preset_combo.clear()
        for preset in presets:
            self._preset_combo.addItem(preset.get("name", preset.get("id", "")),
                                        preset.get("id", ""))
        # 选中默认或之前的预设
        target_id = current_id or default_id
        select_combo_by_id(self._preset_combo, target_id)
        # 同步卷模式预设
        self._volume_panel.set_presets(presets, default_id)

    def get_selected_preset_id(self) -> str:
        """获取选中的预设 ID。"""
        idx = self._preset_combo.currentIndex()
        if idx >= 0:
            data = self._preset_combo.itemData(idx)
            if data:
                return data
        return "default"

    def set_worldbooks(
        self, worldbooks: list[dict], default_id: str = ""
    ) -> None:
        """设置全局世界书列表。

        Args:
            worldbooks: 世界书字典列表，每项含 id/name/enabled
            default_id: 默认选中的世界书 ID
        """
        self._worldbook_panel.set_worldbooks(worldbooks, default_id)

    def get_selected_worldbook_id(self) -> str:
        """获取选中的世界书 ID（未选择时返回空字符串）。"""
        return self._worldbook_panel.get_selected_worldbook_id()

    def is_worldbook_enabled(self) -> bool:
        """是否启用世界书（已勾选且选了具体世界书）。"""
        return self._worldbook_panel.is_enabled()

    def get_selected_endpoint(self) -> dict | None:
        """获取选中的端点。"""
        idx = self._endpoint_combo.currentIndex()
        if idx >= 0:
            return self._endpoint_combo.itemData(idx)
        return None

    def select_endpoint_by_id(self, endpoint_id: str) -> None:
        """按 endpoint_id 选中端点下拉项，未找到则保持当前选中。

        Args:
            endpoint_id: 目标端点 ID
        """
        for i in range(self._endpoint_combo.count()):
            data = self._endpoint_combo.itemData(i)
            if data and data.get("id") == endpoint_id:
                self._endpoint_combo.setCurrentIndex(i)
                return

    def select_model_by_name(self, model: str) -> None:
        """按模型名选中模型下拉项，未找到则保持当前选中。

        供 ``_refresh_endpoints`` 同步流程配置模型到面板。用 ``blockSignals``
        屏蔽 ``_on_model_user_changed``，避免同步操作被误记为会话记忆。

        Args:
            model: 目标模型名
        """
        if not model:
            return
        idx = self._model_combo.findText(model)
        if idx >= 0:
            self._model_combo.blockSignals(True)
            self._model_combo.setCurrentIndex(idx)
            self._model_combo.blockSignals(False)

    def get_selected_model(self) -> str:
        """获取模型下拉当前选中的模型名（空串表示未选）。"""
        return self._model_combo.currentText()

    def get_parameters(self) -> dict[str, Any]:
        """获取续写参数。"""
        return {
            "temperature": self._temp_spin.value(),
            "target_words": self._target_words_spin.value(),
            "lookback_chapters": self._lookback_spin.value(),
            "preset_id": self.get_selected_preset_id(),
            "top_p": 1.0,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
        }

    def set_parameters(self, params: dict[str, Any]) -> None:
        """设置续写参数（重写时沿用上次参数）。"""
        if "temperature" in params:
            self._temp_spin.setValue(params["temperature"])
        if "target_words" in params:
            self._target_words_spin.setValue(int(params["target_words"]))
        if "lookback_chapters" in params:
            self._lookback_spin.setValue(params["lookback_chapters"])

    # ===== 流式输出控制 =====

    def start_streaming(self) -> None:
        """开始流式输出模式。"""
        self._is_streaming = True
        self._chunk_buffer.clear()
        self._output_edit.clear()

        # 启动节流定时器
        self._flush_timer.start()
        # 启动光标闪烁
        self._cursor_timer.start()

        self._update_button_states()

    def stop_streaming(self) -> None:
        """停止流式输出模式。"""
        self._is_streaming = False
        self._flush_timer.stop()
        self._cursor_timer.stop()
        # 最后刷新一次缓冲
        self._flush_buffer()
        # 移除光标
        self._remove_cursor()
        self._update_button_states()

    def restore_streaming_state(self, buffered_text: str) -> None:
        """恢复续写流式状态（切回发起章节时调用）。

        重建流式输出模式（_is_streaming=True + 节流/光标定时器 + 按钮态），
        并回填已缓冲的续写文本。后续 chunk 经 append_chunk 追加。

        Args:
            buffered_text: 已缓冲的续写输出文本
        """
        # 复用 start_streaming 完成状态/定时器/按钮态初始化（会清空输出）
        self.start_streaming()
        # 回填已缓冲的文本
        if buffered_text:
            cursor = self._output_edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText(buffered_text)
            if self._auto_scroll_check.isChecked():
                self._output_edit.ensureCursorVisible()

    def append_chunk(self, text: str) -> None:
        """追加正文 chunk 到缓冲区（节流刷新）。"""
        self._chunk_buffer.append(text)

    def append_reasoning(self, text: str) -> None:
        """推理内容框已移除，此方法保留为 no-op 以兼容外部信号连接。"""
        pass

    def _flush_buffer(self) -> None:
        """刷新缓冲区到输出区（节流批量更新）。"""
        if not self._chunk_buffer:
            return
        text = "".join(self._chunk_buffer)
        self._chunk_buffer.clear()

        # 移除旧光标
        self._remove_cursor()
        # 追加新文本
        cursor = self._output_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)

        # 添加光标
        if self._is_streaming:
            cursor.insertText("█")

        # 自动滚动
        if self._auto_scroll_check.isChecked():
            self._output_edit.ensureCursorVisible()

    def _toggle_cursor(self) -> None:
        """切换光标显示（闪烁动画）。"""
        if not self._is_streaming:
            return
        self._cursor_visible = not self._cursor_visible
        cursor = self._output_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)

        # 获取末尾文本
        doc = self._output_edit.document()
        last_block = doc.lastBlock()
        text = last_block.text()

        if text.endswith("█"):
            # 移除光标
            cursor.setPosition(doc.characterCount() - 2)
            cursor.deleteChar()
        elif self._cursor_visible:
            cursor.insertText("█")

        if self._auto_scroll_check.isChecked():
            self._output_edit.ensureCursorVisible()

    def _remove_cursor(self) -> None:
        """移除输出区末尾的光标。"""
        doc = self._output_edit.document()
        last_block = doc.lastBlock()
        text = last_block.text()
        if text.endswith("█"):
            cursor = self._output_edit.textCursor()
            cursor.setPosition(doc.characterCount() - 2)
            cursor.deleteChar()

    # ===== 按钮事件 =====

    def _on_start_clicked(self) -> None:
        """开始续写按钮。

        统一发射 ``start_flow`` 信号（携带 plugin_id + params），
        由 MainWindow 的 FlowExecutor 按 plugin 声明的阶段序列执行。
        内置插件 ID 与原模式字符串一致，FlowExecutor 内部按 agent 类型
        分发到对应的 handler（continuation/audit/volume_pipeline）。
        """
        params = self.get_parameters()
        params["model"] = self._model_combo.currentText()
        plugin_id = self.get_mode()
        self.start_flow.emit(plugin_id, params)

    def _on_rewrite_clicked(self) -> None:
        """重写按钮。

        按 ``get_mode()`` 分发：
        - ``rewrite_current``：统一发射 ``start_flow`` 信号
          （重新走插件声明的「分析→生成」流程）
        - 其他模式：发射 ``rewrite`` 信号（``created_by="rewrite"``）
        """
        params = self.get_parameters()
        params["model"] = self._model_combo.currentText()
        if self.get_mode() == "rewrite_current":
            # 重写当前章节模式：重写按钮等同重新触发 start_flow
            plugin_id = self.get_mode()
            self.start_flow.emit(plugin_id, params)
        else:
            params["created_by"] = "rewrite"
            self.rewrite.emit(params)

    def _set_swipe_info(self, text: str, state: str = "metaText") -> None:
        """请求 MainWindow 在状态栏显示 swipe 元信息。

        state 参数保留以兼容调用方，实际状态色由状态栏统一处理（不再区分）。

        Args:
            text: 信息文字
            state: 状态对象名（保留兼容，已不生效）
        """
        self.swipe_info_requested.emit(text)

    def _update_button_states(self) -> None:
        """更新按钮状态。"""
        if self._is_streaming:
            self._start_btn.setEnabled(False)
            self._view_prompt_btn.setEnabled(False)
            self._stop_btn.setEnabled(True)
            self._rewrite_btn.setEnabled(False)
            self._accept_btn.setEnabled(False)
            self._accept_continue_btn.setEnabled(False)
            self._edit_accept_btn.setEnabled(False)
            self._compare_btn.setEnabled(False)
            self._audit_btn.setEnabled(False)
        else:
            self._start_btn.setEnabled(True)
            self._view_prompt_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            has_swipe = self._current_swipe is not None
            self._rewrite_btn.setEnabled(has_swipe)
            self._accept_btn.setEnabled(has_swipe)
            self._accept_continue_btn.setEnabled(has_swipe)
            self._edit_accept_btn.setEnabled(has_swipe)
            self._delete_btn.setEnabled(has_swipe)
            self._audit_btn.setEnabled(has_swipe)
            self._compare_btn.setEnabled(len(self._all_swipes) >= 2)

    # ===== swipe 显示 =====

    def set_current_swipe(
        self,
        swipe: Continuation | None,
        all_swipes: list[Continuation] | None = None,
    ) -> None:
        """设置当前显示的 swipe。

        Args:
            swipe: 当前 swipe（None 表示无）
            all_swipes: 所有 swipe 列表（用于对比按钮状态）
        """
        self._current_swipe = swipe
        if all_swipes is not None:
            self._all_swipes = all_swipes

        if swipe:
            # 显示 swipe 内容（若有生成的标题，在内容前显示）
            display_text = swipe.content
            generated_title = getattr(swipe, "generated_title", "") or ""
            if generated_title:
                display_text = f"【生成标题】{generated_title}\n\n{swipe.content}"
            self._output_edit.setPlainText(display_text)
            # 加载该 swipe 已持久化的高亮
            self._current_highlights = list(getattr(swipe, "highlights", []) or [])
            self.apply_highlights()
            # 显示元数据
            self._set_swipe_info(
                f"模型: {swipe.model} | "
                f"状态: {swipe.status} | "
                f"字数: {len(swipe.content)} | "
                f"创建: {swipe.created_at.strftime('%Y-%m-%d %H:%M:%S')} | "
                f"{'已接受' if swipe.is_accepted else '未接受'}",
                "textSuccess" if swipe.is_accepted else "metaText",
            )
        else:
            self._output_edit.clear()
            self._current_highlights = []
            self.apply_highlights()
            self._set_swipe_info("无续写版本", "metaText")

        self._update_button_states()

    # ===== 输出栏右键高亮（4 色 + 可选备注，持久化到 swipe.highlights）=====

    def _on_output_context_menu(self, pos) -> None:
        """输出栏右键菜单：选中文本后可选 4 色高亮 + 备注，或清除高亮。"""
        cursor = self._output_edit.textCursor()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        has_selection = start != end

        menu = QMenu(self._output_edit)

        # 高亮颜色子菜单（仅在选中文本时可用）
        color_menu = menu.addMenu("高亮选中")
        if not has_selection:
            color_menu.setEnabled(False)
        else:
            for color_name, color_hex in _HIGHLIGHT_COLORS.items():
                action = color_menu.addAction(color_name)
                action.triggered.connect(
                    lambda checked=False, ch=color_hex: self._prompt_note_and_add(start, end, ch)
                )

        menu.addSeparator()

        # 清除当前选区高亮
        clear_sel_action = menu.addAction("清除当前选区高亮")
        clear_sel_action.setEnabled(has_selection)
        clear_sel_action.triggered.connect(lambda: self._clear_highlights_in_range(start, end))

        # 清除全部高亮
        clear_all_action = menu.addAction("清除全部高亮")
        clear_all_action.setEnabled(bool(self._current_highlights))
        clear_all_action.triggered.connect(self._clear_all_highlights)

        menu.exec(self._output_edit.viewport().mapToGlobal(pos))

    def _prompt_note_and_add(self, start: int, end: int, color_hex: str) -> None:
        """弹出备注输入对话框，然后添加高亮。"""
        note, ok = QInputDialog.getText(
            self._output_edit, "高亮备注", "请输入备注（可选，留空直接确认）："
        )
        if not ok:
            return
        self._add_highlight(start, end, color_hex, note.strip())

    def _add_highlight(self, start: int, end: int, color: str, note: str) -> None:
        """添加一条高亮并持久化。"""
        self._current_highlights.append({
            "start": int(start),
            "end": int(end),
            "color": color,
            "note": note,
        })
        self.apply_highlights()
        self.highlights_changed.emit(list(self._current_highlights))

    def _clear_highlights_in_range(self, start: int, end: int) -> None:
        """清除与给定区间重叠的高亮条目。"""
        lo, hi = (start, end) if start <= end else (end, start)
        before_count = len(self._current_highlights)
        self._current_highlights = [
            h for h in self._current_highlights
            if not (int(h.get("start", 0)) < hi and int(h.get("end", 0)) > lo)
        ]
        if len(self._current_highlights) != before_count:
            self.apply_highlights()
            self.highlights_changed.emit(list(self._current_highlights))

    def _clear_all_highlights(self) -> None:
        """清除全部高亮。"""
        if not self._current_highlights:
            return
        self._current_highlights = []
        self.apply_highlights()
        self.highlights_changed.emit([])

    def apply_highlights(self) -> None:
        """将 _current_highlights 应用到 _output_edit（setExtraSelections）。"""
        from PySide6.QtWidgets import QTextEdit

        selections = []
        for h in self._current_highlights:
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(h.get("color", "#FFFACD")))
            cursor = self._output_edit.textCursor()
            try:
                cursor.setPosition(int(h.get("start", 0)))
                cursor.setPosition(int(h.get("end", 0)), QTextCursor.MoveMode.KeepAnchor)
            except Exception:
                continue
            sel.cursor = cursor
            selections.append(sel)
        self._output_edit.setExtraSelections(selections)

    def set_highlights(self, highlights: list[dict]) -> None:
        """设置当前输出栏高亮（不触发持久化信号，供加载时回填）。"""
        self._current_highlights = list(highlights or [])
        self.apply_highlights()

    def clear_output(self) -> None:
        """清空输出区。"""
        self._output_edit.clear()
        self._current_swipe = None
        self._all_swipes = []
        self._current_highlights = []
        self.apply_highlights()
        self._set_swipe_info("无续写版本", "metaText")
        self._update_button_states()

    def show_error(self, message: str) -> None:
        """显示错误信息。"""
        self._output_edit.setPlainText(f"错误: {message}")
        self._set_swipe_info(f"错误: {message}", "textDanger")

    def show_toast(self, message: str) -> None:
        """请求 MainWindow 在状态栏显示临时提示（3 秒后由 MainWindow 还原）。"""
        self.toast_requested.emit(message)

    # ===== 属性 =====

    @property
    def is_streaming(self) -> bool:
        """是否正在流式输出。"""
        return self._is_streaming

    @property
    def current_swipe(self) -> Continuation | None:
        """当前 swipe。"""
        return self._current_swipe

    @property
    def output_text(self) -> str:
        """输出区文本。"""
        return self._output_edit.toPlainText()

    @property
    def context_preview_panel(self) -> ContextPreviewPanel:
        """上下文提取预览面板（M4）。"""
        return self._context_preview_panel

    @property
    def output_edit(self) -> QPlainTextEdit:
        """续写输出编辑器控件（由 MainWindow 放入独立分栏）。"""
        return self._output_edit

    @property
    def auto_scroll_check(self) -> QCheckBox:
        """自动滚动复选框控件（由 MainWindow 放入输出分栏）。"""
        return self._auto_scroll_check

    @property
    def volume_panel(self) -> VolumePanel:
        """Volume 卷级多章节续写配置与监控面板。"""
        return self._volume_panel

    def get_volume_panel(self) -> VolumePanel:
        """获取 Volume 面板实例。"""
        return self._volume_panel

    def get_volume_config(self) -> VolumeRunConfig:
        """从 Volume 面板读取当前卷续写配置。

        Returns:
            当前 VolumeRunConfig 对象
        """
        return self._volume_panel.get_config()

    def get_user_input(self) -> str:
        """获取用户输入的续写指令。"""
        return self._user_input_edit.toPlainText().strip()

    def clear_user_input(self) -> None:
        """清空用户输入。"""
        self._user_input_edit.clear()

    def set_output_text(self, text: str) -> None:
        """设置输出区文本（编辑后接受时用）。"""
        self._output_edit.setPlainText(text)
