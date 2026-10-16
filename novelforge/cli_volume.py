"""整卷生成、阶段产物和可恢复运行；与桌面端共用 VolumeLogic。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from novelforge.cli_projects import load_writing_context, storage_session
from novelforge.cli_workspace import check_output, emit, require

PHASES = ("deep_analysis", "volume_outline", "outline_audit", "chapter_writing")


def add_commands(subparsers: Any, common: argparse.ArgumentParser) -> None:
    parser = subparsers.add_parser(
        "volume", help="整卷分析、大纲、审计及多章写作；支持阶段恢复"
    )
    actions = parser.add_subparsers(dest="action", required=True)
    start = actions.add_parser("start", parents=[common], help="开始一卷并保存运行记录")
    start.add_argument("--project", required=True)
    start.add_argument("--chapter", help="续写起点，默认末章")
    start.add_argument(
        "--chapters", type=int, help="本卷章节数，2 到 20；默认沿用卷设置"
    )
    start.add_argument("--target-words", type=int, help="每章目标字数，默认沿用卷设置")
    start.add_argument(
        "--settings",
        type=Path,
        help="VolumeRunConfig JSON：审计、修订、切分与暂停点设置",
    )
    start.add_argument("--prompt", default="", help="本卷创作要求")
    start.add_argument("--preset", help="默认使用项目绑定预设")
    start.add_argument("--jailbreak", choices=["off", "low", "mid", "high", "cot"])
    start.add_argument(
        "--worldbook", action="append", help="世界书 ID，可重复；默认沿用已选世界书"
    )
    start.add_argument("--endpoint")
    start.add_argument("--model")
    start.add_argument(
        "--max-tokens",
        type=int,
        default=8000,
        help="正文输出上限；分析/审计按各阶段配置",
    )
    start.add_argument("--max-context", type=int, default=128000)
    start.add_argument("--lookback", type=int, default=5)
    start.add_argument(
        "--lookback-context", action=argparse.BooleanOptionalAction, default=None
    )
    start.add_argument(
        "--lookback-characters", action=argparse.BooleanOptionalAction, default=None
    )
    for parser in (
        start,
        actions.add_parser(
            "resume", parents=[common], help="从保存的最近未完成阶段重试/继续"
        ),
    ):
        if parser is not start:
            parser.add_argument("id", help="运行 ID，可从 volume list 查询")
        parser.add_argument(
            "--auto", action="store_true", help="自动通过交互暂停点，适合脚本运行"
        )
        parser.add_argument(
            "--pause-after",
            choices=PHASES[:-1],
            help="指定阶段结束后保存并退出，之后 volume resume",
        )
        parser.add_argument(
            "-o", "--output", type=Path, help="可选导出整卷正文；候选始终保存在项目中"
        )
        parser.add_argument("--force", action="store_true")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="预览下个阶段的首个模型请求，不调用 API",
        )
    actions.add_parser("list", parents=[common], help="列出运行状态")
    for name in ("show", "edit"):
        parser = actions.add_parser(
            name,
            parents=[common],
            help="查看运行和阶段产物"
            if name == "show"
            else "用编辑后的 JSON 替换已完成阶段的产物",
        )
        parser.add_argument("id")
        if name == "edit":
            parser.add_argument("phase", choices=PHASES[:-1])
            parser.add_argument("-i", "--input", type=Path, required=True)


async def execute(args: argparse.Namespace, config: Any) -> None:
    """分阶段执行；运行记录不包含 API 密钥或请求认证头。"""
    from novelforge.cli import _write_output
    from novelforge.cli_runtime import (
        connect_checkpoints,
        create_volume_engine,
        preview_volume_request,
        resource_fingerprint,
        source_fingerprint,
    )
    from novelforge.core.storage import atomic_write_file
    from novelforge.models import DeepAnalysis, VolumeOutline, VolumeRunConfig
    from novelforge.services.headless import (
        ContinuationOptions,
        prepare_continuation,
        read_json,
        resolve_connection,
    )
    from novelforge.services.history_service import HistoryService
    from novelforge.services.llm_client import LLMError
    from novelforge.utils.ids import generate_id, validate_id

    directory = config.get_storage_path() / "cli_runs"
    if args.action == "list":
        emit(
            [
                {
                    key: data.get(key)
                    for key in ("id", "project", "status", "phase", "version_id")
                }
                for path in sorted(directory.glob("*.json"))
                if isinstance(data := read_json(path), dict)
            ]
        )
        return
    classes = {
        "deep_analysis": DeepAnalysis,
        "volume_outline": VolumeOutline,
        "outline_audit": VolumeOutline,
    }
    if args.action == "start":
        settings = (
            read_json(args.settings)
            if args.settings
            else config.get_volume_settings().copy()
        )
        if not isinstance(settings, dict):
            raise ValueError("卷配置必须是 JSON 对象")
        if args.chapters is not None:
            settings["chapter_count"] = args.chapters
        if args.target_words is not None:
            settings["target_words_per_chapter"] = args.target_words
        volume_config = VolumeRunConfig.model_validate(settings)
        args.target_words = volume_config.target_words_per_chapter
        state = {
            "id": generate_id("run_"),
            "project": args.project,
            "status": "ready",
            "completed": [],
            "artifacts": {},
            "options": {
                k: v
                for k, v in vars(args).items()
                if k
                in {
                    "project",
                    "chapter",
                    "prompt",
                    "preset",
                    "jailbreak",
                    "worldbook",
                    "endpoint",
                    "model",
                    "max_tokens",
                    "max_context",
                    "lookback",
                    "lookback_context",
                    "lookback_characters",
                    "target_words",
                }
            },
            "settings": volume_config.model_dump(mode="json"),
        }
        path = directory / f"{state['id']}.json"
    else:
        validate_id(args.id)
        path = directory / f"{args.id}.json"
        state = read_json(path)
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("completed"), list)
            or state["completed"] != list(PHASES)[: len(state["completed"])]
            or not isinstance(state.get("artifacts"), dict)
            or not isinstance(state.get("options"), dict)
        ):
            raise ValueError("卷运行记录格式错误，请新建运行")
        if args.action == "show":
            emit(state)
            return
        if args.action == "edit":
            if args.phase not in state["completed"]:
                raise ValueError("只能编辑已完成阶段")
            value = classes[args.phase].model_validate(read_json(args.input))
            state["artifacts"][args.phase] = value.model_dump(mode="json")
            cutoff = PHASES.index(args.phase)
            state["completed"] = [
                p for p in state["completed"] if PHASES.index(p) <= cutoff
            ]
            state["status"] = "paused"
            atomic_write_file(path, json.dumps(state, ensure_ascii=False, indent=2))
            emit({"edited": args.phase, "next": f"volume resume {args.id}"})
            return
        volume_config = VolumeRunConfig.model_validate(state["settings"])
    if not args.dry_run and not args.auto and not sys.stdin.isatty():
        raise ValueError("卷续写包含交互暂停点；在交互终端运行，或加 --auto 自动通过")
    if args.output:
        check_output(args.output, args.force, config.config_path, path)
    options = argparse.Namespace(**state["options"])
    if not args.dry_run:
        directory.mkdir(parents=True, exist_ok=True)

    def save_state():
        atomic_write_file(path, json.dumps(state, ensure_ascii=False, indent=2))

    with storage_session(config) as storage:
        context = await load_writing_context(storage, config, options)
        # 固定首次选中的章节，避免用户增加章节后恢复时偷偷改变续写起点。
        options.chapter = context.current_chapter.id
        if options.worldbook is None:
            options.worldbook = config.get_selected_worldbook_ids()
            state["options"]["worldbook"] = options.worldbook
        state["options"]["chapter"] = options.chapter
        fingerprint = source_fingerprint(context)
        if state.get("source_hash") and state["source_hash"] != fingerprint:
            raise ValueError(
                "运行期间源章节或档案已变化，请新建卷运行，避免复用过时大纲"
            )
        state["source_hash"] = fingerprint
        if args.output:
            check_output(
                args.output,
                args.force,
                *[
                    storage.get_chapter_file_path(context.project.id, c.id)
                    for c in storage.list_chapters(context.project.id)
                ],
            )
        if state["status"] == "completed" or state["completed"] == list(PHASES):
            version = require(
                next(
                    (
                        v
                        for v in storage.list_continuations(context.current_chapter.id)
                        if v.id == state.get("version_id")
                    ),
                    None,
                ),
                "卷候选",
            )
            if args.output:
                _write_output(version.content, args.output, args.force)
            emit({"id": state["id"], "status": "completed", "version_id": version.id})
            return
        connection = resolve_connection(
            config,
            flow_key="volume_continuation",
            endpoint_id=options.endpoint,
            model=options.model,
            decrypt=not args.dry_run,
        )
        if not args.dry_run and (not connection.base_url or not connection.model):
            raise ValueError(
                "卷续写尚未配置端点或模型，请运行 config set-flow volume_continuation"
            )
        inputs = {
            name: classes[name].model_validate(value)
            for name, value in state["artifacts"].items()
            if name in classes
        }
        preparation = ContinuationOptions(
            title=context.project.name,
            instruction=options.prompt,
            preset_id=options.preset or context.project.preset_id,
            jailbreak=options.jailbreak,
            target_words=options.target_words,
            lookback=options.lookback,
            max_tokens=options.max_tokens,
            max_context=options.max_context,
            worldbook_ids=tuple(options.worldbook)
            if options.worldbook is not None
            else None,
        )
        with prepare_continuation(
            "\n".join(c.content for c in context.chapters),
            config,
            preparation,
            model=connection.model,
            context=context,
        ) as prepared:
            if args.dry_run:
                phase = next((p for p in PHASES if p not in state["completed"]), None)
                if phase is None:
                    emit({"status": "completed", "messages": []})
                    return
                engine = create_volume_engine(
                    connection,
                    prepared,
                    context,
                    volume_config,
                    options.prompt,
                    phase,
                    inputs,
                    config.is_prefer_non_stream(),
                )
                emit(await preview_volume_request(engine))
                return
            resources = resource_fingerprint(prepared)
            if state.get("resource_hash") and state["resource_hash"] != resources:
                raise ValueError("预设、世界书或正则发生变化，请新建卷运行")
            state["resource_hash"] = resources
            try:
                for phase in PHASES:
                    if phase in state["completed"]:
                        continue
                    from novelforge.cli_diagnostics import phase as diagnostic_phase
                    diagnostic_phase("整卷阶段", project=context.project.id, run=state["id"], stage=phase)
                    state.update(status="running", phase=phase)
                    save_state()
                    print(
                        f"运行 {state['id']}：{phase}；中断后使用 volume resume {state['id']}。",
                        file=sys.stderr,
                    )
                    engine = create_volume_engine(
                        connection,
                        prepared,
                        context,
                        volume_config,
                        options.prompt,
                        phase,
                        inputs,
                        config.is_prefer_non_stream(),
                    )
                    errors = []
                    outputs = []
                    engine.error.connect(
                        lambda _message, errors=errors: errors.append("阶段调用失败")
                    )
                    engine.auth_error.connect(
                        lambda errors=errors: errors.append("认证失败")
                    )
                    engine.phase_output.connect(
                        lambda _name, output, outputs=outputs: outputs.append(output)
                    )
                    engine.finished.connect(outputs.append)
                    engine.chapter_step_started.connect(
                        lambda index, step: print(
                            f"第 {index + 1} 章：{step}", file=sys.stderr
                        )
                    )

                    connect_checkpoints(
                        engine, auto=args.auto, directory=directory, run_id=state["id"]
                    )
                    await engine.run_async()
                    if not outputs or outputs[-1] is None:
                        raise LLMError("阶段未产生有效结果")
                    output = outputs[-1]
                    if phase == "chapter_writing":
                        if state.get("version_id"):
                            storage.delete_continuation(state["version_id"])
                        output.status = "failed" if errors else "completed"
                        storage.save_continuation(output, context.current_chapter.id)
                        state["version_id"] = output.id
                        history = HistoryService(storage)
                        now = history.now_iso()
                        history.log_continuation(
                            context.project.id,
                            context.current_chapter.id,
                            output.id,
                            now,
                            now,
                            output.status,
                            connection.model,
                            prepared.parameters,
                            output.prompt_snapshot,
                            output.content,
                        )
                    else:
                        inputs[phase] = output
                        state["artifacts"][phase] = output.model_dump(mode="json")
                    if errors:
                        raise LLMError("阶段包含失败结果，已保留可用产物")
                    state["completed"].append(phase)
                    state["status"] = "completed" if phase == PHASES[-1] else "paused"
                    save_state()
                    if args.pause_after == phase:
                        emit(
                            {
                                "id": state["id"],
                                "status": "paused",
                                "next": f"volume show {state['id']}；volume resume {state['id']}",
                            }
                        )
                        return
                if args.output:
                    _write_output(output.content, args.output, args.force)
                emit(
                    {
                        "id": state["id"],
                        "status": "completed",
                        "version_id": state["version_id"],
                        "chapter": context.current_chapter.id,
                        "next": f"versions show --chapter {context.current_chapter.id} {state['version_id']}",
                    }
                )
            except (KeyboardInterrupt, asyncio.CancelledError):
                state["status"] = "interrupted"
                from novelforge.cli_diagnostics import preserve_failure
                with preserve_failure():
                    save_state()
                raise
            except Exception:
                state["status"] = "failed"
                from novelforge.cli_diagnostics import preserve_failure
                with preserve_failure():
                    save_state()
                raise
