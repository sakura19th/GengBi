# Tasks

> **Change-ID**: build-novel-continuation-tool
> **对应 Spec**: spec.md v2.1
> **原则**: 按里程碑组织，每个任务可独立验证，依赖关系明确

---

## M0：脚手架与架构（已完成）

- [x] Task 0.1: 搭建项目结构与依赖安装
  - [x] SubTask 0.1.1: 创建 `novelforge/` 目录结构（ui/core/services/models/utils/resources）
  - [x] SubTask 0.1.2: 编写 `requirements.txt`（PySide6, aiohttp, jinja2, tiktoken, cryptography, regex, pydantic, aiosqlite, python-dateutil）
  - [x] SubTask 0.1.3: 编写 `main.py` 入口与 `config.py` 配置加载骨架
  - [x] SubTask 0.1.4: 创建默认资源文件（`resources/defaults/default_preset.json`、`extract_prompt.txt`、`default_regex_scripts.json`、`resources/themes/dark.qss`、`light.qss`）
  - [x] SubTask 0.1.5: 验证依赖安装无冲突

- [x] Task 0.2: 实现核心数据模型与 SQLite + 文件系统存储层
  - [x] SubTask 0.2.1: 定义 Chapter、Project pydantic 模型（含 novel_profile、chapter_split_rule、manual_overrides 嵌套结构）
  - [x] SubTask 0.2.2: 定义 Continuation（swipe）pydantic 模型（含 is_accepted、status、created_by、parameters_snapshot、preset_snapshot、regex_script_ids_snapshot、extracted_context_snapshot、prompt_snapshot、reasoning_content）
  - [x] SubTask 0.2.3: 定义 SQLite schema（projects, chapters 元数据, continuations, history_log, cache）
  - [x] SubTask 0.2.4: 实现 WAL 模式启用与 `busy_timeout=5000`，检测网络文件系统降级为 DELETE 模式
  - [x] SubTask 0.2.5: 实现原子文件写入（临时文件 → fsync → rename）
  - [x] SubTask 0.2.6: 实现 SQLite 写入失败时删除已写入文件并回滚
  - [x] SubTask 0.2.7: 实现关键操作前 `.bak` 文件备份
  - [x] SubTask 0.2.8: 实现数据损坏恢复（启动时校验、`.bak` 恢复、`.bak` 也损坏时弹窗"重置配置/手动修复"）
  - [x] SubTask 0.2.9: 明确 SQLite 访问线程模型（每线程独立连接或主线程代理），验证 aiosqlite 跨线程访问限制

- [x] Task 0.3: 实现配置与加密基础设施
  - [x] SubTask 0.3.1: 定义全局配置 JSON 结构（config_version, api_endpoints, default_endpoint_id, appearance, continuation, context_extract, data）
  - [x] SubTask 0.3.2: 实现 `cryptography.Fernet` + `PBKDF2HMAC` 加密（随机 salt + 机器 ID passphrase，密钥派生结果缓存避免重复开销）
  - [x] SubTask 0.3.3: 实现加密密钥导出/导入（密码保护文件）
  - [x] SubTask 0.3.4: 实现密钥导入到新机器后用新机器 ID 重新加密所有 API Key 并更新 `api_key_encrypted` 字段
  - [x] SubTask 0.3.5: 实现 config_version 迁移函数链（备份→迁移→回滚）
  - [x] SubTask 0.3.6: 实现首次启动隐私声明对话框

- [x] Task 0.4: 实现日志系统（含脱敏）
  - [x] SubTask 0.4.1: 配置 logging 按天滚动，保留 7 天
  - [x] SubTask 0.4.2: 实现日志脱敏过滤器（API Key → `sk-****`，请求体截断 200 字 + 总字数）
  - [x] SubTask 0.4.3: 实现 DEBUG 级别开关

- [x] Task 0.5: 实现 PySide6 主窗口骨架
  - [x] SubTask 0.5.1: 三栏布局（QSplitter，章节列表 / 预览编辑 / 续写控制）
  - [x] SubTask 0.5.2: 面板可折叠、可拖拽调宽，尺寸持久化
  - [x] SubTask 0.5.3: 菜单栏、状态栏骨架（含保存状态显示位）
  - [x] SubTask 0.5.4: 最小窗口尺寸 1024×700
  - [x] SubTask 0.5.5: 空项目创建入口（菜单"新建项目"或欢迎页）

---

## M1：核心续写闭环（已完成）

- [x] Task 1.1: 实现 TXT 导入与章节拆分
  - [x] SubTask 1.1.1: 实现章节标题正则匹配（默认 `^第[一二三四五六七八九十百千零\d]+[章节回卷]`）
  - [x] SubTask 1.1.2: 实现拆分后章节存入文件系统（`~/.novelforge/projects/{project_id}/chapters/{chapter_id}.txt`）
  - [x] SubTask 1.1.3: 实现项目元数据写入 SQLite
  - [x] SubTask 1.1.4: 验证 100 万字 3 秒内完成拆分

- [x] Task 1.2: 实现章节预览与编辑
  - [x] SubTask 1.2.1: 预览模式（只读 QPlainTextEdit）与编辑模式切换
  - [x] SubTask 1.2.2: 实现 5 秒空闲自动保存，状态栏显示"已保存"/"未保存修改"
  - [x] SubTask 1.2.3: 实现 undo/redo（Ctrl+Z / Ctrl+Y）
  - [x] SubTask 1.2.4: 实现字数统计显示
  - [x] SubTask 1.2.5: 实现流式输出时锁定编辑（只读 + "续写中"提示）

- [x] Task 1.3: 实现章节拆分点手动调整
  - [x] SubTask 1.3.1: 编辑器中选中文本右键"在此处拆分"
  - [x] SubTask 1.3.2: 拆分后章节 index 自动重排
  - [x] SubTask 1.3.3: 原章节 continuations 归属拆分后的前一个章节
  - [x] SubTask 1.3.4: 实现合并章节（与下一章合并，两章 continuations 都归属合并后的章节）
  - [x] SubTask 1.3.5: 拆分/合并支持 undo

- [x] Task 1.4: 实现章节列表 UI（含虚拟滚动）
  - [x] SubTask 1.4.1: QTreeView 树形显示章节（`setUniformRowHeights(True)` 支持虚拟滚动），按 index 排序
  - [x] SubTask 1.4.2: 已有续写的章节显示 swipe 子节点（需在 Task 1.9 后验证）
  - [x] SubTask 1.4.3: 右键菜单（重命名、删除、合并、拆分、导出）；M1 阶段导出菜单项灰显，M5 实现
  - [x] SubTask 1.4.4: 搜索框（标题实时搜索；全文搜索按钮，异步执行、可取消、显示进度）
  - [x] SubTask 1.4.5: 验证 500 章项目首屏渲染 < 500ms

- [x] Task 1.5: 实现项目管理基础（单项目）
  - [x] SubTask 1.5.1: 项目列表面板（最近项目列表、打开、删除）
  - [x] SubTask 1.5.2: 项目切换（保存当前项目状态后切换）

- [x] Task 1.6: 实现 API 连接配置
  - [x] SubTask 1.6.1: 设置对话框中 API 端点管理（增删改）
  - [x] SubTask 1.6.2: API Key 输入（加密存储，UI 显示 `sk-****`）
  - [x] SubTask 1.6.3: 默认端点选择
  - [x] SubTask 1.6.4: 从 API 获取模型列表（GET `/models`），解析 `data[].id`，失败不阻塞保存

- [x] Task 1.7: 实现 LLM 流式调用（SSE 正确解析）
  - [x] SubTask 1.7.1: 实现 `stream_chat_completion()` 异步生成器
  - [x] SubTask 1.7.2: 使用增量 UTF-8 解码器（`codecs.getincrementaldecoder`）
  - [x] SubTask 1.7.3: 正确拼接多行 `data:` 字段
  - [x] SubTask 1.7.4: API 错误时读取 error body（非 `raise_for_status`）
  - [x] SubTask 1.7.5: 推理内容（`reasoning_content`）单独存储
  - [x] SubTask 1.7.6: 收到 `[DONE]` 结束流

- [x] Task 1.8: 实现 ContinuationWorker（QThread 桥接）
  - [x] SubTask 1.8.1: QThread 子类，`run()` 中创建独立 asyncio 事件循环
  - [x] SubTask 1.8.2: 信号：`chunk_received(str)`, `finished(str)`, `error(str)`, `token_count(int)`
  - [x] SubTask 1.8.3: 线程安全停止标志（`threading.Event`）+ `asyncio.Task.cancel()` 双重中断机制
  - [x] SubTask 1.8.4: 流式中断时保留 ≥100 字文本作为 swipe
  - [x] SubTask 1.8.5: 验证 QThread 退出时 asyncio 事件循环正确关闭（loop.close()、aiohttp session 清理、无僵尸线程）

- [x] Task 1.9: 实现续写控制面板 UI
  - [x] SubTask 1.9.1: 续写配置区（M1 阶段预设选择为占位下拉框禁用，M2 启用；模型、温度、字数、复选框）
  - [x] SubTask 1.9.2: 流式输出区（QPlainTextEdit，QTimer 50ms 节流批量更新）
  - [x] SubTask 1.9.3: 流式中按钮（停止），流结束后按钮（重写、接受并追加、接受并继续、编辑后接受）
  - [x] SubTask 1.9.4: 光标动画（█ 闪烁）
  - [x] SubTask 1.9.5: 滚动自动跟随（可锁定）
  - [x] SubTask 1.9.6: 推理内容可折叠显示区域
  - [x] SubTask 1.9.7: 验证 50+ chunks/秒高频场景下 50ms 节流有效，CPU 占用 < 30%（单核）

- [x] Task 1.10: 实现 Swipe 数据模型与快照
  - [x] SubTask 1.10.1: 生成新 swipe 时记录完整快照（spec 定义的 Continuation 全部字段，含 is_accepted）
  - [x] SubTask 1.10.2: 推理内容不参与后续提示词组装

- [x] Task 1.11: 实现 Swipe UI 展示与操作
  - [x] SubTask 1.11.1: swipe 子节点点击切换显示
  - [x] SubTask 1.11.2: swipe 元数据显示（模型、参数、时间、字数）
  - [x] SubTask 1.11.3: 接受续写（标记 is_accepted，其他 swipe 设为 false，追加到章节并保存）
  - [x] SubTask 1.11.4: "接受并继续续写"快捷操作（新 swipe 归属当前章节，基于追加后完整章节内容）
  - [x] SubTask 1.11.5: "重写"沿用上次参数（用户可调整），created_by=rewrite，显示上次结果供参考
  - [x] SubTask 1.11.6: swipe 删除（弹窗确认，已接受版本先取消接受，支持 undo）
  - [x] SubTask 1.11.7: "并排对比"按钮，弹出对比窗口显示两个 swipe 的 diff

- [x] Task 1.12: 实现边界情况处理
  - [x] SubTask 1.12.1: 导入 TXT 无章节标题匹配时整文作为单章，标题为"全文"或文件名，状态栏提示
  - [x] SubTask 1.12.2: 空章节（连续两个标题）保留，content 为空，列表显示可编辑或删除
  - [x] SubTask 1.12.3: 章节删除（弹窗确认，删除文件+SQLite+swipe，index 重排，支持 undo 恢复文件和记录）
  - [x] SubTask 1.12.4: API 限流（429）读取 Retry-After，最多重试 3 次，UI Toast 提示
  - [x] SubTask 1.12.5: API 认证失败（401）不重试，弹窗提示并跳转设置

- [x] Task 1.13: 实现 M1 基础快捷键
  - [x] SubTask 1.13.1: Ctrl+O 导入, Ctrl+S 保存, Ctrl+Enter 续写, Ctrl+R 重写
  - [x] SubTask 1.13.2: Ctrl+Shift+A 接受, Ctrl+Shift+E 编辑后接受
  - [x] SubTask 1.13.3: Esc 停止流式

---

## M2：提示词管线（已完成）

- [x] Task 2.1: 实现写作预设数据模型与存储
  - [x] SubTask 2.1.1: 定义 WritingPreset, Prompt pydantic 模型（字段对齐 ST：identifier, name, role, content, system_prompt, marker, position[start/end], injection_position, injection_depth, injection_order, forbid_overrides, extension）
  - [x] SubTask 2.1.2: 实现预设 JSON 存储与加载（`~/.novelforge/presets/{preset_id}.json`）
  - [x] SubTask 2.1.3: 实现默认预设（`resources/defaults/default_preset.json`）

- [x] Task 2.2: 实现 ST 预设导入导出
  - [x] SubTask 2.2.1: 解析 ST 预设 JSON，提取 prompts, prompt_order, 生成参数
  - [x] SubTask 2.2.2: `character_id == 100000` 视为全局顺序，取第一个匹配项
  - [x] SubTask 2.2.3: 忽略非 100000 的 character_id 条目并记录日志
  - [x] SubTask 2.2.4: 未识别字段保留在 `_raw_st_fields`，导出时原样写回
  - [x] SubTask 2.2.5: `position` 字段对齐 ST（start/end），原样保留

- [x] Task 2.3: 实现预设管理器 UI
  - [x] SubTask 2.3.1: 预设列表（新建、导入、导出、编辑、删除）
  - [x] SubTask 2.3.2: 提示词列表拖拽排序（prompt_order）
  - [x] SubTask 2.3.3: 每个提示启用/禁用复选框
  - [x] SubTask 2.3.4: marker 提示显示 `[marker]` 不可删除
  - [x] SubTask 2.3.5: system_prompt 提示不可删除可禁用
  - [x] SubTask 2.3.6: 提示编辑器含全部字段（identifier, name, role, content, system_prompt[只读], marker[只读], position, injection_position, injection_depth, injection_order, forbid_overrides, extension）
  - [x] SubTask 2.3.7: 管理器窗口位置和尺寸持久化
  - [x] SubTask 2.3.8: 启用 Task 1.9.1 中的预设选择下拉框

- [x] Task 2.4: 实现宏替换引擎
  - [x] SubTask 2.4.1: 定义内置宏（`{{book}}`, `{{protagonist}}`, `{{chapter_title}}`, `{{chapter_index}}`, `{{target_words}}` 等）
  - [x] SubTask 2.4.2: 实现 `MacroEngine.substitute(text, context) -> text`
  - [x] SubTask 2.4.3: 宏替换结果缓存

- [x] Task 2.5: 实现 Token 计数（须先于 Task 2.6 完成）
  - [x] SubTask 2.5.1: OpenAI 模型用 tiktoken（cl100k_base / o200k_base），tiktoken >= 0.6
  - [x] SubTask 2.5.2: 非 OpenAI 模型字符数估算（中文占比 >70% 用 0.6，<30% 用 /4，混合 0.5）
  - [x] SubTask 2.5.3: token 计数结果缓存（key = text hash + tokenizer 名）
  - [x] SubTask 2.5.4: 状态栏提示 token 计数方式（精确/估算）
  - [x] SubTask 2.5.5: 设置中支持为特定模型配置自定义 tokenizer（如 Claude 用 anthropic tokenizer 若安装）

- [x] Task 2.6: 实现提示词组装管线 - 阶段1排序与阶段2注入（依赖 Task 2.5）
  - [x] SubTask 2.6.1: 阶段1（排序）：按 prompt_order 排列启用提示，marker 保留占位
  - [x] SubTask 2.6.2: 阶段2（深度注入）：ABSOLUTE 提示按 depth 从小到大、同深度按 injection_order 从大到小、同优先级按 role 排序，splice 插入历史
  - [x] SubTask 2.6.3: depth=0 插入到最后一条历史消息之后，depth=1 插入到倒数第 1 条之前
  - [x] SubTask 2.6.4: chatHistory 章节消息 role 为 user，content 格式为 `{标题}\n{正文}`
  - [x] SubTask 2.6.5: RELATIVE 与 ABSOLUTE 混合排序

- [x] Task 2.7: 实现提示词组装管线 - 阶段3裁剪与降级（依赖 Task 2.6）
  - [x] SubTask 2.7.1: 阶段3（Token 裁剪）：总预算 = max_context - max_tokens - 系统提示 - 注入提示
  - [x] SubTask 2.7.2: 历史从新到旧填充，预算不足停止
  - [x] SubTask 2.7.3: 注入提示视为不可裁剪项
  - [x] SubTask 2.7.4: 单章超限降级（从末尾截取，<500 token 弹窗提示，至少保留当前章节）
  - [x] SubTask 2.7.5: Token 裁剪导致历史不足 1 章时状态栏警告，不阻止续写
  - [x] SubTask 2.7.6: Token 裁剪在模板渲染前执行，渲染后容忍 10% 超限

- [x] Task 2.8: 实现提示词组装管线 - Marker 注入与 ContextEntry（依赖 Task 2.7）
  - [x] SubTask 2.8.1: worldInfoBefore/After marker 注入：所有条目用 `\n` 拼接为单条 system 消息（对齐 ST，非按 role 分组）
  - [x] SubTask 2.8.2: at_depth ContextEntry 注入（复用 Prompt 深度注入规则，按 depth 升序、同 depth 按 order 升序）
  - [x] SubTask 2.8.3: at_depth 条目不参与 worldInfoBefore/After marker 拼接

- [x] Task 2.9: 集成提示词管线到续写流程
  - [x] SubTask 2.9.1: ContinuationService 调用 PromptAssembler
  - [x] SubTask 2.9.2: 宏替换 → Jinja2 渲染（M3 接入）→ 正则应用（M3 接入）顺序
  - [x] SubTask 2.9.3: 状态栏显示 token 预算使用情况（含渲染前后差值）
  - [x] SubTask 2.9.4: 验证标准：给定测试预设和 10 章历史，组装结果 messages 数组与预期完全一致

---

## M3：正则与模板（已完成）

- [x] Task 3.1: 实现正则脚本数据模型与存储
  - [x] SubTask 3.1.1: 定义 RegexScript pydantic 模型（字段：id, scriptName, findRegex, replaceString, trimStrings, placement, disabled, markdownOnly, promptOnly, runOnEdit, substituteRegex, minDepth, maxDepth, markupSafety）
  - [x] SubTask 3.1.2: 实现正则 JSON 存储（按作用域：global/scoped/preset）
  - [x] SubTask 3.1.3: 实现脚本优先级排序：GLOBAL(0) → PRESET(2) → SCOPED(1)（对齐 ST `SCRIPT_TYPES` 对象插入序）

- [x] Task 3.2: 实现 ST 正则导入导出
  - [x] SubTask 3.2.1: 解析 ST 正则 JSON（单个对象或数组）
  - [x] SubTask 3.2.2: 保留所有字段，导出时原样写回
  - [x] SubTask 3.2.3: 导入时未识别 placement 值（0=MD_DISPLAY, 3=SLASH_COMMAND, 4=sendAs, 6=REASONING）忽略并记录 WARNING 日志

- [x] Task 3.3: 实现正则引擎
  - [x] SubTask 3.3.1: 解析 ST 格式 findRegex（`/pattern/flags`）
  - [x] SubTask 3.3.2: flag 映射：g 不映射，i→IGNORECASE, m→MULTILINE, s→DOTALL, u→UNICODE, y 忽略
  - [x] SubTask 3.3.3: 使用 `regex` 库编译（支持 `\p{Unicode}`、lookbehind）
  - [x] SubTask 3.3.4: 替换字符串语法转换（`$1`→`\1`, `$<name>`→`\g<name>`, `{{match}}`→`\g<0>`）
  - [x] SubTask 3.3.5: trimStrings 裁剪
  - [x] SubTask 3.3.6: 替换后宏替换（substituteRegex 配置）
  - [x] SubTask 3.3.7: depth 过滤（minDepth/maxDepth，depth=0 为最新消息）
  - [x] SubTask 3.3.8: 正则编译错误捕获，跳过脚本、ERROR 日志、UI 错误图标，不影响其他脚本
  - [x] SubTask 3.3.9: 建立 ST 正则测试用例集，验证 regex 库与 JS 正则行为一致性（含零宽匹配、Unicode 类、回溯）

- [x] Task 3.4: 实现正则管理器 UI
  - [x] SubTask 3.4.1: 脚本列表（按作用域分组，显示执行顺序编号）
  - [x] SubTask 3.4.2: 脚本编辑（名称、findRegex、replaceString、trimStrings、placement、depth、substituteRegex）
  - [x] SubTask 3.4.3: 启用/禁用复选框
  - [x] SubTask 3.4.4: 正则单独测试对话框（输入文本，显示匹配高亮和替换结果）
  - [x] SubTask 3.4.5: 管理器窗口位置和尺寸持久化

- [x] Task 3.5: 实现分层变量存储
  - [x] SubTask 3.5.1: 定义变量作用域（global/project/chapter/cache）
  - [x] SubTask 3.5.2: 实现 VariableStore（global/project 持久化 JSON，chapter 持久化到章节元数据，cache 内存）
  - [x] SubTask 3.5.3: 实现 getvar/setvar/hasvar/delvar API

- [x] Task 3.6: 实现 Jinja2 沙箱模板引擎
  - [x] SubTask 3.6.1: 初始化 ImmutableSandboxedEnvironment
  - [x] SubTask 3.6.2: 自定义 is_safe_attribute（拒绝 `_` 开头属性）
  - [x] SubTask 3.6.3: 禁用 attr 过滤器
  - [x] SubTask 3.6.4: 注册白名单函数（getvar, setvar, hasvar, delvar, get_chapter, get_chapters, get_current_chapter, get_chapter_count, get_book, get_protagonist, get_novel_profile, get_writing_style, get_context_entries, regex_apply, substitute_macros, now, word_count, truncate）
  - [x] SubTask 3.6.5: 设置 recursion_limit=50，递归超限跳过模板用原始文本
  - [x] SubTask 3.6.6: 子进程执行模板（multiprocessing），5 秒超时 terminate
  - [x] SubTask 3.6.7: 验证 multiprocessing 在 Windows/macOS/Linux 三平台的 PySide6 兼容性（spawn 模式、pickle 序列化、僵尸进程清理），必要时改用 concurrent.futures.ProcessPoolExecutor

- [x] Task 3.7: 实现双阶段模板执行
  - [x] SubTask 3.7.1: 发送前执行（render_pre_send）：扫描 `{% %}`/`{{ }}`，沙箱执行，替换
  - [x] SubTask 3.7.2: 接收后执行（render_post_receive）：扫描输出模板，执行（可 setvar），替换
  - [x] SubTask 3.7.3: 最终显示文本不含 Jinja2 语法
  - [x] SubTask 3.7.4: 模板语法错误（TemplateSyntaxError）捕获，跳过渲染用原始文本，ERROR 日志，UI 非阻塞警告

- [x] Task 3.8: 实现模板编辑器 UI
  - [x] SubTask 3.8.1: 作用域选择（global/project/chapter）
  - [x] SubTask 3.8.2: 变量列表显示与新建
  - [x] SubTask 3.8.3: 模板编辑区（支持 Jinja2 语法）
  - [x] SubTask 3.8.4: 渲染预览区（实时或点击测试渲染）
  - [x] SubTask 3.8.5: 可用函数列表显示
  - [x] SubTask 3.8.6: 管理器窗口位置和尺寸持久化

- [x] Task 3.9: 集成正则与模板到续写流程
  - [x] SubTask 3.9.1: 组装提示词时对 content 应用 USER_INPUT 正则
  - [x] SubTask 3.9.2: 续写结果后处理应用 AI_OUTPUT 正则
  - [x] SubTask 3.9.3: 上下文提取条目注入前应用 WORLD_INFO 正则
  - [x] SubTask 3.9.4: 执行顺序：宏替换 → Jinja2 渲染 → 正则应用（不递归）

---

## M4：自动上下文提取（已完成）

- [x] Task 4.1: 实现上下文提取器
  - [x] SubTask 4.1.1: 定义 ContextEntry pydantic 模型（含 depth, source_chapter_range[可 null], order[升序] 字段）
  - [x] SubTask 4.1.2: 实现提取提示词模板（`resources/defaults/extract_prompt.txt`）
  - [x] SubTask 4.1.3: 读取 `extractor_prompt_override`，非 null 时使用自定义提示词
  - [x] SubTask 4.1.4: 构造提取请求（前 N 章正文 + 小说档案，N 默认 5 可被 Project.extract_config.lookback_chapters 覆盖）
  - [x] SubTask 4.1.5: 调用提取模型（非流式，默认 gpt-4o-mini 可被覆盖）
  - [x] SubTask 4.1.6: 解析 JSON → list[ContextEntry]，失败时修复（去除 markdown 标记）
  - [x] SubTask 4.1.7: 校验必填字段，content 长度截断 200 字
  - [x] SubTask 4.1.8: 章节数不足 N 时提取所有可用章节，0 章时跳过提取并状态栏提示"无前文可提取"

- [x] Task 4.2: 实现 ST 世界书导入
  - [x] SubTask 4.2.1: 解析 ST 世界书 JSON，转换为 ContextEntry
  - [x] SubTask 4.2.2: position 数字→字符串转换（0→before, 1→after, 2→at_depth）
  - [x] SubTask 4.2.3: 忽略 probability 字段，source_chapter_range 设为 null

- [x] Task 4.3: 实现分段缓存策略
  - [x] SubTask 4.3.1: 按章节 hash 分段缓存（key = 各章节 hash 组合）
  - [x] SubTask 4.3.2: 仅重新提取变化的章节
  - [x] SubTask 4.3.3: 缓存有效期默认 24 小时
  - [x] SubTask 4.3.4: 强制重新提取（跳过缓存）

- [x] Task 4.4: 实现上下文提取预览面板
  - [x] SubTask 4.4.1: 提取中 loading 动画与"提取中..."文本
  - [x] SubTask 4.4.2: 提取中续写按钮禁用
  - [x] SubTask 4.4.3: 取消提取按钮
  - [x] SubTask 4.4.4: 提取完成自动刷新预览
  - [x] SubTask 4.4.5: 按分类分组显示（人物/地点/事件/风格/剧情状态）
  - [x] SubTask 4.4.6: 每条可展开查看、编辑、禁用
  - [x] SubTask 4.4.7: 手动添加条目
  - [x] SubTask 4.4.8: 显示提取耗时和 token 消耗

- [x] Task 4.5: 实现提取失败与取消处理
  - [x] SubTask 4.5.1: 弹窗提示"上下文提取失败"
  - [x] SubTask 4.5.2: 三选项：重试 / 跳过提取继续续写 / 取消续写
  - [x] SubTask 4.5.3: 跳过时状态栏警告
  - [x] SubTask 4.5.4: 用户主动取消提取后复用三选项弹窗（与提取失败处理一致）

- [x] Task 4.6: 集成上下文提取到续写流程
  - [x] SubTask 4.6.1: ContinuationService 在组装提示词前调用 ContextExtractor
  - [x] SubTask 4.6.2: 提取条目注入 worldInfoBefore/After marker（单条 system 消息，`\n` 拼接）
  - [x] SubTask 4.6.3: 提取快照存入 swipe

---

## M5：完善与打磨（已完成）

- [x] Task 5.1: 实现完整项目管理
  - [x] SubTask 5.1.1: 多项目切换（项目列表面板）
  - [x] SubTask 5.1.2: 每个项目独立绑定预设、正则、提取配置
  - [x] SubTask 5.1.3: 项目导出备份（zip，含 manifest.json）
  - [x] SubTask 5.1.4: 项目导入恢复

- [x] Task 5.2: 实现导出功能
  - [x] SubTask 5.2.1: 导出完整 TXT（章节顺序拼接，标题可选）
  - [x] SubTask 5.2.2: 导出单章 TXT/Markdown（Markdown 含 H1 章节标题）
  - [x] SubTask 5.2.3: 导出项目备份 zip
  - [x] SubTask 5.2.4: 启用 Task 1.4.3 中的导出菜单项

- [x] Task 5.3: 实现续写历史日志
  - [x] SubTask 5.3.1: 记录每次续写（含中断、失败）到 SQLite：id, project_id, chapter_id, swipe_id, started_at, finished_at, status, model, parameters_json, prompt_messages_json, output_text, error_message
  - [x] SubTask 5.3.2: 历史日志查看面板支持按项目、章节、时间筛选
  - [x] SubTask 5.3.3: 点击日志条目可查看完整提示词和输出

- [x] Task 5.4: 实现主题与字体
  - [x] SubTask 5.4.1: 暗色/亮色/跟随系统主题切换（QSS + 系统主题监听）
  - [x] SubTask 5.4.2: 正文字体、大小、行距可调

- [x] Task 5.5: 性能验证
  - [x] SubTask 5.5.1: 章节正文按需加载（切换时释放未修改章节内存）
  - [x] SubTask 5.5.2: 验证 500 章项目内存 < 300MB
  - [x] SubTask 5.5.3: 验证单章 10 万字在 QPlainTextEdit 中编辑流畅

- [x] Task 5.6: 实现管理器快捷键
  - [x] SubTask 5.6.1: Ctrl+P 预设, Ctrl+G 正则, Ctrl+T 模板, Ctrl+, 设置
  - [x] SubTask 5.6.2: F5 强制重新提取

- [x] Task 5.7: PyInstaller 打包
  - [x] SubTask 5.7.1: 编写打包配置
  - [x] SubTask 5.7.2: 验证 Windows/macOS/Linux 打包可运行

---

# Task Dependencies

- Task 0.1 → 所有后续任务（项目结构）
- Task 0.2 → Task 1.1, 1.10, 2.1, 3.5, 4.3, 5.3（存储与数据模型依赖）
- Task 0.3 → Task 1.6, 5.1（加密与配置依赖）
- Task 0.4 → Task 1.7（LLM 调用需要日志脱敏）
- Task 0.5 → 所有 UI 任务（主窗口依赖）
- Task 1.1 → Task 1.2, 1.3, 1.4, 1.5, 1.12（章节管理依赖导入）
- Task 1.2 → Task 1.3（拆分点调整依赖编辑器）
- Task 1.4 → Task 1.11（swipe 子节点显示依赖章节列表 UI）
- Task 1.5 → Task 5.1（多项目管理依赖单项目管理）
- Task 1.6 → Task 1.7（API 配置依赖）
- Task 1.7 → Task 1.8, 1.9（流式调用依赖）
- Task 1.8 → Task 1.9, 1.10, 1.11（Worker 依赖）
- Task 1.9 → Task 1.11（swipe 元数据显示依赖续写面板）
- Task 1.10 → Task 1.11, 4.6, 5.3（swipe 数据模型依赖）
- Task 1.11 → Task 1.12（部分边界情况依赖 swipe 操作）
- Task 1.12 → 依赖 Task 1.1（章节边界）、Task 1.7（API 错误）、Task 1.4（章节删除）
- Task 2.1 → Task 2.2, 2.3, 2.6（预设模型依赖）
- Task 2.3 → Task 1.9（启用预设选择下拉框）
- Task 2.4 → Task 2.6（宏替换依赖）
- Task 2.5 → Task 2.6, 2.7（Token 计数依赖，须先于管线完成）
- Task 2.6 → Task 2.7（阶段1/2 依赖）
- Task 2.7 → Task 2.8（阶段3依赖）
- Task 2.8 → Task 2.9（marker 注入依赖）
- Task 2.9 → Task 3.9（正则/模板集成依赖提示词管线）
- Task 2.9 → Task 4.6（上下文提取集成依赖提示词管线）
- Task 3.1 → Task 3.2, 3.3, 3.4（正则模型依赖）
- Task 3.3 → Task 3.9（正则集成依赖引擎）
- Task 3.5 → Task 3.6, 3.7（变量存储依赖）
- Task 3.6 → Task 3.7, 3.8（模板引擎依赖）
- Task 3.7 → Task 3.9（模板集成依赖双阶段执行）
- Task 4.1 → Task 4.3, 4.4, 4.6（提取器依赖）
- Task 4.5 → 依赖 Task 4.1（提取器）和 Task 4.4（UI）
- Task 4.6 → 依赖 Task 2.8（提示词管线 marker）、Task 4.1（提取器）、Task 1.10（swipe 快照）

**可并行任务**：
- M0: Task 0.4 可与 Task 0.2/0.3 并行；Task 0.5 依赖 Task 0.3 完成配置加载骨架后并行
- M1: Task 1.4（章节列表 UI）与 Task 1.6（API 配置）可并行；Task 1.5（项目管理）与 Task 1.6 可并行
- M2: Task 2.4（宏替换）与 Task 2.5（Token 计数）可并行
- M3: Task 3.1-3.4（正则）与 Task 3.5-3.8（模板）可并行；Task 3.9 依赖两条线全部完成
- M4: Task 4.3（缓存）与 Task 4.4（UI）可并行；Task 4.2（ST 世界书导入）可与 Task 4.3 并行
