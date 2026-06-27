# 全局世界书 Spec

## Why

当前赓笔的"上下文条目"（ContextEntry）仅由 LLM 按章节动态提取，缓存于 SQLite `cache` 表并绑定到具体章节。用户手动导入的 SillyTavern 世界书 JSON 仅存内存，切换章节或重启即丢失，且无独立的管理界面（列表/启用禁用/编辑/导入导出）。

用户期望世界书"像预设一样全局加载（不绑定到提取上下文），支持随意打开关闭、编辑、导入导出"。预设已具备完整链路（PresetService 文件系统存储 + PresetManager 独立窗口 + assemble 注入），世界书应复用同一架构。

## What Changes

- **新增** `WorldBook` 数据模型：全局世界书容器，含 `id/name/entries/enabled/created_at/updated_at`，entries 为 `ContextEntry` 列表
- **新增** `WorldBookService`：文件系统持久化 `~/.novelforge/worldbooks/{id}.json`（含 `.bak` 备份），提供 list/load/save/delete/import/export/create 方法，复用 `save_json_with_backup` 模式
- **新增** `WorldBookManager` UI：非模态独立窗口，参照 `PresetManager` 结构——世界书下拉列表 + 启用/禁用 + 条目列表（拖拽排序）+ 条目编辑器 + 导入/导出/复制/删除按钮
- **新增** `WorldBookPanel`：嵌入续写面板的轻量控件（世界书下拉框 + 启用/禁用复选框），类似预设下拉框的对应物
- **修改** `main_window.py`：启动时加载世界书列表到 `WorldBookPanel`；续写时将启用的全局世界书条目与章节提取条目合并后传给 `assemble(context_entries=...)`
- **修改** `continuation_panel.py`：在续写配置区新增 `WorldBookPanel`（世界书选择下拉框 + 启用复选框）
- **修复** `worldbook_importer.py` 的 `POSITION_MAP`：增加 `{4: "at_depth"}`，`2/3`（ANTop/ANBottom）映射为 `before`/`after`，`5/6/7` 映射为 `before`/`after`/`after`
- **复用** `PromptAssembler.assemble(context_entries=...)` 注入逻辑（worldInfoBefore/After marker + at_depth 深度注入）无需改动

## Impact

- **Affected specs**: `build-novel-continuation-tool`（续写流程新增世界书合并环节）
- **Affected code**:
  - 新增：`novelforge/models/worldbook.py`、`novelforge/services/worldbook_service.py`、`novelforge/ui/worldbook_manager.py`、`novelforge/ui/worldbook_panel.py`
  - 修改：`novelforge/services/worldbook_importer.py`（POSITION_MAP 修复）、`novelforge/ui/main_window.py`（加载/合并/菜单入口）、`novelforge/ui/continuation_panel.py`（嵌入 WorldBookPanel）
  - 复用：`novelforge/models/context.py`（ContextEntry 模型）、`novelforge/core/prompt_assembler.py`（注入逻辑）

## ADDED Requirements

### Requirement: 全局世界书数据模型
系统 SHALL 提供 `WorldBook` 数据模型，包含 `id/name/entries/enabled/created_at/updated_at` 字段，entries 为 `ContextEntry` 列表。

#### Scenario: 世界书序列化与反序列化
- **WHEN** WorldBook 实例被 `model_dump(mode="json")` 序列化为 JSON 文件
- **THEN** 可通过 `WorldBook.model_validate(json_data)` 完整恢复，含所有 entries

### Requirement: 世界书文件系统持久化
系统 SHALL 在 `~/.novelforge/worldbooks/{worldbook_id}.json` 持久化每个世界书，写入前创建 `.bak` 备份。

#### Scenario: 保存世界书
- **WHEN** 用户在 WorldBookManager 中编辑条目并保存
- **THEN** WorldBookService 将 JSON 写入 `{id}.json`，同时备份为 `{id}.json.bak`
- **AND** 若写入失败，`.bak` 文件可恢复上次有效状态

#### Scenario: 列出世界书
- **WHEN** 应用启动调用 `list_worldbooks()`
- **THEN** 返回 `~/.novelforge/worldbooks/*.json` 对应的 WorldBook 列表，按 name 排序

### Requirement: 世界书管理器 UI
系统 SHALL 提供独立的非模态 `WorldBookManager` 窗口，支持世界书的列表/启用禁用/编辑/导入导出/复制/删除。

#### Scenario: 导入 ST 世界书
- **WHEN** 用户点击"导入"并选择 SillyTavern 世界书 JSON 文件
- **THEN** WorldBookService 调用 `import_worldbook(file_path)` 解析为 ContextEntry 列表
- **AND** 创建新 WorldBook（name 取文件名），保存到文件系统
- **AND** WorldBookManager 刷新列表显示新世界书

#### Scenario: 编辑世界书条目
- **WHEN** 用户在条目列表选中一条并点击编辑
- **THEN** 弹出编辑对话框，可修改 comment/content/order/position/depth/role/key
- **AND** 保存后写入文件系统

#### Scenario: 启用/禁用世界书
- **WHEN** 用户切换世界书的启用复选框
- **THEN** WorldBook.enabled 更新并持久化
- **AND** 续写面板的 WorldBookPanel 仅显示启用的世界书

### Requirement: 续写面板嵌入世界书选择
系统 SHALL 在续写配置区新增 WorldBookPanel，包含世界书下拉框和启用复选框，类似预设下拉框。

#### Scenario: 续写时选择世界书
- **WHEN** 用户在 WorldBookPanel 下拉框选择一个世界书并勾选启用
- **THEN** 续写时该世界书的 entries 会被合并到 context_entries 传给 assemble()

### Requirement: 续写时合并全局世界书与提取上下文
系统 SHALL 在续写时将启用的全局世界书条目与当前章节的提取上下文条目合并后传给 `assemble(context_entries=...)`。

#### Scenario: 仅启用世界书无提取
- **WHEN** 用户启用了世界书但未提取上下文
- **THEN** 续写时仅使用世界书条目作为 context_entries

#### Scenario: 世界书与提取并存
- **WHEN** 用户启用了世界书且已提取上下文
- **THEN** 续写时合并两者条目（世界书在前，提取结果在后），去重时保留世界书条目优先

## MODIFIED Requirements

### Requirement: worldbook_importer position 映射
修复 `POSITION_MAP` 以正确映射 SillyTavern position 枚举到赓笔 3 种 position。

**修改前**：
```python
POSITION_MAP = {0: "before", 1: "after", 2: "at_depth"}  # 4=atDepth 未映射，2=ANTop 误映射
```

**修改后**：
```python
POSITION_MAP = {
    0: "before",      # before
    1: "after",       # after
    2: "before",      # ANTop → 归入 before
    3: "after",       # ANBottom → 归入 after
    4: "at_depth",    # atDepth
    5: "before",      # EMTop → 归入 before
    6: "after",       # EMBottom → 归入 after
    7: "after",       # outlet → 归入 after（兜底）
}
```

### Requirement: main_window 续写流程
`_on_start_continuation` 和 `_on_view_continuation_prompt` 在读取 context_entries 后，合并启用的全局世界书条目。

## Assumptions & Decisions

1. **并存模式**：全局世界书与按章节绑定的提取上下文并存。世界书像预设一样全局加载（所有项目共享），提取上下文仍按章节。两者在续写时合并注入。
2. **文件系统存储**：`~/.novelforge/worldbooks/{id}.json`，含 `.bak` 备份，复用 `save_json_with_backup` 模式，独立 `WorldBookService` 类。
3. **复用 ContextEntry 模型**：世界书条目即 `ContextEntry`，通过 `source_chapter_range=None`+`extracted_at=None` 区分"导入/手动"与"提取结果"。
4. **复用 assemble 注入逻辑**：`PromptAssembler.assemble(context_entries=...)` 已支持 worldInfoBefore/After marker + at_depth 注入，无需改动。
5. **position 映射修复**：增加 `{4: "at_depth"}`，`2/3`（ANTop/ANBottom）映射为 `before`/`after`，`5/6/7` 归并，保持赓笔 3 种 position 语义。
6. **无关键词触发**：赓笔 ContextEntry 始终注入（无 probability/selective 激活逻辑），仅靠 UI 的 enabled/disabled 控制是否启用。ST 的 probability/selective 字段导入时忽略，保留在 raw_st_fields。
7. **WorldBookManager 独立窗口**：参照 PresetManager 非模态结构，通过菜单"工具 → 世界书管理"打开。
8. **去重策略**：合并时若 uid 冲突，世界书条目优先（全局设定不应被提取结果覆盖）。
