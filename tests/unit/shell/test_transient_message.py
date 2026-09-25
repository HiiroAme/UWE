"""外壳状态提示的存活时间（默认 3 秒自动消失）的单元测试。

位置：tests/unit/shell/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 提示默认 3 秒后消失，并把界面状态里的值一并清掉；
  - 新提示会重新计时（不会继承上一条的倒计时）；
  - 失败类提示用更长的存活时间（6 秒）；
  - 外壳画面（菜单页）过期后不再画这行字；
  - 真存档一次：`已存档` 提示同样会过期（作者报的"提示一直挂着"的回归钉子）。

时钟走构造参数注入（`clock=`），测试里拨表即可，不 sleep。
"""

import shutil
import unittest
from pathlib import Path

from adapters import LocalFileSystem, PythonScriptLoader
from core.ports import WindowEvent
from shell import ShellApp
from shell.shell_app import _MESSAGE_SECONDS, _MESSAGE_SECONDS_LONG

REPO_ROOT = Path(__file__).resolve().parents[3]
MODS_ROOT = REPO_ROOT / "mods"
MODULES_ROOT = REPO_ROOT / "modules"
SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"
SIZE = (960, 640)


class FakeWindow:
    """假窗口：满足 Window + Renderer 两个端口，只记下被画了什么。"""

    def __init__(self) -> None:
        """创建假窗口。"""
        self.drawn: list[tuple] = []
        self.closed = False

    def size(self) -> tuple[int, int]:
        """返回窗口尺寸。"""
        return SIZE

    def poll_events(self) -> tuple[WindowEvent, ...]:
        """没有事件。"""
        return ()

    def close(self) -> None:
        """记下关闭。"""
        self.closed = True

    def begin_frame(self, background) -> None:
        """记账：开始一帧。"""
        self.drawn.append(("begin", background))

    def draw_rect(self, rect, color) -> None:
        """记账：矩形。"""
        self.drawn.append(("rect", rect))

    def draw_polygon(self, points, color, *, outline_color=None, outline_width=0.0) -> None:
        """记账：多边形。"""
        self.drawn.append(("polygon", points))

    def draw_text(self, text, rect, *, size, color, align="center") -> None:
        """记账：文字。"""
        self.drawn.append(("text", text, rect))

    def draw_image(self, path, rect) -> None:
        """记账：图片。"""
        self.drawn.append(("image", path, rect))

    def end_frame(self) -> None:
        """记账：结束一帧。"""
        self.drawn.append(("end",))


class TestTransientMessage(unittest.TestCase):
    """状态提示的过期行为。"""

    def setUp(self) -> None:
        """准备临时存档目录与一个假时钟。"""
        self._folder = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(self._folder, ignore_errors=True)
        self._folder.mkdir(parents=True, exist_ok=True)
        self.now = 1000.0
        self.window = FakeWindow()
        self.app = ShellApp(
            window=self.window,
            files=LocalFileSystem(),
            scripts=PythonScriptLoader(),
            mods_root=str(MODS_ROOT),
            saves_root=str(self._folder),
            modules_root=str(MODULES_ROOT),
            seed_supplier=lambda: 2026,
            clock=lambda: self.now,
        )

    def tearDown(self) -> None:
        """清掉临时目录。"""
        shutil.rmtree(self._folder, ignore_errors=True)
        try:
            SCRATCH_ROOT.rmdir()
        except OSError:
            pass

    def _advance(self, seconds: float) -> None:
        """把假时钟往前拨。"""
        self.now += seconds

    def _message_text(self) -> str:
        """取当前画面上那行状态提示（外壳页与游戏内左下角都算）。"""
        for layer in self.app.build_view().layers:
            if layer.id in ("shell:message", "shell:game:message"):
                return layer.text
        return ""

    def test_message_expires_after_default_ttl(self) -> None:
        """默认 3 秒：2.9 秒还在，3.1 秒没了，而且界面状态被清掉。"""
        self.app._put_message("已存档：demo.json")
        self.assertEqual(self.app._visible_message(), "已存档：demo.json")

        self._advance(_MESSAGE_SECONDS - 0.1)
        self.assertEqual(self.app._visible_message(), "已存档：demo.json")

        self._advance(0.2)
        self.assertEqual(self.app._visible_message(), "")
        self.assertEqual(self.app.context.get("shell.message", ""), "")

    def test_new_message_restarts_the_clock(self) -> None:
        """第二条提示重新计时：不会继承第一条剩下的时间。"""
        self.app._put_message("第一条")
        self._advance(_MESSAGE_SECONDS - 0.5)
        self.app._put_message("第二条")

        self._advance(1.0)          # 距第一条 3.5 秒，距第二条才 1 秒
        self.assertEqual(self.app._visible_message(), "第二条")

        self._advance(_MESSAGE_SECONDS)   # 距第二条 4 秒
        self.assertEqual(self.app._visible_message(), "")

    def test_failure_message_uses_long_ttl(self) -> None:
        """失败类提示活得久一点：3.5 秒还在，6.1 秒没了。"""
        self.app._put_message("读档失败：坏了", _MESSAGE_SECONDS_LONG)

        self._advance(3.5)
        self.assertEqual(self.app._visible_message(), "读档失败：坏了")

        self._advance(2.6)
        self.assertEqual(self.app._visible_message(), "")

    def test_view_stops_drawing_expired_message(self) -> None:
        """画面上也不该再画：菜单页的 `shell:message` 层过期后是空文字。"""
        self.app._put_message("已自动存档：x.json")
        self.assertIn("已自动存档", self._message_text())

        self._advance(_MESSAGE_SECONDS + 0.1)
        self.assertEqual(self._message_text(), "")

    def test_saved_message_expires_end_to_end(self) -> None:
        """真存一次档：`已存档` 提示先出现，3 秒后自动消失（回归钉子）。"""
        self.app._set_screen("mod_select")      # 和界面一样：先扫描 Mod 列表
        self.app.start_new_game("demo_hex")
        self.assertIsNotNone(self.app.session)

        self.app.trigger_syscall("engine:syscall:save")
        self.assertIn("已存档", self._message_text())
        self.assertEqual(self.app._visible_message() != "", True)

        self._advance(_MESSAGE_SECONDS + 0.1)
        self.assertEqual(self._message_text(), "")
        self.assertEqual(self.app._visible_message(), "")


if __name__ == "__main__":
    unittest.main()
