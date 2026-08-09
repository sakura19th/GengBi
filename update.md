# 赓笔 (GengBi) 更新日志

> 本文件按时间倒序记录每次代码修改的详细变更，与 `README.md` 的"更新记录"章节互补：README 仅列版本要点，本文件含完整背景、改动细节、测试与文档同步情况。

## 2026-08-09：v0.2.19 Gemini 兼容 + 全局非流式 + 输入历史

### 背景

1. Antigravity/Gemini 兼容网关在 OpenAI 风格请求中带 `presence_penalty`/`frequency_penalty` 时返回 `400 INVALID_ARGUMENT`。
2. 若切非流式，旧 `chat_completion` 使用 `total=timeout`（默认 300s），长续写会被总超时杀掉。
3. 续写指令输入框无历史回顾，不便复用常用指令。

### 核心改动

1. **`llm_client.py`**：`_filter_unsupported_params` 对 Gemini 删除 penalty；其余模型 0.0 时省略；生成超时 `request_timeout=total=None+sock_read`（流式/非流式对齐）；`chat_completion` 参数与过滤对齐；新增 `complete_text` 统一入口。
2. **`config.py` / `settings_dialog.py`**：`prefer_non_stream`、`user_input_history_size`、`user_input_history` + 设置页控件。
3. **Workers/Extractors**：ContinuationWorker/AuditWorker/VolumeOrchestrator 分支非流式；ontology/style/custom_rule/context 读全局开关；main_window 构造透传 `prefer_non_stream`。
4. **`continuation_panel.py` / `main_window.py`**：历史下拉回填；`_on_start_flow` 前 push 面板原文；设置关闭后刷新。

### 测试

- `tests/test_reasoning_effort.py`：Gemini penalty 过滤 + 0.0 省略
- `tests/test_prefer_non_stream.py`：配置/超时/`complete_text` 分支/main_window 透传
- `tests/test_user_input_history.py`：push 去重截断 + 面板 UI
- 上述合计 38 passed

### 文档同步

- `agent.md` / `README.md` / `__version__` → v0.2.19；`update.md` 本条目

## 2026-08-08：修复 GitHub Actions 打包 UnicodeEncodeError

### 背景

首次触发 GitHub Actions「Build & Release」时，`Build executable` 步骤在 `python -m novelforge.resources.build` 的 `main()` 中 `print(f"开始打包：{app_name}")` 抛出 `UnicodeEncodeError: 'charmap' codec can't encode characters in position 0-4`。根因：GitHub Actions Windows runner 为英文系统，Python stdout 默认 cp1252 代码页，无法编码中文输出；本地中文 Windows（代码页 936）可编码因此未暴露。

### 核心改动

1. **`novelforge/resources/build.py`**：`main()` 开头新增 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` + `sys.stderr.reconfigure(encoding="utf-8", errors="replace")`，保证任意 locale 下中文日志可输出（Python 3.7+ 支持，项目要求 3.11+）
2. **`.github/workflows/release.yml`**：`Build executable` 步骤新增 `env: PYTHONIOENCODING: utf-8` 作为冗余防御（即使 build.py 重配置缺失也不因编码挂掉）

### 测试

- `py_compile` 校验 build.py 语法通过
- PyYAML `safe_load` 校验 release.yml 通过
- 本地模拟验证 UTF-8 重配置后中文 `print` 正常输出

### 文档同步

- `agent.md`：技术栈 GitHub Actions 描述补充编码处理说明
- `update.md`：本条目

## 2026-08-08：GitHub Actions 自动打包发布 Release

### 背景

用户希望新增"更新后自动打包发布 release"功能：代码更新后无需手动在本地打包上传，由 CI 自动完成 Windows exe 打包并发布到 GitHub Release。

### 核心改动

1. **`.github/workflows/release.yml`**（新建）：
   - 触发方式：push `v*` 版本 tag（如 `v0.2.18`）+ `workflow_dispatch` 手动触发
   - Windows runner（windows-latest）+ Python 3.11 + `cache: pip`
   - 安装 `requirements.txt` + PyInstaller 后调 `python -m novelforge.resources.build` 打包（产物 `dist/GengBi_v{版本号}.exe`）
   - `Read version & release notes` 一步从 `novelforge/__init__.py` 读取 `__version__`、从 `README.md`「更新记录」提取最新版本小节（`### v{version}` 到下一个 `### v` 标题间内容，无匹配时回退默认文本），经 `GITHUB_OUTPUT` 多行 heredoc 传递
   - `softprops/action-gh-release@v2` 创建 Release：push tag 触发时 tag 用 `github.ref_name`，手动触发时用 `v{version}`（由 `github.ref_type` 表达式区分）；上传 exe 产物，`fail_on_unmatched_files: true`
   - `permissions: contents: write` 供创建 Release 使用

### 测试

- YAML 语法经 PyYAML `safe_load` 校验通过
- 版本与更新记录提取正则按当前 `README.md`/`__init__.py` 实际内容人工核对（`### v0.2.18` 小节可正确提取）

### 文档同步

- `agent.md`：技术栈追加 GitHub Actions 说明
- `README.md`：打包章节追加「自动打包发布」说明（tag 触发 + Actions 手动触发）
- `update.md`：本条目

## 2026-08-08：上下文预览面板布局重构与增量更新

### 背景

上下文提取预览面板原为 17 个按钮（提取/查看成对并列），布局拥挤。用户要求每个功能合并为一个按钮，点击后弹出操作选择对话框（提取/查看/增量更新/复制等）；并为三类按章节缓存的数据（上下文/主角形象/自定义角色）新增「增量更新」：向上找到最近的、已有提取内容的前文章节作为基准，在该基准之上仅对基准之后到当前章的新增章节做增量提取。已确认：无基准时仅弹提示不执行提取。

### 核心改动

1. **`novelforge/ui/context_preview_panel.py`**：
   - 新增 `_FeatureActionDialog` 轻量对话框（标题 + 垂直排布操作按钮，点击记录动作键并关闭），`_show_feature_dialog` 辅助方法统一弹出
   - 17 个按钮合并为 6 个功能按钮（上下文/世界观底层/主角形象/自定义角色/文风档案/自定义设定），QFlowLayout 排布，点击弹对话框分发动作到既有 `_on_*_clicked` 方法
   - 新增 3 个增量信号 `incremental_context_requested`/`incremental_protagonist_requested`/`incremental_custom_character_requested`（Signal(dict)，dict 为 `get_lookback_config()` 含 lookback/token_limit）
   - 新增 `_feature_buttons: dict[str, QPushButton]` + `_set_feature_buttons_enabled(bool)` 辅助方法统一禁用/恢复功能按钮与 `_add_btn`/`_clear_btn`/`_view_prompt_btn`，`start_*`/`finish_*`/`fail_*`/`restore_extraction_state` 替换原逐按钮 setEnabled

2. **`novelforge/services/context_extractor.py`**：
   - 新增 `find_latest_context_base`（异步）/`find_protagonist_base`/`find_custom_character_base` 三基准查找方法（从当前章前一章往前扫描，返回首个有提取内容的基准章节与数据）
   - `extract`/`extract_streaming`/`_extract_common`/`_extract_common_body` 新增 `incremental_base`/`incremental_base_index` 参数：增量模式跳过 lookback 计算，`target_chapters = sorted[base_index+1 : current_idx+1]`；当前章不晚于基准章时直接返回基准条目
   - 新增 `_build_incremental_prompt`：加载 `extract_incremental_prompt.txt` 模板，`existing_entries` 为基准条目序列化 JSON 数组，`chapters_text` 为 delta 章节文本
   - `extract_protagonist_streaming`/`extract_custom_character_streaming` 新增增量参数，`_extract_protagonist`/`_extract_custom_character` 新增 `initial_accumulated` 将基准档案 `model_dump()` 注入首批提示词（复用 `{{accumulated_protagonist}}` 增量语义）
   - `_save_cached_entries`/`_save_cached_protagonist`/`_save_cached_custom_character` 缓存元数据新增 `incremental_base_index`

3. **`novelforge/resources/defaults/extract_incremental_prompt.txt`**（新建）+ `novelforge/utils/paths.py` 新增 `get_extract_incremental_prompt_path()`：上下文条目增量模板，指令保留仍成立条目（uid 不变）/更新已被新剧情改变的信息（体现演变，uid 不变）/新增新实体（新 uid）/删除过时条目，输出完整更新后的 JSON 数组

4. **`novelforge/ui/main_window.py`**：
   - 面板 3 个增量信号连接至 `_on_incremental_context_requested`/`_on_incremental_protagonist_requested`/`_on_incremental_custom_character_requested`
   - 新增内部信号 `_incremental_no_base = Signal(str)`（槽 `_on_incremental_no_base` 弹 `QMessageBox.information`「未找到更早章节的XX提取结果，未执行提取」）+ `_extract_mode_msg = Signal(str)`（槽 `_set_status_message` 提示「基于第N章提取结果增量更新」）
   - 增量 handler 复用既有校验骨架（章节/端点/API Key/项目加载），无基准提示后 return 不执行提取；有基准带增量参数调对应 streaming 方法，回调复用既有 `_extract_done`/`_protagonist_done`/`_custom_character_done`

### 测试

- 新建 `tests/test_incremental_context_extraction.py`（10 用例）：find_latest_context_base 命中/未命中/空条目跳过/排除当前章、_build_incremental_prompt 占位符/覆盖/无 project、增量 delta 章节与缓存元数据、当前章不晚于基准、多批合并每批携带基准
- 新建 `tests/test_incremental_protagonist_extraction.py`（7 用例）：find_protagonist_base 命中/未命中/排除当前章/跳过空档案、delta 章节 + initial_accumulated 种子、当前章不晚于基准、多批合并
- 新建 `tests/test_incremental_custom_character_extraction.py`（8 用例）：find_custom_character_base 命中/角色名不匹配/未命中/排除当前章/跳过空 dict、delta 章节 + 种子 + 缓存元数据、当前章不晚于基准、多批合并
- 新建 `tests/test_context_preview_panel_layout.py`（25 用例）：6 功能按钮存在/文本/objectName/旧按钮移除、_FeatureActionDialog 动作键、动作分发信号（mock _show_feature_dialog）、_set_feature_buttons_enabled 统一禁用恢复、get_lookback_config
- 更新 `tests/test_custom_character_extraction.py` / `tests/test_protagonist_extraction.py`：断言从旧独立提取/查看按钮改为新合并功能按钮 `_custom_character_btn`/`_protagonist_btn` 及其 isEnabled 状态
- 全部测试通过（1 个预先存在失败 `test_m5_polish.py::test_history_panel_refresh`，stash 验证与本次改动无关）

### 文档同步

- `agent.md`：架构树 context_extractor/context_preview_panel 描述更新、resources/defaults 新增 extract_incremental_prompt.txt、新增设计决策 25「章节缓存增量更新」、测试要求新增增量测试说明
- `update.md`：本条目
- `README.md`：v0.2.18 更新要点 + `novelforge/__init__.py` 版本号同步

## 2026-08-06：细纲 AI 修改功能（AuditDialog 修改按钮）

### 背景

规划写作流程阶段 1 生成细纲后，用户在 AuditDialog 中只能手动编辑文本。用户希望增加"修改"按钮，点击后弹出输入框，输入反馈意见后由 AI 根据反馈修改细纲，支持多次修改直到满意再采纳。

### 核心改动

1. **`novelforge/ui/audit_dialog.py`**：
   - 新增 `revision_requested = Signal(str, str)` 信号（当前文本, 反馈意见）
   - 构造参数新增 `enable_revision: bool = False`（仅细纲场景 True，控制"修改"按钮创建）
   - 新增"修改"按钮 `_revise_btn`（左侧，finish_streaming 后启用；点击→`QInputDialog.getMultiLineText` 弹多行输入框获取反馈→emit revision_requested）
   - 新增 `start_revision_streaming()`：备份当前文本到 `_revision_backup`、清空文本区、设只读、禁用所有按钮、状态"修改中..."
   - 新增 `fail_revision(error_msg)`：修订失败恢复原文本、恢复可编辑、启用按钮（与 `fail` 不同——不禁用按钮，恢复修订前文本，用户可重新修改或采纳原文本）
   - `finish_streaming` / `fail` 同步更新 `_revise_btn` 启用/禁用状态

2. **`novelforge/resources/defaults/agent/phase_planned_writing_outline_revise.txt`**（新建）：
   - 细纲修订模板，2 占位符 `{{original_outline}}` / `{{revision_feedback}}`
   - 修改原则：严格执行反馈、保持格式一致、保留未涉及内容、格式硬性要求不变、beats 3~8 条

3. **`novelforge/ui/main_window.py`**（3 处）：
   - `_on_start_generic_analysis`：创建 AuditDialog 时 `enable_revision=(phase=="planned_writing_outline")`，连接 `revision_requested` 信号到 `_on_outline_revision_requested`
   - 新增 `_on_outline_revision_requested(current_text, feedback)`：加载修订模板→str.replace 注入原细纲与反馈→复用 `planned_writing_outline` 端点/模型/破限配置→清理旧 worker→新 AuditWorker（temperature=0.3, max_tokens=6000）→连接 chunk/finished/error 信号（finished 复用 `_on_generic_analysis_finished`，error 用 `_on_revision_error`）→`dialog.start_revision_streaming()` 切回流式→`worker.start()`
   - 新增 `_on_revision_error(error)`：调 `dialog.fail_revision(error)` 恢复原文本，不 cancel 流程（与 `_on_generic_analysis_error` 的关键差异——后者会 cancel FlowExecutor）

4. **`tests/test_http_proxy.py`**：静态扫描断言更新 7→8 个 worker 构造调用（AuditWorker 3→4，新增细纲修订 worker 已传 proxy）

### 交互流程

1. 阶段 1 细纲生成完成 → AuditDialog 显示细纲，"修改"按钮启用
2. 用户点击"修改" → 弹多行输入框 → 输入反馈意见（如"第 3 拍节奏太快，增加主角内心挣扎"）→ 确认
3. AuditDialog 切回流式状态（备份原细纲、清空、只读、状态"修改中..."）
4. AI 根据反馈修订细纲，流式回填对话框
5. 修订完成 → 恢复可编辑（用户可手动微调、再次"修改"、或"采纳"）
6. 修订失败 → 恢复原细纲（用户可重新修改或采纳原文本）
7. 支持**多次修改**直到满意，最终采纳后 `flow_executor.resume` 推进阶段 2

### 测试

- 修复 `test_http_proxy.py` 静态扫描断言（7→8 个 worker，新增 AuditWorker 已传 proxy）
- 全部测试通过（801 passed, 15 skipped, 12 deselected）

### 文档同步

- `agent.md`：audit_dialog.py 描述新增修改按钮/信号/方法；架构树新增 `phase_planned_writing_outline_revise.txt`；main_window.py 描述新增 `_on_outline_revision_requested`/`_on_revision_error`；决策 24 新增「细纲 AI 修改」条目
- `update.md`：本条目

## 2026-08-06：规划写作流程重命名（body_writing → planned_writing）

### 背景

用户反馈"正文写作"命名与"写作模式"易混淆且不够贴切，要求换一个类似「写作模式」风格的命名。选定「规划写作」（planned_writing），突出"先规划细纲再施工"的特点。

### 核心改动

全面重命名（id / 中文名 / created_by / flow_key / 模板文件名 / 破限文件名 / 缓存键 / 方法名），零 body_* 残留：

1. **插件**：`body_writing.json` → `planned_writing.json`（id/name/description/flow_key/params.phase/created_by 全部更新）
2. **模板**：`phase_body_outline.txt` → `phase_planned_writing_outline.txt`（内容不变，文件名映射新 phase）
3. **破限**：`jb_body_outline.txt` → `jb_planned_writing_outline.txt`（内容不变，文件名映射新 flow_key）
4. **flow_key**：`body_outline` → `planned_writing_outline`（显示名「正文细纲生成」→「规划写作细纲生成」）
5. **config.py**：`FLOW_DEFAULT_JAILBREAKS` 键 `body_outline` → `planned_writing_outline`
6. **flow_endpoint_dialog.py**：`FLOW_DEFINITIONS` 项更新
7. **main_window.py**：`created_by=="body_writing"` → `"planned_writing"`；`_on_start_body_writing_continuation` → `_on_start_planned_writing_continuation`；缓存键 `_body_writing_outline` → `_planned_writing_outline`；注释/docstring「正文写作」→「规划写作」
8. **flow_plugin_service.py**（关键 bug 修复）：`_BUILTIN_PLUGIN_IDS` 元组补 `"planned_writing"`（原仅 4 个 id，导致首启复制逻辑不会把新插件复制到用户目录，续写面板下拉框看不到「规划写作」）；docstring「四种模式」→「五种模式」；新增 `_cleanup_deprecated_builtins` 方法清理用户目录中 `builtin=True` 但 id 不在 `_BUILTIN_PLUGIN_IDS` 中的废弃旧内置插件（如重命名前的 `body_writing`），避免下拉框残留
9. **旧文件清理**：删除 `body_writing.json` / `phase_body_outline.txt` / `jb_body_outline.txt`

### 命名映射

| 旧 | 新 |
|----|----|
| `body_writing`（插件 id/created_by） | `planned_writing` |
| 「正文写作」（中文名） | 「规划写作」 |
| `body_outline`（flow_key） | `planned_writing_outline` |
| `phase_body_outline.txt` | `phase_planned_writing_outline.txt` |
| `jb_body_outline.txt` | `jb_planned_writing_outline.txt` |
| `_body_writing_outline`（缓存键） | `_planned_writing_outline` |
| `_on_start_body_writing_continuation` | `_on_start_planned_writing_continuation` |
| 「正文细纲生成」（显示名） | 「规划写作细纲生成」 |

### 测试

- 全局 Grep 确认零 `body_writing`/`body_outline`/`正文写作`/`jb_body_outline`/`phase_body_outline` 残留
- 全部测试通过（801 passed, 15 skipped, 12 deselected，按 agent.md 约定排除 test_m5_polish.py 与 TestUIComponents）

### 文档同步

- `agent.md` / `FLOW_PLUGIN_GUIDE.md` / `README.md` / `update.md`：全局替换，计数不变（5 内置插件 / 13 flow_key / 14 phase 模板）
- `tests/test_rewrite_current_mode.py`：计数断言描述中 `body_outline` → `planned_writing_outline`

## 2026-08-06：新增规划写作流程（planned_writing 插件）

### 背景

用户需要一个新的规划写作流程：第一步根据用户指令生成场景级细纲（beats），用户审阅/修改/采纳后，第二步将细纲作为唯一施工指令传给正文生成阶段。细纲格式参考 `.trae/xujing` 软件的 drive_beats 细纲体系（章节标题 + 3~8 节拍 + 章末钩子 + 可选硬规则/因果注记/语气提示）。

### 核心改动

1. **新建内置插件 `planned_writing`**（`novelforge/resources/defaults/flow_plugins/planned_writing.json`）：
   - 2 阶段流程：audit（细纲生成，flow_key=planned_writing_outline）→ continuation（正文生成，created_by=planned_writing）
   - ui_mode=standard，accept_mode=promote（接受后提升为新章节）
   - 阶段 2 `input_from="outline"` 接收采纳的细纲作为 `_prev_output`

2. **新建细纲模板**（`novelforge/resources/defaults/agent/phase_planned_writing_outline.txt`）：
   - 参考 xujing drive_beats 格式，输出纯文本【细纲】（title/beats 3~8 条/ending_hook/可选 hard_rules/causal_notes/tone_hint）
   - 复用 `_on_start_generic_analysis` 已注入的 8 个占位符
   - 核心要求：ending_hook 具体可感知对象、设定保真（精确数值或「未知」）、转折须有前置因、浅白表露、专名一致

3. **新建破限模板**（`novelforge/resources/defaults/jailbreaks/jb_planned_writing_outline.txt`）：
   - 三档结构（LOW/MID/HIGH），针对细纲规划场景（强调不拒绝敏感剧情的细纲规划）

4. **`novelforge/ui/flow_endpoint_dialog.py`**：`FLOW_DEFINITIONS` 新增 `("planned_writing_outline", "规划写作细纲生成")`

5. **`novelforge/core/config.py`**：`FLOW_DEFAULT_JAILBREAKS` 新增 `"planned_writing_outline": "low"`（默认 low，与提取类/写作要素分析类一致）

6. **`novelforge/ui/main_window.py`**（3 处）：
   - `_flow_handler_continuation`：新增 `created_by=="planned_writing"` 分支，调 `_on_start_planned_writing_continuation`
   - 新增 `_on_start_planned_writing_continuation` 方法：细纲**替换** user_input（`user_input_override=prev_output`），缓存到 `params["_planned_writing_outline"]` 供重写复用
   - `_on_rewrite`：新增 `planned_writing` 模式缓存复用（`_planned_writing_outline` 缓存键，跳过细纲生成阶段直接生成）

7. **版本号**：`0.2.16` → `0.2.17`

### 测试

- 无新增自动化测试（功能为复用现有 writing_mode 模式，分派逻辑与缓存复用镜像既有实现）
- 修复 2 处过时的 flow_key 计数断言：`tests/test_flow_endpoint_config.py::test_flow_endpoint_dialog_has_10_flows` → `has_13_flows`（10→13）、`tests/test_rewrite_current_mode.py::test_flow_definitions_count_is_11` → `is_13`（11→13），反映 planned_writing_outline 加入后 FLOW_DEFINITIONS 总数
- 全部测试通过（801 passed, 15 skipped, 12 deselected，按 agent.md 约定排除 test_m5_polish.py 与 TestUIComponents）

### 文档同步

- `agent.md`：架构分层目录树新增 3 文件（phase_planned_writing_outline.txt/jb_planned_writing_outline.txt/planned_writing.json）；决策 18 内置插件列表加 planned_writing + 服务层 4→5；新增第 24 条设计决策「规划写作流程」；`flow_endpoint_dialog.py`/`config.py` 描述更新；「修改后必须更新」章节 flow_key 八→十三、内置插件三→五个 + 文件名补全；版本号同步
- `FLOW_PLUGIN_GUIDE.md`：第 2 节内置插件表 4→5 加 planned_writing；第 4.1 节 continuation agent 行加 planned_writing 分支；第 4.4 节 flow_key 表十→十三加 planned_writing_outline；第 7 节四→五个 + 新增 7.5 planned_writing.json 完整 JSON 与使用说明/预期效果/重写缓存复用/与 writing_mode 对比；第 8 节 FAQ 12→13
- `README.md`：版本号 + 更新记录 v0.2.17
- `update.md`：本条目

## 2026-08-05：流程端点配置两段可折叠收纳

### 背景

`FlowEndpointDialog` 纵向堆叠 12 行端点/模型 + 约 10 行破限配置，小显示器上对话框过高、不便操作。需将两段内容做成可收纳框；仅 `setVisible` 隐藏内容时外框仍留白，需同步收缩。

### 核心改动

- **`novelforge/ui/flow_endpoint_dialog.py`**：
  - `_setup_ui` 将端点/模型、破限两段绑为可勾选折叠 GroupBox（端点默认展开、破限默认折叠）
  - `_bind_collapsible_section` / `_on_section_toggled` / `_fit_dialog_height`：折叠时隐藏内容并用 `setFixedHeight(标题栏)` 压段外框；对话框按 `layout.totalSizeHint` 把高度 `setFixedHeight` 收到最小（`QTimer.singleShot` 再收一次防 stale），宽度不锁、仍可横向拖拽
  - 保存/加载逻辑不变

### 测试

- 无新增自动化测试（现有无该对话框 UI 用例）；改动仅布局可见性与尺寸约束。

### 文档同步

- `agent.md`：`flow_endpoint_dialog.py` 目录树描述；UI 布局规范可折叠分组写法；设计决策第 16 条配置入口说明
- `update.md`：本条目

## 2026-08-05：主角形象/自定义角色形象的复制到章节与前文回溯注入

### 背景

v0.2.16 已为「上下文条目」实现两个增强功能：①复制到章节（追加合并 + source 描述标明复制来源）②续写时同步注入回溯窗口最新一章提取结果作为独立 system 消息。本次将这两个机制**镜像拓展**到「主角形象」与「自定义角色形象」两类档案，使三类提取产物具备一致的复用与跨章节衔接能力。

### 核心改动

1. **功能 1：复制主角形象/自定义角色到章节**
   - **`novelforge/models/protagonist.py`**：`ProtagonistProfile` 新增 `copied_from: int | None = None` 字段，记录复制来源章节 index；None=原章提取，非 None=复制自第N章。字段向后兼容（旧档案反序列化时默认 None）。
   - **`novelforge/ui/context_preview_panel.py`**：
     - 新增 `copy_protagonist_to_chapter_requested = Signal()` + `_copy_protagonist_to_chapter_btn` 按钮（"复制主角形象到章节"）+ `_on_copy_protagonist_to_chapter_clicked` 槽
     - 新增 `copy_custom_character_to_chapter_requested = Signal()` + `_copy_custom_character_to_chapter_btn` 按钮（"复制自定义角色到章节"）+ `_on_copy_custom_character_to_chapter_clicked` 槽
   - **`novelforge/ui/main_window.py`**：
     - `_on_copy_protagonist_to_chapter`：取当前章主角档案；空则 toast 提示；`QInputDialog.getItem` 选目标章（排除当前章，按 index 排序）；目标章已有档案时弹确认覆盖对话框；`source_profile.model_copy(update={"copied_from": source_index}, deep=True)` 深拷贝避免内存 LRU 中源/目标档案耦合；`update_chapter_protagonist` 单列持久化；更新内存 LRU `_protagonist_profile_by_chapter` 含 `MAX_CONTEXT_CACHE_SIZE` 淘汰保护
     - `_on_copy_custom_character_to_chapter`：取当前章 `custom_characters` dict；空则 toast 提示；`QInputDialog.getItem` 选要复制的角色名（按字母排序）；选目标章；目标章已有同名角色时弹确认覆盖；`model_copy(update={"copied_from": source_index}, deep=True)` 深拷贝；合并语义仅覆盖该角色其他角色保留（`merged = dict(target_chars); merged[name] = copied`）；`update_chapter_custom_characters` 单列持久化；更新内存 LRU `_custom_characters_by_chapter`
     - 信号接线：`copy_protagonist_to_chapter_requested`/`copy_custom_character_to_chapter_requested` 连接对应 handler

2. **功能 2：同步注入回溯主角形象/自定义角色**
   - **`novelforge/core/config.py`**：`DEFAULT_CONFIG["continuation"]` 新增 `inject_lookback_protagonist: False` + `inject_lookback_custom_characters: False` 默认关闭
   - **`novelforge/ui/continuation_panel.py`**：
     - 新增 `_inject_lookback_protagonist_check` QCheckBox「同步注入回溯主角形象」，默认 False
     - 新增 `_inject_lookback_custom_characters_check` QCheckBox「同步注入回溯自定义角色」，默认 False
     - `get_parameters()` 返回两个布尔值；`set_parameters()` 从 params 恢复勾选状态
   - **`novelforge/ui/main_window.py`**：
     - `_get_latest_lookback_protagonist(lookback, current_chapter) -> tuple[ProtagonistProfile, int] | None`：窗口计算镜像 `_get_latest_lookback_context`（排除当前章），按 index 降序遍历，内存 LRU `_protagonist_profile_by_chapter` 优先、未命中同步查 SQLite（`load_chapter` 取 `protagonist_profile` 列），返回首个有档案章节 `(profile, chapter_index)`
     - `_get_latest_lookback_custom_characters(lookback, current_chapter) -> tuple[dict, int] | None`：镜像逻辑取 `custom_characters` dict，返回首个有档案章节 `(dict, chapter_index)`
     - 3 处 `prompt_assembler.assemble` 调用点（单章续写生成/续写预览/重写生成）均扩展：`params.get("inject_lookback_protagonist")` 为 True 时调 `_get_latest_lookback_protagonist`；`params.get("inject_lookback_custom_characters")` 为 True 时调 `_get_latest_lookback_custom_characters`；返回非 None 时传 `lookback_protagonist_profile`/`lookback_protagonist_source_index`/`lookback_custom_characters`/`lookback_custom_characters_source_index` 给 assemble
   - **`novelforge/core/prompt_assembler.py`**：
     - `assemble()` 新增 4 个参数：`lookback_protagonist_profile`/`lookback_protagonist_source_index`/`lookback_custom_characters`/`lookback_custom_characters_source_index`
     - 新增 `_build_lookback_protagonist_message(profile, source_index) -> dict | None`：profile 为 None 返回 None；复用 `_serialize_profile_or_placeholder` 序列化为 JSON 文本，占位文本「（无主角形象档案）」时返回 None；标题 `# 前章主角形象参考（第N章提取）`（source_index 为 None 时用「前文」）
     - 新增 `_build_lookback_custom_characters_message(characters, source_index) -> dict | None`：镜像主角版，空 dict 返回 None；复用 `_serialize_custom_characters_or_placeholder` 序列化（按角色名分节 `【角色：N】`）；标题 `# 前章自定义角色参考（第N章提取）`
     - 注入逻辑：三类回溯消息 `lookback_context_msg`/`lookback_protagonist_msg`/`lookback_custom_characters_msg` 按序组装为 `lookback_msgs` 列表（**上下文 → 主角 → 自定义角色**顺序），统一注入到 worldInfoBefore marker 之后（或 chatHistory 之前/兜底历史之前）；`lookback_injected` 标记保证三类消息整体仅注入一次

### 测试

- 新增 `tests/test_protagonist_custom_character_copy_and_lookback.py`（20 用例，4 测试类）：
  - `TestProtagonistProfileCopiedFrom`（4）：copied_from 默认值/序列化往返/model_copy(deep=True) 保留+更新/旧数据兼容
  - `TestBuildLookbackProtagonistMessage`（3）：None 返回 None/有效档案返回消息/source_index=None 用「前文」
  - `TestBuildLookbackCustomCharactersMessage`（4）：None 返回 None/空 dict 返回 None/有效 dict 多角色分节/source_index=None 用「前文」
  - `TestAssembleLookbackInjection`（6）：三类全 None 无注入/仅主角注入/仅自定义角色注入/三类并存顺序（上下文<主角<自定义角色）/仅注入一次/worldInfoBefore 之后
  - `TestCopyMergeSemantics`（3）：主角 model_copy(deep=True) 独立/自定义角色合并保留其他角色/同名覆盖
- 全部 57 用例（含既有 `test_lookback_context_injection.py` 20 + `test_context_copy_to_chapter.py` 17 + 新增 20）通过，无回归

### 文档同步

- `agent.md`：`models/protagonist.py` 描述新增 `copied_from`；`core/prompt_assembler.py` 描述新增回溯主角/自定义角色消息构建；`ui/context_preview_panel.py` 新增两个复制按钮与信号；`ui/continuation_panel.py` 新增两个回溯注入复选框；`ui/main_window.py` 新增 4 个 handler/取数方法 + 3 处 assemble 扩展；`core/config.py` 默认配置新增两个开关；新增关键设计决策第 23 条「主角形象/自定义角色形象的复制与回溯注入」；第 22 条补充三类回溯消息统一注入说明
- `update.md`：本条目

## 2026-08-05：v0.2.16 发版——上下文复制/回溯注入 + 网络代理修复 + 缓存过期修复

### 背景

v0.2.15 发布后积累 3 项变更：1 个功能新增（含两个子功能）、2 个 bug 修复。本次汇总发版 v0.2.16，将 2026-07-31 起至 2026-08-05 的全部变更打包发布。

### 核心改动（按发版时间倒序汇总，详见下方各分条目）

1. **新增「上下文复制到章节」+「同步注入回溯上下文」两个增强功能**（2026-08-05）
   - **功能 1**：`ContextEntry` 新增 `copied_from` 字段记录复制来源；`ContextPreviewPanel` 新增「复制到章节」按钮 + 信号；`MainWindow._on_copy_context_to_chapter` 实现追加合并 + 新 uid 生成 + 持久化；条目 source 描述拼接复制来源
   - **功能 2**：`ContinuationPanel` 新增「同步注入回溯上下文」QCheckBox 开关（默认 False，配置持久化）；`MainWindow._get_latest_lookback_context` 取回溯窗口最新一章提取结果（排除当前章避免重复）；`PromptAssembler.assemble` 新增 `lookback_context_entries`/`lookback_context_source_index` 参数，`_build_lookback_context_message` 构建独立 system 消息「# 前章上下文参考（第N章提取）」，注入位置紧跟 worldInfoBefore marker 之后
   - 新增 `tests/test_context_copy_to_chapter.py`（17 用例）+ `tests/test_lookback_context_injection.py`（20 用例）

2. **修复提取的上下文 24 小时后消失**（2026-08-01）
   - `novelforge/services/context_extractor.py`：`DEFAULT_CACHE_TTL_HOURS = 24` → `0`；三处提取流程与 `save_edited_entries` 强制 `ttl_hours=0`（`expires_at=NULL` 永不过期），完全依赖 `chapters_hash` 判断失效
   - 修复根因：`storage.cache` 表带 `expires_at` 过期字段，`get_cache` 读取时若过期会直接 DELETE 该行返回 None，导致提取结果（含用户手动编辑的条目）24 小时后被自动删除
   - `cache_ttl_hours` 配置字段废弃（无 UI 入口，代码不再读取，保留兼容）
   - 新增回归测试 `tests/test_m4_context_extraction.py::TestContextCacheTTLNeverExpires`

3. **修复网络代理在多数流程未生效**（2026-07-31）
   - `novelforge/ui/main_window.py`：补齐 5 处 worker 实例化的 `proxy=self._get_network_proxy()` 参数（原仅 2/7 处传入），修复后 7/7 处统一透传
   - 涉及流程：单章续写、卷续写（含全部 7 子阶段）、单章审计、审计后修正重写、重写当前章节生成
   - 新增 `tests/test_http_proxy.py` 5 个回归用例（共 24 用例），含静态扫描防未来漏传

### 测试

- 新增 42 用例：`tests/test_context_copy_to_chapter.py`（17）+ `tests/test_lookback_context_injection.py`（20）+ `tests/test_m4_context_extraction.py::TestContextCacheTTLNeverExpires` + `tests/test_http_proxy.py` 5 个新增用例
- 全部测试通过，无回归

### 文档同步

- `agent.md`：当前版本号 v0.2.15 → v0.2.16；架构分层多文件描述更新（context.py 新增 `copied_from`、prompt_assembler.py 新增回溯上下文参数与 `_build_lookback_context_message`、main_window.py 新增 `_on_copy_context_to_chapter`/`_get_latest_lookback_context`、continuation_panel.py 新增 `inject_lookback_context_check`、context_extractor.py 缓存永不过期）；新增关键设计决策第 22 条「上下文复制到章节与回溯上下文同步注入」；测试要求章节新增功能 1/2 测试说明；第 3 条「提取与续写解耦」补充缓存永不过期说明
- `README.md`：顶部版本号 v0.2.15 → v0.2.16；打包示例文件名同步；更新记录顶部新增 `### v0.2.16` 小节
- `novelforge/__init__.py`：`__version__` 0.2.15 → 0.2.16
- `update.md`：本汇总条目（下方保留各分条目原始记录）

## 2026-08-05：上下文复制到章节 + 回溯上下文同步注入

### 背景

用户在小说续写工作流中提出两个增强需求：
1. **复制上下文到章节**：在第 100 章提取上下文后，希望将结果复制到第 105 章复用，避免重复提取，且 source 描述应标明「复制自第100章」。
2. **回溯上下文同步注入**：续写时若回溯窗口内某些章节已有提取结果，希望自动将最新一章的提取结果作为独立 system 消息注入提示词，便于跨章节上下文衔接；需提供开关供用户自定义开启或关闭。

### 核心改动

1. **功能 1：复制上下文到章节**
   - **`novelforge/models/context.py`**：`ContextEntry` 新增 `copied_from: int | None = None` 字段，记录复制来源章节 index；非 None 时 UI source 描述显示「复制自第N章」前缀，None 时回退原 `source_chapter_range` 描述或「source=导入」。字段向后兼容（旧条目反序列化时默认 None）。
   - **`novelforge/ui/context_preview_panel.py`**：
     - 新增 `copy_to_chapter_requested = Signal()` 信号 + `_copy_to_chapter_btn` 按钮（"复制到章节"）+ `_on_copy_to_chapter_clicked` 槽（仅 emit 信号由 MainWindow 处理）
     - 新增 `get_all_entries()` 方法返回全部条目（含禁用），与 `get_entries()`（仅启用）区分，复制时需含禁用条目保证完整迁移
     - 条目元数据 source 描述拼接逻辑：`copied_from` 非 None 时优先显示「复制自第N章（原第X-Y章）」或「复制自第N章」，None 时回退「第X-Y章」或「导入」
     - `_EntryEditorDialog.get_entry()` 透传 `copied_from` 字段，编辑时保留复制来源
   - **`novelforge/ui/main_window.py`**（`_on_copy_context_to_chapter` 方法）：
     - 信号接线 `context_panel.copy_to_chapter_requested.connect(self._on_copy_context_to_chapter)`
     - `QInputDialog.getItem` 弹出章节列表选择对话框（排除当前章，按 index 排序展示「第N章 标题」）
     - 空条目保护：当前章无上下文时调 `_on_toast_requested("当前章节无上下文条目可复制")` 并 return
     - 每条 `model_copy(update={uid: generate_id("ctx_"), copied_from: source_index})` 生成新 uid 避免目标章冲突，保留原 `source_chapter_range`
     - 目标章现有条目保留，复制条目追加在后（合并 = existing + copied）
     - 加载目标章现有条目：内存 LRU `_context_entries_by_chapter` 优先，未命中同步查 SQLite（`load_cached_entries`，timeout=5）
     - `save_edited_entries` 非阻塞持久化到目标章 cache_key（续写模式 `exclude_current=False`，`asyncio.run_coroutine_threadsafe` 提交到 `AsyncLoopRunner` 后台循环）
     - 更新内存 LRU 含 `MAX_CONTEXT_CACHE_SIZE` 淘汰保护

2. **功能 2：同步注入回溯上下文**
   - **`novelforge/core/config.py`**：`DEFAULT_CONFIG["continuation"]["inject_lookback_context"] = False` 默认关闭
   - **`novelforge/ui/continuation_panel.py`**：
     - 新增 `_inject_lookback_context_check` QCheckBox「同步注入回溯上下文」，默认 False，tooltip 说明取数规则
     - `get_parameters()` 返回 `inject_lookback_context` 布尔值
     - `apply_parameters()` 从 params 恢复勾选状态
   - **`novelforge/ui/main_window.py`**：
     - `_get_latest_lookback_context(lookback, current_chapter) -> tuple[list, int] | None`：窗口计算镜像 `ContextExtractor._get_lookback_chapters` 但**排除当前章**（当前章条目已通过常规 `context_entries` 注入，避免重复）；lookback=0 取当前章前全部章节，lookback>0 取 `[current_idx - lookback + 1, current_idx)` 区间；按 index 降序遍历（最新章节优先），内存 LRU 优先、未命中同步查 SQLite，返回首个含 `enabled=True` 条目的章节 `(entries, chapter_index)`；窗口内无任何章有结果时返回 None
     - 配置持久化：3 处读写 `continuation.inject_lookback_context`（`_apply_continuation_defaults` 加载 / `_on_start_continuation`+`_on_start_rewrite_analysis_accepted` 写入）
     - 3 处 `prompt_assembler.assemble` 调用点（单章续写生成 L1969 / 续写预览 L3862 / 重写生成 L6155）均新增逻辑：`params.get("inject_lookback_context")` 为 True 时调 `_get_latest_lookback_context`，返回非 None 时传 `lookback_context_entries` + `lookback_context_source_index` 给 assemble
   - **`novelforge/core/prompt_assembler.py`**：
     - `assemble()` 新增 `lookback_context_entries: list[ContextEntry] | None = None` + `lookback_context_source_index: int | None = None` 参数
     - 新增 `_build_lookback_context_message(entries, source_index) -> dict | None`：镜像 `_build_world_info_message` 的分组/Markdown 逻辑，但仅保留 `enabled` 且 position 为 before/after 且 content 非空的条目（at_depth 不纳入，before+after 合并统一注入为单条消息）；首行标题 `# 前章上下文参考（第N章提取）`（source_index 为 None 时用「前文」）；按 category 分组，组内按 order 升序，category 用 `_CATEGORY_LABELS` 中文标签；无条目时返回 None
     - 注入位置优先级：worldInfoBefore marker 之后 > chatHistory marker 之前 > 兜底历史之前；`lookback_injected` 标记保证仅注入一次（front_prompts + back_prompts 多处 marker 均检查）

### 测试

- 新增 `tests/test_context_copy_to_chapter.py`（17 用例，5 测试类）：
  - `TestCopiedFromField`（4 用例）：默认值/序列化往返/None 可省略
  - `TestModelCopyForCopyFeature`（3 用例）：model_copy 生成新 uid+保留字段/保留禁用态/批量唯一 uid
  - `TestGenerateId`（3 用例）：ctx_ 前缀/100 次唯一/非空 hex
  - `TestSourceDescriptionLogic`（5 用例）：复制+原范围/仅复制/原范围/导入/copied_from 优先级
  - `TestCopyMergeSemantics`（2 用例）：现有条目+复制追加合并/空目标章独立构成
- 新增 `tests/test_lookback_context_injection.py`（20 用例，3 测试类）：
  - `TestBuildLookbackContextMessage`（11 用例）：None/空/全禁用/全 at_depth/空 content 返回 None + 基本结构/默认标签/仅 before/order 排序/at_depth 过滤/禁用过滤
  - `TestAssembleLookbackInjection`（8 用例）：无参数不注入/注入 after worldInfoBefore/与 worldInfoBefore 并存/无 worldInfoBefore 时注入 chatHistory 前/空列表不注入/全禁用不注入/system role/仅注入一次
  - `TestLookbackAndCurrentCoexist`（1 用例）：当前章 before+after + 回溯消息三者并存
- 全部 37 用例通过，无 Qt 依赖纯 Python 单元测试

### 文档同步

- `agent.md`：更新 L23（context.py 描述补 copied_from 字段）/ L33（prompt_assembler.py 描述补 lookback_context_entries 参数与 _build_lookback_context_message）/ L61（main_window.py 描述补 _on_copy_context_to_chapter + _get_latest_lookback_context + 3 处 assemble 注入点）/ L62（continuation_panel.py 描述补 inject_lookback_context_check）/ L64（context_preview_panel.py 描述补复制按钮与 source 拼接逻辑）；新增设计决策第 22 条「上下文复制到章节与回溯上下文同步注入」；测试要求章节新增功能 1/2 测试说明

## 2026-08-01：修复提取的上下文 24 小时后消失

### 背景

用户反馈提取的上下文（条目/主角形象/自定义角色档案）在 24 小时后消失。排查发现 `novelforge/core/storage.py` 的 `cache` 表带 `expires_at` 过期字段，`get_cache` 读取时若 `datetime.now() > expires` 会**直接 `DELETE FROM cache WHERE key=?` 并返回 None**。该 TTL 由 `context_extractor.py` 写入时设置（`DEFAULT_CACHE_TTL_HOURS = 24`，三处提取流程读 `config.cache_ttl_hours`，`save_edited_entries` 走默认常量兜底）。

此 TTL 与基于 `chapters_hash` 的内容失效机制重复且有害：缓存本就有正确的失效判断——源章节内容变化导致 hash 不匹配时自动重提取。TTL 在此之上加时间过期纯属冗余，且导致用户手动编辑的条目也在 24 小时后丢失，与 `save_edited_entries` 文档串"避免编辑丢失"的设计意图直接冲突。`cache_ttl_hours` 配置字段无 UI 入口（Settings 已移除「上下文提取」组），属死配置。

### 核心改动

1. **`novelforge/services/context_extractor.py`**
   - `DEFAULT_CACHE_TTL_HOURS = 24` → `0`（0 表示永不过期）
   - 三处提取流程（`_extract_common_body`/`_extract_protagonist_body`/`_extract_custom_character_body` 的 config 读取处）将 `cache_ttl_hours = int(config.get("cache_ttl_hours", DEFAULT_CACHE_TTL_HOURS))` 替换为 `cache_ttl_hours = 0`。强制 0 让旧用户配置文件中已持久化的 `cache_ttl_hours: 24` 失效，保证新旧用户一致（仅改默认常量无法修复存量用户，因 `config.get` 会返回已持久化的 24）
   - `save_edited_entries`：`effective_ttl = ttl_hours if ttl_hours is not None else DEFAULT_CACHE_TTL_HOURS` → `effective_ttl = 0`（用户编辑是明确意图，永不过期）；`ttl_hours` 参数签名保留向后兼容，docstring 标注已废弃
   - 两处日志文案 `TTL=%dh` → `永不过期`，并移除对应 printf 参数
   - 模块 docstring「缓存有效期默认 24 小时」→「缓存永不过期（依赖 chapters_hash 判断失效）」

2. **不改 `novelforge/core/storage.py`**：`set_cache` 已正确支持 `ttl_hours=0`（`expires = now + timedelta(hours=ttl_hours) if ttl_hours > 0 else None` → `expires_at=NULL`），`get_cache` 仅在 `expires_at` 非空时检查过期（`if expires_at:`），`expires_at=NULL` 时永不过期。storage 层无需改动。

3. **不删除 `cache_ttl_hours` 配置字段**：保留 `config.py` 默认值与 `project.py` 文档，避免 config 迁移与跨文件改动。字段成为死配置（无 UI 入口、代码不再读取），仅在 agent.md 标注废弃。这是最小爆炸半径选择。

### 测试

- 新增回归测试 `tests/test_m4_context_extraction.py::TestContextCacheTTLNeverExpires`：验证 `save_edited_entries` 保存后 `expires_at IS NULL`，且 `get_cache` 持续返回非 None（含模拟 25 小时后场景对比旧 `ttl_hours=24` 行为会删除）
- `tests/test_m4_context_extraction.py` / `test_protagonist_extraction.py` / `test_custom_character_extraction.py` / `test_rewrite_current_mode.py` 全部通过，无回归（这些测试 mock 配置含 `cache_ttl_hours: 24`，改动后该键被忽略，测试仍通过）

### 文档同步

- `agent.md`：更新 L43（context_extractor.py 描述）与 L134（设计决策 #3「条目编辑自动持久化」），补充三类缓存永不过期、依赖 `chapters_hash` 失效、修复 24h 消失 bug、`cache_ttl_hours` 字段废弃说明

## 2026-07-31：修复网络代理在多数流程未生效的 bug

### 背景

用户反馈在设置中配置 `127.0.0.1:7890` 代理后未生效。排查发现 `novelforge/ui/main_window.py` 中 7 处 worker 实例化点，仅 2 处（重写需求分析、通用分析）传入了 `proxy=self._get_network_proxy()`，其余 5 处缺失该参数。由于 `_get_network_proxy()` 辅助方法与 worker 构造函数的 `proxy` 形参均已就绪，漏传属机械性遗漏，导致用户配置的 HTTP 代理在最常用的 5 个流程中被静默忽略：单章续写、卷续写（含全部 7 子阶段）、单章审计、审计后修正重写、重写当前章节生成。

agent.md 第 21 条「网络代理」早已声明"main_window 7 处 worker 实例化点（`_get_network_proxy()` 辅助方法统一读取配置）"，但实际仅 2/7 处使用，文档与代码长期不一致。`tests/test_http_proxy.py` 19 用例仅覆盖 config/UI/LLMClient/ModelFetchWorker 参数存储，未覆盖 MainWindow→worker 接线，故 bug 未被发现。

### 核心改动

1. **补齐 5 处 worker 实例化的 proxy 参数**（`novelforge/ui/main_window.py`）
   - `_on_start_continuation`（单章续写 ContinuationWorker，L2076）
   - `_start_volume_phase`（卷续写 VolumeOrchestrator，L2498）
   - `_on_start_single_audit`（单章审计 AuditWorker，L5310）
   - `_on_audit_accepted`（审计后修正 ContinuationWorker，L5553）
   - `_on_rewrite_analysis_accepted`（重写生成 ContinuationWorker，L6063）
   - 每处于 `parent=self,` 前插入 `proxy=self._get_network_proxy(),`，与已正确的 2 处（重写需求分析 L5789、通用分析 L6242）写法对齐。修复后 7/7 处统一透传代理。

2. **新增回归测试**（`tests/test_http_proxy.py`，+5 用例共 24 用例）
   - `test_get_network_proxy_*`（4 用例）：用 `MainWindow.__new__(MainWindow)` 裸实例 + 桩 `config_manager` 单元测试 `_get_network_proxy()` 的开关 on/off × URL 空/非空组合
   - `test_all_worker_instantiations_pass_proxy`（1 用例）：静态扫描 `main_window.py` 源码，按括号深度提取每个 worker 构造调用的参数块，断言 7 个调用（3 ContinuationWorker + 3 AuditWorker + 1 VolumeOrchestrator）均含 `proxy=self._get_network_proxy()`，防止未来新增/修改 worker 实例化点时再次漏传

### 测试

- `tests/test_http_proxy.py` 24 用例全部通过（19 原有 + 5 新增）
- `tests/test_rewrite_current_mode.py` / `test_single_audit.py` / `test_reasoning_effort.py` / `test_volume_*.py` 共 208 用例通过，续写/重写/审计/卷续写流程无回归
- 注：`test_rewrite_current_mode.py::test_flow_definitions_count_is_11` 失败为预存在无关失败（FLOW_DEFINITIONS 已增至 12 项而该计数测试未同步），与本次代理修复无关

### 文档同步

- `agent.md`：第 21 条「网络代理」原文已声明 7 处实例化点统一读取配置，修复后实际与声明一致，无需改文案
- `update.md`：本条目

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
