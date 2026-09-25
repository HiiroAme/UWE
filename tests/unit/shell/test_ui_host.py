"""shell.ui_host 的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/shell/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 画面来源与渲染顺序（交给渲染端口的调用顺序）；
  - 点击 → 统一输入 → （按 ClickResult.settle）触发结算；
  - 没命中可点击层时什么都不发生；
  - make_page_provider 把 Mod 视图函数接成画面来源。

渲染端口用一个"记账用"的假实现（记录被调用了什么），不依赖 pygame。
"""

import unittest

from core.context import Context
from core.pipeline import Input
from core.ui import ClickResult, Layer, PageContext, View, Viewport, WheelResult
from shell import UiHost, make_page_provider


class FakeRenderer:
    """记账用的假渲染器：把每次绘制调用记下来。"""

    def __init__(self, size: tuple[int, int] = (800, 600)) -> None:
        """创建假渲染器。"""
        self._size = size
        self.calls: list[tuple] = []

    def size(self) -> tuple[int, int]:
        """返回画布尺寸。"""
        return self._size

    def begin_frame(self, background) -> None:
        """记账：开始一帧。"""
        self.calls.append(("begin", background))

    def draw_rect(self, rect, color) -> None:
        """记账：画矩形。"""
        self.calls.append(("rect", rect, color))

    def draw_polygon(self, points, color, *, outline_color=None, outline_width=0.0) -> None:
        """记账：画多边形。"""
        self.calls.append(("polygon", points, color))

    def draw_text(self, text, rect, *, size, color, align="center") -> None:
        """记账：画文字。"""
        self.calls.append(("text", text, rect, size, align))

    def draw_image(self, path, rect) -> None:
        """记账：画图片。"""
        self.calls.append(("image", path, rect))

    def end_frame(self) -> None:
        """记账：结束一帧。"""
        self.calls.append(("end",))


class TestUiHost(unittest.TestCase):
    """宿主的基本行为。"""

    def _host(self):
        """造一个带按钮的宿主，并接上"输入去哪"的记账器。"""
        renderer = FakeRenderer()
        host = UiHost(renderer, context=Context())
        host.set_provider(lambda view_host: View(layers=(_button("go", (0, 0, 100, 40)),)))
        inputs: list[Input] = []
        settles: list[tuple[str, ...]] = []
        host.set_input_sink(inputs.append, settles.append)
        return host, renderer, inputs, settles

    def test_render_draws_in_order(self):
        """渲染按 z 顺序逐层交给渲染端口。"""
        host, renderer, _, _ = self._host()
        host.render()
        kinds = [call[0] for call in renderer.calls]
        self.assertEqual(kinds, ["begin", "rect", "end"])

    def test_render_translates_polygon_layer(self):
        """多边形层翻译成渲染端口的 draw_polygon（不是矩形）。"""
        renderer = FakeRenderer()
        host = UiHost(renderer, context=Context())
        points = ((10.0, 0.0), (30.0, 20.0), (10.0, 40.0), (-10.0, 20.0))
        host.set_provider(lambda view_host: View(layers=(
            Layer(id="shape", kind="polygon", points=points, color=(1, 2, 3, 255)),
        )))
        host.render()
        self.assertIn(("polygon", points, (1, 2, 3, 255)), renderer.calls)
        self.assertEqual([call[0] for call in renderer.calls], ["begin", "polygon", "end"])

    def test_render_applies_viewport_but_not_to_fixed_layers(self):
        """视口：普通层按缩放平移画，fixed 层（HUD）原样画。"""
        renderer = FakeRenderer()
        host = UiHost(renderer, context=Context())
        host.set_provider(lambda view_host: View(
            viewport=Viewport(offset_x=100.0, offset_y=50.0, zoom=2.0),
            layers=(
                Layer(id="world", rect=(10.0, 10.0, 50.0, 50.0), kind="rect",
                      color=(1, 1, 1, 255)),
                Layer(id="hud", rect=(0.0, 0.0, 20.0, 20.0), kind="rect",
                      color=(2, 2, 2, 255), fixed=True),
                Layer(id="world_text", rect=(0.0, 0.0, 10.0, 10.0), kind="text",
                      text="x", font_size=12.0, color=(0, 0, 0, 0)),
            ),
        ))
        host.render()

        self.assertIn(("rect", (120.0, 70.0, 100.0, 100.0), (1, 1, 1, 255)), renderer.calls)
        self.assertIn(("rect", (0.0, 0.0, 20.0, 20.0), (2, 2, 2, 255)), renderer.calls)
        text_calls = [call for call in renderer.calls if call[0] == "text"]
        self.assertEqual(text_calls[0][3], 24.0)      # 字号也乘了 zoom
        self.assertEqual(text_calls[0][2], (100.0, 50.0, 20.0, 20.0))

    def test_wheel_and_drag_become_inputs_and_settle(self):
        """滚轮 / 拖动：转成统一输入并立刻结算（拖到哪动到哪）。"""
        host, _, inputs, settles = self._host()
        wheel = host.wheel_at((30.0, 40.0), (0.0, 1.0), timestamp=2.5)
        drag = host.drag_at((31.0, 42.0), (-3.0, 4.0), timestamp=2.6)

        self.assertEqual(wheel.kind, "wheel")
        self.assertEqual(wheel.data, {"point": [30.0, 40.0], "delta": [0.0, 1.0]})
        self.assertEqual(drag.kind, "drag")
        self.assertEqual(drag.data, {"point": [31.0, 42.0], "delta": [-3.0, 4.0]})
        self.assertEqual([item.kind for item in inputs], ["wheel", "drag"])
        self.assertEqual(len(settles), 2)

    def test_wheel_target_routes_to_the_layer(self):
        """鼠标在带 WheelResult 的层上：滚轮发这一层的输入（例如战报滚动）。"""
        renderer = FakeRenderer()
        host = UiHost(renderer, context=Context())
        host.set_provider(lambda view_host: View(layers=(
            Layer(id="log", rect=(0, 0, 200, 200), kind="rect", color=(0, 0, 0, 255),
                  wheel=WheelResult(kind="scroll_log"), fixed=True),
        )))
        inputs, settles = [], []
        host.set_input_sink(inputs.append, settles.append)
        host.render()

        event = host.wheel_at((50.0, 50.0), (0.0, 1.0), timestamp=1.0)
        self.assertEqual(event.kind, "scroll_log")
        self.assertEqual(event.data, {"point": [50.0, 50.0], "delta": [0.0, 1.0]})
        self.assertEqual([item.kind for item in inputs], ["scroll_log"])
        self.assertEqual(len(settles), 1)

    def test_blocker_locks_pointer_inputs_but_allows_controls_above_it(self):
        """阻挡层：挡住下面的点击 / 滚轮 / 拖动；它上面的按钮仍然能点。"""
        renderer = FakeRenderer()
        host = UiHost(renderer, context=Context())
        host.set_provider(lambda view_host: View(layers=(
            Layer(id="map", rect=(0, 0, 800, 600), kind="rect", color=(1, 1, 1, 255),
                  click=ClickResult(kind="map_click"), fixed=True),
            Layer(id="modal:blocker", rect=(0, 0, 800, 600), kind="rect",
                  color=(0, 0, 0, 0), visible=False, blocks_pointer=True, fixed=True, z=10),
            Layer(id="modal:confirm", rect=(300, 500, 200, 40), kind="text", text="确认",
                  color=(0, 0, 0, 0), text_color=(255, 255, 255, 255),
                  click=ClickResult(kind="close_rules"), fixed=True, z=20),
        )))
        inputs, settles = [], []
        host.set_input_sink(inputs.append, settles.append)
        host.render()

        self.assertIsNone(host.click_at((10.0, 10.0)))          # 地图被挡住
        self.assertIsNone(host.wheel_at((10.0, 10.0), (0.0, 1.0)))
        self.assertIsNone(host.drag_at((10.0, 10.0), (5.0, 0.0)))
        confirm = host.click_at((350.0, 510.0))                  # 阻挡层上面的按钮
        self.assertEqual(confirm.kind, "close_rules")
        self.assertEqual([item.kind for item in inputs], ["close_rules"])

        # visible=False 的阻挡层不画，但仍然参与命中。
        drawn = [call for call in renderer.calls if call[0] == "rect"]
        self.assertEqual([call[2] for call in drawn], [(1, 1, 1, 255)])

    def test_escape_target_routes_to_the_layer(self):
        """按 Esc：当前帧有 escape 目标的层就发它的输入；没有返回 None。"""
        renderer = FakeRenderer()
        host = UiHost(renderer, context=Context())
        host.set_provider(lambda view_host: View(layers=(
            Layer(id="modal", rect=(0, 0, 800, 600), kind="rect",
                  visible=False, blocks_pointer=True, fixed=True,
                  escape=ClickResult(kind="close_rules")),
        )))
        inputs, settles = [], []
        host.set_input_sink(inputs.append, settles.append)
        host.render()

        event = host.escape_event(timestamp=1.0)
        self.assertEqual(event.kind, "close_rules")
        self.assertEqual([item.kind for item in inputs], ["close_rules"])
        self.assertEqual(len(settles), 1)
        self.assertIsNone(UiHost(FakeRenderer(), context=Context()).escape_event())

    def test_size_follows_the_renderer(self):
        """窗口被拉伸后，宿主报的尺寸立刻跟上（不再用旧缓存）。"""
        renderer = FakeRenderer(size=(800, 600))
        host = UiHost(renderer, context=Context())
        self.assertEqual(host.size, (800, 600))
        renderer._size = (1024, 768)
        self.assertEqual(host.size, (1024, 768))

    def test_page_size_is_read_live(self):
        """视图脚本每帧拿到的窗口尺寸是**现问现给**的（拉伸窗口后能重新排版）。"""
        sizes = [(800, 600)]
        seen = []

        def page(page_context):
            """记下这一帧拿到的尺寸，返回一张空画面。"""
            seen.append(page_context.size)
            return View()

        provider = make_page_provider(page, state_getter=lambda: {}, context=Context(),
                                      size=lambda: sizes[0])
        host = UiHost(FakeRenderer(size=(800, 600)), context=Context())
        provider(host)
        sizes[0] = (1280, 720)
        provider(host)
        self.assertEqual(seen, [(800, 600), (1280, 720)])

    def test_click_produces_input_and_settles(self):
        """点到按钮：产生统一输入并（按 settle）触发结算。"""
        host, _, inputs, settles = self._host()
        host.render()
        produced = host.click_at((10.0, 10.0), 3.5)
        self.assertIsNotNone(produced)
        self.assertEqual(produced.kind, "go")
        self.assertEqual(produced.data, {"layer": "go"})
        self.assertEqual(produced.timestamp, 3.5)
        self.assertEqual(len(inputs), 1)
        self.assertEqual(len(settles), 1)

    def test_click_without_settle_does_not_settle(self):
        """ClickResult.settle=False：只送输入，不结算（实时制 Mod 会用这种）。"""
        renderer = FakeRenderer()
        host = UiHost(renderer, context=Context())
        host.set_provider(
            lambda view_host: View(
                layers=(Layer(id="no_settle", rect=(0, 0, 50, 50), kind="rect",
                              click=ClickResult(kind="move", data={}, settle=False)),)
            )
        )
        inputs: list[Input] = []
        settles: list[tuple[str, ...]] = []
        host.set_input_sink(inputs.append, settles.append)
        host.render()
        host.click_at((5.0, 5.0))
        self.assertEqual(len(inputs), 1)
        self.assertEqual(settles, [])

    def test_click_outside_does_nothing(self):
        """点在空白处：没有输入、没有结算。"""
        host, _, inputs, settles = self._host()
        host.render()
        self.assertIsNone(host.click_at((500.0, 500.0)))
        self.assertEqual(inputs, [])
        self.assertEqual(settles, [])

class TestPageProvider(unittest.TestCase):
    """把 Mod 视图函数接成画面来源。"""

    def test_provider_passes_state_size_context(self):
        """视图函数拿到的是 State / 尺寸 / 界面状态。"""
        seen = {}

        def page(page_context: PageContext) -> View:
            """记下收到的上下文，并返回一张空画面。"""
            seen["state"] = page_context.state
            seen["size"] = page_context.size
            seen["page_id"] = page_context.page_id
            return View()

        context = Context()
        state = {"turn": 1}
        provider = make_page_provider(
            page, state_getter=lambda: state, context=context,
            size=lambda: (640, 480), page_id="demo:ui:main"
        )
        host = UiHost(FakeRenderer(), context=context)
        host.set_provider(provider)
        host.render()

        self.assertEqual(seen["state"], state)
        self.assertEqual(seen["size"], (640, 480))
        self.assertEqual(seen["page_id"], "demo:ui:main")


def _button(layer_id: str, rect) -> Layer:
    """造一个可点击的矩形层（测试用）。"""
    return Layer(
        id=layer_id,
        rect=rect,
        kind="rect",
        click=ClickResult(kind="go" if layer_id == "go" else "other", data={"layer": layer_id}, settle=True),
    )


if __name__ == "__main__":
    unittest.main()
