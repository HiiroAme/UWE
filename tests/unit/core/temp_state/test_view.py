"""view 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/temp_state/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 读穿透：改动立刻可见、删除后 exists 为 False、列表移位后下标正确；
  - 真实 State 隔离：任何变化量应用后真实 State 逐字节未变；
  - record 原子性：失败时不写进视图、不动真实 State；
  - record 冻结：调用方事后改 value / old_value 不影响记录、视图与提交；
  - Command 绑定：command_id 校验、sequence 严格递增、属性只读暴露；
  - CommandDelta：相邻合并、原始记录保留、提交重放合并结果；
  - 提交：重放到原对象、结果与按序直接应用一致、根身份不变；
  - 生命周期：终态保护、终态释放叠加视图、提交失败进入 FAILED。
"""

import copy
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
from core.state import PathNotFoundError, StateConflictError
from core.temp_state import TempState, TempStateError, ViewStatus

# 本文件所有变化量都属于同一个测试 Command；sequence 由 next_trace 统一分配，
# 保证同一视图内严格递增（D-41）。
COMMAND_ID = "c1"
_next_sequence = 0


def next_trace() -> Trace:
    """分配一条 sequence 递增的 Trace。

    输入：无。
    输出：
        command_id 固定为 COMMAND_ID、sequence 严格递增的 Trace。
    异常：
        无。
    变量：
        _next_sequence: 模块级计数器；测试只要求递增，不要求具体数值。
    """
    global _next_sequence
    _next_sequence += 1
    return Trace(
        sequence=_next_sequence,
        timestamp=1.5,
        command_id=COMMAND_ID,
        action_id="a1",
        service_id="s1",
        mod_id="m1",
    )


def make_trace(sequence: int, command_id: str = COMMAND_ID) -> Trace:
    """显式造一条 Trace，用于测试 command_id 与 sequence 的校验分支。

    输入：
        sequence: 要写进 Trace 的顺序号；
        command_id: 变化量所属的 Command 标识，默认是本文件测试 Command。
    输出：
        构造好的 Trace。
    异常：
        无（字段合法性由 Trace 自身校验）。
    变量：
        无。
    """
    return Trace(
        sequence=sequence,
        timestamp=1.5,
        command_id=command_id,
        action_id="a1",
        service_id="s1",
        mod_id="m1",
    )


def make_view(state: dict) -> TempState:
    """按测试 Command 创建临时状态视图。

    输入：
        state: 真实 State（测试用示例树）。
    输出：
        command_id 为 COMMAND_ID 的 TempState。
    异常：
        无。
    变量：
        无。
    """
    return TempState(state, COMMAND_ID)


def make_state() -> dict:
    """造一棵用于视图测试的示例树。

    输入：无。
    输出：新的示例 State 字典；含字典、列表、列表套列表，便于测深路径。
    变量：无。
    """
    return {
        "system": {"turn": 1, "weather": None},
        "units": {"u1": {"hp": 10, "tags": ["infantry", "land"]}},
        "nodes": [{"id": "n0"}, {"id": "n1"}],
        "grid": [[{"hp": 1}, {"hp": 2}]],
    }


def make_modify(path: str, old_value, new_value, data_type: DataType = DataType.NUMBER) -> Delta:
    """造一条修改变化量。"""
    return Delta(
        path=path, operation=Operation.MODIFY, data_type=data_type,
        trace=next_trace(), value=new_value, old_value=old_value,
    )


def make_add(path: str, value, data_type: DataType) -> Delta:
    """造一条新增变化量。"""
    return Delta(
        path=path, operation=Operation.ADD, data_type=data_type,
        trace=next_trace(), value=value,
    )


def make_remove(path: str, old_value, data_type: DataType = DataType.NUMBER) -> Delta:
    """造一条删除变化量。"""
    return Delta(
        path=path, operation=Operation.REMOVE, data_type=data_type,
        trace=next_trace(), old_value=old_value,
    )


def make_list_insert(index: int, value) -> Delta:
    """造一条在 /nodes 上插入元素的变化量。"""
    return Delta(
        path="/nodes", operation=Operation.MODIFY, data_type=DataType.LIST,
        trace=next_trace(), value=value, list_op=ListOp.INSERT, index=index,
    )


def make_list_remove(index: int, old_value) -> Delta:
    """造一条在 /nodes 上删除元素的变化量。"""
    return Delta(
        path="/nodes", operation=Operation.MODIFY, data_type=DataType.LIST,
        trace=next_trace(), old_value=old_value, list_op=ListOp.REMOVE, index=index,
    )


class TestReadThrough(unittest.TestCase):
    """读穿透：视图上的读看到尚未提交的改动。"""

    def test_initial_read_uses_base(self):
        """还没记录变化量时，读到的就是真实 State。"""
        state = make_state()
        view = make_view(state)
        self.assertIs(view.get(""), state)
        self.assertEqual(view.get("/system/turn"), 1)

    def test_modify_is_visible_immediately(self):
        """改完立刻能读到新值，真实 State 不变。"""
        state = make_state()
        view = make_view(state)
        view.record(make_modify("/system/turn", 1, 2))
        self.assertEqual(view.get("/system/turn"), 2)
        self.assertEqual(state["system"]["turn"], 1)
        self.assertIsNot(view.get(""), state)

    def test_remove_hides_path(self):
        """删掉的路径在视图上不存在，真实 State 仍保留。"""
        state = make_state()
        view = make_view(state)
        view.record(make_remove("/system/weather", None, DataType.NULL))
        self.assertFalse(view.exists("/system/weather"))
        self.assertIn("weather", state["system"])

    def test_add_is_visible(self):
        """新增的键在视图上能读到。"""
        state = make_state()
        view = make_view(state)
        view.record(make_add("/system/phase", "combat", DataType.STRING))
        self.assertEqual(view.get("/system/phase"), "combat")
        self.assertNotIn("phase", state["system"])

    def test_list_insert_shifts_indices(self):
        """插入之后，视图上的下标按新列表算。"""
        state = make_state()
        view = make_view(state)
        view.record(make_list_insert(0, {"id": "x"}))
        self.assertEqual(view.get("/nodes/0"), {"id": "x"})
        self.assertEqual(view.get("/nodes/1"), {"id": "n0"})
        self.assertEqual([item["id"] for item in state["nodes"]], ["n0", "n1"])

    def test_list_remove_shifts_indices(self):
        """删除之后，视图上的下标同样按新列表算。"""
        state = make_state()
        view = make_view(state)
        view.record(make_list_remove(0, {"id": "n0"}))
        self.assertEqual(view.get("/nodes/0"), {"id": "n1"})
        self.assertEqual([item["id"] for item in state["nodes"]], ["n0", "n1"])

    def test_deep_path_through_nested_list(self):
        """穿过两层列表的深路径也能正确读到改动。"""
        state = make_state()
        view = make_view(state)
        view.record(make_modify("/grid/0/1/hp", 2, 9))
        self.assertEqual(view.get("/grid/0/1/hp"), 9)
        self.assertEqual(state["grid"][0][1]["hp"], 2)


class TestIsolation(unittest.TestCase):
    """真实 State 隔离：路径复制不能碰到底座。"""

    def test_base_is_unchanged_for_each_delta_kind(self):
        """六类操作加深路径，应用后真实 State 必须逐字节不变。"""
        builders = [
            lambda: make_modify("/system/turn", 1, 2),
            lambda: make_modify("/units/u1/tags", ["infantry", "land"], ["land"], DataType.LIST),
            lambda: make_add("/system/phase", "combat", DataType.STRING),
            lambda: make_remove("/system/weather", None, DataType.NULL),
            lambda: make_list_insert(0, {"id": "x"}),
            lambda: make_list_remove(1, {"id": "n1"}),
            lambda: make_modify("/grid/0/1/hp", 2, 9),
            lambda: make_modify("/nodes/1", {"id": "n1"}, {"id": "n1-new"}, DataType.DICT),
        ]
        for build in builders:
            with self.subTest(builder=build):
                state = make_state()
                snapshot = copy.deepcopy(state)
                view = make_view(state)
                view.record(build())
                self.assertEqual(state, snapshot)

    def test_unmodified_subtrees_stay_shared(self):
        """没被改到的子树继续与真实 State 共享；改到的层是新副本。"""
        state = make_state()
        view = make_view(state)
        view.record(make_modify("/units/u1/hp", 10, 11))
        self.assertIs(view.get("/system"), state["system"])
        self.assertIs(view.get("/nodes"), state["nodes"])
        self.assertIsNot(view.get("/units"), state["units"])
        self.assertIsNot(view.get("/units/u1"), state["units"]["u1"])
        self.assertIs(view.get("/units/u1/tags"), state["units"]["u1"]["tags"])


class TestRecordAtomicity(unittest.TestCase):
    """record 单条原子：失败时视图与真实 State 都不变。"""

    def test_wrong_old_value_rejected(self):
        """旧值对不上时报冲突，且什么都没记录。"""
        state = make_state()
        view = make_view(state)
        with self.assertRaises(StateConflictError):
            view.record(make_modify("/system/turn", 99, 2))
        self.assertEqual(view.get("/system/turn"), 1)
        self.assertEqual(view.pending_deltas(), ())
        self.assertEqual(state, make_state())

    def test_missing_parent_rejected(self):
        """父容器不存在时报路径错误，且什么都没记录。"""
        state = make_state()
        view = make_view(state)
        with self.assertRaises(PathNotFoundError):
            view.record(make_remove("/nope/hp", 1))
        self.assertEqual(view.pending_deltas(), ())
        self.assertEqual(state, make_state())

    def test_non_delta_rejected(self):
        """入参必须是 Delta。"""
        view = make_view(make_state())
        with self.assertRaises(DeltaError):
            view.record({"path": "/system/turn"})

    def test_successful_record_appends_one_pending(self):
        """成功记录会进入待提交序列，且返回的是只读快照。"""
        view = make_view(make_state())
        view.record(make_modify("/system/turn", 1, 2))
        pending = view.pending_deltas()
        self.assertIsInstance(pending, tuple)
        self.assertEqual(len(pending), 1)


class TestCommandBinding(unittest.TestCase):
    """视图与 Command 绑定：构造参数、归属校验与顺序校验（D-41 / D-46）。"""

    def test_constructor_rejects_non_string_command_id(self):
        """command_id 必须是字符串。"""
        with self.assertRaises(TypeError):
            TempState(make_state(), 123)

    def test_constructor_rejects_empty_command_id(self):
        """command_id 不能是空字符串：每个视图必须绑定一个 Command。"""
        with self.assertRaises(ValueError):
            TempState(make_state(), "")

    def test_properties_expose_base_and_command(self):
        """base_state / command_id 属性分别指向真实 State 与绑定的 Command。"""
        state = make_state()
        view = make_view(state)
        self.assertIs(view.base_state, state)
        self.assertEqual(view.command_id, COMMAND_ID)

    def test_delta_from_other_command_rejected(self):
        """别的 Command 的变化量被拒绝，视图与真实 State 都不变。"""
        state = make_state()
        view = make_view(state)
        other = Delta(
            path="/system/turn", operation=Operation.MODIFY, data_type=DataType.NUMBER,
            trace=make_trace(1, command_id="c2"), value=2, old_value=1,
        )
        with self.assertRaises(TempStateError):
            view.record(other)
        self.assertEqual(view.pending_deltas(), ())
        self.assertEqual(state, make_state())

    def test_duplicate_sequence_rejected(self):
        """sequence 与上一条相同时按"重复记录"拒绝。"""
        state = make_state()
        view = make_view(state)
        view.record(Delta(
            path="/system/turn", operation=Operation.MODIFY, data_type=DataType.NUMBER,
            trace=make_trace(5), value=2, old_value=1,
        ))
        # 这条在数据上完全成立（old_value 取视图当前值），只靠 sequence 挡住。
        duplicate = Delta(
            path="/system/turn", operation=Operation.MODIFY, data_type=DataType.NUMBER,
            trace=make_trace(5), value=3, old_value=2,
        )
        with self.assertRaises(TempStateError):
            view.record(duplicate)
        self.assertEqual(len(view.pending_deltas()), 1)
        self.assertEqual(view.get("/system/turn"), 2)

    def test_out_of_order_sequence_rejected(self):
        """sequence 小于上一条时按"错序"拒绝。"""
        state = make_state()
        view = make_view(state)
        view.record(Delta(
            path="/system/turn", operation=Operation.MODIFY, data_type=DataType.NUMBER,
            trace=make_trace(9), value=2, old_value=1,
        ))
        late = Delta(
            path="/system/turn", operation=Operation.MODIFY, data_type=DataType.NUMBER,
            trace=make_trace(3), value=3, old_value=2,
        )
        with self.assertRaises(TempStateError):
            view.record(late)
        self.assertEqual(view.get("/system/turn"), 2)

    def test_rejected_record_does_not_consume_sequence(self):
        """被拒绝的记录不进入序列，同 sequence 的合法记录仍可后补。"""
        state = make_state()
        view = make_view(state)
        with self.assertRaises(StateConflictError):
            view.record(Delta(
                path="/system/turn", operation=Operation.MODIFY, data_type=DataType.NUMBER,
                trace=make_trace(1), value=2, old_value=99,
            ))
        view.record(Delta(
            path="/system/turn", operation=Operation.MODIFY, data_type=DataType.NUMBER,
            trace=make_trace(1), value=2, old_value=1,
        ))
        self.assertEqual(view.get("/system/turn"), 2)


class TestRecordFreezing(unittest.TestCase):
    """record 冻结 value / old_value：调用方事后改自己手上的容器不影响记录。"""

    def test_value_mutation_after_record_does_not_change_commit(self):
        """新增值被事后修改：视图与提交仍保持一致。"""
        state = make_state()
        view = make_view(state)
        unit = {"hp": 99}
        view.record(Delta(
            path="/units/u2", operation=Operation.ADD, data_type=DataType.DICT,
            trace=next_trace(), value=unit,
        ))
        seen = view.get("/units/u2/hp")
        unit["hp"] = 1  # 调用方继续改自己手上的对象
        view.commit(state)
        self.assertEqual(seen, 99)
        self.assertEqual(state["units"]["u2"]["hp"], 99)

    def test_list_insert_value_mutation_after_record_does_not_change_commit(self):
        """列表插入的元素被事后修改：视图与提交仍保持一致。"""
        state = make_state()
        view = make_view(state)
        node = {"id": "x"}
        view.record(make_list_insert(0, node))
        seen = view.get("/nodes/0")
        node["id"] = "changed"
        view.commit(state)
        self.assertEqual(seen, {"id": "x"})
        self.assertEqual(state["nodes"][0], {"id": "x"})

    def test_old_value_mutation_after_record_keeps_check_basis(self):
        """记录里的 old_value 被事后修改，提交仍按冻结时的旧值核对。"""
        state = {"u": {"hp": 1}}
        view = make_view(state)
        old = {"hp": 1}
        view.record(Delta(
            path="/u", operation=Operation.MODIFY, data_type=DataType.DICT,
            trace=next_trace(), value={"hp": 5}, old_value=old,
        ))
        state["u"] = {"hp": 2}  # 真实 State 被别处改过
        old["hp"] = 2           # 调用方把自己手上的旧值对象也改了
        with self.assertRaises(StateConflictError):
            view.commit(state)
        self.assertEqual(view.status, ViewStatus.FAILED)

    def test_pending_records_hold_frozen_copies(self):
        """pending_deltas 里的值既不是调用方对象，也不是视图树里的对象。"""
        state = make_state()
        view = make_view(state)
        unit = {"hp": 99}
        view.record(Delta(
            path="/units/u2", operation=Operation.ADD, data_type=DataType.DICT,
            trace=next_trace(), value=unit,
        ))
        stored = view.pending_deltas()[0]
        self.assertIsNot(stored.value, unit)
        self.assertIsNot(stored.value, view.get("/units/u2"))
        self.assertEqual(stored.value, {"hp": 99})


class TestCommandDelta(unittest.TestCase):
    """CommandDelta：合并规则与"原始记录 / 提交内容"的分工（§7.4 / D-44）。"""

    def test_empty_command_delta_is_empty_tuple(self):
        """没有记录时合并结果是空元组。"""
        view = make_view(make_state())
        self.assertEqual(view.command_delta(), ())

    def test_adjacent_modifies_are_merged(self):
        """相邻、同路径、首尾相接的修改合并为一条：保留最早旧值与最晚新值。"""
        state = make_state()
        view = make_view(state)
        view.record(make_modify("/system/turn", 1, 2))
        view.record(make_modify("/system/turn", 2, 3))
        merged = view.command_delta()
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].old_value, 1)
        self.assertEqual(merged[0].value, 3)
        # 原始记录不被合并结果覆盖，仍供日志使用。
        self.assertEqual(len(view.pending_deltas()), 2)

    def test_modifies_separated_by_other_path_are_not_merged(self):
        """被别的路径的操作隔开时不合并（只合并紧邻记录）。"""
        state = make_state()
        view = make_view(state)
        view.record(make_modify("/system/turn", 1, 2))
        view.record(make_modify("/units/u1/hp", 10, 11))
        view.record(make_modify("/system/turn", 2, 3))
        self.assertEqual(len(view.command_delta()), 3)

    def test_list_ops_never_merge(self):
        """带 list_op 的列表增删永不合并。"""
        state = make_state()
        view = make_view(state)
        view.record(make_list_insert(0, {"id": "x"}))
        view.record(make_list_insert(0, {"id": "y"}))
        self.assertEqual(len(view.command_delta()), 2)

    def test_commit_replays_merged_delta(self):
        """提交重放合并结果，终态与逐步应用一致。"""
        state = make_state()
        view = make_view(state)
        view.record(make_modify("/system/turn", 1, 2))
        view.record(make_modify("/system/turn", 2, 3))
        view.commit(state)
        self.assertEqual(state["system"]["turn"], 3)
        self.assertEqual(len(view.command_delta()), 1)

    def test_command_delta_available_after_terminal(self):
        """终态后 command_delta 仍可读，供日志与批次聚合；叠加视图已释放。"""
        state = make_state()
        view = make_view(state)
        view.record(make_modify("/system/turn", 1, 2))
        view.commit(state)
        self.assertEqual(len(view.command_delta()), 1)
        self.assertIsNone(view._root)


class TestCommit(unittest.TestCase):
    """提交：按原顺序重放到真实 State 这个对象上。"""

    def test_commit_applies_to_base_object(self):
        """提交改的是原底座对象，根身份不变。"""
        state = make_state()
        view = make_view(state)
        view.record(make_modify("/system/turn", 1, 2))
        view.record(make_list_insert(0, {"id": "x"}))
        view.commit(state)
        self.assertEqual(state["system"]["turn"], 2)
        self.assertEqual([item["id"] for item in state["nodes"]], ["x", "n0", "n1"])
        self.assertEqual(view.status, ViewStatus.COMMITTED)

    def test_commit_matches_direct_apply(self):
        """提交结果与"把同一批变化量按序直接应用"完全一致。"""
        direct_state = make_state()
        view_state = make_state()
        deltas = [
            make_modify("/system/turn", 1, 2),
            make_list_insert(0, {"id": "x"}),
            make_list_remove(2, {"id": "n1"}),
        ]
        view = make_view(view_state)
        for delta in deltas:
            view.record(delta)
        view.commit(view_state)
        for delta in deltas:
            apply_delta(direct_state, delta)
        self.assertEqual(view_state, direct_state)

    def test_empty_commit_is_noop(self):
        """没有待提交变化量时提交是空操作。"""
        state = make_state()
        view = make_view(state)
        view.commit(state)
        self.assertEqual(state, make_state())
        self.assertEqual(view.status, ViewStatus.COMMITTED)

    def test_commit_target_must_be_base(self):
        """提交到别的树是使用错误，视图保持 OPEN。"""
        state = make_state()
        view = make_view(state)
        view.record(make_modify("/system/turn", 1, 2))
        with self.assertRaises(TempStateError):
            view.commit({})
        self.assertEqual(view.status, ViewStatus.OPEN)

    def test_commit_failure_marks_failed(self):
        """真实 State 被别处改过导致重放失败时，视图进入 FAILED。"""
        state = make_state()
        view = make_view(state)
        view.record(make_modify("/system/turn", 1, 2))
        state["system"]["turn"] = 99  # 模拟真实 State 在 Command 期间被改动
        with self.assertRaises(StateConflictError):
            view.commit(state)
        self.assertEqual(view.status, ViewStatus.FAILED)

    def test_operations_after_commit_are_rejected(self):
        """终态之后读写、提交、丢弃全部报错；只读诊断仍可读。"""
        state = make_state()
        view = make_view(state)
        view.commit(state)
        with self.assertRaises(TempStateError):
            view.get("/system/turn")
        with self.assertRaises(TempStateError):
            view.exists("/system/turn")
        with self.assertRaises(TempStateError):
            view.record(make_modify("/system/turn", 1, 2))
        with self.assertRaises(TempStateError):
            view.commit(state)
        with self.assertRaises(TempStateError):
            view.discard()
        self.assertEqual(view.pending_deltas(), ())


class TestDiscard(unittest.TestCase):
    """丢弃：真实 State 一个字节都不动。"""

    def test_discard_leaves_base_untouched(self):
        """丢弃后真实 State 与开始时完全一致。"""
        state = make_state()
        snapshot = copy.deepcopy(state)
        view = make_view(state)
        view.record(make_modify("/system/turn", 1, 2))
        view.record(make_list_insert(0, {"id": "x"}))
        view.discard()
        self.assertEqual(state, snapshot)
        self.assertEqual(view.status, ViewStatus.DISCARDED)
        with self.assertRaises(TempStateError):
            view.get("/system/turn")


class TestListSemanticsInView(unittest.TestCase):
    """列表下标"应用当时"语义在视图 + 提交的整条链路上成立。"""

    def test_insert_then_modify_uses_current_index(self):
        """先插入再按新下标改，提交后结果与逐步操作一致。"""
        state = make_state()
        view = make_view(state)
        view.record(make_list_insert(0, {"id": "x"}))
        view.record(make_modify("/nodes/1", {"id": "n0"}, {"id": "n0-new"}, DataType.DICT))
        view.commit(state)
        self.assertEqual([item["id"] for item in state["nodes"]], ["x", "n0-new", "n1"])

    def test_remove_then_modify_uses_current_index(self):
        """先删除再按新下标改，提交后结果与逐步操作一致。"""
        state = make_state()
        view = make_view(state)
        view.record(make_list_remove(0, {"id": "n0"}))
        view.record(make_modify("/nodes/0", {"id": "n1"}, {"id": "n1-new"}, DataType.DICT))
        view.commit(state)
        self.assertEqual([item["id"] for item in state["nodes"]], ["n1-new"])


if __name__ == "__main__":
    unittest.main()
