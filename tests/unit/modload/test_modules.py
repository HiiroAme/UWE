"""模块目录（ModuleCatalog）的单元测试：uses / requires / 版本 / 拓扑序 / 成环 / 基线模块。

位置：tests/unit/modload/
运行：在仓库根执行 `python run_tests.py`。

这一块以前**没有任何测试**（《评估报告 2》的 C-4）：`resolve_uses` 决定"哪些模块会被加载、
按什么顺序加载"，写错了会在加载期报怪错——所以在这里用**内存里的 ModuleInfo** 直接测规则，
不需要真的造模块目录。
"""

import unittest

from modload.modules import ModuleCatalog, version_satisfies
from modload.pack_info import ModuleInfo, PackInfoError


def make_module(module_id, *, version="0.0.0", requires=None, base=False):
    """造一个内存里的 ModuleInfo（folder 只是好看，测试不读文件）。"""
    return ModuleInfo(
        id=module_id, name=module_id, version=version, folder=f"modules/{module_id}",
        requires=dict(requires or {}), base=base, info_path=f"modules/{module_id}/pack_info.json",
    )


class TestVersionSatisfies(unittest.TestCase):
    """版本约束只支持精确版本与 >=x.y.z（未发布期一律 0.0.0）。"""

    def test_constraints(self):
        self.assertTrue(version_satisfies("0.0.0", ""))
        self.assertTrue(version_satisfies("0.0.0", "0.0.0"))
        self.assertTrue(version_satisfies("1.2.3", ">=1.0.0"))
        self.assertFalse(version_satisfies("0.9.0", ">=1.0.0"))
        self.assertFalse(version_satisfies("1.0.0", "2.0.0"))
        with self.assertRaises(PackInfoError):
            version_satisfies("一", ">=1.0.0")


class TestResolveUses(unittest.TestCase):
    """uses / requires 的解析：依赖先加载、版本不满足就拒、缺模块就拒、成环就拒。"""

    def test_dependency_is_loaded_before_the_module_that_needs_it(self):
        catalog = ModuleCatalog({
            "pack_b": make_module("pack_b", requires={"pack_a": ">=0.0.0"}),
            "pack_a": make_module("pack_a"),
        })
        order = [module.id for module in catalog.resolve_uses(["pack_b"])]
        self.assertEqual(order, ["pack_a", "pack_b"])

    def test_base_modules_are_always_included_and_come_first(self):
        catalog = ModuleCatalog({"pack_common": make_module("pack_common", base=True),
                                 "pack_b": make_module("pack_b")})
        order = [module.id for module in catalog.resolve_uses(["pack_b"])]
        self.assertEqual(order, ["pack_common", "pack_b"])

    def test_missing_module_is_rejected(self):
        catalog = ModuleCatalog({"pack_a": make_module("pack_a")})
        with self.assertRaises(PackInfoError):
            catalog.resolve_uses(["pack_不存在"])

    def test_version_constraint_is_enforced(self):
        catalog = ModuleCatalog({"pack_a": make_module("pack_a", version="0.0.0")})
        with self.assertRaises(PackInfoError):
            catalog.resolve_uses(["pack_a>=9.9.9"])

    def test_dependency_cycle_is_rejected(self):
        catalog = ModuleCatalog({
            "pack_a": make_module("pack_a", requires={"pack_b": ">=0.0.0"}),
            "pack_b": make_module("pack_b", requires={"pack_a": ">=0.0.0"}),
        })
        with self.assertRaises(PackInfoError):
            catalog.resolve_uses(["pack_a"])

class TestVersionSegments(unittest.TestCase):
    """R5-10：版本约束多写一段要报错，而不是静默截断。"""

    def test_too_many_segments_is_rejected(self):
        """`>=1.2.3.4` 以前算"满足"，现在报 PackInfoError。"""
        from modload.modules import version_satisfies
        from modload.pack_info import PackInfoError

        with self.assertRaises(PackInfoError):
            version_satisfies("1.2.3", ">=1.2.3.4")

    def test_three_segments_still_work(self):
        """正常的三段写法照旧。"""
        from modload.modules import version_satisfies

        self.assertTrue(version_satisfies("1.2.3", ">=1.2.3"))
        self.assertFalse(version_satisfies("1.2.2", ">=1.2.3"))

if __name__ == "__main__":
    unittest.main()
