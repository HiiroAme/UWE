"""pygame 适配器的冒烟测试（§17.1：渲染器是 UI 适配器的一部分）。

位置：tests/unit/adapters/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 开窗口（无显示环境用 SDL 的 dummy 驱动）；
  - 画背景 / 矩形 / 多边形 / 文字 / 图片（图片路径不存在时跳过，不抛异常）；
  - 滚轮与拖动翻译成统一窗口事件（"只有适配器碰平台"）；
  - 事件翻译（把 pygame 事件转成统一 WindowEvent）；
  - 关闭窗口。

没装 pygame 时整个测试类跳过（引擎核心不依赖 pygame，只有这个适配器依赖）。
"""

import os
import shutil
import unittest
from pathlib import Path

# 无显示环境下用 SDL 的 dummy 驱动（必须在 import pygame 之前设置）。
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

try:  # pragma: no cover - 取决于环境是否有 pygame
    import pygame

    from adapters.pygame_adapter import PygameAdapter

    PYGAME_AVAILABLE = True
except Exception:  # pragma: no cover
    PYGAME_AVAILABLE = False

SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"


@unittest.skipUnless(PYGAME_AVAILABLE, "没有 pygame，跳过适配器冒烟测试")
class TestPygameAdapter(unittest.TestCase):
    """窗口与渲染的基本冒烟。"""

    def setUp(self):
        """开一个小窗口。"""
        self.window = PygameAdapter("测试窗口", (320, 240))

    def tearDown(self):
        """关掉窗口。"""
        self.window.close()

    def test_size_and_draw(self):
        """尺寸正确；画背景、矩形、多边形、文字、缺失图片都不报错。"""
        self.assertEqual(self.window.size(), (320, 240))
        self.window.begin_frame((0, 0, 0, 255))
        self.window.draw_rect((10, 10, 40, 20), (200, 40, 40, 255))
        self.window.draw_polygon(((0.0, 0.0), (20.0, 0.0), (10.0, 17.0)), (40, 200, 40, 255))
        self.window.draw_text("测试文本", (10, 40, 120, 24), size=16, color=(255, 255, 255, 255),
                              align="left")
        self.window.draw_image("不存在的图片.png", (0, 0, 10, 10))
        self.window.end_frame()

    def test_poll_events_returns_tuple(self):
        """没有输入时事件是空元组（类型正确）。"""
        self.assertEqual(self.window.poll_events(), ())

    def test_transparent_shapes_do_not_paint(self):
        """alpha=0 的 rect / polygon 不填充：背景色保持不变（R6-8 / N-1）。"""
        self.window.begin_frame((18, 20, 26, 255))
        self.window.draw_rect((0, 0, 320, 240), (0, 0, 0, 0))
        self.window.draw_polygon(
            ((0.0, 0.0), (320.0, 0.0), (320.0, 240.0), (0.0, 240.0)), (0, 0, 0, 0))
        self.assertEqual(tuple(self.window._screen.get_at((10, 10)))[:3], (18, 20, 26))
        self.assertEqual(tuple(self.window._screen.get_at((300, 200)))[:3], (18, 20, 26))

    def test_set_icon(self):
        """设置窗口图标：正常路径返回 True，缺失文件返回 False 不抛。"""
        folder = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            icon_path = folder / "icon.png"
            surface = pygame.Surface((16, 16))
            surface.fill((10, 20, 30))
            pygame.image.save(surface, str(icon_path))
            self.assertTrue(self.window.set_icon(str(icon_path)))
            self.assertFalse(self.window.set_icon(str(folder / "missing.png")))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            try:
                SCRATCH_ROOT.rmdir()
            except OSError:
                pass

    def test_asset_root_resolves_relative_image(self):
        """给了资源根目录：相对路径的图片按它解析并真的画出来（C-3）。"""
        folder = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(folder, ignore_errors=True)
        (folder / "assets").mkdir(parents=True, exist_ok=True)
        try:
            paint = pygame.Surface((8, 8))
            paint.fill((250, 30, 40))
            pygame.image.save(paint, str(folder / "assets" / "tile.png"))

            self.window.set_asset_root(str(folder))
            self.window.begin_frame((0, 0, 0, 255))
            self.window.draw_image("assets/tile.png", (0, 0, 320, 240))
            pixel = tuple(self.window._screen.get_at((160, 120)))[:3]
            for channel, expected in zip(pixel, (250, 30, 40)):
                self.assertLessEqual(abs(channel - expected), 2, f"实际像素 {pixel}")

            # 撤回基准目录（回首页）后相对路径找不到：只提醒、不抛，屏幕也不该崩。
            self.window.set_asset_root("")
            self.window.draw_image("assets/tile.png", (0, 0, 10, 10))
        finally:
            self.window.set_asset_root("")
            shutil.rmtree(folder, ignore_errors=True)
            try:
                SCRATCH_ROOT.rmdir()
            except OSError:
                pass

    def test_posted_click_becomes_window_event(self):
        """按下再抬起（没拖动）→ 统一点击事件（点在按下位置）。"""
        pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (12, 34), "button": 1}))
        pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": (12, 34), "button": 1}))
        events = self.window.poll_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "click")
        self.assertEqual(events[0].point, (12, 34))

    def test_drag_does_not_also_click(self):
        """按下后拖动超过阈值：抬起时不再补一个点击，只发拖动。"""
        pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (10, 10), "button": 1}))
        pygame.event.post(pygame.event.Event(pygame.MOUSEMOTION,
                                            {"pos": (20, 10), "rel": (10, 0),
                                             "buttons": (1, 0, 0)}))
        pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": (20, 10), "button": 1}))
        events = self.window.poll_events()
        self.assertEqual([event.kind for event in events], ["drag"])
        self.assertEqual(events[0].point, (20, 10))
        self.assertEqual(events[0].delta, (10.0, 0.0))     # 越过阈值时补上整段位移

    def test_small_motion_still_clicks(self):
        """按下后只抖了一下（没到阈值）：仍然算点击，而且点的是按下位置。"""
        pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (10, 10), "button": 1}))
        pygame.event.post(pygame.event.Event(pygame.MOUSEMOTION,
                                            {"pos": (12, 12), "rel": (2, 2),
                                             "buttons": (1, 0, 0)}))
        pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": (12, 12), "button": 1}))
        events = self.window.poll_events()
        self.assertEqual([event.kind for event in events], ["click"])
        self.assertEqual(events[0].point, (10, 10))

    def test_wheel_and_drag_become_window_events(self):
        """滚轮 → wheel（带滚动量）；按住左键移动 → drag（带本帧位移）。"""
        pygame.event.post(pygame.event.Event(pygame.MOUSEWHEEL, {"x": 0, "y": 2}))
        pygame.event.post(pygame.event.Event(pygame.MOUSEMOTION,
                                            {"pos": (10, 12), "rel": (3, -4),
                                             "buttons": (1, 0, 0)}))
        events = self.window.poll_events()

        kinds = [event.kind for event in events]
        self.assertEqual(kinds, ["wheel", "drag"])
        self.assertEqual(events[0].delta, (0.0, 2.0))
        self.assertEqual(events[1].point, (10, 12))
        self.assertEqual(events[1].delta, (3.0, -4.0))

        # 没按左键的移动不算拖动（那是"鼠标划过"，不是拖拽）。
        pygame.event.post(pygame.event.Event(pygame.MOUSEMOTION,
                                            {"pos": (1, 2), "rel": (1, 1),
                                             "buttons": (0, 0, 0)}))
        self.assertEqual(self.window.poll_events(), ())

    def test_posted_quit_becomes_quit_event(self):
        """关闭窗口事件被翻译成 quit。"""
        pygame.event.post(pygame.event.Event(pygame.QUIT))
        events = self.window.poll_events()
        self.assertEqual([event.kind for event in events], ["quit"])


if __name__ == "__main__":
    unittest.main()
