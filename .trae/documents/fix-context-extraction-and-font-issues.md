# 修复上下文提取完整性与字体错误

## Summary

修复三个 bug + 新增一个功能：
1. **"全部前文"提取失效**：`_get_lookback_chapters()` 在 `lookback=0` 时返回空列表，导致"全部前文"选项无法工作
2. **部分章节仅显示标题**：`_current_chapters` 通过 `list_chapters()` 加载，只含元数据不含正文，导致提取/续写时大部分章节 content 为空
3. **QFont::setPointSize 错误**：`apply_font_to_editor()` 从配置读取 `font_size` 时无下界保护，配置值为 -1 或 0 时触发 Qt 警告
4. **新增：流式提取实时查看窗口**：提取上下文时显示 LLM 原始流式输出，让用户看到实施进度，完全接收后再组装

同时确认：预设 prompts/regex 已正确地仅在写作场景使用，无需修改。

## Current State Analysis

### Bug 1: lookback=0 返回空列表

文件：`novelforge/services/context_extractor.py` 第 388-421 行

```python
def _get_lookback_chapters(self, chapters, current_chapter, lookback):
    ...
    # lookback=0 时：
    start = max(0, current_idx - 0 + 1)  # = current_idx + 1
    return sorted_chapters[current_idx + 1 : current_idx + 1]  # = 空列表 []
```

`lookback=0` 表示"全部前文"，应返回从第 0 章到当前章节的所有章节。实际返回空列表，导致后续 `if not target_chapters` 判断为真，返回 `status="skipped"`。

`build_prompt_for_preview()` 方法（第 958-967 行）有相同的 lookback 逻辑，存在同样 bug。

`continuation_worker.py` 的 `assemble_simple_messages()`（第 75-118 行）也有相同的 `start = max(0, current_idx - lookback + 1)` 逻辑，lookback=0 时同样返回空。但该函数是 M1 遗留函数，主流程使用 `prompt_assembler.assemble()`，影响较小。

### Bug 2: 章节正文未加载

文件：`novelforge/ui/main_window.py`、`novelforge/core/storage.py`、`novelforge/services/storage_service.py`

调用链：
- `main_window._load_project()` / `_refresh_chapter_list()` 调用 `storage_service.list_chapters()`
- `Storage.list_chapters()`（storage.py:531-538）只执行 `SELECT * FROM chapters`，不读取文件系统
- `Storage._row_to_chapter()` 返回的字典不含 content 键
- `Chapter.model_validate(d)` 时 content 使用默认值 `""`
- 只有用户点击选中某章节时，`_on_chapter_selected()` 调用 `load_chapter()` 才加载该章节正文

结果：`_current_chapters` 中只有当前选中章节有 content，其余均为空字符串。提取上下文时 `chapters_text` 拼接为 `## {标题}\n\n{空字符串}`，LLM 只看到标题看不到正文。

影响范围：
- 上下文提取（`extract()` / `extract_streaming()` / `build_prompt_for_preview()`）
- 续写提示词组装（`prompt_assembler.assemble()` 的 `_build_history()`）
- Agent 各阶段（`agent_orchestrator._build_chapters_text()`）
- 缓存 key 计算（`_compute_chapters_hash()` 对空 content 计算哈希，可能导致缓存错误命中）

### Bug 3: QFont::setPointSize(-1)

文件：`novelforge/ui/font_settings.py` 第 201-231 行

```python
def apply_font_to_editor(editor, appearance):
    font_size = int(appearance.get("font_size", 14))  # 无下界保护
    ...
    font.setPointSize(font_size)  # font_size=-1 时触发 Qt 警告
```

配置文件中 `font_size` 可能为 -1、0 或其他非法值（手动编辑、配置损坏、旧版本残留）。`apply_font_to_editor` 无下界校验直接传入 `setPointSize`，触发 `QFont::setPointSize: Point size <= 0 (-1)` 警告。

### 确认：预设 prompts/regex 使用范围（无需修改）

经全面检查：
- **上下文提取**：使用独立模板 `extract_prompt.txt`，简单 `str.replace` 宏替换，不使用 preset prompts、regex_engine、template_engine ✓
- **Agent 非写作阶段**（分析/大纲/验证/修订）：使用独立模板 `agent/phase_*.txt`，简单 `str.replace`，不使用 preset prompts、regex、template ✓
- **Agent 写作阶段**：使用 `prompt_assembler.assemble()`（含 preset prompts + regex + template），后处理使用 AI_OUTPUT 正则 + 模板渲染 ✓
- **单次续写**：使用 `prompt_assembler.assemble()`，后处理使用 AI_OUTPUT 正则 + 模板渲染 ✓

当前实现已符合"预设 prompts/regex 仅在写作时使用"的原则，无需修改。

## Proposed Changes

### Change 1: 修复 `_get_lookback_chapters` 的 lookback=0 处理

**文件**：`novelforge/services/context_extractor.py`

**修改方法**：`_get_lookback_chapters()`（第 388-421 行）

**What**：当 `lookback <= 0` 时，返回从第 0 章到当前章节（含）的所有章节。

**Why**：`lookback=0` 表示"全部前文"，当前逻辑返回空列表导致提取失败。

**How**：
```python
def _get_lookback_chapters(self, chapters, current_chapter, lookback):
    sorted_chapters = sorted(chapters, key=lambda c: c.index)
    current_idx = -1
    for i, ch in enumerate(sorted_chapters):
        if ch.id == current_chapter.id:
            current_idx = i
            break

    if current_idx == -1:
        # 当前章节不在列表中
        if lookback <= 0:
            return sorted_chapters  # 全部前文
        return sorted_chapters[-lookback:] if lookback > 0 else []

    # lookback <= 0 表示全部前文：返回从第 0 章到当前章节
    if lookback <= 0:
        return sorted_chapters[: current_idx + 1]

    # 取前 lookback 章（含当前章节）
    start = max(0, current_idx - lookback + 1)
    return sorted_chapters[start : current_idx + 1]
```

同时修复 `build_prompt_for_preview()` 中相同的 lookback 逻辑（第 958-967 行附近，查找相同的切片逻辑）。

### Change 2: 修复 `assemble_simple_messages` 的 lookback=0 处理

**文件**：`novelforge/services/continuation_worker.py`

**修改方法**：`assemble_simple_messages()`（第 75-118 行）

**What**：当 `lookback <= 0` 时，返回从第 0 章到当前章节的所有章节。

**Why**：与 Bug 1 相同的切片逻辑，虽然主流程不使用此函数，但应保持一致性。

**How**：在 `start = max(0, current_idx - lookback + 1)` 之前增加 `lookback <= 0` 的特殊处理。

### Change 3: 提取/续写前确保章节正文已加载

**文件**：`novelforge/services/storage_service.py`、`novelforge/ui/main_window.py`

**What**：新增 `load_chapter_contents()` 方法批量加载章节正文，在提取和续写前调用。

**Why**：`list_chapters()` 只加载元数据，导致 `_current_chapters` 中大部分章节 content 为空。

**How**：

1. 在 `StorageService` 中新增方法：
```python
async def load_chapter_contents(self, chapters: list[Chapter]) -> list[Chapter]:
    """批量加载章节正文，返回填充了 content 的章节列表副本。"""
    result = []
    for ch in chapters:
        if ch.content:
            result.append(ch)
        else:
            loaded = self.load_chapter(ch.id)
            result.append(loaded if loaded else ch)
    return result
```

2. 在 `main_window.py` 中新增辅助方法：
```python
def _ensure_chapter_contents(self) -> None:
    """确保 _current_chapters 中所有章节的 content 已加载。"""
    need_reload = any(not ch.content for ch in self._current_chapters)
    if not need_reload:
        return
    # 用异步运行器加载
    chapters_with_content = async_runner.run_coroutine(
        self.storage_service.load_chapter_contents(self._current_chapters)
    )
    self._current_chapters = chapters_with_content
```

3. 在以下位置调用 `_ensure_chapter_contents()`：
   - `_on_extract_requested()` 开头（提取上下文前）
   - `_on_view_extract_prompt()` 开头（预览提取提示词前）
   - `_on_start_continuation()` 中 assemble 调用前（单次续写前）
   - `_on_start_agent_continuation()` 中创建 orchestrator 前（Agent 续写前）
   - `_on_view_continuation_prompt()` 中 assemble 调用前（预览续写提示词前）

### Change 4: 修复 QFont::setPointSize 下界保护

**文件**：`novelforge/ui/font_settings.py`

**修改方法**：`apply_font_to_editor()`（第 201-231 行）

**What**：增加 `font_size` 下界保护，确保 `>= 1`。

**Why**：配置中 `font_size` 可能为 -1、0 等非法值，导致 Qt 警告。

**How**：
```python
def apply_font_to_editor(editor, appearance):
    font_family = appearance.get("font_family", "")
    font_size = int(appearance.get("font_size", 14))
    if font_size < 1:
        font_size = 14  # 非法值回退到默认
    ...
```

### Change 5: 修复 `_on_force_refresh_context` 的 lookback 来源

**文件**：`novelforge/ui/main_window.py`

**What**：`_on_force_refresh_context()`（F5）当前调用 `extract()` 不传 `lookback_override`，使用配置默认值而非 UI 选择值。

**Why**：用户在 UI 选择了"全部前文"但 F5 刷新时用的是配置默认值 5，行为不一致。

**How**：在 `_on_force_refresh_context()` 中读取 UI 的 lookback 配置并传入。但由于 `extract()` 不接受 `lookback_override`，需要改为调用 `extract_streaming()` 或为 `extract()` 添加 `lookback_override` 参数。

**决策**：为 `extract()` 添加 `lookback_override` 参数（与 `extract_streaming` 一致），并在 `_on_force_refresh_context` 中传入 UI 选择值。

### Change 6: 新增流式提取实时查看窗口

**文件**：`novelforge/ui/context_preview_panel.py`

**What**：在上下文预览面板中新增一个可折叠的流式输出查看区，提取时实时显示 LLM 原始流式输出，让用户看到实施进度。

**Why**：当前 `update_extraction_progress()` 只更新字符计数（"提取中... 已接收 N 字符"），用户无法看到实际内容。用户需要看到流式传输的原始内容以了解提取进度。

**How**：

1. 在 `_setup_ui()` 中，loading 区域之后、元数据信息之前，新增流式输出查看区：
```python
# ===== 流式输出查看区（提取时显示原始 LLM 输出）=====
self._stream_group = QGroupBox("流式输出（实时）")
self._stream_group.setCheckable(True)
self._stream_group.setChecked(False)  # 默认折叠
self._stream_group.setVisible(False)  # 默认隐藏，提取时显示
stream_layout = QVBoxLayout(self._stream_group)

self._stream_view = QPlainTextEdit()
self._stream_view.setReadOnly(True)
self._stream_view.setPlaceholderText("等待流式输出...")
self._stream_view.setMaximumHeight(200)
self._stream_view.setStyleSheet("font-size: 12px; font-family: monospace;")
stream_layout.addWidget(self._stream_view)

layout.addWidget(self._stream_group)
```

2. 修改 `start_extraction()`：显示流式查看区，清空内容：
```python
self._stream_view.clear()
self._stream_view.setPlaceholderText("等待流式输出...")
self._stream_group.setVisible(True)
self._stream_group.setChecked(True)  # 提取时自动展开
self._stream_group.setTitle("流式输出（实时接收中...）")
```

3. 修改 `update_extraction_progress()`：除了更新字符计数，还追加原始 chunk 到流式查看区：
```python
def update_extraction_progress(self, text: str) -> None:
    if not self._is_extracting:
        return
    # 追加原始 chunk 到流式查看区
    self._stream_view.insertPlainText(text)
    # 自动滚动到底部
    cursor = self._stream_view.textCursor()
    cursor.movePosition(QTextCursor.End)
    self._stream_view.setTextCursor(cursor)
    # 更新字符计数（保留原有逻辑）
    current = self._loading_text.text()
    ...
```

4. 修改 `finish_extraction()`：更新标题为"流式输出（接收完成）"，保持可见但可折叠：
```python
self._stream_group.setTitle("流式输出（接收完成）")
# 保持可见，用户可查看完整原始输出
```

5. 修改 `fail_extraction()` / `cancel_extraction()`：更新标题为"流式输出（已中断）"，保持可见。

6. 需要导入 `QTextCursor`（从 `PySide6.QtGui`）。

**设计决策**：
- 流式查看区默认折叠隐藏，提取开始时自动展开显示
- 提取完成后保持可见（用户可查看完整原始输出），标题变为"接收完成"
- 使用 `QPlainTextEdit`（只读）而非 `QLabel`，因为流式内容可能很长
- 最大高度 200px，超出可滚动
- 使用等宽字体（monospace）便于查看 JSON 结构

## Assumptions & Decisions

1. **lookback=0 语义**：`0` 表示"全部前文"（从第 0 章到当前章节含），负数也视为"全部前文"（防御性处理）。
2. **章节正文加载策略**：不在 `list_chapters` 中加载正文（避免影响章节列表 UI 性能），而是在提取/续写前按需加载。加载后更新 `_current_chapters` 缓存，避免重复加载。
3. **`load_chapter_contents` 实现方式**：遍历调用 `load_chapter`（已有方法，含文件系统读取），而非新增批量 SQL + 批量文件读取（保持简单，章节数通常 < 100）。
4. **QFont 修复范围**：仅修复 `apply_font_to_editor`，不修改配置文件中的非法值（防御性编程，不改变持久化数据）。
5. **预设 prompts/regex 范围**：经确认当前实现已正确，无需修改。在计划中记录此结论。
6. **`assemble_simple_messages` 修复**：虽然主流程不使用，但为一致性修复 lookback=0 bug。
7. **`_on_force_refresh_context` 修复**：F5 强制刷新应使用 UI 选择的 lookback 值，与"提取上下文"按钮行为一致。

## Verification Steps

1. **lookback=0 测试**：选择"全部前文"提取上下文，验证返回所有前文章节（非空列表）
2. **章节正文完整性测试**：提取上下文后查看提示词预览，验证所有章节都有正文（非仅标题）
3. **最近 N 章测试**：分别选择"最近 3 章"、"最近 5 章"等，验证返回正确数量的章节且均有正文
4. **QFont 错误测试**：将配置中 `font_size` 设为 -1，启动应用，验证不再出现 `QFont::setPointSize` 警告
5. **单次续写测试**：验证续写时历史章节有正文（提示词预览确认）
6. **Agent 续写测试**：验证 Agent 各阶段的 chapters_text 包含完整正文
7. **F5 强制刷新测试**：选择"全部前文"后按 F5，验证使用 UI 选择的 lookback 值
8. **流式查看窗口测试**：提取上下文时，验证流式输出查看区实时显示 LLM 原始输出，提取完成后标题变为"接收完成"
9. **运行现有测试套件**：`python -m pytest tests/ --ignore=tests/test_m5_polish.py -q` 确认无回归
