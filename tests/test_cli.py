"""以真实子进程和本地 HTTP/SSE 服务验证 CLI，可复用到打包产物。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STORY = "第一章 来信\n旧信被锁在抽屉里。\n第二章 雨夜\n她终于拆开信封。"
BODY = "门外的脚步停住了。\n\n她握紧信纸，没有出声。"


@pytest.fixture
def cli(tmp_path):
    """每次测试使用独立配置、用户目录和 API 环境变量。"""
    home = tmp_path / "用户目录"
    home.mkdir()
    storage = home / ".novelforge"
    storage.mkdir()
    config = storage / "config.json"
    config.write_text(
        json.dumps({"data": {"storage_path": str(storage)}}), encoding="utf-8"
    )
    story = tmp_path / "前文 空格.txt"
    story.write_text(STORY, encoding="utf-8-sig")
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GENGBI_")
    }
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONIOENCODING="utf-8")
    executable = os.environ.get("GENGBI_TEST_EXECUTABLE")
    command = [executable] if executable else [sys.executable, "-m", "novelforge.cli"]

    def run(*args, stdin=None, extra_env=None):
        return subprocess.run(
            [*command, *map(str, args)],
            cwd=ROOT,
            env={**env, **(extra_env or {})},
            input=stdin,
            capture_output=True,
            check=False,
            encoding="utf-8",
            timeout=30,
        )

    run.story, run.config, run.storage, run.home = story, config, storage, home
    return run


@pytest.fixture
def api():
    """只替代远端模型，保留真实 socket、客户端解析、预设与文件输出。"""
    state = {"requests": [], "status": 200, "content": BODY, "finish": "stop"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(state.get("get_status", 200))
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Request-ID", "req-local-test-123")
            self.end_headers()
            self.wfile.write(state.get("get_body", '{"data":[{"id":"test-model"}]}').encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, body, dict(self.headers)))
            response_content = (
                state["responses"].pop(0)
                if state.get("responses")
                else state["content"]
            )
            self.send_response(state["status"])
            self.send_header("X-Request-ID", "req-local-test-123")
            if state["status"] == 429:
                self.send_header("Retry-After", "0")
            self.send_header(
                "Content-Type",
                "text/event-stream" if body["stream"] else "application/json",
            )
            self.end_headers()
            if "raw" in state:
                self.wfile.write(state["raw"].encode("utf-8"))
                return
            if state["status"] != 200:
                self.wfile.write(state.get("error_body", '{"error":{"message":"server echoed secret-key"}}').encode())
            elif body["stream"]:
                for char in response_content:
                    event = {"choices": [{"delta": {"content": char}}]}
                    data = (
                        "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                    ).encode()
                    # 在多字节字符中间写入，覆盖 SSE 增量 UTF-8 解码。
                    split = data.index(char.encode()) + 1
                    self.wfile.write(data[:split])
                    self.wfile.flush()
                    self.wfile.write(data[split:])
                if state["finish"] is not None:
                    event = {
                        "choices": [{"delta": {}, "finish_reason": state["finish"]}]
                    }
                    self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                result = {
                    "choices": [
                        {
                            "message": {"content": response_content},
                            "finish_reason": state["finish"],
                        }
                    ]
                }
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_help_and_version(cli):
    assert cli("--help").returncode == 0
    result = cli("--version")
    assert result.returncode == 0 and "0.2.22" in result.stdout
    assert "--lookback" in cli("continue", "--help").stdout


def test_dry_run_uses_default_preset_and_leaves_desktop_data_untouched(cli):
    before = {
        p.relative_to(cli.home): p.read_bytes()
        for p in cli.home.rglob("*")
        if p.is_file()
    }
    result = cli(
        "continue",
        "-i",
        cli.story,
        "--config",
        cli.config,
        "--lookback",
        "1",
        "--prompt",
        "不要重复前文",
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    messages = "\n".join(item["content"] for item in payload["messages"])
    assert "她终于拆开信封" in messages
    assert "旧信被锁在抽屉里" not in messages
    assert "不要重复前文" in messages
    assert "{{getvar::COT-items}}" not in messages
    assert payload["history_chapter_count"] == 1
    after = {
        p.relative_to(cli.home): p.read_bytes()
        for p in cli.home.rglob("*")
        if p.is_file()
    }
    assert before == after


@pytest.mark.parametrize("stream", ["--stream", "--no-stream"])
def test_real_http_continuation(cli, api, tmp_path, stream):
    api["content"] = (
        "<think>内部推理</think><novelforge_thinking>分析</novelforge_thinking><novelforge_title>回声</novelforge_title>"
        + BODY
    )
    output = tmp_path / "续写 结果.txt"
    result = cli(
        "continue",
        "-i",
        cli.story,
        "--config",
        cli.config,
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        stream,
        "--max-tokens",
        "1234",
        "--temperature",
        "0.3",
        "--target-words",
        "1000",
        "--lookback",
        "0",
        "-o",
        output,
        extra_env={"GENGBI_API_KEY": "secret-key"},
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").strip() == BODY
    assert result.stdout == ""
    path, request, headers = api["requests"][0]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == "Bearer secret-key"
    assert request["model"] == "test-model"
    assert request["max_tokens"] == 1234 and request["temperature"] == 0.3
    assert request["stream"] is (stream == "--stream")
    assert any(
        "旧信被锁在抽屉里" in message["content"] for message in request["messages"]
    )
    assert "secret-key" not in result.stdout + result.stderr


def test_utf8_stdin_and_stdout(cli, api):
    result = cli(
        "continue",
        "-i",
        "-",
        "--config",
        cli.config,
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        stdin=STORY,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == BODY


def test_legacy_encoding_and_prompt_file(cli, tmp_path):
    cli.story.write_bytes(STORY.encode("gb18030"))
    prompt = tmp_path / "指令.txt"
    prompt.write_text("保持悬念", encoding="utf-8-sig")
    result = cli(
        "continue",
        "-i",
        cli.story,
        "--encoding",
        "gb18030",
        "--prompt-file",
        prompt,
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    assert "保持悬念" in result.stdout


@pytest.mark.parametrize(
    "arguments",
    [
        ["--temperature", "nan"],
        ["--temperature", "3"],
        ["--lookback", "-1"],
        ["--target-words", "0"],
        ["--max-tokens", "0"],
        ["--timeout", "inf"],
        ["--max-context", "10", "--max-tokens", "10"],
        ["--preset", "../escape"],
    ],
)
def test_invalid_options_do_not_request_model(cli, api, arguments):
    result = cli(
        "continue",
        "-i",
        cli.story,
        "--config",
        cli.config,
        "--base-url",
        api["url"],
        "--model",
        "test",
        *arguments,
    )
    assert result.returncode == 2, result.stderr
    assert not api["requests"]
    assert "Traceback" not in result.stderr


def test_missing_config_and_empty_input(cli, tmp_path):
    assert cli("presets", "--config", tmp_path / "missing.json").returncode == 2
    result = cli("continue", "-i", "-", "--dry-run", stdin="  ")
    assert result.returncode == 2


def test_existing_output_and_input_are_protected(cli, api, tmp_path):
    output = tmp_path / "existing.txt"
    output.write_text("保留内容", encoding="utf-8")
    args = ["continue", "-i", cli.story, "--base-url", api["url"], "--model", "test"]
    assert cli(*args, "-o", output).returncode == 2
    assert cli(*args, "-o", cli.story, "--force").returncode == 2
    assert not api["requests"]
    assert output.read_text(encoding="utf-8") == "保留内容"
    assert cli(*args, "-o", output, "--force").returncode == 0
    assert output.read_text(encoding="utf-8").strip() == BODY


@pytest.mark.parametrize(
    "status,content,finish",
    [
        (401, BODY, "stop"),
        (500, BODY, "stop"),
        (200, "", "stop"),
        (200, BODY, "length"),
        (200, BODY, None),
    ],
)
def test_failures_preserve_output_and_hide_server_body(
    cli, api, tmp_path, status, content, finish
):
    api.update(status=status, content=content, finish=finish)
    output = tmp_path / "result.txt"
    output.write_text("原内容", encoding="utf-8")
    result = cli(
        "continue",
        "-i",
        cli.story,
        "--base-url",
        api["url"],
        "--model",
        "test",
        "-o",
        output,
        "--force",
    )
    assert result.returncode == 1, result.stderr
    assert "secret-key" not in result.stderr and "Traceback" not in result.stderr
    assert result.stdout == ""
    assert output.read_text(encoding="utf-8") == "原内容"


def test_endpoint_listing_and_selection(cli, api):
    cli.config.write_text(
        json.dumps(
            {
                "data": {"storage_path": str(cli.storage)},
                "default_endpoint_id": "one",
                "api_endpoints": [
                    {
                        "id": "one",
                        "name": "本地服务",
                        "base_url": api["url"],
                        "default_model": "configured",
                        "extra_headers": {"X-Test": "private-header"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = cli("endpoints", "--config", cli.config)
    assert result.returncode == 0 and "private-header" not in result.stdout
    assert json.loads(result.stdout)[0]["id"] == "one"
    result = cli(
        "continue", "-i", cli.story, "--config", cli.config, "--endpoint", "one"
    )
    assert result.returncode == 0, result.stderr
    assert api["requests"][0][1]["model"] == "configured"
    assert api["requests"][0][2]["X-Test"] == "private-header"
    # 切换地址后不能携带旧端点的私有 HTTP 头。
    result = cli(
        "continue",
        "-i",
        cli.story,
        "--config",
        cli.config,
        "--base-url",
        api["url"],
        "--model",
        "other",
    )
    assert result.returncode == 0, result.stderr
    assert "X-Test" not in api["requests"][-1][2]


def test_st_preset_file_and_environment_overrides(cli, api, tmp_path):
    preset = tmp_path / "ST 预设.json"
    preset.write_text(
        json.dumps(
            {
                "name": "测试预设",
                "temperature": 0.6,
                "openai_max_tokens": 777,
                "prompts": [
                    {
                        "identifier": "main",
                        "content": "续写 {{book}}，约 {{target_words}} 字。",
                        "role": "system",
                    },
                    {"identifier": "chatHistory", "marker": True},
                ],
                "prompt_order": [
                    {
                        "character_id": 100000,
                        "order": [
                            {"identifier": "main", "enabled": True},
                            {"identifier": "chatHistory", "enabled": True},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = cli(
        "continue",
        "-i",
        cli.story,
        "--config",
        cli.config,
        "--preset-file",
        preset,
        "--title",
        "雨夜",
        "--target-words",
        "800",
        extra_env={"GENGBI_BASE_URL": api["url"], "GENGBI_MODEL": "from-env"},
    )
    assert result.returncode == 0, result.stderr
    request = api["requests"][0][1]
    assert request["model"] == "from-env" and request["max_tokens"] == 777
    assert any(
        "续写 雨夜，约 800 字" in item["content"] for item in request["messages"]
    )
    assert not (cli.storage / "presets").exists()


def test_headless_path_does_not_import_qt(cli):
    if os.environ.get("GENGBI_TEST_EXECUTABLE"):
        pytest.skip("打包产物由构建排除 Qt；此测试专门验证源码导入")
    code = """
import importlib.abc, runpy, sys
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PySide', 'PyQt', 'novelforge.ui')):
            raise AssertionError('CLI imported GUI: ' + fullname)
sys.meta_path.insert(0, NoQt())
sys.argv = ['gengbi', 'continue', '-i', sys.argv[1], '--config', sys.argv[2], '--dry-run']
runpy.run_module('novelforge.cli', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(cli.story), str(cli.config)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["messages"]


def test_saved_encrypted_key_is_used_without_rewriting_config(cli, api, monkeypatch):
    from novelforge.core.config import ConfigManager

    monkeypatch.setenv("HOME", str(cli.home))
    monkeypatch.setenv("USERPROFILE", str(cli.home))
    manager = ConfigManager(cli.config)
    manager.load()
    manager.add_endpoint(
        {
            "id": "saved",
            "name": "已保存",
            "base_url": api["url"],
            "api_key": "encrypted-test-key",
            "default_model": "saved-model",
        }
    )
    before = cli.config.read_bytes()
    result = cli("continue", "-i", cli.story, "--config", cli.config)
    assert result.returncode == 0, result.stderr
    assert api["requests"][0][2]["Authorization"] == "Bearer encrypted-test-key"
    assert cli.config.read_bytes() == before
    assert "encrypted-test-key" not in result.stdout + result.stderr


def test_saved_preset_and_regex_toggle(cli, api):
    presets = cli.storage / "presets"
    presets.mkdir()
    from novelforge.utils.paths import get_default_preset_path

    data = json.loads(get_default_preset_path().read_text(encoding="utf-8"))
    data.update(id="custom", name="自定义预设")
    (presets / "custom.json").write_text(json.dumps(data), encoding="utf-8")
    assert {
        row["id"] for row in json.loads(cli("presets", "--config", cli.config).stdout)
    } == {"default", "custom"}
    api["content"] = "<novelforge_thinking>保留测试</novelforge_thinking>" + BODY
    result = cli(
        "continue",
        "-i",
        cli.story,
        "--config",
        cli.config,
        "--preset",
        "custom",
        "--base-url",
        api["url"],
        "--model",
        "test",
        "--no-regex",
    )
    assert result.returncode == 0, result.stderr
    assert "保留测试" in result.stdout


def test_keyboard_interrupt_returns_130(monkeypatch, capsys):
    if os.environ.get("GENGBI_TEST_EXECUTABLE"):
        pytest.skip("协程中断注入为源码专用检查")
    from novelforge import cli as module

    def interrupt(coroutine):
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(module.asyncio, "run", interrupt)
    assert module.main(["presets"]) == 130
    assert "已取消" in capsys.readouterr().err


@pytest.mark.parametrize("data", [{"api_endpoints": "broken"}, {"data": []}, []])
def test_invalid_config_has_controlled_error(cli, data):
    cli.config.write_text(json.dumps(data), encoding="utf-8")
    result = cli("endpoints", "--config", cli.config)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


@pytest.fixture
def jailbreak_preset(tmp_path):
    """用中性标记验证模块选择，故意让提示开关与排序开关冲突。"""
    identifiers = [
        f"nf_jb_{level}" for level in ("low", "mid", "high", "cot", "prefill")
    ]
    data = {
        "id": "test-jailbreak",
        "name": "模块选择测试",
        "generation_params": {},
        "prompts": [
            {
                "identifier": identifier,
                "content": f"CHECK_{identifier}",
                "enabled": False,
            }
            for identifier in identifiers
        ]
        + [{"identifier": "main", "content": "KEEP_MAIN"}],
        "prompt_order": [
            {
                "character_id": 100000,
                "order": [
                    {"identifier": identifier, "enabled": True}
                    for identifier in [*identifiers, "main"]
                ],
            }
        ],
    }
    path = tmp_path / "模块 预设.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize("level", [None, "off", "low", "mid", "high", "cot"])
def test_jailbreak_override_reaches_http_without_changing_preset(
    cli, api, jailbreak_preset, level
):
    before = jailbreak_preset.read_bytes(), cli.config.read_bytes()
    override = [] if level is None else ["--jailbreak", level]
    result = cli(
        "continue",
        "-i",
        cli.story,
        "--config",
        cli.config,
        "--preset-file",
        jailbreak_preset,
        "--base-url",
        api["url"],
        "--model",
        "test",
        *override,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == BODY
    messages = json.dumps(api["requests"][0][1]["messages"])
    for candidate in ("low", "mid", "high", "cot"):
        assert (f"CHECK_nf_jb_{candidate}" in messages) == (
            level is None or level == candidate
        )
    assert ("CHECK_nf_jb_prefill" in messages) == (level != "off")
    assert "KEEP_MAIN" in messages
    assert (jailbreak_preset.read_bytes(), cli.config.read_bytes()) == before


@pytest.mark.parametrize("source", ["saved", "st"])
def test_jailbreak_supports_saved_and_st_presets(cli, jailbreak_preset, source):
    data = json.loads(jailbreak_preset.read_text(encoding="utf-8"))
    if source == "saved":
        directory = cli.storage / "presets"
        directory.mkdir()
        path = directory / "test-jailbreak.json"
        selection = ["--preset", "test-jailbreak"]
    else:
        data.pop("generation_params")
        path = jailbreak_preset
        selection = ["--preset-file", path]
    path.write_text(json.dumps(data), encoding="utf-8")
    before = {
        p.relative_to(cli.home): p.read_bytes()
        for p in cli.home.rglob("*")
        if p.is_file()
    }
    original = path.read_bytes()
    result = cli(
        "continue",
        "-i",
        cli.story,
        "--config",
        cli.config,
        *selection,
        "--jailbreak",
        "mid",
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    messages = json.dumps(json.loads(result.stdout)["messages"])
    assert "CHECK_nf_jb_mid" in messages and "CHECK_nf_jb_high" not in messages
    assert path.read_bytes() == original
    assert {
        p.relative_to(cli.home): p.read_bytes()
        for p in cli.home.rglob("*")
        if p.is_file()
    } == before


@pytest.mark.parametrize("level", ["off", "low", "mid", "high", "cot"])
def test_jailbreak_builtin_preset(cli, level):
    data = json.loads(
        (ROOT / "novelforge/resources/defaults/default_preset.json").read_text(
            encoding="utf-8"
        )
    )
    result = cli(
        "continue",
        "-i",
        cli.story,
        "--config",
        cli.config,
        "--jailbreak",
        level,
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    messages = "\n".join(
        item["content"] for item in json.loads(result.stdout)["messages"]
    )
    for prompt in data["prompts"]:
        if prompt["identifier"] in {
            "nf_jb_low",
            "nf_jb_mid",
            "nf_jb_high",
            "nf_jb_cot",
        }:
            assert (prompt["content"] in messages) == (
                prompt["identifier"] == f"nf_jb_{level}"
            )


@pytest.mark.parametrize("missing", ["prompt", "entry", "group"])
def test_jailbreak_missing_module_fails_before_request(
    cli, api, jailbreak_preset, missing
):
    data = json.loads(jailbreak_preset.read_text(encoding="utf-8"))
    if missing == "prompt":
        data["prompts"] = [
            p for p in data["prompts"] if p["identifier"] != "nf_jb_high"
        ]
    elif missing == "entry":
        data["prompt_order"][0]["order"] = [
            p
            for p in data["prompt_order"][0]["order"]
            if p["identifier"] != "nf_jb_high"
        ]
    else:
        data["prompt_order"][0]["character_id"] = 42
    jailbreak_preset.write_text(json.dumps(data), encoding="utf-8")
    result = cli(
        "continue",
        "-i",
        cli.story,
        "--config",
        cli.config,
        "--preset-file",
        jailbreak_preset,
        "--jailbreak",
        "high",
        "--base-url",
        api["url"],
        "--model",
        "test",
    )
    assert result.returncode == 2
    assert "nf_jb_high" in result.stderr and "Traceback" not in result.stderr
    assert not api["requests"]


def test_jailbreak_invalid_level(cli):
    result = cli("continue", "-i", cli.story, "--jailbreak", "invalid", "--dry-run")
    assert result.returncode == 2
    assert "--jailbreak" in result.stderr and "Traceback" not in result.stderr


def test_workspace_configuration_roundtrip(cli, api):
    result = cli(
        "config",
        "setup",
        "--config",
        cli.config,
        "--id",
        "local-test",
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        "--default",
        extra_env={"GENGBI_API_KEY": "private-test-key"},
    )
    assert result.returncode == 0, result.stderr
    assert "private-test-key" not in cli.config.read_text(encoding="utf-8")
    result = cli("config", "show", "--config", cli.config)
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["default_endpoint"] == "local-test"
    assert state["endpoints"][0]["has_key"] is True
    assert "private-test-key" not in result.stdout
    result = cli(
        "config",
        "set-flow",
        "context_extraction",
        "--endpoint",
        "local-test",
        "--model",
        "extract-model",
        "--config",
        cli.config,
    )
    assert result.returncode == 0, result.stderr
    result = cli("config", "models", "--config", cli.config)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["test-model"]
    result = cli("config", "test", "--config", cli.config)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"]
    assert api["requests"][0][2]["Authorization"] == "Bearer private-test-key"
    result = cli("doctor", "--config", cli.config)
    assert result.returncode == 0 and json.loads(result.stdout)["ready"]


def test_worldbook_edit_export_import(cli, tmp_path):
    def run(*args):
        result = cli("worldbooks", *args, "--config", cli.config)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    book = run("new", "--name", "设定测试")
    content = tmp_path / "城市设定.txt"
    content.write_text("这座城市每年下七天雨。", encoding="utf-8")
    book = run(
        "entry-set",
        book["id"],
        "--content-file",
        content,
        "--name",
        "城市",
        "--key",
        "云城",
        "--position",
        "at_depth",
        "--depth",
        "2",
    )
    uid = book["entries"][0]["uid"]
    assert book["entries"][0]["depth"] == 2
    run("entry-toggle", book["id"], "off", "--uid", uid)
    run("select", book["id"])
    assert run("list")[0]["selected"]
    exported = tmp_path / "世界书.json"
    run("export", book["id"], "-o", exported)
    st = json.loads(exported.read_text(encoding="utf-8"))
    assert st["entries"]["0"]["disable"] is True
    imported = run("import", "-i", exported)
    assert imported["id"] != book["id"]
    assert imported["entries"][0]["content"] == content.read_text(encoding="utf-8")
    result = cli(
        "worldbooks", "export", book["id"], "-o", exported, "--config", cli.config
    )
    assert result.returncode == 2
    run("delete", book["id"], "--yes")
    assert not run("list")[0]["selected"]


def test_project_chapter_roundtrip_preserves_content(cli, tmp_path):
    def run(command, *args):
        result = cli(command, *args, "--config", cli.config)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    project = run("projects", "new", "--name", "我的小说", "-i", cli.story)
    assert project["chapters"] == 2
    chapters = run("chapters", "list", "--project", project["id"])
    first, second = [c["id"] for c in chapters]
    run("chapters", "rename", first, "--title", "新标题")
    run("chapters", "reorder", "--project", project["id"], second, first)
    assert "旧信" in run("chapters", "show", first, "--json")["content"]
    assert "信封" in run("chapters", "show", second, "--json")["content"]
    run("chapters", "reorder", "--project", project["id"], first, second)
    output = tmp_path / "导出.md"
    run("projects", "export", project["id"], "--format", "md", "-o", output)
    text = output.read_text(encoding="utf-8")
    assert "# 新标题" in text and "旧信" in text and "信封" in text
    run("chapters", "split", first, "--position", "3")
    assert len(run("chapters", "list", "--project", project["id"])) == 3
    run("chapters", "merge", first, "--yes")
    assert len(run("chapters", "list", "--project", project["id"])) == 2
    result = cli("projects", "delete", project["id"], "--config", cli.config)
    assert result.returncode == 2
    run("projects", "delete", project["id"], "--yes")
    assert run("projects", "list") == []


def test_no_arguments_show_guide_without_creating_config(cli):
    before = cli.config.read_bytes()
    result = cli()
    assert result.returncode == 0 and "config setup" in result.stdout
    assert cli.config.read_bytes() == before


def test_article_to_worldbook_and_project_continuation(cli, api, tmp_path):
    """验证提取结果可导出、可注入、可接受成章，避免只有命令没有业务闭环。"""

    def run(*args, **kwargs):
        result = cli(*args, "--config", cli.config, **kwargs)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    run(
        "config",
        "setup",
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        extra_env={"GENGBI_API_KEY": "extract-test-key"},
    )
    api["content"] = json.dumps(
        [
            {
                "uid": "rain-rule",
                "category": "locations",
                "key": ["云城"],
                "content": "云城每年只有七天雨。",
                "position": "before",
            }
        ],
        ensure_ascii=False,
    )
    exported = tmp_path / "提取世界书.json"
    result = run(
        "extract",
        "worldbook",
        "-i",
        cli.story,
        "--name",
        "雨夜设定",
        "-o",
        exported,
        "--jailbreak",
        "off",
    )
    assert result["entries"] == 1
    assert "云城" in exported.read_text(encoding="utf-8")
    project, book, chapter = result["project"], result["worldbook"], result["chapter"]
    assert (
        result["next"]["preview"]
        == f"continue --project {project} --worldbook {book} --dry-run"
    )
    run("worldbooks", "select", book)
    preview = run("continue", "--project", project, "--dry-run")
    assert "云城每年只有七天雨" in json.dumps(preview["messages"], ensure_ascii=False)
    api["content"] = BODY
    result = cli("continue", "--project", project, "--save", "--config", cli.config)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == BODY
    assert "云城每年只有七天雨" in json.dumps(
        api["requests"][-1][1]["messages"], ensure_ascii=False
    )
    versions = run("versions", "list", "--chapter", chapter)
    assert len(versions) == 1
    history = run("history", "list", "--project", project)
    assert len(history) == 1
    accepted = run("versions", "accept", "--chapter", chapter, versions[0]["id"])
    stored = run("chapters", "show", accepted["chapter"], "--json")
    assert stored["content"] == BODY
    assert run("versions", "list", "--chapter", chapter) == []
    assert len(run("chapters", "list", "--project", project)) == 3


def test_project_profiles_and_worldbook_switches(cli, tmp_path):
    def run(*args):
        result = cli(*args, "--config", cli.config)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    project = run("projects", "new", "--name", "我的小说", "-i", cli.story)["id"]
    chapters = run("chapters", "list", "--project", project)
    first, last = chapters[0]["id"], chapters[-1]["id"]
    profile = tmp_path / "角色.json"
    profile.write_text(
        json.dumps({"basic_anchors": {"姓名": "角色标记甲"}}), encoding="utf-8"
    )
    run(
        "profiles",
        "set",
        "protagonist",
        "--project",
        project,
        "--chapter",
        first,
        "-i",
        profile,
        "--yes",
    )
    context_file = tmp_path / "上下文.json"
    context_file.write_text(
        json.dumps([{"uid": "x", "category": "events", "content": "事件标记乙"}]),
        encoding="utf-8",
    )
    run(
        "profiles",
        "set",
        "context",
        "--project",
        project,
        "--chapter",
        first,
        "-i",
        context_file,
        "--yes",
    )
    preview = run(
        "continue",
        "--project",
        project,
        "--lookback-context",
        "--lookback-characters",
        "--dry-run",
    )
    messages = json.dumps(preview["messages"], ensure_ascii=False)
    assert "角色标记甲" in messages and "事件标记乙" in messages
    early = run("continue", "--project", project, "--chapter", first, "--dry-run")
    assert "她终于拆开信封" not in json.dumps(early["messages"], ensure_ascii=False)
    run(
        "profiles",
        "copy",
        "context",
        "--project",
        project,
        "--chapter",
        first,
        "--to",
        last,
        "--yes",
    )
    copied = run("profiles", "show", "context", "--project", project, "--chapter", last)
    assert copied[0]["uid"] != "x" and copied[0]["copied_from"] == 0
    book = run("worldbooks", "new", "--name", "场景")
    note = tmp_path / "条目.txt"
    note.write_text("世界书独有标记丙", encoding="utf-8")
    run("worldbooks", "entry-set", book["id"], "--content-file", note)
    run("worldbooks", "select", book["id"])
    selected = run("continue", "-i", cli.story, "--dry-run")
    assert "世界书独有标记丙" in json.dumps(selected["messages"], ensure_ascii=False)
    disabled = run("continue", "-i", cli.story, "--no-worldbooks", "--dry-run")
    assert "世界书独有标记丙" not in json.dumps(
        disabled["messages"], ensure_ascii=False
    )


@pytest.mark.parametrize(
    "kind,response",
    [
        ("ontology", {"existential_topology": {"层级": "世界观标记"}}),
        ("style", {"language_texture": {"特征": "文风标记"}}),
        ("protagonist", {"basic_anchors": {"name": "主角标记"}}),
        ("character", {"basic_anchors": {"name": "配角标记"}}),
    ],
)
def test_profile_extraction_is_persisted(cli, api, kind, response):
    api["content"] = json.dumps(response, ensure_ascii=False)
    result = cli(
        "config",
        "setup",
        "--base-url",
        api["url"],
        "--model",
        "test",
        "--config",
        cli.config,
        extra_env={"GENGBI_API_KEY": "profile-test-key"},
    )
    assert result.returncode == 0, result.stderr
    extra = ["--character", "配角"] if kind == "character" else []
    result = cli(
        "extract",
        kind,
        "-i",
        cli.story,
        "--config",
        cli.config,
        "--jailbreak",
        "off",
        *extra,
    )
    assert result.returncode == 0, result.stderr
    info = json.loads(result.stdout)
    stored_kind = "characters" if kind == "character" else kind
    stored = cli(
        "profiles",
        "show",
        stored_kind,
        "--project",
        info["project"],
        "--config",
        cli.config,
    )
    assert stored.returncode == 0, stored.stderr
    assert "标记" in stored.stdout
    if kind == "ontology":
        books = cli("worldbooks", "list", "--config", cli.config)
        assert books.returncode == 0 and json.loads(books.stdout)[0]["entries"] == 1


@pytest.mark.parametrize("runner", ["volume", "flows"])
def test_volume_pauses_resumes_and_saves_two_chapters(cli, api, tmp_path, runner):
    """真实 HTTP 验证阶段恢复不会重发已完成的分析，并保留逐章产物。"""

    def run(*args, **kwargs):
        result = cli(*args, "--config", cli.config, **kwargs)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    run(
        "config",
        "setup",
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        extra_env={"GENGBI_API_KEY": "volume-test-key"},
    )
    project = run("projects", "new", "--name", "卷测试", "-i", cli.story)["id"]
    settings = tmp_path / "卷参数.json"
    settings.write_text(
        json.dumps(
            {
                "enable_outline_audit": False,
                "enable_chapter_verify": False,
                "enable_chapter_revise": False,
                "checkpoints": {"after_chapter": True},
            }
        ),
        encoding="utf-8",
    )
    outline = {
        "volume_title": "新卷",
        "chapter_count": 2,
        "chapters": [
            {"index": i + 1, "title": f"新章{i + 1}", "summary": "故事继续"}
            for i in range(2)
        ],
    }
    scene = json.dumps(
        {"continuation_goals": "继续故事", "scenes": [{"purpose": "推进"}]}
    )
    api["responses"] = [
        json.dumps({"world_state": "雨夜"}),
        json.dumps(outline),
        scene,
        "第一篇正文。",
        scene,
        "第二篇正文。",
    ]
    preview = run(
        *(["volume", "start"] if runner == "volume" else ["flows", "run", "volume"]),
        "--project",
        project,
        "--chapters",
        "2",
        "--settings",
        settings,
        "--dry-run",
    )
    assert preview["messages"] and not api["requests"]
    assert not (
        cli.storage / ("cli_runs" if runner == "volume" else "cli_flow_runs")
    ).exists()
    paused = run(
        *(["volume", "start"] if runner == "volume" else ["flows", "run", "volume"]),
        "--project",
        project,
        "--chapters",
        "2",
        "--settings",
        settings,
        "--auto",
        "--pause-after",
        "deep_analysis",
    )
    assert paused["status"] == "paused" and len(api["requests"]) == 1
    output = tmp_path / "整卷.txt"
    complete = run(runner, "resume", paused["id"], "--auto", "-o", output)
    if runner == "flows":
        complete["version_id"] = complete["versions"]["chapter_writing"]
    assert complete["status"] == "completed" and len(api["requests"]) == 6
    assert "第一篇正文" in output.read_text(encoding="utf-8")
    assert "第二篇正文" in output.read_text(encoding="utf-8")
    candidate = run(
        "versions", "show", "--chapter", complete["chapter"], complete["version_id"]
    )
    assert len(candidate["volume_artifacts"]["chapter_artifacts"]) == 2
    again = run(runner, "resume", paused["id"], "--auto")
    if runner == "flows":
        again["version_id"] = again["versions"]["chapter_writing"]
    assert again["version_id"] == complete["version_id"] and len(api["requests"]) == 6
    state = run(runner, "show" if runner == "volume" else "run-show", paused["id"])
    assert "volume-test-key" not in json.dumps(state)
    accepted = run(
        "versions", "accept", "--chapter", complete["chapter"], complete["version_id"]
    )
    assert len(accepted["chapters"]) == 2
    assert len(run("chapters", "list", "--project", project)) == 4


def test_audit_and_revision_keep_original(cli, api, tmp_path):
    result = cli(
        "config",
        "setup",
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        "--config",
        cli.config,
        extra_env={"GENGBI_API_KEY": "audit-test-key"},
    )
    assert result.returncode == 0, result.stderr
    result = cli(
        "projects", "new", "--name", "审稿测试", "-i", cli.story, "--config", cli.config
    )
    project = json.loads(result.stdout)["id"]
    api["content"] = '{"passed":false,"issues":["补充环境描写"]}'
    report = tmp_path / "审稿意见.txt"
    result = cli(
        "audit",
        "--project",
        project,
        "--config",
        cli.config,
        "-o",
        report,
        "--jailbreak",
        "off",
    )
    assert result.returncode == 0, result.stderr
    assert "补充环境描写" in report.read_text(encoding="utf-8")
    api["content"] = BODY
    result = cli(
        "revise",
        "--project",
        project,
        "--report",
        report,
        "--config",
        cli.config,
        "--jailbreak",
        "off",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == BODY
    messages = json.dumps(api["requests"][-1][1]["messages"], ensure_ascii=False)
    assert "补充环境描写" in messages and "她终于拆开信封" in messages
    result = cli("chapters", "list", "--project", project, "--config", cli.config)
    chapter = json.loads(result.stdout)[-1]["id"]
    result = cli("chapters", "show", chapter, "--json", "--config", cli.config)
    assert "她终于拆开信封" in json.loads(result.stdout)["content"]
    result = cli("versions", "list", "--chapter", chapter, "--config", cli.config)
    assert len(json.loads(result.stdout)) == 1


def test_assets_presets_regex_and_flow_roundtrip(cli, tmp_path):
    def run(*args):
        result = cli(*args, "--config", cli.config)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    preset = run("preset", "new", "--name", "自定义预设")
    identifier = preset["prompts"][0]["identifier"]
    run("preset", "prompt-toggle", preset["id"], identifier, "off")
    stored = run("preset", "show", preset["id"])
    assert not next(p for p in stored["prompts"] if p["identifier"] == identifier)[
        "enabled"
    ]
    preset_file = tmp_path / "预设导出.json"
    run("preset", "export", preset["id"], "-o", preset_file)
    imported = run("preset", "import", "-i", preset_file)
    assert imported["id"] != preset["id"]
    script = run(
        "regex", "new", "--name", "去冗词", "--find", "/TEST/g", "--replace", "替换"
    )
    run("regex", "toggle", script["id"], "off")
    assert run("regex", "show", script["id"])["disabled"]
    regex_file = tmp_path / "正则导出.json"
    run("regex", "export", "-o", regex_file)
    assert regex_file.exists()
    flows = run("flows", "list")
    assert {f["id"] for f in flows} >= {"single", "volume", "planned_writing"}
    flow_file = tmp_path / "流程.json"
    run("flows", "export", "single", "-o", flow_file)
    first = run("flows", "import", "-i", flow_file)
    second = run("flows", "import", "-i", flow_file)
    assert first["id"] != second["id"] and not first["builtin"]
    result = cli("flows", "delete", "single", "--yes", "--config", cli.config)
    assert result.returncode == 2


def test_wizard_imports_article_and_returns_from_invalid_choice(
    cli, monkeypatch, capsys
):
    """向导调用真实导入/查询服务，不靠重复实现菜单路由的桩判断成功。"""
    import argparse
    import asyncio

    from novelforge.cli_wizard import execute

    if os.environ.get("GENGBI_TEST_EXECUTABLE"):
        pytest.skip("交互输入注入检查源码；EXE 使用子进程验证非交互入口")
    answers = iter(
        ["invalid", "2", "向导小说", str(cli.story), "utf-8-sig", "9", "1", "", "0"]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("HOME", str(cli.home))
    monkeypatch.setenv("USERPROFILE", str(cli.home))
    before = cli.config.read_bytes()
    asyncio.run(execute(argparse.Namespace(config=cli.config)))
    captured = capsys.readouterr()
    assert "请选择" in captured.out and "向导小说" in captured.out
    assert "已导入" in captured.out and not captured.err
    result = cli("projects", "list", "--config", cli.config)
    project = json.loads(result.stdout)[0]
    assert project["name"] == "向导小说"
    result = cli("chapters", "list", "--project", project["id"], "--config", cli.config)
    assert len(json.loads(result.stdout)) == 2
    assert cli.config.read_bytes() == before


def test_wizard_requires_terminal_without_mutation(cli):
    before = cli.config.read_bytes()
    result = cli("wizard", "--config", cli.config, stdin="0\n")
    assert result.returncode == 2
    assert "guide extract" in result.stderr and "Traceback" not in result.stderr
    assert cli.config.read_bytes() == before
    assert not (cli.storage / "novelforge.db").exists()


@pytest.mark.parametrize(
    "flow,steps",
    [
        ("single", 1),
        ("rewrite_current", 2),
        ("planned_writing", 2),
        ("writing_mode", 3),
    ],
)
def test_builtin_flow_executes_each_stage_and_preserves_original(cli, api, flow, steps):
    def run(*args, **kwargs):
        result = cli(*args, "--config", cli.config, **kwargs)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    run(
        "config",
        "setup",
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        extra_env={"GENGBI_API_KEY": "flow-test-key"},
    )
    project = run("projects", "new", "--name", "流程小说", "-i", cli.story)["id"]
    api["responses"] = [f"阶段参考标记{i}" for i in range(steps - 1)] + [BODY]
    preview = run("flows", "run", flow, "--project", project, "--dry-run")
    assert len(preview["plan"]) == steps and not api["requests"]
    assert not (cli.storage / "cli_flow_runs").exists()
    result = run(
        "flows",
        "run",
        flow,
        "--project",
        project,
        "--auto",
        "--analysis-jailbreak",
        "off",
    )
    assert result["status"] == "completed" and len(api["requests"]) == steps
    if steps > 1:
        assert f"阶段参考标记{steps - 2}" in json.dumps(
            api["requests"][-1][1]["messages"], ensure_ascii=False
        )
    chapter = run("chapters", "show", result["chapter"], "--json")
    assert "她终于拆开信封" in chapter["content"]
    version = run(
        "versions",
        "show",
        next(iter(result["versions"].values())),
        "--chapter",
        result["chapter"],
    )
    assert version["content"] == BODY
    assert result["accept_mode"] == (
        "replace" if flow == "rewrite_current" else "promote"
    )
    run("flows", "resume", result["id"], "--auto")
    assert len(api["requests"]) == steps


def test_custom_flow_uses_named_input_and_resumes_edited_analysis(cli, api, tmp_path):
    def run(*args, **kwargs):
        result = cli(*args, "--config", cli.config, **kwargs)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    run(
        "config",
        "setup",
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        extra_env={"GENGBI_API_KEY": "flow-test-key"},
    )
    project = run("projects", "new", "--name", "自定义流程", "-i", cli.story)["id"]
    definition = tmp_path / "流程.json"
    definition.write_text(
        json.dumps(
            {
                "id": "custom",
                "name": "自定义",
                "stages": [
                    {
                        "id": "a",
                        "agent": "audit",
                        "flow_key": "writing_element_analysis",
                    },
                    {
                        "id": "b",
                        "agent": "audit",
                        "flow_key": "writing_element_refinement",
                        "input_from": "a",
                    },
                    {"id": "check", "agent": "checkpoint", "input_from": "a"},
                    {"id": "write", "agent": "continuation", "input_from": "check"},
                ],
            }
        ),
        encoding="utf-8",
    )
    flow = run("flows", "import", "-i", definition)["id"]
    api["responses"] = ["原分析标记", "无关深化标记", BODY]
    paused = run(
        "flows", "run", flow, "--project", project, "--auto", "--pause-after", "a"
    )
    assert len(api["requests"]) == 1
    edited = tmp_path / "修改分析.txt"
    edited.write_text("人工校对参考标记", encoding="utf-8")
    run("flows", "run-edit", paused["id"], "a", "-i", edited)
    complete = run("flows", "resume", paused["id"], "--auto")
    assert complete["status"] == "completed" and len(api["requests"]) == 3
    final_prompt = json.dumps(api["requests"][-1][1]["messages"], ensure_ascii=False)
    assert "人工校对参考标记" in final_prompt and "无关深化标记" not in final_prompt
    assert "flow-test-key" not in json.dumps(run("flows", "run-show", complete["id"]))


def test_advanced_settings_reach_requests_and_reject_invalid_changes(
    cli, api, tmp_path
):
    def run(*args, **kwargs):
        result = cli(*args, "--config", cli.config, **kwargs)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    run(
        "config",
        "setup",
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        extra_env={"GENGBI_API_KEY": "settings-test-key"},
    )
    assert run("config", "models", "--save") == ["test-model"]
    options = tmp_path / "端点选项.json"
    options.write_text(
        json.dumps(
            {
                "enabled_models": ["selected-model"],
                "reasoning_effort": "low",
                "extra_headers": {"X-Client-Test": "private-header-value"},
                "extra_payload": {"seed": 17},
            }
        ),
        encoding="utf-8",
    )
    shown = run("config", "endpoint-options", "default", "-i", options)
    assert shown["default_model"] == "selected-model"
    assert "private-header-value" not in json.dumps(shown)
    run("config", "copy", "default", "--id", "copied")
    run("config", "set-flow", "single_continuation", "--endpoint", "copied")
    settings = tmp_path / "写作设置.json"
    settings.write_text(
        json.dumps({"default_temperature": 0.37, "prefer_non_stream": True}),
        encoding="utf-8",
    )
    run("config", "settings", "continuation", "-i", settings)
    result = cli("continue", "-i", cli.story, "--config", cli.config)
    assert result.returncode == 0, result.stderr
    request, headers = api["requests"][-1][1:]
    assert request["model"] == "selected-model" and request["temperature"] == 0.37
    assert request["stream"] is False and request["seed"] == 17
    assert headers["X-Client-Test"] == "private-header-value"
    assert headers["Authorization"] == "Bearer settings-test-key"
    run("config", "set-flow", "single_continuation", "--reset")
    assert (
        "single_continuation"
        not in json.loads(cli.config.read_text(encoding="utf-8"))["flow_endpoints"]
    )
    text = tmp_path / "流程自定义.txt"
    text.write_text("自定义分析测试锚", encoding="utf-8")
    run(
        "config",
        "set-jailbreak",
        "writing_element_analysis",
        "custom",
        "--text-file",
        text,
    )
    project = run("projects", "new", "--name", "高级配置", "-i", cli.story)["id"]
    preview = run("flows", "run", "writing_mode", "--project", project, "--dry-run")
    assert "自定义分析测试锚" in json.dumps(preview["messages"], ensure_ascii=False)
    before = cli.config.read_bytes()
    settings.write_text('{"default_temperature": "bad"}', encoding="utf-8")
    result = cli(
        "config", "settings", "continuation", "-i", settings, "--config", cli.config
    )
    assert result.returncode == 2 and "Traceback" not in result.stderr
    assert cli.config.read_bytes() == before


def test_project_backup_preserves_profiles_context_and_worldbook(cli, tmp_path):
    def run(*args):
        result = cli(*args, "--config", cli.config)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    project = run("projects", "new", "--name", "备份测试", "-i", cli.story)["id"]
    chapter = run("chapters", "list", "--project", project)[-1]["id"]
    edited = tmp_path / "编辑.json"
    edited.write_text('{"basic_anchors":{"姓名":"备份人物"}}', encoding="utf-8")
    run(
        "profiles",
        "set",
        "protagonist",
        "--project",
        project,
        "--chapter",
        chapter,
        "-i",
        edited,
        "--yes",
    )
    edited.write_text(
        '[{"uid":"saved-context","content":"备份上下文"}]', encoding="utf-8"
    )
    run(
        "profiles",
        "set",
        "context",
        "--project",
        project,
        "--chapter",
        chapter,
        "-i",
        edited,
        "--yes",
    )
    book = run("worldbooks", "new", "--name", "备份世界观")["id"]
    edited.write_text(json.dumps({"worldbook_id": book}), encoding="utf-8")
    run("projects", "update", project, "-i", edited)
    backup = tmp_path / "项目.zip"
    run("projects", "backup", project, "-o", backup)
    restored = run("projects", "restore", "-i", backup)["project"]
    assert restored != project
    metadata = run("projects", "show", restored)
    assert metadata["worldbook_id"] != book
    assert run("worldbooks", "show", metadata["worldbook_id"])["name"] == "备份世界观"
    chapters = run("chapters", "list", "--project", restored)
    assert len(chapters) == 2
    content = run("chapters", "show", chapters[-1]["id"], "--json")
    assert "她终于拆开信封" in content["content"]
    assert "备份人物" in json.dumps(content["protagonist_profile"], ensure_ascii=False)
    context = run("profiles", "show", "context", "--project", restored)
    assert "备份上下文" in json.dumps(context, ensure_ascii=False)


def test_regex_preview_and_manual_candidate_edit(cli, api, tmp_path):
    def run(*args, **kwargs):
        result = cli(*args, "--config", cli.config, **kwargs)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    script = run(
        "regex", "new", "--name", "文本测试", "--find", "/旧信/g", "--replace", "书信"
    )
    result = run("regex", "test", script["id"], "--text-file", cli.story)
    assert "书信" in result["text"] and result["matches"]
    assert "旧信" in cli.story.read_text(encoding="utf-8-sig")
    run(
        "config",
        "setup",
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        extra_env={"GENGBI_API_KEY": "edit-test-key"},
    )
    project = run("projects", "new", "--name", "编辑测试", "-i", cli.story)["id"]
    generated = run("flows", "run", "single", "--project", project, "--auto")
    chapter, version = generated["chapter"], next(iter(generated["versions"].values()))
    text = tmp_path / "人工正文.txt"
    text.write_text("人工修订后的正文。", encoding="utf-8")
    run(
        "versions",
        "edit",
        version,
        "--chapter",
        chapter,
        "--content-file",
        text,
        "--yes",
    )
    edited = run("versions", "show", version, "--chapter", chapter)
    assert edited["content"] == "人工修订后的正文。" and edited["raw_content"] == BODY
    run("versions", "replace", version, "--chapter", chapter, "--yes")
    assert run("chapters", "show", chapter, "--json")["content"] == "人工修订后的正文。"


def test_incremental_and_batched_extraction_cache_and_custom_rule(cli, api, tmp_path):
    def run(*args, **kwargs):
        result = cli(*args, "--config", cli.config, **kwargs)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    run(
        "config",
        "setup",
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        extra_env={"GENGBI_API_KEY": "incremental-test-key"},
    )
    project = run("projects", "new", "--name", "分批增量", "-i", cli.story)["id"]
    first, last = run("chapters", "list", "--project", project)
    api["content"] = '[{"uid":"base","content":"首章资料标记"}]'
    run("extract", "context", "--project", project, "--chapter", first["id"])
    assert len(api["requests"]) == 1
    api["content"] = '[{"uid":"base","content":"更新后资料标记"}]'
    run(
        "extract",
        "context",
        "--project",
        project,
        "--chapter",
        last["id"],
        "--incremental",
    )
    prompt = json.dumps(api["requests"][-1][1]["messages"], ensure_ascii=False)
    assert "首章资料标记" in prompt and "她终于拆开信封" in prompt
    assert "旧信被锁在抽屉里" not in prompt
    cached = run("extract", "context", "--project", project, "--incremental")
    assert cached["from_cache"] is True and len(api["requests"]) == 2
    api["responses"] = [
        '[{"uid":"one","content":"第一批资料"}]',
        '[{"uid":"two","content":"第二批资料"}]',
        '[{"uid":"merged","content":"合并资料标记"}]',
    ]
    batch = run(
        "extract", "context", "--project", project, "--refresh", "--batch-tokens", "1"
    )
    assert len(api["requests"]) == 5
    assert "合并资料标记" in json.dumps(batch, ensure_ascii=False)
    assert "她终于拆开信封" not in json.dumps(
        api["requests"][2][1]["messages"], ensure_ascii=False
    )
    assert "旧信被锁在抽屉里" not in json.dumps(
        api["requests"][3][1]["messages"], ensure_ascii=False
    )
    rule = tmp_path / "要求.txt"
    rule.write_text("所有魔法都必须消耗记忆。", encoding="utf-8")
    api["content"] = json.dumps(
        {
            "title": "魔法代价",
            "requirement": "施法必须消耗记忆",
            "audit_criteria": "检查每次施法后是否体现记忆损失",
            "severity": "critical",
        },
        ensure_ascii=False,
    )
    run("extract", "rule", "--project", project, "--prompt-file", rule)
    preview = run("continue", "--project", project, "--dry-run")
    assert "施法必须消耗记忆" in json.dumps(preview["messages"], ensure_ascii=False)


def test_resume_rejects_changed_worldbook_without_request(cli, api, tmp_path):
    def run(*args, **kwargs):
        result = cli(*args, "--config", cli.config, **kwargs)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    run(
        "config",
        "setup",
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        extra_env={"GENGBI_API_KEY": "resume-test-key"},
    )
    project = run("projects", "new", "--name", "恢复检查", "-i", cli.story)["id"]
    book = run("worldbooks", "new", "--name", "待修改世界书")["id"]
    paused = run(
        "flows",
        "run",
        "planned_writing",
        "--project",
        project,
        "--worldbook",
        book,
        "--auto",
        "--pause-after",
        "outline",
    )
    assert len(api["requests"]) == 1

    changed = tmp_path / "新设定.txt"
    changed.write_text("改变后的世界设定", encoding="utf-8")
    run("worldbooks", "entry-set", book, "--content-file", changed)
    result = cli("flows", "resume", paused["id"], "--auto", "--config", cli.config)
    assert result.returncode == 2 and "发生变化" in result.stderr
    assert len(api["requests"]) == 1


def test_legacy_volume_pipeline_plugin_runs_full_engine(cli, api, tmp_path):
    def run(*args, **kwargs):
        result = cli(*args, "--config", cli.config, **kwargs)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    run(
        "config",
        "setup",
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        extra_env={"GENGBI_API_KEY": "legacy-test-key"},
    )
    project = run("projects", "new", "--name", "整卷旧流程", "-i", cli.story)["id"]
    definition = tmp_path / "旧流程.json"
    definition.write_text(
        json.dumps(
            {
                "id": "legacy",
                "name": "完整卷",
                "accept_mode": "volume_insert",
                "stages": [
                    {
                        "id": "full",
                        "agent": "volume_pipeline",
                        "flow_key": "volume_continuation",
                        "created_by": "volume",
                        "params": {
                            "enable_outline_audit": False,
                            "enable_chapter_verify": False,
                            "enable_chapter_revise": False,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run("flows", "import", "-i", definition)
    outline = {
        "chapter_count": 2,
        "chapters": [
            {"index": i + 1, "title": f"新章{i + 1}", "summary": "故事继续"}
            for i in range(2)
        ],
    }
    scene = json.dumps({"scenes": [{"purpose": "推进"}]})
    api["responses"] = [
        json.dumps({"world_state": "雨夜"}),
        json.dumps(outline),
        scene,
        "第一篇正文。",
        scene,
        "第二篇正文。",
    ]
    result = run(
        "flows", "run", "legacy", "--project", project, "--chapters", "2", "--auto"
    )
    assert result["status"] == "completed" and len(api["requests"]) == 6
    candidate = run(
        "versions", "show", result["versions"]["full"], "--chapter", result["chapter"]
    )
    assert len(candidate["volume_artifacts"]["chapter_artifacts"]) == 2


def test_failed_flow_resumes_without_repeating_analysis(cli, api):
    def run(*args, **kwargs):
        result = cli(*args, "--config", cli.config, **kwargs)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    run(
        "config",
        "setup",
        "--base-url",
        api["url"],
        "--model",
        "test-model",
        extra_env={"GENGBI_API_KEY": "failed-flow-key"},
    )
    project = run("projects", "new", "--name", "恢复失败阶段", "-i", cli.story)["id"]
    paused = run(
        "flows",
        "run",
        "planned_writing",
        "--project",
        project,
        "--auto",
        "--pause-after",
        "outline",
    )
    api["status"] = 401
    failed = cli("flows", "resume", paused["id"], "--auto", "--config", cli.config)
    assert failed.returncode == 1 and "secret-key" not in failed.stderr
    state = run("flows", "run-show", paused["id"])
    assert state["completed"] == ["outline"] and state["status"] == "failed"
    calls = len(api["requests"])
    api["status"] = 200
    complete = run("flows", "resume", paused["id"], "--auto")
    assert complete["status"] == "completed" and len(api["requests"]) == calls + 1


def test_local_no_key_extraction_and_saved_volume_defaults(cli, api, tmp_path):
    def run(*args):
        result = cli(*args, "--config", cli.config)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    run(
        "config", "setup", "--base-url", api["url"], "--model", "test-model", "--no-key"
    )
    run("config", "setup", "--model", "test-model")
    project = run("projects", "new", "--name", "本地模型", "-i", cli.story)["id"]
    api["content"] = '[{"uid":"local","content":"本地模型设定"}]'
    extracted = run("extract", "context", "--project", project)
    assert extracted["entries"] == 1
    assert "Authorization" not in api["requests"][-1][2]
    settings = tmp_path / "全局卷配置.json"
    settings.write_text(
        json.dumps({"chapter_count": 2, "target_words_per_chapter": 1300}),
        encoding="utf-8",
    )
    run("config", "settings", "volume", "-i", settings)
    api["content"] = '{"world_state":"来自本地模型的分析"}'
    for command in (["volume", "start"], ["flows", "run", "volume"]):
        paused = run(
            *command, "--project", project, "--auto", "--pause-after", "deep_analysis"
        )
        state = run(
            command[0], "show" if command[0] == "volume" else "run-show", paused["id"]
        )
        assert state["settings"]["chapter_count"] == 2
        assert state["settings"]["target_words_per_chapter"] == 1300
        assert state["options"]["target_words"] == 1300


def test_terminal_checkpoint_requires_valid_action(monkeypatch, tmp_path):
    if os.environ.get("GENGBI_TEST_EXECUTABLE"):
        pytest.skip("同步回调输入注入为源码专用；EXE 验证 --auto 和暂停恢复")
    from types import SimpleNamespace

    from novelforge.cli_runtime import connect_checkpoints
    from novelforge.services.volume_engine import _Event

    approved = []
    engine = SimpleNamespace(checkpoint_reached=_Event(), resume=approved.append)
    connect_checkpoints(engine, auto=False, directory=tmp_path, run_id="test_run")
    replies = iter(["typo", "reject ", "reject 增加人物动机"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    engine.checkpoint_reached.emit("after_chapter", {"content": "正文"})
    assert approved == [{"action": "reject", "feedback": "增加人物动机"}]
    monkeypatch.setattr("builtins.input", lambda _prompt: "cancel")
    with pytest.raises(KeyboardInterrupt):
        engine.checkpoint_reached.emit("before_audit", {"outline": "大纲"})
    assert len(approved) == 1


@pytest.mark.parametrize(
    "status,content,finish,code,requests",
    [
        (401, "", "stop", "authentication", 1),
        (400, "", "stop", "api_error", 1),
        (200, "private-response-text is not JSON", "stop", "response_json", 2),
        (200, "", "stop", "empty_response", 1),
        (200, '[{"content":"unfinished private-response-text"}', "length", "output_truncated", 1),
        (200, "private-response-text", "content_filter", "provider_filtered", 1),
    ],
)
def test_extraction_failure_has_safe_cause_and_retry_context(
    cli, api, tmp_path, status, content, finish, code, requests
):
    setup = cli(
        "config", "setup", "--config", cli.config, "--base-url", api["url"],
        "--model", "test-model", extra_env={"GENGBI_API_KEY": "private-api-key-value"},
    )
    assert setup.returncode == 0, setup.stderr
    api.update(status=status, content=content, finish=finish)
    diagnostics = tmp_path / "诊断"
    result = cli(
        "extract", "worldbook", "-i", cli.story, "--config", cli.config,
        "--no-stream", "--error-format", "json", "--diagnostics-dir", diagnostics,
    )
    assert result.returncode == 1, result.stderr
    error = json.loads(result.stderr.splitlines()[-1])["error"]
    assert error["code"] == code, error
    assert error["phase"].startswith("提取")
    assert error["context"]["project"].startswith("proj_")
    assert error["context"]["batch"] == "1"
    assert len(api["requests"]) == requests
    saved = Path(error["diagnostic_file"]).read_text(encoding="utf-8")
    for private in ("private-response-text", "private-api-key-value", "secret-key", STORY):
        assert private not in result.stderr + saved
    assert "Traceback" not in result.stderr
    project = cli("projects", "show", error["context"]["project"], "--config", cli.config)
    assert project.returncode == 0, project.stderr


def test_json_error_for_invalid_arguments_and_diagnostic_write_failure(cli, tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("unchanged", encoding="utf-8")
    result = cli(
        "continue", "-i", cli.story, "--jailbreak", "bad-secret-choice",
        "--error-format", "json", "--diagnostics-dir", blocked,
    )
    assert result.returncode == 2
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "usage"
    assert "bad-secret-choice" not in result.stderr
    assert error["diagnostic_write_error"] == "FileExistsError"
    assert blocked.read_text(encoding="utf-8") == "unchanged"

@pytest.mark.parametrize("kind", ["context", "worldbook", "protagonist", "character", "ontology", "style", "rule"])
@pytest.mark.parametrize("stream", ["--stream", "--no-stream"])
def test_all_extraction_types_reject_truncated_response(cli, api, tmp_path, kind, stream):
    setup = cli("config", "setup", "--config", cli.config, "--base-url", api["url"],
                "--model", "test", extra_env={"GENGBI_API_KEY": "test-key"})
    assert setup.returncode == 0, setup.stderr
    rule = tmp_path / "rule.txt"
    rule.write_text("保持人物姓名一致", encoding="utf-8")
    extra = ["--character", "配角"] if kind == "character" else ["--prompt-file", rule] if kind == "rule" else []
    api.update(finish="length", content='{"incomplete": "private-text"}')
    result = cli("extract", kind, "-i", cli.story, "--config", cli.config,
                 "--error-format", "json", stream, *extra)
    assert result.returncode == 1, result.stderr
    error = json.loads(result.stderr.splitlines()[-1])["error"]
    assert error["code"] == "output_truncated", error
    assert error["context"]["project"] in error["retry"]
    assert len(api["requests"]) == 1
    assert "private-text" not in result.stderr


@pytest.mark.parametrize("stream,raw,code", [
    ("--stream", 'data: {broken private-data\n\n', "response_json"),
    ("--stream", 'data: {"error":{"code":"insufficient_quota","message":"private-data"}}\n\n', "quota_exhausted"),
    ("--stream", 'data: {"choices":[{"delta":{"content":"private-data"}}]}\n\n', "incomplete_response"),
    ("--stream", 'data: {"choices":[{"delta":{"refusal":"private-data"}}]}\n\n', "provider_refusal"),
    ("--no-stream", '{"choices":[]}', "response_schema"),
    ("--no-stream", '[]', "response_schema"),
    ("--no-stream", '{"choices":[{"message":{"refusal":"private-data"},"finish_reason":"stop"}]}', "provider_refusal"),
])
def test_protocol_failures_never_write_output(cli, api, tmp_path, stream, raw, code):
    api["raw"] = raw
    output = tmp_path / "result.txt"
    output.write_text("原结果", encoding="utf-8")
    result = cli("continue", "-i", cli.story, "--config", cli.config,
                 "--base-url", api["url"], "--model", "test", stream,
                 "-o", output, "--force", "--error-format", "json")
    assert result.returncode == 1, result.stderr
    error = json.loads(result.stderr.splitlines()[-1])["error"]
    assert error["code"] == code
    assert output.read_text(encoding="utf-8") == "原结果"
    assert "private-data" not in result.stderr
    assert len(api["requests"]) == 1


@pytest.mark.parametrize("command,directory,filename,raw", [
    ("worldbooks", "worldbooks", "broken.json", '{"private-source":'),
    ("regex", "regex", "global.json", '{"wrong":"private-source"}'),
])
def test_corrupt_resources_fail_without_overwriting(cli, command, directory, filename, raw):
    folder = cli.storage / directory
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_text(raw, encoding="utf-8")
    result = cli(command, "list", "--config", cli.config, "--error-format", "json")
    assert result.returncode != 0, result.stdout
    assert "private-source" not in result.stderr
    assert path.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("quota", [False, True])
def test_rate_limit_and_quota_are_distinct(cli, api, quota):
    api["status"] = 429
    if quota:
        api["error_body"] = '{"error":{"code":"insufficient_quota","message":"private-credit"}}'
    result = cli("continue", "-i", cli.story, "--config", cli.config,
                 "--base-url", api["url"], "--model", "test", "--no-stream", "--error-format", "json")
    assert result.returncode == 1, result.stderr
    error = json.loads(result.stderr.splitlines()[-1])["error"]
    assert error["code"] == ("quota_exhausted" if quota else "rate_limited")
    assert len(api["requests"]) == (1 if quota else 4)
    assert "private-credit" not in result.stderr and "secret-key" not in result.stderr


@pytest.mark.parametrize("command,data", [("worldbooks", {"entries": ["private-invalid"]}), ("preset", {"prompts": ["private-invalid"]}), ("regex", ["private-invalid"])])
def test_invalid_resource_import_does_not_create_partial_result(cli, tmp_path, command, data):
    before = cli(command, "list", "--config", cli.config)
    assert before.returncode == 0, before.stderr
    source = tmp_path / "invalid-import.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    result = cli(command, "import", "-i", source, "--config", cli.config)
    assert result.returncode == 2, result.stderr
    assert "private-invalid" not in result.stderr
    after = cli(command, "list", "--config", cli.config)
    assert after.returncode == 0 and json.loads(after.stdout) == json.loads(before.stdout)


def test_protected_preset_toggle_reports_failure(cli):
    result = cli("preset", "toggle", "default", "off", "--config", cli.config)
    assert result.returncode == 2 and "内置保护" in result.stderr
    value = cli("preset", "show", "default", "--config", cli.config)
    assert value.returncode == 0 and json.loads(value.stdout)["enabled"] is True


@pytest.mark.parametrize("mode", ["stream", "nonstream", "sse", "models", "auth", "rate", "text", "extract", "error200"])
def test_provider_reason_survives_all_api_paths(cli, api, mode):
    secret = "custom-opaque-credential-987654321"
    message = "Parameter stream is not supported for this route. " + secret + " " + STORY
    error_body = json.dumps({"error": {"message": message, "code": "unsupported_parameter", "type": "invalid_request_error", "param": "stream"}})
    api.update(status=400, error_body=error_body)
    if mode == "sse":
        api.update(status=200, raw="data: " + error_body + "\n\n")
    elif mode == "error200":
        api.update(status=200, raw=error_body)
    elif mode == "auth":
        api["status"] = 401
    elif mode == "rate":
        api["status"] = 429
    elif mode == "text":
        api["error_body"] = "<html><body>Upstream route rejected stream " + secret + " " + STORY + "</body></html>"
    if mode in {"models", "extract"}:
        setup = cli("config", "setup", "--config", cli.config, "--base-url", api["url"], "--model", "test", extra_env={"GENGBI_API_KEY": secret})
        assert setup.returncode == 0
        if mode == "models":
            api.update(get_status=400, get_body=json.dumps({"error": {"message": "Unknown model catalog " + secret, "code": "catalog_unavailable"}}))
            result = cli("config", "models", "--config", cli.config, "--error-format", "json")
        else:
            result = cli("extract", "worldbook", "-i", cli.story, "--config", cli.config, "--error-format", "json")
    else:
        result = cli("continue", "-i", cli.story, "--config", cli.config,
                     "--base-url", api["url"], "--model", "test",
                     "--no-stream" if mode in {"nonstream", "error200"} else "--stream",
                     "--error-format", "json", extra_env={"GENGBI_API_KEY": secret})
    assert result.returncode == 1, result.stderr
    error = json.loads(result.stderr.splitlines()[-1])["error"]
    details = error["details"]
    assert details["request_id"] == "req-local-test-123"
    assert ("Unknown model catalog" if mode == "models" else "Upstream route rejected stream" if mode == "text" else "Parameter stream is not supported") in details["provider_message"]
    if mode != "text":
        assert details["provider_code"] == ("catalog_unavailable" if mode == "models" else "unsupported_parameter")
    saved = Path(error["diagnostic_file"]).read_text(encoding="utf-8")
    assert secret not in result.stderr + saved
    assert "旧信被锁在抽屉里" not in result.stderr + saved
