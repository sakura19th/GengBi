# 代码审查与优化 Spec

## Why

NovelForge 已完成功能开发并通过多个 milestone 测试，但本次对 `novelforge` 全量代码（core / services / ui / models / tests）的审查发现了一批影响**功能正确性、数据安全、UI 响应性、可维护性**的问题。其中部分是会导致数据丢失或功能失效的真实 bug（如 `setvar` 宏缓存副作用被跳过、`re_encrypt` 失败丢密钥、`save_chapter` 回滚误删已有文件），部分是"假绿"测试（断言不抛异常），还有大量性能与架构问题（UI 线程阻塞、God Class、600 行重复代码、缓存实为 FIFO）。

本 spec 的目标是对审查发现进行分类、定级，并给出可落地的优化方案，使代码在保持现有功能不变的前提下提升健壮性、性能与可维护性。

## What Changes

### A. 正确性 Bug 修复（必须优先）
- 修复 `macros.py` 宏缓存导致 `setvar` 副作用不执行的 bug
- 修复 `crypto.py` `re_encrypt_api_keys` 失败时丢失原始密钥的问题
- 修复 `storage.py` `save_chapter` 回滚逻辑在更新场景下误删已有文件的问题
- 修复 `context_extractor.py` `cancel()` 与 `extract()` 之间的竞态与跨线程 `asyncio.Event` 误用
- 修复 `agent_orchestrator.py` `resume()` 早期 payload 丢失竞态
- 修复 `continuation_worker.py` `except (LLMError, Exception)` 吞没编程错误

### B. 安全增强
- `crypto.py` `get_machine_id` fallback 持久化生成的 ID，避免 `uuid.getnode()` 随机值导致密钥不可解密
- `regex_engine.py` 为正则执行增加超时保护，防止灾难性回溯阻塞
- `template_engine.py` 实际强制执行函数白名单（而非仅定义）
- `storage.py` / `json_utils.py` 为 JSON 加载增加文件大小上限
- `llm_client.py` 在 `__init__` 校验 `base_url` 与 `api_key` 格式

### C. 资源管理与线程安全
- `storage.py` `Storage` 实现异步上下文管理器协议（`__aenter__`/`__aexit__`）
- `crypto.py` 类级 `_key_cache` 增加 `threading.Lock` 保护
- `prompt_assembler.py` 将 `_render_context` 由实例变量改为参数传递，消除并发竞争
- `config.py` `ConfigManager` 增加线程锁保护 `config` 与 `_crypto_manager`
- `llm_client.py` 支持复用 `aiohttp.ClientSession` 并提供 `close()`
- `main.py` 退出流程调用 `AsyncLoopRunner.instance().shutdown()`

### D. 性能优化
- 删除 `prompt_assembler.py` 中 6 处死代码（`sorted_injections`、`history_with_injections` 等未使用计算）
- `context_extractor.py` 增加基于 `(chapter_id, content_hash)` 的 token 计数缓存
- `context_extractor.py` 合并 `extract()` 与 `extract_streaming()` ~600 行重复代码为公共方法
- `agent_orchestrator.py` 缓存 `_build_chapters_text` 结果供多阶段复用
- `storage_service.py` / `storage.py` 增加批量加载接口（`load_chapters_with_continuations`），消除 `load_chapter_contents` 的 2N 次跨线程往返
- 将 `macros.py` / `token_counter.py` 的 FIFO 缓存改为真正的 LRU（`OrderedDict.move_to_end`）
- `template_engine.py` 复用模块级 `Environment` 实例，避免每次属性检查新建
- `config.py` 增加 `begin_batch()`/`commit_batch()` 批量保存模式

### E. UI 响应性与内存泄漏
- 将 `main_window.py` 中所有 `runner.run(timeout=...)` 同步阻塞调用改为非阻塞 `run_coroutine_threadsafe` + 信号回调
- 文件导入/导出/备份操作移入 `QThread`
- 在创建新 `ContinuationWorker`/`AgentOrchestrator` 前断开旧对象信号并 `deleteLater`；将 lambda 信号连接改为具名方法或保存 `QMetaObject.Connection`
- 为 `_context_entries_by_chapter` 缓存与 `_undo_stack` 增加容量上限

### F. 代码重复消除
- `template_engine.py` 合并 `render_pre_send`/`render_post_receive`
- `prompt_assembler.py` 合并 `_process_content`/`_process_context_entry_content`
- 抽取 `novelforge.utils.ids.generate_id(prefix)` 统一 4 处 `_generate_id` 重复
- 抽取 `JsonCrudMixin`/`BaseJsonService[T]` 泛型基类，统一 preset/regex/worldbook CRUD
- 抽取 `novelforge.core.post_processor.post_process_content` 统一 agent/continuation 后处理
- 抽取 `PersistentDialog` 基类统一 4 个管理窗口的窗口状态持久化
- 抽取 `novelforge.utils.outline_serializer` 统一 agent_panel/checkpoint_dialog 的 outline 格式化
- 抽取 UI 工具函数：`set_label_state`、`parse_token_limit`、`select_combo_by_id`

### G. 测试质量修复（关键）
- 修复 `test_m2_prompt_assembly.py` 假绿测试：每个测试末尾增加 `assert result.failed == 0`
- 修复 `test_m3.py` 假绿测试：`check()` 改为抛出 `AssertionError`
- 修复 `test_tgbreak_e2e.py` 硬编码 `/workspace/` 路径，改为 `PROJECT_ROOT` 相对路径
- 强化弱断言（`test_tgbreak_e2e.py:309`、`test_e2e_workflow.py:519`）
- 为 `project.py`、`preset.py`、`regex.py`、`worldbook.py`、`chapter.py` 补充独立模型测试

### H. 模型与输入校验
- 为 `ContextEntry.category/position/role`、`RegexScript.placement`、`Prompt.injection_position`、`CritiqueIssue.category/severity` 添加 pydantic v2 `field_validator`
- 统一 4 个缺失 `model_config` 的模型（`ManualOverride`、`ChapterSplitRule`、`PromptOrderEntry`、`PromptOrderGroup`）
- 抽取 `STRawFieldsMixin` 统一 5 处 `raw_st_fields` 重复
- 在 `models/__init__.py` 导出 `WorldBook`
- `regex_service.create_script` 保存前编译校验正则
- `importer.py` 捕获 `UnicodeDecodeError` 并提示编码问题
- `history_service.log_continuation` 校验 `status` 枚举

### I. 架构改善（中长期，标注为可选）
- 引入 `ServiceLocator`/`AppContext` 容器，禁止 UI 层导入服务层私有函数
- 拆分 `MainWindow` God Class 为多个 Controller（`ContinuationController`/`ChapterController`/`ExportController`/`ThemeManager`）
- 抽离 `context_dialogs.py` 承载 `ContextPreviewPanel` 内嵌对话框

## Impact

- **Affected specs**: `build-novel-continuation-tool`（核心续写流程）、`add-multi-round-agent-continuation`（Agent 编排）、`add-global-worldbook`（世界书管理）
- **Affected code**:
  - `novelforge/core/`: macros.py, crypto.py, storage.py, config.py, prompt_assembler.py, template_engine.py, regex_engine.py, token_counter.py, json_utils.py, variable_store.py, logger.py
  - `novelforge/services/`: 全部 14 个文件，重点 context_extractor.py, llm_client.py, storage_service.py, chapter_service.py, agent_orchestrator.py, continuation_worker.py, importer.py, regex_service.py, history_service.py, preset_service.py, worldbook_service.py, exporter.py, async_runner.py
  - `novelforge/ui/`: main_window.py, continuation_panel.py, context_preview_panel.py, agent_panel.py, checkpoint_dialog.py, preset_manager.py, regex_manager.py, template_editor.py, worldbook_manager.py, chapter_list.py, chapter_editor.py, settings_dialog.py, font_settings.py
  - `novelforge/models/`: 全部 7 个模型文件
  - `tests/`: test_m2_prompt_assembly.py, test_m3.py, test_tgbreak_e2e.py, test_e2e_workflow.py, test_agent_orchestrator.py 及新增模型测试
  - 新增 `novelforge/utils/ids.py`、`novelforge/utils/outline_serializer.py`、`novelforge/core/post_processor.py`、`novelforge/ui/persistent_dialog.py`
- **风险**: A/B/C 类改动涉及核心数据通路与加密，需配套回归测试；E 类 UI 改造涉及信号生命周期，需手动验证续写/Agent 全流程

## ADDED Requirements

### Requirement: 宏缓存的副作用一致性
The system SHALL 保证含 `{{setvar::name::value}}` 宏的文本在缓存命中时仍执行 setvar 副作用，或对该类文本不启用缓存。

#### Scenario: setvar 在重复调用时仍生效
- **WHEN** 同一含 `{{setvar::foo::bar}}` 的文本被 `MacroEngine.substitute` 调用两次
- **THEN** 第二次调用后 `getvar("foo")` 仍返回 `"bar"`，变量被正确设置

### Requirement: API Key 重加密的数据保留
The system SHALL 在 `re_encrypt_api_keys` 解密失败时保留原始加密字符串，而非用空字符串替代。

#### Scenario: 解密失败的密钥保留
- **WHEN** `old_manager.decrypt(encrypted_key)` 抛出 `InvalidToken`
- **THEN** 返回列表中对应位置保留原始 `encrypted_key`，不丢失数据

### Requirement: 章节保存回滚不破坏既有数据
The system SHALL 在 `save_chapter` SQLite 写入失败时，仅删除本次新创建的文件，对已存在的章节文件保留原内容。

#### Scenario: 更新章节时数据库写入失败
- **GIVEN** 章节文件已存在（更新场景）
- **WHEN** SQLite 写入失败触发回滚
- **THEN** 原章节文件内容保持不变，不被删除

### Requirement: 上下文提取的可取消性与竞态安全
The system SHALL 使用线程安全的取消信号，并保证 `cancel()` 在 `extract()` 开始前调用时取消信号不被丢失。

#### Scenario: extract 开始前调用 cancel
- **WHEN** `cancel()` 在 `extract()` 创建取消事件之前被调用
- **THEN** 后续 `extract()` 仍能感知到取消并立即返回

### Requirement: LLM 客户端参数校验
The system SHALL 在 `LLMClient.__init__` 校验 `base_url` 以 `http://` 或 `https://` 开头且 `api_key` 非空。

#### Scenario: 非法 base_url
- **WHEN** 传入 `base_url="example.com"`（缺少协议前缀）
- **THEN** 抛出 `ValueError` 提示格式错误

### Requirement: JSON 加载的大小保护
The system SHALL 在加载 JSON 配置/备份文件前校验文件大小不超过上限（默认 50MB），超限拒绝加载并提示用户。

#### Scenario: 超大 JSON 文件
- **WHEN** 加载超过 50MB 的 JSON 文件
- **THEN** 拒绝加载并返回明确错误，不触发 OOM

### Requirement: 正则执行超时保护
The system SHALL 为用户自定义正则脚本的执行设置超时（默认 5 秒），超时后中止并返回原文。

#### Scenario: 灾难性回溯正则
- **WHEN** 用户配置的正则触发灾难性回溯
- **THEN** 5 秒后中止执行，返回原文，记录 warning，不阻塞 UI

### Requirement: 缓存的 LRU 语义
The system SHALL 使 `MacroEngine` 与 `TokenCounter` 的缓存按"最近最少访问"淘汰，缓存命中时更新访问顺序。

#### Scenario: 缓存命中后再次访问
- **GIVEN** 缓存已满
- **WHEN** 命中一个早期插入的 key 后再插入新 key
- **THEN** 被淘汰的是真正的最近最少访问 key，而非最早插入的 key

### Requirement: UI 非阻塞操作
The system SHALL 不在 Qt 主线程同步阻塞执行超过 1 秒的 I/O 或 LLM 操作；续写提取、上下文加载、文件导入/导出均通过工作线程或异步回调完成。

#### Scenario: 强制刷新上下文时 UI 不冻结
- **WHEN** 用户点击"强制刷新上下文"
- **THEN** UI 保持响应（可移动窗口、切换菜单），提取完成后通过信号更新面板

### Requirement: Worker 信号生命周期管理
The system SHALL 在创建新的 `ContinuationWorker`/`AgentOrchestrator` 前，断开旧对象的所有信号连接并调用 `deleteLater`，避免内存泄漏。

#### Scenario: 连续发起多次续写
- **WHEN** 用户在前一次续写完成后立即发起第二次续写
- **THEN** 旧 worker 的信号被断开并被 GC，不存在残留信号连接

### Requirement: 测试断言有效性
The system SHALL 确保所有 pytest 测试用例在断言失败时使测试失败（抛出 `AssertionError`），不存在"假绿"测试。

#### Scenario: 断言失败导致测试失败
- **WHEN** 测试中的条件不满足
- **THEN** pytest 报告该测试失败，退出码非 0

## MODIFIED Requirements

### Requirement: 配置加密管理
ConfigManager 通过 CryptoManager 加密 API Key。修改后：
- `get_machine_id` 的 fallback 路径持久化生成的 ID，保证机器 ID 稳定
- `_key_cache` 受 `threading.Lock` 保护
- 提供 `clear_cache()` 方法清除内存中的派生密钥

### Requirement: 提示词组装
PromptAssembler.assemble 负责组装提示词。修改后：
- 不修改传入的 `ContextEntry`/`Prompt` 对象（避免副作用）
- `_render_context` 作为参数传递而非实例变量
- 删除未使用的死代码（`sorted_injections` 等）
- token 计数基于处理后的内容

## REMOVED Requirements

### Requirement: 死代码计算
**Reason**: `prompt_assembler.py` 中 `sorted_injections`、`history_with_injections` 等计算结果从未被使用，浪费 CPU（含宏替换、Jinja2 渲染、正则应用）。
**Migration**: 直接删除，调用方无感知（这些变量无外部引用）。
