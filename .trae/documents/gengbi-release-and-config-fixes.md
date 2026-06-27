# 赓笔发布准备与续写配置修复

## 概述

一次性完成 7 项修复与发布准备：①历史预算永久最大 ②移除目标字数/最大Token控件 ③续写参数持久化 ④验证回溯章节数匹配 ⑤软件改名 NovelForge→赓笔 ⑥版本 v0.1 ⑦隐私声明移除同意按钮 ⑧新建 GitHub 发布文件夹。

## 当前状态分析

### 问题 1：历史预算被打压到 500
- `prompt_assembler.py:59` `SINGLE_CHAPTER_MIN_TOKENS=500` 是 history_budget 下限
- `main_window.py:1192` `max_context = params.get("max_context") or gen_params.get("max_context", 32000)`，默认 32000 偏小
- `default_preset.json:83` `"max_context": 32000`
- budget = 32000 - max_tokens(2000) - 系统提示 - 注入 - 用户输入，被挤压后触发 500 兜底
- `get_parameters()` 不返回 max_context，故始终回退预设 32000

### 问题 2：续写面板参数未持久化
- `continuation_panel.py:144-170` 用硬编码默认值（温度 0.8、目标字数 2000、最大Token 2000、回溯 5）
- `config.py:75-82` 有 `continuation.default_*` 四项，但只被 `settings_dialog.py` 编辑，从未应用到面板
- 面板启动用硬编码，设置对话框改的默认值不生效

### 问题 3：目标字数和最大Token控件
- `continuation_panel.py:150-163` 有 `_target_words_spin`（目标字数）和 `_max_tokens_spin`（最大Token）控件
- `settings_dialog.py:398-427` 同样有这两个控件
- `get_parameters()`（404-415）返回 target_words 和 max_tokens
- `set_parameters()`（417-426）回填这两项

### 问题 4：回溯章节数匹配（已验证无需改动）
- 续写：`main_window.py:1210` `lookback_chapters = params.get("lookback_chapters", 0)` → 传入 `assemble(lookback_chapters=)`（1224）→ `_build_history()` 按 lookback 截取（已正确实现）
- 上下文提取：`context_preview_panel._lookback_combo` 独立控件，默认"全部前文"
- 用户确认：保持独立，仅验证续写侧已匹配（✓ 已实现，无需改动）

### 问题 5-8：改名/版本/隐私/发布
- 显示名 NovelForge 散布在 6 个文件；版本仅 `__init__.py:14`；隐私对话框有"同意/不同意"按钮且首次启动强制同意；根目录无 README/LICENSE

## 实施方案

### Part A：max_context 默认 9999999（历史预算永久最大）

**文件**：`novelforge/resources/defaults/default_preset.json`（line 83）
```json
"max_context": 9999999
```

**文件**：`novelforge/ui/main_window.py`
- line 1192：`max_context = params.get("max_context") or gen_params.get("max_context", 9999999)`
- line 1892（查看提示词路径）：同样改回退值为 9999999

**文件**：`novelforge/services/agent_orchestrator.py`（line 473）
- `max_context=self.parameters.get("max_context", 9999999)`

**文件**：`novelforge/ui/preset_manager.py`（line 257-260）
- `_max_context_spin` 默认值改为 9999999（`setValue(9999999)`），保留范围 1000-1000000 的上限改为 9999999（`setRange(1000, 9999999)`）

**效果**：budget = 9999999 - 2000 - 系统提示 ≈ 999万，永不触发 500 兜底，历史仅由 lookback_chapters 控制。

### Part B：移除目标字数和最大Token控件

**决策**：移除两个 UI 控件；target_words 内部回退默认 2000（供 `{{target_words}}` 宏）；max_tokens(生成上限) 不传 LLM（用模型默认上限，等效"不限制"）；assemble 的 max_tokens 仍用预设 2000 仅算 budget。

**文件**：`novelforge/ui/continuation_panel.py`
- 删除 line 150-155（目标字数控件）和 157-163（最大Token控件）
- `get_parameters()`（404-415）：移除 `"target_words"` 和 `"max_tokens"` 两行
- `set_parameters()`（417-426）：移除 target_words 和 max_tokens 的回填分支

**文件**：`novelforge/ui/settings_dialog.py`
- 删除 line 398-406（目标字数控件）和 418-427（最大Token控件）
- 保存逻辑中移除对 `default_target_words` 和 `default_max_tokens` 的读写

**文件**：`novelforge/ui/main_window.py`
- line 1193：`max_tokens = gen_params.get("max_tokens", 2000)`（移除 `params.get("max_tokens") or`，仅从预设读，用于 assemble budget）
- line 1194：`target_words = params.get("target_words", 2000)`（保留，回退 2000 供宏）
- 删除 line 1240-1243（effective_max_tokens 同步逻辑，不再需要覆盖 params）
- `_on_rewrite`（2175-2183）：`set_parameters(last_params)` 仍兼容（set_parameters 已移除对应分支，多余键忽略）

**文件**：`novelforge/services/continuation_worker.py`（line 302）
- `max_tokens=self.parameters.get("max_tokens")` — params 无 max_tokens → 返回 None → `stream_chat_completion` 已支持 None（不写入 payload，LLM 用模型默认上限）✓ 无需改动

**文件**：`novelforge/services/agent_orchestrator.py`
- line 473：`max_tokens=self.parameters.get("max_tokens", 2000)` — params 无 max_tokens → 回退 2000（assemble budget）✓
- 确认 `_run_writing` 中 LLM 调用的 max_tokens 来源；若用 `self.parameters.get("max_tokens")` 则返回 None（LLM 默认）✓

### Part C：续写参数持久化（温度 + 回溯章节数）

**文件**：`novelforge/ui/main_window.py`

**启动时加载**（在 `_refresh_presets` 之后或 `__init__` 末尾）：
```python
def _apply_continuation_defaults(self) -> None:
    """从 config 加载上次的续写参数到面板。"""
    cont = self.config_manager.get_continuation_settings()
    self.continuation_panel.set_parameters({
        "temperature": cont.get("default_temperature", 0.8),
        "lookback_chapters": cont.get("default_lookback_chapters", 5),
    })
```
在 `_refresh_presets()` 调用后调用 `self._apply_continuation_defaults()`。

**续写时保存**（`_on_start_continuation` 开头，获取 params 后）：
```python
# 持久化续写参数到 config
cont = self.config_manager.config.setdefault("continuation", {})
cont["default_temperature"] = params.get("temperature", 0.8)
cont["default_lookback_chapters"] = params.get("lookback_chapters", 5)
self.config_manager.save()
```

**文件**：`novelforge/core/config.py`
- `continuation` 默认结构（line 75-82）：移除 `default_target_words` 和 `default_max_tokens`（已无对应控件），保留 `default_lookback_chapters`、`default_temperature`、`auto_save`、`show_token_count`

### Part D：回溯章节数匹配验证（无需改动）

已验证：续写时 `lookback_chapters` 从 `_lookback_spin` → `params` → `assemble(lookback_chapters=)` → `_build_history()` 按 lookback 截取。上下文提取保持独立控件。无需改动，仅在计划中记录验证结论。

### Part E：软件改名 NovelForge → 赓笔

替换所有**显示名** NovelForge 为"赓笔"（包名 novelforge 保持不变）。

**文件**：`main.py`
- line 30：`app.setApplicationName("赓笔")`
- line 31：`app.setOrganizationName("赓笔")`
- line 1 docstring：`"""赓笔 小说续写器入口模块。"""`

**文件**：`novelforge/__init__.py`
- line 1 docstring：`"""赓笔 小说续写器包。"""`

**文件**：`novelforge/ui/main_window.py`
- line 238：`self.setWindowTitle("赓笔 - 小说续写器")`
- line 802：`self.setWindowTitle(f"赓笔 - {project.name}")`
- line 547：`about_action = QAction("关于 赓笔(&A)", self)`
- line 664/684/693：`QSettings("赓笔", "赓笔")`（三处）
- line 2607：`"关于 赓笔"`
- line 2608：`f"<h3>赓笔 小说续写器</h3>"`

**文件**：`novelforge/ui/dialogs.py`
- line 19：`PRIVACY_NOTICE = """赓笔 隐私声明`
- line 24：`所有小说内容...存储在本地（~/.novelforge/）` — 保留路径名 novelforge（包名不变）
- line 60：`title = QLabel("欢迎使用 赓笔")`

**文件**：`novelforge/resources/build.spec`
- line 2 docstring：`"""赓笔 PyInstaller 打包配置。"""`
- line 10：`生成 赓笔 可执行文件`
- line 48：`name='赓笔'`

**文件**：`novelforge/resources/build.py`
- line 2 docstring：`"""赓笔 打包辅助脚本。"""`
- line 10：`输出 dist/赓笔 可执行文件`

**注**：QSettings 改名会导致旧版窗口状态丢失（v0.1 早期可接受）。

### Part F：版本改为 v0.1

**文件**：`novelforge/__init__.py`（line 14）
```python
__version__ = "0.1"
```

### Part G：隐私声明移除同意按钮

**文件**：`novelforge/ui/dialogs.py`

`PRIVACY_NOTICE`（line 19-41）：
- 末尾 `点击"同意"表示您已阅读并同意以上声明。` 改为 `本声明仅供参考，不作为接受/拒绝条件。`

`PrivacyDialog`（line 44-80）：
- 移除 `QDialogButtonBox`、`agree_btn`、`disagree_btn`（line 72-79）
- 改为单个"关闭"按钮：
```python
close_btn = QPushButton("关闭")
close_btn.setObjectName("primaryBtn")
close_btn.clicked.connect(self.accept)
layout.addWidget(close_btn)
```
- docstring 改为 `"""隐私声明对话框（仅展示，无同意/不同意）。"""`

**文件**：`novelforge/ui/main_window.py`（line 729-738 `_check_privacy_notice`）
```python
def _check_privacy_notice(self) -> None:
    """首次启动展示隐私声明（不强制同意）。"""
    if not self.config_manager.is_privacy_accepted():
        dialog = PrivacyDialog(self)
        dialog.exec()
        self.config_manager.accept_privacy()  # 标记已展示过
        logger.info("隐私声明已展示")
```
- 移除"不同意则退出"逻辑；展示后标记已展示，下次不再弹

### Part H：新建 GitHub 发布文件夹

**创建目录**：`/workspace/release/`

**文件**：`release/README.md`（中文，内容大纲）
- 标题：`# 赓笔 (Gengbi)`
- 一句话简介：古意盎然的小说续写桌面工具，辅助创作者在既有故事后接续新篇，而非替代写作
- 功能特性：上下文提取、按章节绑定、Token 拆分增量提取、回溯章节数控制前文、SillyTavern 风格提示词组装、多轮 Agent 续写、流式输出、正则脚本、主题切换
- 截图占位（可选）
- 安装与运行：Python 3.11+、`pip install -r requirements.txt`、`python main.py`；或下载打包的可执行文件
- 打包说明：`python -m novelforge.resources.build`
- 配置说明：首次启动配置 API 端点与 Key（本地加密存储）
- 数据存储路径：`~/.novelforge/`
- 隐私声明摘要
- 技术栈：PySide6、aiohttp、Jinja2、tiktoken、pydantic
- 开源协议（MIT）
- 免责声明：本工具辅助续写，生成内容由 LLM 服务商处理，使用者自行承担内容责任

**文件**：`release/LICENSE`（MIT 协议，年份 2026，版权人"赓笔开发者"）

## 假设与决策

1. **max_context=9999999 而非"无限"**：用大整数占位实现"不限制"，budget≈999万永不裁剪，历史仅由 lookback 控制。
2. **max_tokens(生成) 不传 LLM（None）**：移除控件后 params 无 max_tokens，worker/orchestrator 传 None，LLM 用模型默认上限。等效"不限制生成长度"。assemble 仍用预设 2000 算 budget（不影响实际生成）。
3. **target_words 内部回退 2000**：移除控件但保留宏 `{{target_words}}` 可用，回退默认 2000。
4. **移除 main_window:1240-1243**：effective_max_tokens 同步逻辑不再需要（budget 巨大不会降 max_tokens）。
5. **QSettings 改名**：v0.1 早期用户极少，窗口状态丢失可接受。
6. **隐私声明保留首次展示但不强制**：`privacy_accepted` 复用为"已展示过"标记，展示后不再弹；帮助菜单仍可查看。
7. **release/ 仅放 README+LICENSE**：源码在仓库根目录，GitHub 发布仓库根即源码；release/ 作为发布说明与协议的集中位置。
8. **包名 novelforge 不变**：避免大规模 import 改动，仅改显示名。
9. **config.continuation 移除 default_target_words/default_max_tokens**：已无对应控件和消费方。

## 验证步骤

1. 运行测试套件：`cd /workspace && QT_QPA_PLATFORM=offscreen python -m pytest tests/ -x -q`
2. 验证导入：`cd /workspace && QT_QPA_PLATFORM=offscreen python -c "from novelforge import __version__; from novelforge.ui.main_window import MainWindow; from novelforge.ui.dialogs import PrivacyDialog; print(__version__)"`
3. 检查改名无遗漏：`grep -rn "NovelForge" novelforge/ main.py`（应仅剩路径名 novelforge，无显示名 NovelForge）
4. 检查移除控件无残留引用：`grep -n "_target_words_spin\|_max_tokens_spin" novelforge/ui/continuation_panel.py novelforge/ui/settings_dialog.py`
5. 确认 max_context 默认：`grep -n "9999999" novelforge/resources/defaults/default_preset.json novelforge/ui/main_window.py novelforge/services/agent_orchestrator.py`
