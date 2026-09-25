"""entry 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/registry/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - RegistryEntry 的字段默认值与只读（frozen）语义；
  - id 结构检查（三段、第二段与 type 一致、不能继承自己）；
  - type 必须在 §14.2 清单里；
  - schema_version / enabled / data / metadata 的类型检查；
  - name 与 display_name 两个便捷属性。
"""

import unittest
from dataclasses import FrozenInstanceError

from core.registry import ENTRY_SCHEMA_VERSION, EntryFormatError, RegistryEntry


def make_entry(**overrides) -> RegistryEntry:
    """造一条字段合法的条目，允许用关键字覆盖单个字段做反例。

    输入：
        overrides: 要覆盖的字段（关键字参数）。
    输出：
        构造好的 RegistryEntry。
    异常：
        无（构造异常由被测用例自己触发）。
    变量：
        fields: 默认字段字典，被 overrides 覆盖后交给 RegistryEntry。
    """
    fields = {
        "id": "my_mod:entity:infantry",
        "type": "entity",
        "data": {"hp": 10},
        "metadata": {"name": "步兵"},
        "extends": None,
        "enabled": True,
        "source": "my_mod:data/entities.json",
        "schema_version": ENTRY_SCHEMA_VERSION,
    }
    fields.update(overrides)
    return RegistryEntry(**fields)


class TestDefaults(unittest.TestCase):
    """默认值：metadata / extends / enabled / source / schema_version。"""

    def test_defaults_are_filled(self):
        """只给必填三项时，其余字段自动填默认值。"""
        entry = RegistryEntry(id="my_mod:rule:can_move", type="rule", data={})
        self.assertEqual(entry.metadata, {})
        self.assertIsNone(entry.extends)
        self.assertTrue(entry.enabled)
        self.assertEqual(entry.source, "")
        self.assertEqual(entry.schema_version, ENTRY_SCHEMA_VERSION)

    def test_entry_is_frozen(self):
        """条目字段不可重新赋值（frozen）——防止运行期被随手改掉。"""
        entry = make_entry()
        with self.assertRaises(FrozenInstanceError):
            entry.data = {}


class TestIdRules(unittest.TestCase):
    """id 的结构检查。"""

    def test_bad_id_shape(self):
        """id 不是三段、有空段都报 EntryFormatError。"""
        for bad_id in ("", "my_mod", "my_mod:entity", ":entity:x", "my_mod::x", "my_mod:entity:"):
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(EntryFormatError):
                    make_entry(id=bad_id)

    def test_type_segment_must_match_type_field(self):
        """id 的第二段必须与 type 字段一致，避免"看 id 猜错表"。"""
        with self.assertRaises(EntryFormatError):
            make_entry(id="my_mod:action:infantry", type="entity")

    def test_unknown_registry_type_rejected(self):
        """清单之外的注册表类型直接拒绝。"""
        with self.assertRaises(EntryFormatError):
            make_entry(id="my_mod:weapon:gun", type="weapon")

    def test_extends_self_rejected(self):
        """条目不能继承自己。"""
        with self.assertRaises(EntryFormatError):
            make_entry(extends="my_mod:entity:infantry")


class TestFieldTypes(unittest.TestCase):
    """字段类型检查。"""

    def test_data_must_be_dict(self):
        """data 必须是 dict（§14.1 必填字段）。"""
        with self.assertRaises(EntryFormatError):
            make_entry(data=[1, 2])

    def test_metadata_must_be_dict(self):
        """metadata 必须是 dict。"""
        with self.assertRaises(EntryFormatError):
            make_entry(metadata="步兵")

    def test_enabled_must_be_bool(self):
        """enabled 必须是布尔值，数字不算。"""
        with self.assertRaises(EntryFormatError):
            make_entry(enabled=1)

    def test_schema_version_must_be_positive_int(self):
        """schema_version 必须是正整数，bool 与 0 都不行。"""
        for bad in (0, -1, True, "1"):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, EntryFormatError)):
                    make_entry(schema_version=bad)

    def test_extends_must_be_str_or_none(self):
        """extends 只能是字符串或 None。"""
        with self.assertRaises(EntryFormatError):
            make_entry(extends=123)


class TestDisplayHelpers(unittest.TestCase):
    """name 与 display_name。"""

    def test_name_is_last_segment(self):
        """name 取 id 的最后一段。"""
        self.assertEqual(make_entry().name, "infantry")

    def test_display_name_prefers_metadata(self):
        """display_name 优先用 metadata["name"]，没有就退回 name。"""
        self.assertEqual(make_entry().display_name, "步兵")
        self.assertEqual(make_entry(metadata={}).display_name, "infantry")
        self.assertEqual(make_entry(metadata={"name": ""}).display_name, "infantry")


if __name__ == "__main__":
    unittest.main()
