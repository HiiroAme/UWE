"""示例兵棋的移动验收测试（标准库 unittest）。

位置：tests/unit/mods/
运行：在仓库根执行 `python run_tests.py`。

覆盖（对应《示例兵棋》§7 移动与 §八 控制区）：
  - 沿路径走：位置变了、按地形扣移动力；
  - 河流进不去；
  - **渡口才能过河**（成对渡口之间花 3 点）；
  - 可以穿过友军，但不能停在友军身上；
  - 进入敌方控制区行动力归零。
"""

import unittest
from pathlib import Path

from adapters import LocalFileSystem, PythonScriptLoader
from core.pipeline import Input
from modload import ModLoader
from shell import GameSession

REPO_ROOT = Path(__file__).resolve().parents[3]
MODS_ROOT = REPO_ROOT / "mods"
MODULES_ROOT = REPO_ROOT / "modules"


def load_river():
    """加载示例兵棋 Mod。"""
    loader = ModLoader(LocalFileSystem(), PythonScriptLoader(), str(MODS_ROOT),
                       module_roots=[str(MODULES_ROOT)])
    return loader.load(loader.load_info(str(MODS_ROOT / "demo_river")))


class TestMove(unittest.TestCase):
    """移动服务：规则全在模块里，这里只看结果。"""

    def setUp(self):
        """开一局，并准备一张"行列 → 节点键"的表，方便摆位置。"""
        self.session = GameSession(load_river(), files=LocalFileSystem(), seed=1)
        self.by_row_col = {(node["row"], node["col"]): key
                           for key, node in self.session.state["nodes"].items()}

    def _send(self, kind, data):
        """送一条输入并结算，返回那条命令的结果。"""
        self.session.submit_input(Input(kind, data, 1.0, "ui"))
        result = self.session.settle(["turn:1"])
        return result.commands[0]

    def _place(self, unit_key, row, col, *, move_points=9):
        """把某个单位直接摆到某格（测试用，绕开移动规则）。"""
        unit = self.session.state["units"][unit_key]
        unit["at"] = self.by_row_col[(row, col)]
        unit["move_left"] = move_points

    def test_move_along_plain_costs_terrain(self):
        """走两格平原：位置对了，移动力按地形扣（9 → 7）。"""
        self._place("north_1", 1, 6, move_points=9)
        result = self._send("move", {"unit": "north_1", "to": self.by_row_col[(3, 6)]})
        self.assertTrue(result.committed)
        self.assertEqual(self.session.state["units"]["north_1"]["at"], self.by_row_col[(3, 6)])
        self.assertEqual(self.session.state["units"]["north_1"]["move_left"], 7)

    def test_river_cannot_be_entered(self):
        """河流不可进入：站在河边也过不去。"""
        self._place("north_1", 7, 6)                       # 河边（第 8 行是河）
        result = self._send("move", {"unit": "north_1", "to": self.by_row_col[(8, 6)]})
        self.assertTrue(result.committed)                  # 命令本身不算被拒
        self.assertEqual(self.session.state["units"]["north_1"]["at"], self.by_row_col[(7, 6)])

    def test_ford_is_the_only_way_across(self):
        """渡口：站在北岸渡口，能花 3 点渡到南岸那格。"""
        self._place("north_1", 7, 4)                       # 西侧渡口·北岸
        result = self._send("move", {"unit": "north_1", "to": self.by_row_col[(9, 4)]})
        self.assertTrue(result.committed)
        unit = self.session.state["units"]["north_1"]
        self.assertEqual(unit["at"], self.by_row_col[(9, 4)])
        self.assertEqual(unit["move_left"], 6)                  # 9 - 3（渡河消耗）

    def test_can_pass_friend_but_not_stop_on_it(self):
        """穿过友军可以，停在友军身上不行。"""
        self._place("north_1", 1, 6)
        self._place("north_2", 1, 7)                       # 友军挡在中间
        blocked = self._send("move", {"unit": "north_1", "to": self.by_row_col[(1, 7)]})
        self.assertTrue(blocked.committed)
        self.assertEqual(self.session.state["units"]["north_1"]["at"], self.by_row_col[(1, 6)])

        passed = self._send("move", {"unit": "north_1", "to": self.by_row_col[(1, 8)]})
        self.assertTrue(passed.committed)
        self.assertEqual(self.session.state["units"]["north_1"]["at"], self.by_row_col[(1, 8)])

    def test_entering_enemy_zoc_uses_all_movement(self):
        """进入敌方控制区：能进去，但行动力立刻归零。"""
        self._place("north_1", 17, 11, move_points=9)      # 南岸，靠近守军
        self._place("south_1", 17, 10)                     # 守军在旁边 → 控制区盖住 (17,11) 周围
        result = self._send("move", {"unit": "north_1", "to": self.by_row_col[(18, 10)]})
        self.assertTrue(result.committed)
        unit = self.session.state["units"]["north_1"]
        self.assertEqual(unit["at"], self.by_row_col[(18, 10)])
        self.assertEqual(unit["move_left"], 0)                  # ZOC-1：进来就停

    def test_leaving_enemy_zoc_keeps_remaining_movement(self):
        """回合初在敌方控制区：第一步离开只按地形扣点，剩余行动力保留（ZOC-2）。"""
        session = self.session
        self._place("north_1", 17, 11, move_points=5)
        self._place("south_1", 17, 10)
        session.state["units"]["north_1"]["zoc_start"] = True
        functions = session.loaded.functions
        zoc = functions["pack_path_hex.zoc.from_units"](
            session.state["units"], session.state["adjacency"], [])
        enemy_zoc = set(zoc.get("south", ()))
        occupied = {item.get("at") for item in session.state["units"].values() if item.get("at")}
        start = session.state["units"]["north_1"]["at"]
        target = next(cell for cell in session.state["adjacency"][start]
                      if cell not in enemy_zoc and cell not in occupied
                      and session.state["nodes"][cell].get("passable"))
        cost = session.state["nodes"][target].get("move_cost", 1)

        result = self._send("move", {"unit": "north_1", "to": target})
        self.assertTrue(result.committed)
        moved = session.state["units"]["north_1"]
        self.assertEqual(moved["at"], target)
        self.assertEqual(moved["move_left"], 5 - cost)     # 不是 0：离开控制区不额外清零

    def test_two_step_interaction_select_preview_move(self):
        """界面两步走：先选中 → 点可达格只预览（不动）→ 再点同格才移动。"""
        selected = self._send("select", {"unit": "north_1"})
        self.assertTrue(selected.committed)
        context = self.session.runtime.context
        self.assertEqual(context.get("demo_river.selected", ""), "north_1")

        target = self.by_row_col[(3, 6)]
        preview = self._send("preview", {"to": target})
        self.assertTrue(preview.committed)
        self.assertEqual(context.get("demo_river.pending", ""), target)
        self.assertEqual(self.session.state["units"]["north_1"]["at"], self.by_row_col[(1, 6)])

        moved = self._send("move", {"unit": "north_1", "to": target})
        self.assertTrue(moved.committed)
        self.assertEqual(self.session.state["units"]["north_1"]["at"], target)
        self.assertEqual(context.get("demo_river.pending", ""), "")
        self.assertEqual(context.get("demo_river.selected", ""), "north_1")

    def test_disordered_unit_cannot_move_or_fight(self):
        """混乱单位：不能移动（规则拦）、不能参战（声明服务拦）——设计文档 M-2 / M-6。"""
        session = self.session
        session.state["units"]["north_1"]["disordered"] = True
        moved = self._send("move", {"unit": "north_1", "to": self.by_row_col[(3, 6)]})
        self.assertFalse(moved.committed)
        self.assertEqual(moved.rule_id, "demo_river:rule:not_disordered")

        session.state["units"]["north_10"]["disordered"] = True
        session.state["stage"] = "attack"       # 先过"攻击阶段"这条规则，才轮得到服务把关（G-16）
        declared = self._send("declare_battle", {"units": ["north_10"], "target": "south_1"})
        self.assertTrue(declared.committed)                    # 命令本身不算被拒
        self.assertEqual(session.state["battles"], [])         # 但服务没把它放进声明列表

    def test_attack_phase_actions_need_the_attack_stage(self):
        """G-16：移动阶段里"声明 / 结算 / 挺进 / 加入参战名单"都被规则拦（把关不只在界面里）。"""
        session = self.session
        self.assertEqual(session.state["stage"], "move")
        cases = (("declare_battle", {"units": ["north_1"], "target": "south_1"}),
                 ("resolve_next", {}),
                 ("advance", {"unit": "north_1"}),
                 ("toggle_attacker", {"unit": "north_1"}))
        for kind, data in cases:
            with self.subTest(kind=kind):
                result = self._send(kind, data)
                self.assertFalse(result.committed)
                self.assertEqual(result.rule_id, "demo_river:rule:stage_is_attack")

    def test_stage_advance_and_pass_control(self):
        """阶段推进与换控制方：换过去时顺手刷新 zoc_start / fought。"""
        # 把一个守军摆到攻方单位旁边，换个控制方之后它应当被标记为"在敌方控制区里"。
        self._place("north_1", 17, 11)
        self._place("south_1", 17, 10)
        self.session.state["units"]["south_1"]["fought"] = True

        attack = self._send("to_attack", {})
        self.assertTrue(attack.committed)
        self.assertEqual(self.session.state["stage"], "attack")

        passed = self._send("pass_control", {})
        self.assertTrue(passed.committed)
        state = self.session.state
        self.assertEqual(state["control_side"], "south")
        self.assertEqual(state["turn"], 2)
        self.assertEqual(state["stage"], "move")                      # 回到移动阶段
        self.assertTrue(state["units"]["south_1"]["zoc_start"])       # 在敌方控制区里
        self.assertFalse(state["units"]["south_1"]["fought"])         # 打过的记录清零了
        self.assertFalse(state["units"]["north_1"]["zoc_start"])     # 别的阵营不动它


if __name__ == "__main__":
    unittest.main()
