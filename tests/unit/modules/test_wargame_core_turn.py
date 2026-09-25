"""pack_wargame_core 的回合结构服务（begin_side）单元测试（标准库 unittest）。

位置：tests/unit/modules/
运行：在仓库根执行 `python run_tests.py`。

覆盖（对应《示例Mod》§六 / G-14、§10.1 M-5、§八 控制区）：
  - 行动力：即时值回到属性值，**只在不一样的时候写变化量**；
  - 控制区标记：站在敌方控制区里的换成 True、出来的换成 False；
  - "本回合已打过"清零；
  - 只碰这一方：对方单位、被消灭的单位都不动；
  - 混乱的单位不产生控制区（标记按"没有控制区"算）；
  - 字段名与路径都是参数（换个 Mod 的写法照样能用）；
  - 缺 side 直接报错（Mod 数据写错要看得见）。
  - 胜负判定（check_victory）：占点 / 守方全灭 / 攻方全灭 / 超时四条分支，
    判定顺序固定、胜方名字来自参数、局已经结束就不再写一次。
"""

import unittest
from pathlib import Path

from adapters import PythonScriptLoader

REPO_ROOT = Path(__file__).resolve().parents[3]
PATHS_SCRIPT = REPO_ROOT / "modules" / "pack_path_hex" / "scripts" / "paths.py"
TURN_SCRIPT = REPO_ROOT / "modules" / "pack_wargame_core" / "scripts" / "turn.py"


def turn_function(name: str):
    """取 pack_wargame_core 的 turn.py 里的一个函数（走脚本端口）。"""
    return PythonScriptLoader().load(str(TURN_SCRIPT), name)


class FakeApi:
    """够用的假服务手柄：读 State、记变化量、走真的模块函数。"""

    def __init__(self, state):
        """state: 一棵可变的状态树。"""
        self.state = state
        self.deltas = []
        self.logs = []

    def get(self, path):
        node = self.state
        for part in [p for p in path.split("/") if p]:
            node = node[part]
        return node

    def emit(self, path, op, kind, **fields):
        self.deltas.append((path, fields.get("value"), fields.get("old_value")))
        node = self.state
        parts = [p for p in path.split("/") if p]
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = fields.get("value")

    def call(self, name, *args, **kwargs):
        if name == "pack_path_hex.zoc.from_units":
            return PythonScriptLoader().load(str(PATHS_SCRIPT), "zoc_cells")(*args, **kwargs)
        raise KeyError(name)

    def log(self, text):
        self.logs.append(text)

    def log_line(self, path, text):
        """与引擎同口径：有路径就往那个列表追一行，没路径就只写引擎日志。"""
        if not path:
            self.log(text)
            return
        current = self.get(path)
        self.emit(path, "modify", "list", value=list(current) + [text], old_value=current)


def make_state():
    """三格走廊（c1-c2-c3）：c2 站着对方单位 c9 之外的一个南军。"""
    return {
        "units": {
            "n1": {"side": "north", "at": "c1", "move": 3, "move_left": 0,
                   "zoc_start": False, "fought": True},
            "n2": {"side": "north", "at": "c3", "move": 2, "move_left": 2,
                   "zoc_start": True, "fought": False},
            "n3": {"side": "north", "at": "c3", "move": 1, "move_left": 0, "destroyed": True},
            "s1": {"side": "south", "at": "c2", "move": 3, "move_left": 0, "fought": True},
        },
        "adjacency": {"c1": ["c2"], "c2": ["c1", "c3"], "c3": ["c2"]},
    }


def make_victory_state():
    """一张三格小图：v 是胜利点；攻方（north）两个单位、守方（south）一个单位。"""
    return {
        "turn": 1,
        "game_over": False,
        "winner": "",
        "nodes": {
            "v": {"victory": True},
            "a": {},
            "b": {},
        },
        "units": {
            "n1": {"side": "north", "at": "a", "destroyed": False},
            "n2": {"side": "north", "at": "b", "destroyed": False},
            "s1": {"side": "south", "at": "b", "destroyed": False},
        },
        "log": [],
    }


# log_path 给上：战报写进 State（Mod 里就是这么传的）；不给就只写引擎日志。
VICTORY_ARGS = {"attacker_side": "north", "defender_side": "south", "log_path": "/log"}


class TestBeginSide(unittest.TestCase):
    """一方回合开始：恢复行动力、刷新控制区标记、清掉已打过。"""

    def test_restores_move_and_clears_fought(self):
        """行动力补回属性值；已打过的清掉；只在真变了的时候写变化量。"""
        begin_side = turn_function("begin_side")
        api = FakeApi(make_state())
        begin_side(api, {"side": "north"})
        written = {path for path, _, _ in api.deltas}
        self.assertIn("/units/n1/move_left", written)        # 0 -> 3
        self.assertNotIn("/units/n2/move_left", written)     # 本来就等于属性值
        self.assertIn("/units/n1/fought", written)
        self.assertEqual(api.state["units"]["n1"]["move_left"], 3)
        self.assertFalse(api.state["units"]["n1"]["fought"])

    def test_zoc_flag_follows_enemy_control(self):
        """站在敌方控制区里的换成 True；不在里面的换成 False。"""
        begin_side = turn_function("begin_side")
        api = FakeApi(make_state())
        begin_side(api, {"side": "north"})
        # c2 的南军管着 c1 与 c3：两个北军都在控制区里。
        self.assertTrue(api.state["units"]["n1"]["zoc_start"])
        self.assertTrue(api.state["units"]["n2"]["zoc_start"])

    def test_other_side_and_dead_units_are_left_alone(self):
        """只碰这一方：对方单位、被消灭的单位都不动。"""
        begin_side = turn_function("begin_side")
        api = FakeApi(make_state())
        begin_side(api, {"side": "north"})
        touched = {path.split("/")[2] for path, _, _ in api.deltas}
        self.assertNotIn("s1", touched)
        self.assertNotIn("n3", touched)
        self.assertEqual(api.state["units"]["s1"]["move_left"], 0)
        self.assertTrue(api.state["units"]["s1"]["fought"])

    def test_disordered_units_project_no_control(self):
        """混乱的单位不产生控制区：北军两个格子都不算被控制。"""
        begin_side = turn_function("begin_side")
        api = FakeApi(make_state())
        api.state["units"]["s1"]["disordered"] = True
        begin_side(api, {"side": "north"})
        self.assertFalse(api.state["units"]["n1"]["zoc_start"])
        self.assertFalse(api.state["units"]["n2"]["zoc_start"])

    def test_field_names_and_paths_are_parameters(self):
        """字段名与路径都是参数：换个 Mod 的写法照样能用。"""
        begin_side = turn_function("begin_side")
        api = FakeApi({
            "board": {
                "u1": {"team": "red", "cell": "x1", "ap": 4, "ap_left": 1,
                       "in_danger": False, "acted": True},
            },
            "links": {"x1": [], "x2": []},
        })
        begin_side(api, {
            "side": "red", "units_path": "/board", "adjacency_path": "/links",
            "unit_field_path": "/board/{unit}/{field}",
            "side_field": "team", "at_field": "cell", "destroyed_field": "dead",
            "move_field": "ap", "move_left_field": "ap_left",
            "zoc_field": "in_danger", "fought_field": "acted",
        })
        self.assertEqual(api.state["board"]["u1"]["ap_left"], 4)
        self.assertFalse(api.state["board"]["u1"]["acted"])

    def test_missing_side_is_an_error(self):
        """缺 side 要直接报错，不能悄悄什么都不刷。"""
        begin_side = turn_function("begin_side")
        with self.assertRaises(KeyError):
            begin_side(FakeApi(make_state()), {})


class TestCheckVictory(unittest.TestCase):
    """回合末判定：占点 / 一方全灭 / 超时，顺序固定。"""

    def test_attacker_holding_the_point_wins(self):
        """攻方站住胜利点 → 攻方胜。"""
        check_victory = turn_function("check_victory")
        api = FakeApi(make_victory_state())
        api.state["units"]["n1"]["at"] = "v"
        check_victory(api, dict(VICTORY_ARGS))
        self.assertTrue(api.state["game_over"])
        self.assertEqual(api.state["winner"], "north")
        self.assertTrue(api.state["log"])                     # 战报里能看见为什么结束

    def test_wiped_defender_gives_the_attacker_the_win(self):
        """守方一个不剩 → 攻方胜（不需要占点）。"""
        check_victory = turn_function("check_victory")
        api = FakeApi(make_victory_state())
        api.state["units"]["s1"]["destroyed"] = True
        check_victory(api, dict(VICTORY_ARGS))
        self.assertEqual(api.state["winner"], "north")

    def test_wiped_attacker_gives_the_defender_the_win(self):
        """攻方一个不剩 → 守方胜。"""
        check_victory = turn_function("check_victory")
        api = FakeApi(make_victory_state())
        for key in ("n1", "n2"):
            api.state["units"][key]["destroyed"] = True
        check_victory(api, dict(VICTORY_ARGS))
        self.assertEqual(api.state["winner"], "south")

    def test_turn_limit_gives_the_defender_the_win(self):
        """打到回合上限还没占点 → 守方胜；上限文字里的 {limit} 会换掉。"""
        check_victory = turn_function("check_victory")
        api = FakeApi(make_victory_state())
        api.state["turn"] = 13
        check_victory(api, {**VICTORY_ARGS, "turn_limit": 12,
                            "win_timeout_message": "第 {limit} 回合已过，守方守住了"})
        self.assertEqual(api.state["winner"], "south")
        self.assertIn("第 12 回合已过", api.state["log"][-1])

    def test_order_is_fixed_defender_wiped_beats_timeout(self):
        """同时满足"守方全灭"和"超时"时按固定顺序判：先判到的那条赢。"""
        check_victory = turn_function("check_victory")
        api = FakeApi(make_victory_state())
        api.state["units"]["s1"]["destroyed"] = True
        api.state["turn"] = 99
        check_victory(api, dict(VICTORY_ARGS))
        self.assertEqual(api.state["winner"], "north")

    def test_finished_game_is_not_written_twice(self):
        """已经结束的局不再写一次（不会把 winner 覆盖成别的人）。"""
        check_victory = turn_function("check_victory")
        api = FakeApi(make_victory_state())
        api.state["game_over"] = True
        api.state["winner"] = "south"
        api.state["units"]["n1"]["at"] = "v"
        check_victory(api, dict(VICTORY_ARGS))
        self.assertEqual(api.state["winner"], "south")
        self.assertEqual(api.deltas, [])

    def test_winner_names_come_from_arguments(self):
        """胜方名字来自参数：换成 red / blue 一样能用。"""
        check_victory = turn_function("check_victory")
        api = FakeApi(make_victory_state())
        for unit in api.state["units"].values():
            unit["side"] = {"north": "red", "south": "blue"}[unit["side"]]
        api.state["units"]["n1"]["at"] = "v"
        check_victory(api, {"attacker_side": "red", "defender_side": "blue"})
        self.assertEqual(api.state["winner"], "red")

    def test_missing_sides_are_an_error(self):
        """缺攻方 / 守方要直接报错。"""
        check_victory = turn_function("check_victory")
        with self.assertRaises(KeyError):
            check_victory(FakeApi(make_victory_state()), {})


if __name__ == "__main__":
    unittest.main()
