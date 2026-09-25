"""snapshot_value 的单元测试（评估报告 5 原则审查 #2：工具落在 state 层）。

位置：tests/unit/core/state/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 容器逐层深拷贝，不共享结构；
  - 标量原样返回，不产生额外副本。
"""

import unittest

from core.state import snapshot_value


class TestSnapshotValue(unittest.TestCase):
    """写入 State 前的独占副本。"""

    def test_container_is_deep_copied(self):
        """容器逐层拷贝：改副本不影响原件。"""
        original = {"gear": [1, {"name": "步枪"}]}
        copied = snapshot_value(original)
        self.assertEqual(copied, original)
        self.assertIsNot(copied, original)
        self.assertIsNot(copied["gear"], original["gear"])
        self.assertIsNot(copied["gear"][1], original["gear"][1])
        copied["gear"][1]["name"] = "改了"
        self.assertEqual(original["gear"][1]["name"], "步枪")

    def test_scalar_is_returned_as_is(self):
        """标量（数值 / 字符串 / None / bool）原样返回同一个对象。"""
        for value in (1, 1.5, "洛川", None, True):
            with self.subTest(value=value):
                self.assertIs(snapshot_value(value), value)


if __name__ == "__main__":
    unittest.main()
