# 上下文提取按章节绑定 + Token 拆分（续）

## 概述

本次任务延续之前会话的工作，完成上下文提取的两个功能：
1. **按章节绑定**：上下文提取结果绑定到每个章节单独一份，切换章节时加载对应章节的提取结果
2. **按 Token 拆分**：选中章节超出 token 限制时，自动按章节拆分成多次请求，增量更新提取信息（解决长上下文提取的 `aiohttp.TimeoutError`）

## 当前状态分析

### 已完成（前序会话）

- `core/config.py`：默认配置已添加 `"token_limit": 0`
- `services/context_extractor.py`：核心逻辑已重写完成
  - `ExtractResult` 新增 `batch_count` 字段
  - `__init__` 接受 `token_counter` 参数
  - `_build_cache_key(project_id, chapter_id)` 按章节绑定
  - `_get_cached_data()` 返回完整 dict（含元数据）
  - `_get_cached_entries(cache_key, current_chapters_hash)` 校验 hash
  - `_save_cached_entries()` 保存 dict（含 chapters_hash/elapsed_seconds/token_usage/lookback/batch_count）
  - `_split_chapters_by_token_limit()` 贪心装填拆分
  - `extract()` 支持 `token_limit_override`，按章节缓存，批次循环 + uid 去重
  - `extract_streaming()` 支持 `token_limit_override` 和 `on_batch_complete` 回调
  - `load_cached_entries(project_id, chapter_id)` 公开方法（供章节切换加载）
- `ui/context_preview_panel.py`：UI 已修改完成
  - Token 限制下拉框（不限制/50k/100k/250k/500k）
  - `get_lookback_config()` 返回 `{"lookback": n, "token_limit": m}`
  - `finish_extraction()` 新增 `batch_count` 参数
  - `update_entries_incremental(entries, batch_idx, total_batches)` 增量更新
  - `load_entries_for_chapter(entries, meta)` 章节切换加载
- `ui/main_window.py`：部分完成
  - 信号 `_extract_batch_done = Signal(list, int, int)` 已添加
  - 实例变量 `_context_entries_by_chapter` 和 `_extracting_chapter_id` 已添加
  - TokenCounter 已注入 ContextExtractor
  - 信号 `_extract_batch_done` 已连接

### 待完成

1. `ui/main_window.py`：按章节绑定逻辑（章节切换加载、提取完成存储、条目变更同步）
2. `ui/main_window.py`：token 拆分信号槽 + 回调传递
3. `ui/settings_dialog.py`：新增 token 限制默认值设置
4. 运行测试验证无回归

## 实施方案

### 1. main_window.py：按章节绑定

#### 1.1 修改 `_on_chapter_selected()`（约 line 847）

在加载新章节后，加载该章节的上下文提取结果：

```python
def _on_chapter_selected(self, chapter_id: str) -> None:
    """章节被选中。"""
    # 保存当前章节
    if self.chapter_editor.has_unsaved_changes:
        self.chapter_editor.save_now()

    # 切换前：保存当前章节的上下文条目到内存缓存
    if self._current_chapter and self._current_chapter.id != chapter_id:
        old_id = self._current_chapter.id
        self._context_entries_by_chapter[old_id] = list(self._current_context_entries)

    chapter = self.storage_service.load_chapter(chapter_id)
    if chapter is None:
        return

    self._current_chapter = chapter
    self.chapter_editor.load_chapter(...)
    self._update_chapter_in_list(chapter)
    # ... 续写信息显示 ...

    # 切换后：加载新章节的上下文提取结果
    self._load_context_entries_for_chapter(chapter)

    self._set_status_message(f"已加载: 第{chapter.index + 1}章 {chapter.title}")
```

新增辅助方法 `_load_context_entries_for_chapter()`：

```python
def _load_context_entries_for_chapter(self, chapter: Chapter) -> None:
    """加载章节对应的上下文提取结果（内存缓存优先，其次 SQLite 缓存）。"""
    context_panel = self.continuation_panel.context_preview_panel

    # 1. 内存缓存优先
    if chapter.id in self._context_entries_by_chapter:
        entries = self._context_entries_by_chapter[chapter.id]
        self._current_context_entries = entries
        context_panel.load_entries_for_chapter(entries, meta=None)
        return

    # 2. SQLite 缓存（通过 AsyncLoopRunner 同步调用）
    if self._current_project_id:
        try:
            from novelforge.services.async_runner import AsyncLoopRunner
            runner = AsyncLoopRunner.instance()
            cached_data = runner.run(
                self.context_extractor.load_cached_entries(
                    self._current_project_id, chapter.id
                ),
                timeout=5,
            )
            if cached_data is not None:
                entries = cached_data.get("entries", [])
                meta = {
                    "elapsed_seconds": cached_data.get("elapsed_seconds", 0),
                    "token_usage": cached_data.get("token_usage", {}),
                    "batch_count": cached_data.get("batch_count", 1),
                }
                self._context_entries_by_chapter[chapter.id] = entries
                self._current_context_entries = entries
                context_panel.load_entries_for_chapter(entries, meta=meta)
                return
        except Exception as e:
            logger.warning("加载章节缓存提取结果失败: %s", e)

    # 3. 无缓存：清空面板
    self._current_context_entries = []
    context_panel.load_entries_for_chapter([], meta=None)
```

#### 1.2 修改 `_on_context_entries_changed()`（约 line 1580）

同步用户编辑到内存缓存：

```python
def _on_context_entries_changed(self, entries: list) -> None:
    """上下文条目变更（用户编辑/禁用/添加）。"""
    self._current_context_entries = entries
    # 同步到按章节绑定的内存缓存
    if self._current_chapter:
        self._context_entries_by_chapter[self._current_chapter.id] = list(entries)
    logger.debug("上下文条目变更: %d 条", len(entries))
```

#### 1.3 修改 `_on_extract_done()`（约 line 1736）

存储到按章节绑定的内存缓存，传递 `batch_count`：

```python
def _on_extract_done(self, result) -> None:
    """流式提取完成回调（在 UI 线程执行）。"""
    context_panel = self.continuation_panel.context_preview_panel

    if result.status == "completed":
        context_panel.finish_extraction(
            entries=result.entries,
            elapsed_seconds=result.elapsed_seconds,
            token_usage=result.token_usage,
            from_cache=result.from_cache,
            batch_count=result.batch_count,
        )
        self._current_context_entries = result.entries
        # 按章节绑定存储
        chapter_id = self._extracting_chapter_id or (
            self._current_chapter.id if self._current_chapter else None
        )
        if chapter_id:
            self._context_entries_by_chapter[chapter_id] = list(result.entries)
        self._extracting_chapter_id = None
        self._set_status_message(
            f"上下文提取完成: {len(result.entries)} 条 "
            f"(耗时 {result.elapsed_seconds:.2f}s)"
        )
    elif result.status == "skipped":
        context_panel.cancel_extraction()
        self._current_context_entries = []
        self._extracting_chapter_id = None
        self._set_status_message("无前文可提取")
    else:
        context_panel.fail_extraction(result.error)
        self._extracting_chapter_id = None
        self._set_status_message(f"上下文提取失败: {result.error}")
```

#### 1.4 修改 `_on_force_refresh_context()`（约 line 1600）

传递 `token_limit_override` 和 `batch_count`，存储到按章节绑定：

```python
def _on_force_refresh_context(self) -> None:
    """F5 强制重新提取上下文。"""
    if not self._current_chapter:
        QMessageBox.warning(self, "提示", "请先选择章节")
        return

    self._ensure_chapter_contents()

    project = None
    if self._current_project_id:
        project = self.storage_service.load_project(self._current_project_id)

    context_panel = self.continuation_panel.context_preview_panel
    context_panel.start_extraction()
    self._set_status_message("强制重新提取上下文...")

    lookback_config = context_panel.get_lookback_config()
    lookback_override = lookback_config.get("lookback")
    token_limit_override = lookback_config.get("token_limit")

    from novelforge.services.async_runner import AsyncLoopRunner
    runner = AsyncLoopRunner.instance()
    try:
        extract_result: ExtractResult = runner.run(
            self.context_extractor.extract(
                project=project,
                chapters=self._current_chapters,
                current_chapter=self._current_chapter,
                force_refresh=True,
                lookback_override=lookback_override,
                token_limit_override=token_limit_override,
            ),
            timeout=300,  # 多批次需要更长超时
        )
    except Exception as e:
        logger.error("强制重新提取异常: %s", e, exc_info=True)
        context_panel.fail_extraction(str(e))
        return

    if extract_result.status == "completed":
        context_panel.finish_extraction(
            entries=extract_result.entries,
            elapsed_seconds=extract_result.elapsed_seconds,
            token_usage=extract_result.token_usage,
            from_cache=False,
            batch_count=extract_result.batch_count,
        )
        self._current_context_entries = extract_result.entries
        # 按章节绑定存储
        self._context_entries_by_chapter[self._current_chapter.id] = list(
            extract_result.entries
        )
        self._set_status_message(
            f"上下文提取完成: {len(extract_result.entries)} 条 "
            f"(耗时 {extract_result.elapsed_seconds:.2f}s)"
        )
    elif extract_result.status == "skipped":
        context_panel.cancel_extraction()
        self._set_status_message("无前文可提取")
    else:
        context_panel.fail_extraction(extract_result.error)
        self._set_status_message(f"上下文提取失败: {extract_result.error}")
```

#### 1.5 修改 `_handle_extraction_failure()`（约 line 1551）

传递 `batch_count`：

```python
if extract_result.status == "completed":
    context_panel.finish_extraction(
        entries=extract_result.entries,
        elapsed_seconds=extract_result.elapsed_seconds,
        token_usage=extract_result.token_usage,
        from_cache=extract_result.from_cache,
        batch_count=extract_result.batch_count,
    )
    return extract_result.entries
```

#### 1.6 修改 `_load_project()`（约 line 784）

切换项目时清空按章节绑定的内存缓存：

```python
def _load_project(self, project_id: str) -> None:
    """加载项目。"""
    self._save_current_state()
    # 清空按章节绑定的上下文条目内存缓存
    self._context_entries_by_chapter.clear()
    self._current_context_entries = []
    # ... 原有逻辑 ...
```

### 2. main_window.py：token 拆分信号槽 + 回调

#### 2.1 新增 `_on_extract_batch_done()` 槽

```python
@Slot(list, int, int)
def _on_extract_batch_done(self, entries: list, batch_idx: int, total_batches: int) -> None:
    """流式提取批次完成回调（UI 线程执行，由 Signal 触发）。

    多批次提取时，每批完成后增量更新 UI 显示。
    """
    context_panel = self.continuation_panel.context_preview_panel
    context_panel.update_entries_incremental(entries, batch_idx, total_batches)
```

#### 2.2 修改 `_on_extract_requested()`（约 line 1660）

记录提取章节 ID，传递 `token_limit_override` 和 `on_batch_complete` 回调：

```python
def _on_extract_requested(self, config: dict) -> None:
    """独立提取上下文（非阻塞，流式进度）。"""
    if not self._current_chapter:
        QMessageBox.warning(self, "提示", "请先选择章节")
        return

    self._ensure_chapter_contents()

    endpoint = self.continuation_panel.get_selected_endpoint()
    if not endpoint:
        QMessageBox.warning(self, "提示", "请先配置 API 端点")
        self._on_open_settings()
        return

    api_key = self.config_manager.decrypt_api_key(endpoint.get("id", ""))
    if not api_key:
        QMessageBox.warning(self, "提示", "API Key 无效，请检查设置")
        self._on_open_settings()
        return

    context_panel = self.continuation_panel.context_preview_panel
    context_panel.start_extraction()
    self._set_status_message("正在提取上下文（流式）...")

    # 记录正在提取的章节 ID（供 _on_extract_done 归档）
    self._extracting_chapter_id = self._current_chapter.id

    project = None
    if self._current_project_id:
        project = self.storage_service.load_project(self._current_project_id)

    lookback_override = config.get("lookback")
    token_limit_override = config.get("token_limit")

    from novelforge.services.async_runner import AsyncLoopRunner
    runner = AsyncLoopRunner.instance()
    loop = runner._loop

    def on_chunk(text: str) -> None:
        self._extract_chunk_received.emit(text)

    def on_batch_complete(entries: list, batch_idx: int, total_batches: int) -> None:
        # 通过 Signal 转发到 UI 线程（自动 QueuedConnection）
        self._extract_batch_done.emit(entries, batch_idx, total_batches)

    future = asyncio.run_coroutine_threadsafe(
        self.context_extractor.extract_streaming(
            project=project,
            chapters=self._current_chapters,
            current_chapter=self._current_chapter,
            lookback_override=lookback_override,
            on_chunk=on_chunk,
            token_limit_override=token_limit_override,
            on_batch_complete=on_batch_complete,
        ),
        loop,
    )

    def on_done(fut) -> None:
        try:
            result = fut.result()
            self._extract_done.emit(result)
        except Exception as e:
            logger.error("流式提取异常: %s", e, exc_info=True)
            err_result = ExtractResult(
                entries=[], status="failed", error=str(e)
            )
            self._extract_done.emit(err_result)

    future.add_done_callback(on_done)
```

### 3. settings_dialog.py：新增 token 限制默认值

#### 3.1 在提取配置组添加 token 限制下拉框（约 line 438 后）

```python
# 上下文提取配置组
extract_group = QGroupBox("上下文提取")
extract_form = QFormLayout(extract_group)

self._extractor_model_edit = QLineEdit()
self._extractor_model_edit.setPlaceholderText("留空=使用端点默认模型")
extract_settings = self._config_manager.get_context_extract_settings()
self._extractor_model_edit.setText(extract_settings.get("extractor_model", ""))
extract_form.addRow("提取模型:", self._extractor_model_edit)

# Token 拆分限制（默认值，UI 面板可按次覆盖）
self._token_limit_combo = QComboBox()
self._token_limit_combo.addItems(["不限制", "50k", "100k", "250k", "500k"])
token_limit = extract_settings.get("token_limit", 0)
if token_limit <= 0:
    self._token_limit_combo.setCurrentText("不限制")
else:
    self._token_limit_combo.setCurrentText(f"{token_limit // 1000}k")
self._token_limit_combo.setToolTip(
    "选中章节超出 token 限制时，自动按章节拆分成多次请求。\n"
    "此为默认值，上下文预览面板可按次覆盖。"
)
extract_form.addRow("Token 拆分:", self._token_limit_combo)

layout.addWidget(extract_group)
```

#### 3.2 保存逻辑（约 line 604 后）

```python
# 保存上下文提取配置
extract_settings = self._config_manager.get_context_extract_settings()
extract_settings["extractor_model"] = self._extractor_model_edit.text().strip()
# Token 限制
token_text = self._token_limit_combo.currentText()
if token_text == "不限制":
    extract_settings["token_limit"] = 0
else:
    import re
    m = re.search(r"(\d+)", token_text)
    extract_settings["token_limit"] = int(m.group(1)) * 1000 if m else 0
self._config_manager.config["context_extract"] = extract_settings
```

### 4. main_window.py：启动时同步 UI token 限制默认值

在 `_apply_font_settings()` 调用后（约 line 250），添加同步逻辑：

```python
# 同步上下文提取 token 限制默认值到 UI
self._sync_token_limit_default()
```

新增方法：

```python
def _sync_token_limit_default(self) -> None:
    """从配置同步 token 限制默认值到上下文预览面板的下拉框。"""
    extract_settings = self.config_manager.get_context_extract_settings()
    token_limit = extract_settings.get("token_limit", 0)
    context_panel = self.continuation_panel.context_preview_panel
    if token_limit <= 0:
        context_panel._token_limit_combo.setCurrentText("不限制")
    else:
        context_panel._token_limit_combo.setCurrentText(f"{token_limit // 1000}k")
```

## 假设与决策

1. **章节切换加载策略**：内存缓存优先（O(1)），其次 SQLite 缓存（同步调用，5s 超时），无缓存则清空面板。SQLite 读取是本地操作，5s 超时足够。
2. **`_on_force_refresh_context` 超时**：从 120s 提升到 300s，以容纳多批次提取。非流式 `extract()` 无 `on_batch_complete` 回调，但内部仍按 token 拆分批次执行。
3. **`_extracting_chapter_id` 归档**：流式提取是异步的，用户可能在提取过程中切换章节。`_on_extract_done` 使用 `_extracting_chapter_id`（提取开始时记录）而非 `_current_chapter`（可能已切换）来正确归档。
4. **项目切换清空缓存**：`_load_project()` 清空 `_context_entries_by_chapter` 和 `_current_context_entries`，避免跨项目污染。
5. **settings_dialog token 限制**：作为默认值，启动时同步到 UI 面板下拉框。UI 面板下拉框可按次覆盖。
6. **向后兼容**：旧缓存条目（纯 list 格式）被 `_get_cached_data` 忽略（返回 None），24h TTL 自动清理。

## 验证步骤

1. 运行测试套件：`cd /workspace && python -m pytest tests/ -x -q`
2. 重点检查上下文提取相关测试：
   - `tests/test_context_extractor.py`（缓存、拆分、批次逻辑）
   - `tests/test_context_preview_panel.py`（UI 方法）
3. 验证无导入错误：`cd /workspace && python -c "from novelforge.ui.main_window import MainWindow"`
