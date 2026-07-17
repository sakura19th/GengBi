"""PySide6 主窗口。

三栏布局主窗口，集成 M1 所有组件：
- 左栏：章节列表（含虚拟滚动、搜索、右键菜单）
- 中栏：章节预览/编辑（含自动保存、undo/redo、拆分）
- 右栏：续写控制面板（含流式输出、swipe 操作）

特性：
- 面板可折叠、可拖拽调宽，尺寸持久化
- 菜单栏（文件、编辑、视图、工具、帮助）
- 状态栏（保存状态、token 计数）
- 最小窗口尺寸 1024×700
- M1 快捷键：Ctrl+O/S/Enter/R, Ctrl+Shift+A/E, Esc
- 首次启动隐私声明对话框
- 主题切换（暗色/亮色/跟随系统）
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from novelforge.core.config import ConfigManager
from novelforge.core.macros import MacroEngine
from novelforge.core.prompt_assembler import PromptAssembler
from novelforge.core.regex_engine import RegexEngine
from novelforge.core.storage import atomic_write_file, load_json_with_recovery
from novelforge.core.template_engine import TemplateEngine
from novelforge.core.token_counter import TokenCounter
from novelforge.core.variable_store import VariableStore
from novelforge.models import Chapter, Continuation, ProtagonistProfile, VolumeRunConfig, WritingPreset
from novelforge.models.style_profile import StyleProfile
from novelforge.services.chapter_service import ChapterOperation, ChapterService
from novelforge.services.context_extractor import ContextExtractor, ExtractResult
from novelforge.services.continuation_worker import (
    ContinuationWorker,
    assemble_simple_messages,
)
from novelforge.services.audit_worker import AuditWorker
from novelforge.services.exporter import (
    export_full_txt,
    export_project_backup,
    import_project_backup,
)
from novelforge.services.history_service import HistoryService
from novelforge.services.importer import TxtImporter
from novelforge.services.jailbreak_provider import JailbreakProvider
from novelforge.services.ontology_extractor import OntologyExtractor
from novelforge.services.style_extractor import StyleExtractor
from novelforge.services.preset_service import PresetService
from novelforge.services.regex_service import RegexService
from novelforge.services.storage_service import StorageService
from novelforge.services.volume_orchestrator import VolumeOrchestrator
from novelforge.services.worldbook_service import WorldBookService
from novelforge.services.flow_plugin_service import FlowPluginService
from novelforge.services.flow_executor import FlowExecutor
from novelforge.ui.flow_plugin_manager import FlowPluginManager
from novelforge.ui.chapter_editor import ChapterEditor
from novelforge.ui.chapter_list import ChapterListWidget
from novelforge.ui.checkpoint_dialog import CheckpointDialog
from novelforge.ui.chapter_confirm_dialog import ChapterConfirmDialog
from novelforge.ui.continuation_panel import ContinuationPanel
from novelforge.ui.debug_prompt_dialog import DebugPromptDialog
from novelforge.ui.dialogs import PrivacyDialog
from novelforge.ui.extraction_dialog import ExtractionDialog
from novelforge.ui.font_settings import FontSettingsDialog, apply_font_to_editor
from novelforge.ui.history_panel import HistoryPanel
from novelforge.ui.preset_manager import PresetManager
from novelforge.ui.project_panel import ProjectPanel
from novelforge.ui.regex_manager import RegexManager
from novelforge.ui.settings_dialog import SettingsDialog
from novelforge.ui.template_editor import TemplateEditor
from novelforge.ui.worldbook_manager import WorldBookManager
from novelforge.ui.audit_dialog import AuditDialog
from novelforge.services.custom_audit_rule_service import CustomAuditRuleService
from novelforge.ui.custom_rule_dialog import CustomRuleInputDialog, CustomRulesViewDialog
from novelforge.ui.flow_endpoint_dialog import FlowEndpointDialog
from novelforge.utils.paths import (
    get_agent_prompt_path,
    get_resource_path,
    get_theme_path,
    load_text_resource,
)
logger = logging.getLogger(__name__)

# 最小窗口尺寸
MIN_WINDOW_WIDTH = 1280
MIN_WINDOW_HEIGHT = 700

# 默认面板宽度（总和应 ≤ 初始窗口宽度 1600）
DEFAULT_PANEL_SIZES = [200, 420, 280, 260, 400]

# 状态变量容量上限（防止内存无限增长）
MAX_CONTEXT_CACHE_SIZE = 50  # 按章节绑定的上下文条目缓存最大条目数
MAX_UNDO_STACK_SIZE = 100  # 章节操作撤销栈最大长度


class CollapsiblePanel(QWidget):
    """可折叠面板。

    包含标题栏（含折叠按钮）和内容区域。
    点击折叠按钮可隐藏/显示内容区域。
    """

    def __init__(self, title: str, parent=None) -> None:
        """初始化可折叠面板。

        Args:
            title: 面板标题
        """
        super().__init__(parent)
        self._collapsed = False
        self._title = title

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题栏
        self._header = QFrame()
        self._header.setFrameShape(QFrame.Shape.StyledPanel)
        self._header.setObjectName("panelHeader")
        header_layout = QVBoxLayout(self._header)
        header_layout.setContentsMargins(8, 4, 8, 4)

        self._toggle_btn = QPushButton(f"▼ {title}")
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setObjectName("collapseToggle")
        self._toggle_btn.clicked.connect(self.toggle_collapsed)
        header_layout.addWidget(self._toggle_btn)

        layout.addWidget(self._header)

        # 内容容器
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._content)

    def toggle_collapsed(self) -> None:
        """切换折叠状态。"""
        self._collapsed = not self._collapsed
        self._content.setVisible(not self._collapsed)
        arrow = "▶" if self._collapsed else "▼"
        self._toggle_btn.setText(f"{arrow} {self._title}")

    def add_widget(self, widget: QWidget) -> None:
        """向内容区域添加控件。"""
        self._content_layout.addWidget(widget)

    @property
    def content_layout(self) -> QVBoxLayout:
        """获取内容区域布局。"""
        return self._content_layout


class MainWindow(QMainWindow):
    """主窗口。

    三栏布局，集成 M1 所有组件，含菜单栏、状态栏、快捷键。

    Attributes:
        config_manager: 配置管理器
        storage_service: 存储服务
        chapter_service: 章节服务
        importer: TXT 导入器
    """

    # 信号
    status_message = Signal(str)
    _extract_chunk_received = Signal(str)   # 流式提取 chunk（跨线程）
    _extract_done = Signal(object)          # 流式提取完成（跨线程）
    _extract_batch_done = Signal(list, int, int)  # 流式提取批次完成（跨线程）
    _ontology_chunk_received = Signal(str)  # 世界观提取 chunk（跨线程）
    _ontology_done = Signal(object, str)    # 世界观提取完成（跨线程）：ontology, status
    _ontology_batch_done = Signal(int, int) # 世界观提取批次完成（跨线程）
    _style_chunk_received = Signal(str)     # 文风档案提取 chunk（跨线程）
    _style_done = Signal(object, str)       # 文风档案提取完成（跨线程）：style_profile, status
    _style_batch_done = Signal(int, int)    # 文风档案提取批次完成（跨线程）
    _protagonist_chunk_received = Signal(str)  # 主角形象提取 chunk（跨线程）
    _protagonist_done = Signal(object, str)    # 主角形象提取完成（跨线程）：profile, status
    _protagonist_batch_done = Signal(int, int) # 主角形象提取批次完成（跨线程）
    _custom_rule_chunk_received = Signal(str)  # 自定义设定流式 chunk（跨线程）
    _custom_rule_done = Signal(object, str)    # 自定义设定完成（跨线程）：rule, status

    def __init__(self, config_manager: ConfigManager) -> None:
        """初始化主窗口。

        Args:
            config_manager: 配置管理器实例
        """
        super().__init__()
        self.config_manager = config_manager

        # 初始化服务层
        self.storage_service = StorageService(config_manager.get_storage_path())
        self.chapter_service = ChapterService(self.storage_service)
        self.importer = TxtImporter(self.storage_service)
        # M2 新增：预设服务、token 计数器、宏引擎、提示词组装器
        self.preset_service = PresetService(config_manager.get_storage_path())
        self.token_counter = TokenCounter()
        self.macro_engine = MacroEngine()
        # M3 新增：正则服务、正则引擎、变量存储、模板引擎
        self.regex_service = RegexService(config_manager.get_storage_path())
        self.regex_engine = RegexEngine()
        self.variable_store = VariableStore(config_manager.get_storage_path())
        self.template_engine = TemplateEngine(variable_store=self.variable_store)
        # 确保全局正则含默认脚本（首次运行注入，已存在则不覆盖）
        self.regex_service.ensure_default_scripts_exist()
        # 提示词组装器（注入正则引擎与模板引擎）
        self.prompt_assembler = PromptAssembler(
            self.token_counter,
            self.macro_engine,
            regex_engine=self.regex_engine,
            template_engine=self.template_engine,
        )
        # M4 新增：上下文提取器
        self.context_extractor = ContextExtractor(
            self.storage_service, config_manager, token_counter=self.token_counter
        )
        # 世界观底层提取器（全文拆分分析提取 7 维度 WorldOntology）
        self.ontology_extractor = OntologyExtractor(
            self.storage_service, config_manager, token_counter=self.token_counter
        )
        # 文风档案提取器（全文拆分分析提取 9 维度 StyleProfile）
        self.style_extractor = StyleExtractor(
            self.storage_service, config_manager, token_counter=self.token_counter
        )
        # 自定义设定/审计必查项 AI 结构化服务（用户输入 → AI 结构化为 CustomAuditRule）
        self.custom_rule_service = CustomAuditRuleService(
            self.storage_service, config_manager
        )
        # 流程破限文本提供器（非正文流程按等级注入 system 消息）
        self._jailbreak_provider = JailbreakProvider()
        # M4: 当前续写使用的上下文条目（提取后存入，供 worker 快照使用）
        self._current_context_entries: list = []
        # 按章节绑定的上下文条目内存缓存（chapter_id -> entries），使用 OrderedDict 实现 LRU 淘汰
        self._context_entries_by_chapter: OrderedDict[str, list] = OrderedDict()
        # 主角形象档案内存 LRU 缓存（chapter_id -> ProtagonistProfile），跟随章节缓存
        self._protagonist_profile_by_chapter: OrderedDict[str, ProtagonistProfile] = OrderedDict()
        # 正在提取的章节 ID（用于提取完成时正确归档）
        self._extracting_chapter_id: str | None = None
        # M5: 历史日志服务
        self.history_service = HistoryService(self.storage_service)
        # M5: 当前续写会话的追踪信息（用于历史日志记录）
        self._continuation_started_at: str = ""
        self._continuation_prompt_messages: list = []
        self._continuation_model: str = ""
        self._continuation_parameters: dict = {}
        # 确保默认预设存在
        self.preset_service.ensure_default_preset_exists()
        # 预设管理器窗口（非模态，可同时打开多个）
        self._preset_manager_windows: list[PresetManager] = []
        # 全局世界书服务
        self.worldbook_service = WorldBookService(self.storage_service.storage_path)
        # 流程控制插件服务（内置 3 插件 + 用户自定义插件 CRUD + 导入导出）
        self.flow_plugin_service = FlowPluginService(config_manager.get_storage_path())
        # 流程执行引擎（按 FlowPlugin.stages 有序执行，支持挂起-恢复）
        self.flow_executor = FlowExecutor()
        # 世界书管理器窗口引用（非模态，避免被 GC）
        self._worldbook_manager_windows: list[WorldBookManager] = []
        # M3: 正则管理器与模板编辑器窗口引用（避免被 GC）
        self._regex_manager_windows: list[RegexManager] = []
        self._template_editor_windows: list[TemplateEditor] = []
        # 流程插件管理器窗口引用（非模态，避免被 GC）
        self._flow_plugin_manager_windows: list[FlowPluginManager] = []

        # 状态
        self._current_project_id: str | None = None
        self._current_chapter: Chapter | None = None
        self._current_chapters: list[Chapter] = []
        self._undo_stack: list[ChapterOperation] = []
        self._continuation_worker: ContinuationWorker | None = None
        # 单章续写审计 worker（与 _continuation_worker 互斥使用）
        self._audit_worker: AuditWorker | None = None
        self._audit_dialog: AuditDialog | None = None
        # Volume 卷级多章节续写编排器（与 _continuation_worker 互斥使用）
        self._volume_orchestrator: VolumeOrchestrator | None = None
        # Volume 已完成卷阶段列表（用于进度指示器）
        self._volume_completed_phases: list[str] = []
        # volume_phase 多阶段状态：首次进入 handler 时 _prepare_volume_run 填充，
        # 各阶段产物存入此 dict；None 表示未在 volume_phase 流程中
        self._volume_state: dict | None = None
        # 当前执行阶段名（重试用）；_start_volume_phase 中设置
        self._volume_current_phase: str | None = None
        # 恢复模式标志（非 FlowExecutor 驱动，关闭软件后恢复时使用）
        self._volume_resuming: bool = False
        # 当前章节已执行的子步骤集合（outline/writing/verify/revise），
        # _on_volume_chapter_started 时重置；供 _on_volume_chapter_step_started 累积 completed_steps
        self._volume_chapter_steps_seen: set[str] = set()

        # 章节切换状态保留：按章节缓冲流式输出，切回时恢复"接收中"态
        # 后台 worker 不因章节切换停止；UI 仅在当前章节 == 操作发起章节时更新
        self._extract_stream_text_by_chapter: dict[str, str] = {}  # 上下文提取流式文本
        self._ontology_extracting: bool = False  # 世界观提取中标志（项目级）
        self._ontology_stream_text: str = ""  # 世界观提取流式文本缓冲
        self._style_extracting: bool = False  # 文风档案提取中标志（项目级）
        self._style_stream_text: str = ""  # 文风档案提取流式文本缓冲
        self._protagonist_extracting: bool = False  # 主角形象提取中标志（章节级）
        self._protagonist_stream_text: str = ""  # 主角形象提取流式文本缓冲
        self._protagonist_stream_text_by_chapter: dict[str, str] = {}  # 按章节缓冲主角提取流式文本
        self._continuation_chapter_id: str | None = None  # 单章续写发起章节
        self._continuation_stream_text_by_chapter: dict[str, str] = {}  # 续写流式文本
        self._audit_chapter_id: str | None = None  # 审计发起章节
        self._volume_chapter_id: str | None = None  # 卷续写发起章节（插入点）

        self.setWindowTitle("赕笔 - 小说续写器")
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        # 设置初始窗口大小为足以容纳所有面板的宽度
        self.resize(1600, 900)

        # 应用主题
        self._apply_theme()

        # 创建中央控件
        self._setup_central_widget()

        # M5: 应用字体设置到章节编辑器
        self._apply_font_settings()

        # 同步上下文提取 token 限制默认值到 UI
        self._sync_token_limit_default()
        # Token 拆分改由预览面板独管，变更时持久化到 config
        context_panel = self.continuation_panel.context_preview_panel
        context_panel._token_limit_combo.currentTextChanged.connect(
            self._save_token_limit_to_config
        )

        # 连接流式提取信号（跨线程安全传递）
        self._extract_chunk_received.connect(self._on_extract_chunk_received)
        self._extract_done.connect(self._on_extract_done)
        self._extract_batch_done.connect(self._on_extract_batch_done)
        # 连接世界观提取信号（跨线程安全传递）
        self._ontology_chunk_received.connect(self._on_ontology_chunk_received)
        self._ontology_done.connect(self._on_ontology_done)
        self._ontology_batch_done.connect(self._on_ontology_batch_done)
        # 连接文风档案提取信号（跨线程安全传递）
        self._style_chunk_received.connect(self._on_style_chunk_received)
        self._style_done.connect(self._on_style_done)
        self._style_batch_done.connect(self._on_style_batch_done)
        # 连接主角形象提取信号（跨线程安全传递）
        self._protagonist_chunk_received.connect(self._on_protagonist_chunk_received)
        self._protagonist_done.connect(self._on_protagonist_done)
        self._protagonist_batch_done.connect(self._on_protagonist_batch_done)
        self._custom_rule_chunk_received.connect(self._on_custom_rule_chunk_received)
        self._custom_rule_done.connect(self._on_custom_rule_done)

        # 创建菜单栏
        self._setup_menu_bar()

        # 创建状态栏
        self._setup_status_bar()

        # 注册快捷键
        self._setup_shortcuts()

        # 恢复窗口状态
        self._restore_window_state()

        # 刷新端点列表
        self._refresh_endpoints()

        # 刷新预设列表
        self._refresh_presets()
        # 刷新世界书列表到续写面板
        self._refresh_worldbooks()
        # 从 config 加载上次的续写参数到面板
        self._apply_continuation_defaults()
        # 从 config 加载上次的卷续写配置到 VolumePanel
        self._load_volume_config()

        # M3: 初始编译正则脚本到引擎（需在 continuation_panel 创建后调用）
        self._refresh_regex_scripts()

        # 首次启动隐私声明
        QTimer.singleShot(100, self._check_privacy_notice)

        logger.info("主窗口初始化完成")

    def _setup_central_widget(self) -> None:
        """创建中央控件：五栏 QSplitter 布局。"""
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 第 1 栏：章节列表
        left_panel = CollapsiblePanel("章节列表")
        self.chapter_list = ChapterListWidget(self.storage_service)
        left_panel.add_widget(self.chapter_list)
        left_panel.setMinimumWidth(160)

        # 第 2 栏：章节编辑器
        center_panel = CollapsiblePanel("预览 / 编辑")
        self.chapter_editor = ChapterEditor()
        center_panel.add_widget(self.chapter_editor)
        center_panel.setMinimumWidth(250)

        # 第 3 栏：上下文提取预览（独立分栏）
        context_panel = CollapsiblePanel("上下文提取预览")
        self.continuation_panel = ContinuationPanel()
        context_panel.add_widget(self.continuation_panel.context_preview_panel)
        context_panel.setMinimumWidth(200)

        # 第 4 栏：续写控制面板
        right_panel = CollapsiblePanel("续写控制")
        right_panel.add_widget(self.continuation_panel)
        right_panel.setMinimumWidth(220)

        # 第 5 栏：续写输出（独立分栏）
        output_panel = CollapsiblePanel("续写输出")
        output_panel.add_widget(self.continuation_panel.output_edit)
        output_panel.add_widget(self.continuation_panel.auto_scroll_check)
        output_panel.setMinimumWidth(280)

        splitter.addWidget(left_panel)
        splitter.addWidget(center_panel)
        splitter.addWidget(context_panel)
        splitter.addWidget(right_panel)
        splitter.addWidget(output_panel)

        # 设置初始面板宽度
        splitter.setSizes(DEFAULT_PANEL_SIZES)

        # 设置伸缩因子（编辑器优先伸缩，上下文预览与输出次之）
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        splitter.setStretchFactor(3, 0)
        splitter.setStretchFactor(4, 1)

        self._splitter = splitter
        self.setCentralWidget(splitter)

        # 连接信号
        self._connect_signals()

    def _connect_signals(self) -> None:
        """连接组件信号。"""
        # 章节列表信号
        self.chapter_list.chapter_selected.connect(self._on_chapter_selected)
        self.chapter_list.rename_requested.connect(self._on_rename_chapter)
        self.chapter_list.delete_requested.connect(self._on_delete_chapter)
        self.chapter_list.merge_requested.connect(self._on_merge_chapter)
        self.chapter_list.split_requested.connect(self._on_split_chapter_from_list)

        # 编辑器信号
        self.chapter_editor.split_requested.connect(self._on_split_chapter_from_editor)
        self.chapter_editor.saved.connect(self._on_chapter_saved)
        self.chapter_editor.save_requested.connect(self._on_save)
        self.chapter_editor.word_count_changed.connect(self._on_word_count_changed)
        self.chapter_editor.title_changed.connect(self._on_chapter_title_changed)

        # 续写面板信号
        self.continuation_panel.start_continuation.connect(
            self._on_start_continuation_routed
        )
        # 统一流程启动信号（plugin_id + params → FlowExecutor 执行）
        self.continuation_panel.start_flow.connect(self._on_start_flow)
        self.continuation_panel.stop_continuation.connect(self._on_stop_continuation)
        self.continuation_panel.rewrite.connect(self._on_rewrite)
        # 注：rewrite_current 模式现已通过 start_flow 信号走 FlowExecutor 统一入口，
        # 原 rewrite_current_analysis_requested 信号不再发射（定义保留向后兼容）
        self.continuation_panel.accept_continuation.connect(self._on_accept_continuation)
        self.continuation_panel.accept_and_continue.connect(
            self._on_accept_and_continue
        )
        self.continuation_panel.edit_then_accept.connect(self._on_edit_then_accept)
        self.continuation_panel.compare_swipes.connect(self._on_compare_swipes)
        self.continuation_panel.delete_continuation.connect(self._on_delete_continuation)
        self.continuation_panel.audit_continuation.connect(self._on_audit_continuation)
        self.continuation_panel.view_prompt_requested.connect(
            self._on_view_continuation_prompt
        )
        # 模式切换（单次/智能）
        self.continuation_panel.mode_changed.connect(self._on_mode_changed)
        self.continuation_panel.worldbook_changed.connect(
            self._on_panel_worldbook_selection_changed
        )
        # 卷模式切换时显隐右侧续写输出面板
        self.continuation_panel.output_panel_visibility_requested.connect(
            self._on_output_panel_visibility_requested
        )
        # 检查点"编辑"后点击面板"继续"按钮恢复 orchestrator（一次性连接，
        # volume_panel 实例在 ContinuationPanel.__init__ 中创建）
        self.continuation_panel.volume_panel.continue_requested.connect(
            self._on_volume_continue
        )
        # before_audit 审计重点输入区"取消续写"按钮
        self.continuation_panel.volume_panel.cancel_checkpoint.connect(
            self._on_volume_cancel_checkpoint
        )
        # 卷续写配置变更时实时保存到 config_manager
        self.continuation_panel.volume_panel.config_changed.connect(
            self._save_volume_config
        )

        # M4: 上下文提取预览面板信号
        context_panel = self.continuation_panel.context_preview_panel
        context_panel.cancel_requested.connect(self._on_cancel_extraction)
        context_panel.entries_changed.connect(self._on_context_entries_changed)
        context_panel.extract_requested.connect(self._on_extract_requested)
        context_panel.extract_ontology_requested.connect(self._on_extract_ontology_requested)
        context_panel.view_ontology_requested.connect(self._on_view_ontology_requested)
        context_panel.extract_style_requested.connect(self._on_extract_style_requested)
        context_panel.view_style_requested.connect(self._on_view_style_requested)
        context_panel.extract_protagonist_requested.connect(self._on_extract_protagonist_requested)
        context_panel.view_protagonist_requested.connect(self._on_view_protagonist_requested)
        context_panel.add_custom_rule_requested.connect(self._on_add_custom_rule_requested)
        context_panel.view_custom_rules_requested.connect(self._on_view_custom_rules_requested)
        context_panel.view_extract_prompt_requested.connect(
            self._on_view_extract_prompt
        )

        # 状态消息
        self.status_message.connect(self._set_status_message)

        # swipe 元信息与限速提示路由到状态栏（替代已删除的 _swipe_info_label）
        self.continuation_panel.swipe_info_requested.connect(self._set_status_message)
        self.continuation_panel.toast_requested.connect(self._on_toast_requested)
        self.continuation_panel.highlights_changed.connect(self._on_highlights_changed)

        # 注册 FlowExecutor agent handler 并填充面板插件下拉
        self._register_flow_handlers()
        self._refresh_flow_plugins()

    def _setup_menu_bar(self) -> None:
        """创建菜单栏。"""
        menubar = self.menuBar()

        # ===== 文件菜单 =====
        file_menu = menubar.addMenu("文件(&F)")

        new_project_action = QAction("新建项目(&N)...", self)
        new_project_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        new_project_action.triggered.connect(self._on_new_project)
        file_menu.addAction(new_project_action)

        import_action = QAction("导入 TXT(&O)...", self)
        import_action.setShortcut(QKeySequence("Ctrl+O"))
        import_action.triggered.connect(self._on_import_txt)
        file_menu.addAction(import_action)

        file_menu.addSeparator()

        # 项目管理
        manage_projects_action = QAction("项目管理(&M)...", self)
        manage_projects_action.triggered.connect(self._on_manage_projects)
        file_menu.addAction(manage_projects_action)

        file_menu.addSeparator()

        # M5: 导出菜单（完整 TXT / 项目备份 zip / 导入项目备份）
        export_menu = file_menu.addMenu("导出/备份(&E)")

        export_txt_action = export_menu.addAction("导出完整 TXT...")
        export_txt_action.triggered.connect(self._on_export_full_txt)

        export_backup_action = export_menu.addAction("导出项目备份(zip)...")
        export_backup_action.triggered.connect(self._on_export_project_backup)

        export_menu.addSeparator()

        import_backup_action = export_menu.addAction("导入项目备份(zip)...")
        import_backup_action.triggered.connect(self._on_import_project_backup)

        # 保留旧 _on_export 兼容入口（无菜单项引用）
        file_menu.addSeparator()

        exit_action = QAction("退出(&Q)", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # ===== 编辑菜单 =====
        edit_menu = menubar.addMenu("编辑(&E)")

        save_action = QAction("保存(&S)", self)
        save_action.setShortcut(QKeySequence("Ctrl+S"))
        save_action.triggered.connect(self._on_save)
        edit_menu.addAction(save_action)

        edit_menu.addSeparator()

        undo_action = QAction("撤销(&U)", self)
        undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        undo_action.triggered.connect(self._on_undo)
        edit_menu.addAction(undo_action)

        redo_action = QAction("重做(&R)", self)
        redo_action.setShortcut(QKeySequence("Ctrl+Y"))
        redo_action.triggered.connect(self._on_redo)
        edit_menu.addAction(redo_action)

        edit_menu.addSeparator()

        undo_chapter_action = QAction("撤销章节操作", self)
        undo_chapter_action.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        undo_chapter_action.triggered.connect(self._on_undo_chapter_operation)
        edit_menu.addAction(undo_chapter_action)

        # ===== 视图菜单 =====
        view_menu = menubar.addMenu("视图(&V)")

        theme_menu = view_menu.addMenu("主题(&T)")
        dark_action = QAction("暗色(&D)", self)
        dark_action.setCheckable(True)
        dark_action.triggered.connect(lambda: self._switch_theme("dark"))
        theme_menu.addAction(dark_action)

        light_action = QAction("亮色(&L)", self)
        light_action.setCheckable(True)
        light_action.triggered.connect(lambda: self._switch_theme("light"))
        theme_menu.addAction(light_action)

        system_action = QAction("跟随系统(&S)", self)
        system_action.setCheckable(True)
        system_action.triggered.connect(lambda: self._switch_theme("system"))
        theme_menu.addAction(system_action)

        current_theme = self.config_manager.get_appearance().get("theme", "dark")
        for action, theme_name in [
            (dark_action, "dark"),
            (light_action, "light"),
            (system_action, "system"),
        ]:
            action.setChecked(theme_name == current_theme)

        view_menu.addSeparator()

        toggle_left = QAction("折叠/展开章节列表", self)
        toggle_left.setShortcut(QKeySequence("F9"))
        toggle_left.triggered.connect(self._toggle_left_panel)
        view_menu.addAction(toggle_left)

        toggle_right = QAction("折叠/展开续写面板", self)
        toggle_right.setShortcut(QKeySequence("F10"))
        toggle_right.triggered.connect(self._toggle_right_panel)
        view_menu.addAction(toggle_right)

        # ===== 调试菜单 =====
        debug_menu = menubar.addMenu("调试(&D)")
        self._debug_mode_action = QAction("调试模式", self)
        self._debug_mode_action.setCheckable(True)
        self._debug_mode_action.setChecked(False)
        self._debug_mode_action.toggled.connect(self._on_debug_mode_toggled)
        debug_menu.addAction(self._debug_mode_action)
        self._debug_mode = False

        # ===== 工具菜单 =====
        tools_menu = menubar.addMenu("工具(&T)")

        preset_action = QAction("预设管理器(&P)", self)
        preset_action.setShortcut(QKeySequence("Ctrl+P"))
        preset_action.triggered.connect(self._on_open_preset_manager)
        tools_menu.addAction(preset_action)

        regex_action = QAction("正则管理器(&G)", self)
        regex_action.setShortcut(QKeySequence("Ctrl+G"))
        regex_action.triggered.connect(self._on_open_regex_manager)
        tools_menu.addAction(regex_action)

        template_action = QAction("模板编辑器(&T)", self)
        template_action.setShortcut(QKeySequence("Ctrl+T"))
        template_action.triggered.connect(self._on_open_template_editor)
        tools_menu.addAction(template_action)

        tools_menu.addSeparator()

        # M5: 续写历史日志
        history_action = QAction("续写历史日志(&H)...", self)
        history_action.triggered.connect(self._on_open_history_panel)
        tools_menu.addAction(history_action)

        # M5: 字体设置
        font_action = QAction("字体设置(&F)...", self)
        font_action.triggered.connect(self._on_open_font_settings)
        tools_menu.addAction(font_action)

        tools_menu.addSeparator()

        # 全局世界书管理器（替代旧的"导入 ST 世界书"快捷导入）
        worldbook_action = QAction("世界书管理器(&W)", self)
        worldbook_action.triggered.connect(self._on_open_worldbook_manager)
        tools_menu.addAction(worldbook_action)

        tools_menu.addSeparator()

        flow_plugin_action = QAction("流程插件管理器(&F)", self)
        flow_plugin_action.triggered.connect(self._on_open_flow_plugin_manager)
        tools_menu.addAction(flow_plugin_action)

        flow_endpoint_action = QAction("流程端点配置(&E)...", self)
        flow_endpoint_action.triggered.connect(self._on_open_flow_endpoint_dialog)
        tools_menu.addAction(flow_endpoint_action)

        settings_action = QAction("设置(&S)...", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._on_open_settings)
        tools_menu.addAction(settings_action)

        # ===== 帮助菜单 =====
        help_menu = menubar.addMenu("帮助(&H)")

        about_action = QAction("关于 赓笔(&A)", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

        privacy_action = QAction("隐私声明(&P)", self)
        privacy_action.triggered.connect(self._on_show_privacy)
        help_menu.addAction(privacy_action)

    def _on_debug_mode_toggled(self, checked: bool) -> None:
        """调试模式开关切换。

        Args:
            checked: 是否开启调试模式
        """
        self._debug_mode = checked
        # 实时更新运行中的 orchestrator
        if hasattr(self, "_volume_orchestrator") and self._volume_orchestrator is not None:
            self._volume_orchestrator.debug_mode = checked
        # 实时更新运行中的续写/审计 worker
        if hasattr(self, "_continuation_worker") and self._continuation_worker is not None:
            self._continuation_worker.debug_mode = checked
        if hasattr(self, "_audit_worker") and self._audit_worker is not None:
            self._audit_worker.debug_mode = checked

    def _on_prompt_debug_requested(
        self,
        phase_name: str,
        messages_json: str,
        current_endpoint_id: str,
        current_model: str,
    ) -> None:
        """调试提示词确认弹窗。

        Args:
            phase_name: 阶段名
            messages_json: messages 列表的 JSON 字符串
            current_endpoint_id: 当前使用的端点 ID（dialog 默认选中）
            current_model: 当前使用的模型名（dialog 默认选中）
        """
        import json as _json
        try:
            messages = _json.loads(messages_json)
        except Exception:
            messages = [{"role": "system", "content": messages_json}]

        endpoints = self.config_manager.get_endpoints()
        dialog = DebugPromptDialog(
            phase_name, messages, endpoints,
            current_endpoint_id, current_model, self,
        )
        dialog.exec()

        # 确认结果 + 端点/模型覆盖传回当前运行的 worker（优先级 volume > continuation > audit）
        target = None
        if hasattr(self, "_volume_orchestrator") and self._volume_orchestrator is not None and self._volume_orchestrator.isRunning():
            target = self._volume_orchestrator
        elif hasattr(self, "_continuation_worker") and self._continuation_worker is not None and self._continuation_worker.isRunning():
            target = self._continuation_worker
        elif hasattr(self, "_audit_worker") and self._audit_worker is not None and self._audit_worker.isRunning():
            target = self._audit_worker

        if target is not None:
            if not dialog.confirmed:
                target.confirm_debug_prompt(False)
                return
            sel_ep = dialog.get_selected_endpoint()
            sel_model = dialog.get_selected_model()
            endpoint_override = None
            api_key_override = ""
            model_override = ""
            # 端点变更：解密新端点 api_key
            if sel_ep and sel_ep.get("id", "") != current_endpoint_id:
                endpoint_override = sel_ep
                api_key_override = self.config_manager.decrypt_api_key(
                    sel_ep.get("id", "")
                )
            # 模型变更
            if sel_model and sel_model != current_model:
                model_override = sel_model
            # 端点覆盖但模型未改：用新端点 default_model
            if endpoint_override and not model_override:
                model_override = endpoint_override.get("default_model", "")
            target.confirm_debug_prompt(
                True, endpoint_override, model_override, api_key_override
            )

    def _setup_status_bar(self) -> None:
        """创建状态栏。"""
        status_bar = self.statusBar()

        # 保存状态显示位
        self._save_status_label = QLabel("就绪")
        status_bar.addWidget(self._save_status_label)

        # token 计数显示位
        self._token_count_label = QLabel("Token: 0")
        self._token_count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        status_bar.addPermanentWidget(self._token_count_label)

    def _setup_shortcuts(self) -> None:
        """注册 M1 快捷键。"""
        # Ctrl+Enter 续写（根据当前模式路由到单次/卷续写流程）
        continue_sc = QKeySequence("Ctrl+Return")
        self._continue_action = QAction(self)
        self._continue_action.setShortcut(continue_sc)
        self._continue_action.triggered.connect(
            lambda: self._on_start_continuation_routed(
                self.continuation_panel.get_parameters()
            )
        )
        self.addAction(self._continue_action)

        # Ctrl+R 重写（根据当前模式路由）
        rewrite_sc = QKeySequence("Ctrl+R")
        self._rewrite_action = QAction(self)
        self._rewrite_action.setShortcut(rewrite_sc)
        self._rewrite_action.triggered.connect(
            lambda: self._on_rewrite(self.continuation_panel.get_parameters())
        )
        self.addAction(self._rewrite_action)

        # Ctrl+Shift+A 接受
        accept_sc = QKeySequence("Ctrl+Shift+A")
        self._accept_action = QAction(self)
        self._accept_action.setShortcut(accept_sc)
        self._accept_action.triggered.connect(self._on_accept_continuation)
        self.addAction(self._accept_action)

        # Ctrl+Shift+E 编辑后接受
        edit_accept_sc = QKeySequence("Ctrl+Shift+E")
        self._edit_accept_action = QAction(self)
        self._edit_accept_action.setShortcut(edit_accept_sc)
        self._edit_accept_action.triggered.connect(self._on_edit_then_accept)
        self.addAction(self._edit_accept_action)

        # Esc 停止流式
        esc_sc = QKeySequence("Escape")
        self._esc_action = QAction(self)
        self._esc_action.setShortcut(esc_sc)
        self._esc_action.triggered.connect(self._on_stop_continuation)
        self.addAction(self._esc_action)

        # M4: F5 强制重新提取上下文
        f5_sc = QKeySequence("F5")
        self._force_extract_action = QAction(self)
        self._force_extract_action.setShortcut(f5_sc)
        self._force_extract_action.triggered.connect(self._on_force_refresh_context)
        self.addAction(self._force_extract_action)

    def _toggle_left_panel(self) -> None:
        """折叠/展开左栏。"""
        panel = self._splitter.widget(0)
        if isinstance(panel, CollapsiblePanel):
            panel.toggle_collapsed()

    def _toggle_right_panel(self) -> None:
        """折叠/展开右栏。"""
        panel = self._splitter.widget(2)
        if isinstance(panel, CollapsiblePanel):
            panel.toggle_collapsed()

    # ===== 主题管理 =====

    def _apply_theme(self) -> None:
        """应用当前主题。

        当主题为"跟随系统"时，监听系统深浅色变化信号以实时重应用 QSS。
        """
        theme = self.config_manager.get_appearance().get("theme", "dark")
        app = QApplication.instance()
        if theme == "system":
            if app and app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
                theme = "dark"
            else:
                theme = "light"
            # 监听系统深浅色变化（仅连接一次，用标志位防止重复连接）
            if app and not getattr(self, "_system_color_connected", False):
                app.styleHints().colorSchemeChanged.connect(
                    self._on_system_color_changed
                )
                self._system_color_connected = True
        else:
            # 非跟随系统时无需监听（保留已连接的信号也无害，避免频繁 connect/disconnect）
            pass

        qss_path = get_theme_path(theme)
        try:
            qss = qss_path.read_text(encoding="utf-8")
            # 注入箭头图标绝对路径（QSS url 在 setStyleSheet 字符串模式下需绝对路径）
            is_dark = theme == "dark"
            combo_chevron = get_resource_path(
                "icons",
                "chevron_down_dark.svg" if is_dark else "chevron_down.svg",
            )
            spin_up = get_resource_path(
                "icons",
                "chevron_up_dark.svg" if is_dark else "chevron_up.svg",
            )
            spin_down = get_resource_path(
                "icons",
                "chevron_down_dark.svg" if is_dark else "chevron_down.svg",
            )
            # Windows 下 Qt QSS 需要正斜杠
            qss = qss.replace("__COMBO_CHEVRON__", combo_chevron.resolve().as_posix())
            qss = qss.replace("__SPIN_CHEVRON_UP__", spin_up.resolve().as_posix())
            qss = qss.replace("__SPIN_CHEVRON_DOWN__", spin_down.resolve().as_posix())
            if app:
                app.setStyleSheet(qss)
            logger.debug("应用主题: %s", theme)
        except OSError as e:
            logger.error("加载主题文件失败 %s: %s", qss_path, e)

    def _on_system_color_changed(self, _color_scheme: Qt.ColorScheme) -> None:
        """系统深浅色变化回调：仅在"跟随系统"模式下重应用主题。"""
        theme = self.config_manager.get_appearance().get("theme", "dark")
        if theme == "system":
            self._apply_theme()
            logger.info("系统深浅色变化，已重应用主题")

    def _switch_theme(self, theme: str) -> None:
        """切换主题。"""
        appearance = self.config_manager.get_appearance()
        appearance["theme"] = theme
        self.config_manager.set_appearance(appearance)
        self._apply_theme()
        logger.info("切换主题: %s", theme)

    # ===== 窗口状态持久化 =====

    def _restore_window_state(self) -> None:
        """从 QSettings 恢复窗口状态。"""
        settings = QSettings("赓笔", "赓笔")
        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

        state = settings.value("windowState")
        if state:
            self.restoreState(state)

        splitter_sizes = settings.value("splitterSizes")
        if splitter_sizes:
            # QSettings 可能返回字符串列表，强制转换为 int
            try:
                sizes = [int(s) for s in splitter_sizes]
                self._splitter.setSizes(sizes)
            except (TypeError, ValueError):
                pass

    def _save_window_state(self) -> None:
        """保存窗口状态到 QSettings。"""
        settings = QSettings("赓笔", "赓笔")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())
        settings.setValue("splitterSizes", self._splitter.sizes())

    def showEvent(self, event) -> None:
        """窗口显示事件：首次显示时应用默认面板尺寸。"""
        super().showEvent(event)
        # 首次显示时确保 splitter 尺寸正确（覆盖 QSettings 中可能损坏的尺寸）
        settings = QSettings("赓笔", "赓笔")
        if not settings.value("splitterSizes"):
            # 首次启动，应用默认尺寸
            self._splitter.setSizes(DEFAULT_PANEL_SIZES)

    def closeEvent(self, event) -> None:
        """窗口关闭事件：保存状态，停止线程。"""
        # 保存当前章节
        if self.chapter_editor.has_unsaved_changes:
            self.chapter_editor.save_now()

        # 停止续写线程
        if self._continuation_worker and self._continuation_worker.isRunning():
            self._continuation_worker.stop()
            self._continuation_worker.wait(3000)

        # 停止审计与卷续写线程
        for worker_attr in ("_audit_worker", "_volume_orchestrator"):
            worker = getattr(self, worker_attr, None)
            if worker and worker.isRunning():
                worker.stop()
                worker.wait(3000)

        # M3: 关闭模板引擎线程池
        try:
            self.template_engine.shutdown()
        except Exception as e:
            logger.warning("关闭模板引擎失败: %s", e)

        # 兜底保存卷续写配置（防止 config_changed 信号丢失）
        try:
            config = self.continuation_panel.get_volume_config()
            self._save_volume_config(config)
        except Exception as e:
            logger.warning("关闭时保存卷续写配置失败: %s", e)

        self._save_window_state()
        logger.info("主窗口关闭，状态已保存")
        super().closeEvent(event)

    # ===== 隐私声明 =====

    def _check_privacy_notice(self) -> None:
        """首次启动展示隐私声明（不强制同意）。"""
        if not self.config_manager.is_privacy_accepted():
            dialog = PrivacyDialog(self)
            dialog.exec()
            self.config_manager.accept_privacy()  # 标记已展示过
            logger.info("隐私声明已展示")

    # ===== 端点管理 =====

    def _refresh_endpoints(self) -> None:
        """刷新 API 端点列表到续写面板。

        端点切换时模型会自动通过 _on_endpoint_changed 更新。
        """
        endpoints = self.config_manager.get_endpoints()
        default_id = self.config_manager.get("default_endpoint_id", "")
        self.continuation_panel.set_endpoints(endpoints, default_id)
        # 模型由 _on_endpoint_changed 自动设置，无需手动调用 set_models
        # 同步面板端点到当前模式的流程配置（流程指定即生效，面板仍可手动覆盖）
        mode = self.continuation_panel.get_mode()
        flow_key = "volume_continuation" if mode == "volume" else "single_continuation"
        flow_ep = self.config_manager.get_flow_endpoint(flow_key)
        if flow_ep:
            self.continuation_panel.select_endpoint_by_id(flow_ep.get("id", ""))
        # 同步流程配置的模型到面板（若该流程配置了模型且在当前端点 enabled_models 中）
        flow_model = self.config_manager.get_flow_model(flow_key)
        if flow_model:
            self.continuation_panel.select_model_by_name(flow_model)

    def _resolve_reasoning_effort(
        self, endpoint: dict, preset: WritingPreset | None = None
    ) -> str:
        """解析思考强度：预设级优先（更具体），其次端点级，均空则不发送。

        Args:
            endpoint: API 端点配置 dict
            preset: 写作预设（可选），其 generation_params.reasoning_effort 优先

        Returns:
            reasoning_effort 字符串，空串表示不发送
        """
        if preset:
            re = preset.generation_params.get("reasoning_effort", "")
            if re and re.lower() not in {"none", "off"}:
                return re
        return endpoint.get("reasoning_effort", "") or ""

    def _get_flow_jailbreak_text(self, flow_key: str) -> str:
        """查配置返回非正文流程的破限文本（自定义优先于等级模板）。

        Args:
            flow_key: 流程标识（如 ``single_audit``/``context_extraction``）

        Returns:
            破限文本；off 或无文本返回空串（调用方据此跳过注入）
        """
        custom = self.config_manager.get_flow_jailbreak_custom(flow_key)
        if custom.strip():
            return custom.strip()
        level = self.config_manager.get_flow_jailbreak(flow_key)
        return self._jailbreak_provider.get_jailbreak(flow_key, level)

    @staticmethod
    def _inject_jailbreak(messages: list[dict], jailbreak_text: str) -> list[dict]:
        """把破限文本作为 system 消息前置到 messages 开头（空文本不注入）。

        Args:
            messages: 原始 messages 数组
            jailbreak_text: 破限文本

        Returns:
            注入后的 messages 数组（原数组不变）
        """
        if not jailbreak_text:
            return messages
        return [{"role": "system", "content": jailbreak_text}] + messages

    def _refresh_presets(self) -> None:
        """刷新预设列表到续写面板（仅显示启用的预设）。"""
        try:
            presets = self.preset_service.list_presets()
            preset_list = [
                {"id": p.id, "name": p.name}
                for p in presets
                if p.enabled or p.id == "default"
            ]
            self.continuation_panel.set_presets(preset_list, default_id="default")
        except Exception as e:
            logger.error("刷新预设列表失败: %s", e)

    def _refresh_worldbooks(self) -> None:
        """刷新世界书列表到续写面板（仅显示启用的世界书）。"""
        try:
            worldbooks = self.worldbook_service.list_worldbooks()
            wb_list = [
                {"id": wb.id, "name": wb.name, "enabled": wb.enabled}
                for wb in worldbooks
                if wb.enabled
            ]
            # 优先保留面板当前多选；为空则从 config 恢复跨会话选中
            current_ids: list[str] = []
            try:
                current_ids = self.continuation_panel.get_selected_worldbook_ids()
            except Exception:
                pass
            if not current_ids:
                current_ids = self.config_manager.get_selected_worldbook_ids()
            self.continuation_panel.set_worldbooks(wb_list, default_ids=current_ids)
        except Exception as e:
            logger.error("刷新世界书列表失败: %s", e)

    def _on_panel_worldbook_selection_changed(self, ids: list) -> None:
        """续写面板世界书多选变化时持久化到 config。"""
        try:
            self.config_manager.set_selected_worldbook_ids(
                [str(i) for i in ids if i]
            )
        except Exception as e:
            logger.error("保存世界书选中状态失败: %s", e)

    def _get_enabled_worldbook_entries(self) -> list:
        """获取续写面板当前选中世界书的启用条目列表。

        遍历多选世界书，仅返回条目级 enabled=True 的条目；
        跨书 uid 冲突时先选中的书优先。

        Returns:
            ContextEntry 列表；未选择时返回空列表
        """
        try:
            if not self.continuation_panel.is_worldbook_enabled():
                return []
            wb_ids = self.continuation_panel.get_selected_worldbook_ids()
            if not wb_ids:
                return []
            merged: list = []
            seen_uids: set[str] = set()
            for wb_id in wb_ids:
                wb = self.worldbook_service.load_worldbook(wb_id)
                if wb is None:
                    logger.warning("世界书 %s 不存在", wb_id)
                    continue
                for e in wb.entries:
                    if not e.enabled:
                        continue
                    uid = getattr(e, "uid", "") or ""
                    if uid and uid in seen_uids:
                        continue
                    if uid:
                        seen_uids.add(uid)
                    merged.append(e)
            return merged
        except Exception as e:
            logger.error("获取启用世界书条目失败: %s", e)
            return []

    def _merge_worldbook_entries(self, extract_entries: list | None) -> list:
        """合并全局世界书条目与提取上下文条目。

        合并策略：世界书条目在前，提取结果在后；
        uid 冲突时世界书条目优先（不被提取结果覆盖）。

        Args:
            extract_entries: 当前章节的提取上下文条目（可能为 None）

        Returns:
            合并后的条目列表
        """
        wb_entries = self._get_enabled_worldbook_entries()
        if not wb_entries:
            return list(extract_entries) if extract_entries else []
        if not extract_entries:
            return list(wb_entries)

        # uid 冲突时世界书优先：用 extract 的 uid 集合去重 extract
        wb_uids = {getattr(e, "uid", "") for e in wb_entries}
        merged = list(wb_entries)
        for e in extract_entries:
            uid = getattr(e, "uid", "")
            if uid and uid in wb_uids:
                continue
            merged.append(e)
        return merged

    def _prompt_continue_without_extraction(
        self,
        entries: list,
        world_ontology,
        protagonist_profile,
        style_profile=None,
    ) -> bool:
        """检查上下文/世界观/主角/文风是否未提取，弹合并提示对话框。

        返回 True 表示用户选择继续不提取生成，False 表示取消。
        """
        missing = []
        if not entries:
            missing.append("上下文条目")
        if world_ontology is None:
            missing.append("世界观底层")
        if protagonist_profile is None:
            missing.append("主角形象")
        if style_profile is None:
            missing.append("文风档案")

        if not missing:
            return True  # 全部已提取，直接放行

        missing_text = "、".join(missing)
        reply = QMessageBox.question(
            self, "提取提示",
            f"以下内容尚未提取：{missing_text}\n\n"
            f"未提取的内容将以空值或占位文字参与生成，可能影响续写质量。\n"
            f"是否继续不提取生成？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _apply_continuation_defaults(self) -> None:
        """从 config 加载上次的续写参数（温度/回溯章节数）到面板。"""
        try:
            cont = self.config_manager.get_continuation_settings()
            self.continuation_panel.set_parameters({
                "temperature": cont.get("default_temperature", 0.8),
                "target_words": cont.get("default_target_words", 2000),
                "lookback_chapters": cont.get("default_lookback_chapters", 5),
            })
        except Exception as e:
            logger.warning("加载续写参数默认值失败: %s", e)

    def _load_volume_config(self) -> None:
        """从 config_manager 加载卷续写配置并回填到 VolumePanel。"""
        try:
            data = self.config_manager.get_volume_settings()
            if data:
                config = VolumeRunConfig.model_validate(data)
                self.continuation_panel.volume_panel.set_config(config)
                logger.info("已加载卷续写配置")
        except Exception as e:
            logger.warning("加载卷续写配置失败: %s", e)

    def _save_volume_config(self, config: VolumeRunConfig) -> None:
        """保存卷续写配置到 config_manager（config_changed 信号触发）。"""
        try:
            self.config_manager.set_volume_settings(config.model_dump(mode="json"))
            logger.debug("已保存卷续写配置")
        except Exception as e:
            logger.warning("保存卷续写配置失败: %s", e)

    # ===== 项目管理 =====

    def _on_new_project(self) -> None:
        """新建空项目。"""
        # 保存当前项目状态
        self._save_current_state()

        from novelforge.services.storage_service import _generate_id

        project = self.storage_service.create_project(
            name=f"新项目_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        )
        self._load_project(project.id)
        self._set_status_message(f"已创建空项目: {project.name}")

    def _on_manage_projects(self) -> None:
        """打开项目管理对话框。"""
        dialog = ProjectPanel(self.storage_service, self)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.project_opened.connect(self._load_project)
        dialog.exec()

    def _load_project(self, project_id: str) -> None:
        """加载项目。

        Args:
            project_id: 项目 ID
        """
        # 保存当前项目状态
        self._save_current_state()

        project = None
        try:
            project = self.storage_service.load_project(project_id)
        except Exception as e:
            logger.error("加载项目元数据失败: %s", e, exc_info=True)
            # 项目元数据加载失败，仍尝试加载章节（章节表独立）
            project = None

        if project is None:
            # 仍尝试加载章节列表（避免项目元数据损坏导致章节不可见）
            try:
                chapters = self.storage_service.list_chapters(project_id)
                self._current_chapters = chapters
                self.chapter_list.set_chapters(chapters)
                self._current_project_id = project_id
                self._context_entries_by_chapter.clear()
                self._current_context_entries = []
                self.chapter_editor.clear()
                self.continuation_panel.clear_output()
                QMessageBox.warning(
                    self, "警告",
                    f"项目元数据加载失败，但已加载 {len(chapters)} 章。\n"
                    f"建议检查数据库或重新导入项目。"
                )
            except Exception as e2:
                logger.error("加载章节也失败: %s", e2, exc_info=True)
                QMessageBox.critical(self, "错误", f"项目加载失败: {e2}")
            return

        self._current_project_id = project_id
        self.setWindowTitle(f"赓笔 - {project.name}")

        # 清空按章节绑定的上下文条目内存缓存（避免跨项目污染）
        self._context_entries_by_chapter.clear()
        self._current_context_entries = []

        # 加载章节列表
        chapters = self.storage_service.list_chapters(project_id)
        self._current_chapters = chapters
        self.chapter_list.set_chapters(chapters)

        # 清空编辑器和续写面板
        self.chapter_editor.clear()
        self.continuation_panel.clear_output()

        self._set_status_message(f"已加载项目: {project.name}")
        logger.info("加载项目: %s (%d 章)", project.name, len(chapters))

    def _save_current_state(self) -> None:
        """保存当前项目状态。"""
        if self.chapter_editor.has_unsaved_changes and self._current_chapter:
            self.chapter_editor.save_now()

    # ===== TXT 导入 =====

    def _on_import_txt(self) -> None:
        """导入 TXT 文件。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择 TXT 文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            # TODO: 将文件 I/O 移入 QThread 以避免 UI 阻塞（见 spec Task 27）
            result = self.importer.import_file(file_path)
            self._load_project(result.project.id)
            self._set_status_message(result.message)
            QMessageBox.information(
                self,
                "导入完成",
                f"{result.message}\n\n"
                f"项目: {result.project.name}\n"
                f"章节数: {len(result.chapters)}\n"
                f"总字数: {result.total_chars}\n"
                f"耗时: {result.elapsed_seconds:.2f}s",
            )
        except Exception as e:
            logger.error("导入失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "导入失败", f"导入 TXT 失败: {e}")

    # ===== 章节选择与编辑 =====

    def _on_chapter_selected(self, chapter_id: str) -> None:
        """章节被选中。"""
        # 保存当前章节
        if self.chapter_editor.has_unsaved_changes:
            self.chapter_editor.save_now()

        # 切换前：保存当前章节的上下文条目到内存缓存
        if self._current_chapter and self._current_chapter.id != chapter_id:
            old_id = self._current_chapter.id
            self._context_entries_by_chapter[old_id] = list(
                self._current_context_entries
            )
            # 容量上限检查：淘汰最旧条目
            if len(self._context_entries_by_chapter) > MAX_CONTEXT_CACHE_SIZE:
                oldest = next(iter(self._context_entries_by_chapter))
                del self._context_entries_by_chapter[oldest]

        chapter = self.storage_service.load_chapter(chapter_id)
        if chapter is None:
            return

        self._current_chapter = chapter
        self.chapter_list.set_current_chapter(chapter_id)
        self.chapter_editor.load_chapter(
            chapter_id=chapter.id,
            project_id=chapter.project_id,
            chapter_index=chapter.index,
            chapter_title=chapter.title,
            content=chapter.content,
        )

        # 更新章节列表中的章节数据（含 continuations）
        self._update_chapter_in_list(chapter)

        # 续写流式态恢复优先：若切回的是续写发起章节且仍有缓冲，恢复"接收中"态
        if (self._continuation_chapter_id == chapter.id
                and chapter.id in self._continuation_stream_text_by_chapter):
            buffered = self._continuation_stream_text_by_chapter[chapter.id]
            self.continuation_panel.restore_streaming_state(buffered)
            self.chapter_editor.set_streaming_locked(True)
        else:
            # 否则：正常显示已有 swipe 或清空输出
            if chapter.continuations:
                # 显示最后一个 swipe
                last_swipe = chapter.continuations[-1]
                self.continuation_panel.set_current_swipe(last_swipe, chapter.continuations)
            else:
                self.continuation_panel.clear_output()

        # 切换后：加载新章节的上下文提取结果
        self._load_context_entries_for_chapter(chapter)

        self._set_status_message(f"已加载: 第{chapter.index + 1}章 {chapter.title}")

        # 暂存节点恢复：检查该章节是否有未完成的卷续写状态
        self._check_volume_resume(chapter_id)

    def _check_volume_resume(self, chapter_id: str) -> None:
        """检查该章节是否有未完成的卷续写暂存状态，提示用户恢复。

        Args:
            chapter_id: 当前选中章节 ID
        """
        # 正在执行卷续写时不弹恢复提示
        if self._volume_orchestrator is not None:
            return
        data = self._load_volume_state(chapter_id)
        if data is None:
            return
        # 弹恢复确认对话框
        completed = data.get("completed_phases", [])
        current = data.get("current_phase", "")
        reply = QMessageBox.question(
            self, "恢复卷续写",
            f"检测到该章节有未完成的卷续写流程（已完成: {len(completed)} 阶段，"
            f"当前: {current}）。\n\n是否恢复？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._resume_volume_state(data)

    def _load_context_entries_for_chapter(self, chapter: Chapter) -> None:
        """加载章节对应的上下文提取结果（内存缓存优先，其次 SQLite 缓存）。

        状态恢复优先级：若该章节正在提取（切回发起章节），或世界观提取进行中
        （项目级，任意章节切回均恢复），优先恢复"接收中" UI 态，跳过缓存加载。

        Args:
            chapter: 新选中的章节
        """
        context_panel = self.continuation_panel.context_preview_panel

        # 0. 优先恢复提取中状态（切回发起章节）
        if (self._extracting_chapter_id == chapter.id
                and chapter.id in self._extract_stream_text_by_chapter):
            context_panel.restore_extraction_state(
                self._extract_stream_text_by_chapter[chapter.id], is_ontology=False
            )
            return
        # 世界观提取为项目级，任意章节切回均恢复
        if self._ontology_extracting and self._ontology_stream_text:
            context_panel.restore_extraction_state(
                self._ontology_stream_text, is_ontology=True
            )
            return
        # 主角形象提取为章节级，切回发起章节恢复流式态
        if (self._protagonist_extracting
                and self._extracting_chapter_id == chapter.id
                and self._protagonist_stream_text):
            context_panel.restore_extraction_state(
                self._protagonist_stream_text, is_protagonist=True
            )
            return

        # 1. 独立恢复主角形象档案（优先持久化字段，兜底 cache 表）
        if chapter.id not in self._protagonist_profile_by_chapter:
            if chapter.protagonist_profile is not None:
                self._protagonist_profile_by_chapter[chapter.id] = chapter.protagonist_profile
                self._protagonist_profile_by_chapter.move_to_end(chapter.id)
                if len(self._protagonist_profile_by_chapter) > MAX_CONTEXT_CACHE_SIZE:
                    self._protagonist_profile_by_chapter.popitem(last=False)
            elif self._current_project_id:
                try:
                    from novelforge.services.async_runner import AsyncLoopRunner

                    runner = AsyncLoopRunner.instance()
                    cached_protagonist_data = runner.run(
                        self.context_extractor.load_cached_protagonist(
                            self._current_project_id, chapter.id
                        ),
                        timeout=5,
                    )
                    if cached_protagonist_data:
                        profile_dict = cached_protagonist_data.get("protagonist_profile")
                        if profile_dict:
                            profile = ProtagonistProfile.model_validate(profile_dict)
                            self._protagonist_profile_by_chapter[chapter.id] = profile
                            self._protagonist_profile_by_chapter.move_to_end(chapter.id)
                            if len(self._protagonist_profile_by_chapter) > MAX_CONTEXT_CACHE_SIZE:
                                self._protagonist_profile_by_chapter.popitem(last=False)
                except Exception as e:
                    logger.warning("加载主角形象缓存失败: %s", e)

        # 2. 内存缓存优先
        if chapter.id in self._context_entries_by_chapter:
            entries = self._context_entries_by_chapter[chapter.id]
            # 缓存命中：更新访问顺序（LRU）
            self._context_entries_by_chapter.move_to_end(chapter.id)
            self._current_context_entries = entries
            context_panel.load_entries_for_chapter(entries, meta=None)
            return

        # 2. SQLite 缓存（通过 AsyncLoopRunner 同步调用）
        if self._current_project_id:
            try:
                from novelforge.services.async_runner import AsyncLoopRunner

                runner = AsyncLoopRunner.instance()
                # TODO: 将此 runner.run(timeout=5) 改为非阻塞（见 spec Task 26）。
                #   该调用在章节切换时频繁触发，转换需新增 Signal/Slot 用于异步
                #   回调，并处理章节快速切换时的竞态（in-flight 章节需用
                #   _loading_context_chapter_id 追踪并在回调中校验当前章节是否
                #   仍匹配，避免显示过期条目）。当前 timeout=5 仅为安全兜底，
                #   SQLite 读通常 <100ms，故暂保留同步实现以避免引入线程缺陷。
                cached_data = runner.run(
                    self.context_extractor.load_cached_entries(
                        self._current_project_id, chapter.id
                    ),
                    timeout=5,
                )
                if cached_data is not None:
                    entries = cached_data.get("entries", [])
                    meta = {
                        "elapsed_seconds": cached_data.get("elapsed_seconds", 0),
                        "token_usage": cached_data.get("token_usage", {}),
                        "batch_count": cached_data.get("batch_count", 1),
                    }
                    self._context_entries_by_chapter[chapter.id] = entries
                    # 容量上限检查：淘汰最旧条目
                    if len(self._context_entries_by_chapter) > MAX_CONTEXT_CACHE_SIZE:
                        oldest = next(iter(self._context_entries_by_chapter))
                        del self._context_entries_by_chapter[oldest]
                    # protagonist 恢复已移至方法开头的独立步骤（优先持久化字段）
                    self._current_context_entries = entries
                    context_panel.load_entries_for_chapter(entries, meta=meta)
                    return
            except Exception as e:
                logger.warning("加载章节缓存提取结果失败: %s", e)

        # 3. 无缓存：清空面板
        self._current_context_entries = []
        context_panel.load_entries_for_chapter([], meta=None)

    def _on_chapter_saved(self) -> None:
        """章节已保存。"""
        self._set_status_message("已保存")

    def _on_chapter_title_changed(self, title: str) -> None:
        """章节标题变更：实时更新当前章节对象与列表显示。"""
        if self._current_chapter:
            self._current_chapter.title = title
            self._update_chapter_in_list(self._current_chapter)

    def _on_word_count_changed(self, count: int) -> None:
        """字数变更。"""
        # 更新当前章节的字数
        if self._current_chapter:
            self._current_chapter.word_count = count

    def _update_chapter_in_list(self, chapter: Chapter) -> None:
        """更新章节列表中的章节数据。"""
        # 更新 _current_chapters 中的对应章节
        for i, ch in enumerate(self._current_chapters):
            if ch.id == chapter.id:
                self._current_chapters[i] = chapter
                break
        self.chapter_list.update_chapter(chapter)

    # ===== 章节操作（拆分/合并/删除/重命名）=====

    def _on_split_chapter_from_list(self, chapter_id: str) -> None:
        """从列表请求拆分章节。"""
        QMessageBox.information(
            self,
            "拆分章节",
            "请在编辑模式下，将光标放在拆分位置，右键选择「在此处拆分」。",
        )

    def _on_split_chapter_from_editor(self, position: int) -> None:
        """从编辑器请求拆分章节。"""
        if not self._current_chapter:
            return

        reply = QMessageBox.question(
            self,
            "拆分章节",
            f"确定在位置 {position} 处拆分当前章节？\n"
            "原章节的续写版本将归属拆分后的前一个章节。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            # 重新加载完整章节（含 continuations）
            chapter = self.storage_service.load_chapter(self._current_chapter.id)
            if chapter is None:
                return

            # 更新 content 为编辑器中的最新内容
            chapter.content = self.chapter_editor.content

            op, front, back = self.chapter_service.split_chapter(chapter, position)
            self._undo_stack.append(op)
            # 容量上限检查：移除最旧操作
            if len(self._undo_stack) > MAX_UNDO_STACK_SIZE:
                self._undo_stack.pop(0)

            # 刷新章节列表
            self._refresh_chapter_list()
            self._set_status_message(f"已拆分: {op.description}")
        except Exception as e:
            logger.error("拆分失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "拆分失败", str(e))

    def _on_merge_chapter(self, chapter_id: str) -> None:
        """合并章节与下一章。"""
        chapter = self.storage_service.load_chapter(chapter_id)
        if chapter is None:
            return

        reply = QMessageBox.question(
            self,
            "合并章节",
            f"确定将「{chapter.title}」与下一章合并？\n"
            "两章的续写版本都将归属合并后的章节。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            op, merged = self.chapter_service.merge_chapter_with_next(chapter)
            self._undo_stack.append(op)
            # 容量上限检查：移除最旧操作
            if len(self._undo_stack) > MAX_UNDO_STACK_SIZE:
                self._undo_stack.pop(0)
            self._refresh_chapter_list()
            self._set_status_message(f"已合并: {op.description}")
        except ValueError as e:
            QMessageBox.warning(self, "无法合并", str(e))
        except Exception as e:
            logger.error("合并失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "合并失败", str(e))

    def _on_delete_chapter(self, chapter_id: str) -> None:
        """删除章节。"""
        chapter = self.storage_service.load_chapter(chapter_id)
        if chapter is None:
            return

        reply = QMessageBox.question(
            self,
            "删除章节",
            f"确定删除「{chapter.title}」及其所有续写版本？\n"
            "此操作可通过 Ctrl+Shift+Z 撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            op = self.chapter_service.delete_chapter(chapter)
            self._undo_stack.append(op)
            # 容量上限检查：移除最旧操作
            if len(self._undo_stack) > MAX_UNDO_STACK_SIZE:
                self._undo_stack.pop(0)
            self._refresh_chapter_list()

            # 清空编辑器（如果删除的是当前章节）
            if self._current_chapter and self._current_chapter.id == chapter_id:
                self.chapter_editor.clear()
                self.continuation_panel.clear_output()
                self._current_chapter = None

            self._set_status_message(f"已删除: {op.description}")
        except Exception as e:
            logger.error("删除失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "删除失败", str(e))

    def _on_rename_chapter(self, chapter_id: str) -> None:
        """重命名章节。"""
        from PySide6.QtWidgets import QInputDialog

        chapter = self.storage_service.load_chapter(chapter_id)
        if chapter is None:
            return

        new_title, ok = QInputDialog.getText(
            self,
            "重命名章节",
            "新标题:",
            text=chapter.title,
        )

        if ok and new_title.strip():
            try:
                op = self.chapter_service.rename_chapter(chapter, new_title.strip())
                self._undo_stack.append(op)
                # 容量上限检查：移除最旧操作
                if len(self._undo_stack) > MAX_UNDO_STACK_SIZE:
                    self._undo_stack.pop(0)
                self._refresh_chapter_list()
                self._set_status_message(f"已重命名: {op.description}")
            except Exception as e:
                QMessageBox.critical(self, "重命名失败", str(e))

    def _on_undo_chapter_operation(self) -> None:
        """撤销章节操作。"""
        if not self._undo_stack:
            QMessageBox.information(self, "撤销", "没有可撤销的操作")
            return

        op = self._undo_stack.pop()
        try:
            self.chapter_service.undo_operation(op)
            self._refresh_chapter_list()
            self._set_status_message(f"已撤销: {op.description}")
        except Exception as e:
            logger.error("撤销失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "撤销失败", str(e))

    def _refresh_chapter_list(self) -> None:
        """刷新章节列表。"""
        if not self._current_project_id:
            return
        chapters = self.storage_service.list_chapters(self._current_project_id)
        self._current_chapters = chapters
        self.chapter_list.set_chapters(chapters)

    # ===== 续写流程 =====

    def _on_start_continuation(
        self, params: dict, user_input_override: str | None = None
    ) -> None:
        """开始续写。

        M4 流程：
        1. 验证输入（章节、端点、API Key、模型）
        2. 调用 ContextExtractor.extract 提取上下文（显示 loading）
        3. 提取失败/取消时弹出 ExtractionDialog（重试/跳过/取消）
        4. 提取成功后将 entries 传给 PromptAssembler.assemble
        5. 将 entries 快照存入 swipe（传给 ContinuationWorker）

        Args:
            params: 续写参数字典（由 continuation_panel 传入）
            user_input_override: 可选的 user_input 覆盖值（写作模式第 3 步用，
                将阶段 2 精炼输出前置【写作参考】后传入；None 时读面板输入）
        """
        if not self._current_chapter:
            QMessageBox.warning(self, "提示", "请先选择章节")
            return

        # 持久化续写参数到 config（温度/回溯章节数）
        try:
            cont = self.config_manager.config.setdefault("continuation", {})
            cont["default_temperature"] = params.get("temperature", 0.8)
            cont["default_target_words"] = params.get("target_words", 2000)
            cont["default_lookback_chapters"] = params.get("lookback_chapters", 5)
            self.config_manager.save()
        except Exception as e:
            logger.warning("保存续写参数失败: %s", e)

        endpoint = self.continuation_panel.get_selected_endpoint()
        if not endpoint:
            QMessageBox.warning(self, "提示", "请先配置 API 端点")
            self._on_open_settings()
            return

        api_key = self.config_manager.decrypt_api_key(endpoint.get("id", ""))
        if not api_key:
            QMessageBox.warning(self, "提示", "API Key 无效，请检查设置")
            self._on_open_settings()
            return

        model = params.get("model") or self.config_manager.get_flow_model("single_continuation")
        if not model:
            QMessageBox.warning(self, "提示", "请选择模型")
            return

        temperature = params.get("temperature", 0.8)
        try:
            temp_val = float(temperature)
        except (TypeError, ValueError):
            QMessageBox.warning(self, "提示", "温度必须是数字")
            return
        if not (0.0 <= temp_val <= 2.0):
            QMessageBox.warning(self, "提示", "温度必须在 0.0 到 2.0 之间")
            return
        target_words = params.get("target_words", 2000)
        try:
            words_val = int(target_words)
        except (TypeError, ValueError):
            QMessageBox.warning(self, "提示", "目标字数必须是整数")
            return
        if words_val < 100 or words_val > 50000:
            QMessageBox.warning(self, "提示", "目标字数必须在 100 到 50000 之间")
            return

        # ===== M4: 上下文提取（已解耦：用户可先点击"提取上下文"按钮）=====
        context_panel = self.continuation_panel.context_preview_panel
        raw_entries = getattr(self, "_current_context_entries", None)
        # 合并全局世界书条目（uid 冲突时世界书优先）
        entries = self._merge_worldbook_entries(raw_entries)
        # 加载项目对象（用于检查世界观/主角是否已提取）
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)
        wo_for_check = project.world_ontology if project else None
        pp_for_check = self._protagonist_profile_by_chapter.get(self._current_chapter.id) if self._current_chapter else None
        sp_for_check = project.style_profile if project else None
        if not self._prompt_continue_without_extraction(entries, wo_for_check, pp_for_check, sp_for_check):
            return

        # 确保章节正文已加载（list_chapters 只加载元数据）
        self._ensure_chapter_contents()

        # ===== M2: 使用 PromptAssembler 组装提示词 =====
        preset_id = params.get("preset_id", "default")
        preset = self.preset_service.load_preset(preset_id)
        if preset is None:
            # 回退到默认预设
            preset = self.preset_service.load_default_preset()
            logger.warning("预设 %s 不存在，回退到默认预设", preset_id)

        # 从预设获取生成参数默认值
        gen_params = preset.generation_params
        max_context = params.get("max_context") or gen_params.get("max_context", 9999999)
        max_tokens = gen_params.get("max_tokens", 2000)
        target_words = params.get("target_words", 2000)

        # 获取小说档案
        novel_profile = {}
        if project:
            novel_profile = project.novel_profile

        # M3: 刷新正则脚本到引擎（确保使用最新脚本）
        self._refresh_regex_scripts()

        # M3: 获取当前章节元数据（用于模板渲染上下文）
        chapter_metadata = dict(self._current_chapter.metadata) if self._current_chapter else {}

        # 调用 PromptAssembler 组装（M4: 传入提取的 context_entries）
        try:
            user_input = (
                user_input_override
                if user_input_override is not None
                else self.continuation_panel.get_user_input()
            )
            lookback_chapters = params.get("lookback_chapters", 0)
            chapter_for_assemble = self._current_chapter
            assemble_result = self.prompt_assembler.assemble(
                preset=preset,
                chapters=self._current_chapters,
                current_chapter=chapter_for_assemble,
                context_entries=entries,
                model=model,
                max_context=max_context,
                max_tokens=max_tokens,
                target_words=target_words,
                novel_profile=novel_profile,
                project_id=self._current_project_id or "",
                chapter_metadata=chapter_metadata,
                user_input=user_input,
                lookback_chapters=lookback_chapters,
                world_ontology=project.world_ontology if project else None,
                protagonist_profile=self._protagonist_profile_by_chapter.get(
                    self._current_chapter.id if self._current_chapter else ""
                ),
                custom_audit_rules=project.custom_audit_rules if project else None,
                style_profile=project.style_profile if project else None,
            )
        except Exception as e:
            logger.error("提示词组装失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "组装失败", f"提示词组装失败: {e}")
            return

        messages = assemble_result.messages

        # 显示 token 预算信息到状态栏
        usage = assemble_result.token_usage
        is_exact, count_desc = assemble_result.count_mode
        self._token_count_label.setText(
            f"Token: {usage.get('total_used', 0)}/{max_context} ({count_desc})"
        )

        # 显示警告
        for warning in assemble_result.warnings:
            self._set_status_message(warning)
            if "建议增大 max_context" in warning or "截断后仅" in warning:
                QMessageBox.warning(self, "Token 预算警告", warning)

        logger.info(
            "提示词组装完成: %d 条消息, token=%d/%d (系统=%d, 注入=%d, 历史=%d章)",
            len(messages),
            usage.get("total_used", 0),
            max_context,
            usage.get("system_tokens", 0),
            usage.get("injection_tokens", 0),
            assemble_result.history_chapter_count,
        )

        # M3: 收集正则脚本 ID 快照（用于 swipe 记录）
        ordered_scripts = self.regex_service.get_ordered_scripts(
            project_id=self._current_project_id or "",
            preset_id=preset.id,
            include_disabled=False,
        )
        regex_script_ids = [s.id for s, _ in ordered_scripts]

        # M4: 构建上下文条目快照（存入 swipe）
        context_snapshot = [
            e.model_dump(mode="json") if hasattr(e, "model_dump") else e
            for e in entries
        ]

        # 锁定编辑器
        self.chapter_editor.set_streaming_locked(True)
        self.continuation_panel.start_streaming()

        # M5: 记录续写会话追踪信息（用于历史日志）
        self._continuation_started_at = self.history_service.now_iso()
        self._continuation_prompt_messages = list(messages)
        self._continuation_model = model
        self._continuation_parameters = dict(params)

        # 断开旧 worker 的信号连接，避免内存泄漏
        old_worker = getattr(self, "_continuation_worker", None)
        if old_worker is not None:
            try:
                old_worker.chunk_received.disconnect()
                old_worker.token_count.disconnect()
                old_worker.finished.disconnect()
                old_worker.error.disconnect()
                old_worker.rate_limit_warning.disconnect()
                old_worker.auth_error.disconnect()
            except (RuntimeError, TypeError):
                pass  # 信号可能已断开或对象已删除
            old_worker.deleteLater()

        # 记录续写发起章节（完成回调用此 id 归档，避免章节切换后存错章节）
        self._continuation_chapter_id = self._current_chapter.id
        # 初始化按章节缓冲的流式输出（切回时恢复 UI）
        self._continuation_stream_text_by_chapter[self._continuation_chapter_id] = ""

        # 注入思考强度（预设级优先，其次端点级）
        params["reasoning_effort"] = self._resolve_reasoning_effort(endpoint, preset)

        # 创建并启动 worker（M4: 注入 extracted_context_snapshot）
        self._continuation_worker = ContinuationWorker(
            base_url=endpoint["base_url"],
            api_key=api_key,
            model=model,
            messages=messages,
            parameters=params,
            chapter_id=self._current_chapter.id,
            created_by="continuation",
            preset_id=preset.id,
            preset_snapshot=preset.model_dump(mode="json"),
            token_budget=usage,
            regex_engine=self.regex_engine,
            template_engine=self.template_engine,
            project_id=self._current_project_id or "",
            chapter_metadata=chapter_metadata,
            regex_script_ids=regex_script_ids,
            extracted_context_snapshot=context_snapshot,
            endpoint_id=endpoint.get("id", ""),
            debug_mode=self._debug_mode,
            extra_payload=endpoint.get("extra_payload") or {},
            extra_headers=endpoint.get("extra_headers") or {},
            parent=self,
        )

        # chunk 路由：按章节缓冲，仅当前章节匹配时更新 UI（避免污染新章节视图）
        self._continuation_worker.chunk_received.connect(
            self._on_continuation_chunk_received
        )
        self._continuation_worker.token_count.connect(
            lambda count: self._token_count_label.setText(
                f"Token: {count} (接收中)"
            )
        )
        self._continuation_worker.finished.connect(self._on_continuation_finished)
        self._continuation_worker.error.connect(self._on_continuation_error)
        self._continuation_worker.rate_limit_warning.connect(
            lambda msg: self.continuation_panel.show_toast(msg)
        )
        self._continuation_worker.auth_error.connect(self._on_auth_error)
        self._continuation_worker.prompt_debug_requested.connect(
            self._on_prompt_debug_requested
        )

        self._continuation_worker.start()
        self._set_status_message("续写中...")

    # ===== 模式路由与卷续写流程 =====

    def _on_start_continuation_routed(self, params: dict) -> None:
        """根据当前续写模式路由（兼容入口，委托给 _on_start_flow）。

        Args:
            params: 续写参数字典（由 continuation_panel 传入）
        """
        plugin_id = self.continuation_panel.get_mode()
        self._on_start_flow(plugin_id, params)

    def _on_mode_changed(self, mode: str) -> None:
        """续写模式切换回调（基于插件 ui_mode 决定面板显隐）。

        Args:
            mode: 插件 ID（内置为 single/volume/rewrite_current）
        """
        plugin = self.flow_plugin_service.load_plugin(mode)
        if plugin and plugin.ui_mode == "volume":
            # 卷模式：显示 volume_panel，隐藏单次参数区
            self.continuation_panel.show_volume_panel(True)
        else:
            # 标准模式：隐藏 volume_panel，显示单次参数区
            self.continuation_panel.show_volume_panel(False)
        logger.info("续写模式切换: %s", mode)

    # ===== 流程控制插件系统 =====

    def _register_flow_handlers(self) -> None:
        """注册 5 种 agent 类型的 handler 到 FlowExecutor。

        handler 签名：handler(stage: FlowStage, params: dict, context: dict) -> Any
        返回值约定：
        - ``"pending"``：挂起等待用户交互（如 AuditDialog 采纳）
        - ``"cancel"``：用户取消，中断流程
        - 其他：阶段输出，经 input_from 传入下一阶段
        """
        self.flow_executor.register_handler(
            "continuation", self._flow_handler_continuation
        )
        self.flow_executor.register_handler("audit", self._flow_handler_audit)
        self.flow_executor.register_handler(
            "checkpoint", self._flow_handler_checkpoint
        )
        self.flow_executor.register_handler(
            "volume_pipeline", self._flow_handler_volume
        )
        self.flow_executor.register_handler(
            "volume_phase", self._flow_handler_volume_phase
        )

    def _refresh_flow_plugins(self) -> None:
        """刷新续写面板的插件下拉列表（内置在前，自定义按序）。"""
        plugins = self.flow_plugin_service.list_plugins()
        # 内置在前，自定义按 ID 排序
        plugins.sort(key=lambda p: (not p.builtin, p.id))
        self.continuation_panel.set_flow_plugins(plugins)

    def _on_start_flow(self, plugin_id: str, params: dict) -> None:
        """统一流程启动入口：加载插件 → FlowExecutor 执行阶段序列。

        Args:
            plugin_id: 流程插件 ID（内置为 single/volume/rewrite_current）
            params: 续写参数（来自面板，含 model/temperature 等）
        """
        plugin = self.flow_plugin_service.load_plugin(plugin_id)
        if not plugin:
            QMessageBox.warning(self, "错误", f"流程插件不存在: {plugin_id}")
            return
        # 存储 plugin_id 到 params，供 accept 时查找 accept_mode
        params["_flow_plugin_id"] = plugin_id
        context = {
            "chapter": self._current_chapter,
            "entries": self._current_context_entries,
        }
        self.flow_executor.execute(plugin, params, context)

    def _flow_handler_continuation(self, stage, params: dict, context: dict):
        """continuation agent handler：流式续写创建 swipe。

        根据 ``_prev_output`` 与 ``created_by`` 判断分支：
        - ``created_by=="rewrite_current"`` + ``_prev_output``：重写生成步骤，
          调用 ``_on_rewrite_analysis_accepted``
        - ``created_by=="writing_mode"`` + ``_prev_output``：写作模式第 3 步，
          调用 ``_on_start_writing_mode_continuation``（精炼输出前置【写作参考】到 user_input）
        - 其余：独立续写，调用 ``_on_start_continuation``
        """
        prev_output = params.get("_prev_output")
        if prev_output and stage.created_by == "rewrite_current":
            # 重写生成步骤：prev_output 是分析文本
            self._on_rewrite_analysis_accepted(prev_output)
        elif prev_output and stage.created_by == "writing_mode":
            # 写作模式第 3 步：prev_output 是阶段 2 精炼输出
            self._on_start_writing_mode_continuation(params, prev_output)
        else:
            # 独立续写
            self._on_start_continuation(params)
        return None

    def _flow_handler_audit(self, stage, params: dict, context: dict):
        """audit agent handler：启动分析流程，返回 pending 等待用户采纳。

        按 ``stage.flow_key`` 分派：
        - ``rewrite_analysis``：内置 rewrite_current 插件，调 ``_on_start_rewrite_current``
          （AuditDialog.accepted_text 信号触发 ``_on_rewrite_analysis_accepted`` 完成生成步骤，
          内置插件由信号链直接完成两步流程，FlowExecutor 在 audit handler 返回 pending 后结束）
        - 其余（如 ``writing_element_analysis`` / ``writing_element_refinement``）：调
          ``_on_start_generic_analysis`` 通用分析路径（读 ``params._prev_output`` 注入
          ``{{prev_analysis}}``，用户采纳后 ``flow_executor.resume`` 推进下一阶段）
        """
        if stage.flow_key == "rewrite_analysis":
            self._on_start_rewrite_current(params)
        else:
            phase = stage.params.get("phase", stage.flow_key)
            phase_name = stage.params.get("phase_name", phase)
            self._on_start_generic_analysis(stage, params, phase, phase_name)
        return "pending"

    def _flow_handler_checkpoint(self, stage, params: dict, context: dict):
        """checkpoint agent handler：暂停点，等待用户确认。

        当前为占位实现，直接返回 continue 推进下一阶段。
        """
        return "continue"

    def _flow_handler_volume(self, stage, params: dict, context: dict):
        """volume_pipeline agent handler：卷级多章节续写。

        调用 ``_on_start_volume_continuation`` 启动 VolumeOrchestrator，
        内部 7 阶段流程由 orchestrator 自行管理，不介入。
        """
        self._on_start_volume_continuation(params)
        return None

    def _flow_handler_volume_phase(self, stage, params: dict, context: dict):
        """volume_phase agent handler：VolumeOrchestrator 单阶段执行。

        首次进入时调用 _prepare_volume_run 准备数据并存入 _volume_state。
        每次从 stage.params["phase"] 取阶段名，从 _volume_state 取输入产物，
        创建 VolumeOrchestrator 并启动。返回 "pending" 等待信号回调。

        阶段 1-3 完成后 VolumeOrchestrator emit phase_output，
        由 _on_volume_phase_output 恢复 FlowExecutor；
        阶段 4（chapter_writing）完成后 emit finished，
        由 _on_volume_continuation_finished 清理 FlowExecutor。
        """
        phase = stage.params.get("phase", "all")
        if self._volume_state is None:
            prepare_data = self._prepare_volume_run(params)
            if prepare_data is None:
                return "cancel"  # 校验失败，中断流程
            self._volume_state = prepare_data
        else:
            prepare_data = self._volume_state
        self._start_volume_phase(phase, prepare_data, stage)
        return "pending"

    def _on_output_panel_visibility_requested(self, visible: bool) -> None:
        """卷模式切换时显隐右侧续写输出面板。

        卷模式开启时隐藏第 4 栏（输出面板）并把其空间让给第 3 栏（续写控制，
        内含 VolumePanel 的产物查看器）；卷模式关闭时恢复默认面板尺寸。

        Args:
            visible: True 显示输出面板（离开卷模式），False 隐藏（进入卷模式）
        """
        output_widget = self._splitter.widget(4)
        output_widget.setVisible(visible)
        if visible:
            # 恢复默认面板尺寸
            self._splitter.setSizes(DEFAULT_PANEL_SIZES)
        else:
            # 卷模式：将输出面板空间让给续写控制面板（VolumePanel）
            sizes = self._splitter.sizes()
            # 把第 4 栏（输出）的尺寸加到第 3 栏（续写控制）
            if len(sizes) >= 5:
                sizes[3] += sizes[4]
                sizes[4] = 0
                self._splitter.setSizes(sizes)

    # ===== 卷续写流程 =====

    def _on_start_volume_continuation(self, params: dict) -> None:
        """开始卷级多章节续写（volume_pipeline agent 入口，向后兼容）。

        重构为薄包装：准备数据 → 启动 phase="all" 完整流程。
        """
        prepare_data = self._prepare_volume_run(params)
        if prepare_data is None:
            return
        self._volume_state = prepare_data
        self._start_volume_phase("all", prepare_data, None)

    def _prepare_volume_run(self, params: dict) -> dict | None:
        """卷续写准备：校验 + 加载预设/上下文/项目数据。

        校验失败时弹提示并返回 None；成功返回包含所有启动数据的 dict。
        volume_phase 多阶段流程中仅在首次进入 handler 时调用一次。

        Args:
            params: 面板参数

        Returns:
            启动数据 dict（endpoint/api_key/model/params/config/entries/
            project/preset/novel_profile/chapter_metadata/regex_script_ids/
            user_input），校验失败返回 None
        """
        if not self._current_chapter:
            QMessageBox.warning(self, "提示", "请先选择章节")
            return None

        endpoint = self.continuation_panel.get_selected_endpoint()
        if not endpoint:
            QMessageBox.warning(self, "提示", "请先配置 API 端点")
            self._on_open_settings()
            return None

        api_key = self.config_manager.decrypt_api_key(endpoint.get("id", ""))
        if not api_key:
            QMessageBox.warning(self, "提示", "API Key 无效，请检查设置")
            self._on_open_settings()
            return None

        model = self.config_manager.get_flow_model("volume_continuation") or params.get("model", "")
        if not model:
            QMessageBox.warning(self, "提示", "请选择模型")
            return None

        # 获取 VolumeRunConfig
        config = self.continuation_panel.get_volume_config()

        # 上下文条目（与 agent 模式一致，提示用户可先提取）
        raw_entries = getattr(self, "_current_context_entries", None)
        # 合并全局世界书条目（uid 冲突时世界书优先）
        entries = self._merge_worldbook_entries(raw_entries)
        # 加载项目对象（用于检查世界观/主角是否已提取）
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)
        wo_for_check = project.world_ontology if project else None
        pp_for_check = self._protagonist_profile_by_chapter.get(self._current_chapter.id) if self._current_chapter else None
        sp_for_check = project.style_profile if project else None
        if not self._prompt_continue_without_extraction(entries, wo_for_check, pp_for_check, sp_for_check):
            return None

        # 确保章节正文已加载（list_chapters 只加载元数据）
        self._ensure_chapter_contents()

        # 加载预设
        preset_id = self.continuation_panel.volume_panel.get_selected_preset_id()
        preset = self.preset_service.load_preset(preset_id)
        if preset is None:
            preset = self.preset_service.load_default_preset()
            logger.warning("预设 %s 不存在，回退到默认预设", preset_id)

        # 获取小说档案
        novel_profile = project.novel_profile if project else {}

        # 刷新正则脚本到引擎
        self._refresh_regex_scripts()

        # 章节元数据
        chapter_metadata = (
            dict(self._current_chapter.metadata) if self._current_chapter else {}
        )

        # 收集正则脚本 ID 快照
        ordered_scripts = self.regex_service.get_ordered_scripts(
            project_id=self._current_project_id or "",
            preset_id=preset.id,
            include_disabled=False,
        )
        regex_script_ids = [s.id for s, _ in ordered_scripts]

        # 用户输入
        user_input = self.continuation_panel.get_user_input()

        # 锁定编辑器（卷模式不使用右侧输出面板的流式状态，
        # 流式输出由 VolumePanel 自身的当前章节正文流式区承接）
        self.chapter_editor.set_streaming_locked(True)

        # 记录会话追踪信息（model 在启动时记录，prompt_messages 在 finished 时从 orchestrator 获取）
        self._continuation_started_at = self.history_service.now_iso()
        self._continuation_parameters = dict(params)
        self._continuation_model = model
        # 占位：VolumeOrchestrator 完成后用 get_writing_messages() 填充
        self._continuation_prompt_messages = []

        # 重置 Volume 面板进度与产物（同时清空流式区与整卷进度条）
        self._volume_completed_phases = []
        self.continuation_panel.volume_panel.reset()

        # 注入思考强度（预设级优先，其次端点级）
        params["reasoning_effort"] = self._resolve_reasoning_effort(endpoint, preset)

        return {
            "endpoint": endpoint,
            "api_key": api_key,
            "model": model,
            "params": params,
            "config": config,
            "entries": entries,
            "project": project,
            "preset": preset,
            "novel_profile": novel_profile,
            "chapter_metadata": chapter_metadata,
            "regex_script_ids": regex_script_ids,
            "user_input": user_input,
        }

    def _start_volume_phase(
        self, phase: str, prepare_data: dict, stage
    ) -> None:
        """创建并启动 VolumeOrchestrator 执行指定阶段。

        服务两种模式：
        - phase="all"：完整 7 阶段流程（volume_pipeline agent 向后兼容）
        - phase="deep_analysis"/"volume_outline"/"outline_audit"/
          "chapter_writing"：单阶段执行（volume_phase agent）

        phase_inputs 从 self._volume_state 取前序阶段产物。

        Args:
            phase: 执行阶段名
            prepare_data: _prepare_volume_run 返回的启动数据 dict
            stage: FlowStage 对象（volume_pipeline 模式传 None）
        """
        endpoint = prepare_data["endpoint"]
        api_key = prepare_data["api_key"]
        model = prepare_data["model"]
        params = prepare_data["params"]
        config = prepare_data["config"]
        entries = prepare_data["entries"]
        project = prepare_data["project"]
        preset = prepare_data["preset"]
        novel_profile = prepare_data["novel_profile"]
        chapter_metadata = prepare_data["chapter_metadata"]
        regex_script_ids = prepare_data["regex_script_ids"]
        user_input = prepare_data["user_input"]

        # 断开旧 volume orchestrator 的信号连接，避免内存泄漏
        old_orchestrator = getattr(self, "_volume_orchestrator", None)
        if old_orchestrator is not None:
            try:
                old_orchestrator.phase_started.disconnect()
                old_orchestrator.phase_finished.disconnect()
                old_orchestrator.chapter_started.disconnect()
                old_orchestrator.chapter_finished.disconnect()
                old_orchestrator.chunk_received.disconnect()
                old_orchestrator.token_count.disconnect()
                old_orchestrator.checkpoint_reached.disconnect()
                old_orchestrator.finished.disconnect()
                old_orchestrator.error.disconnect()
                old_orchestrator.auth_error.disconnect()
                old_orchestrator.phase_output.disconnect()
            except (RuntimeError, TypeError):
                pass  # 信号可能已断开或对象已删除
            old_orchestrator.deleteLater()

        # 创建 VolumeOrchestrator（传 phase/phase_inputs 支持分阶段执行）
        self._volume_orchestrator = VolumeOrchestrator(
            base_url=endpoint["base_url"],
            api_key=api_key,
            model=model,
            parameters=params,
            preset=preset,
            chapters=self._current_chapters,
            current_chapter=self._current_chapter,
            context_entries=entries,
            config=config,
            user_input=user_input,
            novel_profile=novel_profile,
            project_id=self._current_project_id or "",
            chapter_metadata=chapter_metadata,
            regex_engine=self.regex_engine,
            template_engine=self.template_engine,
            regex_script_ids=regex_script_ids,
            preset_id=preset.id,
            preset_snapshot=preset.model_dump(mode="json"),
            chapter_id=self._current_chapter.id,
            endpoint_id=endpoint.get("id", ""),
            world_ontology=project.world_ontology if project else None,
            protagonist_profile=self._protagonist_profile_by_chapter.get(self._current_chapter.id),
            custom_audit_rules=project.custom_audit_rules if project else None,
            phase=phase,
            phase_inputs=self._volume_state,
            extra_payload=endpoint.get("extra_payload") or {},
            extra_headers=endpoint.get("extra_headers") or {},
            parent=self,
        )
        self._volume_orchestrator.debug_mode = self._debug_mode

        # 连接信号
        self._volume_orchestrator.phase_started.connect(
            self._on_volume_phase_started
        )
        self._volume_orchestrator.phase_finished.connect(
            self._on_volume_phase_finished
        )
        self._volume_orchestrator.chapter_started.connect(
            self._on_volume_chapter_started
        )
        self._volume_orchestrator.chapter_finished.connect(
            self._on_volume_chapter_finished
        )
        self._volume_orchestrator.chapter_step_started.connect(
            self._on_volume_chapter_step_started
        )
        self._volume_orchestrator.chunk_received.connect(
            self.continuation_panel.volume_panel.append_chapter_chunk
        )
        self._volume_orchestrator.token_count.connect(
            lambda count: self._token_count_label.setText(
                f"Token: {count} (接收中)"
            )
        )
        self._volume_orchestrator.checkpoint_reached.connect(
            self._on_volume_checkpoint
        )
        self._volume_orchestrator.finished.connect(
            self._on_volume_continuation_finished
        )
        self._volume_orchestrator.error.connect(self._on_continuation_error)
        self._volume_orchestrator.auth_error.connect(self._on_auth_error)
        self._volume_orchestrator.prompt_debug_requested.connect(
            self._on_prompt_debug_requested
        )
        self._volume_orchestrator.phase_output.connect(
            self._on_volume_phase_output
        )

        # 记录卷续写发起章节（完成回调用此 id 定位插入点，避免切换章节后插入错误位置）
        self._volume_chapter_id = self._current_chapter.id if self._current_chapter else None
        # 记录当前执行阶段（重试用）
        self._volume_current_phase = phase

        self._volume_orchestrator.start()
        self._set_status_message("卷续写中...")

    def _on_volume_phase_started(self, phase: str) -> None:
        """卷级阶段开始回调：更新卷阶段进度指示器。

        Args:
            phase: 卷阶段名（deep_analysis/volume_outline/outline_audit）
        """
        # 映射 orchestrator 阶段名到面板阶段名
        panel_phase = self._map_volume_phase(phase)
        self.continuation_panel.volume_panel.update_volume_phase_progress(
            panel_phase, self._volume_completed_phases
        )

    def _on_volume_phase_output(self, phase: str, artifact: object) -> None:
        """volume_phase 单阶段产物回调：存入 _volume_state 并恢复 FlowExecutor。

        阶段 1-3 完成后由 phase_output 信号触发，将产物存入 _volume_state[phase]，
        调用 flow_executor.resume(artifact) 推进到下一阶段。
        恢复模式（_volume_resuming=True）下手动启动下一阶段。
        """
        if self._volume_state is None:
            self._volume_state = {}
        self._volume_state[phase] = artifact
        # 持久化状态到磁盘（暂存节点）
        self._save_volume_state()
        if self.flow_executor.is_active:
            # FlowExecutor 驱动的多阶段流程：resume 推进下一阶段
            self.flow_executor.resume(artifact)
        elif self._volume_resuming:
            # 恢复模式：手动启动下一阶段
            next_phase = self._get_next_volume_phase(phase)
            if next_phase:
                self._start_volume_phase(next_phase, self._volume_state, None)

    @staticmethod
    def _get_next_volume_phase(current_phase: str) -> str | None:
        """获取下一个卷阶段名。"""
        phases = ["deep_analysis", "volume_outline", "outline_audit", "chapter_writing"]
        try:
            idx = phases.index(current_phase)
        except ValueError:
            return None
        if idx + 1 < len(phases):
            return phases[idx + 1]
        return None

    def _get_volume_state_path(self, chapter_id: str) -> Path:
        """卷续写状态文件路径。"""
        return self.storage_service.storage_path / "volume_states" / f"{chapter_id}.json"

    def _save_volume_state(self) -> None:
        """持久化卷续写中间状态到磁盘（暂存节点）。"""
        if self._volume_state is None or not self._volume_chapter_id:
            return
        state = self._volume_state
        # 序列化阶段产物
        phase_artifacts: dict = {}
        completed_phases: list[str] = []
        for phase in ["deep_analysis", "volume_outline", "outline_audit"]:
            artifact = state.get(phase)
            if artifact is not None:
                completed_phases.append(phase)
                phase_artifacts[phase] = artifact.model_dump(mode="json")
        # 构建持久化结构
        preset = state.get("preset")
        config = state.get("config")
        persist = {
            "chapter_id": self._volume_chapter_id,
            "project_id": self._current_project_id or "",
            "preset_id": preset.id if preset else "",
            "endpoint_id": state.get("endpoint", {}).get("id", ""),
            "model": state.get("model", ""),
            "config": config.model_dump(mode="json") if config else None,
            "user_input": state.get("user_input", ""),
            "params": state.get("params", {}),
            "current_phase": self._volume_current_phase or "",
            "completed_phases": completed_phases,
            "phase_artifacts": phase_artifacts,
            "created_at": self.history_service.now_iso(),
        }
        path = self._get_volume_state_path(self._volume_chapter_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_file(path, json.dumps(persist, ensure_ascii=False, indent=2))

    def _load_volume_state(self, chapter_id: str) -> dict | None:
        """从磁盘加载卷续写状态。"""
        path = self._get_volume_state_path(chapter_id)
        if not path.exists():
            return None
        data, err = load_json_with_recovery(path)
        if err or data is None:
            return None
        return data

    def _delete_volume_state(self, chapter_id: str) -> None:
        """删除卷续写状态文件。"""
        path = self._get_volume_state_path(chapter_id)
        if path.exists():
            try:
                path.unlink()
            except OSError as e:
                logger.warning("删除卷续写状态失败: %s", e)

    def _resume_volume_state(self, data: dict) -> None:
        """从持久化状态恢复卷续写流程。

        重建 prepare_data（reload endpoint/project/preset/entries），
        反序列化已完成阶段产物，确定下一未完成阶段，以恢复模式启动。

        Args:
            data: _load_volume_state 返回的持久化状态 dict
        """
        from novelforge.models import (
            DeepAnalysis, VolumeOutline, OutlineAuditReport
        )

        chapter_id = data.get("chapter_id", "")
        endpoint_id = data.get("endpoint_id", "")
        project_id = data.get("project_id", "")
        preset_id = data.get("preset_id", "")
        model = data.get("model", "")
        user_input = data.get("user_input", "")
        params = data.get("params", {})
        completed_phases = data.get("completed_phases", [])
        phase_artifacts = data.get("phase_artifacts", {})

        # 重建 endpoint + api_key
        endpoint = self.config_manager.get_endpoint(endpoint_id) if endpoint_id else None
        if endpoint is None:
            QMessageBox.warning(self, "恢复失败", f"端点 {endpoint_id} 已不存在，无法恢复")
            return
        api_key = self.config_manager.decrypt_api_key(endpoint_id)
        if not api_key:
            QMessageBox.warning(self, "恢复失败", "API Key 无效，请检查设置")
            self._on_open_settings()
            return

        # 重建 config
        config = None
        config_data = data.get("config")
        if config_data:
            try:
                config = VolumeRunConfig.model_validate(config_data)
            except Exception as e:
                logger.warning("恢复 VolumeRunConfig 失败: %s", e)
        if config is None:
            config = self.continuation_panel.get_volume_config()

        # 重建 project + preset + novel_profile
        project = self.storage_service.load_project(project_id) if project_id else None
        if preset_id:
            preset = self.preset_service.load_preset(preset_id)
        else:
            preset = self.preset_service.load_default_preset()
        if preset is None:
            preset = self.preset_service.load_default_preset()
        novel_profile = project.novel_profile if project else {}

        # 重建 entries（当前章节上下文 + 世界书合并）
        raw_entries = getattr(self, "_current_context_entries", None)
        entries = self._merge_worldbook_entries(raw_entries)

        # 重建 chapter_metadata + regex_script_ids
        chapter_metadata = (
            dict(self._current_chapter.metadata) if self._current_chapter else {}
        )
        self._refresh_regex_scripts()
        ordered_scripts = self.regex_service.get_ordered_scripts(
            project_id=project_id,
            preset_id=preset.id,
            include_disabled=False,
        )
        regex_script_ids = [s.id for s, _ in ordered_scripts]

        # 注入 reasoning_effort（与 _prepare_volume_run 一致）
        params["reasoning_effort"] = self._resolve_reasoning_effort(endpoint, preset)

        # 构建 prepare_data
        prepare_data = {
            "endpoint": endpoint,
            "api_key": api_key,
            "model": model,
            "params": params,
            "config": config,
            "entries": entries,
            "project": project,
            "preset": preset,
            "novel_profile": novel_profile,
            "chapter_metadata": chapter_metadata,
            "regex_script_ids": regex_script_ids,
            "user_input": user_input,
        }

        # 反序列化已完成阶段产物，存入 prepare_data（作为 _volume_state）
        for phase, cls in [
            ("deep_analysis", DeepAnalysis),
            ("volume_outline", VolumeOutline),
            ("outline_audit", OutlineAuditReport),
        ]:
            artifact_data = phase_artifacts.get(phase)
            if artifact_data is not None:
                try:
                    prepare_data[phase] = cls.model_validate(artifact_data)
                except Exception as e:
                    logger.warning("恢复阶段产物 %s 失败: %s", phase, e)

        # 确定下一未完成阶段
        all_phases = ["deep_analysis", "volume_outline", "outline_audit", "chapter_writing"]
        next_phase = None
        for ph in all_phases:
            if ph not in completed_phases:
                next_phase = ph
                break
        if next_phase is None:
            # 所有阶段已完成（异常状态）：清理状态文件
            self._delete_volume_state(chapter_id)
            QMessageBox.information(self, "恢复", "该章节卷续写已完成，无需恢复")
            return

        # 设置恢复模式标志
        self._volume_state = prepare_data
        self._volume_chapter_id = chapter_id
        self._volume_current_phase = next_phase
        self._volume_resuming = True

        # 锁定编辑器 + 重置面板
        self.chapter_editor.set_streaming_locked(True)
        self._volume_completed_phases = list(completed_phases)
        self.continuation_panel.volume_panel.reset()

        # 记录会话追踪
        self._continuation_started_at = self.history_service.now_iso()
        self._continuation_parameters = dict(params)
        self._continuation_model = model
        self._continuation_prompt_messages = []

        # 启动下一阶段
        self._start_volume_phase(next_phase, prepare_data, None)

    def _on_volume_phase_finished(self, phase: str, artifact: object) -> None:
        """卷级阶段完成回调：更新产物查看器与进度指示器。

        Args:
            phase: 卷阶段名（deep_analysis/volume_outline/outline_audit/outline_final）
            artifact: 阶段产物对象（DeepAnalysis/VolumeOutline/OutlineAuditReport）
        """
        panel = self.continuation_panel.volume_panel
        if phase == "deep_analysis" and artifact is not None:
            panel.update_deep_analysis(artifact)
        elif phase == "volume_outline" and artifact is not None:
            panel.update_volume_outline(artifact)
        elif phase == "outline_audit":
            if artifact is not None:
                # 多轮审计：传入轮次索引，修订版大纲显示在审计报告 Tab（不覆盖卷大纲 Tab）
                round_idx = len(panel._audit_reports)
                panel.update_audit_report(artifact, round_idx)
                panel._audit_reports.append(artifact)
        elif phase == "outline_final" and artifact is not None:
            # 终稿大纲更新到卷大纲 Tab
            panel.update_final_outline(artifact)

        # 切换产物查看 Tab 到对应阶段
        panel.switch_to_tab(phase)

        # 将 phase 加入已完成列表并刷新进度指示器
        mapped = self._map_volume_phase(phase)
        if mapped and mapped not in self._volume_completed_phases:
            self._volume_completed_phases.append(mapped)
        panel.update_volume_phase_progress("", self._volume_completed_phases)

    def _on_volume_chapter_started(self, chapter_index: int) -> None:
        """章节开始回调：更新章节进度指示器并启动流式区。

        Args:
            chapter_index: 章节序号（0 基）
        """
        config = self.continuation_panel.get_volume_config()
        panel = self.continuation_panel.volume_panel
        # 重置当前章节子步骤累积态（等待 chapter_step_started 信号驱动）
        self._volume_chapter_steps_seen = set()
        # 启动新章节流式：清空流式区并切到流式 tab
        panel.start_chapter_streaming(chapter_index)
        panel.update_chapter_progress(
            chapter_index + 1,  # 转为 1 基
            config.chapter_count,
            "",  # 当前步骤未知，待 chapter_step_started 信号覆盖
            [],
        )

    def _on_volume_chapter_finished(
        self, chapter_index: int, artifacts: object
    ) -> None:
        """章节完成回调：添加章节产物到查看器并更新进度。

        Args:
            chapter_index: 章节序号（0 基）
            artifacts: ChapterArtifacts 对象
        """
        config = self.continuation_panel.get_volume_config()
        # 从卷大纲获取章节标题（若已生成）
        title = ""
        panel = self.continuation_panel.volume_panel
        # 添加章节产物到折叠列表
        panel.add_chapter_artifacts(
            chapter_index + 1, artifacts, title
        )
        # 标记该章所有步骤完成
        panel.update_chapter_progress(
            chapter_index + 1,
            config.chapter_count,
            "",
            ["outline", "writing", "verify", "revise"],
        )

    def _on_volume_chapter_step_started(
        self, chapter_index: int, step: str
    ) -> None:
        """章节子阶段开始回调：更新章节步骤进度指示器与状态栏。

        由 VolumeOrchestrator.chapter_step_started 信号触发，在每个子阶段
        （细纲/写作/验证/修订）开始前调用。累积态 _volume_chapter_steps_seen
        记录已执行过的步骤，保证 verify↔revise 循环中已完成的步骤保留 ✓。

        Args:
            chapter_index: 章节序号（0 基）
            step: 步骤名（outline/writing/verify/revise）
        """
        config = self.continuation_panel.get_volume_config()
        panel = self.continuation_panel.volume_panel
        completed = set(self._volume_chapter_steps_seen)
        panel.update_chapter_progress(
            chapter_index + 1,
            config.chapter_count,
            step,
            list(completed),
        )
        self._volume_chapter_steps_seen.add(step)
        step_label = {
            "outline": "细纲", "writing": "写作",
            "verify": "验证", "revise": "修订",
        }.get(step, step)
        self._set_status_message(
            f"卷续写中: 第{chapter_index + 1}章 {step_label}"
        )

    def _on_volume_checkpoint(
        self, checkpoint_name: str, payload: object
    ) -> None:
        """卷级检查点暂停回调。

        before_audit：不弹对话框，改为在 VolumePanel 卷大纲 Tab 下方显示
        审计重点输入区，用户边看卷大纲边输入，点击"开始审计"恢复。
        其他检查点：仍用 CheckpointDialog 简单模式。

        Args:
            checkpoint_name: 检查点名
                （after_deep_analysis/after_volume_outline/before_audit/after_audit/after_chapter）
            payload: 检查点产物（DeepAnalysis/VolumeOutline/章节正文 dict）
        """
        panel = self.continuation_panel.volume_panel

        if checkpoint_name == "after_chapter":
            # after_chapter：弹出 ChapterConfirmDialog 显示正文，用户选择通过/不通过
            chapter_index = payload.get("chapter_index", 0) if isinstance(payload, dict) else 0
            content = payload.get("content", "") if isinstance(payload, dict) else ""
            dialog = ChapterConfirmDialog(chapter_index, content, self)
            dialog.exec()
            action, feedback = dialog.get_result()
            if action == "approve":
                self._volume_orchestrator.resume({"action": "approve"})
            elif action == "reject":
                self._volume_orchestrator.resume({"action": "reject", "feedback": feedback})
            else:  # cancel
                panel.hide_continue_button()
                self._volume_orchestrator.stop()
            return

        if checkpoint_name == "before_audit":
            # before_audit：面板内嵌输入，不弹对话框
            panel.show_audit_focus_input()
            return

        titles = {
            "after_deep_analysis": "深度分析检查点",
            "after_volume_outline": "卷大纲检查点",
            "after_audit": "审计后检查点",
        }
        title = titles.get(checkpoint_name, "卷续写检查点")

        # 使用 CheckpointDialog 简单模式交互
        dialog = CheckpointDialog(checkpoint_name, payload, self)
        dialog.setWindowTitle(title)
        dialog.exec()
        action, result_payload = dialog.get_result()

        if action == "accept":
            panel.hide_continue_button()
            self._volume_orchestrator.resume(payload)
        elif action == "edit":
            # 用户选择编辑：显示面板继续按钮，不立即 resume
            panel.show_continue_button(checkpoint_name)
        else:  # cancel
            # 取消整个卷续写流程
            panel.hide_continue_button()
            self._volume_orchestrator.stop()

    def _on_volume_continue(self, checkpoint_name: str, payload: object = None) -> None:
        """VolumePanel 继续按钮点击：恢复 orchestrator。

        before_audit：payload 为用户输入的审计重点字符串。
        其他检查点：payload 为 None，读取面板编辑后的产物。

        Args:
            checkpoint_name: 检查点名
                （after_deep_analysis/after_volume_outline/before_audit/after_audit）
            payload: before_audit 时为审计重点字符串，其他为 None
        """
        panel = self.continuation_panel.volume_panel
        if checkpoint_name == "before_audit":
            panel.hide_continue_button()
            self._volume_orchestrator.resume(payload)
        else:
            edited = self._get_edited_volume_checkpoint_payload(checkpoint_name)
            panel.hide_continue_button()
            self._volume_orchestrator.resume(
                edited if edited is not None else None
            )

    def _on_volume_cancel_checkpoint(self) -> None:
        """用户在面板检查点输入区点击"取消续写"。"""
        panel = self.continuation_panel.volume_panel
        panel.hide_continue_button()
        panel.hide_audit_focus_input()
        self._volume_orchestrator.stop()

    def _get_edited_volume_checkpoint_payload(
        self, checkpoint_name: str
    ) -> object:
        """从 VolumePanel 获取编辑后的检查点产物。

        Args:
            checkpoint_name: 检查点名

        Returns:
            编辑后的产物对象，解析失败或编辑器为空时返回 None
        """
        panel = self.continuation_panel.volume_panel
        if checkpoint_name == "after_deep_analysis":
            return panel.get_edited_deep_analysis()
        elif checkpoint_name in ("after_volume_outline", "after_audit"):
            return panel.get_edited_volume_outline()
        return None

    def _on_volume_continuation_finished(
        self, continuation: Continuation
    ) -> None:
        """卷续写完成回调：处理完成的 Continuation。

        镜像 _on_continuation_finished，但从 VolumeOrchestrator 获取
        写作阶段的 model 与 messages 快照，更新 _continuation_prompt_messages
        后再记录历史日志。

        每章作为新章节插入到发起章节之后（后续章节 index 后移）。
        使用 _volume_chapter_id 定位插入点，避免用户切换章节后插入错误位置。
        VolumePanel 流式区显示完整卷正文（保持现有行为）。
        """
        from novelforge.services.storage_service import _generate_id
        from novelforge.models import Chapter

        # 使用发起章节 id 定位插入点（加载章节对象取 index/project_id）
        volume_cid = self._volume_chapter_id
        origin_chapter = None
        if volume_cid:
            origin_chapter = self.storage_service.load_chapter(volume_cid)
        # 回退：若未记录或加载失败，用当前章节
        if origin_chapter is None and self._current_chapter:
            origin_chapter = self._current_chapter
        on_origin_chapter = bool(
            self._current_chapter
            and origin_chapter
            and self._current_chapter.id == origin_chapter.id
        )

        if on_origin_chapter:
            self.continuation_panel.stop_streaming()
            self.chapter_editor.set_streaming_locked(False)

        # 从 volume orchestrator 获取写作阶段的 model 与 messages 快照
        if self._volume_orchestrator is not None:
            self._continuation_model = (
                self._volume_orchestrator.get_writing_model()
            )
            self._continuation_prompt_messages = (
                self._volume_orchestrator.get_writing_messages()
            )

        # 拆分每章为新章节，插入到发起章节之后
        if origin_chapter and continuation.volume_artifacts:
            try:
                chapter_artifacts = continuation.volume_artifacts.chapter_artifacts
                current_index = origin_chapter.index
                project_id = origin_chapter.project_id

                # 从终稿大纲获取章节标题（按 chapter_index 匹配）
                final_outline = continuation.volume_artifacts.final_outline
                chapter_titles: dict[int, str] = {}
                if final_outline is not None:
                    for plan in final_outline.chapters:
                        chapter_titles[plan.index] = plan.title

                # 计算新章节数量
                n_new = sum(1 for ca in chapter_artifacts if ca.content)
                if n_new > 0:
                    # 将发起章节之后的所有章节 index 后移 n_new 位
                    later_chapters = [
                        ch for ch in self._current_chapters
                        if ch.index > current_index
                    ]
                    for ch in later_chapters:
                        self.storage_service.update_chapter_index(
                            ch.id, ch.index + n_new
                        )

                    # 为每章创建新章节
                    new_chapter_index = current_index + 1
                    for ca in chapter_artifacts:
                        if not ca.content:
                            continue
                        title = chapter_titles.get(
                            ca.chapter_index, f"第{new_chapter_index}章"
                        )
                        new_chapter = Chapter(
                            id=_generate_id("ch_"),
                            project_id=project_id,
                            index=new_chapter_index,
                            title=title,
                            content=ca.content,
                            word_count=len(ca.content),
                        )
                        self.storage_service.save_chapter(new_chapter)
                        new_chapter_index += 1

                    # 刷新章节列表
                    self._refresh_chapter_list()
            except Exception as e:
                logger.error("保存卷续写章节失败: %s", e, exc_info=True)
                QMessageBox.warning(self, "警告", f"部分章节保存失败: {e}")

        # 卷模式：在 VolumePanel 流式区显示完整卷正文
        # （不调用 set_current_swipe，避免写入已被隐藏的右侧输出面板）
        self.continuation_panel.volume_panel.set_full_volume_content(
            continuation.content
        )

        # M5: 记录历史日志（用容器 Continuation 的 id 和完整卷正文）
        self._record_history(
            swipe_id=continuation.id,
            status=continuation.status,
            output_text=continuation.content,
            error_message="",
        )

        # 清理 volume orchestrator 引用与发起章节标记
        self._volume_orchestrator = None
        self._volume_chapter_id = None
        # 清理 volume_phase 多阶段状态与 FlowExecutor（chapter_writing 阶段结束流程）
        self._volume_state = None
        self._volume_current_phase = None
        self._volume_resuming = False
        self._volume_chapter_steps_seen = set()
        if self.flow_executor.is_active:
            self.flow_executor.cancel()
        # 删除暂存状态文件（流程已完成）
        if volume_cid:
            self._delete_volume_state(volume_cid)

        self._set_status_message(
            f"卷续写完成: {len(continuation.content)} 字, 状态: {continuation.status}"
        )

    @staticmethod
    def _map_volume_phase(phase: str) -> str:
        """将 VolumeOrchestrator 阶段名映射到 VolumePanel 阶段名。

        Args:
            phase: orchestrator 阶段名（deep_analysis/volume_outline/outline_audit）

        Returns:
            面板阶段名（deep_analysis/volume_outline/audit），无法映射时返回空串
        """
        mapping = {
            "deep_analysis": "deep_analysis",
            "volume_outline": "volume_outline",
            "outline_audit": "audit",
        }
        return mapping.get(phase, "")

    def _handle_extraction_failure(
        self,
        project: "Project | None",
        params: dict,
        error: str,
    ) -> "list | None":
        """处理上下文提取失败/取消。

        弹出 ExtractionDialog，根据用户选择：
        - 重试：重新调用 extract，递归处理
        - 跳过：返回空列表
        - 取消：返回 None

        Args:
            project: 项目对象
            params: 续写参数
            error: 错误信息

        Returns:
            entries 列表（重试成功/跳过时），None（用户取消续写时）
        """
        # 判断是失败还是用户取消
        is_cancelled = "取消" in error or "cancel" in error.lower()
        mode = "cancelled" if is_cancelled else "failed"

        dialog = ExtractionDialog(self, mode=mode, error=error)
        dialog.exec()
        choice = dialog.result_code()

        if choice == ExtractionDialog.RESULT_CANCEL:
            return None

        if choice == ExtractionDialog.RESULT_SKIP:
            # 跳过提取，状态栏警告
            self._set_status_message("警告：跳过上下文提取，续写将无提取条目")
            return []

        # 重试
        from novelforge.services.async_runner import AsyncLoopRunner

        runner = AsyncLoopRunner.instance()
        context_panel = self.continuation_panel.context_preview_panel
        context_panel.start_extraction()
        self._set_status_message("重试提取上下文...")
        try:
            # TODO: 将此 runner.run(timeout=120) 改为非阻塞（见 spec Task 26）。
            #   该方法为递归同步函数（返回 entries/None），调用方依赖同步返回值，
            #   转换需重构整个续写流程为异步驱动（回调链/状态机），风险较高，
            #   暂保留同步实现。原 timeout=120 在重试场景最长阻塞 UI 2 分钟。
            def on_chunk(text: str) -> None:
                self._extract_chunk_received.emit(text)

            def on_batch_complete(
                entries: list, batch_idx: int, total_batches: int
            ) -> None:
                self._extract_batch_done.emit(entries, batch_idx, total_batches)

            extract_result: ExtractResult = runner.run(
                self.context_extractor.extract_streaming(
                    project=project,
                    chapters=self._current_chapters,
                    current_chapter=self._current_chapter,
                    force_refresh=True,
                    on_chunk=on_chunk,
                    on_batch_complete=on_batch_complete,
                ),
                timeout=120,
            )
        except Exception as e:
            logger.error("重试提取异常: %s", e, exc_info=True)
            extract_result = ExtractResult(
                entries=[],
                status="failed",
                error=str(e),
            )

        if extract_result.status == "completed":
            context_panel.finish_extraction(
                entries=extract_result.entries,
                elapsed_seconds=extract_result.elapsed_seconds,
                token_usage=extract_result.token_usage,
                from_cache=extract_result.from_cache,
                batch_count=extract_result.batch_count,
            )
            return extract_result.entries
        elif extract_result.status == "skipped":
            context_panel.cancel_extraction()
            self._set_status_message("无前文可提取，跳过上下文提取")
            return []
        else:
            # 仍然失败，递归处理
            context_panel.fail_extraction(extract_result.error)
            return self._handle_extraction_failure(
                project, params, extract_result.error
            )

    def _on_cancel_extraction(self) -> None:
        """用户点击取消提取按钮（非阻塞）。

        ``cancel()`` 仅设置取消事件，无需阻塞等待结果；实际的 UI 状态更新
        由流式提取的 ``on_done`` 回调经 ``_extract_done`` 信号统一处理。
        """
        from novelforge.services.async_runner import AsyncLoopRunner

        runner = AsyncLoopRunner.instance()
        loop = runner._loop

        # 非阻塞提交：用 run_coroutine_threadsafe + 回调（避免 timeout=5 阻塞 UI）
        future = asyncio.run_coroutine_threadsafe(
            self.context_extractor.cancel(), loop
        )

        def on_done(fut) -> None:
            try:
                fut.result()
            except Exception as e:
                logger.warning("取消提取请求失败: %s", e)

        future.add_done_callback(on_done)

    def _on_context_entries_changed(self, entries: list) -> None:
        """上下文条目变更（用户编辑/禁用/添加）。"""
        self._current_context_entries = entries
        # 同步到按章节绑定的内存缓存
        if self._current_chapter:
            self._context_entries_by_chapter[self._current_chapter.id] = list(entries)
            # 容量上限检查：淘汰最旧条目
            if len(self._context_entries_by_chapter) > MAX_CONTEXT_CACHE_SIZE:
                oldest = next(iter(self._context_entries_by_chapter))
                del self._context_entries_by_chapter[oldest]
        logger.debug("上下文条目变更: %d 条", len(entries))

    def _ensure_chapter_contents(self) -> None:
        """确保 _current_chapters 中所有章节的 content 已加载。

        ``list_chapters`` 只加载元数据不含正文，提取/续写前需调用此方法
        从文件系统批量加载正文，避免章节正文为空导致只显示标题。
        """
        if not self._current_chapters:
            return
        need_reload = any(not ch.content for ch in self._current_chapters)
        if not need_reload:
            return
        self._current_chapters = self.storage_service.load_chapter_contents(
            self._current_chapters
        )

    def _on_force_refresh_context(self) -> None:
        """F5 强制重新提取上下文（非阻塞）。

        复用 ``_on_extract_requested`` 的非阻塞模式：通过
        ``run_coroutine_threadsafe`` 提交协程，完成后经 ``_extract_done`` 信号
        转发到 UI 线程，由 ``_on_extract_done`` 统一处理 UI 更新
        （finish_extraction / 缓存归档 / 状态栏），避免原 ``timeout=300`` 阻塞 UI
        最长 5 分钟。
        """
        if not self._current_chapter:
            QMessageBox.warning(self, "提示", "请先选择章节")
            return

        # 确保章节正文已加载（list_chapters 只加载元数据）
        self._ensure_chapter_contents()

        # 加载项目对象
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)

        context_panel = self.continuation_panel.context_preview_panel
        context_panel.start_extraction()
        self._set_status_message("强制重新提取上下文...")

        # 读取 UI 选择的 lookback 与 token_limit 值（与"提取上下文"按钮行为一致）
        lookback_config = context_panel.get_lookback_config()
        lookback_override = lookback_config.get("lookback")
        token_limit_override = lookback_config.get("token_limit")

        # 记录正在提取的章节 ID（供 _on_extract_done 归档，防止提取过程中切换章节）
        self._extracting_chapter_id = self._current_chapter.id

        # 非阻塞提交：用 run_coroutine_threadsafe + 回调（与 _on_extract_requested 一致）
        from novelforge.services.async_runner import AsyncLoopRunner

        runner = AsyncLoopRunner.instance()
        loop = runner._loop

        def on_chunk(text: str) -> None:
            self._extract_chunk_received.emit(text)

        def on_batch_complete(
            entries: list, batch_idx: int, total_batches: int
        ) -> None:
            self._extract_batch_done.emit(entries, batch_idx, total_batches)

        future = asyncio.run_coroutine_threadsafe(
            self.context_extractor.extract_streaming(
                project=project,
                chapters=self._current_chapters,
                current_chapter=self._current_chapter,
                force_refresh=True,
                lookback_override=lookback_override,
                token_limit_override=token_limit_override,
                on_chunk=on_chunk,
                on_batch_complete=on_batch_complete,
            ),
            loop,
        )

        def on_done(fut) -> None:
            try:
                result = fut.result()
                self._extract_done.emit(result)
            except Exception as e:
                logger.error("强制重新提取异常: %s", e, exc_info=True)
                err_result = ExtractResult(
                    entries=[], status="failed", error=str(e)
                )
                self._extract_done.emit(err_result)

        future.add_done_callback(on_done)

    def _on_extract_requested(self, config: dict) -> None:
        """独立提取上下文（非阻塞，流式进度）。

        用户在上下文预览面板点击"提取上下文"按钮时触发。
        使用流式提取，不阻塞 UI 线程。
        """
        if not self._current_chapter:
            QMessageBox.warning(self, "提示", "请先选择章节")
            return

        # 确保章节正文已加载（list_chapters 只加载元数据）
        self._ensure_chapter_contents()

        endpoint = self.continuation_panel.get_selected_endpoint()
        if not endpoint:
            QMessageBox.warning(self, "提示", "请先配置 API 端点")
            self._on_open_settings()
            return

        api_key = self.config_manager.decrypt_api_key(endpoint.get("id", ""))
        if not api_key:
            QMessageBox.warning(self, "提示", "API Key 无效，请检查设置")
            self._on_open_settings()
            return

        context_panel = self.continuation_panel.context_preview_panel
        context_panel.start_extraction()
        self._set_status_message("正在提取上下文（流式）...")

        # 记录正在提取的章节 ID（供 _on_extract_done 归档，防止提取过程中切换章节）
        self._extracting_chapter_id = self._current_chapter.id

        # 加载项目对象
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)

        lookback_override = config.get("lookback")
        token_limit_override = config.get("token_limit")

        # 重写当前章节模式：上下文提取排除当前章节（当前章节是待重写对象，不是前文）
        # 缓存 key 自动带 :rewrite 后缀避免与续写模式（含当前章节）缓存互相覆盖
        exclude_current = (
            self.continuation_panel.get_mode() == "rewrite_current"
        )

        # 非阻塞提交：用 run_coroutine_threadsafe + 回调
        from novelforge.services.async_runner import AsyncLoopRunner

        runner = AsyncLoopRunner.instance()
        loop = runner._loop

        def on_chunk(text: str) -> None:
            # 通过 Signal 转发到 UI 线程（自动 QueuedConnection）
            self._extract_chunk_received.emit(text)

        def on_batch_complete(
            entries: list, batch_idx: int, total_batches: int
        ) -> None:
            # 通过 Signal 转发到 UI 线程（自动 QueuedConnection）
            self._extract_batch_done.emit(entries, batch_idx, total_batches)

        future = asyncio.run_coroutine_threadsafe(
            self.context_extractor.extract_streaming(
                project=project,
                chapters=self._current_chapters,
                current_chapter=self._current_chapter,
                lookback_override=lookback_override,
                on_chunk=on_chunk,
                token_limit_override=token_limit_override,
                on_batch_complete=on_batch_complete,
                exclude_current=exclude_current,
                jailbreak_text=self._get_flow_jailbreak_text("context_extraction"),
            ),
            loop,
        )

        def on_done(fut) -> None:
            try:
                result = fut.result()
                self._extract_done.emit(result)
            except Exception as e:
                logger.error("流式提取异常: %s", e, exc_info=True)
                err_result = ExtractResult(
                    entries=[], status="failed", error=str(e)
                )
                self._extract_done.emit(err_result)

        future.add_done_callback(on_done)

    @Slot(str)
    def _on_extract_chunk_received(self, text: str) -> None:
        """流式提取 chunk 回调：按章节缓冲，仅当前章节匹配时更新 UI。

        后台 worker 不因章节切换停止；chunk 总是缓冲到发起章节，
        UI 仅在用户停留在发起章节时更新，避免污染新章节视图。
        """
        cid = self._extracting_chapter_id
        if cid is None:
            return
        # 总是缓冲到发起章节
        self._extract_stream_text_by_chapter[cid] = (
            self._extract_stream_text_by_chapter.get(cid, "") + text
        )
        # 仅当用户停留在发起章节时更新 UI
        if self._current_chapter and self._current_chapter.id == cid:
            self.continuation_panel.context_preview_panel.update_extraction_progress(text)

    @Slot(list, int, int)
    def _on_extract_batch_done(
        self, entries: list, batch_idx: int, total_batches: int
    ) -> None:
        """流式提取批次完成回调：按章节守卫，仅当前章节匹配时增量更新 UI。

        多批次提取时，每批完成后增量更新 UI 显示。批次条目仍由
        `_on_extract_done` 归档到正确章节（_extracting_chapter_id）。
        """
        cid = self._extracting_chapter_id
        if cid is None:
            return
        # 仅当用户停留在发起章节时更新 UI
        if self._current_chapter and self._current_chapter.id == cid:
            context_panel = self.continuation_panel.context_preview_panel
            context_panel.update_entries_incremental(entries, batch_idx, total_batches)

    def _on_extract_done(self, result) -> None:
        """流式提取完成回调（在 UI 线程执行）。

        使用发起时记录的 _extracting_chapter_id 归档，避免用户切换章节后
        覆盖当前章节；仅当用户仍停留在发起章节时刷新 UI。
        """
        context_panel = self.continuation_panel.context_preview_panel
        # 按章节绑定存储（使用提取开始时记录的章节 ID）
        chapter_id = self._extracting_chapter_id or (
            self._current_chapter.id if self._current_chapter else None
        )
        on_origin_chapter = bool(
            self._current_chapter and self._current_chapter.id == chapter_id
        )

        if result.status == "completed":
            if on_origin_chapter:
                context_panel.finish_extraction(
                    entries=result.entries,
                    elapsed_seconds=result.elapsed_seconds,
                    token_usage=result.token_usage,
                    from_cache=result.from_cache,
                    batch_count=result.batch_count,
                )
                self._current_context_entries = result.entries
            # 归档到发起章节（无论用户是否切走）
            if chapter_id:
                self._context_entries_by_chapter[chapter_id] = list(result.entries)
                # 容量上限检查：淘汰最旧条目
                if len(self._context_entries_by_chapter) > MAX_CONTEXT_CACHE_SIZE:
                    oldest = next(iter(self._context_entries_by_chapter))
                    del self._context_entries_by_chapter[oldest]
            self._set_status_message(
                f"上下文提取完成: {len(result.entries)} 条 "
                f"(耗时 {result.elapsed_seconds:.2f}s)"
            )
        elif result.status == "skipped":
            if on_origin_chapter:
                context_panel.cancel_extraction()
            if on_origin_chapter:
                self._current_context_entries = []
            self._set_status_message("无前文可提取")
        else:
            if on_origin_chapter:
                context_panel.fail_extraction(result.error)
            self._set_status_message(f"上下文提取失败: {result.error}")

        # 清理缓冲与发起章节标记（所有分支均清理）
        if chapter_id:
            self._extract_stream_text_by_chapter.pop(chapter_id, None)
        self._extracting_chapter_id = None

    def _on_view_continuation_prompt(self) -> None:
        """展示续写组装后的完整提示词。"""
        if not self._current_chapter:
            QMessageBox.warning(self, "提示", "请先选择章节")
            return

        # 确保章节正文已加载（list_chapters 只加载元数据）
        self._ensure_chapter_contents()

        # 加载预设
        preset_id = self.continuation_panel.get_selected_preset_id()
        preset = self.preset_service.load_preset(preset_id)
        if preset is None:
            preset = self.preset_service.load_default_preset()

        # 从端点获取 model（与 _on_start_continuation 一致：面板下拉优先，流程配置回退）
        endpoint = self.continuation_panel.get_selected_endpoint()
        mode = self.continuation_panel.get_mode()
        flow_key = "volume_continuation" if mode == "volume" else "single_continuation"
        model = ""
        if endpoint:
            # 面板模型下拉当前文本优先
            sel_model = self.continuation_panel.get_selected_model()
            model = sel_model or self.config_manager.get_flow_model(flow_key)

        # 获取生成参数
        gen_params = preset.generation_params
        params = self.continuation_panel.get_parameters()
        max_context = params.get("max_context") or gen_params.get("max_context", 9999999)
        max_tokens = gen_params.get("max_tokens", 2000)
        target_words = params.get("target_words", 2000)

        # 获取小说档案
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)
        novel_profile = project.novel_profile if project else {}

        # 获取上下文条目（已提取的或空列表），合并全局世界书
        raw_entries = getattr(self, "_current_context_entries", None) or []
        entries = self._merge_worldbook_entries(raw_entries)

        # 获取章节元数据
        chapter_metadata = (
            dict(self._current_chapter.metadata) if self._current_chapter else {}
        )

        # 组装提示词（纯本地，不调用 LLM）
        try:
            user_input = self.continuation_panel.get_user_input()
            lookback_chapters = params.get("lookback_chapters", 0)
            assemble_result = self.prompt_assembler.assemble(
                preset=preset,
                chapters=self._current_chapters,
                current_chapter=self._current_chapter,
                context_entries=entries,
                model=model,
                max_context=max_context,
                max_tokens=max_tokens,
                target_words=target_words,
                novel_profile=novel_profile,
                project_id=self._current_project_id or "",
                chapter_metadata=chapter_metadata,
                user_input=user_input,
                lookback_chapters=lookback_chapters,
                world_ontology=project.world_ontology if project else None,
                protagonist_profile=self._protagonist_profile_by_chapter.get(
                    self._current_chapter.id if self._current_chapter else ""
                ),
                custom_audit_rules=project.custom_audit_rules if project else None,
                style_profile=project.style_profile if project else None,
            )
        except Exception as e:
            logger.error("提示词组装失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "组装失败", f"提示词组装失败: {e}")
            return

        self._show_prompt_dialog(
            "续写提示词预览",
            assemble_result.messages,
            assemble_result.token_usage,
        )

    def _on_view_extract_prompt(self) -> None:
        """展示提取提示词预览。"""
        if not self._current_chapter:
            QMessageBox.warning(self, "提示", "请先选择章节")
            return

        # 确保章节正文已加载（list_chapters 只加载元数据）
        self._ensure_chapter_contents()

        config = self.continuation_panel.context_preview_panel.get_lookback_config()
        lookback_override = config.get("lookback")

        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)

        try:
            prompt = self.context_extractor.build_prompt_for_preview(
                project=project,
                chapters=self._current_chapters,
                current_chapter=self._current_chapter,
                lookback_override=lookback_override,
            )
        except Exception as e:
            QMessageBox.critical(self, "错误", f"构建提取提示词失败: {e}")
            return

        self._show_prompt_dialog(
            "提取提示词预览",
            [{"role": "user", "content": prompt}],
        )

    def _on_extract_ontology_requested(self) -> None:
        """提取世界观底层（非阻塞，流式进度）。

        用户在上下文预览面板点击"提取世界观底层"按钮时触发。
        全文拆分分析提取 7 维度 WorldOntology，固化到 Project.world_ontology。
        """
        if not self._current_chapter:
            QMessageBox.warning(self, "提示", "请先选择章节")
            return

        # 确保章节正文已加载（list_chapters 只加载元数据）
        self._ensure_chapter_contents()

        endpoint = self.continuation_panel.get_selected_endpoint()
        if not endpoint:
            QMessageBox.warning(self, "提示", "请先配置 API 端点")
            self._on_open_settings()
            return

        if not self._current_chapters:
            QMessageBox.warning(self, "提示", "无章节可提取世界观")
            return

        # 加载项目对象
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)
        if project is None:
            QMessageBox.warning(self, "提示", "项目加载失败")
            return

        # 读取 token_limit 与 lookback 配置（与上下文提取一致）
        config = self.continuation_panel.context_preview_panel.get_lookback_config()
        token_limit_override = config.get("token_limit", 0)
        lookback_override = config.get("lookback", 0)

        # 禁用按钮防止重复点击 + 启动流式进度展示
        context_panel = self.continuation_panel.context_preview_panel
        # 标记世界观提取进行中（项目级，章节切换后切回任意章节均恢复 UI）
        self._ontology_extracting = True
        self._ontology_stream_text = ""
        context_panel.start_ontology_extraction()
        self._set_status_message("正在提取世界观底层（流式）...")

        # 非阻塞提交：用 run_coroutine_threadsafe + 回调
        from novelforge.services.async_runner import AsyncLoopRunner

        runner = AsyncLoopRunner.instance()
        loop = runner._loop

        def on_chunk(text: str) -> None:
            self._ontology_chunk_received.emit(text)

        def on_batch_complete(batch_idx: int, total_batches: int) -> None:
            self._ontology_batch_done.emit(batch_idx, total_batches)

        future = asyncio.run_coroutine_threadsafe(
            self.ontology_extractor.extract_ontology_streaming(
                project=project,
                chapters=self._current_chapters,
                token_limit=token_limit_override,
                on_chunk=on_chunk,
                on_batch_complete=on_batch_complete,
                jailbreak_text=self._get_flow_jailbreak_text("ontology_extraction"),
                current_chapter=self._current_chapter,
                lookback=lookback_override,
            ),
            loop,
        )

        def on_done(fut) -> None:
            try:
                ontology, status = fut.result()
                self._ontology_done.emit(ontology, status)
            except Exception as e:
                logger.error("世界观提取异常: %s", e, exc_info=True)
                self._ontology_done.emit(None, f"failed: {e}")

        future.add_done_callback(on_done)

    @Slot(str)
    def _on_ontology_chunk_received(self, text: str) -> None:
        """世界观提取 chunk 回调：缓冲，面板处于提取态时更新 UI。

        世界观为项目级，章节切换后面板 _is_extracting 被重置；切回任意章节时
        由 _load_context_entries_for_chapter 调 restore_extraction_state 恢复，
        此处仅当面板仍处于提取态时更新 UI，避免污染新章节视图。
        """
        self._ontology_stream_text += text
        context_panel = self.continuation_panel.context_preview_panel
        if context_panel._is_extracting:
            context_panel.update_ontology_progress(text)

    @Slot(int, int)
    def _on_ontology_batch_done(self, batch_idx: int, total_batches: int) -> None:
        """世界观提取批次完成回调（UI 线程执行，由 Signal 触发）。"""
        context_panel = self.continuation_panel.context_preview_panel
        context_panel.update_ontology_batch(batch_idx, total_batches)
        self._set_status_message(
            f"世界观提取进度: 第 {batch_idx}/{total_batches} 批次完成"
        )

    @Slot(object, str)
    def _on_ontology_done(self, ontology, status: str) -> None:
        """世界观提取完成回调：清理状态标记，面板处于提取态时更新 UI。"""
        context_panel = self.continuation_panel.context_preview_panel
        # 面板处于提取态（用户未切走，或切回发起章节已恢复）时更新 UI
        panel_active = context_panel._is_extracting

        if ontology is None:
            if panel_active:
                context_panel.fail_ontology_extraction(status)
            self._set_status_message(f"世界观提取失败: {status}")
            QMessageBox.critical(self, "提取失败", f"世界观提取失败: {status}")
        else:
            # 协程内已通过 await 异步存储直连保存（避免重入死锁）
            if panel_active:
                context_panel.finish_ontology_extraction(status)
            if "保存失败" in status:
                self._set_status_message(f"世界观提取完成但保存失败: {status}")
                QMessageBox.warning(self, "保存警告", f"世界观已提取但保存失败: {status}")
            else:
                self._set_status_message("世界观底层提取完成，已固化到项目")

        # 清理状态标记与缓冲（所有分支均清理）
        self._ontology_extracting = False
        self._ontology_stream_text = ""

    def _on_view_ontology_requested(self) -> None:
        """查看已提取的世界观底层。"""
        if not self._current_project_id:
            QMessageBox.warning(self, "提示", "请先选择项目")
            return
        try:
            project = self.storage_service.load_project(self._current_project_id)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载项目失败: {e}")
            return
        if project is None or project.world_ontology is None:
            QMessageBox.information(
                self, "提示", "尚未提取世界观底层，请先点击「提取世界观底层」按钮"
            )
            return
        # 格式化展示：兼容 dict 与 WorldOntology 实例
        wo = project.world_ontology
        if isinstance(wo, dict):
            try:
                from novelforge.models.ontology import WorldOntology
                wo = WorldOntology.model_validate(wo)
            except Exception:
                pass
        text = self._format_ontology_for_display(wo)
        dialog = QDialog(self)
        dialog.setWindowTitle("世界观底层")
        dialog.resize(800, 600)
        layout = QVBoxLayout(dialog)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        layout.addWidget(edit)
        btn = QPushButton("关闭")
        btn.clicked.connect(dialog.accept)
        layout.addWidget(btn)
        dialog.exec()

    def _on_extract_style_requested(self) -> None:
        """提取文风档案（非阻塞，流式进度）。

        用户在上下文预览面板点击"提取文风档案"按钮时触发。
        全文拆分分析提取 9 维度 StyleProfile，固化到 Project.style_profile。
        """
        if not self._current_chapter:
            QMessageBox.warning(self, "提示", "请先选择章节")
            return

        # 确保章节正文已加载（list_chapters 只加载元数据）
        self._ensure_chapter_contents()

        endpoint = self.continuation_panel.get_selected_endpoint()
        if not endpoint:
            QMessageBox.warning(self, "提示", "请先配置 API 端点")
            self._on_open_settings()
            return

        if not self._current_chapters:
            QMessageBox.warning(self, "提示", "无章节可提取文风档案")
            return

        # 加载项目对象
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)
        if project is None:
            QMessageBox.warning(self, "提示", "项目加载失败")
            return

        # 读取 token_limit 与 lookback 配置（与上下文提取一致）
        config = self.continuation_panel.context_preview_panel.get_lookback_config()
        token_limit_override = config.get("token_limit", 0)
        lookback_override = config.get("lookback", 0)

        # 禁用按钮防止重复点击 + 启动流式进度展示
        context_panel = self.continuation_panel.context_preview_panel
        # 标记文风档案提取进行中（项目级，章节切换后切回任意章节均恢复 UI）
        self._style_extracting = True
        self._style_stream_text = ""
        context_panel.start_style_extraction()
        self._set_status_message("正在提取文风档案（流式）...")

        # 非阻塞提交：用 run_coroutine_threadsafe + 回调
        from novelforge.services.async_runner import AsyncLoopRunner

        runner = AsyncLoopRunner.instance()
        loop = runner._loop

        def on_chunk(text: str) -> None:
            self._style_chunk_received.emit(text)

        def on_batch_complete(batch_idx: int, total_batches: int) -> None:
            self._style_batch_done.emit(batch_idx, total_batches)

        future = asyncio.run_coroutine_threadsafe(
            self.style_extractor.extract_style_streaming(
                project=project,
                chapters=self._current_chapters,
                token_limit=token_limit_override,
                on_chunk=on_chunk,
                on_batch_complete=on_batch_complete,
                jailbreak_text=self._get_flow_jailbreak_text("style_extraction"),
                current_chapter=self._current_chapter,
                lookback=lookback_override,
            ),
            loop,
        )

        def on_done(fut) -> None:
            try:
                style_profile, status = fut.result()
                self._style_done.emit(style_profile, status)
            except Exception as e:
                logger.error("文风档案提取异常: %s", e, exc_info=True)
                self._style_done.emit(None, f"failed: {e}")

        future.add_done_callback(on_done)

    @Slot(str)
    def _on_style_chunk_received(self, text: str) -> None:
        """文风档案提取 chunk 回调：缓冲，面板处于提取态时更新 UI。

        文风档案为项目级，章节切换后面板 _is_extracting 被重置；切回任意章节时
        由 _load_context_entries_for_chapter 调 restore_extraction_state 恢复，
        此处仅当面板仍处于提取态时更新 UI，避免污染新章节视图。
        """
        self._style_stream_text += text
        context_panel = self.continuation_panel.context_preview_panel
        if context_panel._is_extracting:
            context_panel.update_style_progress(text)

    @Slot(int, int)
    def _on_style_batch_done(self, batch_idx: int, total_batches: int) -> None:
        """文风档案提取批次完成回调（UI 线程执行，由 Signal 触发）。"""
        context_panel = self.continuation_panel.context_preview_panel
        context_panel.update_style_batch(batch_idx, total_batches)
        self._set_status_message(
            f"文风档案提取进度: 第 {batch_idx}/{total_batches} 批次完成"
        )

    @Slot(object, str)
    def _on_style_done(self, style_profile, status: str) -> None:
        """文风档案提取完成回调：清理状态标记，面板处于提取态时更新 UI。"""
        context_panel = self.continuation_panel.context_preview_panel
        # 面板处于提取态（用户未切走，或切回发起章节已恢复）时更新 UI
        panel_active = context_panel._is_extracting

        if style_profile is None:
            if panel_active:
                context_panel.fail_style_extraction(status)
            self._set_status_message(f"文风档案提取失败: {status}")
            QMessageBox.critical(self, "提取失败", f"文风档案提取失败: {status}")
        else:
            # 协程内已通过 await 异步存储直连保存（避免重入死锁）
            if panel_active:
                context_panel.finish_style_extraction(status)
            if "保存失败" in status:
                self._set_status_message(f"文风档案提取完成但保存失败: {status}")
                QMessageBox.warning(self, "保存警告", f"文风档案已提取但保存失败: {status}")
            else:
                self._set_status_message("文风档案提取完成，已固化到项目")

        # 清理状态标记与缓冲（所有分支均清理）
        self._style_extracting = False
        self._style_stream_text = ""

    def _on_view_style_requested(self) -> None:
        """查看已提取的文风档案。"""
        if not self._current_project_id:
            QMessageBox.warning(self, "提示", "请先选择项目")
            return
        try:
            project = self.storage_service.load_project(self._current_project_id)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载项目失败: {e}")
            return
        if project is None or project.style_profile is None:
            QMessageBox.information(
                self, "提示", "尚未提取文风档案，请先点击「提取文风档案」按钮"
            )
            return
        # 格式化展示：兼容 dict 与 StyleProfile 实例
        sp = project.style_profile
        if isinstance(sp, dict):
            try:
                sp = StyleProfile.model_validate(sp)
            except Exception:
                pass
        text = self._format_style_profile(sp)
        dialog = QDialog(self)
        dialog.setWindowTitle("文风档案")
        dialog.resize(800, 600)
        layout = QVBoxLayout(dialog)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        layout.addWidget(edit)
        btn = QPushButton("关闭")
        btn.clicked.connect(dialog.accept)
        layout.addWidget(btn)
        dialog.exec()

    def _on_extract_protagonist_requested(self) -> None:
        """提取主角形象（非阻塞，流式进度）。

        用户在上下文预览面板点击"提取主角形象"按钮时触发。
        全文拆分分析提取 8 维度 ProtagonistProfile，缓存到当前章节（独立缓存 key，
        不固化到 Project）。
        """
        if not self._current_chapter:
            QMessageBox.warning(self, "提示", "请先选择章节")
            return

        # 确保章节正文已加载（list_chapters 只加载元数据）
        self._ensure_chapter_contents()

        endpoint = self.continuation_panel.get_selected_endpoint()
        if not endpoint:
            QMessageBox.warning(self, "提示", "请先配置 API 端点")
            self._on_open_settings()
            return

        if not self._current_chapters:
            QMessageBox.warning(self, "提示", "无章节可提取主角形象")
            return

        # 加载项目对象
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)
        if project is None:
            QMessageBox.warning(self, "提示", "项目加载失败")
            return

        # 读取 token_limit 与 lookback 配置（与上下文提取一致）
        config = self.continuation_panel.context_preview_panel.get_lookback_config()
        token_limit_override = config.get("token_limit", 0)
        lookback = config.get("lookback", 0)

        # 禁用按钮防止重复点击 + 启动流式进度展示
        context_panel = self.continuation_panel.context_preview_panel
        # 标记主角形象提取进行中（章节级，切回发起章节恢复 UI）
        self._protagonist_extracting = True
        self._protagonist_stream_text = ""
        self._extracting_chapter_id = self._current_chapter.id
        context_panel.start_protagonist_extraction()
        self._set_status_message("正在提取主角形象（流式）...")

        # 非阻塞提交：用 run_coroutine_threadsafe + 回调
        from novelforge.services.async_runner import AsyncLoopRunner

        runner = AsyncLoopRunner.instance()
        loop = runner._loop

        def on_chunk(text: str) -> None:
            self._protagonist_chunk_received.emit(text)

        def on_batch_complete(batch_idx: int, total_batches: int) -> None:
            self._protagonist_batch_done.emit(batch_idx, total_batches)

        future = asyncio.run_coroutine_threadsafe(
            self.context_extractor.extract_protagonist_streaming(
                project=project,
                chapters=self._current_chapters,
                current_chapter=self._current_chapter,
                token_limit=token_limit_override,
                lookback=lookback,
                on_chunk=on_chunk,
                on_batch_complete=on_batch_complete,
                jailbreak_text=self._get_flow_jailbreak_text("protagonist_extraction"),
            ),
            loop,
        )

        def on_done(fut) -> None:
            try:
                profile, status = fut.result()
                self._protagonist_done.emit(profile, status)
            except Exception as e:
                logger.error("主角形象提取异常: %s", e, exc_info=True)
                self._protagonist_done.emit(None, f"failed: {e}")

        future.add_done_callback(on_done)

    @Slot(str)
    def _on_protagonist_chunk_received(self, text: str) -> None:
        """主角形象提取 chunk 回调：缓冲，面板处于提取态时更新 UI。

        主角形象为章节级，章节切换后面板 _is_extracting 被重置；切回发起章节时
        由 _load_context_entries_for_chapter 调 restore_extraction_state 恢复，
        此处仅当面板仍处于提取态时更新 UI，避免污染新章节视图。
        """
        self._protagonist_stream_text += text
        context_panel = self.continuation_panel.context_preview_panel
        if context_panel._is_extracting:
            context_panel.update_protagonist_progress(text)

    @Slot(int, int)
    def _on_protagonist_batch_done(self, batch_idx: int, total_batches: int) -> None:
        """主角形象提取批次完成回调（UI 线程执行，由 Signal 触发）。"""
        context_panel = self.continuation_panel.context_preview_panel
        context_panel.update_protagonist_batch(batch_idx, total_batches)
        self._set_status_message(
            f"主角形象提取进度: 第 {batch_idx}/{total_batches} 批次完成"
        )

    @Slot(object, str)
    def _on_protagonist_done(self, profile, status: str) -> None:
        """主角形象提取完成回调：清理状态标记，更新内存 LRU，面板处于提取态时更新 UI。"""
        context_panel = self.continuation_panel.context_preview_panel
        # 面板处于提取态（用户未切走，或切回发起章节已恢复）时更新 UI
        panel_active = context_panel._is_extracting
        chapter_id = self._extracting_chapter_id

        if profile is None:
            if panel_active:
                context_panel.fail_protagonist_extraction(status)
            self._set_status_message(f"主角形象提取失败: {status}")
            QMessageBox.critical(self, "提取失败", f"主角形象提取失败: {status}")
        else:
            # 落盘到 chapters 表 protagonist_profile 列 + 内存 LRU 热缓存
            if chapter_id:
                self._protagonist_profile_by_chapter[chapter_id] = profile
                self._protagonist_profile_by_chapter.move_to_end(chapter_id)
                if len(self._protagonist_profile_by_chapter) > MAX_CONTEXT_CACHE_SIZE:
                    self._protagonist_profile_by_chapter.popitem(last=False)
                try:
                    self.storage_service.update_chapter_protagonist(chapter_id, profile)
                except Exception as e:
                    logger.warning("主角形象持久化失败 chapter=%s: %s", chapter_id, e)
            if panel_active:
                context_panel.finish_protagonist_extraction(status)
            self._set_status_message("主角形象提取完成，已保存到当前章节并持久化")

        # 清理状态标记与缓冲（所有分支均清理）
        self._protagonist_extracting = False
        self._protagonist_stream_text = ""
        self._extracting_chapter_id = None

    def _on_view_protagonist_requested(self) -> None:
        """查看当前章节已提取的主角形象档案。"""
        if not self._current_chapter:
            QMessageBox.information(self, "提示", "请先选择章节")
            return
        chapter_id = self._current_chapter.id
        profile = self._protagonist_profile_by_chapter.get(chapter_id)
        if profile is None:
            QMessageBox.information(
                self, "提示",
                "当前章节尚未提取主角形象，请先点击「提取主角形象」按钮"
            )
            return
        text = self._format_protagonist_for_display(profile)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"主角形象档案 - {self._current_chapter.title}")
        dialog.resize(800, 600)
        layout = QVBoxLayout(dialog)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        layout.addWidget(edit)
        btn = QPushButton("关闭")
        btn.clicked.connect(dialog.accept)
        layout.addWidget(btn)
        dialog.exec()

    @staticmethod
    def _format_ontology_for_display(wo) -> str:
        """格式化 WorldOntology 为可读文本（按 7 维度分节）。"""
        import json as _json
        labels = {
            "existential_topology": "存在拓扑",
            "causal_architecture": "因果架构",
            "spatio_temporal_ontology": "时空本体论",
            "information_epistemology": "信息与认识论",
            "axiological_foundation": "价值论基础",
            "becoming_dynamics": "生成动力学",
            "narrative_ontology": "叙事本体论",
        }
        lines = []
        # 元信息
        extracted_at = getattr(wo, "extracted_at", None) or (
            wo.get("extracted_at") if isinstance(wo, dict) else None
        )
        if extracted_at:
            lines.append(f"提取时间: {extracted_at}")
        src_range = getattr(wo, "source_chapter_range", None) or (
            wo.get("source_chapter_range") if isinstance(wo, dict) else None
        )
        if src_range:
            r = src_range
            lines.append(f"来源章节范围: 第{r[0]}章 - 第{r[1]}章")
        if lines:
            lines.append("")
        # 7 维度
        for dim, label in labels.items():
            value = getattr(wo, dim, None) if hasattr(wo, dim) else (
                wo.get(dim) if isinstance(wo, dict) else None
            )
            lines.append(f"【{label}】({dim})")
            if value:
                lines.append(_json.dumps(value, ensure_ascii=False, indent=2))
            else:
                lines.append("（空）")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _format_protagonist_for_display(profile) -> str:
        """格式化 ProtagonistProfile 为可读文本（按 8 维度分节展示）。"""
        labels = {
            "basic_anchors": "角色基础锚点",
            "personality_system": "人格操作系统",
            "motivation_system": "动力与动机系统",
            "emotion_defense": "情感与防御机制",
            "behavior_fingerprint": "行为指纹与身体语言",
            "relationship_coordinate": "关系坐标系",
            "growth_arc": "变化轨迹与弧光",
            "ooc_redlines": "OOC红线与强制约束",
        }
        # 兼容 dict 与 ProtagonistProfile 实例
        if hasattr(profile, "model_dump"):
            data = profile.model_dump()
        elif isinstance(profile, dict):
            data = profile
        else:
            return f"（不支持的主角形象数据类型：{type(profile).__name__}）"
        lines = ["# 主角形象心理学档案（8 维度）", ""]
        for dim, label in labels.items():
            value = data.get(dim, {})
            lines.append(f"【{label}】({dim})")
            if isinstance(value, dict) and value:
                lines.append(json.dumps(value, ensure_ascii=False, indent=2))
            else:
                lines.append("（空）")
            lines.append("")
        return "\n".join(lines)

    def _show_prompt_dialog(
        self, title: str, messages: list[dict], token_usage: dict | None = None
    ) -> None:
        """展示提示词预览对话框。"""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(700, 600)
        layout = QVBoxLayout(dialog)

        # token 信息
        if token_usage:
            total = token_usage.get("total_used", 0)
            max_ctx = token_usage.get("max_context", "?")
            info = QLabel(
                f"Token: {total}/{max_ctx} | "
                f"系统={token_usage.get('system_tokens', 0)} | "
                f"注入={token_usage.get('injection_tokens', 0)} | "
                f"历史预算={token_usage.get('history_budget', 0)}"
            )
            info.setObjectName("metaText")
            info.setWordWrap(True)
            layout.addWidget(info)

        # 消息数量
        count_label = QLabel(f"共 {len(messages)} 条消息")
        count_label.setObjectName("textSecondary")
        layout.addWidget(count_label)

        # messages 展示
        text_edit = QPlainTextEdit()
        text_edit.setReadOnly(True)
        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            content = msg.get("content", "")
            text_edit.appendPlainText(f"--- [{i}] role: {role} ---")
            text_edit.appendPlainText(content)
            text_edit.appendPlainText("")
        layout.addWidget(text_edit)

        # 复制按钮
        btn_layout = QHBoxLayout()
        copy_btn = QPushButton("复制全部")
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(text_edit.toPlainText())
        )
        btn_layout.addWidget(copy_btn)
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dialog.exec()

    def _on_open_worldbook_manager(self) -> None:
        """打开世界书管理器（非模态独立窗口）。"""
        try:
            manager = WorldBookManager(self.worldbook_service, self)
            manager.worldbook_changed.connect(self._on_worldbook_changed)
            manager.setWindowModality(Qt.WindowModality.NonModal)
            manager.show()
            # 保持引用，避免被 GC
            self._worldbook_manager_windows.append(manager)
            # 清理已关闭的窗口
            self._worldbook_manager_windows = [
                w for w in self._worldbook_manager_windows if w.isVisible()
            ]
        except Exception as e:
            logger.error("打开世界书管理器失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "错误", f"打开世界书管理器失败: {e}")

    def _on_worldbook_changed(self) -> None:
        """世界书管理器中的世界书集合或状态变化。"""
        self._refresh_worldbooks()

    def _on_open_flow_plugin_manager(self) -> None:
        """打开流程插件管理器（非模态独立窗口）。"""
        try:
            manager = FlowPluginManager(self.flow_plugin_service, self)
            manager.plugin_changed.connect(self._refresh_flow_plugins)
            manager.setWindowModality(Qt.WindowModality.NonModal)
            manager.show()
            # 保持引用，避免被 GC
            self._flow_plugin_manager_windows.append(manager)
            # 清理已关闭的窗口
            self._flow_plugin_manager_windows = [
                w for w in self._flow_plugin_manager_windows if w.isVisible()
            ]
        except Exception as e:
            logger.error("打开流程插件管理器失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "错误", f"打开流程插件管理器失败: {e}")

    def _on_stop_continuation(self) -> None:
        """停止流式输出（单次续写与卷续写流程均处理）。"""
        if self._continuation_worker and self._continuation_worker.isRunning():
            self._continuation_worker.stop()
            self._set_status_message("正在停止续写...")
        if (
            self._volume_orchestrator is not None
            and self._volume_orchestrator.isRunning()
        ):
            self._volume_orchestrator.stop()
            self._set_status_message("正在停止卷续写流程...")
            # 清理 volume_phase 多阶段状态
            stop_cid = self._volume_chapter_id
            self._volume_state = None
            self._volume_current_phase = None
            self._volume_resuming = False
            self._volume_chapter_steps_seen = set()
            self._volume_orchestrator = None
            self._volume_chapter_id = None
            if self.flow_executor.is_active:
                self.flow_executor.cancel()
            # 删除暂存状态文件（用户主动停止视为放弃）
            if stop_cid:
                self._delete_volume_state(stop_cid)

    def _on_continuation_chunk_received(self, chunk: str) -> None:
        """续写 chunk 路由：按章节缓冲，仅当前章节匹配时更新 UI。

        后台 worker 不因章节切换停止；chunk 总是缓冲到发起章节，
        UI 仅在用户停留在发起章节时更新，避免污染新章节输出框。
        """
        cid = self._continuation_chapter_id
        if cid is None:
            return
        self._continuation_stream_text_by_chapter[cid] = (
            self._continuation_stream_text_by_chapter.get(cid, "") + chunk
        )
        if self._current_chapter and self._current_chapter.id == cid:
            self.continuation_panel.append_chunk(chunk)

    def _on_continuation_finished(self, continuation: Continuation) -> None:
        """续写完成（单次续写流程）。

        使用发起时记录的 _continuation_chapter_id 归档 swipe，避免用户切换
        章节后存入错误章节；仅当用户仍停留在发起章节时刷新 UI。
        """
        cid = self._continuation_chapter_id or (
            self._current_chapter.id if self._current_chapter else None
        )
        on_origin_chapter = bool(
            self._current_chapter and self._current_chapter.id == cid
        )

        if on_origin_chapter:
            self.continuation_panel.stop_streaming()
            self.chapter_editor.set_streaming_locked(False)

        # 保存 swipe 到发起章节（无论用户是否切走）
        if cid:
            self.chapter_service.storage.save_continuation(continuation, cid)
            if on_origin_chapter:
                # 更新当前章节的 continuations（供面板显示），不刷新章节列表
                chapter = self.storage_service.load_chapter(cid)
                if chapter:
                    self._current_chapter = chapter
                # 显示 swipe
                all_swipes = (
                    self._current_chapter.continuations if self._current_chapter else []
                )
                self.continuation_panel.set_current_swipe(continuation, all_swipes)

        # 清理缓冲与发起章节标记
        if cid:
            self._continuation_stream_text_by_chapter.pop(cid, None)
        self._continuation_chapter_id = None

        # M5: 记录历史日志
        self._record_history(
            swipe_id=continuation.id,
            status=continuation.status,
            output_text=continuation.content,
            error_message="",
        )

        self._set_status_message(
            f"续写完成: {len(continuation.content)} 字, 状态: {continuation.status}"
        )

    def _on_continuation_error(self, error: str) -> None:
        """续写出错：仅当用户停留在发起章节时刷新 UI，清理状态标记。"""
        cid = self._continuation_chapter_id
        on_origin_chapter = bool(
            self._current_chapter and self._current_chapter.id == cid
        )

        if on_origin_chapter:
            self.continuation_panel.stop_streaming()
            self.chapter_editor.set_streaming_locked(False)
            self.continuation_panel.show_error(error)

        # 清理缓冲与发起章节标记
        if cid:
            self._continuation_stream_text_by_chapter.pop(cid, None)
        self._continuation_chapter_id = None

        # M5: 记录历史日志（失败）
        self._record_history(
            swipe_id="",
            status="failed",
            output_text="",
            error_message=error,
        )

        self._set_status_message(f"续写错误: {error}")

        # 清理 volume 阶段状态（volume_phase 流程出错时）：弹重试对话框
        if self._volume_orchestrator is not None:
            # 保留状态用于重试（不清理 _volume_state/_volume_current_phase/_volume_chapter_id）
            retry_phase = self._volume_current_phase
            retry_state = self._volume_state
            retry_cid = self._volume_chapter_id
            # 清理 orchestrator 引用（避免 stop 后信号残留）
            self._volume_orchestrator = None
            if self.flow_executor.is_active:
                self.flow_executor.cancel()
            # 弹重试/取消对话框
            reply = QMessageBox.question(
                self, "卷续写出错",
                f"阶段 [{retry_phase}] 失败：{error}\n\n是否重试该阶段？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes and retry_state is not None and retry_phase:
                # 重试：复用已持久化的状态重启该阶段
                self._volume_state = retry_state
                self._volume_chapter_id = retry_cid
                # _volume_current_phase 已保留
                self._start_volume_phase(retry_phase, retry_state, None)
            else:
                # 取消：清理全部 + 删除状态文件
                self._volume_state = None
                self._volume_current_phase = None
                self._volume_resuming = False
                self._volume_chapter_steps_seen = set()
                self._volume_chapter_id = None
                if retry_cid:
                    self._delete_volume_state(retry_cid)

    def _record_history(
        self,
        swipe_id: str,
        status: str,
        output_text: str,
        error_message: str,
    ) -> None:
        """记录续写历史日志（不阻塞续写流程）。

        Args:
            swipe_id: 续写版本 ID（失败时可为空）
            status: 状态（completed/interrupted/failed）
            output_text: 输出文本
            error_message: 错误信息
        """
        try:
            self.history_service.log_continuation(
                project_id=self._current_project_id or "",
                chapter_id=self._current_chapter.id if self._current_chapter else "",
                swipe_id=swipe_id,
                started_at=self._continuation_started_at,
                finished_at=self.history_service.now_iso(),
                status=status,
                model=self._continuation_model,
                parameters=self._continuation_parameters,
                prompt_messages=self._continuation_prompt_messages,
                output_text=output_text,
                error_message=error_message,
            )
        except Exception as e:
            logger.warning("记录历史日志失败: %s", e)

    def _on_auth_error(self) -> None:
        """认证失败。"""
        self.continuation_panel.stop_streaming()
        self.chapter_editor.set_streaming_locked(False)
        QMessageBox.warning(self, "认证失败", "API Key 无效，请检查设置")
        self._on_open_settings()

    def _on_rewrite(self, params: dict) -> None:
        """重写：复用缓存审计结果直接生成新 swipe，无缓存则走完整流程。

        - rewrite_current 模式：若当前 swipe 缓存了 _rewrite_analysis_text，
          直接调 _on_rewrite_analysis_accepted 跳过分析步骤生成新 swipe；
          否则走 _on_start_flow 重新分析
        - writing_mode 模式：若当前 swipe 缓存了 _writing_mode_refinement，
          直接调 _on_start_writing_mode_continuation 跳过阶段 1/2 生成新 swipe；
          否则走 _on_start_flow 重新分析
        - 其他模式：走 _on_start_continuation_routed（原逻辑）
        """
        # 沿用上次参数（含缓存的审计结果）
        cached_analysis = None
        cached_refinement = None
        if self.continuation_panel.current_swipe:
            last_params = dict(self.continuation_panel.current_swipe.parameters_snapshot)
            cached_analysis = last_params.pop("_rewrite_analysis_text", None)
            cached_refinement = last_params.pop("_writing_mode_refinement", None)
            # 移除缓存键后设置参数（避免缓存键污染面板参数）
            self.continuation_panel.set_parameters(last_params)
            params = self.continuation_panel.get_parameters()
            # 补回面板当前选中的模型（get_parameters 不含 model，需显式补充，
            # 与 _on_start_clicked / _on_rewrite_clicked 模式一致）
            params["model"] = self.continuation_panel.get_selected_model()

        mode = self.continuation_panel.get_mode()

        if mode == "rewrite_current" and cached_analysis:
            # 已有审计结果，跳过分析步骤直接生成
            self._rewrite_current_chapter_id = self._current_chapter.id
            self._rewrite_current_params = params
            self._rewrite_current_endpoint = self.continuation_panel.get_selected_endpoint()
            self._on_rewrite_analysis_accepted(cached_analysis)
        elif mode == "writing_mode" and cached_refinement:
            # 已有阶段 2 精炼输出，跳过阶段 1/2 直接生成
            self._on_start_writing_mode_continuation(params, cached_refinement)
        else:
            # 无缓存审计结果，走完整流程（重新分析）
            params["created_by"] = "rewrite"
            self._on_start_continuation_routed(params)

    def _on_accept_continuation(self) -> None:
        """接受续写：根据插件 accept_mode 分支处理。

        优先从 ``parameters_snapshot["_flow_plugin_id"]`` 查插件的 accept_mode，
        回退按 ``created_by`` 推断（兼容旧 swipe）。

        - ``replace``：替换当前章节正文（重写模式）
        - ``promote``：提升为独立章节插入当前章之后（默认）
        - ``volume_insert``：卷续写内部自建章节，不走此入口（直接 return）
        """
        swipe = self.continuation_panel.current_swipe
        if not swipe or not self._current_chapter:
            return

        # 解析 accept_mode：优先查插件，回退 created_by
        plugin_id = swipe.parameters_snapshot.get("_flow_plugin_id", "")
        accept_mode = ""
        if plugin_id:
            plugin = self.flow_plugin_service.load_plugin(plugin_id)
            if plugin:
                accept_mode = plugin.accept_mode
        if not accept_mode:
            # 回退：旧 swipe 无 _flow_plugin_id，按 created_by 推断
            if getattr(swipe, "created_by", "") == "rewrite_current":
                accept_mode = "replace"
            else:
                accept_mode = "promote"

        if accept_mode == "volume_insert":
            # 卷续写内部自建章节，不走 accept 流程
            return

        # 替换当前章节正文（accept_mode == "replace"）
        if accept_mode == "replace":
            reply = QMessageBox.question(
                self,
                "接受重写",
                "确定用重写内容替换当前章节正文？\n原正文将被覆盖，不可撤销。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                # 重新加载完整章节
                chapter = self.storage_service.load_chapter(self._current_chapter.id)
                if chapter is None:
                    return
                # 找到当前续写并更新引用
                for c in chapter.continuations:
                    if c.id == swipe.id:
                        swipe = c
                        break
                # 替换当前章节正文
                updated_chapter = self.chapter_service.replace_chapter_content(
                    chapter, swipe
                )
                self._current_chapter = updated_chapter
                # 刷新章节列表（word_count 变化）与编辑器内容
                self._refresh_chapter_list()
                # 重新加载章节到编辑器（替换后的正文）
                self._on_chapter_selected(updated_chapter.id)
                self.continuation_panel.clear_output()
                self._set_status_message(
                    f"已替换当前章节正文: {len(updated_chapter.content)} 字"
                )
            except Exception as e:
                logger.error("重写接受失败: %s", e, exc_info=True)
                QMessageBox.critical(self, "接受失败", str(e))
            return

        # 默认路径：提升为独立章节插入到当前章节之后
        reply = QMessageBox.question(
            self,
            "接受续写",
            "确定接受当前续写？\n续写将作为新章节插入到当前章节之后。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            # 重新加载完整章节
            chapter = self.storage_service.load_chapter(self._current_chapter.id)
            if chapter is None:
                return

            # 找到当前续写并更新
            for c in chapter.continuations:
                if c.id == swipe.id:
                    swipe = c
                    break

            # 提升为新章节（index 后移 + 建章 + 删续写记录）
            updated_chapter, new_chapter = (
                self.chapter_service.promote_continuation_to_chapter(chapter, swipe)
            )
            self._current_chapter = updated_chapter
            # 刷新章节列表（新增章节）
            self._refresh_chapter_list()
            # 自动选中新章节，加载到编辑器
            self._on_chapter_selected(new_chapter.id)
            self._set_status_message(f"已接受续写，已插入新章节: {new_chapter.title}")
        except Exception as e:
            logger.error("接受续写失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "接受失败", str(e))

    def _on_delete_continuation(self) -> None:
        """删除当前续写。"""
        swipe = self.continuation_panel.current_swipe
        if not swipe or not self._current_chapter:
            return

        reply = QMessageBox.question(
            self,
            "删除续写",
            "确定删除当前续写？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self.storage_service.delete_continuation(swipe.id)
            chapter = self.storage_service.load_chapter(self._current_chapter.id)
            if chapter:
                self._current_chapter = chapter
            self.continuation_panel.clear_output()
            self._set_status_message("已删除续写")
        except Exception as e:
            logger.error("删除续写失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "删除失败", str(e))

    # ===== 单章续写审计与修正 =====

    @staticmethod
    def _format_world_ontology(wo) -> str:
        """格式化世界观底层为 JSON 字符串。"""
        if wo is None:
            return "（无世界观底层）"
        try:
            if hasattr(wo, "model_dump"):
                return json.dumps(wo.model_dump(mode="json"), ensure_ascii=False, indent=2)
            if isinstance(wo, dict):
                return json.dumps(wo, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return "（无世界观底层）"

    @staticmethod
    def _format_protagonist_profile(pp) -> str:
        """格式化主角形象档案为 JSON 字符串。"""
        if pp is None:
            return "（无主角形象档案）"
        try:
            if hasattr(pp, "model_dump"):
                return json.dumps(pp.model_dump(mode="json"), ensure_ascii=False, indent=2)
            if isinstance(pp, dict):
                return json.dumps(pp, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return "（无主角形象档案）"

    @staticmethod
    def _format_style_profile(sp) -> str:
        """格式化文风档案为 JSON 字符串。"""
        if sp is None:
            return "（无文风档案）"
        try:
            if hasattr(sp, "model_dump"):
                return json.dumps(sp.model_dump(mode="json"), ensure_ascii=False, indent=2)
            if isinstance(sp, dict):
                return json.dumps(sp, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return "（无文风档案）"

    @staticmethod
    def _format_custom_audit_rules(rules: list) -> str:
        """格式化自定义设定列表为审计/续写提示词注入文本。"""
        if not rules:
            return "（无自定义设定）"
        parts: list[str] = []
        for i, rule in enumerate(rules, 1):
            if hasattr(rule, "model_dump"):
                r = rule.model_dump(mode="json")
            elif isinstance(rule, dict):
                r = rule
            else:
                continue
            parts.append(
                f"{i}. [{r.get('severity', 'critical').upper()}] {r.get('title', '未命名')}\n"
                f"   要求：{r.get('requirement', '')}\n"
                f"   审计向：{r.get('audit_criteria', '')}"
            )
        return "\n".join(parts) if parts else "（无自定义设定）"

    def _on_add_custom_rule_requested(self) -> None:
        """新增自定义设定：弹输入对话框 → AI 流式结构化 → 持久化到 Project。

        参考 ontology 流式模式：start_custom_rule_parsing 启动流式 UI →
        on_chunk 闭包 emit _custom_rule_chunk_received 信号 →
        on_done 闭包仅 emit _custom_rule_done 信号（不直接调 GUI，避免跨线程违规）→
        槽方法 _on_custom_rule_done 在 UI 线程执行 finish/fail + QMessageBox。
        """
        if not self._current_project_id:
            QMessageBox.warning(self, "提示", "请先选择项目")
            return
        try:
            project = self.storage_service.load_project(self._current_project_id)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载项目失败: {e}")
            return
        if project is None:
            QMessageBox.warning(self, "提示", "项目加载失败")
            return

        dialog = CustomRuleInputDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        raw_input = dialog.get_input()
        if not raw_input:
            return

        # 复用当前章节的上下文条目作为结构化参考
        context_entries = getattr(self, "_current_context_entries", None)

        # 启动流式 UI（复用 _stream_view，与 ontology/protagonist 互斥）
        context_panel = self.continuation_panel.context_preview_panel
        context_panel.start_custom_rule_parsing()
        self._set_status_message("正在结构化自定义设定（流式）...")

        from novelforge.services.async_runner import AsyncLoopRunner

        runner = AsyncLoopRunner.instance()
        loop = runner._loop

        def on_chunk(text: str) -> None:
            # 跨线程安全：仅 emit 信号，槽方法在 UI 线程更新 _stream_view
            self._custom_rule_chunk_received.emit(text)

        future = asyncio.run_coroutine_threadsafe(
            self.custom_rule_service.parse_rule_streaming(
                project=project,
                raw_input=raw_input,
                context_entries=context_entries,
                on_chunk=on_chunk,
                jailbreak_text=self._get_flow_jailbreak_text("custom_rule_parsing"),
            ),
            loop,
        )

        def on_done(fut) -> None:
            # 跨线程安全：仅 emit 信号，槽方法在 UI 线程执行 QMessageBox
            try:
                rule, status = fut.result()
                self._custom_rule_done.emit(rule, status)
            except Exception as e:
                logger.error("自定义设定结构化异常: %s", e, exc_info=True)
                self._custom_rule_done.emit(None, f"failed: {e}")

        future.add_done_callback(on_done)

    @Slot(str)
    def _on_custom_rule_chunk_received(self, text: str) -> None:
        """自定义设定流式 chunk 到达（UI 线程）：追加到 stream_view。"""
        self.continuation_panel.context_preview_panel.update_custom_rule_progress(text)

    @Slot(object, str)
    def _on_custom_rule_done(self, rule: object, status: str) -> None:
        """自定义设定结构化完成（UI 线程）：finish/fail UI + QMessageBox 提示。"""
        context_panel = self.continuation_panel.context_preview_panel
        if rule is not None:
            context_panel.finish_custom_rule_parsing(status)
            title = getattr(rule, "title", "")
            self._set_status_message(f"已新增自定义设定：{title}")
            QMessageBox.information(
                self, "成功", f"已新增自定义设定：\n{title}\n\n{status}"
            )
        else:
            context_panel.fail_custom_rule_parsing(status)
            self._set_status_message("自定义设定新增失败")
            QMessageBox.warning(self, "失败", f"新增自定义设定失败：\n{status}")

    def _on_view_custom_rules_requested(self) -> None:
        """查看已新增的自定义设定列表，支持删除。"""
        if not self._current_project_id:
            QMessageBox.warning(self, "提示", "请先选择项目")
            return
        try:
            project = self.storage_service.load_project(self._current_project_id)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载项目失败: {e}")
            return
        if project is None:
            QMessageBox.warning(self, "提示", "项目加载失败")
            return

        rules = project.custom_audit_rules or []
        if not rules:
            QMessageBox.information(self, "提示", "尚未新增自定义设定")
            return

        dialog = CustomRulesViewDialog(
            rules, on_delete=self._delete_custom_rule, parent=self
        )
        dialog.exec()

    def _delete_custom_rule(self, rule_id: str) -> None:
        """删除指定 ID 的自定义设定并持久化。"""
        if not self._current_project_id or not rule_id:
            return
        try:
            project = self.storage_service.load_project(self._current_project_id)
        except Exception as e:
            logger.error("删除自定义设定时加载项目失败: %s", e, exc_info=True)
            return
        if project is None:
            return

        original_count = len(project.custom_audit_rules or [])
        project.custom_audit_rules = [
            r for r in (project.custom_audit_rules or [])
            if (r.id if hasattr(r, "id") else r.get("id", "")) != rule_id
        ]
        if len(project.custom_audit_rules) == original_count:
            return  # 无变化

        try:
            self.storage_service.save_project(project)
            self._set_status_message("已删除自定义设定")
        except Exception as e:
            logger.error("删除自定义设定持久化失败: %s", e, exc_info=True)
            QMessageBox.warning(self, "失败", f"删除自定义设定持久化失败: {e}")

    def _on_highlights_changed(self, highlights: list) -> None:
        """输出栏高亮变化时持久化到当前 swipe。"""
        if not self._current_chapter:
            return
        swipe = self.continuation_panel.current_swipe
        if swipe is None:
            return
        try:
            swipe.highlights = list(highlights)
            self.storage_service.save_continuation(swipe, self._current_chapter.id)
        except Exception as e:
            logger.error("高亮持久化失败: %s", e, exc_info=True)

    def _on_audit_continuation(self) -> None:
        """审计当前续写：流式生成审计报告。

        组装 phase_single_audit.txt 模板（5 维度精简审计），流式输出到
        AuditDialog，完成后用户可编辑并采纳，采纳后触发修正流程。
        """
        if not self._current_chapter:
            QMessageBox.warning(self, "提示", "请先选择章节")
            return

        swipe = self.continuation_panel.current_swipe
        if not swipe:
            QMessageBox.warning(self, "提示", "请先生成续写后再审计")
            return

        # 获取 endpoint/api_key/model（流程配置优先，回退面板下拉）
        endpoint = self.config_manager.get_flow_endpoint("single_audit")
        if not endpoint:
            endpoint = self.continuation_panel.get_selected_endpoint()
        if not endpoint:
            QMessageBox.warning(self, "提示", "请先配置 API 端点")
            self._on_open_settings()
            return

        api_key = self.config_manager.decrypt_api_key(endpoint.get("id", ""))
        if not api_key:
            QMessageBox.warning(self, "提示", "API Key 无效，请检查设置")
            self._on_open_settings()
            return

        model = self._continuation_model or self.config_manager.get_flow_model("single_audit")
        if not model:
            QMessageBox.warning(self, "提示", "请选择模型")
            return

        # 加载审计提示词模板
        try:
            template_path = get_agent_prompt_path("single_audit")
            template = load_text_resource(template_path)
        except Exception as e:
            logger.error("加载审计模板失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "审计失败", f"加载审计模板失败: {e}")
            return

        # 加载项目（取 world_ontology）
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)

        # 组装宏
        user_input = self.continuation_panel.get_user_input() or "（无用户额外指令）"
        world_ontology_str = self._format_world_ontology(
            project.world_ontology if project else None
        )
        protagonist_profile = (
            self._current_chapter.protagonist_profile
            if self._current_chapter and self._current_chapter.protagonist_profile
            else self._protagonist_profile_by_chapter.get(self._current_chapter.id)
        )
        protagonist_profile_str = self._format_protagonist_profile(protagonist_profile)
        style_profile_str = self._format_style_profile(
            project.style_profile if project else None
        )

        # 简单字符串替换（与 VolumeOrchestrator 一致，不用 MacroEngine）
        rendered = template.replace("{{user_input}}", user_input)
        rendered = rendered.replace("{{written_text}}", swipe.content)
        rendered = rendered.replace("{{world_ontology}}", world_ontology_str)
        rendered = rendered.replace("{{protagonist_profile}}", protagonist_profile_str)
        rendered = rendered.replace("{{style_profile}}", style_profile_str)
        custom_rules_str = self._format_custom_audit_rules(
            project.custom_audit_rules if project else None
        )
        rendered = rendered.replace("{{custom_audit_rules}}", custom_rules_str)

        messages = [{"role": "system", "content": rendered}]
        # 注入破限（single_audit 流程，等级由 FlowEndpointDialog 配置）
        messages = self._inject_jailbreak(
            messages, self._get_flow_jailbreak_text("single_audit")
        )

        # 清理旧审计 worker
        old_audit_worker = getattr(self, "_audit_worker", None)
        if old_audit_worker is not None:
            try:
                old_audit_worker.chunk_received.disconnect()
                old_audit_worker.finished.disconnect()
                old_audit_worker.error.disconnect()
                old_audit_worker.rate_limit_warning.disconnect()
                old_audit_worker.auth_error.disconnect()
                old_audit_worker.token_count.disconnect()
            except (RuntimeError, TypeError):
                pass
            old_audit_worker.deleteLater()
        self._audit_worker = None

        # 解析思考强度（审计无预设，仅端点级）
        reasoning_effort = self._resolve_reasoning_effort(endpoint)

        # 创建审计 worker
        self._audit_worker = AuditWorker(
            base_url=endpoint["base_url"],
            api_key=api_key,
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=3000,
            reasoning_effort=reasoning_effort,
            endpoint_id=endpoint.get("id", ""),
            debug_mode=self._debug_mode,
            phase_name="单章审计",
            extra_payload=endpoint.get("extra_payload") or {},
            extra_headers=endpoint.get("extra_headers") or {},
            parent=self,
        )

        # 记录审计发起章节（采纳后 rewrite worker 用此 id 归档，避免切换章节后存错章节）
        self._audit_chapter_id = self._current_chapter.id

        # 创建审计对话框
        self._audit_dialog = AuditDialog(self)

        # 存储原 swipe 引用（采纳后修正时用）
        self._audit_original_swipe = swipe

        # 连接 worker 信号
        self._audit_worker.chunk_received.connect(self._on_audit_chunk_received)
        self._audit_worker.token_count.connect(
            lambda count: self._token_count_label.setText(
                f"Token: {count} (审计中)"
            )
        )
        self._audit_worker.finished.connect(self._on_audit_worker_finished)
        self._audit_worker.error.connect(self._on_audit_worker_error)
        self._audit_worker.rate_limit_warning.connect(
            lambda msg: self.continuation_panel.show_toast(msg)
        )
        self._audit_worker.auth_error.connect(self._on_auth_error)
        self._audit_worker.prompt_debug_requested.connect(
            self._on_prompt_debug_requested
        )

        # 连接对话框信号
        self._audit_dialog.accepted_text.connect(self._on_audit_accepted)
        self._audit_dialog.cancelled.connect(self._on_audit_cancelled)

        self._audit_dialog.show()
        self._audit_worker.start()
        self._set_status_message("审计中...")

    def _on_audit_chunk_received(self, chunk: str) -> None:
        """转发审计 chunk 到对话框（防止对话框已删除时崩溃）。"""
        audit_dialog = getattr(self, "_audit_dialog", None)
        if audit_dialog is not None:
            try:
                audit_dialog.append_chunk(chunk)
            except RuntimeError:
                # 对话框已被删除，忽略
                pass

    def _on_audit_worker_finished(self, full_text: str) -> None:
        """审计 worker 流式完成。"""
        if self._audit_dialog is not None:
            self._audit_dialog.finish_streaming(full_text)
        self._set_status_message("审计完成，请审阅报告")

    def _on_audit_worker_error(self, error: str) -> None:
        """审计 worker 出错。"""
        if self._audit_dialog is not None:
            self._audit_dialog.fail(error)
        self._set_status_message(f"审计失败: {error}")

    def _on_audit_cancelled(self) -> None:
        """用户取消审计：停止 worker，清理发起章节标记。"""
        if self._audit_worker is not None:
            try:
                self._audit_worker.chunk_received.disconnect()
            except (RuntimeError, TypeError):
                pass
            self._audit_worker.stop()
        self._audit_chapter_id = None
        self._set_status_message("已取消审计")

    def _on_audit_accepted(self, audit_text: str) -> None:
        """用户采纳审计报告：基于审计结果与修改意见重写续写内容。

        新流程：加载 phase_audit_rewrite.txt 独立模板，注入世界观底层、
        主角信息、当前需修改正文、审计结果与修改意见等占位符，
        构造全新 messages（不复用正文生成的提示词快照），
        新建 ContinuationWorker 流式输出修正后正文，作为新 swipe 保存到审计发起章节。
        """
        # 使用审计发起章节（避免用户切换章节后 rewrite 存入错误章节）
        audit_cid = self._audit_chapter_id
        if not audit_cid:
            return

        original_swipe = getattr(self, "_audit_original_swipe", None)
        if not original_swipe:
            return

        # 清理审计 worker 与对话框引用
        self._audit_worker = None
        self._audit_dialog = None

        # 获取生成参数（复用上次续写参数）
        params = dict(self._continuation_parameters) if self._continuation_parameters else {}
        if not params:
            params = self.continuation_panel.get_parameters()
            params["model"] = self._continuation_model

        # 获取 endpoint/api_key
        endpoint = self.continuation_panel.get_selected_endpoint()
        if not endpoint:
            QMessageBox.warning(self, "提示", "请先配置 API 端点")
            return
        api_key = self.config_manager.decrypt_api_key(endpoint.get("id", ""))
        if not api_key:
            QMessageBox.warning(self, "提示", "API Key 无效，请检查设置")
            self._on_open_settings()
            return

        # 解析模型（与审计发起处一致，兜底流程配置模型→端点默认模型）
        model = self._continuation_model or self.config_manager.get_flow_model("single_audit")
        if not model:
            QMessageBox.warning(self, "提示", "请选择模型")
            return

        # 加载审计后修改提示词模板（独立模板，不复用正文生成的提示词）
        try:
            template_path = get_agent_prompt_path("audit_rewrite")
            template = load_text_resource(template_path)
        except Exception as e:
            logger.error("加载审计修改模板失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "修正失败", f"加载审计修改模板失败: {e}")
            return

        # 加载项目（取 world_ontology/protagonist_profile/custom_audit_rules）
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)

        # 组装宏（与 _on_audit_continuation 一致不用 MacroEngine）
        world_ontology_str = self._format_world_ontology(
            project.world_ontology if project else None
        )
        protagonist_profile = None
        if self._current_chapter:
            if self._current_chapter.protagonist_profile:
                protagonist_profile = self._current_chapter.protagonist_profile
            else:
                protagonist_profile = self._protagonist_profile_by_chapter.get(
                    self._current_chapter.id
                )
        protagonist_profile_str = self._format_protagonist_profile(protagonist_profile)
        style_profile_str = self._format_style_profile(
            project.style_profile if project else None
        )
        custom_rules_str = self._format_custom_audit_rules(
            project.custom_audit_rules if project else None
        )

        # str.replace 宏替换占位符（聚焦修改意见，审计报告整体即修改意见）
        rendered = template.replace("{{original_content}}", original_swipe.content)
        rendered = rendered.replace("{{critique}}", audit_text)
        rendered = rendered.replace("{{world_ontology}}", world_ontology_str)
        rendered = rendered.replace("{{protagonist_profile}}", protagonist_profile_str)
        rendered = rendered.replace("{{style_profile}}", style_profile_str)
        rendered = rendered.replace("{{custom_audit_rules}}", custom_rules_str)
        rendered = rendered.replace("{{previous_chapters_text}}", "")
        rendered = rendered.replace("{{chapter_plan}}", "（无卷大纲章节规划）")
        rendered = rendered.replace("{{outline}}", "（无细纲）")
        rendered = rendered.replace("{{pacing_speed}}", "（单章无推进速度约束）")
        target_words = params.get("target_words", "")
        rendered = rendered.replace(
            "{{target_words}}", str(target_words) if target_words else ""
        )

        # 构造全新 messages（不复用原续写提示词快照 _continuation_prompt_messages）
        revision_messages = [
            {"role": "system", "content": rendered},
            {"role": "user", "content": "请基于审计结果与修改意见，重写完整正文。"},
        ]

        # 判断用户是否仍停留在审计发起章节
        on_origin_chapter = bool(
            self._current_chapter and self._current_chapter.id == audit_cid
        )

        # 仅当用户停留在发起章节时清空输出并进入流式状态
        if on_origin_chapter:
            self.chapter_editor.set_streaming_locked(True)
            self.continuation_panel.start_streaming()

        # 记录续写发起章节（rewrite worker 完成回调用此 id 归档 swipe）
        self._continuation_chapter_id = audit_cid
        self._continuation_stream_text_by_chapter[audit_cid] = ""

        # 记录续写会话追踪信息
        self._continuation_started_at = self.history_service.now_iso()
        self._continuation_prompt_messages = list(revision_messages)
        self._continuation_model = model
        self._continuation_parameters = dict(params)

        # 断开旧 worker 的信号连接
        old_worker = getattr(self, "_continuation_worker", None)
        if old_worker is not None:
            try:
                old_worker.chunk_received.disconnect()
                old_worker.token_count.disconnect()
                old_worker.finished.disconnect()
                old_worker.error.disconnect()
                old_worker.rate_limit_warning.disconnect()
                old_worker.auth_error.disconnect()
            except (RuntimeError, TypeError):
                pass
            old_worker.deleteLater()

        # 加载预设与正则快照（用于 swipe 记录）
        preset_id = params.get("preset_id", "default")
        preset = self.preset_service.load_preset(preset_id)
        if preset is None:
            preset = self.preset_service.load_default_preset()
        ordered_scripts = self.regex_service.get_ordered_scripts(
            project_id=self._current_project_id or "",
            preset_id=preset.id,
            include_disabled=False,
        )
        regex_script_ids = [s.id for s, _ in ordered_scripts]
        # 加载审计发起章节以取 metadata（避免用错章节元数据）
        origin_chapter = self.storage_service.load_chapter(audit_cid)
        chapter_metadata = dict(origin_chapter.metadata) if origin_chapter else {}

        # 注入思考强度（重写是续写任务，用续写端点+预设）
        params["reasoning_effort"] = self._resolve_reasoning_effort(endpoint, preset)

        # 创建修正 worker（created_by 标记为 audit_rewrite）
        self._continuation_worker = ContinuationWorker(
            base_url=endpoint["base_url"],
            api_key=api_key,
            model=model,
            messages=revision_messages,
            parameters=params,
            chapter_id=audit_cid,
            created_by="audit_rewrite",
            preset_id=preset.id,
            preset_snapshot=preset.model_dump(mode="json"),
            regex_engine=self.regex_engine,
            template_engine=self.template_engine,
            project_id=self._current_project_id or "",
            chapter_metadata=chapter_metadata,
            regex_script_ids=regex_script_ids,
            endpoint_id=endpoint.get("id", ""),
            debug_mode=self._debug_mode,
            extra_payload=endpoint.get("extra_payload") or {},
            extra_headers=endpoint.get("extra_headers") or {},
            parent=self,
        )

        # chunk 路由：按章节缓冲，仅当前章节匹配时更新 UI
        self._continuation_worker.chunk_received.connect(
            self._on_continuation_chunk_received
        )
        self._continuation_worker.token_count.connect(
            lambda count: self._token_count_label.setText(
                f"Token: {count} (修正中)"
            )
        )
        self._continuation_worker.finished.connect(self._on_continuation_finished)
        self._continuation_worker.error.connect(self._on_continuation_error)
        self._continuation_worker.rate_limit_warning.connect(
            lambda msg: self.continuation_panel.show_toast(msg)
        )
        self._continuation_worker.auth_error.connect(self._on_auth_error)
        self._continuation_worker.prompt_debug_requested.connect(
            self._on_prompt_debug_requested
        )

        self._continuation_worker.start()
        # 清理审计发起章节标记（rewrite 已交接给 _continuation_chapter_id）
        self._audit_chapter_id = None
        self._set_status_message("修正中...")

    # ===== 重写当前章节模式：分析→检查点→生成 =====

    @staticmethod
    def _format_context_entries(entries: list | None) -> str:
        """格式化上下文条目为可读 Markdown（空 → 占位文本）。

        镜像 ``CustomAuditRuleService._format_context_entries`` 实现，
        用于将 ``_current_context_entries`` 注入 ``phase_rewrite_analysis.txt``
        的 ``{{context_entries}}`` 占位符。
        """
        if not entries:
            return "（无上下文条目）"
        parts: list[str] = []
        for entry in entries:
            if isinstance(entry, dict):
                content = entry.get("content", "")
                keys = entry.get("keys", "")
                comment = entry.get("comment", "")
            else:
                content = getattr(entry, "content", "")
                keys = getattr(entry, "keys", "")
                comment = getattr(entry, "comment", "")
            label = comment or keys or "条目"
            if content:
                parts.append(f"- [{label}] {content}")
        return "\n".join(parts) if parts else "（无上下文条目）"

    def _build_previous_chapters_text(self, lookback: int) -> str:
        """构建前 ``lookback`` 章正文文本块（含当前章），供写作模式分析提示词注入。

        写作模式是续写下一章，当前章是最末前文，故包含当前章正文
        （区别于 rewrite_current 的 ``exclude_current=True``）。

        格式：每章一段 ``## {标题}\\n\\n{正文}``，章间空行分隔。
        ``lookback<=0`` 取全部前文（含当前章）。

        Args:
            lookback: 回溯章节数（含当前章）；<=0 取全部

        Returns:
            格式化的前文章节正文文本，无内容时返回占位文本
        """
        if not self._current_chapters or not self._current_chapter:
            return "（无前文）"
        self._ensure_chapter_contents()
        # 定位当前章 index
        idx = -1
        for i, ch in enumerate(self._current_chapters):
            if ch.id == self._current_chapter.id:
                idx = i
                break
        if idx < 0:
            return "（无前文）"
        start = 0 if lookback <= 0 else max(0, idx - lookback + 1)
        selected = self._current_chapters[start: idx + 1]
        parts: list[str] = []
        for ch in selected:
            title = ch.title or f"第{ch.index}章"
            content = (ch.content or "").strip()
            if content:
                parts.append(f"## {title}\n\n{content}")
        return "\n\n".join(parts) if parts else "（无前文）"

    def _on_start_rewrite_current(self, params: dict) -> None:
        """「重写当前章节」模式入口：分析→检查点→生成两步流程。

        Step 1 分析：加载 ``phase_rewrite_analysis.txt`` 模板，str.replace 注入
        6 占位符（current_chapter_text/user_input/world_ontology/protagonist_profile/
        custom_audit_rules/context_entries，**前文不含当前章节**），用
        ``AuditWorker``（低温稳定）流式输出「新章节生成详细需求」，弹
        ``AuditDialog`` 供用户审阅/编辑。

        Step 2 生成：用户采纳分析结果后，将分析文本作为 ``user_input`` 传入
        ``prompt_assembler.assemble``（``exclude_current=True`` 让聊天历史不含
        当前章节），创建 ``ContinuationWorker``（``created_by="rewrite_current"``）
        流式输出新正文，存为 swipe 到当前章节。

        Args:
            params: 续写参数字典（由 continuation_panel 传入）
        """
        if not self._current_chapter:
            QMessageBox.warning(self, "提示", "请先选择章节")
            return

        # 持久化续写参数到 config（温度/回溯章节数）
        try:
            cont = self.config_manager.config.setdefault("continuation", {})
            cont["default_temperature"] = params.get("temperature", 0.8)
            cont["default_target_words"] = params.get("target_words", 2000)
            cont["default_lookback_chapters"] = params.get("lookback_chapters", 5)
            self.config_manager.save()
        except Exception as e:
            logger.warning("保存续写参数失败: %s", e)

        # 取 rewrite_analysis 流程端点（回退面板下拉）
        endpoint = self.config_manager.get_flow_endpoint("rewrite_analysis")
        if not endpoint:
            endpoint = self.continuation_panel.get_selected_endpoint()
        if not endpoint:
            QMessageBox.warning(self, "提示", "请先配置 API 端点")
            self._on_open_settings()
            return

        api_key = self.config_manager.decrypt_api_key(endpoint.get("id", ""))
        if not api_key:
            QMessageBox.warning(self, "提示", "API Key 无效，请检查设置")
            self._on_open_settings()
            return

        model = params.get("model") or self.config_manager.get_flow_model("rewrite_analysis")
        if not model:
            QMessageBox.warning(self, "提示", "请选择模型")
            return

        # 上下文条目（重写模式下用户已用 exclude_current=True 提取，不含当前章节）
        raw_entries = getattr(self, "_current_context_entries", None)
        entries = self._merge_worldbook_entries(raw_entries)
        # 加载项目对象（用于检查世界观/主角是否已提取）
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)
        wo_for_check = project.world_ontology if project else None
        pp_for_check = self._protagonist_profile_by_chapter.get(self._current_chapter.id) if self._current_chapter else None
        sp_for_check = project.style_profile if project else None
        if not self._prompt_continue_without_extraction(entries, wo_for_check, pp_for_check, sp_for_check):
            return

        # 确保章节正文已加载
        self._ensure_chapter_contents()

        # 加载分析提示词模板
        try:
            template_path = get_agent_prompt_path("rewrite_analysis")
            template = load_text_resource(template_path)
        except Exception as e:
            logger.error("加载重写分析模板失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "重写失败", f"加载重写分析模板失败: {e}")
            return

        # 组装宏（与 _on_audit_continuation 一致，不用 MacroEngine，纯 str.replace）
        user_input = self.continuation_panel.get_user_input() or "（无用户额外指令）"
        world_ontology_str = self._format_world_ontology(
            project.world_ontology if project else None
        )
        protagonist_profile = None
        if self._current_chapter:
            if self._current_chapter.protagonist_profile:
                protagonist_profile = self._current_chapter.protagonist_profile
            else:
                protagonist_profile = self._protagonist_profile_by_chapter.get(
                    self._current_chapter.id
                )
        protagonist_profile_str = self._format_protagonist_profile(protagonist_profile)
        style_profile_str = self._format_style_profile(
            project.style_profile if project else None
        )
        custom_rules_str = self._format_custom_audit_rules(
            project.custom_audit_rules if project else None
        )
        context_entries_str = self._format_context_entries(entries)
        current_chapter_text = self._current_chapter.content or ""

        rendered = template.replace("{{current_chapter_text}}", current_chapter_text)
        rendered = rendered.replace("{{user_input}}", user_input)
        rendered = rendered.replace("{{world_ontology}}", world_ontology_str)
        rendered = rendered.replace("{{protagonist_profile}}", protagonist_profile_str)
        rendered = rendered.replace("{{style_profile}}", style_profile_str)
        rendered = rendered.replace("{{custom_audit_rules}}", custom_rules_str)
        rendered = rendered.replace("{{context_entries}}", context_entries_str)

        messages = [{"role": "system", "content": rendered}]
        # 注入破限（rewrite_analysis 流程，等级由 FlowEndpointDialog 配置）
        messages = self._inject_jailbreak(
            messages, self._get_flow_jailbreak_text("rewrite_analysis")
        )

        # 清理旧审计 worker（与 _on_audit_continuation 一致）
        old_audit_worker = getattr(self, "_audit_worker", None)
        if old_audit_worker is not None:
            try:
                old_audit_worker.chunk_received.disconnect()
                old_audit_worker.finished.disconnect()
                old_audit_worker.error.disconnect()
                old_audit_worker.rate_limit_warning.disconnect()
                old_audit_worker.auth_error.disconnect()
                old_audit_worker.token_count.disconnect()
            except (RuntimeError, TypeError):
                pass
            old_audit_worker.deleteLater()
        self._audit_worker = None

        # 解析思考强度（分析步骤无预设，仅端点级）
        reasoning_effort = self._resolve_reasoning_effort(endpoint)

        # 创建分析 worker（低温稳定，max_tokens=4000 容纳详细需求）
        self._audit_worker = AuditWorker(
            base_url=endpoint["base_url"],
            api_key=api_key,
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=4000,
            reasoning_effort=reasoning_effort,
            endpoint_id=endpoint.get("id", ""),
            debug_mode=self._debug_mode,
            phase_name="重写需求分析",
            extra_payload=endpoint.get("extra_payload") or {},
            extra_headers=endpoint.get("extra_headers") or {},
            parent=self,
        )

        # 记录重写发起章节与重写参数（生成步骤用）
        self._rewrite_current_chapter_id = self._current_chapter.id
        self._rewrite_current_params = dict(params)
        self._rewrite_current_endpoint = endpoint

        # 创建分析对话框（标题"重写需求分析"）
        self._audit_dialog = AuditDialog(self)
        self._audit_dialog.setWindowTitle("重写需求分析")

        # 连接 worker 信号（复用审计对话框的流式输出）
        self._audit_worker.chunk_received.connect(self._audit_dialog.append_chunk)
        self._audit_worker.token_count.connect(
            lambda count: self._token_count_label.setText(
                f"Token: {count} (分析中)"
            )
        )
        self._audit_worker.finished.connect(self._on_rewrite_analysis_finished)
        self._audit_worker.error.connect(self._on_rewrite_analysis_error)
        self._audit_worker.rate_limit_warning.connect(
            lambda msg: self.continuation_panel.show_toast(msg)
        )
        self._audit_worker.auth_error.connect(self._on_auth_error)
        self._audit_worker.prompt_debug_requested.connect(
            self._on_prompt_debug_requested
        )

        # 连接对话框信号（采纳→进入生成步骤；取消→终止）
        self._audit_dialog.accepted_text.connect(self._on_rewrite_analysis_accepted)
        self._audit_dialog.cancelled.connect(self._on_rewrite_analysis_cancelled)

        self._audit_dialog.show()
        self._audit_worker.start()
        self._set_status_message("重写需求分析中...")

    def _on_rewrite_analysis_finished(self, full_text: str) -> None:
        """分析 worker 流式完成。"""
        if self._audit_dialog is not None:
            self._audit_dialog.finish_streaming(full_text)
        self._set_status_message("分析完成，请审阅并采纳需求")

    def _on_rewrite_analysis_error(self, error: str) -> None:
        """分析 worker 出错。"""
        if self._audit_dialog is not None:
            self._audit_dialog.fail(error)
        self._set_status_message(f"重写分析失败: {error}")
        self._rewrite_current_chapter_id = None
        self._rewrite_current_params = None
        self._rewrite_current_endpoint = None

    def _on_rewrite_analysis_cancelled(self) -> None:
        """用户取消分析：停止 worker，清理重写状态标记。"""
        if self._audit_worker is not None:
            self._audit_worker.stop()
        self._audit_worker = None
        self._audit_dialog = None
        self._rewrite_current_chapter_id = None
        self._rewrite_current_params = None
        self._rewrite_current_endpoint = None
        self._set_status_message("已取消重写")

    def _on_rewrite_analysis_accepted(self, analysis_text: str) -> None:
        """用户采纳分析结果：进入 Step 2 生成步骤。

        将 ``analysis_text`` 作为 ``user_input`` 传入 ``prompt_assembler.assemble``
        （``exclude_current=True`` 让聊天历史不含当前章节），创建
        ``ContinuationWorker``（``created_by="rewrite_current"``）流式输出新正文，
        存为 swipe 到当前章节。
        """
        # 清理 FlowExecutor 的 pending 状态（内置 rewrite_current 由信号链
        # 直接完成两步流程，FlowExecutor 职责在 audit handler 返回 pending 后结束）
        if self.flow_executor.is_active:
            self.flow_executor.cancel()

        rewrite_cid = getattr(self, "_rewrite_current_chapter_id", None)
        if not rewrite_cid:
            return

        params = getattr(self, "_rewrite_current_params", None) or {}
        # 缓存分析文本到 params，供重写时复用（写入 swipe.parameters_snapshot）
        params["_rewrite_analysis_text"] = analysis_text
        endpoint = getattr(self, "_rewrite_current_endpoint", None)
        if not endpoint:
            endpoint = self.continuation_panel.get_selected_endpoint()
        if not endpoint:
            QMessageBox.warning(self, "提示", "请先配置 API 端点")
            self._rewrite_current_chapter_id = None
            return

        api_key = self.config_manager.decrypt_api_key(endpoint.get("id", ""))
        if not api_key:
            QMessageBox.warning(self, "提示", "API Key 无效，请检查设置")
            self._on_open_settings()
            self._rewrite_current_chapter_id = None
            return

        model = params.get("model") or self.config_manager.get_flow_model("single_continuation")
        if not model:
            QMessageBox.warning(self, "提示", "请选择模型")
            self._rewrite_current_chapter_id = None
            return

        # 清理分析 worker 与对话框引用
        self._audit_worker = None
        self._audit_dialog = None

        # 加载项目对象
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)

        # 重新合并 entries（避免中途变化）
        raw_entries = getattr(self, "_current_context_entries", None)
        entries = self._merge_worldbook_entries(raw_entries)
        wo_for_check = project.world_ontology if project else None
        pp_for_check = self._protagonist_profile_by_chapter.get(rewrite_cid) if rewrite_cid else None
        sp_for_check = project.style_profile if project else None
        if not self._prompt_continue_without_extraction(entries, wo_for_check, pp_for_check, sp_for_check):
            self._rewrite_current_chapter_id = None
            return

        # 加载重写发起章节（避免用户切换章节后用错章节）
        origin_chapter = self.storage_service.load_chapter(rewrite_cid)
        if origin_chapter is None:
            QMessageBox.warning(self, "提示", "重写发起章节不存在")
            self._rewrite_current_chapter_id = None
            return
        # 同步当前章节为发起章节（重写流程针对发起章节）
        self._current_chapter = origin_chapter
        self._ensure_chapter_contents()

        # 加载预设
        preset_id = params.get("preset_id", "default")
        preset = self.preset_service.load_preset(preset_id)
        if preset is None:
            preset = self.preset_service.load_default_preset()
            logger.warning("预设 %s 不存在，回退到默认预设", preset_id)

        # 取预设生成参数
        gen_params = preset.generation_params
        max_context = params.get("max_context") or gen_params.get("max_context", 9999999)
        max_tokens = gen_params.get("max_tokens", 2000)
        target_words = params.get("target_words", 2000)

        # 取小说档案
        novel_profile = project.novel_profile if project else {}

        # 刷新正则脚本到引擎
        self._refresh_regex_scripts()

        # 章节元数据
        chapter_metadata = dict(origin_chapter.metadata) if origin_chapter else {}

        # 调用 PromptAssembler.assemble（exclude_current=True 让历史不含当前章节）
        try:
            assemble_result = self.prompt_assembler.assemble(
                preset=preset,
                chapters=self._current_chapters,
                current_chapter=origin_chapter,
                context_entries=entries,
                model=model,
                max_context=max_context,
                max_tokens=max_tokens,
                target_words=target_words,
                novel_profile=novel_profile,
                project_id=self._current_project_id or "",
                chapter_metadata=chapter_metadata,
                user_input=analysis_text,
                lookback_chapters=params.get("lookback_chapters", 0),
                world_ontology=project.world_ontology if project else None,
                protagonist_profile=self._protagonist_profile_by_chapter.get(rewrite_cid),
                custom_audit_rules=project.custom_audit_rules if project else None,
                style_profile=project.style_profile if project else None,
                exclude_current=True,
            )
        except Exception as e:
            logger.error("重写生成提示词组装失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "组装失败", f"重写生成提示词组装失败: {e}")
            self._rewrite_current_chapter_id = None
            return

        messages = assemble_result.messages

        # 显示 token 预算信息
        usage = assemble_result.token_usage
        is_exact, count_desc = assemble_result.count_mode
        self._token_count_label.setText(
            f"Token: {usage.get('total_used', 0)}/{max_context} ({count_desc})"
        )

        # 显示警告
        for warning in assemble_result.warnings:
            self._set_status_message(warning)
            if "建议增大 max_context" in warning or "截断后仅" in warning:
                QMessageBox.warning(self, "Token 预算警告", warning)

        logger.info(
            "重写生成提示词组装完成: %d 条消息, token=%d/%d",
            len(messages), usage.get("total_used", 0), max_context,
        )

        # 收集正则脚本 ID 快照（用于 swipe 记录）
        ordered_scripts = self.regex_service.get_ordered_scripts(
            project_id=self._current_project_id or "",
            preset_id=preset.id,
            include_disabled=False,
        )
        regex_script_ids = [s.id for s, _ in ordered_scripts]

        # 上下文条目快照
        context_snapshot = [
            e.model_dump(mode="json") if hasattr(e, "model_dump") else e
            for e in entries
        ]

        # 锁定编辑器
        self.chapter_editor.set_streaming_locked(True)
        self.continuation_panel.start_streaming()

        # 记录续写会话追踪信息
        self._continuation_started_at = self.history_service.now_iso()
        self._continuation_prompt_messages = list(messages)
        self._continuation_model = model
        self._continuation_parameters = dict(params)

        # 断开旧 worker 的信号连接
        old_worker = getattr(self, "_continuation_worker", None)
        if old_worker is not None:
            try:
                old_worker.chunk_received.disconnect()
                old_worker.token_count.disconnect()
                old_worker.finished.disconnect()
                old_worker.error.disconnect()
                old_worker.rate_limit_warning.disconnect()
                old_worker.auth_error.disconnect()
            except (RuntimeError, TypeError):
                pass
            old_worker.deleteLater()

        # 记录续写发起章节（完成回调用此 id 归档 swipe）
        self._continuation_chapter_id = rewrite_cid
        self._continuation_stream_text_by_chapter[rewrite_cid] = ""

        # 注入思考强度（重写是续写任务，用 single_continuation 端点+预设）
        # 复用 single_continuation 端点（与单章续写一致）
        single_endpoint = self.config_manager.get_flow_endpoint("single_continuation") or endpoint
        params["reasoning_effort"] = self._resolve_reasoning_effort(single_endpoint, preset)

        # 创建生成 worker（created_by="rewrite_current" 标识重写当前章节 swipe）
        self._continuation_worker = ContinuationWorker(
            base_url=single_endpoint["base_url"],
            api_key=api_key,
            model=model,
            messages=messages,
            parameters=params,
            chapter_id=rewrite_cid,
            created_by="rewrite_current",
            preset_id=preset.id,
            preset_snapshot=preset.model_dump(mode="json"),
            token_budget=usage,
            regex_engine=self.regex_engine,
            template_engine=self.template_engine,
            project_id=self._current_project_id or "",
            chapter_metadata=chapter_metadata,
            regex_script_ids=regex_script_ids,
            extracted_context_snapshot=context_snapshot,
            endpoint_id=single_endpoint.get("id", ""),
            debug_mode=self._debug_mode,
            extra_payload=single_endpoint.get("extra_payload") or {},
            extra_headers=single_endpoint.get("extra_headers") or {},
            parent=self,
        )

        # chunk 路由：按章节缓冲，仅当前章节匹配时更新 UI
        self._continuation_worker.chunk_received.connect(
            self._on_continuation_chunk_received
        )
        self._continuation_worker.token_count.connect(
            lambda count: self._token_count_label.setText(
                f"Token: {count} (重写中)"
            )
        )
        self._continuation_worker.finished.connect(self._on_continuation_finished)
        self._continuation_worker.error.connect(self._on_continuation_error)
        self._continuation_worker.rate_limit_warning.connect(
            lambda msg: self.continuation_panel.show_toast(msg)
        )
        self._continuation_worker.auth_error.connect(self._on_auth_error)
        self._continuation_worker.prompt_debug_requested.connect(
            self._on_prompt_debug_requested
        )

        self._continuation_worker.start()
        # 清理重写状态标记（生成已交接给 _continuation_chapter_id）
        self._rewrite_current_chapter_id = None
        self._rewrite_current_params = None
        self._rewrite_current_endpoint = None
        self._set_status_message("重写生成中...")

    # ===== 写作模式（writing_mode）通用分析路径 =====

    def _on_start_generic_analysis(
        self, stage, params: dict, phase: str, phase_name: str
    ) -> None:
        """通用分析流程入口（audit agent 非 rewrite_analysis 分支）。

        镜像 ``_on_start_rewrite_current`` 结构（加载模板→str.replace 注入→
        AuditWorker 低温流式→AuditDialog 审阅），差异点：
        - 模板由 ``stage.params["phase"]`` 决定（``phase_{phase}.txt``）
        - flow_key 取 ``stage.flow_key``（端点/模型/破限）
        - 读 ``params._prev_output`` 注入 ``{{prev_analysis}}``（阶段 2 接收阶段 1 输出）
        - 读面板 ``lookback_chapters`` 构建前文文本注入 ``{{previous_chapters_text}}``
        - 用户采纳后 ``flow_executor.resume`` 推进下一阶段（不 cancel）

        Args:
            stage: FlowStage 对象（含 flow_key / params）
            params: 合并后的阶段参数（含面板参数 + 阶段 params + _prev_output）
            phase: 提示词模板 phase 名（如 writing_element_analysis）
            phase_name: 显示名 / AuditDialog 标题（如 写作要素分析）
        """
        if not self._current_chapter:
            QMessageBox.warning(self, "提示", "请先选择章节")
            return

        flow_key = stage.flow_key

        # 取流程端点（回退面板下拉）
        endpoint = self.config_manager.get_flow_endpoint(flow_key)
        if not endpoint:
            endpoint = self.continuation_panel.get_selected_endpoint()
        if not endpoint:
            QMessageBox.warning(self, "提示", "请先配置 API 端点")
            self._on_open_settings()
            return

        api_key = self.config_manager.decrypt_api_key(endpoint.get("id", ""))
        if not api_key:
            QMessageBox.warning(self, "提示", "API Key 无效，请检查设置")
            self._on_open_settings()
            return

        model = params.get("model") or self.config_manager.get_flow_model(flow_key)
        if not model:
            QMessageBox.warning(self, "提示", "请选择模型")
            return

        # 上下文条目
        raw_entries = getattr(self, "_current_context_entries", None)
        entries = self._merge_worldbook_entries(raw_entries)
        # 加载项目对象（用于世界观/主角/自定义设定）
        project = None
        if self._current_project_id:
            project = self.storage_service.load_project(self._current_project_id)
        wo_for_check = project.world_ontology if project else None
        pp_for_check = (
            self._protagonist_profile_by_chapter.get(self._current_chapter.id)
            if self._current_chapter
            else None
        )
        sp_for_check = project.style_profile if project else None
        if not self._prompt_continue_without_extraction(
            entries, wo_for_check, pp_for_check, sp_for_check
        ):
            return

        # 确保章节正文已加载
        self._ensure_chapter_contents()

        # 加载分析提示词模板
        try:
            template_path = get_agent_prompt_path(phase)
            template = load_text_resource(template_path)
        except Exception as e:
            logger.error("加载通用分析模板失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "分析失败", f"加载分析模板失败: {e}")
            return

        # 组装宏（纯 str.replace，不用 MacroEngine）
        user_input = self.continuation_panel.get_user_input() or "（无用户额外指令）"
        world_ontology_str = self._format_world_ontology(
            project.world_ontology if project else None
        )
        protagonist_profile = None
        if self._current_chapter:
            if self._current_chapter.protagonist_profile:
                protagonist_profile = self._current_chapter.protagonist_profile
            else:
                protagonist_profile = self._protagonist_profile_by_chapter.get(
                    self._current_chapter.id
                )
        protagonist_profile_str = self._format_protagonist_profile(protagonist_profile)
        style_profile_str = self._format_style_profile(
            project.style_profile if project else None
        )
        custom_rules_str = self._format_custom_audit_rules(
            project.custom_audit_rules if project else None
        )
        context_entries_str = self._format_context_entries(entries)
        lookback_chapters = params.get("lookback_chapters", 5)
        previous_chapters_text = self._build_previous_chapters_text(lookback_chapters)
        prev_analysis = params.get("_prev_output") or "（无前序分析）"

        rendered = template.replace("{{user_input}}", user_input)
        rendered = rendered.replace("{{world_ontology}}", world_ontology_str)
        rendered = rendered.replace("{{protagonist_profile}}", protagonist_profile_str)
        rendered = rendered.replace("{{style_profile}}", style_profile_str)
        rendered = rendered.replace("{{custom_audit_rules}}", custom_rules_str)
        rendered = rendered.replace("{{context_entries}}", context_entries_str)
        rendered = rendered.replace("{{previous_chapters_text}}", previous_chapters_text)
        rendered = rendered.replace("{{prev_analysis}}", prev_analysis)

        messages = [{"role": "system", "content": rendered}]
        # 注入破限（按 stage.flow_key，等级由 FlowEndpointDialog 配置）
        messages = self._inject_jailbreak(
            messages, self._get_flow_jailbreak_text(flow_key)
        )

        # 清理旧审计 worker
        old_audit_worker = getattr(self, "_audit_worker", None)
        if old_audit_worker is not None:
            try:
                old_audit_worker.chunk_received.disconnect()
                old_audit_worker.finished.disconnect()
                old_audit_worker.error.disconnect()
                old_audit_worker.rate_limit_warning.disconnect()
                old_audit_worker.auth_error.disconnect()
                old_audit_worker.token_count.disconnect()
            except (RuntimeError, TypeError):
                pass
            old_audit_worker.deleteLater()
        self._audit_worker = None

        reasoning_effort = self._resolve_reasoning_effort(endpoint)

        # 创建分析 worker（低温稳定，max_tokens 默认 6000 容纳多角色形象）
        self._audit_worker = AuditWorker(
            base_url=endpoint["base_url"],
            api_key=api_key,
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=params.get("analysis_max_tokens", 6000),
            reasoning_effort=reasoning_effort,
            endpoint_id=endpoint.get("id", ""),
            debug_mode=self._debug_mode,
            phase_name=phase_name,
            extra_payload=endpoint.get("extra_payload") or {},
            extra_headers=endpoint.get("extra_headers") or {},
            parent=self,
        )

        # 创建分析对话框
        self._audit_dialog = AuditDialog(self)
        self._audit_dialog.setWindowTitle(phase_name)

        # 连接 worker 信号
        self._audit_worker.chunk_received.connect(self._audit_dialog.append_chunk)
        self._audit_worker.token_count.connect(
            lambda count: self._token_count_label.setText(
                f"Token: {count} (分析中)"
            )
        )
        self._audit_worker.finished.connect(self._on_generic_analysis_finished)
        self._audit_worker.error.connect(self._on_generic_analysis_error)
        self._audit_worker.rate_limit_warning.connect(
            lambda msg: self.continuation_panel.show_toast(msg)
        )
        self._audit_worker.auth_error.connect(self._on_auth_error)
        self._audit_worker.prompt_debug_requested.connect(self._on_prompt_debug_requested)

        # 连接对话框信号（采纳→resume 推进；取消→cancel 清理）
        self._audit_dialog.accepted_text.connect(self._on_generic_analysis_accepted)
        self._audit_dialog.cancelled.connect(self._on_generic_analysis_cancelled)

        self._audit_dialog.show()
        self._audit_worker.start()
        self._set_status_message(f"{phase_name}中...")

    def _on_generic_analysis_finished(self, full_text: str) -> None:
        """通用分析 worker 流式完成。"""
        if self._audit_dialog is not None:
            self._audit_dialog.finish_streaming(full_text)
        self._set_status_message("分析完成，请审阅并采纳")

    def _on_generic_analysis_error(self, error: str) -> None:
        """通用分析 worker 出错：cancel 流程并提示。"""
        if self._audit_dialog is not None:
            self._audit_dialog.fail(error)
        self._set_status_message(f"分析失败: {error}")
        if self.flow_executor.is_active:
            self.flow_executor.cancel()

    def _on_generic_analysis_cancelled(self) -> None:
        """用户取消通用分析：停止 worker，cancel 流程。"""
        if self._audit_worker is not None:
            self._audit_worker.stop()
        self._audit_worker = None
        self._audit_dialog = None
        if self.flow_executor.is_active:
            self.flow_executor.cancel()
        self._set_status_message("已取消写作模式分析")

    def _on_generic_analysis_accepted(self, analysis_text: str) -> None:
        """用户采纳通用分析结果：resume 推进下一阶段（不 cancel）。

        与 ``_on_rewrite_analysis_accepted`` 的关键差异：rewrite_current 走信号链
        直接完成两步流程（cancel FlowExecutor），通用路径须 resume 让 FlowExecutor
        继续执行后续阶段（阶段 2 / 阶段 3）。
        """
        self._audit_dialog = None
        self._audit_worker = None
        if self.flow_executor.is_active:
            self.flow_executor.resume(analysis_text)
        else:
            logger.warning("generic_analysis_accepted 但无活跃流程")

    def _on_start_writing_mode_continuation(
        self, params: dict, prev_output: str
    ) -> None:
        """写作模式第 3 步：把阶段 2 精炼输出前置为【写作参考】到 user_input。

        将 ``prev_output``（阶段 2 角色形象精炼输出）以 ``【写作参考】`` 标签包裹后
        前置到面板 user_input，调 ``_on_start_continuation(user_input_override=...)``
        走单章续写。复用 rewrite_current「分析文本→user_input」模式，零侵入
        ``prompt_assembler`` / ``ContinuationWorker``。

        Args:
            params: 阶段参数（含面板参数 + 阶段 params）
            prev_output: 阶段 2 精炼输出文本
        """
        panel_input = self.continuation_panel.get_user_input() or "（无用户额外指令）"
        combined = (
            f"【写作参考】（写作模式提取的强调参考内容，续写须严格遵循）\n"
            f"{prev_output}\n\n"
            f"【用户写作需求】\n{panel_input}"
        )
        # 缓存阶段 2 精炼输出到 params，供重写时复用（写入 swipe.parameters_snapshot）
        params["_writing_mode_refinement"] = prev_output
        # 清理 FlowExecutor pending 状态（continuation 内部自管 worker）
        if self.flow_executor.is_active:
            self.flow_executor.cancel()
        self._on_start_continuation(params, user_input_override=combined)

    def _on_accept_and_continue(self) -> None:
        """接受并继续续写。"""
        self._on_accept_continuation()
        # 基于追加后的完整章节内容发起新续写（根据当前模式路由）
        QTimer.singleShot(500, lambda: self._on_start_continuation_routed(
            self.continuation_panel.get_parameters()
        ))

    def _on_edit_then_accept(self) -> None:
        """编辑后接受。"""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextEdit

        swipe = self.continuation_panel.current_swipe
        if not swipe:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("编辑续写内容")
        dialog.setMinimumSize(500, 400)
        layout = QVBoxLayout(dialog)

        edit = QTextEdit()
        edit.setPlainText(swipe.content)
        layout.addWidget(edit)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            edited_text = edit.toPlainText()
            swipe.content = edited_text
            self._on_accept_continuation()

    def _on_compare_swipes(self) -> None:
        """并排对比两个 swipe。"""
        if not self._current_chapter or len(self._current_chapter.continuations) < 2:
            QMessageBox.information(self, "对比", "至少需要 2 个续写版本才能对比")
            return

        from PySide6.QtWidgets import QDialog, QHBoxLayout, QTextEdit

        dialog = QDialog(self)
        dialog.setWindowTitle("并排对比")
        dialog.setMinimumSize(800, 500)
        layout = QHBoxLayout(dialog)

        conts = self._current_chapter.continuations
        left_edit = QTextEdit()
        left_edit.setReadOnly(True)
        left_edit.setPlainText(
            f"=== 续写1 ({conts[0].model}) ===\n\n{conts[0].content}"
        )
        layout.addWidget(left_edit)

        right_edit = QTextEdit()
        right_edit.setReadOnly(True)
        right_edit.setPlainText(
            f"=== 续写2 ({conts[1].model}) ===\n\n{conts[1].content}"
        )
        layout.addWidget(right_edit)

        dialog.exec()

    # ===== 菜单事件处理 =====

    def _on_export(self) -> None:
        """导出（M5 已实现，保留兼容入口）。"""
        if not self._current_project_id:
            QMessageBox.warning(self, "提示", "请先打开项目")
            return
        self._on_export_full_txt()

    def _on_export_full_txt(self) -> None:
        """导出完整 TXT。"""
        if not self._current_project_id:
            QMessageBox.warning(self, "提示", "请先打开项目")
            return

        project = self.storage_service.load_project(self._current_project_id)
        default_name = f"{project.name or 'novel'}.txt" if project else "novel.txt"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出完整 TXT", default_name, "文本文件 (*.txt);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            # TODO: 将文件 I/O 移入 QThread 以避免 UI 阻塞（见 spec Task 27）
            count = export_full_txt(
                self.storage_service,
                self._current_project_id,
                file_path,
                include_titles=True,
            )
            QMessageBox.information(
                self, "导出成功", f"已导出 {count} 字到:\n{file_path}"
            )
            self._set_status_message(f"已导出 TXT: {count} 字")
        except Exception as e:
            logger.error("导出完整 TXT 失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "导出失败", f"导出失败: {e}")

    def _on_export_project_backup(self) -> None:
        """导出项目备份 zip。"""
        if not self._current_project_id:
            QMessageBox.warning(self, "提示", "请先打开项目")
            return

        project = self.storage_service.load_project(self._current_project_id)
        default_name = f"{project.name or 'novel'}_backup.zip" if project else "backup.zip"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出项目备份", default_name, "ZIP 文件 (*.zip);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            # TODO: 将文件 I/O 移入 QThread 以避免 UI 阻塞（见 spec Task 27）
            manifest_path = export_project_backup(
                self.storage_service,
                self.preset_service,
                self.regex_service,
                self._current_project_id,
                file_path,
            )
            QMessageBox.information(
                self,
                "备份成功",
                f"已导出项目备份到:\n{file_path}\n\nmanifest: {manifest_path}",
            )
            self._set_status_message("项目备份已导出")
        except Exception as e:
            logger.error("导出项目备份失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "备份失败", f"导出项目备份失败: {e}")

    def _on_import_project_backup(self) -> None:
        """导入项目备份 zip。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择项目备份 zip", "", "ZIP 文件 (*.zip);;所有文件 (*)"
        )
        if not file_path:
            return

        reply = QMessageBox.question(
            self,
            "导入项目备份",
            "导入后将创建新项目（不覆盖原项目）。\n继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            # TODO: 将文件 I/O 移入 QThread 以避免 UI 阻塞（见 spec Task 27）
            new_project_id = import_project_backup(
                self.storage_service,
                self.preset_service,
                self.regex_service,
                file_path,
            )
            self._load_project(new_project_id)
            QMessageBox.information(
                self, "导入成功", f"已导入项目，新项目 ID:\n{new_project_id}"
            )
            self._set_status_message("项目备份已导入")
        except Exception as e:
            logger.error("导入项目备份失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "导入失败", f"导入项目备份失败: {e}")

    def _on_open_history_panel(self) -> None:
        """打开续写历史日志面板。"""
        try:
            panel = HistoryPanel(
                self.storage_service, self.history_service, self
            )
            panel.setWindowModality(Qt.WindowModality.ApplicationModal)
            panel.exec()
        except Exception as e:
            logger.error("打开历史日志面板失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "错误", f"打开历史日志面板失败: {e}")

    def _on_open_font_settings(self) -> None:
        """打开字体设置对话框。"""
        try:
            dialog = FontSettingsDialog(self.config_manager, self)
            if dialog.exec() == FontSettingsDialog.DialogCode.Accepted:
                # 应用字体设置到章节编辑器
                self._apply_font_settings()
                self._set_status_message("字体设置已应用")
        except Exception as e:
            logger.error("打开字体设置失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "错误", f"打开字体设置失败: {e}")

    def _apply_font_settings(self) -> None:
        """应用配置中的字体设置到章节编辑器。"""
        try:
            appearance = self.config_manager.get_appearance()
            if hasattr(self, "chapter_editor"):
                apply_font_to_editor(self.chapter_editor, appearance)
                logger.debug(
                    "已应用字体设置: %s %dpt, 行距 %.2f",
                    appearance.get("font_family", ""),
                    appearance.get("font_size", 14),
                    appearance.get("line_height", 1.6),
                )
        except Exception as e:
            logger.warning("应用字体设置失败: %s", e)

    def _sync_token_limit_default(self) -> None:
        """从配置同步 token 限制默认值到上下文预览面板的下拉框。"""
        try:
            extract_settings = self.config_manager.get_context_extract_settings()
            token_limit = extract_settings.get("token_limit", 0)
            context_panel = self.continuation_panel.context_preview_panel
            if token_limit <= 0:
                context_panel._token_limit_combo.setCurrentText("不限制")
            else:
                context_panel._token_limit_combo.setCurrentText(
                    f"{token_limit // 1000}k"
                )
        except Exception as e:
            logger.warning("同步 token 限制默认值失败: %s", e)

    def _save_token_limit_to_config(self, text: str) -> None:
        """预览面板 Token 拆分变更时持久化到配置。"""
        try:
            from novelforge.ui.helpers import parse_token_limit
            extract_settings = self.config_manager.get_context_extract_settings()
            extract_settings["token_limit"] = parse_token_limit(text)
            self.config_manager.config["context_extract"] = extract_settings
            self.config_manager.save()
        except Exception as e:
            logger.warning("保存 token 限制失败: %s", e)

    def _on_save(self) -> None:
        """保存当前章节。"""
        self.chapter_editor.save_now()
        # 如果有当前章节，更新存储
        if self._current_chapter and self.chapter_editor.chapter_id == self._current_chapter.id:
            self._current_chapter.content = self.chapter_editor.content
            self._current_chapter.title = self.chapter_editor.title
            self.storage_service.save_chapter(self._current_chapter)
            self._update_chapter_in_list(self._current_chapter)
        self._set_status_message("已保存")

    def _on_undo(self) -> None:
        """撤销（编辑器 undo）。"""
        self.chapter_editor.undo()

    def _on_redo(self) -> None:
        """重做（编辑器 redo）。"""
        self.chapter_editor.redo()

    def _on_open_preset_manager(self) -> None:
        """打开预设管理器（非模态独立窗口）。"""
        try:
            manager = PresetManager(
                self.preset_service, self, regex_service=self.regex_service
            )
            manager.preset_changed.connect(self._on_preset_changed)
            manager.setWindowModality(Qt.WindowModality.NonModal)
            manager.show()
            # 保持引用，避免被 GC
            self._preset_manager_windows.append(manager)
            # 清理已关闭的窗口
            self._preset_manager_windows = [
                w for w in self._preset_manager_windows if w.isVisible()
            ]
        except Exception as e:
            logger.error("打开预设管理器失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "错误", f"打开预设管理器失败: {e}")

    def _on_preset_changed(self, preset_id: str) -> None:
        """预设管理器中的预设变更。"""
        self._refresh_presets()

    def _on_open_regex_manager(self) -> None:
        """打开正则管理器（非模态独立窗口）。"""
        try:
            # 获取当前预设 ID（用于 preset 作用域脚本加载）
            preset_id = "default"
            params = self.continuation_panel.get_parameters()
            if params.get("preset_id"):
                preset_id = params.get("preset_id")

            manager = RegexManager(
                regex_service=self.regex_service,
                project_id=self._current_project_id or "",
                preset_id=preset_id,
                parent=self,
            )
            manager.regex_changed.connect(self._on_regex_changed)
            manager.setWindowModality(Qt.WindowModality.NonModal)
            manager.show()
            # 保持引用，避免被 GC
            self._regex_manager_windows.append(manager)
            # 清理已关闭的窗口
            self._regex_manager_windows = [
                w for w in self._regex_manager_windows if w.isVisible()
            ]
        except Exception as e:
            logger.error("打开正则管理器失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "错误", f"打开正则管理器失败: {e}")

    def _on_regex_changed(self) -> None:
        """正则脚本变更：重新编译到引擎。"""
        self._refresh_regex_scripts()
        logger.info("正则脚本已变更，引擎已重新编译")

    def _refresh_regex_scripts(self) -> None:
        """重新加载并编译正则脚本到引擎。

        按 GLOBAL → PRESET → SCOPED 顺序加载所有启用的脚本，
        编译到 RegexEngine 中供 PromptAssembler 与 ContinuationWorker 使用。
        """
        try:
            preset_id = "default"
            params = self.continuation_panel.get_parameters()
            if params.get("preset_id"):
                preset_id = params.get("preset_id")

            ordered = self.regex_service.get_ordered_scripts(
                project_id=self._current_project_id or "",
                preset_id=preset_id,
                include_disabled=False,
            )
            scripts = [script for script, _ in ordered]
            self.regex_engine.compile_scripts(scripts)
            logger.debug("已编译 %d 个正则脚本到引擎", len(scripts))
        except Exception as e:
            logger.error("刷新正则脚本失败: %s", e, exc_info=True)

    def _on_open_template_editor(self) -> None:
        """打开模板编辑器（非模态独立窗口）。"""
        try:
            # 获取当前章节元数据
            chapter_metadata = {}
            if self._current_chapter:
                chapter_metadata = dict(self._current_chapter.metadata)

            editor = TemplateEditor(
                variable_store=self.variable_store,
                template_engine=self.template_engine,
                project_id=self._current_project_id or "",
                chapter_metadata=chapter_metadata,
                parent=self,
            )
            editor.variables_changed.connect(self._on_variables_changed)
            editor.setWindowModality(Qt.WindowModality.NonModal)
            editor.show()
            # 保持引用，避免被 GC
            self._template_editor_windows.append(editor)
            # 清理已关闭的窗口
            self._template_editor_windows = [
                w for w in self._template_editor_windows if w.isVisible()
            ]
        except Exception as e:
            logger.error("打开模板编辑器失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "错误", f"打开模板编辑器失败: {e}")

    def _on_variables_changed(self) -> None:
        """变量变更：使变量存储缓存失效。"""
        try:
            self.variable_store.invalidate_cache()
            logger.info("变量已变更，缓存已失效")
        except Exception as e:
            logger.error("刷新变量缓存失败: %s", e, exc_info=True)

    def _on_open_flow_endpoint_dialog(self) -> None:
        """打开流程端点配置对话框，关闭后同步续写面板端点选择。"""
        dialog = FlowEndpointDialog(self.config_manager, self)
        dialog.exec()
        self._refresh_endpoints()

    def _on_open_settings(self) -> None:
        """打开设置对话框。"""
        dialog = SettingsDialog(
            self.config_manager,
            self,
            storage_service=self.storage_service,
            history_service=self.history_service,
        )
        dialog.exec()
        # 刷新端点列表
        self._refresh_endpoints()
        # M5: 字体设置可能变更，重新应用
        self._apply_font_settings()

    def _on_about(self) -> None:
        """关于对话框。"""
        from novelforge import __version__

        QMessageBox.about(
            self,
            "关于 赓笔",
            f"<h3>赓笔 小说续写器</h3>"
            f"<p>版本: {__version__}</p>"
            f"<p>基于 PySide6 的桌面小说续写工具</p>"
            f"<p>参考 SillyTavern 提示词管线设计</p>"
            f"<p>技术基线: Python 3.11+ / PySide6 / Jinja2 / OpenAI-Compatible API</p>",
        )

    def _on_show_privacy(self) -> None:
        """显示隐私声明。"""
        dialog = PrivacyDialog(self)
        dialog.exec()

    # ===== 工具方法 =====

    def _set_status_message(self, message: str) -> None:
        """设置状态栏消息。"""
        self._save_status_label.setText(message)
        logger.info("状态: %s", message)

    def _on_toast_requested(self, message: str) -> None:
        """在状态栏显示临时提示，3 秒后还原为 swipe 元信息或"就绪"。"""
        self._set_status_message(message)
        QTimer.singleShot(3000, self._restore_status_after_toast)

    def _restore_status_after_toast(self) -> None:
        """toast 超时后还原状态栏。"""
        swipe = self.continuation_panel.current_swipe
        if swipe:
            self._set_status_message(
                f"模型: {swipe.model} | 状态: {swipe.status} | 字数: {len(swipe.content)}"
            )
        else:
            self._set_status_message("就绪")
