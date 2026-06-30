"""RegexService 默认正则注入测试。

覆盖 ensure_default_scripts_exist：
1. 首次调用（global.json 不存在）→ 注入 4 条默认脚本
2. global.json 为空数组 → 注入 4 条默认脚本
3. global.json 已含脚本 → 不覆盖
4. 注入后脚本可被 get_ordered_scripts 加载且含 NF-思维链隐藏
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from novelforge.services.regex_service import RegexService, SCOPE_GLOBAL


@pytest.fixture
def regex_service(tmp_path: Path) -> RegexService:
    """创建临时正则服务。"""
    return RegexService(storage_path=tmp_path)


def _global_path(service: RegexService) -> Path:
    return service._get_scope_file_path(SCOPE_GLOBAL)


def test_ensure_default_scripts_injects_when_absent(
    regex_service: RegexService,
) -> None:
    """global.json 不存在时，注入 4 条默认脚本。"""
    assert not _global_path(regex_service).exists()

    scripts = regex_service.ensure_default_scripts_exist()

    assert len(scripts) == 4
    names = {s.scriptName for s in scripts}
    assert "NF-思维链隐藏" in names
    assert _global_path(regex_service).exists()


def test_ensure_default_scripts_injects_when_empty(
    regex_service: RegexService,
) -> None:
    """global.json 为空数组时，注入默认脚本。"""
    regex_service.ensure_global_scripts_exist()
    assert _global_path(regex_service).exists()

    scripts = regex_service.ensure_default_scripts_exist()

    assert len(scripts) == 4
    thinking = next(
        s for s in scripts if s.scriptName == "NF-思维链隐藏"
    )
    assert 2 in thinking.placement  # AI_OUTPUT


def test_ensure_default_scripts_does_not_overwrite_existing(
    regex_service: RegexService,
) -> None:
    """global.json 已含脚本时，不覆盖。"""
    # 写入一条自定义脚本
    from novelforge.models.regex import RegexScript

    custom = RegexScript(
        id="custom_test",
        scriptName="自定义测试",
        findRegex="/foo/g",
        replaceString="bar",
        placement=[2],
    )
    regex_service.save_scripts([custom], SCOPE_GLOBAL)

    scripts = regex_service.ensure_default_scripts_exist()

    # 应返回原有的 1 条，不注入默认
    assert len(scripts) == 1
    assert scripts[0].scriptName == "自定义测试"


def test_injected_scripts_loadable_via_get_ordered(
    regex_service: RegexService,
) -> None:
    """注入后可通过 get_ordered_scripts 加载，含 NF-思维链隐藏。"""
    regex_service.ensure_default_scripts_exist()

    ordered = regex_service.get_ordered_scripts(
        project_id="",
        preset_id="",
        include_disabled=False,
    )

    scripts = [s for s, _ in ordered]
    names = {s.scriptName for s in scripts}
    assert "NF-思维链隐藏" in names
    assert len(scripts) == 4
