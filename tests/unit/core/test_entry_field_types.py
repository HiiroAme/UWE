"""R5-13：条目字段类型错要给 EntryFormatError（不是裸 TypeError）。

位置：tests/unit/core/
运行：在仓库根执行 `python run_tests.py`。
"""

import unittest

from core.registry import EntryFormatError, RegistryEntry


class TestEntryFieldTypes(unittest.TestCase):
    """字段类型不对时，异常类型与本层的口径一致。"""

    def test_data_must_be_dict_raises_entry_format_error(self):
        """data 传字符串：EntryFormatError（以前是裸 TypeError）。"""
        with self.assertRaises(EntryFormatError):
            RegistryEntry(id="demo:event:e", type="event", data="nope")


if __name__ == "__main__":
    unittest.main()
