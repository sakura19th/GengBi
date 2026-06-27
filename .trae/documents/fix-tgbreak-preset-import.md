# TGbreak V3.1.1 预设导入兼容修复计划

## 摘要

修复 NovelForge 的 SillyTavern 预设导入逻辑，使其能完整解析 `TGbreak😺V3.1.1.json` 这类使用非标准约定的 ST 预设（含两个插件扩展：SPreset 和 tavern_helper），正确导入提示词、正则脚本、生成参数，并支持 ST 风格宏（`{{setvar::}}`、`{{getvar::}}`、`{{user}}`、`{{char}}`）。

## TGbreak JSON 结构分析

### 顶层字段
- `temperature`, `top_p`, `frequency_penalty`, `presence_penalty` — 标准生成参数
- `openai_max_context: 2000000` — 非标准字段名（标准为 `max_context`）
- `openai_max_tokens: 65535` — 非标准字段名（标准为 `max_tokens`）
- `prompts` — 提示词数组（~40 条）
- `prompt_order` — 排序配置，`character_id: 100001`（非标准的 100000）
- `extensions` — 扩展字段，包含三个子结构

### prompts 数组特点
- 每条 prompt 使用 `"marker": true` / `"marker": false`（布尔值，非字符串）
- `marker: true` 时，`identifier` 字段即为 marker 名（`worldInfoBefore`、`chatHistory`、`dialogueExamples`、`personaDescription`、`charDescription`、`charPersonality`、`scenario`、`worldInfoAfter`）
- `marker: false` 时为普通提示
- 大量使用 ST 宏：`{{setvar::name::value}}`、`{{getvar::name}}`、`{{user}}`、`{{char}}`、`{{// comment}}`

### prompt_order 特点
- 仅有一个 `character_id: 100001` 的分组（非全局 100000）
- order 数组含 ~50 条条目，部分 `enabled: false`

### extensions 结构（三个来源）
1. **`extensions.regex_scripts`**（行 2069-2306）：ST 内置正则，10 条脚本
2. **`extensions.SPreset.RegexBinding.regexes`**（行 2327-2537）：SPreset 插件正则，~10 条脚本（与 regex_scripts 部分重复）
3. **`extensions.tavern_helper.scripts`**（行 2541-2569）：TavernHelper 插件 JS 脚本，2 条（无法在 Python 执行，忽略）

### 正则脚本字段
- `placement: [2]` — AI 输出（本工具支持）
- `substituteRegex: 0` — 整数（0=none）
- `minDepth: null` / `maxDepth: null` — null 表示无限制
- `findRegex` — 部分带 `/pattern/flags` 格式，部分为纯 pattern

## 当前状态分析（6 个不兼容问题）

### 问题 1：character_id=100001 被拒绝（致命）
- **文件**：`/workspace/novelforge/services/preset_service.py` 第 112 行
- **现状**：`_parse_preset_order_from_st` 仅接受 `character_id == 100000`，非 100000 的条目被忽略
- **后果**：TGbreak 的 prompt_order 完全丢失，PromptAssembler 无法排序任何 prompt

### 问题 2：marker 为布尔值导致校验失败（致命）
- **文件**：`/workspace/novelforge/models/preset.py` 第 36 行、`/workspace/novelforge/services/preset_service.py` 第 81 行
- **现状**：`marker: str | None`，但 TGbreak 传入 `true`/`false` 布尔值。Pydantic v2 校验失败，`import_from_st_json` 的 `except (ValueError, TypeError)` 捕获后跳过该 prompt
- **后果**：所有 marker 提示（worldInfoBefore/chatHistory/worldInfoAfter/dialogueExamples 等）被跳过，预设结构残缺

### 问题 3：extensions.regex_scripts 未导入（严重）
- **文件**：`/workspace/novelforge/services/preset_service.py` 第 49-51 行
- **现状**：`extensions` 不在 `_KNOWN_PRESET_FIELDS` 中，整体存入 `raw_st_fields`，不解析
- **后果**：TGbreak 内嵌的 10+ 条正则脚本完全丢失

### 问题 4：openai_max_context/openai_max_tokens 未映射（严重）
- **文件**：`/workspace/novelforge/services/preset_service.py` 第 444-448 行
- **现状**：仅提取 `max_context`、`max_tokens` 等白名单字段
- **后果**：`generation_params` 缺少 max_context/max_tokens，PromptAssembler 使用默认值 32000/2000

### 问题 5：minDepth/maxDepth 为 null 语义错误（中等）
- **文件**：`/workspace/novelforge/services/regex_service.py` 第 126-127 行
- **现状**：`int(data.get("minDepth", 0) or 0)` → `null or 0` → `0`
- **后果**：`null`（无限制）被转为 `0`（仅最新消息），语义错误

### 问题 6：ST 宏 {{setvar::}}/{{user}}/{{char}} 未处理（中等）
- **文件**：`/workspace/novelforge/core/macros.py` 第 28 行
- **现状**：`_MACRO_PATTERN = r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}"` 仅匹配简单 `{{name}}`，不匹配 `{{setvar::name::value}}`、`{{user}}`、`{{char}}`（user/char 不在 MacroContext 中）
- **后果**：TGbreak 大量使用的 ST 宏保留原样发送给 LLM，变量功能失效

## 修改方案

### 修改 1：放宽 character_id 限制
**文件**：`/workspace/novelforge/services/preset_service.py`
- **what**：`_parse_preset_order_from_st` 接受任意 character_id，优先取 100000，无则取第一个
- **why**：TGbreak 使用 100001，当前被完全拒绝
- **how**：
  1. 第 107-123 行逻辑改为：先遍历找 `character_id == 100000` 的分组；若无，则取第一个分组的 character_id 作为全局标识（记录 INFO 日志）
  2. 将非 100000 的 character_id 统一映射为 `GLOBAL_CHARACTER_ID`（100000）存储，保持内部一致

### 修改 2：布尔 marker 转换为字符串
**文件**：`/workspace/novelforge/services/preset_service.py`
- **what**：`_parse_prompt_from_st` 将布尔 `marker` 转换为字符串
- **why**：TGbreak 用 `marker: true` + `identifier: "worldInfoBefore"` 表示 marker 提示
- **how**：
  1. 第 81 行 `marker=data.get("marker")` 改为调用新函数 `_parse_marker_from_st(data)`
  2. 新函数逻辑：
     - 若 `marker` 是字符串：直接返回（标准格式）
     - 若 `marker` 是 `True`：返回 `data.get("identifier", "")`（用 identifier 作为 marker 名）
     - 若 `marker` 是 `False` 或 `None`：返回 `None`

### 修改 3：解析 extensions 中的正则脚本
**文件**：`/workspace/novelforge/services/preset_service.py`
- **what**：`import_from_st_json` 解析 `extensions.regex_scripts` 和 `extensions.SPreset.RegexBinding.regexes`，返回正则脚本列表供调用方导入
- **why**：TGbreak 内嵌 20+ 条正则脚本（两个来源），当前完全丢失
- **how**：
  1. 修改 `import_from_st_json` 返回值：从 `WritingPreset` 改为 `tuple[WritingPreset, list[dict]]`（preset + 原始正则脚本字典列表）
  2. 解析逻辑：
     - 收集 `extensions.regex_scripts`（ST 内置）
     - 收集 `extensions.SPreset.RegexBinding.regexes`（SPreset 插件）
     - 去重（按 `id` 字段）
  3. 调用方（`preset_manager.py` 第 436 行）收到正则脚本列表后，调用 `RegexService` 逐条导入（用 `_parse_regex_script_from_st` + `add_script`）
  4. **注意**：`import_from_st_json` 的签名变更需要同步更新调用方和测试

### 修改 4：映射 openai_max_context/openai_max_tokens
**文件**：`/workspace/novelforge/services/preset_service.py`
- **what**：generation_params 提取时映射非标准字段名
- **why**：TGbreak 用 `openai_max_context`/`openai_max_tokens`，当前被忽略
- **how**：
  1. 第 444-448 行白名单循环后，增加字段映射：
     ```python
     if "openai_max_context" in data and "max_context" not in generation_params:
         generation_params["max_context"] = data["openai_max_context"]
     if "openai_max_tokens" in data and "max_tokens" not in generation_params:
         generation_params["max_tokens"] = data["openai_max_tokens"]
     ```

### 修改 5：minDepth/maxDepth null 语义保留
**文件**：`/workspace/novelforge/services/regex_service.py`
- **what**：`null` 保留为 `0` 但语义改为"无限制"（0 在本工具中表示不限制深度）
- **why**：ST 中 `null` 表示无限制，本工具中 `0` 也表示无限制（深度过滤从 0 开始），语义一致
- **how**：
  1. 第 126-127 行改为：
     ```python
     minDepth=data.get("minDepth") if data.get("minDepth") is not None else 0,
     maxDepth=data.get("maxDepth") if data.get("maxDepth") is not None else 0,
     ```
  2. 实际上 `int(None or 0)` 已经返回 `0`，语义正确，但需确认 RegexEngine 中 `maxDepth=0` 的处理逻辑（应表示"不限制"而非"仅最新"）

### 修改 6：支持 ST 风格宏
**文件**：`/workspace/novelforge/core/macros.py`
- **what**：扩展 MacroEngine 支持 ST 风格宏 `{{setvar::name::value}}`、`{{getvar::name}}`、`{{user}}`、`{{char}}`、`{{// comment}}`
- **why**：TGbreak 大量使用这些宏，当前保留原样发送给 LLM
- **how**：
  1. 扩展 `_MACRO_PATTERN` 为多个模式：
     - `{{setvar::name::value}}` → 调用 `setvar` 设置变量，返回空字符串
     - `{{getvar::name}}` → 调用 `getvar` 获取变量值
     - `{{user}}` → 替换为用户名（默认 "User"）
     - `{{char}}` → 替换为角色名（默认 "Assistant"）
     - `{{// comment}}` → 替换为空字符串（注释）
     - `{{name}}` → 现有简单宏（保持兼容）
  2. MacroContext 增加 `user` 和 `char` 字段（默认 "User"/"Assistant"）
  3. MacroEngine.substitute 增加可选的 `variable_store` 参数（VariableStore 实例），用于 setvar/getvar
  4. PromptAssembler._build_macro_context 传入 user/char（从 novel_profile 或默认值）
  5. **注意**：setvar/getvar 需要访问 VariableStore，但 MacroEngine 当前是无状态的。方案：在 MacroContext 中增加 `variable_funcs` 字段（含 getvar/setvar 函数），MacroEngine 从 context 获取

### 修改 7：调用方更新
**文件**：`/workspace/novelforge/ui/preset_manager.py`
- **what**：更新 `_on_import_preset` 处理新的返回值和正则脚本导入
- **why**：`import_from_st_json` 签名变更
- **how**：
  1. 第 436 行改为：
     ```python
     preset, regex_scripts_data = self.preset_service.import_from_st_json(file_path)
     # 导入正则脚本到 preset 作用域
     for script_data in regex_scripts_data:
         try:
             script = _parse_regex_script_from_st(script_data)
             self.regex_service.add_script(script, scope="preset", preset_id=preset.id)
         except Exception as e:
             logger.warning("导入正则脚本失败: %s", e)
     ```
  2. 需要导入 `_parse_regex_script_from_st` 或在 RegexService 中暴露公开方法 `import_scripts_from_list(scripts_data, scope, preset_id)`

## 假设与决策

1. **character_id 决策**：放宽为接受任意 character_id，取第一个分组（而非严格要求 100000）。TGbreak 用 100001 可能是 ST 的角色 ID 约定，但本工具是单用户工具，无需区分角色。
2. **marker 转换决策**：`marker: true` 时用 `identifier` 作为 marker 名。这是 ST 的标准约定——marker 提示的 identifier 即为 marker 类型。
3. **正则去重决策**：`extensions.regex_scripts` 和 `extensions.SPreset.RegexBinding.regexes` 可能重复（TGbreak 中确实重复），按 `id` 去重，保留先出现的。
4. **tavern_helper 决策**：忽略 JS 脚本（无法在 Python 执行），仅记录 INFO 日志。
5. **ST 宏决策**：在 MacroEngine 层面处理 ST 宏，而非 Jinja2 层。因为 ST 宏是 `{{::}}` 格式，与 Jinja2 的 `{{ }}` 冲突，需在 Jinja2 渲染前预处理。
6. **setvar/getvar 决策**：通过 MacroContext 传入 variable_funcs，保持 MacroEngine 无状态。setvar 返回空字符串（ST 行为），getvar 返回变量值。
7. **import_from_st_json 签名变更**：从返回 `WritingPreset` 改为 `tuple[WritingPreset, list[dict]]`。这是破坏性变更，但调用方仅 `preset_manager.py` 一处，可同步更新。

## 验证步骤

1. **单元测试**：用 TGbreak JSON 的子集编写测试，验证：
   - character_id=100001 的 prompt_order 被正确解析
   - marker=true 的 prompt 被正确转换为字符串 marker
   - extensions.regex_scripts 被正确提取
   - openai_max_context/openai_max_tokens 被正确映射
   - ST 宏 {{setvar::}}/{{getvar::}}/{{user}}/{{char}} 被正确处理

2. **集成测试**：导入完整的 TGbreak JSON 文件，验证：
   - 所有 ~40 条 prompts 被解析（无跳过）
   - 所有 ~50 条 prompt_order 条目被保留
   - 20+ 条正则脚本被导入（去重后）
   - generation_params 含 max_context=2000000, max_tokens=65535
   - PromptAssembler 能正确排序和组装 messages

3. **现有测试回归**：`python -m pytest tests/ -v` 确保 161 个测试仍全部通过

4. **UI 冒烟测试**：通过 PresetManager 导入 TGbreak JSON，验证无异常
