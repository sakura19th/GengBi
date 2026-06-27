# 修复输出显示、查看提示词、初始布局问题

## 摘要

用户报告 3 个问题：
1. **续写输出不显示**：`output_edit` 被加入孤立布局（`content_layout.widget()` 返回 `None`），永远不可见
2. **查看提示词功能没用**：需排查并修复
3. **初始布局问题**：打开软件只能看到前 3 栏，需手动拉才能看到续写控制和续写输出

## 当前状态分析

### 问题 1：续写输出不显示（严重 bug）

**文件**：`/workspace/novelforge/ui/main_window.py` 第 291 行

```python
output_layout = QVBoxLayout(output_panel.content_layout.widget())  # BUG
```

`content_layout` 返回 `QVBoxLayout` 对象。`QLayout.widget()` 对布局对象返回 `None`（只有 `QWidgetItem` 才返回 widget）。因此：
- `QVBoxLayout(None)` 创建无父 widget 的孤立布局
- `output_edit` 和 `auto_scroll_check` 被加入孤立布局，永远不可见

**对比其他面板的正确写法**（第 286 行）：
```python
right_panel.add_widget(self.continuation_panel)  # 正确：用 add_widget
```

### 问题 2：查看提示词功能没用

**文件**：`/workspace/novelforge/ui/main_window.py` 第 1462-1522 行

`_on_view_continuation_prompt` 中 `model = params.get("model") or ""`，但 `get_parameters()`（continuation_panel.py 第 346-357 行）不返回 `model` 键，导致 `model=""`。

虽然 `model=""` 不会导致 `assemble()` 崩溃（token counter 回退估算），但可能导致：
- 对话框出现但内容异常
- 或 `assemble()` 因其他参数缺失抛异常被 try/except 捕获，仅显示错误对话框

**根因**：`_on_view_continuation_prompt` 未像 `_on_start_continuation` 那样从 endpoint 获取 model，也未验证 endpoint 存在。

### 问题 3：初始布局只显示前 3 栏

**文件**：`/workspace/novelforge/ui/main_window.py`

**根因**：
1. `DEFAULT_PANEL_SIZES = [220, 400, 280, 260, 400]` 总和 1560px，远超 `MIN_WINDOW_WIDTH = 1280px`
2. 主窗口无显式 `resize()` 调用，以接近最小尺寸（1280px）打开
3. `setSizes()` 在窗口 show 前调用（`__init__` 第 231 行），splitter 实际宽度未知
4. 各栏最小宽度总和 1270px，几乎等于最小窗口宽度 1280px，无伸缩余量
5. 无 `showEvent` 重写在窗口显示后重新应用尺寸

## 拟议修改

### 修改 1：修复 output_edit 挂载（严重）

**文件**：`/workspace/novelforge/ui/main_window.py` 第 289-295 行

将错误的 `QVBoxLayout(content_layout.widget())` 改为使用 `add_widget()`，与其他面板一致：

```python
# 第 5 栏：续写输出（独立分栏）
output_panel = CollapsiblePanel("续写输出")
output_panel.add_widget(self.continuation_panel.output_edit)
output_panel.add_widget(self.continuation_panel.auto_scroll_check)
output_panel.setMinimumWidth(300)
```

### 修改 2：修复查看提示词功能

**文件**：`/workspace/novelforge/ui/main_window.py` 第 1462-1522 行

`_on_view_continuation_prompt` 方法中：
1. 从 endpoint 获取 model（与 `_on_start_continuation` 一致）
2. 从 endpoint 获取 max_context（若预设未配置）

```python
def _on_view_continuation_prompt(self) -> None:
    """展示续写组装后的完整提示词。"""
    if not self._current_chapter:
        QMessageBox.warning(self, "提示", "请先选择章节")
        return

    # 加载预设
    preset_id = self.continuation_panel.get_selected_preset_id()
    preset = self.preset_service.load_preset(preset_id)
    if preset is None:
        preset = self.preset_service.load_default_preset()

    # 从端点获取 model（与 _on_start_continuation 一致）
    endpoint = self.continuation_panel.get_selected_endpoint()
    model = ""
    if endpoint:
        model = endpoint.get("default_model", "")

    # 获取生成参数
    gen_params = preset.generation_params
    params = self.continuation_panel.get_parameters()
    max_context = params.get("max_context") or gen_params.get("max_context", 32000)
    max_tokens = params.get("max_tokens") or gen_params.get("max_tokens", 2000)
    target_words = params.get("target_words", 2000)
    # ... 其余不变
```

### 修改 3：修复初始布局只显示前 3 栏

**文件**：`/workspace/novelforge/ui/main_window.py`

#### 3a. 设置主窗口初始大小（第 225 行后）

```python
self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
# 设置初始窗口大小为足以容纳所有面板的宽度
self.resize(1600, 900)
```

#### 3b. 调整 DEFAULT_PANEL_SIZES 适配窗口宽度（第 88 行）

```python
# 默认面板宽度（总和应 ≤ 初始窗口宽度 1600）
DEFAULT_PANEL_SIZES = [200, 420, 280, 260, 400]
```

总和 = 200 + 420 + 280 + 260 + 400 = 1560px ≤ 1600px

#### 3c. 添加 showEvent 在窗口显示后重新应用尺寸

在 MainWindow 类中添加：
```python
def showEvent(self, event) -> None:
    """窗口显示事件：首次显示时应用默认面板尺寸。"""
    super().showEvent(event)
    # 首次显示时确保 splitter 尺寸正确
    settings = QSettings("NovelForge", "NovelForge")
    if not settings.value("splitterSizes"):
        # 首次启动，应用默认尺寸
        self._splitter.setSizes(DEFAULT_PANEL_SIZES)
```

#### 3d. 减小各栏最小宽度，留出伸缩余量（第 270-295 行）

```python
left_panel.setMinimumWidth(160)    # 原 180
center_panel.setMinimumWidth(250)  # 原 300
context_panel.setMinimumWidth(200) # 原 250
right_panel.setMinimumWidth(220)   # 原 240
output_panel.setMinimumWidth(280)  # 原 300
```

最小宽度总和 = 160 + 250 + 200 + 220 + 280 = 1110px，远小于 1280px，留出 170px 伸缩余量。

## 假设与决策

1. **output_edit 修复用 `add_widget()`**：与 context_panel、right_panel 保持一致，是最简洁正确的写法。
2. **查看提示词的 model 从 endpoint 获取**：与 `_on_start_continuation` 逻辑一致，不要求用户必须配置端点（model 可为空，token counter 回退估算）。
3. **初始窗口大小设为 1600×900**：足以容纳 DEFAULT_PANEL_SIZES 总和 1560px，且是常见显示器分辨率。
4. **showEvent 仅首次启动应用默认尺寸**：后续启动用 QSettings 恢复的用户自定义尺寸，避免覆盖用户偏好。
5. **减小最小宽度**：留出伸缩余量，避免在最小窗口尺寸下后两栏被挤压不可见。

## 验证步骤

1. 启动应用，确认 5 栏全部可见，无需手动拖拽
2. 点击"开始续写"（需先提取），确认输出区正确显示流式内容
3. 点击"查看提示词"（续写控制区），确认对话框弹出并展示组装后的 messages
4. 点击"查看提示词"（上下文预览区），确认对话框弹出并展示提取提示词
5. 调整窗口到最小尺寸（1280×700），确认 5 栏仍可见（可能较窄但不消失）
6. 运行测试套件：`python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"`
