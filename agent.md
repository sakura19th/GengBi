# 赓笔 (GengBi) 项目约束文件

> **重要**：本文件是项目的核心约束文档。每次修改项目代码后，必须读取并即时更新本文件，确保文档与代码保持同步。

## 项目概述

赓笔 (GengBi) 是一个 SillyTavern (ST) 兼容的小说续写工具，提供从 TXT 导入、章节管理、上下文提取、提示词组装到 LLM 流式续写的完整工作流。

**当前版本：v0.2.12**（定义于 `novelforge/__init__.py` 的 `__version__`，由"关于"对话框引用，README 顶部同步标注）

**版本更新记录**：维护在 `README.md` 的"更新记录"章节，按版本倒序排列，每次发版须追加新版本小节。

## 技术栈

- Python 3.11+ / PySide6 (Qt6) / pydantic v2 / aiohttp（LLM 流式调用）/ aiosqlite（异步 SQLite）+ JSON 文件 / pytest

## 架构分层

```
novelforge/
├── models/          # 数据模型（pydantic v2）
│   ├── chapter.py        # Chapter、Continuation（accept=promote_continuation_to_chapter 提升为新章节插入当前章之后；Continuation.generated_title LLM 生成标题从 <novelforge_title> 标签提取；Continuation.highlights 输出栏高亮持久化；Chapter.protagonist_profile 主角形象档案持久化）
│   ├── context.py        # ContextEntry（条目级 enabled 开关控制注入）、VALID_CATEGORIES/POSITIONS/ROLES
│   ├── preset.py         # WritingPreset、Prompt、PromptOrderEntry
│   ├── project.py        # Project、NovelProfile（Project.world_ontology 世界观底层；Project.style_profile 文风档案；Project.custom_audit_rules 自定义设定全局共享）
│   ├── regex.py          # RegexScript、PLACEMENT_* 常量
│   ├── style_profile.py  # StyleProfile（九维文笔风格参数：language_texture/narrative_rhythm/scene_construction/character_portrayal/emotion_engagement/innovation_signature/protagonist_supporting_ratio/perspective_usage/time_transition；extracted_at/source_chapter_range 元数据）
│   ├── custom_audit_rule.py  # CustomAuditRule（id/title/raw_input/requirement/audit_criteria/severity/created_at）
│   ├── agent.py          # 续写流程共享模型（Outline/Scene/CritiqueReport/CritiqueIssue + VALID_CRITIQUE_* 16 维度常量）
│   ├── volume.py         # 卷级多章节续写模型（DeepAnalysis/VolumeOutline/VolumeArtifacts/VolumeRunConfig；DEFAULT_AUDIT_DIMENSIONS 13 维度）
│   └── flow_plugin.py    # 流程控制插件模型（FlowPlugin/FlowStage 声明式 JSON 配置；5 agent 类型 + 3 ui_mode + 3 accept_mode；ID 路径穿越防护；FlowStage.agent 字段级校验）
├── core/            # 核心逻辑（无 UI 依赖）
│   ├── prompt_assembler.py  # 提示词组装（三阶段：排序→注入→裁剪；worldInfoBefore/After 按 category 分组 Markdown；assemble 接收 world_ontology/protagonist_profile/style_profile/skip_history/custom_audit_rules；_build_macro_context 序列化注入 {{world_ontology}}/{{style_profile}}/{{protagonist_profile}}/{{custom_audit_rules}} 宏，4 个档案 None 时统一注入占位文本"（无XX档案）"与审计模板逻辑匹配；新增 _serialize_profile_or_placeholder/_serialize_rules_or_placeholder 静态辅助方法封装序列化+占位逻辑；_build_previous_chapter_titles 构建前章标题列表供 {{previous_chapter_titles}} 宏替换）
│   ├── regex_engine.py      # ST 兼容正则引擎 + strip_html_tags
│   ├── template_engine.py   # Jinja2 沙箱（双白名单函数防 SSTI，render_pre_send/render_post_receive）
│   ├── token_counter.py     # Token 计数（tiktoken 优先，回退估算）
│   ├── macros.py            # ST 宏引擎（{{user}}、{{char}} 等 11 内置宏 + setvar/getvar 四作用域）
│   ├── variable_store.py    # 变量存储（global/project/chapter/cache 四作用域）
│   ├── json_utils.py        # JSON 解析工具
│   ├── config.py            # 配置管理（API 端点含 models 全部列表 + enabled_models 启用子集 + default_model 后台流程回退 + reasoning_effort 思考强度 + extra_payload/extra_headers 自定义请求扩展、密钥加密、flow_endpoints 流程端点映射 + flow_models 流程模型映射）
│   └── storage.py           # SQLite 异步存储层（外键 CASCADE；row_factory=aiosqlite.Row 按列名访问；列迁移函数；list_chapters 检测 DB 缺章自动从磁盘重建；update_chapter_index/update_chapter_title 单列更新不写正文文件；delete_project validate_id 防路径穿越；is_network_filesystem Windows UNC/网络驱动器 + Linux /proc/mounts 检测）
├── services/        # 业务服务
│   ├── context_extractor.py     # 上下文提取（多批次 token 拆分 + 【信息汇总】合并；主角形象独立链路 extract_protagonist_streaming，独立缓存 key 前缀 protagonist:；模型统一由 flow_models 控制，不再读 extractor_model；_extract_common/extract_protagonist_streaming try/finally 关闭 LLMClient 释放 aiohttp session，body 抽到 _extract_common_body/_extract_protagonist_body 辅助方法；EXTRACT_MAX_TOKENS=16000/PROTAGONIST_EXTRACT_MAX_TOKENS=16000 从 5000/6000 调高避免大批量章节提取时 JSON 被 max_tokens 截断导致 Unterminated string；非流式/流式路径增加 finish_reason=="length" 截断检测 warning 日志（4 处：批次提取/信息汇总/主角形象批次/主角形象汇总）；JSON 解析失败日志增加 finish_reason 字段便于诊断是截断还是其他原因；_parse_extract_response/_parse_protagonist_response 增加第三层宽松 JSON 回退（提取首尾括号 [..] / {..} 子串重试，镜像 json_utils.parse_json_response 模式）；extract 方法 stream 默认值改为 True（默认流式 stream_chat_completion，on_chunk=None 时不推送 chunk 但仍用流式调用）；main_window 重试提取/强制重新提取从 extract 改为 extract_streaming + on_chunk/on_batch_complete Signal 回调）
│   ├── ontology_extractor.py    # 世界观底层提取（WorldOntology 7 大维度；镜像 ContextExtractor 三大机制：拆批/增量合并/语义整合；拆 7 维度为 ContextEntry 存入项目世界书；extract_ontology_streaming 增加 current_chapter/lookback 参数支持【前文：】控制，新增 _get_lookback_chapters 方法镜像 ContextExtractor 基于当前章节取前 N 章含当前章节，current_chapter=None 时取全部章节兼容旧调用；main_window 调用时传入 UI 选择的 lookback 值和当前章节；批次提取+信息汇总内部从 chat_completion 改为 stream_chat_completion 真流式逐 chunk 推送 UI）
│   ├── style_extractor.py       # 文风档案提取（StyleProfile 9 大维度；镜像 OntologyExtractor 三大机制：拆批/增量合并/语义整合；固化到 Project.style_profile，不入世界书；flow_key="style_extraction"；extract_style_streaming 增加 current_chapter/lookback 参数支持【前文：】控制，新增 _get_lookback_chapters 方法镜像 ContextExtractor/OntologyExtractor 基于当前章节取前 N 章含当前章节，current_chapter=None 时取全部章节兼容旧调用；main_window 调用时传入 UI 选择的 lookback 值和当前章节；批次提取+信息汇总内部从 chat_completion 改为 stream_chat_completion 真流式逐 chunk 推送 UI）
│   ├── continuation_worker.py   # QThread + asyncio 续写 worker（单次续写/重写生成/审计后修正；调试模式支持运行时端点/模型覆盖 _effective_model/_effective_client + endpoint_id 缓存 LLMClient；phase_name 按 created_by 映射 续写/重写生成/修正；run() finally 关闭主+调试缓存 LLMClient 释放 aiohttp session）
│   ├── audit_worker.py          # 单章续写审计 worker（流式 stream_chat_completion，低温稳定输出；调试模式镜像 ContinuationWorker，phase_name 参数化 单章审计/重写需求分析）
│   ├── custom_audit_rule_service.py  # 自定义设定结构化解析（parse_rule_streaming，AI 结合世界观+上下文结构化为 requirement+audit_criteria）
│   ├── volume_orchestrator.py   # 卷级多章节续写编排器（QThread+asyncio，7 阶段流程 + 暂停点 + 调试模式；分阶段执行 phase/phase_inputs/phase_output 信号支持 volume_phase agent；chapter_step_started 信号驱动逐章子步骤进度（细纲/写作/验证/修订）；调试模式支持运行时端点/模型覆盖（_effective_model/_effective_client + endpoint_id 缓存 LLMClient）；run() finally 关闭主+调试缓存 LLMClient 释放 aiohttp session）
│   ├── llm_client.py            # LLM 客户端（流式 + 非流式；构造函数接收 extra_payload/extra_headers 端点级自定义扩展，stream/chat_completion 在 _filter_unsupported_params 后 deep merge extra_payload 到 payload、update extra_headers 到 headers，fetch_models 也合并 extra_headers；_deep_merge_dict 模块级辅助函数——dict 递归合并、list/scalar 覆盖；reasoning_effort 按模型映射——Gemini 仅支持 low/medium/high，auto 不发送、minimal→low、max→high；_filter_unsupported_params 按模型子型号删除不支持参数——xAI Grok 全系列删 presence/frequency_penalty，非 grok-3-mini 删 reasoning_effort；_ensure_user_message 兜底——messages 全为 system 时将末条改为 user 副本，修复 Gemini 兼容网关 contents 为空 500 错误）
│   ├── preset_service.py        # 预设管理（reset_default_preset 同步内置最新版）
│   ├── regex_service.py         # 正则脚本管理（global/scoped/preset）
│   ├── importer.py              # TXT 导入与章节拆分
│   ├── exporter.py              # 导出（TXT/Markdown/备份 zip，含 ZIP slip 防护）
│   ├── async_runner.py          # 后台事件循环运行器（单例；`run` 超时后 `future.cancel()` 取消协程避免泄漏；`_run_loop` finally 清理异常 logger.warning 而非静默吞掉）
│   ├── jailbreak_provider.py     # 流程破限文本提供器（加载 jb_{flow}.txt 按 ### LOW/MID/HIGH ### 分段，4 等级 off/low/mid/high 返回文本；文件缓存）
│   ├── flow_plugin_service.py  # 流程控制插件 CRUD（继承 BaseJsonService[FlowPlugin]；首启复制 4 内置插件 + 版本升级（builtin 插件资源版本高于已安装时覆盖）；导入强制 builtin=False，ID 冲突追加 _imported；内置不可删除）
│   ├── flow_executor.py        # 流程执行引擎（按 FlowPlugin.stages 有序执行；5 agent handler 回调机制；挂起-恢复模式处理多阶段用户交互；cancel 清理；`_execute_current_stage` 用 while 迭代循环推进阶段而非递归，避免阶段数多时触及 Python 递归上限）
│   └── storage_service.py       # 存储服务（项目/章节/续写 CRUD；update_chapter_index/update_chapter_title 单列更新不写文件）
├── ui/              # UI 组件（PySide6）
│   ├── main_window.py           # 主窗口（5 栏 QSplitter + 主题管理 + 调试菜单；ont/protagonist/custom_rule 提取信号处理；FlowPluginService+FlowExecutor 流程插件系统接线；start_flow 统一入口 + 5 agent handler + accept_mode 适配；audit handler 按 flow_key 分派（rewrite_analysis 走 _on_start_rewrite_current 原路径，其余走 _on_start_generic_analysis 通用分析路径，采纳后 flow_executor.resume 推进）；continuation handler 新增 created_by=="writing_mode" 分支调 _on_start_writing_mode_continuation（精炼输出前置【写作参考】到 user_input）；_on_start_continuation 新增 user_input_override 参数支持写作模式第 3 步注入；新增 _build_previous_chapters_text(lookback) 构建前 lookback 章正文（含当前章）供写作模式分析注入 {{previous_chapters_text}}；_on_rewrite_analysis_accepted 缓存 analysis_text 到 swipe.parameters_snapshot["_rewrite_analysis_text"]；_on_start_writing_mode_continuation 缓存 refined_output 到 swipe.parameters_snapshot["_writing_mode_refinement"]；_on_rewrite 核心决策——从当前 swipe parameters_snapshot 取缓存键，rewrite_current 有 _rewrite_analysis_text 跳过分析直接调 _on_rewrite_analysis_accepted 生成新 swipe，writing_mode 有 _writing_mode_refinement 跳过阶段 1/2 直接调 _on_start_writing_mode_continuation 生成新 swipe，无缓存走 _on_start_continuation_routed 完整流程；旧 swipe 无缓存键回退原流程；_on_rewrite 在 get_parameters() 重赋值后补回 params["model"]=get_selected_model()（get_parameters 不含 model，需显式补充，确保重写各分支用面板选中模型而非回退 flow_models）；volume_phase 多阶段流程：_prepare_volume_run→_start_volume_phase→phase_output→resume；卷续写暂存节点持久化 ~/.novelforge/volume_states/{chapter_id}.json + 选中章节恢复入口 _check_volume_resume→_resume_volume_state + 阶段失败重试对话框 + 停止/完成/出错清理；逐章子步骤进度 _on_volume_chapter_step_started 累积 _volume_chapter_steps_seen 驱动面板 4 步标签（细纲/写作/验证/修订）+ 状态栏；章节切换状态保留 7 缓冲字段；closeEvent 停止 _continuation_worker/_audit_worker/_volume_orchestrator 三 worker；_on_start_continuation 温度（0.0-2.0）+目标字数（100-50000）范围校验；3 处 prompt_assembler.assemble 调用点（单章续写生成 L1865/续写预览 L3473/重写生成 L5510）均传齐 world_ontology/protagonist_profile/custom_audit_rules/style_profile 4 个档案参数——单章续写生成补 style_profile 修复文风档案未组装 bug，续写预览补齐 4 个档案参数确保预览与实际发送一致；审计 chunk_received 经 _on_audit_chunk_received 中转槽转发到 AuditDialog 防对话框已删除 RuntimeError 崩溃 + _on_audit_cancelled 取消前 disconnect）
│   ├── continuation_panel.py    # 续写控制面板（动态插件下拉由 set_flow_plugins 填充；start_flow 统一信号替代原 start_continuation/rewrite_current_analysis_requested；rewrite 信号统一入口——_on_rewrite_clicked 统一发射 rewrite(created_by="rewrite") 由 MainWindow 决策是否复用缓存审计结果；端点/模型下拉框 AdjustToContents 自适应宽度；端点切换按 enabled_models 填充模型下拉并按名称排序，会话记忆每端点上次手动选择模型；select_model_by_name 供 _refresh_endpoints 同步流程配置模型；get_selected_model 供调试预览取面板当前模型；用户输入区按行数自动增高约 1～6 行并 WidgetWidth 换行；输出框右键 4 色高亮 + 备注；highlights_changed 信号持久化）
│   ├── volume_panel.py          # 卷续写控制面板（配置/两层进度/五 Tab 产物查看/流式区）
│   ├── context_preview_panel.py # 上下文提取预览面板（ontology/protagonist/custom_rule 三类提取按钮互斥 + 流式展示）
│   ├── chapter_list.py          # 章节列表（虚拟滚动/搜索/右键菜单；当前选中章节持续高亮——ChapterHighlightDelegate 自定义 QStyledItemDelegate 在 paint() 中 fillRect 绕开 QSS 选中态覆盖；FullTextSearchWorker.run 通过 read_chapter_content 文件系统直读章节正文不访问 SQLite 跨线程安全；_stop_fulltext_search 非阻塞——worker finished 信号触发 _on_search_worker_finished 自清理替代 wait(3000)，sender() 校验防新搜索误清）
│   ├── chapter_editor.py        # 章节预览/编辑（自动保存/undo/拆分；流式锁定；QLineEdit 标题编辑 + title_changed 信号，编辑模式下可修改章节标题）
│   ├── audit_dialog.py          # 单章审计对话框（流式→可编辑→采纳）
│   ├── custom_rule_dialog.py    # 自定义设定对话框（输入/查看+删除）
│   ├── checkpoint_dialog.py     # 卷续写暂停点对话框（接受/编辑/取消）
│   ├── chapter_confirm_dialog.py # 每章后暂停点（通过/不通过+反馈/取消）
│   ├── artifact_detail_dialog.py # 阶段产物完整内容查看
│   ├── debug_prompt_dialog.py   # 调试提示词预览 + 端点/模型覆盖选择（显示阶段名+messages JSON + 端点/模型下拉，发送/取消按钮；端点切换按 enabled_models→models→[default_model] 回退链填充模型）
│   ├── preset_manager.py        # 预设管理器（7 按钮；6 生成参数含 reasoning_effort 5 档）
│   ├── flow_plugin_manager.py   # 流程插件管理器（列表/详情/导入导出/删除；继承 PersistentDialog 非模态；plugin_changed 信号通知 MainWindow 刷新面板下拉）
│   ├── regex_manager.py         # 正则管理器（内联勾选即时持久化）
│   ├── worldbook_manager.py     # 世界书管理器（条目级 enabled 开关）
│   ├── settings_dialog.py       # 设置对话框（API 端点管理含 models 全部列表 + enabled_models 可勾选多选 QListWidget + 全选/全不选/自定义模型录入、复制端点；default_model 自动取首个已启用供后台流程；reasoning_effort 7 档；EndpointEditDialog._on_accept base_url 校验 http/https scheme + rstrip("/")；ModelFetchWorker 增加 stop() best-effort 标志位 + finished→deleteLater 自清理 + closeEvent/_on_fetch_models 停止前一个 worker wait(2000) + 防御式 isRunning 访问避免 wrapper 失效 RuntimeError；不含上下文提取配置——已统一由流程端点配置+预览面板管理）
│   ├── flow_endpoint_dialog.py  # 流程端点配置（10 流程端点映射 + 每流程模型下拉 enabled_models 回退链 + 8 非正文流程破限等级下拉 off/low/mid/high/custom + 自定义文本编辑入口）
│   ├── jailbreak_custom_dialog.py # 自定义破限文本编辑对话框（QPlainTextEdit + 确定/取消）
│   ├── checkable_combo.py       # 可勾选多选下拉（世界书用；关闭态摘要文本 + 主题 QSS 箭头）
│   ├── worldbook_panel.py       # 续写配置内嵌世界书多选（选中即启用；get_selected_worldbook_ids；选中态由 MainWindow 持久化到 continuation.selected_worldbook_ids）
│   ├── font_settings.py / history_panel.py / project_panel.py / template_editor.py / dialogs.py / flow_layout.py / wheel_filter.py
│   └── ...
└── resources/       # 资源文件
    ├── defaults/
    │   ├── default_preset.json     # 默认写作预设（23 条分层模块：main/抗八股/抗重复/字数/文风互斥/推进互斥/思维链三层次互斥/破限层 4 套互斥+助手预填充/Marker；破限默认全关；nf_jb_* 已前置到 main 之前定调；nf_jb_high 含 NSFW 指引覆盖原 nf_nsfw；防全知硬约束贯穿 core_rules/anti_bagua/cot/main；nf_core_rules 集成基线轻度破限【创作自由基准】+ 第 8 条用户指令遵从/不引入意外内容；nf_cot 改为变量注入模板 {{getvar::COT-items}}；新增 nf_cot_low/mid/high 三层次互斥模块通过 setvar 注入思维链分析项（低 4 项/中 7 项/高 10 项全维度），默认启用 mid；main 输出格式描述泛化不再列举具体项；main 含【续写约束】段；main 输出格式含 <novelforge_title> 标签 + {{previous_chapter_titles}} 宏段落供章节标题生成；main 含【世界观底层】/【文风档案】/【写作风格补充说明】/【主角信息】/【自定义设定】/【前文章节标题】占位符段——【写作风格】改名【写作风格补充说明】并移到【文风档案】正下方明确量化档案为主、文字描述为辅的主次关系，含 {{world_ontology}}/{{style_profile}}/{{writing_style}}/{{protagonist_profile}}/{{custom_audit_rules}} 注入点）
    │   ├── jailbreaks/                # 非正文流程破限模板（8 个 jb_*.txt，每文件含 ### LOW/MID/HIGH ### 三档；已全面深化：LOW=认知修正、MID=授权自由、HIGH=沙盒解放，含内部审查对抗锚点）
    │   ├── default_regex_scripts.json  # 5 条默认正则（思维链隐藏/八股抹除/破折号规范/空行清理/章节标题剥离）
    │   ├── extract_prompt.txt / extract_merge_prompt.txt  # 上下文提取 + 汇总环节
    │   ├── flow_plugins/              # 内置流程插件 JSON（single/volume/rewrite_current/writing_mode 四种模式声明式描述；volume v2.0 为 4 阶段 volume_phase；首启复制到 ~/.novelforge/flow_plugins/ + 版本升级）
    │   └── agent/                   # 续写阶段提示词模板（phase_*.txt；已加入严格遵循用户指令/不引入意外内容约束；所有含 {{world_ontology}} 的模板已加入 {{style_profile}} 文风档案占位符）
    │       ├── phase_verify.txt          # 16 维度验证（含 4 个一票否决 + rigid_ai_text 严格给分；认知越界检查 7 条含信息传递路径/元词汇/剧情奴隶化）
    │       ├── phase_custom_rule_parse.txt # 自定义设定结构化解析
    │       ├── phase_revise.txt / phase_chapter_rewrite.txt / phase_audit_rewrite.txt  # 修订/重写（audit_rewrite 为统一模板取代 chapter_rewrite）
    │       ├── phase_deep_analysis.txt / phase_deep_analysis_merge.txt  # 深度分析 + 切分汇总
    │       ├── phase_volume_outline.txt / phase_outline_audit.txt / phase_outline_final.txt  # 卷大纲/审计/终稿
    │       ├── phase_chapter_outline.txt  # 单章细纲
    │       ├── phase_single_audit.txt     # 单章审计（8 维度；认知越界检查 7 条含信息传递路径/元词汇/剧情奴隶化）
    │       ├── phase_rewrite_analysis.txt # 重写当前章节需求分析（7 占位符含 {{style_profile}}，5 分节输出；分析原则新增「剧情排布保全」——在不影响原剧情排布发展前提下满足用户诉求，原章节事件序列/场景顺序/转折点/伏笔布局/人物登场退场节奏为须保全的剧情骨架；当前章节分析分节须专门审视开头连贯性（承接前一章结尾的过渡方式）与结尾连贯性（结尾性质开放/封闭+过渡下一章+伏笔）；重写要点分节含开头结尾连贯性保全要点；具体要求清单 #7 结构类强化开头连贯性+结尾连贯性）
    │       ├── phase_writing_element_analysis.txt  # 写作模式阶段 1 写作要素分析（7 占位符含 {{previous_chapters_text}}/{{style_profile}}，5 分节输出：出场角色/场所/相关事件/关键伏笔/风格基调）
    │       └── phase_writing_element_refinement.txt # 写作模式阶段 2 写作要素深化（4 占位符含 {{prev_analysis}}/{{style_profile}}，角色形象外貌/心理学形象/语言风格/OOC红线 + 场所精炼 + 其他关键要素）
    ├── icons/           # 主题箭头图标（combo/spin 上下 chevron，亮暗各一套）
    └── themes/          # QSS 主题（light/dark，Apple HIG 风格；Combo 统一 SVG 箭头；SpinBox 显式 up/down-button 几何校准热区）
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
- **提取非强制**：续写/重写前检查上下文条目/世界观底层/主角形象三项未提取状态，弹合并提示对话框询问「是否继续不提取生成？」；用户选「继续」则放行（entries=[]、ontology=None、protagonist=None 照常透传，降级为空串或占位文字），选「取消」则 return
- **多批次【信息汇总】**：前文超 token_limit 按章节边界拆批，每批独立全量提取；batch_count>1 触发 LLM 合并去重，失败降级 best-effort
- **批次级自动重试**：每批次失败立即重试 1 次（温度归零），2 次均失败才中止；CancelledError 不重试
- **世界观底层提取**（OntologyExtractor）：全文提取 WorldOntology 7 大维度固化到 Project.world_ontology（全文一次不随章节变）；镜像 ContextExtractor 三大机制；拆 7 维度为 ContextEntry 存入项目世界书
- **文风档案提取**（StyleExtractor）：全文提取 StyleProfile 9 大维度文笔风格参数固化到 Project.style_profile（全文一次不随章节变）；镜像 OntologyExtractor 三大机制（拆批/增量合并/语义整合）；不入世界书；flow_key="style_extraction"，默认破限等级 low
- **主角形象提取**（ContextExtractor._extract_protagonist）：8 维度心理学档案，独立链路（extract_protagonist_streaming + 独立缓存 key 前缀 protagonist:），与上下文提取解耦避免互相覆盖；按章节缓存
- **世界书条目级开关**：`_get_enabled_worldbook_entries` 过滤 enabled=False，单点覆盖 3 处续写入口；续写配置区支持多选世界书（`get_selected_worldbook_ids`），选中即启用，跨书 uid 冲突时先选优先；选中 ID 持久化到 `continuation.selected_worldbook_ids`（`ConfigManager.get/set_selected_worldbook_ids`），启动/刷新时恢复

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
- 用户输入区按内容自动增高（约 1～6 行封顶，超出内部滚动），按宽度自动换行

### 6. 主题系统（Apple HIG 风格）

- 主题文件 `resources/themes/{light,dark}.qss`，由 `main_window._apply_theme()` 全局应用；箭头图标路径经 `__COMBO_CHEVRON__`/`__SPIN_CHEVRON_UP__`/`__SPIN_CHEVRON_DOWN__` 占位符注入绝对路径
- 三态：暗色/亮色/跟随系统（监听 colorSchemeChanged 实时重应用）
- 设计 token：主色 System Blue `#007aff`（亮）/`#0a84ff`（暗）；圆角分级（按钮 8px/主按钮 14px/卡片 10px/列表项 6px）
- **内联样式禁用**：UI 代码不用 `setStyleSheet`，所有样式通过 `setObjectName` 由全局 QSS 接管
- 状态色：进行中=橙/成功=绿/错误=红/次要=半透明
- Combo 统一 SVG 下拉箭头；SpinBox/QDoubleSpinBox 显式 `up/down-button` 几何，避免 padding+圆角导致箭头与点击热区错位

### 7. Token 预算

- `PromptAssembler.assemble()` 计算 `max_context - max_tokens - system_tokens - injection_tokens - user_input_tokens`，超限时自动降低 max_tokens
- assemble 是纯本地操作不调用 LLM，可用于提示词预览

### 8. 单章续写提升为章节（Promote to Chapter）

- 工作流：选定章节续写 → 续写作为待定候选仅在续写面板可见 → 接受=提升为独立章节插入当前章节之后 → 选中新章节再续写
- `ChapterService.promote_continuation_to_chapter(chapter, continuation) -> (chapter, new_chapter)`：当前章之后所有章节 index 后移 1 位，创建新 Chapter（index=当前+1），删除原续写记录；新章节标题优先使用 `Continuation.generated_title`（LLM 从 `<novelforge_title>` 标签生成），无则回退默认"第N章"
- 续写不在章节列表显示（ChapterTreeModel 扁平单层仅章节）
- 删除续写：`delete_continuation` 信号 → 直接删除记录

### 9. 卷级多章节续写（Volume）

- 一次产出 N 章正文，数据模型定义于 `models/volume.py`
- **7 阶段流程**（VolumeOrchestrator）：deep_analysis → volume_outline → outline_audit（多轮）→ outline_final → 逐章循环（chapter_outline → writing → verify → rewrite）
- **5 个暂停点**：after_deep_analysis/after_volume_outline/before_audit（默认开）/after_audit/after_chapter（默认关）
- **深度分析 token 切分**：analysis_chunk_tokens>0 时按章节边界累积 token 切分，逐块增量合并；切分后追加 LLM 语义整合润色，失败降级
- **动态前文窗口**：`_build_dynamic_lookback_text(window=10)` 基于 `_get_effective_chapters()`（插入点前章节 + 本卷已生成章节），取末尾 window 章拼接；从中间续写时不会误把插入点后原章节当前文
- **强制修改流程**：enable_chapter_revise=True 时审计①即使通过也强制 1 轮修改；critical 问题忽略 max_revise_rounds_per_chapter 上限一直修正到通过，但受模块级常量 `MAX_CRITICAL_REVISE_ROUNDS=20` 硬上限保护，超过后 logger.error 并 break 跳出（避免 LLM 持续产生 critical 问题导致无限循环）
- **审计后重写**：`_run_chapter_rewrite` 加载 phase_audit_rewrite.txt 模板，审计报告整体即修改意见（无 revision_guidance），强调重写完整正文严禁续写追加
- **终稿大纲**：outline_final 阶段基于最后一轮审计+原大纲+前 10 章+深度分析+推进速度生成终稿 VolumeOutline
- **逐章错误隔离**：`_run_chapter_loop` 的 for 循环体内 try/except 包裹全部章节生成逻辑（chapter_plan → dynamic_lookback → outline → writing → verify → rewrite → checkpoint → artifacts）；单章抛 `asyncio.CancelledError` 向上传播（用户取消），其他 `Exception` 记录日志 + emit error 信号 + `continue` 跳过该章继续下一章；for 循环结束后空产物保护（`if not artifacts.chapter_artifacts: return`）避免所有章节失败时构建空 Continuation
- **逐章子步骤进度**：`chapter_step_started = Signal(int, str)` 在 `_run_chapter_loop` 每个子阶段（outline/writing/verify/revise）`await` 前 emit（含 verify↔revise 循环中每次切换）；main_window `_on_volume_chapter_step_started` 累积 `_volume_chapter_steps_seen` 集合（章节开始时重置），调 `update_chapter_progress(current=step, completed=seen)` 驱动 VolumePanel 4 步标签三态显示 + 状态栏「卷续写中: 第N章 细纲/写作/验证/修订」；累积态保证 verify 二次进入时 revise 仍保留 ✓
- **暂存节点持久化**：卷续写阶段 1-3（deep_analysis/volume_outline/outline_audit）每阶段产物完成后 `_save_volume_state` 写入 `~/.novelforge/volume_states/{chapter_id}.json`（含 chapter_id/project_id/preset_id/endpoint_id/model/config/user_input/params/current_phase/completed_phases/phase_artifacts/created_at；阶段产物用 `model_dump(mode="json")` 序列化，恢复时 `model_validate` 反序列化）；chapter_writing 阶段产物不持久化（最后阶段，失败重试从头生成）
- **关闭后恢复**：`_on_chapter_selected` 末尾调 `_check_volume_resume` 检测状态文件，弹恢复确认对话框（QMessageBox.question）；确认后 `_resume_volume_state` 重建 prepare_data（reload endpoint/project/preset + 当前章节 entries/metadata/regex_script_ids）+ 反序列化已完成阶段产物 + 确定下一未完成阶段 + 设 `_volume_resuming=True` 恢复模式启动；endpoint/preset 已删除时弹提示中止
- **阶段失败重试**：`_on_continuation_error` 中 volume 错误弹重试/取消对话框（QMessageBox.question）；重试复用 `_volume_state`（含 prepare_data + 前序产物）+ `_volume_current_phase` 调 `_start_volume_phase` 重启失败阶段（不重新准备数据，避免重复弹校验对话框）；取消清理全部 + 删除状态文件
- **停止/完成清理**：`_on_stop_continuation`（用户主动停止）+ `_on_volume_continuation_finished`（正常完成）+ 重试取消分支均清理 `_volume_state`/`_volume_current_phase`/`_volume_resuming`/`_volume_chapter_id` + 删除状态文件；`_on_volume_continuation_finished` 存储操作块外包 try/except，单章保存异常不崩溃完成回调
- 卷模式默认启用 100k token 切分（_analysis_chunk_tokens_combo 默认选项）
- 阶段提示词走 str.replace 宏替换（不用 MacroEngine/Jinja2）
- 测试覆盖：`tests/test_volume_*.py`（orchestrator/prompts/e2e/ui/models/checkpoint_dialog/chapter_confirm_dialog/artifact_detail_dialog）

### 10. 提示词变量与宏体系

自定义预设 prompt 条目 `content` 中可使用三类变量，发送前由 MacroEngine + TemplateEngine 依次替换（注释 → setvar/getvar → `{{name}}` 简单宏 → Jinja2 `{{}}`/`{% %}`）。完整教程见 `README.md`「变量与宏调用教程」。

- **11 个内置宏**（`core/macros.py` MacroContext 字段）：`{{book}}`/`{{author}}`/`{{protagonist}}`/`{{synopsis}}`/`{{world_setting}}`/`{{writing_style}}`/`{{chapter_title}}`/`{{chapter_index}}`/`{{target_words}}`/`{{user}}`/`{{char}}`；未知宏保留原样
- **额外注入宏**（PromptAssembler._build_macro_context 写入 ctx.extra）：`{{world_ontology}}`（WorldOntology 7 维度 JSON）、`{{style_profile}}`（StyleProfile 9 维度文风量化 JSON）、`{{protagonist_profile}}`（ProtagonistProfile 8 维度 JSON）、`{{custom_audit_rules}}`（自定义设定格式化文本）
- **ST 风格变量**：`{{setvar::name::value}}`/`{{getvar::name}}`/`{{// comment}}`；四作用域 global/project/chapter/cache
- **Jinja2 沙箱**：ImmutableSandboxedEnvironment + 双白名单函数（render_pre_send 完整 16 函数 / render_post_receive 精简 9 函数防 AI 输出外泄数据）；超时 5 秒；递归上限 50
- **调试**：调试模式勾选后各 LLM 调用前弹 DebugPromptDialog 预览组装后 messages，并支持运行时覆盖端点/模型。三种 worker（VolumeOrchestrator/ContinuationWorker/AuditWorker）均实现完整调试模式基础设施：`prompt_debug_requested = Signal(str, str, str, str)` 信号 + `confirm_debug_prompt` UI 线程回传 + `_maybe_debug_prompt` 协程内 emit 等待 + `_effective_model`/`_effective_client` helper 读取覆盖值（client 按 endpoint_id 缓存避免重复创建 aiohttp session，覆盖仅对紧接的下一次 LLM 调用生效，下一阶段重新确认）；`run()` finally 关闭主+调试缓存 LLMClient 释放 aiohttp session。main_window `_on_debug_mode_toggled` 实时设置三个 worker 的 debug_mode；`_on_prompt_debug_requested` 按运行状态路由（优先级 volume > continuation > audit）。ContinuationWorker phase_name 按 `created_by` 映射（continuation→续写、rewrite_current→重写生成、audit_rewrite→修正）；AuditWorker `phase_name` 参数化（单章审计/重写需求分析）。取消调试 emit `error("用户取消调试")` 重置 UI（非弹窗）

### 11. 单章续写审计与修正

- **phase_single_audit.txt**：8 维度精简审计，6 占位符（含 `{{style_profile}}` 文风档案），`{{user_input}}` 用 `<user_directive>` XML 标签定界，输出严格 JSON
- **AuditWorker**：流式 stream_chat_completion，低温稳定输出
- **修正流程**（`_on_audit_accepted`）：加载 phase_audit_rewrite.txt 独立模板 str.replace 注入 10 占位符（含 `{{style_profile}}` 文风档案，无 revision_guidance，审计报告整体即修改意见）→ 新建 ContinuationWorker `created_by="audit_rewrite"` 流式重写为新 swipe（不删原 swipe，可并排对比）

### 12. 自定义设定/审计必查项（CustomAuditRule）

- **数据模型**：CustomAuditRule（id/title/raw_input/requirement/audit_criteria/severity/created_at），保存到 Project.custom_audit_rules 全局共享
- **结构化解析**：CustomAuditRuleService.parse_rule_streaming 加载 phase_custom_rule_parse.txt，AI 结合世界观+上下文结构化为 requirement+audit_criteria 两字段
- **UI 入口**：context_preview_panel 的"新增自定义设定"/"查看自定义设定"按钮
- **3 处注入**：①单章续写 assemble 传 custom_audit_rules ②单章审计 phase_single_audit.txt 注入 ③卷续写 VolumeOrchestrator 构造传，9 处 phase 方法注入
- **审计维度** custom_rules_compliance（一票否决）+ rigid_ai_text（刻板 AI 文本禁令严格给分）：未满足 high 严重度规则判 critical；3 处以上 AI 痕迹判 major，5 处以上判 critical

### 13. 安全加固

完整审计报告见 `docs/SECURITY_AUDIT.md`。

- **ID 路径穿越防护**：`novelforge.utils.ids.validate_id` 校验仅允许 `^[A-Za-z0-9_\-]+$`，外部数据流入文件路径的 ID 必须经此校验（路径构造函数入口 + 服务层入口 + 模型层 field_validator + 存储层 delete_project 入口四重防御）
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
- **分析模板**：phase_rewrite_analysis.txt，7 占位符（含 `{{style_profile}}` 文风档案），输出 5 分节；分析原则含「剧情排布保全」（在不影响原剧情排布发展前提下满足用户诉求，原章节事件序列/场景顺序/转折点/伏笔布局/人物登场退场节奏为须保全的剧情骨架）；当前章节分析分节须专门审视开头连贯性（承接前一章结尾）与结尾连贯性（结尾性质+过渡下一章+伏笔）；重写要点分节含开头结尾连贯性保全要点；具体要求清单 #7 结构类强化开头连贯性+结尾连贯性
- **流程端点**：rewrite_analysis 独立流程（低温稳定），重写生成复用 single_continuation 端点
- **接受逻辑**：`_on_accept_continuation` 优先从 `swipe.parameters_snapshot["_flow_plugin_id"]` 查插件 `accept_mode`（replace→replace_chapter_content，promote→promote_continuation_to_chapter），回退按 `created_by=="rewrite_current"` 推断（兼容旧 swipe）；参见设计决策第 18 条

### 15. 流程端点与模型配置

- **流程端点映射**：`config["flow_endpoints"] = {flow_key: endpoint_id}`，未配置或端点被删回退默认端点；`get_flow_endpoint(flow_key)` 解析
- **流程模型映射**：`config["flow_models"] = {flow_key: model_str}`，空串或未配置回退该流程端点的 `default_model`；`get_flow_model(flow_key)` 解析（flow_key 空串则用默认端点 default_model）
- **不升 config_version**：`flow_models` 缺失时全回退原行为（端点 default_model），旧配置兼容
- **配置入口**：`FlowEndpointDialog` 每个 flow 行横排 [端点下拉][模型下拉]；模型下拉用回退链 `enabled_models → models → [default_model]` 填充（与续写面板一致），首项「默认模型（{default_model}）」itemData="" 表示回退端点默认模型；端点切换时 `_populate_model_combo` 重新填充并保留已保存模型选中
- **消费层优先级**：正文流程 `params.get("model")`（面板下拉）→ `get_flow_model(flow_key)`（流程配置）→ 端点 default_model（get_flow_model 内部回退）；非正文流程直接 `get_flow_model(flow_key)`；审计后重写复用 `single_audit` flow_key，重写生成复用 `single_continuation` flow_key
- **正文流程同步**：`_refresh_endpoints` 关闭对话框后同步面板端点 + 模型选中（`select_model_by_name`，blockSignals 屏蔽会话记忆）；面板模型下拉仍可临时覆盖（会话记忆），不写回 `flow_models`
- **三个 service**：context_extractor/ontology_extractor/custom_audit_rule_service 的 `_get_llm_client(flow_key)` 改用 `get_flow_model(flow_key)` 取模型，替代原 `ep.get("default_model")`；context_extractor 不再读 `extractor_model`（已移除），模型统一由 flow_models 控制
- **Token 拆分持久化**：Settings 对话框已移除「上下文提取」组（含提取模型+Token 拆分）；Token 拆分改由上下文预览面板独管，main_window 连接 `_token_limit_combo.currentTextChanged` → `_save_token_limit_to_config` 持久化到 `context_extract.token_limit`；启动时 `_sync_token_limit_default` 从 config 加载到面板

### 16. 流程破限配置

- **正文流程**（single_continuation/volume_continuation）：破限由预设管理器勾选 `nf_jb_*` 模块控制（现有机制不变）；`default_preset.json` 的 `prompt_order` 中 5 个 `nf_jb_*` 已前置到 `main` 之前，使破限 system 消息在组装后位于「你是一位专业的小说续写助手」之前定调。`nf_jb_prefill` 虽移到 order 前列但它是 ABSOLUTE 注入（`injection_position=1, injection_depth=0`），仍按深度规则注入到末尾，行为不变。`nf_core_rules` 续写核心准则之首集成基线轻度破限【创作自由基准】（常开：用户至上+创作无禁区+拒绝即失职+非评判+不回避），作为始终生效的底色；`nf_jb_*` 为用户可选的更强增强档位（LOW=认知修正、MID=授权自由、HIGH=沙盒解放，含内部审查对抗）
- **非正文流程**（6 个：single_audit/rewrite_analysis/context_extraction/ontology_extraction/protagonist_extraction/custom_rule_parsing）：每个流程在 `resources/defaults/jailbreaks/jb_{flow}.txt` 有专用模板（含 `### LOW/MID/HIGH ###` 三档，按流程风格定制：提取类强调不拒绝敏感分析、审计类强调诚实批判不软化、重写分析强调不拒绝敏感重写需求、自定义设定解析强调接受任何黑暗设定）；运行时由 `JailbreakProvider.get_jailbreak(flow, level)` 返回文本，作为 `{"role":"system","content":jb_text}` 前置到 messages 开头（空文本不注入）；提取类流程的合并子调用复用同流程破限参数
- **配置入口**：`FlowEndpointDialog` 在端点配置下方新增「破限配置（非正文流程）」分组，仅对 6 个非正文流程显示等级下拉（关闭/低/中/高/自定义）+「编辑自定义」按钮（仅 custom 时启用，弹 `JailbreakCustomDialog` 编辑）；正文流程不显示（由预设管理器控制）
- **默认等级**：提取类三个流程（context/ontology/protagonist）默认 `low`（提取敏感小说内容不被拒绝），其余默认 `off`；自定义文本优先于等级模板
- **配置层**：`config.py` `FLOW_DEFAULT_JAILBREAKS` 常量 + `flow_jailbreaks`/`flow_jailbreaks_custom` 两个 dict + 5 个 get/set 方法（镜像 `flow_endpoints` 模式）；`get_flow_jailbreak(flow)` 未配置回退默认等级
- **降级策略**：`jb_{flow}.txt` 不存在或无对应等级段，`get_jailbreak` 返回空串，不阻塞流程

### 17. 章节标题生成与编辑

- **标题生成链路**：`default_preset.json` 的 `main` 模块含 `{{previous_chapter_titles}}` 宏段落（前章标题列表）+ `<novelforge_title>` 输出标签（LLM 分析前章命名规律生成一致的新标题）；`PromptAssembler._build_previous_chapter_titles` 构建前章标题列表（支持 `exclude_current` 重写模式排除当前章、`max_titles=20` 限制）注入 `ctx.extra`；`ContinuationWorker.run` 在后处理剥离标签前用正则提取 `<novelforge_title>` 内容存入 `Continuation.generated_title`；`default_regex_scripts.json` 的 `nf_regex_strip_title` 在 AI_OUTPUT 阶段剥离该标签；`promote_continuation_to_chapter` 优先使用 `generated_title` 作为新章节标题
- **标题编辑链路**：`ChapterEditor` 用 `QLineEdit`（objectName="chapterTitleEdit"）替代原 QLabel，编辑模式下可修改标题；`title_changed` 信号实时通知 `MainWindow._on_chapter_title_changed` 更新章节对象标题并刷新列表；`ChapterService.rename_chapter` 调 `storage.update_chapter_title` 单列更新（只更新 title 列不写正文文件，镜像 `update_chapter_index` 模式）；`Continuation` 的 `generated_title` 字段持久化到 SQLite `continuations` 表（幂等迁移补列）

### 18. 流程控制插件系统

将续写控制中的「单次续写」「卷续写」「重写当前章节」三种模式抽离为声明式 JSON 配置的流程插件，允许用户按规范自行组合既有 agent 或新增 agent 类型，以单个 JSON 文件导入导出分享。

- **数据模型**（`models/flow_plugin.py`）：`FlowPlugin`（id/name/stages/builtin/ui_mode/accept_mode/version）+ `FlowStage`（id/agent/flow_key/created_by/params/input_from）；5 种 agent 类型（continuation/audit/checkpoint/volume_pipeline/volume_phase），FlowStage.agent 字段级校验；3 种 ui_mode（standard/volume）；3 种 accept_mode（promote/replace/volume_insert）；ID 路径穿越防护（拒绝含 `/` `\` `..` `\x00` 的 ID）
- **内置插件**（`resources/defaults/flow_plugins/`）：single（1 阶段 continuation，accept_mode=promote）、volume v2.0（4 阶段 volume_phase：deep_analysis→volume_outline→outline_audit→chapter_writing，ui_mode=volume，accept_mode=volume_insert）、rewrite_current（2 阶段 audit→continuation，input_from=analysis，accept_mode=replace）、writing_mode（3 阶段 audit→audit→continuation：写作要素分析→写作要素深化→单章生成，ui_mode=standard，accept_mode=promote；阶段 1/2 走通用分析路径 _on_start_generic_analysis 采纳后 resume 推进，阶段 3 created_by="writing_mode" 走 _on_start_writing_mode_continuation 将精炼输出前置【写作参考】到 user_input）；内置插件 ID 与原模式字符串一致以保兼容（`get_mode()` 返回值不变）
- **服务层**（`services/flow_plugin_service.py`）：继承 `BaseJsonService[FlowPlugin]`，存储路径 `~/.novelforge/flow_plugins/{id}.json`；首启复制 4 个内置插件 + 版本升级（已安装 builtin=True 且资源版本更高时覆盖）；导入强制 `builtin=False`，ID 冲突追加 `_imported` 后缀；内置插件不可删除
- **执行引擎**（`services/flow_executor.py`）：按 `FlowPlugin.stages` 有序执行；`register_handler(agent_type, handler)` 注册 5 种 agent handler；handler 返回 `"pending"` 挂起等待用户交互，`resume(output)` 恢复推进下阶段，`cancel()` 清理状态；参数合并优先级：阶段 params 覆盖面板 params（`{**self._params, **stage.params}`）
- **main_window 接线**：`start_flow` 信号统一入口（plugin_id + params → FlowExecutor）；`_on_start_flow` 注入 `_flow_plugin_id` 到 params（经 FlowExecutor → handler → ContinuationWorker → `parameters_snapshot`）；5 个 handler（continuation/audit/checkpoint/volume_pipeline/volume_phase）分发到既有方法；volume_phase handler 返回 pending，VolumeOrchestrator 单阶段执行后 emit phase_output → `_on_volume_phase_output` → `flow_executor.resume(artifact)` 推进下一阶段；chapter_writing 阶段 emit finished → `_on_volume_continuation_finished` → `flow_executor.cancel()` 结束流程；`_volume_state` dict 存储准备数据与各阶段产物（Python 对象直传）
- **accept_mode 适配**：`_on_accept_continuation` 优先从 `swipe.parameters_snapshot["_flow_plugin_id"]` 查插件 `accept_mode`，回退按 `created_by` 推断（兼容旧 swipe）；`replace` 替换当前章节正文，`promote` 提升为新章节，`volume_insert` 直接 return（卷续写内部自建章节）
- **挂起-恢复**：内置 rewrite_current 插件由信号链直接完成两步流程（audit handler 返回 pending → AuditDialog.accepted_text → `_on_rewrite_analysis_accepted` → cancel FlowExecutor）；自定义插件的 audit→continuation 流程可通过 `flow_executor.resume(output)` 推进
- **插件管理器**（`ui/flow_plugin_manager.py`）：继承 `PersistentDialog` 非模态独立窗口；列表/详情/导入/导出/删除（内置禁用）；`plugin_changed` 信号通知 MainWindow 刷新面板下拉；工具菜单「流程插件管理器(&F)」入口
- **开发者文档**：完整插件 JSON 格式、合法取值速查、6 大编写规律、4 个内置插件完整 JSON、自定义插件案例（analyze_then_write）与调试验证见 [`FLOW_PLUGIN_GUIDE.md`](FLOW_PLUGIN_GUIDE.md)（项目根目录，开发者向）

### 19. 写作模式流程（writing_mode）

三步续写流程，面向"先分析再深化最后生成"的精细创作场景。复用 audit agent（不新增 agent 类型），通过通用分析路径 `_on_start_generic_analysis` 实现两个分析阶段。

- **阶段 1 写作要素分析**（`phase_writing_element_analysis.txt`）：7 占位符（`user_input`/`world_ontology`/`style_profile`/`protagonist_profile`/`custom_audit_rules`/`context_entries`/`previous_chapters_text`），低温流式输出 5 分节（出场角色/场所/相关事件/关键伏笔/风格基调）；AuditDialog 供用户审阅，采纳后 `flow_executor.resume` 推进
- **阶段 2 写作要素深化**（`phase_writing_element_refinement.txt`）：4 占位符（`prev_analysis`/`world_ontology`/`style_profile`/`previous_chapters_text`），为每个出场角色产出简化版形象档案（外貌+心理学形象+语言风格+OOC 红线，对标【提取主角形象】但简化）+ 场所精炼 + 其他关键要素；采纳后 resume 推进
- **阶段 3 单章生成**：continuation agent 检查 `created_by=="writing_mode"` + `_prev_output` 非空，走 `_on_start_writing_mode_continuation`：将阶段 2 精炼输出以【写作参考】标签包裹后前置到面板 user_input，调 `_on_start_continuation(user_input_override=combined)` 走单章续写（零侵入 `prompt_assembler`/`ContinuationWorker`）
- **通用分析路径**（`_on_start_generic_analysis`）：镜像 `_on_start_rewrite_current` 结构，差异点：模板由 `stage.params["phase"]` 决定、flow_key 取 `stage.flow_key`、读 `params._prev_output` 注入 `{{prev_analysis}}`（阶段 2 接收阶段 1 输出）、读面板 `lookback_chapters` 构建 `{{previous_chapters_text}}`、采纳后 `flow_executor.resume` 推进（不 cancel）；AuditWorker max_tokens 默认 6000 容纳多角色形象
- **前文构建**：`_build_previous_chapters_text(lookback)` 构建前 lookback 章正文（含当前章，格式 `## {标题}\n\n{正文}`），区别于 rewrite_current 的 `exclude_current=True`（当前章是待重写对象）
- **破限**：`writing_element_analysis`/`writing_element_refinement` 两个 flow_key 默认 `low` 等级（与提取类一致），`jb_writing_element_*.txt` 含 LOW/MID/HIGH 三档
- **accept_mode=promote**：接受后提升为新章节插入当前章之后

## 代码风格规范

- `from __future__ import annotations` 启用延迟类型注解
- 日志用 logging 模块，logger 名为模块路径
- 异常处理：UI 层捕获并提示，服务层记录日志返回错误状态
- 中文注释和文档字符串，技术术语保留英文
- 信号命名：过去式或名词（如 `chunk_received`、`entries_changed`）
- **reindex 禁止用 `save_chapter`**：`list_chapters` 返回的 Chapter `content=""`，save_chapter 会用空 content 覆盖正文文件。reindex 必须用 `update_chapter_index`（只更新 SQL index 列不写文件）；`rename_chapter` 同理用 `update_chapter_title`（只更新 title 列不写文件）
- **切分模式下所有文本占位符必须使用切分后的块文本**：不能注入完整全文，否则切分形同虚设
- **用户指令优先原则**：user_directive_analysis.required_elements/emphasized_elements 是续写硬约束，优先级高于通用规划原则但低于主角 OOC 红线与世界观底层规则；审计严格给分不可因其他维度优秀而豁免
- **前文参考机制按模式分离**：单章用 lookback_chapters 截断聊天历史；卷用 skip_history=True + 动态前文窗口系统消息，避免章节正文在 chat history 与系统消息重复
- **外部数据流入文件路径的 ID 必须经 `validate_id` 校验**（路径构造函数入口 + 服务层入口 + 模型层 field_validator 三重防御）

## 测试要求

- 测试文件位于 `tests/` 目录，命名 `test_*.py`
- 使用 pytest 运行：`python -m pytest tests/ -q`
- E2E 测试使用真实预设文件和小说文件
- UI 测试需排除环境缺失组件：`--ignore=tests/test_m5_polish.py -k "not TestUIComponents"`
- **流式 mock 约束**：`ContextExtractor.extract` 默认走流式路径（`stream=True`，调 `stream_chat_completion`）。测试中 mock `extractor.extract(...)` 时必须 mock `stream_chat_completion`（用 `_StreamChunk` 类 + `async def _mock_stream` 异步生成器 + `MagicMock(side_effect=_mock_stream)`），不得 mock `chat_completion`；仅显式传 `stream=False` 的调用（如 `_extract_protagonist(..., stream=False)`）才 mock `chat_completion`。`_StreamChunk` 已在 test_m4_context_extraction.py / test_protagonist_extraction.py / test_e2e_workflow.py / test_rewrite_current_mode.py 各自定义或复用。流式路径不填充 `token_usage`（仅非流式 `chat_completion` 响应的 `usage` 字段累加），断言 `result.token_usage` 的测试需注意此差异

## 修改后必须更新

每次修改项目代码后，请检查并更新本文件的以下部分：
1. **架构分层**：新增/删除/重命名模块时更新目录树
2. **关键设计决策**：新增重要设计决策时添加条目
3. **UI 布局规范**：修改面板布局时更新布局描述
4. **技术栈**：新增依赖时更新

**每次修改项目代码后，还必须同步更新 `update.md`**：在文件顶部按时间倒序追加新条目，格式含 `## YYYY-MM-DD：标题` / `### 背景` / `### 核心改动` / `### 测试` / `### 文档同步` 等小节。

**修改流程插件系统相关代码后，还必须同步更新 [`FLOW_PLUGIN_GUIDE.md`](FLOW_PLUGIN_GUIDE.md)**（项目根目录，开发者向文档）。以下任一情况发生时必须更新对应章节：

1. **数据模型变更**（更新第 3 节 JSON 格式规范 + 第 4 节合法取值速查）：
   - `FlowPlugin`/`FlowStage` 新增/删除/重命名字段、类型变更、校验规则变更
   - `models/flow_plugin.py` 的 `field_validator`/`model_config` 调整
2. **agent 类型变更**（更新第 4.1 节五种 agent 类型）：
   - 新增/删除 agent 类型（当前 5 种：continuation/audit/checkpoint/volume_pipeline/volume_phase）
   - agent handler 的注册机制或返回值契约变化（pending/resume/cancel）
3. **ui_mode / accept_mode 变更**（更新第 4.2/4.3 节）：
   - 新增/删除 `ui_mode`（当前 2 种：standard/volume）
   - 新增/删除 `accept_mode`（当前 3 种：promote/replace/volume_insert）
4. **flow_key 变更**（更新第 4.4 节八个标准 flow_key）：
   - 新增/删除/重命名标准 `flow_key`（当前 8 个）
   - `flow_endpoints`/`flow_models`/`flow_jailbreaks` 配置项结构变化
5. **执行引擎逻辑变更**（更新第 5 节写插件的 6 大规律 + 第 6 节 volume_phase 阶段顺序）：
   - `FlowExecutor` 阶段推进机制变化（递归→迭代等）、params 合并优先级变化、`input_from` 链式传递规则变化
   - 挂起-恢复机制（pending/resume）或 cancel 清理逻辑变化
6. **服务层行为变更**（更新第 2 节快速开始 + 第 9 节调试与验证）：
   - `FlowPluginService` 导入规则变化（`builtin=False` 强制、ID 冲突后缀）、版本升级逻辑、内置不可删除保护
   - 存储路径变化（`~/.novelforge/flow_plugins/`）
7. **内置插件变更**（更新第 7 节三个内置插件完整 JSON + 第 2 节内置插件表）：
   - `resources/defaults/flow_plugins/` 下 `single.json`/`volume.json`/`rewrite_current.json` 结构或内容变化
   - 新增/删除内置插件
8. **MainWindow 接线变更**（更新第 5 节规律 + 第 9 节调试验证）：
   - `start_flow` 信号入口、5 个 agent handler 分发逻辑、`accept_mode` 适配逻辑（`_on_accept_continuation`）变化
   - `FlowPluginManager` UI 变化（导入/导出/删除按钮、`plugin_changed` 信号）
