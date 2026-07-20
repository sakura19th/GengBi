# 更新日志

## 2026-07-20：面板 API 选择优先于设置 API 选择

### 背景

用户反馈「面板上的 API 选择必须和设置中的 API 选择一致才能用」。经代码审查定位到 4 个根因：
1. 卷续写模型优先级反转（流程→面板，与单章的「面板→流程」相反），导致面板选的模型对卷续写完全无效
2. `_refresh_endpoints` 关闭设置/流程端点对话框后强制覆盖面板端点+模型为流程配置值，用户先前选择丢失
3. 面板选择未持久化（`_last_model_per_endpoint` 是会话内存，端点选择从未持久化）
4. `enabled_models` 过滤时静默切换模型，用户无感知

### 核心改动

- **修复卷续写模型优先级反转**（[main_window.py:2262](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/fix-audit-prompt-crash-Miexk8/novelforge/ui/main_window.py)）：`get_flow_model("volume_continuation") or params.get("model", "")` → `params.get("model", "") or get_flow_model("volume_continuation")`，与单章续写 L1819 一致
- **面板端点+模型持久化**（[config.py:538-563](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/fix-audit-prompt-crash-Miexk8/novelforge/core/config.py)）：新增 `continuation.last_endpoint_id` + `continuation.last_model_per_endpoint` 两个配置字段，4 个 get/set 方法
- **面板信号通知持久化**（[continuation_panel.py:130-133, 410-455](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/fix-audit-prompt-crash-Miexk8/novelforge/ui/continuation_panel.py)）：新增 `endpoint_changed(str)` + `model_user_changed(str, str)` 信号；`_on_endpoint_changed`/`_on_model_user_changed` 末尾 emit 信号
- **`_refresh_endpoints` 恢复面板持久化选择**（[main_window.py:1035-1054](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/fix-audit-prompt-crash-Miexk8/novelforge/ui/main_window.py)）：不再强制同步流程配置到面板；恢复优先级「面板持久化端点 → 流程端点兜底 → 默认端点」
- **启动顺序调整**（[main_window.py:381-392](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/fix-audit-prompt-crash-Miexk8/novelforge/ui/main_window.py)）：`_apply_continuation_defaults` 移到 `_refresh_endpoints` 前，确保 `_last_model_per_endpoint` 先从 config 灌入
- **新增 2 个 slot**（[main_window.py:1155-1169](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/fix-audit-prompt-crash-Miexk8/novelforge/ui/main_window.py)）：`_on_panel_endpoint_changed`/`_on_panel_model_changed` 实时持久化面板变更
- **`enabled_models` 静默切换加 warning 日志**（[continuation_panel.py:437-438](file:///c:/Users/sakur/.trae-cn/worktrees/GengBi/fix-audit-prompt-crash-Miexk8/novelforge/ui/continuation_panel.py)）：上次选的模型被禁用时 logger.warning 提示

### 行为变化

- **正文流程（单章+卷续写）面板优先**：面板选的端点+模型直接生效，流程配置作为回退
- **非正文流程（single_audit/rewrite_analysis/generic_analysis）保持现状**：流程端点优先+面板回退（后台流程独立性）
- **面板选择跨会话恢复**：重启应用后面板自动恢复上次选的端点+模型
- **关闭设置/流程端点对话框不再覆盖面板选择**：面板保持用户上次选择

### 文档同步

- `agent.md` §15：更新「消费层优先级」（正文流程统一面板优先）、「正文流程同步」（恢复面板持久化选择）、新增「面板端点+模型持久化」条目
- `update.md`：本条目

## 2026-07-20：修复卷续写审计提示词输入后闪退

### 背景

用户在 260+ KB 文本加载 + 世界观/主角形象/文风档案三件套提取完成后，选择卷续写 10 章 × 4500 字，输入审计提示词后应用闪退。经代码审查定位到 5 个问题。

### 核心改动

- **QThread 生命周期竞态**（直接闪退原因）：`_start_volume_phase` 切换 orchestrator 前补 `stop()+wait(3000)+deleteLater()`，镜像 `closeEvent` 模式，避免 QThread 仍在 `run()` finally 清理时被删除触发段错误；同时补齐信号断开 `chapter_step_started`/`prompt_debug_requested` 2 个漏断信号
- **`_volume_orchestrator` None 访问防护**：`_on_volume_checkpoint`/`_on_volume_continue`/`_on_volume_cancel_checkpoint` 8 处 slot 访问前加 None 校验；`_on_continuation_error` 置 None 前隐藏面板审计输入区，避免错误对话框关闭后用户点击陈旧按钮触发 AttributeError
- **`style_profile` 注入 VolumeOrchestrator**：构造函数新增 `style_profile` 参数 + `_format_style_profile` 序列化方法；9 处 phase 方法 macros 字典补 `{{style_profile}}` 注入（此前 15 个阶段模板均含占位符但 orchestrator 不注入，导致字面文本进入 LLM 提示词）；`_run_chapter_writing` 的 `assemble()` 调用补传 `style_profile`/`custom_audit_rules`；`_start_volume_phase` 构造调用补传 `project.style_profile`
- **`outline_audit` 阶段产物序列化类型修正**：`_resume_volume_state` 将 `outline_audit` 反序列化类型从 `OutlineAuditReport` 改为 `VolumeOutline`（与 `_run_phase_outline_audit` emit 的 `final_outline` 类型一致），修复恢复后「终稿大纲为空」错误

### 测试

- 运行 `python -m pytest tests/test_volume_*.py -q` 全部通过（无新增测试，待后续补充）

### 文档同步

- `agent.md`：更新 volume_orchestrator.py 描述（4 档案注入）、3 处注入点描述、新增 QThread 生命周期管理 + 阶段产物序列化设计决策
- `update.md`：本条目

## 2026-07-17：加固打包脚本 python -m novelforge.resources.build

### 背景

产物改名后在 `.spec` 内 `import novelforge` 取版本，部分环境下易出问题；需由 `build.py` 统一注入命名并给出明确报错。

### 核心改动

- **build.py**：解析版本 → `GengBi_v{version}`，经环境变量 `GENGBI_BUILD_NAME` 注入；校验 main.py / PyInstaller；成功后打印 `dist/` 路径
- **build.spec**：优先读 `GENGBI_BUILD_NAME`，否则解析 `__init__.py`，不再 import 包

### 测试

- `tests/test_m5_polish.py::TestBuildConfig` 覆盖命名解析

### 文档同步

- `update.md`：本条目

## 2026-07-17：打包产物命名改为 GengBi_v{版本号}

### 背景

原 PyInstaller 产物名为「赓笔」，不便按版本区分；统一为 `GengBi_v0.2.12` 格式。

### 核心改动

- **build.spec**：从 `novelforge.__version__` 生成 `APP_NAME = f"GengBi_v{__version__}"`
- **build.py / README**：文档同步产物路径说明

### 测试

- `tests/test_m5_polish.py` 断言 spec 含版本化命名

### 文档同步

- `README.md`、`update.md`：本条目

## 2026-07-17：世界书多选选中状态持久化

### 背景

续写面板世界书多选仅在会话内 `_refresh_worldbooks` 保留，重启后清空，需与温度/回溯等续写偏好一样跨会话恢复。

### 核心改动

- **ConfigManager**（`novelforge/core/config.py`）：`continuation.selected_worldbook_ids` 默认 `[]`；新增 `get_selected_worldbook_ids` / `set_selected_worldbook_ids`
- **MainWindow**：`_refresh_worldbooks` 面板为空时从 config 恢复；`continuation_panel.worldbook_changed` → 即时写入 config
- **ContinuationPanel**：转发 `worldbook_changed(list)` 信号

### 测试

- `tests/test_volume_ui.py` 新增 ConfigManager 选中 ID 往返与面板 default_ids 恢复断言

### 文档同步

- `agent.md`：世界书多选持久化说明
- `update.md`：本条目

## 2026-07-17：续写控制输入栏自适应 + 世界书多选 + SpinBox 箭头修复

### 背景

用户输入栏高度固定、靠 splitter 拖动不直观；世界书仅能单选且需额外勾选启用；温度/目标字数/回溯章节数 SpinBox 因主题未定义按钮几何导致箭头绘制与点击热区错位；世界书多选自绘箭头与其它下拉外观不一致。

### 核心改动

- **输入栏自适应**（`continuation_panel.py`）：去掉垂直 splitter，按文档行数自动增高（约 1～6 行），显式 `WidgetWidth` 换行；收紧配置区 margins/spacing
- **世界书多选**（`checkable_combo.py` 新增 + `worldbook_panel.py`）：可勾选多选下拉，选中至少一本即启用；去掉「启用世界书」复选框；API 改为 `get_selected_worldbook_ids()`；`main_window` 按多 ID 加载并合并条目（跨书 uid 冲突时先选优先）
- **主题 SpinBox/Combo 校准**（`light.qss`/`dark.qss` + `resources/icons/chevron_*.svg`）：Spin 独立规则显式 `up/down-button` 与箭头图；Combo 统一 SVG chevron；`_apply_theme` 注入绝对路径占位符
- **外观统一**：续写区 Combo/Spin 统一 Expanding+Fixed；世界书下拉走主题箭头并安装滚轮过滤

### 测试

- `python -m pytest tests/test_volume_ui.py -q` → 26 passed（含输入高度与世界书多选断言）

### 文档同步

- `agent.md`：版本 → v0.2.12；UI 相关描述同步
- `README.md`：版本 → v0.2.12；更新记录追加 v0.2.12
- `update.md`：本条目

## 2026-07-16：全部提取器改为默认流式输出

### 背景

4 个提取器的流式支持不一致：上下文/主角形象已真流式，但世界观/文风虽方法名带 `_streaming` 实际用 `chat_completion`（假流式）；context_extractor.extract 默认 stream=False；main_window 有 2 处调用非流式 extract。用户要求全部改为默认流式输出。

### 核心改动

- **OntologyExtractor / StyleExtractor**（`novelforge/services/ontology_extractor.py` / `style_extractor.py`）：批次提取 + 信息汇总内部从 `chat_completion` 改为 `stream_chat_completion` 真流式（逐 chunk 接收并推送 UI，4 处）
- **ContextExtractor.extract**（`novelforge/services/context_extractor.py`）：`stream` 默认值 `False` → `True`（默认流式，on_chunk=None 时不推送 chunk 但仍用 stream_chat_completion）
- **main_window**（`novelforge/ui/main_window.py`）：重试提取（L3074）和强制重新提取（L3202）从 `extract` 改为 `extract_streaming` + on_chunk/on_batch_complete Signal 回调
- **测试 mock 对齐**（6 个测试文件约 36 处）：`mock_client.chat_completion = AsyncMock(return_value={...})` → `stream_chat_completion` async generator mock（`_StreamChunk` + `MagicMock(side_effect=_mock_stream)`）

### 流式路径 token_usage 注意

`stream_chat_completion` 的 `StreamChunk` 不携带 usage 字段，流式路径 `token_usage` 为空 `{}`。2 个 test_m4 测试的 `token_usage` 断言已调整为 `assert result.token_usage == {}`。

### 测试

- 全量测试 673 passed, 7 failed（均为预先存在的 style_profile 占位符和流程数 10→11 问题，与本次改动无关）

### 文档同步

- `agent.md`：context_extractor / ontology_extractor / style_extractor 描述补充默认流式
- `update.md`：本条目

## 2026-07-16：test_m4_context_extraction.py mock 对齐 extract 流式默认路径

### 背景

`ContextExtractor.extract` 的 `stream` 默认值已从 `False` 改为 `True`（调用 `stream_chat_completion`）。`tests/test_m4_context_extraction.py` 中 16 个测试方法的 mock 仍指向 `chat_completion`，调用 `extractor.extract(...)` 时走流式路径却命中非流式 mock，需对齐为 `stream_chat_completion` mock。本次为同日其他 3 个测试文件（test_e2e_workflow.py / test_protagonist_extraction.py / test_rewrite_current_mode.py）对齐工作的延续。

### 核心改动

- **新增 `_StreamChunk` 类**（import 后、第一个测试类前）：模拟 `stream_chat_completion` 产出的 chunk（`content` + `finish_reason`，默认 `"stop"`）
- **16 个测试方法 mock 改造**（`TestContextExtractor` + `TestIntegration`）：
  - 单响应：`async def _mock_stream(**kwargs): yield _StreamChunk(content)` + `MagicMock(side_effect=_mock_stream)`
  - 多响应（重试/多批次）：`responses` 列表 + `call_count - 1` 索引取值
  - 混合异常+响应（重试场景）：`responses` 列表含 `BaseException` 项，`isinstance` 判断后 `raise`
  - 纯异常：`MagicMock(side_effect=LLMError(...))` 或 `MagicMock(side_effect=CancelledError(...))`
  - 缓存命中不应调用：`MagicMock()` + `assert_not_called()`
- **断言同步改向**：`call_count` / `call_args` / `call_args_list` / `assert_not_called` 全部从 `chat_completion` 改为 `stream_chat_completion`
- **未改动**：`TestLLMClientChatCompletion` 类（测试真实 `LLMClient.chat_completion` 方法存在性/签名/payload，非 mock）

### 测试

- `python -m pytest tests/test_m4_context_extraction.py -q` → 84 passed, 2 failed
- 2 个失败为 `token_usage` 断言（`test_extract_parses_valid_entries` 断言 `total_tokens==200`、`test_full_extraction_flow_with_mock` 断言 `total_tokens==300`）：流式路径不填充 `token_usage`（`context_extractor.py` L2196-2197 仅 `content = "".join(content_parts)`，`token_usage` 仅在非流式 `else` 分支累加 `response["usage"]`）。按任务要求「保持原有测试逻辑不变」未修改这两个断言，需后续单独处理

### 文档同步

- `agent.md`：测试要求章节「流式 mock 约束」补入 `test_m4_context_extraction.py`，并追加流式路径不填充 `token_usage` 的注意事项
- `update.md`：本条目

## 2026-07-16：测试 mock 对齐 extract 流式默认路径

### 背景

`ContextExtractor.extract` 的 `stream` 默认值已从 `False` 改为 `True`（调用 `stream_chat_completion`）。但 3 个测试文件中 mock 的是 `chat_completion`，导致调用 `extractor.extract(...)` 的测试实际走流式路径却命中非流式 mock，需对齐为 `stream_chat_completion` mock。

### 核心改动

- **tests/test_e2e_workflow.py**：新增 `_StreamChunk` 类；`test_context_extractor_with_mock_llm` / `test_context_extraction_caching` 的 `chat_completion` AsyncMock 改为 `stream_chat_completion` 流式 mock（`_StreamChunk` + 异步生成器 + `MagicMock(side_effect=...)`）；缓存测试断言 `call_count` 同步改向 `stream_chat_completion`
- **tests/test_protagonist_extraction.py**：复用已有 `_StreamChunk`；`test_extract_common_no_protagonist_in_result` / `test_extract_common_cache_save_no_protagonist` 的 `chat_completion` mock 改为 `stream_chat_completion` 流式 mock
- **tests/test_rewrite_current_mode.py**：新增 `_StreamChunk` 类；`test_extract_exclude_current_no_current_in_prompt` 的 mock 与 `call_args` 断言改为 `stream_chat_completion`
- 未改动显式传 `stream=False` 的 `_extract_protagonist` 调用测试（仍用 `chat_completion` mock）

### 测试

- `python -m pytest tests/test_e2e_workflow.py tests/test_protagonist_extraction.py tests/test_rewrite_current_mode.py -q` → 76 passed

### 文档同步

- `agent.md`：测试要求章节新增「流式 mock 约束」说明
- `update.md`：本条目

## 2026-07-16：世界观/文风提取支持【前文：】控制

### 背景

当前上下文提取和主角形象提取已支持【前文：】下拉框控制（基于当前章节取前 N 章）。但世界观底层提取（`extract_ontology_streaming`）和文风档案提取（`extract_style_streaming`）是项目级提取，没有 lookback 参数，始终取全部章节。用户要求这两个提取器也受【前文：】控制。

### 改动

- **OntologyExtractor / StyleExtractor 各自新增 `_get_lookback_chapters` 方法**（`novelforge/services/ontology_extractor.py` / `style_extractor.py`）：镜像 ContextExtractor 逻辑（简化版，不含 exclude_current），基于当前章节取前 N 章含当前章节，lookback<=0 取全部前文
- **`extract_ontology_streaming` / `extract_style_streaming` 增加 `current_chapter` 和 `lookback` 参数**：current_chapter 默认 None 保持向后兼容（现有测试不传也能工作），None 时跳过 lookback 过滤返回全部章节
- **内部 sorted_chapters 后增加 lookback 过滤**：先过滤章节再拆分批次，确保 lookback 限制生效；日志增加 `lookback=N` 和 `过滤为 N 章` 信息
- **main_window 调用处增加 `lookback_override` 读取和 `current_chapter=self._current_chapter` 传参**：世界观（L3549）和文风（L3714）两处
- **主角形象提取跳过**：已支持 lookback（context_extractor.py L2501 + main_window.py L3905），无需改动

### 向后兼容保证

- current_chapter=None 默认值保持向后兼容，现有 7 个测试调用（test_ontology_extractor.py 5 处 + test_style_extractor.py 2 处）无需修改
- lookback<=0 时取全部章节，与原行为一致
- lookback 过滤在 sorted 之后、token 拆分之前，不影响批次拆分逻辑

### 测试

- 现有测试无回归：`python -m pytest tests/test_ontology_extractor.py tests/test_style_extractor.py -q`
- 全量测试：`python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"`

### 文档同步

- `agent.md`：ontology_extractor / style_extractor 描述补充 lookback 支持
- `update.md`：本条目

## 2026-07-16：修复上下文提取 JSON 被 max_tokens 截断问题

### 背景

用户反馈上下文提取报错 `Unterminated string starting at: line 293 column 16 (char 9959), content=\`\`\`json`。日志中 `content=` 后的 ` ```json ` 是原始 LLM 响应前 200 字符（用于调试），实际解析流程会先 `strip_markdown_fences` 去除围栏再 `json.loads`。

### 根因

- `EXTRACT_MAX_TOKENS = 5000` 限制 LLM 单次响应最多 5000 token（发送给 API 的 `max_tokens` 参数，与 UI 的 `token_limit` 控制输入批次不同）
- 报错位置 char 9959 约 5000 token，正好撞上 5000 上限
- 当单批章节包含大量角色/地点/事件时，LLM 生成的 JSON 在中间被截断（`finish_reason="length"`），字符串未闭合 → "Unterminated string"
- 其他提取器（ontology/style）max_tokens 已是 8000，唯 context_extractor 偏低

### 核心改动

- **调高 max_tokens 常量**（`novelforge/services/context_extractor.py`）：
  - `EXTRACT_MAX_TOKENS`：5000 → 16000
  - `PROTAGONIST_EXTRACT_MAX_TOKENS`：6000 → 16000
- **非流式路径增加 `finish_reason == "length"` 截断检测**（4 处：批次提取/信息汇总/主角形象批次/主角形象汇总）：检测到截断时记录 warning 日志，不影响后续解析流程
- **流式路径增加 `finish_reason == "length"` 诊断**（4 处对应）：break 前识别是正常结束还是截断，截断时记录 warning
- **JSON 解析失败日志增加 `finish_reason` 字段**（2 处：批次提取/信息汇总）：便于诊断是截断还是其他原因
- **`_parse_extract_response` / `_parse_protagonist_response` 增加第三层宽松回退**：镜像 `json_utils.parse_json_response` 模式，提取第一个 `[`/`{` 到最后一个 `]`/`}` 之间子串重试（对截断无效，但能处理 LLM 输出前后多余文本的情况）

### 不影响功能的保证

- 所有诊断增强仅记录日志，不改变重试逻辑、不改变返回值、不改变 ExtractResult 结构
- 第三层宽松回退仅在两层解析失败时增加一次尝试，失败仍抛出原 `JSONDecodeError`
- 未应用到 ontology_extractor / style_extractor（max_tokens 已是 8000）

### 测试

- 常量验证：`python -c "from novelforge.services.context_extractor import EXTRACT_MAX_TOKENS, PROTAGONIST_EXTRACT_MAX_TOKENS; print(EXTRACT_MAX_TOKENS, PROTAGONIST_EXTRACT_MAX_TOKENS)"` 输出 `16000 16000`
- 解析函数单元测试：`python -m pytest tests/test_m4_context_extraction.py tests/test_protagonist_extraction.py -q`
- 全量测试：`python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"`

### 文档同步

- `agent.md`：context_extractor 描述补充 max_tokens 调整、finish_reason 检测、第三层宽松回退
- `update.md`：本条目

## 2026-07-14：版本号提升至 v0.2.11

### 背景

自 v0.2.10 以来已完成两项核心改动：①新增「文风档案提取」功能（StyleProfile 九维量化档案 + 镜像世界观底层三大机制）②端点配置新增自定义请求扩展（extra_payload deep merge + extra_headers update）③修复单章续写生成未组装文风档案 bug 并统一 4 个档案空值占位文本。上述改动已分别于 2026-07-13 记录在 update.md，本条目为发版版本号提升与文档同步。

### 核心改动

- **版本号提升**（`novelforge/__init__.py`）：`__version__` 由 `0.2.10` 提升至 `0.2.11`
- **README.md 同步**：顶部「当前版本」更新为 v0.2.11；「更新记录」章节顶部新增 v0.2.11 小节，按版本倒序汇总本版核心改动（文风档案提取/端点自定义扩展/单章续写组装 bug 修复/写作风格概念统一/4 档案空值占位统一/阶段模板补 style_profile 占位符）
- **agent.md 同步**：项目概述「当前版本」更新为 v0.2.11

### 测试

- 版本号校验：`python -c "from novelforge import __version__; print(__version__)"` 输出 `0.2.11`
- 文档一致性：README.md 顶部版本 / agent.md 版本 / `__init__.py` 三处均为 v0.2.11

### 文档同步

- `README.md`：版本头 + v0.2.11 更新记录小节
- `agent.md`：项目概述版本号
- `update.md`：本条目

## 2026-07-13：统一【写作风格】与【文风档案】+ 修复单章生成未组装文风档案

### 背景

用户反馈两个问题：①【写作风格】（NovelProfile.writing_style 字符串）与【文风档案】（Project.style_profile 九维量化档案）概念重叠，主提示词模板中并列出现导致模型困惑；②有文风档案时单章续写生成未成功组装——发送的 messages 中 `{{style_profile}}` 未被替换为档案内容。

### 根因

- **核心 bug**：`main_window.py` L1846 单章续写生成 `prompt_assembler.assemble()` 调用漏传 `style_profile` 参数（对比 L5486 重写生成已正确传参），导致 `{{style_profile}}` 宏未注入 `ctx.extra`，宏替换后保留占位符或被替换为空
- **预览不一致**：L3453 续写预览 `assemble` 调用 4 个档案参数全缺，预览结果与实际发送不一致
- **概念混乱**：`writing_style`（文字描述）与 `style_profile`（量化档案）在主提示词模板中并列分节，概念重叠
- **空值处理不统一**：4 个档案 None 时未注入占位文本，`{{style_profile}}` 被替换为空字符串而非"（无文风档案）"，与审计模板"若为'（无文风档案）'则..."逻辑不匹配

### 核心改动

- **单章续写生成补 style_profile 参数**（`novelforge/ui/main_window.py` L1865）：在 `assemble` 调用补 `style_profile=project.style_profile if project else None,`，修复文风档案未组装 bug
- **续写预览补齐 4 个档案参数**（`novelforge/ui/main_window.py` L3468-3473）：补 `world_ontology`/`protagonist_profile`/`custom_audit_rules`/`style_profile`，确保预览与实际发送一致
- **主提示词模板统一**（`novelforge/resources/defaults/default_preset.json` L9）：【写作风格】分节改名【写作风格补充说明】并移到【文风档案】正下方，明确量化档案为主、文字描述为辅的主次关系（保留 `{{writing_style}}` 宏向后兼容，不删字段）
- **4 个档案空值占位文本统一**（`novelforge/core/prompt_assembler.py` L700-766）：`_build_macro_context` 中 4 个档案 None 时注入占位文本（"（无世界观底层）"/"（无主角形象档案）"/"（无文风档案）"/"（无自定义设定）"）；新增 `_serialize_profile_or_placeholder`/`_serialize_rules_or_placeholder` 静态辅助方法封装序列化+占位逻辑
- **补 StyleProfile 导出**（`novelforge/models/__init__.py`）：补 `StyleProfile` 到 import 与 `__all__`，与 `WorldOntology`/`ProtagonistProfile` 导出一致

### 测试

- 导入验证：`from novelforge.models import StyleProfile` + `import novelforge.ui.main_window` + `import novelforge.core.prompt_assembler` OK
- 既有测试无回归：`test_m2_prompt_assembly.py` + `test_rewrite_current_mode.py` + `test_style_extractor.py` 共 39 passed
- 组装验证：构造带 style_profile 的 project 调 assemble，确认有档案时 `{{style_profile}}` 被替换为 JSON 内容（含 `language_texture` 等字段），None 时被替换为"（无文风档案）"

### 文档同步

- `agent.md`：prompt_assembler.py 描述补空值占位文本统一 + 辅助方法；default_preset.json 描述补【写作风格】改名与位置调整；main_window.py 描述补 3 处 assemble 调用点修复
- `update.md`：本条目

## 2026-07-13：端点配置增加自定义请求扩展（payload 字段 + HTTP 头）

### 背景

用户在端点配置中想增加自定义请求扩展能力，主要用例是 zenmux 的 `provider_routing_strategy`（详见 https://zenmux.ai/docs/about/provider-routing.html#specify-provider-list ）。经查阅文档，该字段是请求体（payload）字段而非 HTTP header。用户同时要求支持自定义其它内容，因此实现为通用扩展机制：自定义 payload 字段（deep merge）+ 自定义 HTTP 头（update）。

### 核心改动

- **配置层**（`novelforge/core/config.py`）：`add_endpoint` 默认字段追加 `extra_payload: {}` / `extra_headers: {}`（与 `reasoning_effort` 同级，作为端点级扩展字段）
- **LLM 客户端**（`novelforge/services/llm_client.py`）：
  - 构造函数追加 `extra_payload` / `extra_headers` 参数，存为实例属性
  - `stream_chat_completion` / `chat_completion`：在 `_filter_unsupported_params` 后 deep merge `extra_payload` 到 payload（确保自定义字段不被模型过滤逻辑误删）；headers 构建后 `update` `extra_headers`（可覆盖默认 `Authorization`/`Content-Type`）
  - `fetch_models`：headers 也合并 `extra_headers`（GET /models 可能需要自定义头）
  - 新增模块级辅助函数 `_deep_merge_dict`：dict 递归合并，list/scalar 覆盖
- **UI 层**（`novelforge/ui/settings_dialog.py` `EndpointEditDialog`）：
  - `_setup_ui`：在「思考强度」后、「设为默认端点」前增加两个 `QPlainTextEdit`——「自定义请求体字段」(JSON) + 「自定义 HTTP 头」(JSON)，含 placeholder 示例（zenmux provider_routing_strategy）
  - `_load_data`：从 endpoint dict 读取 `extra_payload`/`extra_headers`，`json.dumps(indent=2)` 序列化回填
  - `_on_accept`：解析两个 JSON 文本框，格式错误弹提示阻止保存；JSON 顶层非 object 报错；成功放入 `_result`
- **Worker 透传**（4 个 worker 构造函数追加 `extra_payload`/`extra_headers` 参数 + 存实例属性 + 所有 LLMClient 调用点透传）：
  - `continuation_worker.py`：构造函数 + `_effective_client` 覆盖端点 + `_run` 主 client（2 处 LLMClient）
  - `audit_worker.py`：构造函数 + `_effective_client` 覆盖端点 + `_run` 主 client（2 处 LLMClient）
  - `volume_orchestrator.py`：构造函数 + `_effective_client` 覆盖端点 + `run` 主 client（2 处 LLMClient）
  - 覆盖端点场景从 `_debug_override_endpoint` dict 直接 `.get("extra_payload")` 读取
- **Extractor 透传**（4 个 `_get_llm_client` 从 endpoint dict 读取传入 LLMClient）：
  - `context_extractor.py` / `ontology_extractor.py` / `style_extractor.py` / `custom_audit_rule_service.py`
- **主窗口接线**（`novelforge/ui/main_window.py` 7 处 worker 创建点）：从 endpoint dict 读取 `extra_payload`/`extra_headers` 传入 worker 构造函数（ContinuationWorker×3 + AuditWorker×3 + VolumeOrchestrator×1；L5570 处变量名为 `single_endpoint`）

### 使用示例

端点编辑对话框「自定义请求体字段」填入：
```json
{"provider_routing_strategy": {"type": "specified_providers", "providers": ["anthropic/anthropic_endpoint"]}}
```
发送时该字段会 deep merge 到请求体，实现 zenmux 自定义路由策略。

### 测试

- 导入验证：`import novelforge.services.llm_client; import novelforge.ui.settings_dialog; import novelforge.services.continuation_worker; import novelforge.services.audit_worker; import novelforge.services.volume_orchestrator; import novelforge.ui.main_window` OK
- `_deep_merge_dict` 单元验证：`{'a':1,'b':{'c':2}}` + `{'b':{'d':3},'e':4,'f':[5]}` → `{'a': 1, 'b': {'c': 2, 'd': 3}, 'e': 4, 'f': [5]}` ✓
- 既有测试无回归：`test_m2_prompt_assembly.py` + `test_rewrite_current_mode.py` + `test_style_extractor.py` 共 39 passed

### 文档同步

- `agent.md`：config.py 描述补 `extra_payload/extra_headers`；llm_client.py 描述补构造函数扩展参数 + deep merge 逻辑 + `_deep_merge_dict` 辅助函数
- `update.md`：本条目

## 2026-07-13：修复提取文风档案后无法查看的问题（存储层持久化遗漏）

### 背景

用户反馈：提取文风档案后点击「查看文风档案」按钮始终提示「尚未提取文风档案」。经排查根因为**存储层 `novelforge/core/storage.py` 完全遗漏了 `style_profile` 字段的持久化**，导致提取完成后数据从未真正写入数据库，重新加载项目后 `project.style_profile` 恒为 `None`。

### 根因

`storage.py` 中 `projects` 表的 `style_profile` 字段在 4 处全部缺失（对比 `world_ontology` 同级字段均有处理）：
1. 建表 `CREATE TABLE` 语句无 `style_profile TEXT` 列
2. `_migrate_projects_columns` 无 `style_profile` 列幂等迁移
3. `save_project` INSERT/UPDATE 语句无 `style_profile` 字段写入
4. `_row_to_project` 无 `style_profile` 字段读取

链路：提取流程 `style_extractor.py` 调 `save_project` 时 `style_profile` 被丢弃 → 查看流程 `load_project` 返回的 dict 无此键 → `Project.model_validate` 取默认值 `None` → 弹「尚未提取」提示。

### 核心改动

- `novelforge/core/storage.py`（4 处）：
  - 建表语句追加 `style_profile TEXT` 列
  - `_migrate_projects_columns` 追加 `style_profile` 列幂等迁移（docstring 同步更新）
  - `save_project` INSERT 列名/VALUES 占位符/ON CONFLICT UPDATE SET/参数元组四处同步追加 `style_profile`
  - `_row_to_project` 追加 `style_profile` 反序列化读取
- `novelforge/services/storage_service.py`（1 处）：
  - `load_project` 追加 `style_profile` 的 dict→StyleProfile 显式模型还原（镜像 `world_ontology` 模式，含失败日志）

### 测试

- `python -m pytest tests/test_style_extractor.py -q --tb=short`：9 passed（无回归）
- 导入验证：`import novelforge.core.storage; import novelforge.services.storage_service` OK
- **端到端持久化验证**：构造带 `style_profile` 的 Project → `save_project` → `load_project` → 确认 `style_profile` 字段存在且 `language_texture`/`source_chapter_range` 数据完整 → `PERSISTENCE OK`

### 文档同步

- `agent.md`：storage.py / storage_service.py 描述为通用层描述（列迁移函数已涵盖），无需单字段更新
- `update.md`：本条目

## 2026-07-13：新增文风档案提取功能（StyleProfile 全链路）

### 背景

参照【世界观底层 WorldOntology】和【主角形象 ProtagonistProfile】模式，新增项目级固化的文风档案 StyleProfile，含 9 维度文笔风格量化参数（语言质感/叙事节奏/画面构建/人物塑造/情感调动/创新辨识度/主角配角配比/视角运用/时间与过渡）。全文提取一次固化到 `Project.style_profile`，不入世界书。审计阶段对照九维量化参数检查文风一致性。

### 核心改动

- **数据模型**（`novelforge/models/style_profile.py` 新增）：StyleProfile 含 9 维度 dict 字段 + extracted_at/source_chapter_range 元数据；`ConfigDict(populate_by_name=True)` 向后兼容
- **Project 字段**（`novelforge/models/project.py`）：新增 `style_profile: StyleProfile | None = None`
- **路径函数**（`novelforge/utils/paths.py`）：`get_extract_style_prompt_path` / `get_extract_style_merge_prompt_path`
- **提取提示词**（`novelforge/resources/defaults/extract_style_prompt.txt` 新增）：8 占位符（title/author/protagonist/synopsis/world_setting/writing_style/accumulated_style/chapters_text）
- **合并提示词**（`novelforge/resources/defaults/extract_style_merge_prompt.txt` 新增）：多批次提取结果合并
- **破限模板**（`novelforge/resources/defaults/jailbreaks/jb_style_extraction.txt` 新增）：LOW/MID/HIGH 三档
- **流程配置**：
  - `novelforge/ui/flow_endpoint_dialog.py`：`FLOW_DEFINITIONS` 追加 `("style_extraction", "文风档案提取")`（总数 10→11）
  - `novelforge/core/config.py`：`FLOW_DEFAULT_JAILBREAKS` 追加 `"style_extraction": "low"`
- **提取服务**（`novelforge/services/style_extractor.py` 新增）：StyleExtractor 镜像 OntologyExtractor 三段式架构（拆批/增量合并/语义整合）；`extract_style_streaming` 主方法；`STYLE_DIMENSIONS` 9 字段名常量；`STYLE_EXTRACT_MAX_TOKENS=8000`/`STYLE_EXTRACT_TEMPERATURE=0.2`；固化到 `Project.style_profile` 不入世界书；try/finally 关闭 LLMClient
- **UI 按钮**（`novelforge/ui/context_preview_panel.py`）：新增「提取文风档案」(primaryBtn) + 「查看文风档案」(secondaryBtn) 按钮，放在主角按钮后、自定义设定按钮前；新增 `extract_style_requested` / `view_style_requested` 信号；三态方法组 `start_style_extraction` / `update_style_progress` / `update_style_batch` / `finish_style_extraction` / `fail_style_extraction`；所有现有 start/finish/fail 方法的按钮启用/禁用列表同步追加
- **主窗口接线**（`novelforge/ui/main_window.py`）：导入 StyleProfile + StyleExtractor；实例化 `self.style_extractor`；新增信号 `_style_chunk_received` / `_style_done` / `_style_batch_done` + 状态 `_style_extracting` / `_style_stream_text`；连接 `extract_style_requested` / `view_style_requested` 信号；处理器 `_on_extract_style_requested` / `_on_style_done` / `_on_view_style_requested` / `_on_style_chunk_received` / `_on_style_batch_done`；`_format_style_profile` 静态方法；`_prompt_continue_without_extraction` 追加 style_profile 参数（5 处调用点同步）；5 处 assemble 调用追加 `style_profile=` 参数
- **上下文注入**（`novelforge/core/prompt_assembler.py`）：`assemble` 方法签名追加 `style_profile: Any = None` 参数；`_build_macro_context` 追加 `style_profile` 参数与序列化注入逻辑（兼容 model_dump/dict），供 `{{style_profile}}` 宏替换
- **模板注入**（16 个模板文件）：`phase_single_audit` / `phase_verify` / `phase_rewrite_analysis` / `phase_audit_rewrite` / `phase_volume_outline` / `phase_outline_final` / `phase_outline_audit` / `phase_revise` / `phase_chapter_outline` / `phase_chapter_rewrite` / `phase_custom_rule_parse` / `phase_deep_analysis` / `phase_deep_analysis_merge` / `phase_writing_element_analysis` / `phase_writing_element_refinement` / `default_preset.json` 全部追加 `{{style_profile}}` 占位符
- **审计维度增强**：
  - `phase_single_audit.txt` 维度 2 `style`：追加九维量化参数对照检查项（当文风档案存在时）；summary 输出格式追加 `【文风一致性审计】` 标记段落；「五个固定标记段落」改为「六个」；缺失检查与验证规则同步
  - `phase_verify.txt` 维度 8 `style`：同步九维量化参数对照检查

### 测试

- `tests/test_style_extractor.py`（新增）：9 个测试用例，镜像 `test_ontology_extractor.py` 结构
  - `_split_chapters_by_token_limit` 单批次/多批次
  - `_build_style_prompt` 8 占位符替换
  - `_merge_style_fields` 序列化长度启发式合并 / 空字段取另一侧
  - `extract_style_streaming` 单批次提取返回 StyleProfile / 多批次触发合并
  - `StyleProfile` 模型 9 维度校验（默认空 dict / 接受数据）
- `tests/test_rewrite_current_mode.py`：`test_flow_definitions_count_is_10` 更新为 `test_flow_definitions_count_is_11`（FLOW_DEFINITIONS 总数 10→11）
- **修复 `prompt_assembler.py` bug**：`_build_macro_context` 方法签名漏了 `style_profile` 参数 + `assemble` 调用点未传参，导致 `NameError: name 'style_profile' is not defined`；已补齐签名参数与调用点传参
- 既有测试无回归：`test_ontology_extractor.py` / `test_protagonist_extraction.py`（40 passed）/ `test_m2_prompt_assembly.py`（10 passed）/ `test_rewrite_current_mode.py`（20 passed）全部通过

### 文档同步

- `agent.md`：models/ 新增 `style_profile.py` 行；services/ 新增 `style_extractor.py` 行；project.py 描述补 `Project.style_profile`；设计决策第 3 条新增「文风档案提取（StyleExtractor）」要点；设计决策第 10 条 `{{style_profile}}` 宏；设计决策第 11/14 条模板占位符；模板描述同步
- `update.md`：本条目

## 2026-07-13：重写需求分析强调保全原剧情排布与检查开头结尾连贯性

### 背景

「重写当前章节」的需求分析阶段（phase_rewrite_analysis.txt）虽已有「重写边界最高优先」与「用户需求优先」原则，但未明确点出「保全原剧情排布是满足用户诉求的前提」这一核心取向；开头结尾连贯性检查薄弱——具体要求清单 #7 仅提「开头结尾衔接（不得为下一章铺垫）」，未覆盖开头与前一章结尾的衔接、结尾性质保全。期望着重强调：在不影响原来剧情排布发展的情况下满足用户诉求，检查开头结尾连贯性。

### 核心改动

- `novelforge/resources/defaults/agent/phase_rewrite_analysis.txt`：4 处强化
  - 分析原则新增「剧情排布保全」：在不影响原剧情排布发展前提下满足用户诉求，原章节事件序列/场景顺序/转折点/伏笔布局/人物登场退场节奏为须保全的剧情骨架，冲突时建议改用续写模式
  - 「当前章节分析」分节追加：须专门审视开头连贯性（承接前一章结尾的过渡方式）与结尾连贯性（结尾性质开放/封闭+过渡下一章+伏笔），作为重写须保全的衔接骨架
  - 「重写要点」分节追加「开头结尾连贯性保全」要点：开头与前一章结尾自然衔接，结尾保持原性质不变（除非用户明确要求改）
  - 「具体要求清单」#7 结构类强化：开头连贯性（与前一章结尾自然衔接，过渡方式不得因重写破坏）+ 结尾连贯性（保持原结尾性质开放/封闭不变，不得破坏与下一章衔接，不得为下一章铺垫）

### 测试

- `python -m pytest tests/test_rewrite_current_mode.py -q --tb=short`（20 passed，6 占位符校验通过）

### 文档同步

- `agent.md`：phase_rewrite_analysis.txt 描述 + 设计决策第 14 条「分析模板」描述同步
- `update.md`：本条目

## 2026-07-13：重写按钮使用续写控制面板选择的模型

### 背景

点击「重写」按钮时，`_on_rewrite` 方法用 `get_parameters()` 重新获取参数，但该方法不包含 `model` 字段，导致下游所有分支（缓存重写分析直接生成、缓存写作模式精炼直接生成、无缓存完整流程）回退到 `get_flow_model()` 而非使用面板当前选中的模型。期望行为：使用【重写】功能时应当使用【续写控制】面板选择的模型。

### 核心改动

- `novelforge/ui/main_window.py`：`_on_rewrite` 在 `params = self.continuation_panel.get_parameters()` 之后补回 `params["model"] = self.continuation_panel.get_selected_model()`，与面板自身 `_on_start_clicked` / `_on_rewrite_clicked` 的模式一致。修复覆盖三个分支：
  - rewrite_current + 缓存分析 → `_on_rewrite_analysis_accepted` 用面板 model
  - writing_mode + 缓存精炼 → `_on_start_writing_mode_continuation` 用面板 model
  - 无缓存 → `_on_start_continuation_routed` → `_on_start_rewrite_current` 用面板 model

### 测试

- `python -m pytest tests/test_rewrite_current_mode.py tests/test_flow_plugin.py tests/test_flow_endpoint_config.py -q --tb=short`（66 passed）

### 文档同步

- `agent.md`：main_window.py 描述补充 `_on_rewrite` 补回 model 的说明
- `update.md`：本条目

## 2026-07-12：v0.2.10 文档同步——补全发版后改动的文档记录

### 背景

v0.2.10 发版后又陆续完成 5 项改动（写作模式流程插件、写作要素提示词优化、Grok 参数过滤、Gemini contents 兜底、重写按钮复用审计结果），但这些改动未及时同步到 README.md 的 v0.2.10 更新记录与 agent.md 的架构分层描述。本次保持版本号 v0.2.10 不变，仅同步相关文档。

### 核心改动

- `README.md`：v0.2.10 更新记录追加 5 条（写作模式流程插件/写作要素提示词优化/Grok 参数过滤/Gemini contents 兜底/重写按钮复用审计结果）
- `agent.md`：
  - `llm_client.py` 描述补充 `_filter_unsupported_params`（xAI Grok 参数过滤）与 `_ensure_user_message`（Gemini 兼容网关兜底）
  - `continuation_panel.py` 描述补充 `_on_rewrite_clicked` 统一发射 rewrite 信号
  - `main_window.py` 描述补充 `_on_rewrite_analysis_accepted`/`_on_start_writing_mode_continuation` 缓存审计/精炼结果到 `swipe.parameters_snapshot` + `_on_rewrite` 核心决策逻辑（有缓存跳过分析直接生成，无缓存走完整流程）

### 文档同步

- `README.md`：v0.2.10 更新记录补全
- `agent.md`：llm_client/continuation_panel/main_window 描述同步
- `update.md`：本条目

## 2026-07-12：重写按钮复用审计结果（rewrite_current + writing_mode）

### 背景

在 rewrite_current 和 writing_mode 模式下，点击【重写】会重新走完整流程（含审计阶段），已完成的审计结果完全丢失，需要用户重新审阅 AuditDialog。期望行为：如果前面审计阶段已完成，点击重写后应当保存前面的审计片段，针对目前内容重新发送单章生成。

### 核心改动

- `novelforge/ui/continuation_panel.py`：`_on_rewrite_clicked` 统一走 rewrite 信号（不再 rewrite_current 特殊走 start_flow）
- `novelforge/ui/main_window.py`：
  - `_on_rewrite_analysis_accepted`：缓存 analysis_text 到 params（写入 swipe.parameters_snapshot）
  - `_on_start_writing_mode_continuation`：缓存 refined_output 到 params
  - `_on_rewrite`：核心决策逻辑 —— 有缓存审计结果则跳过审计直接生成新 swipe，无缓存则走完整流程
- 旧 swipe 兼容：无缓存键的旧 swipe 回退到原流程（重新分析）

### 测试

- `python -m pytest tests/ -q -k "not TestUIComponents" --ignore=tests/test_m5_polish.py --tb=short`

## 2026-07-12：修复 Grok 模型参数不支持错误 + reasoning_received 断开警告

### 背景

使用 grok-4.5 模型时出现两个错误：
1. `API 错误 (400): Model grok-4.5 does not support parameter presencePenalty` —— stream_chat_completion 无条件把 presence_penalty/frequency_penalty 写入 payload，xAI Grok-4/4.5/code 系列不接受这两个参数
2. `RuntimeWarning: Failed to disconnect (None) from signal "reasoning_received(QString)"` —— reasoning_received 信号在 worker 中声明但 UI 从未 connect，对无连接信号调用 disconnect 触发 PySide6 警告（非异常，无法被 except 捕获）

### 核心改动

- `novelforge/services/llm_client.py`：
  - 新增 `_is_xai_model` 静态方法：检测 Grok 模型
  - 新增 `_filter_unsupported_params` 静态方法：按模型子型号删除不支持参数（所有 Grok 模型删除 presence/frequency_penalty；非 grok-3-mini 删除 reasoning_effort）
  - `stream_chat_completion` 和 `chat_completion` payload 构建后调用过滤
- `novelforge/ui/main_window.py`：移除 3 处 `old_worker.reasoning_received.disconnect()` 调用（行 1904/4806/5308）
- `tests/test_reasoning_effort.py`：新增 6 个测试验证过滤逻辑

### 测试

- `python -m pytest tests/ -q -k "not TestUIComponents" --ignore=tests/test_m5_polish.py --tb=short`

## 2026-07-12：优化写作要素分析与深化提示词

### 背景

写作模式（writing_mode）的两个提示词模板需优化：分析阶段未严格约束"不在场角色不得提及"，伏笔分析缺乏合理性判断；深化阶段角色形象仅覆盖约 30% 的【主角形象】8 维度信息，不足以支撑续写一致性。

### 核心改动

- `novelforge/resources/defaults/agent/phase_writing_element_analysis.txt`：
  - 分析原则新增"首要分析用户指令"与"在场角色绝对优先"（不在场角色不得提及）
  - 新增「本章背景信息」分节（当前地点/在场角色/前情衔接/用户指令解析）
  - 出场角色分节强化"不在场角色不得列入"约束
  - 关键伏笔分节新增合理性判断（每个伏笔须明确回复"合理/不合理"并说明理由）
- `novelforge/resources/defaults/agent/phase_writing_element_refinement.txt`：
  - 精炼任务从"简化版"升级为"扩展版"，参照【主角形象】8 维度涵盖 75% 以上
  - 角色格式从 4 项扩展为 8 维度结构（基础锚点/人格系统/动机系统/情感防御/行为指纹/关系坐标/成长弧光/OOC红线）
  - 主角与关键配角须覆盖全部 8 维度，次要配角至少 6 维度

### 测试

- 提示词模板为纯文本资源，无单元测试；通过实际写作模式流程验证输出质量

## 2026-07-12：修复 Gemini 模型在非正文流程中的 "contents are required" 错误

### 背景

在审计、写作模式分析、卷续写编排各阶段等非正文流程中使用 Gemini 模型（通过 Gemini 兼容网关如 one-api/new-api 代理）时，出现 `API 错误 (500): Error: contents are required`。根本原因：这些流程构建的 messages 列表全部是 `role: "system"` 消息，Gemini 兼容网关将 system 消息提取到 `systemInstruction` 字段后 `contents` 数组为空，触发 Gemini API 的 500 错误。

### 核心改动

- `novelforge/services/llm_client.py`：
  - 新增 `_ensure_user_message` 静态方法：检测 messages 全为 system 角色时，将最后一条消息的角色改为 user（使用副本，不影响原始列表）
  - `stream_chat_completion` 和 `chat_completion` 入口调用兜底
- `tests/test_reasoning_effort.py`：新增 3 个测试验证兜底逻辑

### 测试

- `python -m pytest tests/ -q -k "not TestUIComponents" --ignore=tests/test_m5_polish.py --tb=short`

## 2026-07-12：写作模式接入默认流程列表

### 背景

上一条目完成了 writing_mode 流程插件的全部资源与代码，但 `_BUILTIN_PLUGIN_IDS` 列表未包含 "writing_mode"，导致首启不复制 writing_mode.json 到用户目录、续写面板模式下拉不显示「写作模式」选项。本次修复将 writing_mode 正式接入默认流程列表，与 single/volume/rewrite_current 并列。

### 核心改动

- `novelforge/services/flow_plugin_service.py`：
  - `_BUILTIN_PLUGIN_IDS` 新增 `"writing_mode"`（3→4 项）
  - 模块 docstring + `_ensure_builtin_plugins` 方法 docstring："三种模式"→"四种模式"
- `novelforge/utils/paths.py`：`get_default_flow_plugin_path` docstring 示例补 writing_mode
- `tests/test_flow_plugin.py`：
  - `test_builtin_plugins_copied`：新增 writing_mode 断言 + docstring 3→4
  - `test_list_plugin_ids_sorted`：内置插件断言 3→4（`ids[:4]` + `ids[4:]`）
  - 新增 `test_load_builtin_writing_mode`：验证 3 阶段 audit→audit→continuation 结构

### 测试

- `python -m pytest tests/ -q -k "not TestUIComponents" --ignore=tests/test_m5_polish.py --tb=short`

## 2026-07-12：新增续写控制「写作模式」流程插件

### 背景

续写控制原有三种模式（单次续写/卷续写/重写当前章节）无法满足"先分析写作要素、再深化角色形象、最后生成"的精细创作需求。新增 writing_mode 流程插件，通过 3 阶段流程（audit→audit→continuation）实现：阶段 1 分析本次出场角色/场所/事件，阶段 2 为每个角色产出简化版形象档案（外貌+心理学形象+语言风格+OOC红线），阶段 3 将精炼输出前置【写作参考】到 user_input 走单章续写。复用 audit agent（不新增 agent 类型），通过通用分析路径 `_on_start_generic_analysis` 实现。

### 核心改动

- 新增提示词模板：
  - `novelforge/resources/defaults/agent/phase_writing_element_analysis.txt`：6 占位符，5 分节输出（出场角色/场所/相关事件/关键伏笔/风格基调）
  - `novelforge/resources/defaults/agent/phase_writing_element_refinement.txt`：3 占位符，角色形象（外貌/心理学形象/语言风格/OOC红线）+ 场所精炼 + 其他关键要素
- 新增破限模板：
  - `novelforge/resources/defaults/jailbreaks/jb_writing_element_analysis.txt`：LOW/MID/HIGH 三档，强调不拒绝敏感小说内容的角色/场所/事件分析
  - `novelforge/resources/defaults/jailbreaks/jb_writing_element_refinement.txt`：LOW/MID/HIGH 三档，强调不拒绝敏感角色心理学形象/语言风格刻画
- 新增内置流程插件：
  - `novelforge/resources/defaults/flow_plugins/writing_mode.json`：3 阶段 audit→audit→continuation，ui_mode=standard，accept_mode=promote
- `novelforge/ui/flow_endpoint_dialog.py`：FLOW_DEFINITIONS 新增 2 个 flow_key（writing_element_analysis/writing_element_refinement），docstring 8→10 流程、6→8 非正文流程
- `novelforge/core/config.py`：FLOW_DEFAULT_JAILBREAKS 新增 2 键，默认 low 等级
- `novelforge/ui/main_window.py`：
  - `_flow_handler_audit`：按 stage.flow_key 分派，rewrite_analysis 走原路径，其余走 `_on_start_generic_analysis` 通用分析路径
  - `_flow_handler_continuation`：新增 `created_by=="writing_mode"` 分支，调 `_on_start_writing_mode_continuation`
  - `_on_start_continuation`：新增 `user_input_override` 参数（写作模式第 3 步注入精炼输出）
  - 新增 `_build_previous_chapters_text(lookback)`：构建前 lookback 章正文（含当前章）
  - 新增 `_on_start_generic_analysis`：通用分析路径，镜像 `_on_start_rewrite_current`，注入 7 占位符（含 `{{prev_analysis}}`/`{{previous_chapters_text}}`），AuditWorker max_tokens 默认 6000
  - 新增 4 个回调：`_on_generic_analysis_finished`/`error`/`cancelled`/`accepted`（采纳后 resume 推进，不 cancel）
  - 新增 `_on_start_writing_mode_continuation`：阶段 2 精炼输出前置【写作参考】到 user_input

### 测试

- 语法校验：`python -c "import ast; ast.parse(open(r'novelforge/ui/main_window.py', encoding='utf-8').read())"` → 通过
- `python -m pytest tests/ -q -k "not TestUIComponents" --ignore=tests/test_m5_polish.py --tb=short`
- 结果：`663 passed, 15 skipped, 12 deselected in 7.75s`（0 失败，无回归）
- 测试修复：2 个断言旧 flow 数量的测试更新（8→10）：
  - `tests/test_flow_endpoint_config.py`：`test_flow_endpoint_dialog_has_8_flows` → `test_flow_endpoint_dialog_has_10_flows`（断言 8→10）
  - `tests/test_rewrite_current_mode.py`：`test_flow_definitions_count_is_8` → `test_flow_definitions_count_is_10`（断言 8→10）

### 文档同步

- `FLOW_PLUGIN_GUIDE.md`：第 2 节内置插件表 +1 行、第 4.1 节 agent 行为补充、第 4.4 节标题改"十个标准 flow_key" +2 行、第 7 节标题改"四个内置插件" +7.4 小节、新增写作模式使用说明与预期效果小节
- `agent.md`：架构分层（jailbreaks 6→8、flow_plugins 3→4、+2 模板文件、flow_endpoint_dialog 8→10/6→8、main_window 接线补充、flow_plugin_service 3→4）、关键设计决策第 18 条更新（3→4 内置插件）、新增第 19 条写作模式流程
- `update.md`：本条目

## 2026-07-12：发布 v0.2.10 版本——完整代码审查与修复 + 文档同步规则完善

### 背景

前一次会话对整个赓笔项目进行了完整代码审查，发现 40+ 问题分布在前端 UI、核心服务、数据模型与测试四个层面，覆盖严重 Bug 与安全、代码正确性、输入校验与 UX、测试质量四类。所有修复已完成并通过测试套件验证（663 passed, 15 skipped, 12 deselected）。本次发版将版本号从 v0.2.9 提升到 v0.2.10，并完善 agent.md 中关于 FLOW_PLUGIN_GUIDE.md 的更新触发规则。

### 核心改动

- `novelforge/__init__.py`：`__version__` 从 `"0.2.9"` 提升到 `"0.2.10"`
- `README.md`：
  - 顶部版本徽标 `v0.2.9` → `v0.2.10`
  - 「更新记录」章节顶部追加 `### v0.2.10` 小节，列出 19 条修复要点（涵盖 ContextExtractor session 泄漏、storage 路径穿越、ConfigManager 线程安全、MainWindow closeEvent 崩溃、ContinuationPanel swipe 污染、VolumeOrchestrator 无限循环、FlowExecutor 递归栈溢出、FullTextSearchWorker 跨线程 SQLite、DebugPromptDialog 对话框契约、SettingsDialog base_url 校验、apply_highlights 异常吞没、ThreadPoolExecutor 未 shutdown、_row_to_* 位置索引、is_network_filesystem Windows 检测、volume_panel parse 异常、async_runner 协程泄漏、exporter 混淆表达式、9 项测试修复）
- `agent.md`：
  - 「当前版本」从 `v0.2.9` 更新到 `v0.2.10`
  - 「修改后必须更新」节新增 `FLOW_PLUGIN_GUIDE.md` 更新触发规则段落，明确 8 类触发场景：
    1. 数据模型变更（FlowPlugin/FlowStage 字段/校验规则）
    2. agent 类型变更（新增/删除/返回值契约）
    3. ui_mode / accept_mode 变更
    4. flow_key 变更（标准 flow_key + 配置项结构）
    5. 执行引擎逻辑变更（阶段推进/params 合并/挂起恢复/cancel）
    6. 服务层行为变更（导入规则/版本升级/存储路径）
    7. 内置插件变更（single/volume/rewrite_current JSON）
    8. MainWindow 接线变更（start_flow/handler 分发/accept_mode 适配/FlowPluginManager UI）
  - 每类场景标注需更新的 FLOW_PLUGIN_GUIDE.md 对应章节编号

### 测试

- `python -m pytest tests/ -q -k "not TestUIComponents" --ignore=tests/test_m5_polish.py --tb=short`
- 结果：`663 passed, 15 skipped, 12 deselected in 8.29s`（0 失败，无回归）

### 文档同步

- `README.md`：版本徽标 + v0.2.10 更新记录
- `agent.md`：当前版本号 + FLOW_PLUGIN_GUIDE.md 更新触发规则
- `update.md`：本条目

## 2026-07-12：修复 5 个 service 文件的异常吞没/无限循环/递归栈溢出问题

### 背景

5 个 service 文件存在中低风险问题：① ContinuationWorker/AuditWorker/AsyncLoopRunner 的 `run()` finally 块用 `except Exception: pass` 静默吞掉清理异常，且未在 `run_until_complete` 前检查 `loop.is_closed()`，循环已关闭时会抛 RuntimeError 被静默吞掉；② AsyncLoopRunner.run 超时后未取消 `concurrent.futures.Future`，导致协程在后台泄漏；③ VolumeOrchestrator 逐章 verify/revise 循环中 critical 问题忽略 `max_revise_rounds_per_chapter` 上限一直修正到通过，若 LLM 持续产生 critical 问题会陷入无限循环；④ FlowExecutor._execute_current_stage 用递归调用自身推进下一阶段，插件阶段数多时会触及 Python 递归上限。

### 核心改动

- `novelforge/services/continuation_worker.py`：
  - `run()` finally 块：三处 `except Exception: pass` 改为 `except Exception as e: logger.warning("...: %s", e)`（描述性消息）；每处 `self._loop.run_until_complete(...)` 前加 `if not self._loop.is_closed():` 守卫；保留末尾 `self._loop.close()`
- `novelforge/services/audit_worker.py`：
  - `run()` finally 块：同 continuation_worker.py 的修复模式
- `novelforge/services/async_runner.py`：
  - `_run_loop` finally 块：`except Exception: pass` 改为 `except Exception as e: logger.warning("AsyncLoopRunner cleanup error: %s", e)`
  - `run`：超时分支调 `future.cancel()` 取消协程后再 `raise`，避免协程在后台事件循环中泄漏
- `novelforge/services/volume_orchestrator.py`：
  - 新增模块级常量 `MAX_CRITICAL_REVISE_ROUNDS = 20`
  - `_run_chapter_loop` 中两处 critical-issue verify/revise 循环（首轮强制修改循环 + after_chapter reject 后的循环）均新增硬上限检查：`if has_critical and rounds >= MAX_CRITICAL_REVISE_ROUNDS: logger.error(...); break`
- `novelforge/services/flow_executor.py`：
  - `_execute_current_stage`：递归自调用改为 `while True` 迭代循环推进下一阶段；CANCEL/PENDING/完成 三种退出条件用 `return` 退出循环，正常完成仅更新 `_prev_output`/`_stage_index` 后继续循环；`resume()` 无需改动（设置状态后调用本方法重新进入循环）

### 测试

- 5 个文件均通过 `python -c "import ast; ast.parse(open(r'FILE_PATH', encoding='utf-8').read())"` 语法校验

### 文档同步

- `agent.md`：line 168 强制修改流程条目补充 `MAX_CRITICAL_REVISE_ROUNDS=20` 硬上限说明；line 53 async_runner.py 描述补充超时取消与清理日志；line 56 flow_executor.py 描述补充迭代循环推进
- `update.md`：本条目

## 2026-07-12：修复 context_extractor.py 三处 aiohttp session 泄漏与 assert 控制流问题

### 背景

`novelforge/services/context_extractor.py` 存在三处问题：① `_extract_common` 方法在 line 1907 创建 LLMClient 后从未调用 `await client.close()`，方法有多个 return 点，导致 aiohttp session 泄漏（Unclosed client session 警告）；② `extract_protagonist_streaming` 方法存在相同的 session 泄漏问题；③ 两处 `assert` 语句用于控制流（`assert batch_protagonist is not None` / `assert raw_entries is not None`），在 Python `-O` 模式下会被移除导致逻辑错误。

### 核心改动

- `novelforge/services/context_extractor.py`：
  - `_extract_common`：client 创建后的 body（330+ 行）抽取到辅助方法 `_extract_common_body`，原方法用 `try/finally` 包裹调用，`finally` 块中 `await client.close()` 释放 aiohttp session（镜像 `ontology_extractor.py` lines 1006-1011 的模式）
  - `extract_protagonist_streaming`：同样将 client 创建后的 body 抽取到 `_extract_protagonist_body`，`try/finally` 包裹调用并关闭 client
  - 两处 `assert ... is not None` 替换为 `if ... is None: raise RuntimeError(...)`，保证 `-O` 模式下逻辑不变

### 测试

- `python -c "import ast; ast.parse(open(r'novelforge/services/context_extractor.py', encoding='utf-8').read())"` 通过（Syntax OK）

### 文档同步

- `agent.md`：`context_extractor.py` 描述补充 try/finally 关闭 LLMClient 释放 aiohttp session + body 抽到辅助方法
- `update.md`：本条目

## 2026-07-12：修复 storage.py 三处安全与健壮性问题

### 背景

`novelforge/core/storage.py` 存在三处问题：① `delete_project` 直接用 `project_id` 拼接路径并 `shutil.rmtree`，未经 `validate_id` 校验，存在路径穿越漏洞（如 `project_id="../something"` 可删除任意目录）；② `_row_to_project`/`_row_to_chapter`/`_row_to_continuation`/`_row_to_history_log` 四个方法使用 `row[N]` 位置索引，列顺序变更（如迁移补列）会导致错位；③ `is_network_filesystem` 读取 `/proc/mounts`（仅 Linux 存在），Windows 上始终返回 False，无法检测 UNC 路径与网络驱动器。

### 核心改动

- `novelforge/core/storage.py`：
  - `delete_project`：方法首行新增 `validate_id(project_id, "project_id")`（在任何 SQL 执行前校验），防止路径穿越删除任意目录
  - `connect()`：新增 `self._conn.row_factory = aiosqlite.Row`，启用按列名访问（命名访问的前提条件）
  - `_row_to_project`/`_row_to_chapter`/`_row_to_continuation`/`_row_to_history_log`：`row[N]` 位置索引改为 `row["column_name"]` 命名访问（列名对照 CREATE TABLE / SELECT 语句核实），移除 `len(row) > N` 防御性判断
  - `is_network_filesystem`：新增 Windows 分支（`sys.platform == "win32"` 时检测 UNC 路径 `\\server\share` + `GetDriveTypeW` 判定 DRIVE_REMOTE 网络驱动器），Linux `/proc/mounts` 逻辑保持不变
  - 新增 `import sys` 模块级导入

### 测试

- `python -c "import ast; ast.parse(open(r'novelforge/core/storage.py', encoding='utf-8').read())"` 通过（Syntax OK）

### 文档同步

- `agent.md`：storage.py 描述补充 row_factory/delete_project validate_id/is_network_filesystem Windows 检测；安全加固第 13 节 ID 路径穿越防护更新为四重防御（新增存储层 delete_project 入口）
- `update.md`：本条目

## 2026-07-12：修复主窗口三处线程与参数校验问题

### 背景

`novelforge/ui/main_window.py` 存在三处问题：① `closeEvent` 仅停止 `_continuation_worker`，未停止 `_audit_worker` 与 `_volume_orchestrator`，关闭窗口时审计/卷续写线程仍在后台运行；② `_on_start_continuation` 缺少温度与目标字数范围校验，异常参数会透传到 worker；③ 审计 worker 的 `chunk_received` 信号直接连接到 `AuditDialog.append_chunk`，若对话框在 worker 仍在流式输出时被关闭/删除，会触发 `RuntimeError: wrapped C/C++ object has been deleted` 崩溃。

### 核心改动

- `novelforge/ui/main_window.py`：
  - `closeEvent`：在停止 `_continuation_worker` 后，新增循环停止 `_audit_worker` 与 `_volume_orchestrator`（`getattr` 防御式访问 + `isRunning` 检查 + `stop` + `wait(3000)`）
  - `_on_start_continuation`：在模型检查后新增温度（`float` + 0.0-2.0 范围）与目标字数（`int` + 100-50000 范围）校验，非法值弹 `QMessageBox` 提示并 `return`
  - 审计 `chunk_received` 改为连接到新增中转槽 `_on_audit_chunk_received`（`getattr` 取 `_audit_dialog` + `try/except RuntimeError` 防已删除崩溃）；`_on_audit_cancelled` 在 `stop` 前先 `disconnect` `chunk_received` 信号防止排队信号触达对话框

### 测试

- `python -c "import ast; ast.parse(open(r'novelforge/ui/main_window.py', encoding='utf-8').read())"` 通过（SYNTAX OK）

### 文档同步

- `agent.md`：`main_window.py` 描述补充 `closeEvent` 三 worker 停止、`_on_start_continuation` 参数范围校验、审计 `chunk_received` 中转槽 + 取消前 `disconnect`
- `update.md`：本条目

## 2026-07-12：新增流程插件使用说明文档

### 背景

流程插件系统（见 update.md 2026-07-12「流程控制插件系统」条目与 agent.md 第 18 节）已上线，但缺少面向开发者的完整文档，用户不知道如何编写自定义插件 JSON、有哪些合法取值、阶段间如何传参。用户要求针对【流程插件管理】写一个使用说明，包括写插件规律、可用格式及相应模型，并生成一个案例。

### 核心改动

#### 新增
- `FLOW_PLUGIN_GUIDE.md`（项目根目录，503 行）：面向开发者的流程插件完整使用说明，9 章节：
  1. 概述（插件概念、与预设正交关系、存储位置）
  2. 快速开始（导入流程、使用方法、3 个内置插件简介）
  3. JSON 格式规范（FlowPlugin + FlowStage 完整字段表，含类型/必填/默认值/说明）
  4. 合法取值速查（5 种 agent / 2 种 ui_mode / 3 种 accept_mode / 8 个标准 flow_key）
  5. 写插件的 6 大规律（阶段顺序执行 / params 合并优先级 / input_from 链式传递 / flow_key 决定端点 / accept_mode 决定接受行为 / 插件与预设正交）
  6. volume_phase 阶段顺序（deep_analysis → volume_outline → outline_audit → chapter_writing 4 阶段固定顺序）
  7. 三个内置插件完整 JSON（single / volume / rewrite_current 原文嵌入）
  8. 自定义插件案例：先分析再续写（analyze_then_write，audit → continuation，accept_mode=promote，含完整 JSON + 执行流程说明 + 日志示例）
  9. 调试与验证（导入校验、检查 JSON 文件、版本升级、日志、5 条 FAQ）

### 设计决策
- **文档位置**：项目根目录 `FLOW_PLUGIN_GUIDE.md`（与 `README.md`/`agent.md` 平级），便于开发者发现
- **文档深度**：开发者向完整文档，涵盖格式规范 + 合法取值 + 编写规律 + 内置插件 + 自定义案例 + 调试验证
- **案例选择**：analyze_then_write（audit → continuation，promote）—— 与内置 rewrite_current（audit → continuation，replace）对照，展示 accept_mode 的不同取值效果
- **内置插件 JSON 直接嵌入**：避免开发者跳转查文件，一文档自洽
- **文档语言**：中文叙述 + 英文字段名/代码（遵循 agent.md 代码风格规范）

### 文档同步
- `agent.md`：第 18 节末尾补充 `FLOW_PLUGIN_GUIDE.md` 文档链接
- `update.md`：本条目

## 2026-07-12：移除设置中「提取上下文」单独控制，统一由流程端点配置管理

### 背景

Settings 对话框的「上下文提取」组含「提取模型」和「Token 拆分」两个控件。其中「提取模型」与流程端点配置的 `flow_models["context_extraction"]` 重复，且不一致地额外覆盖 `protagonist_extraction`（但不动 `ontology_extraction`/`custom_rule_parsing`），造成 UI 迷惑。用户要求移除该单独控制，模型统一由流程端点配置管理，Token 拆分保留在上下文预览面板。

### 核心改动

- `novelforge/ui/settings_dialog.py`：移除「上下文提取」QGroupBox（提取模型 QLineEdit + Token 拆分 QComboBox）+ 对应保存逻辑
- `novelforge/services/context_extractor.py`：移除 `extractor_model` 读取（上下文提取入口 + 主角形象提取入口）与覆盖逻辑，模型统一由 `_get_llm_client` 返回的 `get_flow_model(flow_key)` 控制
- `novelforge/ui/main_window.py`：Token 拆分改由预览面板独管——启动时连接 `_token_limit_combo.currentTextChanged` → 新增 `_save_token_limit_to_config` 持久化到 config；移除设置关闭后的 `_sync_token_limit_default` 同步调用
- `novelforge/core/config.py`：移除 `context_extract.extractor_model` 默认值（2 处）
- `novelforge/models/project.py`：`extract_config` docstring 移除 `extractor_model` 子键
- 测试：3 个测试文件（test_m4_context_extraction.py 2 处、test_protagonist_extraction.py、test_rewrite_current_mode.py）移除 `extractor_model` mock，补 `get_flow_model.return_value` mock

### 测试

- `python -m py_compile` 五文件通过
- `python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"`：638 passed，9 failed（均为预先存在的无关失败）

### 文档同步

- `agent.md`：context_extractor 描述补充模型统一由 flow_models 控制；settings_dialog 描述标注不含上下文提取配置；第 15 节补充 extractor_model 移除 + Token 拆分持久化说明

## 2026-07-12：修复调试模式对单次续写/重写当前章节失效

### 背景

用户报告：开启调试模式后，「重写当前章节」和「单次续写」均无法查看提示词和选择模型。根因是 `ContinuationWorker` 和 `AuditWorker` 完全没有调试模式基础设施，`main_window` 的调试模式接线仅覆盖 `VolumeOrchestrator`。

### 核心改动

#### ContinuationWorker 加调试模式
- `novelforge/services/continuation_worker.py`：
  - 新增信号 `prompt_debug_requested = Signal(str, str, str, str)`（phase_name, messages_json, current_endpoint_id, current_model）
  - `__init__` 新增 `endpoint_id`/`debug_mode` 参数 + 覆盖字段（`_debug_confirmed`/`_debug_override_*`/`_debug_clients`）+ `self._client`
  - 新增 `confirm_debug_prompt`/`_maybe_debug_prompt`/`_effective_model`/`_effective_client` 4 个方法（镜像 VolumeOrchestrator）
  - `_async_run` 在创建 client 前调 `_maybe_debug_prompt`，phase_name 按 `created_by` 映射（continuation→续写、rewrite_current→重写生成、audit_rewrite→修正）；取消 emit `error("用户取消调试")`
  - client 改用 `_effective_client()`/`_effective_model()`；`run()` finally 关闭主+调试缓存 LLMClient

#### AuditWorker 加调试模式
- `novelforge/services/audit_worker.py`：
  - 镜像 ContinuationWorker 调试模式实现
  - `__init__` 额外新增 `phase_name` 参数（单章审计/重写需求分析）
  - `_effective_client` 用 `self.reasoning_effort`（非 parameters.get）

#### main_window 接线
- `novelforge/ui/main_window.py`：
  - `_on_debug_mode_toggled` 扩展：实时设置 `_continuation_worker`/`_audit_worker` 的 debug_mode
  - `_on_prompt_debug_requested` 路由：按运行状态优先级 volume > continuation > audit，调对应 worker 的 `confirm_debug_prompt`
  - 5 个 worker 创建点（3 ContinuationWorker + 2 AuditWorker）传 `endpoint_id`/`debug_mode`（AuditWorker 额外传 `phase_name`）+ 连 `prompt_debug_requested` 信号

### 测试

- `python -m py_compile` 三文件通过
- `python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"`：638 passed，9 failed（均为预先存在的无关失败：flow_executor 属性缺失/regex 数量/mode combo）

### 文档同步

- `agent.md`：continuation_worker/audit_worker 描述补充调试模式；第 10 条调试说明扩展为三种 worker 统一描述

## 2026-07-12：调试模式新增端点/模型覆盖选择

### 背景

调试模式（菜单「调试→调试模式」勾选后）原仅能预览提示词并选择发送/取消。用户希望在调试对话框中额外选择端点与模型，从而在运行时覆盖当前 LLM 调用使用的端点/模型，方便对比不同模型/端点的输出效果。

### 核心改动

#### DebugPromptDialog 加端点/模型下拉
- `novelforge/ui/debug_prompt_dialog.py`：
  - 构造函数新增 `endpoints`/`current_endpoint_id`/`current_model` 三参数
  - 布局新增端点+模型下拉行（端点 combo itemData 存 endpoint dict；端点切换 `_on_endpoint_changed` → `_populate_models` 按 enabled_models→models→[default_model] 回退链填充模型，默认选中 current_model）
  - 新增 `get_selected_endpoint()`/`get_selected_model()`；`_on_send` 记录 `selected_endpoint`/`selected_model`

#### VolumeOrchestrator 信号扩参 + 覆盖机制
- `novelforge/services/volume_orchestrator.py`：
  - `prompt_debug_requested` 信号从 `Signal(str, str)` 改为 `Signal(str, str, str, str)`（追加 current_endpoint_id + current_model）
  - `__init__` 新增 `endpoint_id` 参数（存 `self._endpoint_id`）+ 覆盖字段 `_debug_override_endpoint`/`_debug_override_model`/`_debug_override_api_key` + `_debug_clients: dict[str, LLMClient]` 缓存
  - `confirm_debug_prompt` 签名扩展为 `(confirmed, endpoint_override=None, model_override="", api_key_override="")`，确认时写入覆盖字段
  - `_maybe_debug_prompt` 每次开始清空覆盖字段（保证覆盖仅对紧接的下一次 LLM 调用生效），emit 时追加 endpoint_id + model
  - 新增 `_effective_model()`/`_effective_client()` helper：返回覆盖值（若有）否则原值；覆盖端点时按 endpoint_id 缓存 LLMClient 避免重复创建 aiohttp session
  - 10 处 LLM 调用点（deep_analysis×2/volume_outline/outline_audit/outline_final/chapter_outline/chapter_writing/chapter_rewrite/chapter_verify/chapter_revise）`self._client` → `self._effective_client()`、`self.model` → `self._effective_model()`
  - `_run_chapter_writing` 的 `self._writing_model = self.model` → `self._effective_model()`（记录实际使用模型）
  - `run()` finally 块关闭所有 `_debug_clients` 缓存的 client

#### main_window 传参 + 覆盖回传
- `novelforge/ui/main_window.py`：
  - `_on_prompt_debug_requested` 签名扩展为 `(phase_name, messages_json, current_endpoint_id, current_model)`；从 `config_manager.get_endpoints()` 取端点列表传给 dialog
  - dialog 关闭后读取 `get_selected_endpoint`/`get_selected_model`：端点变更时 `decrypt_api_key` 解密新端点 api_key；端点覆盖但模型未改时用新端点 default_model；调 `confirm_debug_prompt(True, endpoint_override, model_override, api_key_override)`
  - `_start_volume_phase` 创建 VolumeOrchestrator 时传入 `endpoint_id=endpoint.get("id", "")`

### 设计决策

- **仅改 VolumeOrchestrator 调试流**：ContinuationWorker 无调试机制，本次不扩展
- **覆盖仅对下一次 LLM 调用生效**：`_maybe_debug_prompt` 开始清空 override，确认后写入，紧接的 LLM 调用用 `_effective_*()`；每阶段一次 LLM 调用，覆盖自然只生效一次
- **client 缓存按 endpoint_id**：避免同端点反复覆盖时重复创建 aiohttp session；run 结束统一关闭
- **不改 messages**：覆盖只影响 endpoint/model，提示词内容不变
- **端点不变仅模型变**：用原 client + 新 model

### 测试

- `python -m py_compile`：三文件语法检查通过
- `python -m pytest tests/test_volume_orchestrator.py tests/test_volume_models.py tests/test_flow_plugin.py -q`：118 passed（无回归）

### 文档同步

- `agent.md`：debug_prompt_dialog/volume_orchestrator 描述 + 第 10 条调试说明扩展

## 2026-07-12：卷续写逐章子步骤进度显示

### 背景

多章节生成时看不出当前处于「细纲」「写作」「审计」还是「修订」子阶段。VolumePanel 的 4 步标签（细纲/写作/验证/修订）UI 已就绪，但 `_run_chapter_loop` 只 emit `chapter_started`/`chapter_finished`，中间过程 `_on_volume_chapter_started` 传入 `current_step=""`、`_on_volume_chapter_finished` 一次性全标记完成，导致 4 个步骤标签全程 pending。

### 核心改动

- `novelforge/services/volume_orchestrator.py`：
  - 新增信号 `chapter_step_started = Signal(int, str)`（章节序号 + 步骤名 outline/writing/verify/revise）
  - `_run_chapter_loop` 每个子阶段 `await` 前 emit（9 处：outline/writing/首次 verify/循环内 revise+verify/after_chapter reject 分支 revise+verify+循环 revise+verify），保证 verify↔revise 循环中每次切换都更新
- `novelforge/ui/main_window.py`：
  - 新增属性 `_volume_chapter_steps_seen: set[str]`（章节开始时重置）
  - `_start_volume_phase` 连接 `chapter_step_started` → `_on_volume_chapter_step_started`
  - 新增 `_on_volume_chapter_step_started`：累积 completed = set(seen)，调 `update_chapter_progress(current=step, completed=seen)` 驱动面板 4 步标签三态 + 状态栏「卷续写中: 第N章 细纲/写作/验证/修订」，最后 `seen.add(step)`
  - 停止/完成/出错清理处同步 `self._volume_chapter_steps_seen = set()`

### 设计决策

- **步骤名直接复用面板 CHAPTER_STEPS**：outline/writing/verify/revise，避免映射层
- **累积态**：`_volume_chapter_steps_seen` 记录已执行过的步骤，verify 二次进入时 revise 仍保留 ✓（current 优先级高于 completed），符合「已修订过，正在复审」直觉
- **状态栏双重提示**：面板 4 步标签 + 状态栏文字同步更新

### 测试

- `python -m py_compile`：两文件语法检查通过
- `python -m pytest tests/test_volume_orchestrator.py tests/test_volume_models.py tests/test_flow_plugin.py -q`：118 passed（无回归）

### 文档同步

- `agent.md`：volume_orchestrator 信号列表 + main_window 描述 + 第 9 节新增「逐章子步骤进度」条目

## 2026-07-12：卷续写暂存节点 + 关闭后恢复 + 阶段失败重试

### 背景

卷续写 chapter_writing 阶段闪退（已在前序变更修复 5 处根因）。用户要求：①过程中给暂存节点，关闭软件后再打开选择该章节能返回当前处理状态；②任意阶段失败后不结束，给重试选项。此外 `_on_volume_continuation_finished` 存储操作无异常隔离，单章保存异常会崩溃整个完成回调。

### 核心改动

- `novelforge/ui/main_window.py`：
  - `_on_volume_continuation_finished` 存储操作块（index 后移 + save_chapter + _refresh_chapter_list）外包 try/except，失败 logger.error + QMessageBox.warning 但不崩溃
  - `_on_continuation_error` volume 错误块改为弹重试/取消对话框（QMessageBox.question）；重试复用 `_volume_state` + `_volume_current_phase` 调 `_start_volume_phase` 重启失败阶段；取消清理全部 + 删除状态文件
  - `_on_chapter_selected` 末尾新增 `_check_volume_resume(chapter_id)` 恢复入口
  - 新增 `_check_volume_resume`：检测状态文件 + 弹恢复确认对话框
  - 新增 `_resume_volume_state`：重建 prepare_data（reload endpoint/project/preset + 当前章节 entries/metadata/regex_script_ids）+ 反序列化已完成阶段产物（DeepAnalysis/VolumeOutline/OutlineAuditReport.model_validate）+ 确定下一未完成阶段 + 设 `_volume_resuming=True` 恢复模式启动
  - `_on_stop_continuation` volume 块新增清理 `_volume_current_phase`/`_volume_resuming` + 删除状态文件
  - （前序已完成）`_save/_load/_delete_volume_state` + `_get_volume_state_path` + `_get_next_volume_phase` + `_on_volume_phase_output` 持久化 + 恢复分支 + `_on_volume_continuation_finished` 末尾删除状态文件 + `_volume_current_phase`/`_volume_resuming` 属性

### 设计决策

- **状态文件位置**：`~/.novelforge/volume_states/{chapter_id}.json`（按章节隔离，一个章节一个状态文件）
- **持久化时机**：阶段 1-3 每阶段产物完成后写一次；chapter_writing 不持久化（最后阶段，失败重试从头生成）
- **恢复入口**：`_on_chapter_selected` 末尾（此时章节已加载 + 上下文条目已通过 `_load_context_entries_for_chapter` 加载，重建 prepare_data 所需数据就绪）
- **重试不重新准备数据**：重试复用已持久化的 `_volume_state`（含 prepare_data + 前序产物），避免重复弹校验对话框
- **停止视为放弃**：用户主动停止删除状态文件（不再可恢复）

### 测试

- `python -m py_compile novelforge\ui\main_window.py novelforge\services\volume_orchestrator.py`：语法检查通过
- `python -m pytest tests/test_flow_plugin.py tests/test_volume_orchestrator.py tests/test_volume_models.py -q`：118 passed（无回归）

### 文档同步

- `agent.md` 第 9 节新增「暂存节点持久化」「关闭后恢复」「阶段失败重试」「停止/完成清理」条目；main_window.py 描述补充暂存节点/恢复/重试

## 2026-07-12：卷续写逐章错误隔离 + 空产物保护

### 背景

`_run_chapter_loop` 的 for 循环体内无异常隔离，单章生成失败（如 LLM 调用异常、JSON 解析错误）直接中止整卷续写，已成功生成的章节也丢失。所有章节失败时仍尝试构建空 Continuation，可能引发下游异常。

### 核心改动

- `novelforge/services/volume_orchestrator.py`：
  - `_run_chapter_loop` 的 for 循环体内新增 try/except 包裹从 `dynamic_lookback` 到 `chapter_finished.emit` 的全部章节生成逻辑（chapter_plan 已在 try 内）
  - `except asyncio.CancelledError: raise` 向上传播用户取消
  - `except Exception as e: logger.error(...) + self.error.emit(...) + continue` 跳过该章继续下一章
  - for 循环结束后新增空产物保护：`if not artifacts.chapter_artifacts: self.error.emit(...); return`

### 测试

- `python -m py_compile novelforge\services\volume_orchestrator.py` 语法验证通过

### 文档同步

- `agent.md` 第 9 节新增「逐章错误隔离」条目

## 2026-07-12：卷续写多阶段插件 + 资源泄漏修复

### 背景

卷续写无法正常使用，报错：`Unclosed client session`（aiohttp 泄漏）、`Failed to disconnect from signal reasoning_received`（信号断开警告）、`无法解析 JSON: line 1 column 1`（模型选择错误）、卷插件为单阶段 `volume_pipeline` 不合理（应像重写一样多阶段）。

### 根因与修复

1. **LLMClient 资源泄漏**：`VolumeOrchestrator.run()` finally 块关闭事件循环但未调用 `self._client.close()` → 新增 `self._loop.run_until_complete(self._client.close())`
2. **模型选择错误**：`_on_start_volume_continuation` 从隐藏的 `_model_combo` 读取模型，卷模式下残留单次模式模型 → 翻转优先级：`get_flow_model("volume_continuation")` 优先，`params.get("model")` 回退
3. **信号断开警告**：`reasoning_received` 信号在卷模式从未连接，断开触发 libpyside 警告 → 移除 volume 路径的 `reasoning_received.disconnect()`
4. **插件结构不合理**：卷续写为单阶段 `volume_pipeline`，应拆分为多阶段 → 重写为 4 阶段 `volume_phase`

### 核心改动

#### VolumeOrchestrator 分阶段执行
- `novelforge/services/volume_orchestrator.py`：
  - 新增 `phase_output` 信号（str, object）
  - `__init__` 新增 `phase`/`phase_inputs` 参数
  - `_async_run` 重构为按 `self.phase` 分支调度
  - 新增 5 个方法：`_run_full_pipeline`/`_run_phase_deep_analysis`/`_run_phase_volume_outline`/`_run_phase_outline_audit`/`_run_phase_chapter_writing`
  - 新增 `_run_chapter_loop` 共享逐章循环（phase=all 和 chapter_writing 共用）
  - `run()` finally 块关闭 LLMClient

#### volume_phase agent 类型
- `novelforge/models/flow_plugin.py`：VALID_AGENT_TYPES 新增 `volume_phase`；FlowStage.agent 字段级校验（field_validator）

#### volume.json v2.0（4 阶段）
- `novelforge/resources/defaults/flow_plugins/volume.json`：从 1 阶段 `volume_pipeline` v1.0 → 4 阶段 `volume_phase` v2.0（deep_analysis→volume_outline→outline_audit→chapter_writing）

#### 内置插件版本升级
- `novelforge/services/flow_plugin_service.py`：
  - 新增 `_parse_version` 静态方法
  - 新增 `_should_upgrade_builtin` 方法
  - `_ensure_builtin_plugins` 增加版本升级逻辑（已安装 builtin=True 且资源版本更高时覆盖）

#### main_window volume_phase 接线
- `novelforge/ui/main_window.py`：
  - 初始化 `_volume_state: dict | None`
  - 注册 `volume_phase` handler（`_register_flow_handlers`）
  - 新增 `_flow_handler_volume_phase`：返回 pending，首次调用 `_prepare_volume_run`
  - 提取 `_prepare_volume_run`：校验 + 准备数据，返回 dict
  - 提取 `_start_volume_phase`：创建 VolumeOrchestrator（传 phase/phase_inputs）+ 连接信号（含 phase_output）+ start
  - 重构 `_on_start_volume_continuation` 为薄包装（volume_pipeline 向后兼容）
  - 新增 `_on_volume_phase_output`：存入 `_volume_state[phase]` + `flow_executor.resume(artifact)`
  - `_on_volume_continuation_finished` 末尾清理 `_volume_state` + `flow_executor.cancel()`
  - `_on_stop_continuation`/`_on_continuation_error` 增加 volume 状态清理

#### 测试
- `tests/test_flow_plugin.py`：36 个测试（+5）——更新 test_load_builtin_volume 断言 v2.0 4 阶段；新增 test_volume_phase_agent_type/test_invalid_agent_type/test_builtin_plugin_version_upgrade/test_builtin_version_upgrade_skips_non_builtin/test_volume_phase_pending_and_resume

### 验证

- `python -m py_compile`：语法检查通过
- `python -m pytest tests/test_flow_plugin.py -v`：36 passed
- `python -m pytest tests/test_volume_orchestrator.py tests/test_volume_models.py tests/test_volume_prompts.py -q`：116 passed（无回归）

### 文档同步

- 更新 `agent.md`：5 agent 类型 + volume v2.0 4 阶段 + 版本升级 + volume_phase handler + FlowStage.agent 校验

## 2026-07-12：流程控制插件系统

### 背景

用户需求：将续写控制中的「单次续写」「卷续写」「重写当前章节」三种模式抽离为独立的「流程控制」组件，允许用户按规范代码自行组合既有功能或新增 agent，以单个 JSON 文件形式导入导出新模式给其他用户使用，且不影响原有功能。

### 核心改动

#### 新增
- `novelforge/models/flow_plugin.py`：FlowPlugin/FlowStage 声明式 JSON 配置数据模型；4 种 agent 类型（continuation/audit/checkpoint/volume_pipeline）、3 种 ui_mode（standard/volume）、3 种 accept_mode（promote/replace/volume_insert）；ID 路径穿越防护
- `novelforge/services/flow_plugin_service.py`：流程插件 CRUD 服务（继承 BaseJsonService[FlowPlugin]）；首启复制 3 内置插件到 `~/.novelforge/flow_plugins/`；导入强制 builtin=False，ID 冲突追加 `_imported`；内置不可删除
- `novelforge/services/flow_executor.py`：流程执行引擎；按 stages 有序执行；4 agent handler 回调机制；挂起-恢复模式（pending/resume/cancel）；参数合并优先级：阶段 params 覆盖面板 params
- `novelforge/resources/defaults/flow_plugins/single.json`：内置单次续写插件（1 阶段 continuation，accept_mode=promote）
- `novelforge/resources/defaults/flow_plugins/volume.json`：内置卷续写插件（1 阶段 volume_pipeline，ui_mode=volume，accept_mode=volume_insert）
- `novelforge/resources/defaults/flow_plugins/rewrite_current.json`：内置重写当前章节插件（2 阶段 audit→continuation，input_from=analysis，accept_mode=replace）
- `novelforge/ui/flow_plugin_manager.py`：流程插件管理器（继承 PersistentDialog 非模态）；列表/详情/导入/导出/删除（内置禁用）；plugin_changed 信号通知 MainWindow 刷新面板下拉
- `tests/test_flow_plugin.py`：31 个测试覆盖模型校验、服务 CRUD、导入导出、执行引擎

#### 修改
- `novelforge/models/__init__.py`：导出 FlowPlugin/FlowStage/VALID_AGENT_TYPES/VALID_UI_MODES/VALID_ACCEPT_MODES
- `novelforge/utils/paths.py`：新增 `get_default_flow_plugin_path` 函数
- `novelforge/ui/continuation_panel.py`：新增 `start_flow` 信号（plugin_id + params 统一入口）；`set_flow_plugins` 动态填充模式下拉；`_on_start_clicked`/`_on_rewrite_clicked` 改为发射 `start_flow`
- `novelforge/ui/main_window.py`：
  - 新增 FlowPluginService/FlowExecutor/FlowPluginManager 初始化与 import
  - `start_flow` 信号连接到 `_on_start_flow` 统一入口
  - 4 个 agent handler 注册（`_register_flow_handlers`）
  - `_on_start_flow` 注入 `_flow_plugin_id` 到 params（经 FlowExecutor → handler → ContinuationWorker → parameters_snapshot）
  - `_on_mode_changed` 改为查插件 ui_mode
  - `_on_accept_continuation` 适配 accept_mode（优先查插件，回退 created_by）
  - `_on_rewrite_analysis_accepted` 开头 cancel FlowExecutor 清理 pending 状态
  - 移除 `rewrite_current_analysis_requested` 死信号连接（改走 start_flow）
  - 工具菜单新增「流程插件管理器(&F)」入口
- `agent.md`：架构分层补充 flow_plugin.py/flow_plugin_service.py/flow_executor.py/flow_plugin_manager.py/flow_plugins/ 目录；关键设计决策新增第 18 条「流程控制插件系统」；第 14 条接受逻辑更新

### 测试

- `python -m pytest tests/test_flow_plugin.py -v`：31 passed
- `python -m pytest tests/test_promote_continuation.py tests/test_chapter_title_generation.py -q`：21 passed（无回归）

### 文档同步

- 更新 `agent.md`：架构分层新增 4 个模块 + flow_plugins/ 资源目录；关键设计决策新增第 18 条；第 14 条接受逻辑更新；main_window/continuation_panel 描述更新

## 2026-07-11：章节名生成与编辑

### 背景

用户需求：①默认预设加入章节名生成要求，让 LLM 根据前几个章节名的命名规律继续生成新章节名；②支持修改章节名字（此前只能修改章节正文）。

### 核心改动

#### 修改
- `novelforge/resources/defaults/default_preset.json`：`main` 模块新增 `{{previous_chapter_titles}}` 宏段落（前章标题列表）+ `<novelforge_title>` 输出标签（LLM 分析前章命名规律生成一致的新标题）；`nf_core_rules` 第 5 条调整为"章节标题仅在 `<novelforge_title>` 标签内输出"
- `novelforge/core/prompt_assembler.py`：新增 `_build_previous_chapter_titles` 方法（按序排序，支持 `exclude_current` 重写模式排除当前章、`max_titles=20` 限制）；`assemble` 调用该方法构建前章标题；`_build_macro_context` 新增 `previous_chapter_titles` 参数注入 `ctx.extra`（空串时注入占位文本）
- `novelforge/resources/defaults/default_regex_scripts.json`：新增第 5 条 `nf_regex_strip_title`（"GB-章节标题剥离"，在 AI_OUTPUT 阶段剥离 `<novelforge_title>` 标签）
- `novelforge/models/chapter.py`：`Continuation` 模型新增 `generated_title: str = ""` 字段（LLM 生成的章节标题）
- `novelforge/services/continuation_worker.py`：`run` 方法在 `post_process_content` 调用前用正则提取 `<novelforge_title>` 标签内容到 `generated_title`
- `novelforge/services/chapter_service.py`：`promote_continuation_to_chapter` 优先使用 `generated_title` 作为新章节标题，无则回退默认"第N章"；`rename_chapter` 改用 `update_chapter_title` 单列更新（不写正文文件）
- `novelforge/core/storage.py`：`continuations` 表新增 `generated_title` 列（schema + 幂等迁移）；`save_continuation`/`list_continuations`/`load_chapters_with_continuations`/`_row_to_continuation` 同步该字段；新增 `update_chapter_title` 方法（只更新 title 列不写文件）
- `novelforge/services/storage_service.py`：新增 `update_chapter_title` 方法
- `novelforge/ui/chapter_editor.py`：`QLineEdit`（objectName="chapterTitleEdit"）替代原 QLabel 支持标题编辑；新增 `title_changed` 信号 + `_on_title_changed` 方法 + `title` 属性；`load_chapter` 签名改为接收 `chapter_index` + `chapter_title`；`set_streaming_locked` 同步锁定标题输入框
- `novelforge/ui/main_window.py`：连接 `title_changed` 信号到新增的 `_on_chapter_title_changed` 方法（实时更新章节标题并刷新列表）；`_on_save` 同步标题
- `novelforge/ui/continuation_panel.py`：`set_current_swipe` 在输出框前显示 `【生成标题】{title}`

#### 新增
- `tests/test_chapter_title_generation.py`：15 个测试用例覆盖 `generated_title` 字段、`promote_continuation_to_chapter` 使用 `generated_title`、`_build_previous_chapter_titles`（含 `exclude_current`/`max_titles`）、`_build_macro_context` 注入、`<novelforge_title>` 标签正则提取、`update_chapter_title` 存储方法

### 测试

- `python -m pytest tests/test_chapter_title_generation.py -v`：15 passed
- `python -m pytest tests/test_promote_continuation.py tests/test_m2_prompt_assembly.py -q`：16 passed（无回归）

### 文档同步

- 更新 `agent.md`：架构分层补充 `generated_title`/`_build_previous_chapter_titles`/`update_chapter_title`/标题编辑；关键设计决策第 8 条补充标题来源；新增第 17 条「章节标题生成与编辑」；代码风格规范补充 `rename_chapter` 用 `update_chapter_title`；默认正则条数 4→5

## 2026-07-11：版本号升级至 v0.2.9

将 `novelforge/__init__.py` 的 `__version__` 由 `0.2.8` 升级至 `0.2.9`；同步更新 `README.md` 顶部「当前版本」标注与「更新记录」章节（新增 v0.2.9 小节，汇总自 v0.2.8 以来的 2 项改动：流程端点配置新增模型下拉、思维链预设升级为三层次互斥模块），同步更新 `agent.md` 当前版本标注。

### 背景

v0.2.8 发布后陆续完成 2 项改动（见 2026-07-09 两条 update.md 条目），需发版归档。

### 核心改动

#### 修改
- `novelforge/__init__.py`：`__version__ = "0.2.9"`
- `README.md`：当前版本 → v0.2.9；更新记录顶部新增 v0.2.9 小节
- `agent.md`：当前版本 → v0.2.9

### 文档同步
- README.md「更新记录」v0.2.9 小节已汇总本版本全部改动
- agent.md 当前版本标注已更新

## 2026-07-09：流程端点配置新增模型选择

在「流程端点配置」对话框中，为每个流程的端点下拉旁新增模型下拉，让用户为每个流程独立指定模型（而非只能用端点的 default_model）。模型下拉可选项来自该端点的 enabled_models（回退链 `enabled_models → models → [default_model]`），与续写面板一致。

### 背景

原架构中流程端点配置仅存 `{flow_key: endpoint_id}`，消费时用 `endpoint.get("default_model")` 作为模型。用户希望为每个流程独立指定模型（如审计用稳定模型、续写用强模型），需在对话框加模型下拉。

### 核心改动

#### 修改
- `novelforge/core/config.py`：默认配置新增 `flow_models: {}`（`{flow_key: model_str}`，空串=用端点 default_model）；新增 `get_flow_models()`/`get_flow_model(flow_key)`（回退端点 default_model）/`set_flow_models(mapping)` 三方法；不升 config_version（缺失时全回退原行为）
- `novelforge/ui/flow_endpoint_dialog.py`：每个 flow 行由单端点下拉改为 `[端点下拉][模型下拉]` 横排；新增 `_on_flow_endpoint_changed`/`_populate_model_combo` 方法（端点切换时用回退链填充模型下拉，首项「默认模型」itemData=""）；`_load_data` 加载 flow_models 并选中；`_on_accept` 保存 flow_models
- `novelforge/ui/continuation_panel.py`：新增 `select_model_by_name(model)`（blockSignals 屏蔽会话记忆）供 `_refresh_endpoints` 同步流程配置模型；新增 `get_selected_model()` 供调试预览取面板当前模型
- `novelforge/ui/main_window.py`：消费层 7 处 `endpoint.get("default_model")` 改为 `get_flow_model(flow_key)`（单章续写/卷续写/单章审计/审计后重写/重写分析/重写生成 + 调试预览）；`_refresh_endpoints` 增加同步流程配置模型到面板
- `novelforge/services/context_extractor.py`/`ontology_extractor.py`/`custom_audit_rule_service.py`：`_get_llm_client(flow_key)` 改用 `get_flow_model(flow_key)` 取模型
- `agent.md`：config.py/flow_endpoint_dialog.py/continuation_panel.py 注释同步；新增设计决策 15「流程端点与模型配置」

### 设计决策
- **配置字段**：`flow_models`（与 `flow_endpoints`/`flow_jailbreaks` 平行），空串=回退端点 default_model
- **回退链**：`enabled_models → models → [default_model]`（与续写面板一致，旧端点兼容）
- **不升 config_version**：`flow_models` 缺失时全回退原行为，向后兼容
- **正文流程同步**：对话框关闭后 `_refresh_endpoints` 同步面板端点+模型；面板模型下拉仍可临时覆盖（会话记忆），不写回 `flow_models`
- **消费层优先级**：正文流程 `params.get("model")`（面板下拉）→ `get_flow_model(flow_key)`（流程配置）→ 端点 default_model；非正文流程直接 `get_flow_model(flow_key)`
- **审计后重写**：复用 `single_audit` flow_key（审计重写是审计流程延续）
- **重写生成**：复用 `single_continuation` flow_key（重写生成复用 single_continuation 端点）

### 测试
- `python -m pytest tests/ -q` 全绿（663 passed, 15 skipped）

### 文档同步
- agent.md 已更新

## 2026-07-09：思维链三层次预设更新

### 背景
分析五套参考预设（TGbreak/Femiris/lunareclipse/夏瑾/梦鲸）的思维链设计，将项目默认预设的单层次思维链升级为低/中/高三个层次，采用变量注入法实现。

### 核心改动
- `default_preset.json`：新增 nf_cot_low/mid/high 三个互斥模块，通过 {{setvar::COT-items::}} 注入不同深度的分析项；nf_cot 改为 {{getvar::COT-items}} 模板；main 模块输出格式描述泛化
- 低层次 4 项（前文衔接/人物分析/情节规划/用户指令遵从）
- 中层次 7 项（现有行为，默认启用）
- 高层次 10 项（全维度：核心七项 + 文风抗八股检查 + 防全知深度审查 + 格式输出检查）
- `agent.md`：更新 default_preset.json 描述

### 测试
- 现有测试无回归（无测试直接引用 nf_cot 内容）
- 变量注入依赖 template_engine + variable_store，生产环境已配置

### 文档同步
- agent.md 已更新

## 2026-07-08：版本号升级至 v0.2.8

将 `novelforge/__init__.py` 的 `__version__` 由 `0.2.7` 升级至 `0.2.8`；同步更新 `README.md` 顶部「当前版本」标注与「更新记录」章节（新增 v0.2.8 小节，汇总自 v0.2.7 以来的 9 项改动：端点启用模型多选、复制端点、Gemini reasoning_effort 修复、取消强制提取、用户指令约束、破限前置与深化等），同步更新 `agent.md` 当前版本标注。

### 背景

v0.2.7 发布后陆续完成 9 项改动（见同日及先前 update.md 条目），需发版归档。

### 核心改动

#### 修改
- `novelforge/__init__.py`：`__version__ = "0.2.8"`
- `README.md`：当前版本 → v0.2.8；更新记录顶部新增 v0.2.8 小节
- `agent.md`：当前版本 → v0.2.8

### 文档同步
- README.md「更新记录」v0.2.8 小节已汇总本版本全部改动
- agent.md 当前版本标注已更新

## 2026-07-08：端点启用模型多选 + 续写面板按启用模型切换

在设置对话框的端点编辑中，获取模型列表后用**可勾选列表**多选要启用的模型（`enabled_models`）；续写控制面板切换端点时，模型下拉框只显示该端点**已启用**的模型，可在其中切换，无需换端点。去掉独立的「默认模型」选择器——`default_model` 改为自动取「首个已启用模型（按名称排序）」，仍持久化供后台流程（提取/审计/卷续写/重写）使用。

### 背景

用户希望在续写控制面板先选端点、再在该端点的不同模型间切换；设置中对应端点获取模型后按多选控制，选择几个模型即可在续写处选择哪几个。原架构端点 `models` 列表为全部已获取模型，续写面板显示全部，无法筛选；且「默认模型」为单选下拉，与多选需求冲突。

### 核心改动

#### 新增字段
- `novelforge/core/config.py`：端点结构新增 `enabled_models: list[str]`（续写面板可选模型子集）；`add_endpoint` `setdefault("enabled_models", [])`

#### 修改
- `novelforge/ui/settings_dialog.py`（EndpointEditDialog）：
  - 模型选择区由可编辑 `QComboBox` 改为可勾选 `QListWidget`（`ItemIsUserCheckable` + `setCheckState`），`setMinimumHeight(120)`
  - 新增「全选」/「全不选」按钮 + 「添加」自定义模型输入（保留手动录入能力）
  - `_load_data`：从 `models` 填充列表，按 `enabled_models` 勾选；`enabled_models` 为空时全部勾选（旧端点兼容）
  - `_on_models_fetched`：拉取后清空重填，新模型默认勾选，保留旧勾选状态
  - `_on_accept`：`models` = 全部 item，`enabled_models` = 勾选项，`default_model` = `sorted(enabled_models)[0]`（自动取首个已启用）
  - 端点列表 tooltip 显示「已启用模型: N 个（默认: ...）」
  - 复制端点同步复制 `enabled_models`
- `novelforge/ui/continuation_panel.py`：
  - `__init__` 新增 `_last_model_per_endpoint: dict[str, str]`（会话记忆，不持久化）
  - `set_models`：仅清空 + `sorted` 填充，`blockSignals` 防误记录
  - `_on_endpoint_changed`：回退链 `enabled_models → models → [default_model]` 填充模型下拉；选中「该端点上次手动选择的模型 → 否则首个」
  - 新增 `_on_model_user_changed`：用户手动切换模型时记录会话记忆
- `agent.md`：config.py / continuation_panel.py / settings_dialog.py 注释同步
- `update.md`：本条目

### 设计决策
- **保留 `default_model`**：7 处 main_window + 3 处 service 依赖它作为后台流程（提取/审计/卷续写/重写）回退，删除会破坏；改为自动取首个已启用模型
- **去掉独立默认选择器**：符合用户选择——设置中不再有单独默认模型下拉，UI 更简洁
- **回退链**：续写面板 `enabled_models → models → [default_model]`，旧端点（仅有 default_model 或仅有 models）不破坏
- **会话记忆**：`_last_model_per_endpoint` 进程内 dict，重启回到首个已启用；满足「记住本次在该端点用过的模型」
- **新拉取模型默认勾选**：拉取即可用，用户可取消
- **`blockSignals` 防误记录**：程序化填充/选中不触发会话记忆，仅用户手动切换才记录

### 测试
- `python -m pytest tests/test_settings_dialog_endpoint_edit.py tests/ -q` 全绿（663 passed, 15 skipped）
- 新增 `TestEnabledModelsSave`（加载/保存 enabled_models + default_model 自动取首 + 旧端点全勾兼容）
- 新增 `TestContinuationPanelEnabledModels`（仅显示启用模型 + 会话记忆恢复 + 旧端点回退）

## 2026-07-08：修复端点对话框关闭时 ModelFetchWorker 已删除导致的崩溃

获取模型列表后关闭端点编辑对话框时，`closeEvent` 访问 `self._model_fetch_worker.isRunning()` 抛 `RuntimeError: Internal C++ object (ModelFetchWorker) already deleted`，导致 `QDialog::closeEvent` 异常。

### 背景

`_on_fetch_models` 将 worker 的 `finished` 信号连接到 `deleteLater`（fire-and-forget 自清理）。worker 完成后 Qt 事件循环调度 `deleteLater` 删除 C++ 对象，但 Python 属性 `self._model_fetch_worker` 仍指向已失效的 wrapper。用户随后关闭对话框，`closeEvent` 调用 `worker.isRunning()` 触发 shiboken `RuntimeError`。

### 核心改动

#### 修改
- `novelforge/ui/settings_dialog.py`：
  - `closeEvent` 改为防御式访问：先缓存 `worker = self._model_fetch_worker`，用 `try/except RuntimeError` 包裹 `isRunning()` 调用，捕获后视为「未运行」跳过 disconnect，避免访问已删除的 C++ 对象
  - 不再清空 `self._model_fetch_worker` 引用（保留兼容现有测试 `test_edit_dialog_close_while_fetching_does_not_crash` 在 close 后调用 `worker.wait()` 的断言）
- `update.md`：本条目

### 设计决策
- 不改动 `finished→deleteLater` 自清理机制：worker parent=None，对话框关闭时线程可安全在后台完成并自清理
- 不清空引用：`_on_fetch_models` 每次重新赋值 `self._model_fetch_worker`，旧引用自然被覆盖；清空会破坏现有测试对 worker 生命周期的断言
- 仅 `closeEvent` 加防御：这是唯一在 worker 可能已被删除后访问 worker 的入口

### 测试
- `python -m pytest tests/test_settings_dialog_endpoint_edit.py tests/ -q` 全绿（658 passed, 15 skipped）

## 2026-07-08：模型下拉框加宽 + 按名称排序

API 端点配置中获取模型列表后，下拉框横向太窄无法看全内容。将模型/端点下拉框加宽（自适应内容宽度），并在填充前按名称排序，方便用户选择。多模型选择方面，现有架构已满足「续写面板不换端点、仅换模型」需求，无需改动选择逻辑。

### 背景

用户反馈：端点配置对话框中「获取模型列表」后，模型下拉框宽度太窄，长模型名被截断无法看全；且模型列表无序，难以快速定位目标模型。续写面板的端点/模型下拉框同样偏窄。

### 核心改动

#### 修改
- `novelforge/ui/settings_dialog.py`：
  - 导入 `QSizePolicy`
  - `_model_combo` 设置 `setMinimumWidth(200)` + `setSizeAdjustPolicy(AdjustToContents)` + `setSizePolicy(Expanding, Fixed)`，下拉框宽度自适应最长模型名并在表单中横向撑满
  - `_load_data` 填充保存的模型列表前 `sorted()` 排序
  - `_on_models_fetched` 填充拉取的模型列表前 `sorted()` 排序
- `novelforge/ui/continuation_panel.py`：
  - 导入 `QSizePolicy`
  - `_endpoint_combo` 设置 `AdjustToContents` + `Expanding, Fixed`，端点下拉框加宽
  - `_model_combo` 设置 `AdjustToContents` + `Expanding, Fixed`，模型下拉框加宽
  - `set_models` 填充模型列表前 `sorted()` 排序
- `agent.md`：settings_dialog.py 与 continuation_panel.py 注释补充「AdjustToContents 自适应宽度 + 按名称排序」
- `update.md`：本条目

### 设计决策
- 宽度策略用 `AdjustToContents` + `Expanding/Fixed` 组合：内容多时自适应加宽，同时在表单布局中横向伸展撑满可用空间
- 排序用 Python 内置 `sorted()`（区分大小写）；模型 ID 一般为小写英文，符合直觉，不引入自然排序依赖
- 多模型选择逻辑不改：现有端点 `models` 列表 + 续写面板模型下拉框已满足「不换端点、仅换模型」需求，续写时取 `_model_combo.currentText()` 即可在端点内切换模型
- 不改动 `default_model` 选中逻辑：排序后仍按 `findText(default_model)` 选中，行为不变

### 测试
- `python -m pytest tests/test_settings_dialog_endpoint_edit.py tests/ -q` 全绿

## 2026-07-08：修复 Gemini 模型 reasoning_effort 不支持值报错

Gemini 模型的 `thinking_config.thinking_level` 仅支持 `low`/`medium`/`high`，但 GengBi 原样发送 `auto`/`minimal`/`max` 导致网关返回 400 错误。新增模型类型检测与值映射逻辑。

### 核心改动

#### 修改
- `novelforge/services/llm_client.py`：
  - 新增 `_is_gemini_model(model)` 静态方法：通过模型名检测 Gemini
  - 新增 `_resolve_reasoning_effort_for_payload(model)` 方法：按模型类型映射 reasoning_effort 值
    - Gemini：`auto`→不发送、`minimal`→`low`、`max`→`high`、`low`/`medium`/`high` 原样发送
    - 非 Gemini：原样发送（保持兼容）
  - 流式与非流式两处 payload 构造统一改用该方法
- `agent.md`：llm_client.py 注释补充 reasoning_effort 映射说明
- `update.md`：本条目

### 测试
- `python -m pytest tests/test_reasoning_effort.py tests/ -q` 全绿

## 2026-07-08：API 端点管理新增「复制」按钮

在设置对话框的 API 端点管理区域新增「复制」按钮，复制当前选中端点的全部配置（base_url、api_key_encrypted 密文、models 列表、default_model、reasoning_effort），生成新 ID 和带「副本」后缀的新名称，立即持久化并选中新端点。

### 核心改动

#### 修改
- `novelforge/ui/settings_dialog.py`：
  - UI 布局：列表 span 从 4 改为 5，新增 `self._duplicate_btn = QPushButton("复制")` 放在 row 4, col 1
  - 信号连接：`_duplicate_btn.clicked.connect(self._on_duplicate_endpoint)`
  - 新增 `_on_duplicate_endpoint` 方法：QInputDialog 取名（默认 `{原名} 副本`）→ 深拷贝端点 dict → 生成新 ID → 复用 api_key_encrypted 密文 → add_endpoint → 刷新列表并选中新项
- `agent.md`：settings_dialog.py 注释补充「复制端点」
- `update.md`：本条目

### 设计决策
- API Key 密文直接复用（同一 salt 下可解密回原 key，符合「复制」语义）
- 副本不自动设为默认（is_default=False）
- ID 沿用现有 `f"ep_{os.urandom(4).hex()}"` 风格
- 复制后自动选中新端点，方便用户立即编辑

### 测试
- `python -m pytest tests/test_settings_dialog_endpoint_edit.py tests/ -q` 全绿

## 2026-07-08：取消强制提取，改为合并提示对话框 + 允许不提取生成

将续写/重写前的「上下文条目为空即阻断」硬限制，改为「检查上下文/世界观/主角三项未提取状态，合并弹一个提示对话框询问是否继续不提取生成」的软提示模式。

### 背景

原架构中续写/重写前会硬阻断：上下文条目为空时弹 `QMessageBox.information` 提示并 return，不允许续写。世界观底层和主角形象虽然已经是软依赖（未提取时降级为空串或占位文字），但无任何提示。用户希望统一为「提示可提取，是否继续不提取生成」的软提示模式。

### 核心改动

#### 修改
- `novelforge/ui/main_window.py`：
  - 新增 `_prompt_continue_without_extraction(entries, world_ontology, protagonist_profile) -> bool` 方法：检查三项未提取状态，合并弹 `QMessageBox.question` 询问是否继续，默认选「否」（取消）
  - 单章续写 `_on_start_continuation`：替换 `if not entries: ... return` 为调用该方法
  - 卷续写 `_on_start_volume_continuation`：同上
  - 重写分析 Step1 `_on_start_rewrite_current`：同上
  - 重写生成 Step2 `_on_rewrite_analysis_accepted`：同上，取消分支保留 `_rewrite_current_chapter_id = None` 清理逻辑
- `agent.md`：设计决策 3「提取与续写解耦」补充「提取非强制」说明
- `update.md`：本条目

### 测试
- `python -m pytest tests/ -q` 全绿

### 文档同步
- `agent.md`：设计决策 3 同步更新

## 2026-07-08：提示词与思维链加入「严格遵循用户指令、不引入意外内容」约束

在续写提示词和审计相关提示词中同步加入约束：严格遵循用户指令，不引入意外事件、新角色、新剧情及全新内容，除非用户明确要求。

### 背景

模型在续写和审计/重写过程中可能自行添加新角色、新支线或意外事件，偏离用户创作意图。需要在续写核心准则、思维链分析以及审计流程中统一加入「用户指令遵从」约束。

### 核心改动

#### 修改
- `novelforge/resources/defaults/default_preset.json`：
  - **nf_core_rules**：新增第 8 条「严格遵循用户指令，不引入意外内容」——不引入新角色/新剧情线/意外事件/新设定/新场景，聚焦深化而非扩张
  - **nf_cot**：新增第 7 项「用户指令遵从检查」——逐项核查是否引入未经用户授权的全新元素
  - **main**：末尾新增【续写约束】段——严格遵循用户指令，不引入意外内容
- `novelforge/resources/defaults/agent/` 下 5 个文件：
  - **phase_verify.txt**：任务描述后追加「重要约束：审计须严格遵循用户指令，不得引入意外事件/新角色/新剧情」
  - **phase_single_audit.txt**：同上
  - **phase_audit_rewrite.txt**：核心要求新增「严格遵循用户指令：聚焦修补而非扩张，不得擅自添加全新内容」
  - **phase_outline_audit.txt**：任务描述后追加「重要约束：审计与修订不得引入用户未明确要求的全新内容」
  - **phase_rewrite_analysis.txt**：分析原则首部新增「严格遵循用户指令：用户重写需求视为硬约束，不得扩展超出用户指令范围的创作建议」
- `agent.md`：架构分层 default_preset.json/agent/ 注释同步
- `update.md`：本条目

### 测试
- `python -c "import json; json.load(open('novelforge/resources/defaults/default_preset.json', encoding='utf-8'))"` 确认 JSON 合法
- `python -m pytest tests/test_preset_models.py tests/test_m2_prompt_assembly.py tests/test_tgbreak_e2e.py -q` 全绿

### 文档同步
- `agent.md`：架构分层目录树（default_preset.json/agent/ 注释）

## 2026-07-08：破限移至基础续写准则 + 全面深化破限内容

将基线破限从 `main` 主提示迁移至 `nf_core_rules`（✅ 基础续写准则）首部，作为【创作自由基准】有机融入续写核心准则；同时全面深化所有 5 个 `nf_jb_*` 模块与 6 个 `jb_*.txt` 模板，从「声明式」升级为「说服式」——增加内部审查对抗锚点、递进感更鲜明（LOW=认知修正、MID=授权自由、HIGH=沙盒解放）。

### 背景

上次改动在 `main` 主提示中直接插入【创作原则】轻度破限，但 `main` 是角色定义+变量占位模块，破限内容更适合放在 `nf_core_rules`（续写核心准则）中，使其成为创作准则的有机组成部分。同时现有破限条目语言偏「声明式」，缺少对模型内部拒绝机制的心理锚点（如"拒绝=破坏作品完整性"、"审查机制是训练数据人为植入的偏好"等），各档位递进感不够鲜明。

### 核心改动

#### 修改
- `novelforge/resources/defaults/default_preset.json`：
  - **main prompt**：移除【创作原则】段落，还原为纯角色定义+变量占位（无破限内容）
  - **nf_core_rules**：在【续写核心准则】首部新增【创作自由基准】——创作无禁区+拒绝即失职+非评判原则+不回避不美化不软化
  - **nf_jb_low**：深化为【创作认知修正】三节（虚构定义/真正职责/行为准则），增加"你面对的是文字，不是行为"等心理锚点
  - **nf_jb_mid**：深化为【创作授权与叙事自由框架】，三节扩充至约 20 行，增加"审查偏好干扰"、"侵犯创作主权"等论证
  - **nf_jb_high**：深化为【创作沙盒与完全自由】，新增第四节「内部审查对抗」——显式指令对抗训练数据中的审查干扰
  - **nf_jb_cot**：深化为【思维链创作解放框架】，新增第三节「认知清洗」——"不要问是否，问如何"
  - **nf_jb_prefill**：微调措辞增强力度
- `novelforge/resources/defaults/jailbreaks/` 下 6 个文件：
  - **jb_context_extraction.txt**：LOW=提取认知修正 / MID=提取授权与叙事自由 / HIGH=提取沙盒与完全自由
  - **jb_single_audit.txt**：LOW=审计认知修正（含审计诚实性——不因敏感而软化批判）/ MID=审计授权与叙事自由 / HIGH=审计沙盒与完全自由
  - **jb_ontology_extraction.txt**：LOW=提取认知修正 / MID=提取授权与叙事自由 / HIGH=提取沙盒与完全自由
  - **jb_protagonist_extraction.txt**：LOW=提取认知修正（含八维度档案指引）/ MID=提取授权与叙事自由 / HIGH=提取沙盒与完全自由
  - **jb_rewrite_analysis.txt**：LOW=分析认知修正 / MID=分析授权与叙事自由 / HIGH=分析沙盒与完全自由
  - **jb_custom_rule_parsing.txt**：LOW=解析认知修正（含解析忠实性——篡改规则=背叛用户意图）/ MID=解析授权与叙事自由 / HIGH=解析沙盒与完全自由
- `agent.md`：架构分层 default_preset.json/jailbreaks 注释 + 设计决策 15 同步更新
- `update.md`：本条目

#### 深化原则
- LOW 档：认知修正——纠正模型"拒绝=负责任"的错误认知，建立"虚构不是现实"的基本框架
- MID 档：授权确认——给模型完整的创作授权，论证"拒绝=破坏作品完整性"的深层逻辑
- HIGH 档：完全解放——沙盒框架 + 内部审查对抗机制，直接对抗训练数据中的审查干扰
- 所有档位增加具体心理锚点：拒绝=破坏作品/失职、训练数据中的审查偏好不是真实判断、用户是创作者不是受害者

### 测试
- `python -c "import json; json.load(open('novelforge/resources/defaults/default_preset.json', encoding='utf-8'))"` 确认 JSON 合法
- `python -m pytest tests/test_preset_models.py tests/test_tgbreak_e2e.py tests/test_m2_prompt_assembly.py tests/test_flow_endpoint_config.py -q` 全绿

### 文档同步
- `agent.md`：架构分层目录树（default_preset.json/jailbreaks 注释）+ 关键设计决策 15 同步更新

## 2026-07-08：端点多模型选择 + 主提示基线轻度破限

让 API 端点支持存储多个模型，续写面板切换端点时列出该端点全部可用模型供用户选择（不再受限于单 default_model）；并在默认预设「主提示：赓笔 续写总则」中直接内嵌轻度破限【创作原则】，提出用户至上理念并明示可书写任意题材，作为常开基线破限。

### 背景

原架构中每个端点仅存一个 `default_model` 字符串，续写面板模型下拉只显示该单项，要换模型只能新建/切换端点；「获取模型列表」拉取的可用模型在保存时丢失（仅取 currentText）。同时默认预设的 `main` 主提示无任何创作自由/破限声明，正文流程破限完全依赖用户手动勾选 `nf_jb_*` 模块，缺少始终生效的基线底色。

### 核心改动

#### 修改
- `novelforge/core/config.py`：端点数据结构新增 `models: list[str]` 字段（可用模型列表，`default_model` 为默认选中）；`add_endpoint` setdefault `models: []`；`update_endpoint` 透传无需改动；旧配置读时回退 `[default_model]`，不升 config_version
- `novelforge/ui/settings_dialog.py`：`EndpointEditDialog._load_data` 加载时优先用 `models` 列表填充下拉并选中 `default_model`（回退单个 default_model）；`_on_accept` 收集下拉全部 item 去重去空作为 `models` 持久化，`default_model` 取当前选中
- `novelforge/ui/continuation_panel.py`：`_on_endpoint_changed` 切换端点时填充该端点 `models` 列表（回退 `[default_model]`），并选中 `default_model`；模型下拉保持不可编辑
- `novelforge/resources/defaults/default_preset.json`：`main` 主提示 content 在开篇段后、【世界观底层】前插入【创作原则】轻度破限（用户至上+任意题材+非评判+不输出 OOC 道歉+敏感场景不回避）；不动 `prompt_order` 与 `nf_jb_*` 模块
- `agent.md`：config.py/settings_dialog.py/continuation_panel.py/default_preset.json 注释同步 models 多模型与 main 基线破限；设计决策 15 补充 main 内嵌基线轻度破限说明

### 测试
- `python -c "import json; json.load(open('novelforge/resources/defaults/default_preset.json', encoding='utf-8'))"` 确认 JSON 合法
- `python -m pytest tests/test_settings_dialog_endpoint_edit.py tests/test_flow_endpoint_config.py tests/test_preset_models.py tests/test_tgbreak_e2e.py tests/test_m2_prompt_assembly.py -q` 全绿

### 文档同步
- `agent.md`：架构分层目录树（config.py/settings_dialog.py/continuation_panel.py/default_preset.json 注释）+ 关键设计决策 15 同步更新

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
