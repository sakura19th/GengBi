# 完成 TGbreak 预设导入修复计划

## 摘要

TGbreak😺V3.1.1.json 是一个 SillyTavern (ST) 预设模板，包含 ~40 个提示词、~50 个 prompt_order 条目、10+ 条正则脚本，并大量使用 ST 风格宏（`{{setvar::}}`、`{{getvar::}}`、`{{//}}`、`{{user}}`、`{{char}}`）。该预设依赖两个 ST 插件：**SPreset**（通过 `extensions.SPreset.RegexBinding.regexes` 绑定正则）和 **tavern_helper**（通过 `extensions.tavern_helper.scripts` 提供脚本，本项目仅记录忽略）。

前序会话已完成 7 项修改中的 6 项（preset_service 返回元组、preset_manager 接收 regex_service、macros 支持 ST 宏、regex_service 修复 null 处理等）。本计划完成剩余的 **2 个关键缺陷修复 + 2 个测试更新 + 验证**，使 TGbreak JSON 可正确导入并使用。

## 当前状态分析

经探索发现 **2 个阻断性缺陷** 和 **2 个测试兼容性问题**：

### 缺陷 1：main_window.py 未传递 regex_service（阻断正则导入）

- **位置**: [main_window.py:1797](file:///workspace/novelforge/ui/main_window.py#L1797)
- **现状**: `manager = PresetManager(self.preset_service, self)` — 仅传 2 个参数
- **后果**: `PresetManager.regex_service` 为 `None`，`_on_import_preset` 中正则导入分支（第 442 行 `if regex_scripts_data and self.regex_service is not None`）永不执行，TGbreak 的 10+ 条正则脚本被静默丢弃

### 缺陷 2：prompt_assembler.py 调用不存在的方法（阻断 ST 变量宏）

- **位置**: [prompt_assembler.py:602](file:///workspace/novelforge/core/prompt_assembler.py#L602)
- **现状**: `variable_funcs = var_store.get_functions(project_id=..., chapter_metadata=...)`
- **问题**: `VariableStore` 类**没有 `get_functions` 方法**，实际方法名为 `make_template_context`（[variable_store.py:406](file:///workspace/novelforge/core/variable_store.py#L406)）
- **后果**: 调用抛出 `AttributeError`，被第 606 行 `except Exception` 捕获后仅输出 debug 日志，`variable_funcs` 保持为空字典 `{}`。导致 TGbreak 中大量 `{{setvar::COT-Anti-Omniscience::}}` 等变量初始化宏完全失效，`{{getvar::name}}` 替换为空字符串

### 测试兼容性问题（返回类型已从 `WritingPreset` 变为 `tuple`）

| 文件 | 行号 | 现状代码 | 问题 |
|------|------|----------|------|
| [test_m2_prompt_assembly.py:737](file:///workspace/tests/test_m2_prompt_assembly.py#L737) | 737 | `imported = service.import_from_st_json(st_file)` | 后续访问 `imported.name`/`imported.prompts`，但 `imported` 现在是 tuple |
| [test_e2e_workflow.py:361](file:///workspace/tests/test_e2e_workflow.py#L361) | 361 | `preset = preset_service.import_from_st_json(str(st_file))` | 后续访问 `preset.prompts`/`preset.prompt_order`，但 `preset` 现在是 tuple |

## 提议修改

### 修改 1：main_window.py 传递 regex_service

**文件**: `/workspace/novelforge/ui/main_window.py`
**行号**: 1797

```python
# 修改前
manager = PresetManager(self.preset_service, self)

# 修改后
manager = PresetManager(self.preset_service, self, regex_service=self.regex_service)
```

**原因**: 使预设管理器在导入 ST 预设时能同步导入正则脚本到 preset 作用域。

### 修改 2：prompt_assembler.py 修正方法名

**文件**: `/workspace/novelforge/core/prompt_assembler.py`
**行号**: 602

```python
# 修改前
variable_funcs = var_store.get_functions(
    project_id=project_id,
    chapter_metadata=chapter_metadata or {},
)

# 修改后
variable_funcs = var_store.make_template_context(
    project_id=project_id,
    chapter_metadata=chapter_metadata or {},
)
```

**原因**: `VariableStore` 的实际方法名为 `make_template_context`（行 406），返回 `{"getvar": ..., "setvar": ..., "hasvar": ..., "delvar": ...}` 字典，与 `MacroContext.variable_funcs` 和 `MacroEngine.substitute` 中查找的键名完全匹配。

### 修改 3：更新 test_m2_prompt_assembly.py

**文件**: `/workspace/tests/test_m2_prompt_assembly.py`
**行号**: 737

```python
# 修改前
imported = service.import_from_st_json(st_file)

# 修改后
imported, _regex_scripts = service.import_from_st_json(st_file)
```

**原因**: 适配 `import_from_st_json` 新的二元组返回类型。后续代码访问 `imported.name`、`imported.prompts`、`imported.raw_st_fields` 无需改动。

### 修改 4：更新 test_e2e_workflow.py

**文件**: `/workspace/tests/test_e2e_workflow.py`
**行号**: 361

```python
# 修改前
preset = preset_service.import_from_st_json(str(st_file))

# 修改后
preset, _regex_scripts = preset_service.import_from_st_json(str(st_file))
```

**原因**: 适配二元组返回类型。后续代码访问 `preset.prompts`、`preset.prompt_order` 无需改动。

## 假设与决策

1. **不修改 `import_from_st_json` 的返回类型** — 二元组返回是有意设计，允许调用方决定是否导入正则脚本，保持当前签名。
2. **`make_template_context` 返回格式已匹配** — 经验证，返回的 `{"getvar", "setvar", "hasvar", "delvar"}` 字典与 `macros.py` 中 `context.variable_funcs.get("setvar")` / `.get("getvar")` 查找的键名一致，无需额外适配。
3. **不修改其他测试** — `test_e2e_workflow.py:469/481` 和 `test_m3.py:433` 调用的是 `RegexService.import_from_st_json`（不同类、不同方法、带 `scope` 参数），与预设服务无关，无需改动。
4. **TGbreak 的 `character_id: 100001`** — 前序会话已修改 `_parse_preset_order_from_st` 接受任意 character_id 并回退到首个非 100000 组，无需额外处理。

## 验证步骤

1. **运行完整测试套件**:
   ```bash
   cd /workspace && python -m pytest tests/ test_m3.py -x -q 2>&1 | tail -30
   ```
   确认所有测试通过（前序会话基线为 161 个测试通过）。

2. **TGbreak 导入冒烟测试**（Python 脚本验证）:
   ```python
   from novelforge.services.preset_service import PresetService
   from novelforge.services.regex_service import RegexService
   from pathlib import Path

   ps = PresetService(Path("/tmp/nf_test"))
   rs = RegexService(Path("/tmp/nf_test"))
   preset, regex_data = ps.import_from_st_json("/workspace/TGbreak😺V3.1.1.json")
   print(f"预设名: {preset.name}")
   print(f"提示数: {len(preset.prompts)}")
   print(f"prompt_order 组数: {len(preset.prompt_order)}")
   print(f"正则脚本数据条数: {len(regex_data)}")
   print(f"max_context: {preset.generation_params.get('max_context')}")
   print(f"max_tokens: {preset.generation_params.get('max_tokens')}")
   # 验证 ST 宏存在
   has_setvar = any("{{setvar::" in (p.content or "") for p in preset.prompts)
   has_getvar = any("{{getvar::" in (p.content or "") for p in preset.prompts)
   print(f"含 setvar 宏: {has_setvar}, 含 getvar 宏: {has_getvar}")
   ```

3. **ST 变量宏功能验证**（确认 `make_template_context` 修复生效）:
   ```python
   from novelforge.core.macros import MacroContext, MacroEngine
   from novelforge.core.variable_store import VariableStore
   from pathlib import Path

   vs = VariableStore(Path("/tmp/nf_test"))
   ctx = MacroContext.from_novel_profile(
       novel_profile={},
       variable_funcs=vs.make_template_context(project_id="test"),
   )
   engine = MacroEngine()
   # 测试 setvar + getvar
   engine.substitute("{{setvar::greeting::hello}}", ctx)
   result = engine.substitute("Value is {{getvar::greeting}}", ctx)
   assert result == "Value is hello", f"ST 宏测试失败: {result!r}"
   print(f"ST 变量宏测试通过: {result!r}")
   ```
