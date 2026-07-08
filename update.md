# 更新日志

## 2026-07-07：加强默认预设防全知设定

参考【预设参考】中 5 个预设（梦鲸思客/夏瑾/百家饭/Femiris/TGbreak）的反全知模式，在默认预设的"要求"（nf_core_rules/nf_anti_bagua）、"思维链要求"（nf_cot/main）以及审计相关流程（phase_single_audit/phase_verify/phase_outline_audit/phase_audit_rewrite）中提出严格的防全知要求。纯内容增强，不新增/删除预设模块、不改变审计维度结构与 category 取值。

### 背景

原默认预设的反全知设定偏弱：nf_core_rules 无反全知硬约束；nf_anti_bagua 第六节仅 6 条简略规则；nf_cot 思维链无认知边界核查专项；phase_single_audit/phase_verify 的"认知越界检查"仅 3 条且嵌套于 rigid_ai_text 下；phase_outline_audit 仅顺带提及"认知越界"；phase_audit_rewrite 无反全知约束。参考预设普遍设有独立的反全知/信息差模块，需将这些要点融入默认预设的现有模块。

### 核心改动

#### 修改
- `novelforge/resources/defaults/default_preset.json`：
  - `nf_core_rules` 新增第 7 条反全知硬约束（角色认知边界/信息差/信息传递路径/剧情奴隶化）
  - `nf_anti_bagua` 第六节"认知边界要求（反全知）"由 6 条扩充至 10 条：第 6 条改写加入认知隔离；新增第 7 条信息传递路径（源自夏瑾）、第 8 条 POV 边界界定（源自百家饭/Femiris）、第 9 条元词汇禁令（源自夏瑾）、第 10 条信息差叙事价值（源自梦鲸思客）
  - `nf_cot` 思维链新增第 6 项"认知边界与信息差核查"分析
  - `main` 主提示思维链新增第 5 项"认知边界与信息差核查"
- `novelforge/resources/defaults/agent/phase_single_audit.txt`：8.6 认知越界检查由 3 条扩至 7 条（新增信息传递路径/视角认知隔离/元词汇/剧情奴隶化）；严格给分补充严重全知越界判 major；【刻板AI文本审计】标记段落补强须陈述认知越界结果
- `novelforge/resources/defaults/agent/phase_verify.txt`：16.6 认知越界检查镜像扩至 7 条；严格给分补充；【刻板AI文本审计】标记段落补强
- `novelforge/resources/defaults/agent/phase_outline_audit.txt`：rigid_ai_text 维度"认知越界"显式化（含大纲情节信息传递路径/视角越界/元词汇/剧情奴隶化）
- `novelforge/resources/defaults/agent/phase_audit_rewrite.txt`：核心要求新增"认知边界一致性"约束
- `agent.md`：default_preset.json/phase_single_audit.txt/phase_verify.txt 描述补充防全知要点

### 测试
- `python -m pytest tests/test_preset_models.py tests/test_e2e_workflow.py tests/test_m2_prompt_assembly.py tests/test_tgbreak_e2e.py tests/test_single_audit.py tests/test_volume_prompts.py -q` 全绿，确认预设可加载、结构合法、审计模板未被破坏
- `python -c "import json; json.load(open('novelforge/resources/defaults/default_preset.json', encoding='utf-8'))"` 确认 JSON 合法

### 文档同步
- `agent.md`：架构分层目录树中 default_preset.json/phase_single_audit.txt/phase_verify.txt 注释同步更新

## 2026-07-07：流程破限配置（正文前置 + 非正文流程按等级注入）

把正文流程的 5 个 `nf_jb_*` 破限模块在 `default_preset.json` 的 `prompt_order` 中从末尾移到 `main` 之前，使破限 system 消息在组装后位于「你是一位专业的小说续写助手」之前定调；为 6 个非正文流程（single_audit/rewrite_analysis/context_extraction/ontology_extraction/protagonist_extraction/custom_rule_parsing）每个创建专用破限模板（含 LOW/MID/HIGH 三档，按流程风格定制），运行时按配置作为 system 消息前置到 messages 开头；在【端点流程配置】对话框为 6 个非正文流程增加破限等级下拉（关闭/低/中/高/自定义）+ 自定义文本编辑入口。

### 背景

原架构中正文流程的破限模块排在 `prompt_order` 末尾，削弱了「前置定调」效果；6 个非正文流程（不走预设 prompt_order，用 str.replace 拼 .txt 模板）完全无破限支持，提取含暴力/敏感内容的小说时易被模型拒绝。

### 核心改动

#### 新增
- `novelforge/resources/defaults/jailbreaks/`：6 个流程专用破限模板（jb_context_extraction.txt / jb_ontology_extraction.txt / jb_protagonist_extraction.txt / jb_single_audit.txt / jb_rewrite_analysis.txt / jb_custom_rule_parsing.txt），每文件含 `### LOW/MID/HIGH ###` 三档
- `novelforge/services/jailbreak_provider.py`：`JailbreakProvider` 类，加载 `jb_{flow}.txt` 按 `### LEVEL ###` 标记分段返回文本，文件缓存
- `novelforge/ui/jailbreak_custom_dialog.py`：自定义破限文本编辑对话框（QPlainTextEdit + 确定/取消）

#### 修改
- `novelforge/resources/defaults/default_preset.json`：`prompt_order` 中 5 个 `nf_jb_*` 条目从末尾移到 `main` 之前（`nf_jb_prefill` 行为不变，ABSOLUTE 注入仍按深度注入末尾）
- `novelforge/core/config.py`：新增 `FLOW_DEFAULT_JAILBREAKS` 常量（提取类 low 其余 off）+ `flow_jailbreaks`/`flow_jailbreaks_custom` 两个 dict 字段 + 5 个 get/set 方法
- `novelforge/ui/flow_endpoint_dialog.py`：新增「破限配置（非正文流程）」QGroupBox + 6 行等级下拉 + 自定义编辑按钮
- `novelforge/ui/main_window.py`：新增 `_get_flow_jailbreak_text`/`_inject_jailbreak` 辅助方法 + `self._jailbreak_provider`；6 个非正文流程调用点注入破限（single_audit/rewrite_analysis 直接注入 messages；context/ontology/protagonist/custom_rule 通过 `jailbreak_text` 参数透传到服务层）
- `novelforge/services/context_extractor.py`：`extract`/`extract_streaming`/`_extract_common`/`_run_merge_entries`/`_extract_protagonist`/`_run_protagonist_merge`/`extract_protagonist_streaming` 签名增加 `jailbreak_text` 参数 + 3 处注入点
- `novelforge/services/ontology_extractor.py`：`extract_ontology_streaming`/`_run_ontology_merge` 签名增加 `jailbreak_text` 参数 + 2 处注入点
- `novelforge/services/custom_audit_rule_service.py`：`parse_rule_streaming` 签名增加 `jailbreak_text` 参数 + 1 处注入点
- `agent.md`：新增 §15「流程破限配置」小节；架构分层目录树同步新增 `jailbreak_provider.py`/`jailbreaks/`/`jailbreak_custom_dialog.py`

### 测试
- `python -m pytest tests/ -q` 全绿（591 passed, 15 skipped, 12 deselected）
- `tests/test_flow_endpoint_config.py` 同步：`_combos` → `_endpoint_combos`（适配变更 5 对话框重构）；`test_flow_endpoint_dialog_has_7_flows` → `test_flow_endpoint_dialog_has_8_flows`（适配 FLOW_DEFINITIONS 从 7 项扩至 8 项）

### 文档同步
- agent.md 架构分层 + §15 关键设计决策
- update.md 顶部追加本条目

## 2026-07-06（更新）：优化重写分析提示词，强调不推进剧情

优化 [phase_rewrite_analysis.txt](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-rewrite-current-chapter-SRsOzk/novelforge/resources/defaults/agent/phase_rewrite_analysis.txt) 提示词模板，明确强化「不推进剧情」「仅针对当前章节改写」的硬约束。原模板缺少明确的「重写边界」声明，且 L48「需新增的内容：列出用户需求中要求新增的剧情/场景/描写」易引导 LLM 推进剧情或续写下一章。

### 修改
- **L1 角色定位强化**：首句新增「重写仅针对当前章节已有内容进行改写，不得推进剧情、不得续写下一章、不得扩展到当前章节时间范围之外的事件」边界声明。
- **新增「# ⚠️ 重写边界」硬约束块**（L26-34）：6 条禁止行为（不推进剧情/不续写下一章/不新增场景/不引入新角色/不解决未决伏笔）+ 1 条允许调整清单（修改对话措辞/描写细节/内心独白/结尾性质/场景内节奏）+ 1 条判断标准（读者信息量检验）。
- **修订「分析原则」**：新增「重写边界最高优先」原则（优先级高于用户需求），明确用户需求与不推进冲突时在 conflicts 标注并建议改用续写模式。
- **修订「## 当前章节分析」**：将「剧情要点」改为「事件范围与场景边界」，要求分析步骤先明确时间线范围与场景列表作为重写边界。
- **修订「## 重写要点」**：将「需新增的内容」改为「需补充的内容（原章节已有场景内补充描写/对话/细节/内心独白）」，明确不得新增场景/角色/推进时间线。
- **修订「## 具体要求清单」**：首位新增「重写边界类（最高优先）」硬约束，结构类新增「不得为下一章铺垫」。
- **修订「# 输出要求」**：新增「重写边界类要求必须列在清单首位」（生成步骤须首先校验不推进剧情）。

### 边界定义（宽松边界）
允许在当前章节时间范围内调整（修改结尾性质、增加内心独白、调整对话措辞、补充描写细节），但不得发生当前章节时间范围之外的事件、不得续写下一章、不得新增场景/角色。依据：用户原始需求示例「将悲剧结尾改为开放式结局，增加主角内心独白」暗示允许在当前章节内做实质性调整。

## 2026-07-06：新增「重写当前章节」模式

在「单章续写」与「卷续写」之外新增第三种续写模式「重写当前章节」。该模式用两步流程重写当前章节正文：① 分析当前章节正文 + 用户输入需求，输出结构化的「新章节生成详细需求」；② 检查点暂停（AuditDialog）供用户审阅/编辑；③ 复用单章续写的 `prompt_assembler.assemble` + `ContinuationWorker` 生成新正文，存为 swipe（`created_by="rewrite_current"`），接受时用新内容**替换**当前章节正文（`replace_chapter_content`，不新建章节）。

关键约束：重写模式下「前文提取」与「聊天历史构建」不得包含当前章节（当前章节是待重写对象，不是前文）。

### 新增
- `novelforge/resources/defaults/agent/phase_rewrite_analysis.txt`：分析步骤提示词模板（6 占位符 + 5 输出分节）。
- `novelforge/services/chapter_service.py:replace_chapter_content`：用续写内容替换当前章节正文（与 `promote_continuation_to_chapter` 并列，不新建章节、不后移 index）。
- `novelforge/ui/continuation_panel.py:rewrite_current_analysis_requested` 信号 + 模式第三项 `"rewrite_current"`。
- `novelforge/ui/flow_endpoint_dialog.py:rewrite_analysis` 流程端点（`FLOW_DEFINITIONS` 从 7 项增至 8 项）。
- `novelforge/ui/main_window.py:_on_start_rewrite_current` / `_on_rewrite_analysis_accepted` / `_on_rewrite_analysis_finished/error/cancelled`：分析→检查点→生成两步流程。
- `tests/test_rewrite_current_mode.py`：9 个测试类 20 个用例覆盖 exclude_current / replace_chapter_content / 模板 / Panel / 流程端点 / 接受分支。

### 修改
- `novelforge/services/context_extractor.py`：`extract` / `extract_streaming` / `_get_lookback_chapters` / `_build_cache_key` / `_extract_common` 新增 `exclude_current: bool = False` 参数（True 时排除当前章节 + 缓存 key 带 `:rewrite` 后缀）。
- `novelforge/core/prompt_assembler.py`：`assemble` / `_build_history` 新增 `exclude_current: bool = False` 参数（True 时聊天历史不含当前章节）。
- `novelforge/ui/main_window.py:_on_accept_continuation`：新增 `created_by=="rewrite_current"` 分支调 `replace_chapter_content`；`_on_extract_requested` 按模式分发 `exclude_current`。
- `tests/test_m4_context_extraction.py`：补充 `TestContextExtractorExcludeCurrent` 类（3 个用例：缓存 key 后缀 + lookback 排除 + lookback 截断）。
- `tests/test_m2_prompt_assembly.py`：补充 `test_build_history_exclude_current` / `test_build_history_exclude_current_with_lookback`（2 个用例）。
- `agent.md`：新增 §14「当前章节重写模式」小节（模式枚举 / exclude_current 参数 / 分析模板 / 流程端点 / 两步流程 / 接受逻辑 / 提取入口分发 / 主角形象档案 / 切换模式）。
