# 多轮 Agent 续写 Spec

## Why

当前 NovelForge 仅实现"单次流式续写"（无纲裸写），缺少大纲规划、质量验证、迭代修订三个核心环节。本 spec 将 `novel-continuation-agent` skill 的 6 阶段 agent 工作流落地为软件中可配置的多轮续写模式，与现有单次流程并存，提升续写质量。

> **PRD 审阅结论**：`/workspace/多轮Agent续写PRD.md` 对项目现状的描述高度准确（10 项声明中 9 项准确），但存在 3 处需修正：
> 1. **路径工具归属错误**：PRD 说"复用 `storage.py` 的 `get_*_path` 模式"，实际资源路径工具在 `utils/paths.py`（如 `get_extract_prompt_path`）。`storage.py` 的 `get_*_path` 是用户数据路径工具。
> 2. **存储层迁移遗漏**：PRD 只说扩展 `Continuation.agent_artifacts` 字段（模型层），未提及 `storage.py` 的 SQLite `continuations` 表需 `ALTER TABLE` 迁移 + 更新 save/load SQL。
> 3. **extract_streaming 已存在**：`context_extractor.py` 已有 `extract_streaming` 方法（含 `on_chunk` 回调、`lookback_override`），agent 模式可直接复用，无需新增。
> 4. **skill 不存在于项目中**：`novel-continuation-agent` skill 是外部设计参考，项目内无任何 agent 代码骨架。

## What Changes

- **新增** `novelforge/models/agent.py`：StorySnapshot / Scene / Outline / CritiqueIssue / CritiqueReport / AgentArtifacts / AgentRunConfig 数据模型
- **修改** `novelforge/models/chapter.py`：Continuation 新增 `agent_artifacts: AgentArtifacts | None = None`（向后兼容）
- **修改** `novelforge/models/__init__.py`：导出 agent 模型
- **新增** `novelforge/resources/defaults/agent/`：4 个阶段提示词模板（phase_analysis/outline/verify/revise.txt）
- **新增** `novelforge/utils/paths.py` 中 `get_agent_prompt_path(phase)` 函数（镜像 `get_extract_prompt_path`）
- **新增** `novelforge/core/json_utils.py`：抽取 `_strip_markdown_fences` + 宽松 JSON 解析（供 context_extractor 和 agent_orchestrator 共用）
- **新增** `novelforge/services/agent_orchestrator.py`：AgentOrchestrator（QThread+asyncio，镜像 ContinuationWorker）
- **新增** `novelforge/ui/agent_panel.py`：AgentPanel（阶段开关、暂停点、进度指示器、产物查看器）
- **新增** `novelforge/ui/checkpoint_dialog.py`：暂停点弹窗
- **修改** `novelforge/ui/continuation_panel.py`：顶部新增模式切换（单次/智能）
- **修改** `novelforge/ui/main_window.py`：新增 `_on_start_agent_continuation`，信号接线，模式路由
- **修改** `novelforge/core/storage.py`：continuations 表新增 `agent_artifacts` 列 + schema 迁移 + save/load 更新
- **修改** `novelforge/services/context_extractor.py`：`_strip_markdown_fences` 改为从 `core/json_utils` 导入
- **更新** `agent.md`：架构树、设计决策、UI 布局

## Impact

- **Affected specs**: `build-novel-continuation-tool`（单次续写工具，本 spec 与其并存，不修改单次流程）
- **Affected code**:
  - `models/` — 新增 agent.py，修改 chapter.py、__init__.py
  - `core/` — 新增 json_utils.py，修改 storage.py
  - `services/` — 新增 agent_orchestrator.py，修改 context_extractor.py
  - `ui/` — 新增 agent_panel.py、checkpoint_dialog.py，修改 continuation_panel.py、main_window.py
  - `resources/defaults/` — 新增 agent/ 子目录
  - `utils/paths.py` — 新增路径函数

## ADDED Requirements

### Requirement: 多阶段 Agent 编排

系统 SHALL 提供可配置的多阶段 agent 续写模式，包含 5 个可选阶段（分析/大纲/写作/验证/修订），用户可勾选启用，各组合均合法（优雅降级）。

#### Scenario: 全流程运行
- **WHEN** 用户启用所有阶段并点击"智能续写"
- **THEN** 系统按 ①分析→③大纲→④写作→⑤验证→⑥修订 顺序执行，各阶段产出结构化 JSON 产物，写作阶段流式输出，验证-修订循环至上限或无严重问题

#### Scenario: 优雅降级
- **WHEN** 用户关闭分析阶段
- **THEN** 大纲阶段基于现有 ContextEntry 生成（无 StorySnapshot 注入）
- **WHEN** 用户关闭大纲阶段
- **THEN** 写作阶段直接基于 ContextEntry 续写（无大纲注入）
- **WHEN** 用户关闭验证阶段
- **THEN** 写作完成后直接出稿（不验证不修订）

#### Scenario: 暂停点
- **WHEN** 启用"大纲后暂停"且大纲阶段完成
- **THEN** 系统弹出 checkpoint_dialog 展示大纲，用户可编辑/接受/取消
- **WHEN** 用户接受大纲
- **THEN** 系统继续写作阶段

#### Scenario: 修订循环上限
- **WHEN** 验证未通过且修订轮次 < max_revise_rounds
- **THEN** 系统执行修订→重验证循环
- **WHEN** 修订轮次达到 max_revise_rounds
- **THEN** 系统停止修订，输出当前最佳结果

### Requirement: Agent 产物持久化

系统 SHALL 将 agent 各阶段产物（StorySnapshot/Outline/CritiqueReport/修订轮次）持久化到 Continuation.agent_artifacts 字段，随 swipe 保存到 SQLite。

#### Scenario: 保存 agent 产物
- **WHEN** agent 流程完成
- **THEN** Continuation.agent_artifacts 包含所有阶段产物，序列化为 JSON 存入 continuations 表

#### Scenario: 加载旧 swipe（向后兼容）
- **WHEN** 加载 agent_artifacts 为 None 的旧 Continuation
- **THEN** 正常加载，agent_artifacts 字段为 None，不报错

### Requirement: 模式切换

系统 SHALL 在续写控制面板顶部提供模式切换（单次续写/智能续写），切换时显示/隐藏对应参数区。

#### Scenario: 切换到智能模式
- **WHEN** 用户选择"智能续写（多阶段）"
- **THEN** 显示 AgentPanel（阶段开关、暂停点、max_revise_rounds），隐藏单次参数区
- **WHEN** 用户点击"开始续写"
- **THEN** 路由到 agent 流程

#### Scenario: 切换到单次模式
- **WHEN** 用户选择"单次续写"
- **THEN** 显示单次参数区，隐藏 AgentPanel
- **WHEN** 用户点击"开始续写"
- **THEN** 路由到现有单次流程（零改动）

### Requirement: 阶段提示词独立模板

系统 SHALL 使用独立模板文件（`resources/defaults/agent/phase_*.txt`）管理各阶段提示词，不走 preset prompts[] 系统。宏替换采用简单 `str.replace`（镜像 extract_prompt.txt 机制，不用 MacroEngine/Jinja2）。

#### Scenario: 模板加载
- **WHEN** agent 执行某阶段
- **THEN** 从 `resources/defaults/agent/phase_{phase}.txt` 加载模板，用 str.replace 替换占位符后调用 LLM

#### Scenario: 宏占位符
- **THEN** phase_analysis.txt 含 `{{title}}` `{{author}}` `{{protagonist}}` `{{synopsis}}` `{{world_setting}}` `{{writing_style}}` `{{chapters_text}}`（沿用 extract_prompt 风格）
- **THEN** phase_outline.txt 含 `{{snapshot}}` `{{chapters_text}}` `{{user_input}}`
- **THEN** phase_verify.txt 含 `{{snapshot}}` `{{outline}}` `{{written_text}}`
- **THEN** phase_revise.txt 含 `{{written_text}}` `{{critique}}` `{{outline}}`

### Requirement: 大纲注入复用 ContextEntry

系统 SHALL 将 Outline 格式化为自然语言文本（Markdown 场景列表），序列化为合成 ContextEntry（position=before）注入写作阶段，复用 PromptAssembler 三阶段注入机制。

#### Scenario: 大纲注入
- **WHEN** 写作阶段执行且大纲已生成
- **THEN** Outline 格式化为 Markdown 场景列表文本（非 JSON，避免被宏替换/正则破坏），作为 ContextEntry（uid="agent_outline", position="before", content=格式化文本）并入 context_entries 交给 assemble()

#### Scenario: worldInfoBefore marker 依赖
- **WHEN** 用户 preset 的 prompt_order 无 worldInfoBefore marker
- **THEN** 大纲合成 entry 走 fallback：直接 prepend 到 messages 列表开头作为 system 消息（不依赖 marker）

## MODIFIED Requirements

### Requirement: Continuation 数据模型

Continuation 新增 `agent_artifacts: AgentArtifacts | None = None` 字段，默认 None 向后兼容。单次流程不设置此字段。

### Requirement: SQLite continuations 表

continuations 表新增 `agent_artifacts TEXT` 列（存储 JSON），schema 迁移自动检测旧表并 ALTER TABLE ADD COLUMN。

### Requirement: context_extractor JSON 解析

`_strip_markdown_fences` 从 context_extractor.py 迁移到 `core/json_utils.py`，context_extractor 改为导入使用。

## Assumptions & Decisions

1. **skill 为外部参考**：`novel-continuation-agent` skill 不存在于项目中，PRD 引用的 6 阶段仅作设计参考，本 spec 据此设计实现。
2. **路径工具用 utils/paths.py**：新增 `get_agent_prompt_path(phase)` 镜像 `get_extract_prompt_path`，不用 storage.py。
3. **存储迁移（幂等）**：项目无现有迁移机制，本 spec 引入新模式。continuations 表用 `PRAGMA table_info(continuations)` 检测列是否存在，再决定是否 `ALTER TABLE ADD COLUMN agent_artifacts TEXT`，保证幂等（SQLite 不支持 ADD COLUMN IF NOT EXISTS）。
4. **复用 extract_streaming**：agent 模式开始时若 _current_context_entries is None 则调用现有 extract_streaming（命中缓存零成本）。
5. **phase_revise 策略**：产出修订指导后重跑④写作（更可控、复用流式与后处理），不直接产出修订正文。
6. **StorySnapshot 不持久化到项目**：首版仅随 swipe 快照，不跨章节复用。
7. **每阶段 model 默认沿用用户选定**：per_phase_overrides 留口子但首版不实现独立 model 选择 UI。
8. **JSON 解析失败降级**：失败重试一次（温度归零），再失败则跳过该阶段（优雅降级）。
9. **暂停点线程安全**：UI 线程调 `resume()` 时必须通过 `self._loop.call_soon_threadsafe(self._resume_async_event.set)` 设置 asyncio.Event（不可直接 .set()）。暂停等待用 `asyncio.wait_for(self._resume_event.wait(), timeout=0.5)` 循环轮询，每轮检查 `_stop_event`，确保暂停期间可停止/可被应用关闭中断（超时保护防挂死）。
10. **大纲注入用格式化文本**：Outline 格式化为 Markdown 场景列表（非 JSON），避免被 assembler 的宏替换/正则/WORLD_INFO 处理破坏。若 preset 无 worldInfoBefore marker，走 fallback 直接 prepend 到 messages。
11. **宏替换用 str.replace**：agent 模板沿用 extract_prompt.txt 的简单 str.replace 机制（不用 MacroEngine/Jinja2），在 agent_orchestrator.py 实现独立宏替换函数。
12. **历史日志多阶段记录**：_on_start_agent_continuation 中设置 _continuation_model（写作阶段 model）、_continuation_prompt_messages（写作阶段 messages），_record_history 沿用；agent_artifacts 中的 phase_logs 记录各阶段摘要供后续查询。
13. **SQL 同步**：save_continuation 的 INSERT 列数与 ? 占位符从 15 改为 16，_row_to_continuation 的 row 索引追加 row[15] 并容错 None。
