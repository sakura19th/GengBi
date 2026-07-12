# 流程插件使用说明

本文档面向希望编写自定义流程插件的开发者，完整描述插件 JSON 格式、合法取值、编写规律与案例。

---

## 1. 概述

**流程插件**是赓笔的声明式流程编排机制。每个插件是一个 JSON 文件，描述"续写流程由哪些阶段组成、每阶段用什么 agent、用什么端点/模型/破限"。插件**不含可执行代码**，安全可分享。

### 与预设的关系

插件与预设**正交**，各管一件事：

| 维度 | FlowPlugin | WritingPreset |
|------|-----------|---------------|
| 职责 | 流程编排（阶段顺序 + agent 类型 + flow_key + accept_mode） | 提示词内容 + 生成参数（max_tokens/max_context） |
| 存储 | `~/.novelforge/flow_plugins/{id}.json` | `~/.novelforge/presets/{id}.json` |
| 关键字段 | stages / agent / ui_mode / accept_mode | prompts / prompt_order / generation_params |

插件管"**怎么写**"（流程编排），预设管"**写什么**"（提示词 + 生成参数）。二者互不覆盖。

### 存储位置

- 用户插件：`~/.novelforge/flow_plugins/{plugin_id}.json`
- 内置插件资源：`novelforge/resources/defaults/flow_plugins/{id}.json`（首启复制到用户目录）
- 每个插件一个独立 JSON 文件，文件名 = `{plugin_id}.json`

---

## 2. 快速开始

### 导入自定义插件

1. 在外部编辑器编写插件 JSON 文件（格式见第 3 节）
2. 打开赓笔 → 工具菜单 → **流程插件管理器**（或快捷键 `Alt+T, F`）
3. 点击 **导入** 按钮，选择 JSON 文件
4. 导入成功后，续写面板的模式下拉框自动出现新插件

> 管理器**不提供**新建/编辑功能。插件是声明式 JSON，用户在外部编辑器手写后导入。

### 使用插件

1. 在续写面板顶部模式下拉框选择插件
2. 根据插件 `ui_mode`，面板显示标准续写配置区（`standard`）或卷续写面板（`volume`）
3. 点击"开始续写"按钮启动流程
4. 流程按插件 `stages` 顺序执行，部分阶段会弹窗等待用户交互（如 AuditDialog）

### 4 个内置插件

| 插件 ID | 名称 | 阶段数 | 说明 |
|---------|------|--------|------|
| `single` | 单次续写 | 1 | 单阶段流式续写，接受后提升为新章节 |
| `volume` | 卷续写（多章节） | 4 | 深度分析→卷大纲→审计→逐章写作 |
| `rewrite_current` | 重写当前章节 | 2 | 分析→生成，接受后替换当前章节正文 |
| `writing_mode` | 写作模式 | 3 | 写作要素分析→写作要素深化→单章生成 |

---

## 3. JSON 格式规范

插件 JSON 由顶层 `FlowPlugin` 对象和 `stages` 数组中的多个 `FlowStage` 对象组成。

### FlowPlugin 顶层字段

| 字段 | 类型 | 必填 | 默认值 | 校验规则 | 说明 |
|------|------|------|--------|----------|------|
| `id` | string | 是 | — | 禁含 `/` `\` `..` `\x00` | 插件唯一标识（也成为文件名） |
| `name` | string | 是 | — | — | 插件显示名称（下拉框展示） |
| `description` | string | 否 | `""` | — | 插件描述（管理器详情展示） |
| `version` | string | 否 | `"1.0"` | — | 版本号（内置插件升级用语义比较，如 `"2.0"` > `"1.0"`） |
| `author` | string | 否 | `""` | — | 作者 |
| `builtin` | bool | 否 | `false` | 导入时强制改为 `false` | 是否内置插件（内置不可删除，用户导入的永远为 `false`） |
| `ui_mode` | string | 否 | `"standard"` | `standard` / `volume` | UI 模式，控制续写面板显隐 |
| `accept_mode` | string | 否 | `"promote"` | `promote` / `replace` / `volume_insert` | 接受续写时的行为 |
| `stages` | array | 否 | `[]` | — | 有序阶段列表（FlowStage 对象数组） |

### FlowStage 阶段字段

| 字段 | 类型 | 必填 | 默认值 | 校验规则 | 说明 |
|------|------|------|--------|----------|------|
| `id` | string | 是 | — | 插件内唯一 | 阶段标识（供其他阶段的 `input_from` 引用） |
| `name` | string | 否 | `""` | — | 阶段显示名称 |
| `agent` | string | 是 | — | 5 种合法值（见第 4 节） | agent 类型，决定调用哪个 handler |
| `flow_key` | string | 否 | `""` | 自由字符串（建议用 8 个标准值） | 端点/模型/破限选择键 |
| `streaming` | bool | 否 | `true` | — | 是否流式输出 |
| `created_by` | string | 否 | `""` | — | swipe 来源标记，控制 accept 行为与 continuation 分支 |
| `params` | object | 否 | `{}` | 无校验 | 阶段参数（覆盖面板同名键，见规律 2） |
| `input_from` | string | 否 | `""` | 引用前序阶段 id | 上一阶段输出作为本阶段 `_prev_output`（空串=用面板参数） |

---

## 4. 合法取值速查

### 4.1 五种 agent 类型

`agent` 字段必须在以下 5 种取值中（定义于 `flow_plugin.py:62-64` 的 `VALID_AGENT_TYPES`）：

| agent | 对应 handler | 行为 | 返回值 | 创建的 worker |
|-------|-------------|------|--------|--------------|
| `continuation` | `_flow_handler_continuation` | 流式续写创建 swipe。若 `params._prev_output` 非空且 `created_by=="rewrite_current"`，走重写生成分支；若 `created_by=="writing_mode"`，走写作模式第 3 步（精炼输出前置【写作参考】到 user_input）；否则走独立续写 | `None`（推进下一阶段） | ContinuationWorker |
| `audit` | `_flow_handler_audit` | 低温分析，弹 AuditDialog 供用户审阅采纳。`flow_key=="rewrite_analysis"` 走 `_on_start_rewrite_current` 原路径；其余（如 `writing_element_analysis`/`writing_element_refinement`）走 `_on_start_generic_analysis` 通用分析路径（读 `params._prev_output` 注入 `{{prev_analysis}}`，采纳后 `flow_executor.resume` 推进） | `"pending"`（挂起，等用户采纳后 resume） | AuditWorker |
| `checkpoint` | `_flow_handler_checkpoint` | 暂停点（占位实现，直接推进下一阶段） | `"continue"` | 无 |
| `volume_pipeline` | `_flow_handler_volume` | 旧版卷续写 7 阶段（向后兼容，新版用 `volume_phase`） | `None` | VolumeOrchestrator |
| `volume_phase` | `_flow_handler_volume_phase` | 卷续写单阶段执行，emit `phase_output` 后挂起，由 main_window resume 推进 | `"pending"` | VolumeOrchestrator |

### 4.2 两种 ui_mode

`ui_mode` 字段必须在以下 2 种取值中（`flow_plugin.py:67` 的 `VALID_UI_MODES`）：

- **`standard`**：标准续写配置区（温度/字数/回溯/预设/模型下拉）
- **`volume`**：卷续写面板（隐藏标准配置区，显示卷产物查看器）

### 4.3 三种 accept_mode

`accept_mode` 字段必须在以下 3 种取值中（`flow_plugin.py:70` 的 `VALID_ACCEPT_MODES`）：

- **`promote`**：接受续写时，提升为新章节插入当前章节之后
- **`replace`**：替换当前章节正文（重写模式）
- **`volume_insert`**：卷续写内部自建章节（accept 不触发，由卷流程内部处理）

### 4.4 十个标准 flow_key

`flow_key` 字段是自由字符串（模型层不校验），但建议使用以下 10 个标准值（定义于 `flow_endpoint_dialog.py:34-45` 的 `FLOW_DEFINITIONS`）：

| flow_key | 显示名 | 类别 | 破限配置入口 | 默认破限等级 |
|----------|--------|------|------------|-------------|
| `single_continuation` | 单章续写 | 正文流程 | 预设管理器勾选 `nf_jb_*` 模块 | 由预设控制 |
| `volume_continuation` | 卷续写 | 正文流程 | 预设管理器勾选 `nf_jb_*` 模块 | 由预设控制 |
| `single_audit` | 单章审计 | 非正文流程 | 流程端点配置等级下拉 | `off` |
| `rewrite_analysis` | 重写当前章节分析 | 非正文流程 | 流程端点配置等级下拉 | `off` |
| `context_extraction` | 上下文提取 | 非正文流程 | 流程端点配置等级下拉 | `low` |
| `ontology_extraction` | 世界观底层提取 | 非正文流程 | 流程端点配置等级下拉 | `low` |
| `protagonist_extraction` | 主角形象提取 | 非正文流程 | 流程端点配置等级下拉 | `low` |
| `custom_rule_parsing` | 自定义设定解析 | 非正文流程 | 流程端点配置等级下拉 | `off` |
| `writing_element_analysis` | 写作要素分析 | 非正文流程 | 流程端点配置等级下拉 | `low` |
| `writing_element_refinement` | 写作要素深化 | 非正文流程 | 流程端点配置等级下拉 | `low` |

> **说明**：正文流程（`single_continuation`/`volume_continuation`）的破限由预设管理器勾选 `nf_jb_*` 模块控制；非正文流程的破限由流程端点配置对话框的等级下拉控制（off/low/mid/high/custom）。未配置的 flow_key 回退默认端点 + 端点 default_model。

---

## 5. 写插件的 6 大规律

### 规律 1：阶段顺序执行，支持挂起恢复

FlowExecutor 按 `stages` 列表顺序依次执行阶段。每个 handler 的返回值决定下一步：

- 返回 `None` 或 `"continue"` → 推进下一阶段
- 返回 `"pending"` → 挂起，等待 `flow_executor.resume(output)` 推进
- 返回 `"cancel"` → 中断整个流程

典型挂起场景：`audit` agent 弹 AuditDialog，用户采纳后 `_on_rewrite_analysis_accepted` 调 `resume` 推进；`volume_phase` agent emit `phase_output` 后 `_on_volume_phase_output` 调 `resume` 推进。

### 规律 2：params 合并优先级

FlowExecutor 在执行每阶段前合并参数（`flow_executor.py:153`）：

```python
stage_params = {**面板params, **阶段params}  # 阶段 params 覆盖面板同名键
```

- **面板 params**（来自续写面板 `get_parameters()`）：`temperature` / `target_words` / `lookback_chapters` / `preset_id` / `top_p` / `frequency_penalty` / `presence_penalty` / `model` / `_flow_plugin_id`
- **阶段 params**（来自 stage.params）：常用 `exclude_current`（bool，重写时排除当前章节）、`phase`（str，volume_phase 阶段名）

阶段 params 优先级高于面板 params，但**不影响预设 generation_params**（见规律 6）。

### 规律 3：input_from 链式传递

`input_from` 字段实现阶段间数据传递：

```json
{"id": "analysis", "agent": "audit", "input_from": ""},
{"id": "generate", "agent": "continuation", "input_from": "analysis"}
```

- `input_from: ""` → 用面板参数，无前序输出
- `input_from: "analysis"` → 取 id=analysis 阶段的输出作为本阶段 `params._prev_output`

`continuation` agent 会检查 `_prev_output`：若非空且 `created_by=="rewrite_current"`，走重写生成分支（`_on_rewrite_analysis_accepted`）；否则走独立续写（`_on_start_continuation`）。

### 规律 4：flow_key 决定端点/模型/破限

每个 `flow_key` 对应流程端点配置中的一行（端点 + 模型 + 破限等级）。运行时解析顺序：

- `get_flow_endpoint(flow_key)` → 未配置回退默认端点
- `get_flow_model(flow_key)` → 未配置或空串回退端点 `default_model`
- `get_flow_jailbreak(flow_key)` → 未配置回退 `FLOW_DEFAULT_JAILBREAKS` 默认值

正文流程（`single_continuation`/`volume_continuation`）的破限由预设管理器勾选 `nf_jb_*` 模块控制，不在流程端点配置中。非正文流程的破限由流程端点配置对话框的等级下拉控制。

### 规律 5：accept_mode 决定接受行为

用户接受续写时（`_on_accept_continuation`），从 `swipe.parameters_snapshot._flow_plugin_id` 反查插件的 `accept_mode`：

- `promote` → 提升为新章节插入当前章之后
- `replace` → 替换当前章节正文
- `volume_insert` → 卷续写内部自建章节，accept 不触发

旧 swipe 若无 `_flow_plugin_id`，按 `created_by` 推断：`rewrite_current` → `replace`，其余 → `promote`。

### 规律 6：插件与预设正交

插件管"**怎么写**"（流程编排），预设管"**写什么**"（提示词 + 生成参数）：

- 插件 `stage.params` **不覆盖**预设 `generation_params`
- `max_tokens` 只从预设读（`gen_params.get("max_tokens", 2000)`）
- `max_context` 可被面板 params 覆盖（`params.get("max_context") or gen_params...`）
- `temperature` / `target_words` / `lookback_chapters` 从面板 params 读

---

## 6. volume_phase 阶段顺序

使用 `volume_phase` agent 的插件（如内置 `volume` 插件），4 阶段固定顺序（`main_window.py:_get_next_volume_phase`）：

| 序号 | phase 值 | 阶段名 | 说明 |
|------|----------|--------|------|
| 1 | `deep_analysis` | 前文深度分析 | 分析前文章节，生成 DeepAnalysis 产物 |
| 2 | `volume_outline` | 卷大纲生成 | 基于 DeepAnalysis 生成 VolumeOutline |
| 3 | `outline_audit` | 大纲审计与终稿 | 审计大纲，生成 OutlineAuditReport + 终稿大纲 |
| 4 | `chapter_writing` | 逐章写作 | 按终稿大纲逐章写作，生成 ChapterArtifacts |

**编写要求**：
- 每阶段的 `params.phase` 必须匹配上表的 phase 值
- `input_from` 链式引用前序阶段 id：阶段 1 为 `""`，阶段 2 引用阶段 1 的 id，依此类推
- `accept_mode` 必须为 `volume_insert`（卷续写内部自建章节）
- `ui_mode` 必须为 `volume`（显示卷续写面板）

---

## 7. 四个内置插件完整 JSON

### 7.1 single.json — 单次续写

最简单的单阶段插件，`continuation` agent 流式续写，接受后提升为新章节。

```json
{
  "id": "single",
  "name": "单次续写",
  "description": "单章节流式续写，接受后提升为新章节插入当前章之后",
  "version": "1.0",
  "author": "GengBi",
  "builtin": true,
  "ui_mode": "standard",
  "accept_mode": "promote",
  "stages": [
    {
      "id": "write",
      "name": "续写",
      "agent": "continuation",
      "flow_key": "single_continuation",
      "streaming": true,
      "created_by": "continuation",
      "params": {},
      "input_from": ""
    }
  ]
}
```

**要点**：
- 单阶段，`agent=continuation`，`flow_key=single_continuation`
- `accept_mode=promote`：接受后插入新章节
- `created_by="continuation"`：swipe 来源标记

### 7.2 volume.json — 卷续写 v2.0

4 阶段卷续写，全部使用 `volume_phase` agent，每阶段 `params.phase` 指定阶段名，`input_from` 链式传递。

```json
{
  "id": "volume",
  "name": "卷续写（多章节）",
  "description": "卷级多章节续写：深度分析→卷大纲→审计→逐章写作",
  "version": "2.0",
  "author": "GengBi",
  "builtin": true,
  "ui_mode": "volume",
  "accept_mode": "volume_insert",
  "stages": [
    {
      "id": "deep_analysis",
      "name": "前文深度分析",
      "agent": "volume_phase",
      "flow_key": "volume_continuation",
      "streaming": true,
      "created_by": "volume",
      "params": {"phase": "deep_analysis"},
      "input_from": ""
    },
    {
      "id": "volume_outline",
      "name": "卷大纲生成",
      "agent": "volume_phase",
      "flow_key": "volume_continuation",
      "streaming": true,
      "created_by": "volume",
      "params": {"phase": "volume_outline"},
      "input_from": "deep_analysis"
    },
    {
      "id": "outline_audit",
      "name": "大纲审计与终稿",
      "agent": "volume_phase",
      "flow_key": "volume_continuation",
      "streaming": true,
      "created_by": "volume",
      "params": {"phase": "outline_audit"},
      "input_from": "volume_outline"
    },
    {
      "id": "chapter_writing",
      "name": "逐章写作",
      "agent": "volume_phase",
      "flow_key": "volume_continuation",
      "streaming": true,
      "created_by": "volume",
      "params": {"phase": "chapter_writing"},
      "input_from": "outline_audit"
    }
  ]
}
```

**要点**：
- 4 阶段全部 `volume_phase` agent，`flow_key` 统一为 `volume_continuation`
- `params.phase` 依次为 `deep_analysis` / `volume_outline` / `outline_audit` / `chapter_writing`
- `input_from` 链：`""` → `deep_analysis` → `volume_outline` → `outline_audit`
- `ui_mode=volume`：显示卷续写面板
- `accept_mode=volume_insert`：卷续写内部自建章节

### 7.3 rewrite_current.json — 重写当前章节

2 阶段流程：先 `audit` 分析当前章节，用户采纳后 `continuation` 基于分析结果重写。

```json
{
  "id": "rewrite_current",
  "name": "重写当前章节",
  "description": "分析→生成两步流程，接受后替换当前章节正文",
  "version": "1.0",
  "author": "GengBi",
  "builtin": true,
  "ui_mode": "standard",
  "accept_mode": "replace",
  "stages": [
    {
      "id": "analysis",
      "name": "重写分析",
      "agent": "audit",
      "flow_key": "rewrite_analysis",
      "streaming": true,
      "created_by": "",
      "params": {"exclude_current": true},
      "input_from": ""
    },
    {
      "id": "generate",
      "name": "重写生成",
      "agent": "continuation",
      "flow_key": "single_continuation",
      "streaming": true,
      "created_by": "rewrite_current",
      "params": {"exclude_current": true},
      "input_from": "analysis"
    }
  ]
}
```

**要点**：
- 阶段 1 `agent=audit`，`flow_key=rewrite_analysis`（低温稳定分析），`params.exclude_current=true`（排除当前章节避免自我参照）
- 阶段 2 `agent=continuation`，`created_by="rewrite_current"`（触发重写生成分支），`input_from="analysis"`（接收分析文本作为 `_prev_output`）
- `accept_mode=replace`：接受后替换当前章节正文

### 7.4 writing_mode.json — 写作模式

3 阶段流程：先 `audit` 分析写作要素，再 `audit` 深化角色形象，最后 `continuation` 基于精炼输出单章生成。

```json
{
  "id": "writing_mode",
  "name": "写作模式",
  "description": "三步续写：写作要素分析→写作要素深化→单章生成，接受后提升为新章节",
  "version": "1.0",
  "author": "GengBi",
  "builtin": true,
  "ui_mode": "standard",
  "accept_mode": "promote",
  "stages": [
    {
      "id": "analysis",
      "name": "写作要素分析",
      "agent": "audit",
      "flow_key": "writing_element_analysis",
      "streaming": true,
      "created_by": "",
      "params": {
        "phase": "writing_element_analysis",
        "phase_name": "写作要素分析"
      },
      "input_from": ""
    },
    {
      "id": "refinement",
      "name": "写作要素深化",
      "agent": "audit",
      "flow_key": "writing_element_refinement",
      "streaming": true,
      "created_by": "",
      "params": {
        "phase": "writing_element_refinement",
        "phase_name": "写作要素深化"
      },
      "input_from": "analysis"
    },
    {
      "id": "generate",
      "name": "单章生成",
      "agent": "continuation",
      "flow_key": "single_continuation",
      "streaming": true,
      "created_by": "writing_mode",
      "params": {},
      "input_from": "refinement"
    }
  ]
}
```

**要点**：
- 阶段 1 `agent=audit`，`flow_key=writing_element_analysis`，`params.phase=writing_element_analysis`（决定模板文件名 `phase_writing_element_analysis.txt`），`input_from=""`（无前序输入，用面板参数）
- 阶段 2 `agent=audit`，`flow_key=writing_element_refinement`，`params.phase=writing_element_refinement`（模板 `phase_writing_element_refinement.txt`），`input_from="analysis"`（接收阶段 1 输出注入 `{{prev_analysis}}`）
- 阶段 3 `agent=continuation`，`created_by="writing_mode"`（触发写作模式第 3 步分支 `_on_start_writing_mode_continuation`），`input_from="refinement"`（接收阶段 2 输出前置【写作参考】到 user_input）
- `accept_mode=promote`：接受后提升为新章节
- 与 rewrite_current 的区别：3 阶段（多一层深化）、`accept_mode=promote`（非 replace）、阶段 3 走 `_on_start_writing_mode_continuation` 而非 `_on_rewrite_analysis_accepted`、前文含当前章（续写下一章而非重写当前章）

---

## 8. 自定义插件案例：先分析再续写

以下是一个自定义插件，先用 `audit` agent 低温分析当前章节风格，用户采纳后 `continuation` agent 基于分析结果流式续写，接受后提升为新章节。

```json
{
  "id": "analyze_then_write",
  "name": "先分析再续写",
  "description": "先用低温分析当前章节风格，再基于分析结果流式续写",
  "version": "1.0",
  "author": "用户",
  "builtin": false,
  "ui_mode": "standard",
  "accept_mode": "promote",
  "stages": [
    {
      "id": "analyze",
      "name": "风格分析",
      "agent": "audit",
      "flow_key": "single_audit",
      "streaming": true,
      "created_by": "",
      "params": {},
      "input_from": ""
    },
    {
      "id": "write",
      "name": "基于分析续写",
      "agent": "continuation",
      "flow_key": "single_continuation",
      "streaming": true,
      "created_by": "continuation",
      "params": {},
      "input_from": "analyze"
    }
  ]
}
```

### 案例解析

| 字段 | 值 | 说明 |
|------|-----|------|
| `id` | `analyze_then_write` | 自定义 ID（不含路径字符） |
| `builtin` | `false` | 自定义插件（导入时强制为 false） |
| `ui_mode` | `standard` | 标准续写配置区 |
| `accept_mode` | `promote` | 接受后提升为新章节 |
| 阶段 1 `agent` | `audit` | 低温分析，弹 AuditDialog 供用户审阅 |
| 阶段 1 `flow_key` | `single_audit` | 用单章审计的端点/模型/破限配置 |
| 阶段 1 `input_from` | `""` | 无前序输入，用面板参数 |
| 阶段 2 `agent` | `continuation` | 流式续写 |
| 阶段 2 `flow_key` | `single_continuation` | 用单章续写的端点/模型/破限配置 |
| 阶段 2 `created_by` | `continuation` | 独立续写（非重写，因为不是 `rewrite_current`） |
| 阶段 2 `input_from` | `analyze` | 接收阶段 1（analyze）的分析文本作为 `_prev_output` |

### 执行流程

1. 用户在续写面板选择"先分析再续写"插件，点击开始
2. **阶段 1（analyze）**：`audit` agent 启动 AuditWorker，用 `single_audit` 的端点/模型/破限配置低温分析当前章节风格，流式输出到 AuditDialog
3. 用户在 AuditDialog 审阅分析结果，点击"采纳"
4. `_on_rewrite_analysis_accepted` 调 `flow_executor.resume(分析文本)`，阶段 1 输出作为 `_prev_output` 传递给阶段 2
5. **阶段 2（write）**：`continuation` agent 检查 `_prev_output` 非空，但 `created_by="continuation"`（非 `rewrite_current`），走独立续写分支 `_on_start_continuation`，用 `single_continuation` 的端点/模型/破限配置流式续写
6. 流式输出到续写面板，用户接受后因 `accept_mode=promote`，提升为新章节插入当前章之后

> **与内置 `rewrite_current` 的区别**：本案例 `accept_mode=promote`（插入新章节）而非 `replace`（替换当前章）；阶段 2 `created_by="continuation"`（独立续写）而非 `"rewrite_current"`（重写生成）。

### 写作模式使用说明与预期效果

**使用说明**：
1. 在续写面板模式下拉选择「写作模式」
2. 在 user_input 框填写本次写作需求（如"主角与反派在酒楼对峙，揭示身世秘密"）
3. 点击开始续写，进入 3 阶段流程

**执行流程**（8 步）：
1. 阶段 1（analysis）：audit agent 加载 `phase_writing_element_analysis.txt`，注入 6 占位符（`user_input`/`world_ontology`/`protagonist_profile`/`custom_audit_rules`/`context_entries`/`previous_chapters_text`），AuditWorker 低温流式输出「出场角色/场所/相关事件/关键伏笔/风格基调」5 分节，弹 AuditDialog 供审阅
2. 用户审阅/编辑阶段 1 输出，点击「采纳」
3. `_on_generic_analysis_accepted` 调 `flow_executor.resume(分析文本)`，阶段 1 输出作为 `_prev_output` 传递给阶段 2
4. 阶段 2（refinement）：audit agent 加载 `phase_writing_element_refinement.txt`，注入 3 占位符（`prev_analysis`/`world_ontology`/`previous_chapters_text`），AuditWorker 流式输出「角色形象（外貌/心理学形象/语言风格/OOC红线）+ 场所精炼 + 其他关键要素」，弹 AuditDialog 供审阅
5. 用户审阅/编辑阶段 2 输出，点击「采纳」
6. `_on_generic_analysis_accepted` 调 `flow_executor.resume(精炼文本)`，阶段 2 输出作为 `_prev_output` 传递给阶段 3
7. 阶段 3（generate）：continuation agent 检查 `_prev_output` 非空且 `created_by=="writing_mode"`，走 `_on_start_writing_mode_continuation`，将精炼输出以【写作参考】标签包裹后前置到 user_input，调 `_on_start_continuation(user_input_override=combined)` 走单章续写
8. 流式输出到续写面板，用户接受后因 `accept_mode=promote`，提升为新章节插入当前章之后

**预期效果**：
- 阶段 1 输出：结构化的本次出场角色/场所/事件清单，用户可确认 AI 对写作需求的理解是否准确
- 阶段 2 输出：每个出场角色的简化版形象档案（外貌+心理学形象+语言风格+OOC红线），场所精炼与其他关键要素，用户可确认角色刻画方向
- 阶段 3 输出：严格遵循【写作参考】内容的单章正文，角色行为/语言符合阶段 2 刻画的形象，事件符合阶段 1 的规划

**与 rewrite_current 的对比**：

| 维度 | writing_mode | rewrite_current |
|------|---------------|-----------------|
| 阶段数 | 3（分析→深化→生成） | 2（分析→生成） |
| 分析对象 | 下一章写作要素（角色/场所/事件） | 当前章节重写需求 |
| 前文是否含当前章 | 是（续写下一章，当前章是末尾前文） | 否（当前章是待重写对象） |
| accept_mode | promote（插入新章节） | replace（替换当前章） |
| 第 3 步注入方式 | 前置【写作参考】到 user_input | 分析文本作为 user_input |

---

## 9. 调试与验证

### 导入校验

pydantic 校验失败会在导入时报错（管理器弹"导入失败"对话框）。常见校验错误：

- `非法 agent: {v}r，支持取值: audit/checkpoint/continuation/volume_phase/volume_pipeline` — agent 字段不在 5 种合法值中
- `非法 ui_mode: {v}r，支持取值: standard/volume` — ui_mode 字段不在 2 种合法值中
- `非法 accept_mode: {v}r，支持取值: promote/replace/volume_insert` — accept_mode 字段不在 3 种合法值中
- `非法 ID（含路径字符）: {v}r` — id 字段含 `/` `\` `..` `\x00`

### 检查 JSON 文件

导入成功后，插件 JSON 文件位于：

```
~/.novelforge/flow_plugins/{plugin_id}.json
```

可直接用文本编辑器查看/编辑（编辑后需重启程序或重新导入生效）。

### 内置插件版本升级

内置插件（`builtin=true`）在程序启动时会检查版本升级（`flow_plugin_service.py:_should_upgrade_builtin`）：

- 已安装 `builtin=true` 且资源 `version > 已安装 version` → 升级（覆盖用户目录文件）
- 已安装 `builtin=false`（用户修改过） → **不升级**，保留用户改动
- 用户目录无文件 → 首启复制

版本比较用元组解析（`"2.0"` → `(2, 0)`），解析失败返回 `(0, 0)`。

### 日志

流程执行日志（控制台输出）：

```
INFO: 执行阶段 1/2: analyze（agent=audit）
INFO: 阶段 analyze 挂起等待用户交互
INFO: 执行阶段 2/2: write（agent=continuation）
INFO: 流程插件执行完成: analyze_then_write
```

### 常见问题

**Q: 为什么我的插件导入后 builtin 变成了 false？**
A: 导入时强制设为 `false`（`flow_plugin_service.py:import_plugin`），防止伪装内置插件。内置插件不可删除，用户导入的永远可删。

**Q: ID 冲突怎么办？**
A: 导入时若目标 ID 已存在且 `overwrite=false`（默认），自动追加 `_imported` 后缀（如 `single_imported`）。若要覆盖同名插件，需先删除旧插件再导入。

**Q: 阶段 params 能覆盖预设的 max_tokens 吗？**
A: 不能。`max_tokens` 只从预设 `generation_params` 读，插件 params 无法覆盖。`max_context` 可被面板 params 覆盖（`params.get("max_context") or gen_params...`）。

**Q: 多个插件能同时激活吗？**
A: 不能。续写面板模式下拉是单选，`_on_start_flow` 只加载并执行一个插件。但多个插件可共存于注册表，用户在下拉中切换选择。

**Q: flow_key 能用自定义值吗？**
A: 模型层不校验 flow_key 取值，可填任意字符串。但运行时 `get_flow_endpoint`/`get_flow_model` 找不到配置会回退默认端点/模型，破限配置也无法生效。建议使用 8 个标准 flow_key。
