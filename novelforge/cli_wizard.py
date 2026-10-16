"""复用正式命令的终端任务向导，不另建业务和配置路径。"""

from __future__ import annotations

import argparse
import sys


def ask(label: str, default: str = "", *, optional: bool = False) -> str:
    """读取单项输入；EOF 表示退出整个向导。"""
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{label}{suffix}: ").strip() or default
        if value or optional:
            return value
        print("这一项不能为空。")


def choice(label: str, values: tuple[str, ...], default: str = "") -> str:
    while True:
        value = ask(label, default)
        if value in values:
            return value
        print("请选择：" + " / ".join(values))


async def execute(args: argparse.Namespace) -> None:
    from novelforge.cli import _execute, build_parser
    from novelforge.cli_projects import storage_session
    from novelforge.services.headless import read_config

    if not sys.stdin.isatty():
        raise ValueError("wizard 需要交互终端；脚本请使用 guide extract 或具体子命令")
    parser = build_parser()

    async def run(command: list[str]) -> None:
        if args.config:
            command += ["--config", str(args.config)]
        for option in ("error_format", "diagnostics_dir"):
            value = getattr(args, option, None)
            if value:
                command += ["--" + option.replace("_", "-"), str(value)]
        if getattr(args, "debug", False):
            command.append("--debug")
        await _execute(parser.parse_args(command))

    def select_project() -> str:
        with storage_session(read_config(args.config)) as storage:
            projects = storage.list_projects()
        if not projects:
            raise ValueError("尚无项目，请先选 2 导入 TXT 文章")
        for i, project in enumerate(projects, 1):
            print(f"  {i}. {project.name}（{project.id}）")
        index = choice("项目编号", tuple(str(i) for i in range(1, len(projects) + 1)))
        return projects[int(index) - 1].id

    print("赓笔任务向导。先配置模型，再导入文章、提取资料和续写。")
    print("提取、续写、审稿、修订和整卷任务会调用已配置的模型，使用服务商余额。")
    print("输入 0 退出；Ctrl+C 中断。本文本菜单与常规子命令共用配置和项目。")
    while True:
        try:
            print(
                "\n1 配置模型和密钥   2 导入 TXT 文章   3 提取世界书/档案\n"
                "4 续写并保存候选   5 审稿   6 按意见修订   7 整卷生成\n"
                "8 世界书管理   9 项目与候选查询   10 多阶段写作流程   0 退出"
            )
            selected = choice("选择任务", tuple(str(i) for i in range(11)), "0")
            if selected == "0":
                return
            if selected == "1":
                await run(
                    ["config", "setup", "--id", ask("端点 ID", "default"), "--default"]
                )
                print("配置已保存。要验证连接，可退出后运行 config test。")
            elif selected == "2":
                await run(
                    [
                        "projects",
                        "new",
                        "--name",
                        ask("作品名称"),
                        "-i",
                        ask("TXT 文件路径（无需加引号）"),
                        "--encoding",
                        ask("文本编码", "utf-8-sig"),
                    ]
                )
                print("已导入。选 3 提取世界书/角色/文风，或选 4 直接续写。")
            elif selected == "3":
                from novelforge.cli_extract import EXTRACT_FLOWS

                project = select_project()
                print(
                    "worldbook 世界书；ontology 世界观；protagonist 主角；character 指定角色；style 文风；context 章节上下文；rule 自定义约束。"
                )
                kind = choice("资料类型", tuple(EXTRACT_FLOWS), "worldbook")
                command = ["extract", kind, "--project", project]
                if kind == "character":
                    command += ["--character", ask("角色名")]
                elif kind == "rule":
                    command += ["--prompt-file", ask("自定义约束 TXT 路径")]
                output = ask("可选 JSON 导出路径（留空只保存到项目）", optional=True)
                if output:
                    command += ["-o", output]
                await run(command)
                print(
                    "提取完成，请校对结果。世界书需选 8 保存默认选择，或续写时指定其 ID。"
                )
            elif selected == "4":
                command = ["continue", "--project", select_project(), "--save"]
                command += ["--prompt", ask("续写要求（可以留空）", optional=True)]
                level = choice(
                    "破限等级",
                    ("inherit", "off", "low", "mid", "high", "cot"),
                    "inherit",
                )
                if level != "inherit":
                    command += ["--jailbreak", level]
                book = ask("本次世界书 ID（留空沿用默认选择）", optional=True)
                if book:
                    command += ["--worldbook", book]
                await run(command)
                print("已保存候选。选 9 查询；用 versions accept 接受成章。")
            elif selected in {"5", "6"}:
                command = [
                    "audit" if selected == "5" else "revise",
                    "--project",
                    select_project(),
                ]
                version = ask("候选 ID（留空处理当前章节正文）", optional=True)
                if version:
                    command += ["--version", version]
                if selected == "6":
                    command += ["--report", ask("审稿意见 TXT 路径")]
                command += ["-o", ask("输出 TXT 路径")]
                await run(command)
                print("审稿报告可编辑后选 6 修订；修订候选可用 versions replace 采纳。")
            elif selected == "7":
                project = select_project()
                count = choice("本卷章节数", tuple(str(i) for i in range(2, 21)), "5")
                await run(
                    [
                        "volume",
                        "start",
                        "--project",
                        project,
                        "--chapters",
                        count,
                        "--prompt",
                        ask("本卷要求（可以留空）", optional=True),
                    ]
                )
            elif selected == "8":
                action = choice(
                    "世界书操作", ("list", "import", "new", "select"), "list"
                )
                command = ["worldbooks", action]
                if action == "import":
                    command += ["-i", ask("SillyTavern 世界书 JSON 路径")]
                elif action == "new":
                    command += ["--name", ask("世界书名称")]
                elif action == "select":
                    await run(["worldbooks", "list"])
                    command += ask(
                        "默认世界书 ID，空格分隔；留空清空选择", optional=True
                    ).split()
                await run(command)
            elif selected == "9":
                project = select_project()
                await run(["chapters", "list", "--project", project])
                chapter = ask("查看该章候选的章节 ID（留空返回菜单）", optional=True)
                if chapter:
                    await run(["versions", "list", "--chapter", chapter])
            else:
                from novelforge.services.flow_plugin_service import FlowPluginService

                project = select_project()
                plugins = FlowPluginService(
                    read_config(args.config).get_storage_path()
                ).list_plugins()
                for plugin in plugins:
                    print(f"{plugin.id}：{plugin.name}，{plugin.description}")
                identifier = choice(
                    "流程 ID", tuple(plugin.id for plugin in plugins), "planned_writing"
                )
                await run(
                    [
                        "flows",
                        "run",
                        identifier,
                        "--project",
                        project,
                        "--prompt",
                        ask("写作要求（可以留空）", optional=True),
                    ]
                )
        except EOFError:
            print("\n已退出向导。")
            return
        except Exception as error:  # noqa: BLE001 -- 与 CLI 入口共用诊断，失败后回到菜单
            from novelforge.cli_diagnostics import report_error

            report_error(error)
            print("返回任务菜单；已保存的项目仍可使用。", file=sys.stderr)
