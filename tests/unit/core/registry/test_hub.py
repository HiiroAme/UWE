"""hub 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/registry/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 默认持有 §14.2 全部注册表类型；
  - 按条目 type 分派登记、按类型取表与取条目；
  - 未登记类型的报错；
  - summary / total / 遍历顺序。
"""

import unittest

from core.registry import (
    REGISTRY_TYPES,
    RegistryEntry,
    RegistryHub,
    UnknownRegistryTypeError,
)


def make_entry(entry_id: str, type_name: str, data: dict | None = None, **overrides) -> RegistryEntry:
    """造一条指定类型的条目。

    输入：
        entry_id: 完整 id（第二段必须等于 type_name）；
        type_name: 注册表类型名；
        data: 条目负载；
        overrides: 要覆盖的字段。
    输出：
        构造好的 RegistryEntry。
    异常：
        无（构造异常由被测用例自己触发）。
    变量：
        fields: 默认字段字典。
    """
    fields = {"id": entry_id, "type": type_name, "data": data if data is not None else {}}
    fields.update(overrides)
    return RegistryEntry(**fields)


class TestHub(unittest.TestCase):
    """注册表中心的基本行为。"""

    def test_default_types_are_the_draft_list(self):
        """默认持有 §14.2 清单里的全部类型，且顺序与清单一致。"""
        hub = RegistryHub()
        self.assertEqual(hub.types, REGISTRY_TYPES)
        for type_name in REGISTRY_TYPES:
            self.assertEqual(hub.table(type_name).type_name, type_name)

    def test_register_dispatches_by_type(self):
        """登记按 entry.type 落到对应的注册表。"""
        hub = RegistryHub()
        rule = make_entry("my_mod:rule:can_move", "rule", {"condition": True})
        action = make_entry("my_mod:action:move", "action", {"steps": []})
        hub.register_all([rule, action])

        self.assertEqual(hub.table("rule").ids(), ("my_mod:rule:can_move",))
        self.assertEqual(hub.table("action").ids(), ("my_mod:action:move",))
        self.assertIs(hub.get("rule", "my_mod:rule:can_move"), rule)
        self.assertIs(hub.require("action", "my_mod:action:move"), action)
        self.assertEqual(hub.total(), 2)
        self.assertEqual(hub.summary(), {"rule": 1, "action": 1})

    def test_unknown_type_rejected(self):
        """不在中心的类型名：建表、登记、取表都报 UnknownRegistryTypeError。"""
        hub = RegistryHub(types=("rule",))
        with self.assertRaises(UnknownRegistryTypeError):
            hub.table("action")
        with self.assertRaises(UnknownRegistryTypeError):
            RegistryHub(types=("custom",)).table("action")

    def test_types_argument_validated(self):
        """types 不能是字符串、不能有空名或重名。"""
        with self.assertRaises(TypeError):
            RegistryHub(types="rule")
        with self.assertRaises(ValueError):
            RegistryHub(types=("rule", "rule"))
        with self.assertRaises(ValueError):
            RegistryHub(types=("",))

    def test_registered_entry_type_must_be_in_hub(self):
        """条目 type 不在中心持有的类型里时，登记被拒绝。"""
        hub = RegistryHub(types=("rule",))
        action = make_entry("my_mod:action:move", "action")
        with self.assertRaises(UnknownRegistryTypeError):
            hub.register(action)


if __name__ == "__main__":
    unittest.main()
