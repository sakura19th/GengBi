# 赓笔（NovelForge）UI 苹果风格优化方案

## Summary

将赓笔小说续写器的现有 QSS 主题（Catppuccin 暗色 + Material 亮色混搭）重写为 Apple Human Interface Guidelines 风格，并清理散落在 19 个 UI 文件中的 49 处内联 `setStyleSheet`，使全局苹果风格真正生效。

核心改动：重写 `light.qss` / `dark.qss` 两份主题文件（主色 `#1976d2` → `#007aff`，圆角 4px → 6-12px 分级，阴影改为极淡边框近似，列表选中态改半透明圆角），并重构内联样式为对象名/动态属性驱动，由全局 QSS 统一接管。

## Current State Analysis

### 主题加载机制
- `novelforge/ui/main_window.py` 的 `_apply_theme()`（640-666 行）通过 `get_theme_path(theme)` 读取 `resources/themes/{dark,light}.qss`，用 `app.setStyleSheet(qss)` 全局应用。
- 主题三态：暗色/亮色/跟随系统（后者读 `styleHints().colorScheme()`）。架构保持不变，仅替换 QSS 内容。
- `novelforge/utils/paths.py` 的 `get_theme_path()` 按名拼接 `.qss` 路径，无需改动。

### 现有 QSS 覆盖控件
两份 `.qss` 结构对称，覆盖：QWidget/QMainWindow/QMenuBar/QMenu/QStatusBar/QSplitter/QTreeView/QPlainTextEdit+QTextEdit/QPushButton(含 `#primaryBtn`)/QLineEdit+QSpinBox+QComboBox/QCheckBox/QLabel/QGroupBox/QScrollBar/QDialog/QTabWidget+QTabBar/QToolTip。

### 内联样式散落问题（最大隐性工作量）
Grep 显示 UI 代码中有 49 处内联 `setStyleSheet` 调用，分布在 13 个文件。这些内联样式会覆盖全局 QSS，是苹果风格落地的最大障碍。典型问题：
- 硬编码状态色：`color: blue/orange/red/green/gray` 散布在 `context_preview_panel.py`（约 18 处）、`continuation_panel.py`、`chapter_editor.py`。
- 硬编码字号/字重：`chapter_editor.py:88` 用 `font-size: 14px` 标题。
- `agent_panel.py:50-61` 用模块级常量定义三种状态样式（`#2563eb`/`#e5e7eb`/`#f3f4f6`，非苹果色）。
- `main_window.py:118` 用 `palette(mid)` 引用系统调色板。

### 苹果设计库约束（已研读 `dl_builtin_apple`）
- 主色 System Blue `#007aff`（亮）/`#0a84ff`（暗）。
- 背景层次：亮 `#ffffff`/`#f2f2f7`，暗 `#1c1c1e`/`#2c2c2e`/`#3a3a3c`。
- 文字：亮 `#1d1d1f`，暗 `#f5f5f7`；次要文字用半透明黑/白。
- 分隔线：`rgba(60,60,67,0.29)`（亮）/`rgba(84,84,88,0.6)`（暗）。
- 圆角偏大：卡片 10-12px，按钮可胶囊，输入 6-8px。
- 间距 4/8/12/16/24/32 阶梯。
- 字体优先链：`SF Pro Text`/`-apple-system`/`PingFang SC`/`Microsoft YaHei`。
- 阴影极轻，靠 1px 边框 + 层次色差近似（QSS 无 box-shadow）。

### QSS 能力边界（关键限制）
1. 不支持 `box-shadow` → 用极淡边框 `rgba(0,0,0,0.06)` + 背景层次近似。
2. 不支持 `backdrop-filter`（毛玻璃）→ 跨平台用接近不透明纯色，不追求真模糊。
3. 不支持 CSS 变量 `var()` → 两份 QSS 各硬编码字面量；可选 Python token 字典模板生成（本方案暂不引入，保持读静态文件架构）。
4. `border-radius` 只裁背景/边框不裁子控件 → 容器圆角需配合子控件 `background: transparent`。
5. `QLabel` 不支持 `:hover`；macOS 上 QMenuBar 被系统接管（本应用 Windows 运行，不影响）。

## Proposed Changes

### 改动 1：重写 `novelforge/resources/themes/light.qss`
**What**: 用苹果亮色 token 重写整份 QSS。保持控件选择器结构与现有布局兼容。
**Why**: 现有 Material 蓝 `#1976d2` + 4px 小圆角不符合苹果风格。
**How** — 关键替换：
- 字体族：`"SF Pro Text", "SF Pro", "-apple-system", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif`。
- 主背景 `#ffffff`，分组/侧栏背景 `#f2f2f7`，输入框底 `#ffffff`。
- 文字主 `#1d1d1f`，次 `rgba(60,60,67,0.6)`，三 `rgba(60,60,67,0.3)`。
- 分隔线 `rgba(60,60,67,0.29)`（QSplitter handle、QMenuBar border-bottom、QGroupBox border）。
- 主色 `#007aff`：`#primaryBtn` 背景、`:focus` border、`::item:selected`、QCheckBox::indicator:checked、选区高亮。
- 普通按钮底 `#f2f2f7`，hover `#e5e5ea`，pressed `#d1d1d6`，圆角 8px。
- `#primaryBtn`：accent 底白字，`min-height: 28px; border-radius: 14px`（胶囊），hover `#3395ff`，无 border。
- 输入框圆角 8px，focus 时 `border: 1.5px solid #007aff`。
- QTreeView/QListView：`background: transparent; border: none; outline: 0; show-decoration-selected: 0`；item `padding: 6px 8px; border-radius: 6px; margin: 1px 4px`；`::item:selected` 用 `rgba(0,122,255,0.12)`；`::item:hover` 用 `rgba(0,122,255,0.06)`。
- QPlainTextEdit/QTextEdit 圆角 10px，背景 `#ffffff`，选区 `rgba(0,122,255,0.2)`。
- QGroupBox 圆角 10px，border 用 separator 色，标题用次要文字色。
- QTabBar::tab 圆角 6px，选中白底无 border，未选中透明。
- QScrollBar 宽 8px，handle `rgba(60,60,67,0.3)` 圆角 4px，去两端按钮。
- QMenu/QToolTip/QDialog 圆角 10px，elevated 白底，极淡 border `rgba(0,0,0,0.1)`。
- QMenuBar 高度提至 28px，item padding `6px 12px` 圆角 6px。

### 改动 2：重写 `novelforge/resources/themes/dark.qss`
**What**: 与 light 对称的苹果暗色版。
**Why**: 现有 Catppuccin 暗色（`#1e1e2e`/`#89b4fa`）非苹果色系。
**How** — 与 light 同结构，色值替换：
- 主背景 `#1c1c1e`，分组/侧栏 `#2c2c2e`，输入框/卡片 `#3a3a3c`，elevated `#2c2c2e`。
- 文字主 `#f5f5f7`，次 `rgba(235,235,245,0.6)`，三 `rgba(235,235,245,0.3)`。
- 分隔线 `rgba(84,84,88,0.6)`。
- 主色 `#0a84ff`（暗色 System Blue）：各 accent 位替换。
- 普通按钮底 `#3a3a3c`，hover `#48484a`，pressed `#636366`。
- `#primaryBtn` hover `#3395ff`。
- 选中态 `rgba(10,132,255,0.12)`，hover `rgba(10,132,255,0.06)`。
- 滚动条 handle `rgba(235,235,245,0.3)`。
- 边框/阴影近似用 `rgba(255,255,255,0.08)` 替代亮色的 `rgba(0,0,0,0.06)`。

### 改动 3：清理 `agent_panel.py` 状态样式常量
**What**: 将 `_STYLE_CURRENT/_COMPLETED/_PENDING` 三个模块级常量改为苹果色，并改为由对象名驱动。
**Why**: 现用 `#2563eb`/`#e5e7eb`/`#f3f4f6`（Tailwind 灰蓝），非苹果色；且硬编码绕过全局 QSS。
**How**:
- 删除三常量，改为给 QLabel 设 `setObjectName("phaseCurrent"/"phaseCompleted"/"phasePending")`。
- 在两份 QSS 新增对应选择器：
  - `QLabel#phaseCurrent`：亮 `background: #007aff; color: white; border-radius: 6px`；暗 accent `#0a84ff`。
  - `QLabel#phaseCompleted`：亮 `background: #e5e5ea; color: rgba(60,60,67,0.6)`；暗 `background: #48484a; color: rgba(235,235,245,0.6)`。
  - `QLabel#phasePending`：亮 `background: #f2f2f7; color: rgba(60,60,67,0.6)`；暗 `background: #3a3a3c`。
- `agent_panel.py` 的 `setStyleSheet(_STYLE_*)` 三处（143/255/258/261 行）改为 `setObjectName(...)`。注意：切换状态时需先 `setObjectName("")` 清旧名再设新名，并 `style().unpolish()`/`polish()` 刷新。

### 改动 4：统一状态色到对象名（context_preview_panel.py 等）
**What**: 把 `color: blue/orange/red/green/gray` 内联样式改为对象名驱动，由 QSS 接管。
**Why**: 苹果规范反对随意用系统色；蓝应专属主操作，橙专属警告，红专属危险，绿专属成功。当前 `color: blue` 表"加载中"语义不精确。
**How** — 语义映射（含跨文件统一）：
- `color: gray`（次要/就绪）→ `setObjectName("textSecondary")`，QSS `QLabel#textSecondary { color: <次要文字色> }`。
- `color: blue`（进行中/信息）→ 改用 `setObjectName("textInfo")`，QSS 用 `#ff9500`（橙，苹果"进行中"语义）或保留 accent 蓝。本方案采用：进行中=橙 `#ff9500`，链接/主操作信息=蓝 accent。
- `color: orange`（警告）→ `setObjectName("textWarning")`，`#ff9500`。
- `color: red`（错误）→ `setObjectName("textDanger")`，`#ff3b30`。
- `color: green`（成功）→ `setObjectName("textSuccess")`，`#34c759`。
- 涉及文件与行数：`context_preview_panel.py`（约 18 处）、`continuation_panel.py`（181/596/606/625 行）、`chapter_editor.py`（88/99/171/196/213/242/248/251 行）、`history_panel.py`（187 行）、`regex_manager.py`（245 行）。
- 对同时含 `font-size`/`font-weight`/`padding` 的复合内联（如 `chapter_editor.py:88` 的 `font-weight: bold; font-size: 14px`），拆分为：标题用 `setObjectName("panelTitle")` 由 QSS 统一字号字重；纯状态色用上述语义对象名。
- QSS 新增 `QLabel#textSecondary/textInfo/textWarning/textDanger/textSuccess/panelTitle` 选择器，亮暗各一套。
- 对带 `font-size: 12px/11px` 的元信息标签，用 `setObjectName("metaText")` 统一 12px 次要色。

### 改动 5：清理其他内联样式
**What**: 处理剩余内联 `setStyleSheet`。
**Why**: 确保全局 QSS 接管，无局部样式架空。
**How** — 逐文件：
- `main_window.py:118`（`palette(mid)` 面板头）→ 改 `setObjectName("panelHeader")`，QSS 给 `#panelHeader` 分组背景。
- `main_window.py:124`（折叠按钮 `text-align: left; font-weight: bold`）→ `setObjectName("collapseToggle")`。
- `main_window.py:2083/2089`（提示词预览对话框灰字）→ `setObjectName("textSecondary")`。
- `dialogs.py:57`（标题 18px bold）→ `setObjectName("dialogTitle")`，QSS `font-size: 20px; font-weight: 600`。
- `chapter_list.py:361`（提示灰字）→ `setObjectName("hintText")`。
- `project_panel.py:68/85/90` → 标题用 `#panelTitle`，列表 `font-size` 留 QSS，提示用 `#hintText`。
- `context_preview_panel.py:339`（流式视图样式）、`692`（条目内容标签）、`712`（条目 meta）→ 对象名接管。
- `settings_dialog.py:368`（列表字号）→ 留 QSS 统一 13px，删内联。

### 改动 6：修复"跟随系统"主题实时切换（顺带）
**What**: 在 `main_window.py` 监听系统深浅色变化信号，自动重应用 QSS。
**Why**: 现有"跟随系统"只在启动时判断一次，系统切换深浅色后 UI 不更新，与苹果"实时跟随"体验不符。
**How**: 在 `_apply_theme` 中，当 theme 为 system 时，连接 `app.styleHints().colorSchemeChanged` 信号到重新调用 `_apply_theme`。需用标志位防止重复连接。

## Assumptions & Decisions

1. **不追求真毛玻璃/真阴影**：跨平台 PySide6 无法实现苹果 Liquid Glass 和多层 box-shadow，用极淡边框 + 层次色差近似。这是务实取舍，不引入 `QGraphicsDropShadowEffect`（性能陷阱）和 `FramelessWindowHint`（丢失系统标题栏）。
2. **保持读静态 .qss 文件架构**：不引入 Python token 字典模板生成，避免改动 `_apply_theme` 核心逻辑。两份 QSS 各硬编码色值，人工保持对称。
3. **状态色语义重新映射**：现有 `color: blue`（进行中）改用橙 `#ff9500`，符合苹果"进行中=橙"语义。若用户更希望保持蓝色表示进行中，可在实施时调整（决策点）。
4. **不改动任何业务逻辑**：本次纯 UI 层改动，不触碰 services/core/models，续写/提取/Agent 流程逻辑保持不变。
5. **不新增依赖**：不引入 QDarkStyle 等第三方主题包，纯 QSS 手写。
6. **macOS 兼容性**：QMenuBar 在 macOS 会被系统接管（自动原生外观），QSS 对其无效属预期行为，不需处理。

## Verification Steps

1. **启动验证**：分别用暗色/亮色启动应用，确认主窗口、菜单栏、状态栏、五栏面板整体呈苹果风格（主色蓝、圆角增大、间距舒展）。
2. **控件逐一验证**：
   - `#primaryBtn` 胶囊效果（圆角=高度/2，白字 accent 底）。
   - QTreeView/QListView 章节列表选中态：淡蓝半透明圆角色块，文字不变色。
   - QLineEdit/QComboBox focus 时 accent 描边。
   - QComboBox 弹窗圆角（注意弹窗是独立窗口，圆角可能需额外处理）。
   - QTabBar 选中态白底无 border。
   - QScrollBar 细窄 8px 圆角。
   - QGroupBox 圆角与标题色。
3. **状态色验证**：触发上下文提取流程，确认"提取中/完成/失败"状态文字颜色分别为橙/绿/红（苹果语义色），无裸 blue/gray。
4. **Agent 面板验证**：启动 Agent 续写，确认阶段进度指示器三态（current=accent 蓝/completed=灰/pending=浅灰）为苹果色。
5. **主题切换验证**：菜单"视图→主题"切换暗/亮/跟随系统，确认即时生效；"跟随系统"下切换系统深浅色，UI 实时跟随（验证改动 6）。
6. **回归验证**：运行 `tests/` 下现有测试（`pytest`），确认无业务逻辑回归；重点跑 `test_e2e_workflow.py`、`test_tgbreak_e2e.py`。
7. **圆角溢出检查**：检查设大圆角的容器（QGroupBox、QPlainTextEdit）内部子控件是否刺出方角，必要时补 `background: transparent`。
