"""安全测试：路径穿越防护（H-2a/b/c）。

覆盖：
1. ``validate_id`` 拒绝含路径分隔符 / ``..`` / 空字节 / 空串 / 控制字符的 ID
2. ``validate_id`` 通过内置 ID 前缀（proj_/ep_/ch_/wb_/nf_regex_）
3. pydantic 模型层 ``_validate_path_id`` field_validator 拒绝路径字符
4. 服务层入口（storage/worldbook/regex）拒绝非法 ID
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from novelforge.utils.ids import validate_id


# ===== 1. validate_id 拒绝非法值 =====


@pytest.mark.parametrize(
    "bad_id,desc",
    [
        ("../config", "相对路径穿越"),
        ("a/b", "正斜杠"),
        ("a\\b", "反斜杠"),
        (".hidden", "前导点号"),
        ("", "空串"),
        ("a\x00b", "空字节"),
        ("..", "点号组合"),
        ("a..b", "中间点号组合"),
        ("a b", "空格"),
        ("a\tb", "制表符"),
        ("a/b/c", "多级路径"),
        ("..\\..\\config", "Windows 风格穿越"),
    ],
)
def test_validate_id_rejects_invalid(bad_id: str, desc: str) -> None:
    """validate_id 拒绝各类路径穿越变体。"""
    with pytest.raises(ValueError, match="非法"):
        validate_id(bad_id)


# ===== 2. validate_id 通过合法 ID =====


@pytest.mark.parametrize(
    "good_id",
    [
        "proj_abc123def456",
        "ep_a1b2c3d4e5f6",
        "ch_abcdef123456",
        "wb_000011112222",
        "nf_regex_gb001",
        "default",
        "abc-123_xyz",
        "A1B2C3",
        "nf_regex_test",
    ],
)
def test_validate_id_accepts_valid(good_id: str) -> None:
    """validate_id 通过所有内置 ID 前缀与合法字符。"""
    assert validate_id(good_id) == good_id


def test_validate_id_custom_field_name() -> None:
    """validate_id 错误信息含自定义字段名。"""
    with pytest.raises(ValueError, match="preset_id"):
        validate_id("../evil", "preset_id")


# ===== 3. pydantic 模型层 _validate_path_id 拒绝路径字符 =====


def test_project_model_rejects_path_id() -> None:
    """Project 模型拒绝含路径字符的 id/preset_id/worldbook_id。"""
    from novelforge.models.project import Project

    # id 字段
    with pytest.raises(ValidationError):
        Project(id="../evil", name="t")
    # preset_id 字段
    with pytest.raises(ValidationError):
        Project(id="proj_x", name="t", preset_id="../evil")
    # worldbook_id 字段
    with pytest.raises(ValidationError):
        Project(id="proj_x", name="t", worldbook_id="../evil")


def test_chapter_model_rejects_path_id() -> None:
    """Chapter 模型拒绝含路径字符的 id/project_id。"""
    from novelforge.models.chapter import Chapter

    with pytest.raises(ValidationError):
        Chapter(id="../evil", project_id="proj_x", index=0, title="t", content="")
    with pytest.raises(ValidationError):
        Chapter(id="ch_x", project_id="../evil", index=0, title="t", content="")


def test_preset_model_rejects_path_id() -> None:
    """WritingPreset 模型拒绝含路径字符的 id。"""
    from novelforge.models.preset import WritingPreset

    with pytest.raises(ValidationError):
        WritingPreset(id="../evil", name="t")


def test_regex_model_rejects_path_id() -> None:
    """RegexScript 模型拒绝含路径字符的 id。"""
    from novelforge.models.regex import RegexScript

    with pytest.raises(ValidationError):
        RegexScript(id="../evil", scriptName="t", findRegex="/x/", replaceString="")


def test_worldbook_model_rejects_path_id() -> None:
    """WorldBook 模型拒绝含路径字符的 id。"""
    from novelforge.models.worldbook import WorldBook

    with pytest.raises(ValidationError):
        WorldBook(id="../evil", name="t")


# ===== 4. 服务层入口拒绝非法 ID（纵深防御，已先被模型层拦截，但服务层独立校验也应生效）=====


def test_storage_get_chapter_file_path_rejects_traversal(tmp_path: Path) -> None:
    """storage.get_chapter_file_path 模块函数入口 validate_id 拦截路径穿越。"""
    from novelforge.core.storage import get_chapter_file_path

    with pytest.raises(ValueError, match="project_id"):
        get_chapter_file_path(tmp_path, project_id="../evil", chapter_id="ch_x")
    with pytest.raises(ValueError, match="chapter_id"):
        get_chapter_file_path(tmp_path, project_id="proj_x", chapter_id="../evil")


def test_storage_get_preset_file_path_rejects_traversal(tmp_path: Path) -> None:
    """storage.get_preset_file_path 模块函数入口 validate_id 拦截路径穿越。"""
    from novelforge.core.storage import get_preset_file_path

    with pytest.raises(ValueError, match="preset_id"):
        get_preset_file_path(tmp_path, preset_id="../evil")


def test_worldbook_service_rejects_traversal(tmp_path: Path) -> None:
    """WorldBookService load/save/delete 拦截非法 wb_id。"""
    from novelforge.services.worldbook_service import WorldBookService

    svc = WorldBookService(storage_path=tmp_path)
    with pytest.raises(ValueError, match="worldbook_id"):
        svc.load_worldbook("../evil")
    with pytest.raises(ValueError, match="worldbook_id"):
        svc.delete_worldbook("../evil")
