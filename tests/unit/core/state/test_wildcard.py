"""state.wildcard 的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/state/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 是否含通配、写法校验（捕获名、重复名字、括号不配对）；
  - 展开：字典按键排序、列表按下标升序、`*` 不捕获、`{名字}` 捕获；
  - 匹配与"两条路径可能重合"的判断（派生值查重用）；
  - 用捕获值还原具体路径。
"""

import unittest

from core.state import (
    PathSyntaxError,
    expand,
    is_pattern,
    match,
    patterns_overlap,
    substitute,
    validate_pattern,
)


def make_state() -> dict:
    """造一棵用于通配测试的树。"""
    return {
        "units": {
            "u2": {"hp": 5, "power": 10},
            "u1": {"hp": 3, "power": 6},
        },
        "nodes": [{"cover": 0}, {"cover": 2}],
        "turn": 1,
    }


class TestPatternBasics(unittest.TestCase):
    """是不是通配、写法对不对。"""

    def test_is_pattern(self):
        """含 `*` 或 `{名字}` 段才算通配。"""
        self.assertTrue(is_pattern("/units/*/power"))
        self.assertTrue(is_pattern("/units/{unit}/power"))
        self.assertFalse(is_pattern("/units/u1/power"))

    def test_validate_rejects_bad_capture(self):
        """捕获段写错要当场报错。"""
        for bad in ("/units/{}/power", "/units/{unit/power", "/units/unit}/power"):
            with self.subTest(bad=bad):
                with self.assertRaises(PathSyntaxError):
                    validate_pattern(bad)

    def test_validate_rejects_repeated_name(self):
        """同一个捕获名出现两次没法保证两次拿到同一个值，直接拒绝。"""
        with self.assertRaises(PathSyntaxError):
            validate_pattern("/a/{x}/b/{x}")

    def test_validate_accepts_plain_path(self):
        """普通路径也能过校验（调用方不必先判断）。"""
        validate_pattern("/units/u1/power")


class TestExpand(unittest.TestCase):
    """展开。"""

    def test_expand_sorted_and_captured(self):
        """字典按键排序、捕获名字、给出具体路径。"""
        found = expand(make_state(), "/units/{unit}/power")
        self.assertEqual([item.path for item in found], ["/units/u1/power", "/units/u2/power"])
        self.assertEqual([item.bindings for item in found], [{"unit": "u1"}, {"unit": "u2"}])

    def test_expand_list_indices_are_ints(self):
        """列表下标按升序展开，捕获值是整数（方便算术）。"""
        found = expand(make_state(), "/nodes/{i}/cover")
        self.assertEqual([item.path for item in found], ["/nodes/0/cover", "/nodes/1/cover"])
        self.assertEqual([item.bindings["i"] for item in found], [0, 1])

    def test_star_does_not_capture(self):
        """`*` 只是"任意一层"，不产生捕获。"""
        found = expand(make_state(), "/units/*/hp")
        self.assertEqual([item.path for item in found], ["/units/u1/hp", "/units/u2/hp"])
        self.assertEqual([item.bindings for item in found], [{}, {}])

    def test_plain_path_expands_to_itself_or_nothing(self):
        """普通路径：存在就给一条匹配，不存在就给空。"""
        self.assertEqual([m.path for m in expand(make_state(), "/turn")], ["/turn"])
        self.assertEqual(expand(make_state(), "/nope"), ())

    def test_missing_layer_gives_nothing(self):
        """中间层不存在时展开为空（不报错）。"""
        self.assertEqual(expand(make_state(), "/units/{u}/nope/{x}"), ())


class TestMatchAndOverlap(unittest.TestCase):
    """匹配与重合判断（派生值查重用）。"""

    def test_match(self):
        """匹配给出捕获；不匹配给 None。"""
        self.assertEqual(match("/units/{u}/power", "/units/u2/power"), {"u": "u2"})
        self.assertIsNone(match("/units/{u}/power", "/units/u2/hp"))
        self.assertIsNone(match("/units/{u}/power", "/units/u2/power/extra"))

    def test_overlap(self):
        """可能重合的组合判为 True，明显不重合的判为 False。"""
        self.assertTrue(patterns_overlap("/units/{u}/power", "/units/*/power"))
        self.assertTrue(patterns_overlap("/units/u1/hp", "/units/{u}/hp"))
        self.assertFalse(patterns_overlap("/units/{u}/power", "/units/*/hp"))
        self.assertFalse(patterns_overlap("/units/{u}/power", "/nodes/{n}/power"))

    def test_substitute(self):
        """用捕获值还原具体路径。"""
        self.assertEqual(substitute("/units/{u}/power", {"u": "u1"}), "/units/u1/power")
        with self.assertRaises(KeyError):
            substitute("/units/{u}/power", {})
        with self.assertRaises(PathSyntaxError):
            substitute("/units/*/power", {"u": "u1"})


if __name__ == "__main__":
    unittest.main()
