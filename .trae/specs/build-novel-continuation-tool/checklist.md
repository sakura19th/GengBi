# Checklist

> **Change-ID**: build-novel-continuation-tool
> **对应 Spec**: spec.md v2.1
> **用途**: 实现完成后逐项验证，确保符合规范

---

## M0：脚手架与架构

### 项目结构
- [ ] 项目目录结构完整（ui/core/services/models/utils/resources）
- [ ] requirements.txt 列出所有依赖且版本符合 spec 要求
- [ ] requirements.txt 中**不含** qasync（已移除）
- [ ] 默认资源文件存在（default_preset.json、extract_prompt.txt、default_regex_scripts.json、dark.qss、light.qss）
- [ ] main.py 能启动主窗口

### 数据模型
- [ ] Chapter 模型字段完整：id, project_id, index, title, content, word_count, continuations, metadata, created_at, updated_at
- [ ] Chapter 不含 is_original 字段
- [ ] continuations 为空列表表示无续写版本
- [ ] Project 模型字段完整：id, name, created_at, updated_at, source_file, novel_profile, preset_id, regex_script_ids, extract_config, chapter_split_rule
- [ ] novel_profile 子结构完整（title/author/protagonist/synopsis/world_setting/writing_style）
- [ ] chapter_split_rule 子结构完整（pattern/include_title_in_content/manual_overrides）
- [ ] manual_overrides 元素结构完整（action/chapter_id/position）
- [ ] extract_config 为 null 时使用全局 context_extract，非 null 时按字段覆盖
- [ ] Continuation 模型字段完整：id, created_at, content, model, is_accepted, status, created_by, parameters_snapshot, preset_id, preset_snapshot, regex_script_ids_snapshot, extracted_context_snapshot, prompt_snapshot, reasoning_content
- [ ] prompt_snapshot 类型为 list[dict]（messages 数组）

### 全局配置
- [ ] Config 顶层字段完整：config_version, api_endpoints, default_endpoint_id, appearance, continuation, context_extract, data
- [ ] appearance 子结构完整（theme[dark/light/system]/font_family/font_size/line_height）
- [ ] continuation 子结构完整（default_lookback_chapters[默认5]/default_target_words/default_temperature/default_max_tokens/auto_save/show_token_count）
- [ ] context_extract 子结构完整（extractor_model[默认gpt-4o-mini]/cache_enabled[默认true]/cache_ttl_hours[默认24]/extractor_prompt_override/lookback_chapters[默认5]）
- [ ] data 子结构完整（storage_path[默认~/.novelforge]）

### 存储层
- [ ] SQLite 启用 WAL 模式（`PRAGMA journal_mode=WAL`）
- [ ] SQLite 设置 `busy_timeout=5000`
- [ ] 网络文件系统检测，降级为 DELETE 模式并警告
- [ ] 章节文件写入使用临时文件 → fsync → rename 原子操作
- [ ] SQLite 写入失败时删除已写入的章节文件并回滚
- [ ] 关键操作（保存预设/正则/项目配置）前写 `.bak` 文件
- [ ] 启动时检测 JSON 文件损坏并尝试 `.bak` 恢复
- [ ] `.bak` 也损坏时弹窗提示用户选择"重置该项配置"或"手动修复"
- [ ] SQLite 访问线程模型明确（每线程独立连接或主线程代理）
- [ ] aiosqlite 跨线程访问限制已验证

### 加密与配置
- [ ] API Key 加密使用 PBKDF2HMAC + 随机 salt（非直接 MAC+用户名）
- [ ] 密钥派生结果缓存避免重复开销
- [ ] 加密密钥可导出/导入（密码保护文件）
- [ ] 导入密钥到新机器时使用新机器 ID 重新加密所有 API Key，更新 api_key_encrypted 字段
- [ ] config_version 迁移函数链实现（备份→迁移→回滚）
- [ ] 迁移前备份原配置到 .bak
- [ ] 迁移失败时回滚并提示用户
- [ ] 首次启动显示隐私声明对话框

### 日志
- [ ] 日志按天滚动，保留 7 天
- [ ] 日志 API Key 脱敏为 `sk-****`
- [ ] 日志请求体只记录摘要（前 200 字 + 总字数）
- [ ] 日志不记录完整小说内容
- [ ] DEBUG 级别日志可通过设置开关控制

### 主窗口
- [ ] 主窗口三栏布局（QSplitter）
- [ ] 面板可折叠、可拖拽调宽
- [ ] 窗口尺寸和面板宽度持久化
- [ ] 最小窗口尺寸 1024×700
- [ ] 状态栏含保存状态显示位
- [ ] 空项目创建入口（菜单"新建项目"或欢迎页）

## M1：核心续写闭环

### TXT 导入与章节拆分
- [ ] TXT 导入支持默认章节标题正则 `^第[一二三四五六七八九十百千零\d]+[章节回卷]`
- [ ] 100 万字 TXT 3 秒内完成拆分（不含 token 计数）
- [ ] 章节存入文件系统路径 `~/.novelforge/projects/{project_id}/chapters/{chapter_id}.txt`
- [ ] 项目元数据写入 SQLite
- [ ] 导入 TXT 无章节标题匹配时整文作为单章导入
- [ ] 无匹配时单章标题为"全文"或文件名
- [ ] 状态栏提示"未检测到章节标题，已作为单章导入"
- [ ] 空章节（连续两个标题）保留，content 为空字符串
- [ ] 空章节在列表中正常显示，可编辑或删除

### 章节预览与编辑
- [ ] 预览（只读 QPlainTextEdit）与编辑模式切换
- [ ] 编辑后 5 秒空闲自动保存
- [ ] 状态栏显示"已保存"或"未保存修改"
- [ ] 支持 Ctrl+Z / Ctrl+Y undo/redo
- [ ] 流式输出时编辑区锁定为只读并显示"续写中"

### 章节拆分点调整
- [ ] 编辑器中选中文本右键"在此处拆分"功能正常
- [ ] 拆分后章节 index 自动重排
- [ ] 原章节 continuations 归属拆分后的前一个章节
- [ ] 合并章节（与下一章合并）
- [ ] 合并章节后两章的 continuations 都归属合并后的章节
- [ ] 拆分/合并支持 undo

### 章节列表 UI
- [ ] QTreeView 树形显示章节（`setUniformRowHeights(True)` 支持虚拟滚动）
- [ ] 已有续写的章节显示 swipe 子节点
- [ ] 右键菜单（重命名、删除、合并、拆分、导出）
- [ ] M1 阶段导出菜单项灰显
- [ ] 标题实时搜索
- [ ] 全文搜索按钮，异步执行、可取消、显示进度
- [ ] 500 章项目首屏渲染 < 500ms

### 项目管理基础
- [ ] 项目列表面板（最近项目列表、打开、删除）
- [ ] 项目切换（保存当前项目状态后切换）

### API 连接配置
- [ ] API 端点管理（增删改）
- [ ] API Key 加密存储，UI 显示 `sk-****`，需点击显示才可见
- [ ] 默认端点选择
- [ ] 从 API 获取模型列表（GET `/models`），解析 `data[].id`
- [ ] 获取模型列表失败时不阻塞端点保存
- [ ] 续写时可从下拉框选择端点

### LLM 流式调用
- [ ] SSE 流式解析使用增量 UTF-8 解码器（`codecs.getincrementaldecoder`）
- [ ] 使用 `stream: true` 发送 POST 请求到 `{endpoint}/chat/completions`
- [ ] 多行 `data:` 字段正确拼接
- [ ] API 4xx/5xx 错误时读取 error body（非 `raise_for_status`）
- [ ] API 错误时不创建 swipe（区别于流式中断）
- [ ] API 429 限流读取 Retry-After，最多重试 3 次
- [ ] 429 重试时 UI Toast 提示"API 限流，等待 N 秒后重试"
- [ ] API 401 认证失败不重试，弹窗提示并跳转设置
- [ ] 推理内容（`reasoning_content`）单独存储
- [ ] 推理内容 UI 可折叠显示
- [ ] 推理内容不参与后续提示词组装
- [ ] 收到 `[DONE]` 正确结束流

### ContinuationWorker
- [ ] ContinuationWorker 使用 QThread + 独立 asyncio 事件循环
- [ ] **不使用** qasync
- [ ] 线程停止标志使用 `threading.Event` + `asyncio.Task.cancel()` 双重中断
- [ ] QThread 退出时 asyncio 事件循环正确关闭（loop.close()、aiohttp session 清理、无僵尸线程）
- [ ] 流式中断时 ≥100 字文本存为 swipe（status=interrupted）
- [ ] 流式中断时 <100 字文本丢弃
- [ ] swipe 记录中断原因（network_error/user_stopped/api_error）

### 续写控制面板
- [ ] M1 阶段预设选择为占位下拉框禁用
- [ ] 流式输出区使用 QPlainTextEdit + QTimer 50ms 节流批量更新
- [ ] 流式中按钮（停止），流结束后按钮（重写、接受并追加、接受并继续、编辑后接受）
- [ ] 光标动画（█ 闪烁）
- [ ] 滚动自动跟随（可锁定）
- [ ] 推理内容可折叠显示区域
- [ ] 50+ chunks/秒高频场景下 50ms 节流有效
- [ ] 流式输出 CPU 占用 < 30%（单核）

### Swipe 管理
- [ ] Continuation 数据结构含完整快照（spec 定义的 Continuation 全部字段，含 is_accepted）
- [ ] swipe 子节点点击切换显示
- [ ] swipe 元数据显示（模型、参数、时间、字数）
- [ ] 接受续写标记 is_accepted，其他 swipe 设为 false
- [ ] 接受后 content 追加到章节正文并保存
- [ ] "接受并继续续写"快捷操作可用，新 swipe 归属当前章节
- [ ] "接受并继续续写"的新续写基于追加后的完整章节内容
- [ ] "重写"沿用上次参数，用户可在续写配置区调整参数，created_by=rewrite，显示上次结果供参考
- [ ] swipe 删除弹窗确认，已接受版本先取消接受，支持 undo
- [ ] "并排对比"按钮弹出对比窗口显示两个 swipe 的 diff

### 边界情况
- [ ] 章节删除弹窗确认，删除文件+SQLite+swipe，index 重排，支持 undo
- [ ] 章节删除 undo 时恢复文件和 SQLite 记录

### M1 基础快捷键
- [ ] Ctrl+O 导入
- [ ] Ctrl+S 保存
- [ ] Ctrl+Enter 续写
- [ ] Ctrl+R 重写
- [ ] Ctrl+Shift+A 接受
- [ ] Ctrl+Shift+E 编辑后接受
- [ ] Esc 停止流式

## M2：提示词管线

### 预设数据模型
- [ ] WritingPreset, Prompt pydantic 模型字段对齐 ST
- [ ] Prompt 字段完整：identifier, name, role, content, system_prompt, marker, position, injection_position, injection_depth, injection_order, forbid_overrides, extension
- [ ] Prompt.position 语义为 start/end（**对齐 ST**，非 before/after）
- [ ] 预设 JSON 存储与加载正常
- [ ] 默认预设 `resources/defaults/default_preset.json` 存在且格式正确

### ST 预设导入导出
- [ ] ST 预设导入：character_id==100000 取第一个匹配项
- [ ] 非 100000 的 character_id 条目忽略并记录日志
- [ ] 本工具创建预设 character_id 统一用 100000
- [ ] 未识别字段保留在 _raw_st_fields，导出原样写回
- [ ] 导入 ST 预设时 position 原样保留（start/end）

### 预设管理器 UI
- [ ] 提示词列表拖拽排序
- [ ] 每个提示启用/禁用复选框
- [ ] marker 提示显示 `[marker]` 不可删除
- [ ] system_prompt 提示不可删除可禁用
- [ ] 提示编辑器含全部字段（system_prompt/marker 只读，其余可编辑）
- [ ] 管理器窗口位置和尺寸持久化
- [ ] 启用 Task 1.9.1 中的预设选择下拉框

### 宏替换
- [ ] 宏替换引擎支持所有内置宏（{{book}}, {{protagonist}}, {{chapter_title}} 等）
- [ ] 宏替换结果缓存

### Token 计数
- [ ] OpenAI 模型用 tiktoken（cl100k_base / o200k_base），tiktoken >= 0.6
- [ ] 非 OpenAI 模型字符数估算（中文占比 >70% 用 0.6，<30% 用 /4，混合 0.5）
- [ ] token 计数结果缓存
- [ ] 状态栏提示 token 计数方式（精确/估算）
- [ ] 设置中支持为特定模型配置自定义 tokenizer

### 提示词组装管线
- [ ] 三阶段顺序：**排序 → 注入 → 裁剪**（非 裁剪 → 注入）
- [ ] 阶段1 排序：按 prompt_order 排列，marker 保留占位
- [ ] 阶段2 深度注入：ABSOLUTE 提示按 depth 从小到大、同深度按 injection_order 从大到小、同优先级按 role 排序
- [ ] depth=0 表示插入到最后一条历史消息之后
- [ ] depth=1 表示插入到倒数第 1 条历史消息之前
- [ ] 阶段3 Token 裁剪：总预算 = max_context - max_tokens - 系统提示 - 注入提示
- [ ] 注入提示视为不可裁剪项
- [ ] 历史从新到旧填充，预算不足停止
- [ ] 单章超限时从末尾截取（保留最新内容）
- [ ] 至少保留当前续写点章节（即使截断）
- [ ] 截取后 <500 token 弹窗提示增大 max_context 或减小 max_tokens
- [ ] Token 裁剪导致历史不足 1 章时状态栏警告
- [ ] 上下文严重不足时不阻止续写
- [ ] worldInfoBefore/After marker 注入：所有条目用 `\n` 拼接为**单条 system 消息**（对齐 ST，非按 role 分组）
- [ ] ContextEntry.role 字段在 worldInfoBefore/After 场景不生效（仅 at_depth 生效）
- [ ] 无条目时跳过 marker（不插入空消息）
- [ ] at_depth ContextEntry 按深度注入历史，复用 Prompt 深度注入规则
- [ ] at_depth 条目按 depth 升序、同 depth 按 order 升序排序
- [ ] at_depth 条目不参与 worldInfoBefore/After marker 拼接
- [ ] chatHistory 章节消息 role 为 user，content 格式为 `{章节标题}\n{章节正文}`
- [ ] RELATIVE 与 ABSOLUTE 混合排序：RELATIVE 按 order 排列历史前后，ABSOLUTE 注入历史
- [ ] Token 裁剪在模板渲染前执行
- [ ] 模板渲染后总 token 超限时容忍 10% 超限（不二次裁剪）
- [ ] 状态栏显示渲染前后 token 差值
- [ ] 状态栏显示 token 预算使用情况
- [ ] 验证标准：给定测试预设和 10 章历史，组装结果 messages 数组与预期完全一致

## M3：正则与模板

### 正则数据模型
- [ ] RegexScript pydantic 模型字段完整：id, scriptName, findRegex, replaceString, trimStrings, placement, disabled, markdownOnly, promptOnly, runOnEdit, substituteRegex, minDepth, maxDepth, markupSafety
- [ ] 正则 JSON 按作用域存储（global/scoped/preset）
- [ ] 脚本优先级排序：**GLOBAL(0) → PRESET(2) → SCOPED(1)**（对齐 ST `SCRIPT_TYPES` 对象插入序，非数值大小）

### ST 正则导入
- [ ] ST 正则导入支持单个对象或数组
- [ ] 导入时未识别 placement 值（0=MD_DISPLAY、3=SLASH_COMMAND、4=sendAs、6=REASONING）忽略并记录 WARNING 日志
- [ ] 本工具仅处理 USER_INPUT(1)、AI_OUTPUT(2)、WORLD_INFO(5) 三种 placement

### 正则引擎
- [ ] findRegex 解析 `/pattern/flags` 格式
- [ ] **g flag 不映射任何 Python flag**（关键修正点）
- [ ] i→IGNORECASE, m→MULTILINE, s→DOTALL, u→UNICODE
- [ ] y flag 忽略并记录日志
- [ ] 使用 `regex` 库（**非** re 库）编译
- [ ] 替换字符串转换：`$1`→`\1`, `$<name>`→`\g<name>`, `{{match}}`→`\g<0>`
- [ ] trimStrings 裁剪功能正常
- [ ] 替换后宏替换（substituteRegex 配置允许时）
- [ ] depth 过滤（minDepth/maxDepth，depth=0 为最新消息）正常
- [ ] 正则编译错误时跳过该脚本，记录 ERROR 日志，UI 显示错误图标，不影响其他脚本
- [ ] ST 正则测试用例集通过（含零宽匹配、Unicode 类、回溯）

### 正则管理器 UI
- [ ] 脚本列表按作用域分组，显示执行顺序编号
- [ ] 正则单独测试对话框（输入文本，显示匹配高亮和替换结果）
- [ ] 管理器窗口位置和尺寸持久化

### 变量存储
- [ ] 变量作用域：global/project/chapter/cache
- [ ] global/project 持久化 JSON，chapter 持久化到章节元数据，cache 内存
- [ ] getvar/setvar/hasvar/delvar API 正常

### Jinja2 沙箱
- [ ] Jinja2 使用 **ImmutableSandboxedEnvironment**（非 SandboxedEnvironment）
- [ ] 自定义 is_safe_attribute 拒绝 `_` 开头属性
- [ ] 禁用 attr 过滤器
- [ ] 白名单函数全部注册且可用：getvar, setvar, hasvar, delvar, get_chapter, get_chapters, get_current_chapter, get_chapter_count, get_book, get_protagonist, get_novel_profile, get_writing_style, get_context_entries, regex_apply, substitute_macros, now, word_count, truncate
- [ ] recursion_limit=50
- [ ] 模板递归超限（>50）时跳过模板使用原始文本
- [ ] 模板在子进程中执行（multiprocessing）
- [ ] 5 秒超时后 terminate 子进程
- [ ] 超时后跳过模板使用原始文本，记录 ERROR 日志
- [ ] multiprocessing 在 Windows/macOS/Linux 三平台 PySide6 兼容性验证通过

### 双阶段模板
- [ ] 模板语法错误（TemplateSyntaxError）捕获，跳过渲染用原始文本，ERROR 日志，UI 非阻塞警告
- [ ] 发送前模板执行：扫描 `{% %}`/`{{ }}`，沙箱执行，替换
- [ ] 接收后模板执行：扫描输出模板，执行（可 setvar），替换
- [ ] 最终显示文本不含 Jinja2 语法
- [ ] 变量作用域访问统一用 `getvar(name, scope=...)`（**不支持** `global.x` 点号语法）
- [ ] 执行顺序：宏替换 → Jinja2 渲染 → 正则应用（不递归）

### 模板编辑器 UI
- [ ] 作用域选择、变量列表、模板编辑、渲染预览、可用函数列表
- [ ] 管理器窗口位置和尺寸持久化

### 集成
- [ ] 组装提示词时对 content 应用 USER_INPUT 正则
- [ ] 续写结果后处理应用 AI_OUTPUT 正则
- [ ] 上下文提取条目注入前应用 WORLD_INFO 正则

## M4：自动上下文提取

### ContextEntry 数据结构
- [ ] ContextEntry pydantic 模型字段完整：uid, category, key, comment, content, order, position, depth, role, source_chapter_range, extracted_at
- [ ] ContextEntry.position 用字符串（before/after/at_depth）
- [ ] ContextEntry.order 统一升序（数字越小越优先）
- [ ] ContextEntry.role 仅 at_depth 注入时生效，worldInfoBefore/After 固定 system
- [ ] source_chapter_range 导入 ST 世界书时为 null
- [ ] ContextEntry 不含 probability 字段，导入 ST 世界书时忽略 probability

### 提取流程
- [ ] 提取提示词模板 `resources/defaults/extract_prompt.txt` 存在
- [ ] 读取 extractor_prompt_override，非 null 时使用自定义提示词
- [ ] 提取默认取前 5 章正文（可被 Project.extract_config.lookback_chapters 覆盖）
- [ ] 提取输入包含小说档案（novel_profile）
- [ ] 提取调用非流式 API
- [ ] 提取模型默认 gpt-4o-mini（可被 Project.extract_config.extractor_model 覆盖）
- [ ] JSON 解析失败时尝试修复（去除 markdown 代码块标记）
- [ ] content 长度截断 200 字
- [ ] 章节数不足 N 时提取所有可用章节，不报错
- [ ] 可用章节为 0 时跳过提取，状态栏提示"无前文可提取"

### ST 世界书导入
- [ ] ST 世界书 JSON 导入功能正常
- [ ] position 数字→字符串转换（0→before, 1→after, 2→at_depth）
- [ ] 忽略 probability 字段
- [ ] source_chapter_range 设为 null

### 缓存
- [ ] 按章节 hash 分段缓存
- [ ] 仅重新提取变化的章节
- [ ] 缓存有效期默认 24 小时
- [ ] 强制重新提取跳过缓存

### 提取预览面板
- [ ] 提取中 loading 动画与"提取中..."文本
- [ ] 提取中续写按钮禁用
- [ ] 取消提取按钮可用
- [ ] 提取完成自动刷新预览
- [ ] 按分类分组显示（人物/地点/事件/风格/剧情状态）
- [ ] 每条可展开查看、编辑、禁用
- [ ] 手动添加条目功能
- [ ] 显示提取耗时和 token 消耗

### 提取失败与取消
- [ ] 提取失败弹窗三选项：重试 / 跳过提取继续续写 / 取消续写
- [ ] 跳过时状态栏警告"未提取上下文，续写质量可能下降"
- [ ] 用户主动取消提取后弹出三选项（重试/跳过/取消），与提取失败处理一致

### 集成
- [ ] 提取条目注入 worldInfoBefore/After marker（单条 system 消息，`\n` 拼接）
- [ ] 提取快照存入 swipe

## M5：完善与打磨

### 项目管理
- [ ] 多项目切换功能
- [ ] 每个项目独立绑定预设、正则、提取配置
- [ ] 项目导出备份 zip（含 manifest.json）
- [ ] 项目导入恢复

### 导出
- [ ] 导出完整 TXT（章节顺序拼接，标题可选）
- [ ] 导出单章 TXT/Markdown
- [ ] 导出单章 Markdown 时章节标题作为 H1
- [ ] 启用 Task 1.4.3 中的导出菜单项

### 续写历史日志
- [ ] SQLite 记录字段完整：id, project_id, chapter_id, swipe_id, started_at, finished_at, status, model, parameters_json, prompt_messages_json, output_text, error_message
- [ ] 每次续写完成（含中断、失败）都记录
- [ ] 历史日志查看面板支持按项目、章节、时间筛选
- [ ] 点击日志条目可查看完整提示词和输出

### 主题与字体
- [ ] 暗色/亮色/跟随系统主题切换（QSS + 系统主题监听）
- [ ] 正文字体、大小、行距可调

### 性能验证
- [ ] 章节正文按需加载（切换时释放未修改章节内存）
- [ ] 500 章项目内存 < 300MB
- [ ] 单章 10 万字在 QPlainTextEdit 中编辑流畅

### 管理器快捷键
- [ ] Ctrl+P 预设
- [ ] Ctrl+G 正则
- [ ] Ctrl+T 模板
- [ ] Ctrl+, 设置
- [ ] F5 强制重新提取

### 打包
- [ ] PyInstaller 打包 Windows/macOS/Linux 可运行

---

## 跨里程碑验证（关键修正点）

- [ ] **异步桥接统一为 QThread + 独立事件循环**（无 qasync）
- [ ] **QThread 退出时 asyncio 事件循环正确关闭**
- [ ] **正则 g flag 不映射任何 Python flag**（关键修正）
- [ ] **正则脚本执行顺序为 GLOBAL(0) → PRESET(2) → SCOPED(1)**（关键修正，对齐 ST 对象插入序）
- [ ] **提示词管线顺序为 排序 → 注入 → 裁剪**（关键修正）
- [ ] **worldInfoBefore/After 为单条 system 消息，`\n` 拼接**（关键修正，非按 role 分组）
- [ ] **单章超限从末尾截取**（关键修正，非报错阻止）
- [ ] **tiktoken 仅用于 OpenAI 模型**，非 OpenAI 用字符估算（关键修正）
- [ ] **Jinja2 用 ImmutableSandboxedEnvironment**（关键修正）
- [ ] **模板超时用子进程实现**（关键修正，非 signal.alarm）
- [ ] **multiprocessing 在三平台 PySide6 兼容性验证**
- [ ] **SQLite + 文件系统原子写入**（临时文件 → rename）
- [ ] **SQLite 写入失败时删除已写入文件并回滚**
- [ ] **API Key 加密用 PBKDF2HMAC + 随机 salt**（关键修正）
- [ ] **密钥导入到新机器后重新加密所有 API Key**
- [ ] **config_version 迁移函数链**
- [ ] **日志脱敏**（API Key, 请求体截断+总字数, 不记录小说内容, 按天滚动保留7天）
- [ ] **流式中断 ≥100 字存 swipe**（关键修正）
- [ ] **UI 更新 50ms 节流**（关键修正）
- [ ] **Prompt.position 为 start/end**（关键修正，对齐 ST，非 before/after）
- [ ] **ContextEntry.position 为 before/after/at_depth**（本工具自定义，与 Prompt.position 不同概念）
- [ ] **ContextEntry.order 统一升序**（关键修正，消除 worldInfoBefore/After 与 at_depth 二义性）
- [ ] **变量访问用 getvar(name, scope=...)**（关键修正，非点号语法）
- [ ] **暂停按钮已移除**（只保留停止）
- [ ] **QPlainTextEdit 替代 QTextEdit**（大文本性能）
- [ ] **aiosqlite 跨线程访问限制已处理**

---

## 非目标反向验证

- [ ] 无群聊功能
- [ ] 无 TTS/语音/图片生成
- [ ] 无向量数据库/RAG 检索
- [ ] 无云端同步/多用户
- [ ] 无常驻世界书编辑器（只有自动提取预览）
- [ ] 无 JS 脚本执行
- [ ] 无插入续写（只能在章节末尾追加）
- [ ] 无批量续写
- [ ] 无 EPUB/DOCX 导出
- [ ] 无 i18n（硬编码中文）
