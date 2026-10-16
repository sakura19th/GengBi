"""文章提取、世界书生成及项目资料维护的无界面工作流。"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from novelforge.cli_diagnostics import operation_failed, phase
from novelforge.cli_projects import (
    load_project_chapters,
    on_storage_loop,
    storage_session,
)
from novelforge.cli_workspace import check_output, emit, require

EXTRACT_FLOWS = {
    "context": "context_extraction",
    "worldbook": "context_extraction",
    "ontology": "ontology_extraction",
    "protagonist": "protagonist_extraction",
    "character": "custom_character_extraction",
    "style": "style_extraction",
    "rule": "custom_rule_parsing",
}


def add_commands(subparsers: Any, common: argparse.ArgumentParser) -> None:
    parser = subparsers.add_parser(
        "extract",
        parents=[common],
        help="从文章或项目提取世界书、上下文、角色、文风和约束",
    )
    parser.add_argument("kind", choices=list(EXTRACT_FLOWS))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--project", help="已有项目 ID，结果绑定项目或当前章")
    source.add_argument(
        "-i", "--input", type=Path, help="TXT 文章；先创建项目并导入，返回项目 ID"
    )
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--chapter", help="提取截至此章节的前文，默认末章")
    parser.add_argument("--name", help="新项目/世界书名称，默认文件名或项目名")
    parser.add_argument("--character", help="指定角色名，character 提取必填")
    parser.add_argument(
        "--prompt-file", type=Path, help="自定义约束原文，rule 提取必填"
    )
    parser.add_argument(
        "--lookback", type=int, default=0, help="最近 N 章；0 为截至当前章的所有前文"
    )
    parser.add_argument(
        "--batch-tokens", type=int, default=100000, help="每批输入 Token 上限，0 不分批"
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="从之前最近一次上下文/角色档案增量提取",
    )
    parser.add_argument("--refresh", action="store_true", help="忽略当前缓存重新提取")
    parser.add_argument("--endpoint", help="本次使用的端点 ID")
    parser.add_argument("--model", help="本次模型 ID，默认使用此提取流程的配置")
    parser.add_argument("--api-key-env", default="GENGBI_API_KEY")
    parser.add_argument("--stream", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument(
        "--jailbreak",
        choices=["off", "low", "mid", "high"],
        help="提取流程破限等级，省略继承流程配置",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="可选导出路径；worldbook 导出 ST JSON，其余导出原生 JSON",
    )
    parser.add_argument("--force", action="store_true", help="允许覆盖已有导出文件")

    profiles = subparsers.add_parser(
        "profiles", help="查看、编辑和复制项目/章节的提取资料"
    )
    actions = profiles.add_subparsers(dest="action", required=True)
    for action in ("show", "set", "copy"):
        parser = actions.add_parser(action, parents=[common])
        parser.add_argument(
            "kind",
            choices=[
                "context",
                "ontology",
                "style",
                "protagonist",
                "characters",
                "rules",
            ],
        )
        parser.add_argument("--project", required=True)
        parser.add_argument("--chapter", help="章节资料默认末章")
        if action == "set":
            parser.add_argument(
                "-i",
                "--input",
                type=Path,
                required=True,
                help="编辑后的 JSON，覆盖该类资料；先用 show 导出",
            )
            parser.add_argument("--yes", action="store_true", required=True)
        if action == "copy":
            parser.add_argument("--to", required=True, help="同项目目标章节 ID")
            parser.add_argument(
                "--yes",
                action="store_true",
                required=True,
                help="角色档案覆盖同名项，上下文追加副本",
            )


def operation_config(config: Any, flow: str, args: argparse.Namespace):
    """仅在内存应用模型/密钥覆盖，保留其他流程设置。"""
    from novelforge.core.config import ConfigManager
    from novelforge.services.headless import resolve_connection

    runtime = ConfigManager(config.config_path)
    runtime.config = copy.deepcopy(config.config)
    connection = resolve_connection(
        config,
        flow_key=flow,
        endpoint_id=args.endpoint,
        model=args.model or os.environ.get("GENGBI_MODEL"),
        api_key=os.environ.get(args.api_key_env),
        base_url=None if args.endpoint else os.environ.get("GENGBI_BASE_URL"),
    )
    if not connection.base_url or not connection.model:
        raise ValueError(
            "此流程未配置端点或模型，请运行 config setup 或 config set-flow"
        )
    from novelforge.cli_diagnostics import register_secret
    register_secret(connection.api_key)
    phase("读取配置与输入", model=connection.model, endpoint=args.endpoint or "默认流程端点")
    identifier = "cli-operation"
    endpoint = {
        "id": identifier,
        "name": "本次调用",
        "base_url": connection.base_url,
        "default_model": connection.model,
        "api_key_encrypted": runtime.encrypt_api_key(connection.api_key),
        "allow_empty_key": not connection.api_key,
        "reasoning_effort": connection.reasoning_effort,
        "extra_headers": connection.extra_headers,
        "model_prompts": connection.model_prompts,
        "extra_payload": connection.extra_payload,
        "merge_system_messages": connection.merge_system_messages,
    }
    runtime.config["api_endpoints"] = [*runtime.get_endpoints(), endpoint]
    runtime.config.setdefault("flow_endpoints", {})[flow] = identifier
    runtime.config.setdefault("flow_models", {})[flow] = connection.model
    if args.stream is not None:
        runtime.config.setdefault("continuation", {})[
            "prefer_non_stream"
        ] = not args.stream
    return runtime


def flow_jailbreak(config: Any, flow: str, level: str | None) -> str:
    from novelforge.services.jailbreak_provider import JailbreakProvider

    selected = level if level is not None else config.get_flow_jailbreak(flow)
    return (
        config.get_flow_jailbreak_custom(flow)
        if selected == "custom"
        else JailbreakProvider().get_jailbreak(flow, selected)
    )


async def execute(args: argparse.Namespace, config: Any) -> None:
    """完成提取、持久化和导出，不把底层 API 错误体打印到终端。"""
    from novelforge.cli import _write_output
    from novelforge.core.token_counter import TokenCounter
    from novelforge.models.worldbook import WorldBook
    from novelforge.services.context_extractor import ContextExtractor
    from novelforge.services.importer import DEFAULT_CHAPTER_PATTERN, TxtImporter
    from novelforge.services.worldbook_service import WorldBookService
    from novelforge.utils.ids import generate_id

    if args.command == "profiles":
        await run_profiles(args, config)
        return
    if args.lookback < 0 or args.batch_tokens < 0:
        raise ValueError("回溯章节数和分批 Token 上限不能为负数")
    if args.kind == "character" and not args.character:
        raise ValueError("指定角色提取需要 --character 角色名")
    if args.kind == "rule" and not args.prompt_file:
        raise ValueError("约束提取需要 --prompt-file 要求.txt")
    if args.incremental and args.kind not in {
        "context",
        "worldbook",
        "protagonist",
        "character",
    }:
        raise ValueError(
            "此类提取不支持增量；可对上下文、世界书、主角或指定角色使用 --incremental"
        )
    if args.input and args.chapter:
        raise ValueError("--chapter 只能与已有 --project 一起使用")
    if args.output:
        check_output(
            args.output, args.force, args.input, args.prompt_file, config.config_path
        )
    raw_rule = (
        args.prompt_file.read_text(encoding="utf-8-sig") if args.prompt_file else ""
    )
    if args.kind == "rule" and not raw_rule.strip():
        raise ValueError("约束原文为空")
    runtime = operation_config(config, EXTRACT_FLOWS[args.kind], args)
    with storage_session(config) as storage:
        if args.input:
            text = args.input.read_text(encoding=args.encoding)
            if not text.strip():
                raise ValueError("输入文章为空")
            name = args.name or args.input.stem
            chapters = TxtImporter.split_text(
                text, "pending", DEFAULT_CHAPTER_PATTERN, False, name
            )
            project = storage.create_project(
                name=name, source_file=str(args.input), novel_profile={"title": name}
            )
            for chapter in chapters:
                chapter.project_id = project.id
                storage.save_chapter(chapter)
            args.project = project.id
            print(
                f"已导入项目 {project.id}，共 {len(chapters)} 章；提取失败时可用 --project {project.id} 重试。",
                file=sys.stderr,
            )
        project, chapters, current = load_project_chapters(
            storage, args.project, args.chapter
        )
        if args.output:
            check_output(
                args.output,
                args.force,
                *[storage.get_chapter_file_path(project.id, c.id) for c in chapters],
            )
        extractor = ContextExtractor(storage, runtime)
        phase("提取请求与响应解析", project=project.id, chapter=current.id)
        jailbreak = flow_jailbreak(runtime, EXTRACT_FLOWS[args.kind], args.jailbreak)
        base = None
        if args.incremental:
            if args.kind in {"context", "worldbook"}:
                base = await on_storage_loop(
                    storage,
                    extractor.find_latest_context_base(project.id, chapters, current),
                )
            elif args.kind == "protagonist":
                base = extractor.find_protagonist_base(chapters, current)
            else:
                base = extractor.find_custom_character_base(
                    chapters, current, args.character
                )
            if not base:
                raise ValueError(
                    "未找到更早章节的提取结果；先不带 --incremental 完成一次提取"
                )
        incremental = (
            {
                "incremental_base": base[1],
                "incremental_base_index": chapters.index(base[0]),
            }
            if base
            else {}
        )
        print(
            f"正在提取 {args.kind}，截至第 {current.index + 1} 章；结果保存到项目 {project.id}。",
            file=sys.stderr,
        )
        if args.kind in {"context", "worldbook"}:
            result = await on_storage_loop(
                storage,
                extractor.extract(
                    project,
                    chapters,
                    current,
                    force_refresh=args.refresh,
                    lookback_override=args.lookback,
                    token_limit_override=args.batch_tokens,
                    jailbreak_text=jailbreak,
                    **incremental,
                ),
            )
            if result.status != "completed" or not result.entries:
                raise operation_failed("上下文/世界书提取失败。", result.error or "没有有效条目")
            phase("保存提取结果", project=project.id, chapter=current.id)
            await on_storage_loop(
                storage,
                extractor.save_edited_entries(project.id, current.id, result.entries),
            )
            data = [e.model_dump(mode="json") for e in result.entries]
            book = None
            if args.kind == "worldbook":
                book = WorldBook(
                    id=generate_id("wb_"),
                    name=args.name or f"{project.name}-提取设定",
                    entries=result.entries,
                )
                WorldBookService(storage.storage_path).save_worldbook(book)
                data = book.model_dump(mode="json")
            info = {
                "project": project.id,
                "chapter": current.id,
                "kind": args.kind,
                "entries": len(result.entries),
                "from_cache": result.from_cache,
                "worldbook": book.id if book else None,
                "data": data,
            }
            if book:
                info["next"] = {
                    "review": f"worldbooks show {book.id}",
                    "preview": f"continue --project {project.id} --worldbook {book.id} --dry-run",
                    "continue": f"continue --project {project.id} --worldbook {book.id} --save",
                }
        else:
            kwargs = {
                "token_limit": args.batch_tokens,
                "lookback": args.lookback,
                "jailbreak_text": jailbreak,
            }
            if args.kind in {"protagonist", "character"}:
                method = (
                    extractor.extract_protagonist_streaming
                    if args.kind == "protagonist"
                    else extractor.extract_custom_character_streaming
                )
                character = (
                    {"character_name": args.character}
                    if args.kind == "character"
                    else {}
                )
                value, message = await on_storage_loop(
                    storage,
                    method(
                        project, chapters, current, **kwargs, **character, **incremental
                    ),
                )
                if value is not None:
                    if args.kind == "protagonist":
                        storage.update_chapter_protagonist(current.id, value)
                    else:
                        storage.update_chapter_custom_characters(
                            current.id,
                            {**current.custom_characters, args.character: value},
                        )
            elif args.kind == "ontology":
                from novelforge.services.ontology_extractor import OntologyExtractor

                value, message = await on_storage_loop(
                    storage,
                    OntologyExtractor(
                        storage, runtime, TokenCounter()
                    ).extract_ontology_streaming(
                        project, chapters, current_chapter=current, **kwargs
                    ),
                )
            elif args.kind == "style":
                from novelforge.services.style_extractor import StyleExtractor

                value, message = await on_storage_loop(
                    storage,
                    StyleExtractor(
                        storage, runtime, TokenCounter()
                    ).extract_style_streaming(
                        project, chapters, current_chapter=current, **kwargs
                    ),
                )
            else:
                from novelforge.services.custom_audit_rule_service import (
                    CustomAuditRuleService,
                )

                cached = await on_storage_loop(
                    storage, extractor.load_cached_entries(project.id, current.id)
                )
                value, message = await on_storage_loop(
                    storage,
                    CustomAuditRuleService(storage, runtime).parse_rule_streaming(
                        project,
                        raw_rule,
                        context_entries=(cached or {}).get("entries", []),
                        jailbreak_text=jailbreak,
                    ),
                )
            if value is None or "保存失败" in message or "持久化失败" in message:
                raise operation_failed(f"{args.kind} 提取或保存失败。", message)
            data = value.model_dump(mode="json")
            info = {
                "project": project.id,
                "chapter": current.id,
                "kind": args.kind,
                "data": data,
                "degraded": "降级" in message,
            }
        if args.output:
            phase("导出提取结果", project=project.id, chapter=current.id)
            if args.kind == "worldbook":
                with TemporaryDirectory(prefix="gengbi-worldbook-") as directory:
                    temporary = Path(directory) / "export.json"
                    WorldBookService(storage.storage_path).export_to_st_json(
                        book, temporary
                    )
                    _write_output(
                        temporary.read_text(encoding="utf-8"), args.output, args.force
                    )
            else:
                _write_output(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    args.output,
                    args.force,
                )
            info["output"] = str(args.output)
        emit(info)


async def run_profiles(args: argparse.Namespace, config: Any) -> None:
    """使用模型校验编辑内容，按原生规则复制上下文及角色资料。"""
    from novelforge.models import ContextEntry, ProtagonistProfile
    from novelforge.models.custom_audit_rule import CustomAuditRule
    from novelforge.models.ontology import WorldOntology
    from novelforge.models.style_profile import StyleProfile
    from novelforge.services.context_extractor import ContextExtractor
    from novelforge.services.headless import read_json
    from novelforge.utils.ids import generate_id, validate_id

    with storage_session(config) as storage:
        project, _, current = load_project_chapters(storage, args.project, args.chapter)
        extractor = ContextExtractor(storage, config)
        cached = await on_storage_loop(
            storage, extractor.load_cached_entries(project.id, current.id)
        )
        current_values = {
            "context": (cached or {}).get("entries", []),
            "ontology": project.world_ontology,
            "style": project.style_profile,
            "protagonist": current.protagonist_profile,
            "characters": current.custom_characters,
            "rules": project.custom_audit_rules,
        }
        value = current_values[args.kind]
        if args.action == "set":
            data = read_json(args.input)
            if args.kind in {"context", "rules"} and not isinstance(data, list):
                raise ValueError("上下文和约束资料必须是 JSON 数组")
            if args.kind == "characters" and not isinstance(data, dict):
                raise ValueError("角色资料必须是角色名到档案的 JSON 对象")
            if args.kind == "context":
                value = [ContextEntry.model_validate(item) for item in data]
            elif args.kind == "characters":
                value = {
                    name: ProtagonistProfile.model_validate(item)
                    for name, item in data.items()
                }
            elif args.kind == "rules":
                value = [CustomAuditRule.model_validate(item) for item in data]
            else:
                cls = {
                    "ontology": WorldOntology,
                    "style": StyleProfile,
                    "protagonist": ProtagonistProfile,
                }[args.kind]
                value = None if data is None else cls.model_validate(data)
        if args.action == "copy":
            if not value:
                raise ValueError("来源章节没有可复制的资料，请先提取或编辑")
            if args.kind not in {"context", "protagonist", "characters"}:
                raise ValueError("世界观、文风和约束是项目全局资料，无需复制到章节")
            validate_id(args.to)
            target = require(storage.load_chapter(args.to), "目标章节")
            if target.project_id != project.id or target.id == current.id:
                raise ValueError("目标必须是同一项目中的另一个章节")
            if args.kind == "context":
                existing = await on_storage_loop(
                    storage, extractor.load_cached_entries(project.id, target.id)
                )
                value = [
                    *(existing or {}).get("entries", []),
                    *[
                        e.model_copy(
                            update={
                                "uid": generate_id("ctx_"),
                                "copied_from": current.index,
                            },
                            deep=True,
                        )
                        for e in value
                    ],
                ]
            elif args.kind == "characters":
                value = {
                    **target.custom_characters,
                    **{
                        name: p.model_copy(
                            update={"copied_from": current.index}, deep=True
                        )
                        for name, p in value.items()
                    },
                }
            elif value is not None:
                value = value.model_copy(
                    update={"copied_from": current.index}, deep=True
                )
            current = target
        if args.action != "show":
            if args.kind == "context":
                await on_storage_loop(
                    storage,
                    extractor.save_edited_entries(project.id, current.id, value),
                )
            elif args.kind == "protagonist":
                storage.update_chapter_protagonist(current.id, value)
            elif args.kind == "characters":
                storage.update_chapter_custom_characters(current.id, value)
            else:
                setattr(
                    project,
                    {
                        "ontology": "world_ontology",
                        "style": "style_profile",
                        "rules": "custom_audit_rules",
                    }[args.kind],
                    value,
                )
                storage.save_project(project)

        def serialize(item):
            if hasattr(item, "model_dump"):
                return item.model_dump(mode="json")
            if isinstance(item, list):
                return [serialize(x) for x in item]
            if isinstance(item, dict):
                return {key: serialize(val) for key, val in item.items()}
            return item

        emit(serialize(value))
