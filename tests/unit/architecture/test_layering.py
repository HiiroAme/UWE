"""分层与"没有硬编码"的守卫测试（P1 / P3 / P7）。

位置：tests/unit/architecture/
运行：在仓库根执行 `python run_tests.py`。
覆盖（用 AST 解析源码，不靠肉眼）：
  - 核心逻辑层（engine/src/core）**不许** import 平台库、适配器、加载层、外壳、几何；
  - 核心逻辑层**不许**自己读写文件（不出现 open / os / pathlib / importlib）；
  - 核心逻辑层**不许**出现演示 Mod 的名字（P1：引擎不认识任何玩法）；
  - pygame 只许出现在 `adapters/pygame_*.py` 里；
  - engine/src 里不许 import 测试代码；
  - core 内部模块之间不许成环，也不许用非顶层 import 绕环（评估报告 5 原则审查 #1）。

这些是"架构会被改坏"的地方：写起来很容易，坏起来不容易被发现，所以用测试钉住。
"""

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENGINE_SOURCE = REPO_ROOT / "engine" / "src"
CORE_ROOT = ENGINE_SOURCE / "core"


def iter_python_files(folder: Path):
    """遍历文件夹里的 .py（跳过 __pycache__）。

    输入：
        folder: 要遍历的目录。
    输出：
        逐个给出源码文件路径。
    异常：
        无。
    变量：
        path: 当前文件。
    """
    for path in sorted(folder.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def imported_modules(path: Path) -> set:
    """取一个文件里 import 的全部模块名。

    输入：
        path: 源码文件。
    输出：
        模块名字符串集合，例如 {"pygame", "core.state", "adapters"}。
    异常：
        SyntaxError: 源码写坏了（测试会直接报出来）。
    变量：
        tree / node / alias: 语法树与遍历到的节点。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.add(node.module)
    return found


def module_name(path: Path) -> str:
    """把 core 下的源码路径换成绝对模块名。

    输入：
        path: core 目录里的一个 .py 文件。
    输出：
        如 `core.pipeline.engine_api` / `core.pipeline`（__init__.py）/ `core.version`。
    异常：
        无。
    变量：
        parts: 去掉后缀后的路径段；`__init__` 表示包本身。
    """
    parts = list(path.relative_to(CORE_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return "core" + "".join(f".{part}" for part in parts)


def resolve_import(path: Path, node: ast.AST) -> list[str]:
    """把一条 import 解析成它真正指向的绝对模块名。

    输入：
        path: 出现这条 import 的文件；
        node: ast.Import 或 ast.ImportFrom。
    输出：
        绝对模块名列表（相对 import 按所在包展开；解析不了的返回空列表）。
    异常：
        无。
    变量：
        package: 当前文件所在包（普通模块去掉最后一段，__init__.py 保留）。
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    package = module_name(path).split(".")
    if path.name != "__init__.py":
        package = package[:-1]
    if node.level == 0:
        return [node.module] if node.module else []
    if node.level > len(package):
        return []
    parts = package[: len(package) - node.level + 1]
    if node.module:
        parts = parts + node.module.split(".")
    return [".".join(parts)]


class TestCoreIsolation(unittest.TestCase):
    """核心逻辑层的隔离性。"""

    def test_core_does_not_import_outside_world(self):
        """core 不许 import 平台库 / 适配器 / 加载层 / 外壳 / 几何 / 测试。"""
        forbidden = ("pygame", "adapters", "modload", "shell", "geometry", "tests")
        offenders = []
        for path in iter_python_files(CORE_ROOT):
            for module in imported_modules(path):
                if module.split(".")[0] in forbidden:
                    offenders.append(f"{path.name}: {module}")
        self.assertEqual(offenders, [], f"核心逻辑层出现了不该有的依赖：{offenders}")

    def test_core_does_not_touch_the_file_system(self):
        """core 不许自己读写文件、不许用 os / pathlib / importlib（都走端口）。

        说明：
            用 AST 找**真正的调用**（`open(...)`、`os.path...`、`pathlib` 的用法），
            而不是在正文里搜字符串——文档里写"本模块不直接 open() 文件"是完全正常的。
        """
        forbidden_names = {"open", "Path"}
        forbidden_modules = {"os", "pathlib", "importlib", "shutil", "io"}
        offenders = []
        for path in iter_python_files(CORE_ROOT):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in forbidden_names:
                        offenders.append(f"{path.name}: 调用了 {node.func.id}()")
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    if node.value.id in forbidden_modules:
                        offenders.append(f"{path.name}: 用了 {node.value.id}.{node.attr}")
        self.assertEqual(offenders, [], f"核心逻辑层直接碰了文件系统 / 平台：{offenders}")

    def test_core_does_not_know_any_mod(self):
        """core 里不许出现演示 Mod 的名字（P1：引擎不认识任何玩法）。"""
        offenders = []
        for path in iter_python_files(CORE_ROOT):
            if "demo_hex" in path.read_text(encoding="utf-8"):
                offenders.append(path.name)
        self.assertEqual(offenders, [], f"核心逻辑层出现了演示 Mod 的名字：{offenders}")


class TestPlatformIsolation(unittest.TestCase):
    """平台库的落点。"""

    def test_pygame_only_in_pygame_adapters(self):
        """pygame 只许出现在 adapters/pygame_*.py 里。"""
        offenders = []
        for path in iter_python_files(ENGINE_SOURCE):
            if any("pygame" in module for module in imported_modules(path)):
                if path.parent.name != "adapters" or not path.name.startswith("pygame_"):
                    offenders.append(str(path.relative_to(ENGINE_SOURCE)))
        self.assertEqual(offenders, [], f"pygame 出现在了不该出现的地方：{offenders}")

    def test_engine_does_not_import_tests(self):
        """引擎源码不许 import 测试代码。"""
        offenders = []
        for path in iter_python_files(ENGINE_SOURCE):
            for module in imported_modules(path):
                if module.split(".")[0] == "tests":
                    offenders.append(str(path.relative_to(ENGINE_SOURCE)))
        self.assertEqual(offenders, [], f"引擎源码 import 了测试代码：{offenders}")


class TestCoreDependencyHygiene(unittest.TestCase):
    """core 内部依赖卫生：不成环、不靠非顶层 import 绕环（评估报告 5 原则审查 #1）。"""

    def test_core_internal_imports_do_not_form_cycles(self):
        """core 内部模块之间不许成环；函数里的 import 也算一条边。"""
        files = list(iter_python_files(CORE_ROOT))
        names = {module_name(path) for path in files}
        graph: dict[str, set[str]] = {name: set() for name in names}
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                for target in resolve_import(path, node):
                    if target in graph:
                        graph[module_name(path)].add(target)

        cycles: list[str] = []
        color: dict[str, int] = {}
        stack: list[str] = []

        def visit(name: str) -> None:
            """深度优先找环，把栈上的环拼成可读的一条链。"""
            color[name] = 1
            stack.append(name)
            for target in sorted(graph[name]):
                if color.get(target) == 1:
                    start = stack.index(target)
                    cycles.append(" → ".join(stack[start:] + [target]))
                elif color.get(target, 0) == 0:
                    visit(target)
            stack.pop()
            color[name] = 2

        for name in sorted(graph):
            if color.get(name, 0) == 0:
                visit(name)
        self.assertEqual(cycles, [], f"core 内部出现了循环依赖：{cycles}")

    def test_core_imports_are_all_at_module_level(self):
        """引擎内部模块的 import 一律写在模块顶层，不许用函数内 import 绕环。"""
        offenders = []
        for path in iter_python_files(CORE_ROOT):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            top_level = set(tree.body)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if node in top_level:
                    continue
                for target in resolve_import(path, node):
                    if target == "core" or target.startswith("core."):
                        offenders.append(f"{module_name(path)}:{node.lineno} → {target}")
        self.assertEqual(
            offenders,
            [],
            f"core 内部出现了非顶层 import（要共享就放中立模块或上移调用点）：{offenders}",
        )


class TestValueSnapshotHome(unittest.TestCase):
    """写入快照的落点：公开在 core.state，不再挂在 core.delta（原则审查 #2）。"""

    def test_snapshot_value_lives_in_state(self):
        """core.state.snapshot_value 可用；core.delta 不再导出它。"""
        import core.delta as delta_package
        from core.state import snapshot_value

        self.assertTrue(callable(snapshot_value))
        self.assertFalse(hasattr(delta_package, "snapshot_value"))


if __name__ == "__main__":
    unittest.main()
