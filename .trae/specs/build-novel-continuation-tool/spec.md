# NovelForge 小说续写器 Spec

> **Change-ID**: build-novel-continuation-tool
> **版本**: v2.1（基于 PRD v1.0 多角度审阅后优化）
> **日期**: 2026-06-22
> **技术基线**: Python 3.11+ / PySide6 / Jinja2 / OpenAI-Compatible API
> **设计参考**: SillyTavern（提示词管线）、ST-Prompt-Template（动态模板）、JS-Slash-Runner（分层钩子）

---

## Why

长篇小说续写时，AI 容易遗忘前文设定、风格漂移、人物性格不一致。现有工具（如 SillyTavern）面向角色扮演对话场景，不直接适配小说续写工作流。需要一个本地桌面工具，能导入 TXT 小说、按章节拆分、在选定章节处用 ST 范式组装提示词、流式续写，并自动提取前文关键信息注入上下文。

## What Changes

- 新建 Python PySide6 桌面应用 NovelForge
- 实现 TXT 导入与章节拆分（支持手动调整拆分点）
- 实现三栏布局 UI（章节列表 / 预览编辑 / 续写控制），所有面板可折叠、可拖拽调宽
- 实现 OpenAI 兼容 API 流式续写（SSE 解析，含 UTF-8 增量解码、多行 data 拼接、error body 读取）
- 实现提示词组装管线（对齐 ST 三阶段：排序 → 深度注入 → Token 裁剪）
- 实现写作预设管理（兼容 ST 预设 JSON 导入导出）
- 实现正则脚本引擎（兼容 ST 正则 JSON，修正 flag 映射，明确替换字符串语法转换）
- 实现 Jinja2 沙箱模板引擎（双阶段执行，使用 ImmutableSandboxedEnvironment，子进程超时）
- 实现自动上下文提取（续写前 LLM 分析前文，世界书格式输出，分段缓存）
- 实现分层变量作用域（global / project / chapter / cache）
- 实现多版本（swipe）管理，含完整参数快照
- 实现 Token 计数（按模型族选择 tokenizer，非 OpenAI 模型 fallback 估算）

## Impact

- **新建项目**：无既有代码影响
- **依赖外部资源**：SillyTavern 预设 JSON、ST 正则 JSON（导入兼容）
- **运行环境**：用户本地机器，需 Python 3.11+ 或打包后可执行文件

---

## ADDED Requirements

### Requirement: 项目与章节管理

系统 SHALL 提供项目管理功能，每个项目对应一本小说，独立绑定预设、正则、提取配置。

#### Scenario: 导入 TXT 并自动拆分章节
- **WHEN** 用户选择一个 `.txt` 文件并点击导入
- **THEN** 系统使用项目配置的章节标题正则（默认 `^第[一二三四五六七八九十百千零\d]+[章节回卷]`）匹配拆分点
- **AND** 创建项目，将拆分后的章节存入文件系统（`~/.novelforge/projects/{project_id}/chapters/{chapter_id}.txt`）
- **AND** 项目元数据写入 SQLite
- **AND** 100 万字 TXT 应在 3 秒内完成拆分（不含 token 计数，token 按需计算并缓存）

#### Scenario: 手动调整章节拆分点
- **WHEN** 用户在章节编辑器中选中文本并右键选择"在此处拆分"
- **THEN** 当前章节在选中位置拆分为两个章节
- **AND** 后续章节 index 自动重排
- **AND** 原章节的 continuations 归属拆分后的前一个章节
- **AND** 操作支持 undo（Ctrl+Z）

#### Scenario: 合并章节
- **WHEN** 用户右键章节选择"与下一章合并"
- **THEN** 当前章节与下一章合并为一个章节
- **AND** 两章的 continuations 都归属合并后的章节
- **AND** 操作支持 undo

#### Scenario: 章节正文按需加载
- **WHEN** 用户在章节列表选中某章
- **THEN** 系统从文件系统加载该章节正文到内存
- **AND** 切换到其他章节时释放未修改章节的内存
- **AND** 500 章项目内存占用应 < 300MB

### Requirement: 章节预览与编辑

系统 SHALL 提供章节预览（只读）和编辑两种模式，支持 undo/redo。

#### Scenario: 编辑章节并自动保存
- **WHEN** 用户在编辑模式修改章节正文
- **THEN** 5 秒空闲后自动保存到文件系统
- **AND** 状态栏显示"已保存"或"未保存修改"
- **AND** 支持 Ctrl+Z / Ctrl+Y undo/redo

#### Scenario: 流式输出时锁定编辑
- **WHEN** 续写流式输出进行中
- **THEN** 章节编辑区切换为只读模式并显示"续写中"提示
- **AND** 用户无法修改章节正文直到续写结束或停止

### Requirement: OpenAI 兼容 API 流式续写

系统 SHALL 以 OpenAI 兼容格式调用 LLM，支持 SSE 流式实时输出。

#### Scenario: 流式续写成功
- **WHEN** 用户点击"开始续写"且 API 配置有效
- **THEN** 系统以 `stream: true` 发送 POST 请求到 `{endpoint}/chat/completions`
- **AND** 使用增量 UTF-8 解码器（`codecs.getincrementaldecoder('utf-8')`）解析响应流，避免多字节字符截断
- **AND** 正确拼接多行 `data:` 字段为完整事件
- **AND** 每个 chunk 的增量文本通过信号实时追加到 UI
- **AND** UI 更新使用 QTimer 节流（每 50ms 批量合并一次），避免高频更新卡顿
- **AND** 收到 `data: [DONE]` 时结束流

#### Scenario: API 错误时读取 error body
- **WHEN** API 返回 4xx/5xx 状态码
- **THEN** 系统不调用 `raise_for_status()`，而是读取完整 response body
- **AND** 解析 JSON 错误信息并显示给用户
- **AND** 不创建 swipe（区别于流式中断）

#### Scenario: 流式中断保留部分文本
- **WHEN** 流式过程中网络断开、用户点击停止、或 API 返回错误
- **THEN** 已接收的文本若 ≥ 100 字则存为 swipe，status 标记为 `interrupted`
- **AND** 已接收文本 < 100 字则丢弃，不存 swipe
- **AND** swipe 记录中断原因（`network_error` / `user_stopped` / `api_error`）

#### Scenario: 推理内容单独处理
- **WHEN** API 返回 `delta.reasoning_content`（DeepSeek/xAI 等）
- **THEN** 推理内容与正文内容分离存储
- **AND** UI 可折叠显示推理过程
- **AND** 推理内容不参与后续提示词组装

### Requirement: 提示词组装管线

系统 SHALL 按 SillyTavern 范式组装提示词，三阶段执行：**排序 → 深度注入 → Token 裁剪**。

#### Scenario: 三阶段组装提示词
- **WHEN** 发起续写请求
- **THEN** 阶段1（排序）：按 `prompt_order[0].order` 排列所有启用的提示，marker 保留占位
- **AND** 阶段2（深度注入）：对 `injection_position == ABSOLUTE(1)` 的提示，按 `injection_depth` 从小到大、同深度按 `injection_order` 从大到小、同优先级按 role（system→user→assistant）排序，splice 插入历史数组
- **AND** 阶段3（Token 裁剪）：计算总预算 = `max_context - max_tokens - 系统提示占用 - 注入提示占用`，从新到旧填充历史章节，预算不足时停止
- **AND** 注入提示视为不可裁剪项，其 token 占用从预算中扣除

#### Scenario: 深度注入 depth 语义
- **WHEN** 提示的 `injection_position == ABSOLUTE` 且 `injection_depth == N`
- **THEN** 该提示作为消息插入到历史数组的倒数第 N 条消息之前
- **AND** `depth=0` 表示插入到最后一条历史消息之后
- **AND** `depth=1` 表示插入到倒数第 1 条历史消息之前

#### Scenario: RELATIVE 与 ABSOLUTE 混合排序
- **WHEN** 预设中同时存在 RELATIVE（按 order 排列）和 ABSOLUTE（按深度注入）提示
- **THEN** RELATIVE 提示按 `prompt_order` 顺序排列在历史前后
- **AND** ABSOLUTE 提示从 RELATIVE 序列中移除，改为按深度注入到历史数组中
- **AND** 最终 messages 数组 = [前部 RELATIVE 提示] + [历史 + 注入提示] + [后部 RELATIVE 提示]

#### Scenario: 单章超限降级
- **WHEN** 当前续写点所在章节单独就超过 Token 预算（扣除系统提示和注入提示后）
- **THEN** 系统从章节末尾截取符合预算的文本（保留最新内容）
- **AND** 状态栏警告"当前章节过长，已截取末尾 N 字作为上下文"
- **AND** 若截取后仍不足 500 token，弹窗提示用户增大 `max_context` 或减小 `max_tokens`

#### Scenario: worldInfoBefore/After marker 注入规则
- **WHEN** 提示词组装到达 `worldInfoBefore` 或 `worldInfoAfter` marker
- **THEN** 系统收集所有 `position` 为 `before`（对应 worldInfoBefore）或 `after`（对应 worldInfoAfter）的 ContextEntry
- **AND** 所有条目内容（无论 role）用 `\n` 拼接为单一字符串
- **AND** 作为**单条 `system` role 消息**注入到 marker 位置（对齐 ST 行为）
- **AND** ContextEntry 的 `role` 字段在此场景下不生效（仅 at_depth 注入时生效）
- **AND** 若无任何条目，则跳过该 marker（不插入空消息）

### Requirement: 写作预设管理（ST 兼容）

系统 SHALL 兼容 SillyTavern 预设 JSON 格式，支持导入导出。

#### Scenario: 导入 ST 预设
- **WHEN** 用户选择一个 ST 预设 JSON 文件导入
- **THEN** 系统解析 JSON，提取 `prompts`、`prompt_order`、生成参数
- **AND** `prompt_order` 中 `character_id == 100000` 的条目视为全局顺序，取第一个匹配项
- **AND** 若有多个 `character_id`，忽略非 100000 的条目并记录日志
- **AND** 本工具创建预设时 `character_id` 统一使用 `100000`
- **AND** 未识别的字段保留在 `_raw_st_fields`，导出时原样写回

#### Scenario: Prompt 条目字段
- **WHEN** 定义或解析 Prompt 条目
- **THEN** 字段包括：`identifier`、`name`、`role`（system/user/assistant）、`content`、`system_prompt`、`marker`、`position`（`start`/`end`，对齐 ST 语义）、`injection_position`（0=RELATIVE, 1=ABSOLUTE）、`injection_depth`（默认 4）、`injection_order`（默认 100）、`forbid_overrides`、`extension`
- **AND** `position` 字段语义对齐 ST：`start` 表示相对 main 提示插入到前部，`end` 表示插入到后部（chatHistory 前后由 prompt_order 排列决定）
- **AND** 导入 ST 预设时 `position` 原样保留（ST 即用 `start`/`end`）
- **AND** 本工具内部统一使用 `start`/`end`，不引入 `before`/`after` 命名

#### Scenario: 可视化编辑 prompt_order
- **WHEN** 用户在预设管理器中编辑预设
- **THEN** 提示词列表支持拖拽排序
- **AND** 每个提示可勾选启用/禁用（对应 `enabled` 字段）
- **AND** marker 提示显示 `[marker]` 标记，不可删除
- **AND** `system_prompt: true` 的提示标记为系统提示，不可删除但可禁用

### Requirement: 正则脚本引擎（ST 兼容）

系统 SHALL 兼容 SillyTavern 正则脚本 JSON 格式，修正 flag 映射。

#### Scenario: RegexScript 数据结构
- **WHEN** 定义或解析正则脚本
- **THEN** 字段包括：`id`、`scriptName`、`findRegex`（`/pattern/flags` 格式）、`replaceString`、`trimStrings`（list[str]，替换前后裁剪指定字符串）、`placement`（list[int]，取值 USER_INPUT=1/AI_OUTPUT=2/WORLD_INFO=5）、`disabled`（bool）、`markdownOnly`（bool）、`promptOnly`（bool）、`runOnEdit`（bool）、`substituteRegex`（bool/int，允许替换后宏替换）、`minDepth`（int，过滤历史消息深度下限）、`maxDepth`（int，过滤历史消息深度上限）、`markupSafety`（bool）
- **AND** `minDepth`/`maxDepth` 语义：仅对 depth 范围内的历史消息应用该脚本（depth=0 为最新消息）
- **AND** 未识别字段保留在 `_raw_st_fields`

#### Scenario: 正则 flag 解析
- **WHEN** 解析 ST 格式的 `findRegex` 字符串（`/pattern/flags`）
- **THEN** 提取 pattern 和 flags 字符串
- **AND** flag 映射规则：`g`（global）不映射任何 Python flag（Python `re.sub` 默认全局替换）；`i` → `re.IGNORECASE`；`m` → `re.MULTILINE`；`s` → `re.DOTALL`；`u` → `re.UNICODE`（默认已启用）；`y`（sticky）忽略并记录日志
- **AND** 使用 `regex` 库（非 `re`）以支持 JS 正则特性如 `\p{Unicode}`、lookbehind

#### Scenario: 替换字符串语法转换
- **WHEN** 执行正则替换
- **THEN** `replaceString` 中的 `$1`、`$2` 转换为 Python 的 `\1`、`\2`
- **AND** `$<name>` 转换为 `\g<name>`
- **AND** `{{match}}` 转换为 `\g<0>`
- **AND** 替换后对结果再做一次宏替换（`substituteRegex` 配置允许时）

#### Scenario: 正则脚本应用时机
- **WHEN** 组装提示词时
- **THEN** 对每个提示 content 应用 `placement` 包含 `USER_INPUT(1)` 的脚本
- **WHEN** 续写结果后处理时
- **THEN** 对输出应用 `placement` 包含 `AI_OUTPUT(2)` 的脚本
- **WHEN** 上下文提取条目注入前
- **THEN** 对条目 content 应用 `placement` 包含 `WORLD_INFO(5)` 的脚本
- **AND** 多个脚本按优先级顺序执行：GLOBAL(0) → PRESET(2) → SCOPED(1)（顺序由 ST `SCRIPT_TYPES` 对象插入序决定，非数值大小），前一个输出是后一个输入
- **AND** UI 在脚本列表显示执行顺序编号

#### Scenario: 正则单独测试
- **WHEN** 用户在正则管理器点击"测试"
- **THEN** 弹出测试对话框，输入测试文本
- **AND** 只应用当前选中的单个正则脚本
- **AND** 显示匹配高亮和替换结果

### Requirement: Jinja2 沙箱模板引擎

系统 SHALL 使用 Jinja2 `ImmutableSandboxedEnvironment` 实现双阶段模板执行。

#### Scenario: 发送前模板执行
- **WHEN** 组装提示词时，对每个提示 content 执行模板渲染
- **THEN** 扫描 `{% %}` 和 `{{ }}` 块，在沙箱中执行
- **AND** 用执行结果替换原块
- **AND** 执行顺序：宏替换 → Jinja2 渲染 → 正则应用（三者不递归，模板内调用 `substitute_macros()` 视为显式二次替换）

#### Scenario: 接收后模板执行
- **WHEN** 续写流结束、正则后处理后
- **THEN** 扫描 AI 输出中的 `{% %}` 和 `{{ }}` 块
- **AND** 在沙箱中执行（可调用 `setvar` 更新变量）
- **AND** 用执行结果替换原块
- **AND** 最终显示给用户的文本不含 Jinja2 语法

#### Scenario: 沙箱安全配置
- **WHEN** 初始化 Jinja2 环境
- **THEN** 使用 `ImmutableSandboxedEnvironment`
- **AND** 自定义 `is_safe_attribute`：拒绝所有以 `_` 开头的属性
- **AND** 禁用 `attr` 过滤器
- **AND** 白名单函数：`getvar`、`setvar`、`hasvar`、`delvar`、`get_chapter`、`get_chapters`、`get_current_chapter`、`get_chapter_count`、`get_book`、`get_protagonist`、`get_novel_profile`、`get_writing_style`、`get_context_entries`、`regex_apply`、`substitute_macros`、`now`、`word_count`、`truncate`
- **AND** 禁止 `import`、文件 IO、子进程

#### Scenario: 模板超时执行
- **WHEN** 模板渲染可能耗时较长
- **THEN** 在子进程中执行模板渲染（使用 `multiprocessing`）
- **AND** 父进程设置 5 秒超时，超时后 terminate 子进程
- **AND** 超时后跳过模板，使用原始文本，记录 ERROR 日志
- **AND** 限制模板迭代次数（`recursion_limit=50`）

#### Scenario: 变量作用域访问
- **WHEN** 模板内访问变量
- **THEN** `getvar(name, scope='chapter')` 读取指定作用域变量
- **AND** `scope` 参数可选 `global` / `project` / `chapter` / `cache`
- **AND** 不支持 `global.x` 点号语法，统一用 `getvar('x', scope='global')`

### Requirement: 自动上下文提取

系统 SHALL 在续写前自动分析前文，以世界书格式提取创作必备内容。

#### Scenario: 提取流程
- **WHEN** 用户启用"自动提取上下文"并点击续写
- **THEN** 系统取前 N 章（默认 5，可被 Project.extract_config.lookback_chapters 覆盖）正文 + 小说档案
- **AND** 构造提取提示词（优先用 `extractor_prompt_override`，为 null 时用默认模板 `resources/defaults/extract_prompt.txt`）
- **AND** 调用提取模型（非流式，默认 gpt-4o-mini，可被 Project.extract_config.extractor_model 覆盖）
- **AND** 解析 JSON → `list[ContextEntry]`，失败时尝试修复（去除 markdown 代码块标记）
- **AND** ContextEntry 的 content 字段最长 200 字，超出截断
- **AND** 提取结果缓存，key = 各章节 hash 的组合（按章节分段缓存，仅重新提取变化的章节）

#### Scenario: ST 世界书导入
- **WHEN** 用户选择导入 ST 世界书 JSON 文件
- **THEN** 解析条目，转换为 ContextEntry 格式
- **AND** `position` 字段做数字→字符串转换（0→before, 1→after, 2→at_depth）
- **AND** 忽略 `probability` 字段（本工具无概率激活）
- **AND** 未识别字段保留在 `_raw_st_fields`
- **AND** 导入的条目标记 `source_chapter_range = null`（非自动提取）

#### Scenario: 提取中 UI 状态
- **WHEN** 上下文提取进行中（可能 15 秒）
- **THEN** 上下文提取预览区显示 loading 动画和"提取中..."文本
- **AND** 续写按钮禁用，显示"提取中"
- **AND** 用户可点击"取消提取"终止提取
- **AND** 提取完成后自动刷新预览区

#### Scenario: 提取失败处理
- **WHEN** 提取 API 错误或 JSON 解析失败
- **THEN** 弹窗提示"上下文提取失败"，提供三个选项：重试 / 跳过提取继续续写 / 取消续写
- **AND** 选择"跳过"时续写继续，但状态栏警告"未提取上下文，续写质量可能下降"

#### Scenario: 用户干预提取结果
- **WHEN** 提取完成并显示在预览区
- **THEN** 用户可编辑任意条目的 content
- **AND** 用户可禁用某条目（本次续写不注入）
- **AND** 用户可手动添加条目
- **AND** 用户可点击"强制重新提取"跳过缓存

#### Scenario: ContextEntry 数据结构
- **WHEN** 定义提取条目
- **THEN** 字段包括：`uid`、`category`（characters/locations/events/style/plot_state）、`key`（关键词数组，用于 UI 显示和搜索过滤）、`comment`、`content`、`order`（默认 100，用于排序，**统一为升序，数字越小越优先**）、`position`（before/after/at_depth）、`depth`（仅 at_depth 时有效，默认 4）、`role`（system/user/assistant，默认 system；仅 at_depth 注入时生效，worldInfoBefore/After marker 注入时固定为 system）、`source_chapter_range`（`tuple[int, int]` 闭区间，如 `(0, 4)` 表示提取自第 0-4 章；导入 ST 世界书时为 null）、`extracted_at`
- **AND** `position` 用字符串（本工具内部格式，表示 worldInfoBefore/worldInfoAfter/at_depth 注入位置），导入 ST 世界书时做数字→字符串转换
- **AND** `order` 字段在 worldInfoBefore/After 和 at_depth 场景下**统一升序**（数字越小越优先），消除二义性
- **AND** **不包含** `probability` 字段（本工具 ContextEntry 始终注入，无概率激活逻辑；导入 ST 世界书时忽略 probability 字段）

#### Scenario: at_depth ContextEntry 注入
- **WHEN** ContextEntry 的 `position == "at_depth"`
- **THEN** 该条目按 `depth` 字段值注入到历史数组，复用 Prompt 深度注入规则（depth=N 表示插入到倒数第 N 条历史消息之前）
- **AND** 多个 at_depth 条目按 `depth` 从小到大、同 depth 按 `order` 从小到大排序（统一升序）
- **AND** at_depth 条目不参与 worldInfoBefore/After marker 的拼接

### Requirement: 多版本（Swipe）管理

系统 SHALL 支持同一续写点生成多个版本，含完整参数快照。

#### Scenario: 生成新 swipe
- **WHEN** 用户点击"重写"或"开始续写"
- **THEN** 生成新 swipe，记录完整快照：`id`、`created_at`、`content`、`model`、`is_accepted`（bool，默认 false）、`status`（completed/interrupted/failed）、`created_by`（continuation/rewrite）、`parameters_snapshot`（temperature/max_tokens/target_words/top_p/frequency_penalty/presence_penalty）、`preset_id`、`preset_snapshot`（预设内容副本）、`regex_script_ids_snapshot`、`extracted_context_snapshot`、`prompt_snapshot`（messages 数组）、`reasoning_content`（若有）

#### Scenario: 多 swipe 切换与对比
- **WHEN** 章节有多个 swipe
- **THEN** 章节列表显示 swipe 子节点（续写1、续写2...）
- **AND** 点击 swipe 子节点显示该版本内容
- **AND** swipe 元数据（模型、参数、时间、字数）显示在续写面板顶部
- **AND** 提供"并排对比"按钮，弹出对比窗口显示两个 swipe 的 diff

#### Scenario: 接受续写
- **WHEN** 用户点击"接受并追加"
- **THEN** 当前 swipe 标记 `is_accepted = true`，其他 swipe 的 `is_accepted` 设为 false
- **AND** swipe content 追加到章节正文末尾
- **AND** 章节保存到文件系统
- **AND** 提供"接受并继续续写"快捷操作（基于刚追加的内容立即发起新续写）

### Requirement: Token 计数与模型兼容

系统 SHALL 按模型族选择 tokenizer，非 OpenAI 模型提供 fallback 估算。

#### Scenario: OpenAI 模型 token 计数
- **WHEN** 模型名匹配 OpenAI 系列（gpt-3.5/gpt-4/gpt-4o/gpt-5 等）
- **THEN** 使用 tiktoken 对应 tokenizer（cl100k_base / o200k_base）
- **AND** token 计数结果缓存（key = text hash + tokenizer 名）

#### Scenario: 非 OpenAI 模型 fallback
- **WHEN** 模型名不匹配 OpenAI 系列
- **THEN** 使用字符数估算：中文 `len(text) * 0.6`，英文 `len(text) / 4`，混合取 `len(text) * 0.5`
- **AND** 状态栏提示"使用估算 token 计数（非 OpenAI 模型），实际可能偏差 ±20%"
- **AND** 用户可在设置中为特定模型配置 tokenizer（如 Claude 用 `anthropic` tokenizer 若安装）

### Requirement: API 连接配置与安全

系统 SHALL 支持多 API 端点管理，API Key 加密存储。

#### Scenario: API Key 加密
- **WHEN** 用户输入 API Key
- **THEN** 使用 `cryptography.Fernet` + `PBKDF2HMAC`（salt 随机生成并存储，passphrase = 机器 ID + 用户名）加密
- **AND** 加密后存储在配置 JSON，不明文落盘
- **AND** UI 显示为 `sk-****`，需点击"显示"才可见
- **AND** 提供"导出加密密钥"功能（用于换机器迁移，导出为密码保护的文件）

#### Scenario: 多端点管理
- **WHEN** 用户在设置中配置多个 API 端点
- **THEN** 每个端点独立存储：`id`、`name`、`base_url`、`api_key_encrypted`、`default_model`
- **AND** 续写时可从下拉框选择端点
- **AND** 默认端点可在设置中指定

#### Scenario: 获取可用模型列表
- **WHEN** 用户在 API 端点配置中点击"获取模型列表"
- **THEN** 系统发送 GET 请求到 `{endpoint}/models`
- **AND** 解析返回的 JSON，提取 `data[].id` 作为模型名列表
- **AND** 模型列表填充到端点的模型选择下拉框
- **AND** 请求失败时显示错误提示，不阻塞端点保存

#### Scenario: 数据隐私声明
- **WHEN** 应用首次启动
- **THEN** 显示隐私声明："本工具除调用您配置的 LLM API 外，不发送任何数据到第三方。崩溃日志不包含小说内容和 API Key。"
- **AND** 用户需同意才能继续使用

### Requirement: 错误处理与数据可靠性

系统 SHALL 对各类错误提供明确处理策略，保证数据一致性。

#### Scenario: SQLite + 文件系统一致性
- **WHEN** 写入章节（先写文件再写 DB）
- **THEN** 先写章节文件到临时路径，fsync 后 rename 到目标路径（原子操作）
- **AND** 文件写入成功后更新 SQLite
- **AND** SQLite 启用 WAL 模式，`busy_timeout=5000`
- **AND** 若 SQLite 写入失败，删除已写入的文件并回滚

#### Scenario: 数据损坏恢复
- **WHEN** 启动时检测到 JSON 文件损坏
- **THEN** 尝试从 `.bak` 备份恢复
- **AND** 若 `.bak` 也损坏，弹窗提示用户选择"重置该项配置"或"手动修复"
- **AND** 关键操作（保存预设、正则、项目配置）前自动写 `.bak` 文件

#### Scenario: 日志脱敏
- **WHEN** 记录日志
- **THEN** API Key 始终脱敏为 `sk-****`
- **AND** 请求体只记录摘要（前 200 字 + 总字数）
- **AND** 不记录完整小说内容
- **AND** DEBUG 级别日志可通过设置开关控制是否启用
- **AND** 日志按天滚动，保留 7 天，超过自动删除

#### Scenario: Token 超限裁剪下限
- **WHEN** Token 裁剪导致历史章节不足 1 章
- **THEN** 至少保留当前续写点章节（即使截断）
- **AND** 状态栏警告"上下文严重不足，建议增大 max_context 或减小 max_tokens"
- **AND** 不阻止续写（让用户自行决定）

### Requirement: UI/UX 布局

系统 SHALL 提供可折叠、可调宽的三栏布局。

#### Scenario: 三栏布局调整
- **WHEN** 用户拖动面板分隔条
- **THEN** 调整左侧章节列表和右侧续写面板的宽度
- **AND** 每个面板可通过按钮折叠/展开
- **AND** 最小窗口尺寸 1024×700
- **AND** 窗口尺寸和面板宽度持久化

#### Scenario: 预设/正则/模板管理器集成
- **WHEN** 用户打开预设管理器、正则管理器或模板编辑器
- **THEN** 以非模态独立窗口打开（可同时打开多个）
- **AND** 窗口位置和尺寸持久化
- **AND** 支持快捷键切换（Ctrl+P / Ctrl+G / Ctrl+T）

### Requirement: 导出功能

系统 SHALL 支持多种导出格式。

#### Scenario: 导出完整小说
- **WHEN** 用户选择"导出完整 TXT"
- **THEN** 按章节顺序拼接所有章节正文（含已接受的续写）
- **AND** 章节标题作为分隔（可选）
- **AND** 导出到用户指定路径

#### Scenario: 导出单章
- **WHEN** 用户右键章节选择"导出"
- **THEN** 可选格式：TXT / Markdown
- **AND** Markdown 格式包含章节标题作为 H1

#### Scenario: 导出项目备份
- **WHEN** 用户选择"导出项目备份"
- **THEN** 打包项目目录（含章节、续写、配置）为 zip
- **AND** 含 `manifest.json` 记录项目元数据、预设、正则引用
- **AND** 可在另一台机器导入恢复

### Requirement: 性能基线

系统 SHALL 满足以下性能指标。

#### Scenario: 大规模项目性能
- **WHEN** 项目包含 500 章
- **THEN** 章节列表渲染使用虚拟滚动（QTreeView + `setUniformRowHeights(True)` + 自定义 model，支持树形 swipe 子节点），首屏 < 500ms
- **AND** 章节搜索按标题实时搜索，按内容搜索需点击"全文搜索"按钮（异步，可取消，显示进度）
- **AND** 内存占用 < 300MB（章节按需加载）
- **AND** 单章 10 万字在 QPlainTextEdit 中编辑流畅（滚动无卡顿，优先用 QPlainTextEdit 替代 QTextEdit 以提升大文本性能）

#### Scenario: 流式输出性能
- **WHEN** 流式输出高频 chunk（50+ chunks/秒）
- **THEN** UI 使用 QTimer 每 50ms 批量合并 chunk 更新
- **AND** 滚动自动跟随最新内容（可手动锁定滚动）
- **AND** CPU 占用 < 30%（单核）

---

## MODIFIED Requirements

### Requirement: 提示词组装管线（相对 PRD v1.0 修正）

**修正点**：
1. 三阶段顺序从"合并 → 裁剪 → 注入"改为"排序 → 注入 → 裁剪"（对齐 ST 实际行为）
2. 注入提示视为不可裁剪项，其 token 占用从预算中扣除
3. 单章超限时从章节末尾截取（保留最新内容），而非报错阻止
4. worldInfoBefore/After marker 注入规则明确：所有条目用 `\n` 拼接为单条 system 消息（对齐 ST，非按 role 分组）
5. RELATIVE 与 ABSOLUTE 混合排序规则明确

### Requirement: 正则脚本引擎（相对 PRD v1.0 修正）

**修正点**：
1. `g` flag 不映射任何 Python flag（Python `re.sub` 默认全局）
2. 增加 `u` → `re.UNICODE` 映射
3. 明确替换字符串语法转换：`$1`→`\1`、`$<name>`→`\g<name>`、`{{match}}`→`\g<0>`
4. 统一使用 `regex` 库（非 `re`）
5. 正则单独测试功能
6. 脚本执行顺序修正为 GLOBAL(0) → PRESET(2) → SCOPED(1)（对齐 ST `SCRIPT_TYPES` 对象插入序，非数值大小）
7. placement 未知值（含 REASONING=6）忽略并记录 WARNING 日志

### Requirement: Jinja2 模板引擎（相对 PRD v1.0 修正）

**修正点**：
1. 使用 `ImmutableSandboxedEnvironment`（非 `SandboxedEnvironment`）
2. 自定义 `is_safe_attribute` 拒绝 `_` 开头属性
3. 禁用 `attr` 过滤器
4. 超时通过子进程实现（非 signal.alarm）
5. 限制 `recursion_limit=50`
6. 变量作用域访问统一用 `getvar(name, scope=...)`，不支持点号语法
7. 宏→Jinja2→正则 三者不递归，模板内调用 `substitute_macros()` 为显式二次替换

### Requirement: 流式输出（相对 PRD v1.0 修正）

**修正点**：
1. 异步桥接统一用 QThread + 独立事件循环（移除 qasync 依赖）
2. SSE 解析使用增量 UTF-8 解码器
3. 多行 `data:` 字段拼接
4. API 错误时读取 error body 而非 `raise_for_status()`
5. 推理内容单独存储
6. UI 更新节流（50ms 批量合并）
7. 流式中断保留 ≥100 字的文本作为 swipe

### Requirement: 数据结构（相对 PRD v1.0 修正）

**修正点**：
1. `Continuation`（swipe）增加 `parameters_snapshot`、`preset_id`、`preset_snapshot`、`regex_script_ids_snapshot`、`status`、`created_by`、`reasoning_content`
2. `prompt_snapshot` 类型明确为 `list[dict]`（messages 数组）
3. `Prompt.position` 语义对齐 ST：`start`/`end`（非 `before`/`after`，ST 实际用 `start`/`end`）
4. `ContextEntry` 增加 `depth` 字段（at_depth 时有效）
5. `ContextEntry.position` 用字符串，导入 ST 世界书时做数字→字符串转换
6. `prompt_order` 的 `character_id` 统一使用 `100000`

### Requirement: Token 计数（相对 PRD v1.0 修正）

**修正点**：
1. 按模型族选择 tokenizer
2. 非 OpenAI 模型用字符数估算 fallback
3. 状态栏提示 token 计数方式（精确/估算）
4. token 计数结果缓存

### Requirement: 安全（相对 PRD v1.0 修正）

**修正点**：
1. API Key 加密用 PBKDF2HMAC + 随机 salt
2. 提供加密密钥导出/导入（换机器迁移）
3. 日志脱敏策略明确
4. 首次启动显示隐私声明
5. Jinja2 沙箱使用 ImmutableSandboxedEnvironment + 自定义安全属性检查

### Requirement: 错误处理（相对 PRD v1.0 修正）

**修正点**：
1. SQLite + 文件系统用"先写临时文件再 rename"保证原子性
2. SQLite 启用 WAL 模式
3. 关键操作前写 `.bak` 文件
4. 上下文提取失败提供"重试/跳过/取消"三选项
5. Token 超限至少保留当前章节（截断）
6. 项目数据损坏恢复流程明确

---

## REMOVED Requirements

### Requirement: 暂停按钮（流式输出）
**Reason**: PRD v1.0 的"暂停 UI 显示但不中断 API 请求"语义不清，暂停期间数据去向不明，功能价值存疑
**Migration**: 移除暂停按钮，只保留"停止"按钮（中断 API 请求，保留已接收 ≥100 字文本作为 swipe）

### Requirement: qasync 依赖
**Reason**: 与 QThread + 独立事件循环方案冲突，二选一后移除 qasync
**Migration**: 统一用 QThread + `asyncio.new_event_loop()`，依赖清单移除 qasync

### Requirement: `position: before/after` 语义（本工具自定义）
**Reason**: 早期 PRD 草稿曾用 `before/after` 作为 Prompt.position 命名，与 ST 实际使用的 `start/end` 不一致
**Migration**: 统一用 `start`/`end` 对齐 ST。注意：ContextEntry.position 仍用 `before`/`after`/`at_depth`（本工具自定义，表示 worldInfoBefore/worldInfoAfter/at_depth 注入，与 ST 的 Prompt.position 是不同概念）

### Requirement: `global.x` 点号变量访问语法
**Reason**: 与 `getvar(name, scope=...)` 函数式访问重复，且点号语法在 Jinja2 中可能触发属性访问安全风险
**Migration**: 统一用 `getvar('x', scope='global')`

---

## 补充数据结构与配置定义

### Requirement: 完整数据结构定义

系统 SHALL 明确定义所有核心数据结构，消除歧义。

#### Scenario: Chapter 数据结构
- **WHEN** 定义章节
- **THEN** 字段包括：`id`、`project_id`、`index`（章节序号，从 0 开始）、`title`、`content`（正文，存文件系统）、`word_count`、`continuations`（list[Continuation]）、`metadata`（dict，含 notes、tags）、`created_at`、`updated_at`
- **AND** **不包含** `is_original` 字段（续写内容追加到章节末尾，不创建新章节，无需区分原文/续写）
- **AND** `continuations` 为空列表表示该章节尚无续写版本

#### Scenario: Project 数据结构
- **WHEN** 定义项目
- **THEN** 字段包括：`id`、`name`、`created_at`、`updated_at`、`source_file`、`novel_profile`（dict）、`preset_id`、`regex_script_ids`（list[str]）、`extract_config`（dict，覆盖全局 `context_extract`，字段结构相同）、`chapter_split_rule`（dict）
- **AND** `novel_profile` 结构：`{"title": str, "author": str, "protagonist": str, "synopsis": str, "world_setting": str, "writing_style": str}`
- **AND** `chapter_split_rule` 结构：`{"pattern": str, "include_title_in_content": bool, "manual_overrides": list[dict]}`
- **AND** `manual_overrides` 元素结构：`{"action": "split"|"merge", "chapter_id": str, "position": int（split 时为字符偏移量，merge 时忽略）}`
- **AND** `extract_config` 为 null 时使用全局 `context_extract`，非 null 时按字段覆盖（字段结构：`extractor_model`、`cache_enabled`、`cache_ttl_hours`、`extractor_prompt_override`、`lookback_chapters`）

#### Scenario: 全局配置结构
- **WHEN** 定义全局配置
- **THEN** 顶层字段包括：`config_version`（int，当前为 1）、`api_endpoints`、`default_endpoint_id`、`appearance`、`continuation`、`context_extract`、`data`
- **AND** `api_endpoints` 元素结构：`{"id": str, "name": str, "base_url": str, "api_key_encrypted": str, "default_model": str}`
- **AND** `appearance` 结构：`{"theme": "dark"|"light"|"system", "font_family": str, "font_size": int, "line_height": float}`
- **AND** `continuation` 结构：`{"default_lookback_chapters": int（默认 5）, "default_target_words": int, "default_temperature": float, "default_max_tokens": int, "auto_save": bool, "show_token_count": bool}`
- **AND** `context_extract` 结构：`{"extractor_model": str（默认 "gpt-4o-mini"）, "cache_enabled": bool（默认 true）, "cache_ttl_hours": int（默认 24）, "extractor_prompt_override": str|null（字符串为自定义提示词文本，null 用默认模板）, "lookback_chapters": int（默认 5，可被 Project.extract_config 覆盖）}`
- **AND** `data` 结构：`{"storage_path": str（默认 "~/.novelforge"）}`

#### Scenario: 配置版本迁移
- **WHEN** 启动时检测到 `config_version` 低于当前版本
- **THEN** 按版本顺序执行迁移函数链（如 v1→v2 迁移函数、v2→v3 迁移函数）
- **AND** 迁移前备份原配置到 `.bak`
- **AND** 迁移失败时回滚并提示用户

#### Scenario: 导入加密密钥到新机器
- **WHEN** 用户在新机器导入加密密钥文件
- **THEN** 用户输入密钥文件的密码解密
- **AND** 使用新机器 ID 重新加密所有 API Key
- **AND** 更新配置中的 `api_key_encrypted` 字段

### Requirement: chatHistory 消息 role 规则

系统 SHALL 明确章节正文作为消息注入 chatHistory 时的 role。

#### Scenario: 章节消息 role
- **WHEN** 前文章节正文注入到 chatHistory marker 位置
- **THEN** 每章作为一条消息，role 统一为 `user`（视为既定文本上下文，非用户实时输入）
- **AND** 消息 content 格式为 `{章节标题}\n{章节正文}`（如 `"第五十章 风起云涌\n山崖之上..."`）
- **AND** 不使用 assistant role（避免 LLM 误认为前文是自己的回复）

### Requirement: 边界情况处理

系统 SHALL 对常见边界情况提供明确处理策略。

#### Scenario: 导入 TXT 无章节标题匹配
- **WHEN** 章节标题正则无任何匹配
- **THEN** 整个文本作为单章导入，标题为"全文"或文件名
- **AND** 状态栏提示"未检测到章节标题，已作为单章导入"

#### Scenario: 空章节（连续两个章节标题）
- **WHEN** 拆分后某章正文为空
- **THEN** 保留空章节，content 为空字符串
- **AND** 章节列表显示空章节，用户可手动编辑或删除

#### Scenario: 模板渲染语法错误
- **WHEN** Jinja2 模板含语法错误（如未闭合 `{% %}`）
- **THEN** 捕获 `TemplateSyntaxError`，跳过模板渲染使用原始文本
- **AND** 记录 ERROR 日志含错误位置
- **AND** UI 显示非阻塞警告"模板语法错误，已使用原始文本"

#### Scenario: 正则编译错误
- **WHEN** 正则脚本的 findRegex 无效
- **THEN** 跳过该脚本，记录 ERROR 日志
- **AND** UI 在脚本列表显示错误图标
- **AND** 不影响其他脚本执行

#### Scenario: swipe 删除
- **WHEN** 用户右键 swipe 选择"删除"
- **THEN** 弹窗确认"确定删除此续写版本？"
- **AND** 确认后删除 swipe（若为已接受版本，先取消接受再删除）
- **AND** 操作支持 undo

#### Scenario: 章节删除
- **WHEN** 用户右键章节选择"删除"
- **THEN** 弹窗确认"确定删除此章节及其所有续写版本？"
- **AND** 确认后删除章节文件、SQLite 记录、关联 swipe
- **AND** 后续章节 index 自动重排
- **AND** 操作支持 undo（undo 时恢复文件和记录）

#### Scenario: API 限流（429）
- **WHEN** API 返回 429 状态码
- **THEN** 读取 `Retry-After` 头，等待指定秒数后重试
- **AND** 最多重试 3 次
- **AND** UI Toast 提示"API 限流，等待 N 秒后重试"

#### Scenario: API 认证失败（401）
- **WHEN** API 返回 401 状态码
- **THEN** 不重试，弹窗提示"API Key 无效，请检查设置"
- **AND** 跳转设置对话框的 API 配置页

#### Scenario: 取消提取后的行为
- **WHEN** 用户在提取中点击"取消提取"
- **THEN** 终止提取请求
- **AND** 弹窗提供选项：重试提取 / 跳过提取继续续写 / 取消续写
- **AND** 与"提取失败"处理一致

#### Scenario: 章节数不足 N（提取时）
- **WHEN** 续写点之前不足 N 章（如新小说只有 1-2 章）
- **THEN** 提取所有可用章节（不足 N 章不报错）
- **AND** 若可用章节为 0（续写第 1 章），跳过提取，状态栏提示"无前文可提取"

#### Scenario: "重写"行为
- **WHEN** 用户点击"重写"
- **THEN** 沿用上次续写的参数（temperature、max_tokens、target_words 等）作为默认
- **AND** 用户可在续写配置区调整参数
- **AND** 生成新 swipe，`created_by` 标记为 `rewrite`
- **AND** UI 显示上次结果供参考（可折叠）

#### Scenario: "接受并继续续写"的 swipe 归属
- **WHEN** 用户点击"接受并继续续写"
- **THEN** 当前 swipe 接受并追加到章节正文
- **AND** 新续写生成的 swipe 归属**当前章节**（非新章节）
- **AND** 新续写基于追加后的完整章节内容

### Requirement: Token 预算与模板渲染

系统 SHALL 明确 Token 裁剪与模板渲染的关系。

#### Scenario: Token 裁剪时机
- **WHEN** 提示词组装时
- **THEN** Token 裁剪在**模板渲染前**执行（基于原始 content 估算）
- **AND** 模板渲染后若总 token 超限，容忍 10% 超限（不二次裁剪，避免循环依赖）
- **AND** 状态栏显示渲染前后 token 差值供用户参考

#### Scenario: 非 OpenAI token 估算语言判定
- **WHEN** 对非 OpenAI 模型估算 token
- **THEN** 统计中文字符（Unicode CJK 范围）占比
- **AND** 中文字符占比 > 70% 视为中文：`len(text) * 0.6`
- **AND** 中文字符占比 < 30% 视为英文：`len(text) / 4`
- **AND** 其余为混合：`len(text) * 0.5`

### Requirement: 正则脚本 placement 未知值处理

#### Scenario: 导入时 placement 未知值
- **WHEN** 导入 ST 正则 JSON 时遇到未识别的 placement 值（如 0=MD_DISPLAY、3=SLASH_COMMAND、4=sendAs legacy、6=REASONING）
- **THEN** 忽略该值（从 placement 数组中移除）
- **AND** 记录 WARNING 日志
- **AND** 不阻止导入，其他 placement 值正常处理
- **AND** 本工具仅处理 USER_INPUT(1)、AI_OUTPUT(2)、WORLD_INFO(5) 三种 placement

### Requirement: 续写历史日志

系统 SHALL 记录每次续写的完整历史，供回溯。

#### Scenario: 历史日志记录
- **WHEN** 每次续写完成（含中断、失败）
- **THEN** 记录到 SQLite：`id`、`project_id`、`chapter_id`、`swipe_id`、`started_at`、`finished_at`、`status`、`model`、`parameters_json`、`prompt_messages_json`、`output_text`、`error_message`
- **AND** 历史日志查看面板支持按项目、章节、时间筛选
- **AND** 点击日志条目可查看完整提示词和输出

### Requirement: 主题与字体

系统 SHALL 支持主题切换和字体调整。

#### Scenario: 主题切换
- **WHEN** 用户在设置中选择暗色/亮色/跟随系统
- **THEN** 立即应用主题（QSS 样式表）
- **AND** 主题选择持久化

#### Scenario: 字体调整
- **WHEN** 用户调整正文字体、大小、行距
- **THEN** 章节预览/编辑区立即应用
- **AND** 设置持久化

### Requirement: 快捷键

系统 SHALL 支持以下快捷键。

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

---

## 非目标（明确排除）

- 不做 多角色群聊（Group Chat）
- 不做 TTS/语音/图片生成
- 不做 向量数据库/RAG 检索（上下文提取用 LLM 分析而非向量检索）
- 不做 多用户/云端同步（纯本地单用户）
- 不做 完整的常驻世界书编辑器（改为自动提取）
- 不做 用户自定义 JS 脚本执行（Python 工具无 JS 运行时）
- 不做 插入续写（章节中间插入 AI 内容）—— v1.0 不支持，未来版本考虑
- 不做 批量续写（一次生成多章）—— v1.0 不支持，未来版本考虑
- 不做 EPUB/DOCX 导出 —— v1.0 只支持 TXT/Markdown
- 不做 国际化（i18n）—— v1.0 硬编码中文

### 非目标反向验证清单

实现完成后 SHALL 验证以下功能确实未实现（防止功能越界）：
- 无群聊功能
- 无 TTS/语音/图片生成
- 无向量数据库/RAG 检索
- 无云端同步/多用户
- 无常驻世界书编辑器（只有自动提取预览）
- 无 JS 脚本执行
- 无插入续写（只能在章节末尾追加）
- 无批量续写
- 无 EPUB/DOCX 导出
- 无 i18n（硬编码中文）

---

## 技术选型（修正后）

| 依赖 | 版本 | 用途 | 修正说明 |
|---|---|---|---|
| `PySide6` | >=6.6 | UI 框架 | — |
| `aiohttp` | >=3.9 | 异步 HTTP / SSE 流式 | — |
| `jinja2` | >=3.1 | 模板引擎（ImmutableSandboxedEnvironment） | 升级沙箱等级 |
| `tiktoken` | >=0.6 | OpenAI 模型 token 计数 | 仅 OpenAI 模型使用 |
| `cryptography` | >=42.0 | API Key 加密（PBKDF2HMAC + Fernet） | 修正 KDF |
| `regex` | >=2024.0 | 正则引擎（统一使用，非 re） | 明确统一 |
| `pydantic` | >=2.0 | 数据模型校验 | — |
| `aiosqlite` | >=0.19 | 异步 SQLite（WAL 模式） | — |
| `python-dateutil` | >=2.9 | 日期处理 | — |
| ~~`qasync`~~ | ~~>=0.27~~ | ~~PySide6 + asyncio 桥接~~ | **移除**，改用 QThread |

**新增依赖**：
| 依赖 | 版本 | 用途 |
|---|---|---|
| `multiprocessing` | 内置 | 模板子进程超时执行 |

---

## 默认资源文件路径

| 路径 | 用途 |
|---|---|
| `resources/defaults/default_preset.json` | 默认写作预设（含 main、chatHistory、worldInfoBefore/After marker） |
| `resources/defaults/extract_prompt.txt` | 默认上下文提取提示词模板 |
| `resources/defaults/default_regex_scripts.json` | 默认正则脚本（可选，空数组） |
| `resources/themes/dark.qss` | 暗色主题样式表 |
| `resources/themes/light.qss` | 亮色主题样式表 |

---

## 开发里程碑（修正后）

### 功能编号索引

| 编号 | 功能 |
|---|---|
| F-01 | TXT 导入与章节拆分 |
| F-02 | 章节预览与编辑 |
| F-03 | LLM 流式调用 |
| F-04 | 续写结果管理（Swipe） |
| F-05 | 写作预设管理 |
| F-06 | 正则脚本管理 |
| F-07 | Jinja2 模板引擎 |
| F-08 | 自动上下文提取 |
| F-09 | 宏替换系统 |
| F-10 | API 连接配置 |
| F-11 | 项目管理 |
| F-12 | 导出功能 |
| F-13 | Token 计数与预算 |
| F-14 | 续写历史日志 |
| F-15 | 主题与字体 |

### M0：脚手架与架构（新增）
- 项目结构搭建、依赖安装
- PySide6 主窗口骨架（三栏布局，可折叠可调宽）
- SQLite + 文件系统存储层（WAL 模式、原子写入）
- 配置加载与加密基础设施
- 日志系统（含脱敏）

**交付标准**：能启动主窗口，创建空项目，配置 API 端点

### M1：核心续写闭环
- TXT 导入与章节拆分（F-01）
- 章节预览与编辑（F-02，含 undo/redo）
- 基础项目管理（F-11 子集，单项目）
- API 连接配置（F-10）
- LLM 流式调用（F-03，含 SSE 正确解析）
- 基础续写流程（简单 messages 组装，无预设/正则/模板）
- 续写结果管理（F-04，含 swipe 完整快照）

**交付标准**：能导入 TXT、预览章节、发起流式续写、接受结果、多 swipe 切换

### M2：提示词管线
- 写作预设管理 + ST 预设导入（F-05）
- 预设可视化编辑器（prompt_order 拖拽排序）
- 提示词组装管线（三阶段：排序 → 注入 → 裁剪）
- 宏替换系统（F-09）
- Token 计数与预算（F-13，含非 OpenAI fallback）

**交付标准**：能导入 ST 预设、按 prompt_order 组装提示词、宏替换正常、Token 预算裁剪正确

### M3：正则与模板
- 正则脚本管理 + ST 正则导入（F-06）
- 正则引擎（修正 flag 映射、替换字符串转换、单独测试）
- Jinja2 模板引擎（F-07，ImmutableSandboxedEnvironment、子进程超时）
- 双阶段模板执行（发送前 + 接收后）
- 分层变量作用域（global/project/chapter/cache）
- 模板编辑器 UI

**交付标准**：能导入 ST 正则、Jinja2 模板双阶段执行正常、变量持久化、沙箱安全

### M4：自动上下文提取
- 上下文提取器（F-08）
- 提取提示词模板
- 分段缓存策略
- 上下文提取预览面板（含 loading 状态、用户干预）
- 提取失败处理（重试/跳过/取消）

**交付标准**：续写前自动提取前文关键信息、以世界书格式注入提示词、缓存有效

### M5：完善与打磨
- 完整项目管理（多本小说）（F-11）
- 导出功能（F-12，TXT/Markdown/项目备份）
- 续写历史日志（F-14）
- 主题与字体（F-15）
- 性能优化（虚拟滚动、按需加载）
- PyInstaller 打包

**交付标准**：功能完整、错误处理健壮、性能达标、可打包分发

---

## 术语表

| 术语 | 说明 |
|---|---|
| Swipe | 同一续写点的多个版本，源自 SillyTavern |
| Marker | 预设中的占位符提示，标记特定内容插入位置（如 chatHistory、worldInfoBefore） |
| 深度注入 | 将提示插入到历史消息的指定深度位置（ABSOLUTE 模式） |
| prompt_order | 预设中定义提示排列顺序的配置，含 identifier 和 enabled 字段 |
| 双阶段模板 | 发送前执行模板 + 接收后执行输出模板 |
| 上下文提取 | 续写前自动分析前文，提取关键信息为 ContextEntry |
| RELATIVE / ABSOLUTE | 提示注入位置模式：RELATIVE（0，按 order 排列）/ ABSOLUTE（1，按深度注入历史） |
| worldInfoBefore/After | 预设中的 marker，标记上下文提取条目的注入位置（前/后） |
| chatHistory | 预设中的 marker，标记前文章节历史的插入位置 |
| ContextEntry | 上下文提取的条目，类世界书格式，含 category/key/content/position 等字段 |
| character_id | ST 预设中 prompt_order 的分组标识，本工具统一用 100000 表示全局顺序 |

---

**文档结束。**
