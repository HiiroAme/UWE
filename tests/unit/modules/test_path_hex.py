"""pack_path_hex 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/modules/
运行：在仓库根执行 `python run_tests.py`。

覆盖（对应《示例兵棋》§7.3 通行等级与 §八 控制区，但模块本身不含任何玩法名词）：
  - 可达范围按地形消耗算（不是按格数）；
  - 不可进入的地形进不去；
  - **可以穿过友军，但不能停在他身上**（can_pass / can_stop 拆开）；
  - 特例通道（渡口）能跨过去，普通相邻关系里它们不相邻；
  - 进入敌方控制区就停住；回合初在敌方控制区时先出来，之后可继续走；
  - 路径还原；控制区计算（混乱单位不产生控制区）。
  - 通行等级（levels.from_state）：地形消耗 / 不可通行 / 友军"可穿不可停" / 敌军硬阻挡 / 被消灭的不占格。
"""

import unittest
from pathlib import Path

from adapters import PythonScriptLoader

REPO_ROOT = Path(__file__).resolve().parents[3]
PATHS_SCRIPT = REPO_ROOT / "modules" / "pack_path_hex" / "scripts" / "paths.py"


def path_function(name: str):
    """取 pack_path_hex 的 paths.py 里的一个函数（走脚本端口）。"""
    return PythonScriptLoader().load(str(PATHS_SCRIPT), name)


def row_neighbors(cells):
    """把一串格子连成一条链（相邻表就这么简单，测试够用）。"""
    table = {}
    for index, cell in enumerate(cells):
        table[cell] = []
        if index > 0:
            table[cell].append(cells[index - 1])
        if index + 1 < len(cells):
            table[cell].append(cells[index + 1])
    return table


class TestReachable(unittest.TestCase):
    """可达范围与通行等级。"""

    def test_cost_is_by_terrain_not_by_steps(self):
        """消耗按地形算：预算 2 走到第二格就用完了，进不了"丘陵"那一格（要 2 点）。"""
        reachable = path_function("reachable_cells")
        chain = ["a", "b", "c", "d"]
        result = reachable(
            start="a", budget=2, neighbors=row_neighbors(chain),
            enter_cost={"a": 1, "b": 1, "c": 2, "d": 1},
        )
        self.assertEqual(result["costs"], {"a": 0.0, "b": 1.0})

    def test_impassable_cells_are_skipped(self):
        """没列进 enter_cost 的格子 = 不可进入（河流）。"""
        reachable = path_function("reachable_cells")
        chain = ["a", "b", "c"]
        result = reachable(start="a", budget=9, neighbors=row_neighbors(chain),
                           enter_cost={"a": 1, "b": 1})
        self.assertEqual(sorted(result["costs"]), ["a", "b"])

    def test_can_pass_but_not_stop(self):
        """友军占的格：能穿过去（继续走），但不能当落点。"""
        reachable = path_function("reachable_cells")
        chain = ["a", "b", "c"]
        neighbors = row_neighbors(chain)
        result = reachable(start="a", budget=3, neighbors=neighbors,
                           enter_cost={"a": 1, "b": 1, "c": 1},
                           can_pass={"a", "b", "c"}, can_stop={"a", "c"})
        self.assertIn("c", result["costs"])          # 穿过 b 到了 c
        self.assertNotIn("b", result["costs"])       # 但不能停在 b
        self.assertEqual(result["previous"]["c"], "b")

    def test_extra_moves_are_the_fords(self):
        """特例通道（渡口）：不靠相邻关系也能跨过去，按它自己的消耗算。"""
        reachable = path_function("reachable_cells")
        result = reachable(
            start="north_ford", budget=3,
            neighbors={"north_ford": [], "south_ford": []},
            enter_cost={"north_ford": 1, "south_ford": 1},
            extra_moves=[{"from": "north_ford", "to": "south_ford", "cost": 3}],
        )
        self.assertEqual(result["costs"]["south_ford"], 3.0)

    def test_zoc_stops_movement(self):
        """进入敌方控制区就停住（还能停在那儿，但不能再往下走）。"""
        reachable = path_function("reachable_cells")
        chain = ["a", "b", "c", "d"]
        result = reachable(start="a", budget=9, neighbors=row_neighbors(chain),
                           enter_cost={cell: 1 for cell in chain},
                           zoc={"b"})
        self.assertIn("b", result["costs"])            # 可以停在控制区里
        self.assertNotIn("c", result["costs"])         # 但不能穿过它继续走

    def test_must_leave_zoc_first(self):
        """回合初在敌方控制区：第一步必须先出来，之后可以继续用剩余移动力走。"""
        reachable = path_function("reachable_cells")
        chain = ["a", "b", "c", "d"]
        result = reachable(start="b", budget=9, neighbors=row_neighbors(chain),
                           enter_cost={cell: 1 for cell in chain},
                           zoc={"b"}, must_leave_zoc=True)
        self.assertEqual(sorted(result["costs"]), ["a", "c", "d"])
        self.assertEqual(result["costs"]["d"], 2.0)      # 先出到 c（1 点），再走到 d（共 2 点）
        short = reachable(start="b", budget=1, neighbors=row_neighbors(chain),
                          enter_cost={cell: 1 for cell in chain},
                          zoc={"b"}, must_leave_zoc=True)
        self.assertEqual(sorted(short["costs"]), ["a", "c"])   # 预算只够出控制区

    def test_path_reconstruction(self):
        """路径还原：从起点到终点一路串起来；走不到就是空列表。"""
        reachable = path_function("reachable_cells")
        path_to = path_function("path_to")
        chain = ["a", "b", "c"]
        result = reachable(start="a", budget=3, neighbors=row_neighbors(chain),
                           enter_cost={"a": 1, "b": 1, "c": 1})
        self.assertEqual(path_to(result["previous"], "a", "c"), ["a", "b", "c"])
        self.assertEqual(path_to(result["previous"], "a", "zzz"), [])


class TestZoc(unittest.TestCase):
    """控制区计算。"""

    def test_zoc_is_adjacent_cells_and_skips_disordered(self):
        """控制区 = 相邻格；混乱单位不产生控制区；一格可以同时属于双方。"""
        zoc = path_function("zoc_cells")
        neighbors = {"n0_0": ["n1_0", "n-1_0"], "n1_0": ["n0_0"], "n-1_0": ["n0_0"]}
        units = {
            "red_1": {"at": "n0_0", "side": "red"},
            "blue_1": {"at": "n1_0", "side": "blue"},
            "blue_2": {"at": "n-1_0", "side": "blue", "disordered": True},
        }
        table = zoc(units, neighbors, skip=["blue_2"])
        self.assertEqual(table["red"], ["n-1_0", "n1_0"])
        self.assertEqual(table["blue"], ["n0_0"])


class TestLevels(unittest.TestCase):
    """通行等级：把"地形 + 占位"翻成（进一格花多少 / 能穿 / 能停）。"""

    def test_cell_kinds_empty_ally_enemy(self):
        """三种格子各归各位：空地能穿能停；友军占的能穿不能停；敌军占的连进都不行。"""
        levels = path_function("levels_from_state")
        nodes = {"a": {"passable": True, "move_cost": 1},
                 "b": {"passable": True, "move_cost": 1},
                 "c": {"passable": True, "move_cost": 1}}
        units = {"me": {"at": "a", "side": "north"},
                 "ally": {"at": "b", "side": "north"},
                 "foe": {"at": "c", "side": "south"}}
        enter_cost, can_pass, can_stop = levels(nodes, units, "me")
        # 敌军的 c 仍留在代价表里（代价照抄地形），但不在 can_pass 里 —— 真正拦人的是 can_pass。
        self.assertEqual(enter_cost, {"a": 1.0, "b": 1.0, "c": 1.0})
        self.assertEqual(can_pass, {"a", "b"})
        self.assertEqual(can_stop, {"a"})                    # 友军身上不能停

    def test_terrain_cost_and_impassable(self):
        """地形消耗照抄节点（没写就给缺省 1）；不可通行的格子既不进表也不能穿。"""
        levels = path_function("levels_from_state")
        nodes = {"a": {"passable": True},
                 "hill": {"passable": True, "move_cost": 2},
                 "river": {"passable": False}}
        units = {"me": {"at": "a", "side": "north"}}
        enter_cost, can_pass, can_stop = levels(nodes, units, "me")
        self.assertEqual(enter_cost, {"a": 1.0, "hill": 2.0})
        self.assertNotIn("river", can_pass)
        self.assertNotIn("river", can_stop)

    def test_dead_units_do_not_occupy(self):
        """被消灭的单位不占格：它那一格照常能停。"""
        levels = path_function("levels_from_state")
        nodes = {"a": {"passable": True}, "b": {"passable": True}}
        units = {"me": {"at": "a", "side": "north"},
                 "dead": {"at": "b", "side": "south", "destroyed": True}}
        enter_cost, can_pass, can_stop = levels(nodes, units, "me")
        self.assertIn("b", enter_cost)
        self.assertIn("b", can_stop)

    def test_moving_unit_does_not_block_itself(self):
        """正在移动的单位不算占位（"不能跟自己堆叠"要排除掉自己）。"""
        levels = path_function("levels_from_state")
        nodes = {"a": {"passable": True}}
        units = {"me": {"at": "a", "side": "north"}}
        _, _, can_stop = levels(nodes, units, "me")
        self.assertIn("a", can_stop)

    def test_field_names_are_parameters(self):
        """字段名是参数：换个 Mod 的写法（cell / team / price / open）照样能用。"""
        levels = path_function("levels_from_state")
        nodes = {"a": {"open": True, "price": 3}, "b": {"open": True, "price": 1}}
        units = {"me": {"cell": "a", "team": "north"}, "rival": {"cell": "b", "team": "south"}}
        enter_cost, can_pass, _ = levels(
            nodes, units, "me", cost_key="price", passable_key="open",
            occupant_key="cell", side_key="team")
        self.assertEqual(enter_cost, {"a": 3.0, "b": 1.0})    # b 被敌军占着：在表里但穿不过去
        self.assertEqual(can_pass, {"a"})


class TestRetreat(unittest.TestCase):
    """撤退：每步比上一步更远一格；退不动就是空列表（调用方按"被消灭"处理）。"""

    def _line(self):
        """一条 5 格的直线（a-b-c-d-e），战斗点是 b。"""
        chain = ["a", "b", "c", "d", "e"]
        return chain, row_neighbors(chain)

    def test_retreat_walks_away_step_by_step(self):
        """退 2 格：只能往"更远"的方向走（c→d，不可能回头）。"""
        retreat = path_function("retreat_path")
        chain, neighbors = self._line()
        path = retreat(start="c", battle="b", steps=2, neighbors=neighbors,
                       can_enter=set(chain), rand_int=lambda low, high: 0)
        self.assertEqual(path, ["c", "d", "e"])

    def test_no_way_out_means_eliminated(self):
        """前面被堵住（只剩起点能进）：退不了 → 空列表。"""
        retreat = path_function("retreat_path")
        chain, neighbors = self._line()
        path = retreat(start="c", battle="b", steps=2, neighbors=neighbors,
                       can_enter={"c"}, rand_int=lambda low, high: 0)
        self.assertEqual(path, [])

    def test_last_step_must_land_on_stop_cell(self):
        """最后一步必须落在"能停"的格子上（友军占的格只能穿过）。"""
        retreat = path_function("retreat_path")
        chain, neighbors = self._line()
        path = retreat(start="c", battle="b", steps=2, neighbors=neighbors,
                       can_enter=set(chain), can_stop={"c", "d"},
                       rand_int=lambda low, high: 0)
        self.assertEqual(path, [])          # e 不能停 → 这条退路不合法


if __name__ == "__main__":
    unittest.main()
