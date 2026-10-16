"""写作预设、正则脚本与流程定义的 CLI 管理。"""

from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from novelforge.cli_workspace import add_output, check_output, emit, require


def add_commands(subparsers: Any, common: argparse.ArgumentParser) -> None:
    for kind, label in [
        ("preset", "写作预设及提示模块"),
        ("regex", "全局/预设/项目正则脚本"),
        ("flows", "内置与自定义写作流程"),
    ]:
        parser = subparsers.add_parser(kind, help="管理" + label)
        actions = parser.add_subparsers(dest="action", required=True)
        if kind == "regex":
            test = actions.add_parser(
                "test", parents=[common], help="在文本上测试一个正则脚本，不改文件"
            )
            test.add_argument("id")
            test.add_argument("--text-file", type=Path, required=True)
            test.add_argument(
                "--scope", choices=["global", "preset", "scoped"], default="global"
            )
            test.add_argument("--project", default="")
            test.add_argument("--preset", default="")
        if kind == "flows":
            from novelforge.cli_flows import add_commands as add_execution_commands

            add_execution_commands(actions, common)
        for action in (
            "list",
            "show",
            "new",
            "save",
            "import",
            "export",
            "delete",
            "toggle",
        ):
            if kind == "flows" and action in {"new", "toggle"}:
                continue
            child = actions.add_parser(
                action,
                parents=[common],
                help={
                    "list": "列出资源 ID",
                    "show": "显示原生 JSON，供导出编辑",
                    "new": "新建资源",
                    "save": "保存编辑后的原生 JSON",
                    "import": "导入分享文件",
                    "export": "导出分享文件",
                    "delete": "删除指定资源",
                    "toggle": "启用或禁用",
                }[action],
            )
            if action in {"show", "delete", "toggle"} or (
                action == "export" and kind != "regex"
            ):
                child.add_argument("id")
            if action in {"import", "save"}:
                child.add_argument("-i", "--input", type=Path, required=True)
                child.add_argument(
                    "--force", action="store_true", help="允许覆盖已有同 ID 资源"
                )
            if action == "export":
                add_output(child)
            if action == "new":
                child.add_argument("--name", required=True)
                if kind == "regex":
                    child.add_argument(
                        "--find", required=True, help="查找正则，例如 /foo/g"
                    )
                    child.add_argument("--replace", default="")
                    child.add_argument(
                        "--placement",
                        choices=[1, 2, 5],
                        type=int,
                        action="append",
                        help="1 用户输入，2 AI 输出，5 世界书，可重复",
                    )
            if action == "toggle":
                child.add_argument("state", choices=["on", "off"])
            if action == "delete":
                child.add_argument("--yes", action="store_true", required=True)
            if kind == "regex":
                child.add_argument(
                    "--scope", choices=["global", "preset", "scoped"], default="global"
                )
                child.add_argument("--project", default="", help="scoped 作用域必填")
                child.add_argument("--preset", default="", help="preset 作用域必填")
        if kind == "preset":
            toggle = actions.add_parser(
                "prompt-toggle", parents=[common], help="单独切换预设中的提示模块"
            )
            toggle.add_argument("id")
            toggle.add_argument("identifier")
            toggle.add_argument("state", choices=["on", "off"])


def execute(args: argparse.Namespace, config: Any) -> None:
    from novelforge.cli import _write_output
    from novelforge.models import FlowPlugin, WritingPreset
    from novelforge.models.regex import RegexScript
    from novelforge.services.flow_plugin_service import FlowPluginService
    from novelforge.services.headless import read_json
    from novelforge.services.preset_service import PresetService
    from novelforge.services.regex_service import RegexService
    from novelforge.utils.ids import validate_id

    for key in ("id", "project", "preset"):
        value = getattr(args, key, "")
        if value:
            validate_id(value)
    kind, action = args.command, args.action
    if kind == "regex":
        service = RegexService(config.get_storage_path())
        scope = {
            "scope": args.scope,
            "project_id": args.project,
            "preset_id": args.preset,
        }
        existing = service.load_scripts(**scope)
        if action == "list":
            emit([s.model_dump(mode="json", by_alias=True) for s in existing])
            return
        if action == "new":
            value = service.create_script(
                args.name, args.find, args.replace, args.placement or [2], **scope
            )
        elif action in {"save", "import"}:
            if action == "import":
                # 导入接口生成独立脚本 ID；编辑已有脚本使用 save。
                values = service.import_from_st_json(args.input, **scope)
            else:
                data = read_json(args.input)
                values = [
                    RegexScript.model_validate(item)
                    for item in (data if isinstance(data, list) else [data])
                ]
                if len({v.id for v in values}) != len(values):
                    raise ValueError("脚本 ID 重复")
                current = {s.id: s for s in existing}
                if set(current) & {s.id for s in values} and not args.force:
                    raise ValueError("脚本已存在；覆盖需要 --force")
                current.update({s.id: s for s in values})
                service.save_scripts(list(current.values()), **scope)
            emit([s.model_dump(mode="json", by_alias=True) for s in values])
            return
        elif action == "export":
            source = service._get_scope_file_path(**scope)
            check_output(args.output, args.force, config.config_path, source)
            with TemporaryDirectory(prefix="gengbi-regex-") as directory:
                path = Path(directory) / "regex.json"
                service.export_to_st_json(existing, path)
                _write_output(path.read_text(encoding="utf-8"), args.output, args.force)
            emit({"output": str(args.output)})
            return
        else:
            value = require(
                next((s for s in existing if s.id == args.id), None), "正则脚本"
            )
            if action == "test":
                from novelforge.core.regex_engine import RegexEngine

                engine = RegexEngine()
                engine.compile_scripts([value])
                if engine.failed_script_ids:
                    raise ValueError("正则表达式无效，请先编辑脚本")
                output, matches = engine.apply_single_script(
                    value, args.text_file.read_text(encoding="utf-8-sig")
                )
                emit({"text": output, "matches": matches})
                return
            if action == "delete":
                if not service.delete_script(args.id, **scope):
                    raise ValueError("正则脚本删除失败或已不存在")
                emit({"deleted": args.id})
                return
            if action == "toggle":
                value.disabled = args.state == "off"
                if not service.update_script(value, **scope):
                    raise ValueError("正则脚本更新失败或已不存在")
        emit(value.model_dump(mode="json", by_alias=True))
        return

    preset = kind == "preset"
    service = (
        PresetService(config.get_storage_path())
        if preset
        else FlowPluginService(config.get_storage_path())
    )
    if preset:
        service.ensure_default_preset_exists()
    load = service.load_preset if preset else service.load_plugin
    save = service.save_preset if preset else service.save_plugin
    if action == "list":
        values = service.list_presets() if preset else service.list_plugins()
        emit(
            [
                v.model_dump(mode="json", by_alias=True)
                if hasattr(v, "model_dump")
                else v
                for v in values
            ]
        )
        return
    if action == "new":
        value = service.create_preset(args.name)
    elif action == "save":
        cls = WritingPreset if preset else FlowPlugin
        value = cls.model_validate(read_json(args.input))
        validate_id(value.id)
        current = load(value.id)
        if current and not args.force:
            raise ValueError("资源已存在；覆盖需要 --force")
        if not preset:
            value.builtin = current.builtin if current else False
        save(value)
    elif action == "import":
        if preset:
            value, embedded = service.import_from_st_json(args.input)
            if embedded:
                RegexService(config.get_storage_path()).save_scripts(
                    [RegexScript.model_validate(s) for s in embedded],
                    "preset",
                    preset_id=value.id,
                )
        else:
            imported = FlowPlugin.model_validate(read_json(args.input))
            existing = load(imported.id)
            if existing and existing.builtin and args.force:
                raise ValueError("不能用导入文件覆盖内置流程；请去掉 --force 生成副本")
            if existing and not args.force:
                base = imported.id + "_imported"
                identifier = base
                count = 2
                while load(identifier):
                    identifier = f"{base}_{count}"
                    count += 1
                imported.id = identifier
            imported.builtin = False
            save(imported)
            value = imported
    else:
        value = require(load(args.id), "预设" if preset else "流程")
        if action == "export":
            source = (
                config.get_storage_path()
                / ("presets" if preset else "flow_plugins")
                / f"{args.id}.json"
            )
            check_output(args.output, args.force, config.config_path, source)
            with TemporaryDirectory(prefix="gengbi-asset-") as directory:
                path = Path(directory) / "asset.json"
                if preset:
                    service.export_to_st_json(value, path)
                else:
                    service.export_plugin(args.id, path)
                _write_output(path.read_text(encoding="utf-8"), args.output, args.force)
            emit({"output": str(args.output)})
            return
        if action == "delete":
            deleted = (
                service.delete_preset(args.id)
                if preset
                else service.delete_plugin(args.id)
            )
            if not deleted:
                raise ValueError("资源受内置保护或删除失败，请复制为自定义资源后编辑")
            emit({"deleted": args.id})
            return
        if action == "toggle":
            if not service.set_preset_enabled(args.id, args.state == "on"):
                raise ValueError("预设受内置保护或不存在，无法更改启用状态")
            value = load(args.id)
        elif action == "prompt-toggle":
            require(
                next(
                    (p for p in value.prompts if p.identifier == args.identifier), None
                ),
                "提示模块",
            )
            service.set_prompt_enabled(value, args.identifier, args.state == "on")
            service.save_preset(value)
    emit(value.model_dump(mode="json", by_alias=True))
