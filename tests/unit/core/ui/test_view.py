"""core.ui 的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/ui/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - Layer / View / ClickResult 的字段校验（写错了当场报）；
  - 绘制顺序（z 从小到大，z 相同按声明顺序）；
  - 命中判定（取最上面能吃点击的层；不吃点击的层不算命中）；
  - polygon 层：rect 由顶点算出来、按"点在不在多边形内"判定（相邻六边形的外接矩形
    互相重叠，按矩形判定会点到隔壁去）。
"""

import math
import unittest

from core.ui import ClickResult, Layer, View, Viewport, WheelResult


def box(layer_id: str, rect, *, z: int = 0, click: bool = True) -> Layer:
    """造一个矩形层（测试用）。"""
    return Layer(
        id=layer_id,
        rect=rect,
        kind="rect",
        click=ClickResult(kind="click", data={"id": layer_id}) if click else None,
        z=z,
    )


def hexagon(center, size: float, layer_id: str = "hex", *, z: int = 0, click: bool = True) -> Layer:
    """造一个正六边形层（顶点按 30° + 60°k 给，测试用）。"""
    cx, cy = center
    points = tuple(
        (cx + size * math.cos(math.radians(30 + 60 * index)),
         cy + size * math.sin(math.radians(30 + 60 * index)))
        for index in range(6)
    )
    return Layer(
        id=layer_id,
        kind="polygon",
        points=points,
        click=ClickResult(kind="click", data={"id": layer_id}) if click else None,
        z=z,
    )


class TestValidation(unittest.TestCase):
    """字段校验。"""

    def test_click_result_checks_fields(self):
        """ClickResult 的 kind / data / settle 都要合法。"""
        with self.assertRaises(ValueError):
            ClickResult(kind="")
        with self.assertRaises(TypeError):
            ClickResult(kind="click", data=["不是对象"])
        with self.assertRaises(TypeError):
            ClickResult(kind="click", settle="yes")

    def test_layer_checks_fields(self):
        """Layer 的 id / rect / kind / align / z 都要合法。"""
        with self.assertRaises(ValueError):
            Layer(id="", rect=(0, 0, 1, 1))
        with self.assertRaises(TypeError):
            Layer(id="a", rect=(0, 0, 1))
        with self.assertRaises(ValueError):
            Layer(id="a", rect=(0, 0, -1, 1))
        with self.assertRaises(ValueError):
            Layer(id="a", rect=(0, 0, 1, 1), kind="圆形")
        with self.assertRaises(ValueError):
            Layer(id="a", rect=(0, 0, 1, 1), align="justify")
        with self.assertRaises(TypeError):
            Layer(id="a", rect=(0, 0, 1, 1), z=True)
        with self.assertRaises(ValueError):
            Layer(id="a", rect=(0, 0, 1, 1), kind="image", image="")
        with self.assertRaises(TypeError):
            Layer(id="a", rect=(0, 0, 1, 1), click="不是点击结果")

    def test_wheel_result_and_new_layer_fields(self):
        """WheelResult 与本轮新增的 visible / blocks_pointer / wheel 字段都要校验。"""
        with self.assertRaises(ValueError):
            WheelResult(kind="")
        with self.assertRaises(TypeError):
            WheelResult(kind="scroll", data=["不是对象"])
        with self.assertRaises(TypeError):
            Layer(id="a", rect=(0, 0, 1, 1), wheel="不是滚轮结果")
        with self.assertRaises(TypeError):
            Layer(id="a", rect=(0, 0, 1, 1), visible="yes")
        with self.assertRaises(TypeError):
            Layer(id="a", rect=(0, 0, 1, 1), blocks_pointer="yes")
        with self.assertRaises(TypeError):
            Layer(id="a", rect=(0, 0, 1, 1), escape="不是点击结果")

    def test_view_checks_layers(self):
        """View 只收 Layer 元组。"""
        with self.assertRaises(TypeError):
            View(layers=[box("a", (0, 0, 1, 1))])
        with self.assertRaises(TypeError):
            View(layers=("不是层",))
        with self.assertRaises(TypeError):
            View(background=(1, 2, 3))

    def test_center_helper(self):
        """center 给出矩形中心。"""
        self.assertEqual(box("a", (10, 20, 100, 50)).center, (60.0, 45.0))

    def test_polygon_needs_points(self):
        """polygon 层必须给至少三个顶点。"""
        with self.assertRaises(TypeError):
            Layer(id="a", kind="polygon")
        with self.assertRaises(TypeError):
            Layer(id="a", kind="polygon", points=((0.0, 0.0), (1.0, 0.0)))
        with self.assertRaises(TypeError):
            Layer(id="a", kind="polygon", points=((0.0, 0.0), (1.0, 0.0), (0.0, "1")))

    def test_polygon_rect_comes_from_points(self):
        """polygon 层的 rect 由顶点算出来；自己写 rect 会被拒绝。"""
        layer = Layer(id="a", kind="polygon", points=((10.0, 20.0), (30.0, 20.0), (20.0, 40.0)))
        self.assertEqual(layer.rect, (10.0, 20.0, 20.0, 20.0))
        self.assertEqual(layer.center, (20.0, 30.0))
        with self.assertRaises(ValueError):
            Layer(id="a", kind="polygon", rect=(0, 0, 10, 10),
                  points=((0.0, 0.0), (10.0, 0.0), (5.0, 10.0)))


class TestViewport(unittest.TestCase):
    """视口：世界坐标 ↔ 屏幕坐标的换算，以及"带视口的命中判定"。"""

    def test_roundtrip_and_validation(self):
        """来回换算一致；zoom 必须为正。"""
        viewport = Viewport(offset_x=100.0, offset_y=50.0, zoom=2.0)
        self.assertEqual(viewport.to_screen((10.0, 20.0)), (120.0, 90.0))
        self.assertEqual(viewport.to_world((120.0, 90.0)), (10.0, 20.0))
        self.assertEqual(viewport.scale_rect((10.0, 20.0, 30.0, 40.0)), (120.0, 90.0, 60.0, 80.0))
        with self.assertRaises(ValueError):
            Viewport(zoom=0.0)
        with self.assertRaises(TypeError):
            Viewport(zoom="2")

    def test_hit_test_uses_inverse_transform(self):
        """有视口时：屏幕上点到的是"换算回世界坐标之后"的那一层。"""
        viewport = Viewport(offset_x=100.0, offset_y=50.0, zoom=2.0)
        layer = box("world", (10.0, 10.0, 50.0, 50.0))
        view = View(layers=(layer,), viewport=viewport)

        # 世界 (10,10)-(60,60) → 屏幕 (120,70)-(220,170)：点 (150,100) 命中。
        self.assertEqual(view.hit_test((150.0, 100.0)).id, "world")
        # 屏幕 (110,60) 在屏幕矩形之外（也就在世界矩形之外）。
        self.assertIsNone(view.hit_test((110.0, 60.0)))

    def test_fixed_layers_ignore_viewport(self):
        """fixed 层（HUD / 按钮）按屏幕坐标判定，不跟着视口动。"""
        viewport = Viewport(offset_x=500.0, offset_y=500.0, zoom=4.0)
        hud = Layer(id="hud", rect=(0.0, 0.0, 100.0, 40.0), kind="rect",
                    click=ClickResult(kind="click"), fixed=True)
        view = View(layers=(hud,), viewport=viewport)
        self.assertEqual(view.hit_test((10.0, 10.0)).id, "hud")
        self.assertIsNone(view.hit_test((150.0, 200.0)))

    def test_polygon_hit_test_with_viewport(self):
        """多边形层在缩放平移之后，仍然按"点在不在形内"判定。"""
        viewport = Viewport(offset_x=50.0, offset_y=0.0, zoom=2.0)
        layer = hexagon((100.0, 100.0), 40.0, "hex")
        view = View(layers=(layer,), viewport=viewport)

        # 中心：世界 (100,100) → 屏幕 (250,200)。
        self.assertEqual(view.hit_test((250.0, 200.0)).id, "hex")
        # 世界坐标里外接矩形的左上角在形状外：屏幕坐标也要判不中。
        corner = viewport.to_screen((layer.rect[0] + 1.0, layer.rect[1] + 1.0))
        self.assertIsNone(view.hit_test(corner))


class TestHitTest(unittest.TestCase):
    """命中判定。"""

    def test_hit_inside_and_outside(self):
        """点在框内命中，点在框外没有命中。"""
        view = View(layers=(box("a", (10, 10, 50, 50)),))
        self.assertEqual(view.hit_test((30.0, 30.0)).id, "a")
        self.assertEqual(view.hit_test((10.0, 10.0)).id, "a")  # 边界算内
        self.assertIsNone(view.hit_test((5.0, 5.0)))

    def test_topmost_wins(self):
        """重叠时取最上面的层（z 大的）。"""
        view = View(
            layers=(
                box("bottom", (0, 0, 100, 100), z=0),
                box("top", (50, 50, 100, 100), z=10),
            )
        )
        self.assertEqual(view.hit_test((60.0, 60.0)).id, "top")
        self.assertEqual(view.hit_test((10.0, 10.0)).id, "bottom")

    def test_same_z_keeps_declaration_order(self):
        """z 相同时，后声明的画在上面，也先被命中。"""
        view = View(layers=(box("first", (0, 0, 100, 100)), box("second", (0, 0, 100, 100))))
        self.assertEqual([layer.id for layer in view.sorted_layers()], ["first", "second"])
        self.assertEqual(view.hit_test((10.0, 10.0)).id, "second")

    def test_layers_without_click_are_not_hit(self):
        """不吃点击的层（纯显示）不算命中。"""
        view = View(layers=(box("display", (0, 0, 100, 100), click=False),))
        self.assertIsNone(view.hit_test((10.0, 10.0)))

    def test_pointer_target_and_blocker(self):
        """pointer_target 找滚轮目标；命中纯阻挡层时点击 / 滚轮都被挡住。"""
        log = Layer(id="log", rect=(0, 0, 200, 200), kind="rect",
                    wheel=WheelResult(kind="scroll_log"))
        plain = View(layers=(box("map", (0, 0, 800, 600)), log))
        self.assertEqual(plain.pointer_target((50.0, 50.0), attr="wheel").id, "log")
        self.assertEqual(plain.hit_test((10.0, 10.0)).id, "map")

        blocker = Layer(id="blocker", rect=(0, 0, 800, 600), kind="rect",
                        visible=False, blocks_pointer=True, z=10)
        confirm = Layer(id="confirm", rect=(300, 500, 200, 40), kind="rect",
                        click=ClickResult(kind="close"), z=20)
        blocked = View(layers=(box("map", (0, 0, 800, 600)), blocker, confirm))
        self.assertEqual(blocked.pointer_blocker((50.0, 50.0)).id, "blocker")
        self.assertIsNone(blocked.hit_test((50.0, 50.0)))       # 地图被阻挡层挡住
        self.assertEqual(blocked.hit_test((350.0, 510.0)).id, "confirm")   # 上面的按钮仍可点

    def test_escape_target_takes_the_topmost(self):
        """Esc 目标取最上面那个声明了 escape 的层（弹窗接管 Esc）。"""
        bottom = Layer(id="bottom", rect=(0, 0, 100, 100),
                       escape=ClickResult(kind="close_bottom"))
        top = Layer(id="top", rect=(0, 0, 100, 100), z=10,
                    escape=ClickResult(kind="close_top"))
        view = View(layers=(bottom, top))
        self.assertEqual(view.escape_target().id, "top")
        self.assertIsNone(View().escape_target())

    def test_bad_point_rejected(self):
        """坐标写法不对直接报错。"""
        view = View()
        with self.assertRaises(TypeError):
            view.hit_test((1.0,))
        with self.assertRaises(TypeError):
            view.hit_test((1.0, True))

    def test_polygon_hit_test_ignores_bounding_box_corners(self):
        """六边形按形状判定：外接矩形的角（在框内、形外）不算命中。"""
        layer = hexagon((100.0, 100.0), 40.0)
        view = View(layers=(layer,))
        self.assertEqual(view.hit_test((100.0, 100.0)).id, "hex")   # 中心命中
        x, y, width, height = layer.rect
        self.assertIsNone(view.hit_test((x + 1, y + 1)))           # 左上角：框内、形外

    def test_overlapping_hexagons_hit_the_right_one(self):
        """相邻六边形的外接矩形重叠，命中判定仍然落在形状所在的那一个上。"""
        # 中心 (0,0) 与它的相邻格 (√3·size/2, 1.5·size)：外接矩形在 (0..34.6, 20..40) 重叠，
        # 但两个形状本身只共享一条边。
        size = 40.0
        on_top = hexagon((0.0, 0.0), size, "on_top", z=1)
        below = hexagon((math.sqrt(3.0) * size / 2.0, 1.5 * size), size, "below", z=0)
        view = View(layers=(on_top, below))

        # 这一点在两个外接矩形里，但形状上只属于 below（按矩形判定会错点到 on_top）。
        point = (25.0, 35.0)
        self.assertFalse(on_top.contains(point), "这个点不该落在上层的六边形里")
        self.assertTrue(below.contains(point), "这个点该落在下面那个六边形里")
        self.assertEqual(view.hit_test(point).id, "below")


if __name__ == "__main__":
    unittest.main()
