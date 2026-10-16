"""命令行的配置、世界书和操作引导；帮助解析不加载业务或 Qt 模块。"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


def emit(value: Any) -> None:
    """输出可供管道消费的 JSON。"""
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def require(value: Any, label: str) -> Any:
    """将不存在的业务对象转换为可理解的输入错误。"""
    if value is None:
        raise ValueError(f"{label}不存在，请先使用对应的 list 命令查询 ID")
    return value


def add_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-o", "--output", type=Path, required=True, help="输出文件")
    parser.add_argument("--force", action="store_true", help="允许覆盖已有输出")


def check_output(path: Path, force: bool, *sources: Path | None) -> None:
    """请求及写入前检查目标，防止源文件被覆盖。"""
    for source in sources:
        if source is not None and (
            path.resolve() == source.resolve()
            or (path.exists() and source.exists() and path.samefile(source))
        ):
            raise ValueError("输出不能覆盖输入或配置文件")
    if path.exists() and not force:
        raise FileExistsError(f"输出已存在；更换路径或使用 --force：{path}")
    if not path.parent.is_dir() or path.is_dir():
        raise ValueError(f"输出目录不存在或目标为目录：{path}")


def add_commands(subparsers: Any, common: argparse.ArgumentParser) -> None:
    """注册工作区命令；参数帮助保持可独立执行。"""
    guide = subparsers.add_parser("guide", help="按任务查看操作指南和示例")
    guide.add_argument(
        "topic",
        nargs="?",
        default="start",
        choices=[
            "start",
            "config",
            "worldbooks",
            "extract",
            "projects",
            "audit",
            "volume",
            "assets",
            "flows",
        ],
    )
    subparsers.add_parser(
        "wizard", parents=[common], help="交互选择配置、文章提取、续写和审稿任务"
    )
    subparsers.add_parser(
        "doctor", parents=[common], help="诊断本机配置，不发送模型请求"
    )
    config = subparsers.add_parser("config", help="在终端配置端点、加密密钥和流程模型")
    actions = config.add_subparsers(dest="action", required=True)
    from novelforge.cli_settings import add_commands as add_settings_commands

    add_settings_commands(actions, common)
    actions.add_parser("show", parents=[common], help="显示配置摘要，不显示密钥")
    setup = actions.add_parser(
        "setup", parents=[common], help="新增/更新端点；缺少参数时交互引导"
    )
    setup.add_argument("--id", default="default", help="端点 ID，更新时沿用原 ID")
    setup.add_argument("--name", help="端点显示名称")
    setup.add_argument(
        "--base-url", help="OpenAI 兼容 API 根地址，例如 https://example.com/v1"
    )
    setup.add_argument("--model", help="服务商提供的模型 ID")
    setup.add_argument(
        "--api-key-env",
        default="GENGBI_API_KEY",
        help="密钥来源环境变量；不把密钥写在参数中",
    )
    setup.add_argument(
        "--no-key", action="store_true", help="明确使用不需要密钥的本地服务"
    )
    setup.add_argument("--default", action="store_true", help="设置为默认端点")
    for action, description in [
        ("use", "设置默认端点"),
        ("remove", "删除保存的端点"),
        ("models", "从服务商获取模型 ID 列表"),
        ("test", "发送一次简短请求验证模型（产生 API 调用费用）"),
    ]:
        parser = actions.add_parser(action, parents=[common], help=description)
        parser.add_argument(
            "--endpoint",
            required=action in {"use", "remove"},
            help="端点 ID，默认使用续写端点",
        )
        if action == "remove":
            parser.add_argument(
                "--yes", action="store_true", required=True, help="确认删除此端点"
            )
        if action in {"test", "models"}:
            parser.add_argument("--model", help="仅本次覆盖模型 ID")
            parser.add_argument(
                "--timeout", type=float, default=120, help="请求超时秒数"
            )
        if action == "models":
            parser.add_argument(
                "--save", action="store_true", help="获取后保存到该端点的模型列表"
            )
    flow = actions.add_parser(
        "set-flow", parents=[common], help="指定某个业务流程的端点/模型"
    )
    flow.add_argument("flow", help="流程 ID，可从 config show 查询")
    flow.add_argument("--endpoint")
    flow.add_argument("--model", help="省略则使用端点默认模型")
    flow.add_argument(
        "--reset", action="store_true", help="清除本流程覆盖，恢复默认端点/模型"
    )

    books = subparsers.add_parser(
        "worldbooks", help="管理世界书、ST 导入导出和条目开关"
    )
    actions = books.add_subparsers(dest="action", required=True)
    actions.add_parser("list", parents=[common], help="列出世界书 ID 和条目数")
    new = actions.add_parser("new", parents=[common], help="新建空世界书")
    new.add_argument("--name", required=True)
    imported = actions.add_parser(
        "import", parents=[common], help="导入 ST 世界书 JSON"
    )
    imported.add_argument("-i", "--input", type=Path, required=True)
    save = actions.add_parser(
        "save", parents=[common], help="保存编辑后的原生世界书 JSON；从 show 获取格式"
    )
    save.add_argument("-i", "--input", type=Path, required=True)
    save.add_argument("--force", action="store_true", help="允许更新已存在的世界书")
    select = actions.add_parser(
        "select", parents=[common], help="保存默认选中的世界书，未提供 ID 则清空"
    )
    select.add_argument("ids", nargs="*")
    for action in (
        "show",
        "export",
        "delete",
        "toggle",
        "entry-set",
        "entry-delete",
        "entry-toggle",
    ):
        parser = actions.add_parser(
            action,
            parents=[common],
            help={
                "show": "查看原生 JSON，可重定向到文件编辑后 save",
                "export": "导出 ST 世界书 JSON",
                "delete": "删除整本世界书",
                "toggle": "启用或禁用整本世界书",
                "entry-set": "新增或更新条目",
                "entry-delete": "删除条目",
                "entry-toggle": "启用或禁用条目",
            }[action],
        )
        parser.add_argument("id", help="世界书 ID")
        if action == "export":
            add_output(parser)
        if action in {"delete", "entry-delete"}:
            parser.add_argument("--yes", action="store_true", required=True)
        if action in {"toggle", "entry-toggle"}:
            parser.add_argument("state", choices=["on", "off"])
        if action.startswith("entry-"):
            parser.add_argument(
                "--uid", required=action != "entry-set", help="条目 ID，新增时省略"
            )
        if action == "entry-set":
            parser.add_argument(
                "--content-file", type=Path, required=True, help="UTF-8 条目正文"
            )
            parser.add_argument("--name", help="条目备注")
            parser.add_argument(
                "--key", action="append", help="关键词，可重复；用于显示和查找"
            )
            parser.add_argument(
                "--category",
                choices=[
                    "characters",
                    "locations",
                    "events",
                    "style",
                    "plot_state",
                    "relationships",
                    "atmosphere",
                    "foreshadowing",
                    "protagonist_behavior",
                ],
            )
            parser.add_argument("--position", choices=["before", "after", "at_depth"])
            parser.add_argument("--depth", type=int)
            parser.add_argument("--role", choices=["system", "user", "assistant"])
            parser.add_argument("--order", type=int)


def show_guide(topic: str = "start") -> None:
    """提供按任务组织的短指南，完整功能清单随接入进度维护。"""
    guides = {
        "start": """赓笔命令行使用引导

首次使用：
  gengbi wizard                       按菜单选择任务，不需要记住命令
  gengbi config setup                 交互配置地址、模型和密钥
  gengbi doctor                       检查默认端点及本机配置
  gengbi config test                  发起一次简短的付费连通性验证

已有文章，直接续写：
  gengbi continue -i 前文.txt -o 续写.txt --prompt "保持原文风格"
  gengbi continue -i 前文.txt --dry-run

从文章提取设定，再续写：
  gengbi guide extract                TXT → 世界书 → 校对 → 续写 → 接受候选

更多任务指南：
  gengbi guide projects               项目、章节和候选
  gengbi guide worldbooks             世界书导入、编辑和选用
  gengbi guide audit                  审稿和按意见修订
  gengbi guide volume                 整卷生成、阶段编辑与恢复
  gengbi guide assets                 写作预设、正则和流程文件
  gengbi guide flows                  重写、规划写作和自定义多阶段流程

获取参数：gengbi <命令> --help；配置指南：gengbi guide config
Windows 将 gengbi 替换为 .\\GengBi_CLI_v0.2.22.exe；源码使用 python -m novelforge.cli。
API 调用使用你的服务商余额；支持 --dry-run 的命令可先预览请求，不产生调用费用。
""",
        "config": """配置模型与密钥

交互配置：gengbi config setup --id my-api --default
交互输入密钥时不回显。脚本中设置 GENGBI_API_KEY 后执行：
  gengbi config setup --id my-api --base-url https://example.com/v1 --model MODEL --default
已有端点更新地址/模型时，未提供新密钥则保留原密钥。
  gengbi config show
  gengbi config models --endpoint my-api
  gengbi config test --endpoint my-api
  gengbi config set-flow single_continuation --endpoint my-api --model MODEL

配置加密保存至本机，后续终端无需重复输入。使用 Chat Completions 接口。
临时覆盖：continue --base-url URL --model MODEL；密钥通过 --api-key-env 指定环境变量名。
诊断：401 检查密钥；404 检查 API 根地址和模型 ID；超时可提高 --timeout。
""",
        "worldbooks": """管理世界书

  gengbi worldbooks new --name 世界设定
  gengbi worldbooks list
  gengbi worldbooks entry-set WORLD_ID --name 城市 --key 云城 --content-file 城市设定.txt
  gengbi worldbooks entry-toggle WORLD_ID off --uid ENTRY_ID
  gengbi worldbooks import -i 酒馆世界书.json
  gengbi worldbooks show WORLD_ID > 世界书编辑.json
  gengbi worldbooks save -i 世界书编辑.json --force
  gengbi worldbooks export WORLD_ID -o 分享世界书.json
  gengbi worldbooks select WORLD_ID

WORLD_ID 和 ENTRY_ID 使用命令返回的实际 ID。条目按启用状态注入，关键词用于查找，
不执行酒馆的概率激活。编辑已有世界书需要 --force；删除需要 --yes。
""",
        "extract": """从已有文章提取世界书，再用于续写

1. 首次配置：gengbi config setup；检查：gengbi doctor。
2. 准备 UTF-8 TXT（其他编码可传 --encoding gb18030），运行：
   gengbi extract worldbook -i 小说.txt --name 小说设定 -o 世界书.json
   此步骤调用模型并产生费用；创建项目、按标题拆章、提取并保存世界书。
   返回 JSON 的 project / chapter / worldbook 是后续使用的实际 ID。
3. 校对提取结果：
   gengbi worldbooks show WORLD_ID > 世界书编辑.json
   gengbi worldbooks save -i 世界书编辑.json --force
4. 先预览，再引用世界书续写：
   gengbi continue --project PROJECT_ID --worldbook WORLD_ID --dry-run
   gengbi continue --project PROJECT_ID --worldbook WORLD_ID --save -o 续写.txt --prompt "承接末章，保持人物与设定一致"
5. 查看候选并接受成新章：
   gengbi versions list --chapter CHAPTER_ID
   gengbi versions accept VERSION_ID --chapter CHAPTER_ID

WORLD_ID / PROJECT_ID / CHAPTER_ID / VERSION_ID 都要换成命令返回的 ID。
只提取不会自动选用这本世界书；--worldbook 本次指定，或 worldbooks select WORLD_ID 保存默认选择。
世界书导出为 SillyTavern JSON；show/save 使用赓笔原生 JSON，两种格式不要混用。
模型可能漏掉细节或产生不准确归纳，请重点校对人物关系、时间线和设定。

可提取的资料：
  worldbook     人物、关系、地点、事件、氛围、风格、伏笔、剧情状态、主角既有行为
  context       同类条目保存到章节，不另建世界书
  ontology      世界观底层规则，保存为项目档案和世界书
  protagonist   主角心理、动机及行为档案
  character     指定角色档案，需 --character 角色名
  style         文风档案
  rule          解析自定义约束，需 --prompt-file 要求.txt
例如：gengbi extract ontology --project PROJECT_ID
      gengbi extract character --project PROJECT_ID --character 林川
      gengbi extract style --project PROJECT_ID

范围与长文：--chapter CHAPTER_ID 截至指定章；--lookback N 只读最近 N 章，0 为全部前文。
默认按 --batch-tokens 100000 分批；服务商上下文较小时可调低，例如 20000。
新增章节后用 --project PROJECT_ID --incremental 更新上下文/角色；需要更早章节已有提取结果。
--refresh 强制重新提取上下文/世界书；其他档案流程会直接重新运行。
提取失败后可用已返回的 --project 重试，避免重复导入。查项目：gengbi projects list。
""",
        "projects": """项目、章节和候选版本

  gengbi projects new --name 我的小说 -i 小说.txt
  gengbi projects list
  gengbi projects show PROJECT_ID
  gengbi chapters list --project PROJECT_ID
  gengbi chapters show CHAPTER_ID
  gengbi chapters rename CHAPTER_ID --title 新标题
  gengbi chapters edit CHAPTER_ID -i 修改后的正文.txt --yes
  gengbi continue --project PROJECT_ID --chapter CHAPTER_ID --save
  gengbi versions list --chapter CHAPTER_ID
  gengbi versions show VERSION_ID --chapter CHAPTER_ID
  gengbi versions accept VERSION_ID --chapter CHAPTER_ID
  gengbi versions replace VERSION_ID --chapter CHAPTER_ID --yes
  gengbi projects export PROJECT_ID -o 全文.txt
  gengbi history list --project PROJECT_ID

accept 插入新章节；replace 替换当前正文。生成候选本身不会改写已有章节。
从中间章续写只读截至该章的前文；后续已有章节不会注入。
章节还支持 add / reorder / split / merge / delete；各操作可用 --help 查参数。
项目资料用 projects update PROJECT_ID -i 档案修改.json；上下文/角色用 profiles show/set/copy。
""",
        "audit": """审稿和按报告重写

  gengbi audit --project PROJECT_ID -o 审稿意见.txt
  gengbi audit --project PROJECT_ID --version VERSION_ID -o 候选审稿意见.txt
  gengbi revise --project PROJECT_ID --report 审稿意见.txt -o 修订正文.txt

可以先编辑报告再 revise。两步都会调用模型；修订保存新候选，原文保留。
使用 --chapter CHAPTER_ID 指定章节，省略则默认末章；--version 需属于该章。
用 versions list/show 查看修订，用 versions replace --yes 采纳为当前章。
audit / revise 均支持 --dry-run 预览模型请求。
""",
        "volume": """整卷写作与恢复

  gengbi volume start --project PROJECT_ID --chapters 5 --prompt "推进寻找旧信的主线"
  gengbi volume list
  gengbi volume show RUN_ID
  gengbi volume resume RUN_ID

交互模式可以在暂停点审阅和编辑；脚本必须加 --auto 自动通过暂停点。
阶段：deep_analysis → volume_outline → outline_audit → chapter_writing。
分次操作：volume start --project PROJECT_ID --auto --pause-after volume_outline
编辑大纲：volume edit RUN_ID volume_outline -i 修改后的大纲.json
继续：volume resume RUN_ID --auto -o 整卷正文.txt

失败或取消保留已完成阶段；恢复时检查原文章节是否变更。写作阶段失败会重试该阶段。
整卷保存为候选，versions accept 会插入生成的多章。用 --settings 指定 VolumeRunConfig JSON。
多阶段会多次调用模型，费用高于单章续写。
""",
        "assets": """预设、正则与流程文件

  gengbi preset list
  gengbi preset show PRESET_ID > 预设编辑.json
  gengbi preset save -i 预设编辑.json --force
  gengbi preset import -i 酒馆预设.json
  gengbi preset export PRESET_ID -o 分享预设.json
  gengbi preset prompt-toggle PRESET_ID PROMPT_ID off
  gengbi regex list
  gengbi regex list --scope scoped --project PROJECT_ID
  gengbi flows list
  gengbi flows show FLOW_ID
  gengbi flows import -i 流程.json

资源的 show/save 使用原生 JSON；import/export 使用相应分享格式。
continue --preset PRESET_ID --dry-run 预览最终请求。
continue --jailbreak off|low|mid|high|cot 临时覆盖等级，省略沿用预设。
流程执行：gengbi guide flows；正则试运行：regex test SCRIPT_ID --text-file 样本文本.txt。
""",
        "flows": """内置与自定义多阶段写作

  gengbi flows list
  gengbi flows run planned_writing --project PROJECT_ID --dry-run
  gengbi flows run planned_writing --project PROJECT_ID --prompt "先推进调查，再揭开线索"
  gengbi flows run writing_mode --project PROJECT_ID --auto
  gengbi flows run rewrite_current --project PROJECT_ID --auto
  gengbi flows run volume --project PROJECT_ID --chapters 5 --auto

单章 single；分析后重写 rewrite_current；分析/深化/生成 writing_mode；细纲后生成 planned_writing；整卷 volume。
交互运行可审阅分析并用文件替换，脚本加 --auto。正文保存在项目候选中，原文章节保留。
rewrite_current 用 versions replace VERSION_ID --chapter CHAPTER_ID --yes 采纳；其余用 versions accept。
自定义流程先 flows import -i 流程.json，再 flows run FLOW_ID --project PROJECT_ID。

  gengbi flows run planned_writing --project PROJECT_ID --auto --pause-after outline
  gengbi flows runs
  gengbi flows run-show RUN_ID
  gengbi flows run-edit RUN_ID outline -i 修改后的细纲.txt
  gengbi flows resume RUN_ID --auto -o 正文.txt

恢复跳过已完成阶段；修改产物后重新执行后续阶段。源文章、档案、预设、世界书或正则变化需新建运行。
--dry-run 预览下个可执行阶段；后续请求需等待前序输出才能组装。卷阶段预览在首次发送前截获请求，不调用模型。
""",
    }
    print(guides[topic])


async def run_config(args: argparse.Namespace, config: Any) -> None:
    """调用原生配置加密和 HTTP 客户端，避免另存一套连接状态。"""
    from urllib.parse import urlsplit

    from novelforge.cli_settings import ACTIONS
    from novelforge.cli_settings import execute as execute_settings
    from novelforge.services.headless import resolve_connection
    from novelforge.utils.ids import validate_id

    if args.action in ACTIONS:
        execute_settings(args, config)
        return
    if args.action == "set-flow" and args.reset:
        if args.endpoint or args.model:
            raise ValueError("--reset 不能与 --endpoint/--model 同时使用")
        config.set_flow_endpoints(
            {k: v for k, v in config.get_flow_endpoints().items() if k != args.flow}
        )
        config.set_flow_models(
            {k: v for k, v in config.get_flow_models().items() if k != args.flow}
        )
        emit({"reset": args.flow})
        return

    if args.action == "show":
        from novelforge.core.config import FLOW_DEFINITIONS

        emit(
            {
                "config_path": str(config.config_path),
                "default_endpoint": config.config.get("default_endpoint_id", ""),
                "endpoints": [
                    {k: ep.get(k, "") for k in ("id", "name", "default_model")}
                    | {
                        "has_key": bool(ep.get("api_key_encrypted")),
                        "host": urlsplit(ep.get("base_url", "")).hostname,
                    }
                    for ep in config.get_endpoints()
                ],
                "flows": FLOW_DEFINITIONS,
                "flow_endpoints": config.get_flow_endpoints(),
                "flow_models": config.get_flow_models(),
            }
        )
    elif args.action == "setup":
        validate_id(args.id)
        existing = config.get_endpoint(args.id) or {}

        def field(value: str | None, key: str, label: str) -> str:
            value = value or existing.get(key, "")
            if not value and sys.stdin.isatty():
                value = input(label + ": ").strip()
            if not value:
                raise ValueError(
                    f"缺少{label}，请提供 --{key.replace('_', '-') if key != 'default_model' else 'model'}"
                )
            return value

        url = field(args.base_url, "base_url", "API 根地址")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "API 根地址必须是 HTTP(S) URL，不能包含凭据、查询参数或片段"
            )
        model = field(args.model, "default_model", "模型 ID")
        key = "" if args.no_key else os.environ.get(args.api_key_env)
        if (
            key is None
            and not existing.get("api_key_encrypted")
            and not existing.get("allow_empty_key")
        ):
            if not sys.stdin.isatty():
                raise ValueError(
                    f"请设置环境变量 {args.api_key_env}，或交互运行 config setup；免认证服务用 --no-key"
                )
            key = getpass.getpass("API Key（输入不回显）: ").strip()
            if not key:
                raise ValueError("密钥为空；免认证服务请显式使用 --no-key")
        updates = {
            "id": args.id,
            "name": args.name or existing.get("name") or args.id,
            "base_url": url.rstrip("/"),
            "default_model": model,
            "allow_empty_key": args.no_key
            or (key is None and existing.get("allow_empty_key", False)),
        }
        if key is not None:
            updates["api_key"] = key
        if existing:
            config.update_endpoint(args.id, updates)
        else:
            config.add_endpoint(updates)
        if args.default:
            config.set_default_endpoint(args.id)
        emit(
            {
                "saved": True,
                "endpoint": args.id,
                "model": model,
                "next": "config test --endpoint " + args.id,
            }
        )
    elif args.action in {"use", "remove", "set-flow"}:
        require(config.get_endpoint(args.endpoint), "端点")
        if args.action == "remove":
            config.remove_endpoint(args.endpoint)
        elif args.action == "use":
            config.set_default_endpoint(args.endpoint)
        else:
            from novelforge.core.config import FLOW_DEFINITIONS

            if args.flow not in {item[0] for item in FLOW_DEFINITIONS}:
                raise ValueError("未知流程 ID，请运行 config show 查询")
            config.set_flow_endpoints(
                {**config.get_flow_endpoints(), args.flow: args.endpoint}
            )
            config.set_flow_models(
                {**config.get_flow_models(), args.flow: args.model or ""}
            )
        emit({"saved": True})
    else:
        if args.timeout <= 0 or not __import__("math").isfinite(args.timeout):
            raise ValueError("超时必须为有限正数")
        connection = resolve_connection(
            config, endpoint_id=args.endpoint, model=args.model, timeout=args.timeout
        )
        if not connection.base_url or (args.action == "test" and not connection.model):
            raise ValueError("尚未配置端点或模型，请先运行 config setup")
        from novelforge.cli_diagnostics import phase
        phase("模型连接检测", model=connection.model)
        client = connection.client()
        try:
            if args.action == "models":
                models = await client.fetch_models()
                if args.save:
                    endpoint = (
                        config.get_endpoint(args.endpoint)
                        if args.endpoint
                        else config.get_flow_endpoint("single_continuation")
                    )
                    require(endpoint, "端点")
                    config.update_endpoint(endpoint["id"], {"models": models})
                emit(models)
            else:
                content, finish = await client.complete_text(
                    model=connection.model,
                    messages=[{"role": "user", "content": "请只回复：连接成功"}],
                    max_tokens=2048,
                    temperature=0,
                    stream=False,
                )
                from novelforge.services.llm_client import LLMError

                if finish != "stop" or not content:
                    raise LLMError("测试响应未正常完成")
                emit({"ok": True, "model": connection.model, "reply": content})
        finally:
            from novelforge.cli_diagnostics import preserve_failure
            with preserve_failure():
                await client.close()


def run_worldbooks(args: argparse.Namespace, config: Any) -> None:
    """维护可被桌面端直接使用的原生世界书。"""
    from novelforge.cli import _write_output
    from novelforge.models.context import ContextEntry
    from novelforge.models.worldbook import WorldBook
    from novelforge.services.headless import read_json
    from novelforge.services.worldbook_service import WorldBookService
    from novelforge.utils.ids import generate_id

    service = WorldBookService(config.get_storage_path())
    action = args.action
    if action == "list":
        selected = config.get_selected_worldbook_ids()
        emit(
            [
                {
                    "id": b.id,
                    "name": b.name,
                    "entries": len(b.entries),
                    "enabled": b.enabled,
                    "selected": b.id in selected,
                }
                for b in service.list_worldbooks()
            ]
        )
        return
    if action == "new":
        book = service.create_worldbook(args.name)
    elif action == "import":
        book = service.import_from_st_json(args.input)
    elif action == "save":
        book = WorldBook.model_validate(read_json(args.input))
        if service.load_worldbook(book.id) and not args.force:
            raise ValueError("世界书已存在；更新需要 --force")
        service.save_worldbook(book)
    elif action == "select":
        for identifier in args.ids:
            require(service.load_worldbook(identifier), "世界书")
        config.set_selected_worldbook_ids(list(dict.fromkeys(args.ids)))
        emit({"selected": config.get_selected_worldbook_ids()})
        return
    else:
        book = require(service.load_worldbook(args.id), "世界书")
        if action == "show":
            emit(book.model_dump(mode="json", by_alias=True))
            return
        if action == "export":
            source = service.worldbooks_dir / f"{book.id}.json"
            check_output(args.output, args.force, source, config.config_path)
            with TemporaryDirectory(prefix="gengbi-export-") as directory:
                temporary = Path(directory) / "worldbook.json"
                service.export_to_st_json(book, temporary)
                _write_output(
                    temporary.read_text(encoding="utf-8"), args.output, args.force
                )
            emit({"output": str(args.output)})
            return
        if action == "delete":
            if not service.delete_worldbook(book.id):
                raise ValueError("世界书删除失败或已不存在")
            config.set_selected_worldbook_ids(
                [x for x in config.get_selected_worldbook_ids() if x != book.id]
            )
            emit({"deleted": book.id})
            return
        if action == "toggle":
            book.enabled = args.state == "on"
        else:
            entry = next((e for e in book.entries if e.uid == args.uid), None)
            if action == "entry-set":
                if args.uid:
                    require(entry, "世界书条目")
                else:
                    entry = ContextEntry(uid=generate_id("ctx_"))
                    book.entries.append(entry)
                entry.content = args.content_file.read_text(encoding="utf-8-sig")
                for name in ("category", "position", "depth", "role", "order"):
                    value = getattr(args, name)
                    if value is not None:
                        setattr(entry, name, value)
                if entry.depth < 0:
                    raise ValueError("注入深度不能为负数")
                if args.name is not None:
                    entry.comment = args.name
                if args.key is not None:
                    entry.key = args.key
            else:
                require(entry, "世界书条目")
                if action == "entry-delete":
                    book.entries = [e for e in book.entries if e.uid != args.uid]
                else:
                    entry.enabled = args.state == "on"
        service.save_worldbook(book)
    emit(book.model_dump(mode="json", by_alias=True))


async def execute(args: argparse.Namespace) -> None:
    """分发工作区命令，避免无关命令初始化数据库或网络。"""
    from novelforge.services.headless import read_config

    if args.command == "guide":
        show_guide(args.topic)
        return
    if args.command == "wizard":
        from novelforge.cli_wizard import execute as execute_wizard

        await execute_wizard(args)
        return
    config = read_config(args.config)
    if args.command in {"audit", "revise"}:
        from novelforge.cli_audit import execute as execute_audit

        await execute_audit(args, config)
    elif args.command == "flows" and args.action in {
        "run",
        "resume",
        "runs",
        "run-show",
        "run-edit",
    }:
        from novelforge.cli_flows import execute as execute_flow

        await execute_flow(args, config)
    elif args.command in {"preset", "regex", "flows"}:
        from novelforge.cli_assets import execute as execute_assets

        execute_assets(args, config)
    elif args.command == "volume":
        from novelforge.cli_volume import execute as execute_volume

        await execute_volume(args, config)
    elif args.command in {"extract", "profiles"}:
        from novelforge.cli_extract import execute as execute_extract

        await execute_extract(args, config)
    elif args.command in {"projects", "chapters", "versions", "history"}:
        from novelforge.cli_projects import execute as execute_project

        execute_project(args, config)
    elif args.command == "config":
        await run_config(args, config)
    elif args.command == "worldbooks":
        run_worldbooks(args, config)
    elif args.command == "doctor":
        endpoint = config.get_flow_endpoint("single_continuation") or {}
        ready = bool(
            endpoint.get("base_url") and config.get_flow_model("single_continuation")
        )
        emit(
            {
                "config_exists": config.config_path.exists(),
                "config_path": str(config.config_path),
                "storage_path": str(config.get_storage_path()),
                "endpoint": endpoint.get("id"),
                "model": config.get_flow_model("single_continuation"),
                "saved_key_present": bool(endpoint.get("api_key_encrypted")),
                "ready": ready,
                "next": "config test" if ready else "config setup",
            }
        )
