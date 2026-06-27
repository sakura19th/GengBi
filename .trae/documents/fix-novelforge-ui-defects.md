# NovelForge UI 缺陷修复计划

## 摘要

修复用户反馈的 6 个 UI/逻辑问题：API 端点模型列表显示、最大 token 位数限制、token 预算 0 警告、续写面板布局、续写按钮文字不可见、项目管理点击无效。

## 当前状态分析

### 问题 1：API 端点模型列表只显示一个
- **文件**：`/workspace/novelforge/ui/settings_dialog.py` 第 195-208 行
- **现状**：`_load_data()` 编辑端点时仅 `addItem(self._endpoint.get("default_model", ""))` 添加一个已保存模型。用户必须手动点"获取模型列表"才能看到完整列表。QComboBox 已设为 `setEditable(True)`，本身是下拉控件，但初始只有一个 item。
- **根因**：编辑现有端点时未自动拉取模型列表

### 问题 2：最大 token 只有 5 位数
- **文件**：`settings_dialog.py:414`、`continuation_panel.py:139`、`preset_manager.py:247`
- **现状**：三处均 `setRange(100, 100000)`，上限 100000（6 位）。但 QSpinBox 默认宽度可能只显示 5 位数字（99999），且某些模型（如 Claude 3.7 支持 64000 输出）接近上限。
- **根因**：`setRange` 上限偏低 + SpinBox 宽度未设置

### 问题 3：token 预算警告显示 0 token 但实际在输出
- **文件**：`/workspace/novelforge/core/prompt_assembler.py` 第 448-453 行、第 900-916 行
- **现状**：`budget = max_context - max_tokens - system_tokens - injection_tokens`。当 `max_tokens` 过大时 budget=0，当前章节被截断到约 0 token（无上下文），但 LLM 仍以 `max_tokens` 生成输出。
- **根因**：预算分配逻辑未保证当前章节最低 token（`SINGLE_CHAPTER_MIN_TOKENS=500`），budget=0 时仍允许续写

### 问题 4：上下文提取预览与续写输出布局不好看
- **文件**：`/workspace/novelforge/ui/continuation_panel.py` 第 99-219 行
- **现状**：整个面板用 QVBoxLayout 垂直堆叠（配置区→上下文预览→swipe 信息→推理内容→续写输出→按钮区），无 QSplitter，用户无法调整各区域大小。ContextPreviewPanel 和续写输出区互相挤压。
- **根因**：缺少可调整的分割布局

### 问题 5：右下角续写控制按钮看不见字
- **文件**：`/workspace/novelforge/ui/continuation_panel.py` 第 193-219 行
- **现状**：7 个按钮（开始续写/停止/重写/接受并追加/接受并继续/编辑后接受/并排对比）全部放在一个 QHBoxLayout 中。右栏默认宽度仅 280px（`main_window.py:85`），7 个按钮水平排列必然溢出/截断。
- **根因**：按钮数量多但布局只有一行，无换行/网格

### 问题 6：点击项目管理没有用
- **文件**：`/workspace/novelforge/ui/main_window.py` 第 707-712 行、`/workspace/novelforge/ui/project_panel.py` 第 33 行
- **现状**：`ProjectPanel` 继承自 `QWidget`，但 `main_window.py:712` 调用 `dialog.exec()`。QWidget 没有 `exec()` 方法（只有 QDialog 有），引发 `AttributeError`，对话框无法打开。
- **根因**：ProjectPanel 应继承 QDialog 而非 QWidget

## 修改方案

### 修改 1：API 端点模型列表自动拉取
**文件**：`/workspace/novelforge/ui/settings_dialog.py`
- **what**：编辑现有端点时，若有 base_url 和解密的 API key，自动触发模型列表拉取
- **why**：用户编辑端点时只看到一个模型，需手动点按钮才能看到完整列表
- **how**：
  1. 在 `_load_data()` 末尾（第 208 行后），检查 `base_url` 和 `self._decrypted_key` 是否都存在
  2. 若存在，自动调用 `self._on_fetch_models()`（已有方法，第 228 行）
  3. 确保 `_model_combo` 有 `setMinimumWidth(200)` 保证下拉箭头可见

### 修改 2：增大最大 token 上限与显示宽度
**文件**：`settings_dialog.py`、`continuation_panel.py`、`preset_manager.py`
- **what**：将 `setRange(100, 100000)` 改为 `setRange(100, 1000000)`，并设置 SpinBox 最小宽度
- **why**：支持大输出 token 模型 + 确保显示完整数字
- **how**：
  1. 三处 `setRange(100, 100000)` → `setRange(100, 1000000)`
  2. 每处添加 `self._max_tokens_spin.setMinimumWidth(120)`
  3. `preset_manager.py:252` 的 `max_context_spin` 已是 1000000，保持不变

### 修改 3：修复 token 预算 0 token 警告
**文件**：`/workspace/novelforge/core/prompt_assembler.py`
- **what**：保证当前章节至少有 `SINGLE_CHAPTER_MIN_TOKENS`（500）token 预算，必要时自动降低 max_tokens
- **why**：budget=0 时当前章节被截断到 0 token，LLM 无上下文仍生成输出，逻辑矛盾
- **how**：
  1. 第 448 行预算计算后，增加逻辑：
     ```python
     min_context_budget = SINGLE_CHAPTER_MIN_TOKENS
     if budget < min_context_budget:
         # 尝试降低 max_tokens 以保证最低上下文预算
         reduced_max_tokens = max_context - system_tokens - injection_tokens - min_context_budget
         if reduced_max_tokens > 0:
             result.warnings.append(
                 f"max_tokens 已从 {max_tokens} 自动降低至 {reduced_max_tokens} "
                 f"以保证当前章节最低 {min_context_budget} token 上下文"
             )
             max_tokens = reduced_max_tokens
             budget = min_context_budget
         else:
             # 即使 max_tokens=0 也无法保证最低预算
             result.warnings.append(
                 f"Token 预算严重不足：max_context({max_context})不足以容纳"
                 f"系统提示({system_tokens})+注入({injection_tokens})+"
                 f"最低上下文({min_context_budget})，续写质量将严重下降"
             )
             budget = max(0, reduced_max_tokens + min_context_budget)
     ```
  2. 确保 `result.max_tokens` 反映调整后的值（需检查 result 结构是否有此字段，若无则添加到 warnings 中说明）

### 修改 4：续写面板布局改用 QSplitter
**文件**：`/workspace/novelforge/ui/continuation_panel.py`
- **what**：将上下文预览面板和续写输出区放入 QSplitter（垂直），用户可拖拽调整高度
- **why**：当前垂直堆叠无法调整大小，上下文预览挤压输出区
- **how**：
  1. 在 `_setup_ui()` 中，创建 `QSplitter(Qt.Orientation.Vertical)`
  2. 将 `ContextPreviewPanel`、推理内容区、续写输出区加入 splitter
  3. 设置初始大小比例（如上下文预览 30%、推理 15%、输出 55%）：`splitter.setSizes([300, 150, 550])`
  4. 设置最小高度避免完全折叠：各区域 `setMinimumHeight(80)`
  5. 配置区和按钮区保持在 splitter 之外（顶部和底部）

### 修改 5：续写按钮改用网格布局
**文件**：`/workspace/novelforge/ui/continuation_panel.py`
- **what**：将 7 个按钮从单行 QHBoxLayout 改为 2 行网格布局
- **why**：280px 宽度无法容纳 7 个按钮，文字被截断
- **how**：
  1. 替换 `btn_layout = QHBoxLayout()` 为 `btn_layout = QGridLayout()`
  2. 第一行（主要操作）：开始续写、停止、重写（3 个）
  3. 第二行（接受操作）：接受并追加、接受并继续、编辑后接受、并排对比（4 个）
  4. 设置按钮 `setMinimumWidth(0)` + `setSizePolicy(Expanding, Fixed)` 让按钮均匀填充
  5. 导入 `QGridLayout`（从 `PySide6.QtWidgets`）

### 修改 6：ProjectPanel 改为继承 QDialog
**文件**：`/workspace/novelforge/ui/project_panel.py`、`/workspace/novelforge/ui/main_window.py`
- **what**：将 ProjectPanel 从 QWidget 改为 QDialog，修复 exec() 调用
- **why**：QWidget 无 exec() 方法，导致 AttributeError
- **how**：
  1. `project_panel.py` 第 15 行导入添加 `QDialog`
  2. 第 33 行 `class ProjectPanel(QWidget)` → `class ProjectPanel(QDialog)`
  3. 在 `_on_open_project` 和 `_open_project_by_id` 中，发射信号后调用 `self.accept()` 关闭对话框
  4. 在 `_on_new_project` 中，发射信号后调用 `self.accept()` 关闭对话框
  5. `main_window.py` 第 710 行 `setWindowModality` 可保留（QDialog 默认 ApplicationModal）
  6. 确认 `project_opened` 信号在 `accept()` 前发射（信号是同步的，先 emit 再 accept）

## 假设与决策

1. **问题 1 假设**：用户反馈"只显示一个"是指编辑端点时仅显示已保存的默认模型。QComboBox 本身已是下拉控件，无需更换控件类型。
2. **问题 3 决策**：采用"自动降低 max_tokens"策略而非"阻止续写"，因为用户可能仍希望获得输出（即使上下文不足）。
3. **问题 4 决策**：使用 QSplitter 而非改为水平双列布局，因为右栏宽度仅 280px，水平双列会更拥挤。
4. **问题 5 决策**：使用 2 行网格布局而非 FlowLayout，因为 Qt 无内置 FlowLayout，网格更简单可靠。
5. **问题 6 决策**：直接改继承为 QDialog 而非在 main_window 中包装，因为 ProjectPanel 本身就是对话框用途。

## 验证步骤

1. **运行现有测试**：`python -m pytest tests/ -v` 确保 161 个测试仍全部通过
2. **UI 冒烟测试**：
   ```bash
   QT_QPA_PLATFORM=offscreen python -c "
   from PySide6.QtWidgets import QApplication
   import sys; app = QApplication(sys.argv)
   from novelforge.ui.main_window import MainWindow
   from novelforge.core.config import ConfigManager
   w = MainWindow(config_manager=ConfigManager())
   print('MainWindow OK')
   "
   ```
3. **项目管理对话框测试**：验证 `ProjectPanel` 可作为 QDialog 打开
4. **Token 预算测试**：编写测试验证 budget < 500 时 max_tokens 自动降低
5. **模型列表测试**：验证编辑端点时自动拉取模型列表（需 mock）
