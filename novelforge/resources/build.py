#!/usr/bin/env python3
"""赓笔 打包辅助脚本。

用法：
    python -m novelforge.resources.build        # 当前平台
    python -m novelforge.resources.build --win  # Windows 交叉打包（需在 Windows 上运行）

说明：
- 调用 PyInstaller 按 build.spec 配置打包
- 输出 dist/赓笔 可执行文件
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


def main() -> None:
    """执行打包。"""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean", "-y",
        str(ROOT / "novelforge" / "resources" / "build.spec"),
    ]
    subprocess.run(cmd, check=True, cwd=ROOT)


if __name__ == "__main__":
    main()
