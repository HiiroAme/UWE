"""core.derived 的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 排序：先按 layer，再按注册顺序（确定性）；
  - 只上报"真的需要改"的条目（值已经对就不产生变化量）；
  - 目标路径原本不存在时标成 is_new（要新增键）；
  - 逐层推进：后一层的公式能看到前一层刚写回的结果。
"""

import unittest

from core.content import CompiledFormula
from core.derived import DerivedUpdate, refresh_derived, sort_formulas
from core.logic import parse_data
from core.ports import SilentMedia


class DictReader:
    """用一棵 dict 当读取入口（测试用）。"""

    def __init__(self, state: dict) -> None:
        """包一棵树。"""
        from core.state import exists as state_exists, get as state_get

        self._state = state
        self._get = state_get
        self._exists = state_exists

    def get(self, path: str):
        """按路径读值。"""
        return self._get(self._state, path)

    def exists(self, path: str) -> bool:
        """判断路径是否存在。"""
        return self._exists(self._state, path)


def formula(formula_id: str, target: str, expression, layer: int = 0) -> CompiledFormula:
    """造一条编译后的公式（测试用）。"""
    return CompiledFormula(
        formula_id=formula_id,
        target=target,
        expression=parse_data(expression),
        layer=layer,
        location=formula_id,
    )


class TestSorting(unittest.TestCase):
    """按层排序。"""

    def test_layer_then_registration_order(self):
        """先按 layer 升序，同层保持传入顺序。"""
        formulas = {
            "b": formula("b", "/b", 1, layer=1),
            "a": formula("a", "/a", 1, layer=0),
            "c": formula("c", "/c", 1, layer=1),
        }
        self.assertEqual([item.formula_id for item in sort_formulas(formulas)], ["a", "b", "c"])


class TestRefresh(unittest.TestCase):
    """重算与"只报需要改的"。"""

    def _run(self, state: dict, formulas: dict):
        """跑一次重算，返回 (读取器, 更新列表)。"""
        from core.delta import data_type_of
        from core.temp_state import TempState
        from core.pipeline import Command, EngineApi, SequenceCounter
        from core.context import Context
        from core.logger import Logger
        from core.rng import Rng

        view = TempState(state, "c1")
        command = Command(
            command_id="c1", definition_id="engine:service:refresh_derived",
            source="engine", payload={}, created_at=0.0,
        )
        api = EngineApi(
            view, SequenceCounter(), command, "demo",
            rng=Rng(1), logger=Logger(sink=None), context=Context(), media=SilentMedia(),
            functions={},
        )
        handle = api.for_service("a1", "engine:service:refresh_derived")

        def apply_update(update: DerivedUpdate) -> None:
            """把更新写回视图（走变化量通道）。"""
            if update.is_new:
                handle.emit(update.path, "add", data_type_of(update.new_value), value=update.new_value)
            else:
                handle.emit(
                    update.path,
                    "modify",
                    data_type_of(update.new_value),
                    value=update.new_value,
                    old_value=update.old_value,
                )

        updates = refresh_derived(view, formulas, apply_update)
        return view, updates

    def test_unchanged_values_produce_no_updates(self):
        """值已经对了：不产生任何更新。"""
        state = {"units": {"u1": {"hp": 10}}, "power": 20}
        formulas = {"f": formula("f", "/power", ["*", ["get", "/units/u1/hp"], 2])}
        _, updates = self._run(state, formulas)
        self.assertEqual(updates, ())

    def test_changed_value_is_reported_and_written(self):
        """基础值变了：算出新值、写回、并报出来。"""
        state = {"units": {"u1": {"hp": 7}}, "power": 20}
        formulas = {"f": formula("f", "/power", ["*", ["get", "/units/u1/hp"], 2])}
        view, updates = self._run(state, formulas)
        self.assertEqual([item.path for item in updates], ["/power"])
        self.assertEqual(updates[0].old_value, 20)
        self.assertEqual(updates[0].new_value, 14)
        self.assertFalse(updates[0].is_new)
        self.assertEqual(view.get("/power"), 14)

    def test_missing_target_is_marked_new(self):
        """目标路径原本不存在：标成新增。"""
        state = {"hp": 3}
        formulas = {"f": formula("f", "/power", ["*", ["get", "/hp"], 2])}
        _, updates = self._run(state, formulas)
        self.assertEqual(len(updates), 1)
        self.assertTrue(updates[0].is_new)
        self.assertIsNot(updates[0].old_value, None)

    def test_higher_layer_sees_lower_layer_result(self):
        """第二层的公式能看到第一层刚写回的结果（逐层推进）。"""
        state = {"hp": 5, "power": 0, "advantage": 0}
        formulas = {
            "power": formula("power", "/power", ["*", ["get", "/hp"], 2], layer=1),
            "advantage": formula("advantage", "/advantage", ["+", ["get", "/power"], 1], layer=2),
        }
        view, updates = self._run(state, formulas)
        self.assertEqual([item.formula_id for item in updates], ["power", "advantage"])
        self.assertEqual(view.get("/power"), 10)
        self.assertEqual(view.get("/advantage"), 11)

    def test_service_carries_the_function_table(self):
        """R6-3：触发链用的原子服务也要带函数表，公式里的 ["call", …] 才算得出来。"""
        from core.context import Context
        from core.derived import make_refresh_service
        from core.logger import Logger
        from core.pipeline import Command, EngineApi, SequenceCounter
        from core.rng import Rng
        from core.temp_state import TempState

        view = TempState({"roll": 0}, "c1")
        command = Command(
            command_id="c1", definition_id="engine:service:refresh_derived",
            source="engine", payload={}, created_at=0.0,
        )
        api = EngineApi(
            view, SequenceCounter(), command, "demo",
            rng=Rng(1), logger=Logger(sink=None), context=Context(),
            media=SilentMedia(), functions={},
        )
        handle = api.for_service("a1", "engine:service:refresh_derived")
        service = make_refresh_service(
            {"f": formula("f", "/roll", ["call", "rng.int", 1, 6])},
            functions={"rng.int": lambda low, high: 4},
        )
        service(handle, {})
        self.assertEqual(view.get("/roll"), 4)


if __name__ == "__main__":
    unittest.main()
