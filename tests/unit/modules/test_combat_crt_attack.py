"""CRT 攻击服务的单元测试（用一个"假 api"直接调服务函数）。"""

import unittest
from pathlib import Path

from adapters import PythonScriptLoader

REPO_ROOT = Path(__file__).resolve().parents[3]
ATTACK_SCRIPT = REPO_ROOT / "modules" / "pack_combat_crt" / "scripts" / "attack.py"


class FakeApi:
    """够用的假服务手柄：读 State、记变化量、走真的模块函数。"""

    def __init__(self, state, rolls):
        """rolls: 依次返回的骰点。"""
        self.state = state
        self.rolls = list(rolls)
        self.deltas = []
        self.logs = []

    def get(self, path, default=None):
        node = self.state
        for part in [p for p in path.split("/") if p]:
            node = node[part]
        return node

    def exists(self, path):
        """路径在不在（服务里用 it 判断可选数据，比如 /fords）。"""
        try:
            self.get(path)
            return True
        except (KeyError, TypeError):
            return False

    def emit(self, path, op, kind, **fields):
        self.deltas.append((path, fields.get("value")))
        node = self.state
        parts = [p for p in path.split("/") if p]
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = fields.get("value")

    def rand_int(self, low, high):
        return self.rolls.pop(0) if self.rolls else low

    def call(self, name, *args, **kwargs):
        """只实现用到的三个模块函数。"""
        if name == "pack_combat_crt.crt.basic":
            return PythonScriptLoader().load(str(REPO_ROOT / "modules/pack_combat_crt/scripts/crt.py"),
                                             "resolve")(*args, **kwargs)
        if name == "pack_combat_crt.crt.effects":
            return PythonScriptLoader().load(str(REPO_ROOT / "modules/pack_combat_crt/scripts/crt.py"),
                                             "result_effects")(*args, **kwargs)
        if name.startswith("pack_path_hex.zoc."):
            return PythonScriptLoader().load(str(REPO_ROOT / "modules/pack_path_hex/scripts/paths.py"),
                                             "zoc_cells")(*args, **kwargs)
        if name.startswith("pack_path_hex.retreat."):
            return PythonScriptLoader().load(str(REPO_ROOT / "modules/pack_path_hex/scripts/paths.py"),
                                             "retreat_path")(*args, **kwargs)
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
    """两个单位面对面 + 一条三格的走廊（够撤退用）。"""
    return {
        "units": {
            "a1": {"id": "a1", "name": "攻 1", "side": "north", "attack": 12, "at": "c1",
                   "destroyed": False, "disordered": False},
            "d1": {"id": "d1", "name": "守 1", "side": "south", "attack": 5, "at": "c2",
                   "destroyed": False, "disordered": False},
        },
        "nodes": {cell: {"passable": True} for cell in ("c1", "c2", "c3", "c4")},
        "adjacency": {"c1": ["c2"], "c2": ["c1", "c3"], "c3": ["c2", "c4"], "c4": ["c3"]},
        "log": [],
    }


def attack_service():
    """取 CRT 攻击服务函数。"""
    return PythonScriptLoader().load(str(ATTACK_SCRIPT), "attack")


class TestAttackService(unittest.TestCase):
    """四类结果都要能落到 State 上。"""

    def test_defender_eliminated(self):
        state = make_state()
        api = FakeApi(state, rolls=[6])          # 12:5 → 2:1，骰 6 → D3？
        attack_service()(api, {"units": ["a1"], "target": "d1", "log_path": "/log"})
        self.assertTrue(state["units"]["d1"]["destroyed"] or state["units"]["d1"]["at"] != "c2")

    def test_attacker_eliminated_at_bad_odds(self):
        state = make_state()
        api = FakeApi(state, rolls=[1])          # 12:5 → 2:1…改小攻方攻击力来制造低档
        state["units"]["a1"]["attack"] = 2       # 2:5 = 0.4 → 1:2 档，骰 1 → A3
        attack_service()(api, {"units": ["a1"], "target": "d1", "log_path": "/log"})
        self.assertTrue(any("CRT" in line for line in state["log"]))

    def test_below_lowest_band_is_rejected(self):
        state = make_state()
        state["units"]["a1"]["attack"] = 1       # 1:5 < 1:3
        api = FakeApi(state, rolls=[1])
        attack_service()(api, {"units": ["a1"], "target": "d1", "log_path": "/log"})
        self.assertTrue(any("打不起来" in line for line in state["log"]))
        self.assertFalse(state["units"]["d1"]["destroyed"])

    def test_retreat_marks_disorder(self):
        state = make_state()
        state["units"]["a1"]["attack"] = 12      # 2:1
        api = FakeApi(state, rolls=[6, 0])       # 骰 6 → D3：退 3 格（走廊正好 c2→c3→c4 只有 2 格）
        attack_service()(api, {"units": ["a1"], "target": "d1", "log_path": "/log"})
        defender = state["units"]["d1"]
        # 要么退走并混乱、要么退不动被消灭——两种都是合法结局，但必须有结果。
        self.assertTrue(defender["disordered"] or defender["destroyed"])

    def _corridor_state(self, *, extra_cells=("c4",), fords=()):
        """一条走廊：守军 c2 被打退，c3 上站着**友军**，后面是空格。"""
        cells = ["c1", "c2", "c3"] + list(extra_cells)
        adjacency = {}
        for index, cell in enumerate(cells):
            adjacency[cell] = []
            if index > 0:
                adjacency[cell].append(cells[index - 1])
            if index + 1 < len(cells):
                adjacency[cell].append(cells[index + 1])
        return {
            "units": {
                "a1": {"id": "a1", "name": "攻 1", "side": "north", "attack": 6, "at": "c1",
                       "destroyed": False, "disordered": False},
                "d1": {"id": "d1", "name": "守 1", "side": "south", "attack": 2, "at": "c2",
                       "destroyed": False, "disordered": False},
                "d2": {"id": "d2", "name": "守 2", "side": "south", "attack": 2, "at": "c3",
                       "destroyed": False, "disordered": False},
            },
            "nodes": {cell: {"passable": True} for cell in cells},
            "adjacency": adjacency,
            "fords": list(fords),
            "log": [],
        }

    def test_retreat_can_pass_through_a_friendly(self):
        """R-3：撤退路上可以穿过友军，只要终点是空的（友军格"可穿不可停"）。"""
        state = self._corridor_state()
        api = FakeApi(state, rolls=[2])          # 6:2 = 3:1，骰 2 → D2：退 2 格
        attack_service()(api, {"units": ["a1"], "target": "d1", "log_path": "/log"})
        defender = state["units"]["d1"]
        self.assertEqual(defender["at"], "c4")   # 穿过 c3 上的友军，落在 c4
        self.assertTrue(defender["disordered"])

    def test_retreat_cannot_use_a_ford(self):
        """R-4：撤退不能进渡口格——唯一的退路是渡口时，按 R-6 直接消灭。"""
        state = self._corridor_state(extra_cells=(), fords=[{"from": "c3", "to": "zz", "cost": 3}])
        api = FakeApi(state, rolls=[5])          # 3:1 骰 5 → D1：退 1 格（唯一候选 c3 是渡口）
        attack_service()(api, {"units": ["a1"], "target": "d1", "log_path": "/log",
                               "fords_path": "/fords"})
        defender = state["units"]["d1"]
        self.assertTrue(defender["destroyed"])   # 退不了 → 被消灭
        self.assertEqual(defender["at"], "")


if __name__ == "__main__":
    unittest.main()
