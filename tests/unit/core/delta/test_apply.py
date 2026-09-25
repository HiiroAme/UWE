"""apply 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/delta/
运行：在仓库根执行 `python run_tests.py`。
覆盖：六类操作落地、下标"当时语义"、写入快照（防别名）、严格旧值比对、
      state 层异常透传。
"""

import unittest

from core.delta import (
    DataType,
    Delta,
    DeltaError,
    ListOp,
    Operation,
    Trace,
    apply_delta,
)
from core.state import PathNotFoundError, StateConflictError, StateShapeError, get


def make_trace(sequence: int = 1) -> Trace:
    """造一个最小可用的 Trace。"""
    return Trace(
        sequence=sequence,
        timestamp=1.5,
        command_id="c1",
        action_id="a1",
        service_id="s1",
        mod_id="m1",
    )


def make_state() -> dict:
    """造一棵用于应用测试的示例树。

    输入：无。
    输出：新的示例 State 字典。
    变量：无。
    """
    return {
        "system": {"turn": 1, "weather": None},
        "units": {"u1": {"hp": 10, "tags": ["infantry", "land"]}},
        "nodes": [{"id": "n0"}, {"id": "n1"}],
    }


class TestKeywiseApply(unittest.TestCase):
    """新增 / 删除 / 修改落地。"""

    def test_add_key(self):
        """新增字典键后能立刻读到。"""
        state = make_state()
        apply_delta(state, Delta(
            path="/units/u2", operation=Operation.ADD, data_type=DataType.DICT,
            trace=make_trace(), value={"hp": 5},
        ))
        self.assertEqual(get(state, "/units/u2/hp"), 5)

    def test_add_top_level_key_with_none_value(self):
        """根下新增一个值为 None 的键是合法操作。"""
        state = make_state()
        apply_delta(state, Delta(
            path="/weather", operation=Operation.ADD, data_type=DataType.NULL,
            trace=make_trace(), value=None,
        ))
        self.assertIsNone(get(state, "/weather"))

    def test_add_existing_key_conflicts(self):
        """新增已存在的键由 state 层报冲突。"""
        state = make_state()
        with self.assertRaises(StateConflictError):
            apply_delta(state, Delta(
                path="/system/turn", operation=Operation.ADD, data_type=DataType.NUMBER,
                trace=make_trace(), value=2,
            ))

    def test_add_into_list_is_rejected(self):
        """对列表用"新增键"是误用，state 层报形状错误。"""
        state = make_state()
        with self.assertRaises(StateShapeError):
            apply_delta(state, Delta(
                path="/nodes/9", operation=Operation.ADD, data_type=DataType.DICT,
                trace=make_trace(), value={"id": "x"},
            ))

    def test_remove_key(self):
        """删除键后路径不存在，旧值一致时正常通过。"""
        state = make_state()
        apply_delta(state, Delta(
            path="/units/u1/hp", operation=Operation.REMOVE, data_type=DataType.NUMBER,
            trace=make_trace(), old_value=10,
        ))
        self.assertNotIn("hp", state["units"]["u1"])

    def test_remove_old_value_mismatch(self):
        """旧值对不上时报冲突，并且树保持原样（单条应用是原子的）。"""
        state = make_state()
        with self.assertRaises(StateConflictError):
            apply_delta(state, Delta(
                path="/units/u1/hp", operation=Operation.REMOVE, data_type=DataType.NUMBER,
                trace=make_trace(), old_value=999,
            ))
        self.assertEqual(get(state, "/units/u1/hp"), 10)

    def test_remove_missing_key_conflicts(self):
        """删除一个不存在的键属于操作冲突（键是操作的目标，不是寻址路径）。"""
        state = make_state()
        with self.assertRaises(StateConflictError):
            apply_delta(state, Delta(
                path="/units/u1/not_there", operation=Operation.REMOVE,
                data_type=DataType.NUMBER, trace=make_trace(), old_value=1,
            ))

    def test_modify_value(self):
        """修改替换值并核对旧值。"""
        state = make_state()
        apply_delta(state, Delta(
            path="/system/turn", operation=Operation.MODIFY, data_type=DataType.NUMBER,
            trace=make_trace(), value=2, old_value=1,
        ))
        self.assertEqual(get(state, "/system/turn"), 2)

    def test_modify_old_value_mismatch(self):
        """修改的旧值对不上时报冲突。"""
        state = make_state()
        with self.assertRaises(StateConflictError):
            apply_delta(state, Delta(
                path="/system/turn", operation=Operation.MODIFY, data_type=DataType.NUMBER,
                trace=make_trace(), value=2, old_value=99,
            ))

    def test_modify_whole_list(self):
        """字典成员里的整张列表可以整体替换。"""
        state = make_state()
        apply_delta(state, Delta(
            path="/units/u1/tags", operation=Operation.MODIFY, data_type=DataType.LIST,
            trace=make_trace(), value=["land"], old_value=["infantry", "land"],
        ))
        self.assertEqual(get(state, "/units/u1/tags"), ["land"])


class TestListApply(unittest.TestCase):
    """列表插入 / 删除 / 元素替换。"""

    def test_list_insert(self):
        """在中间插入，顺序正确。"""
        state = make_state()
        apply_delta(state, Delta(
            path="/nodes", operation=Operation.MODIFY, data_type=DataType.LIST,
            trace=make_trace(), value={"id": "x"}, list_op=ListOp.INSERT, index=1,
        ))
        self.assertEqual([item["id"] for item in get(state, "/nodes")], ["n0", "x", "n1"])

    def test_list_insert_at_end(self):
        """index 等于长度表示追加。"""
        state = make_state()
        apply_delta(state, Delta(
            path="/nodes", operation=Operation.MODIFY, data_type=DataType.LIST,
            trace=make_trace(), value={"id": "x"}, list_op=ListOp.INSERT, index=2,
        ))
        self.assertEqual([item["id"] for item in get(state, "/nodes")], ["n0", "n1", "x"])

    def test_list_remove(self):
        """删除并核对被移除的元素。"""
        state = make_state()
        apply_delta(state, Delta(
            path="/nodes", operation=Operation.MODIFY, data_type=DataType.LIST,
            trace=make_trace(), old_value={"id": "n0"}, list_op=ListOp.REMOVE, index=0,
        ))
        self.assertEqual([item["id"] for item in get(state, "/nodes")], ["n1"])

    def test_list_remove_old_value_mismatch(self):
        """被移除元素与记录不符时报冲突，列表保持原样。"""
        state = make_state()
        with self.assertRaises(StateConflictError):
            apply_delta(state, Delta(
                path="/nodes", operation=Operation.MODIFY, data_type=DataType.LIST,
                trace=make_trace(), old_value={"id": "n9"}, list_op=ListOp.REMOVE, index=0,
            ))
        self.assertEqual([item["id"] for item in get(state, "/nodes")], ["n0", "n1"])

    def test_list_insert_out_of_range_conflicts(self):
        """参数下标越界由 state 层报冲突。"""
        state = make_state()
        with self.assertRaises(StateConflictError):
            apply_delta(state, Delta(
                path="/nodes", operation=Operation.MODIFY, data_type=DataType.LIST,
                trace=make_trace(), value={"id": "x"}, list_op=ListOp.INSERT, index=9,
            ))

    def test_element_replace_via_path(self):
        """元素替换走路径式修改（下标写在路径里）。"""
        state = make_state()
        apply_delta(state, Delta(
            path="/nodes/1", operation=Operation.MODIFY, data_type=DataType.DICT,
            trace=make_trace(), value={"id": "n1-new"}, old_value={"id": "n1"},
        ))
        self.assertEqual(get(state, "/nodes/1/id"), "n1-new")

    def test_index_semantics_is_at_apply_time(self):
        """下标按应用这条变化量时的列表算，不是命令开始时的快照。

        先插入 x 到 0 号位置，列表变成 [x, n0, n1]；
        随后对 /nodes/1 的替换应当作用在 n0 上。
        """
        state = make_state()
        apply_delta(state, Delta(
            path="/nodes", operation=Operation.MODIFY, data_type=DataType.LIST,
            trace=make_trace(), value={"id": "x"}, list_op=ListOp.INSERT, index=0,
        ))
        apply_delta(state, Delta(
            path="/nodes/1", operation=Operation.MODIFY, data_type=DataType.DICT,
            trace=make_trace(), value={"id": "n0-new"}, old_value={"id": "n0"},
        ))
        self.assertEqual([item["id"] for item in get(state, "/nodes")], ["x", "n0-new", "n1"])


class TestValueSnapshot(unittest.TestCase):
    """写入即快照：树里的容器与调用方手上的容器不共享（§6.2）。"""

    def test_added_container_is_not_aliased(self):
        """同一个 dict 新增到两条路径，树里得到两份互不影响的副本。"""
        state = make_state()
        template = {"hp": 5}
        apply_delta(state, Delta(
            path="/units/u2", operation=Operation.ADD, data_type=DataType.DICT,
            trace=make_trace(), value=template,
        ))
        apply_delta(state, Delta(
            path="/units/u3", operation=Operation.ADD, data_type=DataType.DICT,
            trace=make_trace(2), value=template,
        ))
        # 树里的两份既不是调用方的对象，也不是同一个对象
        self.assertIsNot(state["units"]["u2"], template)
        self.assertIsNot(state["units"]["u3"], template)
        self.assertIsNot(state["units"]["u2"], state["units"]["u3"])
        # 改调用方的模板或其中一条路径，都不影响另一条
        template["hp"] = 999
        state["units"]["u2"]["hp"] = 7
        self.assertEqual(state["units"]["u2"]["hp"], 7)
        self.assertEqual(state["units"]["u3"]["hp"], 5)

    def test_modified_container_is_snapshot(self):
        """修改写入的 list 是副本：应用后改原对象不影响树。"""
        state = make_state()
        new_tags = ["land"]
        apply_delta(state, Delta(
            path="/units/u1/tags", operation=Operation.MODIFY, data_type=DataType.LIST,
            trace=make_trace(), value=new_tags, old_value=["infantry", "land"],
        ))
        new_tags.append("air")
        self.assertEqual(get(state, "/units/u1/tags"), ["land"])

    def test_list_insert_value_is_snapshot(self):
        """列表插入的元素是副本：应用后改原对象不影响树。"""
        state = make_state()
        node = {"id": "x"}
        apply_delta(state, Delta(
            path="/nodes", operation=Operation.MODIFY, data_type=DataType.LIST,
            trace=make_trace(), value=node, list_op=ListOp.INSERT, index=0,
        ))
        node["id"] = "changed"
        self.assertEqual(get(state, "/nodes/0"), {"id": "x"})


class TestStrictOldValueComparison(unittest.TestCase):
    """旧值比对是严格比对：bool 与数值区分，int / float 同属 number。"""

    def test_bool_is_not_number(self):
        """True 与 1 类型不同：旧值核对必须报冲突，树保持原样。"""
        state = {"flag": True}
        with self.assertRaises(StateConflictError):
            apply_delta(state, Delta(
                path="/flag", operation=Operation.MODIFY, data_type=DataType.BOOL,
                trace=make_trace(), value=False, old_value=1,
            ))
        self.assertIs(state["flag"], True)

    def test_int_and_float_are_same_number_type(self):
        """int 与 float 同属 number：1.0 可以作为 1 的记录旧值。"""
        state = {"turn": 1}
        apply_delta(state, Delta(
            path="/turn", operation=Operation.MODIFY, data_type=DataType.NUMBER,
            trace=make_trace(), value=2, old_value=1.0,
        ))
        self.assertEqual(state["turn"], 2)

    def test_nested_container_compare_is_recursive(self):
        """容器逐值递归比对：内层 bool / number 混淆也要挡住。"""
        state = {"a": {"x": 1}}
        with self.assertRaises(StateConflictError):
            apply_delta(state, Delta(
                path="/a", operation=Operation.MODIFY, data_type=DataType.DICT,
                trace=make_trace(), value={"x": 2}, old_value={"x": True},
            ))
        apply_delta(state, Delta(
            path="/a", operation=Operation.MODIFY, data_type=DataType.DICT,
            trace=make_trace(2), value={"x": 2}, old_value={"x": 1},
        ))
        self.assertEqual(state["a"], {"x": 2})

    def test_list_remove_value_compare_is_strict(self):
        """列表删除的元素核对同样走严格比对。"""
        state = {"xs": [True]}
        with self.assertRaises(StateConflictError):
            apply_delta(state, Delta(
                path="/xs", operation=Operation.MODIFY, data_type=DataType.LIST,
                trace=make_trace(), old_value=1, list_op=ListOp.REMOVE, index=0,
            ))
        self.assertEqual(state["xs"], [True])


class TestFailurePropagation(unittest.TestCase):
    """state 层异常与参数校验。"""

    def test_missing_parent_container_propagates(self):
        """父容器路径不存在时抛 PathNotFoundError（属于寻址失败）。"""
        state = make_state()
        with self.assertRaises(PathNotFoundError):
            apply_delta(state, Delta(
                path="/units/not_there/hp", operation=Operation.REMOVE,
                data_type=DataType.NUMBER, trace=make_trace(), old_value=1,
            ))

    def test_non_delta_input(self):
        """入参必须是 Delta。"""
        with self.assertRaises(DeltaError):
            apply_delta(make_state(), {"path": "/system/turn"})


if __name__ == "__main__":
    unittest.main()
