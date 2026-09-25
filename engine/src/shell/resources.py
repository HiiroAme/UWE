"""引擎自带资源的路径解析（shell 层）。

位置：
    shell 包。core 不碰文件系统（P7）；真正加载图片的是适配器，
    本模块只负责"逻辑资源名 → 实际路径"的规则。

约定：
    - 源码运行时根目录：engine/src/shell/assets/
    - 打包后根目录：_MEIPASS/shell/assets/（PyInstaller 保持同样的相对路径）
    - 逻辑名只用 POSIX 风格的相对路径，例如 "brand/icon.ico"；
      不允许绝对路径，也不允许用 ".." 跳出资源根。

这样同一个资源在所有 shell 页面、启动代码和打包配置里都是同一个逻辑名，
换文件只换资源根里的实际文件，不需要到处改路径。
"""

from pathlib import Path, PurePosixPath
import sys


class AssetResolver:
    """把逻辑资源名解析成某个根目录下的实际路径。"""

    def __init__(self, root: str | Path) -> None:
        """创建解析器；root 是资源的物理根目录。"""
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        """返回解析器的物理根目录。"""
        return self._root

    def path(self, name: str) -> Path:
        """把逻辑名解析成路径；只做路径计算，不读文件。"""
        if not isinstance(name, str):
            raise TypeError(f"资源名必须是字符串，实际是 {type(name).__name__}")
        if not name:
            raise ValueError("资源名不能为空")
        logical = PurePosixPath(name)
        if logical.is_absolute() or name.startswith("/") or ".." in logical.parts:
            raise ValueError(f"资源名必须是一个相对路径，不能是 {name!r}")
        if Path(name).is_absolute():
            raise ValueError(f"资源名不能是绝对路径：{name!r}")
        return self._root.joinpath(*logical.parts)

    def first(self, names) -> Path | None:
        """按顺序找第一个存在的文件；都不存在时返回 None。"""
        for name in names:
            candidate = self.path(name)
            if candidate.is_file():
                return candidate
        return None


def default_asset_root() -> Path:
    """引擎自带资源的物理根：源码运行与打包运行两种布局。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "shell" / "assets"
    return Path(__file__).resolve().parent / "assets"


def resource_roots(root: str | Path | None = None) -> tuple[Path, ...]:
    """资源根候选：打包后"exe 旁边的外部目录"优先，然后是内置目录。

    这样 one-folder 发布时，把 `shell/assets/texts/zh-CN.json` 放在 exe 旁边
    就能覆盖内置文字；源码运行时只有内置目录一个根。
    """
    if root is not None:
        return (Path(root).resolve(),)
    roots = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        roots.append(Path(sys.executable).resolve().parent / "shell" / "assets")
    roots.append(default_asset_root())
    unique = []
    for item in roots:
        if item not in unique:
            unique.append(item)
    return tuple(unique)


def brand_root(root: str | Path | None = None) -> Path:
    """引擎品牌资源的根目录（brand/ 子目录）。"""
    base = Path(root).resolve() if root is not None else default_asset_root()
    return base / "brand"


def brand_asset(name: str, *, root: str | Path | None = None) -> Path:
    """按逻辑名取品牌资源路径（如 "logo.png" → .../brand/logo.png）。"""
    return AssetResolver(brand_root(root)).path(name)


def brand_icon_candidates(*, root: str | Path | None = None) -> tuple[Path, ...]:
    """按优先级返回存在的图标文件：外部根优先，ico 在 png 前。"""
    result = []
    for base in resource_roots(root):
        resolver = AssetResolver(brand_root(base))
        for name in ("icon.ico", "icon.png"):
            candidate = resolver.path(name)
            if candidate.is_file():
                result.append(candidate)
    return tuple(result)


def brand_icon(*, root: str | Path | None = None) -> Path | None:
    """返回第一个存在的品牌图标；没有时返回 None。"""
    candidates = brand_icon_candidates(root=root)
    return candidates[0] if candidates else None
