# 代码审查修复计划

## 摘要

对前序会话修改的代码进行审查，发现 2 个严重问题、3 个中等问题需修复。核心问题集中在 `strip_html_tags` 函数：步骤 C 替换模式缺少 lookbehind 导致误删正常文本，且该函数被无条件应用于所有续写输出（包括非 HTML 预设的纯文本输出），存在静默数据损坏风险。

## 当前状态分析

### 严重问题

#### 问题 1：`strip_html_tags` 步骤 C 替换模式缺少 lookbehind

**文件**：`/workspace/novelforge/core/regex_engine.py` 第 536-546 行

**根因**：搜索模式使用 `(?<![/a-zA-Z0-9_])` lookbehind，但替换模式 `rf"/?{re.escape(tag)}(?![a-zA-Z0-9_])"` 缺少此断言。导致替换比搜索更激进。

**实例验证**：文本 `"into the path/to/file go to school"`
- `slash_tags` = `{"to", "file"}`（来自 `/to` 和 `/file`）
- 搜索 `"to"`：`"into"` 中的 `"to"` 前面是 `"n"`（word char），lookbehind 阻止匹配 → 但 `"go to"` 中的 `"to"` 匹配 → 搜索成功
- 替换 `/?to(?![a-zA-Z0-9_])`：无 lookbehind，匹配 `"into"` 中的 `"to"` → `"into"` 被破坏为 `"in"`
- 结果：`"in the path/file go  school"` — 正常文本被损坏

#### 问题 2：`strip_html_tags` 被无条件应用于所有续写输出

**文件**：`/workspace/novelforge/services/continuation_worker.py` 第 377-383 行

**根因**：无论预设是否产生 HTML，`strip_html_tags` 都在所有非空 `final_content` 上执行。其中第 515 行 `re.sub(r"<[^>]+>", "", result)` 会删除 `<` 和 `>` 之间的所有内容，包括正常文本如 `x < 5 and y > 3`（被破坏为 `x  3`）。

### 中等问题

#### 问题 3：`user_input` 未纳入 token 预算计算

**文件**：`/workspace/novelforge/core/prompt_assembler.py` 第 450、543-545 行

**根因**：token 预算 `budget = max_context - max_tokens - system_tokens - injection_tokens`（第 450 行）未包含 `user_input` 的 token。`user_input` 在历史裁剪完成后才追加（第 544-545 行），较长的用户输入可能导致总 token 超出 `max_context`。

#### 问题 4：`_on_prompts_reordered` 不发射 `preset_changed` 信号

**文件**：`/workspace/novelforge/ui/preset_manager.py` 第 620-635 行

**根因**：拖拽排序后保存了新顺序但不发射信号，与 `_on_prompt_check_changed`（第 811 行）和 `_on_toggle_preset_enabled`（第 826 行）行为不一致。MainWindow 若依赖该信号刷新续写面板，排序变更不会生效。

#### 问题 5：测试静默吞没所有异常

**文件**：`/workspace/tests/test_tgbreak_e2e.py` 第 88-96 行

**根因**：`except Exception: pass` 完全静默吞没正则脚本解析异常，可能掩盖真实 bug。

## 拟议修改

### 修改 1：修复步骤 C 替换模式 lookbehind（严重）

**文件**：`/workspace/novelforge/core/regex_engine.py` 第 544-546 行

将替换模式从：
```python
result = re.sub(
    rf"/?{re.escape(tag)}(?![a-zA-Z0-9_])", "", result
)
```
改为：
```python
result = re.sub(
    rf"(?<![/a-zA-Z0-9_])/?{re.escape(tag)}(?![a-zA-Z0-9_])", "", result
)
```

加入与搜索模式一致的 `(?<![/a-zA-Z0-9_])` lookbehind，确保替换范围与搜索范围一致，不误伤更长单词的子串。

### 修改 2：`strip_html_tags` 条件执行 + 标签模式精确化（严重）

**文件 A**：`/workspace/novelforge/services/continuation_worker.py` 第 377-383 行

将无条件调用改为检测 HTML 特征后再调用：
```python
# 剥离 HTML 标签，输出纯文本
# 仅当内容含 HTML 标签特征时才执行，避免破坏纯文本输出
if final_content and _contains_html(final_content):
    try:
        from novelforge.core.regex_engine import strip_html_tags
        final_content = strip_html_tags(final_content)
        logger.debug("已剥离 HTML 标签")
    except Exception as e:
        logger.warning("HTML 标签剥离失败: %s", e)
```

新增辅助函数 `_contains_html`（在 continuation_worker.py 模块级）：
```python
import re as _re

_HTML_PATTERN = _re.compile(r"<[a-zA-Z/!]")

def _contains_html(text: str) -> bool:
    """检测文本是否含 HTML 标签特征。

    匹配 < 后跟字母、/ 或 !（HTML 标签开始），不匹配 < 后跟空格/数字
    （如数学表达式 x < 5）。
    """
    return bool(_HTML_PATTERN.search(text))
```

**文件 B**：`/workspace/novelforge/core/regex_engine.py` 第 515 行

将通用标签移除模式从 `<[^>]+>` 精确化为 `</?[a-zA-Z][^>]*>`，要求 `<` 后跟可选 `/` 和字母（真正的 HTML 标签），不匹配 `< 5` 等数学表达式：
```python
# 移除所有剩余 HTML/XML 标签（要求 < 后跟字母或 /，避免误伤数学表达式）
result = re.sub(r"</?[a-zA-Z][^>]*>", "", result)
```

### 修改 3：`user_input` 纳入 token 预算（中等）

**文件**：`/workspace/novelforge/core/prompt_assembler.py` 第 446-450 行

在预算计算前计算 `user_input` 的 token 占用并扣除：
```python
# 计算 user_input 的 token 占用
user_input_tokens = 0
if user_input:
    user_input_tokens = self.token_counter.count_messages(
        [{"role": "user", "content": user_input}], model
    )

budget = (
    max_context - max_tokens - system_tokens
    - injection_tokens - user_input_tokens
)
```

同步更新第 461 行的 `reduced_max_tokens` 计算，也扣除 `user_input_tokens`：
```python
reduced_max_tokens = (
    max_context - system_tokens - injection_tokens
    - user_input_tokens - min_context_budget
)
```

### 修改 4：`_on_prompts_reordered` 发射信号（中等）

**文件**：`/workspace/novelforge/ui/preset_manager.py` 第 635 行后

在 `save_preset` 后追加信号发射：
```python
self.preset_service.reorder_prompts(self._current_preset, new_order)
self.preset_service.save_preset(self._current_preset)
self.preset_changed.emit(self._current_preset.id)
```

### 修改 5：测试异常日志记录（中等）

**文件**：`/workspace/tests/test_tgbreak_e2e.py` 第 88-96 行

将 `except Exception: pass` 改为记录日志：
```python
import logging
logger = logging.getLogger(__name__)

# ...
for script_data in regex_scripts_data:
    try:
        script = _parse_regex_script_from_st(script_data)
        if not script.id:
            script.id = _generate_regex_id()
        regex_service.add_script(script, scope="preset", preset_id=preset.id)
        scripts.append(script)
    except Exception as e:
        logger.warning("导入正则脚本失败: %s", e)
```

## 假设与决策

1. **不修改 `strip_html_tags` 的整体结构**：保留步骤 A/B/C 的清理逻辑，仅修复 lookbehind 缺失和标签模式精确化。
2. **HTML 检测使用简单模式**：`<[a-zA-Z/!]` 检测足够覆盖真实 HTML 标签（`<div>`、`</p>`、`<!--`），不覆盖数学表达式（`< 5`）。对于 TGbreak 的半残标签（`cliche>` 无 `<`），由 `_contains_html` 检测到 `>` 不触发（因无 `<`），但此时 `strip_html_tags` 不会被调用——这是可接受的，因为半残标签仅在 TGbreak 预设的 trimStrings 作用下产生，而 trimStrings 同时会剥离 `<`，使得 `_contains_html` 检测不到 `<`。

   **修正决策**：为覆盖 TGbreak 半残标签场景，`_contains_html` 也检测 `>` 后跟 ASCII 标签名的模式。更新检测函数：
   ```python
   _HTML_PATTERN = _re.compile(r"<[a-zA-Z/!]|/?[a-zA-Z_]\w*>")
   ```
   这样 `cliche>` 和 `ai_last_output>` 也会触发 `strip_html_tags`。

3. **`user_input` token 计算使用 `count_messages`**：与系统消息的计算方式一致，确保准确性。
4. **不修复实体反转义顺序问题**（审查中的中等问题 3）：该问题在小说续写场景中极少触发（AI 输出很少包含 `&lt;` 等实体），优先级低，不在本次修复范围。
5. **不修复默认预设 ID 硬编码问题**（审查中的轻微问题 10）：影响小，不在本次范围。

## 验证步骤

1. 运行 TGbreak E2E 测试：`python -m pytest tests/test_tgbreak_e2e.py -v` — 预期 14/14 通过
2. 运行正则引擎相关测试：`python -m pytest tests/ -q -k "regex or strip or html"` — 预期全通过
3. 运行全量测试套件（排除环境缺失的 UI 测试）：`python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"` — 预期无回归
4. 手动验证步骤 C 修复：用诊断脚本确认 `"into"` 不被破坏为 `"in"`
5. 手动验证条件执行：确认纯文本输出（无 HTML）不被 `strip_html_tags` 处理
