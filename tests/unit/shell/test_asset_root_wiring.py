"""渲染资源根目录的装配测试（标准库 unittest）。

位置：tests/unit/shell/
运行：在仓库根执行 `python run_tests.py`。
覆盖（C-3）：
  - 进游戏时，`ShellApp(asset_root_sink=...)` 收到的是**这一局的 Mod 文件夹**；
  - 回首页时撤回（收到空字符串），首页的图片相对路径不会继续指向那个 Mod。
"""

import shutil
import unittest
from pathlib import Path

from adapters import LocalFileSystem, PythonScriptLoader
from core.ports import WindowEvent
from shell import ShellApp

REPO_ROOT = Path(__file__).resolve().parents[3]
MODS_ROOT = REPO_ROOT / "mods"
MODULES_ROOT = REPO_ROOT / "modules"
SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"
SIZE = (960, 640)


class FakeWindow:
    """假窗口：满足 Window + Renderer 两个端口（画什么无所谓）。"""

    def __init__(self) -> None:
        """创建假窗口。"""
        self.closed = False

    def size(self) -> tuple[int, int]:
        """窗口尺寸。"""
        return SIZE

    def poll_events(self) -> tuple[WindowEvent, ...]:
        """没有事件。"""
        return ()

    def close(self) -> None:
        """记下关闭。"""
        self.closed = True

    def begin_frame(self, background) -> None:
        """空实现。"""

    def draw_rect(self, rect, color) -> None:
        """空实现。"""

    def draw_polygon(self, points, color, *, outline_color=None, outline_width=0.0) -> None:
        """空实现。"""

    def draw_text(self, text, rect, *, size, color, align="center") -> None:
        """空实现。"""

    def draw_image(self, path, rect) -> None:
        """空实现。"""

    def end_frame(self) -> None:
        """空实现。"""


class TestAssetRootWiring(unittest.TestCase):
    """`ShellApp` 把"这一局的资源根目录"告诉宿主。"""

    def setUp(self) -> None:
        """准备临时存档目录与外壳应用。"""
        self._folder = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(self._folder, ignore_errors=True)
        self._folder.mkdir(parents=True, exist_ok=True)
        self.seen: list[str] = []
        self.app = ShellApp(
            window=FakeWindow(),
            files=LocalFileSystem(),
            scripts=PythonScriptLoader(),
            mods_root=str(MODS_ROOT),
            saves_root=str(self._folder),
            modules_root=str(MODULES_ROOT),
            seed_supplier=lambda: 2026,
            asset_root_sink=self.seen.append,
        )

    def tearDown(self) -> None:
        """清掉临时目录。"""
        shutil.rmtree(self._folder, ignore_errors=True)
        try:
            SCRATCH_ROOT.rmdir()
        except OSError:
            pass

    def test_enter_game_sets_mod_root_and_leave_clears_it(self):
        """开局给 Mod 文件夹；回首页撤回（空字符串）。"""
        self.app._set_screen("mod_select")       # 和界面一样：先扫描 Mod 列表
        self.app.start_new_game("demo_hex")
        self.assertIsNotNone(self.app.session)
        self.assertTrue(self.seen, "开局没有把资源根目录告诉宿主")
        self.assertTrue(self.seen[-1].endswith("demo_hex"), self.seen[-1])

        self.app._leave_game()
        self.assertEqual(self.seen[-1], "")

    def test_wiring_is_optional(self):
        """宿主没注入这个口子也能开局（缺省静默）。"""
        app = ShellApp(
            window=FakeWindow(),
            files=LocalFileSystem(),
            scripts=PythonScriptLoader(),
            mods_root=str(MODS_ROOT),
            saves_root=str(self._folder),
            modules_root=str(MODULES_ROOT),
            seed_supplier=lambda: 2026,
        )
        app._set_screen("mod_select")
        app.start_new_game("demo_hex")
        self.assertIsNotNone(app.session)


if __name__ == "__main__":
    unittest.main()
