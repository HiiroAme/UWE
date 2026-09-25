"""表达式求值：求值器与逻辑器。

位置：
    引擎核心逻辑层 → logic 子包，位于 core.state 之上。
职责：
    evaluate(表达式, 读取器, args=…, functions=…) → 值
    truth(表达式, 读取器, args=…, functions=…)    → 布尔（草案里的 matcher）
    StateReader：把一棵 State 树包成读取器；TempState 视图本身也符合 Reader，
    直接把它传进来即可（它正好有 get / exists 两个方法）。

求值约定：
    - 纯函数、无副作用、确定性：同样输入永远同样输出，不读时钟、不取随机、不写文件；
    - 不用 Python 的 eval / exec：表达式是纯数据，由本包按操作表解释（P2）；
    - 字面量容器返回深拷贝，改它不会影响表达式本身；
    - 读取器返回的容器按只读处理（§16）：求值器只读，不复制、不修改。
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from ..state.errors import StateError
from ..state.tree import exists as tree_exists
from ..state.tree import get as tree_get
from ..state.wildcard import expand as expand_pattern
from .ast import Node, parse
from .errors import LogicError
from .ops import (
    KIND_CONTEXT,
    KIND_LAZY,
    OPERATORS,
    _need_bool,
    require_arity,
)


class Reader(Protocol):
    """表达式读取数据的入口（端口）。

    职责：
        把"路径 → 值"这件事交给调用方决定：读临时视图还是读真实 State 都行。
    实现者：
        StateReader（本文件）、TempState（视图，方法名正好一致）。
    方法：
        get(path): 取路径上的值；路径不存在时抛 state 层的异常。
        exists(path): 路径是否存在。
        expand(pattern): 按通配路径列出全部匹配（F-05 批量操作要用）。
    """

    def get(self, path: str) -> Any:
        """按路径取值。"""

    def exists(self, path: str) -> bool:
        """判断路径是否存在。"""

    def expand(self, pattern: str) -> tuple:
        """按通配路径列出全部匹配（具体路径 + 捕获到的名字）。"""


class StateReader:
    """把一棵 State 树包成 Reader（用 core.state 的读写原语）。

    字段：
        _state: 被包装的 State 树；本类只读它。
    说明：
        只读不写：表达式的改动不在这里发生，改数据必须走变化量通道。
    """

    def __init__(self, state: dict) -> None:
        """创建读取器。

        输入：
            state: State 树根，必须是 dict。
        输出：
            无（构造对象）。
        异常：
            TypeError: state 不是 dict（由 core.state 的原语抛出）。
        变量：
            无。
        """
        if not isinstance(state, dict):
            raise TypeError(f"StateReader 需要 dict 作为 State 根，实际是 {type(state).__name__}")
        self._state = state

    def get(self, path: str) -> Any:
        """按路径取值（转发 core.state.get）。

        输入：path 路径字符串。
        输出：路径上的值。
        异常：PathSyntaxError / PathNotFoundError / StateShapeError。
        """
        return tree_get(self._state, path)

    def exists(self, path: str) -> bool:
        """判断路径是否存在（转发 core.state.exists）。

        输入：path 路径字符串。
        输出：True / False。
        异常：PathSyntaxError（路径写法不合法）。
        """
        return tree_exists(self._state, path)

    def expand(self, pattern: str) -> tuple:
        """按通配路径列出全部匹配（F-05）。

        输入：
            pattern: 通配路径（也可以是普通路径）。
        输出：
            core.state.Match 元组（顺序确定）。
        异常：
            PathSyntaxError: 路径写法不合法。
        """
        return expand_pattern(self._state, pattern)


@dataclass(frozen=True)
class _Context:
    """一次求值的上下文（内部使用）。

    字段：
        reader: 数据入口，可为 None（表达式不读数据时不需要）。
        args: 当前 Action / Command 的参数表，供 ["arg", 名字] 使用。
        functions: 外部函数表，供 ["call", 名字, 参数…] 使用。
    """

    reader: Reader | None
    args: Mapping[str, Any] | None
    functions: Mapping[str, Callable[..., Any]] | None


def evaluate(
    expression: Any,
    reader: Reader | None = None,
    *,
    args: Mapping[str, Any] | None = None,
    functions: Mapping[str, Callable[..., Any]] | None = None,
) -> Any:
    """求出表达式的值。

    输入：
        expression: JSON 表达式，或已经 parse 过的 Node；
        reader: 数据入口（StateReader 或 TempState 视图）；表达式不读数据时可省略；
        args: 本次 Action / Command 的参数表，供 arg 使用；
        functions: 外部函数表，供 call 使用（几何、随机等由调用方注入）。
    输出：
        表达式算出来的值（JSON 类型：数字、字符串、布尔、null、列表、字典）。
    异常：
        LogicError: 表达式不合法、类型对不上、路径读不到、函数没提供等。
    变量：
        node: 解析后的表达式；
        context: 本次求值的上下文。
    """
    node = parse(expression)
    context = _Context(reader=reader, args=args, functions=functions)
    return _evaluate(node, context)


def truth(
    expression: Any,
    reader: Reader | None = None,
    *,
    args: Mapping[str, Any] | None = None,
    functions: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """求出表达式并当作条件：结果必须是布尔值。

    输入：同 evaluate。
    输出：True / False。
    异常：
        LogicError: 求值失败，或结果不是布尔（数字、字符串不算——这样能挡住
            "把数量当条件"这类写法错误）。
    变量：
        value: 求值结果。
    说明：
        这就是草案里说的 matcher：Rule 检查、condition 分支、trigger 条件都用它。
    """
    value = evaluate(expression, reader, args=args, functions=functions)
    if not isinstance(value, bool):
        raise LogicError(f"条件的结果必须是布尔值，实际是 {type(value).__name__}")
    return value


def _evaluate(node: Any, context: _Context) -> Any:
    """递归求值一个节点或字面量（内部实现）。

    输入：
        node: Node 或字面量；
        context: 本次求值的上下文。
    输出：该节点的值。
    异常：
        LogicError: 操作符没登记、参数个数不对、求值失败。
    变量：
        operator: 操作符登记信息；
        values: eager 类参数求值后的结果。
    """
    if not isinstance(node, Node):
        return deepcopy(node)

    operator = OPERATORS.get(node.op)
    if operator is None:
        raise LogicError(f"没有登记的操作符：{node.op!r}", node.location)
    require_arity(operator, len(node.args), node.location)

    if operator.kind == KIND_LAZY:
        return _LAZY_HANDLERS[node.op](node, context)
    if operator.kind == KIND_CONTEXT:
        return _CONTEXT_HANDLERS[node.op](node, context)

    values = [_evaluate(argument, context) for argument in node.args]
    try:
        return operator.func(*values)
    except LogicError as exc:
        raise exc.at(node.location) from exc


def _location_of(node: Node, index: int) -> str:
    """算出"某个参数"的位置，仅用于报错。

    输入：
        node: 当前节点；
        index: 参数下标。
    输出：
        位置字符串，如 "/1"、" /2/0"。
    异常：
        无。
    """
    return f"{node.location}/{index}"


def _condition_bool(value: Any, what: str, location: str) -> bool:
    """把值当布尔检查，报错时补上参数位置。

    输入：
        value: 待检查的值；
        what: 出错消息里的称呼；
        location: 该参数在表达式里的位置。
    输出：
        布尔值。
    异常：
        LogicError: 不是布尔。
    说明：
        lazy 类操作符自己算参数，所以位置要在这里补；eager 类由 _evaluate 统一补。
    """
    try:
        return _need_bool(value, what)
    except LogicError as exc:
        raise exc.at(location) from exc


def _require_reader(context: _Context, name: str, node: Node) -> Reader:
    """要求本次求值提供了读取器。

    输入：
        context: 求值上下文；
        name: 操作符名，用于报错；
        node: 当前节点，用于报错位置。
    输出：
        读取器。
    异常：
        LogicError: 没有提供读取器。
    """
    if context.reader is None:
        raise LogicError(f"操作符 {name!r} 需要读取器，本次求值没有提供", node.location)
    return context.reader


def _path_of(node: Node, context: _Context, name: str) -> str:
    """取出读数据类操作符的路径参数。

    输入：
        node: 当前节点（第一个参数是路径）；
        context: 求值上下文；
        name: 操作符名，用于报错。
    输出：
        路径字符串。
    异常：
        LogicError: 路径写成表达式时算出来的不是字符串。
    变量：
        raw / value: 原始参数与（必要时）求值结果。

    说明：
        路径写成字面量字符串时，写法已经在加载期校验过（ast.parse），这里直接用；
        写成表达式时（按参数拼出来的路径）在这里求值，并要求结果是字符串。
    """
    raw = node.args[0]
    if isinstance(raw, str):
        return raw
    value = _evaluate(raw, context)
    if not isinstance(value, str):
        raise LogicError(
            f"{name} 的路径必须是字符串，实际算出来是 {type(value).__name__}",
            _location_of(node, 0),
        )
    return value


def _op_get(node: Node, context: _Context) -> Any:
    """取值 ["get", 路径]。

    输入：路径是字面量字符串（解析阶段已校验写法），或一个算出字符串的表达式。
    输出：路径上的值。
    异常：LogicError 没有读取器，或读取失败（路径不存在等）。
    """
    reader = _require_reader(context, "get", node)
    path = _path_of(node, context, "get")
    try:
        return reader.get(path)
    except StateError as exc:
        raise LogicError(f"读取路径 {path!r} 失败：{exc.detail}", node.location) from exc


def _op_exists(node: Node, context: _Context) -> bool:
    """判断路径是否存在 ["exists", 路径]。

    输入：路径是字面量字符串，或一个算出字符串的表达式。
    输出：True / False。
    异常：LogicError 没有读取器，或路径写法不合法。
    """
    reader = _require_reader(context, "exists", node)
    path = _path_of(node, context, "exists")
    try:
        return reader.exists(path)
    except StateError as exc:
        raise LogicError(f"判断路径 {path!r} 失败：{exc.detail}", node.location) from exc


def _op_get_or(node: Node, context: _Context) -> Any:
    """取值、取不到就用默认 ["get_or", 路径, 默认值表达式]。

    输入：路径是字面量字符串或算出字符串的表达式；默认值是任意子表达式。
    输出：路径上的值，或默认值。
    异常：LogicError 没有读取器。
    说明：
        只有"读取失败"（路径不存在、路径穿过标量）才用默认值；默认值本身也是
        子表达式，只有真要用到时才求值。
    """
    reader = _require_reader(context, "get_or", node)
    path = _path_of(node, context, "get_or")
    try:
        return reader.get(path)
    except StateError:
        return _evaluate(node.args[1], context)


def _op_arg(node: Node, context: _Context) -> Any:
    """取本次 Action / Command 的参数 ["arg", 名字]。

    输入：名字是字面量字符串。
    输出：参数值。
    异常：LogicError 没有提供参数表，或参数表里没有这个名字。
    说明：参数值按只读处理，不要在表达式或服务里就地改它。
    """
    name = node.args[0]
    if context.args is None:
        raise LogicError("操作符 'arg' 需要参数表，本次求值没有提供", node.location)
    if name not in context.args:
        raise LogicError(f"本次参数里没有 {name!r}", node.location)
    return context.args[name]


def _op_call(node: Node, context: _Context) -> Any:
    """调用外部函数 ["call", 函数名, 参数…]。

    输入：函数名是字面量字符串；参数是任意子表达式。
    输出：函数返回的值。
    异常：LogicError 函数没提供，或函数执行失败。
    说明：
        几何、随机这类能力由调用方注入（函数表），本包不实现它们；注入的函数必须是
        纯的、确定的，否则回放与确定性测试会失效。
    """
    name = node.args[0]
    function = (context.functions or {}).get(name)
    if function is None:
        available = sorted(context.functions) if context.functions else []
        raise LogicError(f"外部函数 {name!r} 没有提供（已提供：{available}）", node.location)

    values = [_evaluate(argument, context) for argument in node.args[1:]]
    try:
        return function(*values)
    except LogicError as exc:
        raise exc.at(node.location) from exc
    except Exception as exc:  # 外部实现的意外异常统一转成 LogicError，便于上层记录
        raise LogicError(f"外部函数 {name!r} 执行失败：{exc}", node.location) from exc


def _op_if(node: Node, context: _Context) -> Any:
    """三元选择 ["if", 条件, 真值, 假值]：只算被选中的那一支。

    输入：条件是子表达式且结果必须是布尔；真值 / 假值是任意子表达式。
    输出：被选中分支的值。
    异常：LogicError 条件不是布尔。
    """
    condition = _condition_bool(_evaluate(node.args[0], context), "if 的条件", _location_of(node, 0))
    branch = node.args[1] if condition else node.args[2]
    return _evaluate(branch, context)


def _op_and(node: Node, context: _Context) -> bool:
    """逻辑与 ["and", a, b, …]：遇到 False 立刻返回，后面的不算。

    输入：至少两个子表达式，每个结果都必须是布尔。
    输出：True / False。
    异常：LogicError 某一项不是布尔。
    """
    for index, argument in enumerate(node.args):
        value = _evaluate(argument, context)
        if not _condition_bool(value, "and 的参数", _location_of(node, index)):
            return False
    return True


def _op_or(node: Node, context: _Context) -> bool:
    """逻辑或 ["or", a, b, …]：遇到 True 立刻返回，后面的不算。

    输入：至少两个子表达式，每个结果都必须是布尔。
    输出：True / False。
    异常：LogicError 某一项不是布尔。
    """
    for index, argument in enumerate(node.args):
        value = _evaluate(argument, context)
        if _condition_bool(value, "or 的参数", _location_of(node, index)):
            return True
    return False


# lazy 与 context 两类操作符的实现表：新加这类操作符时在这里登记。
_LAZY_HANDLERS: dict[str, Callable[[Node, _Context], Any]] = {
    "if": _op_if,
    "and": _op_and,
    "or": _op_or,
}

_CONTEXT_HANDLERS: dict[str, Callable[[Node, _Context], Any]] = {
    "get": _op_get,
    "exists": _op_exists,
    "get_or": _op_get_or,
    "arg": _op_arg,
    "call": _op_call,
}
