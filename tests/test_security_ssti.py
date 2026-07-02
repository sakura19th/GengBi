"""安全测试：SSTI 白名单收紧（M-1）。

覆盖：
1. ``POST_RECEIVE_WHITELIST`` 仅含 9 个纯操作函数（无数据读取函数）
2. ``_build_post_receive_context`` 返回的上下文不含数据读取函数
3. ``render_post_receive`` 渲染含 ``{{ get_chapters() }}`` 的 AI 输出时不执行（报错或字面输出）
4. ``render_pre_send`` 保留完整白名单（可调用 get_chapters 等）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from novelforge.core.template_engine import (
    POST_RECEIVE_WHITELIST,
    WHITELIST_FUNCTION_NAMES,
    TemplateEngine,
)


# ===== 1. POST_RECEIVE_WHITELIST 常量正确性 =====


def test_post_receive_whitelist_is_subset_of_full_whitelist() -> None:
    """POST_RECEIVE_WHITELIST 必须是 WHITELIST_FUNCTION_NAMES 的子集。"""
    assert POST_RECEIVE_WHITELIST <= WHITELIST_FUNCTION_NAMES


def test_post_receive_whitelist_excludes_data_reading_functions() -> None:
    """POST_RECEIVE_WHITELIST 不含任何数据读取函数。"""
    excluded = {
        "get_chapter", "get_chapters", "get_current_chapter", "get_chapter_count",
        "get_book", "get_protagonist", "get_novel_profile", "get_writing_style",
        "get_context_entries",
    }
    assert excluded.isdisjoint(POST_RECEIVE_WHITELIST), (
        f"post_receive 白名单不应含数据读取函数，但交集为: "
        f"{excluded & POST_RECEIVE_WHITELIST}"
    )


def test_post_receive_whitelist_contains_pure_operation_functions() -> None:
    """POST_RECEIVE_WHITELIST 含 9 个纯操作函数。"""
    expected = {
        "getvar", "setvar", "hasvar", "delvar",
        "regex_apply", "substitute_macros",
        "now", "word_count", "truncate",
    }
    assert POST_RECEIVE_WHITELIST == expected
    assert len(POST_RECEIVE_WHITELIST) == 9


# ===== 2. _build_post_receive_context 不含数据读取函数 =====


def test_post_receive_context_excludes_data_reading_functions() -> None:
    """_build_post_receive_context 返回的上下文不含数据读取函数。"""
    engine = TemplateEngine()
    ctx = engine._build_post_receive_context(
        project_id="proj_x",
        chapters=[{"title": "ch1"}],
        novel_profile={"title": "novel"},
    )
    excluded = [
        "get_chapter", "get_chapters", "get_current_chapter", "get_chapter_count",
        "get_book", "get_protagonist", "get_novel_profile", "get_writing_style",
        "get_context_entries",
    ]
    for name in excluded:
        assert name not in ctx, f"post_receive 上下文不应含 {name}"


def test_post_receive_context_contains_pure_operation_functions() -> None:
    """_build_post_receive_context 返回的上下文含 9 个纯操作函数。"""
    engine = TemplateEngine()
    ctx = engine._build_post_receive_context(project_id="proj_x")
    for name in POST_RECEIVE_WHITELIST:
        assert name in ctx, f"post_receive 上下文应含 {name}"
        assert callable(ctx[name])


def test_pre_send_context_contains_all_whitelist_functions() -> None:
    """_build_default_context（pre_send 用）含完整 WHITELIST_FUNCTION_NAMES。"""
    engine = TemplateEngine()
    ctx = engine._build_default_context(project_id="proj_x")
    for name in WHITELIST_FUNCTION_NAMES:
        assert name in ctx, f"pre_send 上下文应含 {name}"


# ===== 3. render_post_receive 不执行数据读取函数 =====


def test_render_post_receive_get_chapters_not_executed() -> None:
    """含 {{ get_chapters() }} 的 AI 输出经 post_receive 渲染不执行该函数。

    函数未定义时 Jinja2 沙箱会抛 UndefinedError，_render_with_timeout 捕获后
    返回 (原始文本, 错误信息)。断言渲染后文本不含章节数据。
    """
    engine = TemplateEngine()
    # chapters 含敏感数据，若 get_chapters 被执行会外泄到输出
    chapters = [{"title": "SECRET_CHAPTER_TITLE_DO_NOT_LEAK"}]
    ai_output = "{{ get_chapters() }}"
    rendered, err = engine.render_post_receive(
        ai_output,
        project_id="proj_x",
        chapters=chapters,
    )
    # 不论是报错返回原文还是字面输出，都不应泄露 SECRET_CHAPTER_TITLE
    assert "SECRET_CHAPTER_TITLE_DO_NOT_LEAK" not in rendered, (
        "post_receive 渲染泄露了章节数据，SSTI 防护失效"
    )


def test_render_post_receive_get_novel_profile_not_executed() -> None:
    """含 {{ get_novel_profile() }} 的 AI 输出不泄露 novel_profile。"""
    engine = TemplateEngine()
    novel_profile = {"title": "SECRET_NOVEL_TITLE_DO_NOT_LEAK"}
    ai_output = "{{ get_novel_profile() }}"
    rendered, err = engine.render_post_receive(
        ai_output,
        project_id="proj_x",
        novel_profile=novel_profile,
    )
    assert "SECRET_NOVEL_TITLE_DO_NOT_LEAK" not in rendered


def test_render_post_receive_get_book_not_executed() -> None:
    """含 {{ get_book() }} 的 AI 输出不泄露书名。"""
    engine = TemplateEngine()
    novel_profile = {"title": "SECRET_BOOK_NAME"}
    ai_output = "{{ get_book() }}"
    rendered, err = engine.render_post_receive(
        ai_output,
        project_id="proj_x",
        novel_profile=novel_profile,
    )
    assert "SECRET_BOOK_NAME" not in rendered


def test_render_post_receive_setvar_still_works() -> None:
    """post_receive 仍可调用 setvar（保留变量回写功能）。

    setvar 是 Jinja2 上下文函数，调用签名 setvar(name, value, scope=...)；
    默认 chapter 作用域需 chapter_metadata，这里用 cache 作用域便于验证。
    """
    engine = TemplateEngine()
    # setvar(name, value, scope='cache') 写入 cache 作用域
    ai_output = "{{ setvar('myvar', 'hello', 'cache') }}done"
    rendered, err = engine.render_post_receive(ai_output, project_id="proj_x")
    assert "done" in rendered
    # setvar 应已写入 cache 作用域变量
    val = engine.variable_store.getvar("myvar", scope="cache")
    assert val == "hello"


def test_render_post_receive_getvar_still_works() -> None:
    """post_receive 仍可调用 getvar（保留变量读取功能）。"""
    engine = TemplateEngine()
    # 先用 cache 作用域写入变量
    engine.variable_store.setvar("myvar", "world", scope="cache")
    ai_output = "{{ getvar('myvar', 'cache') }}"
    rendered, err = engine.render_post_receive(ai_output, project_id="proj_x")
    assert "world" in rendered


# ===== 4. render_pre_send 保留完整白名单 =====


def test_render_pre_send_can_call_get_chapters() -> None:
    """pre_send 可调用 get_chapters（用户预设受信任场景）。"""
    engine = TemplateEngine()
    chapters = [{"title": "PRE_SEND_ALLOWED_TITLE"}]
    text = "{{ get_chapters() }}"
    rendered, err = engine.render_pre_send(
        text,
        project_id="proj_x",
        chapters=chapters,
    )
    # pre_send 应执行 get_chapters 返回章节列表，含标题
    assert "PRE_SEND_ALLOWED_TITLE" in rendered
