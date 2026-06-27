# 修复上下文提取流式预览与模型选择

## 摘要

用户报告上下文提取预览无法获取流式内容、无法格式化显示（但 API 实际已完整返回），且未使用全局规定的模型而是使用了 `openai/gpt-4o-mini`。经探索发现两个独立的核心 bug：

1. **流式跨线程通信失效**：`on_chunk`/`on_done` 回调在后台 asyncio 线程中用 `QTimer.singleShot(0, lambda)` 转发到 UI 线程，但后台线程无 Qt 事件循环，回调永不触发 → 流式内容和最终结果都不显示。
2. **模型选择逻辑错误**：`or DEFAULT_EXTRACTOR_MODEL` 把空字符串转成 `"gpt-4o-mini"`，加上 `!= DEFAULT_EXTRACTOR_MODEL` 启发式判断，导致用户配置被忽略。

## 当前状态分析

### 问题 1：流式跨线程通信失效

**调用链**：`_on_extract_requested()` → `asyncio.run_coroutine_threadsafe(extract_streaming(...), loop)` → 后台 `AsyncLoopRunner` 线程执行 → `on_chunk(chunk.content)` 回调 → `QTimer.singleShot(0, lambda)` → ❌ 永不触发

**根因**（[main_window.py:1690-1694](file:///workspace/novelforge/ui/main_window.py#L1690-L1694)）：
```python
def on_chunk(text: str) -> None:
    QTimer.singleShot(
        0, lambda t=text: context_panel.update_extraction_progress(t)
    )
```

- `AsyncLoopRunner` 是普通 `threading.Thread`（[async_runner.py:42-44](file:///workspace/novelforge/services/async_runner.py#L42-L44)），运行 `asyncio.run_forever()`，**无 Qt 事件循环**
- `QTimer.singleShot(0, callable)` 只传 callable 不传 QObject receiver 时，定时器在**调用线程**创建
- 后台线程无 Qt 事件循环 → 定时器事件不被处理 → lambda 永不执行
- `update_extraction_progress()`（[context_preview_panel.py:787-811](file:///workspace/novelforge/ui/context_preview_panel.py#L787-L811)）从未被调用 → 流式内容不显示
- 同样 `on_done` 中的 `QTimer.singleShot`（[main_window.py:1710](file:///workspace/novelforge/ui/main_window.py#L1710)）也不触发 → `finish_extraction()` 不被调用 → 最终格式化结果不显示

**对比**：续写流程使用 `ContinuationWorker(QThread)` + `Signal.emit()`（[continuation_worker.py:149,311](file:///workspace/novelforge/services/continuation_worker.py#L149)），QThread + Signal 是 Qt 标准跨线程通信方式，自动用 QueuedConnection，工作正常。

**注意**：流式累积逻辑本身是正确的（[context_extractor.py:868-919](file:///workspace/novelforge/services/context_extractor.py#L868-L919)）：`content_parts` 列表累积所有 chunk，`content = "".join(content_parts)` 拼接后传给 `_parse_extract_response(content)` 解析。问题仅在于 on_chunk 回调没有正确传递到 UI。

### 问题 2：模型选择逻辑错误

**两处相同 bug**：`extract()`（[context_extractor.py:558-560, 640-645](file:///workspace/novelforge/services/context_extractor.py#L558-L645)）和 `extract_streaming()`（[context_extractor.py:779-781, 859-864](file:///workspace/novelforge/services/context_extractor.py#L779-L864)）

```python
# Bug 1: or DEFAULT_EXTRACTOR_MODEL 把空字符串转成 "gpt-4o-mini"
extractor_model = config.get(
    "extractor_model", DEFAULT_EXTRACTOR_MODEL
) or DEFAULT_EXTRACTOR_MODEL

# Bug 2: 死代码，立即被覆盖
model = extractor_model or default_model

# Bug 3: != DEFAULT_EXTRACTOR_MODEL 启发式判断脆弱
if extractor_model and extractor_model != DEFAULT_EXTRACTOR_MODEL:
    model = extractor_model
else:
    model = default_model or DEFAULT_EXTRACTOR_MODEL
```

**分析**：
- 配置默认值 `extractor_model = ""`（[config.py:83](file:///workspace/novelforge/core/config.py#L83)）
- `config.get("extractor_model", DEFAULT_EXTRACTOR_MODEL)` → 返回 `""`（key 存在，值为空）
- `"" or DEFAULT_EXTRACTOR_MODEL` → 返回 `"gpt-4o-mini"`
- `extractor_model = "gpt-4o-mini"`，等于 `DEFAULT_EXTRACTOR_MODEL`
- 条件 `extractor_model != DEFAULT_EXTRACTOR_MODEL` → **False**
- 进入 else：`model = default_model`（端点的 `default_model`，如 `"openai/gpt-4o-mini"`）

**结果**：用户未设置 `extractor_model` 时用端点 `default_model`；用户设置为 `"gpt-4o-mini"` 时也被忽略。用户期望的"全局规定的模型"（`extractor_model`）未被尊重。

## 修改方案

### 修改 1：流式跨线程通信改用 Qt Signal

**文件**：`/workspace/novelforge/ui/main_window.py`

**1a. 添加 Slot 导入**（[line 25](file:///workspace/novelforge/ui/main_window.py#L25)）

```python
# 修改前
from PySide6.QtCore import QSettings, Qt, QTimer, Signal

# 修改后
from PySide6.QtCore import QSettings, Qt, QTimer, Signal, Slot
```

**1b. 在 MainWindow 类中添加 Signal 定义**（[line 165-166](file:///workspace/novelforge/ui/main_window.py#L165-L166)）

```python
# 信号
status_message = Signal(str)
_extract_chunk_received = Signal(str)   # 流式提取 chunk（跨线程）
_extract_done = Signal(object)          # 流式提取完成（跨线程）
```

**1c. 在 `__init__` 中连接信号到槽**

在 `__init__` 方法合适位置（服务初始化之后）添加：
```python
# 连接流式提取信号（跨线程安全传递）
self._extract_chunk_received.connect(self._on_extract_chunk_received)
self._extract_done.connect(self._on_extract_done)
```

**1d. 添加 chunk 槽函数**

新增方法（放在 `_on_extract_done` 之前）：
```python
@Slot(str)
def _on_extract_chunk_received(self, text: str) -> None:
    """流式提取 chunk 回调（UI 线程执行，由 Signal 触发）。"""
    context_panel = self.continuation_panel.context_preview_panel
    context_panel.update_extraction_progress(text)
```

**1e. 修改 `on_chunk` 回调**（[line 1690-1694](file:///workspace/novelforge/ui/main_window.py#L1690-L1694)）

```python
# 修改前
def on_chunk(text: str) -> None:
    QTimer.singleShot(
        0, lambda t=text: context_panel.update_extraction_progress(t)
    )

# 修改后
def on_chunk(text: str) -> None:
    self._extract_chunk_received.emit(text)
```

**1f. 修改 `on_done` 回调**（[line 1707-1721](file:///workspace/novelforge/ui/main_window.py#L1707-L1721)）

```python
# 修改前
def on_done(fut) -> None:
    try:
        result = fut.result()
        QTimer.singleShot(0, lambda r=result: self._on_extract_done(r))
    except Exception as e:
        logger.error("流式提取异常: %s", e, exc_info=True)
        from novelforge.services.context_extractor import ExtractResult
        err_result = ExtractResult(
            entries=[], status="failed", error=str(e)
        )
        QTimer.singleShot(
            0, lambda r=err_result: self._on_extract_done(r)
        )

# 修改后
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
```

**原理**：Qt Signal 的 `emit()` 是线程安全的，当发射信号的线程与接收对象所在线程不同时，Qt 自动使用 `Qt.QueuedConnection` 将调用排队到接收线程的事件循环中执行。MainWindow 生活在 UI 线程，所以信号会安全传递到 UI 线程。

### 修改 2：模型选择逻辑

**文件**：`/workspace/novelforge/services/context_extractor.py`

**2a. 修复 `extract()` 的模型读取和选择**（[line 558-560](file:///workspace/novelforge/services/context_extractor.py#L558-L560) 和 [line 639-645](file:///workspace/novelforge/services/context_extractor.py#L639-L645)）

```python
# 修改前（line 558-560）
extractor_model = config.get(
    "extractor_model", DEFAULT_EXTRACTOR_MODEL
) or DEFAULT_EXTRACTOR_MODEL

# 修改后
extractor_model = config.get("extractor_model", "") or ""
```

```python
# 修改前（line 639-645）
client, default_model = client_info
# 优先使用配置中的 extractor_model，否则用端点默认模型
model = extractor_model or default_model
# 优先使用端点配置的模型；若用户在提取配置中明确指定了 extractor_model 且非默认值，则使用 extractor_model
if extractor_model and extractor_model != DEFAULT_EXTRACTOR_MODEL:
    model = extractor_model
else:
    model = default_model or DEFAULT_EXTRACTOR_MODEL

# 修改后
client, default_model = client_info
# 模型选择优先级：用户配置的 extractor_model > 端点 default_model > DEFAULT_EXTRACTOR_MODEL
if extractor_model:
    model = extractor_model
else:
    model = default_model or DEFAULT_EXTRACTOR_MODEL
```

**2b. 修复 `extract_streaming()` 的模型读取和选择**（[line 779-781](file:///workspace/novelforge/services/context_extractor.py#L779-L781) 和 [line 858-864](file:///workspace/novelforge/services/context_extractor.py#L858-L864)）

同样修改：
```python
# 修改前（line 779-781）
extractor_model = config.get(
    "extractor_model", DEFAULT_EXTRACTOR_MODEL
) or DEFAULT_EXTRACTOR_MODEL

# 修改后
extractor_model = config.get("extractor_model", "") or ""
```

```python
# 修改前（line 858-864）
client, default_model = client_info
model = extractor_model or default_model
# 优先使用端点配置的模型；若用户在提取配置中明确指定了 extractor_model 且非默认值，则使用 extractor_model
if extractor_model and extractor_model != DEFAULT_EXTRACTOR_MODEL:
    model = extractor_model
else:
    model = default_model or DEFAULT_EXTRACTOR_MODEL

# 修改后
client, default_model = client_info
# 模型选择优先级：用户配置的 extractor_model > 端点 default_model > DEFAULT_EXTRACTOR_MODEL
if extractor_model:
    model = extractor_model
else:
    model = default_model or DEFAULT_EXTRACTOR_MODEL
```

**修复后优先级**：
1. 用户在设置中配置了 `extractor_model`（非空）→ 用 `extractor_model`
2. 否则用端点的 `default_model`
3. 端点也没配置 → 用 `DEFAULT_EXTRACTOR_MODEL`（`"gpt-4o-mini"`）

这样无论用户的"全局规定的模型"是指 `extractor_model` 还是端点 `default_model`，都能正确工作。

## 假设与决策

1. **不修改 `extract_streaming()` 的累积逻辑**：`content_parts` 列表累积和 `"".join()` 拼接是正确的，问题仅在于 on_chunk 回调未传递到 UI。
2. **不修改 `context_preview_panel.py`**：`update_extraction_progress()` 和 `finish_extraction()` 逻辑正确，只是从未被调用。
3. **不修改 `DEFAULT_EXTRACTOR_MODEL`**：保留 `"gpt-4o-mini"` 作为最终兜底，但仅在用户和端点都未配置时使用。
4. **使用 Qt Signal 而非 QThread**：提取流程已用 `AsyncLoopRunner` 提交协程，改用 QThread 需大重构。Signal 的 `emit()` 线程安全，能正确跨线程传递，是最小改动方案。
5. **`_on_extract_done` 已存在**（[line 1723](file:///workspace/novelforge/ui/main_window.py#L1723)），直接复用，只需把 `QTimer.singleShot` 改为 `emit`。
6. **配置保存逻辑已正确**（[settings_dialog.py:602-607](file:///workspace/novelforge/ui/settings_dialog.py#L602-L607)），无需修改。

## 验证步骤

1. **运行测试套件**：`cd /workspace && python -m pytest tests/ -x -q`，确认 171 个测试无回归。
2. **模型选择验证**：编写脚本验证 5 种场景：
   - `extractor_model=""` + `default_model="deepseek-chat"` → 用 `deepseek-chat`
   - `extractor_model="claude-3-sonnet"` + `default_model="deepseek-chat"` → 用 `claude-3-sonnet`
   - `extractor_model="gpt-4o-mini"` + `default_model="deepseek-chat"` → 用 `gpt-4o-mini`（用户明确设置）
   - `extractor_model=""` + `default_model=""` → 用 `DEFAULT_EXTRACTOR_MODEL`
   - 配置默认值 `extractor_model=""`（空字符串）
3. **流式通信验证**：确认 `on_chunk` 和 `on_done` 使用 `emit()`，`_on_extract_chunk_received` 有 `@Slot` 装饰，信号在 `__init__` 中连接。
