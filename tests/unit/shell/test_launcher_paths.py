"""启动器路径的单元测试：源码运行与 PyInstaller 打包两种布局。

位置：tests/unit/shell/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - `main._app_root(frozen=False)` = 仓库根（源码运行，行为与改动前一致）；
  - `main._app_root(frozen=True, executable=...)` = exe 所在文件夹（打包运行）；
  - 不传参数时读 `sys.frozen` / `sys.executable`（模拟 PyInstaller 进程）。

打包后的真实布局（one-folder）：
    UWE/UWE.exe + UWE/mods + UWE/modules + UWE/saves + UWE/logs
这里只测"根目录怎么算"，不需要真的打包。
"""

import sys
import unittest
from pathlib import Path

# 仓库根：tests/unit/shell/ → 往上三层。
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import main as launcher  # noqa: E402


class TestAppRoot(unittest.TestCase):
    """`main._app_root` 与它派生出来的目录。"""

    def test_source_layout_is_repo_root(self):
        """源码运行：程序根目录就是仓库根，mods / modules 与现在一致。"""
        self.assertEqual(launcher._app_root(frozen=False), REPO_ROOT)
        self.assertEqual(launcher.MODS_ROOT, REPO_ROOT / "mods")
        self.assertEqual(launcher.MODULES_ROOT, REPO_ROOT / "modules")

    def test_frozen_layout_is_the_exe_folder(self):
        """打包运行：程序根目录 = exe 所在文件夹，mods / modules / saves 跟着它走。"""
        root = launcher._app_root(frozen=True, executable="C:/games/UWE/UWE.exe")
        self.assertEqual(root, Path("C:/games/UWE"))
        self.assertEqual(root / "mods", Path("C:/games/UWE/mods"))
        self.assertEqual(root / "saves", Path("C:/games/UWE/saves"))

    def test_frozen_without_arguments_reads_sys(self):
        """不传参数时读 `sys.frozen` / `sys.executable`（PyInstaller 的真实运行方式）。"""
        original_executable = sys.executable
        try:
            sys.frozen = True  # type: ignore[attr-defined]
            sys.executable = "C:/games/UWE/UWE.exe"
            self.assertEqual(launcher._app_root(), Path("C:/games/UWE"))
        finally:
            del sys.frozen  # type: ignore[attr-defined]
            sys.executable = original_executable


if __name__ == "__main__":
    unittest.main()
