"""把"数据里的表达式"整块处理掉：加载期解析一次，运行期反复求值。

位置：
    引擎核心逻辑层 → logic 子包，与 evaluate / truth 并列。

要解决的问题：
    注册表里的参数块是**混着表达式与字面量的 JSON**：

        {"target": ["arg", "unit_id"],
         "damage": ["*", ["get", "/units/u1/atk"], 2],
         "note": "突击"}

    引擎既要在加载期把这些表达式检查一遍（操作符、参数个数、路径写法），
    又要在运行期把它们算成具体值。所以本模块分成两步：

        parse_data(value)  加载期：递归解析整块数据，表达式变成 Node，字面量原样保留；
        resolve(value)     运行期：把 Node 算成值，容器返回新副本。

两个入口必须同时使用（content 编译器用 parse_data，Applier / Dispatcher 用 resolve），
否则"检查过的写法"与"实际求值的写法"就会不一致。

嵌套口径（与 ast.parse 的书写约定一致）：
    任何一个**首元素是字符串的列表**都当作操作调用（不管嵌在多深），
    所以"一串字符串"当数据要写成 ["list", "a", "b"]。
    这样操作名拼错会在加载期当场报错，而不是悄悄当成普通列表。

边界：
    - 只算不写：不产生变化量、不改 State（P9 纯逻辑与副作用分离）；
    - 不做默认值填充：字段该不该有由 Mod 决定，引擎不替它补（P1 / D-14）。

草案依据：
    §9 第 5～7 步（condition → result 的分支、Action 的参数）；
    §16 脚本产出变化量时的读取口径（求值时读的是调用方给的读取器，
    运行时传进来的是临时状态视图）；P9 计算与副作用分离。
"""

from typing import Any, Callable, Mapping

from .ast import Node, parse
from .errors import LogicError
from .eval import Reader, evaluate


def parse_data(value: Any, location: str = "") -> Any:
    """加载期：把一整块数据里的表达式解析成 Node（递归到每个容器内部）。

    输入：
        value: 任意 JSON 数据（dict / list / 标量，可嵌套）；
        location: 位置串，递归时自动拼接，用于报错。
    输出：
        与输入同构的结构：表达式位置变成 Node，字面量原样保留；
        dict / list 都是新对象。
    异常：
        LogicError: 表达式写法不合法（操作符没登记、参数个数不对、路径写法不合法），
            或出现不是 JSON 数据的值。
    变量：
        key / item / index: 递归时当前的键、值与下标。
    """
    if isinstance(value, Node):
        return value
    if isinstance(value, dict):
        result: dict = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise LogicError(f"字面量对象的键必须是字符串，实际是 {type(key).__name__}", location)
            result[key] = parse_data(item, f"{location}/{key}")
        return result
    if isinstance(value, list):
        if value and isinstance(value[0], str):
            # 首元素是字符串 → 一次操作调用，交给 ast.parse 做全部静态检查。
            return parse(value, location)
        return [
            parse_data(item, f"{location}/{index}") for index, item in enumerate(value)
        ]
    return parse(value, location)


def resolve(
    value: Any,
    reader: Reader | None = None,
    *,
    args: Mapping[str, Any] | None = None,
    functions: Mapping[str, Callable[..., Any]] | None = None,
) -> Any:
    """运行期：把一块（可能已解析过的）数据里的表达式算成具体值。

    输入：
        value: 待处理的数据块；可以是 parse_data 的产物，也可以是原始 JSON
            （原始 JSON 里首元素是字符串的列表会当场按表达式解析）；
        reader: 数据入口（State 读取器或临时状态视图）；表达式不读数据时可以不给；
        args: 当前 Action / Command 的参数表，供 ["arg", 名字] 使用；
        functions: 外部函数表，供 ["call", 名字, …] 使用（例如随机、几何）。
    输出：
        与输入同构的新数据：表达式被替换成它的值，字面量原样保留；
        dict / 列表都是新对象，改它不会影响输入（返回的是可安全持有的副本）。
    异常：
        LogicError: 表达式写法不合法、操作符没登记、参数个数不对、求值失败，
            或出现了不是 JSON 数据的值；
        state 层的异常: 表达式里的 get / exists 路径走不通（由 state 原语抛出）。
    变量：
        key / item: 递归处理容器时的键与元素。
    """
    if isinstance(value, Node):
        return evaluate(value, reader, args=args, functions=functions)
    if isinstance(value, dict):
        return {
            key: resolve(item, reader, args=args, functions=functions)
            for key, item in value.items()
        }
    if isinstance(value, list):
        if value and isinstance(value[0], str):
            return evaluate(parse(value), reader, args=args, functions=functions)
        return [resolve(item, reader, args=args, functions=functions) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise LogicError(f"字面量只能是 JSON 数据，出现了 {type(value).__name__}")


def resolve_mapping(
    value: Any,
    reader: Reader | None = None,
    *,
    args: Mapping[str, Any] | None = None,
    functions: Mapping[str, Callable[..., Any]] | None = None,
) -> dict:
    """把一块数据算成一个 dict（参数块的专用入口）。

    输入：
        value: 待处理的数据块；必须是 dict（表达式算出来的结果也必须是 dict）；
        reader / args / functions: 与 resolve 相同。
    输出：
        新的 dict（内容已全部求值）。
    异常：
        LogicError: value 不是 dict，或求值结果不是 dict。
    变量：
        result: resolve 的结果。

    说明：
        Action 的 args、Command 的 payload 都是"名字 → 值"的表，用这个入口能让
        "写错了（写成列表 / 数字）"在当场就报错，而不是等到脚本里取值时才炸。
    """
    result = resolve(value, reader, args=args, functions=functions)
    if not isinstance(result, dict):
        raise LogicError(f"参数块必须是 dict，实际算出来是 {type(result).__name__}")
    return result
