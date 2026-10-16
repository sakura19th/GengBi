"""项目、章节、候选版本和历史的命令行入口，复用桌面端存储服务。"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from novelforge.cli_workspace import add_output, check_output, emit, require


def add_commands(subparsers: Any, common: argparse.ArgumentParser) -> None:
    projects = subparsers.add_parser(
        "projects", help="导入文章、管理项目档案和导出作品"
    )
    actions = projects.add_subparsers(dest="action", required=True)
    actions.add_parser("list", parents=[common], help="列出项目 ID、名称和章节数")
    new = actions.add_parser("new", parents=[common], help="新建项目，可直接导入 TXT")
    new.add_argument("--name", required=True)
    new.add_argument("-i", "--input", type=Path)
    new.add_argument("--encoding", default="utf-8-sig")
    backup = actions.add_parser(
        "backup",
        parents=[common],
        help="导出原生项目 ZIP，含章节、候选、档案和引用资源",
    )
    backup.add_argument("id")
    add_output(backup)
    restore = actions.add_parser(
        "restore", parents=[common], help="从原生项目 ZIP 创建新项目，保留原项目"
    )
    restore.add_argument("-i", "--input", type=Path, required=True)
    for action in ("show", "update", "export", "delete"):
        parser = actions.add_parser(
            action,
            parents=[common],
            help={
                "show": "查看项目档案 JSON",
                "update": "编辑项目档案字段",
                "export": "导出项目正文为 TXT/Markdown",
                "delete": "删除项目及所有章节",
            }[action],
        )
        parser.add_argument("id", help="项目 ID")
        if action == "update":
            parser.add_argument(
                "-i",
                "--input",
                type=Path,
                required=True,
                help="JSON 对象，可含 name/novel_profile/preset_id/extract_config 等字段",
            )
        elif action == "export":
            add_output(parser)
            parser.add_argument("--format", choices=["txt", "md"], default="txt")
        elif action == "delete":
            parser.add_argument("--yes", action="store_true", required=True)

    chapters = subparsers.add_parser(
        "chapters", help="读取、编辑、拆分、合并和排序章节"
    )
    actions = chapters.add_subparsers(dest="action", required=True)
    for action in ("list", "add", "reorder"):
        parser = actions.add_parser(
            action,
            parents=[common],
            help={
                "list": "列出项目章节 ID",
                "add": "添加一个章节",
                "reorder": "按提供的完整 ID 顺序重排章节",
            }[action],
        )
        parser.add_argument("--project", required=True)
        if action == "add":
            parser.add_argument("-i", "--input", type=Path, required=True)
            parser.add_argument("--title", required=True)
            parser.add_argument("--encoding", default="utf-8-sig")
        elif action == "reorder":
            parser.add_argument("ids", nargs="+", help="必须包含项目的每个章节且不重复")
    for action in ("show", "edit", "metadata", "rename", "split", "merge", "delete"):
        parser = actions.add_parser(
            action,
            parents=[common],
            help={
                "show": "查看正文或 JSON",
                "edit": "用文件替换章节正文",
                "metadata": "用 JSON 编辑章节备注、标签等元数据",
                "rename": "修改标题，不触碰正文",
                "split": "按字符偏移拆分章节",
                "merge": "与下一章合并",
                "delete": "删除章节和对应候选",
            }[action],
        )
        parser.add_argument("id", help="章节 ID")
        if action == "show":
            parser.add_argument("--json", action="store_true")
        if action in {"edit", "metadata", "merge", "delete"}:
            parser.add_argument("--yes", action="store_true", required=True)
        if action in {"edit", "metadata"}:
            parser.add_argument("-i", "--input", type=Path, required=True)
        if action == "rename":
            parser.add_argument("--title", required=True)
        if action == "split":
            parser.add_argument("--position", type=int, required=True)

    versions = subparsers.add_parser(
        "versions", help="查看续写候选、接受为章节或替换正文"
    )
    actions = versions.add_subparsers(dest="action", required=True)
    for action in ("list", "show", "edit", "accept", "replace", "delete"):
        parser = actions.add_parser(action, parents=[common])
        parser.add_argument("--chapter", required=True)
        if action != "list":
            parser.add_argument("id", help="候选版本 ID")
        if action in {"edit", "replace", "delete"}:
            parser.add_argument("--yes", action="store_true", required=True)
        if action == "edit":
            parser.add_argument(
                "--content-file",
                type=Path,
                help="替换候选正文，保留 API 原文并标记为人工完成；卷候选需按章节产物编辑",
            )
            parser.add_argument(
                "--metadata-file",
                type=Path,
                help="JSON，可含 generated_title/highlights/parent_id",
            )

    history = subparsers.add_parser("history", help="查看或删除本地生成历史")
    actions = history.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", parents=[common])
    listing.add_argument("--project")
    listing.add_argument("--chapter")
    listing.add_argument("--limit", type=int, default=100)
    for action in ("show", "delete", "clear"):
        parser = actions.add_parser(action, parents=[common])
        if action == "clear":
            parser.add_argument("--project", required=True, help="只清理指定项目的历史")
        else:
            parser.add_argument("id")
        if action != "show":
            parser.add_argument("--yes", action="store_true", required=True)


@contextmanager
def storage_session(config: Any):
    """任何成功、输入错误或取消路径均关闭数据库。"""
    from novelforge.services.storage_service import StorageService

    service = StorageService(config.get_storage_path())
    try:
        yield service
    finally:
        from novelforge.cli_diagnostics import preserve_failure
        with preserve_failure():
            service.shutdown()


def load_project_chapters(service: Any, project_id: str, chapter_id: str | None = None):
    """加载正文并限制范围到选中章，防止从中间续写时注入后续情节。"""
    from novelforge.utils.ids import validate_id

    validate_id(project_id)
    project = require(service.load_project(project_id), "项目")
    chapters = sorted(
        service.load_chapter_contents(service.list_chapters(project_id)),
        key=lambda c: c.index,
    )
    if not chapters:
        raise ValueError("项目没有章节，请使用 chapters add 添加正文")
    current = (
        chapters[-1]
        if chapter_id is None
        else require(
            next((c for c in chapters if c.id == chapter_id), None), "项目中的章节"
        )
    )
    return project, [c for c in chapters if c.index <= current.index], current


async def on_storage_loop(storage: Any, coroutine):
    """在数据库所属循环执行异步业务，取消时同步取消后台任务。"""
    future = asyncio.run_coroutine_threadsafe(coroutine, storage._runner._loop)
    try:
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        future.cancel()
        raise


async def load_writing_context(storage: Any, config: Any, args: argparse.Namespace):
    """读取当前章资料及按需读取回溯资料，不调用模型。"""
    from novelforge.services.context_extractor import ContextExtractor
    from novelforge.services.headless import WritingContext

    project, chapters, current = load_project_chapters(
        storage, args.project, args.chapter
    )
    extractor = ContextExtractor(storage, config)
    cached = await on_storage_loop(
        storage, extractor.load_cached_entries(project.id, current.id)
    )
    context = WritingContext(
        project, chapters, current, (cached or {}).get("entries", [])
    )
    lookback = (
        args.lookback
        if args.lookback is not None
        else config.get_continuation_settings().get("default_lookback_chapters", 5)
    )
    window = chapters[-lookback:] if lookback else chapters
    previous = [c for c in window if c.id != current.id]
    defaults = config.get_continuation_settings()
    use_context = (
        args.lookback_context
        if args.lookback_context is not None
        else defaults.get("inject_lookback_context", False)
    )
    use_protagonist = (
        args.lookback_characters
        if args.lookback_characters is not None
        else defaults.get("inject_lookback_protagonist", False)
    )
    use_characters = (
        args.lookback_characters
        if args.lookback_characters is not None
        else defaults.get("inject_lookback_custom_characters", False)
    )
    if use_context:
        base = await on_storage_loop(
            storage,
            extractor.find_latest_context_base(
                project.id, [*previous, current], current
            ),
        )
        if base:
            context.lookback.update(
                lookback_context_entries=base[1],
                lookback_context_source_index=base[0].index,
            )
    if use_protagonist:
        base = extractor.find_protagonist_base([*previous, current], current)
        if base:
            context.lookback.update(
                lookback_protagonist_profile=base[1],
                lookback_protagonist_source_index=base[0].index,
            )
    if use_characters:
        base_chapter = next(
            (c for c in reversed(previous) if c.custom_characters), None
        )
        if base_chapter:
            context.lookback.update(
                lookback_custom_characters=base_chapter.custom_characters,
                lookback_custom_characters_source_index=base_chapter.index,
            )
    return context


def save_candidate(
    storage: Any,
    context: Any,
    prepared: Any,
    content: str,
    model: str,
    created_by: str = "continuation",
):
    """保存与桌面端一致的候选和历史快照。"""
    import re

    from novelforge.models import Continuation
    from novelforge.services.history_service import HistoryService
    from novelforge.utils.ids import generate_id

    title = re.search(
        r"<novelforge_title>(.*?)</novelforge_title>", prepared.raw_content, re.DOTALL
    )
    version = Continuation(
        id=generate_id("cont_"),
        content=content,
        raw_content=prepared.raw_content,
        model=model,
        created_by=created_by,
        preset_id=prepared.preset.id,
        preset_snapshot=prepared.preset.model_dump(mode="json"),
        parameters_snapshot=prepared.parameters,
        prompt_snapshot=prepared.assembled.messages,
        extracted_context_snapshot=prepared.context_entries,
        generated_title=title.group(1).strip() if title else "",
    )
    from novelforge.cli_diagnostics import phase
    phase("保存候选")
    storage.save_continuation(version, context.current_chapter.id)
    history = HistoryService(storage)
    now = history.now_iso()
    history.log_continuation(
        project_id=context.project.id,
        chapter_id=context.current_chapter.id,
        swipe_id=version.id,
        started_at=now,
        finished_at=now,
        status="completed",
        model=model,
        parameters=prepared.parameters,
        prompt_messages=prepared.assembled.messages,
        output_text=content,
    )
    return version


def execute(args: argparse.Namespace, config: Any) -> None:
    """所有实体操作调用现有业务层，保留桌面端章节重排及版本语义。"""
    from novelforge.cli import _write_output
    from novelforge.models import Chapter, Project
    from novelforge.services.chapter_service import ChapterService
    from novelforge.services.headless import read_json
    from novelforge.services.importer import DEFAULT_CHAPTER_PATTERN, TxtImporter
    from novelforge.utils.ids import generate_id, validate_id

    for name in ("id", "project", "chapter"):
        value = getattr(args, name, None)
        if value is not None:
            validate_id(value)
    with storage_session(config) as storage:
        chapters_service = ChapterService(storage)
        action = args.action
        if args.command == "projects":
            if action in {"backup", "restore"}:
                from tempfile import TemporaryDirectory

                from novelforge.services.exporter import (
                    export_project_backup,
                    import_project_backup,
                )
                from novelforge.services.preset_service import PresetService
                from novelforge.services.regex_service import RegexService

                presets = PresetService(config.get_storage_path())
                regex = RegexService(config.get_storage_path())
                if action == "restore":
                    import zipfile

                    try:
                        identifier = import_project_backup(
                            storage, presets, regex, args.input
                        )
                    except zipfile.BadZipFile:
                        raise ValueError("备份不是有效的 ZIP 文件") from None
                    emit(
                        {
                            "project": identifier,
                            "next": f"chapters list --project {identifier}",
                        }
                    )
                else:
                    require(storage.load_project(args.id), "项目")
                    check_output(
                        args.output,
                        args.force,
                        config.config_path,
                        *[
                            storage.get_chapter_file_path(args.id, c.id)
                            for c in storage.list_chapters(args.id)
                        ],
                    )
                    with TemporaryDirectory(prefix="gengbi-backup-") as directory:
                        temporary = Path(directory) / "project.zip"
                        export_project_backup(
                            storage, presets, regex, args.id, temporary
                        )
                        with args.output.open("wb" if args.force else "xb") as target:
                            target.write(temporary.read_bytes())
                    emit({"output": str(args.output)})
                return
            if action == "list":
                emit(
                    [
                        {
                            "id": p.id,
                            "name": p.name,
                            "chapters": len(storage.list_chapters(p.id)),
                        }
                        for p in storage.list_projects()
                    ]
                )
                return
            if action == "new":
                text = (
                    args.input.read_text(encoding=args.encoding) if args.input else None
                )
                if text is not None and not text.strip():
                    raise ValueError("输入文章为空")
                # 在创建项目前解析，避免无效输入留下空项目。
                parsed = (
                    TxtImporter.split_text(
                        text, "pending", DEFAULT_CHAPTER_PATTERN, False, args.name
                    )
                    if text
                    else []
                )
                project = storage.create_project(
                    name=args.name,
                    source_file=str(args.input or ""),
                    novel_profile={"title": args.name},
                )
                for chapter in parsed:
                    chapter.project_id = project.id
                    storage.save_chapter(chapter)
                emit(
                    {
                        "id": project.id,
                        "name": project.name,
                        "chapters": len(parsed),
                        "next": f"chapters list --project {project.id}",
                    }
                )
                return
            project = require(storage.load_project(args.id), "项目")
            if action == "show":
                emit(project.model_dump(mode="json"))
            elif action == "delete":
                storage.delete_project(project.id)
                emit({"deleted": project.id})
            elif action == "update":
                changes = read_json(args.input)
                allowed = {
                    "name",
                    "novel_profile",
                    "preset_id",
                    "regex_script_ids",
                    "extract_config",
                    "worldbook_id",
                    "world_ontology",
                    "style_profile",
                    "custom_audit_rules",
                }
                if not isinstance(changes, dict) or set(changes) - allowed:
                    raise ValueError(
                        "项目更新包含不支持的字段；可编辑 name/novel_profile/preset_id/regex_script_ids/extract_config/worldbook_id/world_ontology/style_profile/custom_audit_rules"
                    )
                project = Project.model_validate(
                    project.model_dump(mode="json") | changes
                )
                for identifier in [project.preset_id, project.worldbook_id]:
                    if identifier:
                        validate_id(identifier)
                storage.save_project(project)
                emit(project.model_dump(mode="json"))
            elif action == "export":
                chapters = storage.load_chapter_contents(
                    storage.list_chapters(project.id)
                )
                sources = [
                    storage.get_chapter_file_path(project.id, c.id) for c in chapters
                ]
                check_output(args.output, args.force, config.config_path, *sources)
                prefix = "# " if args.format == "md" else ""
                text = "\n\n".join(
                    f"{prefix}{c.title}\n\n{c.content}"
                    for c in sorted(chapters, key=lambda c: c.index)
                )
                _write_output(text, args.output, args.force)
                emit({"output": str(args.output), "chapters": len(chapters)})
        elif args.command == "chapters":
            if action in {"list", "add", "reorder"}:
                require(storage.load_project(args.project), "项目")
                chapters = storage.list_chapters(args.project)
                if action == "list":
                    emit(
                        [
                            {
                                "id": c.id,
                                "number": c.index + 1,
                                "title": c.title,
                                "word_count": c.word_count,
                            }
                            for c in chapters
                        ]
                    )
                elif action == "add":
                    content = args.input.read_text(encoding=args.encoding)
                    if not content.strip():
                        raise ValueError("章节正文为空")
                    chapter = Chapter(
                        id=generate_id("ch_"),
                        project_id=args.project,
                        index=max((c.index for c in chapters), default=-1) + 1,
                        title=args.title,
                        content=content,
                    )
                    storage.save_chapter(chapter)
                    emit(chapter.model_dump(mode="json"))
                else:
                    if len(args.ids) != len(set(args.ids)) or set(args.ids) != {
                        c.id for c in chapters
                    }:
                        raise ValueError("排序必须完整包含本项目的所有章节且不能重复")
                    storage.reorder_chapters(args.project, args.ids)
                    emit({"reordered": args.ids})
                return
            chapter = require(storage.load_chapter(args.id), "章节")
            if action == "show":
                if args.json:
                    emit(chapter.model_dump(mode="json"))
                else:
                    print(chapter.content)
            elif action == "edit":
                chapter.content = args.input.read_text(encoding="utf-8-sig")
                if not chapter.content.strip():
                    raise ValueError("章节正文为空")
                storage.save_chapter(chapter)
                emit({"saved": chapter.id})
            elif action == "metadata":
                metadata = read_json(args.input)
                if not isinstance(metadata, dict):
                    raise TypeError("章节元数据必须是 JSON 对象")
                chapter.metadata = metadata
                storage.save_chapter(chapter)
                emit({"saved": chapter.id})
            elif action == "rename":
                chapters_service.rename_chapter(chapter, args.title)
                emit({"renamed": chapter.id})
            elif action == "split":
                if not 0 < args.position < len(chapter.content):
                    raise ValueError("拆分位置必须在正文内部，单位为字符")
                chapters_service.split_chapter(chapter, args.position)
                emit(
                    {
                        "split": chapter.id,
                        "next": f"chapters list --project {chapter.project_id}",
                    }
                )
            elif action == "merge":
                if not any(
                    c.index > chapter.index
                    for c in storage.list_chapters(chapter.project_id)
                ):
                    raise ValueError("该章之后没有可合并的章节")
                chapters_service.merge_chapter_with_next(chapter)
                emit({"merged": chapter.id})
            else:
                chapters_service.delete_chapter(chapter)
                emit({"deleted": chapter.id})
        elif args.command == "versions":
            chapter = require(storage.load_chapter(args.chapter), "章节")
            if action == "list":
                emit(
                    [
                        {
                            "id": c.id,
                            "model": c.model,
                            "created_at": c.created_at,
                            "status": c.status,
                        }
                        for c in chapter.continuations
                    ]
                )
                return
            version = require(
                next((c for c in chapter.continuations if c.id == args.id), None),
                "章节候选",
            )
            if action == "show":
                emit(version.model_dump(mode="json"))
            elif action == "edit":
                from novelforge.models import Continuation

                changes = read_json(args.metadata_file) if args.metadata_file else {}
                if not isinstance(changes, dict) or set(changes) - {
                    "generated_title",
                    "highlights",
                    "parent_id",
                }:
                    raise ValueError(
                        "候选元数据只允许 generated_title/highlights/parent_id"
                    )
                if not args.content_file and not args.metadata_file:
                    raise ValueError("请提供 --content-file 或 --metadata-file")
                if args.content_file:
                    if version.volume_artifacts:
                        raise ValueError(
                            "卷候选包含多章产物，请先接受成章后使用 chapters edit 分章编辑"
                        )
                    text = args.content_file.read_text(encoding="utf-8-sig")
                    if not text.strip():
                        raise ValueError("候选正文不能为空")
                    changes.update(content=text, status="completed")
                if changes.get("parent_id") and (
                    changes["parent_id"] == version.id
                    or not any(
                        v.id == changes["parent_id"] for v in chapter.continuations
                    )
                ):
                    raise ValueError("parent_id 必须指向同章节的其他候选")
                parents = {v.id: v.parent_id for v in chapter.continuations}
                parent = changes.get("parent_id")
                seen = {version.id}
                while parent:
                    if parent in seen:
                        raise ValueError("候选引用不能形成循环")
                    seen.add(parent)
                    parent = parents.get(parent)
                version = Continuation.model_validate(
                    version.model_dump(mode="json") | changes
                )
                storage.save_continuation(version, chapter.id)
                emit({"saved": version.id})
            elif action == "accept":
                if version.status != "completed":
                    raise ValueError("候选未完整生成，请先重试或人工整理后再接受")
                if (
                    version.volume_artifacts
                    and version.volume_artifacts.chapter_artifacts
                ):
                    artifacts = version.volume_artifacts.chapter_artifacts
                    for later in storage.list_chapters(chapter.project_id):
                        if later.index > chapter.index:
                            storage.update_chapter_index(
                                later.id, later.index + len(artifacts)
                            )
                    added = []
                    plan = version.volume_artifacts.final_outline
                    for offset, artifact in enumerate(artifacts, 1):
                        title = (
                            plan.chapters[artifact.chapter_index].title
                            if plan and artifact.chapter_index < len(plan.chapters)
                            else f"第{chapter.index + offset + 1}章"
                        )
                        added_chapter = Chapter(
                            id=generate_id("ch_"),
                            project_id=chapter.project_id,
                            index=chapter.index + offset,
                            title=title,
                            content=artifact.content,
                        )
                        storage.save_chapter(added_chapter)
                        added.append(added_chapter.id)
                    storage.delete_continuation(version.id)
                    emit({"chapters": added})
                    return
                _, new_chapter = chapters_service.promote_continuation_to_chapter(
                    chapter, version
                )
                emit({"chapter": new_chapter.id, "title": new_chapter.title})
            elif action == "replace":
                if version.status != "completed":
                    raise ValueError("候选未完整生成，请先重试或编辑后再替换")
                chapters_service.replace_chapter_content(chapter, version)
                emit({"replaced": chapter.id})
            else:
                chapters_service.delete_continuation(chapter, version)
                emit({"deleted": version.id})
        else:
            from novelforge.services.history_service import HistoryService

            history = HistoryService(storage)
            if action == "list":
                if args.limit <= 0:
                    raise ValueError("历史条数必须大于 0")
                emit(
                    history.list_history(
                        project_id=args.project,
                        chapter_id=args.chapter,
                        limit=args.limit,
                    )
                )
            elif action == "show":
                emit(require(history.get_history_detail(args.id), "历史记录"))
            elif action == "delete":
                require(history.get_history_detail(args.id), "历史记录")
                history.delete_history(args.id)
                emit({"deleted": args.id})
            else:
                require(storage.load_project(args.project), "项目")
                history.clear_history(args.project)
                emit({"cleared_project": args.project})
