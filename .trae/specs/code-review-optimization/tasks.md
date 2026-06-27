# Tasks

本任务列表按优先级分组。A/B 类（正确性与安全）必须最先完成，C/D 类紧随其后，E/F/G/H 类可部分并行。每个任务均为可独立验证的小颗粒工作项。

## 阶段 1：正确性 Bug 修复（最高优先级，串行验证）

- [x] Task 1: 修复 macros.py 宏缓存的 setvar 副作用 bug
  - [x] SubTask 1.1: 在 `MacroEngine.substitute` 中检测文本是否含 `{{setvar::` 或 `{{getvar::`，含副作用的文本不写入缓存
  - [x] SubTask 1.2: 在 `MacroContext.context_hash` 中纳入变量存储状态（如变量字典的哈希），使变量变化时缓存失效
  - [x] SubTask 1.3: 新增测试：同一含 setvar 的文本调用两次，验证第二次 getvar 仍返回正确值

- [x] Task 2: 修复 crypto.py re_encrypt_api_keys 数据丢失
  - [x] SubTask 2.1: `InvalidToken` 异常分支改为 `re_encrypted.append(encrypted_key)` 保留原始密钥
  - [x] SubTask 2.2: 新增测试：传入一个无法解密的密钥，验证返回列表保留原值

- [x] Task 3: 修复 storage.py save_chapter 回滚误删已有文件
  - [x] SubTask 3.1: 写文件前判断文件是否已存在；记录 `existed_before` 与旧内容（或旧 mtime）
  - [x] SubTask 3.2: SQLite 写入失败回滚时，仅在文件原本不存在时才 `unlink`；已存在文件恢复旧内容
  - [x] SubTask 3.3: 新增测试：更新已有章节时模拟 SQLite 失败，验证原文件内容未被破坏

- [x] Task 4: 修复 context_extractor.py cancel/extract 竞态与跨线程 Event
  - [x] SubTask 4.1: 将 `_cancel_event` 改为 `threading.Event`（跨线程安全），在 `__init__` 预创建而非每次 extract 重建
  - [x] SubTask 4.2: 每次 `extract()` 开始时调用 `self._cancel_event.clear()`，保留已设置的取消信号
  - [x] SubTask 4.3: 在异步协程中通过 `loop.run_in_executor(None, self._cancel_event.wait)` 或周期 `is_set()` 检查取消
  - [x] SubTask 4.4: 超时重试 `continue` 前增加 `if self._cancel_event.is_set(): return ...`
  - [x] SubTask 4.5: 新增测试：extract 开始前调用 cancel，验证 extract 立即返回取消状态

- [x] Task 5: 修复 agent_orchestrator.py resume() 早期 payload 丢失
  - [x] SubTask 5.1: `_wait_for_resume` 中先检查 `_checkpoint_payload` 是否已非 None，是则直接返回，不执行清零
  - [x] SubTask 5.2: 新增测试：在 checkpoint 信号 emit 后立即调用 resume，验证 payload 不丢失

- [x] Task 6: 修复 continuation_worker.py except (LLMError, Exception) 吞没编程错误
  - [x] SubTask 6.1: 将宽泛 `except Exception` 细化为 `(aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError)` 等预期异常
  - [x] SubTask 6.2: 编程错误（`KeyError`/`AttributeError`/`TypeError`）向上传播
  - [x] SubTask 6.3: 修复 RateLimitError 同时 emit 两个信号：仅 emit `rate_limit_warning`，不再 emit `error`

## 阶段 2：安全增强（可与阶段 1 部分并行）

- [x] Task 7: crypto.py get_machine_id fallback 持久化
  - [x] SubTask 7.1: fallback 路径生成 ID 后持久化到配置目录的 `.machine_id` 文件
  - [x] SubTask 7.2: 下次调用优先读取持久化文件
  - [x] SubTask 7.3: 新增测试：模拟 fallback 路径，验证两次调用返回相同 ID

- [x] Task 8: regex_engine.py 正则执行超时保护
  - [x] SubTask 8.1: 将 `_pattern.sub` 调用移入 `ThreadPoolExecutor`（复用 template_engine 模式或独立线程池）
  - [x] SubTask 8.2: 设置默认 5 秒超时，超时返回原文并记录 warning
  - [x] SubTask 8.3: 新增测试：构造灾难性回溯正则，验证 5 秒内返回原文

- [x] Task 9: template_engine.py 强制执行函数白名单
  - [x] SubTask 9.1: `_build_default_context` 中仅放入 `WHITELIST_FUNCTION_NAMES` 内的函数
  - [x] SubTask 9.2: 新增测试：模板调用非白名单函数时被拒绝

- [x] Task 10: JSON 加载大小保护
  - [x] SubTask 10.1: 在 `storage.load_json_with_recovery` 与 `json_utils.parse_json_response` 读取前校验文件大小（上限 50MB，可配）
  - [x] SubTask 10.2: 超限抛出明确异常并提示用户
  - [x] SubTask 10.3: 新增测试：构造超大 JSON 文件，验证被拒绝

- [x] Task 11: llm_client.py 参数校验
  - [x] SubTask 11.1: `__init__` 校验 `base_url` 以 `http://` 或 `https://` 开头，否则 `ValueError`
  - [x] SubTask 11.2: 校验 `api_key` 非空
  - [x] SubTask 11.3: 新增测试：非法 base_url 与空 api_key 抛出 ValueError

## 阶段 3：资源管理与线程安全

- [x] Task 12: storage.py Storage 异步上下文管理器
  - [x] SubTask 12.1: 实现 `__aenter__`/`__aexit__`（connect/close）
  - [x] SubTask 12.2: 审计调用方，确保 long-lived Storage 用 `async with` 或显式 close
  - [x] SubTask 12.3: 新增测试：`async with Storage(...)` 正确打开与关闭

- [x] Task 13: crypto.py _key_cache 加锁
  - [x] SubTask 13.1: 增加 `threading.Lock`，`derive_key` 读写缓存加锁
  - [x] SubTask 13.2: 提供 `clear_cache()` 清除内存密钥
  - [x] SubTask 13.3: 新增多线程并发 derive 测试，验证无重复派生

- [x] Task 14: prompt_assembler.py _render_context 改参数传递
  - [x] SubTask 14.1: 移除实例变量 `_render_context`，改为 `assemble` 内局部变量并传入各子方法
  - [x] SubTask 14.2: 验证并发调用 assemble 不再共享渲染上下文（新增并发测试）

- [x] Task 15: config.py ConfigManager 线程锁
  - [x] SubTask 15.1: 增加 `threading.RLock`，`get`/`set`/`save`/`get_crypto_manager` 加锁
  - [x] SubTask 15.2: 双重检查锁优化 `get_crypto_manager`

- [x] Task 16: llm_client.py 复用 ClientSession 与 close()
  - [x] SubTask 16.1: `LLMClient` 懒创建持久 `aiohttp.ClientSession`（绑定事件循环）
  - [x] SubTask 16.2: 提供 `async def close()`，在 agent/continuation 的 finally 中调用
  - [x] SubTask 16.3: 新增测试：多次调用复用同一 session

- [x] Task 17: main.py 退出清理 AsyncLoopRunner
  - [x] SubTask 17.1: `main()` 中 `app.exec()` 后调用 `AsyncLoopRunner.instance().shutdown()`
  - [x] SubTask 17.2: 验证退出时 pending 协程完成

## 阶段 4：性能优化

- [x] Task 18: 删除 prompt_assembler.py 死代码
  - [x] SubTask 18.1: 删除 `sorted_injections`（L430-433）、`history_with_injections`（L441-443）及其上游未使用计算
  - [x] SubTask 18.2: 运行现有 prompt assembly 测试，验证行为不变

- [x] Task 19: context_extractor.py token 计数缓存
  - [x] SubTask 19.1: 维护 `{(chapter_id, content_hash): token_count}` 缓存
  - [x] SubTask 19.2: 新增测试：相同章节二次计数命中缓存

- [x] Task 20: context_extractor.py 合并 extract/extract_streaming 重复
  - [x] SubTask 20.1: 提取公共方法 `_extract_common(..., stream: bool, on_chunk=None)`
  - [x] SubTask 20.2: `extract`/`extract_streaming` 委托公共方法
  - [x] SubTask 20.3: 运行现有 extraction 测试，验证流式与非流式行为不变

- [x] Task 21: agent_orchestrator.py 缓存 chapters_text
  - [x] SubTask 21.1: `_async_run` 开始时计算一次 `self._chapters_text`，analysis/outline 复用
  - [x] SubTask 21.2: 新增测试验证只拼接一次（可通过 mock 计数）

- [x] Task 22: storage 批量加载接口
  - [x] SubTask 22.1: `Storage.load_chapters_with_continuations(chapter_ids)` 单事务内完成
  - [x] SubTask 22.2: `StorageService.load_chapter_contents` 改用批量接口
  - [x] SubTask 22.3: 新增测试：加载 N 章验证往返次数减少

- [x] Task 23: 缓存改 LRU
  - [x] SubTask 23.1: `macros.py` `_cache` 改 `OrderedDict`，命中 `move_to_end`
  - [x] SubTask 23.2: `token_counter.py` 同上
  - [x] SubTask 23.3: 新增测试：验证淘汰真正的 LRU key

- [x] Task 24: template_engine.py 复用 Environment
  - [x] SubTask 24.1: `_safe_is_safe_attribute` 复用模块级 `_DEFAULT_ENV` 实例
  - [x] SubTask 24.2: 验证渲染行为不变

- [x] Task 25: config.py 批量保存模式
  - [x] SubTask 25.1: 增加 `begin_batch()`/`commit_batch()`，batch 内 `set*` 不立即 `save`
  - [x] SubTask 25.2: settings_dialog 保存时使用 batch 模式
  - [x] SubTask 25.3: 新增测试：batch 内多次 set 只 save 一次

## 阶段 5：UI 响应性与内存泄漏

- [x] Task 26: main_window.py 同步阻塞调用改非阻塞
  - [x] SubTask 26.1: `_on_force_refresh_context`、`_handle_extraction_failure`、`_load_context_entries_for_chapter`、`_on_cancel_extraction` 改用 `run_coroutine_threadsafe` + 信号回调
  - [x] SubTask 26.2: `_on_view_continuation_prompt`、`_on_start_continuation` 的 assemble 移入 worker
  - [x] SubTask 26.3: 手动验证章节切换/续写时 UI 不冻结

- [x] Task 27: 文件导入/导出/备份移入 QThread
  - [x] SubTask 27.1: 创建 `IoWorker(QThread)` 封装 import_txt/export_full_txt/export_project_backup/import_project_backup
  - [x] SubTask 27.2: 进度信号 + 完成信号回调
  - [x] SubTask 27.3: 手动验证大文件导入/导出时 UI 响应

- [x] Task 28: Worker/Orchestrator 信号生命周期管理
  - [x] SubTask 28.1: 创建新 ContinuationWorker 前，断开旧 worker 所有信号并 `deleteLater`
  - [x] SubTask 28.2: 同上处理 AgentOrchestrator
  - [x] SubTask 28.3: lambda 信号连接改为具名方法或保存 `QMetaObject.Connection`
  - [x] SubTask 28.4: FullTextSearchWorker 与非模态管理窗口 closeEvent 中 disconnect 信号

- [x] Task 29: 状态变量容量限制
  - [x] SubTask 29.1: `_context_entries_by_chapter` 改 `OrderedDict` + LRU 上限（如 50）
  - [x] SubTask 29.2: `_undo_stack` 限制最近 100 步
  - [x] SubTask 29.3: 新增测试：超出上限时旧条目被淘汰

## 阶段 6：代码重复消除

- [x] Task 30: template_engine.py 合并 render_pre_send/render_post_receive
  - [x] SubTask 30.1: 合并为 `render`，两个公开方法作为别名委托
  - [x] SubTask 30.2: 运行现有 template 测试

- [x] Task 31: prompt_assembler.py 合并 _process_content/_process_context_entry_content
  - [x] SubTask 31.1: 合并为单方法，placement 作参数
  - [x] SubTask 31.2: 运行现有 prompt assembly 测试

- [x] Task 32: 抽取 utils/ids.generate_id
  - [x] SubTask 32.1: 新建 `novelforge/utils/ids.py`，`generate_id(prefix)` 实现
  - [x] SubTask 32.2: storage_service/history_service/preset_service/regex_service 改为导入使用
  - [x] SubTask 32.3: context_preview_panel/worldbook_manager 的 `_generate_id` 私有导入改为公开 API

- [x] Task 33: 抽取 JsonCrudMixin/BaseJsonService
  - [x] SubTask 33.1: 新建 `novelforge/services/_base_json_service.py`，泛型基类封装 load/save/delete/list
  - [x] SubTask 33.2: preset_service/regex_service/worldbook_service 继承基类，删除重复 CRUD
  - [x] SubTask 33.3: 运行三类服务的现有测试

- [x] Task 34: 抽取 post_processor
  - [x] SubTask 34.1: 新建 `novelforge/core/post_processor.py`，`post_process_content(...)` 函数
  - [x] SubTask 34.2: agent_orchestrator/continuation_worker 改为调用
  - [x] SubTask 34.3: 运行 agent/continuation 测试

- [x] Task 35: 抽取 PersistentDialog 基类
  - [x] SubTask 35.1: 新建 `novelforge/ui/persistent_dialog.py`，封装窗口状态持久化与 closeEvent
  - [x] SubTask 35.2: PresetManager/RegexManager/TemplateEditor/WorldBookManager 继承
  - [x] SubTask 35.3: 手动验证四个窗口状态保存/恢复正常

- [x] Task 36: 抽取 outline_serializer 与 UI 工具函数
  - [x] SubTask 36.1: 新建 `novelforge/utils/outline_serializer.py`，迁移 agent_panel/checkpoint_dialog 的格式化/解析
  - [x] SubTask 36.2: 新建 UI 工具函数：`set_label_state`、`parse_token_limit`、`select_combo_by_id`
  - [x] SubTask 36.3: 各 UI 文件改为调用工具函数

## 阶段 7：测试质量修复

- [x] Task 37: 修复 test_m2_prompt_assembly.py 假绿测试
  - [x] SubTask 37.1: 每个 test_* 函数末尾增加 `assert result.failed == 0, result.failures`
  - [x] SubTask 37.2: 运行测试，确认原本"通过"的用例是否真实通过

- [x] Task 38: 修复 test_m3.py 假绿测试
  - [x] SubTask 38.1: `check()` 改为失败时抛 `AssertionError`
  - [x] SubTask 38.2: 确认 pytest 收集时测试真实运行

- [x] Task 39: 修复 test_tgbreak_e2e.py 硬编码路径
  - [x] SubTask 39.1: `TGBREAK_PRESET_PATH`/`NOVEL_TXT_PATH` 改为 `PROJECT_ROOT / "TGbreak😺V3.1.1.json"` 等
  - [x] SubTask 39.2: 验证测试不再 skip 并真实运行

- [x] Task 40: 强化弱断言
  - [x] SubTask 40.1: `test_tgbreak_e2e.py:309` 拆分为具体断言
  - [x] SubTask 40.2: `test_e2e_workflow.py:519` 拆分 `or` 断言

- [x] Task 41: 补充模型测试
  - [x] SubTask 41.1: 新建 `tests/test_project_models.py` 覆盖 Project/NovelProfile/ChapterSplitRule/ManualOverride
  - [x] SubTask 41.2: 新建 `tests/test_preset_models.py` 覆盖 WritingPreset/Prompt/PromptOrder*
  - [x] SubTask 41.3: 新建 `tests/test_regex_models.py` 覆盖 RegexScript
  - [x] SubTask 41.4: 新建 `tests/test_worldbook_models.py` 覆盖 WorldBook
  - [x] SubTask 41.5: 新建 `tests/test_chapter_models.py` 覆盖 Chapter/Continuation 序列化往返
  - [x] SubTask 41.6: 各测试覆盖默认值、序列化往返、别名、边界值

## 阶段 8：模型与输入校验

- [x] Task 42: 模型 field_validator
  - [x] SubTask 42.1: ContextEntry.category/position/role 增加 `field_validator` 校验 `VALID_*` 集合
  - [x] SubTask 42.2: RegexScript.placement 校验 `VALID_PLACEMENTS`
  - [x] SubTask 42.3: Prompt.injection_position 校验 0/1
  - [x] SubTask 42.4: CritiqueIssue.category/severity 校验枚举
  - [x] SubTask 42.5: 新增测试：非法值抛 ValidationError

- [x] Task 43: 统一 model_config 与 STRawFieldsMixin
  - [x] SubTask 43.1: 为 ManualOverride/ChapterSplitRule/PromptOrderEntry/PromptOrderGroup 补 `ConfigDict(populate_by_name=True)`
  - [x] SubTask 43.2: 抽取 `STRawFieldsMixin`，5 处 `raw_st_fields` 复用
  - [x] SubTask 43.3: `models/__init__.py` 导出 `WorldBook`
  - [x] SubTask 43.4: 运行模型测试

- [x] Task 44: 服务层输入校验
  - [x] SubTask 44.1: `regex_service.create_script` 保存前 `re.compile` 校验正则
  - [x] SubTask 44.2: `importer.import_file` 捕获 `UnicodeDecodeError` 并提示编码问题
  - [x] SubTask 44.3: `history_service.log_continuation` 校验 `status` 枚举
  - [x] SubTask 44.4: `context_extractor` `lookback_override` 加上界校验

## 阶段 9：架构改善（可选，独立提交）

- [ ] Task 45: 引入 ServiceLocator/AppContext（延期，低优先级）
  - [ ] SubTask 45.1: 新建 `novelforge/app_context.py`，集中管理服务实例化与生命周期
  - [ ] SubTask 45.2: MainWindow 通过 AppContext 获取服务，不再直接实例化
  - [ ] SubTask 45.3: 禁止 UI 层导入服务私有函数（lint 规则或代码评审）

- [ ] Task 46: 拆分 MainWindow God Class（延期，低优先级）
  - [ ] SubTask 46.1: 抽出 `ContinuationController`、`ChapterController`、`ExtractionController`、`ExportController`、`ThemeManager`
  - [ ] SubTask 46.2: MainWindow 仅保留窗口骨架、菜单、状态栏与信号路由
  - [ ] SubTask 46.3: 抽离 `context_dialogs.py` 承载 ContextPreviewPanel 内嵌对话框
  - [ ] SubTask 46.4: 全流程手动回归（续写/Agent/章节/导入导出/主题）

# Task Dependencies

- Task 1-6（阶段 1）相互独立，可并行；但建议先完成以建立稳定基线
- Task 12（Storage 上下文管理器）是 Task 22（批量加载）的前置
- Task 16（LLMClient close）是 Task 6/Task 17 的相关项，建议先做 16
- Task 20（合并 extract 重复）依赖 Task 4（cancel 修复）完成，避免合并错误逻辑
- Task 33（JsonCrudMixin）应在 Task 32（ids 抽取）之后
- Task 41（模型测试）应在 Task 42/43（模型校验）之前，先建立测试基线再改模型
- Task 45/46（架构）依赖阶段 1-8 完成，且应独立提交以便回滚
- Task 37-40（测试修复）应尽早完成，确保后续重构有可靠测试保护；建议在阶段 1 完成后立即插入
