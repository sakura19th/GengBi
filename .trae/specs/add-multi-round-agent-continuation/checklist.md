# Checklist

## 数据模型（Task 1）
- [x] models/agent.py 定义了 StorySnapshot / Scene / Outline / CritiqueIssue / CritiqueReport / AgentArtifacts / AgentRunConfig
- [x] StorySnapshot 包含 structure_position / tone / core_conflict_status / stakes / active_characters / plot_threads / unresolved_promises / foreshadowing_tracker / world_state / style_profile
- [x] Scene 包含 purpose / pov / scene_type / goal / conflict / outcome / value_shift / foreshadowing / exit_hook
- [x] CritiqueIssue 的 category 限定为 consistency/style/structure/engagement，severity 限定为 critical/major/minor
- [x] AgentRunConfig 包含 phases(dict) / checkpoints(dict) / max_revise_rounds / per_phase_overrides
- [x] Continuation 新增 agent_artifacts: AgentArtifacts | None = None（默认 None）
- [x] models/__init__.py 导出所有 agent 模型
- [x] 旧 Continuation JSON（无 agent_artifacts 字段）能正常反序列化

## 存储层（Task 2）
- [x] continuations 表新增 agent_artifacts TEXT 列
- [x] schema 迁移逻辑用 PRAGMA table_info 检测列是否存在（幂等，多次启动不报错）
- [x] save_continuation 的 INSERT 列数 15→16，占位符同步
- [x] _row_to_continuation 的 row[15] 容错 None
- [x] 旧 Continuation（agent_artifacts 为 NULL）能正常加载

## JSON 工具（Task 3）
- [x] core/json_utils.py 包含 strip_markdown_fences 和 parse_json_response
- [x] parse_json_response 支持宽松解析（去 markdown fence 后 json.loads）
- [x] context_extractor.py 的 _strip_markdown_fences 改为从 core/json_utils 导入
- [x] context_extractor 现有测试全部通过

## 阶段提示词模板（Task 4）
- [x] resources/defaults/agent/phase_analysis.txt 存在且含 {{title}} {{author}} {{protagonist}} {{synopsis}} {{world_setting}} {{writing_style}} {{chapters_text}} 占位
- [x] resources/defaults/agent/phase_outline.txt 存在且含 {{snapshot}} {{chapters_text}} {{user_input}} 占位
- [x] resources/defaults/agent/phase_verify.txt 存在且含 {{snapshot}} {{outline}} {{written_text}} 占位
- [x] resources/defaults/agent/phase_revise.txt 存在且含 {{written_text}} {{critique}} {{outline}} 占位
- [x] utils/paths.py 新增 get_agent_prompt_path(phase) 函数
- [x] get_agent_prompt_path 返回正确的资源路径

## AgentOrchestrator（Task 5）
- [x] AgentOrchestrator 继承 QThread，run() 创建 asyncio 事件循环
- [x] 信号定义完整：phase_started/finished、chunk_received、reasoning_received、checkpoint_reached、finished、error、auth_error、token_count
- [x] _async_run 按 config.phases 顺序执行
- [x] 关闭分析阶段时，大纲基于 ContextEntry（优雅降级）
- [x] 关闭大纲阶段时，写作直接基于 ContextEntry（优雅降级）
- [x] 关闭验证阶段时，写作完直接出稿（优雅降级）
- [x] _run_writing 调用 prompt_assembler.assemble 并将大纲格式化为 Markdown 文本合成 ContextEntry 注入
- [x] _run_writing 含 worldInfoBefore marker fallback（无 marker 时直接 prepend 到 messages）
- [x] _run_writing 使用 stream_chat_completion 并 emit chunk_received
- [x] _run_writing 末尾执行正则/模板/HTML 后处理（与 ContinuationWorker 一致）
- [x] 修订循环：while not critique.passed and rounds < max_revise_rounds
- [x] 暂停点：emit checkpoint_reached 后 asyncio.wait_for(_resume_event.wait, 0.5) 循环轮询，每轮检查 _stop_event
- [x] resume 通过 call_soon_threadsafe 线程安全唤醒 asyncio.Event
- [x] 暂停期间支持停止/取消（超时保护防挂死）
- [x] 停止机制：threading.Event + asyncio.Task.cancel 双重中断
- [x] agent 模板宏替换用 str.replace（不用 MacroEngine/Jinja2）
- [x] finished 信号发射含 agent_artifacts 的 Continuation 对象
- [x] JSON 解析失败时重试一次（温度归零），再失败则跳过该阶段
- [x] 测试覆盖：各阶段、暂停点、修订循环上限、优雅降级、停止/取消

## AgentPanel UI（Task 6）
- [x] 阶段开关复选框：分析/大纲/写作/验证/修订（写作默认锁定开启）
- [x] 暂停点复选框：大纲后/验证后
- [x] max_revise_rounds SpinBox（1~3，默认1）
- [x] 阶段进度指示器（步进条，高亮当前阶段）
- [x] 产物查看器：大纲预览（可编辑）、评审报告（折叠组）
- [x] 信号：config_changed、resume、cancel_checkpoint

## CheckpointDialog UI（Task 7）
- [x] 大纲暂停模式：可编辑 QPlainTextEdit + 接受/编辑后继续/取消按钮
- [x] 验证暂停模式：只读评审报告 + 修订/接受/重写按钮

## 模式切换与接线（Task 8）
- [x] continuation_panel 顶部新增模式切换（单次/智能）
- [x] 切换到智能模式时显示 AgentPanel，隐藏单次参数区
- [x] 切换到单次模式时显示单次参数区，隐藏 AgentPanel
- [x] main_window 新增 _on_start_agent_continuation
- [x] 信号接线：phase_started→步进条、phase_finished→产物查看器、chunk_received→append_chunk、checkpoint_reached→弹窗、finished→_on_continuation_finished
- [x] 模式路由：根据 panel 模式路由到单次/agent 处理器
- [x] Ctrl+Enter 根据当前模式触发对应流程
- [x] agent 模式开始时若 _current_context_entries is None 则自动触发 extract_streaming
- [x] _on_start_agent_continuation 中设置 _continuation_model 和 _continuation_prompt_messages 确保 _record_history 正常

## 测试与文档（Task 9）
- [x] test_agent_models.py 覆盖 pydantic 校验 + 向后兼容
- [x] test_agent_orchestrator.py 覆盖各阶段、暂停点、修订循环、降级、停止
- [x] test_agent_prompts.py 覆盖模板宏替换与 JSON 约束
- [x] E2E 测试用真实 preset + 测试小说，mock LLM 跑全流程
- [x] agent.md 更新架构树（新增 models/agent.py、services/agent_orchestrator.py、ui/agent_panel.py、resources/defaults/agent/）
- [x] agent.md 更新关键设计决策（多阶段 agent）
- [x] agent.md 更新 UI 布局（模式切换）
- [x] 全量测试套件通过，无回归
