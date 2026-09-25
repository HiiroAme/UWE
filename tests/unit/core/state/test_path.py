"""path 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/state/
运行：在仓库根执行 `python run_tests.py`（它负责把 engine/src 加入 import 路径）。
覆盖：草案 §6.3 要求的 JSON Pointer 语法、转义、以及 parse / format_path 的往返一致。
"""

import unittest

from core.state.errors import PathSyntaxError
from core.state.path import escape_token, format_path, parse, unescape_token


class TestParse(unittest.TestCase):
    """parse：路径字符串 → 路径段元组。"""

    def test_root_is_empty_tuple(self):
        """空字符串表示整棵树的根。"""
        self.assertEqual(parse(""), ())

    def test_simple_path(self):
        """普通多段路径按 "/" 切分。"""
        self.assertEqual(parse("/a/b/c"), ("a", "b", "c"))

    def test_single_slash_means_empty_key(self):
        """RFC 6901："/" 表示根下键名为空字符串的那个键。"""
        self.assertEqual(parse("/"), ("",))

    def test_trailing_slash_keeps_empty_last_token(self):
        """末尾的 "/" 表示最后一段是空字符串键，不能被丢掉。"""
        self.assertEqual(parse("/a/"), ("a", ""))

    def test_escapes_are_decoded(self):
        """"~0" 还原为 "~"，"~1" 还原为 "/"。"""
        self.assertEqual(parse("/~0/~1"), ("~", "/"))

    def test_escape_order_is_not_double_decoded(self):
        """"~01" 必须还原成 "~1"（先还原 "~0"，后面的 "1" 是普通字符）。"""
        self.assertEqual(parse("/~01"), ("~1",))

    def test_invalid_escape_raises(self):
        """非法转义与结尾单独的 "~" 都报 PathSyntaxError。"""
        with self.assertRaises(PathSyntaxError):
            parse("/~2")
        with self.assertRaises(PathSyntaxError):
            parse("/~")

    def test_error_carries_full_path(self):
        """异常必须携带完整路径，供日志按路径溯源（§19.1）。"""
        with self.assertRaises(PathSyntaxError) as ctx:
            parse("/foo/~2/bar")
        self.assertEqual(ctx.exception.path, "/foo/~2/bar")
        self.assertIn("~2", ctx.exception.detail)

    def test_path_must_start_with_slash(self):
        """非空路径必须以 "/" 开头。"""
        with self.assertRaises(PathSyntaxError):
            parse("a/b")

    def test_non_string_raises_type_error(self):
        """路径必须是字符串，否则是调用方的编程错误。"""
        with self.assertRaises(TypeError):
            parse(123)


class TestEscape(unittest.TestCase):
    """escape_token / unescape_token：单段的转义与还原。"""

    def test_escape(self):
        """转义规则："~" → "~0"，"/" → "~1"，且不产生二次转义。"""
        self.assertEqual(escape_token("~"), "~0")
        self.assertEqual(escape_token("/"), "~1")
        self.assertEqual(escape_token("~1"), "~01")
        self.assertEqual(escape_token("plain"), "plain")

    def test_unescape_is_inverse_of_escape(self):
        """还原必须是转义的逆运算，覆盖含特殊字符的段。"""
        for token in ("plain", "~", "/", "~1", "/a/b", "a~b"):
            self.assertEqual(unescape_token(escape_token(token)), token)

    def test_unescape_rejects_bad_input(self):
        """非法转义要报错，不能悄悄放过去。"""
        with self.assertRaises(PathSyntaxError):
            unescape_token("~9")

    def test_unescape_error_path_is_the_token(self):
        """unescape_token 只认识单个段，异常的 path 字段就是该段文本本身。"""
        with self.assertRaises(PathSyntaxError) as ctx:
            unescape_token("~2")
        self.assertEqual(ctx.exception.path, "~2")


class TestFormat(unittest.TestCase):
    """format_path：段元组 → 路径字符串。"""

    def test_empty_segments_is_root(self):
        """空序列表示根，输出空字符串。"""
        self.assertEqual(format_path(()), "")

    def test_segments_are_escaped_and_joined(self):
        """各段先转义再用 "/" 连接，空段保留成连续斜杠。"""
        self.assertEqual(format_path(("a", "", "b")), "/a//b")
        self.assertEqual(format_path(("~", "/")), "/~0/~1")

    def test_round_trip(self):
        """parse(format_path(x)) 必须等于 x。"""
        for segments in ((), ("a",), ("a", "b"), ("",), ("~", "/"), ("~1", "~0")):
            self.assertEqual(parse(format_path(segments)), segments)

    def test_non_string_segment_raises_type_error(self):
        """段必须是字符串，否则是调用方的编程错误。"""
        with self.assertRaises(TypeError):
            format_path(("a", 1))


if __name__ == "__main__":
    unittest.main()
