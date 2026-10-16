# 赓笔 CLI 操作指南

CLI 复用桌面端的项目、预设、世界书、提取和写作服务，不需要打开桌面窗口。它通过 OpenAI Chat Completions 兼容接口调用配置的服务商。所有示例中的 ID 都要替换为上一步返回的实际值。

## 1. 启动与选任务

Windows 在 EXE 所在目录打开 PowerShell：

```powershell
.\GengBi_CLI_v0.2.22.exe wizard
```

向导提供模型配置、导入文章、提取世界书/档案、续写、审稿修订、整卷、多阶段流程、世界书管理和候选查询。不带参数运行会显示入门路径。

下文使用 `gengbi` 作为程序名。在 Windows 替换为 `.\GengBi_CLI_v0.2.22.exe`；源码运行时替换为 `python -m novelforge.cli`。Python 3.11+ 安装 `requirements-cli.txt` 即可，不需要 Qt。macOS/Linux 使用相同 Python 入口；Windows EXE 不能直接在 macOS 运行，macOS 实机验证状态以交付说明为准。

每条命令可以加 `--help`。分主题说明：`guide config`、`guide extract`、`guide worldbooks`、`guide projects`、`guide audit`、`guide volume`、`guide assets`、`guide flows`。

## 2. 配置模型和密钥

```text
gengbi config setup --id my-api --default
gengbi doctor
gengbi config test
gengbi config show
```

`setup` 按提示输入 API 根地址、模型 ID、密钥，密钥输入不回显并加密保存。以后启动无需重复填写。已有端点未提供新密钥时保留原密钥。免认证本地服务使用 `--no-key`。

脚本中可先在进程环境设置 `GENGBI_API_KEY`，再运行：

```text
gengbi config setup --id my-api --base-url https://example.com/v1 --model MODEL --default
gengbi config models --endpoint my-api --save
gengbi config set-flow context_extraction --endpoint my-api --model MODEL
gengbi config set-flow context_extraction --reset
gengbi config copy my-api --id second-api
gengbi config use --endpoint my-api
```

`config models` 获取服务商模型列表，`--save` 保存到端点。`set-flow` 可以为 13 类业务流程分别指定模型，流程 ID 见 `config show`。删除端点使用 `config remove --endpoint ID --yes`。诊断不调用模型；`config test`、提取和写作会产生服务商 API 调用费用。

高级端点设置：

```text
gengbi config endpoint-options my-api
gengbi config endpoint-options my-api -i 端点选项.json
```

JSON 可含 `models`、`enabled_models`、`default_model`、`reasoning_effort`、`merge_system_messages`、`extra_payload`、`extra_headers`。请求扩展的读取结果只显示字段名，避免把认证信息输出到终端。启用模型列表改变而未提供默认模型时，使用首个已启用模型。

```json
{
  "enabled_models": ["MODEL"],
  "reasoning_effort": "low",
  "merge_system_messages": false,
  "extra_payload": {"seed": 17}
}
```

默认设置按分组查看/更新：

```text
gengbi config settings continuation
gengbi config settings continuation -i 写作默认设置.json
gengbi config settings context_extract
gengbi config settings volume
gengbi config settings network -i 代理设置.json
```

更新 JSON 按字段合并，未知字段或非法类型不会保存。例如写作设置可用 `{"default_temperature":0.8,"default_target_words":2000,"prefer_non_stream":true}`；代理设置可用 `{"proxy_enabled":true,"http_proxy":"http://127.0.0.1:7897"}`。

`--config 路径` 放在具体操作之后，如 `gengbi config show --config 配置.json`。默认配置与桌面端共用 `~/.novelforge/config.json`；配置中的 `data.storage_path` 指定数据目录。

### 按模型附加提示词

`config model-prompt` 按“端点 ID + 完整模型 ID”保存额外的任务说明。默认不启用，也不会预置任何附加文本。将自己需要的说明保存为 UTF-8 TXT 后执行：

```powershell
gengbi config model-prompt --endpoint teamorouter --model gemini-4-argon -i ".\模型附加提示词.txt"
gengbi config model-prompt --endpoint teamorouter --model gemini-4-argon
gengbi config model-prompt --endpoint teamorouter --model gemini-4-argon --clear
```

第一条设置，第二条查看原文，第三条清除。模型名称区分大小写、精确匹配，没有厂商推断或通配规则；同一端点的其他模型不受影响。文本作为额外 system 消息插在最后一条原 system 消息后，不替换原版提取提示词、正文或预设。开启端点的 `merge_system_messages` 时，发送前会合并 system 消息。`extra_payload` 覆盖模型或消息时，按最终模型匹配，再附加文本。

该设置作用于此端点和模型的所有生成请求，包括连接测试、提取、续写、审稿、整卷和流程各阶段。只想用于世界书提取，可复制端点、对副本设置提示词，再用 `config set-flow context_extraction --endpoint 副本ID` 绑定。命令的 `--model` 覆盖也参与匹配；`continue --base-url` 不继承保存端点的附加提示词。

`--dry-run` 的 `messages` 仍是原流程组装结果，`model_prompt` 单独显示将在发送时附加的文本，不会调用模型。修改提示词后重新提取已有文章时加 `--refresh`，避免复用已有缓存；已完成的流程阶段不会自动重新运行。配置仅影响模型输入，不保证模型遵循或服务商接受请求。旧版桌面 EXE 不具备此功能，需要包含本改动的版本。

## 3. 已有文章 → 世界书 → 续写

```text
gengbi extract worldbook -i 小说.txt --name 小说设定 -o 世界书.json
```

此命令导入 TXT、按章节标题拆分、调用模型提取并保存世界书。返回 JSON 含 `project`、`chapter`、`worldbook` 和下一步命令。TXT 默认 UTF-8；其他编码使用 `--encoding gb18030` 等。

世界书可以包含人物、关系、地点、事件、氛围、风格、伏笔、剧情状态和主角既有行为。提取结果应先校对，尤其是人物关系、时间线、尚未揭晓的秘密和推测性结论。

```text
gengbi worldbooks show WORLD_ID > 世界书编辑.json
gengbi worldbooks save -i 世界书编辑.json --force
gengbi continue --project PROJECT_ID --worldbook WORLD_ID --dry-run
gengbi continue --project PROJECT_ID --worldbook WORLD_ID --save -o 续写.txt --prompt "承接末章，保持人物与设定一致"
gengbi versions list --chapter CHAPTER_ID
gengbi versions accept VERSION_ID --chapter CHAPTER_ID
```

世界书创建后不会自动改变默认选择。`--worldbook` 本次指定，可重复传多本；`worldbooks select WORLD_ID OTHER_ID` 保存默认选择；不传 ID 则清空。`continue --no-worldbooks` 本次忽略选中的世界书。世界书和条目都必须启用才注入。关键词用于显示和搜索；不执行酒馆的关键词概率激活机制。

`extract worldbook -o` 和 `worldbooks export` 输出 SillyTavern JSON；`worldbooks show/save` 使用赓笔原生 JSON，两者不要混用。世界书还可使用 `new/import/export/delete/toggle/entry-set/entry-delete/entry-toggle` 管理。

## 4. 提取角色、世界观和文风

| 命令 | 作用及保存位置 |
|---|---|
| `extract context` | 九类上下文条目，保存在选定章节 |
| `extract worldbook` | 同类条目，另外创建独立世界书 |
| `extract ontology` | 世界观底层规则，保存在项目档案和项目世界书 |
| `extract protagonist` | 主角心理、动机及行为档案，保存在章节 |
| `extract character --character 角色名` | 指定角色档案，按名字保存在章节 |
| `extract style` | 文风档案，保存在项目 |
| `extract rule --prompt-file 要求.txt` | 把自定义约束结构化为项目审稿规则 |

已有项目使用 `--project PROJECT_ID`；首次输入文章可以用 `-i 小说.txt`。避免每次提取都重复导入：后续继续使用返回的项目 ID。

```text
gengbi extract ontology --project PROJECT_ID
gengbi extract protagonist --project PROJECT_ID
gengbi extract character --project PROJECT_ID --character 林川
gengbi extract style --project PROJECT_ID
gengbi profiles show protagonist --project PROJECT_ID
gengbi profiles set protagonist --project PROJECT_ID -i 修改档案.json --yes
gengbi profiles copy protagonist --project PROJECT_ID --chapter SOURCE_ID --to TARGET_ID --yes
```

`--chapter` 指定截至哪章，默认末章；`--lookback N` 仅取最近 N 章，0 表示截至当前章的全部前文。长文默认按 `--batch-tokens 100000` 分批，可按服务商上下文上限调小。分批按章节边界，单章过长需先 `chapters split`，Token 上限不是截断正文的硬限。

上下文、世界书、主角和指定角色支持 `--incremental`，要求更早章节已有提取结果；只发送新增章节及旧资料。上下文/世界书用 `--refresh` 绕过缓存。其他档案提取直接重新运行。提取失败后已导入的项目保留，可用项目 ID 重试。

项目续写会读取当前章上下文与角色，以及项目世界观、文风和约束。回溯窗口里最近一次提取资料可用 `--lookback-context`、`--lookback-characters` 注入；省略沿用默认设置，`--no-lookback-context/--no-lookback-characters` 明确关闭。

## 5. 单章续写与破限等级

```text
gengbi continue -i 前文.txt -o 续写.txt --prompt "保持原文风格"
gengbi continue --project PROJECT_ID --chapter CHAPTER_ID --save --jailbreak mid
gengbi continue --project PROJECT_ID --preset PRESET_ID --target-words 3000 --lookback 8 --dry-run
```

文件方式只输出正文；项目方式可 `--save` 保存候选和历史。`--chapter` 选择中间章时，后面的已有章节不会注入。输出文件存在时需更换路径或显式 `--force`，不能覆盖输入文章或配置。

正文破限用 `--jailbreak off|low|mid|high|cot`，省略沿用预设；只影响本次，不修改预设。`off` 关闭破限及助手预填充，其他等级互斥选择对应模块、保留预填充原设置。非正文流程使用四档 `off|low|mid|high`，可保存自定义文本：

```text
gengbi config set-jailbreak context_extraction low
gengbi config set-jailbreak writing_element_analysis custom --text-file 流程提示.txt
```

采样与连接参数包括 `--temperature`、`--max-tokens`、`--max-context`、`--stream/--no-stream`、`--reasoning-effort`、`--proxy`、`--timeout`。完整参数见 `continue --help`。预设保存额外生成参数和提示模块；端点 `extra_payload` 可设置服务商扩展字段。

## 6. 审稿、重写和多阶段流程

```text
gengbi audit --project PROJECT_ID -o 审稿意见.txt
gengbi revise --project PROJECT_ID --report 审稿意见.txt -o 修订正文.txt
```

可以编辑报告再修订。`--version VERSION_ID` 针对候选，省略处理当前章节。两条命令都支持 `--dry-run`。修订生成新候选，不改原文章节。

```text
gengbi flows run rewrite_current --project PROJECT_ID --auto
gengbi flows run writing_mode --project PROJECT_ID --auto
gengbi flows run planned_writing --project PROJECT_ID --auto
```

| 流程 ID | 步骤 | 采纳方式 |
|---|---|---|
| `single` | 正文生成 | 插入新章 |
| `rewrite_current` | 重写分析 → 重写正文 | 替换当前章 |
| `writing_mode` | 写作要素分析 → 深化 → 正文 | 插入新章 |
| `planned_writing` | 细纲 → 正文 | 插入新章 |
| `volume` | 分析 → 卷大纲 → 审计 → 逐章写作 | 插入多章 |

不加 `--auto` 时在终端审阅阶段产物，回车接受、`edit 文件路径` 替换、`cancel` 中断。脚本必须显式使用 `--auto`。运行记录保留阶段结果：

```text
gengbi flows run planned_writing --project PROJECT_ID --auto --pause-after outline
gengbi flows runs
gengbi flows run-show RUN_ID
gengbi flows run-edit RUN_ID outline -i 修改细纲.txt
gengbi flows resume RUN_ID --auto -o 正文.txt
```

编辑阶段产物会使后续阶段失效，恢复后重新生成后续内容；旧候选保留供比较。正文使用 `versions edit` 或接受成章后编辑。流程快照随运行保存，不因随后修改流程文件而变更；文章、档案、预设、世界书或正则变化后需新建运行，避免混用旧分析。

自定义流程使用 `flows import -i 流程.json`，再按返回 ID 执行。五类 agent 和 JSON 格式见 [FLOW_PLUGIN_GUIDE.md](FLOW_PLUGIN_GUIDE.md)。`input_from` 指向更早阶段，按 ID 获取产物；重复 ID、前向引用或缺少卷依赖会报输入错误。

## 7. 整卷写作

```text
gengbi volume start --project PROJECT_ID --chapters 5 --prompt "推进调查主线" --dry-run
gengbi volume start --project PROJECT_ID --chapters 5 --auto --pause-after volume_outline
gengbi volume list
gengbi volume show RUN_ID
gengbi volume edit RUN_ID volume_outline -i 修改大纲.json
gengbi volume resume RUN_ID --auto -o 整卷正文.txt
```

阶段 ID 为 `deep_analysis`、`volume_outline`、`outline_audit`、`chapter_writing`。卷设置使用 `--settings 卷参数.json`，字段与 `VolumeRunConfig` 一致，可控制大纲审计、逐章细纲、验证修订轮数和暂停点。`flows run volume` 使用相同卷引擎；运行记录分别由 `volume` 和 `flows` 查询。

恢复跳过已完成阶段；逐章写作阶段失败时重试整个写作阶段。失败产生的部分候选标记为未完成，不能直接采纳。完成候选的 `volume_artifacts` 保存逐章产物，`versions accept` 会插入对应多章。交互暂停点可修改 JSON 产物；审计前用 `focus 审计重点`，每章后用 `reject 修改意见` 请求修订。

`--dry-run` 只截获下一个阶段的首个待发送请求，不调用 API；后续请求依赖真实阶段输出，不提前伪造。多阶段流程可能产生多次模型调用。

## 8. 项目、章节、候选与备份

| 对象 | 操作 |
|---|---|
| `projects` | `list/new/show/update/export/delete/backup/restore` |
| `chapters` | `list/add/show/edit/metadata/rename/reorder/split/merge/delete` |
| `versions` | `list/show/edit/accept/replace/delete` |
| `history` | `list/show/delete/clear` |

```text
gengbi projects new --name 我的小说 -i 小说.txt
gengbi chapters list --project PROJECT_ID
gengbi chapters edit CHAPTER_ID -i 修改正文.txt --yes
gengbi versions edit VERSION_ID --chapter CHAPTER_ID --content-file 人工修订.txt --yes
gengbi versions replace VERSION_ID --chapter CHAPTER_ID --yes
gengbi projects export PROJECT_ID -o 全文.md --format md
gengbi projects backup PROJECT_ID -o 项目备份.zip
gengbi projects restore -i 项目备份.zip
```

`versions accept` 插入新章，`versions replace --yes` 替换当前正文；人工编辑保留 API 原文 `raw_content`。卷候选先接受为多章，再用章节编辑逐章修改。标题、高亮与备注可以用相应 JSON 编辑入口处理。

ZIP 备份包含项目档案、章节、候选、提取上下文、项目世界书、项目变量及引用预设/正则；不含 API 密钥、全局配置或生成历史。恢复创建新项目/章节/候选/世界书 ID，保留已有项目。同 ID 已存在的预设/正则沿用本机版本。独立且未绑定项目的世界书需另行导出。

## 9. 预设、模板、正则和流程资源

```text
gengbi preset list
gengbi preset show PRESET_ID > 预设编辑.json
gengbi preset save -i 预设编辑.json --force
gengbi preset prompt-toggle PRESET_ID PROMPT_ID off
gengbi preset import -i 酒馆预设.json
gengbi regex list --scope scoped --project PROJECT_ID
gengbi regex test SCRIPT_ID --text-file 样本.txt
gengbi flows export FLOW_ID -o 分享流程.json
```

原生预设 JSON 可编辑提示模块、排序、生成参数和模板。`save` 覆盖已有资源需 `--force`；删除需 `--yes`。正则支持 `global/preset/scoped` 作用域，后两者需对应预设/项目 ID；`test` 返回替换结果与匹配位置，不修改文本。

预设导入会保存嵌入的正则脚本。流程导入同 ID 默认创建副本，内置流程不可删除或通过导入覆盖。资源 `show/save` 使用原生格式；`import/export` 使用对应分享格式。

## 10. 脚本、预览与错误排查

正文写 stdout；进度和候选提示写 stderr；管理/提取/流程结果输出 JSON。退出码：0 成功、1 模型或运行失败、2 参数/文件错误、130 用户中断。`--dry-run` 仅用于明确支持它的续写、审稿、修订、卷和流程命令。

- 未配置：运行 `config setup`、`doctor`；401 检查密钥，404 检查 API 根地址和模型 ID。
- 输出已有：改文件名或加 `--force`；不能覆盖源正文或配置。
- 提取失败：使用已有项目 ID 重试，减少 `--batch-tokens`，确认服务商支持当前模型。
- 正文被截断：提高 `--max-tokens`，并确保 `--max-context` 更大。
- 找不到项目/资源：使用对应 `list` 命令取得实际 ID，确认 `--config` 是否指向同一数据目录。
- 恢复失败：先查看运行记录；若源资料或写作资源已变化，创建新运行。
- 非交互终端：不要把输入管道接到 `wizard`；使用具体子命令，流程/整卷加 `--auto`。

配置摘要不会输出密钥或原始 API 错误体。预览、备份、提取文件和历史可能包含你的文章内容，保存到自己希望使用的目录即可。

## 错误诊断与恢复

失败会显示错误代码、失败阶段、项目/章节/流程定位、可执行建议和诊断文件路径。`--debug` 补充异常类型与调用位置；`--error-format json` 输出机器可读诊断；`--diagnostics-dir 路径` 指定诊断目录。这些参数放在具体子命令后。

```powershell
gengbi extract worldbook --project PROJECT_ID --jailbreak off --debug
gengbi config test --error-format json --diagnostics-dir ".\diagnostics"
```

默认诊断保存在配置文件所在目录的 `logs/cli-errors` 下；没有指定配置时通常是 `%USERPROFILE%\.novelforge\logs\cli-errors`。每次错误有独立 ID，诊断包含异常因果链、代码位置、HTTP 状态和可识别的服务商错误类别，不保存 API Key、请求正文、响应正文、局部变量或完整命令行。服务端错误信封中的 `message/code/type/param/status` 会经脱敏后显示在详情中，并保留可用的请求 ID。非 JSON 网关错误会提取有长度限制的文本摘要；密钥、认证头和匹配到的请求文本回显会遮蔽。完整错误响应及其他未知字段不写入诊断。服务未提供可识别说明时，会明确标注 `provider_message_unavailable`。无法明确分类时会报告通用 API/内部错误，不把猜测当作原因。

| 错误代码 | 含义与处理 |
| --- | --- |
| `usage` / `invalid_input` / `validation` | 参数或数据字段不合法；查对应 `--help` 和 fields |
| `input_json` / `encoding` | 本地文件格式或编码错误；修复文件后重试 |
| `file_missing` / `permission` / `disk_full` / `filesystem` / `database` | 本地路径、权限、空间或数据库问题 |
| `authentication` / `rate_limited` / `quota_exhausted` | 分别是密钥认证、限流、余额或配额不足 |
| `api_error` / `model_not_found` / `context_limit` / `output_limit_parameter` | HTTP 状态、模型、输入长度或输出参数不被服务接受 |
| `connection` / `proxy` / `tls` / `timeout` | 网络、代理、证书或超时 |
| `response_json` / `response_schema` / `response_validation` | 模型结构化输出无效 |
| `empty_response` / `incomplete_response` / `output_truncated` | 无正文、未正常结束、达到输出上限；不会保存为成功候选 |
| `provider_refusal` / `provider_filtered` | 服务明确拒答或返回过滤标记；不伪装成网络错误 |
| `persistence_failed` / `internal` | 业务保存失败或程序异常；保留诊断文件排查 |

输入导入成功后，提取失败也会保留项目 ID。使用 `--project` 重试，替换原来的 `-i`，并保留原调用的配置、角色/约束、范围和输出参数；不要重复导入。流程错误会给出 `flows resume RUN_ID` 或 `volume resume RUN_ID` 入口，已完成阶段保留。恢复状态或清理同时失败时，诊断的 `secondary_error` 与 `cleanup_failed` 会明确标记，此时应先核对运行记录再恢复。

确定性的认证、接口和协议失败不会在提取层盲目重试。限流等待有上限；流式正文已经开始后遇到传输中断，不会重试并拼接两次输出。非关键恢复或降级成功时，退出码仍为 0，但会输出 `completed_with_warnings` 提示及诊断文件。

退出码：`0` 成功，`1` 运行失败，`2` 参数/本地输入错误，`130` 用户中断。诊断文件写入失败不会覆盖最初原因。普通命令进度仍写 stderr，`--error-format json` 的最终记录为 `{"error": ...}`，成功但有提示为 `{"warning": ...}`；自动化程序应读取对应 JSON 记录，不应把全部 stderr 当作单个 JSON。

`response_tool_calls` 表示模型返回了工具调用，写作命令需要正文文本；检查端点 `extra_payload` 中是否启用了 `tools/tool_choice/functions/function_call`。`unsupported_finish_reason` 表示服务商使用了不支持的结束标记；诊断保留经过脱敏和限长的 `finish_reason`，不会将其误报成网络中断。真正缺少结束标记时，`incomplete_response` 的详情会显示 `finish_reason: null`。

旧版 CLI 没有保存足够的错误信息，不能仅凭旧的“模型请求未成功完成”还原失败原因；新版本在下一次失败时提供诊断。
