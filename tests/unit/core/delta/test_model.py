"""model 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/delta/
运行：在仓库根执行 `python run_tests.py`。
覆盖：model.py 模块文档里的四条格式约定，以及 Trace 的字段校验。
"""

import copy
import pickle
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
)
from core.delta.model import _Missing


def make_trace(sequence: int = 1) -> Trace:
    """造一个最小可用的 Trace。

    输入：sequence 排序号，默认 1。
    输出：Trace 实例。
    变量：无。
    """
    return Trace(
        sequence=sequence,
        timestamp=1.5,
        command_id="c1",
        action_id="a1",
        service_id="s1",
        mod_id="m1",
    )


def make_add(**overrides) -> Delta:
    """造一条合法的"新增键"变化量，用 overrides 替换个别字段。

    输入：overrides 关键字参数，覆盖默认字段。
    输出：Delta 实例（可能因覆盖值不合法而抛 DeltaError）。
    变量：
        params: 默认字段与覆盖字段合并后的构造参数。
    """
    params = {
        "path": "/units/u2",
        "operation": Operation.ADD,
        "data_type": DataType.DICT,
        "trace": make_trace(),
        "value": {"id": "u2"},
    }
    params.update(overrides)
    return Delta(**params)


def make_remove(**overrides) -> Delta:
    """造一条合法的"删除键"变化量。"""
    params = {
        "path": "/units/u1",
        "operation": Operation.REMOVE,
        "data_type": DataType.DICT,
        "trace": make_trace(),
        "old_value": {"id": "u1"},
    }
    params.update(overrides)
    return Delta(**params)


def make_modify(**overrides) -> Delta:
    """造一条合法的"修改值"变化量。"""
    params = {
        "path": "/system/turn",
        "operation": Operation.MODIFY,
        "data_type": DataType.NUMBER,
        "trace": make_trace(),
        "value": 2,
        "old_value": 1,
    }
    params.update(overrides)
    return Delta(**params)


def make_list_insert(**overrides) -> Delta:
    """造一条合法的"列表插入"变化量。"""
    params = {
        "path": "/nodes",
        "operation": Operation.MODIFY,
        "data_type": DataType.LIST,
        "trace": make_trace(),
        "value": {"id": "x"},
        "list_op": ListOp.INSERT,
        "index": 1,
    }
    params.update(overrides)
    return Delta(**params)


def make_list_remove(**overrides) -> Delta:
    """造一条合法的"列表删除"变化量。"""
    params = {
        "path": "/nodes",
        "operation": Operation.MODIFY,
        "data_type": DataType.LIST,
        "trace": make_trace(),
        "old_value": {"id": "n0"},
        "list_op": ListOp.REMOVE,
        "index": 0,
    }
    params.update(overrides)
    return Delta(**params)


class TestKeywiseOperations(unittest.TestCase):
    """非列表增删：新增 / 删除 / 修改的字段存在性规则。"""

    def test_valid_shapes(self):
        """三种基本操作都能正常构造。"""
        self.assertEqual(make_add().value, {"id": "u2"})
        self.assertEqual(make_remove().old_value, {"id": "u1"})
        self.assertEqual(make_modify().old_value, 1)

    def test_add_rejects_old_value(self):
        """新增时键不存在，不允许带 old_value。"""
        with self.assertRaises(DeltaError):
            make_add(old_value=1)

    def test_add_requires_value(self):
        """新增必须带 value。"""
        with self.assertRaises(DeltaError):
            make_add(value=MISSING)

    def test_remove_rejects_value(self):
        """删除不允许带 value。"""
        with self.assertRaises(DeltaError):
            make_remove(value=1)

    def test_remove_requires_old_value(self):
        """删除必须带 old_value。"""
        with self.assertRaises(DeltaError):
            make_remove(old_value=MISSING)

    def test_modify_requires_both_values(self):
        """修改必须同时带 value 与 old_value。"""
        with self.assertRaises(DeltaError):
            make_modify(old_value=MISSING)
        with self.assertRaises(DeltaError):
            make_modify(value=MISSING)

    def test_none_is_a_legal_value(self):
        """None 是合法值，与 MISSING 不同：新增一个值为 None 的键是允许的。"""
        delta = make_add(value=None)
        self.assertIsNone(delta.value)
        self.assertIs(delta.old_value, MISSING)


class TestListOperations(unittest.TestCase):
    """列表增删：list_op / index 的规则。"""

    def test_valid_list_ops(self):
        """列表插入与删除都能正常构造。"""
        self.assertEqual(make_list_insert().index, 1)
        self.assertEqual(make_list_remove().index, 0)

    def test_list_op_requires_index(self):
        """列表增删必须带 index。"""
        with self.assertRaises(DeltaError):
            make_list_insert(index=None)
        with self.assertRaises(DeltaError):
            make_list_remove(index=None)

    def test_list_op_requires_list_type(self):
        """列表增删的 data_type 必须是 list。"""
        with self.assertRaises(DeltaError):
            make_list_insert(data_type=DataType.STRING)

    def test_list_op_requires_modify(self):
        """列表增删的 operation 必须是 modify（D-34）。"""
        with self.assertRaises(DeltaError):
            make_list_insert(operation=Operation.ADD)

    def test_insert_rejects_old_value(self):
        """插入不携带被删的值。"""
        with self.assertRaises(DeltaError):
            make_list_insert(old_value={"id": "n0"})

    def test_remove_rejects_value(self):
        """删除不携带新值。"""
        with self.assertRaises(DeltaError):
            make_list_remove(value={"id": "x"})

    def test_index_must_be_plain_non_negative_int(self):
        """index 拒绝 bool 与负数。"""
        with self.assertRaises(DeltaError):
            make_list_insert(index=True)
        with self.assertRaises(DeltaError):
            make_list_insert(index=-1)

    def test_index_forbidden_without_list_op(self):
        """普通修改不允许带 index。"""
        with self.assertRaises(DeltaError):
            make_modify(index=0)


class TestPathAndFieldTypes(unittest.TestCase):
    """路径与字段类型校验。"""

    def test_root_path_is_rejected(self):
        """根不是一个变量，不允许作为变化量目标。"""
        with self.assertRaises(DeltaError):
            make_modify(path="")

    def test_bad_pointer_is_rejected(self):
        """非法 JSON Pointer 要早失败。"""
        with self.assertRaises(DeltaError):
            make_modify(path="/a/~2")

    def test_enums_are_required(self):
        """operation / data_type 必须是枚举成员，不接受裸字符串。"""
        with self.assertRaises(DeltaError):
            make_modify(operation="modify")
        with self.assertRaises(DeltaError):
            make_modify(data_type="number")

    def test_trace_is_required(self):
        """trace 必须是 Trace 实例。"""
        with self.assertRaises(DeltaError):
            make_modify(trace={"sequence": 1})

    def test_label_must_be_str(self):
        """label 必须是字符串。"""
        with self.assertRaises(DeltaError):
            make_modify(label=1)


class TestTrace(unittest.TestCase):
    """Trace 自身的字段校验。"""

    def test_valid_trace(self):
        """合法 Trace 能构造。"""
        self.assertEqual(make_trace().sequence, 1)

    def test_sequence_must_be_non_negative_int(self):
        """sequence 拒绝 bool 与负数。"""
        with self.assertRaises(DeltaError):
            make_trace(sequence=True)
        with self.assertRaises(DeltaError):
            make_trace(sequence=-1)

    def test_timestamp_must_be_number(self):
        """timestamp 必须是数字。"""
        with self.assertRaises(DeltaError):
            Trace(sequence=1, timestamp="now", command_id="", action_id="", service_id="", mod_id="")

    def test_ids_must_be_str(self):
        """标识字段必须是字符串。"""
        with self.assertRaises(DeltaError):
            Trace(sequence=1, timestamp=0.0, command_id=1, action_id="", service_id="", mod_id="")


class TestFormatVersion(unittest.TestCase):
    """格式版本常量。"""

    def test_version_is_pinned(self):
        """初版格式版本是 1；改字段时必须同时提升它。"""
        self.assertEqual(DELTA_FORMAT_VERSION, 1)


class TestMissingSentinel(unittest.TestCase):
    """MISSING 必须是真单例：构造、浅拷贝、深拷贝、pickle 都不能造出第二个哨兵。"""

    def test_constructor_returns_singleton(self):
        """直接构造也只拿到同一个哨兵。"""
        self.assertIs(_Missing(), MISSING)

    def test_copy_and_deepcopy_return_singleton(self):
        """浅拷贝与深拷贝都返回同一个哨兵。"""
        self.assertIs(copy.copy(MISSING), MISSING)
        self.assertIs(copy.deepcopy(MISSING), MISSING)

    def test_pickle_round_trip_returns_singleton(self):
        """pickle 往返后仍是同一个哨兵。"""
        self.assertIs(pickle.loads(pickle.dumps(MISSING)), MISSING)

    def test_delta_deepcopy_keeps_missing(self):
        """深拷贝一条变化量后，缺省字段仍被识别为 MISSING。"""
        copied = copy.deepcopy(make_add())
        self.assertIs(copied.old_value, MISSING)
        self.assertEqual(copied.value, {"id": "u2"})


class TestEnumStringForm(unittest.TestCase):
    """枚举的字符串表示统一用英文取值，f-string 与日志不受 Python 版本影响。"""

    def test_str_returns_value(self):
        """str(成员) 返回取值本身。"""
        self.assertEqual(str(Operation.ADD), "add")
        self.assertEqual(str(DataType.LIST), "list")
        self.assertEqual(str(ListOp.INSERT), "insert")


class TestErrorPath(unittest.TestCase):
    """DeltaError 要带上变化量路径，便于日志按路径检索。"""

    def test_validation_error_carries_path(self):
        """字段组合错误也要带上本条变化量的路径。"""
        with self.assertRaises(DeltaError) as ctx:
            make_modify(index=0)
        self.assertEqual(ctx.exception.path, "/system/turn")
        self.assertIn("index", ctx.exception.detail)

    def test_bad_path_still_carries_raw_path(self):
        """路径本身不合法时，异常里仍然带上原始写法。"""
        with self.assertRaises(DeltaError) as ctx:
            make_modify(path="/a/~2")
        self.assertEqual(ctx.exception.path, "/a/~2")


if __name__ == "__main__":
    unittest.main()
