#!/usr/bin/env python3
"""赓笔 打包辅助脚本。

用法：
    python -m novelforge.resources.build

说明：
- 调用 PyInstaller 按 build.spec 配置打包
- 产物名由 novelforge.__version__ 生成：dist/GengBi_v{版本号}.exe
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _read_version() -> str:
    """读取版本号（优先 import，失败则解析 __init__.py）。"""
    try:
        from novelforge import __version__

        return str(__version__)
    except Exception:
        init_path = ROOT / "novelforge" / "__init__.py"
        text = init_path.read_text(encoding="utf-8")
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
        if not match:
            raise RuntimeError(f"无法从 {init_path} 解析 __version__") from None
        return match.group(1)


def _app_name(version: str) -> str:
    """生成打包产物名：GengBi_v0.2.12。"""
    return f"GengBi_v{version}"


def main() -> None:
    """执行打包。"""
    if not (ROOT / "main.py").is_file():
        print(f"错误：未找到项目入口 main.py（ROOT={ROOT}）", file=sys.stderr)
        raise SystemExit(1)

    spec = ROOT / "novelforge" / "resources" / "build.spec"
    if not spec.is_file():
        print(f"错误：未找到打包配置 {spec}", file=sys.stderr)
        raise SystemExit(1)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "错误：未安装 PyInstaller。请先执行：pip install pyinstaller",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    version = _read_version()
    app_name = _app_name(version)
    # 供 build.spec 读取，避免在 spec 内 import 包引发环境差异
    env = os.environ.copy()
    env["GENGBI_BUILD_NAME"] = app_name

    print(f"开始打包：{app_name}")
    print(f"项目根目录：{ROOT}")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "-y",
        str(spec),
    ]
    result = subprocess.run(cmd, cwd=ROOT, env=env)
    if result.returncode != 0:
        print("打包失败。", file=sys.stderr)
        raise SystemExit(result.returncode)

    suffix = ".exe" if sys.platform.startswith("win") else ""
    out = ROOT / "dist" / f"{app_name}{suffix}"
    if out.is_file():
        print(f"打包完成：{out}")
    else:
        print(f"打包完成，请检查 dist/ 目录（预期：{out.name}）")


if __name__ == "__main__":
    main()
