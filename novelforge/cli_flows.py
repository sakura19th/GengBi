"""声明式流程的无界面执行、阶段审阅和恢复。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from novelforge.cli_workspace import check_output, emit, require

EXECUTION_ACTIONS = {"run", "resume", "runs", "run-show", "run-edit"}


def add_commands(actions: Any, common: argparse.ArgumentParser) -> None:
    start = actions.add_parser(
        "run", parents=[common], help="执行内置或导入流程，并保存阶段产物"
    )
    start.add_argument(
        "id", help="流程 ID，例如 planned_writing / writing_mode / rewrite_current"
    )
    start.add_argument("--project", required=True)
    start.add_argument("--chapter")
    start.add_argument("--prompt", default="")
    start.add_argument("--preset")
    start.add_argument("--worldbook", action="append")
    start.add_argument("--endpoint", help="临时覆盖所有阶段端点，省略按各流程配置")
    start.add_argument("--model", help="临时覆盖所有阶段模型")
    start.add_argument(
        "--jailbreak",
        choices=["off", "low", "mid", "high", "cot"],
        help="正文预设破限覆盖",
    )
    start.add_argument(
        "--analysis-jailbreak",
        choices=["off", "low", "mid", "high"],
        help="分析/审计破限覆盖",
    )
    start.add_argument("--target-words", type=int)
    start.add_argument("--lookback", type=int, default=5)
    start.add_argument(
        "--lookback-context", action=argparse.BooleanOptionalAction, default=None
    )
    start.add_argument(
        "--lookback-characters", action=argparse.BooleanOptionalAction, default=None
    )
    start.add_argument("--max-tokens", type=int, default=8000)
    start.add_argument("--max-context", type=int, default=128000)
    start.add_argument("--temperature", type=float)
    start.add_argument(
        "--settings", type=Path, help="卷阶段使用的 VolumeRunConfig JSON"
    )
    start.add_argument(
        "--chapters", type=int, help="含卷阶段时生成的章节数，默认沿用卷设置"
    )
    start.add_argument("--stream", action=argparse.BooleanOptionalAction, default=None)
    for parser in (
        start,
        actions.add_parser(
            "resume", parents=[common], help="继续已有流程运行，跳过已完成阶段"
        ),
    ):
        if parser is not start:
            parser.add_argument("id", help="运行 ID，见 flows runs")
        parser.add_argument(
            "--auto", action="store_true", help="自动接受分析和暂停点，适合脚本"
        )
        parser.add_argument("--pause-after", help="指定阶段 ID 完成后保存并退出")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="不调用模型；显示执行计划及下个可组装请求",
        )
        parser.add_argument(
            "-o", "--output", type=Path, help="导出最后阶段文本，候选仍保存在项目"
        )
        parser.add_argument("--force", action="store_true")
    actions.add_parser("runs", parents=[common], help="列出流程运行记录")
    for action in ("run-show", "run-edit"):
        parser = actions.add_parser(action, parents=[common])
        parser.add_argument("id", help="运行 ID")
        if action == "run-edit":
            parser.add_argument("stage", help="已完成阶段 ID；修改后需重新运行后续阶段")
            parser.add_argument(
                "-i",
                "--input",
                type=Path,
                required=True,
                help="替换阶段产物的 UTF-8 文本/JSON",
            )


def validate_plugin(plugin: Any) -> None:
    from novelforge.cli_volume import PHASES
    from novelforge.utils.ids import validate_id

    seen = set()
    phases = set()
    if not plugin.stages:
        raise ValueError("流程没有阶段")
    for stage in plugin.stages:
        validate_id(stage.id)
        if stage.id in seen:
            raise ValueError(f"阶段 ID 重复：{stage.id}")
        if stage.input_from and stage.input_from not in seen:
            raise ValueError(f"阶段 {stage.id} 的 input_from 必须指向更早的阶段")
        seen.add(stage.id)
        if stage.agent == "volume_phase":
            phase = stage.params.get("phase", stage.id)
            if phase not in PHASES:
                raise ValueError(f"未知卷阶段：{phase}")
            dependencies = {
                "volume_outline": {"deep_analysis"},
                "outline_audit": {"deep_analysis", "volume_outline"},
                "chapter_writing": {"deep_analysis", "outline_audit"},
            }.get(phase, set())
            if not dependencies <= phases:
                raise ValueError(
                    f"卷阶段 {phase} 缺少前序产物：{', '.join(sorted(dependencies - phases))}"
                )
            phases.add(phase)


def continuation_instruction(
    stage, prompt: str, previous: str, current_text: str
) -> str:
    if not previous:
        return prompt
    if stage.created_by == "planned_writing":
        return previous
    if stage.created_by == "rewrite_current":
        return f"请依据以下分析重写当前章节的完整正文。\n\n【原章节】\n{current_text}\n\n【重写分析】\n{previous}\n\n【用户要求】\n{prompt}"
    return f"【写作参考】\n{previous}\n\n【用户要求】\n{prompt}"


async def execute(args: argparse.Namespace, config: Any) -> None:
    from novelforge.cli import _write_output
    from novelforge.cli_audit import render_phase
    from novelforge.cli_extract import flow_jailbreak
    from novelforge.cli_projects import (
        load_writing_context,
        save_candidate,
        storage_session,
    )
    from novelforge.cli_runtime import (
        connect_checkpoints,
        create_volume_engine,
        preview_volume_request,
        resource_fingerprint,
        review_text,
        source_fingerprint,
    )
    from novelforge.core.storage import atomic_write_file
    from novelforge.models import (
        DeepAnalysis,
        FlowPlugin,
        VolumeOutline,
        VolumeRunConfig,
    )
    from novelforge.services.flow_plugin_service import FlowPluginService
    from novelforge.services.headless import (
        ContinuationOptions,
        prepare_continuation,
        read_json,
        resolve_connection,
    )
    from novelforge.services.history_service import HistoryService
    from novelforge.services.llm_client import LLMError
    from novelforge.utils.ids import generate_id, validate_id

    directory = config.get_storage_path() / "cli_flow_runs"
    if args.action == "runs":
        emit(
            [
                {
                    key: data.get(key)
                    for key in ("id", "project", "status", "stage", "versions")
                }
                for path in sorted(directory.glob("*.json"))
                if isinstance(data := read_json(path), dict)
            ]
        )
        return
    if args.action == "run":
        plugin = require(
            FlowPluginService(config.get_storage_path()).load_plugin(args.id), "流程"
        )
        validate_plugin(plugin)
        settings = (
            read_json(args.settings) if args.settings else config.get_volume_settings()
        )
        settings = dict(settings)
        if args.chapters is not None:
            settings["chapter_count"] = args.chapters
        if args.target_words is not None:
            settings["target_words_per_chapter"] = args.target_words
        volume_config = VolumeRunConfig.model_validate(settings)
        if args.target_words is None:
            args.target_words = (
                volume_config.target_words_per_chapter
                if any(s.agent.startswith("volume_") for s in plugin.stages)
                else config.get_continuation_settings().get(
                    "default_target_words", 2000
                )
            )
        option_keys = {
            "project",
            "chapter",
            "prompt",
            "preset",
            "worldbook",
            "endpoint",
            "model",
            "jailbreak",
            "analysis_jailbreak",
            "target_words",
            "lookback",
            "lookback_context",
            "lookback_characters",
            "max_tokens",
            "max_context",
            "temperature",
            "stream",
        }
        state = {
            "id": generate_id("flowrun_"),
            "project": args.project,
            "plugin": plugin.model_dump(mode="json"),
            "settings": volume_config.model_dump(mode="json"),
            "options": {k: v for k, v in vars(args).items() if k in option_keys},
            "completed": [],
            "outputs": {},
            "versions": {},
            "status": "ready",
        }
    else:
        validate_id(args.id)
        state = read_json(directory / f"{args.id}.json")
        if not isinstance(state, dict) or not all(
            k in state
            for k in (
                "plugin",
                "options",
                "outputs",
                "completed",
                "versions",
                "settings",
            )
        ):
            raise ValueError("运行记录格式错误，请查看 flows runs 或重新运行")
        plugin = FlowPlugin.model_validate(state["plugin"])
        validate_plugin(plugin)
        volume_config = VolumeRunConfig.model_validate(state["settings"])
    path = directory / f"{state['id']}.json"
    identifiers = [stage.id for stage in plugin.stages]
    if (
        not isinstance(state["completed"], list)
        or state["completed"] != identifiers[: len(state["completed"])]
        or not isinstance(state["outputs"], dict)
        or any(
            not isinstance(state["outputs"].get(key), str) for key in state["completed"]
        )
        or not isinstance(state["options"], dict)
        or not isinstance(state["versions"], dict)
    ):
        raise ValueError("运行记录的完成阶段或产物不完整，请新建运行")

    def save():
        atomic_write_file(path, json.dumps(state, ensure_ascii=False, indent=2))

    def volume_inputs(stage, phase, previous):
        inputs = {}
        for earlier in plugin.stages:
            if earlier.id not in state["completed"] or earlier.agent != "volume_phase":
                continue
            name = earlier.params.get("phase", earlier.id)
            if name != "chapter_writing":
                model = DeepAnalysis if name == "deep_analysis" else VolumeOutline
                inputs[name] = model.model_validate_json(state["outputs"][earlier.id])
        predecessor = {
            "volume_outline": "deep_analysis",
            "outline_audit": "volume_outline",
            "chapter_writing": "outline_audit",
        }.get(phase)
        if stage.input_from and previous and predecessor:
            model = DeepAnalysis if predecessor == "deep_analysis" else VolumeOutline
            inputs[predecessor] = model.model_validate_json(previous)
        return inputs

    if args.action == "run-show":
        emit(state)
        return
    if args.action == "run-edit":
        if args.stage not in state["completed"]:
            raise ValueError("只能修改已完成的阶段")
        replacement = args.input.read_text(encoding="utf-8-sig")
        if not replacement.strip():
            raise ValueError("阶段产物不能为空")
        stage = next(s for s in plugin.stages if s.id == args.stage)
        if (
            stage.agent in {"continuation", "volume_pipeline"}
            or stage.params.get("phase") == "chapter_writing"
        ):
            raise ValueError(
                "正文候选请用 versions 编辑；阶段编辑用于分析、大纲及暂停点"
            )
        if stage.agent == "volume_phase":
            model = (
                DeepAnalysis
                if stage.params.get("phase", stage.id) == "deep_analysis"
                else VolumeOutline
            )
            model.model_validate_json(replacement)
        state["outputs"][args.stage] = replacement
        cutoff = state["completed"].index(args.stage) + 1
        state["completed"] = state["completed"][:cutoff]
        state["outputs"] = {
            k: v for k, v in state["outputs"].items() if k in state["completed"]
        }
        state["status"] = "paused"
        save()
        emit({"edited": args.stage, "next": f"flows resume {state['id']}"})
        return
    if args.pause_after and args.pause_after not in {s.id for s in plugin.stages}:
        raise ValueError("--pause-after 必须是本流程中的阶段 ID")
    if not args.dry_run and not args.auto and not sys.stdin.isatty():
        raise ValueError("流程需要阶段审阅；交互终端运行，或使用 --auto 自动接受")
    options = argparse.Namespace(**state["options"])
    if options.lookback < 0:
        raise ValueError("回溯章节数不能为负数")
    if args.output:
        check_output(args.output, args.force, config.config_path, path)
    with storage_session(config) as storage:
        context = await load_writing_context(storage, config, options)
        state["options"]["chapter"] = context.current_chapter.id
        if options.worldbook is None:
            options.worldbook = config.get_selected_worldbook_ids()
            state["options"]["worldbook"] = options.worldbook
        fingerprint = source_fingerprint(context)
        if state.get("source_hash") and fingerprint != state["source_hash"]:
            raise ValueError("源章节或项目档案已改变，请新建流程运行")
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
        plan = [
            {
                "id": s.id,
                "agent": s.agent,
                "flow_key": s.flow_key,
                "input_from": s.input_from,
                "completed": s.id in state["completed"],
            }
            for s in plugin.stages
        ]
        if not args.dry_run:
            directory.mkdir(parents=True, exist_ok=True)
        try:
            for stage in plugin.stages:
                if stage.id in state["completed"]:
                    continue
                previous = (
                    state["outputs"].get(stage.input_from, "")
                    if stage.input_from
                    else ""
                )
                params = {**vars(options), **stage.params}
                flow = stage.flow_key or (
                    "single_audit" if stage.agent == "audit" else "single_continuation"
                )
                connection = resolve_connection(
                    config,
                    endpoint_id=options.endpoint,
                    model=options.model,
                    flow_key=flow,
                    decrypt=not args.dry_run,
                )
                if (
                    not args.dry_run
                    and stage.agent != "checkpoint"
                    and (not connection.base_url or not connection.model)
                ):
                    raise ValueError(
                        f"阶段 {stage.id} 未配置模型/端点，请使用 config set-flow {flow}"
                    )
                instruction = continuation_instruction(
                    stage, options.prompt, previous, context.current_chapter.content
                )
                preparation = ContinuationOptions(
                    instruction=instruction,
                    preset_id=options.preset or context.project.preset_id,
                    jailbreak=options.jailbreak,
                    target_words=params["target_words"],
                    lookback=params["lookback"],
                    max_tokens=params["max_tokens"],
                    max_context=params["max_context"],
                    temperature=params.get("temperature"),
                    worldbook_ids=tuple(options.worldbook),
                    exclude_current=params.get("exclude_current", False),
                )
                with prepare_continuation(
                    context.current_chapter.content,
                    config,
                    preparation,
                    model=connection.model,
                    context=context,
                ) as prepared:
                    stage_volume_config = VolumeRunConfig.model_validate(
                        volume_config.model_dump()
                        | {
                            k: v
                            for k, v in stage.params.items()
                            if k in VolumeRunConfig.model_fields
                        }
                    )
                    resources = resource_fingerprint(prepared)
                    if (
                        state.get("resource_hash")
                        and state["resource_hash"] != resources
                    ):
                        raise ValueError("预设、世界书或正则发生变化，请新建流程运行")
                    state["resource_hash"] = resources
                    messages = prepared.assembled.messages
                    if stage.agent == "audit":
                        phase = params.get("phase") or (
                            flow if flow != "single_continuation" else "single_audit"
                        )
                        rendered = render_phase(
                            phase,
                            context,
                            prepared.context_entries,
                            prompt=options.prompt,
                            content=context.current_chapter.content,
                            previous_output=previous,
                            target_words=params["target_words"],
                            lookback=params["lookback"],
                            exclude_current=params.get("exclude_current", False),
                        )
                        messages = [{"role": "system", "content": rendered}]
                        jailbreak = flow_jailbreak(
                            config, flow, options.analysis_jailbreak
                        )
                        if jailbreak:
                            messages.insert(0, {"role": "system", "content": jailbreak})
                    if args.dry_run:
                        if stage.agent in {"volume_phase", "volume_pipeline"}:
                            phase = (
                                "all"
                                if stage.agent == "volume_pipeline"
                                else params.get("phase", stage.id)
                            )
                            engine = create_volume_engine(
                                connection,
                                prepared,
                                context,
                                stage_volume_config,
                                options.prompt,
                                phase,
                                volume_inputs(stage, phase, previous),
                                config.is_prefer_non_stream(),
                            )
                            preview = await preview_volume_request(engine)
                            emit({"plan": plan, "next_stage": stage.id, **preview})
                            return
                        emit(
                            {
                                "plan": plan,
                                "next_stage": stage.id,
                                "model": connection.model,
                                "model_prompt": connection.prompt_summary(),
                                "messages": messages
                                if stage.agent in {"audit", "continuation"}
                                else [],
                                "note": "后续阶段使用实际前序产物；卷阶段内部请求由共享卷引擎按阶段组装。",
                            }
                        )
                        return
                    from novelforge.cli_diagnostics import phase as diagnostic_phase
                    diagnostic_phase("流程阶段", project=context.project.id, run=state["id"], stage=stage.id)
                    state.update(status="running", stage=stage.id)
                    save()
                    print(
                        f"流程 {state['id']}：{stage.name or stage.id}；恢复命令 flows resume {state['id']}",
                        file=sys.stderr,
                    )
                    stream = (
                        options.stream
                        if options.stream is not None
                        else stage.streaming and not config.is_prefer_non_stream()
                    )
                    if stage.agent == "audit":
                        client = connection.client()
                        try:
                            output, finish = await client.complete_text(
                                model=connection.model,
                                messages=messages,
                                temperature=params.get("temperature")
                                if params.get("temperature") is not None
                                else 0.2,
                                max_tokens=params["max_tokens"],
                                stream=stream,
                            )
                        finally:
                            from novelforge.cli_diagnostics import preserve_failure
                            with preserve_failure():
                                await client.close()
                        from novelforge.cli_diagnostics import require_model_text

                        require_model_text(output, finish, "流程分析")
                        if not args.auto:
                            output = review_text(
                                output, directory / f"{state['id']}-{stage.id}.txt"
                            )
                    elif stage.agent == "checkpoint":
                        output = previous or options.prompt
                        if not args.auto:
                            output = review_text(
                                output, directory / f"{state['id']}-{stage.id}.txt"
                            )
                    elif stage.agent == "continuation":
                        output = await prepared.generate(connection, stream=stream)
                        candidate = save_candidate(
                            storage,
                            context,
                            prepared,
                            output,
                            connection.model,
                            created_by=stage.created_by or "continuation",
                        )
                        state["versions"][stage.id] = candidate.id
                    else:
                        phase = (
                            "all"
                            if stage.agent == "volume_pipeline"
                            else params.get("phase", stage.id)
                        )
                        inputs = volume_inputs(stage, phase, previous)
                        engine = create_volume_engine(
                            connection,
                            prepared,
                            context,
                            stage_volume_config,
                            options.prompt,
                            phase,
                            inputs,
                            not stream,
                        )
                        connect_checkpoints(
                            engine,
                            auto=args.auto,
                            directory=directory,
                            run_id=state["id"],
                        )
                        outputs, errors = [], []
                        engine.phase_output.connect(
                            lambda _name, value, outputs=outputs: outputs.append(value)
                        )
                        engine.finished.connect(outputs.append)
                        engine.error.connect(
                            lambda _message, errors=errors: errors.append(True)
                        )
                        engine.auth_error.connect(
                            lambda errors=errors: errors.append(True)
                        )
                        engine.chapter_step_started.connect(
                            lambda index, step: print(
                                f"第 {index + 1} 章：{step}", file=sys.stderr
                            )
                        )
                        await engine.run_async()
                        if not outputs or outputs[-1] is None:
                            raise LLMError("卷阶段未生成产物")
                        artifact = outputs[-1]
                        if phase in {"all", "chapter_writing"}:
                            artifact.status = "failed" if errors else "completed"
                            storage.save_continuation(
                                artifact, context.current_chapter.id
                            )
                            state["versions"][stage.id] = artifact.id
                            history = HistoryService(storage)
                            now = history.now_iso()
                            history.log_continuation(
                                context.project.id,
                                context.current_chapter.id,
                                artifact.id,
                                now,
                                now,
                                artifact.status,
                                connection.model,
                                prepared.parameters,
                                artifact.prompt_snapshot,
                                artifact.content,
                            )
                            output = artifact.content
                        else:
                            output = artifact.model_dump_json(indent=2)
                        if errors:
                            raise LLMError(
                                "卷阶段包含错误；已保留可用候选，下次恢复重试该阶段"
                            )
                    state["outputs"][stage.id] = output
                    state["completed"].append(stage.id)
                    state["status"] = "paused"
                    save()
                    if args.pause_after == stage.id:
                        emit(
                            {
                                "id": state["id"],
                                "status": "paused",
                                "next": f"flows run-show {state['id']}；flows resume {state['id']}",
                            }
                        )
                        return
            if args.dry_run:
                emit({"plan": plan, "next_stage": None, "status": "completed"})
                return
            state["status"] = "completed"
            save()
            if args.output:
                _write_output(
                    state["outputs"][plugin.stages[-1].id], args.output, args.force
                )
            emit(
                {
                    "id": state["id"],
                    "status": "completed",
                    "chapter": context.current_chapter.id,
                    "versions": state["versions"],
                    "accept_mode": plugin.accept_mode,
                    "next": "versions replace --yes"
                    if plugin.accept_mode == "replace"
                    else "versions accept",
                }
            )
        except (KeyboardInterrupt, asyncio.CancelledError, EOFError):
            state["status"] = "interrupted"
            if not args.dry_run:
                from novelforge.cli_diagnostics import preserve_failure
                with preserve_failure():
                    save()
            raise KeyboardInterrupt from None
        except Exception:
            state["status"] = "failed"
            if not args.dry_run:
                from novelforge.cli_diagnostics import preserve_failure
                with preserve_failure():
                    save()
            raise
