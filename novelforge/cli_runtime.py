"""CLI 多阶段任务共用的恢复校验、卷执行和终端暂停点。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def source_fingerprint(context: Any) -> str:
    """候选增删不会失效；正文、档案和项目设置变化会使旧阶段失效。"""
    project = context.project.model_dump(mode="json")
    project.pop("updated_at", None)
    chapters = []
    for chapter in context.chapters:
        data = chapter.model_dump(mode="json")
        data.pop("continuations", None)
        data.pop("updated_at", None)
        chapters.append(data)
    value = {
        "project": project,
        "chapters": chapters,
        "entries": [e.model_dump(mode="json") for e in context.entries],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def resource_fingerprint(prepared: Any) -> str:
    value = {
        "preset": prepared.preset.model_dump(mode="json"),
        "entries": [
            entry.model_dump(mode="json") for entry in prepared.context_entries
        ],
        "regex": [
            entry.script.model_dump(mode="json")
            for entry in prepared.regex_engine._compiled
        ],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


async def preview_volume_request(engine: Any) -> dict:
    """在共享引擎的发送前暂停点捕获首个请求，不建立 API 连接。"""
    from novelforge.services.model_prompts import prompt_summary
    captured = []
    engine.debug_mode = True
    engine.checkpoint_reached.connect(lambda _name, _artifact: engine.resume(None))

    def capture(phase, messages, _endpoint, model):
        captured.append(
            {"phase": phase, "model": model, "messages": json.loads(messages),
             "model_prompt": prompt_summary(engine.model_prompts, engine.extra_payload.get("model", model))}
        )
        raise asyncio.CancelledError

    engine.prompt_debug_requested.connect(capture)
    try:
        await engine.run_async()
    except asyncio.CancelledError:
        if not captured:
            raise
    return (
        captured[0]
        if captured
        else {"messages": [], "note": "此阶段按配置不发送模型请求"}
    )


def review_text(text: str, path: Path) -> str:
    """审阅分析文本；只在明确接受或读取有效替换文件后推进。"""
    from novelforge.core.storage import atomic_write_file

    atomic_write_file(path, text)
    print(f"阶段产物：{path}", file=sys.stderr)
    while True:
        answer = input("回车接受；edit 文件路径替换；cancel 中断：").strip()
        if not answer:
            return text
        if answer == "cancel":
            raise KeyboardInterrupt
        if answer.startswith("edit "):
            try:
                replacement = Path(answer[5:].strip().strip('"')).read_text(
                    encoding="utf-8-sig"
                )
                if replacement.strip():
                    return replacement
                print("替换文件为空。", file=sys.stderr)
            except (OSError, UnicodeError) as error:
                from novelforge.cli_diagnostics import report_error
                report_error(error)
        else:
            print("输入无效，尚未接受阶段产物。", file=sys.stderr)


def connect_checkpoints(
    engine: Any, *, auto: bool, directory: Path, run_id: str
) -> None:
    from novelforge.core.storage import atomic_write_file
    from novelforge.services.headless import read_json

    def checkpoint(name, artifact):
        if auto:
            engine.resume({"action": "approve"} if name == "after_chapter" else None)
            return
        path = directory / f"{run_id}-{name}.json"
        data = (
            artifact.model_dump(mode="json")
            if hasattr(artifact, "model_dump")
            else artifact
        )
        atomic_write_file(
            path, json.dumps(data, ensure_ascii=False, indent=2, default=str)
        )
        print(f"暂停点 {name}，产物：{path}", file=sys.stderr)
        while True:
            prompt = "回车接受；cancel 中断"
            if name == "before_audit":
                prompt += "；focus 审计重点"
            elif name == "after_chapter":
                prompt += "；reject 修改意见"
            elif hasattr(artifact, "model_validate"):
                prompt += "；edit JSON文件路径"
            try:
                answer = input(prompt + "：").strip()
            except EOFError:
                raise KeyboardInterrupt from None
            if answer == "cancel":
                raise KeyboardInterrupt
            if not answer:
                engine.resume(
                    {"action": "approve"} if name == "after_chapter" else None
                )
                return
            if name == "before_audit" and answer.startswith("focus "):
                engine.resume(answer[6:].strip())
                return
            if (
                name == "after_chapter"
                and answer.startswith("reject ")
                and answer[7:].strip()
            ):
                engine.resume({"action": "reject", "feedback": answer[7:].strip()})
                return
            if (
                name not in {"before_audit", "after_chapter"}
                and answer.startswith("edit ")
                and hasattr(artifact, "model_validate")
            ):
                try:
                    engine.resume(
                        type(artifact).model_validate(
                            read_json(Path(answer[5:].strip().strip('"')))
                        )
                    )
                    return
                except (ValueError, OSError) as error:
                    from novelforge.cli_diagnostics import report_error
                    report_error(error)
                    continue
            print("输入无效，仍停留在当前阶段。", file=sys.stderr)

    engine.checkpoint_reached.connect(checkpoint)


def create_volume_engine(
    connection,
    prepared,
    context,
    volume_config,
    prompt,
    phase,
    inputs,
    prefer_non_stream=False,
):
    from novelforge.services.volume_engine import VolumeEngine

    return VolumeEngine(
        base_url=connection.base_url,
        api_key=connection.api_key,
        model=connection.model,
        parameters=prepared.parameters
        | {"reasoning_effort": connection.reasoning_effort},
        preset=prepared.preset,
        chapters=list(context.chapters),
        current_chapter=context.current_chapter,
        context_entries=prepared.context_entries,
        config=volume_config,
        user_input=prompt,
        novel_profile=context.project.novel_profile,
        project_id=context.project.id,
        chapter_id=context.current_chapter.id,
        chapter_metadata=context.current_chapter.metadata,
        regex_engine=prepared.regex_engine,
        template_engine=prepared.template_engine,
        preset_id=prepared.preset.id,
        preset_snapshot=prepared.preset.model_dump(mode="json"),
        world_ontology=context.project.world_ontology,
        protagonist_profile=context.current_chapter.protagonist_profile,
        style_profile=context.project.style_profile,
        custom_audit_rules=context.project.custom_audit_rules,
        custom_characters=context.current_chapter.custom_characters,
        phase=phase,
        phase_inputs=inputs,
        extra_payload=connection.extra_payload,
        extra_headers=connection.extra_headers,
        model_prompts=connection.model_prompts,
        merge_system_messages=connection.merge_system_messages,
        proxy=connection.proxy,
        prefer_non_stream=prefer_non_stream,
    )
