# 更新日志

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
