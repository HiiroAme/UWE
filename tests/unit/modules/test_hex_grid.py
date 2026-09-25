"""pack_hex_grid 模块的几何函数单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/modules/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 节点键的生成与还原（含负数）；
  - 六个邻居、距离（相邻 1、自身 0、对称）；
  - 半径内的格子数量与顺序确定；
  - 相邻关系表：只连组内格子、顺序确定、重复坐标报错；
  - 网格界面件的两个变体：方块格子（plain）与真六边形（hexagon）；
  - pack_info.json 里声明的能力 / 变体确实指向脚本里的函数（名字写错就在测试里炸）。

这些函数以前住在引擎源码树的 geometry/ 包里；现在几何属于模块（引擎不含玩法内容），
所以测试也直接对着模块脚本，而不是 import 引擎的内部包。
"""

import json
import math
import unittest
from pathlib import Path

from adapters import PythonScriptLoader
from core.ui import ClickResult

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_FOLDER = REPO_ROOT / "modules" / "pack_hex_grid"
# 模块拆成三份：几何 / 字符地图 / 画面。函数按名字去对的那一份里取。
GRID_SCRIPTS = ("hex_math.py", "hex_text_map.py", "hex_canvas.py")
PACK_INFO = PACK_FOLDER / "pack_info.json"


def grid_function(name: str):
    """取 pack_hex_grid 某一个脚本里的函数（走脚本端口，和引擎同一条路）。

    脚本文件之间不能互相 import，所以拆开之后"名字在哪个文件里"要自己找一下；
    找不到就报错（不让测试悄悄用错文件）。
    """
    for script in GRID_SCRIPTS:
        path = PACK_FOLDER / "scripts" / script
        if f"def {name}(" in path.read_text(encoding="utf-8"):
            return PythonScriptLoader().load(str(path), name)
    raise AssertionError(f"pack_hex_grid 的三个脚本里都没有函数 {name}")


class TestKeys(unittest.TestCase):
    """节点键。"""

    def test_key_roundtrip(self):
        """key_q_r 生成的键能被 parse_key_q_r 还原（含负数）。"""
        key = grid_function("key_q_r")
        parse = grid_function("parse_key_q_r")
        for coord in ((0, 0), (1, -1), (-2, 3), (-1, 0)):
            with self.subTest(coord=coord):
                self.assertEqual(parse(key(*coord)), coord)

    def test_bad_key(self):
        """写法不对的键直接报错。"""
        parse = grid_function("parse_key_q_r")
        for bad in ("0_0", "n0", "n0-0", "n0_x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse(bad)

    def test_bad_types(self):
        """坐标必须是 int（bool 不算）。"""
        key = grid_function("key_q_r")
        with self.assertRaises(TypeError):
            key(1.0, 0)
        with self.assertRaises(TypeError):
            key(True, 0)


class TestNeighborsAndDistance(unittest.TestCase):
    """邻居与距离。"""

    def test_six_neighbors_are_unique_and_adjacent(self):
        """恰好六个互不相同的邻居，且每个的距离都是 1。"""
        neighbors = grid_function("neighbors_hex")
        distance = grid_function("distance_hex")
        around = neighbors(0, 0)
        self.assertEqual(len(around), 6)
        self.assertEqual(len(set(around)), 6)
        for coord in around:
            with self.subTest(coord=coord):
                self.assertEqual(distance((0, 0), coord), 1)

    def test_distance_basics(self):
        """自身距离 0、对称、算例正确。"""
        distance = grid_function("distance_hex")
        self.assertEqual(distance((2, -1), (2, -1)), 0)
        self.assertEqual(distance((0, 0), (2, -1)), 2)
        self.assertEqual(distance((2, -1), (0, 0)), 2)
        self.assertEqual(distance((-1, 0), (1, 0)), 2)

    def test_all_within_count_and_order(self):
        """半径 r 的格子数是 3r(r+1)+1；顺序按 r、q 从小到大固定。"""
        all_within = grid_function("all_within_hex")
        self.assertEqual(len(all_within(0)), 1)
        self.assertEqual(len(all_within(1)), 7)
        self.assertEqual(len(all_within(2)), 19)
        coords = all_within(1)
        self.assertEqual(coords, tuple(sorted(coords, key=lambda pair: (pair[1], pair[0]))))

    def test_bad_radius(self):
        """半径必须是非负整数。"""
        all_within = grid_function("all_within_hex")
        with self.assertRaises(ValueError):
            all_within(-1)
        with self.assertRaises(TypeError):
            all_within(True)


class TestAdjacencyMap(unittest.TestCase):
    """相邻关系表。"""

    def test_center_has_six_neighbors(self):
        """半径 1 的地图：中心连六个，边角连三个。"""
        all_within = grid_function("all_within_hex")
        adjacency_map = grid_function("adjacency_map_from_coords")
        key = grid_function("key_q_r")
        table = adjacency_map(all_within(1))
        self.assertEqual(len(table[key(0, 0)]), 6)
        corner = key(1, 0)
        self.assertEqual(len(table[corner]), 3)
        self.assertIn(key(0, 0), table[corner])

    def test_only_connects_present_cells(self):
        """组内不存在的格子不出现在相邻表里。"""
        adjacency_map = grid_function("adjacency_map_from_coords")
        key = grid_function("key_q_r")
        table = adjacency_map([(0, 0), (1, 0)])
        self.assertEqual(table[key(0, 0)], [key(1, 0)])
        self.assertEqual(table[key(1, 0)], [key(0, 0)])

    def test_duplicate_coordinate_rejected(self):
        """重复坐标报错（避免相邻表里出现重复项）。"""
        adjacency_map = grid_function("adjacency_map_from_coords")
        with self.assertRaises(ValueError):
            adjacency_map([(0, 0), (0, 0)])


class TestPackDeclaration(unittest.TestCase):
    """pack_info.json 与脚本要对得上（模块作者写错名字在测试里就炸）。"""

    def test_variants_point_to_real_functions(self):
        """每个能力的每个变体，都能在它声明的脚本里取到同名函数。"""
        info = json.loads(PACK_INFO.read_text(encoding="utf-8"))
        loader = PythonScriptLoader()
        for capability, spec in info["capabilities"].items():
            script = PACK_FOLDER / spec["script"]
            for variant, function_name in spec["variants"].items():
                with self.subTest(capability=capability, variant=variant):
                    self.assertTrue(callable(loader.load(str(script), function_name)))


class TestTextMap(unittest.TestCase):
    """字符地图：行列 → 轴向、解析、屏障与渡口校验（示例兵棋 M1 要用的那套）。"""

    def test_offset_roundtrip(self):
        """行列 ↔ 轴向：来回换算必须一模一样（地图与相邻关系全靠它对齐）。"""
        to_axial = grid_function("offset_to_axial")
        to_offset = grid_function("axial_to_offset")
        for row in range(8):
            for col in range(8):
                with self.subTest(row=row, col=col):
                    self.assertEqual(to_offset(*to_axial(row, col)), (row, col))

    def test_parse_text_map(self):
        """解析字符地图：注释与空行不算，# 行可以做码表。"""
        parse = grid_function("parse_text_map")
        data = parse("# 码表：p 平原 r 河\np p h\n\n p r p\n")
        self.assertEqual((data["rows"], data["cols"]), (2, 3))
        self.assertEqual(data["cells"][(1, 1)], "r")
        self.assertEqual(len(data["cells"]), 6)

    def test_parse_rejects_ragged_rows(self):
        """各行格数不一致：直接报错（这是手写地图最容易犯的错）。"""
        parse = grid_function("parse_text_map")
        with self.assertRaises(ValueError):
            parse("p p p\np p\n")
        with self.assertRaises(ValueError):
            parse("# 只有注释\n")

    def test_check_barrier_ok_and_gap(self):
        """屏障校验：整整一行河 → 连通且封锁；挖一个洞 → 两条都失败。"""
        check = grid_function("check_barrier")
        solid = {(r, c): ("r" if r == 2 else "p") for r in range(5) for c in range(5)}
        good = check(solid, "r")
        self.assertTrue(good["connected"], good)
        self.assertTrue(good["blocks"], good)
        self.assertEqual(good["problems"], [])

        gap = dict(solid)
        gap[(2, 2)] = "p"
        broken = check(gap, "r")
        self.assertFalse(broken["connected"])
        self.assertFalse(broken["blocks"])
        self.assertEqual(len(broken["problems"]), 2)

    def test_ford_pairs_need_a_partner(self):
        """渡口成对：两岸各一格、中间隔一格河水；落单的渡口要报出来。"""
        fords = grid_function("ford_pairs")
        cells = {(r, c): ("r" if r == 2 else "p") for r in range(5) for c in range(5)}
        cells[(1, 1)] = "f"
        cells[(3, 1)] = "f"
        cells[(1, 3)] = "f"          # 落单
        result = fords(cells, "f", "r")
        self.assertEqual(result["pairs"], [((1, 1), (2, 1), (3, 1))])
        self.assertEqual(len(result["problems"]), 1)


class TestHexWidget(unittest.TestCase):
    """网格界面件：两个画法都要能画出可点的格子。"""

    @staticmethod
    def _nodes():
        """两个相邻格子的坐标表（用真实的坐标口径）。"""
        return {"n0_0": {"q": 0, "r": 0}, "n1_0": {"q": 1, "r": 0}}

    def test_plain_variant_draws_text_cells(self):
        """方块变体：一格一个文字层（带底色），点击挂在文字层上。"""
        click = ClickResult(kind="select", data={"unit": "u1"})
        make = grid_function("hex_grid_layers")
        layers = make(nodes=self._nodes(), size=20.0, center=(0.0, 0.0),
                      items={"n0_0": {"click": click}})
        self.assertEqual([layer.kind for layer in layers], ["text", "text"])
        by_id = {layer.id: layer for layer in layers}
        self.assertIs(by_id["grid:n0_0"].click, click)

    def test_hexagon_variant_draws_six_sided_polygons(self):
        """六边形变体：一格一个六顶点多边形，尺寸符合尖顶排法的口径。"""
        make = grid_function("hex_grid_hexagon_layers")
        layers = make(nodes=self._nodes(), size=20.0, center=(0.0, 0.0), outline_width=2.0)
        by_id = {layer.id: layer for layer in layers}

        cell = by_id["grid:n0_0"]
        self.assertEqual(cell.kind, "polygon")
        self.assertEqual(len(cell.points), 6)
        # 外接圆半径 size → 宽 √3·size、高 2·size；中心就是 to_pixel 给的那个点。
        _, _, width, height = cell.rect
        self.assertAlmostEqual(width, math.sqrt(3.0) * 20.0, places=6)
        self.assertAlmostEqual(height, 40.0, places=6)
        self.assertAlmostEqual(cell.center[0], 0.0, places=6)
        self.assertAlmostEqual(cell.center[1], 0.0, places=6)
        self.assertAlmostEqual(by_id["grid:n1_0"].center[0], math.sqrt(3.0) * 20.0, places=6)

    def test_hexagon_grid_lines_and_click_target(self):
        """格线是**一条原语**画出来的（填充 + 描边）；点击挂在多边形上，文字层不吃点击。"""
        click = ClickResult(kind="move", data={"to": "n0_0"})
        make = grid_function("hex_grid_hexagon_layers")
        layers = make(nodes=self._nodes(), size=20.0, outline_width=3.0,
                      items={"n0_0": {"click": click, "color": (9, 9, 9, 255)}})
        by_id = {layer.id: layer for layer in layers}

        cell = by_id["grid:n0_0"]
        text = by_id["grid:n0_0:text"]
        self.assertEqual(cell.color, (9, 9, 9, 255))     # items 里的颜色覆盖填充色
        self.assertEqual(cell.outline_width, 3.0)        # 描边宽度 = 格线宽
        self.assertIsNotNone(cell.outline_color)         # 有描边色（不再是"大一圈的实心多边形"）
        self.assertIs(cell.click, click)                 # 点击挂在多边形上
        self.assertIsNone(text.click)
        self.assertEqual(text.color[3], 0)               # 文字层不铺底色

    def test_hexagon_cells_are_centered_where_the_coords_say(self):
        """一格只有一个多边形：它的中心就是坐标算出来的那个点（不再有"内外两层"要对齐）。"""
        make = grid_function("hex_grid_hexagon_layers")
        layers = make(nodes=self._nodes(), size=20.0, center=(100.0, 50.0), outline_width=2.0)
        by_id = {layer.id: layer for layer in layers}

        polygons = [l for l in layers if l.kind == "polygon"]
        self.assertEqual(len(polygons), 2)               # 两格 = 两个多边形（+ 两个文字层）
        for node_key in self._nodes():
            cell = by_id[f"grid:{node_key}"]
            self.assertEqual(len(cell.points), 6)
            self.assertAlmostEqual(cell.rect[2], math.sqrt(3.0) * 20.0, places=6)

    def test_hexagon_without_outline_still_clickable(self):
        """outline_width=0：只铺一层（填充色），照样能点。"""
        click = ClickResult(kind="select", data={"unit": "u1"})
        make = grid_function("hex_grid_hexagon_layers")
        layers = make(nodes=self._nodes(), size=20.0, outline_width=0,
                      items={"n0_0": {"click": click}})
        by_id = {layer.id: layer for layer in layers}
        self.assertEqual(len(layers), 4)                 # 两格：一格两层（多边形 + 文字）
        self.assertIs(by_id["grid:n0_0"].click, click)


if __name__ == "__main__":
    unittest.main()
