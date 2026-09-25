"""table 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/registry/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 登记、查重（重复 id 报错）、按注册顺序遍历；
  - get / find / require 三种取法在"未注册"与"已禁用"两种情况下的差别；
  - 类型不匹配的条目拒绝登记。
"""

import unittest

from core.registry import (
    DuplicateEntryError,
    EntryFormatError,
    MissingEntryError,
    RegistryEntry,
    RegistryTable,
)


def make_rule(name: str, **overrides) -> RegistryEntry:
    """造一条 rule 类型条目。

    输入：
        name: id 的第三段（例如 "can_move"）；
        overrides: 要覆盖的字段。
    输出：
        构造好的条目。
    异常：
        无（构造异常由被测用例自己触发）。
    变量：
        fields: 默认字段字典。
    """
    fields = {
        "id": f"my_mod:rule:{name}",
        "type": "rule",
        "data": {"condition": True},
    }
    fields.update(overrides)
    return RegistryEntry(**fields)


class TestRegister(unittest.TestCase):
    """登记与查重。"""

    def test_register_and_get(self):
        """登记的条目能按 id 取回，长度与包含判断正确。"""
        table = RegistryTable("rule")
        entry = make_rule("can_move")
        table.register(entry)
        self.assertIs(table.get("my_mod:rule:can_move"), entry)
        self.assertEqual(len(table), 1)
        self.assertIn("my_mod:rule:can_move", table)
        self.assertTrue(table.has("my_mod:rule:can_move"))

    def test_duplicate_id_rejected(self):
        """同一个 id 不能登记两次（单 Mod 运行，重复即数据写错）。"""
        table = RegistryTable("rule")
        table.register(make_rule("can_move"))
        with self.assertRaises(DuplicateEntryError):
            table.register(make_rule("can_move"))

    def test_wrong_type_rejected(self):
        """条目 type 与注册表不一致时拒绝登记。"""
        table = RegistryTable("rule")
        with self.assertRaises(EntryFormatError):
            table.register(RegistryEntry(id="my_mod:entity:infantry", type="entity", data={}))

    def test_iteration_keeps_registration_order(self):
        """遍历顺序就是登记顺序（确定性要求：禁止依赖无序容器）。"""
        table = RegistryTable("rule")
        for name in ("c", "a", "b"):
            table.register(make_rule(name))
        self.assertEqual(table.ids(), ("my_mod:rule:c", "my_mod:rule:a", "my_mod:rule:b"))
        self.assertEqual([entry.name for entry in table], ["c", "a", "b"])


class TestLookup(unittest.TestCase):
    """get / find / require 的口径差别。"""

    def test_missing_id(self):
        """没登记过的 id：find 给 None，get / require 抛 MissingEntryError。"""
        table = RegistryTable("rule")
        self.assertIsNone(table.find("my_mod:rule:nope"))
        with self.assertRaises(MissingEntryError):
            table.get("my_mod:rule:nope")
        with self.assertRaises(MissingEntryError):
            table.require("my_mod:rule:nope")

    def test_disabled_entry(self):
        """被禁用的条目：find / get 能拿到，require 拒绝并说明原因。"""
        table = RegistryTable("rule")
        table.register(make_rule("off", enabled=False))
        self.assertIsNotNone(table.find("my_mod:rule:off"))
        self.assertFalse(table.get("my_mod:rule:off").enabled)
        with self.assertRaises(MissingEntryError) as ctx:
            table.require("my_mod:rule:off")
        self.assertIn("禁用", str(ctx.exception))
        self.assertEqual(table.enabled(), ())


if __name__ == "__main__":
    unittest.main()
