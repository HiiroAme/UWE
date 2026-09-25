"""context 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 存取、覆盖、删除、判存在；
  - 名字的类型检查；
  - 值可以是任意对象（不入存档，所以不受"纯数据"约束）；
  - 遍历顺序与 clear。
"""

import unittest

from core.context import Context


class TestContext(unittest.TestCase):
    """Context 的存取语义。"""

    def test_put_get_has_remove(self):
        """写入后能读回；删除后 has 为假。"""
        context = Context()
        context.put("animation.frame", 3)
        self.assertEqual(context.get("animation.frame"), 3)
        self.assertTrue(context.has("animation.frame"))
        context.remove("animation.frame")
        self.assertFalse(context.has("animation.frame"))
        self.assertIsNone(context.get("animation.frame"))

    def test_default_value(self):
        """没有这个名字时返回给定默认值。"""
        self.assertEqual(Context().get("nope", "备用"), "备用")

    def test_none_value_is_distinguishable(self):
        """存进去 None 时，has 仍为真（与"没存过"区分开）。"""
        context = Context()
        context.put("x", None)
        self.assertTrue(context.has("x"))
        self.assertIsNone(context.get("x"))

    def test_values_can_be_arbitrary_objects(self):
        """Context 不入存档，因此可以放任意对象（这里放一个函数）。"""
        context = Context()
        context.put("callback", len)
        self.assertIs(context.get("callback"), len)

    def test_names_keep_insertion_order_and_clear(self):
        """名字按写入顺序返回，clear 之后为空。"""
        context = Context()
        for name in ("b", "a", "c"):
            context.put(name, 1)
        self.assertEqual(context.names(), ("b", "a", "c"))
        self.assertEqual(len(context), 3)
        context.clear()
        self.assertEqual(context.names(), ())

    def test_name_must_be_non_empty_string(self):
        """名字必须是字符串且非空。"""
        context = Context()
        with self.assertRaises(TypeError):
            context.put(1, "x")
        with self.assertRaises(ValueError):
            context.get("")


if __name__ == "__main__":
    unittest.main()
