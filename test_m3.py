#!/usr/bin/env python
"""M3 里程碑验证测试脚本。

验证正则引擎、变量存储、模板引擎、正则服务的关键功能：
1. 正则 flag 解析（g/i/m/s/u/y）
2. 替换字符串转换（$1/$<name>/{{match}}/$&）
3. 正则编译错误处理
4. 脚本执行顺序（GLOBAL → PRESET → SCOPED）
5. Jinja2 沙箱安全（_ 开头属性、attr 过滤器）
6. 模板渲染（白名单函数、双阶段）
7. 模板语法错误处理
8. 变量作用域（global/project/chapter/cache）
9. ST 正则导入导出（placement 过滤）
10. trimStrings 裁剪
11. depth 过滤
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 确保使用项目根目录
sys.path.insert(0, str(Path(__file__).parent))

from novelforge.core.regex_engine import (
    RegexEngine,
    apply_trim_strings,
    compile_flags,
    convert_replace_string,
    parse_find_regex,
)
from novelforge.core.template_engine import TemplateEngine
from novelforge.core.variable_store import VariableStore
from novelforge.models import (
    PLACEMENT_AI_OUTPUT,
    PLACEMENT_USER_INPUT,
    PLACEMENT_WORLD_INFO,
    RegexScript,
)
from novelforge.services.regex_service import (
    SCOPE_GLOBAL,
    SCOPE_PRESET,
    SCOPE_SCOPED,
    RegexService,
)

# 测试计数
_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    """检查断言并计数。"""
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name} {detail}")
        raise AssertionError(f"{name} {detail}")


def test_parse_find_regex() -> None:
    """测试 1: 正则 findRegex 解析。"""
    print("\n=== 测试 1: 正则 findRegex 解析 ===")

    # /pattern/flags 格式
    pattern, flags = parse_find_regex("/foo/gi")
    check("解析 /foo/gi 的 pattern", pattern == "foo", f"got {pattern!r}")
    check("解析 /foo/gi 的 flags", flags == "gi", f"got {flags!r}")

    # 纯 pattern（无 /）
    pattern, flags = parse_find_regex("foo")
    check("解析 foo 的 pattern", pattern == "foo", f"got {pattern!r}")
    check("解析 foo 的 flags", flags == "", f"got {flags!r}")

    # 空字符串
    pattern, flags = parse_find_regex("")
    check("解析空字符串", pattern == "" and flags == "", f"got {pattern!r}, {flags!r}")


def test_compile_flags() -> None:
    """测试 2: 正则 flag 映射。"""
    print("\n=== 测试 2: 正则 flag 映射 ===")
    import regex

    # g 不映射任何 flag
    flags = compile_flags("g")
    check("g 不映射任何 flag", flags == 0, f"got {flags}")

    # i → IGNORECASE
    flags = compile_flags("i")
    check("i → IGNORECASE", flags == regex.IGNORECASE, f"got {flags}")

    # m → MULTILINE
    flags = compile_flags("m")
    check("m → MULTILINE", flags == regex.MULTILINE, f"got {flags}")

    # s → DOTALL
    flags = compile_flags("s")
    check("s → DOTALL", flags == regex.DOTALL, f"got {flags}")

    # u → UNICODE
    flags = compile_flags("u")
    check("u → UNICODE", flags == regex.UNICODE, f"got {flags}")

    # y 被忽略（返回 0，不报错）
    flags = compile_flags("y")
    check("y 被忽略", flags == 0, f"got {flags}")

    # 组合 flag
    flags = compile_flags("gim")
    check(
        "gim 组合",
        flags == regex.IGNORECASE | regex.MULTILINE,
        f"got {flags}",
    )


def test_convert_replace_string() -> None:
    """测试 3: 替换字符串转换。"""
    print("\n=== 测试 3: 替换字符串转换 ===")

    # $1 → \1
    result = convert_replace_string("$1")
    check("$1 → \\1", result == "\\1", f"got {result!r}")

    # $<name> → \g<name>
    result = convert_replace_string("$<name>")
    check("$<name> → \\g<name>", result == "\\g<name>", f"got {result!r}")

    # {{match}} → \g<0>
    result = convert_replace_string("{{match}}")
    check("{{match}} → \\g<0>", result == "\\g<0>", f"got {result!r}")

    # $& → \g<0>
    result = convert_replace_string("$&")
    check("$& → \\g<0>", result == "\\g<0>", f"got {result!r}")

    # $10 → \10（两位数）
    result = convert_replace_string("$10")
    check("$10 → \\10", result == "\\10", f"got {result!r}")

    # 混合
    result = convert_replace_string("前缀$1中缀$<name>后缀{{match}}")
    check(
        "混合替换字符串",
        result == "前缀\\1中缀\\g<name>后缀\\g<0>",
        f"got {result!r}",
    )


def test_regex_compile_error() -> None:
    """测试 4: 正则编译错误处理。"""
    print("\n=== 测试 4: 正则编译错误处理 ===")

    # 无效正则
    script = RegexScript(
        id="test_invalid",
        scriptName="无效正则",
        findRegex="/[/g",  # 未闭合的字符类
        replaceString="",
        placement=[PLACEMENT_USER_INPUT],
    )
    engine = RegexEngine()
    engine.compile_scripts([script])

    # 编译失败的脚本应记录在 failed_script_ids 中
    check(
        "编译失败脚本被记录",
        "test_invalid" in engine.failed_script_ids,
        f"failed_ids={engine.failed_script_ids}",
    )

    # 应用时不应抛出异常，返回原文本
    result = engine.apply_to_text("hello", placement=PLACEMENT_USER_INPUT)
    check("编译失败脚本应用返回原文本", result == "hello", f"got {result!r}")


def test_script_execution_order() -> None:
    """测试 5: 脚本执行顺序（GLOBAL → PRESET → SCOPED）。"""
    print("\n=== 测试 5: 脚本执行顺序 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        service = RegexService(storage_path=Path(tmpdir))

        # 创建 GLOBAL 脚本（将 "A" 替换为 "A_GLOBAL"）
        global_script = RegexScript(
            id="g1",
            scriptName="全局脚本",
            findRegex="/A/g",
            replaceString="A_GLOBAL",
            placement=[PLACEMENT_USER_INPUT],
        )
        service.add_script(global_script, scope=SCOPE_GLOBAL)

        # 创建 PRESET 脚本（将 "A_GLOBAL" 替换为 "A_PRESET"）
        preset_script = RegexScript(
            id="p1",
            scriptName="预设脚本",
            findRegex="/A_GLOBAL/g",
            replaceString="A_PRESET",
            placement=[PLACEMENT_USER_INPUT],
        )
        service.add_script(preset_script, scope=SCOPE_PRESET, preset_id="default")

        # 创建 SCOPED 脚本（将 "A_PRESET" 替换为 "A_SCOPED"）
        scoped_script = RegexScript(
            id="s1",
            scriptName="项目脚本",
            findRegex="/A_PRESET/g",
            replaceString="A_SCOPED",
            placement=[PLACEMENT_USER_INPUT],
        )
        service.add_script(
            scoped_script, scope=SCOPE_SCOPED, project_id="proj_test"
        )

        # 获取排序后的脚本
        ordered = service.get_ordered_scripts(
            project_id="proj_test", preset_id="default"
        )

        # 验证顺序：GLOBAL → PRESET → SCOPED
        scopes = [scope for _, scope in ordered]
        check(
            "执行顺序为 GLOBAL → PRESET → SCOPED",
            scopes == [SCOPE_GLOBAL, SCOPE_PRESET, SCOPE_SCOPED],
            f"got {scopes}",
        )

        # 验证实际应用结果
        engine = RegexEngine()
        engine.compile_scripts([s for s, _ in ordered])
        result = engine.apply_to_text("A", placement=PLACEMENT_USER_INPUT)
        check(
            "脚本按顺序链式应用",
            result == "A_SCOPED",
            f"got {result!r}",
        )


def test_jinja2_sandbox_security() -> None:
    """测试 6: Jinja2 沙箱安全。"""
    print("\n=== 测试 6: Jinja2 沙箱安全 ===")

    engine = TemplateEngine()

    # 测试 _ 开头属性被拒绝
    template = "{{ obj.__class__ }}"
    rendered, error = engine.render_template(template, {"obj": "hello"})
    check(
        "_ 开头属性被拒绝",
        error is not None or "__class__" not in rendered,
        f"rendered={rendered!r}, error={error}",
    )

    # 测试 attr 过滤器被禁用
    template = "{{ obj|attr('__class__') }}"
    rendered, error = engine.render_template(template, {"obj": "hello"})
    check(
        "attr 过滤器被禁用",
        error is not None or "__class__" not in rendered,
        f"rendered={rendered!r}, error={error}",
    )


def test_template_rendering() -> None:
    """测试 7: 模板渲染（白名单函数）。"""
    print("\n=== 测试 7: 模板渲染（白名单函数）===")

    with tempfile.TemporaryDirectory() as tmpdir:
        store = VariableStore(storage_path=Path(tmpdir))
        store.setvar("protagonist", "李明", scope=SCOPE_GLOBAL)
        engine = TemplateEngine(variable_store=store)

        # 测试 getvar 函数
        template = "主角是 {{ getvar('protagonist', scope='global') }}"
        rendered, error = engine.render_pre_send(
            template, project_id="", chapter_metadata={}
        )
        check(
            "getvar 函数可用",
            error is None and rendered == "主角是 李明",
            f"rendered={rendered!r}, error={error}",
        )

        # 测试 now 函数
        template = "{{ now(format='%Y') }}"
        rendered, error = engine.render_template(template)
        check(
            "now 函数可用",
            error is None and len(rendered) == 4,
            f"rendered={rendered!r}, error={error}",
        )

        # 测试 word_count 函数
        template = "{{ word_count('hello world') }}"
        rendered, error = engine.render_template(template)
        check(
            "word_count 函数可用",
            error is None and rendered == "11",
            f"rendered={rendered!r}, error={error}",
        )

        # 测试 truncate 函数
        template = "{{ truncate('abcdefghij', length=5) }}"
        rendered, error = engine.render_template(template)
        check(
            "truncate 函数可用",
            error is None and rendered == "abcde...",
            f"rendered={rendered!r}, error={error}",
        )


def test_template_syntax_error() -> None:
    """测试 8: 模板语法错误处理。"""
    print("\n=== 测试 8: 模板语法错误处理 ===")

    engine = TemplateEngine()

    # 语法错误（未闭合的块）
    template = "{% if True %}未闭合"
    rendered, error = engine.render_template(template)
    check(
        "语法错误返回错误信息",
        error is not None,
        f"rendered={rendered!r}, error={error}",
    )
    check(
        "语法错误时返回原文本",
        rendered == template,
        f"rendered={rendered!r}",
    )


def test_variable_scope() -> None:
    """测试 9: 变量作用域（global/project/chapter/cache）。"""
    print("\n=== 测试 9: 变量作用域 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        store = VariableStore(storage_path=Path(tmpdir))

        # global 作用域
        store.setvar("g_var", "global_value", scope="global")
        check(
            "global 作用域读取",
            store.getvar("g_var", scope="global") == "global_value",
        )
        check("global 作用域存在", store.hasvar("g_var", scope="global"))

        # project 作用域
        store.setvar("p_var", "project_value", scope="project", project_id="proj1")
        check(
            "project 作用域读取",
            store.getvar("p_var", scope="project", project_id="proj1")
            == "project_value",
        )

        # chapter 作用域
        chapter_md = {"variables": {}}
        store.setvar("c_var", "chapter_value", scope="chapter", chapter_metadata=chapter_md)
        check(
            "chapter 作用域读取",
            store.getvar("c_var", scope="chapter", chapter_metadata=chapter_md)
            == "chapter_value",
        )
        check(
            "chapter 作用域写入 metadata",
            chapter_md["variables"]["c_var"] == "chapter_value",
        )

        # cache 作用域
        store.setvar("cache_var", "cache_value", scope="cache")
        check(
            "cache 作用域读取",
            store.getvar("cache_var", scope="cache") == "cache_value",
        )

        # delvar
        check(
            "delvar 删除 global 变量",
            store.delvar("g_var", scope="global") is True,
        )
        check(
            "delvar 后变量不存在",
            store.hasvar("g_var", scope="global") is False,
        )

        # 作用域隔离
        check(
            "project 变量不影响 global",
            store.getvar("p_var", scope="global", default="N/A") == "N/A",
        )


def test_st_regex_import_export() -> None:
    """测试 10: ST 正则导入导出（placement 过滤）。"""
    print("\n=== 测试 10: ST 正则导入导出 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        service = RegexService(storage_path=Path(tmpdir))

        # 创建 ST 格式正则 JSON（含未识别 placement）
        import json

        st_data = [
            {
                "id": "st1",
                "scriptName": "测试脚本",
                "findRegex": "/foo/g",
                "replaceString": "bar",
                "trimStrings": [],
                "placement": [1, 2, 5, 3, 6],  # 3 和 6 是未识别的
                "disabled": False,
                "markdownOnly": False,
                "promptOnly": False,
                "runOnEdit": False,
                "substituteRegex": False,
                "minDepth": 0,
                "maxDepth": 0,
                "markupSafety": False,
                "unknownField": "保留未知字段",
            }
        ]

        json_path = Path(tmpdir) / "test_regex.json"
        json_path.write_text(json.dumps(st_data, ensure_ascii=False), encoding="utf-8")

        # 导入
        scripts = service.import_from_st_json(json_path, scope=SCOPE_GLOBAL)
        check("导入成功", len(scripts) == 1, f"got {len(scripts)}")

        if scripts:
            script = scripts[0]
            # 验证 placement 过滤（3 和 6 被过滤）
            check(
                "placement 过滤未识别值",
                script.placement == [1, 2, 5],
                f"got {script.placement}",
            )
            # 验证未识别字段保留
            check(
                "未识别字段保留",
                script.raw_st_fields.get("unknownField") == "保留未知字段",
                f"got {script.raw_st_fields}",
            )

        # 导出
        export_path = Path(tmpdir) / "exported.json"
        service.export_scope_to_st_json(export_path, scope=SCOPE_GLOBAL)
        exported = json.loads(export_path.read_text(encoding="utf-8"))
        check("导出成功", len(exported) == 1, f"got {len(exported)}")
        if exported:
            check(
                "导出含未识别字段",
                exported[0].get("unknownField") == "保留未知字段",
                f"got {exported[0]}",
            )


def test_trim_strings() -> None:
    """测试 11: trimStrings 裁剪。"""
    print("\n=== 测试 11: trimStrings 裁剪 ===")

    # 基本裁剪
    result = apply_trim_strings("hello[TRIM]world[TRIM]", ["[TRIM]"])
    check(
        "trimStrings 裁剪子串",
        result == "helloworld",
        f"got {result!r}",
    )

    # 多个 trim 字符串
    result = apply_trim_strings("aXXbYYc", ["XX", "YY"])
    check(
        "trimStrings 多个子串",
        result == "abc",
        f"got {result!r}",
    )

    # 空 trim_strings
    result = apply_trim_strings("hello", [])
    check("trimStrings 空列表", result == "hello", f"got {result!r}")

    # 在正则脚本中应用（前后都裁剪）
    script = RegexScript(
        id="trim_test",
        scriptName="裁剪测试",
        findRegex="/world/g",
        replaceString="[TRIM]earth[TRIM]",
        trimStrings=["[TRIM]"],
        placement=[PLACEMENT_USER_INPUT],
    )
    engine = RegexEngine()
    engine.compile_scripts([script])
    result = engine.apply_to_text("hello world", placement=PLACEMENT_USER_INPUT)
    check(
        "trimStrings 在脚本中前后裁剪",
        result == "hello earth",
        f"got {result!r}",
    )


def test_depth_filter() -> None:
    """测试 12: depth 过滤。"""
    print("\n=== 测试 12: depth 过滤 ===")

    # minDepth=1, maxDepth=2 的脚本只对 depth 1-2 生效
    script = RegexScript(
        id="depth_test",
        scriptName="深度过滤",
        findRegex="/X/g",
        replaceString="Y",
        placement=[PLACEMENT_USER_INPUT],
        minDepth=1,
        maxDepth=2,
    )
    engine = RegexEngine()
    engine.compile_scripts([script])

    # depth=0（最新消息）：不应用
    result = engine.apply_to_text("X", placement=PLACEMENT_USER_INPUT, depth=0)
    check("depth=0 不应用", result == "X", f"got {result!r}")

    # depth=1：应用
    result = engine.apply_to_text("X", placement=PLACEMENT_USER_INPUT, depth=1)
    check("depth=1 应用", result == "Y", f"got {result!r}")

    # depth=2：应用
    result = engine.apply_to_text("X", placement=PLACEMENT_USER_INPUT, depth=2)
    check("depth=2 应用", result == "Y", f"got {result!r}")

    # depth=3：不应用
    result = engine.apply_to_text("X", placement=PLACEMENT_USER_INPUT, depth=3)
    check("depth=3 不应用", result == "X", f"got {result!r}")


def test_placement_filter() -> None:
    """测试 13: placement 过滤。"""
    print("\n=== 测试 13: placement 过滤 ===")

    script = RegexScript(
        id="placement_test",
        scriptName="placement 过滤",
        findRegex="/foo/g",
        replaceString="bar",
        placement=[PLACEMENT_USER_INPUT],  # 仅 USER_INPUT
    )
    engine = RegexEngine()
    engine.compile_scripts([script])

    # USER_INPUT：应用
    result = engine.apply_to_text("foo", placement=PLACEMENT_USER_INPUT)
    check("USER_INPUT 应用", result == "bar", f"got {result!r}")

    # AI_OUTPUT：不应用
    result = engine.apply_to_text("foo", placement=PLACEMENT_AI_OUTPUT)
    check("AI_OUTPUT 不应用", result == "foo", f"got {result!r}")

    # WORLD_INFO：不应用
    result = engine.apply_to_text("foo", placement=PLACEMENT_WORLD_INFO)
    check("WORLD_INFO 不应用", result == "foo", f"got {result!r}")


def test_template_post_receive_setvar() -> None:
    """测试 14: 接收后模板渲染（setvar）。"""
    print("\n=== 测试 14: 接收后模板渲染（setvar）===")

    with tempfile.TemporaryDirectory() as tmpdir:
        store = VariableStore(storage_path=Path(tmpdir))
        engine = TemplateEngine(variable_store=store)

        chapter_md = {"variables": {}}
        # 模板中设置变量并输出
        template = "{% set _ = setvar('counter', 42, scope='chapter') %}计数已设置"
        rendered, error = engine.render_post_receive(
            template, project_id="", chapter_metadata=chapter_md
        )
        check(
            "post_receive 渲染成功",
            error is None and rendered == "计数已设置",
            f"rendered={rendered!r}, error={error}",
        )
        # 验证变量已设置
        check(
            "setvar 在模板中生效",
            store.getvar("counter", scope="chapter", chapter_metadata=chapter_md)
            == 42,
            f"got {store.getvar('counter', scope='chapter', chapter_metadata=chapter_md)}",
        )


def test_apply_to_messages() -> None:
    """测试 15: 对 messages 数组应用正则。"""
    print("\n=== 测试 15: 对 messages 数组应用正则 ===")

    script = RegexScript(
        id="msg_test",
        scriptName="消息正则",
        findRegex="/foo/g",
        replaceString="bar",
        placement=[PLACEMENT_USER_INPUT],
        minDepth=0,
        maxDepth=1,  # 仅最新和倒数第二条
    )
    engine = RegexEngine()
    engine.compile_scripts([script])

    messages = [
        {"role": "user", "content": "foo"},
        {"role": "user", "content": "foo"},
        {"role": "user", "content": "foo"},
    ]
    result = engine.apply_to_messages(messages, placement=PLACEMENT_USER_INPUT)

    # depth=0（最后一条）和 depth=1（倒数第二条）应用，depth=2（第一条）不应用
    check(
        "最后一条消息应用正则",
        result[2]["content"] == "bar",
        f"got {result[2]['content']!r}",
    )
    check(
        "倒数第二条消息应用正则",
        result[1]["content"] == "bar",
        f"got {result[1]['content']!r}",
    )
    check(
        "第一条消息不应用正则",
        result[0]["content"] == "foo",
        f"got {result[0]['content']!r}",
    )


def test_apply_single_script() -> None:
    """测试 16: 单脚本测试（返回匹配位置）。"""
    print("\n=== 测试 16: 单脚本测试（返回匹配位置）===")

    script = RegexScript(
        id="single_test",
        scriptName="单脚本测试",
        findRegex="/foo/g",
        replaceString="bar",
        placement=[PLACEMENT_USER_INPUT],
    )
    engine = RegexEngine()
    result, matches = engine.apply_single_script(script, "foo and foo")

    check("替换结果正确", result == "bar and bar", f"got {result!r}")
    check("匹配位置数量正确", len(matches) == 2, f"got {matches}")
    if len(matches) == 2:
        check(
            "第一个匹配位置",
            matches[0] == (0, 3),
            f"got {matches[0]}",
        )
        check(
            "第二个匹配位置",
            matches[1] == (8, 11),
            f"got {matches[1]}",
        )


def main() -> int:
    """运行所有测试。"""
    print("=" * 60)
    print("NovelForge M3 里程碑验证测试")
    print("=" * 60)

    test_parse_find_regex()
    test_compile_flags()
    test_convert_replace_string()
    test_regex_compile_error()
    test_script_execution_order()
    test_jinja2_sandbox_security()
    test_template_rendering()
    test_template_syntax_error()
    test_variable_scope()
    test_st_regex_import_export()
    test_trim_strings()
    test_depth_filter()
    test_placement_filter()
    test_template_post_receive_setvar()
    test_apply_to_messages()
    test_apply_single_script()

    print("\n" + "=" * 60)
    print(f"测试结果: {_passed} 通过, {_failed} 失败")
    print("=" * 60)

    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
