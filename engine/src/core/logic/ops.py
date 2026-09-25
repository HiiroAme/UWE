"""常用运算与常用逻辑的操作表——logic 包的"字典"。

位置：
    引擎核心逻辑层 → logic 子包。
职责：
    登记每个操作符的名字、需要几个参数、属于哪一类，以及（可立即求值的）实现函数。
    解析器（ast.py）与求值器（eval.py）都按这张表工作，新增操作符只需要在这里登记。

三类操作符：
    eager   参数先求值，再把值交给实现函数（四则、比较、字符串、列表……）。
    lazy    实现自己决定要不要算参数，用于短路：if / and / or。
    context 需要读取器、参数表或函数表：get / exists / get_or / arg / call。
    后两类的实现放在 eval.py（它们要用到求值上下文），本文件只登记元信息。

数值口径（与 delta 的旧值比对保持一致）：
    number 包含 int 与 float；bool 不算数字（True 不是 1）。
    int 与 float 之间按数值比较（1 == 1.0），保证 JSON 往返不产生假差异。

确定性口径：
    本文件全部是纯函数：不读时钟、不取随机、不改传入的值。字符串比较按 Unicode 码点，
    不依赖系统语言环境；round 明确为"半值远离零"，不用 Python 的银行家舍入。
"""

import re
from dataclasses import dataclass
from math import ceil as _ceil_number
from math import floor as _floor_number
from operator import ge, gt, le, lt
from typing import Any, Callable, Sequence

from .errors import LogicError

# 三类操作符的标记。
KIND_EAGER = "eager"
KIND_LAZY = "lazy"
KIND_CONTEXT = "context"


@dataclass(frozen=True)
class Operator:
    """一个操作符的登记信息。

    属性：
        name: 操作符名，写在表达式列表的第一个位置（如 "+"、"get"）。
        kind: 三类之一，见模块文档。
        min_args: 最少参数个数。
        max_args: 最多参数个数；None 表示不限。
        func: eager 类的实现函数；lazy / context 类为 None（实现在 eval.py）。
    """

    name: str
    kind: str
    min_args: int
    max_args: int | None = None
    func: Callable[..., Any] | None = None


def describe_arity(operator: Operator) -> str:
    """把参数个数要求说成一句人话（报错用）。

    输入：
        operator: 操作符登记信息。
    输出：
        形如"恰好 2 个"、"至少 2 个"、"2 到 3 个"、"任意多个"的文字。
    异常：
        无。
    变量：
        无。
    """
    if operator.max_args is None:
        return f"至少 {operator.min_args} 个"
    if operator.min_args == operator.max_args:
        return f"恰好 {operator.min_args} 个"
    return f"{operator.min_args} 到 {operator.max_args} 个"


def require_arity(operator: Operator, count: int, location: str = "") -> None:
    """检查参数个数是否落在允许范围内。

    输入：
        operator: 操作符登记信息；
        count: 实际参数个数；
        location: 表达式位置，仅用于报错。
    输出：
        无；通过检查直接返回。
    异常：
        LogicError: 参数个数超范围。
    变量：
        无。
    """
    if count < operator.min_args or (operator.max_args is not None and count > operator.max_args):
        raise LogicError(
            f"操作符 {operator.name!r} 需要{describe_arity(operator)}参数，实际给了 {count} 个",
            location,
        )


# ---------------------------------------------------------------------------
# 类型检查小工具：所有失败都抛 LogicError，消息里说清期望什么、实际是什么。
# ---------------------------------------------------------------------------


def _need_number(value: Any, what: str = "参数") -> Any:
    """要求一个值是数字（int / float，bool 不算）。

    输入：value 待检查的值；what 出错消息里的称呼。
    输出：原值。
    异常：LogicError 不是数字。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LogicError(f"{what}必须是数字，实际是 {type(value).__name__}")
    return value


def _need_int(value: Any, what: str = "参数") -> int:
    """要求一个值是整数（bool 不算）。

    输入：value 待检查的值；what 出错消息里的称呼。
    输出：原值。
    异常：LogicError 不是整数。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise LogicError(f"{what}必须是整数，实际是 {type(value).__name__}")
    return value


def _need_string(value: Any, what: str = "参数") -> str:
    """要求一个值是字符串。

    输入：value 待检查的值；what 出错消息里的称呼。
    输出：原值。
    异常：LogicError 不是字符串。
    """
    if not isinstance(value, str):
        raise LogicError(f"{what}必须是字符串，实际是 {type(value).__name__}")
    return value


def _need_bool(value: Any, what: str = "参数") -> bool:
    """要求一个值是布尔。

    输入：value 待检查的值；what 出错消息里的称呼。
    输出：原值。
    异常：LogicError 不是布尔（数字、字符串都不算）。
    """
    if not isinstance(value, bool):
        raise LogicError(f"{what}必须是布尔值（true / false），实际是 {type(value).__name__}")
    return value


def _need_list(value: Any, what: str = "参数") -> list:
    """要求一个值是列表。

    输入：value 待检查的值；what 出错消息里的称呼。
    输出：原值。
    异常：LogicError 不是列表。
    """
    if not isinstance(value, list):
        raise LogicError(f"{what}必须是列表，实际是 {type(value).__name__}")
    return value


def _need_dict(value: Any, what: str = "参数") -> dict:
    """要求一个值是字典。

    输入：value 待检查的值；what 出错消息里的称呼。
    输出：原值。
    异常：LogicError 不是字典。
    """
    if not isinstance(value, dict):
        raise LogicError(f"{what}必须是字典，实际是 {type(value).__name__}")
    return value


def _is_number_value(value: Any) -> bool:
    """判断值是不是数字（bool 不算）。

    输入：value 任意值。
    输出：True / False。
    异常：无。
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# 数值运算
# ---------------------------------------------------------------------------


def _add(*values: Any) -> Any:
    """加法 ["+", a, b, …]。

    输入：至少两个数字。
    输出：和；全是 int 时结果是 int，出现 float 就是 float。
    异常：LogicError 参数不是数字。
    """
    total = _need_number(values[0])
    for value in values[1:]:
        total = total + _need_number(value)
    return total


def _subtract(*values: Any) -> Any:
    """减法 ["-", a, b, …]：从左往右依次减。

    输入：至少两个数字。
    输出：差。
    异常：LogicError 参数不是数字。
    """
    result = _need_number(values[0])
    for value in values[1:]:
        result = result - _need_number(value)
    return result


def _multiply(*values: Any) -> Any:
    """乘法 ["*", a, b, …]。

    输入：至少两个数字。
    输出：积。
    异常：LogicError 参数不是数字。
    """
    result = _need_number(values[0])
    for value in values[1:]:
        result = result * _need_number(value)
    return result


def _divide(*values: Any) -> Any:
    """除法 ["/", a, b, …]：从左往右依次除，结果是浮点。

    输入：至少两个数字，除数不能为 0。
    输出：商。
    异常：LogicError 参数不是数字，或除数为 0。
    """
    result = _need_number(values[0])
    for value in values[1:]:
        divisor = _need_number(value)
        if divisor == 0:
            raise LogicError("除法的除数不能为 0")
        result = result / divisor
    return result


def _floor_divide(left: Any, right: Any) -> int:
    """整除 ["//", a, b]：向下取整的整数除法。

    输入：两个数字，除数不能为 0。
    输出：整数商。
    异常：LogicError 参数不是数字，或除数为 0。
    """
    divisor = _need_number(right)
    if divisor == 0:
        raise LogicError("整除的除数不能为 0")
    return _need_number(left) // divisor


def _modulo(left: Any, right: Any) -> Any:
    """取余 ["%", a, b]：结果符号跟随除数，与 Python 一致。

    输入：两个数字，除数不能为 0。
    输出：余数。
    异常：LogicError 参数不是数字，或除数为 0。
    """
    divisor = _need_number(right)
    if divisor == 0:
        raise LogicError("取余的除数不能为 0")
    return _need_number(left) % divisor


def _negate(value: Any) -> Any:
    """取负 ["neg", a]。

    输入：一个数字。
    输出：相反数。
    异常：LogicError 参数不是数字。
    """
    return -_need_number(value)


def _absolute(value: Any) -> Any:
    """绝对值 ["abs", a]。

    输入：一个数字。
    输出：绝对值。
    异常：LogicError 参数不是数字。
    """
    return abs(_need_number(value))


def _spread(values: Sequence[Any]) -> list:
    """把 min / max / sum 的参数摊平。

    输入：values 参数序列。
    输出：只有一个参数且它是列表时返回该列表的副本；否则返回参数列表本身。
    异常：无。
    """
    if len(values) == 1 and isinstance(values[0], list):
        return list(values[0])
    return list(values)


def _minimum(*values: Any) -> Any:
    """取最小值 ["min", …]，可直接给一串，也可给一个列表。

    输入：至少一个数字，或一个数字列表。
    输出：最小值。
    异常：LogicError 参数不是数字 / 列表里混进非数字。
    """
    numbers = [_need_number(item, "min 的参数") for item in _spread(values)]
    if not numbers:
        raise LogicError("min 至少需要一个数字")
    return min(numbers)


def _maximum(*values: Any) -> Any:
    """取最大值 ["max", …]，用法同 min。

    输入：至少一个数字，或一个数字列表。
    输出：最大值。
    异常：LogicError 参数不是数字 / 列表里混进非数字。
    """
    numbers = [_need_number(item, "max 的参数") for item in _spread(values)]
    if not numbers:
        raise LogicError("max 至少需要一个数字")
    return max(numbers)


def _total(*values: Any) -> Any:
    """求和 ["sum", …]，用法同 min。

    输入：至少一个数字，或一个数字列表。
    输出：总和；空列表得到 0。
    异常：LogicError 参数不是数字 / 列表里混进非数字。
    """
    numbers = [_need_number(item, "sum 的参数") for item in _spread(values)]
    total: Any = 0
    for number in numbers:
        total = total + number
    return total


def _clamp(value: Any, low: Any, high: Any) -> Any:
    """夹范围 ["clamp", 值, 下限, 上限]：血量、行动力这类最常用。

    输入：三个数字，下限不能大于上限。
    输出：小于下限取下限，大于上限取上限，否则原值。
    异常：LogicError 参数不是数字，或下限大于上限。
    """
    number = _need_number(value)
    lower = _need_number(low, "下限")
    upper = _need_number(high, "上限")
    if lower > upper:
        raise LogicError(f"clamp 的下限 {lower} 大于上限 {upper}")
    return max(lower, min(upper, number))


def _floor(value: Any) -> int:
    """向下取整 ["floor", a]。

    输入：一个数字。
    输出：不大于它的最大整数。
    异常：LogicError 参数不是数字。
    """
    return _floor_number(_need_number(value))


def _ceil(value: Any) -> int:
    """向上取整 ["ceil", a]。

    输入：一个数字。
    输出：不小于它的最小整数。
    异常：LogicError 参数不是数字。
    """
    return _ceil_number(_need_number(value))


def _round_half_away(number: float) -> int:
    """四舍五入到整数，半值远离零（2.5 → 3，-2.5 → -3）。

    输入：number 浮点数。
    输出：整数。
    异常：无。
    """
    if number >= 0:
        return _floor_number(number + 0.5)
    return _ceil_number(number - 0.5)


def _round(value: Any, digits: Any = None) -> Any:
    """四舍五入 ["round", 值] 或 ["round", 值, 保留位数]。

    输入：一个数字；可选一个非负整数表示保留几位小数。
    输出：不带位数时给整数；带位数时给数字（十进制不保证精确，与浮点本身有关）。
    异常：LogicError 参数不是数字 / 位数不是非负整数。
    """
    number = _need_number(value)
    if digits is None:
        return _round_half_away(float(number))
    places = _need_int(digits, "保留位数")
    if places < 0:
        raise LogicError(f"round 的保留位数不能是负数：{places}")
    if places > 15:
        # 位数太大时 Python 会抛 OverflowError（不是我们的 LogicError），
        # 那会绕过"规则条件只接 LogicError/StateError"的口径冒到兜底去（R4-9）。
        raise LogicError(f"round 的保留位数最大 15，实际是 {places}")
    factor = 10 ** places
    return _round_half_away(float(number) * factor) / factor


# ---------------------------------------------------------------------------
# 逻辑、比较、判断
# ---------------------------------------------------------------------------


def _equals(left: Any, right: Any) -> bool:
    """严格相等：bool 与数字分开，int 与 float 按数值比，容器逐项递归。

    输入：两个任意值。
    输出：True / False。
    异常：无（NaN 与任何值都不相等）。
    说明：
        与 delta 的旧值比对口径保持一致（True != 1、1 == 1.0、字典比键集合与逐值、
        列表比长度与逐元素）。两处口径将来若要合并成一份实现，属于后续清理项。
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if _is_number_value(left) and _is_number_value(right):
        return left == right
    if isinstance(left, dict) or isinstance(right, dict):
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        if left.keys() != right.keys():
            return False
        return all(_equals(left[key], right[key]) for key in left)
    if isinstance(left, list) or isinstance(right, list):
        if not isinstance(left, list) or not isinstance(right, list):
            return False
        if len(left) != len(right):
            return False
        return all(_equals(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def _equal(left: Any, right: Any) -> bool:
    """相等判断 ["==", a, b]。

    输入：两个任意值。
    输出：按 _equals 的口径给 True / False。
    异常：无。
    """
    return _equals(left, right)


def _not_equal(left: Any, right: Any) -> bool:
    """不等判断 ["!=", a, b]。

    输入：两个任意值。
    输出：_equals 取反。
    异常：无。
    """
    return not _equals(left, right)


_ORDER = {"<": lt, "<=": le, ">": gt, ">=": ge}


def _ordered(name: str, left: Any, right: Any) -> bool:
    """比大小的公共实现，只允许"两个数字"或"两个字符串"。

    输入：name 操作符名；left / right 被比较的值。
    输出：True / False。
    异常：LogicError 两边类型不同或不是数字 / 字符串。
    """
    if _is_number_value(left) and _is_number_value(right):
        return _ORDER[name](left, right)
    if isinstance(left, str) and isinstance(right, str):
        return _ORDER[name](left, right)
    raise LogicError(
        f"{name} 只能比较两个数字或两个字符串，实际是 {type(left).__name__} 和 {type(right).__name__}"
    )


def _less(left: Any, right: Any) -> bool:
    """小于 ["<", a, b]。

    输入：两个数字或两个字符串。
    输出：True / False。
    异常：LogicError 类型不支持比较。
    """
    return _ordered("<", left, right)


def _less_or_equal(left: Any, right: Any) -> bool:
    """小于等于 ["<=", a, b]。

    输入：两个数字或两个字符串。
    输出：True / False。
    异常：LogicError 类型不支持比较。
    """
    return _ordered("<=", left, right)


def _greater(left: Any, right: Any) -> bool:
    """大于 [">", a, b]。

    输入：两个数字或两个字符串。
    输出：True / False。
    异常：LogicError 类型不支持比较。
    """
    return _ordered(">", left, right)


def _greater_or_equal(left: Any, right: Any) -> bool:
    """大于等于 [">=", a, b]。

    输入：两个数字或两个字符串。
    输出：True / False。
    异常：LogicError 类型不支持比较。
    """
    return _ordered(">=", left, right)


def _logical_not(value: Any) -> bool:
    """逻辑非 ["not", a]。

    输入：一个布尔值（数字、字符串不算）。
    输出：取反。
    异常：LogicError 参数不是布尔。
    """
    return not _need_bool(value, "not 的参数")


def _between(value: Any, low: Any, high: Any) -> bool:
    """闭区间判断 ["between", 值, 下限, 上限]：两端都算在内。

    输入：三个数字，或三个字符串。
    输出：下限 ≤ 值 ≤ 上限 时 True。
    异常：LogicError 类型不支持比较。
    """
    return _ordered("<=", low, value) and _ordered("<=", value, high)


def _in(value: Any, container: Any) -> bool:
    """成员判断 ["in", 值, 容器]：列表看元素，字典看键。

    输入：value 被查的值；container 列表或字典。
    输出：True / False（列表按 _equals 逐项比；字典的键必须是字符串）。
    异常：LogicError 容器不是列表 / 字典，或用字典查却不是字符串键。
    """
    if isinstance(container, list):
        return any(_equals(value, item) for item in container)
    if isinstance(container, dict):
        key = _need_string(value, "用字典查找时，被查的键")
        return key in container
    raise LogicError(f"in 的容器必须是列表或字典，实际是 {type(container).__name__}")


def _contains(container: Any, value: Any) -> bool:
    """成员判断 ["contains", 容器, 值]：与 in 相同，只是参数顺序反着写更好读。

    输入：container 列表或字典；value 被查的值。
    输出：True / False。
    异常：LogicError 容器类型不支持。
    """
    return _in(value, container)


def _is_null(value: Any) -> bool:
    """判断是不是 null ["is_null", a]。

    输入：任意值。
    输出：True / False。
    异常：无。
    """
    return value is None


def _is_number(value: Any) -> bool:
    """判断是不是数字 ["is_number", a]（bool 不算）。

    输入：任意值。
    输出：True / False。
    异常：无。
    """
    return _is_number_value(value)


def _is_string(value: Any) -> bool:
    """判断是不是字符串 ["is_string", a]。

    输入：任意值。
    输出：True / False。
    异常：无。
    """
    return isinstance(value, str)


def _is_list(value: Any) -> bool:
    """判断是不是列表 ["is_list", a]。

    输入：任意值。
    输出：True / False。
    异常：无。
    """
    return isinstance(value, list)


def _is_dict(value: Any) -> bool:
    """判断是不是字典 ["is_dict", a]。

    输入：任意值。
    输出：True / False。
    异常：无。
    """
    return isinstance(value, dict)


# ---------------------------------------------------------------------------
# 字符串
# ---------------------------------------------------------------------------


def _concat(*values: Any) -> str:
    """拼接字符串 ["concat", a, b, …]。

    输入：至少一个字符串。数字要先自己用 format 或字符串常量转。
    输出：拼好的字符串。
    异常：LogicError 参数不是字符串。
    """
    parts = [_need_string(value, "concat 的参数") for value in values]
    return "".join(parts)


def _length(value: Any) -> int:
    """长度 ["len", 值]：字符串、列表、字典都可以。

    输入：一个字符串 / 列表 / 字典。
    输出：元素个数（字符串按字符数）。
    异常：LogicError 类型不支持。
    """
    if isinstance(value, (str, list, dict)):
        return len(value)
    raise LogicError(f"len 只支持字符串、列表、字典，实际是 {type(value).__name__}")


def _substring(text: Any, start: Any, end: Any = None) -> str:
    """取子串 ["substr", 文本, 起点] 或 ["substr", 文本, 起点, 终点]。

    输入：文本是字符串；起点 / 终点是整数，可用负数（从末尾算），终点省略表示到末尾。
    输出：子串（左闭右开，与 Python 一致）。
    异常：LogicError 类型不对。
    """
    source = _need_string(text, "substr 的文本")
    begin = _need_int(start, "substr 的起点")
    if end is None:
        return source[begin:]
    return source[begin:_need_int(end, "substr 的终点")]


def _split(text: Any, separator: Any) -> list:
    """分割字符串 ["split", 文本, 分隔符]。

    输入：文本是字符串；分隔符是非空字符串。
    输出：字符串列表。
    异常：LogicError 类型不对或分隔符为空。
    """
    source = _need_string(text, "split 的文本")
    mark = _need_string(separator, "split 的分隔符")
    if not mark:
        raise LogicError("split 的分隔符不能是空字符串")
    return source.split(mark)


def _replace_text(text: Any, old: Any, new: Any) -> str:
    """替换子串 ["replace", 文本, 旧, 新]：全部替换。

    输入：三个字符串。
    输出：替换后的字符串。
    异常：LogicError 参数不是字符串。
    """
    return _need_string(text, "replace 的文本").replace(
        _need_string(old, "replace 的旧串"), _need_string(new, "replace 的新串")
    )


def _lower(text: Any) -> str:
    """转小写 ["lower", 文本]。

    输入：一个字符串。
    输出：小写字符串。
    异常：LogicError 参数不是字符串。
    """
    return _need_string(text, "lower 的文本").lower()


def _upper(text: Any) -> str:
    """转大写 ["upper", 文本]。

    输入：一个字符串。
    输出：大写字符串。
    异常：LogicError 参数不是字符串。
    """
    return _need_string(text, "upper 的文本").upper()


_PLACEHOLDER = re.compile(r"\{([0-9]+)\}")


def _format(template: Any, *values: Any) -> str:
    """模板填充 ["format", 模板, 值0, 值1, …]：把 {0}、{1} 换成对应的值。

    输入：模板是字符串（只认 {数字} 形式）；值可以是字符串、数字、布尔、null。
    输出：填好的字符串。
    异常：LogicError 模板不是字符串、占位符下标超范围、值类型不合适。
    说明：
        值是按参数顺序编号的（第 1 个值就是 {0}），需要先算出来的东西就写成子表达式；
        没用到的多余值不报错。
    """
    text = _need_string(template, "format 的模板")

    def replace(match: "re.Match[str]") -> str:
        index = int(match.group(1))
        if index >= len(values):
            raise LogicError(f"format 模板里的 {{{index}}} 超出给出的值个数 {len(values)}")
        value = values[index]
        if isinstance(value, bool) or value is None or isinstance(value, (int, float, str)):
            return str(value)
        raise LogicError(f"format 只能填字符串、数字、布尔、null，第 {index} 个值是 {type(value).__name__}")

    return _PLACEHOLDER.sub(replace, text)


# ---------------------------------------------------------------------------
# 列表与字典
# ---------------------------------------------------------------------------


def _first(items: Any) -> Any:
    """取第一个元素 ["first", 列表]。

    输入：一个非空列表。
    输出：第一个元素。
    异常：LogicError 不是列表或列表为空。
    """
    values = _need_list(items, "first 的参数")
    if not values:
        raise LogicError("first 不能用在空列表上")
    return values[0]


def _last(items: Any) -> Any:
    """取最后一个元素 ["last", 列表]。

    输入：一个非空列表。
    输出：最后一个元素。
    异常：LogicError 不是列表或列表为空。
    """
    values = _need_list(items, "last 的参数")
    if not values:
        raise LogicError("last 不能用在空列表上")
    return values[-1]


def _slice(items: Any, start: Any, end: Any = None) -> list:
    """截取列表 ["slice", 列表, 起点] 或 ["slice", 列表, 起点, 终点]。

    输入：列表；起点 / 终点是整数，可用负数，终点省略表示到末尾。
    输出：新列表（左闭右开）。
    异常：LogicError 类型不对。
    """
    values = _need_list(items, "slice 的参数")
    begin = _need_int(start, "slice 的起点")
    if end is None:
        return values[begin:]
    return values[begin:_need_int(end, "slice 的终点")]


def _keys(mapping: Any) -> list:
    """取键 ["keys", 字典]：按插入顺序返回，保证同样的数据给同样的结果。

    输入：一个字典。
    输出：键组成的列表。
    异常：LogicError 不是字典。
    """
    return list(_need_dict(mapping, "keys 的参数").keys())


def _make_list(*values: Any) -> list:
    """构造列表 ["list", …]：先算出每个参数，再装成列表（也可以用来写空列表）。

    输入：任意个值。
    输出：列表。
    异常：无。
    """
    return list(values)


# ---------------------------------------------------------------------------
# 操作表：所有操作符都在这里登记，解析器与求值器都查它。
# ---------------------------------------------------------------------------

OPERATORS: dict[str, Operator] = {
    # 取值与上下文（实现在 eval.py）
    "get": Operator("get", KIND_CONTEXT, 1, 1),
    "exists": Operator("exists", KIND_CONTEXT, 1, 1),
    "get_or": Operator("get_or", KIND_CONTEXT, 2, 2),
    "arg": Operator("arg", KIND_CONTEXT, 1, 1),
    "call": Operator("call", KIND_CONTEXT, 1),
    # 数值
    "+": Operator("+", KIND_EAGER, 2, None, _add),
    "-": Operator("-", KIND_EAGER, 2, None, _subtract),
    "*": Operator("*", KIND_EAGER, 2, None, _multiply),
    "/": Operator("/", KIND_EAGER, 2, None, _divide),
    "//": Operator("//", KIND_EAGER, 2, 2, _floor_divide),
    "%": Operator("%", KIND_EAGER, 2, 2, _modulo),
    "neg": Operator("neg", KIND_EAGER, 1, 1, _negate),
    "abs": Operator("abs", KIND_EAGER, 1, 1, _absolute),
    "min": Operator("min", KIND_EAGER, 1, None, _minimum),
    "max": Operator("max", KIND_EAGER, 1, None, _maximum),
    "sum": Operator("sum", KIND_EAGER, 1, None, _total),
    "clamp": Operator("clamp", KIND_EAGER, 3, 3, _clamp),
    "floor": Operator("floor", KIND_EAGER, 1, 1, _floor),
    "ceil": Operator("ceil", KIND_EAGER, 1, 1, _ceil),
    "round": Operator("round", KIND_EAGER, 1, 2, _round),
    # 逻辑与判断
    "if": Operator("if", KIND_LAZY, 3, 3),
    "and": Operator("and", KIND_LAZY, 2, None),
    "or": Operator("or", KIND_LAZY, 2, None),
    "not": Operator("not", KIND_EAGER, 1, 1, _logical_not),
    "==": Operator("==", KIND_EAGER, 2, 2, _equal),
    "!=": Operator("!=", KIND_EAGER, 2, 2, _not_equal),
    "<": Operator("<", KIND_EAGER, 2, 2, _less),
    "<=": Operator("<=", KIND_EAGER, 2, 2, _less_or_equal),
    ">": Operator(">", KIND_EAGER, 2, 2, _greater),
    ">=": Operator(">=", KIND_EAGER, 2, 2, _greater_or_equal),
    "between": Operator("between", KIND_EAGER, 3, 3, _between),
    "in": Operator("in", KIND_EAGER, 2, 2, _in),
    "contains": Operator("contains", KIND_EAGER, 2, 2, _contains),
    "is_null": Operator("is_null", KIND_EAGER, 1, 1, _is_null),
    "is_number": Operator("is_number", KIND_EAGER, 1, 1, _is_number),
    "is_string": Operator("is_string", KIND_EAGER, 1, 1, _is_string),
    "is_list": Operator("is_list", KIND_EAGER, 1, 1, _is_list),
    "is_dict": Operator("is_dict", KIND_EAGER, 1, 1, _is_dict),
    # 字符串
    "concat": Operator("concat", KIND_EAGER, 1, None, _concat),
    "len": Operator("len", KIND_EAGER, 1, 1, _length),
    "substr": Operator("substr", KIND_EAGER, 2, 3, _substring),
    "split": Operator("split", KIND_EAGER, 2, 2, _split),
    "replace": Operator("replace", KIND_EAGER, 3, 3, _replace_text),
    "lower": Operator("lower", KIND_EAGER, 1, 1, _lower),
    "upper": Operator("upper", KIND_EAGER, 1, 1, _upper),
    "format": Operator("format", KIND_EAGER, 1, None, _format),
    # 列表与字典
    "first": Operator("first", KIND_EAGER, 1, 1, _first),
    "last": Operator("last", KIND_EAGER, 1, 1, _last),
    "slice": Operator("slice", KIND_EAGER, 2, 3, _slice),
    "keys": Operator("keys", KIND_EAGER, 1, 1, _keys),
    "list": Operator("list", KIND_EAGER, 0, None, _make_list),
}
