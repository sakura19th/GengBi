# NovelForge 多轮 Agent 续写流程 PRD

> 状态：设计稿 · 待评审
> 日期：2026-06-23
> 目标：把 `novel-continuation-agent` skill 的 6 阶段 agent 工作流落地为 NovelForge 软件中的可配置多轮续写模式。

---

## 一、背景与差距

### 1.1 Skill 定义的 6 阶段 agent

`novel-continuation-agent/novel-continuation-agent/` 定义了完整的小说续写 agent 工作流：

1. **前文分析（Source Text Analysis）** — 结构定位、张力曲线、活跃剧情线、人物状态、伏笔清单、世界状态、风格指纹。
2. **要素快照（Element Extraction Report）** — 结构化的 STORY STATUS SNAPSHOT（活跃人物 / 剧情线 / 未兑现承诺 / 伏笔追踪 / 世界状态 / 风格档案）。
3. **续写大纲（Continuation Outline）** — 3~7 个场景，每个含 purpose / pov / scene type / goal-conflict-outcome / value shift / foreshadowing / exit hook。
4. **细化写作（Detailed Writing）** — 场景构建、人物声音匹配、描写与内心活动。
5. **质量验证（Quality Verification）** — 一致性 / 风格匹配 / 结构 / 吸引力四维 checklist。
6. **迭代修订（Iterative Refinement）** — 定位问题 → 根因 → 定向修订 → 复核，循环至通过。

### 1.2 软件当前实现（仅覆盖一小片）

| Skill 阶段 | 软件现状 | 缺口 |
|---|---|---|
| ①前文分析 | `ContextExtractor` 做浅层 JSON 提取（8 类、每条 ≤500 字、扁平 `ContextEntry`，面向**注入**而非**推理**） | 无深度结构化快照（张力曲线、契诃夫之枪、风格指纹等） |
| ②要素快照 | 同上，扁平条目 | 无 STORY STATUS SNAPSHOT 整体视图 |
| ③续写大纲 | **无** | 完全缺失 |
| ④细化写作 | `ContinuationWorker` 单次流式 prose 调用 | 已实现，但是"无纲裸写" |
| ⑤质量验证 | **无** | 完全缺失 |
| ⑥迭代修订 | **无**（仅人肉：重写=新 swipe、接受并继续=手动串接） | 完全缺失 |

**结论**：软件只执行了 6 阶段中的 ①②简化版 + ④写作，缺 ③大纲 / ⑤验证 / ⑥修订三个核心环节。本次 PRD 即补齐这三环，并把全流程串成可配置的多轮 agent 编排器。

### 1.3 用户已确认的产品决策

1. **运行模式 = 可配置开关**：每阶段可勾选 + 暂停点可选，每次续写前用户自选组合。
2. **与现有流程 = 并存**：保留「开始续写」单次流程，新增「智能续写（多阶段）」模式。
3. **验证-修订 = 自动修订（有上限）**：验证产出结构化问题 → 自动定向修订 → 回验证，循环至上限或无严重问题。

---

## 二、总体架构

```
                      ┌──────────── AgentOrchestrator (新, QThread+asyncio) ────────────┐
                      │  复用 ContinuationWorker 的线程模型 + LLMClient + stop/cancel     │
                      │                                                                  │
  现有提取(复用,缓存) │  ①分析 _run_analysis  → StorySnapshot (新模型, JSON)              │
  ContextEntry ──────►│        │ (注入基底, 不变)                                          │
                      │        ▼                                                          │
                      │  ②[已含于①]                                                       │
                      │        ▼                                                          │
                      │  ③大纲 _run_outline   → Outline[Scene] (新模型, JSON)             │
                      │        │  [可选暂停点: 用户确认/编辑大纲]                          │
                      │        ▼                                                          │
                      │  ④写作 _run_writing   → PromptAssembler.assemble() (复用)         │
                      │        │  + 大纲作为合成 ContextEntry 注入 → stream_chat_completion│
                      │        │  chunk_received 实时推 UI (复用现有流式显示)             │
                      │        ▼                                                          │
                      │  ⑤验证 _run_verify    → CritiqueReport (新模型, JSON)             │
                      │        │  [可选暂停点: 用户查看评审]                               │
                      │        ▼                                                          │
                      │  ⑥修订 _run_revise    (若 Critique 有严重问题 & 轮次<上限)        │
                      │        │  → 重跑④(带评审上下文) → 回⑤   自动循环 ≤ max_rounds     │
                      │        ▼                                                          │
                      │  finished(Continuation)  ← agent_artifacts 快照                  │
                      └──────────────────────────────────────────────────────────────────┘
```

### 2.1 最大化复用点

- **`LLMClient`**（stream + non-stream + `stop_event` + AuthError/RateLimitError/APIError 错误类型）—— 各阶段共用同一 client。
- **`ContinuationWorker` 的 QThread+asyncio 桥接 + `threading.Event`/`asyncio.Task.cancel` 双重中断** —— `AgentOrchestrator` 镜像该模式。
- **`PromptAssembler.assemble()`** —— ④写作阶段直接复用，大纲以**合成 `ContextEntry`**（`position=before` 或 `at_depth`）注入，走现有三阶段注入机制，零侵入。
- **`ContextExtractor`** —— 不改；agent 模式开始时若 `_current_context_entries is None` 则自动触发提取（命中 24h 缓存则零成本）。
- **`macros.py` / `template_engine.py`** —— 阶段提示词模板的宏替换与 Jinja2 渲染。
- **`Continuation` 快照模型** —— 扩展一个可选 `agent_artifacts` 字段，旧 swipe 默认空、向后兼容。
- **现有流式 UI**（⑤列 `output_edit`、`append_chunk`、推理内容折叠区、token 状态栏）—— ④写作阶段直接复用信号。

### 2.2 阶段提示词策略

采用"独立模板"路线（镜像 `extract_prompt.txt`，而非塞进 preset 的 `prompts[]`），新增 `resources/defaults/agent/*.txt`。
> 理由：explore 确认 assembler 的 marker/depth 逻辑专为 world-info + history 注入设计，不适合多步 agent 控制；preset 系统仅服务于④写作阶段。

---

## 三、实现里程碑

### M1 — 数据模型（`novelforge/models/`）

新增 pydantic 模型 `agent.py`，对齐 skill 的结构化产物：

- `StorySnapshot`：structure_position / tone / core_conflict_status / stakes / active_characters[] / plot_threads[] / unresolved_promises[] / foreshadowing_tracker[] / world_state / style_profile（对齐 skill 的 STORY STATUS SNAPSHOT）
- `Scene`：purpose / pov / scene_type / goal / conflict / outcome / value_shift / foreshadowing / exit_hook
- `Outline`：continuation_goals / foreshadowing_plan / scenes: list[Scene]
- `CritiqueIssue`：category(consistency/style/structure/engagement) / severity(critical/major/minor) / location / description / suggestion
- `CritiqueReport`：summary / issues: list[CritiqueIssue] / passed: bool
- `AgentArtifacts`：snapshot / outline / critique / final_critique / revision_rounds: int / phase_logs: list[dict]
- `AgentRunConfig`：phases(dict 开关: analysis/outline/writing/verify/revise) / checkpoints(dict) / max_revise_rounds / per_phase_overrides(model/temperature)

扩展 `chapter.py` 的 `Continuation`：新增 `agent_artifacts: AgentArtifacts | None = None`（向后兼容）。
更新 `models/__init__.py` 导出。

### M2 — 阶段提示词模板（`novelforge/resources/defaults/agent/`）

4 个独立模板，均要求严格 JSON 输出（参照 `extract_prompt.txt` 风格 + 提取原则）：

- `phase_analysis.txt`：注入 `{{novel_profile}}` + `{{chapters_text}}`，产出 `StorySnapshot` JSON（深度版本：含张力曲线、契诃夫之枪清单、风格指纹）。
- `phase_outline.txt`：注入 `{{snapshot}}` + `{{chapters_text}}` + `{{user_input}}`，产出 `Outline` JSON（3-7 场景，含 value_shift/foreshadowing/exit_hook）。
- `phase_verify.txt`：注入 `{{snapshot}}` + `{{outline}}` + `{{written_text}}`，产出 `CritiqueReport` JSON（4 类 × 严重度，附定位与建议）。
- `phase_revise.txt`：注入 `{{written_text}}` + `{{critique}}` + `{{outline}}`，产出修订指导（或直接产出修订后正文）。

配套 `resources/defaults/agent/__init__.py` 与路径工具（复用 `storage.py` 的 `get_*_path` 模式）。

### M3 — AgentOrchestrator 服务（`novelforge/services/agent_orchestrator.py`，新）

镜像 `ContinuationWorker` 的 QThread+asyncio 模式：

- `__init__(config: AgentRunConfig, base_url, api_key, model, preset, chapters, current_chapter, context_entries, novel_profile, params, prompt_assembler, regex_engine, template_engine, ...)`
- 信号：
  - `phase_started(str)` / `phase_finished(str, object)`
  - `chunk_received(str)` / `reasoning_received(str)`
  - `phase_progress(str, int)`
  - `checkpoint_reached(str, object)`
  - `finished(object)` / `error(str)` / `auth_error()`
  - `token_count(int)` / `token_budget_info(dict)`
- `run()` → asyncio loop → `_async_run()`：按 `config.phases` 勾选顺序执行；缺失前置则优雅降级（如关闭①则③大纲退化为仅基于 ContextEntry）。
- 阶段方法：`_run_analysis()` / `_run_outline()` / `_run_writing()` / `_run_verify()` / `_run_revise()`，均用 `LLMClient`（①③⑤⑥ non-stream + JSON 解析；④ stream）。
- **④写作**：调 `prompt_assembler.assemble(context_entries=existing + [outline合成entry])` → `stream_chat_completion` → `chunk_received`；末尾走与 `ContinuationWorker` 相同的正则/模板/HTML 后处理。
- **⑥修订循环**：`while not critique.passed and rounds < max_revise_rounds:` → 修订 → 重验证；每轮记入 `revision_rounds`。
- **暂停点**：emit `checkpoint_reached` 后 `await` 一个 `threading.Event`（`_resume_event`），UI 调 `resume(approved_artifact_or_None)` 唤醒；支持取消。
- **停止**：复用 `threading.Event` + `asyncio.Task.cancel`。
- **JSON 解析**：复用 `context_extractor._strip_markdown_fences` 思路（抽到 `core/json_utils.py` 共用）。
- **最终产物**：构建 `Continuation`（含 `agent_artifacts`），emit `finished`。
- **默认 per-phase 参数**：①③⑤⑥ 低温度（0.2~0.4），④用用户参数；可被 `per_phase_overrides` 覆盖。

### M4 — UI（`novelforge/ui/`）

- `continuation_panel.py`：顶部新增**模式切换**（`单次续写` / `智能续写（多阶段）` 单选）。切到智能模式时显示 `AgentPanel`，隐藏单次参数区（反之亦然）。
- `agent_panel.py`（新）：
  - 阶段开关复选框：①分析 ②大纲 ③写作 ④验证 ⑤修订（③写作默认锁定开启）。
  - 暂停点复选框：大纲后 / 验证后。
  - `max_revise_rounds` SpinBox（1~3，默认 1）。
  - 复用现有 endpoint/model/temperature/target_words/max_tokens 控件（④写作参数）。
  - 阶段进度指示器（①→②→③→④→⑤ 步进条，高亮当前阶段）。
  - 产物查看器：大纲预览（可编辑，暂停点用）、评审报告（4 类折叠组，复用 `context_preview_panel` 的 `QGroupBox` 折叠风格）。
- `checkpoint_dialog.py`（新）：暂停点弹窗。大纲暂停 = 可编辑 `QPlainTextEdit` + 接受/编辑后继续；验证暂停 = 只读评审报告 + 修订/接受/重写。
- 复用⑤列 `output_edit` 显示④写作流式正文；推理内容折叠区复用。

### M5 — MainWindow 编排与存储（`novelforge/ui/main_window.py`）

- 新增 `_on_start_agent_continuation(params)`：镜像 `_on_start_continuation`，但构建 `AgentOrchestrator`；先确保提取已完成（`None` 则自动触发提取，命中缓存零成本）。
- 信号接线：
  - `phase_started` → 步进条更新
  - `phase_finished` → 产物查看器填充
  - `chunk_received` → `append_chunk`
  - `checkpoint_reached` → 弹 `checkpoint_dialog`
  - `finished` → `_on_continuation_finished`（复用现有保存/显示/历史逻辑，swipe 自带 agent_artifacts）
- 模式切换根据 panel 模式路由到单次/agent 处理器。
- Ctrl+Enter / 按钮根据当前模式触发对应流程。
- 历史日志（`HistoryService`）扩展记录各阶段摘要。

### M6 — 测试与文档

- `tests/test_agent_models.py`：pydantic 校验 + 向后兼容（旧 swipe 无 agent_artifacts 能加载）。
- `tests/test_agent_orchestrator.py`：mock `LLMClient`，覆盖各阶段、暂停点、修订循环上限、优雅降级、停止/取消。
- `tests/test_agent_prompts.py`：模板宏替换与 JSON 约束。
- E2E：用真实 preset + `穿进赛博游戏后干掉BOSS成功上位.txt`，mock LLM 跑全流程。
- 更新 `agent.md`：架构树（新增 `models/agent.py`、`services/agent_orchestrator.py`、`ui/agent_panel.py`、`resources/defaults/agent/`）、关键设计决策（多阶段 agent）、UI 布局（模式切换）、技术栈。

---

## 四、关键设计决策

1. **大纲注入复用 ContextEntry 机制**：④写作时把 `Outline` 序列化为一个合成 `ContextEntry`（`position=before` 或 `at_depth,depth=1`），并入现有 `context_entries` 交给 `assemble()`，零侵入复用三阶段注入。无需改 preset。
2. **分析与注入分离**：`StorySnapshot`（①）面向 agent 自身推理；`ContextEntry`（现有提取）面向 prose 注入。两者并存、各司其职，避免把推理用的大段结构塞进 prose 上下文。
3. **阶段提示词走独立模板**而非 preset `prompts[]`（assembler 的 marker/depth 逻辑专为 world-info+history 设计，不适合多步 agent 控制）。
4. **优雅降级**：关闭①→③大纲基于 ContextEntry；关闭③→④直接写；关闭⑤→不验证直接出稿；关闭⑥→验证完即止。各开关组合均合法。
5. **向后兼容**：`Continuation.agent_artifacts` 默认 None；单次流程完全不动；旧项目/swipe 正常加载。
6. **成本控制**：①③⑤⑥用低温度（必要时可用更便宜模型，`per_phase_overrides` 支持每阶段独立 model）；提取命中 24h 缓存；修订循环有上限。

---

## 五、风险与回退

| 风险 | 缓解 |
|---|---|
| 多阶段 = 多次 LLM 调用，延迟与成本上升 | 可配置开关按需启用；缓存提取；修订上限；非写作阶段可用快/便宜模型 |
| JSON 解析失败（模型不守格式） | 复用 `_strip_markdown_fences` + 宽松解析 + 失败重试一次（温度归零）+ 降级为跳过该阶段 |
| 暂停点阻塞线程 | `threading.Event` + 明确取消路径；超时保护 |
| 回退 | 所有改动新增为主，单次流程零改动；出问题切回「单次续写」模式即可，无数据迁移 |

---

## 六、执行顺序

M1（模型）→ M2（模板）→ M3（编排器）→ M4（UI）→ M5（接线）→ M6（测试+文档）。
每个里程碑可独立提交、可 review。

---

## 七、附：阶段-产物-信号对照表

| 阶段 | 产物模型 | LLM 调用 | 默认温度 | 信号 | 暂停点 |
|---|---|---|---|---|---|
| ①分析 | `StorySnapshot` | non-stream | 0.2 | `phase_started/finished` | 无 |
| ②快照 | （含于①） | — | — | — | — |
| ③大纲 | `Outline` | non-stream | 0.3 | `phase_started/finished` + `checkpoint_reached` | 大纲后（可选） |
| ④写作 | prose（`Continuation.content`） | stream | 用户值 | `chunk_received` / `reasoning_received` / `token_count` | 无 |
| ⑤验证 | `CritiqueReport` | non-stream | 0.2 | `phase_started/finished` + `checkpoint_reached` | 验证后（可选） |
| ⑥修订 | 修订后 prose | stream（重跑④）| 用户值 | 同④ | 无 |

---

## 八、待确认 / 开放问题

1. `phase_revise` 是"产出修订指导后再重跑④写作"，还是"直接产出修订后正文"？倾向前者（更可控、可复用④的流式与后处理），待实现时定。
2. `StorySnapshot` 是否要持久化到项目（跨章节复用，类似提取缓存）？首版不持久化，仅随 swipe 快照；后续按需加。
3. 每阶段独立 model 的默认值：是否给 ①③⑤⑥ 默认配一个更便宜/更快的模型？首版默认沿用用户选定 model，`per_phase_overrides` 留口子。
