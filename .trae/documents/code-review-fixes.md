# 代码审查修复（高+中+部分低严重度问题）

## 问题概述

对最近修改的代码（plot_role 容错、错误反馈重试、before_audit 检查点、配置持久化）进行全面审查后，发现 1 高 + 3 中 + 3 低共 7 个问题需修复。本计划聚焦实际漏洞和易改的优化项，不涉及大规模重构。

## 修复清单（7 项）

### 修复 1（高）：章节数不足不触发反馈重试 — _run_volume_outline

**文件**: `novelforge/services/volume_orchestrator.py`（L851-857）

**问题**: LLM 返回章节数不足时 `return None` 直接返回，不进入 except 分支，不触发反馈重试。导致 LLM 偶发少输出一章就终止整个卷续写流程（`error.emit("卷大纲生成失败，流程终止")`）。

**修复**: 将 `return None` 改为 `raise ValueError(...)`，让其进入 except 分支触发反馈重试：
```python
elif len(outline.chapters) < chapter_count:
    raise ValueError(
        f"章节数不足: 期望 {chapter_count}, 实际 {len(outline.chapters)}"
    )
```
同时删除原 `logger.error(...)`（except 分支会 logger.warning）。

### 修复 2（中）：章节数不足不触发反馈重试 — _run_outline_final

**文件**: `novelforge/services/volume_orchestrator.py`（L1020-1026）

**问题**: 同修复 1，`_run_outline_final` 章节数不足时 `return None` 不触发反馈重试。虽有降级保底（保持审计后大纲），但丧失了让 LLM 修正的机会。

**修复**: 同修复 1，改为 `raise ValueError(...)`：
```python
elif len(outline.chapters) < chapter_count:
    raise ValueError(
        f"章节数不足: 期望 {chapter_count}, 实际 {len(outline.chapters)}"
    )
```

### 修复 3（中）：重试时 messages 缺少 LLM 上次 assistant 输出

**文件**: `novelforge/services/volume_orchestrator.py`（3 处 except 分支）

**问题**: 重试时 messages 为 `[system, user_feedback]`，缺少 LLM 上次的 assistant 输出。LLM 只看到错误描述，看不到自己具体输出了什么错误内容，修正针对性较弱。且无 assistant 却有 user 追问的对话顺序可能让部分模型困惑。

**修复**: 在 3 个 except 分支中，`messages.append(user_feedback)` 之前先 `messages.append({"role": "assistant", "content": content})`。注意 `content` 变量在 try 块中已赋值（`content = response["choices"][0]["message"]["content"]`），except 分支可访问。

**3.1 `_run_volume_outline` except 分支**（L864-876）：
```python
except Exception as e:
    logger.warning("VolumeOutline 失败 (attempt %d): %s", attempt + 1, e)
    # 补充 LLM 上次输出到上下文，让它在下次重试时针对性修正
    messages.append({"role": "assistant", "content": content})
    # 反馈错误信息给 LLM
    messages.append({
        "role": "user",
        "content": (
            f"上次输出校验失败：{e}。请修正上述问题，严格按 JSON Schema "
            "重新输出完整的 VolumeOutline JSON 对象。特别注意：plot_role "
            "必须为 起/承/转/合/高潮/过渡 中的单一值，严禁组合拼接"
            "（如\"承转\"无效，应输出\"承\"或\"转\"）。"
        ),
    })
    continue
```

**3.2 `_run_outline_audit` except 分支**（L945-957）：同样在 user 消息前补充 `messages.append({"role": "assistant", "content": content})`。

**3.3 `_run_outline_final` except 分支**（L1033-1044）：同样在 user 消息前补充 `messages.append({"role": "assistant", "content": content})`。

**注意**: 若异常发生在 `content = response[...]` 之前（如网络错误），`content` 变量未定义。需用 `content = locals().get("content", "")` 或在 try 块外预初始化 `content = ""`。采用预初始化更清晰：在 `for attempt` 循环内、try 之前加 `content = ""`。

### 修复 4（中）：except Exception 捕获 RateLimitError/APIError 不合理

**文件**: `novelforge/services/volume_orchestrator.py`（3 处 except 分支）

**问题**: `except Exception` 会捕获 `RateLimitError`（限流）和 `APIError`（服务端错误），立即重试可能加重服务端压力。网络/服务端错误应直接上抛由 `_async_run` 顶层处理。

**修复**: 在 `except AuthError` 之后、`except Exception` 之前，增加 `except (RateLimitError, APIError): raise`。需先 import 这两个异常类。

**import 修改**（文件顶部）：在现有 `from novelforge.services.llm_client import ...` 行补充 `RateLimitError, APIError`（搜索确认现有 import）。

**3 处 except 分支结构**（统一改为）：
```python
except AuthError:
    raise
except RateLimitError:
    raise
except APIError:
    raise
except asyncio.CancelledError:
    raise
except Exception as e:
    logger.warning("... 失败 (attempt %d): %s", attempt + 1, e)
    messages.append({"role": "assistant", "content": content})
    messages.append({"role": "user", "content": "..."})
    continue
```

### 修复 5（低）：plot_role 未 strip 导致含空格值误判

**文件**: `novelforge/models/volume.py`（L138-141）

**问题**: `if not v` 仅处理空字符串和 None。LLM 偶发返回带前后空白的值（如 `" 高潮 "`）会被误判为非法——`v in VALID_PLOT_ROLES` 为 False（含空格），后续匹配也失败，最终抛错。

**修复**: 在 `if not v` 之后增加 `v = v.strip()`，strip 后为空也返回空字符串：
```python
def validate_plot_role(cls, v):
    # 允许空字符串（默认值）
    if not v:
        return v
    # 去除前后空白，避免 LLM 偶发输出带空格的值被误判
    v = v.strip()
    if not v:
        return v
    # 精确匹配
    if v in VALID_PLOT_ROLES:
        return v
    # ...（后续容错归一化逻辑不变）
```

### 修复 6（低）：before_audit 对话框 QPlainTextEdit 无最小高度

**文件**: `novelforge/ui/checkpoint_dialog.py`（L186-190）

**问题**: `_setup_audit_focus_mode` 中 `QPlainTextEdit()` 未设置 `setMinimumHeight`。用户缩小对话框时编辑区会被压缩到很小，不便输入。

**修复**: 在 `self._audit_focus_edit = QPlainTextEdit()` 之后增加：
```python
self._audit_focus_edit.setMinimumHeight(120)
```

### 修复 7（低）：CheckpointDialog 内部标题被 main_window 覆盖（冗余）

**文件**: `novelforge/ui/checkpoint_dialog.py`（L174）

**问题**: `_setup_audit_focus_mode` 中 `self.setWindowTitle("审计前 - 输入需着重审计的部分")` 会被 `main_window._on_volume_checkpoint` 的 `dialog.setWindowTitle(title)`（L2161）覆盖为"审计前检查点"。内部标题是冗余的。

**修复**: 删除 `_setup_audit_focus_mode` 中的 `self.setWindowTitle(...)` 行（L174）。标题统一由 main_window 的 titles 字典管理。

## 不修改的部分

- **问题 B.2**（三处反馈消息文本重复）：保持显式，差异明确，不抽取辅助方法。
- **问题 B.3**（配置持久化无防抖）：变更频率低，当前实现可接受。
- **问题 B.4**（_token_value_to_text 硬编码）：5 项映射稳定，不抽取共享常量。
- **问题 C.1**（暂停点复选框标签过短无 tooltip）：当前标签在卷续写上下文中基本可理解，不增加 tooltip。
- **问题 4.2**（analysis_depth/pacing_speed 未匹配时静默失败）：VolumeRunConfig validator 已拦截非法值，持久化 JSON 被篡改的概率极低，不增加日志。

## 验证步骤

1. 运行编排器测试：
   ```
   python -m pytest tests/test_volume_orchestrator.py -q --tb=short
   ```
2. 运行模型测试：
   ```
   python -m pytest tests/test_volume_models.py -q --tb=short
   ```
3. 运行 UI 测试：
   ```
   python -m pytest tests/test_volume_ui.py -q --tb=short
   ```
4. 运行完整测试套件确认无回归：
   ```
   python -m pytest tests/ -q --tb=short
   ```
   预期：428+ passed, 13 skipped, 0 failed。

### 新增/更新测试

**文件**: `tests/test_volume_orchestrator.py`
- **新增** `test_volume_outline_chapter_count_short_triggers_retry`：
  - mock 第一次响应返回 chapters 数量不足（如 1 章，期望 2 章），第二次返回合法 2 章。
  - 断言重试发生（第二次调用时 messages 含 assistant + user 反馈）。
  - 断言最终返回有效 VolumeOutline。
- **更新** `test_volume_outline_error_feedback_retry`（已有）：
  - 断言第二次调用时 messages 含 assistant 消息（LLM 上次输出）。

**文件**: `tests/test_volume_models.py`
- **新增** `test_chapter_plan_plot_role_with_spaces_normalized`：
  - 验证 `ChapterPlan(plot_role=" 高潮 ")` 归一化为 `"高潮"`。
  - 验证 `ChapterPlan(plot_role="  承  ")` 归一化为 `"承"`。

## 假设与决策

- **决策**：修复 1/2 将章节数不足改为 raise ValueError 进入 except 分支——让反馈重试机制覆盖此情况，避免 LLM 单次失误终止流程。
- **决策**：修复 3 补充 assistant 消息——让 LLM 看到自己的错误输出，修正针对性更强。用 `content = ""` 预初始化避免变量未定义。
- **决策**：修复 4 将 RateLimitError/APIError 上抛——网络错误不应立即重试，由顶层处理。
- **决策**：修复 5 增加 `v.strip()`——防御 LLM 偶发输出带空格的值，符合容错兜底精神。
- **决策**：修复 6/7 是易改的 UI 小问题——最小高度提升可用性，删除冗余标题统一管理。
- **假设**：`content` 变量在 except 分支可访问（Python 作用域），但若异常发生在 `content = response[...]` 之前则未定义，需预初始化 `content = ""`。
- **假设**：`from novelforge.services.llm_client import ...` 现有 import 行可补充 `RateLimitError, APIError`（需搜索确认现有 import 内容）。
