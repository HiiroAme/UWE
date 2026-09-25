"""R5-4：初始化的引用型写入也要深拷贝（State 要是一棵树）。

位置：tests/unit/core/
运行：在仓库根执行 `python run_tests.py`。

说明：`apply_system_defaults` 收的是**编译后**的 system 条目（值是表达式节点），
所以这里用编译器同一个解析入口 `core.logic.parse_data` 把 JSON 表达式变成节点。
"""

import unittest

from core.content.compiled import CompiledSystem
from core.logic import parse_data
from core.templates import apply_system_defaults


def make_system(values):
    """把一个 system 条目的 {路径: 表达式} 编译成 CompiledSystem（测试用）。"""
    return CompiledSystem(
        system_id="s",
        values={path: parse_data(expression, location=f"test{path}")
                for path, expression in values.items()},
        source="test",
    )


class TestInitWritesAreSnapshots(unittest.TestCase):
    """system 初始值里写 ["get", 容器路径] 时，两条路径不能共享对象。"""

    def test_reference_value_is_copied(self):
        """写完之后 /a 与 /b 是两棵树：改一份不影响另一份。"""
        state = {"a": {"gear": [1, 2]}}
        apply_system_defaults({"s": make_system({"/b": ["get", "/a"]})}, state, functions={})
        self.assertEqual(state["b"], {"gear": [1, 2]})
        self.assertIsNot(state["a"], state["b"])
        self.assertIsNot(state["a"]["gear"], state["b"]["gear"])

    def test_literal_values_still_work(self):
        """字面量写法照旧（别因为深拷贝把正常路径弄坏）。"""
        state = {}
        apply_system_defaults({"s": make_system({"/turn": 1, "/name": "洛川"})}, state, functions={})
        self.assertEqual(state, {"turn": 1, "name": "洛川"})


if __name__ == "__main__":
    unittest.main()
