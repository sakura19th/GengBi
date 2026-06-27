# NovelForge 项目约束文件

> **重要**：本文件是项目的核心约束文档。每次修改项目代码后，必须读取并即时更新本文件，确保文档与代码保持同步。

## 项目概述

NovelForge 是一个 SillyTavern (ST) 兼容的小说续写工具，提供从 TXT 导入、章节管理、上下文提取、提示词组装到 LLM 流式续写的完整工作流。

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
│   └── agent.py         # Agent 多阶段续写数据模型
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
│   ├── llm_client.py         # LLM 客户端（流式 + 非流式）
│   ├── preset_service.py     # 预设管理（导入/导出/启用/禁用）
│   ├── regex_service.py      # 正则脚本管理（global/scoped/preset）
│   ├── importer.py           # TXT 导入与章节拆分
│   ├── exporter.py           # 导出（TXT/Markdown/备份）
│   ├── async_runner.py       # 后台事件循环运行器（单例）
│   └── storage_service.py    # 存储服务（项目/章节/续写 CRUD）
├── ui/              # UI 组件（PySide6）
│   ├── main_window.py           # 主窗口（5 栏 QSplitter 布局 + 主题管理）
│   ├── continuation_panel.py    # 续写控制面板（流式布局按钮区）
│   ├── agent_panel.py           # Agent 控制面板（阶段开关/暂停点/进度/产物）
│   ├── checkpoint_dialog.py     # 检查点对话框（大纲/验证暂停）
│   ├── context_preview_panel.py # 上下文提取预览面板（流式布局操作行）
│   ├── flow_layout.py           # QFlowLayout 流式布局（窄屏自动换行）
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
- 续写模式切换：单次续写 / 智能续写（多阶段 Agent）
- 智能模式显示 AgentPanel（阶段开关、暂停点、max_revise_rounds、进度指示器、产物查看器）
- 单次模式显示原有续写参数区（温度/字数/Token/回溯章节数）
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
- 暂停点（checkpoint）：asyncio.wait_for 轮询 + call_soon_threadsafe 线程安全恢复
- JSON 解析失败重试一次（温度归零），再失败跳过该阶段（优雅降级）
- 修订循环：critique.passed=False 时重跑写作阶段，rounds 不超过 max_revise_rounds
- agent 产物持久化到 Continuation.agent_artifacts，随 swipe 保存到 SQLite
- SQLite 迁移幂等：PRAGMA table_info 检测列存在性再 ALTER TABLE ADD COLUMN

## 代码风格规范

- 使用 `from __future__ import annotations` 启用延迟类型注解
- 日志使用 `logging` 模块，logger 名称为模块路径
- 异常处理：UI 层捕获并提示用户，服务层记录日志并返回错误状态
- 中文注释和文档字符串，技术术语保留英文
- 信号命名：过去式或名词（如 `chunk_received`、`entries_changed`）

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
