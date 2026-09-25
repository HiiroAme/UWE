"""示例兵棋（mods/demo_river）地图的验收测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/mods/
运行：在仓库根执行 `python run_tests.py`。

这个测试把示例兵棋设计文档 §3.2 的两道校验钉住，另外核对附录 A 的布局：
  - 地图能解析：35 列 × 20 行 = 700 格，每行格数一致；
  - 行列 ↔ 轴向坐标能来回换算，而且 700 格一一对应（不撞车）；
  - 河**连成一条**、并且**真的挡住南北**（不走渡口过不去）；
  - 渡口**成对**：两岸各一格、中间隔一格河水，一共 3 对，位置就是设计文档里写的三处；
  - 关键地形与胜利点：村庄 7 格、胜利点 1 格且在最南端。

模块脚本走的是脚本端口（和引擎加载模块是同一条路），不 import 模块内部。
"""

import unittest
from pathlib import Path

from adapters import PythonScriptLoader

REPO_ROOT = Path(__file__).resolve().parents[3]
GRID_SCRIPTS = ("hex_math.py", "hex_text_map.py", "hex_canvas.py")
GRID_FOLDER = REPO_ROOT / "modules" / "pack_hex_grid" / "scripts"
MAP_FILE = REPO_ROOT / "mods" / "demo_river" / "maps" / "luo_chuan.txt"

# 设计文档里写死的三处渡口（北岸格、河水格、南岸格）。
EXPECTED_FORDS = [((8, 5), (9, 5), (10, 5)),
                  ((8, 17), (9, 17), (10, 17)),
                  ((8, 29), (9, 29), (10, 29))]


def grid_function(name: str):
    """取 pack_hex_grid 某一个脚本里的函数（走脚本端口）。"""
    for script in GRID_SCRIPTS:
        path = GRID_FOLDER / script
        if f"def {name}(" in path.read_text(encoding="utf-8"):
            return PythonScriptLoader().load(str(path), name)
    raise AssertionError(f"pack_hex_grid 的三个脚本里都没有函数 {name}")


class TestRiverMap(unittest.TestCase):
    """示例兵棋 v1 地图（洛川平原 · 柳浦村）。"""

    @classmethod
    def setUpClass(cls):
        """读一次地图，后面反复用。"""
        cls.data = grid_function("parse_text_map")(MAP_FILE.read_text(encoding="utf-8"))
        cls.cells = cls.data["cells"]

    def test_map_shape_and_terrain_counts(self):
        """尺寸与地形数量：35×20 = 700 格，各类地形数量对得上。"""
        self.assertEqual((self.data["rows"], self.data["cols"]), (20, 35))
        self.assertEqual(len(self.cells), 700)

        counts = {}
        for code in self.cells.values():
            counts[code] = counts.get(code, 0) + 1
        self.assertEqual(counts.get("r"), 37)     # 河
        self.assertEqual(counts.get("f"), 6)      # 3 对渡口
        self.assertEqual(counts.get("v"), 7)      # 村庄（不含胜利点）
        self.assertEqual(counts.get("V"), 1)      # 胜利点
        self.assertEqual(counts.get("h"), 17)     # 丘陵
        self.assertEqual(sum(counts.values()), 700)

    def test_offset_to_axial_is_one_to_one(self):
        """行列 → 轴向：700 格换算后互不重叠，而且能原样换回来。"""
        to_axial = grid_function("offset_to_axial")
        to_offset = grid_function("axial_to_offset")
        axial = {to_axial(row, col) for (row, col) in self.cells}
        self.assertEqual(len(axial), 700)
        for (row, col) in self.cells:
            self.assertEqual(to_offset(*to_axial(row, col)), (row, col))

    def test_river_connects_and_blocks(self):
        """河必须连成一条，而且不走渡口时南北不连通（设计文档 §3.2）。"""
        result = grid_function("check_barrier")(self.cells, "r")
        self.assertTrue(result["connected"], result)
        self.assertTrue(result["blocks"], result)
        self.assertEqual(result["problems"], [])

    def test_fords_are_paired(self):
        """渡口成对：两岸各一格、中间隔一格河水，一共三处、位置固定。"""
        result = grid_function("ford_pairs")(self.cells, "f", "r")
        self.assertEqual(result["problems"], [])
        self.assertEqual(sorted(result["pairs"]), sorted(EXPECTED_FORDS))

    def test_victory_point_is_the_southern_most_village(self):
        """胜利点在村庄地块上，而且在最南端（离攻方出发点最远）。"""
        victory = [pos for pos, code in self.cells.items() if code == "V"]
        self.assertEqual(len(victory), 1)
        vp_row, vp_col = victory[0]
        village_rows = [row for (row, _), code in self.cells.items() if code in ("v", "V")]
        self.assertEqual(vp_row, max(village_rows))
        self.assertEqual((vp_row, vp_col), (19, 15))


if __name__ == "__main__":
    unittest.main()
