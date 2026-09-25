"""媒体端口的装配测试（标准库 unittest）。

位置：tests/unit/shell/
运行：在仓库根执行 `python run_tests.py`。
覆盖（R6-6）：
  - `ShellApp(media_factory=...)` 会用**这一局的 Mod 文件夹**去造媒体端口；
  - 造出来的东西真的进了会话的运行期（`session.runtime.media`）。
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


class FakeMedia:
    """假媒体端口：只记下自己被造出来了。"""

    def play_sound(self, path: str) -> bool:
        """不真的播。"""
        return False

    def play_music(self, path: str, *, loop: bool = True) -> bool:
        """不真的播。"""
        return False

    def stop_music(self) -> None:
        """什么都不做。"""


class TestMediaWiring(unittest.TestCase):
    """`ShellApp` 把媒体工厂接到会话上。"""

    def setUp(self) -> None:
        """准备临时存档目录与外壳应用。"""
        self._folder = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(self._folder, ignore_errors=True)
        self._folder.mkdir(parents=True, exist_ok=True)
        self.made: list[str] = []
        self.media = FakeMedia()
        self.app = ShellApp(
            window=FakeWindow(),
            files=LocalFileSystem(),
            scripts=PythonScriptLoader(),
            mods_root=str(MODS_ROOT),
            saves_root=str(self._folder),
            modules_root=str(MODULES_ROOT),
            seed_supplier=lambda: 2026,
            media_factory=self._factory,
        )

    def _factory(self, mod_folder: str) -> FakeMedia:
        """记下 Mod 文件夹，返回同一个假媒体。"""
        self.made.append(mod_folder)
        return self.media

    def tearDown(self) -> None:
        """清掉临时目录。"""
        shutil.rmtree(self._folder, ignore_errors=True)
        try:
            SCRATCH_ROOT.rmdir()
        except OSError:
            pass

    def test_new_game_builds_media_from_the_mod_folder(self):
        """开局：工厂收到的是 Mod 文件夹，媒体端口进了运行期。"""
        self.app._set_screen("mod_select")       # 和界面一样：先扫描 Mod 列表
        self.app.start_new_game("demo_hex")
        self.assertIsNotNone(self.app.session)
        self.assertEqual(len(self.made), 1)
        self.assertTrue(self.made[0].endswith("demo_hex"))
        self.assertIs(self.app.session.runtime.media, self.media)


if __name__ == "__main__":
    unittest.main()
