# 小说续写器 PRD（产品需求文档）

> **项目代号**：NovelForge
> **版本**：v1.0
> **日期**：2026-06-22
> **技术基线**：Python 3.11+ / PySide6 / Jinja2 / OpenAI-Compatible API
> **设计参考**：SillyTavern（提示词管线）、ST-Prompt-Template（动态模板）、JS-Slash-Runner（分层钩子）

---

## 1. 项目概述

### 1.1 项目目标

构建一个**本地桌面端 Python 小说续写器**，让用户能够：

1. 导入 `.txt` 格式的小说文本，按章节自动拆分并预览
2. 在任意章节处发起 AI 续写，以 OpenAI 兼容格式调用 LLM
3. 流式实时查看续写结果，可接受/重写/编辑
4. 按 SillyTavern 的提示词组装范式构建上下文（预设 + 顺序 + 深度注入 + 正则）
5. 支持 Jinja2 动态模板，在发送前执行模板逻辑、在接收后执行输出模板更新状态
6. 每次续写前自动分析前文，以世界书格式提取创作必备内容（人物/地点/事件/风格），注入上下文

### 1.2 与 SillyTavern 的关系

本工具**借鉴并兼容** SillyTavern 的核心概念，但面向"小说续写"场景做裁剪与增强：

| SillyTavern 概念 | 本工具对应 | 说明 |
|---|---|---|
| Chat（聊天消息） | Chapter（章节段落） | 章节是续写的基本单位，而非对话楼层 |
| Character Card（角色卡） | Novel Profile（小说档案） | 记录书名、作者风格、主角设定、世界观摘要 |
| Prompt Preset（预设） | Writing Preset（写作预设） | 直接兼容 ST 预设 JSON 格式，可导入 |
| Regex Script（正则脚本） | Regex Script（正则脚本） | 直接兼容 ST 正则 JSON 格式，可导入 |
| World Info / Lorebook | Auto Context Extract（自动上下文提取） | 不维护常驻设定库，而是每次续写前自动分析前文提取 |
| Prompt Manager | Prompt Manager Panel | 可视化管理预设中的提示词顺序与启用状态 |
| Macro `{{char}}`/`{{user}}` | `{{book}}`/`{{protagonist}}`/`{{chapter}}` 等 | 面向小说的宏集合 |
| EJS 模板（ST-Prompt-Template） | Jinja2 模板 | 双阶段处理：发送前执行 + 接收后执行 |

### 1.3 非目标（明确排除）

- **不做**多角色群聊（Group Chat）
- **不做**TTS/语音/图片生成
- **不做**向量数据库/RAG 检索（上下文提取用 LLM 分析而非向量检索）
- **不做**多用户/云端同步（纯本地单用户）
- **不做**完整的常驻世界书编辑器（改为自动提取）
- **不做**用户自定义 JS 脚本执行（Python 工具无 JS 运行时）

---

## 2. 目标用户与使用场景

### 2.1 目标用户

- 有一定 LLM 使用经验、熟悉 SillyTavern 或提示词工程的小说创作者
- 需要对长篇小说进行分段续写、保持上下文连贯性的写作者
- 希望复用已有 ST 预设/正则资源的用户

### 2.2 核心使用流程

```
导入 TXT → 自动拆章 → 预览章节 → 选定续写点 → [自动提取前文上下文]
    → [组装提示词：预设+模板+正则+提取内容] → 流式调用 LLM
    → 实时查看输出 → 接受/重写/编辑 → 续写内容追加到章节 → 继续
```

### 2.3 典型场景

1. **续写已有小说**：导入一本写到第 50 章的 TXT，在第 50 章末尾让 AI 续写第 51 章
2. **风格仿写**：导入某作者的小说作为前文样本，让 AI 按相同风格续写新章节
3. **分支创作**：在同一章节处生成多个续写版本（swipes），对比择优
4. **提示词调试**：用 Jinja2 模板动态控制续写指令（如根据章节类型调整叙事视角）

---

## 3. 核心功能清单

| 编号 | 功能 | 优先级 | 说明 |
|---|---|---|---|
| F-01 | TXT 导入与章节拆分 | P0 | 支持多种章节标题正则，可手动调整拆分 |
| F-02 | 章节预览与编辑 | P0 | 阅读、编辑章节正文，支持字数统计 |
| F-03 | AI 续写（流式） | P0 | OpenAI 兼容 API，SSE 流式实时输出 |
| F-04 | 续写结果管理 | P0 | 接受/重写/编辑/追加，支持多版本（swipes） |
| F-05 | 写作预设管理 | P0 | 兼容 ST 预设 JSON，可视化编辑 prompt_order |
| F-06 | 正则脚本管理 | P0 | 兼容 ST 正则 JSON，支持输入/输出双向处理 |
| F-07 | Jinja2 动态模板 | P0 | 双阶段执行（发送前+接收后），分层变量作用域 |
| F-08 | 自动上下文提取 | P0 | 续写前用 LLM 分析前文，世界书格式提取 |
| F-09 | 宏替换系统 | P0 | `{{book}}`/`{{protagonist}}`/`{{chapter}}` 等小说宏 |
| F-10 | API 连接配置 | P0 | 多 API 端点管理，支持密钥加密存储 |
| F-11 | 项目管理 | P1 | 多本小说独立项目，各自绑定预设/正则/提取配置 |
| F-12 | 导出功能 | P1 | 导出续写后的完整 TXT / 单章 Markdown |
| F-13 | Token 计数与预算 | P1 | 上下文 token 预算管理，历史从新到旧填充 |
| F-14 | 续写历史日志 | P2 | 记录每次续写的提示词、参数、输出，便于回溯 |
| F-15 | 主题与字体 | P2 | 暗色/亮色主题，正文字体可调 |

---

## 4. 系统架构

### 4.1 分层架构

```
┌─────────────────────────────────────────────────┐
│                   UI 层 (PySide6)                │
│  MainWindow · ChapterPanel · ContinuationPanel  │
│  PresetManager · RegexManager · TemplateEditor  │
│  ContextExtractViewer · SettingsDialog           │
├─────────────────────────────────────────────────┤
│                 业务逻辑层                        │
│  NovelService · ContinuationService             │
│  PromptAssembler · ContextExtractor             │
│  TemplateEngine · RegexEngine · MacroEngine     │
├─────────────────────────────────────────────────┤
│                  服务层                          │
│  LLMClient(流式) · Tokenizer · ConfigStore      │
│  ProjectStore · PresetImporter · RegexImporter  │
├─────────────────────────────────────────────────┤
│                  数据层                          │
│  SQLite(项目元数据) · JSON(预设/正则/配置)        │
│  文件系统(TXT原文/续写产出)                       │
└─────────────────────────────────────────────────┘
```

### 4.2 核心模块职责

| 模块 | 职责 | 关键接口 |
|---|---|---|
| `NovelService` | TXT 导入、章节拆分、章节CRUD | `import_txt()`, `split_chapters()`, `get_chapter()` |
| `PromptAssembler` | 按 ST 范式组装最终 messages 数组 | `assemble(chapter, preset, context) -> messages` |
| `ContextExtractor` | 续写前分析前文，提取世界书格式条目 | `extract(chapters, config) -> list[ContextEntry]` |
| `TemplateEngine` | Jinja2 双阶段模板执行 | `render_pre_send(text)`, `render_post_receive(text)` |
| `RegexEngine` | ST 兼容正则脚本应用 | `apply(text, placement, depth) -> text` |
| `MacroEngine` | 宏替换 `{{book}}` 等 | `substitute(text, context) -> text` |
| `LLMClient` | OpenAI 兼容流式调用 | `stream(messages, config) -> AsyncGenerator[str]` |
| `ContinuationService` | 编排续写全流程 | `continue_at(chapter_id, options) -> Result` |
| `PresetImporter` | 导入/导出 ST 预设 JSON | `import_st_preset(json) -> Preset` |
| `RegexImporter` | 导入/导出 ST 正则 JSON | `import_st_regex(json) -> list[RegexScript]` |

### 4.3 数据流（续写请求）

```
用户点击"续写"
    │
    ▼
ContinuationService.continue_at(chapter_id)
    │
    ├─► 1. NovelService 获取章节正文 + 前N章历史
    │
    ├─► 2. ContextExtractor.extract(前文) 
    │       └─ LLM 分析 → 人物/地点/事件/风格条目
    │
    ├─► 3. PromptAssembler.assemble(chapter, preset, context_entries)
    │       ├─ 加载预设 prompts + prompt_order
    │       ├─ MacroEngine 替换 {{book}} 等宏
    │       ├─ TemplateEngine.render_pre_send() 执行 Jinja2 模板
    │       ├─ RegexEngine.apply(text, USER_INPUT) 应用输入正则
    │       ├─ 按 prompt_order 排列，marker 占位符替换为实际内容
    │       ├─ 深度注入：ABSOLUTE 提示按 injection_depth 插入历史
    │       └─ Token 预算裁剪：历史从新到旧填充
    │
    ├─► 4. LLMClient.stream(messages, config)
    │       └─ SSE 解析 → 逐 chunk yield
    │
    ├─► 5. UI 实时显示流式输出
    │
    ├─► 6. 流结束后
    │       ├─ RegexEngine.apply(output, AI_OUTPUT) 应用输出正则
    │       ├─ TemplateEngine.render_post_receive() 执行输出模板
    │       └─ 存为 swipe 版本，等待用户接受/重写
    │
    └─► 7. 用户接受 → 追加到章节正文 → 保存
```

---

## 5. 数据结构设计

### 5.1 项目（Project）

```json
{
  "id": "uuid",
  "name": "我的小说",
  "created_at": "2026-06-22T10:00:00",
  "updated_at": "2026-06-22T12:00:00",
  "source_file": "/path/to/novel.txt",
  "novel_profile": {
    "title": "书名",
    "author": "原作者",
    "protagonist": "主角名",
    "synopsis": "一句话简介",
    "world_setting": "世界观摘要",
    "writing_style": "写作风格描述"
  },
  "preset_id": "uuid-of-bound-preset",
  "regex_script_ids": ["uuid1", "uuid2"],
  "extract_config": {
    "lookback_chapters": 5,
    "extract_categories": ["characters", "locations", "events", "style"],
    "max_entries_per_category": 10,
    "extractor_model": "gpt-4o-mini",
    "extractor_prompt_override": null
  },
  "chapter_split_rule": {
    "pattern": "^第[一二三四五六七八九十百千零\\d]+[章节回卷]",
    "include_title_in_content": true,
    "manual_overrides": []
  }
}
```

### 5.2 章节（Chapter）

```json
{
  "id": "uuid",
  "project_id": "uuid",
  "index": 50,
  "title": "第五十章 风起云涌",
  "content": "章节正文...",
  "word_count": 3200,
  "is_original": true,
  "continuations": [
    {
      "id": "uuid",
      "created_at": "2026-06-22T11:00:00",
      "content": "AI续写的内容...",
      "model": "gpt-4o",
      "is_accepted": false,
      "extracted_context_snapshot": [],
      "prompt_snapshot": {}
    }
  ],
  "metadata": {
    "notes": "用户备注",
    "tags": []
  }
}
```

### 5.3 写作预设（WritingPreset）—— 兼容 ST 格式

直接采用 SillyTavern 的预设 JSON 结构，确保可互导：

```json
{
  "id": "uuid",
  "name": "我的写作预设",
  "chat_completion_source": "openai",
  "openai_model": "gpt-4o",
  "temperature": 0.8,
  "openai_max_context": 128000,
  "openai_max_tokens": 4000,
  "stream_openai": true,
  "freq_penalty": 0,
  "pres_penalty": 0,
  "top_p": 1,

  "prompts": [
    {
      "identifier": "main",
      "name": "主提示",
      "role": "system",
      "content": "你是一位专业的小说续写助手。请根据以下前文内容，以相同的风格和叙事视角续写下一章。\n\n书名：{{book}}\n主角：{{protagonist}}\n当前章节：{{chapter_title}}",
      "system_prompt": true,
      "marker": false
    },
    {
      "identifier": "worldInfoBefore",
      "name": "前文上下文（前）",
      "system_prompt": true,
      "marker": true
    },
    {
      "identifier": "novelProfile",
      "name": "小说档案",
      "role": "system",
      "content": "{{novel_profile}}",
      "system_prompt": false,
      "marker": false
    },
    {
      "identifier": "chatHistory",
      "name": "前文章节",
      "system_prompt": true,
      "marker": true
    },
    {
      "identifier": "continuationInstruction",
      "name": "续写指令",
      "role": "user",
      "content": "请续写下一章，约{{target_words}}字。保持前文的叙事节奏和人物性格。",
      "injection_position": 0,
      "injection_depth": 4,
      "system_prompt": false,
      "marker": false
    },
    {
      "identifier": "jailbreak",
      "name": "后置指令",
      "role": "system",
      "content": "直接输出续写内容，不要添加任何解释或元描述。",
      "system_prompt": true,
      "marker": false
    }
  ],

  "prompt_order": [
    {
      "character_id": 100000,
      "order": [
        { "identifier": "main", "enabled": true },
        { "identifier": "worldInfoBefore", "enabled": true },
        { "identifier": "novelProfile", "enabled": true },
        { "identifier": "chatHistory", "enabled": true },
        { "identifier": "continuationInstruction", "enabled": true },
        { "identifier": "jailbreak", "enabled": true }
      ]
    }
  ]
}
```

**Prompt 条目完整字段**（对齐 ST `Prompt` 类）：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `identifier` | string | — | 唯一标识符 |
| `name` | string | — | 显示名称 |
| `role` | string | "system" | `system` / `user` / `assistant` |
| `content` | string | "" | 提示内容（支持宏与 Jinja2） |
| `system_prompt` | bool | false | 是否为系统核心提示（不可删除） |
| `marker` | bool | false | 是否为占位符标记 |
| `position` | string | "start" | 扩展提示相对位置 `start`/`end` |
| `injection_position` | int | 0 | `0=RELATIVE`（按 order 排列）/ `1=ABSOLUTE`（按深度注入历史） |
| `injection_depth` | int | 4 | ABSOLUTE 时注入到倒数第 N 条消息处 |
| `injection_order` | int | 100 | 同深度内的优先级（越大越优先） |
| `injection_trigger` | string[] | [] | 生成类型触发器 |
| `forbid_overrides` | bool | false | 禁止角色卡覆盖 |
| `extension` | bool | false | 是否由扩展添加 |

### 5.4 正则脚本（RegexScript）—— 兼容 ST 格式

```json
{
  "id": "uuid",
  "scriptName": "移除思考标签",
  "findRegex": "/<think>.*?<\\/think>/gs",
  "replaceString": "",
  "trimStrings": [],
  "placement": [2],
  "disabled": false,
  "markdownOnly": false,
  "promptOnly": false,
  "runOnEdit": false,
  "substituteRegex": 0,
  "minDepth": -1,
  "maxDepth": -1
}
```

**placement 枚举**（对齐 ST `regex_placement`）：

| 值 | 常量 | 说明 |
|---|---|---|
| 0 | MD_DISPLAY | （已废弃，不使用） |
| 1 | USER_INPUT | 用户输入（续写指令） |
| 2 | AI_OUTPUT | AI 输出（续写结果） |
| 3 | SLASH_COMMAND | 斜杠命令（本工具不使用） |
| 5 | WORLD_INFO | 世界信息（提取的上下文条目） |
| 6 | REASONING | 推理内容 |

**substituteRegex 枚举**：

| 值 | 常量 | 说明 |
|---|---|---|
| 0 | NONE | 不替换 |
| 1 | RAW | 原始替换（`{{book}}` → 书名） |
| 2 | ESCAPED | 转义替换（特殊字符转义为正则安全） |

**脚本作用域**（对齐 ST `SCRIPT_TYPES`，按优先级排序）：

| 值 | 常量 | 说明 |
|---|---|---|
| 0 | GLOBAL | 全局脚本（所有项目共享） |
| 1 | SCOPED | 项目绑定脚本 |
| 2 | PRESET | 预设绑定脚本 |

### 5.5 自动上下文提取条目（ContextEntry）

采用类世界书格式，但由 LLM 自动生成：

```json
{
  "uid": 0,
  "category": "characters",
  "key": ["林墨", "林公子"],
  "comment": "主角",
  "content": "林墨，25岁，修仙者，筑基期三层。性格沉稳但内心热血，擅长剑术。当前目标：寻找失踪的师父。与苏婉儿有婚约。",
  "order": 100,
  "position": "before",
  "probability": 100,
  "role": "system",
  "source_chapter_range": [45, 50],
  "extracted_at": "2026-06-22T11:00:00"
}
```

**category 分类**：

| 分类 | 说明 | 提取内容 |
|---|---|---|
| `characters` | 人物 | 姓名、年龄、身份、性格、关系、当前状态 |
| `locations` | 地点 | 地名、描述、关联事件 |
| `events` | 事件 | 近期关键事件、未解决的伏笔 |
| `style` | 风格 | 叙事视角、文风特征、常用修辞 |
| `plot_state` | 剧情状态 | 当前主线进展、悬念、下一步方向 |

**position 注入位置**（简化版 ST world_info_position）：

| 值 | 说明 |
|---|---|
| `before` | 注入到 chatHistory marker 之前（worldInfoBefore 位置） |
| `after` | 注入到 chatHistory marker 之后（worldInfoAfter 位置） |
| `at_depth` | 按 depth 注入到历史中 |

### 5.6 Jinja2 变量作用域

| 作用域 | 对应 ST-Prompt-Template | 持久化 | 说明 |
|---|---|---|---|
| `global` | global | 是 | 全局变量，所有项目共享 |
| `project` | local | 是 | 项目级变量，绑定当前小说 |
| `chapter` | message | 是 | 章节级变量，绑定特定章节 |
| `cache` | cache | 否 | 临时缓存变量，续写结束后清除 |

---

## 6. UI/UX 设计

### 6.1 主窗口布局

```
┌──────────────────────────────────────────────────────────────────┐
│ 菜单栏：文件 | 项目 | 预设 | 正则 | 模板 | 设置 | 帮助           │
├────────────┬───────────────────────────────┬─────────────────────┤
│            │                               │                     │
│  章节列表   │      章节预览/编辑区           │    续写控制面板      │
│            │                               │                     │
│ ▸ 第一章   │  第五十章 风起云涌             │  ┌─ 续写配置 ──────┐ │
│ ▸ 第二章   │                               │  │ 预设: [我的预设▼]│ │
│ ...        │  夜色如墨，林墨独立山崖之上...  │  │ 模型: [gpt-4o ▼]│ │
│ ▸ 第四十九章│                              │  │ 温度: ─●──── 0.8 │ │
│ ▶第五十章  │  [编辑] [预览]                 │  │ 字数: [3000]    │ │
│  └续写1    │                               │  │ ☑ 自动提取上下文 │ │
│  └续写2    │                               │  │ ☑ 流式输出      │ │
│ ▸ 第五十一章│                              │  └─────────────────┘ │
│   (待续写) │                               │                     │
│            │                               │  ┌─ 流式输出 ──────┐ │
│ [+导入TXT] │                               │  │                 │ │
│ [+新建章节] │                               │  │  AI续写中...    │ │
│            │                               │  │  苏婉儿踏月而...│ │
│            │                               │  │  █ (光标)       │ │
│            │                               │  │                 │ │
│            │                               │  └─────────────────┘ │
│            │                               │  [▶ 开始续写]        │
│            │                               │  [↻ 重写] [✓ 接受]   │
├────────────┴───────────────────────────────┴─────────────────────┤
│ 状态栏：字数 3200 | Token 8420/128000 | API: connected | 项目: xxx│
└──────────────────────────────────────────────────────────────────┘
```

### 6.2 面板说明

#### 6.2.1 章节列表面板（左侧）

- 树形结构显示所有章节，按 index 排序
- 当前选中章节高亮
- 已有续写的章节显示子节点（续写1、续写2...），标记已接受版本
- 底部按钮：`[+导入TXT]` `[+新建章节]`
- 右键菜单：重命名、删除、合并、拆分、导出
- 搜索框：按标题/内容搜索章节

#### 6.2.2 章节预览/编辑区（中间）

- Tab 切换：`[预览]` `[编辑]`
- 预览模式：只读渲染，支持字体/行距调整
- 编辑模式：纯文本编辑器，支持查找替换
- 顶部显示：章节标题、字数、是否原文/续写
- 底部：`[在此处续写]` 按钮（在章节末尾发起续写）

#### 6.2.3 续写控制面板（右侧）

分为三个折叠区域：

**A. 续写配置区**
- 预设选择下拉框（绑定到项目，可切换）
- 模型选择下拉框（从 API 获取模型列表）
- 温度滑块（0-2）
- 最大 token 输入框
- 目标字数输入框
- 复选框：`☑ 自动提取上下文` `☑ 流式输出` `☑ 应用正则`
- 高级参数折叠：top_p、frequency_penalty、presence_penalty

**B. 流式输出区**
- 实时显示 AI 输出文本（逐字流式）
- 输出过程中显示 token 计数和速度
- 底部按钮：`[⏸ 暂停]` `[⏹ 停止]`（流式过程中）
- 流结束后显示：`[↻ 重写]` `[✓ 接受并追加]` `[✎ 编辑后接受]`

**C. 上下文提取预览区**（可折叠）
- 显示本次续写自动提取的上下文条目
- 按分类分组：人物 / 地点 / 事件 / 风格 / 剧情状态
- 每条可展开查看内容、可手动编辑/禁用
- 显示提取耗时和 token 消耗

### 6.3 预设管理器（独立窗口）

```
┌─ 写作预设管理器 ─────────────────────────────────────┐
│ [+新建] [↓导入ST预设] [↑导出] [✎编辑] [🗑删除]      │
├──────────────┬──────────────────────────────────────┤
│ 预设列表      │  预设详情                              │
│              │                                       │
│ ▸ 默认预设    │  名称: [我的写作预设          ]        │
│ ▸ 我的预设    │  模型: [gpt-4o              ▼]        │
│ ▸ 仙侠风格    │  温度: ─●──── 0.8                    │
│ ▸ 悬疑风格    │  上下文: [128000]  最大输出: [4000]   │
│              │                                       │
│              │  ┌─ 提示词列表（可拖拽排序）─────────┐ │
│              │  │ ☑ main        system  [编辑]      │ │
│              │  │ ☑ worldInfoBefore [marker]        │ │
│              │  │ ☑ novelProfile  system  [编辑]    │ │
│              │  │ ☑ chatHistory  [marker]           │ │
│              │  │ ☑ continuation  user    [编辑]    │ │
│              │  │ ☑ jailbreak    system  [编辑]    │ │
│              │  │ [+ 添加提示词]                    │ │
│              │  └──────────────────────────────────┘ │
│              │                                       │
│              │  ┌─ 提示词编辑器 ───────────────────┐ │
│              │  │ 标识符: [continuationInstruction] │ │
│              │  │ 名称:   [续写指令]                │ │
│              │  │ 角色:   [user ▼]                  │ │
│              │  │ 注入:   [○ 相对位置] [● 绝对深度] │ │
│              │  │ 深度:   [4]  优先级: [100]        │ │
│              │  │ ┌─ 内容（支持 Jinja2 + 宏）─────┐ │ │
│              │  │ │ 请续写下一章，约{{target_words}}│ │ │
│              │  │ │ 字。保持前文的叙事节奏。       │ │ │
│              │  │ │                                │ │ │
│              │  │ └────────────────────────────────┘ │ │
│              │  │ [预览渲染结果]                     │ │
│              │  └──────────────────────────────────┘ │
└──────────────┴──────────────────────────────────────┘
```

### 6.4 正则脚本管理器（独立窗口）

```
┌─ 正则脚本管理器 ─────────────────────────────────────┐
│ 作用域: [全局 ▼]  [+新建] [↓导入ST正则] [↑导出]      │
├──────────────────────────────────────────────────────┤
│ ☑ 移除思考标签    AI输出  /<think>.*?<\/think>/gs    │
│ ☑ 格式化对话      AI输出  /^([^:]+):/m → "$1道:"    │
│ ☐ 过滤元描述      AI输出  /\[.*?\]/g                 │
│ ☑ 清理续写指令    用户输入 /请续写.*/g               │
├──────────────────────────────────────────────────────┤
│ 脚本详情                                              │
│  名称: [移除思考标签]                                 │
│  查找: [/<think>.*?<\/think>/gs                     ] │
│  替换: [                                            ] │
│  裁剪: [                                            ] │
│  位置: ☑AI输出 ☐用户输入 ☐世界信息 ☐推理内容        │
│  深度: 最小[-1] 最大[-1]  (-1=不限)                  │
│  宏替换查找正则: [○不替换 ○原始 ●转义]               │
│  ☐ 仅Markdown  ☐ 仅提示  ☐ 编辑时运行               │
│  [测试] [保存]                                       │
└──────────────────────────────────────────────────────┘
```

### 6.5 模板编辑器（独立窗口）

```
┌─ Jinja2 模板编辑器 ──────────────────────────────────┐
│ 作用域: [全局 ▼] / [项目: 我的小说 ▼] / [章节 ▼]     │
├──────────────────────────────────────────────────────┤
│ 变量列表:                                             │
│  global.写作风格 = "仙侠古风"                         │
│  project.当前卷 = "第三卷"                            │
│  chapter.字数 = 3200                                  │
│  [+ 新建变量]                                         │
├──────────────────────┬───────────────────────────────┤
│ 模板编辑              │ 渲染预览                       │
│                      │                               │
│ {% if getvar('写作风  │ 你是一位专业的仙侠小说续写    │
│   格') == '仙侠古风'  │ 助手。                         │
│   %}                  │                               │
│ 你是一位专业的仙侠    │                               │
│ 小说续写助手。        │                               │
│ {% else %}            │                               │
│ 你是一位专业的小说    │                               │
│ 续写助手。            │                               │
│ {% endif %}          │                               │
│                      │                               │
│ 当前卷：{{ project.   │                               │
│   当前卷 }}           │                               │
├──────────────────────┴───────────────────────────────┤
│ 可用函数: getvar() setvar() get_chapter()             │
│           get_characters() get_context()              │
│           substitute_macros() regex_apply()           │
│ [测试渲染] [保存]                                     │
└──────────────────────────────────────────────────────┘
```

### 6.6 设置对话框

```
┌─ 设置 ───────────────────────────────────────────────┐
│ ▸ API 连接                                             │
│   端点列表:                                            │
│   ┌──────────────────────────────────────────────┐   │
│   │ ● 默认 (https://api.openai.com/v1)  [编辑]   │   │
│   │ ○ 本地 (http://localhost:11434/v1)  [编辑]   │   │
│   │ ○ 自定义                            [编辑]   │   │
│   │ [+ 添加端点]                                 │   │
│   └──────────────────────────────────────────────┘   │
│   API Key: [sk-**************] (AES加密存储)         │
│   默认模型: [gpt-4o ▼]                               │
│                                                       │
│ ▸ 外观                                                │
│   主题: [● 暗色 ○ 亮色 ○ 跟随系统]                    │
│   正文字体: [思源宋体 ▼] 大小: [16px ▼]               │
│   行距: [1.8 ▼]                                      │
│                                                       │
│ ▸ 续写                                                │
│   默认回看章节数: [5]                                  │
│   默认目标字数: [3000]                                 │
│   ☑ 续写后自动保存                                    │
│   ☑ 显示 token 计数                                   │
│                                                       │
│ ▸ 上下文提取                                          │
│   提取模型: [gpt-4o-mini ▼]                           │
│   ☑ 缓存提取结果（相同前文不重复提取）                 │
│   缓存有效期: [24小时]                                │
│                                                       │
│ ▸ 数据                                                │
│   项目存储路径: [/path/to/projects] [浏览]            │
│   [导出全部配置] [导入配置]                           │
└───────────────────────────────────────────────────────┘
```

---

## 7. 提示词组装管线

### 7.1 组装阶段（对齐 ST 三阶段管线）

#### 阶段 1：合并系统提示与预设顺序

```
输入：预设 prompts[] + prompt_order[] + 小说档案 + 提取的上下文条目

1. 构造系统提示条目（带固定 identifier）：
   - worldInfoBefore  ← 提取的上下文条目（position=before）
   - worldInfoAfter   ← 提取的上下文条目（position=after）
   - novelProfile     ← 小说档案（书名/主角/世界观/风格）
   - continuationInst ← 续写指令（用户配置）

2. 按 prompt_order[0].order 排列所有提示
   - marker 提示保留位置占位
   - 非 marker 提示按 order 顺序排列
   - enabled=false 的提示跳过

3. 对每个提示的 content 执行：
   a. MacroEngine.substitute() — 替换 {{book}} 等宏
   b. TemplateEngine.render_pre_send() — 执行 Jinja2 模板
   c. RegexEngine.apply(content, USER_INPUT) — 应用输入正则
```

#### 阶段 2：按 Token 预算填充上下文

```
1. 计算总预算：max_context - max_tokens - 系统提示占用
2. 按顺序添加非历史提示（main, worldInfoBefore, novelProfile...）
3. 到达 chatHistory marker 时，从新到旧填充前文章节：
   - 最新章节 → 最旧章节
   - 每章计算 token，预算不足则停止
   - 确保当前续写点所在章节一定被包含
4. 填充剩余非历史提示（continuationInstruction, jailbreak...）
```

#### 阶段 3：深度注入

```
对于 injection_position == ABSOLUTE 的提示：
  按 injection_depth 从小到大处理
  同深度内按 injection_order 从大到小排序
  同优先级内按角色排序：system → user → assistant
  将提示作为消息 splice 插入到历史数组的对应深度位置

深度语义：
  depth=0 → 在最后一条历史消息之后
  depth=1 → 倒数第1条消息之前
  depth=4 → 倒数第4条消息之前（ST 默认值）
```

### 7.2 最终 messages 数组示例

```json
[
  { "role": "system", "content": "你是一位专业的仙侠小说续写助手..." },
  { "role": "system", "content": "【人物】林墨，25岁，筑基期三层..." },
  { "role": "system", "content": "【地点】青云山，主角所在宗门..." },
  { "role": "system", "content": "【事件】林墨刚突破筑基三层，正准备下山寻找师父..." },
  { "role": "system", "content": "【风格】第三人称限知视角，古风文言色彩，多用四字短语..." },
  { "role": "system", "content": "小说档案：书名《青云录》，主角林墨..." },
  { "role": "user", "content": "第四十八章 ...\n夜色如墨..." },
  { "role": "user", "content": "第四十九章 ...\n林墨拔剑出鞘..." },
  { "role": "user", "content": "第五十章 风起云涌\n山崖之上，寒风凛冽..." },
  { "role": "system", "content": "请续写下一章，约3000字。保持前文的叙事节奏。", "_depth": 0 },
  { "role": "system", "content": "直接输出续写内容，不要添加任何解释。" }
]
```

### 7.3 宏替换系统

#### 内置宏

| 宏 | 替换值 | 示例 |
|---|---|---|
| `{{book}}` | 小说书名 | `青云录` |
| `{{protagonist}}` | 主角名 | `林墨` |
| `{{author}}` | 原作者 | `佚名` |
| `{{chapter_title}}` | 当前章节标题 | `第五十章 风起云涌` |
| `{{chapter_index}}` | 当前章节序号 | `50` |
| `{{chapter_content}}` | 当前章节正文 | `山崖之上...` |
| `{{target_words}}` | 目标续写字数 | `3000` |
| `{{novel_profile}}` | 完整小说档案 | `书名...主角...世界观...` |
| `{{synopsis}}` | 一句话简介 | `少年修仙寻师之路` |
| `{{world_setting}}` | 世界观摘要 | `修仙世界，筑基/金丹/元婴...` |
| `{{writing_style}}` | 写作风格 | `仙侠古风，第三人称` |
| `{{lookback_chapters}}` | 回看章节数 | `5` |
| `{{model}}` | 当前模型名 | `gpt-4o` |
| `{{date}}` | 当前日期 | `2026-06-22` |

#### 宏替换时机

```
原始 content
    │
    ▼
MacroEngine.substitute()     ← 替换 {{book}} 等
    │
    ▼
TemplateEngine.render_pre_send()  ← 执行 {% %} Jinja2 逻辑
    │
    ▼
RegexEngine.apply(placement=USER_INPUT)  ← 应用输入正则
    │
    ▼
最终 content
```

---

## 8. 续写工作流

### 8.1 续写流程详解

```
用户在章节预览区点击 [在此处续写]
    │
    ▼
1. ContinuationService.continue_at(chapter_id, options)
    │
    ├─ options 包含：preset_id, model, temperature, max_tokens,
    │              target_words, extract_context, stream, apply_regex
    │
    ▼
2. 加载数据
    │
    ├─ chapter = NovelService.get_chapter(chapter_id)
    ├─ history = NovelService.get_chapters_range(
    │       start=max(0, chapter.index - lookback),
    │       end=chapter.index
    │   )
    ├─ preset = PresetStore.get(project.preset_id)
    └─ regex_scripts = RegexStore.get(project.regex_script_ids + global_scripts)
    │
    ▼
3. 自动上下文提取（若启用）
    │
    ├─ ContextExtractor.extract(history, project.extract_config)
    │   │
    │   ├─ 构造提取提示词（要求 LLM 以 JSON 格式输出条目）
    │   ├─ 调用 extractor_model（非流式）
    │   ├─ 解析 JSON → list[ContextEntry]
    │   └─ 缓存结果（key = hash(前文内容)）
    │
    └─ context_entries = 结果列表
    │
    ▼
4. 组装提示词
    │
    ├─ PromptAssembler.assemble(chapter, history, preset, context_entries, options)
    │   │
    │   ├─ 阶段1：合并预设顺序 + 系统提示
    │   ├─ 阶段2：Token 预算填充历史
    │   ├─ 阶段3：深度注入
    │   └─ 返回 messages: list[dict]
    │
    ▼
5. 流式调用 LLM
    │
    ├─ LLMClient.stream(messages, api_config, model, temperature, max_tokens)
    │   │
    │   ├─ POST {endpoint}/chat/completions
    │   │   Body: { model, messages, temperature, max_tokens, stream: true, ... }
    │   ├─ 解析 SSE 流：data: {choices:[{delta:{content:"..."}}]}
    │   └─ yield 每个 chunk 的增量文本
    │
    ├─ UI 实时追加显示
    │
    └─ 流结束 → 完整文本
    │
    ▼
6. 后处理
    │
    ├─ RegexEngine.apply(output, placement=AI_OUTPUT)
    │   └─ 应用所有 placement 包含 AI_OUTPUT 的正则脚本
    │
    ├─ TemplateEngine.render_post_receive(output)
    │   └─ 执行输出中的 Jinja2 模板（更新变量）
    │
    └─ processed_output = 最终文本
    │
    ▼
7. 存储为 swipe 版本
    │
    ├─ chapter.continuations.append({
    │       content: processed_output,
    │       model: model,
    │       extracted_context_snapshot: context_entries,
    │       prompt_snapshot: messages,
    │       is_accepted: false
    │   })
    │
    └─ UI 显示 [↻ 重写] [✓ 接受] [✎ 编辑后接受]
    │
    ▼
8. 用户接受
    │
    ├─ 标记 is_accepted = true
    ├─ 将续写内容追加到章节 content（或作为新章节）
    └─ NovelService.save_chapter(chapter)
```

### 8.2 多版本（Swipes）机制

- 每次续写生成一个 swipe，存储在 `chapter.continuations[]`
- 同一章节可有多个 swipe，用户对比择优
- 只有一个 swipe 可被 `is_accepted = true`
- 接受后内容追加到章节正文，或作为新章节插入
- 重写 = 生成新 swipe（可调整参数）

### 8.3 暂停/停止

- 流式过程中可点击 `[⏸ 暂停]`：暂停 UI 显示（不中断 API 请求）
- 点击 `[⏹ 停止]`：中断 API 请求（abort fetch），保留已接收的部分文本作为 swipe

---

## 9. 自动上下文提取系统

### 9.1 设计理念

本工具**不维护常驻世界书**，而是在每次续写前**自动分析前文**，以世界书格式提取创作必备内容。这解决了长篇小说续写时"AI 忘记前文设定"的问题，同时免去用户手动维护设定库的负担。

### 9.2 提取流程

```
输入：前 N 章正文（默认 5 章）+ 小说档案
    │
    ▼
1. 构造提取提示词
    │
    │  系统提示：
    │  "你是一位小说分析助手。请分析以下小说章节，提取对续写最关键的信息。
    │   按以下分类输出 JSON 数组，每个条目包含 category, key, content, position 字段。
    │   
    │   分类：
    │   - characters: 出场人物的当前状态（姓名、身份、性格、关系、当前目标）
    │   - locations: 重要地点及其关联
    │   - events: 近期关键事件和未解决的伏笔
    │   - style: 叙事视角、文风特征、常用修辞手法
    │   - plot_state: 当前主线进展、悬念、可能的下一步方向
    │   
    │   要求：
    │   - 每个分类最多 {{max_entries}} 条
    │   - content 简洁准确，每条不超过 200 字
    │   - 只提取对续写有用的信息，忽略无关细节
    │   - position: before（背景信息）或 after（即时状态）"
    │
    │  用户消息：前文章节正文
    │
    ▼
2. 调用提取模型（非流式，用低成本模型如 gpt-4o-mini）
    │
    ▼
3. 解析 JSON → list[ContextEntry]
    │
    ├─ 容错：JSON 解析失败时尝试修复（去除 markdown 代码块标记等）
    ├─ 校验：必填字段检查，content 长度截断
    └─ 排序：按 category 分组，组内按 order 排序
    │
    ▼
4. 缓存结果
    │
    ├─ key = hash(前文内容 + 提取配置)
    ├─ 有效期内重复续写不重新提取
    └─ 用户可手动强制重新提取
    │
    ▼
5. 注入提示词
    │
    ├─ position=before 的条目 → worldInfoBefore marker 位置
    ├─ position=after 的条目 → worldInfoAfter marker 位置
    └─ position=at_depth 的条目 → 按 depth 注入历史
```

### 9.3 提取提示词模板（可自定义）

```
你是一位专业的小说分析助手。请仔细阅读以下小说章节，提取对续写最关键的信息。

## 输出格式

请输出一个 JSON 数组，每个元素格式如下：

```json
{
  "category": "characters|locations|events|style|plot_state",
  "key": ["关键词1", "关键词2"],
  "content": "条目内容，简洁准确，不超过200字",
  "position": "before|after",
  "comment": "简短备注"
}
```

## 分类说明

- **characters**: 出场人物的当前状态。包括姓名、身份、性格特征、人际关系、当前目标和处境。
- **locations**: 重要地点及其描述、关联事件。
- **events**: 近期发生的关键事件、未解决的伏笔和悬念。
- **style**: 叙事视角（第一/第三人称、限知/全知）、文风特征、常用修辞手法、语言风格。
- **plot_state**: 当前主线进展、各方势力动态、可能的下一步发展方向。

## 要求

1. 每个分类最多 {{max_entries}} 条
2. 只提取对续写有用的信息，忽略无关细节
3. content 必须简洁准确，每条不超过 200 字
4. position="before" 适用于背景设定，position="after" 适用于即时状态
5. 确保输出是合法的 JSON 数组

## 小说档案

书名：{{book}}
主角：{{protagonist}}
世界观：{{world_setting}}
写作风格：{{writing_style}}

## 前文章节

{{chapters_text}}
```

### 9.4 用户可干预

- 提取结果在 UI 的"上下文提取预览区"显示
- 用户可手动编辑任意条目的 content
- 用户可禁用某条目（本次续写不注入）
- 用户可手动添加条目
- 用户可点击 `[强制重新提取]` 跳过缓存

---

## 10. Jinja2 模板引擎

### 10.1 设计理念

对齐 ST-Prompt-Template 的 EJS 双阶段处理能力，使用 Python 的 Jinja2 引擎实现：

- **发送前执行**：扫描提示词内容中的 `{% %}` / `{{ }}`，执行模板逻辑，用结果替换
- **接收后执行**：扫描 AI 输出中的 `{% %}` / `{{ }}`，执行模板逻辑（可更新变量），用结果替换

### 10.2 Jinja2 语法映射

| ST-Prompt-Template (EJS) | 本工具 (Jinja2) | 说明 |
|---|---|---|
| `<% if (x) { %>` | `{% if x %}` | 条件 |
| `<% } else { %>` | `{% else %}` | else |
| `<% } %>` | `{% endif %}` | 结束 |
| `<%- getvar('x') %>` | `{{ getvar('x') }}` | 输出变量 |
| `<% for (i of arr) { %>` | `{% for i in arr %}` | 循环 |
| `<% } %>` | `{% endfor %}` | 结束循环 |
| `<% setvar('x', 1) %>` | `{{ setvar('x', 1) }}` | 设置变量 |
| `<%# 注释 %>` | `{# 注释 #}` | 注释 |

### 10.3 模板内可用函数

```python
# 变量读写
getvar(name, default=None, scope='chapter') -> Any
setvar(name, value, scope='chapter') -> None
hasvar(name, scope='chapter') -> bool
delvar(name, scope='chapter') -> None

# 章节访问
get_chapter(index) -> dict          # 获取指定章节
get_chapters(start, end) -> list    # 获取章节范围
get_current_chapter() -> dict       # 获取当前章节
get_chapter_count() -> int          # 总章节数

# 小说档案
get_book() -> str                   # 书名
get_protagonist() -> str            # 主角名
get_novel_profile() -> dict         # 完整档案
get_writing_style() -> str          # 写作风格

# 上下文提取
get_context_entries(category=None) -> list[dict]  # 获取提取的条目

# 正则
regex_apply(text, placement='ai_output') -> str   # 应用正则

# 宏
substitute_macros(text) -> str      # 执行宏替换

# 工具
now() -> str                        # 当前时间
word_count(text) -> int             # 字数统计
truncate(text, length) -> str       # 截断
```

### 10.4 模板示例

**示例 1：根据章节序号调整指令**

```jinja2
{% if chapter_index > 40 %}
你正在续写一部已有 {{ chapter_index }} 章的长篇小说。
请确保续写内容与前文保持一致，不要引入与前文矛盾的内容。
{% else %}
你正在续写一部小说的第 {{ chapter_index }} 章。
{% endif %}

当前章节标题：{{ chapter_title }}
目标续写字数：{{ target_words }} 字
```

**示例 2：根据提取的人物动态生成指令**

```jinja2
请续写下一章。以下是当前关键人物状态：

{% for entry in get_context_entries('characters') %}
- {{ entry.key[0] }}: {{ entry.content }}
{% endfor %}

{% set plot = get_context_entries('plot_state') %}
{% if plot %}
当前剧情进展：
{% for p in plot %}
- {{ p.content }}
{% endfor %}
{% endif %}
```

**示例 3：接收后执行（LLM 输出中嵌入模板）**

AI 输出：
```
{% setvar('current_chapter_mood', '紧张') %}
苏婉儿踏月而来，剑光如匹练般划破夜空...
```

用户看到：
```
苏婉儿踏月而来，剑光如匹练般划破夜空...
```

同时 `chapter.current_chapter_mood` 被设置为 `"紧张"`，后续续写可引用。

### 10.5 安全沙箱

- 使用 Jinja2 的 `SandboxedEnvironment`，禁止访问危险属性
- 模板内只能调用白名单函数
- 禁止 `import`、文件 IO、子进程
- 模板执行超时 5 秒，防止死循环

---

## 11. 正则脚本引擎

### 11.1 应用流程（对齐 ST regex-engine.js）

```
getRegexedString(rawString, placement, { depth, isPrompt, isEdit })
    │
    ▼
1. 获取所有允许的脚本
    │
    ├─ 按优先级排序：GLOBAL(0) → SCOPED(1) → PRESET(2)
    ├─ 过滤 disabled=true
    ├─ 过滤 promptOnly=true 且当前非 prompt 场景
    ├─ 过滤 markdownOnly=true 且当前非 markdown 场景
    ├─ 过滤 runOnEdit=false 且当前是编辑场景
    └─ 过滤 depth 不在 [minDepth, maxDepth] 范围
    │
    ▼
2. 对每个脚本执行
    │
    ├─ 解析 findRegex 字符串为 Python re 对象
    │   ├─ 格式："/pattern/flags" 或 "pattern"
    │   ├─ flags: g→re.MULTILINE, s→re.DOTALL, i→re.IGNORECASE, m→re.MULTILINE
    │   └─ substituteRegex 设置决定是否对 pattern 做宏替换
    │
    ├─ 执行替换
    │   ├─ replaceString 支持 $1, $<name>, {{match}}
    │   ├─ 对每个匹配应用 trimStrings 裁剪
    │   └─ 替换后对结果再做一次宏替换
    │
    └─ 累加到结果字符串
    │
    ▼
3. 返回处理后的字符串
```

### 11.2 正则字符串解析

ST 格式的 `findRegex` 是 `/pattern/flags` 字符串，需要解析：

```python
def parse_regex(regex_str: str) -> tuple[str, int]:
    """解析 ST 格式的正则字符串 '/pattern/flags' → (pattern, flags)"""
    if regex_str.startswith('/') and regex_str.rfind('/') > 0:
        last_slash = regex_str.rfind('/')
        pattern = regex_str[1:last_slash]
        flags_str = regex_str[last_slash + 1:]
    else:
        pattern = regex_str
        flags_str = ''
    
    flags = 0
    if 'g' in flags_str: flags |= re.MULTILINE  # g 在 JS 是全局，Python 默认全局
    if 'i' in flags_str: flags |= re.IGNORECASE
    if 'm' in flags_str: flags |= re.MULTILINE
    if 's' in flags_str: flags |= re.DOTALL
    
    return pattern, flags
```

### 11.3 应用时机

| 时机 | placement | 说明 |
|---|---|---|
| 组装提示词时，对每个提示 content | USER_INPUT (1) | 修改发送给 LLM 的提示 |
| 续写结果后处理 | AI_OUTPUT (2) | 修改 AI 输出 |
| 上下文提取条目注入前 | WORLD_INFO (5) | 修改提取的条目内容 |
| 推理内容（若 API 返回） | REASONING (6) | 修改推理内容 |

---

## 12. 流式输出

### 12.1 SSE 流解析

对齐 ST 的 `sse-stream.js`，解析 OpenAI 兼容的 SSE 流：

```python
async def stream_chat_completion(
    endpoint: str,
    api_key: str,
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    **kwargs
) -> AsyncGenerator[str, None]:
    """
    流式调用 OpenAI 兼容 API，逐 chunk yield 增量文本。
    """
    url = f"{endpoint}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        **kwargs,
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.content:
                line = line.decode('utf-8').strip()
                if not line or not line.startswith('data: '):
                    continue
                data = line[6:]  # 去掉 "data: " 前缀
                if data == '[DONE]':
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk['choices'][0]['delta']
                    if 'content' in delta and delta['content']:
                        yield delta['content']
                    # 兼容推理内容（DeepSeek/xAI 等）
                    if 'reasoning_content' in delta and delta['reasoning_content']:
                        yield delta['reasoning_content']  # 或单独处理
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
```

### 12.2 UI 流式显示

- 使用 `QTimer` 或 `qasync` 将异步生成器桥接到 PySide6 事件循环
- 每收到一个 chunk，通过信号 `text_appended(str)` 追加到 QTextEdit
- 显示光标动画（█ 闪烁）
- 实时更新 token 计数和速度（tokens/s）
- 支持 `[⏹ 停止]` 中断（通过取消 asyncio.Task）

### 12.3 异步桥接方案

```python
class ContinuationWorker(QThread):
    chunk_received = Signal(str)
    finished = Signal(str)      # 完整文本
    error = Signal(str)
    token_count = Signal(int)
    
    def __init__(self, messages, api_config, ...):
        super().__init__()
        self.messages = messages
        self.api_config = api_config
        self._stop = False
    
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            full_text = loop.run_until_complete(self._stream())
            self.finished.emit(full_text)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            loop.close()
    
    async def _stream(self):
        full = ""
        async for chunk in stream_chat_completion(...):
            if self._stop:
                break
            full += chunk
            self.chunk_received.emit(chunk)
        return full
    
    def stop(self):
        self._stop = True
```

---

## 13. ST 资源导入

### 13.1 预设导入

```python
def import_st_preset(json_path: str) -> WritingPreset:
    """
    导入 SillyTavern 预设 JSON，转换为本工具的 WritingPreset。
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        st_preset = json.load(f)
    
    return WritingPreset(
        id=str(uuid4()),
        name=st_preset.get('name', '导入的预设'),
        chat_completion_source=st_preset.get('chat_completion_source', 'openai'),
        openai_model=st_preset.get('openai_model', ''),
        temperature=st_preset.get('temperature', 0.8),
        openai_max_context=st_preset.get('openai_max_context', 128000),
        openai_max_tokens=st_preset.get('openai_max_tokens', 4000),
        stream_openai=st_preset.get('stream_openai', True),
        prompts=st_preset.get('prompts', []),
        prompt_order=st_preset.get('prompt_order', []),
        # 保留原始字段以便导出时还原
        _raw_st_fields={k: v for k, v in st_preset.items() 
                        if k not in ['prompts', 'prompt_order']}
    )
```

**兼容性处理**：
- ST 预设中的 `character_id: 100000` 视为全局顺序
- 忽略 ST 特有但本工具不适用的字段（如 `names_behavior`、`send_if_empty`）
- 保留未识别字段，导出时原样写回

### 13.2 正则脚本导入

```python
def import_st_regex(json_path: str) -> list[RegexScript]:
    """
    导入 SillyTavern 正则脚本 JSON。
    支持单个脚本对象或脚本数组。
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    scripts = data if isinstance(data, list) else [data]
    result = []
    for s in scripts:
        result.append(RegexScript(
            id=s.get('id', str(uuid4())),
            scriptName=s.get('scriptName', '未命名'),
            findRegex=s.get('findRegex', ''),
            replaceString=s.get('replaceString', ''),
            trimStrings=s.get('trimStrings', []),
            placement=s.get('placement', [2]),
            disabled=s.get('disabled', False),
            markdownOnly=s.get('markdownOnly', False),
            promptOnly=s.get('promptOnly', False),
            runOnEdit=s.get('runOnEdit', False),
            substituteRegex=s.get('substituteRegex', 0),
            minDepth=s.get('minDepth', -1),
            maxDepth=s.get('maxDepth', -1),
        ))
    return result
```

### 13.3 导出

- 预设导出：将 WritingPreset 转回 ST JSON 格式，保留所有字段
- 正则导出：将 RegexScript 列表导出为 ST JSON 数组
- 导出的文件可直接在 SillyTavern 中导入使用

---

## 14. 配置与持久化

### 14.1 存储方案

| 数据 | 存储方式 | 位置 |
|---|---|---|
| 项目元数据 | SQLite | `~/.novelforge/projects.db` |
| 章节正文 | 文件系统 | `~/.novelforge/projects/{project_id}/chapters/{chapter_id}.txt` |
| 续写版本 | JSON 文件 | `~/.novelforge/projects/{project_id}/chapters/{chapter_id}/swipes/{swipe_id}.json` |
| 写作预设 | JSON 文件 | `~/.novelforge/presets/{preset_id}.json` |
| 正则脚本 | JSON 文件 | `~/.novelforge/regex/{scope}/{script_id}.json` |
| 全局配置 | JSON 文件 | `~/.novelforge/config.json` |
| 全局变量 | JSON 文件 | `~/.novelforge/variables/global.json` |
| 项目变量 | JSON 文件 | `~/.novelforge/projects/{project_id}/variables.json` |
| 上下文提取缓存 | SQLite | `~/.novelforge/cache.db` |

### 14.2 全局配置结构

```json
{
  "version": "1.0",
  "api_endpoints": [
    {
      "id": "uuid",
      "name": "默认",
      "base_url": "https://api.openai.com/v1",
      "api_key_encrypted": "AES加密后的密钥",
      "default_model": "gpt-4o"
    }
  ],
  "default_endpoint_id": "uuid",
  "appearance": {
    "theme": "dark",
    "font_family": "思源宋体",
    "font_size": 16,
    "line_height": 1.8
  },
  "continuation": {
    "default_lookback_chapters": 5,
    "default_target_words": 3000,
    "auto_save": true,
    "show_token_count": true
  },
  "context_extract": {
    "extractor_model": "gpt-4o-mini",
    "cache_enabled": true,
    "cache_ttl_hours": 24
  },
  "data": {
    "storage_path": "~/.novelforge"
  }
}
```

### 14.3 API Key 加密

- 使用 `cryptography.Fernet` 对称加密
- 密钥派生自机器指纹（MAC 地址 + 用户名），确保本机解密
- 加密后存储在配置 JSON 中，不明文落盘
- UI 显示为 `sk-****`，需点击显示才可见

---

## 15. 错误处理

### 15.1 错误分类与处理

| 错误类型 | 处理方式 | UI 表现 |
|---|---|---|
| API 连接失败 | 重试 3 次，间隔递增 | 状态栏红色提示 + Toast |
| API 认证失败 | 不重试，提示检查 Key | 弹窗提示 |
| API 限流 (429) | 等待 Retry-After 后重试 | Toast 提示等待中 |
| 流式中断 | 保留已接收文本作为 swipe | Toast 提示中断原因 |
| 上下文提取 JSON 解析失败 | 尝试修复，失败则跳过提取 | 黄色警告条 |
| 模板执行错误 | 跳过模板，使用原始文本 | 红色错误提示 |
| 正则编译错误 | 跳过该脚本，记录日志 | Toast 提示 |
| Token 超限 | 自动裁剪历史章节 | 状态栏提示裁剪了 N 章 |
| 文件读写错误 | 提示重试 | 弹窗提示 |

### 15.2 日志

- 日志文件：`~/.novelforge/logs/novelforge.log`
- 按天滚动，保留 7 天
- 级别：DEBUG / INFO / WARNING / ERROR
- UI 开发者面板可实时查看日志（折叠面板）

---

## 16. 非功能需求

### 16.1 性能

- TXT 导入 100 万字应在 3 秒内完成拆分
- 章节预览打开延迟 < 200ms
- 流式输出首 token 延迟取决于 API，UI 渲染延迟 < 50ms
- 上下文提取（5 章前文）应在 15 秒内完成
- 内存占用：加载 100 章小说 < 200MB

### 16.2 可靠性

- 自动保存：续写接受后立即保存，编辑章节 5 秒空闲后自动保存
- 崩溃恢复：异常退出后重新打开，恢复到最后一次自动保存状态
- 数据备份：每周自动备份项目数据到 `~/.novelforge/backups/`

### 16.3 可用性

- 所有操作支持键盘快捷键
- 章节列表支持搜索和过滤
- 预设/正则/模板支持搜索
- 暗色主题为默认（长时间写作护眼）
- 字体/行距可调，适配长时间阅读

### 16.4 兼容性

- 操作系统：Windows 10+ / macOS 11+ / Linux（Ubuntu 20.04+）
- Python：3.11+
- 打包：PyInstaller 打包为独立可执行文件
- API：兼容 OpenAI / Azure OpenAI / 本地 Ollama / LM Studio / vLLM / DeepSeek / 通义千问等

---

## 17. 技术选型

### 17.1 依赖清单

| 依赖 | 版本 | 用途 |
|---|---|---|
| `PySide6` | >=6.6 | UI 框架 |
| `aiohttp` | >=3.9 | 异步 HTTP / SSE 流式 |
| `jinja2` | >=3.1 | 模板引擎（SandboxedEnvironment） |
| `tiktoken` | >=0.6 | Token 计数（OpenAI 模型） |
| `cryptography` | >=42.0 | API Key 加密 |
| `regex` | >=2024.0 | 增强正则引擎（兼容 JS 正则特性） |
| `pydantic` | >=2.0 | 数据模型校验 |
| `aiosqlite` | >=0.19 | 异步 SQLite |
| `python-dateutil` | >=2.9 | 日期处理 |
| `qasync` | >=0.27 | PySide6 + asyncio 事件循环桥接 |

### 17.2 为什么选这些

- **PySide6**：LGPL 许可，控件丰富，QTextEdit 支持大文本，QThread 支持后台流式
- **aiohttp**：原生支持 SSE 流式读取，比 requests 更适合异步场景
- **Jinja2 SandboxedEnvironment**：原生沙箱，限制危险操作，对齐 ST-Prompt-Template 的安全模型
- **tiktoken**：OpenAI 官方 token 计数器，准确计算上下文预算
- **regex 库**（非 re）：支持 JS 正则的 `\p{Unicode}` 等特性，更好兼容 ST 正则
- **qasync**：让 PySide6 信号槽与 asyncio 协程无缝协作

---

## 18. 模块划分与文件结构

```
novelforge/
├── main.py                          # 入口
├── config.py                        # 全局配置加载
├── requirements.txt
│
├── ui/                              # UI 层
│   ├── main_window.py               # 主窗口
│   ├── panels/
│   │   ├── chapter_list_panel.py    # 章节列表面板
│   │   ├── chapter_view_panel.py    # 章节预览/编辑面板
│   │   ├── continuation_panel.py    # 续写控制面板
│   │   ├── context_extract_panel.py # 上下文提取预览面板
│   │   └── status_bar.py            # 状态栏
│   ├── dialogs/
│   │   ├── preset_manager.py        # 预设管理器
│   │   ├── regex_manager.py         # 正则脚本管理器
│   │   ├── template_editor.py       # 模板编辑器
│   │   ├── settings_dialog.py       # 设置对话框
│   │   ├── import_dialog.py         # 导入对话框
│   │   └── api_config_dialog.py     # API 配置对话框
│   ├── widgets/
│   │   ├── streaming_text_view.py   # 流式文本显示组件
│   │   ├── prompt_order_editor.py   # 提示词排序编辑器
│   │   ├── regex_tester.py          # 正则测试器
│   │   └── code_editor.py           # 代码编辑器（模板/正则）
│   └── workers/
│       ├── continuation_worker.py   # 续写后台线程
│       └── extract_worker.py        # 上下文提取后台线程
│
├── core/                            # 业务逻辑层
│   ├── novel_service.py             # 小说/章节管理
│   ├── continuation_service.py      # 续写流程编排
│   ├── prompt_assembler.py          # 提示词组装管线
│   ├── context_extractor.py         # 自动上下文提取
│   ├── template_engine.py           # Jinja2 模板引擎
│   ├── regex_engine.py              # 正则脚本引擎
│   ├── macro_engine.py              # 宏替换引擎
│   ├── token_budget.py              # Token 预算管理
│   └── variable_store.py            # 分层变量存储
│
├── services/                        # 服务层
│   ├── llm_client.py                # OpenAI 兼容流式客户端
│   ├── tokenizer.py                 # Token 计数
│   ├── preset_store.py              # 预设存储与导入导出
│   ├── regex_store.py               # 正则脚本存储与导入导出
│   ├── project_store.py             # 项目存储
│   ├── config_store.py              # 配置存储
│   └── cache_store.py               # 上下文提取缓存
│
├── models/                          # 数据模型
│   ├── project.py                   # Project / NovelProfile
│   ├── chapter.py                   # Chapter / Continuation
│   ├── preset.py                    # WritingPreset / Prompt
│   ├── regex_script.py              # RegexScript
│   ├── context_entry.py             # ContextEntry
│   └── config.py                    # AppConfig / ApiEndpoint
│
├── utils/
│   ├── crypto.py                    # AES 加密
│   ├── regex_parser.py              # ST 正则字符串解析
│   ├── text.py                      # 文本处理工具
│   └── logger.py                    # 日志配置
│
└── resources/                       # 资源文件
    ├── icons/
    ├── styles/
    │   ├── dark.qss
    │   └── light.qss
    └── defaults/
        ├── default_preset.json      # 默认写作预设
        └── extract_prompt.txt       # 默认提取提示词
```

---

## 19. 开发里程碑

### M1：基础框架（P0 核心闭环）

- 项目脚手架、PySide6 主窗口骨架
- TXT 导入与章节拆分（F-01）
- 章节预览与编辑（F-02）
- API 连接配置（F-10）
- LLM 流式调用（F-03）
- 基础续写流程（无预设/正则/模板，简单 messages 组装）
- 续写结果管理（F-04）

**交付标准**：能导入 TXT、预览章节、发起流式续写、接受结果

### M2：提示词管线（P0 ST 兼容）

- 写作预设管理 + ST 预设导入（F-05）
- 预设可视化编辑器（prompt_order 拖拽排序）
- 提示词组装管线（三阶段：合并→预算填充→深度注入）
- 宏替换系统（F-09）
- Token 计数与预算（F-13）

**交付标准**：能导入 ST 预设、按 prompt_order 组装提示词、宏替换正常工作

### M3：正则与模板（P0 高级能力）

- 正则脚本管理 + ST 正则导入（F-06）
- 正则引擎（输入/输出双向处理）
- Jinja2 模板引擎（F-07）
- 双阶段模板执行（发送前+接收后）
- 分层变量作用域（global/project/chapter）
- 模板编辑器 UI

**交付标准**：能导入 ST 正则、Jinja2 模板双阶段执行正常、变量持久化

### M4：自动上下文提取（P0 差异化能力）

- 上下文提取器（F-08）
- 提取提示词模板
- 提取结果缓存
- 上下文提取预览面板
- 用户干预（编辑/禁用/手动添加条目）

**交付标准**：续写前自动提取前文关键信息、以世界书格式注入提示词

### M5：完善与打磨（P1/P2）

- 项目管理（多本小说）（F-11）
- 导出功能（F-12）
- 续写历史日志（F-14）
- 主题与字体（F-15）
- 错误处理完善
- 性能优化
- PyInstaller 打包

**交付标准**：功能完整、错误处理健壮、可打包分发

---

## 20. 附录

### 20.1 默认写作预设（内置）

随软件附带一个默认预设 `default_preset.json`，包含：
- `main`：主提示（小说续写指令）
- `worldInfoBefore`：marker（上下文提取条目前置位）
- `novelProfile`：小说档案
- `chatHistory`：marker（前文章节）
- `continuationInstruction`：续写指令（字数/风格要求）
- `jailbreak`：后置指令（直接输出内容）

### 20.2 默认提取提示词

随软件附带默认的上下文提取提示词模板 `extract_prompt.txt`，用户可在设置中自定义覆盖。

### 20.3 快捷键

| 快捷键 | 功能 |
|---|---|
| `Ctrl+O` | 导入 TXT |
| `Ctrl+S` | 保存当前章节 |
| `Ctrl+Enter` | 在当前章节发起续写 |
| `Ctrl+R` | 重写（生成新 swipe） |
| `Ctrl+Shift+A` | 接受续写结果 |
| `Ctrl+Shift+E` | 编辑后接受 |
| `Ctrl+P` | 打开预设管理器 |
| `Ctrl+G` | 打开正则管理器 |
| `Ctrl+T` | 打开模板编辑器 |
| `Ctrl+,` | 打开设置 |
| `F5` | 强制重新提取上下文 |
| `Esc` | 停止流式输出 |

### 20.4 术语表

| 术语 | 说明 |
|---|---|
| Swipe | 同一续写点的多个版本，源自 SillyTavern |
| Marker | 预设中的占位符提示，标记特定内容插入位置 |
| 深度注入 | 将提示插入到历史消息的指定深度位置 |
| prompt_order | 预设中定义提示排列顺序的配置 |
| 双阶段模板 | 发送前执行模板 + 接收后执行输出模板 |
| 上下文提取 | 续写前自动分析前文，提取关键信息 |
| RELATIVE / ABSOLUTE | 提示注入位置模式：相对（按 order 排列）/ 绝对（按深度注入） |

---

**文档结束。**
