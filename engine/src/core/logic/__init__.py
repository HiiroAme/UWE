"""logic 包：把 Mod 写在注册表里的 JSON 表达式变成引擎内的运算与判断。

位置：
    引擎核心逻辑层 → logic 子包。位于 core.state 之上，被 pipeline（Orchestrator
    的分支条件、Rule 的检查）与将来的派生值重算使用；不依赖任何平台库（P7）。

边界：
    - 只做"读数据 + 算结果"，不产生变化量、不写 State（改数据仍然走变化量通道）；
    - 表达式是纯数据，由本包按操作表解释，不使用 Python 的 eval / exec（P2）；
    - 几何、随机这类外部能力通过 call + 调用方注入的函数表提供，本包不实现它们。

用法：
    from core.logic import parse, evaluate, truth

    node = parse([">", ["get", "/units/u1/hp"], 0])   # 加载期检查一遍（可反复求值）
    truth(node, view)                                  # 运行期：视图本身就是读取器
    evaluate(["clamp", ["get", "/units/u1/hp"], 0, 100], state_reader)

文件分工：
    errors.py  LogicError（本包唯一的异常）
    ops.py     操作表：常用运算与常用逻辑的登记与实现
    ast.py     解析 + 加载期静态检查
    eval.py    求值器 evaluate / 逻辑器 truth / 读取器 Reader 与 StateReader
    resolve.py 把"数据里的表达式"整块算成具体数据（resolve / resolve_mapping）
"""

from .ast import Node, collect_literal_paths, parse
from .errors import LogicError
from .eval import Reader, StateReader, evaluate, truth
from .ops import OPERATORS, Operator
from .resolve import parse_data, resolve, resolve_mapping

__all__ = [
    "LogicError",
    "Node",
    "Operator",
    "OPERATORS",
    "parse",
    "collect_literal_paths",
    "evaluate",
    "truth",
    "parse_data",
    "resolve",
    "resolve_mapping",
    "Reader",
    "StateReader",
]
