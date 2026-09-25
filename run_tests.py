"""仓库测试入口（标准库 unittest，无第三方依赖）。

位置：
    仓库根目录。
职责：
    1. 把 engine/src 加入 import 路径，让测试可以 `from core.state import ...`。
       当前 engine/src 还不是可安装的包，本文件是唯一的路径引导点；
    2. 递归收集 tests/ 下所有 test_*.py 并执行。

用法（在仓库根执行）：
    python run_tests.py

说明：
    将来目录迁移到 src/ 布局并支持 `pip install -e .` 之后，本文件可以删除，
    改用 pytest 收集测试；现有这些 unittest.TestCase 用例不需要改写。
"""

import importlib.util
import sys
import unittest
from pathlib import Path

# 仓库根目录（本文件所在目录）。
REPO_ROOT = Path(__file__).resolve().parent
# 引擎源码根目录：当前布局下它是 engine/src，core 包就在它下面。
SOURCE_ROOT = REPO_ROOT / "engine" / "src"
# 测试目录：递归收集其中的 test_*.py。
TESTS_ROOT = REPO_ROOT / "tests"


def _load_suite_from_file(test_file: Path) -> unittest.TestSuite:
    """按文件路径加载一个测试模块，避免依赖 __init__.py 或包布局。

    输入：
        test_file: 测试文件路径（Path 对象）。
    输出：
        该文件里全部用例组成的 TestSuite。
    变量：
        relative: 测试文件相对仓库根的路径（去掉后缀），用来生成模块名；
        module_name: 点分模块名，仅用于注册到 sys.modules，保证唯一即可；
        spec: importlib 的模块规格对象；
        module: 执行后的模块对象。
    """
    relative = test_file.relative_to(REPO_ROOT).with_suffix("")
    module_name = ".".join(relative.parts)
    spec = importlib.util.spec_from_file_location(module_name, test_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载测试模块：{test_file}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return unittest.defaultTestLoader.loadTestsFromModule(module)


def build_suite() -> unittest.TestSuite:
    """收集 tests/ 下所有测试文件。

    输入：无。
    输出：汇总后的 TestSuite。
    变量：
        suite: 顶层测试套件；
        test_file: 当前遍历到的测试文件路径。
    """
    suite = unittest.TestSuite()
    for test_file in sorted(TESTS_ROOT.rglob("test_*.py")):
        suite.addTests(_load_suite_from_file(test_file))
    return suite


def main() -> int:
    """执行全部测试并返回进程退出码。

    输入：无。
    输出：0 表示全部通过；1 表示存在失败或错误。
    变量：
        result: unittest 的运行结果对象，用来判断是否全部通过。
    """
    sys.path.insert(0, str(SOURCE_ROOT))
    result = unittest.TextTestRunner(verbosity=2).run(build_suite())
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
