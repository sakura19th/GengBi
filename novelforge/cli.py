"""赓笔命令行入口：python -m novelforge.cli。帮助命令不加载 Qt 或业务依赖。"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from novelforge import __version__
from novelforge.cli_diagnostics import (
    ArgumentParser,
    Diagnostics,
    phase,
    reset_operation,
)


def _positive_timeout(value: str) -> float:
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("超时必须是有限正数")
    return timeout


def build_parser() -> argparse.ArgumentParser:
    """定义稳定的子命令与显式覆盖选项。"""
    parser = ArgumentParser(
        prog="gengbi", description="赓笔：无需桌面窗口的文本续写"
    )
    parser.add_argument("--version", action="version", version=f"赓笔 {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config", type=Path, help="桌面配置 JSON；默认读取 ~/.novelforge/config.json"
    )
    common.add_argument("--error-format", choices=["text", "json"], default="text", help="错误输出格式，写入 stderr")
    common.add_argument("--diagnostics-dir", type=Path, help="脱敏错误诊断保存目录")
    common.add_argument("--debug", action="store_true", help="显示脱敏异常位置，不输出请求正文或密钥")
    from novelforge.cli_workspace import add_commands

    add_commands(subparsers, common)
    from novelforge.cli_projects import add_commands as add_project_commands

    add_project_commands(subparsers, common)
    from novelforge.cli_extract import add_commands as add_extract_commands

    add_extract_commands(subparsers, common)
    from novelforge.cli_volume import add_commands as add_volume_commands

    add_volume_commands(subparsers, common)
    from novelforge.cli_assets import add_commands as add_asset_commands

    add_asset_commands(subparsers, common)
    from novelforge.cli_audit import add_commands as add_audit_commands

    add_audit_commands(subparsers, common)
    subparsers.add_parser(
        "endpoints", parents=[common], help="列出已保存端点（不显示密钥）"
    )
    subparsers.add_parser("presets", parents=[common], help="列出可用写作预设")
    continuation = subparsers.add_parser(
        "continue", parents=[common], help="根据 TXT 前文续写下一章"
    )
    source = continuation.add_mutually_exclusive_group(required=True)
    source.add_argument("-i", "--input", help="输入 TXT 路径；- 表示标准输入")
    source.add_argument("--project", help="已有项目 ID，读取正文及项目档案")
    continuation.add_argument("--chapter", help="项目中的当前章节 ID，默认末章")
    continuation.add_argument(
        "--save", action="store_true", help="将项目续写保存为候选版本"
    )
    continuation.add_argument(
        "--lookback-context",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="注入回溯窗口内最近一次上下文提取结果",
    )
    continuation.add_argument(
        "--lookback-characters",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="注入回溯窗口内最近的主角和自定义角色档案",
    )
    books = continuation.add_mutually_exclusive_group()
    books.add_argument(
        "--worldbook", action="append", help="世界书 ID，可重复；省略沿用已选世界书"
    )
    books.add_argument(
        "--no-worldbooks", action="store_true", help="本次不注入已选世界书"
    )
    continuation.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="输入编码，默认 utf-8-sig；也可用 gb18030",
    )
    continuation.add_argument(
        "-o", "--output", type=Path, help="UTF-8 输出文件；省略时正文写入标准输出"
    )
    continuation.add_argument(
        "--force", action="store_true", help="允许替换已有输出文件"
    )
    instruction = continuation.add_mutually_exclusive_group()
    instruction.add_argument("--prompt", default="", help="本次续写要求")
    instruction.add_argument(
        "--prompt-file", type=Path, help="从 UTF-8 文件读取续写要求"
    )
    continuation.add_argument("--title", help="作品名称，默认使用输入文件名")
    endpoint = continuation.add_mutually_exclusive_group()
    endpoint.add_argument("--endpoint", help="已保存端点 ID，可通过 endpoints 查看")
    endpoint.add_argument("--base-url", help="API 根地址；也可设置 GENGBI_BASE_URL")
    continuation.add_argument("--model", help="模型名称；也可设置 GENGBI_MODEL")
    continuation.add_argument(
        "--api-key-env",
        default="GENGBI_API_KEY",
        help="提供密钥的环境变量名，默认 GENGBI_API_KEY",
    )
    continuation.add_argument(
        "--reasoning-effort",
        choices=["", "none", "auto", "minimal", "low", "medium", "high", "max"],
    )
    continuation.add_argument("--proxy", help="HTTP 代理 URL；传空字符串禁用已保存代理")
    continuation.add_argument(
        "--timeout",
        type=_positive_timeout,
        default=300.0,
        help="网络读取超时秒数，默认 300",
    )
    preset = continuation.add_mutually_exclusive_group()
    preset.add_argument("--preset", help="已保存预设 ID，默认项目绑定预设或 default")
    preset.add_argument("--preset-file", type=Path, help="赓笔或 SillyTavern 预设 JSON")
    continuation.add_argument(
        "--jailbreak",
        choices=["off", "low", "mid", "high", "cot"],
        help="本次破限等级；省略沿用预设，off 关闭破限及助手预填充",
    )
    continuation.add_argument("--temperature", type=float, help="温度，0 到 2")
    continuation.add_argument("--target-words", type=int, help="目标字数，100 到 50000")
    continuation.add_argument(
        "--lookback", type=int, help="最近多少章（含末章）；0 表示全部"
    )
    continuation.add_argument("--max-tokens", type=int, help="输出 Token 上限")
    continuation.add_argument(
        "--max-context", type=int, help="上下文 Token 上限，必须大于输出上限"
    )
    continuation.add_argument(
        "--stream",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="API 使用流式请求；正文在完成后统一输出",
    )
    continuation.add_argument(
        "--regex",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="对正文应用正则后处理",
    )
    continuation.add_argument(
        "--dry-run", action="store_true", help="只输出提示词与参数 JSON，不调用模型"
    )
    return parser


def _read_input(source: str, encoding: str) -> str:
    """从文件或管道读取文本；明确编码以兼容 Windows。"""
    if source != "-":
        return Path(source).read_text(encoding=encoding)
    if sys.stdin.isatty():
        raise ValueError("--input - 需要管道输入；也可直接指定 TXT 文件")
    if hasattr(sys.stdin, "buffer"):
        return sys.stdin.buffer.read().decode(encoding)
    return sys.stdin.read().lstrip("\ufeff")


def _check_output(args: argparse.Namespace) -> None:
    """在 API 调用前检查输出，避免覆盖源文件或无谓请求。"""
    if args.output is None:
        return
    for source in (
        args.input if args.input != "-" else None,
        args.prompt_file,
        args.preset_file,
        args.config,
    ):
        if source is not None:
            path = Path(source)
            if args.output.resolve() == path.resolve() or (
                args.output.exists() and path.exists() and args.output.samefile(path)
            ):
                raise ValueError("输出文件不能与输入、指令、预设或配置文件相同")
    if args.output.exists() and not args.force:
        raise FileExistsError(f"输出文件已存在（覆盖需 --force）: {args.output}")
    if not args.output.parent.is_dir():
        raise ValueError(f"输出目录不存在: {args.output.parent}")
    if args.output.is_dir():
        raise ValueError("输出路径不能是目录")


def _write_output(text: str, output: Path | None, force: bool) -> None:
    """完成后才提交文件；默认以硬链接原子创建，防止并发覆盖已有文件。"""
    text = text.rstrip("\n") + "\n"
    if output is None:
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    phase("写入输出文件")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=output.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        if force:
            os.replace(temporary, output)
        else:
            os.link(temporary, output)
    finally:
        if temporary is not None:
            from novelforge.cli_diagnostics import preserve_failure
            with preserve_failure():
                temporary.unlink(missing_ok=True)


async def _execute(args: argparse.Namespace) -> None:
    """编排输入输出；业务模块保持无 Qt 依赖。"""
    reset_operation(args)
    from novelforge.services.llm_client import strict_responses
    strict_responses.set(True)
    phase("读取配置与输入", project=getattr(args, "project", None), chapter=getattr(args, "chapter", None))
    if args.command not in {"continue", "endpoints", "presets"}:
        from novelforge.cli_workspace import execute

        await execute(args)
        return
    from novelforge.services.headless import read_config, read_json

    config = read_config(args.config)
    if args.command == "endpoints":
        rows = [
            {key: endpoint.get(key, "") for key in ("id", "name", "default_model")}
            for endpoint in config.get_endpoints()
        ]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.command == "presets":
        rows = {"default": {"id": "default", "name": "赓笔 默认预设"}}
        for path in sorted((config.get_storage_path() / "presets").glob("*.json")):
            data = read_json(path)
            rows[path.stem] = {"id": path.stem, "name": data.get("name", path.stem)}
        print(json.dumps(list(rows.values()), ensure_ascii=False, indent=2))
        return
    if args.project:
        from novelforge.cli_projects import storage_session

        with storage_session(config) as storage:
            await _continue(args, config, storage)
    else:
        await _continue(args, config)


async def _continue(args: argparse.Namespace, config, storage=None) -> None:
    """以相同组装链支持文件和项目，生成成功后才保存候选。"""
    from novelforge.services.headless import (
        ContinuationOptions,
        prepare_continuation,
        resolve_connection,
    )

    _check_output(args)
    context = None
    if storage is not None:
        from novelforge.cli_projects import load_writing_context
        from novelforge.cli_workspace import check_output

        context = await load_writing_context(storage, config, args)
        text = "\n".join(c.content for c in context.chapters)
        if args.output:
            check_output(
                args.output,
                args.force,
                config.config_path,
                *[
                    storage.get_chapter_file_path(context.project.id, c.id)
                    for c in storage.list_chapters(context.project.id)
                ],
            )
    else:
        if (
            args.chapter
            or args.save
            or args.lookback_context
            or args.lookback_characters
        ):
            raise ValueError(
                "--chapter/--save/--lookback-context/--lookback-characters 需要 --project"
            )
        text = _read_input(args.input, args.encoding)
    instruction = (
        args.prompt_file.read_text(encoding="utf-8-sig")
        if args.prompt_file
        else args.prompt
    )
    connection = resolve_connection(
        config,
        endpoint_id=args.endpoint,
        base_url=args.base_url
        if args.base_url is not None
        else (None if args.endpoint else os.environ.get("GENGBI_BASE_URL")),
        model=args.model if args.model is not None else os.environ.get("GENGBI_MODEL"),
        api_key=os.environ.get(args.api_key_env),
        reasoning_effort=args.reasoning_effort,
        proxy=args.proxy,
        timeout=args.timeout,
        decrypt=not args.dry_run,
    )
    if not args.dry_run and (not connection.base_url or not connection.model):
        raise ValueError("请指定 API 地址和模型，或先运行 config setup 配置默认端点")
    defaults = config.get_continuation_settings()
    options = ContinuationOptions(
        title=args.title
        or (
            context.project.name
            if context
            else Path(args.input).stem
            if args.input != "-"
            else "全文"
        ),
        instruction=instruction,
        preset_id=args.preset or (context.project.preset_id if context else "default"),
        preset_file=args.preset_file,
        jailbreak=args.jailbreak,
        temperature=args.temperature,
        target_words=args.target_words,
        lookback=args.lookback,
        max_tokens=args.max_tokens,
        max_context=args.max_context,
        apply_regex=args.regex
        if args.regex is not None
        else defaults.get("apply_regex", True),
        worldbook_ids=()
        if args.no_worldbooks
        else tuple(args.worldbook)
        if args.worldbook is not None
        else None,
    )
    with prepare_continuation(
        text, config, options, model=connection.model, context=context
    ) as prepared:
        if args.dry_run:
            result = json.dumps(
                {
                    "model": connection.model,
                    "model_prompt": connection.prompt_summary(),
                    "parameters": prepared.parameters,
                    "messages": prepared.assembled.messages,
                    "history_chapter_count": prepared.assembled.history_chapter_count,
                    "warnings": prepared.assembled.warnings,
                },
                ensure_ascii=False,
                indent=2,
            )
        else:
            for warning in prepared.assembled.warnings:
                print(f"提示: {warning}", file=sys.stderr)
            stream = (
                args.stream
                if args.stream is not None
                else not config.is_prefer_non_stream()
            )
            result = await prepared.generate(connection, stream=stream)
            if args.save:
                from novelforge.cli_projects import save_candidate

                version = save_candidate(
                    storage, context, prepared, result, connection.model
                )
                print(
                    f"已保存候选 {version.id}；接受为新章节：versions accept --chapter {context.current_chapter.id} {version.id}",
                    file=sys.stderr,
                )
    _write_output(result, args.output, args.force)


def main(argv: Sequence[str] | None = None) -> int:
    """返回 0=成功，1=运行失败，2=输入错误，130=用户中断。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if not arguments:
        from novelforge.cli_workspace import show_guide

        show_guide()
        return 0
    # 参数解析失败也要使用统一出口；仅预读诊断选项，不记录原始 argv。
    early = argparse.Namespace(error_format="text", diagnostics_dir=None, config=None, debug="--debug" in arguments)
    for option, attribute in (("--error-format", "error_format"), ("--diagnostics-dir", "diagnostics_dir"), ("--config", "config")):
        for item in arguments:
            if item.startswith(option + "="):
                setattr(early, attribute, item.split("=", 1)[1])
        if option in arguments:
            index = arguments.index(option)
            if index + 1 < len(arguments) and not arguments[index + 1].startswith("--"):
                setattr(early, attribute, arguments[index + 1])
    diagnostics = Diagnostics(early)
    diagnostics.start()
    try:
        args = build_parser().parse_args(arguments)
        diagnostics.args = args
        asyncio.run(_execute(args))
        if diagnostics.events:
            from novelforge.cli_diagnostics import CLIProblem, Failure
            diagnostics.report(CLIProblem(Failure(
                "completed_with_warnings", "命令已完成，但过程中出现重试、恢复或降级提示。",
                "检查诊断记录中的事件位置；如结果存在 degraded 标记，请复核提取结果。", 0,
            )))
        return 0
    except BrokenPipeError:
        # 避免 Python 退出时再次刷新已关闭管道，导致退出码被改写为 120。
        if hasattr(sys.stdout, "fileno"):
            with open(os.devnull, "w") as sink:
                os.dup2(sink.fileno(), sys.stdout.fileno())
        return 1
    except (Exception, KeyboardInterrupt, asyncio.CancelledError) as error:  # noqa: BLE001 -- 最外层统一保存结构化诊断
        return diagnostics.report(error)
    finally:
        diagnostics.close()


if __name__ == "__main__":
    raise SystemExit(main())
