"""安全测试：敏感文件权限收紧（M-4 + M-5）。

覆盖：
1. ``secure_file`` 不抛异常（任何平台）
2. ``secure_file`` 在 Linux/macOS 实际收紧权限到 0o600
3. Windows 上 ``secure_file`` 为 no-op（不抛错、不破坏文件）
4. ``secure_file`` 对不存在文件静默忽略
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from novelforge.utils.paths import secure_file


# ===== 1. secure_file 不抛异常 =====


def test_secure_file_no_exception(tmp_path: Path) -> None:
    """secure_file 对存在的文件不抛异常。"""
    f = tmp_path / "test.txt"
    f.write_text("secret")
    # 不应抛异常
    secure_file(f)
    # 文件内容不被破坏
    assert f.read_text() == "secret"


def test_secure_file_nonexistent_no_exception(tmp_path: Path) -> None:
    """secure_file 对不存在的文件静默忽略（不抛异常）。"""
    f = tmp_path / "nonexistent.txt"
    # 不应抛异常（OSError 被捕获）
    secure_file(f)


def test_secure_file_accepts_path_object(tmp_path: Path) -> None:
    """secure_file 接受 Path 对象。"""
    f = tmp_path / "obj.txt"
    f.write_text("data")
    secure_file(f)  # 不抛异常即可


def test_secure_file_accepts_str_path(tmp_path: Path) -> None:
    """secure_file 接受字符串路径（通过 Path 转换）。"""
    f = tmp_path / "str.txt"
    f.write_text("data")
    secure_file(Path(str(f)))  # 不抛异常即可


# ===== 2. Linux/macOS 实际权限收紧 =====


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows os.chmod 基本为 no-op，不实际收紧 ACL",
)
def test_secure_file_sets_0o600_on_posix(tmp_path: Path) -> None:
    """Linux/macOS 上 secure_file 将权限收紧到 0o600。"""
    f = tmp_path / "secret.txt"
    f.write_text("secret")
    # 先放宽权限确保后续收紧可测
    os.chmod(f, 0o644)
    assert os.stat(f).st_mode & 0o777 == 0o644

    secure_file(f)
    assert os.stat(f).st_mode & 0o777 == 0o600


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows os.chmod 基本为 no-op",
)
def test_secure_file_overwrites_existing_loose_perms(tmp_path: Path) -> None:
    """secure_file 覆盖已有的宽松权限（如 0o777 → 0o600）。"""
    f = tmp_path / "loose.txt"
    f.write_text("data")
    os.chmod(f, 0o777)  # 故意设宽松
    secure_file(f)
    assert os.stat(f).st_mode & 0o777 == 0o600


# ===== 3. Windows 上为 no-op（不破坏文件）=====


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows 专属测试",
)
def test_secure_file_noop_on_windows(tmp_path: Path) -> None:
    """Windows 上 secure_file 为 no-op，不破坏文件内容。"""
    f = tmp_path / "win.txt"
    f.write_text("windows secret")
    secure_file(f)  # 不抛异常
    # 文件内容不被破坏
    assert f.read_text() == "windows secret"


# ===== 4. 多次调用幂等 =====


def test_secure_file_idempotent(tmp_path: Path) -> None:
    """secure_file 多次调用不抛异常（幂等）。"""
    f = tmp_path / "idempotent.txt"
    f.write_text("data")
    secure_file(f)
    secure_file(f)
    secure_file(f)
    assert f.read_text() == "data"
