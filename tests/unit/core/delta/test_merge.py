"""merge 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/delta/
运行：在仓库根执行 `python run_tests.py`。
覆盖：相邻合并的正例与反例、列表增删永不合并、结果保留最晚的 label / trace。
      以及"首尾相接"的严格口径：`True` 与 `1` 不算相接（与 apply 的 values_match 一致，D-44）。
"""

import unittest

from core.delta import (
    DataType,
    Delta,
    DeltaError,
    ListOp,
    Operation,
    Trace,
    merge_adjacent,
)


def make_trace(sequence: int = 1, command_id: str = "c1") -> Trace:
    """造一个最小可用的 Trace。

    输入：
        sequence: 排序号；
        command_id: Command 标识（跨 Command 合并测试要用不同的值）。
    输出：Trace 实例。
    变量：无。
    """
    return Trace(
        sequence=sequence,
        timestamp=1.5,
        command_id=command_id,
        action_id="a1",
        service_id="s1",
        mod_id="m1",
    )


def make_modify(path: str, old_value, value, sequence: int = 1, label: str = "") -> Delta:
    """造一条修改变化量。

    输入：
        path: 目标路径；
        old_value / value: 替换前后的值；
        sequence: 排序号，同时写进 trace；
        label: 事件 id。
    输出：Delta 实例。
    变量：无。
    """
    return Delta(
        path=path,
        operation=Operation.MODIFY,
        data_type=DataType.NUMBER,
        trace=make_trace(sequence),
        label=label,
        value=value,
        old_value=old_value,
    )


def make_list_insert(index: int, value, sequence: int = 1) -> Delta:
    """造一条列表插入变化量。"""
    return Delta(
        path="/nodes",
        operation=Operation.MODIFY,
        data_type=DataType.LIST,
        trace=make_trace(sequence),
        value=value,
        list_op=ListOp.INSERT,
        index=index,
    )


class TestMergeAdjacent(unittest.TestCase):
    """相邻合并规则。"""

    def test_chained_modifies_merge(self):
        """首尾相接的两条相邻修改合并成一条：最早旧值 + 最晚新值。"""
        first = make_modify("/system/turn", 1, 2, sequence=1, label="事件A")
        second = make_modify("/system/turn", 2, 3, sequence=2, label="事件B")
        merged = merge_adjacent([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].old_value, 1)
        self.assertEqual(merged[0].value, 3)
        self.assertEqual(merged[0].label, "事件B")
        self.assertEqual(merged[0].trace.sequence, 2)

    def test_broken_chain_does_not_merge(self):
        """前一条的新值与后一条的旧值不相接时不合并，留给 apply 报错。"""
        first = make_modify("/system/turn", 1, 2, sequence=1)
        second = make_modify("/system/turn", 99, 3, sequence=2)
        self.assertEqual(len(merge_adjacent([first, second])), 2)

    def test_bool_and_number_are_not_a_valid_chain(self):
        """甲-5：`True` 与 `1` 不算"首尾相接"（与 apply 的 values_match 同一口径，D-44）。

        裸 `==` 会说它们相接，于是把"记录本来就不一致"的两条合并掉、
        吞掉 apply 该报的冲突；这里钉住严格口径。
        """
        first = make_modify("/system/flag", 1, True, sequence=1)
        second = make_modify("/system/flag", 1, 5, sequence=2)
        self.assertEqual(len(merge_adjacent([first, second])), 2)     # bool 与 1 不相接

        third = make_modify("/system/flag", True, 7, sequence=3)
        self.assertEqual(len(merge_adjacent([first, third])), 1)      # 真·相接（True → True）

    def test_int_and_float_still_merge(self):
        """`1` 与 `1.0` 仍然算相接：JSON 往返不该产生假冲突（values_match 的既有口径）。"""
        first = make_modify("/system/turn", 1, 2, sequence=1)
        second = make_modify("/system/turn", 2.0, 3, sequence=2)
        merged = merge_adjacent([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0].old_value, merged[0].value), (1, 3))

    def test_container_bool_and_number_are_not_a_valid_chain(self):
        """容器里的 bool / 数值也一样严格：`{"a": 1}` 与 `{"a": True}` 不算相接。"""
        first = make_modify("/system/flags", {"a": 0}, {"a": 1}, sequence=1)
        second = make_modify("/system/flags", {"a": True}, {"a": 2}, sequence=2)
        self.assertEqual(len(merge_adjacent([first, second])), 2)

    def test_non_adjacent_does_not_merge(self):
        """中间隔着别的变化量时不合并（下标可能漂移）。"""
        first = make_modify("/system/turn", 1, 2, sequence=1)
        middle = make_modify("/system/other", 10, 20, sequence=2)
        third = make_modify("/system/turn", 2, 3, sequence=3)
        merged = merge_adjacent([first, middle, third])
        self.assertEqual(len(merged), 3)
        self.assertEqual(
            [delta.path for delta in merged],
            ["/system/turn", "/system/other", "/system/turn"],
        )

    def test_different_path_or_type_does_not_merge(self):
        """路径或类型不同不合并。"""
        first = make_modify("/system/turn", 1, 2, sequence=1)
        other_path = make_modify("/system/weather", 2, 3, sequence=2)
        self.assertEqual(len(merge_adjacent([first, other_path])), 2)

        other_type = Delta(
            path="/system/turn", operation=Operation.MODIFY, data_type=DataType.STRING,
            trace=make_trace(3), value="x", old_value="y",
        )
        self.assertEqual(len(merge_adjacent([first, other_type])), 2)

    def test_list_ops_never_merge(self):
        """带 list_op 的列表增删永不合并。"""
        first = make_list_insert(0, {"id": "a"}, sequence=1)
        second = make_list_insert(0, {"id": "b"}, sequence=2)
        self.assertEqual(len(merge_adjacent([first, second])), 2)

    def test_cross_command_does_not_merge(self):
        """跨 Command 的变化量不合并：否则会丢掉中间 Command 的 trace。"""
        first = Delta(
            path="/system/turn", operation=Operation.MODIFY, data_type=DataType.NUMBER,
            trace=make_trace(1, command_id="cmd-A"), value=2, old_value=1,
        )
        second = Delta(
            path="/system/turn", operation=Operation.MODIFY, data_type=DataType.NUMBER,
            trace=make_trace(2, command_id="cmd-B"), value=3, old_value=2,
        )
        self.assertEqual(len(merge_adjacent([first, second])), 2)

    def test_full_round_trip_keeps_noop(self):
        """完整往返（3→5→3）合并后是一条"3→3"的空操作：只压紧，不做代数化简。"""
        first = make_modify("/system/turn", 3, 5, sequence=1)
        second = make_modify("/system/turn", 5, 3, sequence=2)
        merged = merge_adjacent([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].old_value, 3)
        self.assertEqual(merged[0].value, 3)

    def test_element_replaces_merge(self):
        """元素替换是普通修改，相邻且相接时可以合并。"""
        first = Delta(
            path="/nodes/1", operation=Operation.MODIFY, data_type=DataType.DICT,
            trace=make_trace(1), value={"id": "x"}, old_value={"id": "n0"},
        )
        second = Delta(
            path="/nodes/1", operation=Operation.MODIFY, data_type=DataType.DICT,
            trace=make_trace(2), value={"id": "y"}, old_value={"id": "x"},
        )
        merged = merge_adjacent([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].old_value, {"id": "n0"})
        self.assertEqual(merged[0].value, {"id": "y"})

    def test_input_is_not_modified(self):
        """返回新列表，不改动传入的列表。"""
        first = make_modify("/system/turn", 1, 2, sequence=1)
        second = make_modify("/system/turn", 2, 3, sequence=2)
        source = [first, second]
        merged = merge_adjacent(source)
        self.assertEqual(len(source), 2)
        self.assertEqual(len(merged), 1)

    def test_empty_and_single(self):
        """空序列返回空列表；单条原样返回。"""
        self.assertEqual(merge_adjacent([]), [])
        only = make_modify("/system/turn", 1, 2)
        self.assertEqual(merge_adjacent([only]), [only])

    def test_non_delta_input(self):
        """序列里出现非 Delta 对象要报错。"""
        with self.assertRaises(DeltaError):
            merge_adjacent([make_modify("/system/turn", 1, 2), "not a delta"])


if __name__ == "__main__":
    unittest.main()
