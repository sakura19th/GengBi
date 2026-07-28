# 赓笔 (GengBi) 更新日志

> 本文件按时间倒序记录每次代码修改的详细变更，与 `README.md` 的"更新记录"章节互补：README 仅列版本要点，本文件含完整背景、改动细节、测试与文档同步情况。

## 2026-07-28：v0.2.15 发版——自定义角色提取 + 网络代理 + 多项修复

### 背景

v0.2.14 发布后积累多项功能新增与缺陷修复，涉及自定义角色形象提取、全局网络代理、卷续写衔接 bug、单章续写宏注入断链、跨线程信号类型不匹配等。本次汇总发版 v0.2.15，将 2026-07-27 起至 2026-07-28 的全部变更打包发布。

### 核心改动（按发版时间倒序汇总，详见下方各分条目）

1. **修复单章续写 `{{custom_characters}}` 宏未注入**（2026-07-28）
   - `novelforge/core/prompt_assembler.py`：`assemble()` 调用 `_build_macro_context()` 补传 `custom_characters` 参数；`_build_macro_context()` 方法体补 `ctx.extra["custom_characters"]` 注入
   - `README.md`：额外注入宏注释补充 `{{previous_chapter_titles}}` 仅在单章续写预设 main 模块注入的说明

2. **修复自定义角色提取信号类型不匹配 + 改进查看列表 UI**（2026-07-28）
   - `novelforge/ui/main_window.py`：`_custom_character_done` 信号类型 `Signal(object, str, str)` → `Signal(str, object, str)`，与 emit 参数顺序 `(name, profile, status)` 严格匹配
   - `_show_custom_character_dialog` 重写为 master-detail 列表（QListWidget 角色名排序 + QPlainTextEdit 档案展示 + currentItemChanged 联动）

3. **新增网络代理 http_proxy 设置**（2026-07-28）
   - `novelforge/core/config.py`：新增 `network` 顶层分组（`proxy_enabled`/`http_proxy`）+ `get/set_network_settings` 方法
   - `novelforge/services/llm_client.py`：构造函数新增 `proxy` 参数，三处 aiohttp 请求统一传 `proxy=self.proxy or None`
   - 4 个 extractor `_get_llm_client` + 3 个 worker 构造 + ModelFetchWorker + main_window 7 处 worker 实例化点透传 proxy
   - `novelforge/ui/settings_dialog.py`：新增「网络代理」QGroupBox 分组（启用开关 + URL 输入框，开关联动）
   - 新增 `tests/test_http_proxy.py`（19 用例）

4. **新增自定义角色形象提取功能**（2026-07-28）
   - `novelforge/models/chapter.py`：`Chapter` 新增 `custom_characters: dict[str, ProtagonistProfile]` 字段
   - `novelforge/core/storage.py`：`chapters` 表新增 `custom_characters TEXT` 列 + 幂等迁移 + `update_chapter_custom_characters` 单列更新
   - `novelforge/services/context_extractor.py`：新增 `extract_custom_character_streaming` 链路（缓存 key 前缀 `custom_character:`），镜像主角提取流程
   - 新增 `extract_custom_character_prompt.txt` / `extract_custom_character_merge_prompt.txt` / `jb_custom_character_extraction.txt`
   - 注册 `custom_character_extraction` flow_key（默认破限 `low`）
   - `novelforge/ui/context_preview_panel.py`：新增「提取自定义角色」「查看自定义角色」按钮 + 流式接口 + 切章状态恢复
   - `novelforge/ui/main_window.py`：信号接线 + 槽实现 + 章节切换状态恢复
   - 新增 `tests/test_custom_character_extraction.py`（31 用例）

5. **卷级多章节续写流程审查修复**（2026-07-27）
   - `novelforge/services/volume_orchestrator.py`：修复卷第一章 `previous_chapter_text` 初始化为空导致衔接断裂 + `chapter_transition` 审计维度误判跳过；删除已废弃的 `_run_chapter_revise` 死代码
   - `tests/test_volume_orchestrator.py`：同步 reject 路径测试过时注释

### 测试

- `tests/test_custom_character_extraction.py` 31 用例 + `tests/test_protagonist_extraction.py` 29 用例无回归
- `tests/test_http_proxy.py` 19 用例 + `tests/test_settings_dialog_endpoint_edit.py` 兼容新 `proxy` 参数
- `tests/test_volume_orchestrator.py` + `tests/test_volume_prompts.py` 通过
- `tests/test_m2_prompt_assembly.py` + `tests/test_volume_prompts.py` 验证宏替换无回归
- 所有修改文件 `python -m py_compile` 编译通过

### 文档同步

- `agent.md`：当前版本号 v0.2.14 → v0.2.15；架构分层多文件描述更新（含 proxy/自定义角色链路）；关键设计决策新增第 20 条「自定义角色形象提取」+ 第 21 条「网络代理（HTTP Proxy）」；第 9 条「卷级多章节续写」补「卷第一章衔接」条目；第 10 条「额外注入宏」列表补齐 `{{custom_characters}}` 与 `{{previous_chapter_titles}}`
- `README.md`：顶部版本号 v0.2.14 → v0.2.15；打包示例文件名同步；更新记录顶部新增 `### v0.2.15` 小节；额外注入宏注释补 `{{previous_chapter_titles}}` 适用范围说明
- `update.md`：本汇总条目（下方保留各分条目原始记录）

## 2026-07-28：修复单章续写 {{custom_characters}} 宏未注入 + 校对 README 额外注入宏文档

### 背景

检查自定义角色提取内容是否被组合进预设时发现：卷续写路径（VolumeOrchestrator）已正确注入 `{{custom_characters}}` 宏到 8 处 phase 方法，但单章续写路径（PromptAssembler）存在断链 bug——`assemble()` 和 `_build_macro_context()` 均已声明 `custom_characters` 参数且 `_serialize_custom_characters_or_placeholder()` 静态方法已实现，但 `assemble()` 调用 `_build_macro_context()` 时未传该参数，且 `_build_macro_context()` 方法体未设置 `ctx.extra["custom_characters"]`。导致单章续写时 `default_preset.json` main 模块中的 `{{custom_characters}}` 占位符不会被替换，原样发送给 LLM。

同时校对 README "额外注入宏" 表格：6 个宏（`{{world_ontology}}`/`{{protagonist_profile}}`/`{{custom_characters}}`/`{{style_profile}}`/`{{custom_audit_rules}}`/`{{previous_chapter_titles}}`）覆盖了 PromptAssembler 所有 `ctx.extra` 注入项，无遗漏。但 L150 注释"这些宏在卷续写的各阶段模板中也同样可用"对 `{{previous_chapter_titles}}` 不准确——该宏仅在单章续写预设 main 模块使用，15 个 phase_*.txt 模板均未引用。

### 核心改动

1. **`novelforge/core/prompt_assembler.py`**：修复单章续写宏注入断链
   - `assemble()` 调用 `_build_macro_context()` 时补传 `custom_characters=custom_characters` 关键字参数
   - `_build_macro_context()` 方法体补加 `ctx.extra["custom_characters"] = self._serialize_custom_characters_or_placeholder(custom_characters)`（位于 `custom_audit_rules` 与 `previous_chapter_titles` 注入之间）

2. **`README.md`**：修正额外注入宏文档措辞
   - L150 注释后追加说明：`{{previous_chapter_titles}}` 仅在单章续写预设 main 模块注入，卷续写 phase 模板未使用该宏（卷续写通过 `{{previous_chapters_text}}`/`{{previous_chapter_text}}` 等流程专用占位符提供前文衔接）

### 测试

- `python -m py_compile novelforge\core\prompt_assembler.py` 编译通过
- `python -m pytest tests/test_m2_prompt_assembly.py tests/test_volume_prompts.py -q` 验证宏替换无回归

### 文档同步

- `agent.md`：`core/prompt_assembler.py` 架构分层描述补充 `custom_characters` 参数与 `{{custom_characters}}` 宏注入；关键设计决策第 10 条"额外注入宏"列表补齐 `{{custom_characters}}` 与 `{{previous_chapter_titles}}` 两项
- `README.md`：额外注入宏注释补齐 `{{previous_chapter_titles}}` 适用范围说明
- `update.md`：本条目

## 2026-07-28：修复自定义角色提取信号类型不匹配 + 改进查看列表 UI

### 背景

自定义角色提取完成后报两个错误：
1. `Shiboken::Conversions::_pythonToCppCopy: Cannot copy-convert ... (ProtagonistProfile) to C++.`
2. `WARNING: 自定义角色持久化失败 ... 'str' object has no attribute 'model_dump'`

另外用户反馈：查看自定义角色时多角色场景下应排列成列表供点击查看，而非简单的 QInputDialog 下拉。

### 根因

`main_window.py` 第 205 行信号 `_custom_character_done = Signal(object, str, str)` 的类型声明与 emit 参数顺序 `(name: str, profile: ProtagonistProfile, status: str)` 不匹配——第 2 位声明为 `str` 但实际传 `ProtagonistProfile`，Shiboken 转换失败后 profile 被损坏为 str，持久化时 `p.model_dump()` 报错。

### 核心改动

1. **`novelforge/ui/main_window.py`**：信号类型修复
   - `_custom_character_done = Signal(object, str, str)` → `Signal(str, object, str)`
   - 使类型声明与 emit `(name, profile, status)` + Slot `@Slot(str, object, str)` 完全匹配

2. **`novelforge/ui/main_window.py`**：查看对话框改为 master-detail 列表
   - import 新增 `QListWidget`、`QListWidgetItem`
   - `_on_view_custom_character_requested` 简化：不再区分单角色/多角色，统一调 `_show_custom_character_dialog(chars)`
   - `_show_custom_character_dialog` 重写：接收 `chars: dict`，QHBoxLayout 左右布局——左侧 QListWidget 角色名按字母排序，右侧 QPlainTextEdit 展示选中角色档案，`currentItemChanged` 联动刷新，默认选中第一项

### 测试

- `python -m py_compile novelforge\ui\main_window.py` 编译通过
- `python -m pytest tests/test_custom_character_extraction.py -q` 31 用例全部通过无回归

### 文档同步

- `agent.md`：自定义角色第 20 条设计决策更新 UI 入口描述（master-detail 列表）+ 新增跨线程信号类型说明
- `update.md`：本条目

## 2026-07-28：新增网络代理 http_proxy 设置

### 背景

用户需要在设置中新增网络代理（http_proxy）配置，使所有 API 端点的 LLM 请求（续写/审计/提取/模型获取）能通过统一的 HTTP/HTTPS 代理发出，适配需要代理访问 API 的网络环境。

### 核心改动

1. **`novelforge/core/config.py`**：新增 `network` 顶层配置分组
   - `get_default_config()` 返回值新增 `"network": {"proxy_enabled": False, "http_proxy": ""}`
   - 新增 `get_network_settings()` / `set_network_settings()` 方法（RLock 线程安全，set 内调 `save()` 持久化）
   - 不升 `config_version`，`_merge_defaults` 平滑合并旧配置

2. **`novelforge/services/llm_client.py`**：LLMClient 支持 proxy 参数
   - 构造函数新增 `proxy: str | None = None`，`self.proxy = proxy.strip() if proxy and proxy.strip() else None`（空串/纯空白归一为 None）
   - `stream_chat_completion` / `chat_completion` / `fetch_models` 三处 aiohttp 请求均传 `proxy=self.proxy or None`

3. **4 个 extractor 的 `_get_llm_client` 工厂方法**（context_extractor/ontology_extractor/style_extractor/custom_audit_rule_service）
   - 构造 LLMClient 前读 `config_manager.get_network_settings()`，启用且非空时传入 proxy

4. **3 个 worker 构造函数新增 proxy 参数并透传**
   - `continuation_worker.py` / `audit_worker.py` / `volume_orchestrator.py`：构造函数新增 `proxy: str | None = None`，保存 `self.proxy`
   - 主 client 实例化 + 调试覆盖 client 实例化（`_effective_client`）均传 `proxy=self.proxy`

5. **`novelforge/ui/main_window.py`**：7 处 worker 实例化点传入 proxy
   - 新增 `_get_network_proxy()` 辅助方法统一读取网络配置
   - 7 处 worker 实例化（1 ContinuationWorker 续写 + 1 VolumeOrchestrator + 3 AuditWorker + 2 ContinuationWorker 修正/重写）均传 `proxy=self._get_network_proxy()`

6. **`novelforge/ui/settings_dialog.py`**：ModelFetchWorker + 网络代理 UI
   - `ModelFetchWorker.__init__` 新增 `proxy` 参数，`run()` 构造 LLMClient 时传入
   - `EndpointEditDialog._on_fetch_models` 从 `config_manager.get_network_settings()` 读取代理传入 ModelFetchWorker
   - `SettingsDialog._setup_ui` 新增「网络代理」QGroupBox 分组（QCheckBox 启用开关 + QLineEdit 代理 URL 输入框），开关联动输入框启用状态，加载时从配置回填
   - `SettingsDialog._on_accept` 调 `set_network_settings` 持久化（URL 去首尾空白）

### 测试

- 新增 `tests/test_http_proxy.py`（19 用例）：
  - 配置层：默认配置含 network 分组、get/set 往返持久化、旧配置无 network 字段兼容、开关与 URL 独立保存
  - LLMClient：proxy 参数 5 场景（正常 URL/None/空串/纯空白/带空白去空白）+ 默认 None
  - ModelFetchWorker：默认 None + 传入保存
  - SettingsDialog UI：分组存在、加载配置、禁用态联动、保存持久化、开关切换启用、去空白
- 修复 `tests/test_settings_dialog_endpoint_edit.py` 的 `_SlowModelFetchWorker` stub 兼容新 `proxy` 参数
- `python -m py_compile` 11 个修改文件全部通过
- `tests/test_http_proxy.py` + `tests/test_settings_dialog_endpoint_edit.py` + `tests/test_custom_character_extraction.py` + `tests/test_protagonist_extraction.py` 全部通过无回归

### 文档同步

- `agent.md`：架构分层 config.py/llm_client.py/3 worker/settings_dialog.py 描述补充 proxy；关键设计决策新增第 21 条「网络代理（HTTP Proxy）」
- `update.md`：本条目

## 2026-07-28：新增自定义角色形象提取功能

### 背景

用户希望在提取主角形象的基础上新增「提取自定义角色」功能：用户输入角色名，按与主角形象相同的流程（全文拆分/增量合并/语义整合/8 维度心理学档案）提取指定角色，面板增加对应 UI 按钮，支持持久化与切章恢复。

### 核心改动

1. **`novelforge/models/chapter.py`**：`Chapter` 新增 `custom_characters: dict[str, ProtagonistProfile]` 字段
   - 复用 `ProtagonistProfile` 模型，与主角形象提取链路解耦
   - 按角色名键存档，每章可存多个角色

2. **`novelforge/core/storage.py`**：SQLite `chapters` 表新增 `custom_characters TEXT` 列
   - 幂等迁移函数 `_migrate_chapters_columns` 补列
   - `_row_to_chapter` 反序列化 JSON → dict
   - 新增 `update_chapter_custom_characters` 单列更新方法（不触碰正文文件）

3. **`novelforge/services/storage_service.py`**：新增 `update_chapter_custom_characters` 包装方法
   - 接收 `dict[str, ProtagonistProfile]`，序列化为 JSON 后调存储层

4. **`novelforge/services/context_extractor.py`**：新增自定义角色提取链路
   - 常量：`CUSTOM_CHARACTER_CACHE_KEY_PREFIX = "custom_character"` + 提取/合并温度等
   - 辅助函数：`_filter_custom_character_dimensions` / `_parse_custom_character_response`（复用 protagonist 实现）
   - 缓存 key 构建：`_build_custom_character_cache_key` → `custom_character:{project_id}:{chapter_id}:{character_name}`
   - 主方法 `extract_custom_character_streaming`（镜像 `extract_protagonist_streaming`，多 `character_name` 参数）
   - 缓存读写：`_get_cached_custom_character` / `_save_cached_custom_character` / `load_cached_custom_character`

5. **`novelforge/utils/paths.py`**：新增 `get_extract_custom_character_prompt_path` / `get_extract_custom_character_merge_prompt_path`

6. **`novelforge/resources/defaults/extract_custom_character_prompt.txt`** 与 **`extract_custom_character_merge_prompt.txt`**：新建提示词模板，含 `{{character_name}}` 占位符

7. **`novelforge/resources/defaults/jailbreaks/jb_custom_character_extraction.txt`**：新建破限模板，含 LOW/MID/HIGH 三档

8. **`novelforge/ui/flow_endpoint_dialog.py`** + **`novelforge/core/config.py`**：注册 `custom_character_extraction` flow_key
   - `FLOW_DEFINITIONS` 新增 `("custom_character_extraction", "自定义角色提取")`
   - `FLOW_DEFAULT_JAILBREAKS` 新增 `"custom_character_extraction": "low"`

9. **`novelforge/ui/context_preview_panel.py`**：UI 按钮 + 信号 + 流式接口
   - 新增信号 `extract_custom_character_requested` / `view_custom_character_requested`
   - 新增按钮「提取自定义角色」「查看自定义角色」（与主角按钮并列，所有提取按钮互斥禁用）
   - 新增槽 `_on_extract_custom_character_clicked` / `_on_view_custom_character_clicked`
   - 新增流式接口 `start_custom_character_extraction` / `update_custom_character_progress` / `update_custom_character_batch` / `finish_custom_character_extraction` / `fail_custom_character_extraction`
   - `restore_extraction_state` 新增 `is_custom_character` 参数支持切回发起章节恢复"接收中"态（同时修复原主角/世界观分支的缩进错误）

10. **`novelforge/ui/main_window.py`**：信号接线 + 槽实现 + 状态恢复
    - 新增信号 `_custom_character_chunk_received` / `_custom_character_done(name, profile, status)` / `_custom_character_batch_done`
    - 新增状态变量 `_custom_character_extracting` / `_custom_character_name` / `_custom_character_stream_text` / `_custom_characters_by_chapter`
    - 信号连接 5 个：panel 的 extract/view → handler，3 个内部信号 → chunk/batch/done 槽
    - 槽实现：
      - `_on_extract_custom_character_requested`：弹 `QInputDialog.getText` 取角色名 → 非阻塞调 `extract_custom_character_streaming`
      - `_on_custom_character_chunk_received` / `_on_custom_character_batch_done`：缓冲 + 面板更新
      - `_on_custom_character_done`：落盘到 `chapters.custom_characters` 列 + 内存 LRU + 失败弹窗
      - `_on_view_custom_character_requested`：单角色直接展示，多角色弹 `QInputDialog.getItem` 选择
      - `_show_custom_character_dialog`：展示档案对话框
    - `_load_context_entries_for_chapter`：新增切回发起章节恢复流式态分支 + 章节切换从 `chapter.custom_characters` 恢复内存缓存

### 测试

- `python -m py_compile` 对所有修改文件语法检查通过（exit code 0）
- UI 按钮状态在所有提取流程（上下文/世界观/主角/自定义角色/文风/自定义设定）中互斥禁用，避免并发提取
- 新增 `tests/test_custom_character_extraction.py`（31 用例，10 测试类）：
  - 维度过滤：`_filter_custom_character_dimensions` 8 维度保留/非 dict 替换/额外字段丢弃/委托 protagonist
  - JSON 解析：`_parse_custom_character_response` 直接 JSON/```json fence/``` fence/无效 JSON 抛错/委托 protagonist
  - 常量对齐：`CUSTOM_CHARACTER_DIMENSIONS` = `PROTAGONIST_DIMENSIONS`；`CUSTOM_CHARACTER_*` 温度/max_tokens 与 protagonist 对齐，缓存 key 前缀不同
  - 缓存 key：`_build_custom_character_cache_key` 格式 `custom_character:{project_id}:{chapter_id}:{character_name}`；不同角色名互不覆盖；与 `protagonist:` 前缀解耦
  - 流式提取：`extract_custom_character_streaming` 单批次正常/多批次合并/缓存往返 `load_cached_custom_character`/回调调用/LLM 失败返回 None
  - 缓存解耦：自定义角色写入 `custom_character:` 前缀不覆盖 `protagonist:`；不同角色名独立缓存
  - UI 按钮：按钮存在/objectName 正确/信号发射/`start_custom_character_extraction` 禁用/`finish_custom_character_extraction` 恢复
  - flow_key 注册：`FLOW_DEFINITIONS` 含 `custom_character_extraction`/默认破限 `low`/越狱模板文件存在/提示词模板文件存在/含 `{{character_name}}` 占位符
  - Chapter 字段：`custom_characters` 默认空 dict/可存多个 ProtagonistProfile/序列化反序列化往返
- 运行 `python -m pytest tests/test_custom_character_extraction.py -v` 全部 31 用例通过；`tests/test_protagonist_extraction.py` 29 用例无回归

### 文档同步

- `agent.md`：架构分层多文件描述更新；关键设计决策第 16 条「流程破限配置」非正文流程清单含 `custom_character_extraction`；新增「自定义角色提取」设计决策条目含测试覆盖；测试要求「流式 mock 约束」补充 `test_custom_character_extraction.py`

## 2026-07-27：卷级多章节续写流程审查修复

### 背景

审查卷级多章节续写（Volume）流程的代码、提示词模板与格式化输出（outline_serializer），修复潜在失败与错误。审查覆盖 `volume_orchestrator.py` 核心、8 个 `phase_*.txt` 提示词、`outline_serializer.py`、`models/volume.py` 与 `models/agent.py` 数据模型校验、`json_utils.py`。

### 核心改动

1. **`novelforge/services/volume_orchestrator.py`**：修复卷第一章衔接 bug + 清理死代码
   - **关键修复**：`_run_chapter_loop` 初始化 `previous_chapter_text = self.current_chapter.content or ""`（原为空字符串）。卷第一章紧邻的上一章是插入点章节，原空值导致 `phase_chapter_outline.txt` 的 `{{previous_chapter_text}}` 占位符为空（LLM 无法获得插入点结尾正文做紧密衔接），且 `phase_verify.txt` 的 `chapter_transition` 必审维度误判为"第一章"跳过衔接审计，卷第一章与插入点章节的断裂无法被检出
   - **死代码清理**：删除 `_run_chapter_revise` 方法（原 line 2088-2157）。新流程审计后直接用 `_run_chapter_rewrite` 加载 phase_audit_rewrite.txt 重写，审计报告整体即修改意见，不再单独生成修订指导。grep 确认无生产/测试调用点，`phase_revise.txt` 资源文件保留不动（agent.md 已标注 legacy）
2. **`tests/test_volume_orchestrator.py`**：同步 reject 路径测试的过时注释（原注释提及已删除的 `_run_chapter_revise`，改为反映 `_run_chapter_rewrite` 直接重写流程）

### 审查通过项（无 bug）

- 8 个 `phase_*.txt` 提示词内部一致：`plot_role` 合法值与 `VALID_PLOT_ROLES` 一致；`DEFAULT_AUDIT_DIMENSIONS`（13 维度）与 outline_audit 模板一致；`VALID_CRITIQUE_CATEGORIES`（16 维度）与 verify 模板 16 维度一致
- `outline_serializer.py` format/parse round-trip 字段映射正确
- `models/volume.py` `ChapterPlan.validate_plot_role` 容错归一化合理，`VolumeRunConfig` 各 field_validator 范围校验正确
- `json_utils.py` `parse_json_response` 宽松解析稳健，含 50MB 大小上限保护
- `_build_lookback_chapters_text` 有测试覆盖（`test_build_lookback_chapters_text`），非死代码，保留

### 测试

`python -m pytest tests/test_volume_orchestrator.py tests/test_volume_prompts.py -q`

### 文档同步

agent.md 第 9 节「卷级多章节续写」新增「卷第一章衔接」条目说明 `previous_chapter_text` 初始化机制；服务层描述同步 `_run_chapter_revise` 已移除与新流程衔接机制

## 2026-07-26：v0.2.14 — 上下文条目编辑自动持久化

### 背景

用户反馈：上下文提取结果在关闭软件后偶尔丢失，重新打开后消失。经排查根因——`ContextPreviewPanel.entries_changed` 信号此前仅更新 `MainWindow._context_entries_by_chapter` 内存缓存，未写入 SQLite，应用关闭后内存丢失；用户手动新增的条目（无现有缓存）则完全未落盘。

### 核心改动

1. **`novelforge/services/context_extractor.py`**：新增 `save_edited_entries` 方法
   - 复用现有 cache_key（`ctx_extract:{project_id}:{chapter_id}[:rewrite]`）
   - 保留原 `chapters_hash` 与元数据（保证提取时缓存校验仍通过），仅更新 entries 字段并刷新 TTL
   - 无现有缓存时使用 sentinel hash `"manual_edit"` 保存（切章加载可恢复，后续提取会因 hash 不匹配自动重提取）
   - 不检查 `cache_enabled` 配置（用户编辑是明确意图），无条件保存避免编辑丢失

2. **`novelforge/ui/context_preview_panel.py`**：`_on_disable_toggled` 同步更新 `entry.enabled` 字段
   - `entry.enabled` 字段为禁用状态真源，`_disabled_uids` 仅作 UI 显示影子缓存
   - `set_entries`/`_load_entries`/`_on_extract_finished`/`_on_entries_loaded` 等 4 处入口从 `enabled` 字段重建 `_disabled_uids` 保证一致性

3. **`novelforge/ui/main_window.py`**：`_on_context_entries_changed` 增加异步持久化 + 新增 `_persist_context_entries` 方法
   - 内存缓存存全部条目（含禁用，与 SQLite 保持一致）
   - `_persist_context_entries` 用 `asyncio.run_coroutine_threadsafe` 提交 `save_edited_entries` 到 `AsyncLoopRunner` 后台循环 fire-and-forget
   - 重写模式（`exclude_current`）由当前续写模式判定，与提取时一致（cache_key 追加 `:rewrite` 后缀避免互相覆盖）
   - 失败仅日志告警，不弹窗打断用户编辑

### 测试

- `python -m py_compile` 对三个修改文件语法检查通过（exit code 0）
- 5 类操作（新增/编辑/删除/清空/禁用）均会 emit `entries_changed` 信号触发持久化路径，已通过代码审查验证

### 文档同步

- `agent.md`：架构分层 3 处补充说明（context_extractor.py / context_preview_panel.py / main_window.py）+ 关键设计决策第 3 条"提取与续写解耦"新增"条目编辑自动持久化"小节
- `README.md`：顶部版本号 v0.2.13 → v0.2.14，打包示例版本号同步，"更新记录"章节追加 v0.2.14 小节
- `novelforge/__init__.py`：`__version__` 0.2.13 → 0.2.14
