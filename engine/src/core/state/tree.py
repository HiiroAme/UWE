"""State 纯数据树的读取与写入原语。

位置：
    引擎核心逻辑层 → state 子包。
职责：
    在"根是 dict、内部只用 dict / list / str / int / float / bool / None"的
    纯数据树上，按 JSON Pointer 路径做读取、新增键、删除键、替换值、
    列表插入与列表删除。

草案依据：
    §6.2 State 是一棵树，结构由 Mod 决定；
    §7.2 新增/删除 = 往容器里加/删一个键；列表增删元素算"修改该列表"（D-34）；
    D-09 State 单树；D-15 一个变化量只改一个变量；D-14 运行时不校验 Mod 数据。

设计约定（与上层模块的契约）：
    1. State 的规范表示只允许 dict / list / str / int / float / bool / None，
       不出现自定义对象：保证可直接 JSON 序列化、可直接比较、可直接求逆。
    2. 写操作**不自动创建中间节点**：目标父容器必须已经存在，这与草案里
       "新增 = 往一个已存在的容器里加进一个键"的定义一致。
    3. 本模块的函数**直接修改传入的那棵树**，不做任何复制；改真实 State 还是
       改临时状态，由上层（临时状态视图、提交逻辑）决定。
    4. 本模块不做值类型校验（D-14），只做"能不能继续寻址"所需的结构判断。

异常口径（权威说明见 errors.py 模块文档）：
    路径寻址失败（键不存在、路径里的下标越界）→ PathNotFoundError；
    操作参数与数据冲突（键已存在/不存在、参数下标越界）→ StateConflictError；
    类型不支持该操作 → StateShapeError；参数类型错误 → TypeError。
"""

from typing import Any, Sequence

from .errors import PathNotFoundError, PathSyntaxError, StateConflictError, StateShapeError
from .path import parse


def get(state: dict, path: str) -> Any:
    """按路径读取一个值。

    输入：
        state: State 树根，必须是 dict。
        path: JSON Pointer 路径字符串；"" 表示读取整棵树。
    输出：
        路径指向的值；值是 None 时同样返回 None（表示"存在且为空值"）。
    异常：
        TypeError: state 不是 dict 或 path 不是字符串。
        PathSyntaxError: 路径写法不合法，或对列表使用了下标写法不合法的段。
        PathNotFoundError: 路径中存在走不通的段（键不存在 / 下标越界）。
        StateShapeError: 中途遇到不能再向下寻址的标量。
    变量：
        tokens: parse 得到的路径段元组。
    """
    _require_dict_root(state)
    tokens = parse(path)
    return _resolve(state, tokens, path)


def exists(state: dict, path: str) -> bool:
    """判断路径在树里是否存在（值是 None 也算存在）。

    输入：
        state: State 树根，必须是 dict。
        path: JSON Pointer 路径字符串；"" 恒为 True。
    输出：
        True 表示能按该路径取到值；False 表示键不存在、下标越界，
        或中途遇到标量导致路径走不通。
    异常：
        TypeError: state 不是 dict 或 path 不是字符串。
        PathSyntaxError: 路径写法不合法（含列表下标写法不合法）。
    变量：
        tokens: parse 得到的路径段元组。
    说明：
        这是唯一的"探测"接口：把"不存在"转成 False，供 UI、日志这类
        只需要判断存在性的调用方使用；路径写法错误仍然抛出。
    """
    _require_dict_root(state)
    tokens = parse(path)
    try:
        _resolve(state, tokens, path)
    except (PathNotFoundError, StateShapeError):
        return False
    return True


def add_key(state: dict, path: str, key: str, value: Any) -> None:
    """新增：往 path 指向的容器里加进一个键（键名 + 键值）。

    输入：
        state: State 树根，必须是 dict。
        path: 指向容器的路径；"" 表示根容器本身。
        key: 新键的键名，必须是字符串且当前不存在。
        value: 新键的值，可以是任意纯数据类型（含整棵子树）。
    输出：
        无；直接修改 state。
    异常：
        TypeError: state 不是 dict、path 不是字符串或 key 不是字符串。
        PathSyntaxError / PathNotFoundError: 容器路径走不通。
        StateShapeError: 容器不是 dict（例如目标是一个列表）。
        StateConflictError: 键已经存在。
    变量：
        container: path 指向的那个容器，必须是 dict。
    """
    _require_dict_root(state)
    if not isinstance(key, str):
        raise TypeError(f"键名必须是字符串，实际是 {type(key).__name__}")

    container = _resolve(state, parse(path), path)
    if not isinstance(container, dict):
        raise StateShapeError(path, f"新增要求目标容器是 dict，实际是 {type(container).__name__}")
    if key in container:
        raise StateConflictError(path, f"新增失败：键 {key!r} 已存在")

    container[key] = value


def remove_key(state: dict, path: str, key: str) -> Any:
    """删除：从 path 指向的容器里删掉一个键（键名 + 键值）。

    输入：
        state: State 树根，必须是 dict。
        path: 指向容器的路径；"" 表示根容器本身。
        key: 要删除的键名，必须是字符串且当前存在。
    输出：
        被删除的键值（供求逆与日志记录原始值使用）。
    异常：
        TypeError: state 不是 dict、path 不是字符串或 key 不是字符串。
        PathSyntaxError / PathNotFoundError: 容器路径走不通。
        StateShapeError: 容器不是 dict。
        StateConflictError: 键不存在。
    变量：
        container: path 指向的那个容器，必须是 dict。
    """
    _require_dict_root(state)
    if not isinstance(key, str):
        raise TypeError(f"键名必须是字符串，实际是 {type(key).__name__}")

    container = _resolve(state, parse(path), path)
    if not isinstance(container, dict):
        raise StateShapeError(path, f"删除要求目标容器是 dict，实际是 {type(container).__name__}")
    if key not in container:
        raise StateConflictError(path, f"删除失败：键 {key!r} 不存在")

    return container.pop(key)


def replace(state: dict, path: str, value: Any) -> Any:
    """修改（替换）：把 path 指向的值换成新值，并返回旧值。

    输入：
        state: State 树根，必须是 dict。
        path: 指向被替换目标的路径；不允许是 ""（整棵树）。
        value: 新值，可以是任意纯数据类型（含整棵子树）。
    输出：
        被替换掉的旧值（供求逆与日志使用）。
    异常：
        TypeError: state 不是 dict 或 path 不是字符串。
        PathSyntaxError / PathNotFoundError: 目标路径走不通（含路径里的列表下标越界）。
        StateShapeError: 目标的父容器不是 dict / list。
        StateConflictError: path 是根。
    说明（异常口径）：
        列表下标越界在"路径里"属于寻址失败，因此抛 PathNotFoundError；
        只有作为操作参数传入的下标越界（list_insert / list_remove）才是
        StateConflictError。完整口径见 errors.py 模块文档。
    变量：
        tokens: 路径段元组；
        parent: 目标的父容器（dict 或 list）；
        last_token: 最后一段；在 dict 里是键名，在 list 里是下标原文；
        index: 最后一段在 list 上解释出的下标。
    说明：
        允许把子树整体换成标量，也允许反向替换——Mod 有修改任意路径的自由
        （草案明确不设路径禁区），本模块只负责记账通道所需的读写原语。
    """
    _require_dict_root(state)
    tokens = parse(path)
    if len(tokens) == 0:
        raise StateConflictError(path, "不允许替换整棵树（根节点），只能修改根下面的值")

    parent = _resolve(state, tokens[:-1], path)
    last_token = tokens[-1]

    if isinstance(parent, dict):
        if last_token not in parent:
            raise PathNotFoundError(path, last_token)
        old_value = parent[last_token]
        parent[last_token] = value
        return old_value

    if isinstance(parent, list):
        index = _parse_list_index(last_token, path)
        if index >= len(parent):
            raise PathNotFoundError(path, last_token)
        old_value = parent[index]
        parent[index] = value
        return old_value

    raise StateShapeError(path, f"不能对 {type(parent).__name__} 使用下标或键")


def list_insert(state: dict, path: str, index: int, value: Any) -> None:
    """修改（列表）：在 path 指向的列表的 index 位置插入一个元素。

    输入：
        state: State 树根，必须是 dict。
        path: 指向 list 容器的路径。
        index: 插入位置，取值范围 0..列表当前长度；等于长度表示追加到末尾。
        value: 插入的元素值。
    输出：
        无；直接修改 state。
    异常：
        TypeError: state 不是 dict、path 不是字符串，或 index 不是 int（bool 也拒绝）。
        PathSyntaxError / PathNotFoundError: 列表路径走不通。
        StateShapeError: 目标不是 list。
        StateConflictError: 下标越界（index 是操作参数，与"路径里的下标越界"
            区分开，后者抛 PathNotFoundError；见 errors.py 模块文档）。
    变量：
        target: path 指向的容器，必须是 list。
    说明：
        列表增删元素属于"修改该列表"（D-34），不属于新增/删除操作，
        因此单独成函数，而不是复用 add_key / remove_key。
    """
    _require_dict_root(state)
    _require_int_index(index, path)

    target = _resolve(state, parse(path), path)
    if not isinstance(target, list):
        raise StateShapeError(path, f"列表插入要求目标是 list，实际是 {type(target).__name__}")
    if index < 0 or index > len(target):
        raise StateConflictError(path, f"列表下标 {index} 越界（当前长度 {len(target)}，允许 0..{len(target)}）")

    target.insert(index, value)


def list_remove(state: dict, path: str, index: int) -> Any:
    """修改（列表）：删除 path 指向的列表里 index 位置的元素，并返回它。

    输入：
        state: State 树根，必须是 dict。
        path: 指向 list 容器的路径。
        index: 删除位置，取值范围 0..列表长度-1。
    输出：
        被删除的元素值（供求逆与日志使用）。
    异常：
        TypeError: state 不是 dict、path 不是字符串，或 index 不是 int（bool 也拒绝）。
        PathSyntaxError / PathNotFoundError: 列表路径走不通。
        StateShapeError: 目标不是 list。
        StateConflictError: 下标越界（含对空列表删除）。index 是操作参数，
            与"路径里的下标越界"区分开，后者抛 PathNotFoundError；见 errors.py。
    变量：
        target: path 指向的容器，必须是 list。
    """
    _require_dict_root(state)
    _require_int_index(index, path)

    target = _resolve(state, parse(path), path)
    if not isinstance(target, list):
        raise StateShapeError(path, f"列表删除要求目标是 list，实际是 {type(target).__name__}")
    if index < 0 or index >= len(target):
        raise StateConflictError(path, f"列表下标 {index} 越界（当前长度 {len(target)}）")

    return target.pop(index)


def _require_dict_root(state: Any) -> None:
    """检查 State 树根必须是 dict。

    输入：
        state: 待检查的对象。
    输出：
        无；通过检查时直接返回。
    异常：
        TypeError: state 不是 dict。根类型由调用方决定，属于编程错误。
    变量：
        无。
    说明：
        "根是 dict"是 State 规范表示的一部分（见模块文档第 1 条）。
        先统一检查它，后续寻址就不必反复处理"根不是容器"这种特殊情况。
    """
    if not isinstance(state, dict):
        raise TypeError(f"State 根节点必须是 dict，实际是 {type(state).__name__}")


def _require_int_index(index: Any, path: str) -> None:
    """检查列表下标参数必须是 int（且不是 bool）。

    输入：
        index: 待检查的下标参数。
        path: 原始路径字符串，仅用于异常消息。
    输出：
        无；通过检查时直接返回。
    异常：
        TypeError: index 不是 int，或是 bool（bool 是 int 的子类但语义上不是下标）；
            消息里会带上 path，便于在日志里定位是哪次调用。
    变量：
        无。
    """
    if not isinstance(index, int) or isinstance(index, bool):
        raise TypeError(f"列表下标必须是 int，实际是 {type(index).__name__}（path={path!r}）")


def _parse_list_index(token: str, path: str) -> int:
    """把一段文本解释为列表下标（按 RFC 6901 的数组下标规则）。

    输入：
        token: 路径段原文。
        path: 原始路径字符串，仅用于异常消息。
    输出：
        非负整数下标。
    异常：
        PathSyntaxError: 既不是 "0"，也不是"非零开头的纯 ASCII 数字"；
            包含 "-"、前导零、Unicode 数字等情况都报错。
    变量：
        is_plain_digits: 判断 token 是否只由 ASCII 数字组成，
            避免阿拉伯-印度数字等 Unicode 数字被 int() 接受。
    说明：
        RFC 6901 中 "-" 表示"最后一个元素之后"的虚构位置；本引擎的列表插入
        用函数参数传下标，不需要该语义，因此这里直接视为非法写法。
    """
    is_plain_digits = token.isascii() and token.isdigit()
    if token == "0":
        return 0
    if is_plain_digits and not token.startswith("0"):
        return int(token)
    raise PathSyntaxError(path, f"{token!r} 不是合法的列表下标（不允许前导零、负号或 '-'）")


def _resolve(state: dict, tokens: Sequence[str], path: str) -> Any:
    """按路径段逐段向下走，返回 tokens 指向的值。

    输入：
        state: 已确认是 dict 的树根。
        tokens: parse 得到的路径段元组；空元组表示根自身。
        path: 原始路径字符串，仅用于异常消息。
    输出：
        最终指向的值，可能是容器，也可能是标量。
    异常：
        PathNotFoundError: 字典里没有该键，或列表下标越界。
        StateShapeError: 中途遇到标量，无法继续向下寻址。
        PathSyntaxError: 对列表使用了不合法的下标写法。
    变量：
        node: 当前走到的那一层容器或值；
        token: 当前处理的段原文；
        index: token 在 list 上解释出的下标。
    """
    node: Any = state
    for token in tokens:
        if isinstance(node, dict):
            if token not in node:
                raise PathNotFoundError(path, token)
            node = node[token]
            continue

        if isinstance(node, list):
            index = _parse_list_index(token, path)
            if index >= len(node):
                raise PathNotFoundError(path, token)
            node = node[index]
            continue

        raise StateShapeError(path, f"{type(node).__name__} 不是容器，无法继续向下寻址")
    return node
