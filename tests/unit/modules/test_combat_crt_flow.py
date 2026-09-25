"""pack_combat_crt 的战斗流程服务单元测试：声明 / 逐场结算 / 战后挺进。

位置：tests/unit/modules/
运行：在仓库根执行 `python run_tests.py`。

覆盖（对应《示例Mod》§六 / §九 / §10、D-5、D-29）：
  - declare：入列 + 记 order + 把参战单位标成"打过仗"；
  - declare 的资格检查：打过仗的 / 混乱的 / 打自己人 / 不相邻 / 目标已经没了，都不入列；
  - resolve_next：结算第一场并出列；列表空时什么都不做；
  - resolve_next：目标已经不在战场时**不掷骰**、只记"打不成了"、照样出列；
  - advance：走进空出来的那一格；有人占着 / 没参加这一场，都不动。
"""

import unittest
from pathlib import Path

from adapters import PythonScriptLoader

REPO_ROOT = Path(__file__).resolve().parents[3]
ATTACK_SCRIPT = REPO_ROOT / "modules" / "pack_combat_crt" / "scripts" / "attack.py"
PATHS_SCRIPT = REPO_ROOT / "modules" / "pack_path_hex" / "scripts" / "paths.py"
CRT_SCRIPT = REPO_ROOT / "modules" / "pack_combat_crt" / "scripts" / "crt.py"


def flow_function(name: str):
    """取 attack.py 里的一个服务函数（走脚本端口）。"""
    return PythonScriptLoader().load(str(ATTACK_SCRIPT), name)


class FakeApi:
    """够用的假服务手柄：读 State、记变化量、走真的模块函数。"""

    def __init__(self, state, rolls=(6,)):
        """rolls: 依次返回的骰点（用光后回落到最小值）。"""
        self.state = state
        self.rolls = list(rolls)
        self.deltas = []
        self.logs = []
        self.used_rolls = 0

    def get(self, path):
        node = self.state
        for part in [p for p in path.split("/") if p]:
            node = node[part]
        return node

    def exists(self, path):
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
        self.used_rolls += 1
        return self.rolls.pop(0) if self.rolls else low

    def call(self, name, *args, **kwargs):
        """只实现流程里用到的三个模块函数。"""
        if name.startswith("pack_combat_crt.crt.basic"):
            return PythonScriptLoader().load(str(CRT_SCRIPT), "resolve")(*args, **kwargs)
        if name.startswith("pack_combat_crt.crt.effects"):
            return PythonScriptLoader().load(str(CRT_SCRIPT), "result_effects")(*args, **kwargs)
        if name.startswith("pack_path_hex."):
            loader = PythonScriptLoader()
            if name.endswith("zoc.from_units"):
                return loader.load(str(PATHS_SCRIPT), "zoc_cells")(*args, **kwargs)
            if name.endswith("retreat.random"):
                return loader.load(str(PATHS_SCRIPT), "retreat_path")(*args, **kwargs)
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
    """三格走廊：攻方 a1 在 c1，守方 d1 在 c2，后面 c3 是空地。"""
    return {
        "control_side": "north",
        "battles": [],
        "battle_report": {},
        "units": {
            "a1": {"id": "a1", "name": "攻 1", "side": "north", "attack": 12, "at": "c1",
                   "destroyed": False, "disordered": False, "fought": False},
            "d1": {"id": "d1", "name": "守 1", "side": "south", "attack": 2, "at": "c2",
                   "destroyed": False, "disordered": False},
        },
        "nodes": {cell: {"passable": True} for cell in ("c1", "c2", "c3")},
        "adjacency": {"c1": ["c2"], "c2": ["c1", "c3"], "c3": ["c2"]},
        "log": [],
    }


ARGS = {"log_path": "/log", "report_path": "/battle_report"}


class TestDeclare(unittest.TestCase):
    """声明：入列 + 参战资格检查。"""

    def test_declare_queues_the_battle_and_spends_the_units(self):
        """一场合法声明：队列 +1（带 order）、参战单位标成"打过仗"、战报留一行。"""
        declare = flow_function("declare")
        state = make_state()
        api = FakeApi(state)
        declare(api, {**ARGS, "units": ["a1"], "target": "d1"})
        self.assertEqual(len(state["battles"]), 1)
        self.assertEqual(state["battles"][0],
                         {"units": ["a1"], "target": "d1", "order": 0})
        self.assertTrue(state["units"]["a1"]["fought"])
        self.assertTrue(any("声明一场" in line for line in state["log"]))

    def test_illegal_declarations_are_refused(self):
        """打过仗 / 混乱 / 打自己人 / 不相邻：都不入列，只记一条战报。"""
        declare = flow_function("declare")
        cases = {
            "已经打过仗": lambda s: s["units"]["a1"].update({"fought": True}),
            "还在混乱中": lambda s: s["units"]["a1"].update({"disordered": True}),
            "打自己人": lambda s: s["units"]["d1"].update({"side": "north"}),
            "不相邻": lambda s: s["units"]["d1"].update({"at": "c3"}),
        }
        for name, break_it in cases.items():
            with self.subTest(case=name):
                state = make_state()
                break_it(state)
                api = FakeApi(state)
                declare(api, {**ARGS, "units": ["a1"], "target": "d1"})
                self.assertEqual(state["battles"], [])
                written = {path for path, _ in api.deltas}
                self.assertNotIn("/battles", written)                     # 没入列
                self.assertFalse(any(path.endswith("/fought") for path in written))
                self.assertTrue(state["log"])                            # 但战报里说清了原因

    def test_target_already_gone_is_refused(self):
        """目标已经被消灭：声明不成。"""
        declare = flow_function("declare")
        state = make_state()
        state["units"]["d1"]["destroyed"] = True
        api = FakeApi(state)
        declare(api, {**ARGS, "units": ["a1"], "target": "d1"})
        self.assertEqual(state["battles"], [])


class TestResolveNext(unittest.TestCase):
    """逐场结算：结算第一场并出列。"""

    def test_empty_queue_does_nothing(self):
        """没有声明的战斗：什么都不做（连骰子都不掷）。"""
        resolve_next = flow_function("resolve_next")
        state = make_state()
        api = FakeApi(state)
        resolve_next(api, dict(ARGS))
        self.assertEqual(api.deltas, [])
        self.assertEqual(api.used_rolls, 0)

    def test_resolves_the_first_battle_and_pops_it(self):
        """结算第一场：出列，并且真的掷了骰（CRT 战报在）。"""
        resolve_next = flow_function("resolve_next")
        state = make_state()
        state["battles"] = [{"units": ["a1"], "target": "d1", "order": 0}]
        api = FakeApi(state)
        resolve_next(api, dict(ARGS))
        self.assertEqual(state["battles"], [])
        self.assertEqual(api.used_rolls, 1)
        self.assertTrue(any("CRT" in line for line in state["log"]))

    def test_target_gone_is_skipped_without_rolling(self):
        """目标已经不在战场：不掷骰、只记"打不成了"、照样出列（跳过 = 不再重试）。"""
        resolve_next = flow_function("resolve_next")
        state = make_state()
        state["battles"] = [{"units": ["a1"], "target": "d1", "order": 0}]
        state["units"]["d1"]["destroyed"] = True
        api = FakeApi(state)
        resolve_next(api, dict(ARGS))
        self.assertEqual(state["battles"], [])
        self.assertEqual(api.used_rolls, 0)
        self.assertTrue(any("打不成" in line for line in state["log"]))

    def test_vacated_cell_is_recorded_when_the_defender_leaves(self):
        """守军被打退 / 被消灭 → 战报要记下"空出来的格子"（战后挺进就靠它）。

        这条是回归用例：旧写法在函数开头缓存了一份 /units 快照，后面的撤退改不到它，
        于是 vacated 永远是空的，挺进实际打不开。
        """
        resolve_next = flow_function("resolve_next")
        state = make_state()
        state["battles"] = [{"units": ["a1"], "target": "d1", "order": 0}]
        api = FakeApi(state, rolls=[6])
        resolve_next(api, dict(ARGS))
        defender = state["units"]["d1"]
        self.assertTrue(defender["destroyed"] or defender["at"] != "c2")   # 守军确实离开了 c2
        self.assertEqual(state["battle_report"]["vacated"], "c2")


class TestAdvance(unittest.TestCase):
    """战后挺进：走进刚空出来的那一格。"""

    def _report_state(self):
        """守军被打了出去、c2 空出来的局面。"""
        state = make_state()
        state["units"]["d1"]["at"] = "c3"
        state["battle_report"] = {"code": "D1", "units": ["a1"], "target": "d1",
                                  "vacated": "c2"}
        return state

    def test_advance_moves_in_and_clears_the_report(self):
        """挺进走进空出来的格子，并且把战报里的 vacated 清掉（用过就清）。"""
        advance = flow_function("advance")
        state = self._report_state()
        api = FakeApi(state)
        advance(api, {**ARGS, "unit": "a1"})
        self.assertEqual(state["units"]["a1"]["at"], "c2")
        self.assertEqual(state["battle_report"]["vacated"], "")

    def test_cannot_stack_or_advance_without_participating(self):
        """那一格有人 / 没参加这一场：都不动。"""
        advance = flow_function("advance")
        state = self._report_state()
        state["units"]["a1"]["at"] = "c1"
        state["units"]["x1"] = {"id": "x1", "side": "north", "at": "c2", "destroyed": False}
        api = FakeApi(state)
        advance(api, {**ARGS, "unit": "a1"})
        self.assertEqual(state["units"]["a1"]["at"], "c1")          # 有人占着

        state = self._report_state()
        state["units"]["b1"] = {"id": "b1", "side": "north", "at": "c1", "destroyed": False}
        api = FakeApi(state)
        advance(api, {**ARGS, "unit": "b1"})
        self.assertEqual(state["units"]["b1"]["at"], "c1")          # 没参加这一场


if __name__ == "__main__":
    unittest.main()
