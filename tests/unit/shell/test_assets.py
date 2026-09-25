"""shell.resources 的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/shell/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 逻辑资源名只能解析到资源根之下，拒绝绝对路径与 `..`；
  - `first()` 按候选顺序取第一个存在的文件；
  - 品牌资源 `brand_asset` / `brand_icon` 的路径规则与 ico→png 优先顺序。
"""

import shutil
import unittest
from pathlib import Path

from shell.resources import (
    AssetResolver,
    brand_asset,
    brand_icon,
    brand_icon_candidates,
)

SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"


class TestAssetResolver(unittest.TestCase):
    """资源路径解析。"""

    def setUp(self):
        """每个用例一个干净的临时资源根。"""
        self.folder = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(self.folder, ignore_errors=True)
        self.folder.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """清掉临时目录。"""
        shutil.rmtree(self.folder, ignore_errors=True)
        try:
            SCRATCH_ROOT.rmdir()
        except OSError:
            pass

    def test_path_resolves_under_root_and_rejects_escape(self):
        """逻辑名解析到根下；绝对路径 / .. / 空名字都不允许。"""
        resolver = AssetResolver(self.folder)
        self.assertEqual(resolver.path("brand/icon.ico"),
                         self.folder / "brand" / "icon.ico")
        with self.assertRaises(ValueError):
            resolver.path("../escape")
        with self.assertRaises(ValueError):
            resolver.path("")
        with self.assertRaises(TypeError):
            resolver.path(123)

    def test_first_prefers_candidate_order(self):
        """按候选顺序取第一个存在的文件；都不存在返回 None。"""
        (self.folder / "brand").mkdir()
        png = self.folder / "brand" / "icon.png"
        ico = self.folder / "brand" / "icon.ico"
        png.write_bytes(b"png")
        ico.write_bytes(b"ico")
        resolver = AssetResolver(self.folder)
        self.assertEqual(resolver.first(("brand/icon.ico", "brand/icon.png")), ico)
        self.assertEqual(resolver.first(("brand/missing.png", "brand/icon.png")), png)
        self.assertIsNone(resolver.first(("brand/missing.png",)))

    def test_brand_asset_and_icon(self):
        """品牌资源固定在 brand/ 下；图标优先 ico，再退到 png。"""
        (self.folder / "brand").mkdir()
        self.assertEqual(brand_asset("logo.png", root=self.folder),
                         self.folder / "brand" / "logo.png")
        self.assertIsNone(brand_icon(root=self.folder))

        png = self.folder / "brand" / "icon.png"
        png.write_bytes(b"png")
        self.assertEqual(brand_icon(root=self.folder), png)
        self.assertEqual(brand_icon_candidates(root=self.folder), (png,))

        ico = self.folder / "brand" / "icon.ico"
        ico.write_bytes(b"ico")
        self.assertEqual(brand_icon(root=self.folder), ico)
        self.assertEqual(brand_icon_candidates(root=self.folder), (ico, png))


if __name__ == "__main__":
    unittest.main()
