"""R4-9：round 的保留位数越界要给 LogicError（不是 OverflowError）。

位置：tests/unit/core/
运行：在仓库根执行 `python run_tests.py`。
"""

import unittest

from core.logic import LogicError
from core.logic.ops import OPERATORS


class TestRoundGuard(unittest.TestCase):
    """round 的位数守卫。"""

    def test_negative_places_is_logic_error(self):
        """负位数：LogicError（原有口径）。"""
        with self.assertRaises(LogicError):
            OPERATORS["round"].func(1.234, -1)

    def test_too_many_places_is_logic_error_not_overflow(self):
        """位数过大（原来抛 OverflowError）现在也是 LogicError。"""
        with self.assertRaises(LogicError) as ctx:
            OPERATORS["round"].func(1.234, 400)
        self.assertIn("15", str(ctx.exception))

    def test_normal_places_still_work(self):
        """正常位数照旧。"""
        self.assertEqual(OPERATORS["round"].func(1.2345, 2), 1.23)


if __name__ == "__main__":
    unittest.main()
