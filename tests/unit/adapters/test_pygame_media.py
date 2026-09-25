"""PygameMedia 的资源路径解析测试（标准库 unittest）。

位置：tests/unit/adapters/
运行：在仓库根执行 `python run_tests.py`。
覆盖（R6-6）：
  - 相对路径先按 base_dir（这一局的 Mod 目录）解析；
  - base_dir 里没有就回退当前工作目录；
  - 绝对路径照用。

说明：只测路径解析，不真的放声音——没有声卡的机器上 `PygameMedia` 会自动退化成静默，
那部分由适配器自己的容错负责。
"""

import shutil
import unittest
from pathlib import Path

from adapters.pygame_media import PygameMedia

SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"


class TestResolve(unittest.TestCase):
    """`PygameMedia._resolve` 的三种情况。"""

    def test_relative_path_prefers_base_dir_then_falls_back(self):
        """相对路径：先按 Mod 目录找；找不到就按当前工作目录。"""
        folder = SCRATCH_ROOT / "case_media_resolve"
        shutil.rmtree(folder, ignore_errors=True)
        (folder / "assets").mkdir(parents=True, exist_ok=True)
        (folder / "assets" / "hit.wav").write_bytes(b"RIFF")
        try:
            media = PygameMedia(base_dir=str(folder))
            self.assertEqual(media._resolve("assets/hit.wav"), folder / "assets" / "hit.wav")
            self.assertEqual(media._resolve("missing.wav"), Path("missing.wav"))
            absolute = folder / "assets" / "hit.wav"
            self.assertEqual(media._resolve(str(absolute)), absolute)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            try:
                SCRATCH_ROOT.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
