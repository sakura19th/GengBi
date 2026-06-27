# 续写流程解耦与提示词预览功能

## 摘要

用户要求 7 项改动：
1. 上下文提取与续写解耦：提取预览区新增独立"提取"按钮，先提取确认后再续写
2. 提取支持流式 token 读取（不卡住 UI）
3. 修复续写输出看不到内容的问题
4. 交换续写输出与续写控制的位置（控制→输出，更符合逻辑）
5. 开始续写旁新增"查看提示词"按钮，展示组装后的完整提示词
6. 上下文提取预览新增"前文章节控制"（默认全部前文章节）+ "查看提示词"按钮
7. 创建 `agent.md` 项目约束文件，每次修改后即时更新

## 当前状态分析

### 提取与续写耦合（问题 1、2）

`_on_start_continuation`（main_window.py:1048-1105）中，提取通过 `AsyncLoopRunner.run()` 阻塞 UI 主线程（`future.result(timeout=120)`，async_runner.py:88）。提取使用非流式 `chat_completion`（context_extractor.py:654），UI 冻结直到提取完成。

提取流程：点击"开始续写" → `context_panel.start_extraction()` → `runner.run(extract(...))` 阻塞 → 组装提示词 → 启动 worker 流式续写。

### 续写输出不可见（问题 3）

前序会话将 `output_edit` 移至独立分栏（main_window.py:282-287）。可能原因：
- `DEFAULT_PANEL_SIZES = [220, 400, 300, 300, 260]`（main_window.py:85），5 栏总宽 1480px，输出栏仅 300px
- 交换位置后输出栏在右侧，stretch factor=1 可能被压缩
- 需确认 `output_edit` 是否正确显示流式内容（`append_chunk` → `_flush_buffer` → `cursor.insertText`，continuation_panel.py:393-423）

### 布局顺序（问题 4）

当前：`[目录] [编辑器] [上下文预览] [续写输出] [续写控制]`（main_window.py:294-298）
目标：`[目录] [编辑器] [上下文预览] [续写控制] [续写输出]` — 控制→输出，左到右工作流

### 提示词预览（问题 5、6）

`PromptAssembler.assemble()` 是纯本地操作（不调用 LLM），返回 `AssembleResult.messages`（prompt_assembler.py:65-87）。可直接调用以预览。
`ContextExtractor._build_prompt()`（context_extractor.py:351-406）构建提取提示词，也是纯本地操作。

### 前文章节控制（问题 6）

当前提取使用 `lookback_chapters`（默认 5，context_extractor.py:564-566），通过 `project.extract_config` 配置。用户要求 UI 控件选择前文章节范围，默认全部前文章节。

## 拟议修改

### 修改 1：上下文提取预览新增"提取"按钮 + 前文章节控制 + 查看提示词按钮

**文件**：`/workspace/novelforge/ui/context_preview_panel.py`

在 `_setup_ui` 的操作按钮区（第 330-343 行）新增：

1. **"提取上下文"按钮**（`_extract_btn`）：触发独立提取流程
2. **"前文章节"下拉框**（`_lookback_combo`）：选项 `["全部前文", "最近 3 章", "最近 5 章", "最近 10 章", "最近 20 章"]`，默认"全部前文"
3. **"查看提示词"按钮**（`_view_prompt_btn`）：展示提取提示词

新增信号：
- `extract_requested = Signal(dict)` — 发射提取请求，参数含 `lookback` 配置
- `view_extract_prompt_requested = Signal()` — 请求查看提取提示词

布局调整（第 330-343 行 btn_layout）：
```python
# 操作按钮区（两行）
# 第一行：提取 + 前文章节控制
extract_row = QHBoxLayout()
self._extract_btn = QPushButton("提取上下文")
self._extract_btn.setObjectName("primaryBtn")
self._extract_btn.clicked.connect(self._on_extract_clicked)
extract_row.addWidget(self._extract_btn)

extract_row.addWidget(QLabel("前文:"))
self._lookback_combo = QComboBox()
self._lookback_combo.addItems(["全部前文", "最近 3 章", "最近 5 章", "最近 10 章", "最近 20 章"])
self._lookback_combo.setCurrentText("全部前文")
extract_row.addWidget(self._lookback_combo)
layout.addLayout(extract_row)

# 第二行：添加条目 + 清空 + 查看提示词
btn_layout = QHBoxLayout()
self._add_btn = QPushButton("添加条目")
self._add_btn.clicked.connect(self._on_add_clicked)
btn_layout.addWidget(self._add_btn)
self._clear_btn = QPushButton("清空")
self._clear_btn.clicked.connect(self._on_clear_clicked)
btn_layout.addWidget(self._clear_btn)
self._view_prompt_btn = QPushButton("查看提示词")
self._view_prompt_btn.clicked.connect(self._on_view_prompt_clicked)
btn_layout.addWidget(self._view_prompt_btn)
btn_layout.addStretch()
layout.addLayout(btn_layout)
```

新增方法：
```python
def get_lookback_config(self) -> dict:
    """获取前文章节配置。"""
    text = self._lookback_combo.currentText()
    if text == "全部前文":
        return {"lookback": 0}  # 0 表示全部
    # 解析"最近 N 章"
    import re
    m = re.search(r"(\d+)", text)
    n = int(m.group(1)) if m else 5
    return {"lookback": n}

def _on_extract_clicked(self) -> None:
    """点击提取按钮。"""
    config = self.get_lookback_config()
    self.extract_requested.emit(config)

def _on_view_prompt_clicked(self) -> None:
    """点击查看提示词按钮。"""
    self.view_extract_prompt_requested.emit()
```

### 修改 2：提取改为非阻塞 + 流式

**文件 A**：`/workspace/novelforge/services/context_extractor.py`

新增 `extract_streaming` 方法，使用 `stream_chat_completion` 并通过回调推送进度：

```python
async def extract_streaming(
    self,
    project: Project | None,
    chapters: list[Chapter],
    current_chapter: Chapter | None,
    force_refresh: bool = False,
    lookback_override: int | None = None,
    on_chunk: Callable[[str], None] | None = None,
) -> ExtractResult:
    """流式提取上下文（不阻塞，通过 on_chunk 回调推送进度）。

    Args:
        lookback_override: 覆盖 lookback_chapters（0=全部前文，None=用配置默认值）
        on_chunk: 每个 chunk 的回调函数
    """
```

实现要点：
- 复用 `extract()` 的前半段（配置、章节选择、缓存检查、prompt 构建）
- 将 `client.chat_completion(...)` 替换为 `client.stream_chat_completion(...)`
- 逐 chunk 累积 `content_parts`，调用 `on_chunk(chunk.content)` 推送进度
- 流结束后拼接完整内容，解析 JSON（与现有 `_parse_extract_response` 一致）
- `lookback_override` 参数：0 表示全部前文，正整数表示最近 N 章

新增 `build_prompt_for_preview` 公开方法（供"查看提示词"按钮调用）：
```python
def build_prompt_for_preview(
    self,
    project: Project | None,
    chapters: list[Chapter],
    current_chapter: Chapter | None,
    lookback_override: int | None = None,
) -> str:
    """构建提取提示词（纯本地，不调用 LLM），供预览使用。"""
    config = self._get_extract_config(project)
    lookback = lookback_override if lookback_override is not None else int(
        config.get("lookback_chapters", DEFAULT_LOOKBACK_CHAPTERS)
    )
    if current_chapter is not None:
        target_chapters = self._get_lookback_chapters(
            chapters, current_chapter, lookback
        )
    else:
        sorted_chapters = sorted(chapters, key=lambda c: c.index)
        target_chapters = sorted_chapters[-lookback:] if lookback > 0 else sorted_chapters
    return self._build_prompt(project, target_chapters, config)
```

**文件 B**：`/workspace/novelforge/ui/main_window.py`

新增 `_on_extract_requested` 方法处理独立提取（非阻塞）：
```python
def _on_extract_requested(self, config: dict) -> None:
    """独立提取上下文（非阻塞，流式进度）。"""
    if not self._current_chapter:
        QMessageBox.warning(self, "提示", "请先选择章节")
        return
    # 验证端点/API Key（同续写验证）
    ...
    context_panel = self.continuation_panel.context_preview_panel
    context_panel.start_extraction()
    self._set_status_message("正在提取上下文（流式）...")

    project = ...
    lookback_override = config.get("lookback")  # 0=全部, N=最近N章

    # 非阻塞提交：用 run_coroutine_threadsafe + 回调
    from novelforge.services.async_runner import AsyncLoopRunner
    runner = AsyncLoopRunner.instance()
    loop = runner._loop

    def on_chunk(text: str) -> None:
        # 通过 QTimer.singleShot 转发到 UI 线程更新进度
        QTimer.singleShot(0, lambda: context_panel.update_extraction_progress(text))

    future = asyncio.run_coroutine_threadsafe(
        self.context_extractor.extract_streaming(
            project=project,
            chapters=self._current_chapters,
            current_chapter=self._current_chapter,
            lookback_override=lookback_override,
            on_chunk=on_chunk,
        ),
        loop,
    )

    def on_done(fut) -> None:
        try:
            result = fut.result()
            QTimer.singleShot(0, lambda: self._on_extract_done(result))
        except Exception as e:
            QTimer.singleShot(0, lambda: self._on_extract_done(
                ExtractResult(entries=[], status="failed", error=str(e))
            ))

    future.add_done_callback(on_done)
```

新增 `_on_extract_done` 方法处理提取完成回调（更新 UI，不触发续写）。

修改 `_on_start_continuation`：移除提取步骤（第 1048-1105 行），改为使用已有的 `self._current_context_entries`（若用户未提取则提示先提取，或自动用空 entries）。

**决策**：续写时若用户未提取，提示"请先点击提取上下文"，不自动提取。这实现"先提取确认后再续写"。

### 修改 3：修复续写输出不可见

**文件**：`/workspace/novelforge/ui/main_window.py`

排查并修复输出显示问题：
1. 确认 `output_edit` 的 `append_chunk` → `_flush_buffer` → `cursor.insertText` 链路正常
2. 调整 `DEFAULT_PANEL_SIZES` 增大输出栏宽度
3. 确认 `_flush_timer`（continuation_panel.py:246）正常启动
4. 确认 `strip_html_tags` 条件调用（continuation_worker.py:398）不会清空内容 — 若 `_contains_html` 返回 False 则不调用，内容原样保留

### 修改 4：交换续写输出与续写控制位置

**文件**：`/workspace/novelforge/ui/main_window.py`（第 281-298 行）

将 output_panel 和 right_panel 的添加顺序交换：
```python
# 第 4 栏：续写控制面板（原第 5 栏）
right_panel = CollapsiblePanel("续写控制")
right_panel.add_widget(self.continuation_panel)
right_panel.setMinimumWidth(240)

# 第 5 栏：续写输出（原第 4 栏）
output_panel = CollapsiblePanel("续写输出")
output_layout = QVBoxLayout(output_panel.content_layout.widget())
output_layout.setContentsMargins(0, 0, 0, 0)
output_layout.addWidget(self.continuation_panel.output_edit)
output_layout.addWidget(self.continuation_panel.auto_scroll_check)
output_panel.setMinimumWidth(300)

splitter.addWidget(left_panel)      # 0: 目录
splitter.addWidget(center_panel)    # 1: 编辑器
splitter.addWidget(context_panel)   # 2: 上下文预览
splitter.addWidget(right_panel)     # 3: 续写控制（新位置）
splitter.addWidget(output_panel)    # 4: 续写输出（新位置）
```

更新 `DEFAULT_PANEL_SIZES` 和 stretch factors：
```python
DEFAULT_PANEL_SIZES = [220, 400, 280, 260, 400]  # 输出栏加宽
# stretch: 目录(0)=固定, 编辑器(1)=伸缩, 预览(2)=伸缩, 控制(3)=固定, 输出(4)=伸缩
splitter.setStretchFactor(0, 0)
splitter.setStretchFactor(1, 1)
splitter.setStretchFactor(2, 1)
splitter.setStretchFactor(3, 0)
splitter.setStretchFactor(4, 1)
```

### 修改 5：续写控制新增"查看提示词"按钮

**文件 A**：`/workspace/novelforge/ui/continuation_panel.py`

在按钮区第一行（第 230-232 行）新增"查看提示词"按钮：
```python
# 第一行：开始续写 + 查看提示词 + 停止 + 重写
self._view_prompt_btn = QPushButton("查看提示词")
self._view_prompt_btn.clicked.connect(self._on_view_prompt_clicked)
btn_layout.addWidget(self._view_prompt_btn, 0, 1)  # 插入到开始续写和停止之间
```

新增信号和方法：
```python
view_prompt_requested = Signal()

def _on_view_prompt_clicked(self) -> None:
    self.view_prompt_requested.emit()
```

**文件 B**：`/workspace/novelforge/ui/main_window.py`

连接信号并实现预览对话框：
```python
self.continuation_panel.view_prompt_requested.connect(self._on_view_continuation_prompt)

def _on_view_continuation_prompt(self) -> None:
    """展示续写组装后的完整提示词。"""
    if not self._current_chapter:
        QMessageBox.warning(self, "提示", "请先选择章节")
        return
    # 组装提示词（纯本地，不调用 LLM）
    preset_id = self.continuation_panel.get_selected_preset_id()
    preset = self.preset_service.load_preset(preset_id) or self.preset_service.load_default_preset()
    entries = self._current_context_entries or []
    user_input = self.continuation_panel.get_user_input()
    ...
    assemble_result = self.prompt_assembler.assemble(...)
    # 展示对话框
    self._show_prompt_dialog("续写提示词预览", assemble_result.messages, assemble_result.token_usage)
```

新增 `_show_prompt_dialog` 通用方法（展示 messages 列表的只读对话框）：
```python
def _show_prompt_dialog(self, title: str, messages: list[dict], token_usage: dict = None) -> None:
    """展示提示词预览对话框。"""
    dialog = QDialog(self)
    dialog.setWindowTitle(title)
    dialog.resize(700, 600)
    layout = QVBoxLayout(dialog)
    # token 信息
    if token_usage:
        info = QLabel(f"Token: {token_usage.get('total_used', 0)}/{token_usage.get('max_context', '?')}")
        layout.addWidget(info)
    # messages 展示
    text_edit = QPlainTextEdit()
    text_edit.setReadOnly(True)
    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        text_edit.appendPlainText(f"--- [{i}] {role} ---")
        text_edit.appendPlainText(content)
        text_edit.appendPlainText("")
    layout.addWidget(text_edit)
    # 复制按钮
    copy_btn = QPushButton("复制全部")
    copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(text_edit.toPlainText()))
    layout.addWidget(copy_btn)
    dialog.exec()
```

### 修改 6：提取预览"查看提示词"按钮实现

**文件**：`/workspace/novelforge/ui/main_window.py`

连接信号并实现：
```python
context_panel.view_extract_prompt_requested.connect(self._on_view_extract_prompt)

def _on_view_extract_prompt(self) -> None:
    """展示提取提示词预览。"""
    if not self._current_chapter:
        QMessageBox.warning(self, "提示", "请先选择章节")
        return
    config = self.continuation_panel.context_preview_panel.get_lookback_config()
    lookback_override = config.get("lookback")
    project = ...
    prompt = self.context_extractor.build_prompt_for_preview(
        project=project,
        chapters=self._current_chapters,
        current_chapter=self._current_chapter,
        lookback_override=lookback_override,
    )
    # 展示对话框（单条 user 消息）
    self._show_prompt_dialog("提取提示词预览", [{"role": "user", "content": prompt}])
```

### 修改 7：创建 agent.md

**文件**：`/workspace/agent.md`

写入项目约束、架构规范、代码风格、测试要求、关键设计决策等内容。包括：
- 项目概述（NovelForge：SillyTavern 兼容的小说续写工具）
- 技术栈（Python 3.11+、PySide6、pydantic、aiohttp）
- 架构分层（models / core / services / ui）
- 关键设计决策（ST 兼容、QThread+asyncio 桥接、trimStrings 行为等）
- 代码风格规范
- 测试要求
- UI 布局规范（5 栏 QSplitter）
- 修改后必须更新本文件的提示

## 假设与决策

1. **续写时不再自动提取**：用户必须先点击"提取上下文"按钮，提取确认后再点"开始续写"。若未提取，续写时提示"请先提取上下文"。
2. **lookback=0 表示全部前文**：与现有 `lookback > 0` 时取最后 N 章的逻辑一致（context_extractor.py:596），0 时取全部。
3. **流式提取的 on_chunk 回调**：提取需要完整 JSON 才能解析，流式过程中仅显示进度（字符数增长），不展示部分 JSON。这解决"卡住"问题——UI 不冻结，用户看到进度。
4. **非阻塞提取用 run_coroutine_threadsafe + add_done_callback**：不阻塞 UI 线程，完成后通过 QTimer.singleShot 转发到 UI 线程。
5. **提示词预览对话框复用**：`_show_prompt_dialog` 通用方法同时服务续写提示词和提取提示词预览。
6. **输出不可见问题**：优先排查布局/尺寸问题，调整 DEFAULT_PANEL_SIZES 和 stretch factors。
7. **agent.md 更新机制**：在文件头部注明"每次修改项目后必须更新本文件"，作为约束提示。

## 验证步骤

1. 启动应用，确认 5 栏布局顺序：`[目录] [编辑器] [上下文预览] [续写控制] [续写输出]`
2. 在上下文预览区点击"提取上下文"，确认 UI 不冻结、进度实时更新、提取完成后条目显示
3. 点击"查看提示词"（提取），确认展示提取提示词内容
4. 调整"前文章节"下拉框，重新提取，确认章节数量变化
5. 在续写控制区点击"查看提示词"，确认展示组装后的完整 messages
6. 点击"开始续写"，确认输出区正确显示流式内容
7. 确认未提取时点击"开始续写"提示"请先提取上下文"
8. 运行测试套件：`python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"`
9. 确认 `agent.md` 文件存在且内容完整
