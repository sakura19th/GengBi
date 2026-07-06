# 更新日志

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
