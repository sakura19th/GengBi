# Checklist

- [x] WorldBook 模型定义完整（id/name/entries/enabled/created_at/updated_at）
- [x] WorldBookService 实现文件系统持久化（~/.novelforge/worldbooks/{id}.json + .bak）
- [x] WorldBookService 支持 list/load/save/delete/create/import/export/setEnabled
- [x] POSITION_MAP 修复：4→at_depth，2/3→before/after，5/6/7 归并
- [x] WorldBookManager 独立窗口：列表/启用禁用/编辑/导入导出/复制/删除
- [x] WorldBookManager 条目列表支持拖拽排序
- [x] WorldBookManager 条目编辑器支持 comment/content/order/position/depth/role/key
- [x] WorldBookPanel 嵌入续写配置区（下拉框 + 启用复选框）
- [x] main_window 启动时加载世界书列表到 WorldBookPanel
- [x] main_window 续写时合并全局世界书条目与提取上下文条目
- [x] 合并去重策略：uid 冲突时世界书条目优先
- [x] main_window 查看提示词路径同样合并世界书条目
- [x] Agent 流程（_on_start_continuation_routed）合并世界书条目
- [x] 菜单"工具 → 世界书管理"打开非模态 WorldBookManager
- [x] WorldBookManager worldbook_changed 信号触发主窗口 _refresh_worldbooks
- [x] tests/test_worldbook_service.py 覆盖保存/加载/列表/导入/导出
- [x] tests/test_worldbook_position_map.py 验证 position 映射修复
- [x] 完整测试套件（tests/）全部通过，无回归（239 项全过）
