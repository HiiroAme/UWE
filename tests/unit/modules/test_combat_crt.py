"""pack_combat_crt 模块的单元测试（标准库 unittest）。"""

import unittest
from pathlib import Path

from adapters import PythonScriptLoader

REPO_ROOT = Path(__file__).resolve().parents[3]
CRT_SCRIPT = REPO_ROOT / "modules" / "pack_combat_crt" / "scripts" / "crt.py"


def crt_function(name: str):
    """取 CRT 模块里的一个函数（走脚本端口）。"""
    return PythonScriptLoader().load(str(CRT_SCRIPT), name)


class TestBand(unittest.TestCase):
    """档位换算：不整除按对守方有利取整，不足最低档不许打。"""

    def test_band_rounds_down_in_favor_of_defender(self):
        band_of = crt_function("band_of")
        self.assertEqual(band_of(12, 5), "2:1")     # 2.4 → 2:1（不是 3:1）
        self.assertEqual(band_of(7, 4), "1:1")      # 1.75 → 1:1
        self.assertEqual(band_of(4, 4), "1:1")
        self.assertEqual(band_of(16, 4), "4:1+")    # 4.0 及以上都按最高档
        self.assertEqual(band_of(40, 4), "4:1+")

    def test_below_lowest_band_is_rejected(self):
        band_of = crt_function("band_of")
        self.assertEqual(band_of(1, 4), "")         # 0.25 < 1:3：打不了
        with self.assertRaises(ValueError):
            band_of(2, 0)


class TestResolve(unittest.TestCase):
    """查表与效果翻译。"""

    def test_resolve_uses_the_table(self):
        resolve = crt_function("resolve")
        self.assertEqual(resolve(12, 5, 1), "A1")   # 2:1，骰 1
        self.assertEqual(resolve(12, 5, 6), "D3")   # 2:1，骰 6
        self.assertEqual(resolve(16, 4, 3), "DE")   # 4:1+ 必 DE
        self.assertEqual(resolve(4, 4, 3), "O")     # 1:1，骰 3 → 白打
        self.assertEqual(resolve(1, 4, 1), "")      # 不足最低档

    def test_resolve_checks_roll(self):
        resolve = crt_function("resolve")
        for bad in (0, 7, True, 2.0):
            with self.subTest(roll=bad):
                with self.assertRaises(ValueError):
                    resolve(4, 4, bad)

    def test_result_effects(self):
        effects = crt_function("result_effects")
        self.assertEqual(effects("DE")["eliminated"], "defender")
        self.assertEqual(effects("AE")["eliminated"], "attacker")
        self.assertEqual(effects("D3"), {"eliminated": "", "retreat": 3, "side": "defender"})
        self.assertEqual(effects("A1"), {"eliminated": "", "retreat": 1, "side": "attacker"})
        self.assertEqual(effects("O"), {"eliminated": "", "retreat": 0, "side": ""})
        self.assertEqual(effects(""), {"eliminated": "", "retreat": 0, "side": ""})
        with self.assertRaises(ValueError):
            effects("ZZ")

    def test_table_exposes_a_copy(self):
        """界面用的表结构：档位/骰点都在；返回值是副本，改它不污染模块数据。"""
        table = crt_function("table")()
        self.assertEqual(table["bands"], ["1:3", "1:2", "1:1", "2:1", "3:1", "4:1+"])
        self.assertEqual(table["rows"][0]["roll"], 1)
        self.assertEqual(table["rows"][0]["results"]["1:3"], "AE")
        table["rows"][0]["results"]["1:3"] = "XX"
        self.assertEqual(crt_function("table")()["rows"][0]["results"]["1:3"], "AE")


if __name__ == "__main__":
    unittest.main()
