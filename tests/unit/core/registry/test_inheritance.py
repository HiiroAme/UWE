"""inheritance 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/registry/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - merge_data 的合并口径（dict 递归、列表整体覆盖）；
  - resolve_entry 的多层继承、metadata 合并、extends 展开后清空；
  - 父条目缺失、类型不一致、继承成环三种失败；
  - resolve_entries 的批内继承、外部查找与重复 id。
"""

import unittest

from core.registry import (
    DuplicateEntryError,
    InheritanceError,
    RegistryEntry,
    merge_data,
    resolve_entries,
    resolve_entry,
)


def make_entry(entry_id: str, data: dict, *, extends=None, metadata=None) -> RegistryEntry:
    """造一条 entity 条目。

    输入：
        entry_id: 完整 id；
        data: 条目负载；
        extends: 父条目 id；
        metadata: 元信息。
    输出：
        构造好的 RegistryEntry。
    异常：
        无。
    变量：
        无。
    """
    return RegistryEntry(
        id=entry_id,
        type="entity",
        data=data,
        extends=extends,
        metadata=metadata or {},
    )


class TestMergeData(unittest.TestCase):
    """merge_data 的合并口径。"""

    def test_dict_merges_recursively(self):
        """两边的 dict 递归合并，同名子键继续往下走。"""
        base = {"combat": {"attack": 5, "defense": 3}, "hp": 10}
        child = {"combat": {"attack": 8}, "move": 2}
        self.assertEqual(
            merge_data(base, child),
            {"combat": {"attack": 8, "defense": 3}, "hp": 10, "move": 2},
        )

    def test_list_is_replaced_not_appended(self):
        """列表不做追加，整体由子条目覆盖。"""
        self.assertEqual(merge_data({"tags": ["a", "b"]}, {"tags": ["c"]}), {"tags": ["c"]})

    def test_inputs_are_not_modified(self):
        """合并不改入参（返回的是新对象）。"""
        base = {"combat": {"attack": 5}}
        child = {"combat": {"attack": 8}}
        merge_data(base, child)
        self.assertEqual(base, {"combat": {"attack": 5}})
        self.assertEqual(child, {"combat": {"attack": 8}})

    def test_result_is_detached(self):
        """结果与入参不共享可变容器：改结果影响不到入参。"""
        base = {"combat": {"attack": 5}}
        merged = merge_data(base, {})
        merged["combat"]["attack"] = 99
        self.assertEqual(base, {"combat": {"attack": 5}})


class TestResolveEntry(unittest.TestCase):
    """单条条目的继承解析。"""

    def test_multi_level_inheritance(self):
        """三层继承：从最顶层父条目往下逐层覆盖。"""
        grandpa = make_entry("my_mod:entity:base", {"hp": 10, "attack": 1})
        parent = make_entry("my_mod:entity:infantry_base", {"attack": 3}, extends="my_mod:entity:base")
        child = make_entry("my_mod:entity:rifle", {"attack": 5}, extends="my_mod:entity:infantry_base")
        lookup = {entry.id: entry for entry in (grandpa, parent, child)}.get

        resolved = resolve_entry(child, lookup)
        self.assertEqual(resolved.data, {"hp": 10, "attack": 5})
        self.assertIsNone(resolved.extends)

    def test_metadata_is_merged_and_child_wins(self):
        """metadata 同样按链合并，子条目覆盖同名键。"""
        parent = make_entry("my_mod:entity:base", {}, metadata={"author": "父", "tags": ["a"]})
        child = make_entry("my_mod:entity:rifle", {}, extends="my_mod:entity:base", metadata={"author": "子"})
        resolved = resolve_entry(child, {parent.id: parent}.get)
        self.assertEqual(resolved.metadata, {"author": "子", "tags": ["a"]})

    def test_missing_parent(self):
        """父条目查不到时明确报错。"""
        child = make_entry("my_mod:entity:rifle", {}, extends="my_mod:entity:nope")
        with self.assertRaises(InheritanceError) as ctx:
            resolve_entry(child, lambda entry_id: None)
        self.assertIn("my_mod:entity:nope", str(ctx.exception))

    def test_type_mismatch(self):
        """父条目类型与子条目不同时报错。"""
        rule = RegistryEntry(id="my_mod:rule:r", type="rule", data={})
        child = make_entry("my_mod:entity:rifle", {}, extends="my_mod:rule:r")
        with self.assertRaises(InheritanceError) as ctx:
            resolve_entry(child, {rule.id: rule}.get)
        self.assertIn("类型", str(ctx.exception))

    def test_cycle_detected(self):
        """继承成环时报错，消息里给出环上的 id。"""
        first = make_entry("my_mod:entity:a", {}, extends="my_mod:entity:b")
        second = make_entry("my_mod:entity:b", {}, extends="my_mod:entity:a")
        lookup = {first.id: first, second.id: second}.get
        with self.assertRaises(InheritanceError) as ctx:
            resolve_entry(first, lookup)
        self.assertIn("成环", str(ctx.exception))

    def test_top_entry_without_extends_is_returned_as_copy(self):
        """没有 extends 的条目也能解析（得到一份等值的新条目）。"""
        entry = make_entry("my_mod:entity:solo", {"hp": 1})
        resolved = resolve_entry(entry, lambda entry_id: None)
        self.assertEqual(resolved.data, entry.data)
        self.assertIsNone(resolved.extends)


class TestResolveEntries(unittest.TestCase):
    """批量解析。"""

    def test_batch_inheritance_and_external_lookup(self):
        """批内可以互相继承，父条目也可以来自外部（已登记的条目）。"""
        builtin = make_entry("builtin:entity:base_unit", {"hp": 10})
        mid = make_entry("my_mod:entity:infantry", {"attack": 2}, extends="builtin:entity:base_unit")
        top = make_entry("my_mod:entity:rifle", {"attack": 4}, extends="my_mod:entity:infantry")

        resolved = resolve_entries([top, mid], external_lookup={builtin.id: builtin}.get)
        by_id = {entry.id: entry for entry in resolved}
        self.assertEqual(by_id["my_mod:entity:rifle"].data, {"hp": 10, "attack": 4})
        self.assertEqual(by_id["my_mod:entity:infantry"].data, {"hp": 10, "attack": 2})

    def test_duplicate_id_in_batch(self):
        """同一批里出现重复 id 直接拒绝。"""
        first = make_entry("my_mod:entity:a", {})
        second = make_entry("my_mod:entity:a", {"hp": 1})
        with self.assertRaises(DuplicateEntryError):
            resolve_entries([first, second])


if __name__ == "__main__":
    unittest.main()
