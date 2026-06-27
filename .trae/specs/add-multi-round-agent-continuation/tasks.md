# Tasks

- [x] Task 1: 创建 agent 数据模型（models/agent.py）
  - [x] SubTask 1.1: 创建 `novelforge/models/agent.py`，定义 StorySnapshot / Scene / Outline / CritiqueIssue / CritiqueReport / AgentArtifacts / AgentRunConfig pydantic 模型
  - [x] SubTask 1.2: 修改 `novelforge/models/chapter.py`，Continuation 新增 `agent_artifacts: AgentArtifacts | None = None`
  - [x] SubTask 1.3: 修改 `novelforge/models/__init__.py`，导出 agent 模型
  - [x] SubTask 1.4: 编写 `tests/test_agent_models.py`，验证 pydantic 校验 + 向后兼容（旧 swipe 无 agent_artifacts 能加载）

- [x] Task 2: 存储层迁移（storage.py）
  - [x] SubTask 2.1: 修改 `novelforge/core/storage.py`，continuations 表 schema 新增 `agent_artifacts TEXT` 列
  - [x] SubTask 2.2: 实现幂等迁移逻辑（PRAGMA table_info 检测列是否存在，再 ALTER TABLE ADD COLUMN，保证多次启动不报错）
  - [x] SubTask 2.3: 更新 `save_continuation`（INSERT 列数 15→16，占位符同步）和 `_row_to_continuation`（row[15] 容错 None）处理 agent_artifacts 序列化/反序列化
  - [x] SubTask 2.4: 验证旧 Continuation（无 agent_artifacts）能正常加载

- [x] Task 3: 抽取 JSON 工具（core/json_utils.py）
  - [x] SubTask 3.1: 创建 `novelforge/core/json_utils.py`，迁移 `_strip_markdown_fences` + 新增 `parse_json_response`（宽松解析，含重试逻辑）
  - [x] SubTask 3.2: 修改 `novelforge/services/context_extractor.py`，`_strip_markdown_fences` 改为从 `core/json_utils` 导入
  - [x] SubTask 3.3: 验证 context_extractor 测试仍通过

- [x] Task 4: 阶段提示词模板（resources/defaults/agent/）
  - [x] SubTask 4.1: 创建 `novelforge/resources/defaults/agent/phase_analysis.txt`（注入 novel_profile + chapters_text，产出 StorySnapshot JSON）
  - [x] SubTask 4.2: 创建 `novelforge/resources/defaults/agent/phase_outline.txt`（注入 snapshot + chapters_text + user_input，产出 Outline JSON）
  - [x] SubTask 4.3: 创建 `novelforge/resources/defaults/agent/phase_verify.txt`（注入 snapshot + outline + written_text，产出 CritiqueReport JSON）
  - [x] SubTask 4.4: 创建 `novelforge/resources/defaults/agent/phase_revise.txt`（注入 written_text + critique + outline，产出修订指导 JSON）
  - [x] SubTask 4.5: 在 `novelforge/utils/paths.py` 新增 `get_agent_prompt_path(phase: str)` 函数

- [x] Task 5: AgentOrchestrator 服务（services/agent_orchestrator.py）
  - [x] SubTask 5.1: 创建 `novelforge/services/agent_orchestrator.py`，镜像 ContinuationWorker 的 QThread+asyncio 模式
  - [x] SubTask 5.2: 实现信号定义（phase_started/finished、chunk_received、reasoning_received、checkpoint_reached、finished、error、auth_error、token_count）
  - [x] SubTask 5.3: 实现 `_async_run()` 按 config.phases 顺序执行，缺失前置则优雅降级
  - [x] SubTask 5.4: 实现 `_run_analysis()` / `_run_outline()`（non-stream + JSON 解析）
  - [x] SubTask 5.5: 实现 `_run_writing()`（调 prompt_assembler.assemble + 大纲格式化为 Markdown 文本合成 ContextEntry position=before + worldInfoBefore marker fallback + stream_chat_completion + 后处理）
  - [x] SubTask 5.6: 实现 `_run_verify()`（non-stream + JSON 解析）
  - [x] SubTask 5.7: 实现 `_run_revise()` 修订循环（while not passed and rounds < max）
  - [x] SubTask 5.8: 实现暂停点（emit checkpoint_reached + asyncio.wait_for(_resume_event.wait, 0.5) 循环轮询 + 每轮检查 _stop_event + resume 通过 call_soon_threadsafe 线程安全唤醒 + 取消支持）
  - [x] SubTask 5.9: 实现停止/取消（threading.Event + asyncio.Task.cancel）
  - [x] SubTask 5.10: 实现 agent 模板宏替换函数（str.replace，占位符清单：title/author/protagonist/synopsis/world_setting/writing_style/chapters_text/snapshot/outline/written_text/critique/user_input）
  - [x] SubTask 5.11: 构建 Continuation（含 agent_artifacts）并 emit finished
  - [x] SubTask 5.12: 编写 `tests/test_agent_orchestrator.py`（mock LLMClient，覆盖各阶段、暂停点、修订循环、降级、停止）

- [x] Task 6: AgentPanel UI（ui/agent_panel.py）
  - [x] SubTask 6.1: 创建 `novelforge/ui/agent_panel.py`，阶段开关复选框（分析/大纲/写作/验证/修订，写作默认锁定开启）
  - [x] SubTask 6.2: 暂停点复选框（大纲后/验证后）+ max_revise_rounds SpinBox（1~3，默认1）
  - [x] SubTask 6.3: 阶段进度指示器（步进条，高亮当前阶段）
  - [x] SubTask 6.4: 产物查看器（大纲预览可编辑、评审报告折叠组）
  - [x] SubTask 6.5: 信号定义（config_changed、resume、cancel_checkpoint）

- [x] Task 7: CheckpointDialog UI（ui/checkpoint_dialog.py）
  - [x] SubTask 7.1: 创建 `novelforge/ui/checkpoint_dialog.py`，大纲暂停 = 可编辑 QPlainTextEdit + 接受/编辑后继续/取消
  - [x] SubTask 7.2: 验证暂停 = 只读评审报告 + 修订/接受/重写

- [x] Task 8: 模式切换与 MainWindow 接线
  - [x] SubTask 8.1: 修改 `novelforge/ui/continuation_panel.py`，顶部新增模式切换（单次/智能），切换时显示/隐藏对应面板
  - [x] SubTask 8.2: 修改 `novelforge/ui/main_window.py`，新增 `_on_start_agent_continuation`
  - [x] SubTask 8.3: 信号接线（phase_started→步进条、phase_finished→产物查看器、chunk_received→append_chunk、checkpoint_reached→弹窗、finished→_on_continuation_finished）
  - [x] SubTask 8.4: 模式路由（根据 panel 模式路由到单次/agent 处理器）
  - [x] SubTask 8.5: Ctrl+Enter / 按钮根据当前模式触发对应流程
  - [x] SubTask 8.6: agent 模式开始时若 _current_context_entries is None 则自动触发 extract_streaming
  - [x] SubTask 8.7: 在 _on_start_agent_continuation 中设置 _continuation_model（写作阶段 model）和 _continuation_prompt_messages（写作阶段 messages），确保 _record_history 正常工作

- [x] Task 9: 测试与文档
  - [x] SubTask 9.1: 编写 `tests/test_agent_prompts.py`（模板宏替换与 JSON 约束）
  - [x] SubTask 9.2: E2E 测试：用真实 preset + 测试小说，mock LLM 跑全流程
  - [x] SubTask 9.3: 更新 `agent.md`（架构树、设计决策、UI 布局、技术栈）
  - [x] SubTask 9.4: 运行全量测试套件确认无回归

# Task Dependencies

- Task 2 依赖 Task 1（agent_artifacts 字段定义）
- Task 3 无依赖，可与 Task 1/2 并行
- Task 4 依赖 Task 1（模板产出模型需先定义）
- Task 5 依赖 Task 1、3、4（模型、JSON 工具、模板）
- Task 6、7 依赖 Task 1（AgentRunConfig 等模型）
- Task 8 依赖 Task 5、6、7（orchestrator、panel、dialog）
- Task 9 依赖所有前置任务
