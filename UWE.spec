# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（one-folder，Windows）。

位置：仓库根目录（打包配置只有这一份）。

用法（在仓库根执行，需要先装好 pyinstaller）：
    python -m PyInstaller --noconfirm UWE.spec

产物：
    dist/UWE-<版本>-<演示标签>-win64/
        UWE.exe        双击运行
        _internal/     引擎代码、pygame、图标、文字（PyInstaller 收进来的）
        mods/          Mod 文件夹（复制进来的，玩家可以改）
        modules/       模块文件夹（复制进来的）
        README.md
        saves/ logs/   运行时自己生成

为什么复制在 spec 里做：打包是"一条命令出完整可玩目录"，省得每次手工搬；
`mods/`、`modules/` 故意**不打进 exe**，它们就是给玩家和作者改的普通文件夹。
"""

import re
import shutil
from pathlib import Path

ROOT = Path(SPECPATH)

# 包名跟 engine/src/core/version.py 走，升级版本号不用改这里。
_version_source = (ROOT / "engine" / "src" / "core" / "version.py").read_text(encoding="utf-8")
_version = re.search(r'^ENGINE_VERSION\s*=\s*"([^"]+)"', _version_source, re.M).group(1)
_demo = re.search(r'^ENGINE_DEMO_LABEL\s*=\s*"([^"]+)"', _version_source, re.M)
APP_NAME = "-".join(["UWE", _version, _demo.group(1) if _demo else "", "win64"]).replace("--", "-")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT / "engine" / "src")],
    binaries=[],
    # 引擎自带的资源（图标、文字 JSON）：放进 _internal/shell/assets，
    # 与 shell/resources.py 的 default_asset_root()（_MEIPASS/shell/assets）对齐。
    datas=[(str(ROOT / "engine" / "src" / "shell" / "assets"), "shell/assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 排掉引擎没用到的东西：pygame 的 hook 会顺手把 numpy（surfarray 用）等收进来，
    # 我们只用 draw / font / image / mixer，不收能让发布包小一半。
    excludes=["tkinter", "numpy", "yaml", "charset_normalizer"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UWE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 游戏窗口：不弹黑色控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "engine" / "src" / "shell" / "assets" / "brand" / "icon.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

# ---- 打包后：把"要跟着 exe 走"的东西复制到它旁边 -------------------------

_target = Path(DISTPATH) / APP_NAME

for _name in ("mods", "modules"):
    # 忽略 Python 缓存：跑过测试之后再打包，mods/ modules/ 里会留 __pycache__，
    # 复制进发布包既没用又难看。
    shutil.copytree(
        ROOT / _name,
        _target / _name,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )

for _name in ("README.md",):
    _source = ROOT / _name
    if _source.exists():
        shutil.copy2(_source, _target / _name)
