"""把 dist 里的 exe 目录压成发布 zip（条目名用正斜杠）。

位置：
    仓库根目录 tools/ 下（开发工具，不进发布包）。

为什么要这个脚本：
    Windows 自带的 `Compress-Archive` 会把 zip 条目名写成**反斜杠**
    （`UWE-…\\mods\\…`）——Windows 资源管理器能打开，但 Linux / macOS 的 unzip
    会解出带反斜杠的怪文件名。Python 的 zipfile 写正斜杠，所有平台都正常。

用法（在仓库根执行；先用 UWE.spec 打包出目录）：
    python tools/package_exe_zip.py                 # 自动挑最新的 UWE-*-win64
    python tools/package_exe_zip.py --name UWE-0.0.0-demo-win64

不进包的东西：
    `logs/`（试玩日志）、`saves/`（试玩存档）、`__pycache__/`（字节码缓存）——
    它们都是**运行产物**，可能出现在打包目录里（在那份目录里跑过一次游戏就会有），
    发布包一律不带。以前这几个目录都不存在时看不出问题，2026-09-26 那次试玩后的
    存档就混进了 zip（批次 71）。
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

# 仓库根目录（本文件在 tools/ 下）。
REPO_ROOT = Path(__file__).resolve().parent.parent
# 产物目录：与 tools/package_source.py 共用 dist/。
DIST_ROOT = REPO_ROOT / "dist"

# 运行产物目录（相对包根的第一段）：一律不进发布包。
EXCLUDED_DIRS = frozenset({"logs", "saves", "__pycache__"})


def _latest_package_name() -> str:
    """取 dist 里最新的 `UWE-*-win64` 目录名。"""
    candidates = [path for path in DIST_ROOT.glob("UWE-*-win64") if path.is_dir()]
    if not candidates:
        raise SystemExit(f"dist 里没有 UWE-*-win64 目录：{DIST_ROOT}")
    return max(candidates, key=lambda path: path.stat().st_mtime).name


def main(argv: list[str] | None = None) -> int:
    """命令行入口：压包并汇报。"""
    parser = argparse.ArgumentParser(description="把 dist/<包名> 压成同名 zip（正斜杠条目名）")
    parser.add_argument("--name", default="", help="dist 里的目录名；缺省取最新的 UWE-*-win64")
    args = parser.parse_args(argv)

    name = args.name or _latest_package_name()
    root = DIST_ROOT / name
    if not root.is_dir():
        raise SystemExit(f"找不到目录：{root}")

    zip_path = DIST_ROOT / f"{name}.zip"
    def _keep(path: Path) -> bool:
        """这个文件要不要进包：运行产物目录下的一律不要。"""
        parts = path.relative_to(root).parts
        return not (parts and parts[0] in EXCLUDED_DIRS)

    files = sorted(path for path in root.rglob("*") if path.is_file() and _keep(path))
    skipped = [path for path in root.rglob("*") if path.is_file() and not _keep(path)]
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=f"{name}/{path.relative_to(root).as_posix()}")

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"打包完成：{zip_path.relative_to(REPO_ROOT).as_posix()}"
          f"（{len(files)} 个文件，{size_mb:.2f} MB，条目名全是正斜杠）")
    if skipped:
        print(f"已过滤 {len(skipped)} 个运行产物文件（logs/ saves/ __pycache__/）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
