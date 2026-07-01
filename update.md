# 更新记录

> 本文件记录 NovelForge 项目每次更新内容，按时间倒序排列。

---

## 2026-07-01：发布 v0.2.4（自定义设定/审计必查项 + 高亮 + 流程端点 + 多项修复）

### 版本号同步

- `novelforge/__init__.py`：`__version__` 由 `0.2.3` → `0.2.4`
- `README.md`：顶部「当前版本」由 `v0.2.3` → `v0.2.4`；「更新记录」章节新增 `v0.2.4` 小节（新增功能/修复分类，每条一行简洁描述）
- `agent.md`：「当前版本」由 `v0.2.3` → `v0.2.4`

### 主要内容（详见下文各日期条目）

本次发版整合自 v0.2.3 以来的全部改动，涵盖自定义设定/审计必查项、输出栏高亮、主角形象提取独立按钮与章节级持久化、API 端点思考强度与流程端点配置、编辑器保存按钮，以及多项关键 bug 修复。

- 新增「自定义设定/审计必查项」：用户输入自定义设定，AI 结合世界观与上下文结构化为"要求 + 审计向"两字段，项目全局共享，注入续写生成与审计全链路，未满足 high 严重度项一票否决
- 新增「输出栏高亮」：续写输出框右键菜单 4 色高亮 + 备注 + 持久化
- 新增「主角形象提取独立按钮」：从上下文提取副产品解耦为独立流程，独立缓存与按钮
- 新增「API 端点思考强度」：7 档配置，预设级优先于端点级
- 新增「流程端点配置」：7 个流程可独立选择 API 端点
- 新增「自定义设定流式窗口」+ 修复 session 泄漏与跨线程 GUI 违规
- 主角形象提取结果落盘到 chapters 表
- 编辑器工具栏新增可见「保存」按钮
- 修复编辑保存 API 端点后闪退、已有端点无法编辑、章节保存报错与正文丢失、世界观序列化失败

### 文档同步

README.md 新增 v0.2.4 更新记录小节；agent.md 同步版本号；update.md 追加本发布条目。

### 回归

无需重跑测试——本次为版本号同步与发布记录，未改动生产代码逻辑（全量回归见下文各日期条目末尾的 pytest 结果）。

---

## 2026-07-01：修复编辑保存端点后闪退

### 背景

用户反馈：编辑已有 API 端点点击保存后闪退，控制台报 `QThread: Destroyed while thread '' is still running` 及 aiohttp 未关闭连接警告。

### 根因

`EndpointEditDialog._load_data()` 编辑已有端点时自动触发 `_on_fetch_models()` 启动 `ModelFetchWorker`（QThread，parent=dialog）拉取模型列表。用户点 OK 时网络请求尚未完成，`self.accept()` 关闭并销毁对话框，其子 QThread 被销毁时仍在运行 → 闪退。附带 `ModelFetchWorker.run()` 未调 `client.close()` 导致 aiohttp session 泄漏。

### 核心改动

- **`novelforge/ui/settings_dialog.py`**：
  - `ModelFetchWorker.run()`：增加内层 try/finally 调 `loop.run_until_complete(client.close())` 关闭 aiohttp ClientSession，修复未关闭连接警告。
  - `EndpointEditDialog._on_fetch_models`：worker 创建时 parent 改为 None（解耦对话框生命周期），连接 `finished` → `deleteLater` 让线程结束后自清理（fire-and-forget）。
  - `EndpointEditDialog.closeEvent`（新增）：对话框关闭时若 worker 仍在运行，断开 `models_fetched`/`error` 信号避免回调已销毁 UI；worker 在后台安全完成并自清理，不阻塞 UI、不闪退。

### 设计决策

1. parent=None + finished→deleteLater 而非 `wait()`：`fetch_models` 超时 30s，`wait()` 会冻结 UI 最多 30s；fire-and-forget 模式不阻塞、不闪退，资源在 run() finally 中清理。
2. closeEvent 覆盖 accept/reject/Escape/X 按钮全部关闭路径。
3. 保留 `_load_data` 自动拉取模型（UX 有用），修复线程生命周期后自动拉取安全。
4. 字体警告 `QFont::setPointSize: Point size <= 0 (-1)` 是既有告警，与闪退无关，不在本次范围。

### 测试

- 追加 `tests/test_settings_dialog_endpoint_edit.py` 1 用例：`test_edit_dialog_close_while_fetching_does_not_crash`——stub `ModelFetchWorker` 为慢速线程（run 中 sleep 0.5s），触发自动拉取后立即 `dialog.close()`，断言无异常、对话框正常关闭、worker 可安全 wait 完成。
- `python -m pytest tests/ -q`：564 passed, 13 skipped, 0 failed。

### 文档同步

- 更新 `agent.md`：settings_dialog.py 条目补注 ModelFetchWorker 生命周期解耦 + closeEvent 修复说明。

---

## 2026-07-01：修复已有端点无法编辑

### 背景

用户反馈：设置对话框中点击"编辑"按钮无法编辑已有 API 端点（点击无反应）。

### 根因

`QPushButton.clicked(bool)` 信号把 `False` 传给 `_on_edit_endpoint(self, item=None)` 的 `item` 参数。`if item is None` 无法捕获 `False`（`False is not None`），随后 `if not item`（`not False` 为 `True`）提前 return，`EndpointEditDialog` 从未构造。双击列表项正常（`itemDoubleClicked` 传 `QListWidgetItem` 实例，truthy）。

其他按钮（新建/删除/设为默认）的槽方法不接受 `item` 参数，`clicked(bool)` 的 bool 被 Qt 自动丢弃，工作正常。只有 `_on_edit_endpoint` 因同时承担"按钮点击"与"列表双击"两个角色而引入 `item` 参数，被 `clicked(False)` 误伤。

### 核心改动

- **`novelforge/ui/settings_dialog.py`**：L499 用 lambda 包裹编辑按钮点击连接 `self._edit_btn.clicked.connect(lambda: self._on_edit_endpoint())`，确保 `item` 取默认 `None`；L504 双击连接保持不变。

### 测试

- 新增 `tests/test_settings_dialog_endpoint_edit.py` 2 用例：点击"编辑"按钮后 stub 的 `EndpointEditDialog` 被构造且加载选中端点（回归）+ 双击列表项同样触发编辑（回归保护）。
- `python -m pytest tests/ -q`：563 passed, 13 skipped, 0 failed。

### 文档同步

- 更新 `agent.md`：settings_dialog.py 条目补注编辑按钮 lambda 修复说明。

---

## 2026-07-01：API 端点思考强度 + 流程端点配置

### 背景

1. 用户希望在 API 端点管理中为支持推理的模型（如 DeepSeek V4 max 思考、OpenAI o 系列）配置思考强度，避免每个模型私有参数各自适配。
2. 不同流程（单章续写/卷续写/审计/上下文提取/世界观提取/主角提取/自定义设定解析）希望独立选择 API 端点，默认沿用端点管理总默认端点，也可指定其它已配置端点。

### 核心改动

- **`novelforge/services/llm_client.py`**：`__init__` 新增 `reasoning_effort` 参数；`stream_chat_completion` 与 `chat_completion` 在 reasoning_effort 非空且不属于禁用集合 `{"", "none", "off"}` 时写入 payload `reasoning_effort` 字段（OpenAI 兼容网关统一字段）。
- **`novelforge/ui/settings_dialog.py`**：新增 `REASONING_EFFORT_OPTIONS` 7 档（不发送/auto/minimal/low/medium/high/max）+ `_reasoning_effort_combo` 下拉，端点编辑表单含此字段，保存/加载 roundtrip。
- **`novelforge/core/config.py`**：新增 `flow_endpoints: {}` 默认字段（{flow_key: endpoint_id} 映射）；`add_endpoint` setdefault `reasoning_effort` 字段；新增 `get_flow_endpoints`/`get_flow_endpoint(flow_key)`（未配置/端点被删回退默认端点）/`set_flow_endpoints(mapping)` 三方法；无需迁移版本（setdefault 自动补全）。
- **`novelforge/ui/flow_endpoint_dialog.py`**（新文件）：`FLOW_DEFINITIONS` 7 流程清单 + `FlowEndpointDialog` 为每流程提供端点下拉（默认=端点管理默认端点，可选其它已配置端点），保存调 `set_flow_endpoints` 持久化。
- **3 个 worker**（`continuation_worker.py`/`volume_orchestrator.py`/`audit_worker.py`）：从 parameters/`__init__` 读取 reasoning_effort 传入 LLMClient。
- **3 个提取器/服务**（`context_extractor.py`/`ontology_extractor.py`/`custom_audit_rule_service.py`）：`_get_llm_client(flow_key="")` 支持流程端点解析 + reasoning_effort 注入，调用点传对应 flow_key（context_extraction/ontology_extraction/protagonist_extraction/custom_rule_parsing）。
- **`novelforge/ui/continuation_panel.py`**：新增 `select_endpoint_by_id` 方法供流程配置同步面板下拉。
- **`novelforge/ui/main_window.py`**：工具菜单新增"流程端点配置(&E)..."入口；`_on_open_flow_endpoint_dialog` 弹对话框后调 `_refresh_endpoints` 同步面板端点到当前模式流程配置；`_resolve_reasoning_effort(endpoint, preset)` 解析思考强度（预设级 generation_params.reasoning_effort 优先，其次端点级，均空不发送）激活预设管理器原有死字段；4 处 worker 注入点（单章续写/卷续写/单章审计/审计后重写）调用此方法；single_audit 流程端点配置优先回退面板下拉。

### 设计决策

1. 统一 `reasoning_effort` 字段（OpenAI 兼容），不区分厂商私有格式。
2. 预设级优先于端点级（激活预设管理器现有死字段，更具体的配置胜出）。
3. 流程指定即生效，提取器/审计直接用配置，续写/卷续写通过面板同步。
4. 审计重写用续写端点（非审计端点）。
5. 不新增迁移版本（`flow_endpoints` + `reasoning_effort` 通过 setdefault 自动补全）。

### 测试

- 新增 `tests/test_reasoning_effort.py` 12 用例：LLMClient 存储/注入条件（high 注入、空串跳过、none/off 大小写不敏感跳过、max 注入）+ `_resolve_reasoning_effort` 优先级（预设优先、预设 none/off 忽略、端点回退、无预设、均空、端点缺字段）。
- 新增 `tests/test_flow_endpoint_config.py` 9 用例：ConfigManager flow_endpoints CRUD（未配置/已配置/端点被删回退/无端点/roundtrip/默认空）+ FlowEndpointDialog（7 流程/加载保存 roundtrip/默认选中）。
- 修复 `tests/test_protagonist_extraction.py` 7 处 `_get_llm_client` lambda stub 回归（签名从 `lambda:` 改为 `lambda flow_key="":` 适配 context_extractor 新签名）。
- `python -m pytest tests/ -q`：561 passed, 13 skipped, 0 failed。

### 文档同步

- 更新 `agent.md`：6 处描述同步（settings_dialog reasoning_effort 下拉 / config.py flow_endpoints + 3 方法 / 新增 flow_endpoint_dialog 模块条目 / main_window 工具菜单入口 + _resolve_reasoning_effort + 4 注入点 / ontology_extractor + custom_audit_rule_service _get_llm_client flow_key 改造）。
- 更新 `update.md`：顶部追加本条目。

---

## 2026-07-01：修复章节保存报错 + 正文丢失 + 编辑器新增保存按钮

### 背景

1. 编辑章节保存时报错 `sqlite3.OperationalError: table chapters has no column named protagonist_profile`，保存失败。
2. 报错后该章正文文件被删除，章节内容消失。根因：`save_chapter` 回滚时无条件删文件，未用已捕获的旧正文恢复。
3. 编辑器工具栏仅有"编辑"按钮，无可见"保存"按钮（仅 Ctrl+S 菜单与 5 秒自动保存）。

### 核心改动

- **`novelforge/core/storage.py`**：
  - open 流程补调 `await self._migrate_chapters_columns()`（迁移函数早已实现但漏调用，旧库缺 `protagonist_profile` 列导致保存报错）。
  - `save_chapter` 回滚区分已存在/新章节：已存在章节 SQLite 写入失败时用 `chapter_file.write_bytes(old_bytes)` 恢复旧正文而非删文件（修复编辑现有章节保存失败时正文丢失）；新章节仍 `unlink` 删刚写入文件。
- **`novelforge/ui/chapter_editor.py`**：新增 `save_requested = Signal()`；工具栏在"编辑"按钮前插入"保存"按钮 `_save_btn`；`_setup_connections` 连接 `_save_btn.clicked` → `save_requested.emit`；`set_streaming_locked` 流式输出时禁用保存按钮。
- **`novelforge/ui/main_window.py`**：`_connect_signals` 连接 `chapter_editor.save_requested` → `_on_save`，复用 Ctrl+S 落盘链路（`save_now()` + `storage_service.save_chapter()`）。

### 测试

- `python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"`：468 passed, 13 skipped, 12 deselected，无回归。

### 文档同步

- 更新 `agent.md`：`core/storage.py` 条目补注 open 流程调用迁移 + 回滚恢复 old_bytes；`ui/chapter_editor.py` 条目补注保存按钮 + save_requested 信号。

---

## 2026-07-01：主角形象章节级持久化 + 世界观序列化修复

### 背景

1. 主角形象提取结果仅存内存 LRU + 24h TTL cache 表，未落盘到 chapters 表，进程重启/缓存过期后丢失。
2. 自定义规则生成时世界观底层显示"（世界观底层序列化失败）"——WorldOntology.extracted_at 为 datetime 对象，model_dump() 返回 dict 含 datetime，json.dumps 无法序列化 → TypeError 被静默 except 吞掉 → 返回占位文本。

### 核心改动

- **`novelforge/models/chapter.py`**：Chapter 新增 `protagonist_profile: ProtagonistProfile | None` 字段
- **`novelforge/core/storage.py`**：chapters 表 SCHEMA 加 `protagonist_profile TEXT` 列；新增 `_migrate_chapters_columns` 幂等迁移旧库；新增 `update_chapter_protagonist` 单列 UPDATE 方法（避免 save_chapter 用空 content 覆盖正文）；`_row_to_chapter` row[8] + len(row) 防御；save_chapter INSERT/UPSERT 加列
- **`novelforge/services/storage_service.py`**：新增 `update_chapter_protagonist` 同步代理
- **`novelforge/ui/main_window.py`**：`_on_protagonist_done` 提取完成后落盘 chapters 表 + LRU 热缓存；`_load_context_entries_for_chapter` 独立恢复分支优先 chapter.protagonist_profile → 兜底 cache 表 → LRU（与 ctx_extract 解耦）；4 个使用点（单章续写/卷级/查看/审计）改为优先 chapter 字段兜底 LRU
- **`novelforge/services/custom_audit_rule_service.py`**：`_format_world_ontology` 改用 `model_dump(mode="json")` 递归转 datetime→ISO 字符串 + `except Exception as e: logger.warning` 暴露根因

### 修复效果

- 主角形象提取完成后持久化到当前章节，切换章节/重启进程后可从 chapter.protagonist_profile 恢复
- 自定义规则生成时世界观底层正确序列化为 JSON（含 extracted_at ISO 时间戳），不再显示"序列化失败"

## 2026-06-30：自定义设定流式窗口 + 跨线程/session 泄漏修复

### 背景

新增自定义设定时输出完成后卡死，且与其它功能不一致（无流式窗口）。同时控制台报三类错误：`ERROR asyncio: Unclosed client session` / `ERROR asyncio: Unclosed connector` / `QObject::setParent: Cannot set parent, new parent is in a different thread`。

### 核心改动

- **`novelforge/services/custom_audit_rule_service.py`**：`parse_rule_streaming` 由非流式 `chat_completion` 改为 `stream_chat_completion` 真流式 async iterator，逐 chunk 经 `on_chunk(content)` 回调推送 UI；整个 client 使用包裹 `try/finally`，`finally` 中 `await client.close()` 释放 aiohttp session，消除 Unclosed client session/connector 警告
- **`novelforge/services/ontology_extractor.py`**：`extract_ontology_streaming` 同样补 `try/finally: await client.close()`，消除 ontology 链路 session 泄漏
- **`novelforge/ui/context_preview_panel.py`**：新增 4 个 custom_rule 流式方法 `start_custom_rule_parsing`/`update_custom_rule_progress`/`finish_custom_rule_parsing`/`fail_custom_rule_parsing`，复用 `_stream_view`，与 ontology/protagonist 互斥（单次 LLM 调用无批次概念）
- **`novelforge/ui/main_window.py`**：新增 2 个类级信号 `_custom_rule_chunk_received(str)`/`_custom_rule_done(object,str)` + `__init__` 连接；重写 `_on_add_custom_rule_requested` 调 `start_custom_rule_parsing` 启动流式 UI，`on_chunk`/`on_done` 闭包仅 emit 信号不直接调 GUI；新增槽 `_on_custom_rule_chunk_received`/`_on_custom_rule_done` 在 UI 线程执行 finish/fail + QMessageBox，消除 `QObject::setParent` 跨线程违规与卡死

### 根因

1. `QObject::setParent` + 卡死：`on_done` 回调在 asyncio 后台线程运行却直接调 `QMessageBox`（parent=MainWindow 在 UI 线程），违反 Qt 跨线程 GUI 规则 → 改为仅 emit 信号，槽方法在 UI 线程执行
2. `Unclosed client session/connector`：`_get_client` 创建 `LLMClient` 但全程未 `await client.close()`，aiohttp session 在 GC 时触发警告 → `try/finally` 兜底关闭
3. 无流式窗口：原用非流式 `chat_completion`，MainWindow 未传 `on_chunk`、未调 `start_*` UI 方法 → 改用 `stream_chat_completion` + 4 个流式 UI 方法 + 信号槽

### 文档同步

- `agent.md`：4 处描述更新（custom_audit_rule_service.py 流式+finally / ontology_extractor.py finally / context_preview_panel.py 4 custom_rule 流式方法 / main_window.py §12 信号+槽+重写说明）

---

## 2026-06-30：自定义审计必查项 + 输出栏高亮

### 背景

用户希望输入自定义设定/要求（如"主角不得杀人""禁止穿越时空"），由 AI 结合世界观底层与上下文结构化为"要求 + 审计向"两字段，保存到项目全局，作为审计必查项注入续写生成与审计全链路；审计不通过则一票否决。参考世界观底层提取模式，在上下文预览面板按钮旁增加"新增自定义设定"和"查看自定义设定"按钮。同时需求 4：输出栏可自行选中对应高亮让我记住（右键菜单 + 多色 4 色：黄/绿/蓝/红 + 可选备注 + 持久化）。

### 核心改动

- **新增数据模型** `novelforge/models/custom_audit_rule.py`：`CustomAuditRule`（id/title/raw_input/requirement/audit_criteria/severity/created_at）；`Project.custom_audit_rules: list[Any]` 字段保存全局自定义设定；`Continuation.highlights: list[dict]` 字段保存输出栏高亮 {start,end,color,note}
- **新增服务** `novelforge/services/custom_audit_rule_service.py`：`CustomAuditRuleService.parse_rule_streaming` 异步协程加载 `phase_custom_rule_parse.txt` 模板，注入 raw_input + world_ontology + context_entries，AI 结构化为 requirement + audit_criteria，2 次重试温度 0.2/0.0，协程内 `await storage_service.storage.save_project` 持久化避免重入死锁
- **新增 UI** `novelforge/ui/custom_rule_dialog.py`：`CustomRuleInputDialog`（输入 raw_input）+ `CustomRulesViewDialog`（列表展示 + 删除回调）
- **上下文预览面板** `context_preview_panel.py`：新增"新增自定义设定"按钮 + `add_custom_rule_requested` 信号 + "查看自定义设定"按钮 + `view_custom_rules_requested` 信号，4 个提取按钮 setEnabled 同步联动
- **续写面板** `continuation_panel.py`：输出框启用右键菜单高亮——4 色（黄#FFFACD/绿#C8FFB4/蓝#B4DCFF/红#FFB4B4）+ 可选备注（QInputDialog）+ 清除选区/全部；7 方法（`_on_output_context_menu`/`_prompt_note_and_add`/`_add_highlight`/`_clear_highlights_in_range`/`_clear_all_highlights`/`apply_highlights`/`set_highlights`）；`highlights_changed = Signal(list)` 通知 MainWindow 持久化；`set_current_swipe` 加载 `swipe.highlights` + `apply_highlights`
- **主窗口** `main_window.py`：CustomAuditRuleService 实例 + 5 槽方法（`_format_custom_audit_rules`/`_on_add_custom_rule_requested`/`_on_view_custom_rules_requested`/`_delete_custom_rule`/`_on_highlights_changed`）；3 处注入：单章续写 assemble 传 `custom_audit_rules`、单章审计 `rendered.replace("{{custom_audit_rules}}", custom_rules_str)`、VolumeOrchestrator 构造传 `custom_audit_rules`
- **存储层** `core/storage.py`：continuations 表新增 `highlights TEXT` 列（幂等迁移 `_migrate_continuations_columns`），INSERT/SELECT/`_row_to_continuation` 同步处理（JSON 序列化/反序列化）
- **提示词组装** `core/prompt_assembler.py`：`assemble` + `_build_macro_context` 支持 `custom_audit_rules` 参数，经 `_format_custom_audit_rules` 格式化填入 `{{custom_audit_rules}}` 宏
- **卷编排器** `services/volume_orchestrator.py`：`__init__` 接收 `custom_audit_rules` 参数，9 处 phase 方法注入 `{{custom_audit_rules}}` 宏
- **默认预设** `default_preset.json`：main 提示词在【主角信息】段后追加【自定义设定/审计必查项】段注入 `{{custom_audit_rules}}`
- **审计维度同步**：`agent.py` `VALID_CRITIQUE_CATEGORIES` 15 值（+custom_rules_compliance）、`volume.py` `DEFAULT_AUDIT_DIMENSIONS` 12 维度（+custom_rules_compliance）、`phase_verify.txt` 15 维度、`phase_outline_audit.txt` 12 维度、`phase_single_audit.txt` 6 维度；10 个 phase 模板 + phase_custom_rule_parse.txt 均含 `{{custom_audit_rules}}` 占位符段；`custom_rules_compliance` 为单一合并维度一票否决（未满足 high 严重度项判 critical，score ≤ 2）

### 测试

- `tests/test_volume_prompts.py`：5 个测试 macros 字典追加 `"{{custom_audit_rules}}": "（无自定义设定）"`；`test_phase_verify_template_14_dimensions` 断言从"十四个维度"改为"十五个维度"，维度列表追加 `custom_rules_compliance`
- `tests/test_volume_models.py`：`test_volume_run_config_default_audit_dimensions` 与 `test_volume_run_config_audit_dimensions_independent` 的 `len == 11` 改为 `len == 12`，期望列表追加 `custom_rules_compliance`
- `tests/test_volume_orchestrator.py`：`test_style_audit_dimension` 的 `len(DEFAULT_AUDIT_DIMENSIONS) == 11` 改为 `== 12`
- `python -m pytest tests/test_volume_ui.py tests/test_volume_prompts.py tests/test_volume_models.py -q`：89 passed
- 全量回归：8 处测试同步修复后无新增失败；7 个 context extraction 失败为预先存在（主角提取解耦未同步测试，`context_extractor.py:2156` 注释明确"主角形象提取已从上下文提取流程中移除"），与本任务无关

### 文档同步

- `agent.md`：架构分层新增 3 模块（custom_audit_rule.py/custom_audit_rule_service.py/custom_rule_dialog.py）；更新 agent.py（14→15 值）、volume.py（11→12 维度）、storage.py（highlights 列）、prompt_assembler.py（custom_audit_rules 参数）、volume_orchestrator.py（custom_audit_rules 参数 + 9 处注入）、context_preview_panel.py（2 信号+2 按钮）、continuation_panel.py（右键高亮）、main_window.py（5 槽方法+3 处注入）、default_preset.json（{{custom_audit_rules}} 宏）、phase_verify.txt（14→15 维度）；§9 OutlineAuditReport 12 维度；§10 额外注入宏新增 `{{custom_audit_rules}}`；§11 单章审计 6 维度；新增 §12"自定义设定/审计必查项"设计决策小节；测试描述同步维度计数

## 2026-06-30：主角形象提取独立按钮

### 背景

将主角形象提取从"上下文提取副产品"模式解耦为独立流程，提供独立的"提取/查看"按钮，方便用户按需操作，与"提取世界观底层/查看世界观底层"按钮对称。原模式中主角形象作为上下文提取的副产品自动产出，与 8 维度条目共享缓存 key `ctx_extract:`，存在耦合难以独立触发与查看。

### 核心改动

- **`novelforge/services/context_extractor.py`**：新增独立缓存 key 前缀 `PROTAGONIST_CACHE_KEY_PREFIX = "protagonist"`（与 `ctx_extract:` 解耦避免互相覆盖）；新增公共方法 `extract_protagonist_streaming`（流式 + 多批次合并 + 批次级 2 次重试，镜像 `OntologyExtractor.extract_ontology_streaming` 模式）与 `load_cached_protagonist`（章节级独立加载）；`_extract_common` 不再调用 `_extract_protagonist`（完全解耦，ExtractResult.protagonist_profile 恒为 None）；新增辅助方法 `_build_protagonist_cache_key`/`_get_cached_protagonist`/`_save_cached_protagonist`
- **`novelforge/ui/context_preview_panel.py`**：新增"提取主角形象"按钮（`#primaryBtn`）+ `extract_protagonist_requested` 信号 + "查看主角形象"按钮（`#secondaryBtn`）+ `view_protagonist_requested` 信号；新增 5 个流式方法 `start_protagonist_extraction`/`update_protagonist_progress`/`update_protagonist_batch`/`finish_protagonist_extraction`/`fail_protagonist_extraction` 复用 `_stream_view`；`restore_extraction_state` 新增 `is_protagonist` 参数（与 `is_ontology` 互斥，同时为 True 时以 `is_protagonist` 为准）
- **`novelforge/ui/main_window.py`**（4 处改动）：①新增 6 个 protagonist 信号处理方法（`_on_extract_protagonist_requested`/`_on_protagonist_chunk_received`/`_on_protagonist_batch_done`/`_on_protagonist_done`/`_on_view_protagonist_requested`/`_format_protagonist_for_display`），镜像 ontology 模式但 `_on_protagonist_done` **不调 save_project**（章节级 LRU only 不持久化 Project），`_on_view_protagonist_requested` 从 `_protagonist_profile_by_chapter` 加载而非 `project.world_ontology`；②章节切换新增主角恢复分支（`_protagonist_stream_text` 缓冲 + `restore_extraction_state(is_protagonist=True)`）；③`_load_context_entries_for_chapter` 新增独立 protagonist 缓存恢复分支（调 `load_cached_protagonist` 用 `cached_protagonist_data.get("protagonist_profile")` key 还原）；④`_on_extract_done` 删除原 `result.protagonist_profile` 捕获代码（解耦后恒为 None）

### 测试

- `tests/test_protagonist_extraction.py`：+3 测试类 12 用例
  - `TestExtractProtagonistStreaming`（5 用例）：单批次返回/多批次合并/提取+load_cached roundtrip/on_chunk 回调/2 次重试失败返回 None
  - `TestExtractCommonNoProtagonist`（2 用例）：extract 结果 protagonist_profile 为 None/缓存不写入 protagonist 独立 key
  - `TestContextPanelProtagonistButtons`（5 用例）：按钮存在/extract 信号发射/view 信号发射/start 禁用按钮/finish 恢复按钮
- `python -m pytest tests/test_protagonist_extraction.py tests/test_volume_ui.py -q`：52 passed，无回归

### 文档同步

- `agent.md`：§3 services/context_extractor.py 描述更新（独立缓存 key + 公共方法 + 解耦）；§3 关键设计决策第 3 项"主角形象一致性提取"段落补充独立链路解耦说明；§5 context_preview_panel.py 描述新增 protagonist 按钮/信号/流式方法/restore_extraction_state 三参数；§5 main_window.py 描述新增 6 个 protagonist 处理方法 + 章节切换恢复 + 独立缓存加载 + 删除旧捕获代码

## 2026-06-30：批次提取失败自动重试

### 背景

世界观底层与上下文提取分批次调用 LLM 时，单批次失败（网络波动/JSON 解析失败/超时等）会立即中止整体提取，用户需从头重跑。用户要求：单批次失败立即自动重试该次，而非整体中止。

### 核心改动

- **`novelforge/services/ontology_extractor.py`**：`extract_ontology_streaming` 批次循环改为 2 次重试（温度 0.2/0.0），覆盖 TimeoutError/AuthError/RateLimitError/APIError/LLMError/JSONDecodeError/无 choices/其他非取消异常，`asyncio.CancelledError` 不重试；新增常量 `ONTOLOGY_EXTRACT_TEMPERATURE_RETRY = 0.0`
- **`novelforge/services/context_extractor.py`**：8 维度批次循环（`extract`/`extract_streaming`）与主角形象批次循环（`_extract_protagonist`）均改为 2 次重试，重试条件同上；新增常量 `EXTRACT_TEMPERATURE_RETRY`/`PROTAGONIST_EXTRACT_TEMPERATURE_RETRY`；流式分支重试时清空 `content_parts` 避免拼接残缺内容
- 镜像【信息汇总】合并环节（`_run_ontology_merge`/`_run_protagonist_merge`/`_run_merge_entries`）现有的 2 次重试策略

### 测试

- `tests/test_ontology_extractor.py`：+3 用例（LLMError 重试 / JSON 解析失败重试 / 重试耗尽失败）
- `tests/test_protagonist_extraction.py`：+1 用例（LLMError 重试）
- `tests/test_m4_context_extraction.py`：+2 用例（LLMError 重试 / 重试耗尽失败）+ 5 旧用例 call_count 断言同步更新（主角形象解析失败现触发重试，调用次数 +1）
- `tests/test_e2e_workflow.py`：1 用例 call_count 断言同步更新（同上）
- 全量回归：456 passed, 13 skipped

### 文档同步

- agent.md §3「提取与续写解耦」新增「批次级自动重试」条目，覆盖三个提取器（ContextExtractor 8 维度批次 / _extract_protagonist 主角形象批次 / OntologyExtractor 批次）

## 2026-06-30：重新检视【回溯章节数】与【最近 10 章正文参考】概念分离

### 背景

卷续写模式下章节正文会同时出现在两个地方造成重复：(1) 聊天历史（受单章模式"回溯章节数"参数 `lookback_chapters` 控制，默认 5 章，来自 ContinuationPanel `_lookback_spin`，卷模式下隐藏但仍活跃）截断 chat history；(2) `volume_orchestrator.py` 在 assemble 后额外注入"最近 10 章正文参考（含本卷已生成）"系统消息。两者重叠导致 5 章正文重复出现两次。用户要求：单章生成用"回溯章节数"，多章节生成用"最近 10 章正文参考（含本卷已生成）"，卷模式不再使用"回溯章节数"以免多次重复章节。

### 核心改动

- **`novelforge/core/prompt_assembler.py`**：`assemble()` 与 `_build_history()` 新增 `skip_history: bool = False` 参数；`skip_history=True` 时 `_build_history` 直接返回空列表，不构建任何章节历史消息。默认 `False`，单章续写与提示词预览不受影响。
- **`novelforge/services/volume_orchestrator.py`**：`_run_chapter_writing()` 的 `prompt_assembler.assemble()` 调用新增 `skip_history=True`，移除原 `lookback_chapters=self.parameters.get("lookback_chapters", 0)` 参数（不再泄漏单章模式参数到卷模式）；卷模式写作阶段聊天历史为空，前文仅由"最近 10 章正文参考（含本卷已生成）"系统消息提供；`effective_chapters` 仍传入供 Jinja2 render_context 与宏上下文使用。

### 测试

- 新增 `tests/test_volume_orchestrator.py::test_volume_writing_skips_chat_history`：N=2 预置 1 章含唯一正文标记，断言写作 messages 含"最近 10 章正文参考"系统消息但无 user 消息含预置章节正文，验证 `skip_history=True` 消除 chat history 与系统消息的章节正文重复。
- `python -m pytest tests/test_volume_orchestrator.py tests/test_m2_prompt_assembly.py tests/test_volume_e2e.py tests/test_volume_prompts.py -q`：74 passed，无回归。

### 文档同步

- `agent.md`：§5 UI 布局规范明确"回溯章节数仅在单章模式生效"；§9 卷级续写 `_run_chapter_writing` 描述注明 `skip_history=True` 跳过聊天历史；架构分层 `prompt_assembler.py` 描述加 `skip_history` 参数；代码风格规范新增"前文参考机制按模式分离"条目；测试用例计数 36→37。

---

## 2026-06-30：发布 v0.2.2（世界书/正则条目内联勾选开关）

### 版本号同步

- `novelforge/__init__.py`：`__version__` 由 `0.2.1` → `0.2.2`
- `README.md`：顶部「当前版本」由 `v0.2.1` → `v0.2.2`；「更新记录」章节新增 `v0.2.2` 小节（精简 3 条）
- `agent.md`：「当前版本」由 `v0.2.1` → `v0.2.2`

### 核心改动

- **模型**（`novelforge/models/context.py`）：`ContextEntry` 新增 `enabled: bool = True` 字段，控制条目是否注入上下文；默认 True 保证旧 JSON/提取/新建条目向后兼容。
- **ST 导入/导出**（`worldbook_importer.py` / `worldbook_service.py`）：导入时由 ST `disable` 字段反序列化（`disable=true → enabled=false`）；导出时 `disable = not enabled`（原硬编码 `False`），实现 ST 往返保持。
- **世界书 UI**（`worldbook_manager.py`）：`_refresh_entry_list` 为每条目加 `ItemIsUserCheckable` + `setCheckState`（参照 `preset_manager._refresh_prompt_list`）；新增 `_on_entry_check_changed` handler（`itemChanged` → `worldbook_service.set_entry_enabled` 即时持久化 + `[禁用]` 前缀 + `worldbook_changed` 信号）；`_refresh_entry_list` 整体 `blockSignals(True)` 防递归；复制世界书补 `enabled=e.enabled`。
- **服务层**（`worldbook_service.py`）：新增 `set_entry_enabled(wb, uid, enabled) -> bool`。
- **注入过滤**（`main_window.py`）：`_get_enabled_worldbook_entries` 由 `list(wb.entries)` 改为 `[e for e in wb.entries if e.enabled]`，单点覆盖 3 处续写入口（单章/卷/提示词预览均经 `_merge_worldbook_entries`），禁用条目不注入上下文。
- **正则 UI**（`regex_manager.py`）：`_refresh_script_list` 当前作用域脚本加 `ItemIsUserCheckable` + `setCheckState`（反向映射 `disabled`，Checked=启用）；移除 `[禁用]` 文本前缀（改由复选框表达）；整体 `blockSignals(True)` 包裹防递归；新增 `_on_script_check_changed` handler（仅处理可勾选项，跳过分隔项与其他作用域脚本；`model_copy(update={"disabled": ...})` + `update_script` 即时持久化 + 同步右侧 `_disabled_check`）；其他作用域脚本保留 `[禁用]` 文本前缀且不可勾选。
- **正则默认状态**：无需额外改动——`default_regex_scripts.json` 4 条 `disabled:false`（全启用），`ensure_default_scripts_exist` 首次注入即写 `disabled:false`，内联复选框读取 `script.disabled` 后默认全勾选，符合「按案例默认开关加载」。

### 测试

`python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"`：430 passed, 13 skipped, 12 deselected。含 worldbook/regex/context 相关 146 用例全通过，无回归。

### 文档同步

README.md 新增 v0.2.2 更新记录小节（精简 3 条）；agent.md 同步版本号；update.md 追加本发布条目。

---

## 2026-06-30：世界书/正则条目内联勾选开关（参照预设）

### 背景

世界书与正则管理器的条目列表此前无内联勾选开关：世界书条目无条目级启用字段（仅有书级 enabled），正则虽模型含 `disabled` 字段但列表仅以 `[禁用]` 文本前缀呈现，切换需进入右侧编辑器勾选「禁用」再点保存，操作繁琐。用户要求参照「预设管理器」的条目前方勾选开关，可单独点击启用/禁用；正则默认加载后按内置案例 `default_regex_scripts.json` 的默认开关加载，无需手动调整。

### 核心改动

- **模型**（`novelforge/models/context.py`）：`ContextEntry` 新增 `enabled: bool = True` 字段，控制条目是否注入上下文；默认 True 保证旧 JSON/提取/新建条目向后兼容。
- **ST 导入/导出**（`worldbook_importer.py` / `worldbook_service.py`）：导入时由 ST `disable` 字段反序列化（`disable=true → enabled=false`）；导出时 `disable = not enabled`（原硬编码 `False`），实现 ST 往返保持。
- **世界书 UI**（`worldbook_manager.py`）：`_refresh_entry_list` 为每条目加 `ItemIsUserCheckable` + `setCheckState`（参照 `preset_manager._refresh_prompt_list`）；新增 `_on_entry_check_changed` handler（`itemChanged` → `worldbook_service.set_entry_enabled` 即时持久化 + `[禁用]` 前缀 + `worldbook_changed` 信号）；`_refresh_entry_list` 整体 `blockSignals(True)` 防递归；复制世界书补 `enabled=e.enabled`。
- **服务层**（`worldbook_service.py`）：新增 `set_entry_enabled(wb, uid, enabled) -> bool`。
- **注入过滤**（`main_window.py`）：`_get_enabled_worldbook_entries` 由 `list(wb.entries)` 改为 `[e for e in wb.entries if e.enabled]`，单点覆盖 3 处续写入口（单章/卷/提示词预览均经 `_merge_worldbook_entries`），禁用条目不注入上下文。
- **正则 UI**（`regex_manager.py`）：`_refresh_script_list` 当前作用域脚本加 `ItemIsUserCheckable` + `setCheckState`（反向映射 `disabled`，Checked=启用）；移除 `[禁用]` 文本前缀（改由复选框表达）；整体 `blockSignals(True)` 包裹防递归；新增 `_on_script_check_changed` handler（仅处理可勾选项，跳过分隔项与其他作用域脚本；`model_copy(update={"disabled": ...})` + `update_script` 即时持久化 + 同步右侧 `_disabled_check`）；其他作用域脚本保留 `[禁用]` 文本前缀且不可勾选。
- **正则默认状态**：无需额外改动——`default_regex_scripts.json` 4 条 `disabled:false`（全启用），`ensure_default_scripts_exist` 首次注入即写 `disabled:false`，内联复选框读取 `script.disabled` 后默认全勾选，符合「按案例默认开关加载」。

### 测试

`python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"`：430 passed, 13 skipped, 12 deselected。含 worldbook/regex/context 相关 146 用例全通过，无回归。

### 文档同步

`agent.md`：目录树 `context.py`/`regex_manager.py`/`worldbook_manager.py` 描述行更新；关键设计决策 §1 ST 兼容性新增条目级 enabled↔disable 映射说明；§3 提取与续写新增「世界书条目级开关控制注入」条目。

---

## 2026-06-30：发布 v0.2.1（合并 feat-context-extraction-merge 分支）

### 背景

合并 `feat-context-extraction-merge-DRARZ8` 工作树分支至主干，整合自 v0.2.0 以来的全部改动并发布 v0.2.1。本次合并涉及 72 个文件、+22571/-535 行变更，涵盖上下文提取体系重构、单章续写模型重做、默认预设/正则量身定制、卷续写三项增强与多项关键 bug 修复。详细变更见下文各日期条目（2026-06-29 ~ 2026-06-30）。

### 版本号同步

- `novelforge/__init__.py`：`__version__` 由 `0.2.0` → `0.2.1`（"关于"对话框 `_on_about` 引用，运行时显示 `版本: 0.2.1`）
- `README.md`：顶部「当前版本」由 `v0.2.0` → `v0.2.1`；「更新记录」章节新增 `v0.2.1` 小节，按新增功能/修复分类汇总本次合并要点
- `agent.md`：「当前版本」由 `v0.2.0` → `v0.2.1`

### 主要内容（详见下文各日期条目）

- 上下文提取改造：世界观底层 7 维度提取 + 主角形象 8 维度一致性 + protagonist_behavior 维度
- 单章续写模型重做：链式 → 提升为章节 + 删除续写 + 审计与修正
- 章节切换状态保留（后台继续 + UI 按章保留）
- 卷续写三项增强：强制修改 / 阶段产物阅览 / 动态前文修复
- 默认预设 4→16 条分层模块化 + 4 条核心正则 + 预设管理器增强
- 重写 phase_verify.txt 为 10 维度审计
- 变量与宏调用教程（README 新增章节）
- 关键 bug 修复：save_project/save_chapter 级联删除、章节列表为空、世界观提取死锁、默认正则未注入、深度分析切分超长、删除章节 reindex 清空正文

### 文档同步

README.md 新增 v0.2.1 更新记录小节；agent.md 同步版本号；update.md 追加本发布条目。

### 回归

无需重跑测试——本次为版本号同步与发布记录，未改动生产代码逻辑（合并本身的全量回归见 2026-06-29 各条目末尾的 pytest 结果）。

---

## 2026-06-30：章节切换状态保留机制（后台继续 + UI 按章保留）

### 背景

用户反馈：在「制定章节提取上下文」等流式接收过程中切换到其它章节，操作会被打断——UI 状态丢失、chunk 被丢弃、swipe 存入错误章节。需保证切换出去再切回时仍处于"接收中"状态。经确认采用"后台继续 + UI 按章保留"策略，覆盖全部场景含卷续写：上下文提取 / 世界观提取 / 单章续写 / 单章审计采纳后重写 / 卷续写。

### 核心改动

- **`novelforge/ui/main_window.py`**：
  - `__init__` 新增 7 个状态缓冲字段：`_extract_stream_text_by_chapter`（dict 按章）/`_ontology_extracting`+`_ontology_stream_text`（项目级单字符串）/`_continuation_chapter_id`+`_continuation_stream_text_by_chapter`（dict 按章）/`_audit_chapter_id`/`_volume_chapter_id`
  - 章节守卫模式 `on_origin_chapter = bool(self._current_chapter and self._current_chapter.id == origin_cid)` — 仅用户停留在发起章节时刷新 UI；swipe 归档 / 插入点定位始终用发起章节 id
  - chunk 路由方法 `_on_continuation_chunk_received` / `_on_extract_chunk_received` / `_on_ontology_chunk_received`：总是缓冲到发起章节，UI 更新受守卫
  - 完成回调 `_on_continuation_finished` / `_on_continuation_error` / `_on_extract_done` / `_on_ontology_done` / `_on_volume_continuation_finished` / `_on_audit_cancelled` 均在所有分支清理对应缓冲字段与发起章节标记
  - `_on_start_continuation` / `_on_audit_continuation` / `_on_start_volume_continuation` 启动时记录发起章节 id
  - `_on_audit_accepted` 采纳后 `_audit_chapter_id` 交接给 `_continuation_chapter_id`，由续写链路接管 chunk 路由与 swipe 归档
  - `_on_chapter_selected` 重构：续写流式态恢复优先于 swipe 显示（`_continuation_chapter_id == chapter.id` 且缓冲存在 → `restore_streaming_state` + `set_streaming_locked(True)`，跳过 `set_current_swipe`/`clear_output`）
  - `_load_context_entries_for_chapter` 优先恢复提取/世界观态（`_extracting_chapter_id == chapter.id` 或 `_ontology_extracting` → `restore_extraction_state`，return 跳过缓存加载）
- **`novelforge/ui/context_preview_panel.py`**：新增 `restore_extraction_state(stream_text, is_ontology)` 方法，重建提取中视觉态（loading 动画 + 流式输出区回填 + 按钮态），区分上下文提取与世界观提取
- **`novelforge/ui/continuation_panel.py`**：新增 `restore_streaming_state(buffered_text)` 方法，复用 `start_streaming` 初始化后回填缓冲文本

### 测试

新增 `tests/test_chapter_switch_state.py` 26 用例 7 测试类：状态字段初值 2 项、续写 chunk 路由 4 项、续写完成归档 4 项、续写出错清理 2 项、提取 chunk 路由 3 项、世界观项目级跟踪 2 项、审计取消清理 2 项、卷续写完成清理 2 项、`_on_chapter_selected` 续写态恢复 5 项。全部通过（0.49s）。

### 文档同步

agent.md 新增 §12「章节切换状态保留（后台继续 + UI 按章保留）」设计决策，含 7 字段 + 章节守卫 + chunk 路由 + 恢复方法 + 恢复入口 + 单例约束 + 完成回调清理 7 要点；架构分层树 `main_window.py` 行追加章节切换状态保留简述；update.md 追加本条目。

### 回归

`python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"` 不破坏现有测试。

---

## 2026-06-29：新增变量与宏调用教程（README）+ agent.md 同步记录

### 背景

自定义预设中可调用的变量（如 `{{world_ontology}}`、`{{protagonist_profile}}`、`{{setvar}}`/`{{getvar}}`、Jinja2 白名单函数）此前仅散见于源码注释与 agent.md 架构描述，用户编写自定义预设时无集中参考。需在 README 编写一份完整调用教程，并在 agent.md 记录变量来源与更新规则，确保后续变更随时同步。

### 核心改动

- **README.md**：新增「变量与宏调用教程」章节（位于「技术栈」与「测试」之间），含替换顺序、三类变量说明（内置宏 11 个 + 额外注入宏 2 个 + ST 风格变量 3 语法 + Jinja2 白名单函数 16 个）、作用域与持久化、安全限制、调试方法，附示例代码
- **agent.md**：新增「关键设计决策 §10 提示词变量与宏体系（自定义预设可调用）」条目，集中记录变量来源（`MacroContext`/`PromptAssembler._build_macro_context` extra/`VariableStore`/`TemplateEngine` 白名单）与更新规则（任一来源变化时同步更新本条目与 README 教程）

### 文档同步

README.md 新增教程章节；agent.md 新增 §10 设计决策条目；update.md 追加本条目。

### 回归

纯文档改动，无代码变更，无需重跑测试。

---

## 2026-06-29：单章续写新增审计与修正功能

### 背景

单章续写生成后，用户无法对续写内容进行质量审计与基于审计意见的修正。卷续写虽有 10 维度审计（phase_verify.txt），但依赖大纲与卷级上下文，不适用单章场景。新增单章续写审计功能：精简 5 维度审计（仅针对用户输入、底层世界观、主角形象），流式输出审计报告，用户可编辑后采纳，采纳后基于审计意见重写续写内容。

### 核心改动

- **resources/defaults/agent/phase_single_audit.txt**（新建）：精简审计模板，5 维度（consistency/style/coherence/protagonist_consistency/worldview_consistency），4 占位符（written_text/user_input/world_ontology/protagonist_profile），不含 snapshot/outline/previous_chapters_text；protagonist_consistency 一票否决，worldview_consistency 严格给分；输出 JSON（summary/issues/passed），summary 必含【主角一致性审计】与【世界观一致性审计】标记段落
- **services/audit_worker.py**（新建）：QThread+asyncio 流式审计 worker，流式 stream_chat_completion 输出审计报告，不做正则后处理，finished 信号返回完整文本字符串；temperature 0.2/max_tokens 3000 低温稳定
- **ui/audit_dialog.py**（新建）：审计对话框，流式输出区（只读）+ 完成后可编辑 + 采纳/取消按钮；accepted_text/cancelled 信号；Esc 键触发取消
- **ui/continuation_panel.py**：新增"审计"按钮 _audit_btn + audit_continuation 信号，has_swipe 时启用，流式中禁用
- **ui/main_window.py**：新增 _on_audit_continuation（加载模板+组装宏+AuditWorker+AuditDialog）、_on_audit_accepted（采纳后构造修正 messages=原 messages+原内容+审计意见+修正要求，新建 ContinuationWorker created_by="audit_rewrite" 流式重写为新 swipe）、_format_world_ontology/_format_protagonist_profile 静态方法（JSON 序列化，None 占位）
- **utils/paths.py**：get_agent_prompt_path docstring 补充 single_audit 阶段名

### 测试

新增 tests/test_single_audit.py 17 用例：模板存在性/占位符完整性/维度正确性/输出格式 4 项、AuditWorker 初始化/信号/停止 3 项、AuditDialog 初始化/追加/完成/采纳信号/取消信号/失败 6 项、格式化辅助方法 None/dict 4 项。

### 文档同步

agent.md 架构分层树新增 audit_worker.py/audit_dialog.py 描述，更新 continuation_panel.py/main_window.py 描述；新增 §11 设计决策记录单章审计与修正流程；update.md 追加本条目。

### 回归

`python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"`：404 passed, 13 skipped, 12 deselected。

---

## 2026-06-29：单章续写改为提升为章节模型 + 新增删除续写功能

### 背景

单章续写原采用链式模型（Continuation.parent_id 形成链，接受仅标记入链不合并正文），不符合用户实际工作流：选定章节续写 → 根据内容决定保留 → 保留则在该章节下方插入一节 → 选中新章节再续写，以此往复。且续写无法删除。改为"提升为章节"模型并增加删除选项。

### 核心改动

- **chapter_list.py**：ChapterTreeModel 回退为扁平单层（仅章节，按 index 排序），移除 _TreeNode/N 级树/internalPointer/swipe_selected 信号/_build_tree/_build_node；续写不在章节列表显示
- **chapter_service.py**：新增 promote_continuation_to_chapter(chapter, continuation) -> (chapter, new_chapter)——当前章节之后所有章节 index 后移 1 位，创建新 Chapter（index=当前+1，content=续写内容），删除原续写记录（存储与 chapter.continuations）
- **continuation_panel.py**：新增"删除"按钮 _delete_btn + delete_continuation 信号，有续写时启用
- **main_window.py**：移除链式残留（_current_continuation/_build_chain_content/_on_swipe_selected/chain 内容拼接/parent_continuation_id 传参）；_on_accept_continuation 重写为调 promote_continuation_to_chapter → _refresh_chapter_list → _on_chapter_selected 自动选中新章节；新增 _on_delete_continuation 调 storage_service.delete_continuation
- **continuation_worker.py**：parent_continuation_id 参数保留但不再传入（默认 None）

### 测试

删除 tests/test_chain_continuation.py（链式模型废弃）；新增 tests/test_promote_continuation.py 6 用例（promote 创建新章节/后移后续章节 index/删除续写记录/原章节正文不变/从 chapter.continuations 移除/delete_continuation 删除记录）；适配 tests/test_e2e_workflow.py 2 处断言为 promote 模型。

### 文档同步

agent.md §8 更新为"单章续写提升为章节模型"；架构分层 chapter_list.py/chapter.py/continuation_worker.py/main_window.py 描述同步；update.md 追加本条目。

### 回归

`python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"`：387 passed, 13 skipped, 12 deselected。

---

## 2026-06-29：单章续写链式模型重构

### 背景

单章续写生成后，续写直接出现在章节列表【续写】下，且接受时合并到章节正文，不符合"接受后入链、续写链式向下追加"的预期。用户反馈：应在接受后才在章节列表出现，后续续写继续往下加在链上，而非直接加在正文里。

### 核心改动

- **数据模型**：`Continuation` 新增 `parent_id: str | None` 字段形成链式结构（None=章节直接子节点，否则挂在另一续写下）
- **存储层**：continuations 表新增 `parent_id` 列 + `idx_continuations_parent` 索引；CREATE TABLE 含该列；`_migrate_continuations_columns` 幂等 ALTER TABLE 补列；`save_continuation` INSERT 含 parent_id；`list_continuations`/`load_chapters_with_continuations` 改用显式列名 SELECT（非 `SELECT *`）保证 row 索引与 INSERT 列序一致；`_row_to_continuation` row[6]=parent_id 后续字段顺延 + `len(row)` 防御
- **接受逻辑**：`ChapterService.accept_continuation` 重写为链式模型——仅标记 `is_accepted=True`，不合并到 `chapter.content`，不互斥取消同父下其他已接受续写
- **章节树**：`ChapterTreeModel` 重构为 N 级树（`_TreeNode` + `internalPointer`），仅展示 `is_accepted=True` 的续写按 `parent_id` 形成链；未接受续写不在树中
- **主窗口**：`_current_continuation` 状态跟踪当前选中续写节点；`_build_chain_content` 沿 `parent_id` 回溯拼接链内容供 `PromptAssembler.assemble`；`_on_chapter_selected` 切换章节重置 `_current_continuation=None`（避免跨章节链污染）；`_on_continuation_finished` 不刷新树（未接受续写不在树中）；`_on_accept_continuation` 标记入链+刷新树+不重载编辑器
- **Worker**：`ContinuationWorker.__init__` 接收 `parent_continuation_id`，构造 `Continuation(parent_id=self.parent_continuation_id)`
- **面板**：`ContinuationPanel._accept_btn` 文案"接受并追加"→"接受"（链式模型不"追加"到正文）

### 修复的隐藏 Bug

实施过程中发现存储层 `_row_to_continuation` 从未读取 `parent_id`（前序会话声称已完成但实际遗漏），且 `SELECT *` 返回的物理列序在迁移后 parent_id 位于末尾，导致 row 索引错位。修复：CREATE TABLE 显式含 parent_id 列 + SELECT 改用显式列名 + `_row_to_continuation` 正确读取 row[6]。

### 测试

新增 `tests/test_chain_continuation.py` 13 用例：parent_id 默认值/round-trip/嵌套 round-trip、accept 不合并正文/不互斥取消兄弟、N 级树仅展示已接受/链式嵌套/未接受不显示、`_build_chain_content` 链回溯算法（含循环防护）、storage parent_id 持久化（含 None）。适配 `tests/test_e2e_workflow.py` 2 处旧 swipe 合并断言为链式模型断言。

### 文档同步

agent.md 新增"关键设计决策 §8 单章续写链式模型"+ 架构分层 5 处模块描述更新（chapter.py/storage.py/continuation_worker.py/chapter_list.py/main_window.py）；update.md 追加本条目。

### 回归

`python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"`：394 passed, 13 skipped, 12 deselected。

---

## 2026-06-29：修复默认正则未注入引擎导致思维链标签泄漏到正文

### 背景

默认预设的 `nf_cot` 模块要求 LLM 输出 `<novelforge_thinking>` 标签包裹思维链，配套的 `default_regex_scripts.json` 含 `NF-思维链隐藏` 正则（placement=AI_OUTPUT）用于在后处理阶段剔除思维链。但 `RegexService.load_default_scripts()` 方法存在却全代码库无调用方，默认正则从未被编译进 RegexEngine，导致续写生成的正文中思维链标签及内容不会被剔除。

### 根因

`main_window._refresh_regex_scripts` 仅通过 `get_ordered_scripts` 加载 global/preset/scoped 三个作用域脚本，默认正则资源文件不在其中。`ensure_global_scripts_exist` 仅在 global.json 不存在时创建空数组 `[]`，不写入默认脚本。

### 核心改动

- **`regex_service.py`**：新增 `ensure_default_scripts_exist()` 方法（与默认预设的 `ensure_default_preset_exists` 对称），首次运行或 global.json 为空时将 4 条默认正则注入 global.json，已含脚本时不覆盖；更新 `load_default_scripts` docstring
- **`main_window.py`**：初始化时 `ensure_global_scripts_exist` 替换为 `ensure_default_scripts_exist`（后者已覆盖前者职责）
- **`tests/test_regex_service_defaults.py`**：新增 4 个单元测试（不存在时注入/空数组时注入/已存在不覆盖/注入后可加载）

### 设计决策

- 默认正则写入 global 作用域而非新增"默认作用域"，复用现有 GLOBAL 加载分支，改动最小
- `ensure_default_scripts_exist` 仅在 global.json 为空时写入，不覆盖用户已有脚本（与 `ensure_default_preset_exists` 的"不覆盖"语义一致）
- 用户可在正则管理器中查看/编辑/禁用注入的默认脚本，与全局正则统一管理
- 不在「恢复默认预设」按钮中联动重置正则，保持单一职责

### 测试

- 新增 4 个单元测试全部通过
- 全量回归：`python -m pytest tests/ -x -q` → 448 passed, 13 skipped

### 文档同步

- `agent.md`：更新 `regex_service.py` 描述（新增 ensure_default_scripts_exist 说明）

---

## 2026-06-29：预设管理器新增「恢复默认预设」按钮

### 背景

内置默认预设升级为 16 条分层模块化提示后，已运行过软件的用户的本地 `~/.novelforge/presets/default.json` 仍保留旧版本（4 条最小提示）。`ensure_default_preset_exists` 仅在本地文件不存在时写入，不会自动覆盖，需提供主动同步入口。

### 核心改动

- **`preset_service.py`**：新增 `reset_default_preset()` 方法，从 `resources/defaults/default_preset.json` 重新加载并覆盖本地默认预设
- **`preset_manager.py`**：工具栏新增「恢复默认预设」按钮，点击后弹出确认对话框（警告会覆盖自定义修改且不可撤销），确认后调用 `reset_default_preset` 并刷新预设列表/提示列表/生成参数，发射 `preset_changed` 信号

### 测试

- 全量回归：`python -m pytest tests/ -x -q` → 444 passed, 13 skipped

### 文档同步

- `agent.md`：更新 `preset_service.py`（新增 reset_default_preset 说明）和 `preset_manager.py`（工具栏七按钮）描述

---

## 2026-06-29：为 NovelForge 量身定制默认预设 + 4 条核心正则 + 预设管理面板生成参数增强

### 背景

分析 `预设参考/` 下 5 个 SillyTavern 预设（TGbreak V3.1.1 / Femiris DS特化 / 百家饭 lunareclipse 2.0.1 / 夏瑾 双鱼座 0.40 / 梦鲸思客 V4），提炼分层结构、模块化切换、思维链、抗八股、结构化 XML 输出、emoji 分类等设计，为 NovelForge 量身定制功能丰富的默认预设。原 `default_preset.json` 仅 4 条最小提示，`default_regex_scripts.json` 为空数组。

### 核心改动

- **重写 `default_preset.json`**：4 条 → 16 条分层模块化提示
  - 系统基础层：`main`（system_prompt，含 `{{world_ontology}}`/`{{protagonist_profile}}`/`{{writing_style}}`/`{{target_words}}` 宏引用，要求 `<novelforge_thinking>` + `<novelforge_chapter>` XML 输出）
  - 功能模块层（默认开）：`nf_core_rules`（✅基础准则）/ `nf_anti_bagua`（🧊抗八股）/ `nf_anti_repetition`（🧊抗重复）/ `nf_word_count`（📘字数控制）
  - 文风互斥层：`nf_style_general`（通用网文，默认开）/ `nf_style_classical`（古风白描，默认关）/ `nf_style_lightnovel`（轻小说，默认关）
  - 推进互斥层：`nf_pacing_medium`（适中，默认开）/ `nf_pacing_conservative`（保守，默认关）/ `nf_pacing_aggressive`（冒险，默认关）
  - 增强层（默认开）：`nf_cot`（📖思维链 COT）
  - 可选层（默认关）：`nf_nsfw`（🌸NSFW）
  - Marker 层：`worldInfoBefore` / `chatHistory` / `worldInfoAfter`（沿用 ST 标准）
  - generation_params：temperature=1.0 / top_p=0.99 / top_k=0 / max_tokens=32000 / max_context=2000000 / reasoning_effort=high
- **重写 `default_regex_scripts.json`**：空数组 → 4 条核心正则（placement 均为 AI_OUTPUT=2）
  - `NF-思维链隐藏`：移除 `<novelforge_thinking>` 标签及内容
  - `NF-八股抹除`：移除"不容置疑/不易觉察/微不可察/指关节白/一抹弧度"等套话
  - `NF-破折号规范`：中文字符间的 `——` 改为逗号
  - `NF-空行清理`：3+ 连续换行压缩为 2 个
- **增强 `preset_manager.py` 生成参数区**：新增 `top_p`（QSpinBox 0-100 ×0.01）/ `top_k`（QSpinBox 0-1000）/ `reasoning_effort`（QComboBox auto/low/medium/high/max）三个控件，`_refresh_params` 和 `_on_save_params` 同步适配

### 设计决策

- 借鉴百家饭 setvar 三段式模块化 + 梦鲸 XML schema 结构化输出 + 夏瑾 emoji 分类 + TGbreak 分层结构 + Femiris few-shot 思路
- 八股抹除仅移除"不容置疑/不易觉察/微不可察/指关节白/一抹弧度"等几乎无合法用法的套路词，不盲目移除"而是/突然"等高频通用词
- temperature=1.0（追求创意）而非 TGbreak 的 0.01（追求稳定），适合小说续写场景
- NSFW 默认关闭，用户按需在预设管理面板开启
- 互斥文风/推进仅通过命名约定提示，不强制程序联动（用户可自由开关）

### 测试

- 结构校验：16 prompts / 16 order / character_id=100000 / 5 默认关闭 / 4 regex placement=[2] 全部通过
- 全量回归：`python -m pytest tests/ -x -q` → 444 passed, 13 skipped

### 文档同步

- `agent.md`：新增 `default_preset.json` / `default_regex_scripts.json` 描述，更新 `preset_manager.py` 描述（生成参数六项）

---

## 2026-06-29：重写 phase_verify.txt 沿用 outline_audit 10 维度并扩展模型

### 背景

用户要求重新修改"小说质量评审员"提示词（phase_verify.txt），着重提示世界观底层和主角信息的审计，反复多次着重强调，每次必须输出对应的审计结果。用户补充：沿用 phase_outline_audit.txt 中对 protagonist_consistency/worldview_consistency 两个维度的详细审计要求，并增加多个审计维度对齐 outline_audit 的 10 维度。

### 核心改动

- `models/agent.py`：扩展 VALID_CRITIQUE_CATEGORIES 从 6 值增至 10 值（新增 pacing/coherence/foreshadowing/characters），对齐 phase_outline_audit 的 10 维度
- `phase_verify.txt` 重写：
  - 维度从 6 个扩展为 10 个（沿用 phase_outline_audit.txt 的维度定义）
  - 7 处反复强调 protagonist_consistency/worldview_consistency 为"必审项"
  - summary 字段强制要求含 `【主角一致性审计】`/`【世界观一致性审计】` 两个固定标记段落
  - worldview_consistency 维度检查项从 2 条扩展为 8 条，对齐 WorldOntology 7 大维度
  - 沿用 outline_audit 的"一票否决"（protagonist_consistency）与"严格给分"（worldview_consistency）规则

### 设计决策

- 扩展 VALID_CRITIQUE_CATEGORIES：用户确认允许模型层改动以支持新增维度的 issues category 值
- 不在 issues 中输出"已检查无问题"记录：避免干扰下游 _run_chapter_revise 修订逻辑，审计结论通过 summary 固定标记段落承载
- 保留"档案缺失则跳过审查"降级路径：但要求 summary 明确注明"档案缺失，无法审计"

### 测试

- 回归测试：`377 passed, 13 skipped, 12 deselected`（无新增失败，无测试直接断言 VALID_CRITIQUE_CATEGORIES 具体值）

### 文档同步

- agent.md：更新 agent.py + phase_verify.txt 描述

---

## 2026-06-29：世界观底层 + 主角形象注入全部生成环节

### 背景

用户要求将本次新增的 `world_ontology`（世界观底层）和 `protagonist_profile`（主角形象）注入到单章生成和多章节生成的**各个环节**。前序会话已通过 MacroContext.extra + PromptAssembler.assemble 加参打通单章与卷级 chapter_writing 共享链路，但卷级 5 个阶段模板文件仍缺 `{{world_ontology}}`/`{{protagonist_profile}}` 占位符（macros dict 已补但模板未补，会导致 `{{...}}` 残留）。

### 核心改动

- 5 个阶段模板补全占位符：`phase_deep_analysis.txt`(+protagonist_profile)、`phase_deep_analysis_merge.txt`(+两个)、`phase_outline_final.txt`(+两个)、`phase_chapter_outline.txt`(+world_ontology)、`phase_revise.txt`(+world_ontology)
- 2 个测试 macros dict 适配：`test_macro_replacement_deep_analysis`/`test_macro_replacement_chapter_outline`

### 前序已完成（本日早些时候）

- `prompt_assembler.py`：`assemble` 加 `world_ontology`/`protagonist_profile` 两参数，`_build_macro_context` 序列化为 JSON 填入 `MacroContext.extra`
- `main_window.py`：单章 `_on_start_continuation` 的 assemble 调用传 `project.world_ontology` + `_protagonist_profile_by_chapter.get(chapter.id)`
- `volume_orchestrator.py`：8 个 phase 方法 macros dict 全部补两个宏 + chapter_writing assemble 调用传 `self.world_ontology`/`self.protagonist_profile`

### 测试

- `tests/test_volume_prompts.py`：2 个 macro replacement 测试 macros dict 适配
- 回归测试：`377 passed, 13 skipped, 12 deselected`（无新增失败）

### 文档同步

- agent.md：更新 `core/prompt_assembler.py`/`services/volume_orchestrator.py`/`ui/main_window.py` + 5 个模板文件描述

---

## 2026-06-29：章节列表自动恢复（从磁盘重建 DB 行）

### 背景

用户反馈"还是不行"——章节列表仍为空。深入调查确认：UPSERT 修复已正确生效（不会再级联删除），但用户 DB 中的 chapters 行在修复前就已被旧的 `INSERT OR REPLACE` 级联删除。SQLite 的 `ON DELETE CASCADE` 只删 DB 行不删磁盘文件，章节正文 `.txt` 文件仍在 `~/.novelforge/projects/{project_id}/chapters/` 目录下。

### 核心修复

- [novelforge/core/storage.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/core/storage.py)：
  - 新增 `rebuild_chapters_from_disk(project_id)` 方法：扫描 chapters 目录的 `.txt` 文件，为每个文件重建 DB 行（id=文件名、index=按文件名排序、title=正文首行、word_count=内容长度、metadata={}），已存在的 DB 行不覆盖，用 UPSERT 兜底并发冲突
  - `list_chapters` 增加自动检测恢复：返回空列表时检测磁盘是否有 `.txt` 文件，有则自动调 `rebuild_chapters_from_disk` 重建后重新查询返回
- [novelforge/services/storage_service.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/services/storage_service.py)：新增 `rebuild_chapters_from_disk` 同步包装方法，供 UI 手动调用

### 恢复机制

用户下次加载项目时，`list_chapters` 自动检测 DB 无章节但磁盘有 `.txt` 文件，自动从磁盘重建 DB 行，章节列表恢复正常。无需手动操作。`continuations` 续写历史已永久丢失，无法恢复。

### 测试

- 恢复场景验证：建项目+章节 → 手动 DELETE FROM chapters（模拟级联删除）→ list_chapters 自动恢复返回 1 章，title/index 正确
- 幂等性验证：rebuild_chapters_from_disk 对已存在 DB 行返回 0（不覆盖）
- 回归测试：`377 passed, 13 skipped, 12 deselected`（无新增失败）

### 文档同步

- agent.md：更新 `core/storage.py` 描述（list_chapters 自动恢复 + rebuild_chapters_from_disk）

---

## 2026-06-29：修复 save_project/save_chapter 级联删除章节/续写 bug

### 背景

用户反馈：提取世界观底层后左侧章节列表为空。**根因**：`storage.py` 的 `save_project` 使用 `INSERT OR REPLACE INTO projects`，配合 `PRAGMA foreign_keys=ON` 和 chapters 表的 `ON DELETE CASCADE` 外键，导致每次 `save_project` 时 SQLite 先 DELETE 旧 projects 行（触发级联删除 chapters 表所有匹配行），再 INSERT 新行。结果：projects 更新成功但 chapters 表被清空。同样的 bug 也存在于 `save_chapter`（清空 continuations 续写历史）。

### 核心修复

- [novelforge/core/storage.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/core/storage.py)：
  - `save_project`（L428）：`INSERT OR REPLACE INTO projects` 改为 `INSERT INTO projects ... ON CONFLICT(id) DO UPDATE SET ...`（UPSERT），主键冲突时执行 UPDATE 而非 DELETE+INSERT，不触发 `ON DELETE CASCADE`，章节元数据保留
  - `save_chapter`（L562）：同样改为 UPSERT，避免清空 continuations 续写历史
  - 不修改外键级联设计（`delete_project`/`delete_chapter` 仍需级联语义），仅改 save 的 SQL

### 测试

- 关键场景验证：save_project 后 list_chapters 仍返回 1 章（修复前为 0 章）；save_chapter 后 list_continuations 仍返回 1 条（修复前为 0 条）
- 回归测试：`377 passed, 13 skipped, 12 deselected`（无新增失败）

### 文档同步

- agent.md：更新 `core/storage.py` 描述（save_project/save_chapter 用 UPSERT 避免级联删除）

---

## 2026-06-29：章节列表为空修复 + 查看世界观底层入口 + world_ontology 类型还原

### 背景

用户反馈：①上轮修复后导入项目，左侧章节列表看不到章节；②需要一个入口查看已提取的世界观底层信息。调研还发现第三个隐藏问题：`project.world_ontology` 从 DB 加载后是 dict 而非 WorldOntology 实例，导致 `VolumeOrchestrator._format_world_ontology` 调 `.model_dump()` 时 AttributeError，世界观无法注入续写提示词。

### 核心修复

#### 1. `_row_to_project` 加 `len(row)` 防御

- [novelforge/core/storage.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/core/storage.py)：`_row_to_project` 读 `row[10]`/`row[11]` 时加 `len(row) > N` 防御（对齐 `_row_to_continuation` 模式），防止旧库 row 不足 12 列时 IndexError

#### 2. `_load_project` 解耦章节加载

- [novelforge/ui/main_window.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/ui/main_window.py)：`_load_project` 将 `load_project` 包裹 try/except，项目元数据加载失败时仍尝试 `list_chapters` 加载章节表（章节表独立，不受 projects 表影响），避免章节列表为空且无错误提示

#### 3. `load_project` 还原 WorldOntology 实例

- [novelforge/services/storage_service.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/services/storage_service.py)：`load_project` 检测 `world_ontology` 为 dict 时用 `WorldOntology.model_validate(wo)` 还原为实例，失败保留 dict。修复下游 `VolumeOrchestrator._format_world_ontology` 的 AttributeError

#### 4. 新增"查看世界观底层"入口

- [novelforge/ui/context_preview_panel.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/ui/context_preview_panel.py)：新增 `view_ontology_requested` 信号 + "查看世界观底层"按钮（位于"提取世界观底层"按钮旁）+ `_on_view_ontology_clicked` 处理器
- [novelforge/ui/main_window.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/ui/main_window.py)：新增 `_on_view_ontology_requested` 处理器——加载 `project.world_ontology`，弹 QDialog 按 7 维度分节展示（标题 + JSON 内容），兼容 dict/WorldOntology 实例；`_format_ontology_for_display` 静态方法格式化（存在拓扑/因果架构/时空本体论/信息与认识论/价值论基础/生成动力学/叙事本体论 + extracted_at + source_chapter_range）

### 测试

- 导入验证：`StorageService`/`ContextPreviewPanel`/`Storage` 均正常导入
- 还原验证：`WorldOntology.model_dump(mode='json')` → dict → `model_validate` 还原成功
- 回归测试：`377 passed, 13 skipped, 12 deselected`（无新增失败）

### 文档同步

- agent.md：更新 `core/storage.py`（_row_to_project len(row) 防御）、`services/storage_service.py`（load_project 还原 WorldOntology）、`ui/main_window.py`（_load_project 解耦 + _on_view_ontology_requested + _format_ontology_for_display）、`ui/context_preview_panel.py`（查看按钮 + 信号）描述

---

## 2026-06-29：世界观提取流式进度窗口 + 保存死锁修复 + DB schema 补全

### 背景

用户反馈：①提取世界观底层没有流式进度窗口（chunk 被丢弃，仅状态栏文本）；②保存时报 `TimeoutError`——`extract_ontology_streaming` 在后台事件循环内调用同步 `storage_service.save_project()`，向同一循环提交协程并阻塞等待，形成重入死锁，30 秒后超时。调研还发现第三个隐藏问题：`projects` 表无 `world_ontology`/`worldbook_id` 列，即使修复死锁，重启后数据丢失。

### 核心修复

#### 1. 流式进度窗口（复用 _stream_view）

- [novelforge/ui/context_preview_panel.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/ui/context_preview_panel.py)：新增 5 个 ontology 流式方法（`start_ontology_extraction`/`update_ontology_progress`/`update_ontology_batch`/`finish_ontology_extraction`/`fail_ontology_extraction`），复用已有的 `_stream_view`（QPlainTextEdit）+ `_stream_group`（QGroupBox）展示世界观提取流式输出，镜像上下文提取的 `start_extraction`/`update_extraction_progress`/`finish_extraction`/`fail_extraction` 模式
- [novelforge/ui/main_window.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/ui/main_window.py)：4 个 ontology 处理方法改为调用面板流式方法——`_on_extract_ontology_requested` 调 `start_ontology_extraction`、`_on_ontology_chunk_received` 调 `update_ontology_progress`（原 `pass` 丢弃 chunk）、`_on_ontology_batch_done` 调 `update_ontology_batch`、`_on_ontology_done` 调 `finish_ontology_extraction`/`fail_ontology_extraction`

#### 2. 修复重入死锁（协程内 await 异步存储直连）

- [novelforge/services/ontology_extractor.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/services/ontology_extractor.py) L933：将同步 `self.storage_service.save_project(project)` 替换为 `await self.storage_service.storage.save_project(project.model_dump(mode="json"))`，镜像 ContextExtractor 的 `await self.storage_service.storage.set_cache(...)` 模式，绕过同步包装层直接 await 异步存储层，不阻塞事件循环
- [novelforge/ui/main_window.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/ui/main_window.py) `_on_ontology_done`：移除 UI 线程的重复 `load_project`/`save_project`（死锁补丁，协程内已保存成功），改为根据 status 判断：含"保存失败"则 QMessageBox.warning 警告，否则状态栏提示完成

#### 3. 补全 projects 表 schema（world_ontology + worldbook_id 列）

- [novelforge/core/storage.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/core/storage.py)：
  - `SCHEMA_SQL` 的 `CREATE TABLE projects` 新增 `world_ontology TEXT` + `worldbook_id TEXT DEFAULT ''` 两列
  - 新增 `_migrate_projects_columns` 幂等迁移方法（镜像 `_migrate_continuations_columns`），`connect()` 中调用
  - `save_project` INSERT OR REPLACE 扩展为 12 列含 `world_ontology`/`worldbook_id`
  - `_row_to_project` 读取 row[10]/row[11] 返回两字段

### 测试

- 导入验证：`OntologyExtractor`/`ContextPreviewPanel`/`Storage` 均正常导入
- DB 迁移验证：新建库 `PRAGMA table_info(projects)` 含两列 + save/load 往返 `world_ontology={'a':1}`/`worldbook_id='wb_1'` 正确
- 回归测试：`377 passed, 13 skipped, 12 deselected`（无新增失败）

### 文档同步

- agent.md：更新 `core/storage.py`（SQLite 异步存储层 + _migrate_projects_columns）、`services/ontology_extractor.py`（协程内 await 直连避免死锁）、`ui/main_window.py`（4 个 ontology 处理方法改为面板流式调用 + 移除重复 save_project）、`ui/context_preview_panel.py`（5 个 ontology 流式方法）描述

---

## 2026-06-29：上下文提取改造（世界观底层 + 主角形象一致性）

### 背景

重新优化上下文提取：世界观底层全文拆分分析提取 7 维度 WorldOntology 固化到项目并绑定世界书；主角形象 8 维度 ProtagonistProfile 跟随章节缓存独立提取；审计阶段主角一致性一票否决；其它信息（剧情/物品/伏笔等）剔除世界观底层内容但包含主角既有行为。用户要求两项新功能必须具备按 tokens 自动拆分分段提取、增量更新及合并功能；主角形象提取仅反映至当前章节状态。

### 核心改造

#### 1. 世界观底层提取（OntologyExtractor）

- 新增 [novelforge/services/ontology_extractor.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/services/ontology_extractor.py)：全文拆分分析提取 `WorldOntology` 7 大维度（existential_topology/causal_architecture/spatio_temporal_ontology/information_epistemology/axiological_foundation/becoming_dynamics/narrative_ontology）
- **三大机制**：
  - token 拆分：`_split_chapters_by_token_limit` 按章节边界贪婪累积 token 拆批
  - 增量更新：每批携带 `{{accumulated_ontology}}` 占位符，`_merge_ontology_fields` 程序化字段级合并（空字段取另侧/双侧非空按序列化长度启发式/冲突新批次优先）
  - 合并：`batch_count > 1` 触发 `_run_ontology_merge`（加载 `extract_ontology_merge_prompt.txt`，2 次重试温度 0.2/0.0，失败降级 accumulated_ontology）
- 固化到 `Project.world_ontology`（全文提取一次，不随章节变化）
- `_save_ontology_to_worldbook` 拆 7 维度为 `ContextEntry`（category="plot_state"）绑定 `project.worldbook_id`
- 后续所有流程（深度分析/卷大纲/审计/验证/修订）引用 `world_ontology` 作为底层规则约束

#### 2. 主角形象一致性（ProtagonistProfile）

- 新增 [novelforge/models/protagonist.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/models/protagonist.py)：8 维度心理学档案（basic_anchors/motivation_system/personality_structure/cognitive_style/defense_mechanisms/behavioral_fingerprint/relationship_coordinates/growth_arc）+ OOC 红线
- [novelforge/services/context_extractor.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/services/context_extractor.py) 新增 `_extract_protagonist`：与 8 维度共用 `batches`（继承 token 拆分），三大机制完整支持
- **growth_arc 特殊处理**：合并时新批次直接覆盖旧值（反映弧光演变），不按长度启发式
- 跟随章节缓存：与 `ContextEntry` 共享 SQLite 缓存键 `ctx_extract:{project_id}:{chapter_id}`，仅反映至当前章节状态
- 失败不阻塞 8 维度结果（异常被捕获返回 `(None, batch_count, False)`）
- **一票否决机制**：大纲审计 `protagonist_consistency` 维度 score ≤ 4 → 整体 `passed=false`；章节验证 critical 级主角一致性问题 → `passed=false`

#### 3. 其它信息优化

- `VALID_CATEGORIES` 新增 `protagonist_behavior`（主角既有行为：已做决策/已展能力/已发行为模式/已建关系动态）
- `extract_prompt.txt` 剔除世界观底层内容（由 OntologyExtractor 独立负责），新增第 9 维度 `protagonist_behavior`
- 提取提示词边界声明：剔除主角心理档案（由 ProtagonistProfile 独立流程提取）

#### 4. VolumeOrchestrator 注入

- `__init__` 新增 `world_ontology` + `protagonist_profile` 两参数
- 新增 `_format_world_ontology`/`_format_protagonist_profile` 辅助方法（格式化为 JSON 注入）
- 6 个 phase 方法 macros 注入占位符：
  - `_run_deep_analysis_single`：仅 `{{world_ontology}}`
  - `_run_volume_outline`/`_run_outline_audit`/`_run_chapter_verify`：两者
  - `_run_chapter_outline`/`_run_chapter_revise`：仅 `{{protagonist_profile}}`
- 4 个提示词模板新增占位符段落（phase_deep_analysis/volume_outline/chapter_outline/revise）

#### 5. UI 适配

- [novelforge/ui/context_preview_panel.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/ui/context_preview_panel.py)：新增"提取世界观底层"按钮（`extract_ontology_requested` 信号）+ `protagonist_behavior` 维度展示（CATEGORY_DISPLAY_NAMES/CATEGORY_ORDER）
- [novelforge/ui/volume_panel.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/ui/volume_panel.py)：`_AUDIT_DIMENSION_LABELS` 新增 `protagonist_consistency`/`worldview_consistency`（复选框网格自动迭代 `DEFAULT_AUDIT_DIMENSIONS`）
- [novelforge/ui/main_window.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/ui/main_window.py)：OntologyExtractor 实例 + `_protagonist_profile_by_chapter` 内存 LRU 缓存 + 4 个 ontology 信号处理方法 + VolumeOrchestrator 传参

#### 6. 审计维度扩展

- `DEFAULT_AUDIT_DIMENSIONS`：8 → 10（+protagonist_consistency +worldview_consistency）
- `VALID_CRITIQUE_CATEGORIES`：4 → 6（+protagonist_consistency +worldview_consistency）
- `phase_outline_audit.txt`：主角一致性一票否决（score ≤ 4）+ 世界观一致性严格给分（违反底层世界观元规则时 score ≤ 3）
- `phase_verify.txt`：critical 级别主角一致性问题 → passed=false

### 测试补全

- 新增 [tests/test_protagonist_extraction.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/tests/test_protagonist_extraction.py)：16 用例 9 测试类，覆盖 `_filter_protagonist_dimensions`/`_parse_protagonist_response`/`_merge_protagonist_fields`(growth_arc覆盖+长度启发式)/`ExtractResult`/`PROTAGONIST_DIMENSIONS`/`ProtagonistProfile`模型/`_safe_serialize_dim`
- 新增 [tests/test_ontology_extractor.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/tests/test_ontology_extractor.py)：8 用例，覆盖 `_split_chapters_by_token_limit`/`_build_ontology_prompt`/`_merge_ontology_fields`/`_save_ontology_to_worldbook`/`extract_ontology_streaming`(单批+多批合并)
- 修改 [tests/test_m4_context_extraction.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/tests/test_m4_context_extraction.py)：新增 2 个 `ExtractResult` 主角字段测试 + 4 处 LLM 调用计数断言更新（主角提取增加 1 次调用）

### 文档同步

- [agent.md](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/agent.md) 5 处更新：volume_orchestrator/main_window/volume_panel/context_preview_panel 说明行 + 第 3 节主角形象提取说明
- [update.md](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/update.md) 新增本次条目

### 涉及文件清单

**生产代码（新增 3 + 修改 9）**：
- 新增：`novelforge/models/ontology.py`、`novelforge/models/protagonist.py`、`novelforge/services/ontology_extractor.py`
- 修改：`novelforge/models/project.py`、`novelforge/models/context.py`、`novelforge/models/volume.py`、`novelforge/models/agent.py`、`novelforge/services/context_extractor.py`、`novelforge/services/volume_orchestrator.py`、`novelforge/ui/main_window.py`、`novelforge/ui/context_preview_panel.py`、`novelforge/ui/volume_panel.py`
- 资源：`extract_prompt.txt`、`extract_ontology_prompt.txt`、`extract_ontology_merge_prompt.txt`、`extract_protagonist_prompt.txt`、`extract_protagonist_merge_prompt.txt`、`phase_deep_analysis.txt`、`phase_volume_outline.txt`、`phase_chapter_outline.txt`、`phase_revise.txt`、`phase_outline_audit.txt`、`phase_verify.txt`

**测试代码（新增 2 + 修改 1）**：
- 新增：`tests/test_protagonist_extraction.py`、`tests/test_ontology_extractor.py`
- 修改：`tests/test_m4_context_extraction.py`

**文档（2）**：
- `agent.md`、`update.md`

---

## 2026-06-29：卷级续写三项增强 + agent.md 同步 + 全量回归验证

### 背景

针对卷级多章节续写系统的三项增强需求完成实现、测试补全、文档同步与回归验证。

### 三项增强（生产代码已落地）

#### 1. 强制修改流程

- **位置**：[novelforge/services/volume_orchestrator.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/services/volume_orchestrator.py#L504-L541)
- **逻辑**：`enable_chapter_revise=True` 时每章生成后强制1轮修改
  - 流程：生成 → 审计① → 强制修改(rounds=1) → 审计② → 自动修订循环(至 `max_revise_rounds_per_chapter` 上限) → `after_chapter` 用户决策
  - while 条件 `rounds==0 or (critique not passed)` 保证审计①即使通过也强制修改1次
- `enable_chapter_revise=False` 时无强制修改与自动修订

#### 2. 阶段产物阅览

- **数据模型**：[novelforge/models/volume.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/models/volume.py#L231-L276)
  - 新增 `ChapterStageArtifact` 模型：`stage_type`∈outline/draft/audit/revise + `round_index` + `content`/`critique`/`guidance`/`outline` 四个可选产物字段
  - `ChapterArtifacts` 新增 `stages: list[ChapterStageArtifact]` 字段（默认空列表，向后兼容）
- **编排器捕获**：[volume_orchestrator.py L459-641](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/services/volume_orchestrator.py#L459-L641)
  - 每步 `stages.append(ChapterStageArtifact(...))`，`ChapterArtifacts` 末尾传入 `stages=stages`
- **UI 展示重构**：[novelforge/ui/volume_panel.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/ui/volume_panel.py#L1252-L1393)
  - `add_chapter_artifacts`：stages 非空时按次序每阶段一行（QLabel + "查看完整内容"按钮），空时回退 `_build_legacy_summary`
  - `_format_stage_label`：outline→细纲 / draft→初稿 / audit→审计①②③ / revise→修改正文①②③
  - `_show_stage_detail`：弹 `ArtifactDetailDialog.exec()`
- **新增对话框**：[novelforge/ui/artifact_detail_dialog.py](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/ui/artifact_detail_dialog.py)
  - 只读 `QPlainTextEdit` + 关闭按钮
  - `_format_content()` 按 `stage_type` 格式化：outline→`format_outline` / audit→`format_critique` / revise→"【修订指导】"+"【修改后正文】" / draft→原文

#### 3. 动态前文修复

- **问题**：从中间续写时动态前文窗口误把全文末尾章节当作前文
- **修复**：[volume_orchestrator.py L1731-1759](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/novelforge/services/volume_orchestrator.py#L1731-L1759)
  - `__init__` 记录 `_original_chapter_count = len(self.chapters)`（L180）
  - `_get_effective_chapters()` 返回 `chapters[0:_current_chapter_index+1] + chapters[_original_chapter_count:]`，跳过插入点后原章节
  - `_build_dynamic_lookback_text(window=10)` 基于有效章节序列取末尾 window 章拼接
  - `_run_chapter_writing` 的 `prompt_assembler.assemble` 也使用 `effective_chapters`（L1396-1399）
- **效果**：生成第 11 章时前文只含刚生成的 10 章，不再混入插入点之后原章节

### 测试补全（已完成）

| 测试文件 | 用例数 | 新增/修改 |
|---------|-------|----------|
| `tests/test_volume_e2e.py` | 3 | 修改：A1 加 `enable_chapter_revise=False` 修复降级测试 / A2 加章节2-3强制修改 mock 响应 + 断言 `revision_rounds==1` |
| `tests/test_volume_models.py` | 32 | +4：`ChapterStageArtifact` 模型（默认值/构造/stages 默认空/stages roundtrip） |
| `tests/test_volume_orchestrator.py` | 36 | +4：`test_forced_revise_flow_stages_captured` / `test_forced_revise_max_2_auto_loop` / `test_dynamic_lookback_excludes_post_insertion_chapters` / `test_stages_capture_after_chapter_reject` |
| `tests/test_volume_ui.py` | 23 | +2：`TestAddChapterArtifactsStages`（`test_add_chapter_artifacts_with_stages` / `test_add_chapter_artifacts_legacy_fallback`） |
| `tests/test_artifact_detail_dialog.py` | 5 | 新文件：覆盖 outline/audit/revise/draft 四类 stage 格式化 + 关闭按钮 |

### 文档同步

- [agent.md](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/feat-context-extraction-merge-DRARZ8/agent.md) 第 8 节"卷级多章节续写"5 处更新：
  - D1 L171：数据模型段补充 `ChapterStageArtifact` + `stages` 字段
  - D2 L189：`_async_run` 流程段补充强制修改流程
  - D3 L194：动态前文窗口段重写为 `_get_effective_chapters` 实现
  - D4 L212：产物查看段补充 `add_chapter_artifacts` 重构
  - D5 L200/L203-205：测试计数更新（orchestrator 30→36 / ui +2 / 新增 dialog 5 用例 / 新增 models 4 stage 用例）
- 目录树 L61：新增 `artifact_detail_dialog.py` 注释

### 回归验证

```powershell
python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"
```

结果：**351 passed, 13 skipped, 12 deselected in 5.54s** — 零失败、零回归。

### 涉及文件清单

**生产代码（4 文件）**：
- `novelforge/models/volume.py`
- `novelforge/services/volume_orchestrator.py`
- `novelforge/ui/volume_panel.py`
- `novelforge/ui/artifact_detail_dialog.py`（新增）

**测试代码（5 文件）**：
- `tests/test_volume_e2e.py`
- `tests/test_volume_models.py`
- `tests/test_volume_orchestrator.py`
- `tests/test_volume_ui.py`
- `tests/test_artifact_detail_dialog.py`（新增）

**文档（2 文件）**：
- `agent.md`
- `update.md`（新增）
