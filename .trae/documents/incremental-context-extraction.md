# 上下文分段提取增量更新 + 续写回溯章节数解禁

## 概述

本次修改包含两部分：

1. **分段提取增量更新**：按 token 拆分的多批次提取中，后续批次在提示词中附上已有提取结果，LLM 基于新增章节输出"新增"或"修改"的条目，代码按 uid 合并（替换=修改，新增=追加），而非每批独立提取。

2. **续写回溯章节数解禁**：当前续写面板的"回溯章节数"spinbox（范围 1-50）**完全未接入续写流程**——`_build_history()` 取全部章节，token 裁剪将其砍到个位数。本次将 lookback_chapters 接入 `assemble()` 和 `_build_history()`，作为历史章节数的主控制，并解除 spinbox 上限。

## 当前状态分析

### 问题 1：分段提取无增量更新

`context_extractor.py` 的批次循环（`extract()` line 748 / `extract_streaming()` line 1038）：
- `_build_prompt(project, batch_chapters, config)` 不传已有条目
- 合并逻辑"首次 uid 优先"（`seen_uids` 集合），后续批次无法修改已有条目

### 问题 2：续写回溯章节数未接入

- `continuation_panel.py:167` — `self._lookback_spin.setRange(1, 50)`，值传入 `params["lookback_chapters"]`
- `main_window.py:1210` — `assemble()` 调用**不传 lookback_chapters**
- `prompt_assembler.py:695` — `_build_history(chapters, current_chapter)` 取**全部**章节（旧→新到当前章节止），无 lookback 限制
- `prompt_assembler.py:492` — `_trim_history()` 按 token 预算从新到旧填充，max_context=32000 时仅容纳 ~5-8 章 → "个位数"
- `settings_dialog.py:389` — 默认回溯章节数 spinbox 范围 1-50

**根因**：lookback_chapters 从 UI 到 params 到 main_window，但从未传入 assemble()，是断路状态。

## 实施方案

### 第一部分：分段提取增量更新

#### 1.1 修改 `_build_prompt()` — 新增 `previous_entries` 参数

**文件**：`novelforge/services/context_extractor.py`（line 337）

```python
def _build_prompt(
    self,
    project: Project | None,
    chapters: list[Chapter],
    config: dict[str, Any],
    previous_entries: list[ContextEntry] | None = None,
) -> str:
    # ... 原有模板填充逻辑不变 ...
    prompt = template
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)

    # 增量更新模式：追加已有提取结果
    if previous_entries:
        prompt += self._build_incremental_section(previous_entries)

    return prompt
```

**新增辅助方法 `_build_incremental_section()`**：

```python
def _build_incremental_section(
    self, previous_entries: list[ContextEntry]
) -> str:
    """构建增量更新指令段（附在 prompt 末尾）。"""
    condensed = []
    for e in previous_entries:
        content = (e.content or "")[:200]
        condensed.append({
            "uid": e.uid,
            "category": e.category,
            "content": content,
        })
    previous_json = json.dumps(condensed, ensure_ascii=False, indent=2)

    return (
        "\n\n# 增量更新模式（重要）\n"
        "以下是基于前序章节已提取的条目。请基于本次新增章节内容，"
        "仅输出需要**新增**或**修改**的条目：\n"
        "- 保持已有 uid 表示修改该条目（更新 content 以反映新章节带来的变化）\n"
        "- 使用新 uid 表示新增条目\n"
        "- **无需重复输出未发生变化的条目**\n"
        "- 若本次新增章节未带来任何新信息或变化，可输出空数组 `[]`\n\n"
        f"已有条目：\n```json\n{previous_json}\n```"
    )
```

#### 1.2 修改 `extract()` 批次循环

**文件**：`novelforge/services/context_extractor.py`（line 743-875）

**修改 1**：构建 prompt 时传入已有条目

```python
prompt = self._build_prompt(
    project, batch_chapters, config,
    previous_entries=all_entries if batch_idx > 0 else None,
)
```

**修改 2**：合并逻辑从"首次优先去重"改为"替换式合并"

```python
# 删除 seen_uids: set[str] = set()
# 新增 entries_by_uid: dict[str, int] = {}  # uid -> all_entries 索引

for raw in raw_entries:
    entry = _validate_and_normalize_entry(raw, batch_range, extracted_at)
    if entry is not None:
        if entry.uid in entries_by_uid:
            # 增量更新：替换已有条目
            idx = entries_by_uid[entry.uid]
            all_entries[idx] = entry
            logger.debug("增量更新条目: %s", entry.uid)
        else:
            # 新增条目
            entries_by_uid[entry.uid] = len(all_entries)
            all_entries.append(entry)
```

#### 1.3 修改 `extract_streaming()` 批次循环

**文件**：`novelforge/services/context_extractor.py`（line 1038-1155）

与 `extract()` 完全相同的两处修改（prompt 传参 + 替换式合并）。

#### 1.4 不修改 `build_prompt_for_preview()`

预览方法不传 `previous_entries`，保持原有行为。

### 第二部分：续写回溯章节数解禁

#### 2.1 修改 `PromptAssembler.assemble()` — 新增 `lookback_chapters` 参数

**文件**：`novelforge/core/prompt_assembler.py`（line 341）

```python
def assemble(
    self,
    preset: WritingPreset,
    chapters: list[Any],
    current_chapter: Any,
    context_entries: list[ContextEntry] | None = None,
    model: str = "",
    max_context: int = 32000,
    max_tokens: int = 2000,
    target_words: int = 2000,
    novel_profile: Any = None,
    project_id: str = "",
    chapter_metadata: dict[str, Any] | None = None,
    user_input: str = "",
    lookback_chapters: int = 0,  # 新增：0=全部前文
) -> AssembleResult:
```

在阶段 2 调用 `_build_history()` 时传入：

```python
history_messages = self._build_history(
    chapters, current_chapter, lookback_chapters
)
```

#### 2.2 修改 `_build_history()` — 按 lookback 限制章节数

**文件**：`novelforge/core/prompt_assembler.py`（line 695）

```python
def _build_history(
    self,
    chapters: list[Any],
    current_chapter: Any,
    lookback_chapters: int = 0,  # 新增：0=全部前文
) -> list[dict[str, Any]]:
    # ... 原有排序和查找当前章节逻辑不变 ...

    history: list[dict[str, Any]] = []
    for ch in sorted_chapters:
        ch_id = ch.get("id", "") if isinstance(ch, dict) else getattr(ch, "id", "")
        history.append(_build_history_message(ch))
        if ch_id == current_id:
            break

    # 按 lookback 限制章节数（从末尾保留最近 N 章，0=全部）
    if lookback_chapters > 0 and len(history) > lookback_chapters:
        history = history[-lookback_chapters:]

    # ... 原有"当前章节不在列表中"逻辑不变 ...
    return history
```

#### 2.3 修改 `main_window.py` — 传递 lookback_chapters 到 assemble()

**文件**：`novelforge/ui/main_window.py`

**单次续写**（line 1210 附近）：

```python
lookback_chapters = params.get("lookback_chapters", 0)

assemble_result = self.prompt_assembler.assemble(
    preset=preset,
    chapters=self._current_chapters,
    current_chapter=self._current_chapter,
    context_entries=entries,
    model=model,
    max_context=max_context,
    max_tokens=max_tokens,
    target_words=target_words,
    novel_profile=novel_profile,
    project_id=self._current_project_id or "",
    chapter_metadata=chapter_metadata,
    user_input=user_input,
    lookback_chapters=lookback_chapters,  # 新增
)
```

**查看提示词**（line 1917 附近）：同样添加 `lookback_chapters` 参数。

#### 2.4 修改 `agent_orchestrator.py` — 传递 lookback_chapters

**文件**：`novelforge/services/agent_orchestrator.py`（line 467）

```python
result = self._prompt_assembler.assemble(
    preset=self.preset,
    chapters=self.chapters,
    current_chapter=self.current_chapter,
    context_entries=all_entries,
    model=self.model,
    max_context=self.parameters.get("max_context", 32000),
    max_tokens=self.parameters.get("max_tokens", 2000),
    target_words=self.parameters.get("target_words", 2000),
    novel_profile=self.novel_profile,
    project_id=self.project_id,
    chapter_metadata=self.chapter_metadata,
    user_input=self.user_input,
    lookback_chapters=self.parameters.get("lookback_chapters", 0),  # 新增
)
```

#### 2.5 修改续写面板 spinbox 范围

**文件**：`novelforge/ui/continuation_panel.py`（line 167）

```python
# 旧：self._lookback_spin.setRange(1, 50)
# 新：0=全部前文，上限 99999（实际不限制）
self._lookback_spin.setRange(0, 99999)
self._lookback_spin.setValue(5)
self._lookback_spin.setSpecialValueText("全部前文")  # 值为 0 时显示"全部前文"
```

#### 2.6 修改设置对话框 spinbox 范围

**文件**：`novelforge/ui/settings_dialog.py`（line 389）

```python
# 旧：self._lookback_spin.setRange(1, 50)
# 新：
self._lookback_spin.setRange(0, 99999)
self._lookback_spin.setSpecialValueText("全部前文")
```

## 假设与决策

### 增量提取部分

1. **增量指令追加在 prompt 末尾**：不修改模板文件，避免影响自定义模板用户。末尾指令在 LLM 注意力机制中权重高，足以覆盖默认"输出全部"要求。
2. **精简展示已有条目**：仅含 `uid`/`category`/`content`（截断 200 字），节省 token 同时让 LLM 识别条目。
3. **替换式合并**：相同 uid=修改（替换），新 uid=新增（追加），未输出=保持不变。比"输出完整集合"更安全（LLM 遗漏时不丢失）。
4. **首批次不受影响**：`batch_idx == 0` 时 `previous_entries=None`，行为不变。

### 回溯章节数解禁部分

5. **lookback_chapters 作为主控制**：`_build_history()` 先按 lookback 限制章节数，再由 token 裁剪作为安全网。用户设 lookback=20 且 max_context 足够大时，20 章全部保留；max_context 不足时 token 裁剪仍会裁掉最旧的章节。
6. **0=全部前文**：与上下文提取面板的"全部前文"语义一致。spinbox 使用 `setSpecialValueText("全部前文")` 在值为 0 时显示友好文本。
7. **保留 token 裁剪作为安全网**：不移除 `_trim_history()`，防止超出模型上下文窗口导致 API 错误。用户可通过预设管理器将 max_context 设至 1,000,000。
8. **spinbox 上限 99999**：实际不限制，避免 QSpinBox 最大值的整数溢出问题。
9. **`assemble()` 的 lookback_chapters 默认值 0**：向后兼容，现有调用方（如测试）不传此参数时行为不变（全部前文）。

## 验证步骤

1. 运行测试套件：`cd /workspace && python -m pytest tests/ -x -q`
2. 验证无导入错误：`cd /workspace && python -c "from novelforge.ui.main_window import MainWindow; from novelforge.core.prompt_assembler import PromptAssembler"`
3. 重点验证现有测试不回归：
   - `test_build_prompt_*`（无 previous_entries 时行为不变）
   - `test_extract_*`（单批次时无增量逻辑）
   - PromptAssembler 相关测试（lookback_chapters 默认 0 = 全部前文）
