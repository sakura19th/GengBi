# 赓笔 (GengBi)

> 古意盎然的小说续写桌面工具——辅助创作者在既有故事之后接续新篇，而非替代写作。

**当前版本：v0.2.3**

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
# 当前平台打包（生成 dist/赓笔 可执行文件）
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

### 安全限制

- 自定义 `is_safe_attribute` 拒绝所有以 `_` 开头的属性
- `attr` 过滤器已禁用
- 仅 `WHITELIST_FUNCTION_NAMES` 中的函数可调用
- 递归超限或超时时跳过模板渲染，使用原始文本（记 ERROR 日志）
- 模板语法错误跳过渲染，使用原始文本（记 ERROR 日志）

### 调试

在主窗口菜单栏「调试」勾选「调试模式」，可在每个 LLM 调用前预览组装后的完整 messages（含宏替换与模板渲染后的最终文本），确认变量是否正确注入。

## 测试

```bash
# 运行全部测试
python -m pytest tests/ -q
```

部分 UI 测试依赖桌面环境，在无显示环境（如 CI/容器）下可按需排除：

```bash
python -m pytest tests/ -q --ignore=tests/test_m5_polish.py -k "not TestUIComponents"
```

## 更新记录

### v0.2.3

- 品牌名称统一：将文档与默认资源（预设/正则脚本名称）中残留的 NovelForge 描述统一为「赓笔 / GengBi」，与软件名称保持一致
- 默认正则脚本显示名前缀由 `NF-` 更改为 `GB-`（思维链隐藏/八股抹除/破折号规范/空行清理），内部 ID 保持不变以兼容已安装用户
- 默认预设名称由「NovelForge 默认预设」改为「赓笔 默认预设」，主提示名称同步更新

### v0.2.2

- 世界书/正则管理器条目列表新增内联勾选开关（参照预设），可单独点击启用/禁用，即时持久化
- `ContextEntry` 新增 `enabled` 字段，禁用条目不注入续写上下文；导入/导出 ST 世界书时与 `disable` 字段双向映射
- 正则列表移除 `[禁用]` 文本前缀改由复选框表达；默认正则按 `default_regex_scripts.json` 默认开关加载

### v0.2.1

本次合并 `feat-context-extraction-merge` 分支，聚焦上下文提取体系重构、单章续写模型重做、默认预设/正则量身定制与多项关键 bug 修复。

- 新增「世界观底层提取」：全文拆分分析提取 7 大维度 `WorldOntology` 固化到项目，token 拆分 + 增量更新 + 语义合并三大机制，可查看与注入全部生成环节
- 新增「主角形象一致性提取」：8 维度心理学档案 `ProtagonistProfile` 按章节缓存，growth_arc 反映弧光演变；大纲审计主角一致性一票否决、章节验证 critical 级问题一票否决
- 上下文提取新增 `protagonist_behavior` 维度（主角既有行为），剔除世界观底层内容（由 OntologyExtractor 独立负责）
- 单章续写改为「提升为章节」模型：续写作为待定候选仅在续写面板可见，接受即提升为独立章节插入当前章节之后，并新增删除续写功能
- 单章续写新增「审计与修正」功能：5 维度精简审计（含主角/世界观一致性必审项），流式输出审计报告，采纳后基于审计意见重写为新 swipe
- 章节切换状态保留：流式接收过程中切换章节再切回仍处于接收中状态（后台继续 + UI 按章保留），覆盖上下文提取/世界观提取/单章续写/审计采纳重写/卷续写全场景
- 卷续写三项增强：强制修改流程（审计通过仍强制 1 轮修改）、阶段产物阅览（细纲/初稿/审计/修订每阶段可查看完整内容）、动态前文修复（从中间续写不再误把全文末尾章节当作前文）
- 重写 `phase_verify.txt` 为 10 维度审计，`protagonist_consistency`/`worldview_consistency` 为必审项，summary 强制含两个固定标记段落
- 为赓笔量身定制默认预设：4 条 → 16 条分层模块化提示（系统基础/功能模块/文风互斥/推进互斥/增强/可选/Marker 层），含 `{{world_ontology}}`/`{{protagonist_profile}}` 宏引用与 XML 输出格式
- 新增 4 条核心默认正则（思维链隐藏/八股抹除/破折号规范/空行清理），首次运行自动注入 global.json
- 预设管理器新增「恢复默认预设」按钮，生成参数新增 `top_p`/`top_k`/`reasoning_effort` 三项
- 新增「变量与宏调用教程」章节（README），集中说明内置宏/ST 风格变量/Jinja2 白名单函数
- 修复 `save_project`/`save_chapter` 因 `INSERT OR REPLACE` + 外键级联导致章节/续写被清空的严重 bug（改用 UPSERT）
- 修复章节列表为空问题：`_row_to_project` 加 `len(row)` 防御、`_load_project` 解耦章节加载、`list_chapters` 检测磁盘有 `.txt` 但 DB 无行时自动从磁盘重建
- 修复世界观提取保存死锁（协程内 await 异步存储直连）与 projects 表 schema 缺失 `world_ontology`/`worldbook_id` 列
- 修复默认正则未注入引擎导致思维链标签泄漏到正文
- 修复深度分析切分模式下全文占位符仍注入完整全文导致超长
- 修复删除章节后其余章节正文被清空（reindex 改用 `update_chapter_index`）

### v0.2.0

- 移除"智能续写（多阶段 Agent）"模式（已被卷续写完全替代），续写模式精简为单次续写与卷续写两种
- 新增"卷级多章节续写"模式：一次产出 N 章，含前文深度分析、卷大纲规划、多维度审计、终稿大纲生成、逐章细纲与写作/验证/修订循环
- 新增 token 切分增量分析（默认 100k/块）、推进速度（缓速/中速/快速）、多轮审计（1-3 轮）
- 新增调试模式：可在各阶段 LLM 调用前预览并确认提示词
- 修复删除章节后其余章节正文被清空的问题（reindex 改用 `update_chapter_index`，不再用 `save_chapter`）
- 修复深度分析切分模式下全文占位符仍注入完整全文导致超长的问题
- 修复卷续写暂停点因残留 payload 跳过用户编辑的问题
- 优化续写控制面板布局（QSplitter 重排，模式面板撑满空间，用户输入框可拖动调高度）
- 优化卷模式隐藏右侧输出面板，将空间让给卷控制面板的产物查看器

### v0.1.0

- 初始版本：单次续写与多阶段 Agent 续写（前文分析 → 大纲 → 写作 → 验证 → 修订）
- 上下文提取与 Token 拆分增量提取
- SillyTavern 预设与世界书兼容
- 三阶段提示词组装（排序 → 深度注入 → Token 裁剪）
- 正则脚本引擎、Jinja2 模板、宏替换
- Apple HIG 风格主题（暗色/亮色/跟随系统）与自定义字体
- 本地优先存储，API Key 加密落盘

## 开源协议

本项目采用 [MIT 协议](LICENSE)。

## 免责声明

赓笔是辅助续写工具，生成内容由你所配置的 LLM 服务商处理并返回。使用者应自行承担生成内容的使用与发布责任，并遵守所使用 LLM 服务商的服务条款与当地法律法规。本工具不存储、不审核、不对生成内容负责。
