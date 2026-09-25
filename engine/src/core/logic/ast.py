"""表达式解析：把 JSON 表达式变成引擎内部的节点树。

位置：
    引擎核心逻辑层 → logic 子包。
职责：
    1. 判断一个 JSON 值是"字面量"还是"操作调用"，把操作调用解析成 Node；
    2. 在解析阶段做完静态检查：操作符是否登记、参数个数对不对、路径 / 名字这类必须是
       字面量字符串的参数有没有写错、字面量是不是纯 JSON 数据。

为什么单独一层：
    Node 是不可变对象，解析一次可以反复求值。Mod 加载时解析一遍就等于做完了静态检查
    （对应草案 D-29：检查项由 Mod 给出、给多少查多少），运行期不必再检查结构。

表达式写法：
    字面量      数字、字符串、true / false、null、对象（对象永远是字面量）
    操作调用    [操作名, 参数…]，操作名必须是已登记的名字，例如 ["+", 1, 2]
    字面量列表  第一个元素不是字符串的列表（如 [1, 2]）直接当字面量；第一个元素是
                字符串的列表一律当作操作调用，所以"一串字符串"要写成 ["list", "a", "b"]。
                这样操作名拼错（["adn", …]）会当场报错，而不是悄悄变成一个列表。

路径参数的两种写法（读数据的三个操作符：get / exists / get_or）：
    ["get", "/units/u1/hp"]                        字面量：加载期就检查路径写法
    ["get", ["format", "/units/{0}/hp", ["arg","unit"]]]   表达式：运行期算出来再读
    后者用于"按参数拼路径"（单位 id 来自输入时只能这么写）。名字类参数
    （arg / call / format）仍然要求是字面量字符串，保证名字当场看得见。
"""

from dataclasses import dataclass
from typing import Any

from ..state.errors import PathSyntaxError
from ..state.path import parse as parse_pointer
from .errors import LogicError
from .ops import OPERATORS, require_arity

# 首个参数必须是"字面量字符串"的操作符：参数名、函数名、模板名都要当场看得见，
# 不允许是算出来的（这样加载期就能检查名字，日志也更好读）。
_LITERAL_STRING_FIRST_ARG = ("arg", "call", "format")

# 读数据的操作符：路径写成字面量字符串时在加载期校验写法；写成表达式时改在运行期
# 求值（用于"按参数拼出来的路径"，例如 ["get", ["format", "/units/{0}/hp", ["arg", "unit"]]]）。
_PATH_OPERATORS = ("get", "exists", "get_or")


@dataclass(frozen=True)
class Node:
    """一个操作调用节点。

    属性：
        op: 操作符名（已在 OPERATORS 里登记）。
        args: 参数元组，每个元素是 Node（子表达式）或已校验的字面量。
        location: 它在整条表达式里的位置（"/0"、" /0/1"），只用于报错。
    """

    op: str
    args: tuple[Any, ...]
    location: str


def parse(expression: Any, location: str = "") -> Any:
    """把 JSON 表达式解析成 Node 或字面量，并完成全部静态检查。

    输入：
        expression: JSON 值（对象、列表、数字、字符串、布尔、null），也可传入已经
            解析好的 Node（直接返回，便于反复使用）。
        location: 当前位置，递归时自动拼接；外部调用不用传。
    输出：
        Node（操作调用）或字面量本身。
    异常：
        LogicError: 操作符没登记、参数个数不对、该写字面量字符串的地方写了表达式、
            路径写法不合法、出现不是 JSON 数据的值（P2 数据包原则）。
    变量：
        head: 列表的第一个元素，用来判断这个列表是不是一次操作调用。
    """
    if isinstance(expression, Node):
        return expression
    if isinstance(expression, dict):
        _check_literal(expression, location)
        return expression
    if isinstance(expression, list):
        head = expression[0] if expression else None
        if isinstance(head, str):
            return _parse_call(expression, location)
        _check_literal(expression, location)
        return expression
    _check_literal(expression, location)
    return expression


def _parse_call(expression: list, location: str) -> Node:
    """解析一次操作调用（只被 parse 调用）。

    输入：
        expression: 形如 [操作名, 参数…] 的列表。
        location: 当前位置。
    输出：
        Node。
    异常：
        LogicError: 参数个数不对、首个参数该是字面量字符串却没写、路径写法不合法。
    变量：
        op: 操作符名；
        operator: 该操作符的登记信息（参数个数上下限）；
        count: 实际参数个数（不含操作名）。
    """
    op = expression[0]
    operator = OPERATORS.get(op)
    if operator is None:
        raise LogicError(
            f"没有登记的操作符：{op!r}（要写一串字符串当数据，请写成 [\"list\", …]）", location
        )
    count = len(expression) - 1
    require_arity(operator, count, location)

    if op in _LITERAL_STRING_FIRST_ARG and count > 0 and not isinstance(expression[1], str):
        raise LogicError(f"操作符 {op!r} 的第一个参数必须是字面量字符串", f"{location}/0")
    if op in _PATH_OPERATORS and count > 0 and isinstance(expression[1], str):
        _check_pointer(expression[1], f"{location}/0")

    args = [parse(item, f"{location}/{index}") for index, item in enumerate(expression[1:])]
    return Node(op=op, args=tuple(args), location=location)


def _check_pointer(path: str, location: str) -> None:
    """检查路径参数是不是合法的 JSON Pointer。

    输入：
        path: 路径字符串（parse 已保证是字符串）；
        location: 表达式位置，仅用于报错。
    输出：
        无；通过检查直接返回。
    异常：
        LogicError: 路径写法不合法（复用 state 层的判定）。
    变量：
        无。
    """
    try:
        parse_pointer(path)
    except PathSyntaxError as exc:
        raise LogicError(f"路径写法不合法：{exc.detail}", location) from exc


def _check_literal(value: Any, location: str) -> None:
    """检查一个值是不是纯 JSON 数据，并递归检查容器内部。

    输入：
        value: 待检查的值；
        location: 表达式位置，仅用于报错。
    输出：
        无；通过检查直接返回。
    异常：
        LogicError: 出现非 JSON 数据，或对象里出现非字符串的键。
    变量：
        index / key: 当前检查到的元素下标或键名，用来拼更精确的位置。
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_literal(item, f"{location}/{index}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise LogicError(f"字面量对象的键必须是字符串，实际是 {type(key).__name__}", location)
            _check_literal(item, f"{location}/{key}")
        return
    raise LogicError(f"字面量只能是 JSON 数据，出现了 {type(value).__name__}", location)


def collect_literal_paths(value: Any) -> tuple[str, ...]:
    """收集一段已解析的表达式里**写死的**那些路径。

    输入：
        value: 已解析的表达式（Node / dict / list / 标量，即 parse 或 parse_data 的产物）。
    输出：
        去重后的路径元组（按出现顺序）；没有写死路径时是空元组。
    异常：
        无（不认识的东西直接跳过）。
    变量：
        found: 收集到的路径（按出现顺序）；
        item: 遍历时当前的值。

    用途：
        派生值要在加载期检查"会不会读到别的派生值、层号是否够高"（§6.5 / D-39），
        依据就是这里收集到的字面量路径。按参数拼出来的动态路径没法在加载期判断，
        这里自然也收集不到（那种情况由 Mod 自己保证顺序）。

    说明：
        只认 get / exists / get_or 三个读路径的操作符，且只认**字面量字符串**路径；
        其余操作符与表达式路径不进结果。
    """
    found: list[str] = []
    _collect_paths(value, found)
    return tuple(found)


def _collect_paths(value: Any, found: list) -> None:
    """递归收集路径（collect_literal_paths 的内部实现）。

    输入：
        value: 当前值；
        found: 收集用的列表（就地追加）。
    输出：
        无。
    异常：
        无。
    变量：
        item / argument: 遍历时的元素。
    """
    if isinstance(value, Node):
        if value.op in _PATH_OPERATORS and value.args and isinstance(value.args[0], str):
            if value.args[0] not in found:
                found.append(value.args[0])
        for argument in value.args:
            _collect_paths(argument, found)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_paths(item, found)
        return
    if isinstance(value, list):
        for item in value:
            _collect_paths(item, found)
