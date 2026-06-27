# 续写 UI 修正与预设开关 — 剩余实施计划

## 概述

本计划承接前一会话的部分完成工作，完成用户提出的 6 项 UI 修正 + 端到端测试。
前一会话已完成：5 栏布局、用户输入区 UI、端点选择、提取提示词重写（8 维度/5000 token）、
`strip_html_tags()` 函数、`WritingPreset.enabled` 字段、按钮尺寸策略等。

本计划聚焦**尚未完成的代码逻辑与测试**。

---

## 当前状态分析（基于代码核查）

### 已完成
| 项 | 文件 | 状态 |
|---|---|---|
| 5 栏 QSplitter 布局 | `main_window.py` `_setup_central_widget` | ✅ |
| 用户输入区 UI | `continuation_panel.py` `_user_input_edit` | ✅ |
| `get_user_input()`/`clear_user_input()` | `continuation_panel.py` | ✅ |
| 端点选择 + 模型自动填充 | `continuation_panel.py` `_on_endpoint_changed` | ✅ |
| 模型 combo 不可编辑 | `continuation_panel.py` | ✅ |
| `output_edit`/`auto_scroll_check` 属性 | `continuation_panel.py` | ✅ |
| 提取提示词 8 维度重写 | `resources/defaults/extract_prompt.txt` | ✅ |
| `EXTRACT_MAX_TOKENS=5000`/`MAX_CONTENT_LENGTH=500` | `context_extractor.py` | ✅ |
| `VALID_CATEGORIES` 增加 3 类 | `models/context.py` | ✅ |
| `strip_html_tags()` 函数 | `regex_engine.py` line 470 | ✅ |
| `WritingPreset.enabled` 字段 | `models/preset.py` line 75 | ✅ |
| `assemble()` 签名增加 `user_input` | `prompt_assembler.py` line 354 | ✅ |
| main_window 调用传 `user_input` | `main_window.py` line 1150 | ✅ |
| `_refresh_presets` 过滤禁用预设 | `main_window.py` | ✅ |
| 按钮尺寸 `MinimumExpanding` + minWidth 110 | `continuation_panel.py` | ✅ |
| `set_prompt_enabled()` 服务方法 | `preset_service.py` line 788 | ✅ |

### 待完成（本计划范围）
1. `prompt_assembler.py` — `assemble()` 方法体未使用 `user_input`（签名已加，body 未实现）
2. `continuation_worker.py` — 正则后处理后未调用 `strip_html_tags()`
3. `preset_manager.py` — 提示词列表无交互式开关复选框（当前仅文本标签 `[禁用]`）
4. `preset_service.py` — 缺 `set_preset_enabled(preset_id, enabled)` 方法
5. `preset_manager.py` — 缺预设级启用/禁用按钮
6. 端到端测试脚本 — 未编写
7. 测试套件运行验证 — 未执行

---

## 实施步骤

### 步骤 1：实现 `user_input` 注入到 messages

**文件**: `novelforge/core/prompt_assembler.py`
**位置**: `assemble()` 方法，约 line 540（`# 如果没有 chatHistory marker，仍需插入历史` 之后，`# 计算 token 使用情况` 之前）

**改动**:
在 messages 组装完成后、token 计算前，插入用户输入消息：

```python
# 如果没有 chatHistory marker，仍需插入历史
if not any(p.marker == "chatHistory" for p in front_prompts + back_prompts):
    messages.extend(final_history)

# 用户输入指令注入（非空时追加为最后一条 user 消息）
if user_input:
    messages.append({"role": "user", "content": user_input})
```

**理由**: 用户输入作为续写指令，应放在 messages 末尾（最后一条 user 消息），让模型优先响应。token 计算在注入后执行，自动纳入预算。

---

### 步骤 2：续写输出后剥离 HTML 标签

**文件**: `novelforge/services/continuation_worker.py`
**位置**: `_async_run()` 方法，约 line 358（AI_OUTPUT 正则应用之后，模板渲染之前或之后）

**改动**:
在正则后处理与模板渲染完成后，对 `final_content` 调用 `strip_html_tags`：

```python
# M3: 对结果应用 AI_OUTPUT 正则和接收后模板渲染
final_content = result.content
if self.regex_engine is not None and final_content:
    try:
        from novelforge.models.regex import PLACEMENT_AI_OUTPUT
        final_content = self.regex_engine.apply_to_text(
            final_content, placement=PLACEMENT_AI_OUTPUT,
        )
    except Exception as e:
        logger.error("AI_OUTPUT 正则后处理失败: %s", e)

if self.template_engine is not None and final_content:
    try:
        rendered, error = self.template_engine.render_post_receive(...)
        if not error:
            final_content = rendered
    except Exception as e:
        logger.error("接收后模板渲染失败: %s", e)

# 新增：剥离 HTML 标签，输出纯文本（TGbreak 等预设正则会产生 HTML 卡片/折叠块）
if final_content:
    try:
        from novelforge.core.regex_engine import strip_html_tags
        final_content = strip_html_tags(final_content)
    except Exception as e:
        logger.warning("HTML 标签剥离失败: %s", e)
```

**理由**: TGbreak 预设的 13 条 AI_OUTPUT 正则脚本会将 `<draft_notes>`、`<w2g>` 等标记转换为 HTML 折叠块/卡片。QPlainTextEdit 无法渲染 HTML，需剥离为纯文本。

---

### 步骤 3：预设管理器 — 提示词开关复选框

**文件**: `novelforge/ui/preset_manager.py`

**改动 A** — `_refresh_prompt_list()` 方法（约 line 358）：
将每个 `QListWidgetItem` 设置为带复选框，复选框状态反映 `entry.enabled`：

```python
item = QListWidgetItem(label)
item.setData(Qt.ItemDataRole.UserRole, prompt.identifier)
item.setData(Qt.ItemDataRole.UserRole + 1, entry.enabled)
# 新增：复选框
item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
item.setCheckState(
    Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked
)
self._prompt_list.addItem(item)
```

**改动 B** — `_setup_connections()` 方法：
连接 `itemChanged` 信号到新方法 `_on_prompt_check_changed`：

```python
self._prompt_list.itemChanged.connect(self._on_prompt_check_changed)
```

**改动 C** — 新增 `_on_prompt_check_changed` 方法：
```python
def _on_prompt_check_changed(self, item: QListWidgetItem) -> None:
    """提示词复选框状态变化。"""
    if not self._current_preset:
        return
    identifier = item.data(Qt.ItemDataRole.UserRole)
    enabled = item.checkState() == Qt.CheckState.Checked
    # 防止 _refresh_prompt_list 触发递归
    self._prompt_list.blockSignals(True)
    try:
        self.preset_service.set_prompt_enabled(
            self._current_preset, identifier, enabled
        )
        self.preset_service.save_preset(self._current_preset)
        # 更新 item 数据与标签
        item.setData(Qt.ItemDataRole.UserRole + 1, enabled)
        prompt_map = {p.identifier: p for p in self._current_preset.prompts}
        prompt = prompt_map.get(identifier)
        if prompt:
            label = prompt.name or prompt.identifier
            if prompt.marker:
                label = f"[marker] {label}"
            elif prompt.system_prompt:
                label = f"[系统] {label}"
            if not enabled:
                label = f"[禁用] {label}"
            item.setText(label)
    finally:
        self._prompt_list.blockSignals(False)
    self.preset_changed.emit(self._current_preset.id)
```

**改动 D** — `_refresh_prompt_list()` 开头加 `blockSignals` 防止刷新时触发：
```python
self._prompt_list.blockSignals(True)
try:
    self._prompt_list.clear()
    # ... 原有逻辑
finally:
    self._prompt_list.blockSignals(False)
```

---

### 步骤 4：预设服务 — 新增 `set_preset_enabled`

**文件**: `novelforge/services/preset_service.py`
**位置**: `set_prompt_enabled` 方法之后（约 line 810）

**改动**:
```python
def set_preset_enabled(self, preset_id: str, enabled: bool) -> bool:
    """切换预设启用状态。

    禁用的预设不会出现在续写面板的预设下拉列表中。

    Args:
        preset_id: 预设 ID
        enabled: 是否启用

    Returns:
        是否成功（预设不存在时返回 False）
    """
    preset = self.load_preset(preset_id)
    if preset is None:
        return False
    if preset.id == "default":
        logger.warning("默认预设不允许禁用")
        return False
    preset.enabled = enabled
    self.save_preset(preset)
    return True
```

**理由**: 默认预设始终可用，不允许禁用。

---

### 步骤 5：预设管理器 — 预设级启用/禁用按钮

**文件**: `novelforge/ui/preset_manager.py`

**改动 A** — `_setup_ui()` 顶部工具栏（约 line 130）：
在 `_duplicate_btn` 之后、`addStretch()` 之前添加启用/禁用按钮：

```python
self._toggle_preset_btn = QPushButton("禁用预设")
toolbar.addWidget(self._toggle_preset_btn)
```

**改动 B** — `_setup_connections()`：
```python
self._toggle_preset_btn.clicked.connect(self._on_toggle_preset_enabled)
```

**改动 C** — 新增方法：
```python
def _on_toggle_preset_enabled(self) -> None:
    """切换当前预设的启用/禁用状态。"""
    if not self._current_preset:
        return
    if self._current_preset.id == "default":
        QMessageBox.warning(self, "提示", "默认预设不允许禁用")
        return
    new_state = not self._current_preset.enabled
    if self.preset_service.set_preset_enabled(
        self._current_preset.id, new_state
    ):
        self._current_preset.enabled = new_state
        self._update_toggle_preset_btn_text()
        self.preset_changed.emit(self._current_preset.id)
    else:
        QMessageBox.warning(self, "错误", "切换预设状态失败")

def _update_toggle_preset_btn_text(self) -> None:
    """更新启用/禁用按钮文本。"""
    if not self._current_preset:
        self._toggle_preset_btn.setEnabled(False)
        self._toggle_preset_btn.setText("禁用预设")
        return
    # 默认预设不允许禁用
    if self._current_preset.id == "default":
        self._toggle_preset_btn.setEnabled(False)
        self._toggle_preset_btn.setText("默认预设（始终启用）")
        return
    self._toggle_preset_btn.setEnabled(True)
    if self._current_preset.enabled:
        self._toggle_preset_btn.setText("禁用预设")
    else:
        self._toggle_preset_btn.setText("启用预设")
```

**改动 D** — `_load_preset()` 末尾调用 `self._update_toggle_preset_btn_text()`。

---

### 步骤 6：端到端测试脚本

**文件**: `tests/test_tgbreak_e2e.py`（新建）

**测试目标**: 验证 TGbreak 预设的提示词与正则能正确处理实际小说生成场景，最终输出纯文本。

**测试内容**:
1. 导入 `TGbreak😺V3.1.1.json` 预设（122 提示 + 13 正则脚本）
2. 导入 `穿进赛博游戏后干掉BOSS成功上位.txt` 小说（取前几章）
3. 用 PromptAssembler 组装 messages（验证 ST 宏 `{{setvar}}`/`{{getvar}}`/`{{user}}`/`{{char}}` 正确解析）
4. 模拟 AI 输出含 TGbreak 标记的文本（如 `<draft_notes>...</draft_notes>`、`<w2g>...</w2g>`）
5. 应用 AI_OUTPUT 正则脚本（13 条）
6. 调用 `strip_html_tags()` 剥离 HTML
7. 断言最终输出为纯文本（无 `<` 标签、无 HTML 实体、含可读中文正文）

**关键断言**:
- `assemble_result.messages` 非空且含 system/user 角色
- 正则应用后文本含 HTML 标签（证明正则生效）
- `strip_html_tags()` 后文本无 `<` 字符（纯文本）
- 用户输入 `user_input` 出现在 messages 末尾

---

### 步骤 7：运行测试套件验证

**命令**:
```bash
cd /workspace && python -m pytest tests/ -x -q 2>&1 | tail -30
```

**验证点**:
- 全部测试通过（含原有 177 测试 + 新增 TGbreak E2E 测试）
- 无 import 错误
- 无回归

---

## 假设与决策

1. **user_input 注入位置**: 作为最后一条 user 消息追加到 messages 末尾（非插入到 chatHistory 之前），让模型将其视为即时指令。
2. **HTML 剥离时机**: 在 AI_OUTPUT 正则 + 模板渲染之后执行，确保所有 HTML 转换完成后再剥离。
3. **预设禁用语义**: 禁用的预设从续写面板下拉列表隐藏（`_refresh_presets` 已实现过滤），但仍在预设管理器中可见可编辑。
4. **默认预设不可禁用**: 保证系统始终有可用预设。
5. **提示词复选框**: 使用 `QListWidgetItem.setCheckState`，不引入额外控件，保持拖拽排序兼容。
6. **测试不调用真实 LLM**: 用模拟 AI 输出（含 TGbreak 标记的样例文本）验证正则与剥离链路。

---

## 验证步骤

1. 运行 `python -m pytest tests/test_tgbreak_e2e.py -v` — 新增 E2E 测试通过
2. 运行 `python -m pytest tests/ -q` — 全量测试无回归
3. 检查 `strip_html_tags` 输出无残留 HTML 标签
4. 检查 `user_input` 出现在 messages 末尾
