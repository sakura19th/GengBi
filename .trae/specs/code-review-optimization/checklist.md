# Checklist

本清单用于系统化验证各优化项是否真正落地。每项对应 spec/tasks 中的具体要求，验证时需检查代码与（如适用）测试。

## A. 正确性 Bug 修复

- [x] macros.py: 含 setvar/getvar 的文本不启用缓存或缓存 key 纳入变量状态，重复调用副作用仍执行
- [x] crypto.py: re_encrypt_api_keys 解密失败时保留原始 encrypted_key，不替换为空字符串
- [x] storage.py: save_chapter 回滚仅删除新创建文件，更新场景不破坏既有文件内容
- [x] context_extractor.py: _cancel_event 使用 threading.Event 且在 __init__ 预创建，cancel 早于 extract 时信号不丢失
- [x] context_extractor.py: 超时重试 continue 前检查 cancel_event
- [x] agent_orchestrator.py: _wait_for_resume 先检查已有 payload，早期 resume 不丢失
- [x] continuation_worker.py: except 不再吞没编程错误，仅捕获预期网络/解析异常
- [x] continuation_worker.py: RateLimitError 不再同时 emit rate_limit_warning 与 error 两个信号

## B. 安全增强

- [x] crypto.py: get_machine_id fallback 持久化生成 ID，两次调用返回相同值
- [x] regex_engine.py: 正则执行有 5 秒超时，灾难性回溯不阻塞
- [x] template_engine.py: 非白名单函数无法被模板调用
- [x] storage.py / json_utils.py: JSON 加载有 50MB 上限，超大文件被拒绝
- [x] llm_client.py: __init__ 校验 base_url 协议前缀与 api_key 非空

## C. 资源管理与线程安全

- [x] storage.py: Storage 实现 __aenter__/__aexit__
- [x] crypto.py: _key_cache 受 threading.Lock 保护，提供 clear_cache()
- [x] prompt_assembler.py: _render_context 作为参数传递，非实例变量
- [x] config.py: ConfigManager 读写受 RLock 保护
- [x] llm_client.py: 复用 aiohttp.ClientSession，提供 async close()
- [ ] agent_orchestrator.py / continuation_worker.py: finally 中调用 LLMClient.close()（LLMClient.close() 已实现，finally 调用未接入）
- [x] main.py: 退出时调用 AsyncLoopRunner.instance().shutdown()

## D. 性能优化

- [x] prompt_assembler.py: sorted_injections、history_with_injections 等死代码已删除
- [x] context_extractor.py: token 计数有 (chapter_id, content_hash) 缓存
- [x] context_extractor.py: extract 与 extract_streaming 共享公共方法，重复消除
- [x] agent_orchestrator.py: chapters_text 只拼接一次并复用
- [x] storage.py / storage_service.py: 提供 load_chapters_with_continuations 批量接口，load_chapter_contents 往返次数减少
- [x] macros.py / token_counter.py: 缓存为真正 LRU（OrderedDict + move_to_end）
- [x] template_engine.py: 复用模块级 Environment 实例
- [x] config.py: 提供 begin_batch/commit_batch，settings 保存时批量

## E. UI 响应性与内存泄漏

- [ ] main_window.py: force_refresh_context/handle_extraction_failure/load_context_entries/cancel_extraction 均非阻塞（部分完成：cancel_extraction/force_refresh 已改非阻塞，另 2 处留 TODO）
- [ ] main_window.py: view_continuation_prompt/start_continuation 的 assemble 在 worker 线程（未实现）
- [ ] main_window.py: 文件导入/导出/备份在 QThread 中执行（仅添加 TODO 注释，IoWorker 未实现）
- [x] main_window.py: 新建 ContinuationWorker/AgentOrchestrator 前断开旧对象信号并 deleteLater
- [ ] main_window.py: lambda 信号连接改为具名方法或保存 Connection（未实现）
- [ ] chapter_list.py: FullTextSearchWorker 信号在切换项目时断开（未实现）
- [ ] 各管理窗口: closeEvent 中 disconnect 自身信号（未实现）
- [x] main_window.py: _context_entries_by_chapter 有 LRU 上限
- [x] main_window.py: _undo_stack 限制最近 100 步

## F. 代码重复消除

- [x] template_engine.py: render_pre_send/render_post_receive 合并为 render（保留别名）
- [x] prompt_assembler.py: _process_content/_process_context_entry_content 合并
- [x] novelforge/utils/ids.py: generate_id 统一，4 处 _generate_id 改用
- [x] novelforge/services/_base_json_service.py: JsonCrudMixin，preset/regex/worldbook 复用
- [x] novelforge/core/post_processor.py: post_process_content，agent/continuation 复用
- [x] novelforge/ui/persistent_dialog.py: PersistentDialog，4 个管理窗口复用
- [x] novelforge/utils/outline_serializer.py: agent_panel/checkpoint_dialog 复用
- [x] UI 工具函数 set_label_state/parse_token_limit/select_combo_by_id 抽取并使用

## G. 测试质量修复

- [x] test_m2_prompt_assembly.py: 每个 test_* 末尾有 assert result.failed == 0
- [x] test_m2_prompt_assembly.py: 运行后真实反映断言结果（修复假绿）
- [x] test_m3.py: check() 失败时抛 AssertionError
- [x] test_tgbreak_e2e.py: 路径改为 PROJECT_ROOT 相对路径，测试不再 skip
- [x] test_tgbreak_e2e.py:309 弱断言已拆分为具体断言
- [x] test_e2e_workflow.py:519 弱断言已拆分
- [x] tests/test_project_models.py: 覆盖 Project/NovelProfile/ChapterSplitRule/ManualOverride
- [x] tests/test_preset_models.py: 覆盖 WritingPreset/Prompt/PromptOrder*
- [x] tests/test_regex_models.py: 覆盖 RegexScript
- [x] tests/test_worldbook_models.py: 覆盖 WorldBook
- [x] tests/test_chapter_models.py: 覆盖 Chapter/Continuation 序列化往返

## H. 模型与输入校验

- [x] context.py: ContextEntry.category/position/role 有 field_validator
- [x] regex.py: RegexScript.placement 有 field_validator
- [x] preset.py: Prompt.injection_position 有 field_validator
- [x] agent.py: CritiqueIssue.category/severity 有 field_validator
- [x] project.py/preset.py: ManualOverride/ChapterSplitRule/PromptOrderEntry/PromptOrderGroup 补 model_config
- [ ] models: STRawFieldsMixin 抽取，5 处 raw_st_fields 复用（未实现，延期）
- [x] models/__init__.py: 导出 WorldBook
- [x] regex_service.py: create_script 保存前 re.compile 校验
- [x] importer.py: 捕获 UnicodeDecodeError 并提示
- [x] history_service.py: log_continuation 校验 status 枚举
- [x] context_extractor.py: lookback_override 有上界校验

## I. 全局验证

- [x] 运行全量测试套件（pytest tests/ test_m3.py）全部通过，无假绿（355/355 通过）
- [ ] 手动回归续写全流程（单次续写 + 流式输出 + swipe 切换）
- [ ] 手动回归 Agent 多轮续写（含 checkpoint resume）
- [ ] 手动回归章节增删改/拆分/合并/重排
- [ ] 手动回归上下文提取（含取消 + 超时重试）
- [ ] 手动回归导入 TXT / 导出 TXT / 备份 / 恢复
- [ ] 手动回归主题切换（暗/亮）与字体设置
- [ ] 手动回归预设/正则/世界书/模板管理窗口
- [ ] 连续多次续写后内存不持续增长（验证信号断开）
- [ ] 大项目（50+ 章）下章节切换/导出 UI 不冻结
