"""rng 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 同种子同序列、不同种子不同序列；
  - 状态的导出 / 恢复（存档往返）；
  - 取值范围的检查与错误处理；
  - functions() 提供的表达式函数表；
  - 状态是纯 JSON 数据（可直接 json.dumps）。
"""

import json
import unittest

from core.rng import Rng, RngError


class TestDeterminism(unittest.TestCase):
    """确定性与状态往返。"""

    def test_same_seed_same_sequence(self):
        """同一种子得到同一条随机序列。"""
        first = Rng(2026)
        second = Rng(2026)
        self.assertEqual([first.next_int(1, 6) for _ in range(10)],
                         [second.next_int(1, 6) for _ in range(10)])

    def test_state_roundtrip(self):
        """导出状态再恢复，后续序列与原对象完全一致。"""
        rng = Rng(7)
        for _ in range(3):
            rng.next_int(0, 100)
        saved = rng.state()
        expected = [rng.next_float() for _ in range(5)]

        restored = Rng(0)
        restored.restore(saved)
        self.assertEqual([restored.next_float() for _ in range(5)], expected)

    def test_state_is_json_serializable(self):
        """状态是纯 JSON 数据：能直接 json.dumps（存档的前提）。"""
        text = json.dumps(Rng(1).state())
        self.assertIn('"version"', text)

    def test_two_instances_do_not_share_state(self):
        """两个实例各自独立，互不影响。"""
        first = Rng(1)
        second = Rng(2)
        first_values = [first.next_int(0, 10) for _ in range(5)]
        self.assertNotEqual(first_values, [second.next_int(0, 10) for _ in range(5)])


class TestValues(unittest.TestCase):
    """取值范围与参数检查。"""

    def test_next_int_stays_in_range(self):
        """next_int 的结果落在闭区间里，取到两端也合法。"""
        rng = Rng(3)
        values = [rng.next_int(2, 4) for _ in range(200)]
        self.assertTrue(all(2 <= value <= 4 for value in values))
        self.assertEqual(set(values), {2, 3, 4})

    def test_next_float_range(self):
        """next_float 落在 [0, 1)。"""
        rng = Rng(4)
        self.assertTrue(all(0.0 <= rng.next_float() < 1.0 for _ in range(200)))

    def test_chance_bounds(self):
        """概率 0 永远 False、概率 1 永远 True。"""
        rng = Rng(5)
        self.assertFalse(any(rng.chance(0.0) for _ in range(50)))
        self.assertTrue(all(rng.chance(1.0) for _ in range(50)))

    def test_invalid_arguments(self):
        """参数不合法时报错（bool 不算数字、区间不能倒置、概率必须在 [0,1]）。"""
        rng = Rng(6)
        with self.assertRaises(TypeError):
            rng.next_int(1.0, 3)
        with self.assertRaises(ValueError):
            rng.next_int(3, 1)
        with self.assertRaises(ValueError):
            rng.chance(1.5)
        with self.assertRaises(ValueError):
            rng.chance(-0.1)
        with self.assertRaises(TypeError):
            rng.chance(True)
        with self.assertRaises(ValueError):
            rng.choice([])
        with self.assertRaises(TypeError):
            rng.choice(5)

    def test_restore_rejects_bad_state(self):
        """坏掉的随机状态被明确拒绝（不猜、不修）。"""
        with self.assertRaises(TypeError):
            Rng(0).restore([1, 2])
        with self.assertRaises(RngError):
            Rng(0).restore({"version": 3})
        with self.assertRaises(RngError):
            Rng(0).restore({"version": 3, "state": "abc", "gauss_next": None})


class TestFunctions(unittest.TestCase):
    """表达式函数表。"""

    def test_functions_are_bound_and_shared(self):
        """functions() 里的函数绑在本对象上：调用它们会推进同一份随机状态。"""
        rng = Rng(11)
        table = rng.functions()
        self.assertEqual(set(table), {"rng.int", "rng.float", "rng.chance"})

        before = rng.state()
        table["rng.int"](1, 6)
        self.assertNotEqual(rng.state(), before)


if __name__ == "__main__":
    unittest.main()
