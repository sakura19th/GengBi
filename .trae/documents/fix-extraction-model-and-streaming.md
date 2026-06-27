# 修复上下文提取流式接收与模型选择

## Summary

修复两个问题：
1. **模型选择错误**：上下文提取强制使用 `gpt-4o-mini`（全局配置默认值），忽略用户在 API 端点配置的 `default_model`。当用户的 API 提供商不支持 `gpt-4o-mini` 时，API 调用失败，导致流式查看区空白、无法接收内容、无法格式化。
2. **流式内容接收问题**：`on_chunk` 回调中的异常被 `except Exception: pass` 静默吞掉，跨线程 `QTimer.singleShot` 调用若出现异常，用户看不到任何错误，流式查看区不更新但内容仍能累积。

## Current State Analysis

### 问题 1: 模型选择逻辑错误

文件：`novelforge/services/context_extractor.py` 第 767-854 行

模型选择链路：
1. `DEFAULT_EXTRACTOR_MODEL = "gpt-4o-mini"`（第 51 行）
2. 全局配置默认值 `context_extract.extractor_model = "gpt-4o-mini"`（config.py 第 83 行）
3. `_get_extract_config()` 读取配置：`extractor_model = config.get("extractor_model", DEFAULT_EXTRACTOR_MODEL) or DEFAULT_EXTRACTOR_MODEL`（第 771 行）
4. `_get_llm_client()` 返回端点的 `default_model`（第 527 行）
5. 最终选择：`model = extractor_model or default_model`（第 854 行）

**根因**：由于全局配置中 `extractor_model` 默认为 `"gpt-4o-mini"`（非空字符串），`config.get("extractor_model", ...)` 永远返回 `"gpt-4o-mini"`，`extractor_model or default_model` 中 `extractor_model` 永远为真值，**端点的 `default_model` 被完全忽略**。

**影响**：用户使用 DeepSeek、Moonshot、本地模型等非 OpenAI 提供商时，端点不支持 `gpt-4o-mini`，API 返回错误，流式调用在第一个 chunk 之前就抛异常，流式查看区完全空白。

**设置对话框缺失**：`settings_dialog.py` 中没有 `extractor_model` 的配置 UI，用户无法通过 UI 修改提取模型。

### 问题 2: on_chunk 异常静默吞掉

文件：`novelforge/services/context_extractor.py` 第 869-873 行

```python
if on_chunk is not None:
    try:
        on_chunk(chunk.content)
    except Exception:
        pass  # ← 静默吞掉所有异常
```

`on_chunk` 在后台 asyncio 线程中被调用，内部用 `QTimer.singleShot(0, ...)` 转发到 UI 线程。如果跨线程调用出现异常（Qt 对象生命周期、事件循环问题），错误被完全隐藏。内容仍会正确累积到 `content_parts`（因为 append 在 on_chunk 之前），最终解析不受影响，但流式查看区不会实时更新。

## Proposed Changes

### Change 1: 修复模型选择逻辑，优先使用端点配置的模型

**文件**：`novelforge/services/context_extractor.py`

**修改 1a**：修改 `extract_streaming()` 中的模型选择（第 854 行）

**What**：将 `model = extractor_model or default_model` 改为优先使用端点配置的 `default_model`，仅当端点未配置模型时才回退到 `extractor_model`。

**Why**：用户在 API 端点配置的 `default_model` 是用户实际可用的模型，应优先使用。`extractor_model` 作为可选的覆盖项，仅在用户明确配置时生效。

**How**：
```python
# 优先使用端点配置的模型；若用户在提取配置中明确指定了 extractor_model 且非默认值，则使用 extractor_model
if extractor_model and extractor_model != DEFAULT_EXTRACTOR_MODEL:
    model = extractor_model
else:
    model = default_model or DEFAULT_EXTRACTOR_MODEL
```

**修改 1b**：同样修复 `extract()` 方法中的模型选择逻辑（非流式版本，第 530 行起，查找相同的 `model = extractor_model or default_model` 模式）

**修改 1c**：修改全局配置默认值，将 `extractor_model` 默认值改为空字符串

**文件**：`novelforge/core/config.py` 第 83 行

**What**：将 `"extractor_model": "gpt-4o-mini"` 改为 `"extractor_model": ""`

**Why**：空字符串表示"未配置"，让 `extractor_model or default_model` 能正确回退到端点模型。保留 `DEFAULT_EXTRACTOR_MODEL` 常量作为最终回退。

**How**：
```python
"context_extract": {
    "extractor_model": "",  # 空=使用端点默认模型
    ...
}
```

### Change 2: on_chunk 异常记录日志而非静默吞掉

**文件**：`novelforge/services/context_extractor.py` 第 869-873 行

**What**：将 `except Exception: pass` 改为记录 warning 日志。

**Why**：静默吞掉异常会导致调试困难，用户看不到任何错误信息。记录日志有助于诊断跨线程通信问题。

**How**：
```python
if on_chunk is not None:
    try:
        on_chunk(chunk.content)
    except Exception as e:
        logger.warning("on_chunk 回调异常: %s", e)
```

### Change 3: 在设置对话框中添加提取模型配置

**文件**：`novelforge/ui/settings_dialog.py`

**What**：在续写配置组（`cont_group`，第 385 行）之后新增"上下文提取"配置组，包含提取模型输入框。

**Why**：当前设置对话框没有 `extractor_model` 的配置 UI，用户无法通过 UI 修改提取模型。添加此配置项让用户可以按需指定提取模型（留空则使用端点默认模型）。

**How**：

1. 在 `_setup_ui()` 中，`cont_group` 之后（第 428 行 `layout.addWidget(cont_group)` 之后）新增上下文提取配置组：
```python
# 上下文提取配置组
extract_group = QGroupBox("上下文提取")
extract_form = QFormLayout(extract_group)

self._extractor_model_edit = QLineEdit()
self._extractor_model_edit.setPlaceholderText("留空=使用端点默认模型")
extract_settings = self._config_manager.get_context_extract_settings()
self._extractor_model_edit.setText(extract_settings.get("extractor_model", ""))
extract_form.addRow("提取模型:", self._extractor_model_edit)

layout.addWidget(extract_group)
```

2. 在 `_on_accept()` 方法中（第 580 行起），续写配置保存之后新增提取配置保存：
```python
# 保存上下文提取配置
extract_settings = self._config_manager.get_context_extract_settings()
extract_settings["extractor_model"] = self._extractor_model_edit.text().strip()
self._config_manager.config["context_extract"] = extract_settings
```

3. 需要确认 `QLineEdit` 已在导入列表中（搜索导入区，若无则添加）。

**注意**：设置对话框中已有 `QFormLayout`、`QGroupBox` 的使用模式，新增配置组遵循相同模式。

## Assumptions & Decisions

1. **模型优先级**：端点 `default_model` > 用户配置的 `extractor_model`（非空非默认值）> `DEFAULT_EXTRACTOR_MODEL`。这样既尊重用户在端点级的配置，又允许用户通过设置对话框覆盖。
2. **配置默认值改为空字符串**：`extractor_model` 默认为空表示"未配置"，语义清晰，让回退逻辑正常工作。已有配置文件中若值为 `"gpt-4o-mini"`，用户可通过设置对话框清空。
3. **保留 `DEFAULT_EXTRACTOR_MODEL` 常量**：作为最终回退（当端点也未配置模型时），保持向后兼容。
4. **on_chunk 异常处理**：改为记录 warning 日志而非抛出，避免影响流式接收主流程（内容累积在 on_chunk 之前已完成）。
5. **设置对话框**：添加提取模型输入框，placeholder 提示"留空=使用端点默认模型"，让用户理解空值的语义。

## Verification Steps

1. **模型选择测试**：配置一个非 OpenAI 端点（如 DeepSeek），不配置 extractor_model，验证提取使用端点的 default_model 而非 gpt-4o-mini
2. **extractor_model 覆盖测试**：在设置对话框中填写 extractor_model，验证提取使用该模型
3. **流式接收测试**：提取上下文时，验证流式查看区实时显示 LLM 原始输出
4. **API 失败测试**：使用不支持的模型名，验证错误信息正确显示（而非空白）
5. **运行现有测试套件**：`python -m pytest tests/ --ignore=tests/test_m5_polish.py -q` 确认无回归
