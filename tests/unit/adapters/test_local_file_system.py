"""LocalFileSystem 的读取口径测试（标准库 unittest）。

位置：tests/unit/adapters/
运行：在仓库根执行 `python run_tests.py`。
覆盖（R6-7）：
  - 普通 UTF-8 文本能读；
  - **带 BOM 的 UTF-8**（记事本 / PowerShell 5.1 常写出来的那种）也能读，且不把 BOM 带进字符串。
"""

import shutil
import unittest
from pathlib import Path

from adapters import LocalFileSystem

SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"


class TestReadText(unittest.TestCase):
    """读文本：UTF-8 与 UTF-8 BOM 都要能读。"""

    def test_reads_utf8_with_and_without_bom(self):
        """R6-7：带 BOM 的 JSON 不能因为一个不可见字符就加载失败。"""
        folder = SCRATCH_ROOT / "case_read_text_bom"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            plain = folder / "plain.json"
            plain.write_text('{"a": 1}', encoding="utf-8")
            bom = folder / "bom.json"
            bom.write_bytes(b"\xef\xbb\xbf" + '{"a": 1}'.encode("utf-8"))

            files = LocalFileSystem()
            self.assertEqual(files.read_text(str(plain)), '{"a": 1}')
            self.assertEqual(files.read_text(str(bom)), '{"a": 1}')   # BOM 被吃掉，不进字符串
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            try:
                SCRATCH_ROOT.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
