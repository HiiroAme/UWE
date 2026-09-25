"""codec 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/delta/
运行：在仓库根执行 `python run_tests.py`。
覆盖：JSON 往返、缺省字段不写、None 与 MISSING 的区分、版本与非法记录拒绝。
"""

import copy
import json
import unittest

from core.delta import (
    DELTA_FORMAT_VERSION,
    MISSING,
    DataType,
    Delta,
    DeltaError,
    ListOp,
    Operation,
    Trace,
    from_dict,
    to_dict,
)


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


def make_deltas() -> list[Delta]:
    """造一批覆盖全部操作形态的变化量，用于往返测试。

    输入：无。
    输出：Delta 列表，依次覆盖新增、值为 None 的新增、删除、修改、
        整表替换、列表插入、列表删除。
    变量：无。
    """
    return [
        Delta(
            path="/system/turn", operation=Operation.ADD, data_type=DataType.NUMBER,
            trace=make_trace(), value=1,
        ),
        Delta(
            path="/system/weather", operation=Operation.ADD, data_type=DataType.NULL,
            trace=make_trace(), value=None,
        ),
        Delta(
            path="/units/u1/name", operation=Operation.REMOVE, data_type=DataType.STRING,
            trace=make_trace(), old_value="步兵",
        ),
        Delta(
            path="/units/u1/moved", operation=Operation.MODIFY, data_type=DataType.BOOL,
            trace=make_trace(), value=True, old_value=False,
        ),
        Delta(
            path="/units/u1/tags", operation=Operation.MODIFY, data_type=DataType.LIST,
            trace=make_trace(), value=["land"], old_value=["infantry", "land"],
        ),
        Delta(
            path="/nodes", operation=Operation.MODIFY, data_type=DataType.LIST,
            trace=make_trace(), value={"id": "x"}, list_op=ListOp.INSERT, index=0,
        ),
        Delta(
            path="/nodes", operation=Operation.MODIFY, data_type=DataType.LIST,
            trace=make_trace(), old_value={"id": "n1"}, list_op=ListOp.REMOVE, index=1,
        ),
    ]


class TestRoundTrip(unittest.TestCase):
    """序列化往返。"""

    def test_each_shape_round_trips_through_json(self):
        """每种操作形态都能经 json.dumps/loads 往返相等。"""
        for delta in make_deltas():
            with self.subTest(path=delta.path, operation=delta.operation, list_op=delta.list_op):
                record = to_dict(delta)
                text = json.dumps(record, ensure_ascii=False)
                restored = from_dict(json.loads(text))
                self.assertEqual(restored, delta)

    def test_missing_fields_are_omitted(self):
        """缺省字段不出现在记录里，而不是写成 null。"""
        add_record = to_dict(make_deltas()[0])
        self.assertNotIn("old_value", add_record)
        self.assertNotIn("list_op", add_record)
        self.assertNotIn("index", add_record)

        insert_record = to_dict(make_deltas()[5])
        self.assertNotIn("old_value", insert_record)
        self.assertIn("list_op", insert_record)
        self.assertEqual(insert_record["index"], 0)

    def test_none_value_is_written(self):
        """值为 None 的字段必须写出来（键存在，值为 null）。"""
        record = to_dict(make_deltas()[1])
        self.assertIn("value", record)
        self.assertIsNone(record["value"])
        restored = from_dict(record)
        self.assertIsNone(restored.value)
        self.assertIs(restored.old_value, MISSING)

    def test_format_version_is_written(self):
        """每条记录自带格式版本。"""
        self.assertEqual(to_dict(make_deltas()[0])["format_version"], DELTA_FORMAT_VERSION)


class TestRejections(unittest.TestCase):
    """坏记录必须被拒绝。"""

    def test_version_mismatch(self):
        """版本不符直接报错，不做兼容猜测。"""
        record = to_dict(make_deltas()[0])
        record["format_version"] = DELTA_FORMAT_VERSION + 1
        with self.assertRaises(DeltaError):
            from_dict(record)

    def test_unknown_field(self):
        """未知字段说明拼写或版本有问题，必须报错。"""
        record = to_dict(make_deltas()[0])
        record["reason"] = "因为"
        with self.assertRaises(DeltaError):
            from_dict(record)

    def test_missing_required_field(self):
        """缺必填字段报错。"""
        record = to_dict(make_deltas()[0])
        del record["operation"]
        with self.assertRaises(DeltaError):
            from_dict(record)

    def test_unknown_enum_value(self):
        """枚举取值不明报错。"""
        record = to_dict(make_deltas()[0])
        record["operation"] = "delete"
        with self.assertRaises(DeltaError):
            from_dict(record)

    def test_non_dict_input(self):
        """输入必须是 dict。"""
        with self.assertRaises(DeltaError):
            from_dict(["not", "a", "dict"])

    def test_bad_trace_record(self):
        """trace 缺字段或出现未知字段都要报错。"""
        record = to_dict(make_deltas()[0])
        record["trace"] = {"sequence": 1}
        with self.assertRaises(DeltaError):
            from_dict(record)

        record = to_dict(make_deltas()[0])
        record["trace"]["extra"] = 1
        with self.assertRaises(DeltaError):
            from_dict(record)

    def test_inconsistent_field_combination_is_rejected(self):
        """字段组合矛盾由模型层拦下（这里用"插入带 old_value"举例）。"""
        record = to_dict(make_deltas()[5])
        record["old_value"] = {"id": "n0"}
        with self.assertRaises(DeltaError):
            from_dict(record)

    def test_missing_label_is_rejected(self):
        """label 是必填字段（空字符串表示无事件，但键必须存在）。"""
        record = to_dict(make_deltas()[0])
        del record["label"]
        with self.assertRaises(DeltaError):
            from_dict(record)

    def test_error_carries_record_path(self):
        """坏记录报错时，异常要带上记录里的 path。"""
        record = to_dict(make_deltas()[0])
        record["operation"] = "delete"
        with self.assertRaises(DeltaError) as ctx:
            from_dict(record)
        self.assertEqual(ctx.exception.path, record["path"])

    def test_deepcopied_delta_still_serializes(self):
        """深拷贝后的变化量仍能正常序列化（哨兵没有在复制中失效）。"""
        copied = copy.deepcopy(make_deltas()[0])
        record = to_dict(copied)
        self.assertNotIn("old_value", record)
        json.dumps(record)


if __name__ == "__main__":
    unittest.main()
