"""shell.texts 的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/shell/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - `ShellTexts.get` 的键查找 / 缺失兜底 / 模板填充；
  - `ShellTexts.load` 的文件缺失、JSON 损坏、正常读取与多根优先级。
"""

import json
import shutil
import unittest
from pathlib import Path

from adapters import LocalFileSystem
from shell.texts import ShellTexts

SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"


class TestShellTexts(unittest.TestCase):
    """外壳文字加载与查询。"""

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

    def test_get_uses_json_and_falls_back(self):
        """有键用文件值，缺键用代码默认值。"""
        texts = ShellTexts({"home.new": "来自文件"})
        self.assertEqual(texts.get("home.new", "默认"), "来自文件")
        self.assertEqual(texts.get("home.load", "默认"), "默认")

    def test_get_formats_fields(self):
        """模板占位符能填；缺字段时保留原模板（不崩）。"""
        texts = ShellTexts({"entry": "{name} v{version}"})
        self.assertEqual(texts.get("entry", "默认", name="演示", version="0.0.0"),
                         "演示 v0.0.0")
        self.assertEqual(texts.get("entry", "默认", name="演示"), "{name} v{version}")

    def test_load_missing_and_broken_json(self):
        """文件缺失 / JSON 损坏都退回空表，由调用方默认值兜底。"""
        files = LocalFileSystem()
        texts = ShellTexts.load(files, roots=(self.folder,))
        self.assertEqual(texts.get("home.new", "兜底"), "兜底")

        folder = self.folder / "texts"
        folder.mkdir()
        path = folder / "zh-CN.json"
        path.write_text("{不是 JSON", encoding="utf-8")
        texts = ShellTexts.load(files, roots=(self.folder,))
        self.assertEqual(texts.get("home.new", "兜底"), "兜底")

        path.write_text(json.dumps({"home.new": "来自文件"}, ensure_ascii=False), encoding="utf-8")
        texts = ShellTexts.load(files, roots=(self.folder,))
        self.assertEqual(texts.get("home.new", "兜底"), "来自文件")

    def test_load_prefers_first_root(self):
        """外部根优先于内置根。"""
        files = LocalFileSystem()
        first = self.folder / "first"
        second = self.folder / "second"
        for base, value in ((first, "外部"), (second, "内置")):
            (base / "texts").mkdir(parents=True)
            (base / "texts" / "zh-CN.json").write_text(
                json.dumps({"home.new": value}, ensure_ascii=False), encoding="utf-8")
        texts = ShellTexts.load(files, roots=(first, second))
        self.assertEqual(texts.get("home.new", "兜底"), "外部")


if __name__ == "__main__":
    unittest.main()
