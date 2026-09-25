"""R4-13：脚本加载器 clear() 之后不留旧模块对象。

位置：tests/unit/adapters/
运行：在仓库根执行 `python run_tests.py`。
"""

import sys
import unittest
from pathlib import Path

from adapters import PythonScriptLoader

SCRATCH = Path(__file__).resolve().parents[2] / "_scratch"


class TestClearDropsModules(unittest.TestCase):
    """clear() 也要把 sys.modules 里的脚本模块名弹掉（热重载用）。"""

    def test_clear_pops_registered_module_names(self):
        """加载一个脚本 → sys.modules 多一条；clear() 之后那条要消失。"""
        folder = SCRATCH / "case_python_scripts"
        folder.mkdir(parents=True, exist_ok=True)
        script = folder / "demo_script.py"
        script.write_text('def run():\n    """空的。"""\n    return 1\n', encoding="utf-8")
        try:
            loader = PythonScriptLoader()
            loader.load(str(script), "run")
            loaded = [name for name in sys.modules if name.startswith("mod_script_")]
            self.assertTrue(loaded)
            loader.clear()
            self.assertEqual([name for name in loaded if name in sys.modules], [])
        finally:
            script.unlink(missing_ok=True)
            try:
                folder.rmdir()
                SCRATCH.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
