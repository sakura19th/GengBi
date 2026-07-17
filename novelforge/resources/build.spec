# -*- mode: python ; coding: utf-8 -*-
"""赓笔 PyInstaller 打包配置。

用法：
    python -m novelforge.resources.build
    # 或
    pyinstaller --clean -y novelforge/resources/build.spec

说明：
- 通过 collect_data_files 收集 novelforge.resources 包的数据文件
  （主题 QSS、默认预设、提取提示词、Agent 阶段提示词等）
- hiddenimports 包含 tiktoken_ext.openai_public，避免打包后
  OpenAI 模型 token 计数因模块缺失而回退为估算
- 单文件模式（onefile），产物命名为 GengBi_v{版本号}
  （优先读环境变量 GENGBI_BUILD_NAME，由 build.py 注入）
"""
import os
import re

from PyInstaller.utils.hooks import collect_data_files

# spec 文件位于 <项目根>/novelforge/resources/，向上两级即项目根
PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, '..', '..'))


def _resolve_app_name() -> str:
    """解析产物名：环境变量 > 解析 __init__.py > 回退。"""
    env_name = os.environ.get("GENGBI_BUILD_NAME", "").strip()
    if env_name:
        return env_name
    init_path = os.path.join(PROJECT_ROOT, "novelforge", "__init__.py")
    try:
        with open(init_path, encoding="utf-8") as f:
            text = f.read()
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
        if match:
            return f"GengBi_v{match.group(1)}"
    except OSError:
        pass
    return "GengBi"


APP_NAME = _resolve_app_name()

# 收集 novelforge.resources 包的数据文件（themes/defaults 等资源）
datas = collect_data_files('novelforge.resources')

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'main.py')],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'tiktoken',
        'tiktoken_ext',
        'tiktoken_ext.openai_public',
        'regex',
        'jinja2',
        'aiosqlite',
        'cryptography',
        'PySide6',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,
    icon=None,
)
