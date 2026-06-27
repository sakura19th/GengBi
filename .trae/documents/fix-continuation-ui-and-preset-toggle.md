# 续写面板 UI 优化与预设管理增强计划

## 摘要

本计划解决 6 个问题：(1) 新增用户输入内容区；(2) 续写面板使用全局 API 设置，不再手动填写；(3) 上下文提取预览独立分栏 + 优化提取提示词至 5000 token；(4) 续写输出栏独立分栏；(5) 修复右下角按钮文字显示不全；(6) 预设管理器中提示词与预设的启用/禁用开关。最后使用 `穿进赛博游戏后干掉BOSS成功上位.txt` 和 `TGbreak😺V3.1.1.json` 进行端到端测试，验证提示词组装与正则后处理能正确输出纯文本。

## 当前状态分析

### 问题 1：无用户输入内容区
- [continuation_panel.py](file:///workspace/novelforge/ui/continuation_panel.py) 中无用户输入文本框，`_output_edit` 为只读显示
- [prompt_assembler.py](file:///workspace/novelforge/core/prompt_assembler.py) 的 `assemble()` 方法无 `user_input` 参数，不支持注入用户自由指令

### 问题 2：API 设置重复填写
- [continuation_panel.py:123](file:///workspace/novelforge/ui/continuation_panel.py#L123) `_model_combo` 为 `setEditable(True)`，需手动输入
- [main_window.py:670](file:///workspace/novelforge/ui/main_window.py#L670) `_refresh_endpoints` 仅设置默认端点的单个模型
- `_endpoint_combo.currentIndexChanged` 未连接刷新模型的槽，切换端点后模型不更新

### 问题 3：上下文预览被压缩 + 提取提示词不足
- [continuation_panel.py:165-203](file:///workspace/novelforge/ui/continuation_panel.py#L165) 上下文预览/推理/输出在垂直 QSplitter 中，比例 30%/15%/55%
- [context_extractor.py:53](file:///workspace/novelforge/services/context_extractor.py#L53) `EXTRACT_MAX_TOKENS = 3000`，需提升至 5000
- [context_extractor.py:59](file:///workspace/novelforge/services/context_extractor.py#L59) `MAX_CONTENT_LENGTH = 200`，单条内容过短
- [extract_prompt.txt](file:///workspace/novelforge/resources/defaults/extract_prompt.txt) 提取提示词较简单，仅 5 个类别

### 问题 4：输出栏被压缩
- 输出区与上下文预览、推理区共用同一垂直 QSplitter，空间不足

### 问题 5：按钮文字显示不全
- [continuation_panel.py:206-243](file:///workspace/novelforge/ui/continuation_panel.py#L206) QGridLayout 2 行，第一行 3 按钮、第二行 4 按钮，列数不匹配
- 按钮无 `setMinimumWidth`，窄窗口下中文文字被截断

### 问题 6：提示词/预设无法开关
- [preset_manager.py:60-69](file:///workspace/novelforge/ui/preset_manager.py#L60) `PromptListWidget` 无复选框
- [preset_service.py:788](file:///workspace/novelforge/services/preset_service.py#L788) `set_prompt_enabled` 已实现但从未被 UI 调用
- [preset.py:62-75](file:///workspace/novelforge/models/preset.py#L62) `WritingPreset` 无 `enabled` 字段

## 提议修改

### 修改 1：新增用户输入内容区

**文件**: `/workspace/novelforge/ui/continuation_panel.py`
- 在配置区下方新增 `QGroupBox("用户输入")`，内含 `QPlainTextEdit`（`_user_input_edit`），最小高度 60
- 新增 `get_user_input() -> str` 方法返回用户输入文本
- 新增 `clear_user_input()` 方法

**文件**: `/workspace/novelforge/core/prompt_assembler.py`
- `assemble()` 方法新增参数 `user_input: str = ""`
- 在 messages 组装完成后（阶段 4 之后），若 `user_input` 非空，追加一条 `{"role": "user", "content": user_input}` 到 messages 末尾
- 在 token 预算计算中扣减 user_input 的 token 数

**文件**: `/workspace/novelforge/ui/main_window.py`
- `_on_start_continuation` 中从 `continuation_panel.get_user_input()` 获取用户输入
- 传递给 `prompt_assembler.assemble(user_input=...)`

### 修改 2：API 设置使用全局配置

**文件**: `/workspace/novelforge/ui/continuation_panel.py`
- `_model_combo` 改为 `setEditable(False)`（不可手动输入）
- 连接 `_endpoint_combo.currentIndexChanged` 到新方法 `_on_endpoint_changed`
- `_on_endpoint_changed` 从选中端点的 `default_model` 自动设置模型
- 新增 `set_models_for_endpoint(endpoint: dict)` 方法，根据端点设置模型

**文件**: `/workspace/novelforge/ui/main_window.py`
- `_refresh_endpoints` 中，设置端点列表后，根据默认端点设置模型
- 端点切换时自动更新模型（通过 continuation_panel 的信号或直接调用）

### 修改 3：上下文预览独立分栏 + 优化提取提示词

**文件**: `/workspace/novelforge/ui/main_window.py` — `_setup_central_widget`
- 主 QSplitter 从 3 列改为 5 列：章节列表 | 编辑器 | 上下文预览 | 续写输出 | 续写控制
- 上下文预览面板从 `ContinuationPanel` 移出到独立 `CollapsiblePanel`
- 续写输出也从 `ContinuationPanel` 移出到独立 `CollapsiblePanel`

**文件**: `/workspace/novelforge/ui/continuation_panel.py`
- `_setup_ui` 中移除包含上下文预览/推理/输出的 QSplitter
- 保留推理区（可折叠）在控制面板内
- 暴露 `output_edit` 为属性（`context_preview_panel` 已是公开属性）
- 新增 `output_edit` property 返回 `_output_edit` widget

**文件**: `/workspace/novelforge/resources/defaults/extract_prompt.txt`
- 重写提取提示词，新增创作技巧指导：
  - 人物：性格弧光、动机冲突、情感节拍、关系网络演变
  - 地点：感官细节、氛围营造、功能象征
  - 事件：因果链、张力升级、转折点、时间线节点
  - 风格：叙事视角、节奏控制、修辞手法、对话风格
  - 剧情状态：悬念伏笔、目标冲突、未解线索、主题意象
  - 新增类别：relationships（关系动态）、atmosphere（氛围感官）、foreshadowing（伏笔回收）
- 强调提取尽可能多的信息，每类多条

**文件**: `/workspace/novelforge/services/context_extractor.py`
- `EXTRACT_MAX_TOKENS` 从 3000 改为 5000
- `MAX_CONTENT_LENGTH` 从 200 改为 500
- 更新提取提示词中的类别说明

### 修改 4：续写输出栏独立分栏

（与修改 3 合并实现，输出栏移至独立 `CollapsiblePanel`）

**文件**: `/workspace/novelforge/ui/main_window.py`
- 新建 `output_panel = CollapsiblePanel("续写输出")`
- 将 `continuation_panel.output_edit` 添加到 `output_panel`
- 设置 splitter 初始比例和伸缩因子

### 修改 5：修复按钮文字显示

**文件**: `/workspace/novelforge/ui/continuation_panel.py`
- 按钮区改为统一 4 列 QGridLayout（2 行 × 4 列 = 8 格，7 个按钮）
- 第一行：开始续写 | 停止 | 重写 | （空）
- 第二行：接受并追加 | 接受并继续 | 编辑后接受 | 并排对比
- 每个按钮设置 `setMinimumWidth(100)` 确保文字完整显示
- 使用 `QSizePolicy.Policy.MinimumExpanding` 替代 `Expanding`

### 修改 6：提示词与预设启用/禁用开关

**文件**: `/workspace/novelforge/models/preset.py`
- `WritingPreset` 新增 `enabled: bool = True` 字段

**文件**: `/workspace/novelforge/ui/preset_manager.py`
- `PromptListWidget` 的每个 item 添加复选框：`item.setCheckState(Qt.CheckState.Checked/Unchecked)`
- `_refresh_prompt_list` 中根据 `entry.enabled` 设置复选框状态
- 连接 `itemChanged` 信号到新方法 `_on_prompt_check_changed`，调用 `preset_service.set_prompt_enabled`
- 预设列表中每个预设添加启用/禁用标记，新增「启用/禁用」按钮
- 预设 combo 中仅显示启用的预设

**文件**: `/workspace/novelforge/services/preset_service.py`
- 新增 `set_preset_enabled(preset_id: str, enabled: bool)` 方法
- `list_presets` 可选参数 `include_disabled: bool = True`

**文件**: `/workspace/novelforge/ui/main_window.py`
- `_refresh_presets` 中仅加载启用的预设到 continuation_panel

### 修改 7：端到端测试

**测试脚本**: 验证以下流程：
1. 导入小说 `穿进赛博游戏后干掉BOSS成功上位.txt` 为项目
2. 导入预设 `TGbreak😺V3.1.1.json`
3. 导入正则脚本到 preset 作用域
4. 组装提示词，验证 ST 宏（setvar/getvar/user/char）正确替换
5. 模拟 AI 输出（含 `<draft_notes>`、`<w2g>` 等 XML 标签）
6. 应用 AI_OUTPUT 正则脚本
7. 验证正则后处理结果（思维链隐藏、标签清理等）
8. 若输出含 HTML 标签，添加 HTML 剥离步骤产出纯文本

**HTML 剥离**（如需要）:
**文件**: `/workspace/novelforge/core/regex_engine.py`
- 新增 `strip_html_tags(text: str) -> str` 函数，移除所有 HTML/XML 标签
- 在 `continuation_worker.py` 的正则后处理之后，对最终输出调用此函数（可选，通过配置控制）

## 假设与决策

1. **5 列布局**：主 splitter 改为 5 列（章节 | 编辑器 | 上下文预览 | 输出 | 控制），每列可折叠。最小窗口宽度从 1024 增至 1280。
2. **用户输入注入点**：作为最后一条 user 消息追加到 messages 末尾，在当前章节消息之后。
3. **模型自动填充**：端点切换时自动设置模型为端点的 `default_model`，模型 combo 不可编辑。
4. **预设 enabled 字段**：新增 `enabled: bool = True` 到 `WritingPreset`，默认预设始终启用。禁用的预设不出现在续写面板的预设选择中。
5. **提取提示词优化**：参考创意写作技巧（人物弧光、感官细节、张力升级、伏笔回收等），新增 3 个提取类别，单条内容上限提升至 500 字。
6. **HTML 剥离**：TGbreak 正则部分脚本生成 HTML（用于 ST 渲染），NovelForge 使用 QPlainTextEdit 显示纯文本。在正则后处理之后添加 HTML 标签剥离，仅保留文本内容。
7. **按钮布局**：统一 4 列网格，每按钮 `setMinimumWidth(100)`。

## 验证步骤

1. **运行测试套件**：
   ```bash
   cd /workspace && python -m pytest tests/ test_m3.py -x -q 2>&1 | tail -30
   ```

2. **UI 冒烟测试**：验证主窗口 5 列布局正常显示，按钮文字完整，上下文预览和输出栏有足够空间。

3. **端到端测试脚本**：
   ```python
   # 导入小说 + 预设 + 正则
   # 组装提示词（验证 ST 宏替换）
   # 模拟 AI 输出 + 应用正则
   # 验证纯文本输出
   ```

4. **预设开关测试**：在预设管理器中切换提示词启用/禁用，验证组装结果正确排除禁用提示。

5. **API 设置测试**：验证端点切换时模型自动更新，模型 combo 不可编辑。
