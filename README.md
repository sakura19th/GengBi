# 赓笔 (GengBi)

> 古意盎然的小说续写桌面工具——辅助创作者在既有故事之后接续新篇，而非替代写作。

**当前版本：v0.2.13**

赓笔（gēng bǐ）取"赓续笔墨"之意。"赓"本义为接续他人诗文，正契合本工具的定位：在你已写就的章节之后，借助大语言模型续写后续内容，让故事自然生长。

## 功能特性

- **上下文提取**：自动从前文章节提取人物、设定、伏笔等关键信息，按章节独立绑定存储，切换章节即加载对应上下文
- **Token 拆分增量提取**：当选中章节较多时，按 Token 数自动拆分为多批请求，每批附带已有提取结果，仅输出新增或修改的条目（替换式合并）
- **回溯章节数控制**：通过"回溯章节数"精确控制纳入续写提示词的前文范围（0=全部前文，N=最近 N 章），历史预算不设上限
- **SillyTavern 兼容**：预设格式兼容 SillyTavern（`prompts` + `prompt_order` + `extensions.regex_scripts`），支持导入 ST 预设 JSON 与世界书（world info）条目，开箱即用现有生态资源
- **SillyTavern 风格提示词组装**：三阶段组装（排序 → 深度注入 → Token 裁剪），支持宏替换、Jinja2 模板、正则脚本、worldInfoBefore/After 注入
- **卷级多章节续写**：一次产出 N 章正文，包含前文深度分析、卷大纲规划、多维度审计（一致性/节奏/吸引力/结构/连贯/伏笔/人物）、终稿大纲生成、逐章细纲与写作/验证/修订循环；支持 token 切分增量分析、推进速度（缓速/中速/快速）与多轮审计
- **流式输出**：实时显示续写内容，支持停止、重写、多版本（swipe）对比、推理内容展示
- **正则脚本引擎**：支持 USER_INPUT / AI_OUTPUT / WORLD_INFO 三类 placement，对接收与发送内容做正则变换
- **主题与字体**：Apple HIG 风格主题，支持暗色/亮色/跟随系统三种模式（系统模式实时响应切换），可自定义字体族、字号、行高
- **本地优先**：所有项目数据、配置、API Key 均存储在本地，API Key 加密落盘

## 安装与运行

### 环境要求

- Python 3.11 或更高版本
- 兼容 OpenAI API 格式的 LLM 服务端点（如 OpenAI、DeepSeek、Moonshot、本地 Ollama 等）

### 从源码运行

```bash
# 1. 克隆仓库
git clone <仓库地址>
cd <仓库目录>

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动
python main.py
```

### 打包为可执行文件

```bash
# 当前平台打包（生成 dist/GengBi_v{版本号} 可执行文件，如 GengBi_v0.2.13.exe）
python -m novelforge.resources.build

# 或直接调用 PyInstaller
pyinstaller --clean -y novelforge/resources/build.spec
```

## 配置说明

首次启动后，请前往"设置"配置：

1. **API 端点**：填写 base_url、API Key、默认模型
2. **提取模型**（可选）：上下文提取使用的模型，留空则使用端点默认模型
3. **续写参数**：温度、回溯章节数（启动时自动加载上次设置）
4. **Token 拆分限制**：上下文提取的单批 Token 上限（不限制 / 50k / 100k / 250k / 500k）

API Key 使用 PBKDF2HMAC + Fernet 加密存储，不明文落盘。

## 快速上手

1. **导入文本**：菜单「文件 → 导入 TXT」选择小说文本文件，自动按章节拆分；也可在项目管理中手动新建章节
2. **（可选）提取上下文**：在上下文提取面板点击「提取上下文」，自动抽取人物/地点/事件/伏笔等条目，绑定到当前章节
3. **（可选）提取世界观底层 / 主角形象 / 自定义设定**：点击对应按钮分别提取全文世界观 7 维度、主角 8 维度心理档案、或将自定义要求结构化为审计必查项
4. **续写**：在续写面板选择模式（单章续写 / 卷续写），填写用户指令（可选），点击「开始续写」，流式输出结果
5. **（可选）审计与修正**：续写完成后点击「审计」，AI 流式输出审计报告，可编辑后采纳，基于审计意见重写为新版本
6. **输出**：接受续写即将其提升为独立章节插入当前章节之后；可导出为 TXT/Markdown

> 单章续写：接受即提升为章节，可继续以此往复。卷续写：一次产出 N 章，含深度分析、卷大纲、多维度审计与逐章写作/验证/修订循环。

## 数据存储路径

所有数据存储在本地：

```
~/.novelforge/
├── config.json          # 全局配置（含加密的 API Key）
├── projects/            # 项目数据库与附件
└── logs/                # 运行日志
```

## 隐私声明摘要

- 除调用你配置的 LLM API 外，不发送任何数据到第三方
- 所有小说内容、项目数据存储在本地，不进行云端同步
- API Key 加密存储，日志中自动脱敏
- 续写请求内容（含小说正文片段）由你选择的 LLM 服务商处理，请自行评估其隐私政策

完整隐私声明可在应用内"帮助 → 隐私声明"查看。

## 技术栈

- **GUI**：PySide6 (Qt6)
- **异步 HTTP**：aiohttp
- **模板引擎**：Jinja2
- **Token 计数**：tiktoken
- **数据模型**：pydantic v2
- **存储**：aiosqlite（异步 SQLite）
- **加密**：cryptography
- **测试**：pytest

## 变量与宏调用教程

赓笔在续写提示词中支持三类可调用变量：**内置宏**（`{{name}}` 简单替换）、**ST 风格变量**（`{{setvar}}`/`{{getvar}}`/注释）和 **Jinja2 模板表达式**（`{% %}` / `{{ func(...) }}`）。自定义预设的任何 prompt 条目 `content` 中均可使用，发送前由宏引擎与模板引擎依次替换。

### 替换顺序

发送前对每条 prompt `content` 的处理顺序（不递归）：

1. `{{// comment}}` 注释 → 空字符串
2. `{{setvar::name::value}}` / `{{getvar::name}}` → 变量读写（副作用，不缓存）
3. `{{name}}` 简单宏 → 上下文取值，未知宏保留原样
4. Jinja2 `{{ expr }}` / `{% stmt %}` → 沙箱渲染

接收后（AI 输出）也会对输出文本再跑一遍模板渲染，便于在输出中 `setvar` 回写变量。

### 一、内置宏（`{{name}}`）

直接替换为字符串，无需调用函数。来自 `MacroContext`，由小说档案 + 当前章节 + 续写参数自动填充。

| 宏 | 说明 | 来源 |
|---|---|---|
| `{{book}}` | 小说标题 | 项目档案 `title` |
| `{{author}}` | 作者 | 项目档案 `author` |
| `{{protagonist}}` | 主角姓名 | 项目档案 `protagonist` |
| `{{synopsis}}` | 小说简介 | 项目档案 `synopsis` |
| `{{world_setting}}` | 世界观设定 | 项目档案 `world_setting` |
| `{{writing_style}}` | 写作风格 | 项目档案 `writing_style` |
| `{{chapter_title}}` | 当前章节标题 | 当前章节 |
| `{{chapter_index}}` | 当前章节序号（从 1 开始） | 当前章节 |
| `{{target_words}}` | 目标续写字数 | 续写参数 |
| `{{user}}` | 用户名（ST 兼容，默认 `User`） | 全局配置 |
| `{{char}}` | 角色名（ST 兼容，默认 `Assistant`） | 全局配置 |

**额外注入宏**（由主窗口在续写时动态注入，非 `MacroContext` 字段）：

| 宏 | 说明 | 来源 |
|---|---|---|
| `{{world_ontology}}` | 世界观底层 7 维度 JSON（格式化缩进） | `OntologyExtractor` 提取，固化到 `Project.world_ontology` |
| `{{protagonist_profile}}` | 主角形象 8 维度心理学档案 JSON（格式化缩进） | `ContextExtractor._extract_protagonist`，按章节缓存 |

> 这两个宏在卷续写的各阶段模板中也同样可用（深度分析、卷大纲、章节细纲、验证、修订）。

示例（默认预设 main 条目片段）：

```text
【世界观底层】
{{world_ontology}}

【主角信息】
{{protagonist_profile}}

【写作风格】
{{writing_style}}

目标字数约 {{target_words}} 字。
```

### 二、ST 风格变量（`{{setvar}}` / `{{getvar}}`）

兼容 SillyTavern 变量语法，配合分层变量存储（`VariableStore`，global/project/chapter/cache 四作用域）。

| 语法 | 作用 |
|---|---|
| `{{setvar::name::value}}` | 设置变量 `name=value`，替换为空字符串。默认写入 chapter 作用域 |
| `{{getvar::name}}` | 读取变量 `name`，不存在返回空字符串 |
| `{{// comment}}` | 注释，替换为空字符串 |

变量作用域与持久化：

- `global`：`~/.novelforge/variables/global_variables.json`，跨项目持久化
- `project`：`~/.novelforge/projects/{project_id}/variables.json`，项目内持久化
- `chapter`（默认）：章节元数据 `chapter.metadata['variables']`，随章节持久化
- `cache`：内存，进程结束失效

> 注：`{{setvar::x::v}}` / `{{getvar::x}}` 默认操作 chapter 作用域。如需跨作用域读写，请改用下文 Jinja2 的 `setvar(..., scope='global')` 调用形式。

示例：在提示词中累加计数器

```text
{{setvar::scene_count::1}}
当前是第 {{getvar::scene_count}} 个场景。
```

### 三、Jinja2 模板表达式（`{{ func() }}` / `{% %}`）

沙箱环境（`ImmutableSandboxedEnvironment`），5 秒超时保护，递归深度限制 50。可调用白名单函数：

**变量读写（支持指定作用域）**

| 函数 | 说明 |
|---|---|
| `getvar(name, scope='chapter', default=None)` | 读取变量，可指定作用域 |
| `setvar(name, value, scope='chapter')` | 写入变量 |
| `hasvar(name, scope='chapter')` | 判断变量是否存在 |
| `delvar(name, scope='chapter')` | 删除变量 |

**章节访问**

| 函数 | 说明 |
|---|---|
| `get_chapter(index)` | 获取第 index 章（0 基） |
| `get_chapters()` | 获取所有章节列表 |
| `get_current_chapter()` | 获取当前章节对象 |
| `get_chapter_count()` | 获取章节总数 |

**小说档案**

| 函数 | 说明 |
|---|---|
| `get_book()` | 小说标题 |
| `get_protagonist()` | 主角姓名 |
| `get_novel_profile()` | 小说档案对象 |
| `get_writing_style()` | 写作风格 |
| `get_context_entries()` | 当前章节的上下文条目列表 |

**工具与正则**

| 函数 | 说明 |
|---|---|
| `regex_apply(text, placement=1)` | 对文本应用正则脚本（placement: 1=USER_INPUT / 2=AI_OUTPUT / 5=WORLD_INFO） |
| `substitute_macros(text)` | 对文本再跑一次 `{{name}}` 宏替换 |
| `now(format='%Y-%m-%d %H:%M:%S')` | 当前时间字符串 |
| `word_count(text)` | 计算字数 |
| `truncate(text, length=200, suffix='...')` | 截断文本 |

示例：

```jinja
{% if get_current_chapter() %}
当前章节：{{ get_current_chapter().title }}（共 {{ get_chapter_count() }} 章）
{% endif %}

上一章末尾：{{ truncate(get_chapter(get_chapter_count() - 2).content, 500) }}

主角：{{ get_protagonist() }}
写作风格：{{ get_writing_style() }}

{% set scene_count = getvar('scene_count', scope='project', default=0) + 1 %}
{{ setvar('scene_count', scene_count, scope='project') }}
本卷已完成 {{ scene_count }} 个场景。
```

### 调试

在主窗口菜单栏「调试」勾选「调试模式」，可在每个 LLM 调用前预览组装后的完整 messages（含宏替换与模板渲染后的最终文本），确认变量是否正确注入。

## 更新记录

### v0.2.13

- 修复可能导致闪退的bug
- 面板API配置持久化

### v0.2.12

- 优化用户输入栏
- 续写配置区世界书改为可多选下拉

### v0.2.11

- 新增「文风档案提取」功能
- 端点配置新增自定义请求扩展
- 修复上下文提取max_tokens过小的问题

### v0.2.10

- 删除设置中遗留提取上下文设置
- 新增流程插件管理器，可导入自定义流程，详见FLOW_PLUGIN_GUIDE.md
- 调试功能新增发送模型选择
- 默认预设新增章节名格式
- 新增「写作模式」流程插件：3 阶段（写作要素分析→写作要素深化→单章生成）
- 修复了gemini，grok模型可能发生的一些bug

### v0.2.9

- 流程端点配置新增模型下拉选择
- 预设思维链预设改成低、中、高三档

### v0.2.8

- API 端点新增「启用模型」多选，续写面板切换端点时仅显示已启用模型
- 取消上下文强制提取，改为合并提示对话框，允许「不提取直接续写」（降级为空串/占位）
- 默认破限调整

### v0.2.7

- 新增流程破限配置
- 更新默认预设
- 新增默认 `JailbreakProvider` 服务、`jailbreak_custom_dialog` 对话框、6 个 `jb_*.txt` 模板文件

### v0.2.6

- 新增「重写当前章节」模式
- 优化重写分析提示词

### v0.2.5

- 新增审计维度
- 更新默认预设
- 单章审计新增维度

### v0.2.4

- 新增「自定义设定/审计必查项」：AI 结构化为"要求+审计向"两字段，项目全局共享，注入续写与审计全链路，未满足 high 项一票否决
- 新增「输出栏高亮」（4 色+备注+持久化）、「主角形象提取独立按钮」、API 端点思考强度（7 档）、流程端点配置（7 流程独立选端点）
- 主角形象提取结果落盘到 chapters 表；编辑器新增可见保存按钮
- 修复编辑保存端点闪退、端点无法编辑、章节保存报错与正文丢失、世界观序列化失败、session 泄漏与跨线程违规

### v0.2.3

- 批次提取失败自动重试 1 次（温度归零）；品牌名称统一为「赓笔/GengBi」，默认正则前缀 NF-→GB-

### v0.2.2

- 世界书/正则条目列表新增内联勾选开关；`ContextEntry` 新增 `enabled` 字段，与 ST `disable` 双向映射

### v0.2.1

- 上下文提取体系重构：世界观底层 7 维度提取、主角形象 8 维度一致性、`protagonist_behavior` 维度
- 单章续写改为「提升为章节」模型 + 新增审计与修正功能
- 章节切换状态保留（后台继续 + UI 按章保留）
- 卷续写三项增强：强制修改流程、阶段产物阅览、动态前文修复
- 默认预设 4→16 条分层模块化 + 4 条核心正则；README 新增「变量与宏调用教程」
- 修复 save_project/save_chapter 级联删除、章节列表为空、世界观提取死锁等多项关键 bug

### v0.2.0

- 新增「卷级多章节续写」模式（深度分析/卷大纲/审计/逐章写作-验证-修订），移除旧多阶段 Agent 模式
- 新增 token 切分增量分析、推进速度、多轮审计、调试模式

### v0.1.0

- 初始版本：单次续写、上下文提取、SillyTavern 兼容、三阶段提示词组装、正则/Jinja2/宏替换、Apple HIG 主题、本地优先存储

## 开源协议

本项目采用 [MIT 协议](LICENSE)。

## 免责声明

赓笔是辅助续写工具，生成内容由你所配置的 LLM 服务商处理并返回。使用者应自行承担生成内容的使用与发布责任，并遵守所使用 LLM 服务商的服务条款与当地法律法规。本工具不存储、不审核、不对生成内容负责。
