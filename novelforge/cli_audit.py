"""审稿、按报告修订与多阶段写作所需的共享提示词渲染。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from novelforge.cli_extract import flow_jailbreak
from novelforge.cli_projects import (
    load_writing_context,
    save_candidate,
    storage_session,
)
from novelforge.cli_workspace import check_output, require


def add_commands(subparsers: Any, common: argparse.ArgumentParser) -> None:
    for command, label in [
        ("audit", "审查章节或候选，生成可编辑报告"),
        ("revise", "按审稿报告重写完整正文并保存新候选"),
    ]:
        parser = subparsers.add_parser(command, parents=[common], help=label)
        parser.add_argument("--project", required=True)
        parser.add_argument("--chapter", help="默认末章")
        parser.add_argument("--version", help="候选 ID；省略则针对当前章节正文")
        parser.add_argument("--prompt", default="", help="额外要求")
        parser.add_argument(
            "--report",
            type=Path,
            required=command == "revise",
            help="UTF-8 审稿意见文件，可先人工编辑",
        )
        parser.add_argument("--endpoint")
        parser.add_argument("--model")
        parser.add_argument("--preset")
        parser.add_argument("--worldbook", action="append")
        parser.add_argument("--lookback", type=int, default=5)
        parser.add_argument(
            "--lookback-context", action=argparse.BooleanOptionalAction, default=None
        )
        parser.add_argument(
            "--lookback-characters", action=argparse.BooleanOptionalAction, default=None
        )
        parser.add_argument("--target-words", type=int, default=2000)
        parser.add_argument("--max-tokens", type=int, default=8000)
        parser.add_argument("--max-context", type=int, default=128000)
        parser.add_argument(
            "--stream", action=argparse.BooleanOptionalAction, default=None
        )
        parser.add_argument(
            "--jailbreak",
            choices=["off", "low", "mid", "high"],
            help="审稿流程破限覆盖",
        )
        parser.add_argument("--dry-run", action="store_true", help="仅预览完整请求")
        parser.add_argument("-o", "--output", type=Path)
        parser.add_argument("--force", action="store_true")


def render_phase(
    phase: str,
    context: Any,
    entries: list,
    *,
    prompt: str = "",
    content: str = "",
    previous_output: str = "",
    report: str = "",
    target_words: int = 2000,
    lookback: int = 5,
    exclude_current: bool = False,
) -> str:
    """复用桌面端模板，显式填满宏，防止未替换占位符进入请求。"""
    from novelforge.utils.ids import validate_id
    from novelforge.utils.paths import get_agent_prompt_path, load_text_resource

    validate_id(phase)
    template = load_text_resource(get_agent_prompt_path(phase))

    def serialize(value: Any) -> str:
        if value is None or value == [] or value == {}:
            return "（无对应档案）"
        return json.dumps(
            value, ensure_ascii=False, default=lambda item: item.model_dump(mode="json")
        )

    chapters = [
        c
        for c in context.chapters
        if not exclude_current or c.id != context.current_chapter.id
    ]
    if lookback:
        chapters = chapters[-lookback:]
    values = {
        "book": context.project.novel_profile.title or context.project.name,
        "protagonist": context.project.novel_profile.protagonist,
        "user_input": prompt or "（无额外要求）",
        "written_text": content,
        "original_content": content,
        "critique": report,
        "current_chapter_text": context.current_chapter.content,
        "world_ontology": serialize(context.project.world_ontology),
        "style_profile": serialize(context.project.style_profile),
        "protagonist_profile": serialize(context.current_chapter.protagonist_profile),
        "custom_characters": serialize(context.current_chapter.custom_characters),
        "custom_audit_rules": serialize(context.project.custom_audit_rules),
        "context_entries": serialize([e for e in entries if e.enabled]),
        "previous_chapters_text": "\n\n".join(
            f"{c.title}\n{c.content}" for c in chapters
        ),
        "prev_analysis": previous_output,
        "original_outline": previous_output,
        "revision_feedback": report,
        "target_words": str(target_words),
        "chapter_plan": "（无卷大纲章节规划）",
        "outline": "（无细纲）",
        "pacing_speed": "（单章无推进速度约束）",
    }
    unknown = set(re.findall(r"\{\{(\w+)\}\}", template)) - values.keys()
    if unknown:
        raise ValueError("模板存在未支持的占位符：" + ", ".join(sorted(unknown)))
    return re.sub(r"\{\{(\w+)\}\}", lambda match: values[match.group(1)], template)


async def execute(args: argparse.Namespace, config: Any) -> None:
    from novelforge.cli import _write_output
    from novelforge.services.headless import (
        ContinuationOptions,
        prepare_continuation,
        resolve_connection,
    )

    if args.lookback < 0:
        raise ValueError("回溯章节数不能为负数")
    if args.output:
        check_output(args.output, args.force, config.config_path, args.report)
    report = args.report.read_text(encoding="utf-8-sig") if args.report else ""
    if args.command == "revise" and not report.strip():
        raise ValueError("审稿意见为空")
    with storage_session(config) as storage:
        context = await load_writing_context(storage, config, args)
        if args.output:
            check_output(
                args.output,
                args.force,
                *[
                    storage.get_chapter_file_path(context.project.id, c.id)
                    for c in storage.list_chapters(context.project.id)
                ],
            )
        content = context.current_chapter.content
        if args.version:
            candidate = require(
                next(
                    (
                        v
                        for v in context.current_chapter.continuations
                        if v.id == args.version
                    ),
                    None,
                ),
                "候选版本",
            )
            content = candidate.content
        connection = resolve_connection(
            config,
            endpoint_id=args.endpoint,
            model=args.model,
            flow_key="single_audit",
            decrypt=not args.dry_run,
        )
        if not args.dry_run and (not connection.base_url or not connection.model):
            raise ValueError("审稿流程未配置端点或模型，请先运行 config setup")
        options = ContinuationOptions(
            preset_id=args.preset or context.project.preset_id,
            target_words=args.target_words,
            lookback=args.lookback,
            max_tokens=args.max_tokens,
            max_context=args.max_context,
            worldbook_ids=tuple(args.worldbook) if args.worldbook is not None else None,
        )
        with prepare_continuation(
            content, config, options, context=context, model=connection.model
        ) as prepared:
            rendered = render_phase(
                "single_audit" if args.command == "audit" else "audit_rewrite",
                context,
                prepared.context_entries,
                prompt=args.prompt,
                content=content,
                report=report,
                target_words=args.target_words,
                lookback=args.lookback,
                exclude_current=not bool(args.version),
            )
            messages = [{"role": "system", "content": rendered}]
            jailbreak = flow_jailbreak(config, "single_audit", args.jailbreak)
            if jailbreak:
                messages.insert(0, {"role": "system", "content": jailbreak})
            if args.dry_run:
                result = json.dumps(
                    {
                        "model": connection.model,
                        "model_prompt": connection.prompt_summary(),
                        "messages": messages,
                        "max_tokens": args.max_tokens,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            elif args.command == "audit":
                from novelforge.cli_diagnostics import phase
                phase("审稿模型请求", model=connection.model)
                client = connection.client()
                try:
                    result, finish = await client.complete_text(
                        model=connection.model,
                        messages=messages,
                        temperature=0.2,
                        max_tokens=args.max_tokens,
                        stream=args.stream
                        if args.stream is not None
                        else not config.is_prefer_non_stream(),
                    )
                finally:
                    from novelforge.cli_diagnostics import preserve_failure
                    with preserve_failure():
                        await client.close()
                from novelforge.cli_diagnostics import require_model_text

                require_model_text(result, finish, "审稿结果")
            else:
                prepared.assembled.messages[:] = messages
                result = await prepared.generate(
                    connection,
                    stream=args.stream
                    if args.stream is not None
                    else not config.is_prefer_non_stream(),
                )
                candidate = save_candidate(
                    storage,
                    context,
                    prepared,
                    result,
                    connection.model,
                    created_by="audit_rewrite",
                )
                print(
                    f"已保存修订候选 {candidate.id}，原文保留；使用 versions show 查看。",
                    file=sys.stderr,
                )
        _write_output(result, args.output, args.force)
