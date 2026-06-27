# 窄屏按钮溢出优化方案

## Summary

修复上下文提取预览面板的"提取上下文"操作行、以及续写控制面板的 8 个操作按钮在面板宽度不足时文字被截断/溢出无法完全显示的问题。核心思路:把固定列数的横向布局改为可按宽度自动换行的流式布局(`QFlowLayout`),让按钮在窄屏下自动折行而非挤压。

## Current State Analysis

### 问题 1:续写控制面板按钮区(`continuation_panel.py:208-251`)
- 按钮区用固定 `QGridLayout` 4 列 × 2 行,共 8 个按钮。
- 每个按钮 `setMinimumWidth(110)`(第 238 行),4 列至少需 440px + 间距。
- 但主窗口 splitter 给该面板设的 `setMinimumWidth(220)`(`main_window.py:319`),远小于 440px。
- 当用户拖窄该分栏(或窗口本身较窄)时,4 列无法容纳,按钮文字被截断、按钮重叠或溢出面板边界。
- `QGridLayout` 不支持自动换行,列数固定为 4。

### 问题 2:上下文提取预览操作行(`context_preview_panel.py:350-377`)
- "提取上下文"按钮 + "前文:" QLabel + lookback QComboBox + "Token:" QLabel + token_limit QComboBox,5 个控件横排在一个 `QHBoxLayout` 里。
- 该面板 `setMinimumWidth(200)`(`main_window.py:314`)。
- `QHBoxLayout` 不换行,窄屏时 5 个控件被强行压缩,ComboBox 文字截断、按钮文字溢出。

### 根因
`QHBoxLayout`/`QGridLayout` 都是固定结构,不会根据可用宽度自动折行。Qt 没有内置的"流式布局"(类似 CSS flex-wrap),需要自定义 `QFlowLayout` 实现按宽度自动换行。

## Proposed Changes

### 改动 1:新增 `novelforge/ui/flow_layout.py`
**What**: 实现一个 `QFlowLayout` 类,控件按添加顺序从左到右排列,超出可用宽度时自动换到下一行。
**Why**: Qt 没有内置流式布局,这是解决按钮自动换行的标准做法(Qt 官方示例 flowlayout)。
**How**:
- 继承 `QLayout`,实现 `addItem`、`count`、`itemAt`、`takeAt`、`sizeHint`、`doLayout`。
- `doLayout` 核心逻辑:遍历 item,若当前行剩余宽度不足以放下该 item 的 width,则换行;每行垂直方向取最大 height 对齐。
- 支持通过 `setSpacing` / `setHorizontalSpacing` / `setVerticalSpacing` 控制间距。
- `heightForWidth` 返回 True,使布局在宽度受限时正确计算所需高度。
- 无外部依赖,纯 PySide6。

### 改动 2:续写控制面板按钮区改用 `QFlowLayout`(`continuation_panel.py:208-251`)
**What**: 把按钮区从 `QGridLayout`(4 列固定)改为 `QFlowLayout`(自动换行)。
**Why**: 让 8 个按钮在面板宽时排成 2 行 × 4 列,窄时自动折成 3 列、2 列甚至 1 列,文字始终完整。
**How**:
- 导入 `QFlowLayout`。
- 第 209 行 `btn_layout = QGridLayout()` 改为 `btn_layout = QFlowLayout()`,设 `setSpacing(4)`。
- 移除 `expanding_policy`/`setMinimumWidth(110)` 那段(229-238 行):改为给每个按钮设较小的 `setMinimumWidth`(如 80px,仅保证"停止""重写"等短文字不挤压),去掉 `MinimumExpanding` 策略改为默认 `Preferred`,让流式布局自然排列。
- 按添加顺序 `addWidget`(保持现有顺序:开始续写、查看提示词、停止、重写、接受并追加、接受并继续、编辑后接受、并排对比),宽屏时自然排成接近 4 列,窄屏自动换行。
- 移除 `QGridLayout`/`QSizePolicy` 中不再使用的导入(若其他地方未用)。

### 改动 3:上下文提取预览操作行改用 `QFlowLayout`(`context_preview_panel.py:350-377`)
**What**: 把"提取上下文"操作行从 `QHBoxLayout` 改为 `QFlowLayout`。
**Why**: 让"提取上下文"按钮 + 两个 ComboBox 在窄屏下自动换行(如按钮独占一行,两个下拉框折到下一行),而非全部挤压在一行。
**How**:
- 导入 `QFlowLayout`。
- 第 351 行 `extract_row = QHBoxLayout()` 改为 `extract_row = QFlowLayout()`,`setSpacing(4)`。
- 控件添加顺序保持不变(按钮 → 前文标签 → lookback 下拉 → Token 标签 → token_limit 下拉)。
- 给两个 QComboBox 设 `setMinimumWidth`(如 100px),防止下拉框被压到只剩箭头。
- 窄屏时:可能第一行放"提取上下文"按钮 + "前文:"标签 + lookback 下拉,第二行放"Token:"标签 + token_limit 下拉,自动适应。

### 改动 4:顺带处理"操作按钮"行(`context_preview_panel.py:379-396`)
**What**: 把"添加条目/清空/查看提示词"三按钮行从 `QHBoxLayout` 改为 `QFlowLayout`。
**Why**: 保持一致性;虽然只有 3 个按钮,窄屏下也可能需要换行。当前有 `addStretch()` 做左对齐,流式布局默认从左排列,移除 stretch 即可。
**How**:
- 第 380 行 `btn_layout = QHBoxLayout()` 改为 `btn_layout = QFlowLayout()`。
- 移除第 394 行的 `btn_layout.addStretch()`(流式布局不需要 stretch 做左对齐)。

## Assumptions & Decisions

1. **采用自定义 QFlowLayout 而非 QGridLayout 减列**:QGridLayout 改列数需在 resize 事件中动态重建,代码复杂且闪烁;QFlowLayout 是 Qt 官方推荐方案,一次实现处处复用。
2. **不调整面板最小宽度**:面板 `setMinimumWidth(200/220)` 是合理的下限,问题在布局不自适应而非最小值太小。提高最小宽度会限制用户拖窄自由度,非最优解。
3. **按钮最小宽度降为 80px**:原 110px 是为 4 列网格预留,流式布局下无需这么宽;80px 足以保证中文 2-4 字按钮文字完整,窄屏能放下更多按钮。
4. **不引入第三方布局库**:纯 PySide6 实现,无新依赖。
5. **QFlowLayout 实现 Qt 官方 flowlayout 示例的成熟模式**:已验证可在 PySide6 稳定运行。

## Verification Steps

1. **窄屏换行验证**:启动应用,将"续写控制"分栏拖窄至 220px(最小宽度),确认 8 个按钮自动折成多行,文字完整可见无截断。
2. **宽屏布局验证**:将分栏拖宽至 600px+,确认按钮回到接近 4 列的紧凑排列,不浪费空间。
3. **提取操作行验证**:将"上下文提取预览"分栏拖窄至 200px,确认"提取上下文"按钮与两个下拉框自动换行,下拉框文字不截断。
4. **中间宽度过渡验证**:在 300-400px 中间宽度反复拖动,确认换行/折回过程平滑无闪烁、无按钮重叠。
5. **回归验证**:运行 `python -m pytest tests/ -x -q`,确认无回归(布局改动不影响业务逻辑)。
6. **导入验证**:`python -c "import novelforge.ui.flow_layout, novelforge.ui.continuation_panel, novelforge.ui.context_preview_panel"` 确认无语法错误。
