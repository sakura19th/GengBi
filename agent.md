# 赓笔 (GengBi) 项目约束文件

> **重要**：本文件是项目的核心约束文档。每次修改项目代码后，必须读取并即时更新本文件，确保文档与代码保持同步。

## 项目概述

赓笔 (GengBi) 是一个 SillyTavern (ST) 兼容的小说续写工具，提供从 TXT 导入、章节管理、上下文提取、提示词组装到 LLM 流式续写的完整工作流。

**当前版本：v0.2.7**（定义于 `novelforge/__init__.py` 的 `__version__`，由"关于"对话框引用，README 顶部同步标注）

**版本更新记录**：维护在 `README.md` 的"更新记录"章节，按版本倒序排列，每次发版须追加新版本小节。

## 技术栈

- Python 3.11+ / PySide6 (Qt6) / pydantic v2 / aiohttp（LLM 流式调用）/ aiosqlite（异步 SQLite）+ JSON 文件 / pytest

## 架构分层

```
novelforge/
├── models/          # 数据模型（pydantic v2）
│   ├── chapter.py        # Chapter、Continuation（accept=promote_continuation_to_chapter 提升为新章节插入当前章之后；Continuation.highlights 输出栏高亮持久化；Chapter.protagonist_profile 主角形象档案持久化）
│   ├── context.py        # ContextEntry（条目级 enabled 开关控制注入）、VALID_CATEGORIES/POSITIONS/ROLES
│   ├── preset.py         # WritingPreset、Prompt、PromptOrderEntry
│   ├── project.py        # Project、NovelProfile（Project.world_ontology 世界观底层；Project.custom_audit_rules 自定义设定全局共享）
│   ├── regex.py          # RegexScript、PLACEMENT_* 常量
│   ├── custom_audit_rule.py  # CustomAuditRule（id/title/raw_input/requirement/audit_criteria/severity/created_at）
│   ├── agent.py          # 续写流程共享模型（Outline/Scene/CritiqueReport/CritiqueIssue + VALID_CRITIQUE_* 16 维度常量）
│   └── volume.py         # 卷级多章节续写模型（DeepAnalysis/VolumeOutline/VolumeArtifacts/VolumeRunConfig；DEFAULT_AUDIT_DIMENSIONS 13 维度）
├── core/            # 核心逻辑（无 UI 依赖）
│   ├── prompt_assembler.py  # 提示词组装（三阶段：排序→注入→裁剪；worldInfoBefore/After 按 category 分组 Markdown；assemble 接收 world_ontology/protagonist_profile/skip_history/custom_audit_rules）
│   ├── regex_engine.py      # ST 兼容正则引擎 + strip_html_tags
│   ├── template_engine.py   # Jinja2 沙箱（双白名单函数防 SSTI，render_pre_send/render_post_receive）
│   ├── token_counter.py     # Token 计数（tiktoken 优先，回退估算）
│   ├── macros.py            # ST 宏引擎（{{user}}、{{char}} 等 11 内置宏 + setvar/getvar 四作用域）
│   ├── variable_store.py    # 变量存储（global/project/chapter/cache 四作用域）
│   ├── json_utils.py        # JSON 解析工具
│   ├── config.py            # 配置管理（API 端点、密钥加密、flow_endpoints 流程端点映射）
│   └── storage.py           # SQLite 异步存储层（外键 CASCADE；列迁移函数；list_chapters 检测 DB 缺章自动从磁盘重建）
├── services/        # 业务服务
│   ├── context_extractor.py     # 上下文提取（多批次 token 拆分 + 【信息汇总】合并；主角形象独立链路 extract_protagonist_streaming，独立缓存 key 前缀 protagonist:）
│   ├── ontology_extractor.py    # 世界观底层提取（WorldOntology 7 大维度；镜像 ContextExtractor 三大机制：拆批/增量合并/语义整合）
│   ├── continuation_worker.py   # QThread + asyncio 续写 worker
│   ├── audit_worker.py          # 单章续写审计 worker（流式 stream_chat_completion，低温稳定输出）
│   ├── custom_audit_rule_service.py  # 自定义设定结构化解析（parse_rule_streaming，AI 结合世界观+上下文结构化为 requirement+audit_criteria）
│   ├── volume_orchestrator.py   # 卷级多章节续写编排器（QThread+asyncio，7 阶段流程 + 暂停点 + 调试模式）
│   ├── llm_client.py            # LLM 客户端（流式 + 非流式）
│   ├── preset_service.py        # 预设管理（reset_default_preset 同步内置最新版）
│   ├── regex_service.py         # 正则脚本管理（global/scoped/preset）
│   ├── importer.py              # TXT 导入与章节拆分
│   ├── exporter.py              # 导出（TXT/Markdown/备份 zip，含 ZIP slip 防护）
│   ├── async_runner.py          # 后台事件循环运行器（单例）
│   ├── jailbreak_provider.py     # 流程破限文本提供器（加载 jb_{flow}.txt 按 ### LOW/MID/HIGH ### 分段，4 等级 off/low/mid/high 返回文本；文件缓存）
│   └── storage_service.py       # 存储服务（项目/章节/续写 CRUD；update_chapter_index 仅更新 index 列不写文件）
├── ui/              # UI 组件（PySide6）
│   ├── main_window.py           # 主窗口（5 栏 QSplitter + 主题管理 + 调试菜单；ont/protagonist/custom_rule 提取信号处理；续写/审计/重写三模式接线；章节切换状态保留 7 缓冲字段）
│   ├── continuation_panel.py    # 续写控制面板（三模式下拉；输出框右键 4 色高亮 + 备注；highlights_changed 信号持久化）
│   ├── volume_panel.py          # 卷续写控制面板（配置/两层进度/五 Tab 产物查看/流式区）
│   ├── context_preview_panel.py # 上下文提取预览面板（ontology/protagonist/custom_rule 三类提取按钮互斥 + 流式展示）
│   ├── chapter_list.py          # 章节列表（虚拟滚动/搜索/右键菜单；当前选中章节持续高亮——ChapterHighlightDelegate 自定义 QStyledItemDelegate 在 paint() 中 fillRect 绕开 QSS 选中态覆盖）
│   ├── chapter_editor.py        # 章节预览/编辑（自动保存/undo/拆分；流式锁定）
│   ├── audit_dialog.py          # 单章审计对话框（流式→可编辑→采纳）
│   ├── custom_rule_dialog.py    # 自定义设定对话框（输入/查看+删除）
│   ├── checkpoint_dialog.py     # 卷续写暂停点对话框（接受/编辑/取消）
│   ├── chapter_confirm_dialog.py # 每章后暂停点（通过/不通过+反馈/取消）
│   ├── artifact_detail_dialog.py # 阶段产物完整内容查看
│   ├── debug_prompt_dialog.py   # 调试提示词预览
│   ├── preset_manager.py        # 预设管理器（7 按钮；6 生成参数含 reasoning_effort 5 档）
│   ├── regex_manager.py         # 正则管理器（内联勾选即时持久化）
│   ├── worldbook_manager.py     # 世界书管理器（条目级 enabled 开关）
│   ├── settings_dialog.py       # 设置对话框（API 端点管理；reasoning_effort 7 档）
│   ├── flow_endpoint_dialog.py  # 流程端点配置（8 流程端点映射 + 6 非正文流程破限等级下拉 off/low/mid/high/custom + 自定义文本编辑入口）
│   ├── jailbreak_custom_dialog.py # 自定义破限文本编辑对话框（QPlainTextEdit + 确定/取消）
│   ├── font_settings.py / history_panel.py / project_panel.py / template_editor.py / worldbook_panel.py / dialogs.py / flow_layout.py / wheel_filter.py
│   └── ...
└── resources/       # 资源文件
    ├── defaults/
    │   ├── default_preset.json     # 默认写作预设（20 条分层模块：main/抗八股/抗重复/字数/文风互斥/推进互斥/思维链/破限层 4 套互斥+助手预填充/Marker；破限默认全关；nf_jb_* 已前置到 main 之前定调；nf_jb_high 含 NSFW 指引覆盖原 nf_nsfw）
    │   ├── jailbreaks/                # 非正文流程破限模板（6 个 jb_*.txt，每文件含 ### LOW/MID/HIGH ### 三档）
    │   ├── default_regex_scripts.json  # 4 条默认正则（思维链隐藏/八股抹除/破折号规范/空行清理）
    │   ├── extract_prompt.txt / extract_merge_prompt.txt  # 上下文提取 + 汇总环节
    │   └── agent/                   # 续写阶段提示词模板（phase_*.txt）
    │       ├── phase_verify.txt          # 16 维度验证（含 4 个一票否决 + rigid_ai_text 严格给分）
    │       ├── phase_custom_rule_parse.txt # 自定义设定结构化解析
    │       ├── phase_revise.txt / phase_chapter_rewrite.txt / phase_audit_rewrite.txt  # 修订/重写（audit_rewrite 为统一模板取代 chapter_rewrite）
    │       ├── phase_deep_analysis.txt / phase_deep_analysis_merge.txt  # 深度分析 + 切分汇总
    │       ├── phase_volume_outline.txt / phase_outline_audit.txt / phase_outline_final.txt  # 卷大纲/审计/终稿
    │       ├── phase_chapter_outline.txt  # 单章细纲
    │       ├── phase_single_audit.txt     # 单章审计（8 维度）
    │       └── phase_rewrite_analysis.txt # 重写当前章节需求分析
    └── themes/          # QSS 主题（light/dark，Apple HIG 风格）
```

## 关键设计决策

### 1. ST 兼容性

- 预设格式兼容 SillyTavern 的 `prompts` + `prompt_order` + `extensions.regex_scripts`
- 正则脚本支持 ST 的 `findRegex`（`/pattern/flags`）、`trimStrings`、`placement`（1=USER_INPUT/2=AI_OUTPUT/5=WORLD_INFO）
- 宏系统兼容 ST 的 `{{user}}`/`{{char}}`/`{{setvar::name::value}}`/`{{getvar::name}}`
- 世界书条目级开关：`ContextEntry.enabled` 与 ST `disable` 反向映射；正则 `RegexScript.disabled` 与 ST 同向

### 2. QThread + asyncio 桥接

- Worker（ContinuationWorker/AuditWorker/VolumeOrchestrator）继承 QThread，`run()` 创建独立 asyncio 事件循环
- 流式 chunk 通过 Qt 信号跨线程推送至 UI
- `AsyncLoopRunner` 单例提供持久后台事件循环供同步 UI 代码提交协程

### 3. 提取与续写解耦

- 上下文提取是独立步骤：用户先提取，确认结果后再续写；提取用流式不阻塞 UI
- **多批次【信息汇总】**：前文超 token_limit 按章节边界拆批，每批独立全量提取；batch_count>1 触发 LLM 合并去重，失败降级 best-effort
- **批次级自动重试**：每批次失败立即重试 1 次（温度归零），2 次均失败才中止；CancelledError 不重试
- **世界观底层提取**（OntologyExtractor）：全文提取 WorldOntology 7 大维度固化到 Project.world_ontology（全文一次不随章节变）；镜像 ContextExtractor 三大机制
- **主角形象提取**（ContextExtractor._extract_protagonist）：8 维度心理学档案，独立链路（extract_protagonist_streaming + 独立缓存 key 前缀 protagonist:），与上下文提取解耦避免互相覆盖；按章节缓存
- **世界书条目级开关**：`_get_enabled_worldbook_entries` 过滤 enabled=False，单点覆盖 3 处续写入口

### 4. trimStrings 行为

- TGbreak 等预设的 trimStrings 会剥离 `<`/`>` 字符；`strip_html_tags` 需额外清理残留碎片，仅在检测到 HTML 特征时调用

### 5. UI 布局规范

主窗口 5 栏 QSplitter 布局（左→右）：
1. 章节目录（200px，不伸缩）2. 预览/编辑器（420px，伸缩）3. 上下文提取预览（280px，伸缩）4. 续写控制（260px，不伸缩）5. 续写输出（400px，伸缩）

- 初始窗口 1600×900，最小 1280×700；`DEFAULT_PANEL_SIZES = [200, 420, 280, 260, 400]`
- 续写模式三选一：单次续写 / 卷续写 / 重写当前章节
- 卷模式隐藏右侧输出面板，VolumePanel 流式区承接章节流式
- 单次模式用 `lookback_chapters` 截断聊天历史；卷模式用 `skip_history=True` 跳过聊天历史，前文由"最近 10 章正文参考"系统消息提供
- 窄屏自适应：按钮区用 QFlowLayout 自动换行
- CollapsiblePanel 添加控件必须用 `add_widget()`
- 按钮高度统一 28px（全局 QSS 接管）

### 6. 主题系统（Apple HIG 风格）

- 主题文件 `resources/themes/{light,dark}.qss`，由 `main_window._apply_theme()` 全局应用
- 三态：暗色/亮色/跟随系统（监听 colorSchemeChanged 实时重应用）
- 设计 token：主色 System Blue `#007aff`（亮）/`#0a84ff`（暗）；圆角分级（按钮 8px/主按钮 14px/卡片 10px/列表项 6px）
- **内联样式禁用**：UI 代码不用 `setStyleSheet`，所有样式通过 `setObjectName` 由全局 QSS 接管
- 状态色：进行中=橙/成功=绿/错误=红/次要=半透明

### 7. Token 预算

- `PromptAssembler.assemble()` 计算 `max_context - max_tokens - system_tokens - injection_tokens - user_input_tokens`，超限时自动降低 max_tokens
- assemble 是纯本地操作不调用 LLM，可用于提示词预览

### 8. 单章续写提升为章节（Promote to Chapter）

- 工作流：选定章节续写 → 续写作为待定候选仅在续写面板可见 → 接受=提升为独立章节插入当前章节之后 → 选中新章节再续写
- `ChapterService.promote_continuation_to_chapter(chapter, continuation) -> (chapter, new_chapter)`：当前章之后所有章节 index 后移 1 位，创建新 Chapter（index=当前+1），删除原续写记录
- 续写不在章节列表显示（ChapterTreeModel 扁平单层仅章节）
- 删除续写：`delete_continuation` 信号 → 直接删除记录

### 9. 卷级多章节续写（Volume）

- 一次产出 N 章正文，数据模型定义于 `models/volume.py`
- **7 阶段流程**（VolumeOrchestrator）：deep_analysis → volume_outline → outline_audit（多轮）→ outline_final → 逐章循环（chapter_outline → writing → verify → rewrite）
- **5 个暂停点**：after_deep_analysis/after_volume_outline/before_audit（默认开）/after_audit/after_chapter（默认关）
- **深度分析 token 切分**：analysis_chunk_tokens>0 时按章节边界累积 token 切分，逐块增量合并；切分后追加 LLM 语义整合润色，失败降级
- **动态前文窗口**：`_build_dynamic_lookback_text(window=10)` 基于 `_get_effective_chapters()`（插入点前章节 + 本卷已生成章节），取末尾 window 章拼接；从中间续写时不会误把插入点后原章节当前文
- **强制修改流程**：enable_chapter_revise=True 时审计①即使通过也强制 1 轮修改；critical 问题忽略 max_revise_rounds_per_chapter 上限一直修正到通过
- **审计后重写**：`_run_chapter_rewrite` 加载 phase_audit_rewrite.txt 模板，审计报告整体即修改意见（无 revision_guidance），强调重写完整正文严禁续写追加
- **终稿大纲**：outline_final 阶段基于最后一轮审计+原大纲+前 10 章+深度分析+推进速度生成终稿 VolumeOutline
- 卷模式默认启用 100k token 切分（_analysis_chunk_tokens_combo 默认选项）
- 阶段提示词走 str.replace 宏替换（不用 MacroEngine/Jinja2）
- 测试覆盖：`tests/test_volume_*.py`（orchestrator/prompts/e2e/ui/models/checkpoint_dialog/chapter_confirm_dialog/artifact_detail_dialog）

### 10. 提示词变量与宏体系

自定义预设 prompt 条目 `content` 中可使用三类变量，发送前由 MacroEngine + TemplateEngine 依次替换（注释 → setvar/getvar → `{{name}}` 简单宏 → Jinja2 `{{}}`/`{% %}`）。完整教程见 `README.md`「变量与宏调用教程」。

- **11 个内置宏**（`core/macros.py` MacroContext 字段）：`{{book}}`/`{{author}}`/`{{protagonist}}`/`{{synopsis}}`/`{{world_setting}}`/`{{writing_style}}`/`{{chapter_title}}`/`{{chapter_index}}`/`{{target_words}}`/`{{user}}`/`{{char}}`；未知宏保留原样
- **额外注入宏**（PromptAssembler._build_macro_context 写入 ctx.extra）：`{{world_ontology}}`（WorldOntology 7 维度 JSON）、`{{protagonist_profile}}`（ProtagonistProfile 8 维度 JSON）、`{{custom_audit_rules}}`（自定义设定格式化文本）
- **ST 风格变量**：`{{setvar::name::value}}`/`{{getvar::name}}`/`{{// comment}}`；四作用域 global/project/chapter/cache
- **Jinja2 沙箱**：ImmutableSandboxedEnvironment + 双白名单函数（render_pre_send 完整 16 函数 / render_post_receive 精简 9 函数防 AI 输出外泄数据）；超时 5 秒；递归上限 50
- **调试**：调试模式勾选后各 LLM 调用前弹 DebugPromptDialog 预览组装后 messages

### 11. 单章续写审计与修正

- **phase_single_audit.txt**：8 维度精简审计，5 占位符，`{{user_input}}` 用 `<user_directive>` XML 标签定界，输出严格 JSON
- **AuditWorker**：流式 stream_chat_completion，低温稳定输出
- **修正流程**（`_on_audit_accepted`）：加载 phase_audit_rewrite.txt 独立模板 str.replace 注入 9 占位符（无 revision_guidance，审计报告整体即修改意见）→ 新建 ContinuationWorker `created_by="audit_rewrite"` 流式重写为新 swipe（不删原 swipe，可并排对比）

### 12. 自定义设定/审计必查项（CustomAuditRule）

- **数据模型**：CustomAuditRule（id/title/raw_input/requirement/audit_criteria/severity/created_at），保存到 Project.custom_audit_rules 全局共享
- **结构化解析**：CustomAuditRuleService.parse_rule_streaming 加载 phase_custom_rule_parse.txt，AI 结合世界观+上下文结构化为 requirement+audit_criteria 两字段
- **UI 入口**：context_preview_panel 的"新增自定义设定"/"查看自定义设定"按钮
- **3 处注入**：①单章续写 assemble 传 custom_audit_rules ②单章审计 phase_single_audit.txt 注入 ③卷续写 VolumeOrchestrator 构造传，9 处 phase 方法注入
- **审计维度** custom_rules_compliance（一票否决）+ rigid_ai_text（刻板 AI 文本禁令严格给分）：未满足 high 严重度规则判 critical；3 处以上 AI 痕迹判 major，5 处以上判 critical

### 13. 安全加固

完整审计报告见 `docs/SECURITY_AUDIT.md`。

- **ID 路径穿越防护**：`novelforge.utils.ids.validate_id` 校验仅允许 `^[A-Za-z0-9_\-]+$`，外部数据流入文件路径的 ID 必须经此校验（路径构造函数入口 + 服务层入口 + 模型层 field_validator 三重防御）
- **ZIP slip 防护**：exporter.import_project_backup 逐成员 resolve() 校验位于 tmp_path 之下
- **SSTI 白名单收紧**：template_engine 双白名单函数（render_pre_send 16 函数 / render_post_receive 9 函数移除数据读取）
- **ReDoS 超时保护**：regex_engine/importer 共享 ThreadPoolExecutor + future.result(timeout=5.0)
- **日志脱敏**：logger 三模式（AUTH_HEADER → API_KEY → BEARER）覆盖 sk-/sk-ant-/sk-or- 前缀与 Authorization 头
- **敏感文件权限**：paths.secure_file `os.chmod(path, 0o600)` 5 处接入（config/crypto/storage/logger）
- **HTTP 明文警告**：llm_client 保留 http:// 兼容本地 LLM，但 api_key 非空时 warning 提示 MITM 风险
- **依赖版本锁定**：requirements.txt floor 提升至已修复 CVE 版本（aiohttp>=3.9.5/jinja2>=3.1.4/cryptography>=42.0.4/pydantic>=2.5.3）

### 14. 重写当前章节模式（Rewrite Current Chapter）

第三种续写模式，两步流程重写当前章节正文：①分析当前章节+用户需求输出结构化「新章节生成详细需求」②检查点暂停供用户审阅/编辑③复用单章续写 assemble+ContinuationWorker 生成新正文存为 swipe（created_by="rewrite_current"），接受时 replace_chapter_content 覆盖当前章节正文（不新建章节、不后移 index）。

- **关键约束**：重写模式下「前文提取」与「聊天历史构建」**不得包含当前章节**（当前章节是待重写对象不是前文）
- **exclude_current 参数**：贯穿 ContextExtractor.extract/extract_streaming/_get_lookback_chapters/_build_cache_key 与 PromptAssembler.assemble/_build_history；True 时 lookback 返回 `sorted_chapters[:current_idx]`，_build_history 遇当前章节 break 且不 append，缓存 key 带 `:rewrite` 后缀
- **分析模板**：phase_rewrite_analysis.txt，6 占位符，输出 5 分节
- **流程端点**：rewrite_analysis 独立流程（低温稳定），重写生成复用 single_continuation 端点
- **接受逻辑**：`_on_accept_continuation` 检查 swipe.created_by=="rewrite_current" 则调 replace_chapter_content，否则走 promote_continuation_to_chapter

### 15. 流程破限配置

- **正文流程**（single_continuation/volume_continuation）：破限由预设管理器勾选 `nf_jb_*` 模块控制（现有机制不变）；`default_preset.json` 的 `prompt_order` 中 5 个 `nf_jb_*` 已前置到 `main` 之前，使破限 system 消息在组装后位于「你是一位专业的小说续写助手」之前定调。`nf_jb_prefill` 虽移到 order 前列但它是 ABSOLUTE 注入（`injection_position=1, injection_depth=0`），仍按深度规则注入到末尾，行为不变
- **非正文流程**（6 个：single_audit/rewrite_analysis/context_extraction/ontology_extraction/protagonist_extraction/custom_rule_parsing）：每个流程在 `resources/defaults/jailbreaks/jb_{flow}.txt` 有专用模板（含 `### LOW/MID/HIGH ###` 三档，按流程风格定制：提取类强调不拒绝敏感分析、审计类强调诚实批判不软化、重写分析强调不拒绝敏感重写需求、自定义设定解析强调接受任何黑暗设定）；运行时由 `JailbreakProvider.get_jailbreak(flow, level)` 返回文本，作为 `{"role":"system","content":jb_text}` 前置到 messages 开头（空文本不注入）；提取类流程的合并子调用复用同流程破限参数
- **配置入口**：`FlowEndpointDialog` 在端点配置下方新增「破限配置（非正文流程）」分组，仅对 6 个非正文流程显示等级下拉（关闭/低/中/高/自定义）+「编辑自定义」按钮（仅 custom 时启用，弹 `JailbreakCustomDialog` 编辑）；正文流程不显示（由预设管理器控制）
- **默认等级**：提取类三个流程（context/ontology/protagonist）默认 `low`（提取敏感小说内容不被拒绝），其余默认 `off`；自定义文本优先于等级模板
- **配置层**：`config.py` `FLOW_DEFAULT_JAILBREAKS` 常量 + `flow_jailbreaks`/`flow_jailbreaks_custom` 两个 dict + 5 个 get/set 方法（镜像 `flow_endpoints` 模式）；`get_flow_jailbreak(flow)` 未配置回退默认等级
- **降级策略**：`jb_{flow}.txt` 不存在或无对应等级段，`get_jailbreak` 返回空串，不阻塞流程

## 代码风格规范

- `from __future__ import annotations` 启用延迟类型注解
- 日志用 logging 模块，logger 名为模块路径
- 异常处理：UI 层捕获并提示，服务层记录日志返回错误状态
- 中文注释和文档字符串，技术术语保留英文
- 信号命名：过去式或名词（如 `chunk_received`、`entries_changed`）
- **reindex 禁止用 `save_chapter`**：`list_chapters` 返回的 Chapter `content=""`，save_chapter 会用空 content 覆盖正文文件。reindex 必须用 `update_chapter_index`（只更新 SQL index 列不写文件）
- **切分模式下所有文本占位符必须使用切分后的块文本**：不能注入完整全文，否则切分形同虚设
- **用户指令优先原则**：user_directive_analysis.required_elements/emphasized_elements 是续写硬约束，优先级高于通用规划原则但低于主角 OOC 红线与世界观底层规则；审计严格给分不可因其他维度优秀而豁免
- **前文参考机制按模式分离**：单章用 lookback_chapters 截断聊天历史；卷用 skip_history=True + 动态前文窗口系统消息，避免章节正文在 chat history 与系统消息重复
- **外部数据流入文件路径的 ID 必须经 `validate_id` 校验**（路径构造函数入口 + 服务层入口 + 模型层 field_validator 三重防御）

## 测试要求

- 测试文件位于 `tests/` 目录，命名 `test_*.py`
- 使用 pytest 运行：`python -m pytest tests/ -q`
- E2E 测试使用真实预设文件和小说文件
- UI 测试需排除环境缺失组件：`--ignore=tests/test_m5_polish.py -k "not TestUIComponents"`

## 修改后必须更新

每次修改项目代码后，请检查并更新本文件的以下部分：
1. **架构分层**：新增/删除/重命名模块时更新目录树
2. **关键设计决策**：新增重要设计决策时添加条目
3. **UI 布局规范**：修改面板布局时更新布局描述
4. **技术栈**：新增依赖时更新

**每次修改项目代码后，还必须同步更新 `update.md`**：在文件顶部按时间倒序追加新条目，格式含 `## YYYY-MM-DD：标题` / `### 背景` / `### 核心改动` / `### 测试` / `### 文档同步` 等小节。
