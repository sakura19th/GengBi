# NovelForge 项目约束文件

> **重要**：本文件是项目的核心约束文档。每次修改项目代码后，必须读取并即时更新本文件，确保文档与代码保持同步。

## 项目概述

NovelForge 是一个 SillyTavern (ST) 兼容的小说续写工具，提供从 TXT 导入、章节管理、上下文提取、提示词组装到 LLM 流式续写的完整工作流。

**当前版本：v0.2.0**（定义于 `novelforge/__init__.py` 的 `__version__`，由"关于"对话框引用，README 顶部同步标注）

**版本更新记录**：维护在 `README.md` 的"更新记录"章节，按版本倒序排列，每次发版须追加新版本小节（新增功能/修复/优化分类，每条一行简洁描述）

## 技术栈

- **语言**：Python 3.11+
- **GUI 框架**：PySide6 (Qt6)
- **数据模型**：pydantic v2
- **异步 HTTP**：aiohttp（LLM 流式调用）
- **存储**：aiosqlite（异步 SQLite）+ JSON 文件
- **测试**：pytest

## 架构分层

```
novelforge/
├── models/          # 数据模型（pydantic）
│   ├── chapter.py       # Chapter、Continuation
│   ├── context.py       # ContextEntry、VALID_CATEGORIES/POSITIONS/ROLES
│   ├── preset.py        # WritingPreset、Prompt、PromptOrderEntry
│   ├── project.py       # Project、NovelProfile
│   ├── regex.py         # RegexScript、PLACEMENT_* 常量
│   ├── agent.py         # Agent 多阶段续写数据模型
│   └── volume.py        # 卷级多章节续写数据模型（DeepAnalysis/VolumeOutline/VolumeArtifacts/VolumeRunConfig）
├── core/            # 核心逻辑（无 UI 依赖）
│   ├── prompt_assembler.py  # 提示词组装（三阶段：排序→注入→裁剪）
│   ├── regex_engine.py      # ST 兼容正则引擎 + strip_html_tags
│   ├── template_engine.py   # Jinja2 模板渲染
│   ├── token_counter.py     # Token 计数（tiktoken 优先，回退估算）
│   ├── macros.py            # ST 宏引擎（{{user}}、{{char}} 等）
│   ├── variable_store.py    # 变量存储（{{setvar}}/{{getvar}}）
│   ├── json_utils.py        # JSON 解析工具（strip_markdown_fences + parse_json_response）
│   ├── config.py            # 配置管理（API 端点、密钥加密）
│   └── storage.py           # 存储路径、JSON 读写工具
├── services/        # 业务服务
│   ├── context_extractor.py  # 上下文提取（extract + extract_streaming）
│   ├── continuation_worker.py # QThread + asyncio 续写 worker
│   ├── agent_orchestrator.py # 多阶段 Agent 续写编排器（QThread+asyncio）
│   ├── volume_orchestrator.py # 卷级多章节续写编排器（QThread+asyncio，独立类）
│   ├── llm_client.py         # LLM 客户端（流式 + 非流式）
│   ├── preset_service.py     # 预设管理（导入/导出/启用/禁用）
│   ├── regex_service.py      # 正则脚本管理（global/scoped/preset）
│   ├── importer.py           # TXT 导入与章节拆分
│   ├── exporter.py           # 导出（TXT/Markdown/备份）
│   ├── async_runner.py       # 后台事件循环运行器（单例）
│   └── storage_service.py    # 存储服务（项目/章节/续写 CRUD）
├── ui/              # UI 组件（PySide6）
│   ├── main_window.py           # 主窗口（5 栏 QSplitter 布局 + 主题管理 + 调试菜单）
│   ├── continuation_panel.py    # 续写控制面板（流式布局按钮区）
│   ├── agent_panel.py           # Agent 控制面板（阶段开关/暂停点/进度/产物）
│   ├── volume_panel.py          # 卷续写控制面板（卷配置/审计与逐章/暂停点/两层进度/产物）
│   ├── checkpoint_dialog.py     # 检查点对话框（大纲/验证/卷续写简单暂停/审计前用户输入）
│   ├── context_preview_panel.py # 上下文提取预览面板（流式布局操作行）
│   ├── debug_prompt_dialog.py   # 调试提示词预览对话框（调试模式下发送前确认）
│   ├── flow_layout.py           # QFlowLayout 流式布局（窄屏自动换行）
│   ├── wheel_filter.py          # 滚轮事件过滤器（未聚焦控件不响应滚轮，转发父级滚动区域）
│   ├── chapter_list.py          # 章节列表（虚拟滚动/搜索/右键菜单）
│   ├── chapter_editor.py        # 章节预览/编辑（自动保存/undo/拆分）
│   ├── history_panel.py         # 续写历史日志面板
│   ├── font_settings.py         # 字体设置对话框（字体/字号/行距）
│   ├── extraction_dialog.py     # 提取失败重试对话框
│   ├── dialogs.py               # 通用对话框（隐私声明等）
│   ├── project_panel.py         # 项目管理对话框
│   ├── preset_manager.py        # 预设管理器
│   ├── regex_manager.py         # 正则管理器
│   ├── template_editor.py       # 模板编辑器（变量/模板渲染）
│   ├── worldbook_manager.py     # 世界书管理器（导入/编辑/启停）
│   ├── worldbook_panel.py       # 世界书选择面板（嵌入续写配置）
│   ├── settings_dialog.py       # 设置对话框（API 端点管理）
│   └── ...
└── resources/       # 资源文件
    ├── defaults/        # 默认预设、正则、提取提示词
    │   └── agent/       # Agent 阶段提示词模板（phase_*.txt）
    │       ├── phase_analysis.txt        # 单次续写：前文分析（STORY STATUS SNAPSHOT）
    │       ├── phase_outline.txt         # 单次续写：3-7 场景大纲
    │       ├── phase_verify.txt          # 单次续写：验证
    │       ├── phase_revise.txt          # 单次续写：修订（含 previous_chapters_text + pacing_speed 占位符，卷续写复用）
    │       ├── phase_deep_analysis.txt   # 卷续写：前文深度分析（DeepAnalysis）
    │       ├── phase_volume_outline.txt  # 卷续写：N 章卷大纲（VolumeOutline）
    │       ├── phase_outline_audit.txt   # 卷续写：大纲多维度审计+修订（OutlineAuditReport）
    │       └── phase_chapter_outline.txt # 卷续写：单章场景级细纲（Outline，场景数量按 pacing_speed 动态：slow=1-3/medium=3-5/fast=5-8）
    └── themes/          # QSS 主题（Apple HIG 风格 暗色/亮色）
```

## 关键设计决策

### 1. ST 兼容性

- 预设格式兼容 SillyTavern 的 `prompts` + `prompt_order` + `extensions.regex_scripts` 结构
- 正则脚本支持 ST 的 `findRegex`（`/pattern/flags` 格式）、`trimStrings`、`placement`（1=USER_INPUT, 2=AI_OUTPUT, 5=WORLD_INFO）
- 宏系统兼容 ST 的 `{{user}}`、`{{char}}`、`{{setvar::name::value}}`、`{{getvar::name}}` 等

### 2. QThread + asyncio 桥接

- `ContinuationWorker` 继承 `QThread`，在 `run()` 中创建独立 asyncio 事件循环
- 流式 chunk 通过 Qt 信号 `chunk_received(str)` 跨线程推送至 UI
- `AsyncLoopRunner` 单例提供持久后台事件循环，供同步 UI 代码提交协程

### 3. 提取与续写解耦

- 上下文提取是独立步骤：用户先点击"提取上下文"按钮，确认结果后再续写
- 提取使用流式 `extract_streaming()`，不阻塞 UI 线程
- 续写时使用已提取的 `self._current_context_entries`，不再自动提取

### 4. trimStrings 行为

- TGbreak 等预设的 `trimStrings` 会剥离 `<` 和 `>` 字符
- `strip_html_tags` 需额外清理残留碎片（半残标签、HTML 注释碎片、配对标签碎片）
- `strip_html_tags` 仅在检测到 HTML 特征时调用（`_contains_html` 检测）

### 5. UI 布局规范

主窗口 5 栏 QSplitter 布局（左→右）：
1. 章节目录（最小 160px，默认 200px，不伸缩）
2. 预览/编辑器（最小 250px，默认 420px，伸缩）
3. 上下文提取预览（最小 200px，默认 280px，伸缩）
4. 续写控制（最小 220px，默认 260px，不伸缩）
5. 续写输出（最小 280px，默认 400px，伸缩）

- 初始窗口大小：1600×900（`resize(1600, 900)`）
- 最小窗口大小：1280×700
- `DEFAULT_PANEL_SIZES = [200, 420, 280, 260, 400]`（总和 1560px ≤ 1600px）
- `showEvent` 在首次启动时应用默认面板尺寸（QSettings 无保存时）
- CollapsiblePanel 添加控件必须用 `add_widget()`，不能用 `QVBoxLayout(content_layout.widget())`（后者返回 None）
- 续写模式切换：单次续写 / 智能续写（多阶段 Agent）/ 卷续写（多章节）
- 智能模式显示 AgentPanel（阶段开关、暂停点、max_revise_rounds、进度指示器、产物查看器）
- 卷续写模式显示 VolumePanel（章节数/分析深度/审计与逐章设置/暂停点/两层进度/产物查看器），隐藏 AgentPanel 与单次参数区
- 单次模式显示原有续写参数区（温度/字数/Token/回溯章节数）
- ContinuationPanel 内部布局：顶部 `mode_group`（续写模式下拉框，固定高度）+ 中部 `_content_splitter`（垂直 QSplitter，伸缩因子 1）+ 底部 `btn_layout`（流式按钮区，固定）；`_content_splitter` 上半为 `_mode_content_widget`（容纳 `_config_group`/`_agent_panel`/`_volume_panel`，三者 `addWidget` 均传伸缩因子 1，确保可见面板撑满中间空间），下半为 `_user_input_group`（用户输入框，伸缩因子 0 默认小、可拖动把手调整高度，`setChildrenCollapsible(False)` 防止拖到消失，`setHandleWidth(6)` 加宽把手，初始 sizes `[400, 60]`）
- 三模式路由：`_on_start_continuation_routed` 按 `continuation_panel.get_mode()` 分发到 `_on_start_continuation`/`_on_start_agent_continuation`/`_on_start_volume_continuation`；Ctrl+Enter 与重写按钮均经此路由
- `_on_mode_changed` 调用 `show_volume_panel`/`show_agent_panel` 管理三种模式的面板显隐互斥
- 窄屏自适应：续写控制面板按钮区、上下文提取预览操作行/按钮行使用 `QFlowLayout`（`flow_layout.py`），按宽度自动换行，避免窄屏按钮文字截断/溢出
- 按钮高度统一：普通按钮与 `#primaryBtn` 主按钮均 `min-height: 28px`，由全局 QSS 接管

### 6. 主题系统（Apple HIG 风格）

- 主题文件：`resources/themes/{light,dark}.qss`，由 `main_window._apply_theme()` 读取并用 `app.setStyleSheet()` 全局应用
- 主题三态：暗色/亮色/跟随系统（后者读 `styleHints().colorScheme()`，并监听 `colorSchemeChanged` 信号实时重应用）
- 设计 token：主色 System Blue `#007aff`（亮）/`#0a84ff`（暗）；背景层次 `#ffffff`/`#f2f2f7`（亮）、`#1c1c1e`/`#2c2c2e`/`#3a3a3c`（暗）
- 圆角分级：按钮 8px、主按钮胶囊 14px、卡片/文本框 10px、列表项选中块 6px
- 列表选中态：淡 accent 半透明圆角（`rgba(0,122,255,0.12)`），文字不变色
- 阴影近似：QSS 不支持 box-shadow，用极淡边框 `rgba(0,0,0,0.06)` + 背景层次色差近似
- 内联样式禁用：UI 代码不使用 `setStyleSheet`，所有样式通过对象名（`setObjectName`）由全局 QSS 接管（如 `panelTitle`/`textSecondary`/`textInfo`/`textSuccess`/`textDanger`/`metaText` 等）
- 状态色语义：进行中=橙 `#ff9500`、成功=绿 `#34c759`、错误=红 `#ff3b30`、次要=半透明黑/白

### 7. Token 预算

- `PromptAssembler.assemble()` 计算 token 预算：`max_context - max_tokens - system_tokens - injection_tokens - user_input_tokens`
- 超限时自动降低 `max_tokens` 以保证当前章节最低上下文
- `assemble()` 是纯本地操作，不调用 LLM，可用于提示词预览

### 8. 多阶段 Agent 续写

- AgentOrchestrator 镜像 ContinuationWorker 的 QThread+asyncio 模式
- 5 阶段可选：分析→大纲→写作→验证→修订，各组合均合法（优雅降级）
- 阶段提示词用独立模板文件（resources/defaults/agent/phase_*.txt），不走 preset prompts[] 系统
- 宏替换用 str.replace（不用 MacroEngine/Jinja2），镜像 extract_prompt.txt 机制
- 大纲注入复用 ContextEntry（position=before），格式化为 Markdown 文本避免被宏/正则破坏
- worldInfoBefore marker 不存在时 fallback：直接 prepend system 消息到 messages
- 暂停点（checkpoint）：asyncio.wait_for 轮询 + call_soon_threadsafe 线程安全恢复；CheckpointDialog 大纲模式 actions 为 "accept"/"edit"/"cancel"（"edit" 仅关闭对话框并显示面板"继续"按钮，不解析产物，用户在 AgentPanel 编辑大纲后点"继续"恢复）；验证模式 actions 为 "accept"/"revise"/"rewrite"
- JSON 解析失败重试一次（温度归零），再失败跳过该阶段（优雅降级）
- 修订循环：critique.passed=False 时重跑写作阶段，rounds 不超过 max_revise_rounds；修订重写时 `_run_writing` 接收 `original_content` 参数（当前已生成正文），非空时修订指导 user 消息改为"以下是当前已生成的内容，请根据修订指导重写"+原文+指导（_async_run 修订循环调用处传 `original_content=final_content`）
- 前文回溯（lookback）：`__init__` 通过 `current_chapter.id` 在 `chapters` 列表中查找位置计算 `_current_chapter_index`（找不到为 -1，fallback 全量）；`_build_lookback_chapters_text(max_chapters=10)` 返回 `chapters[max(0,idx-9):idx+1]` 拼接（含当前章共 10 章，找不到时 fallback 全量）；`_run_outline` 的 `{{chapters_text}}` 与 `_run_verify` 的 `{{previous_chapters_text}}` 占位符均注入 lookback 文本（而非全量 `_chapters_text`），缩小前文窗口
- 调试模式：`prompt_debug_requested = Signal(str, str)` 信号（phase_name, messages_json）；`__init__` 初始化 `debug_mode`/`_debug_confirmed`/`_debug_confirmed_result` 三属性，`_async_run` 创建 `_debug_confirmed = asyncio.Event()`；UI 线程设 `debug_mode=True` 后，每次 LLM 调用前 `_maybe_debug_prompt(messages, phase_name)` emit 信号并 `await _debug_confirmed.wait()` 阻塞，UI 线程调 `confirm_debug_prompt(confirmed)` 唤醒（True=发送/False=取消）；5 阶段均接入（前文分析/大纲规划/续写写作/质量验证/修订指导），取消时各阶段返回降级值（None/None/("","","")/None/{}）；续写写作的调试确认位于 messages 全部组装后（含 outline fallback + 修订指导）、流式循环前
- agent 产物持久化到 Continuation.agent_artifacts，随 swipe 保存到 SQLite
- SQLite 迁移幂等：PRAGMA table_info 检测列存在性再 ALTER TABLE ADD COLUMN（continuations 表的 agent_artifacts / volume_artifacts 两列均走此模式，迁移函数 `_migrate_continuations_columns` 集中处理）

### 9. 卷级多章节续写（Volume-level Multi-chapter Continuation）

- 在单次 Agent 续写之外，新增"卷续写"流程：一次产出 N 章正文
- 数据模型定义于 `novelforge/models/volume.py`（镜像 agent.py 的 pydantic v2 风格）：
  - `DeepAnalysis`：前文深度分析产物，分故事状态/深度分析/结构化清单三组字段，所有字段默认空；`character_arc_patterns` 与 `key_phrases` 均为 `list[dict[str, Any]]`（与 phase_deep_analysis.txt 模板要求输出对象数组一致，避免 pydantic 校验报错）
  - `VolumeOutline`/`ChapterPlan`：卷大纲与单章节计划，`plot_role` 枚举（起/承/转/合/高潮/过渡）
  - `OutlineAuditReport`/`AuditDimension`：大纲审计，默认 7 维度（consistency/pacing/engagement/structure/coherence/foreshadowing/characters）
  - `ChapterArtifacts`/`VolumeArtifacts`：复用 `agent.py` 的 `Outline`/`CritiqueReport`，聚合卷级各阶段产物；`VolumeArtifacts` 新增 `audit_reports: list[OutlineAuditReport]`（多轮审计报告列表）与 `final_outline: VolumeOutline|None`（终稿大纲，审计+终稿生成后的最终版本）
  - `VolumeRunConfig`：`chapter_count` 校验范围 [2,20]，`analysis_depth` 枚举校验（light/standard/thorough/exhaustive），4 个暂停点（after_deep_analysis/after_volume_outline/before_audit/after_audit），其中 `before_audit` 默认开启（审计前用户输入协助审定窗口），其余默认关闭；`analysis_chunk_tokens`（0=不切分全量发送，>0=按该 token 数切分前文章节）+ `analysis_chunk_strategy`（sequential=按章节顺序切分）两字段支持深度分析 token 切分；VolumePanel `_analysis_chunk_tokens_combo` 默认选项为"100k"（index 2，对应 100000 tokens/块），使深度分析默认启用切分与增量更新（前文超 100k 时自动切分逐块分析，不足 100k 时单次调用无副作用），用户可手动改为"不限制"或其他档位；新增 `audit_rounds: int=1`（大纲审计轮次，1-3 校验器）与 `pacing_speed: str="medium"`（推进速度 slow/medium/fast 校验器，slow=缓速同一场景1-2章/medium=中速一个场景半章/fast=快速按目前设定）两字段
  - `Continuation.volume_artifacts` 字段默认 None，向后兼容旧 JSON（无该字段时反序列化为 None）
  - 卷级产物持久化到 SQLite continuations 表的 `volume_artifacts TEXT` 列（紧跟 `agent_artifacts` 之后），随 swipe 保存；`save_continuation` INSERT 17 列，None 存 NULL，非 None 用 `json.dumps` 序列化；`_row_to_continuation` 从 row[16] 读取并容错 None（旧 NULL 行加载为 None 不报错）；幂等迁移见第 8 条
  - 导入链无循环：volume.py → agent.py，chapter.py → volume.py，无反向依赖
- 5 阶段独立提示词模板（resources/defaults/agent/phase_*.txt）：
  - phase_deep_analysis.txt：前文深度分析（DeepAnalysis），强调**非模板化**、基于前文实际内容剖析剧情排布/张力/钩子/文风/对话/伏笔/常用梗/设定库/人物弧光/反复元素/故事状态；受 analysis_depth（light/standard/thorough/exhaustive）与 max_analysis_entries 共同约束条目上限；模板含单个 `{{chapters_text}}` 占位符（共 9 个占位符），由编排器注入 lookback chapters 文本（插入点之前，不切分时）或切分块文本（切分时）；早期 Task 5 曾区分 `{{full_chapters_text}}`（全文）与 `{{lookback_chapters_text}}`（续写点前文）两个占位符，但因导致 token 重复注入超限且切分逻辑复杂，已合并为单个 `{{chapters_text}}`；"# 分析任务"段开头"重要：文本使用说明"改为说明所有维度分析均基于上述章节文本，切分模式下每块仅含部分章节通过增量合并产出完整报告
  - phase_volume_outline.txt：依据 DeepAnalysis 规划 N 章 VolumeOutline，按起承转合（约 25%/50%/25%）分配 plot_role，跨章张力曲线，伏笔回收/埋设；强调**严格依据 DeepAnalysis** 而非套通用模板；Task 5 起前文段落标签改为 "# 续写点前文内容"（仍用 `{{chapters_text}}` 占位符，由编排器注入 lookback 文本而非全文）；新增 `{{pacing_speed}}` 占位符（卷参数段），推进速度说明：缓速=同一场景1-2章/中速=一个场景半章/快速=按目前设定；新增 `{{context_entries}}` 占位符（上下文条目段，由 `_build_context_entries_text` 注入自动提取的人物/地点/事件/风格/伏笔等，共 7 个占位符）
  - phase_outline_audit.txt：一轮调用同时完成多维度审计（consistency/pacing/engagement/structure/coherence/foreshadowing/characters）与修订，产出 OutlineAuditReport（含 revised_outline 完整 VolumeOutline）；注入 `{{previous_chapters_text}}`（最近 10 章正文，Task 9 起按 `_current_chapter_index` 回看：取 `[max(0, idx-9) .. idx]` 区间而非 `chapters[-10:]`，避免把卷续写新追加的章节算进审计前文）与 `{{deep_analysis}}`（DeepAnalysis JSON）两个占位符供一致性审计参考；新增 `{{round_idx}}`/`{{total_rounds}}` 占位符（审计任务段开头，标注当前轮次/总轮次，支持多轮审计循环）；新增 `{{audit_focus}}` 占位符（用户指定的审计重点段，由 `before_audit` 检查点收集用户输入的 `_audit_focus` 注入，空则注入"（用户未指定重点）"，审计任务段补充"用户特别关注"提示，共 7 个占位符）
  - phase_outline_final.txt：**终稿大纲生成**（阶段③.5），将最后一轮审计结果+原大纲+前10章前文+深度分析+推进速度一起发送给 AI 生成终稿 VolumeOutline；5 个占位符：`{{original_outline}}`/`{{audit_report}}`/`{{previous_chapters_text}}`/`{{deep_analysis}}`/`{{pacing_speed}}`；编排器 `_run_outline_final` 校验 chapters 长度 == chapter_count，失败降级保持审计后大纲
  - phase_chapter_outline.txt：基于卷大纲+当前章 ChapterPlan 产出场景级 Outline（3-7 个 Scene），格式与 phase_outline.txt 完全一致以保证兼容；章末钩子需与下一章衔接；新增 `{{previous_chapter_text}}`（紧邻上一章完整正文）占位符用于紧密衔接；Task 9 起新增 `{{lookback_chapters_text}}`（续写点前文，由 `_build_lookback_10_chapters_text` 注入插入点前 10 章，不再注入全量 `_lookback_chapters_text`）占位符；新增 `{{pacing_speed}}` 占位符（推进速度段，slow=同一场景1-2章/medium=一个场景半章/fast=按目前设定）；新增 `{{context_entries}}` 占位符（上下文条目段，由 `_build_context_entries_text` 注入，共 8 个占位符）；任务描述区强调当前章 ChapterPlan 优先级高于用户总指令 `{{user_input}}`
- 路径解析：`paths.get_volume_prompt_path(phase)` 镜像 `get_agent_prompt_path`，但限定 phase 取值（deep_analysis/volume_outline/outline_audit/outline_final/chapter_outline），非法值抛 ValueError
- 阶段提示词仍走 str.replace 宏替换（不用 MacroEngine/Jinja2），与单次 Agent 续写机制一致
- JSON 输出要求严格（只输出 JSON、不用 markdown 代码块、所有字段必须存在）
- 编排器 `novelforge/services/volume_orchestrator.py` 的 `VolumeOrchestrator` 镜像 `AgentOrchestrator` 的 QThread+asyncio 模式，独立类（不继承 AgentOrchestrator，通过组合持有 PromptAssembler，复用 post_process_content）：
  - `__init__` 参数镜像 AgentOrchestrator，但 config 类型为 `VolumeRunConfig`（替代 AgentRunConfig）
  - 信号：`phase_started(str)`/`phase_finished(str,object)`（卷级阶段 deep_analysis/volume_outline/outline_audit/outline_final）+ `chapter_started(int)`/`chapter_finished(int,object)`（章节级）+ `chunk_received`/`reasoning_received`/`checkpoint_reached`/`finished`/`error`/`auth_error`/`token_count` + `prompt_debug_requested(str,str)`（Task 9 调试模式：phase_name + messages JSON，UI 线程弹窗确认后调 `confirm_debug_prompt` 恢复）
  - `_async_run` 流程：①deep_analysis（non-stream+JSON，失败重试温度归零，再失败返回 None 降级；**改为基于 lookback chapters（插入点之前 `chapters[0..current_chapter_index]`）切分判断与发送，不再分析全文**；支持 token 切分：`analysis_chunk_tokens`>0 时按 lookback chapters 章节边界累积 token 切分，逐块调用 `_run_deep_analysis_single`，每块携带已有分析 JSON 增量补充，最终 `_merge_deep_analysis` 合并：字符串字段 base 空则取 new、列表字段拼接按 JSON 序列化去重）→ ②volume_outline（注入 DeepAnalysis JSON + lookback 文本 + pacing_speed + context_entries，校验 chapters 长度==chapter_count，超长截断/不足返回 None emit error 终止）→ **before_audit 检查点（可选，默认开启，emit checkpoint_reached 让用户输入需着重审计的部分，resume payload 为字符串存入 `self._audit_focus`，多轮审计共享同一焦点；空字符串/None 视为无重点）**→ ③outline_audit（可选，**多轮循环** `for round_idx in range(audit_rounds)`，每轮审计上一轮修订版，注入最近 10 章前文正文 + DeepAnalysis JSON + round_idx/total_rounds + audit_focus 供一致性审计，Task 9 起最近 10 章按 `_current_chapter_index` 回看 `[max(0, idx-9) .. idx]` 而非 `chapters[-10:]`，失败降级用原大纲作为 final_outline；**暂停点 after_audit 移到审计循环后**，显示最后一轮修订大纲供用户编辑）→ **③.5 outline_final（终稿大纲生成，输入=最后一轮审计结果+原大纲+前10章前文+深度分析+推进速度，`_run_outline_final` 产出终稿 VolumeOutline，失败降级保持审计后大纲）**→ ④逐章循环（每章：chapter_outline→writing→verify→revise；维护 `previous_chapters_text`（本卷已生成正文累积）与 `previous_chapter_text`（紧邻上一章正文，供下一章紧密衔接）两个变量；修订循环重写时调 `_run_chapter_writing` 传 `original_content=content` 让 LLM 参考当前已生成内容重写）
  - 单章写作/验证/修订逻辑在本类内重新实现（与 AgentOrchestrator 同模式，接受少量代码重复换取隔离性）：写作用 `format_outline` 格式化大纲+ContextEntry(position=before)注入+worldInfoBefore marker fallback+`stream_chat_completion` 流式；`_run_chapter_writing` 接收 `previous_chapter_text` 参数，非空时 insert `# 上一章正文（用于衔接）` system 消息到 `len(messages)-1` 位置（Task 9 起从 index 0 改为"最后一条消息之前"，避免把上一章正文塞到 system prompt 前面稀释 system 指令权重），并将 `prompt_assembler.assemble` 的 `user_input` 替换为本章 ChapterPlan 派生的生成要求（标题/摘要/剧情角色/关键事件/章节钩子，chapter_plan 为 None 时回退 `self.user_input`）；`_run_chapter_writing` 还接收 `revision_guidance` + `original_content` 两参数，修订指导非空时追加 user 消息，若 `original_content` 非空则把当前已生成内容拼到修订指导前一起发，让 LLM 基于现有内容重写而非从零续写；`_run_chapter_outline` 同样接收 `previous_chapter_text` 注入到 `{{previous_chapter_text}}` 占位符，macros 新增 `{{lookback_chapters_text}}` 注入 `_build_lookback_10_chapters_text()`（插入点前 10 章，不再注入全量 `_lookback_chapters_text`）+ `{{pacing_speed}}` 注入推进速度 + `{{context_entries}}` 注入 `_build_context_entries_text()`（自动提取的人物/地点/事件/风格/伏笔等，供细纲场景设计参考）；`_run_chapter_verify` 的 macros Task 9 起新增 `{{previous_chapters_text}}` 注入 `_build_lookback_10_chapters_text()`（前 10 章，与细纲/审计一致，不再注入全量 `_lookback_chapters_text`）供验证阶段参考前文；`_run_chapter_revise` macros 新增 `{{previous_chapters_text}}`（前 10 章，供修订指导参考前文一致性）+ `{{pacing_speed}}`（推进速度，让修订指导与节奏匹配）；验证/修订复用 `phase_verify.txt`/`phase_revise.txt`（用 `get_agent_prompt_path`）
  - 暂停点 after_deep_analysis/after_volume_outline/before_audit/after_audit：`_wait_for_resume` 镜像 AgentOrchestrator（asyncio.wait_for 轮询 + call_soon_threadsafe 线程安全唤醒），恢复后从 `_checkpoint_payload` 取编辑后的产物；after_deep_analysis/after_volume_outline/after_audit 用 CheckpointDialog 简单模式（不显示产物）提供 接受/编辑/取消，"编辑"仅关闭对话框并显示 VolumePanel"继续"按钮（不解析产物），用户在面板编辑产物后点"继续"由 `_on_volume_continue` 读取编辑版产物并 resume；**before_audit 用 CheckpointDialog 审计前模式（QPlainTextEdit + 确认/取消，无 edit 路径），确认后 resume payload 为用户输入字符串（可能为空），由 `_on_volume_checkpoint` 捕获 `dialog.get_result()` 的 result_payload 传给 orchestrator.resume()**（其他检查点 resume 传原产物 payload）
  - 停止机制：threading.Event + asyncio.Task.cancel 双重中断，逐章循环每轮检查 `_stop_event`
  - 深度分析 max_tokens 按深度调整：light=8000 / standard=20000 / thorough=50000 / exhaustive=不设上限(200000 近似)；**前文章节文本基于 lookback chapters（插入点之前 `chapters[0..current_chapter_index]`）注入，不再分析全文**；`analysis_chunk_tokens`>0 时按 lookback chapters 章节边界切分（`_split_chapters_by_tokens` 用 `count_text_tokens` 累积 token，不跨章切分），逐块调用 LLM 并增量携带已有分析（`# 已有分析内容（增量补充参考）` 段落追加到 system_prompt），最终合并为完整 DeepAnalysis
  - lookback 文本缓存（供卷大纲/章节细纲/验证/修订阶段使用）：`__init__` 中通过 `current_chapter.id` 在 `chapters` 列表中查找位置计算 `_current_chapter_index`（找不到为 -1，fallback 为全量）；`_build_lookback_chapters_text()` 返回 `chapters[0..current_chapter_index]` 拼接（找不到时 fallback 全量）；`_build_lookback_10_chapters_text()` 返回 `chapters[max(0,idx-9):idx+1]` 拼接（插入点前 10 章，供章节细纲 `{{lookback_chapters_text}}` 与终稿大纲 `{{previous_chapters_text}}` 注入）；`_async_run` 预构建两份文本缓存 `_chapters_text`（全文）与 `_lookback_chapters_text`（续写点前文，注入卷大纲 `{{chapters_text}}` 与验证的 `{{previous_chapters_text}}`）；`_run_deep_analysis_single` 的 macros 只含 `{{chapters_text}}`（早期 Task 5 曾含 `{{full_chapters_text}}`/`{{lookback_chapters_text}}` 两占位符，已移除合并为单个）；`_run_deep_analysis` 切分判断与发送均基于 lookback chapters（用户要求只分析插入点之前）；`_run_volume_outline` 的 `{{chapters_text}}` 注入 `_lookback_chapters_text`；`_build_context_entries_text()` 格式化 `self.context_entries` 为可读 Markdown（按 category 分组 characters/locations/events/style/plot_state/relationships/atmosphere/foreshadowing，order 升序，空列表或无 content 返回空字符串），注入卷大纲与章节细纲的 `{{context_entries}}` 占位符（审计与终稿阶段不注入）
  - Task 9 调试模式 + 审计章节范围修复 + 写作消息顺序优化：①`__init__` 新增 `debug_mode: bool=False`、`_debug_confirmed: asyncio.Event|None=None`、`_debug_confirmed_result: bool=False` 三字段（UI 线程设 `debug_mode=True` 启用）；`_async_run` 开头创建 `self._debug_confirmed = asyncio.Event()`；新增 `confirm_debug_prompt(confirmed)` 方法（UI 线程调，设结果并通过 `call_soon_threadsafe` 唤醒 Event）和 `_maybe_debug_prompt(messages, phase_name)` 协程（debug_mode=False 直接返回 True，否则 emit `prompt_debug_requested(phase_name, messages_json)` 并 `await _debug_confirmed.wait()` 返回用户确认结果）。②所有 7 个 LLM 调用方法（`_run_deep_analysis_single`/`_run_volume_outline`/`_run_outline_audit`/`_run_chapter_outline`/`_run_chapter_writing`/`_run_chapter_verify`/`_run_chapter_revise`）在 messages 组装完成、`for attempt in range(2)` 重试循环之前调 `_maybe_debug_prompt`，取消时返回方法签名对应的"空产物"（None / `("", "", [])` / `{}`）；`_run_chapter_writing` 的检查位于所有 messages 组装之后（previous_chapter_text 插入 + outline fallback + revision guidance 追加之后）、流式循环之前。③审计阶段 `_run_outline_audit` 的最近 10 章前文改用 `_current_chapter_index` 回看 `[max(0, idx-9) .. idx+1]` 区间，避免把卷续写过程中新追加的章节误算进审计前文。④`_run_chapter_writing` 的 `previous_chapter_text` 注入位置从 `messages.insert(0, ...)` 改为 `messages.insert(len(messages)-1, ...)`（插入到最后一条消息之前），避免上一章正文被塞到 system prompt 前面稀释 system 指令权重。⑤`_run_chapter_writing` 新增 `original_content: str=""` 参数，修订循环重写时由 `_async_run` 传入当前已生成的 `content`，让 LLM 基于现有内容重写而非从零续写。⑥`_run_chapter_outline` 的 macros 新增 `{{lookback_chapters_text}}`，`_run_chapter_verify` 的 macros 新增 `{{previous_chapters_text}}`，两者均注入 `_lookback_chapters_text` 供阶段参考前文。
  - 逐章循环中每章正文作为新 `Chapter` 追加到 `chapters` 列表供下一章前文；`previous_chapters_text` 累积本卷已生成正文
  - 最终拼接所有 `chapter_artifacts.content` 为完整卷正文，构建 `Continuation`（`created_by="volume"`，含 `volume_artifacts`），emit finished
  - `get_writing_messages()`/`get_writing_model()` 返回最后一章写作阶段的 messages 和 model（供历史日志）
  - 测试 `tests/test_volume_orchestrator.py` 覆盖 24 用例：深度分析成功/失败降级、卷大纲成功/失败/章节数不匹配、审计成功/失败降级、逐章循环 N=2、修订循环（单轮/上限）、暂停点触发/停止、停止机制、marker 存在/不存在、章节追加、get_writing_messages、审计阶段注入前文+深度分析（Task 7）、章节衔接（第 2 章注入第 1 章正文+第 2 章 ChapterPlan，Task 8）、深度分析 token 切分增量合并（Task 3：analysis_chunk_tokens=10 触发 2 块切分，验证多块调用+增量携带+合并字段）、Task 5 全文 vs 续写点前文区分（`_current_chapter_index` 计算/`_build_lookback_chapters_text` 截断/深度分析宏含 full+lookback 双文本/卷大纲用 lookback 文本）；FakeLLMClient 增加 `chat_messages_history` 捕获每次 chat_completion 的 messages 供宏注入断言
  - 测试 `tests/test_volume_prompts.py` 覆盖 4 模板存在性/占位符完整性（深度分析 10 占位符含 full/lookback 双文本、卷大纲前文标签改为 # 续写点前文内容）/深度参数注入/章节数约束/7 审计维度/get_volume_prompt_path 路径与非法抛错/str.replace 宏替换无残留
  - 测试 `tests/test_volume_e2e.py` 用合成 preset + 合成章节 mock LLM 跑 N=3 全流程（DeepAnalysis/VolumeOutline/OutlineAuditReport/3 章 ChapterArtifacts/Continuation 产物/phase_logs），含深度分析降级场景与修订循环场景
  - 测试 `tests/test_volume_ui.py` 覆盖卷续写及相关面板 UI 检查点（离屏 Qt 平台，模块级 QApplication fixture）：VolumePanel `set_presets`/`get_selected_preset_id` 预设选择与切换、`_analysis_chunk_tokens_combo` 联动 `get_config().analysis_chunk_tokens`（不限制=0/50k=50000/100k=100000）、ContinuationPanel `_user_input_edit` 高度约束（maxHeight 80/minHeight 60）、VolumePanel `show_continue_button`/`hide_continue_button` 显隐（用 `isHidden()` 断言显式 show/hide 状态）与产物 tab 切换（after_deep_analysis→tab0/after_volume_outline→tab1）、AgentPanel `show_continue_button`/`hide_continue_button` 显隐
  - 测试 `tests/test_checkpoint_dialog.py` 覆盖 CheckpointDialog 大纲模式（after_outline）编辑按钮回调：调用 `_on_edit()` 后 `get_result() == ("edit", None)`
- UI 面板 `novelforge/ui/volume_panel.py` 的 `VolumePanel` 镜像 `AgentPanel` 结构与代码风格（QWidget/信号/配置读写/QGroupBox 分组/`set_label_state`+`_OBJ_PHASE_*` 进度样式/`from __future__ import annotations`+logging+中文文档字符串）：
  - 整体用 QScrollArea 包裹（窄高面板可滚动，避免内容挤压）；**顶部固定整卷进度条** `QProgressBar#volumeProgressBar`（高 24px，不随滚动，始终可见，初始文本"准备中..."）；配置区用 QGridLayout 横排五行（预设选择 `_preset_combo` QComboBox(AdjustToContents+Expanding，默认"default") / 章节数+分析深度 / 条目上限+每章字数 `_target_words_spin` QSpinBox(500-20000,步进500,默认2000) / 切分 tokens `_analysis_chunk_tokens_combo` QComboBox(不限制/50k/100k/250k/500k，默认不限制=0，`parse_token_limit` 解析文本为数值) / 推进速度 `_pacing_speed_combo` QComboBox(缓速slow/中速medium/快速fast，默认中速)+审计轮次 `_audit_rounds_spin` QSpinBox(1-3,默认1)），暂停点 4 复选框横排并入配置组底部（分析后/大纲后/审计前[默认开启]/审计后，不再单独成组）；审计与逐章设置组：审计/逐章验证/逐章修订 3 开关横排 + 7 维度勾选组 + 每章最大修订轮次 SpinBox(1-3,默认1) 单独一行
  - 两层进度 + 顶部进度条：卷阶段 4 标签(深度分析/卷大纲/审计/逐章)+当前章节"第 i/N 章"+4 步标签(细纲/写作/验证/修订)；`update_volume_phase_progress`/`update_chapter_progress` 镜像 `update_phase_progress`，**两者内部均调用 `update_volume_progress(percent, text)` 同步顶部整卷进度条**；权重：deep_analysis 10% / volume_outline 20% / audit 30% / chapter 30-100%（章节加权：进行中 `30+int((n-1)/total*70)`、完成 `30+int(n/total*70)`）；文本格式如"深度分析中... 10%"/"第 3/5 章 写作 44%"
  - 产物查看：QTabWidget **五标签页**切换（深度分析预览可编辑 / 卷大纲预览可编辑,终稿生成后 `update_final_outline` 更新为终稿 / **审计报告**上半 `_audit_report_edit` readonly 显示审计维度/分数/问题/建议/总体评估（多轮支持，`_format_audit_report(report,round_idx)` 格式化）+下半 `_revised_outline_edit` readonly 显示修订版大纲预览，审计阶段 `update_audit_report(report,round_idx)` 更新 / 各章产物折叠列表 QScrollArea 内动态 `add_chapter_artifacts` 添加可折叠 QGroupBox / **当前章节正文流式区** `QPlainTextEdit#volumeChapterStream` readonly min-height 200，承接卷续写写作流式增量，避免路由到右侧输出面板）；Tab 索引：0=深度分析/1=卷大纲/2=审计报告/3=各章产物/4=当前章节正文；流式方法：`append_chapter_chunk`(moveCursor End + insertPlainText 真流式无额外换行)/`start_chapter_streaming`(清空+切 tab via `indexOf(_chapter_stream_tab)`+placeholder)/`reset_streaming`/`set_full_volume_content`(完成后显示完整卷正文)
  - 信号 `config_changed(object)`/`resume(object)`/`cancel_checkpoint()`/`continue_requested(str)`（检查点选择编辑后点击"继续"恢复）；`get_config()` 返回 `VolumeRunConfig`；`set_presets(list[dict], default_id)`/`get_selected_preset_id()` 管理卷模式独立预设（`_preset_combo`，clear+addItem+`select_combo_by_id` 选中 default_id，空时返回"default"）；`get_edited_deep_analysis`/`get_edited_volume_outline` 宽松解析回对象(失败返回 None)；`show_continue_button(checkpoint_name)`/`hide_continue_button()` 显隐顶部"继续"按钮并切换到对应产物 tab（深度分析→tab0/卷大纲→tab1/审计报告→tab2）；`switch_to_tab(phase)` 按阶段名切换产物 Tab（deep_analysis→tab0/volume_outline+outline_final→tab1/outline_audit→tab2，供 `_on_volume_phase_finished` 自动切 Tab 复用）；`reset()` 清空产物+流式区+审计报告区+重置进度（含进度条）并 `hide_continue_button()`；`_on_config_changed` 更新联动(审计关闭禁用维度组、逐章验证关闭禁用修订相关)并 emit；`_preset_combo`/`_analysis_chunk_tokens_combo` 的 currentIndexChanged 均接入 `_on_config_changed`；继续按钮 `_continue_btn`（`#primaryBtn`）置于顶部进度条与滚动区之间
  - 不用 `setStyleSheet`，样式由 `setObjectName`+全局 QSS 接管；DeepAnalysis/VolumeOutline 的格式化与宽松解析内置于本模块（outline_serializer 未覆盖此类模型）；`QProgressBar#volumeProgressBar` 样式定义于 `light.qss`/`dark.qss`（亮色 chunk `#007aff`、暗色 chunk `#0a84ff`，圆角 5-6px，文本居中白色加粗）
- MainWindow 三模式接线（`novelforge/ui/main_window.py` + `continuation_panel.py`）：
  - `ContinuationPanel._mode_combo` 三项：单次续写(single)/智能续写(agent)/卷续写(volume)；`_volume_panel = VolumePanel()` 实例由 `show_volume_panel` 控制显隐；`volume_panel` 属性 + `get_volume_panel()`/`get_volume_config()` 方法；`ContinuationPanel.set_presets` 末尾同步调用 `_volume_panel.set_presets(presets, default_id)`（卷模式独立预设，单次模式 combo 仍保留原 current_id/default_id 逻辑）；用户输入框 `_user_input_edit` maxHeight 50/minHeight 36（紧凑化，QGroupBox 内边距 2px/spacing 2px）
  - **卷模式显隐右侧输出面板**：`ContinuationPanel` 新增 `output_panel_visibility_requested(bool)` 信号，`show_volume_panel(visible)` 末尾 `emit(not visible)`（卷模式开启→emit False 隐藏输出面板，卷模式关闭→emit True 恢复）；MainWindow `_on_output_panel_visibility_requested(visible)` 调 `_splitter.widget(4).setVisible(visible)`，visible 时 `setSizes(DEFAULT_PANEL_SIZES)` 恢复默认，非 visible 时把第 4 栏尺寸并入第 3 栏（卷控制面板）让 VolumePanel 产物查看器获得更大空间；三模式切换均经 `show_volume_panel` 触发该信号，保证 single↔volume/agent↔volume 互切时输出面板正确显隐
  - `_on_start_continuation_routed` 按 `get_mode()` 分发到单次/Agent/卷续写流程；Ctrl+Enter 与重写按钮均经此路由
  - `_on_start_volume_continuation` 镜像 `_on_start_agent_continuation`：验证 API 配置→获取 VolumeRunConfig→检查上下文条目(无则提示先提取)→创建 `VolumeOrchestrator`→连接信号→start()；预设 ID 从 `continuation_panel.volume_panel.get_selected_preset_id()` 读取（卷模式独立预设，不再从 params 取 preset_id）；**卷模式不调用 `continuation_panel.start_streaming()`**（卷模式不用右侧输出面板的流式状态，流式由 VolumePanel 自身流式区承接），仅 `chapter_editor.set_streaming_locked(True)` + `volume_panel.reset()`
  - 信号接线：`phase_started`→`_on_volume_phase_started`(仅更新卷阶段进度，不写输出面板)；`phase_finished`→`_on_volume_phase_finished`(更新深度分析/卷大纲/审计产物预览+进度，不写输出面板，避免 deep_analysis 阶段输出面板跳动；**outline_audit 阶段调 `update_audit_report(artifact, round_idx)` 显示审计报告+修订版大纲到审计报告 Tab（不再覆盖卷大纲 Tab）**，多轮审计 round_idx 取 `len(panel._audit_reports)` 并 append；**outline_final 阶段调 `update_final_outline(artifact)` 更新卷大纲 Tab 为终稿**)；`chapter_started`→`_on_volume_chapter_started`(调 `volume_panel.start_chapter_streaming` 清空流式区+切 tab，再更新章节进度)；`chapter_finished`→`_on_volume_chapter_finished`(添加章节产物+标记完成)；**`chunk_received`→`volume_panel.append_chapter_chunk`**（路由到 VolumePanel 流式区，不再走 `continuation_panel.append_chunk`）；`token_count` 复用现有 label 方法；`reasoning_received` 信号在 service 层保留但 UI 端不再连接（推理内容框已移除，推理内容仍通过 finished 回调落库）；`checkpoint_reached`→`_on_volume_checkpoint`(CheckpointDialog 简单模式三选一：接受/编辑/取消；接受=resume(payload)，编辑=关闭对话框并调 `volume_panel.show_continue_button` 显隐"继续"按钮不 resume，取消=stop()；`volume_panel.continue_requested`→`_on_volume_continue` 一次性连接在 `_connect_signals`，点击继续时读 `_get_edited_volume_checkpoint_payload` 后 `resume(edited)`，resume 设置 `_checkpoint_payload` 并 `_resume_event.set` 唤醒 `_wait_for_resume`)；`finished`→`_on_volume_continuation_finished`(**每章作为新章节插入当前章节之后**：从 `continuation.volume_artifacts.chapter_artifacts` 拆分每章正文，先 将当前章节之后的所有章节 index 后移 N 位（N=新章节数），再为每章创建新 `Chapter` 插入到 `current_index+1..current_index+N` 位置，标题从 `final_outline.chapters` 按 `chapter_index` 匹配获取，无则用 `第{index}章`；不再创建 swipe 保存到当前章节；调 `_refresh_chapter_list()` 刷新列表；容器 Continuation 仍携带完整卷正文与 volume_artifacts 用于历史日志；仍调 `volume_panel.set_full_volume_content(continuation.content)` 显示完整卷正文 + `stop_streaming()`/`set_streaming_locked(False)`/记录历史日志)；`error`/`auth_error` 复用现有方法；Agent 检查点镜像：`agent_panel.continue_requested`→`_on_agent_continue` 一次性连接，`_on_agent_checkpoint` 大纲模式 "edit"=show_continue_button 不 resume（"accept"=resume(payload)/"cancel"=stop()）
  - `_map_volume_phase` 将 orchestrator 阶段名(outline_audit)映射到面板阶段名(audit)
  - 历史日志兼容：启动时设 `_continuation_model=model`/`_continuation_prompt_messages=[]`；完成时用 `get_writing_messages()` 填充后再 `_record_history`
  - `_on_stop_continuation` 三模式统一处理：worker/agent_orchestrator/volume_orchestrator 任一运行中均调 stop()
  - 暂停点统一使用 CheckpointDialog：大纲模式（after_outline）含可编辑文本区，简单模式（卷续写 after_deep_analysis/after_volume_outline/after_audit）仅提示信息无编辑区（产物已在 VolumePanel 内置可编辑预览区展示）；两模式 actions 均为 接受/编辑/取消，"编辑"只关闭对话框并显示面板"继续"按钮（`_continue_btn#primaryBtn`），不解析产物也不立即 resume，用户在面板编辑后点"继续"由 `_on_*_continue` 读取编辑版产物再 resume
  - **暂停点暂停修复**：`VolumeOrchestrator._wait_for_resume` 移除"检查 `_checkpoint_payload is not None` 提前返回"路径（该路径会因上次运行残留 stale payload 触发立即返回，导致用户编辑内容被忽略、直接用原产物继续）；改为仅在 `resume()` 设置 `_resume_event` 后才读取 payload；`_async_run` 开头额外 `self._checkpoint_payload = None` + `self._resume_event.clear()` 清理可能残留状态
  - **阶段完成自动切 Tab**：`_on_volume_phase_finished` 在更新产物预览后调 `volume_panel.switch_to_tab(phase)` 自动切换产物查看 Tab（deep_analysis→tab0/volume_outline+outline_final→tab1/outline_audit→tab2），便于用户即时看到刚生成的产物；`VolumePanel.switch_to_tab(phase)` 镜像 `show_continue_button` 的 Tab 切换逻辑但独立成方法以便 phase_finished 复用
  - **调试模式 UI**：MainWindow 菜单栏新增"调试(&D)"菜单，含可勾选 `调试模式` QAction（`_debug_mode_action`），状态存于 `self._debug_mode`（默认 False）；`_on_debug_mode_toggled(checked)` 实时同步 `_debug_mode` 并更新运行中的 `_volume_orchestrator`/`_agent_orchestrator` 的 `debug_mode` 属性；创建 orchestrator 后立即 `orchestrator.debug_mode = self._debug_mode` 同步初始状态，并连接 `prompt_debug_requested(str,str)` 信号到 `_on_prompt_debug_requested`；该槽解析 messages JSON 后弹出 `DebugPromptDialog`（显示阶段名+完整 messages JSON 只读文本+"发送"/"取消"按钮），用户确认后调运行中 orchestrator 的 `confirm_debug_prompt(bool)` 回传结果（取消则跳过本次 LLM 调用）
  - **滚轮事件过滤器**：新增 `novelforge/ui/wheel_filter.py` 的 `WheelEventFilter(QObject)`，安装到 QComboBox/QSpinBox/QDoubleSpinBox 后，未聚焦时拦截 QWheelEvent 并转发给父级 `QAbstractScrollArea.viewport()` 滚动页面（避免误滚动改值），聚焦时放行正常响应；ContinuationPanel 安装到 `_mode_combo`/`_preset_combo`/`_endpoint_combo`/`_model_combo`/`_temp_spin`/`_lookback_spin`，VolumePanel 安装到 `_preset_combo`/`_analysis_depth_combo`/`_analysis_chunk_tokens_combo`/`_chapter_count_spin`/`_max_entries_spin`/`_max_revise_spin`，AgentPanel 安装到 `_max_revise_spin`；过滤器实例存为面板 `_wheel_filter` 属性（parent=面板自身，生命周期随面板）
  - **用户输入框紧凑化**：`ContinuationPanel._user_input_edit` 的 `user_input_group` QVBoxLayout 设置 `setContentsMargins(2,2,2,2)` + `setSpacing(2)` 压缩内边距；后续改造移除 maxHeight 固定约束（见"续写控制面板布局重排"），保留 minHeight=36 作为拖动底线
  - **续写控制面板布局重排**：`ContinuationPanel._setup_ui` 引入垂直 `QSplitter`（`_content_splitter`）重构布局——顶部 `mode_group`（续写模式下拉框，固定）+ 中部 QSplitter（伸缩因子 1）+ 底部 `btn_layout`（按钮区，固定）；QSplitter 上半 `_mode_content_widget` 容纳 `_config_group`/`_agent_panel`/`_volume_panel`（三者 `addWidget` 均传伸缩因子 1，确保可见面板撑满中间空间），下半 `_user_input_group`（用户输入框，伸缩因子 0 默认小、可拖动把手调整高度）；`setChildrenCollapsible(False)` 防止拖到消失，`setHandleWidth(6)` 加宽把手，初始 sizes `[400, 60]`；移除 `_user_input_edit.setMaximumHeight(50)`，保留 `setMinimumHeight(36)`；`user_input_group` 改存为 `self._user_input_group` 实例属性；测试 `tests/test_volume_ui.py::TestContinuationPanelInputHeight` 更新——删除 `maxHeight==50` 断言，新增 `maxHeight==16777215`（QWIDGETSIZE_MAX）断言（验证无固定上限），保留 `minHeight==36` 断言
  - **续写控制面板显示框撑满修复**：上一轮 `mode_content_layout` 末尾的 `addStretch(0)` 会吸收剩余空间导致模式面板按 sizeHint 显示、下方留白（"显示框被截取到中间"）；修复改为三个模式面板 `addWidget` 均传伸缩因子 1 + 移除末尾 `addStretch(0)`，使当前可见面板（卷模式→VolumePanel / 智能续写→AgentPanel / 单次→配置区）撑满从【续写模式】下拉框下方到【用户输入】框上方的整个空间；三种模式互斥（`hide()` 不参与布局），故三个面板都传伸缩因子 1 即可保证任意模式切换后可见面板撑满，无需动态调整
  - **产物编辑区高度增大**：`VolumePanel._deep_analysis_edit`/`_volume_outline_edit` 的 `setMinimumHeight` 从 100 增至 200，给可编辑产物预览区更多展示空间
  - **删除 swipe 信息标签与推理内容框**：`ContinuationPanel` 移除 `_swipe_info_label`（QLabel "无续写版本"）、`_reasoning_group`/`_reasoning_edit`（可折叠推理内容框）、`_reasoning_buffer`（半废弃缓冲）；新增两信号 `swipe_info_requested(str)`/`toast_requested(str)` 解耦——swipe 元信息（模型/状态/字数/创建时间/已接受）与限速提示（原 `show_toast`，被 `rate_limit_warning` 复用）改路由到 MainWindow 状态栏 `_save_status_label`；`_set_swipe_info` 改为 emit `swipe_info_requested`，`show_toast` 改为 emit `toast_requested`；MainWindow 新增 `_on_toast_requested`（写状态栏 + `QTimer.singleShot(3000, _restore_status_after_toast)`，还原为简化 swipe 元信息或"就绪"）、`_restore_status_after_toast`；`append_reasoning` 改为 no-op（保留签名兼容）；service 层 3 个 `reasoning_received` 信号定义+emit 保留（持久化不依赖此信号，仍通过 finished 回调落库），但 main_window.py 三处 `reasoning_received.connect(append_reasoning)` 删除（disconnect 调用保留在 try/except 中无副作用）；清理 `continuation_panel.py` 未使用 import（`datetime`/`QLabel`/`QSplitter`/`QTextEdit`/`set_label_state`）；`_reasoning_group` 的 `addWidget(..., 1)` 伸缩因子删除后续写控制面板高度自然缩短
  - **卷深度分析默认启用 token 切分**：`VolumePanel._analysis_chunk_tokens_combo` 默认选项从"不限制"（index 0，`analysis_chunk_tokens=0` 不切分）改为"100k"（index 2，100000 tokens/块）；切分逻辑早已实现（`_split_chapters_by_tokens` 按 lookback 章节边界累积 token 切分不跨章 + `_run_deep_analysis_single` 逐块调用携带 `existing_analysis` 增量 + `_merge_deep_analysis` 合并：字符串字段 base 空取 new、列表字段拼接去重），但原默认"不限制"导致用户不主动切换时全量发送未触发切分；改默认后前文超 100k 自动切分逐块分析，不足 100k 时 `chapter_chunks` 长度为 1 走单次调用无副作用；Tooltip 同步更新说明"默认 100k"
  - **删除章节 reindex 内容丢失修复**：`ChapterService._reindex_after` 原用 `list_chapters`（只返回元数据，`content=""` 默认值）获取章节，再对 index 变化的章节调 `save_chapter`，而 `save_chapter` 会用空 content 原子覆盖正文文件（`atomic_write_file`），导致删除非末尾章节时其后所有章节正文被清空；同样 bug 影响 `split_chapter`/`merge_chapter_with_next`/`undo_operation`（均调用 `_reindex_after`/`_reindex_all`）；修复：新增 `Storage.update_chapter_index(chapter_id, new_index)` + `StorageService.update_chapter_index` 只执行 `UPDATE chapters SET "index"=? WHERE id=?` 不写文件，`_reindex_after` 改用此方法替代 `save_chapter`；新增测试 `test_delete_chapter_preserves_remaining_content` 验证删除后剩余章节正文完整保留
  - **深度分析切分模式全文注入修复**：`_run_deep_analysis_single` 的 macros 原将 `{{full_chapters_text}}` 设为 `self._chapters_text`（完整全文）、`{{lookback_chapters_text}}` 设为 `self._lookback_chapters_text`（完整前文），即使切分模式下 `{{chapters_text}}` 被切分为块文本，模板中使用的全文占位符仍注入完整全文，导致每块提示词都包含全文（用户案例：选 250k 切分但发送 1169743 tokens 超过 1048565 限制）；修复：三个占位符 `{{chapters_text}}`/`{{full_chapters_text}}`/`{{lookback_chapters_text}}` 统一使用传入的 `chapters_text` 参数（不切分时为全文，切分时为块文本），确保切分模式下每块只含块文本不超长；测试 `test_deep_analysis_macros_include_full_and_lookback` 断言更新为每章内容出现 2 次（两个占位符都注入相同全文）

## 代码风格规范

- 使用 `from __future__ import annotations` 启用延迟类型注解
- 日志使用 `logging` 模块，logger 名称为模块路径
- 异常处理：UI 层捕获并提示用户，服务层记录日志并返回错误状态
- 中文注释和文档字符串，技术术语保留英文
- 信号命名：过去式或名词（如 `chunk_received`、`entries_changed`）
- **reindex 禁止用 `save_chapter`**：`list_chapters` 返回的 Chapter 对象 `content=""`（只含元数据），`save_chapter` 会用空 content 覆盖正文文件。reindex 操作必须使用 `update_chapter_index`（只更新 SQL index 列，不写文件）
- **切分模式下所有文本占位符必须使用切分后的块文本**：不能注入完整全文，否则切分形同虚设。`_run_deep_analysis_single` 的 `{{chapters_text}}`/`{{full_chapters_text}}`/`{{lookback_chapters_text}}` 统一使用传入的 `chapters_text` 参数

## 测试要求

- 测试文件位于 `tests/` 目录，命名 `test_*.py`
- 使用 pytest 运行：`python -m pytest tests/ -q`
- E2E 测试使用真实预设文件（如 `TGbreak😺V3.1.1.json`）和小说文件
- UI 测试需排除环境缺失的组件：`--ignore=tests/test_m5_polish.py -k "not TestUIComponents"`

## 修改后必须更新

每次修改项目代码后，请检查并更新本文件的以下部分：
1. **架构分层**：新增/删除/重命名模块时更新目录树
2. **关键设计决策**：新增重要设计决策时添加条目
3. **UI 布局规范**：修改面板布局时更新布局描述
4. **技术栈**：新增依赖时更新
