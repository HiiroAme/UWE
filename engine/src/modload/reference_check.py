"""加载期引用核对（E-1 / 甲-8）：脚本与数据里写死的函数名、脚本的 import 白名单。

为什么单独一个文件：加载器本体已经很长，核对逻辑也该能单独测、单独换（例如以后给能力
声明参数契约）。这里只做**加载期、只看结构与名字**的检查（D-29 / D-14：不校验值）。

三类检查：
1. 脚本里的 `api.call("名字", …)` 与 `functions["名字"]`：**按 AST 找真正的调用 / 下标**——
   注释与 docstring 里提到的名字不会误报，跨行写法也不会漏（早前的按行正则两处都错，见评估报告 3 的 N-3）。
2. 数据里的 `["call", "名字", …]`（编译后的表达式）：与同一张函数表核对，
   报错带上"哪个条目 / 哪个位置"。
3. 脚本的 import：只允许标准库与 `core.ui` 的公开数据类；不许 import 引擎内部
   （core 其余部分 / adapters / modload / shell）——模块与 Mod 契约 §4.3。

口径：**只对字面量报错**。动态拼出来的名字（f-string / 变量）查不到，那部分由作者自己负责。
"""

import ast
import dataclasses
import sys
from typing import Any, Iterable, Mapping

from core.content import ContentReferenceError
from core.logic.ast import Node

# 引擎内部包：脚本不许 import（`core.ui` 是唯一例外——那是"一帧画面怎么说"的公开契约）。
_ENGINE_PACKAGES = ("adapters", "core", "modload", "shell")
_ENGINE_ALLOWED = ("core.ui",)


def check_scripts(files: Any, paths: Iterable[str], functions: Mapping[str, Any]) -> None:
    """核对一批脚本里的字面量函数名与 import。

    输入：
        files: 文件端口（读脚本源码）；paths: 要检查的脚本路径（可重复）；
        functions: 已经组装好的函数表（合法名字都在里面）。
    输出：
        无。
    异常：
        ContentReferenceError: 引用了不存在的函数，或 import 了引擎内部包。
    变量：
        table / checked / path / tree: 遍历与解析的中间结果。
    """
    table = set(functions)
    checked: set[str] = set()
    for path in paths:
        if path in checked or not files.exists(path):
            continue
        checked.add(path)
        try:
            tree = ast.parse(files.read_text(path), filename=path)
        except SyntaxError:
            continue        # 语法错会在"取脚本函数"那一步报出来，这里不重复报
        # R4-1：文件里若把 functions 赋成"不是 *.functions"的东西（例如自建字典），
        # 就不再核对裸名字——保守一点，宁可不查也不误报。
        shadowed = _shadows_functions(tree)
        for node in ast.walk(tree):
            name = _call_name(node)
            if name is None:
                name = _subscript_name(node, bare=not shadowed)
            if name is not None:
                _require_known(name, table, location=f"{path}:{getattr(node, 'lineno', 0)}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    _check_import(alias.name, f"{path}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    raise ContentReferenceError(
                        "脚本之间不能互相 import（每个脚本都是单独加载的）；"
                        "要共享就先放进同一个文件里",
                        location=f"{path}:{node.lineno}",
                    )
                _check_import(node.module or "", f"{path}:{node.lineno}")


def check_expressions(content: Any, functions: Mapping[str, Any]) -> None:
    """核对数据（编译后的表达式）里的 `["call", "字面量", …]`。

    输入：
        content: 编译结果（CompiledContent）；functions: 函数表。
    输出：
        无。
    异常：
        ContentReferenceError: 表达式里调用了函数表里没有的名字。
    变量：
        table / rule / action / step / command / branch / invocation: 遍历的中间结果。
    """
    table = set(functions)

    def check(expression: Any, location: str) -> None:
        """核对一个表达式里的全部 call 字面量。"""
        for name, node_location in _calls_in(expression):
            _require_known(name, table,
                          location=_join_location(location, node_location))

    for rule in content.rules.values():
        check(rule.condition, rule.location)
    for action in content.actions.values():
        for step in action.steps:
            check(step.args, step.location)
    for command in content.commands.values():
        for branch in command.branches:
            check(branch.condition, branch.location)
            for invocation in branch.actions:
                check(invocation.args, invocation.location)
    for triggers in content.triggers.values():
        for trigger in triggers:
            for invocation in trigger.actions:
                check(invocation.args, invocation.location)
    for inputs in content.inputs.values():
        for item in inputs:
            check(item.condition, item.location)
            check(item.payload, item.location)
    for formula in content.formulas.values():
        check(formula.expression, formula.location)
    for template in content.templates.values():
        check(template.attributes, template.source)
    for phase in content.phases.values():
        check(phase.settings, phase.source)
    for system in content.systems.values():
        check(system.values, system.source)


def _calls_in(value: Any) -> list[tuple[str, str]]:
    """递归找出任意编译产物里的 call 字面量：(名字, 位置后缀)。"""
    found: list[tuple[str, str]] = []
    for node in _nodes_in(value):
        if node.op == "call" and node.args and isinstance(node.args[0], str):
            found.append((node.args[0], node.location))
    return found


def _nodes_in(value: Any) -> list[Node]:
    """递归收集一个值里的全部表达式节点（容器与 dataclass 都往下走）。"""
    if isinstance(value, Node):
        found = [value]
        for argument in value.args:
            found.extend(_nodes_in(argument))
        return found
    if isinstance(value, Mapping):
        found: list[Node] = []
        for item in value.values():
            found.extend(_nodes_in(item))
        return found
    if isinstance(value, (list, tuple, set, frozenset)):
        found = []
        for item in value:
            found.extend(_nodes_in(item))
        return found
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        found = []
        for field in dataclasses.fields(value):
            found.extend(_nodes_in(getattr(value, field.name)))
        return found
    return []


def _call_name(node: ast.AST) -> str | None:
    """`api.call("名字", …)` 里的字面量名字；不是这种写法或名字不是字面量时给 None。"""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr != "call" or not isinstance(node.func.value, ast.Name):
        return None
    if node.func.value.id != "api" or not node.args:
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def _shadows_functions(tree: ast.AST) -> bool:
    """文件里有没有把 `functions` 赋成"不是 *.functions"的东西（自建字典之类）。

    有的话，裸 `functions["名字"]` 到底是函数表还是本地字典就说不准了——
    保守处理：这一份脚本里不再核对裸名字（`page_context.functions[...]` 照旧核对）。
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        values = node.value if isinstance(node, ast.Assign) else node.value
        for target in targets:
            if not (isinstance(target, ast.Name) and target.id == "functions"):
                continue
            if not (isinstance(values, ast.Attribute) and values.attr == "functions"):
                return True
    return False


def _subscript_name(node: ast.AST, *, bare: bool = True) -> str | None:
    """`functions["名字"]` / `page_context.functions["名字"]` 里的字面量名字。"""
    if not isinstance(node, ast.Subscript) or not isinstance(node.slice, ast.Constant):
        return None
    if not isinstance(node.slice.value, str):
        return None
    target = node.value
    if isinstance(target, ast.Attribute) and target.attr == "functions":
        return node.slice.value
    if bare and isinstance(target, ast.Name) and target.id == "functions":
        return node.slice.value
    return None


def _check_import(module: str, location: str) -> None:
    """检查一个 import：只放行标准库与 `core.ui`（模块要能独立分发，不能拴平台库）。"""
    root = module.split(".")[0]
    if root not in _ENGINE_PACKAGES:
        if root in sys.stdlib_module_names or root in ("__future__",):
            return                                # 标准库随便用
        raise ContentReferenceError(
            f"脚本只许 import 标准库与 {_ENGINE_ALLOWED[0]}：{module!r} 不是标准库"
            "（模块将来要独立分发，不能拴第三方库；平台相关的东西走适配器）",
            location=location,
        )
    if any(module == allowed or module.startswith(allowed + ".") for allowed in _ENGINE_ALLOWED):
        return
    raise ContentReferenceError(
        f"脚本不许 import 引擎内部（{module!r}）：模块与 Mod 只能用 Mod 也能用的东西，"
        f"引擎内部只放行 {_ENGINE_ALLOWED[0]} 的公开数据类",
        location=location,
    )


def _require_known(name: str, table: set, *, location: str) -> None:
    """名字必须在函数表里（或 rng.* 这类引擎提供的名字）。"""
    if name in table or name.startswith("rng."):
        return
    raise ContentReferenceError(
        f"引用了不存在的函数 {name!r}（函数表里共 {len(table)} 个名字："
        f"模块能力全限定名 / 绑定名 / Mod 自己的函数；随机走 rng.*）",
        location=location,
    )

def check_imports(files: Any, paths: Iterable[str]) -> None:
    """只核对 import（在"取脚本函数"之前跑：相对 import 与引擎内部 import 要先报出来）。

    输入：files（文件端口）、paths（脚本路径）。
    输出：无。
    异常：ContentReferenceError。
    变量：checked / path / tree / node: 遍历的中间结果。
    """
    checked: set[str] = set()
    for path in paths:
        if path in checked or not files.exists(path):
            continue
        checked.add(path)
        try:
            tree = ast.parse(files.read_text(path), filename=path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _check_import(alias.name, f"{path}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    raise ContentReferenceError(
                        "脚本之间不能互相 import（每个脚本都是单独加载的）；"
                        "要共享就先放进同一个文件里",
                        location=f"{path}:{node.lineno}",
                    )
                _check_import(node.module or "", f"{path}:{node.lineno}")


def _join_location(owner: str, node: str) -> str:
    """拼"条目位置 + 节点位置"，但别把同一个前缀拼两遍（R4-4）。

    条目位置（如 "m:rule:r2 → data.condition"）与节点位置有时已经互相包含，
    直接相加会出现 "m:rule:r2m:rule:r2 → data.condition" 这种难看的串。
    """
    if not node:
        return owner
    if not owner or node.startswith(owner) or owner.endswith(node):
        return node if owner and node.startswith(owner) else owner or node
    return f"{owner}{node}"
