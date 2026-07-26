# 赓笔 (GengBi) 更新日志

> 本文件按时间倒序记录每次代码修改的详细变更，与 `README.md` 的"更新记录"章节互补：README 仅列版本要点，本文件含完整背景、改动细节、测试与文档同步情况。

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
