"""源码发布包打包器（白名单制）。

位置：
    仓库根目录 tools/ 下。**它自己不在白名单里**，所以不会被打进发布包。

职责：
    只把"跑起来真正需要"的东西复制进 dist/<包名>-src/，再压成同名 zip：

        main.py            启动入口
        engine/src         引擎（含 shell/assets 的品牌与文字资源）
        modules            模块（玩法逻辑）
        mods               示例 Mod
        README.md          说明（没有就跳过并提示）
        requirements.txt   依赖（没有就跳过并提示）
        UWE.spec           PyInstaller 打包配置（想自己打 exe 的人需要）

    其余目录/文件（plans/ tests/ tools/ run_tests.py saves/ logs/ .vscode/
    .roo/ dist/ …）一律不在白名单里，因此不会出现在发布包中。
    这样"该排除什么"永远由白名单说了算，不用维护一串排除规则。

用法（在仓库根执行）：
    python tools/package_source.py                # 文件夹 + zip
    python tools/package_source.py --dry-run      # 只打印清单，不写盘
    python tools/package_source.py --no-zip       # 只要文件夹
    python tools/package_source.py --keep-target  # 不先清空旧产物目录
"""

from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from pathlib import Path

# 仓库根目录（本文件在 tools/ 下，往上一层就是根）。
REPO_ROOT = Path(__file__).resolve().parent.parent

# 发布包文件名前缀（版本号后面的小写 slug，不要中文与括号，免得压缩包在各平台出乱码）。
PACKAGE_SLUG = "UWE"

# 白名单：**运行包只认这一份清单**。加东西先加到这里，别去写排除规则。
WHITELIST = (
    "main.py",
    "engine/src",
    "modules",
    "mods",
    "README.md",
    "requirements.txt",
    "UWE.spec",
)

# 白名单里也必须清掉的临时文件（测试/运行会生成）。
IGNORE_DIRS = ("__pycache__", ".git", ".mypy_cache", ".pytest_cache")
IGNORE_SUFFIXES = (".pyc", ".pyo", ".log")
IGNORE_NAMES = (".DS_Store", "Thumbs.db")

# 这几项缺了就打不成运行包（README / requirements / UWE.spec 允许以后再补）。
REQUIRED_ENTRIES = ("main.py", "engine/src", "modules", "mods")


def _read_engine_info(repo_root: Path) -> tuple[str, str]:
    """从唯一的版本来源 engine/src/core/version.py 里读版本与演示标签。"""
    source = repo_root / "engine" / "src" / "core" / "version.py"
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"读不到版本文件 {source}：{exc}")
    version = re.search(r'^ENGINE_VERSION\s*=\s*"([^"]+)"', text, re.M)
    if version is None:
        raise SystemExit(f"没在 {source} 里找到 ENGINE_VERSION")
    demo = re.search(r'^ENGINE_DEMO_LABEL\s*=\s*"([^"]+)"', text, re.M)
    return version.group(1), (demo.group(1) if demo else "")


def _package_name(version: str, demo: str) -> str:
    """包名：UWE-0.0.0-demo-src（没有演示标签时省掉那一段）。"""
    parts = [PACKAGE_SLUG, version]
    if demo:
        parts.append(demo)
    parts.append("src")
    return "-".join(parts)


def _is_ignored(path: Path) -> bool:
    """白名单内的路径是否属于必须清掉的临时文件。"""
    if any(part in IGNORE_DIRS for part in path.parts):
        return True
    if path.name in IGNORE_NAMES:
        return True
    return path.suffix.lower() in IGNORE_SUFFIXES


def _list_entry(repo_root: Path, entry: str) -> list[str]:
    """列出一个白名单条目会带上哪些文件（相对仓库根的 posix 路径）。"""
    source = repo_root / entry
    if source.is_dir():
        return [
            path.relative_to(repo_root).as_posix()
            for path in sorted(source.rglob("*"))
            if path.is_file() and not _is_ignored(path.relative_to(repo_root))
        ]
    return [entry]


def _copy_entry(repo_root: Path, target_root: Path, entry: str) -> list[str]:
    """把一个白名单条目复制进产物目录，返回带上的文件清单。"""
    source = repo_root / entry
    destination = target_root / entry
    if source.is_dir():
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns(*IGNORE_DIRS, *IGNORE_SUFFIXES, *IGNORE_NAMES),
        )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return _list_entry(repo_root, entry)


def _human_size(byte_count: int) -> str:
    """把字节数写成 KB / MB，给终端汇报用。"""
    if byte_count >= 1024 * 1024:
        return f"{byte_count / (1024 * 1024):.2f} MB"
    if byte_count >= 1024:
        return f"{byte_count / 1024:.1f} KB"
    return f"{byte_count} B"


def _directory_size(root: Path) -> int:
    """产物目录里所有文件的总字节数。"""
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _make_zip(target_root: Path, zip_path: Path) -> int:
    """把产物目录压成 zip（顶层就是包名目录），返回压缩包字节数。"""
    if zip_path.exists():
        zip_path.unlink()
    files = [path for path in sorted(target_root.rglob("*")) if path.is_file()]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=f"{target_root.name}/{path.relative_to(target_root).as_posix()}")
    return zip_path.stat().st_size


def _format_path(path: Path) -> str:
    """终端里优先显示相对仓库根的路径，超出仓库的（比如自定义绝对输出）原样显示。"""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    """命令行入口：解析参数、打包、汇报产物位置。"""
    parser = argparse.ArgumentParser(description="按白名单打包源码发布包（UWE）")
    parser.add_argument("--output-dir", default="dist",
                        help="产物目录，默认 dist/（相对仓库根；也可以给绝对路径）")
    parser.add_argument("--dry-run", action="store_true", help="只打印会带上哪些文件，不写盘")
    parser.add_argument("--no-zip", action="store_true", help="只生成文件夹，不压 zip")
    parser.add_argument("--keep-target", action="store_true", help="不先清空旧产物目录")
    args = parser.parse_args(argv)

    version, demo = _read_engine_info(REPO_ROOT)
    name = _package_name(version, demo)

    out_dir = Path(args.output_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir = out_dir.resolve()
    target_root = out_dir / name

    if args.dry_run:
        total_files = 0
        total_bytes = 0
        print(f"[dry-run] 包名：{name}")
        print(f"[dry-run] 产物目录：{_format_path(target_root)}")
        for entry in WHITELIST:
            source = REPO_ROOT / entry
            if not source.exists():
                level = "缺少" if entry in REQUIRED_ENTRIES else "缺少（跳过）"
                print(f"  {level} {entry}")
                if entry in REQUIRED_ENTRIES:
                    return 1
                continue
            files = _list_entry(REPO_ROOT, entry)
            size = sum((REPO_ROOT / item).stat().st_size for item in files)
            total_files += len(files)
            total_bytes += size
            mark = "/" if source.is_dir() else ""
            print(f"  {entry}{mark} -> {len(files)} 个文件，{_human_size(size)}")
        print(f"[dry-run] 合计 {total_files} 个文件，{_human_size(total_bytes)}（没有写盘）")
        return 0

    for entry in REQUIRED_ENTRIES:
        if not (REPO_ROOT / entry).exists():
            raise SystemExit(f"缺少必需条目 {entry}，不能打包")

    if target_root.exists() and not args.keep_target:
        # 只删自己产物目录里的这一个包，路径必须落在 out_dir 正下方。
        if target_root.parent != out_dir or target_root.name != name:
            raise SystemExit(f"拒绝清理意料之外的路径：{target_root}")
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True, exist_ok=True)

    total_files = 0
    total_bytes = 0
    for entry in WHITELIST:
        source = REPO_ROOT / entry
        if not source.exists():
            print(f"  缺少（跳过） {entry}")
            continue
        files = _copy_entry(REPO_ROOT, target_root, entry)
        size = sum((target_root / item).stat().st_size for item in files)
        total_files += len(files)
        total_bytes += size
        print(f"  {entry} -> {len(files)} 个文件，{_human_size(size)}")

    print("")
    print(f"打包完成：{name}")
    print(f"  文件夹  {_format_path(target_root)}/（{total_files} 个文件，{_human_size(total_bytes)}）")
    if not args.no_zip:
        zip_path = out_dir / f"{name}.zip"
        zip_bytes = _make_zip(target_root, zip_path)
        print(f"  压缩包  {_format_path(zip_path)}（{_human_size(zip_bytes)}，解压后顶层是 {name}/）")
    print("  未包含  plans/ tests/ tools/ run_tests.py saves/ logs/ .vscode/ .roo/ dist/ 等")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
