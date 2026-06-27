# Tasks

- [x] Task 1: 新增 WorldBook 数据模型与 WorldBookService
  - [x] SubTask 1.1: 创建 `novelforge/models/worldbook.py`，定义 `WorldBook` 模型（id/name/entries:list[ContextEntry]/enabled/created_at/updated_at）
  - [x] SubTask 1.2: 创建 `novelforge/services/worldbook_service.py`，实现 `WorldBookService` 类
    - list_worldbooks() — glob `~/.novelforge/worldbooks/*.json`
    - load_worldbook(wb_id) — 读 JSON + model_validate，含 .bak 恢复
    - save_worldbook(wb) — save_json_with_backup 写入
    - delete_worldbook(wb_id) — 删除文件
    - create_worldbook(name) — 复制空模板
    - import_from_st_json(file_path) — 调用 import_worldbook + 创建 WorldBook
    - export_to_st_json(wb, file_path) — 导出为 ST 格式
    - set_worldbook_enabled(wb_id, enabled)
  - [x] SubTask 1.3: 确认 storage_service.py 或新增工具方法提供 `~/.novelforge/worldbooks/` 目录创建

- [x] Task 2: 修复 worldbook_importer.py 的 POSITION_MAP
  - [x] SubTask 2.1: 修改 `POSITION_MAP`（L42-46）为完整映射：0→before, 1→after, 2→before, 3→after, 4→at_depth, 5→before, 6→after, 7→after
  - [x] SubTask 2.2: 更新 POSITION_MAP 上方注释说明 ST position 枚举与归并策略

- [x] Task 3: 新增 WorldBookManager 独立窗口 UI
  - [x] SubTask 3.1: 创建 `novelforge/ui/worldbook_manager.py`，参照 PresetManager 结构
    - 顶部：世界书下拉框 + 新建/导入ST/导出/复制/删除/禁用 按钮
    - 左侧：条目列表（DragDrop 排序 + 启用复选框）
    - 右侧：条目编辑器（comment/content/order/position/depth/role/key）
    - 底部：保存按钮
    - 窗口状态持久化：QSettings("赓笔", "WorldBookManager")
    - 信号：worldbook_changed（通知主窗口刷新）
  - [x] SubTask 3.2: 条目编辑复用 ContextPreviewPanel 的 `_EntryEditorDialog` 或新建简化版

- [x] Task 4: 新增 WorldBookPanel 嵌入续写面板
  - [x] SubTask 4.1: 创建 `novelforge/ui/worldbook_panel.py`，轻量控件
    - 世界书下拉框 + 启用复选框
    - get_selected_worldbook_id() / is_enabled()
    - set_worldbooks(list, default_id)
  - [x] SubTask 4.2: 在 `continuation_panel.py` 的续写配置区新增 WorldBookPanel（回溯章节数之后）

- [x] Task 5: main_window.py 集成世界书加载与合并
  - [x] SubTask 5.1: __init__ 中创建 WorldBookService 实例
  - [x] SubTask 5.2: 新增 _refresh_worldbooks() 方法，加载启用世界书到 WorldBookPanel
  - [x] SubTask 5.3: 启动时调用 _refresh_worldbooks()（_refresh_presets 之后）
  - [x] SubTask 5.4: 新增 _get_enabled_worldbook_entries() — 返回启用世界书的所有 entries
  - [x] SubTask 5.5: 修改 _on_start_continuation：合并 worldbook_entries + extract_entries（uid 冲突时世界书优先）
  - [x] SubTask 5.6: 修改 _on_view_continuation_prompt：同样合并
  - [x] SubTask 5.7: 新增菜单项"工具 → 世界书管理"，创建非模态 WorldBookManager，连接 worldbook_changed → _refresh_worldbooks

- [x] Task 6: Agent 流程集成世界书
  - [x] SubTask 6.1: 修改 _on_start_continuation_routed：合并世界书条目到 context_entries 传给 AgentOrchestrator
    （在 _on_start_agent_continuation 中实施，使用 _merge_worldbook_entries 辅助方法）

- [x] Task 7: 测试与验证
  - [x] SubTask 7.1: 新增 tests/test_worldbook_service.py：保存/加载/列表/导入/导出测试（9 用例全过）
  - [x] SubTask 7.2: 新增 tests/test_worldbook_position_map.py：验证 POSITION_MAP 修复（7 用例全过）
  - [x] SubTask 7.3: 运行完整测试套件确认无回归（239 全过）
  - [x] SubTask 7.4: 更新旧测试 test_import_worldbook_position_conversion 以匹配新 POSITION_MAP（8 种 position 全覆盖）

# Task Dependencies
- Task 1 → Task 3, Task 5（WorldBookService 是 UI 和主窗口的前提）
- Task 4 → Task 5（WorldBookPanel 需先创建才能嵌入续写面板）
- Task 2 独立，可并行
- Task 5 → Task 6（主窗口合并逻辑完成后 Agent 流程复用）
- Task 7 依赖所有前置任务
