"""R5-3 / R5-8：视图脚本抛异常或返回类型不对时沿用上一帧、不冒到主循环。

另外钉住原则审查 #3：错误去重集合在 __init__ 里显式声明，且有上限。

位置：tests/unit/shell/
运行：在仓库根执行 `python run_tests.py`。
"""

import unittest

from core.logger import Logger
from core.ui import View
from shell import UiHost
from shell.ui_host import _VIEW_ERROR_LIMIT


class _StubRenderer:
    """只实现 UiHost 构造要用的最小渲染端口（测试不碰窗口）。"""

    def size(self):
        """返回一个固定画布尺寸。"""
        return (640, 480)

    def begin_frame(self):
        """空实现（本文件只调 build_view，不真正绘制）。"""


class TestViewFailureGuard(unittest.TestCase):
    """视图来源抛异常时的兜底。"""

    def _host(self, provider):
        """用真实构造造一个 UiHost（给最小渲染端口，不碰窗口）。"""
        host = UiHost(_StubRenderer(), logger=Logger(sink=None))
        host.set_provider(provider)
        return host

    def test_exception_keeps_last_view(self):
        """来源抛 KeyError：不抛出，返回上一帧。"""

        def boom(_host):
            raise KeyError("/units/typo")

        host = self._host(boom)
        view = UiHost.build_view(host)
        self.assertIsInstance(view, View)

    def test_error_is_only_logged_once(self):
        """同一条错误只记一次（视图是每帧调的）。"""

        def boom(_host):
            raise KeyError("/units/typo")

        host = self._host(boom)
        for _ in range(5):
            UiHost.build_view(host)
        self.assertEqual(len(host._view_errors), 1)

    def test_none_return_keeps_last_view(self):
        """返回 None（忘了 return）也算视图的错：沿用上一帧、不抛（R5-8）。"""

        def nothing(_host):
            return None

        host = self._host(nothing)
        self.assertIsInstance(UiHost.build_view(host), View)

    def test_error_set_is_declared_and_bounded(self):
        """错误集合构造后就有，且不同错误再多也不会无限增长。"""
        counter = {"value": 0}

        def boom(_host):
            counter["value"] += 1
            raise RuntimeError(f"第 {counter['value']} 种错误")

        host = self._host(boom)
        self.assertEqual(host._view_errors, set())     # 不靠 build_view 临时 getattr 造出来
        for _ in range(_VIEW_ERROR_LIMIT + 16):
            UiHost.build_view(host)
        self.assertGreater(len(host._view_errors), 0)
        self.assertLessEqual(len(host._view_errors), _VIEW_ERROR_LIMIT)


class TestDrawViewGuard(unittest.TestCase):
    """R5-14：draw_view 收到非 View 要给明确的 TypeError。"""

    def test_non_view_is_a_clear_type_error(self):
        """直接传字符串：报的是我们自己的消息，不是 AttributeError。"""
        from shell.ui_host import draw_view

        with self.assertRaises(TypeError) as ctx:
            draw_view(object(), "nope")
        self.assertIn("需要一个 View", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
