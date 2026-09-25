"""路径通配：一条路径覆盖"很多条具体路径"（F-05 的落地）。

位置：
    引擎核心逻辑层 → state 子包（它只处理路径与树的匹配，不涉及任何玩法）。

为什么要它：
    派生值（§6.5）与模板实例化都逃不开"一批同类的东西"，而 State 树是 Mod 设计的
    （D-09）——引擎不能假设"单位在哪"，但可以让 Mod 写一条**带通配的路径**：

        /units/{unit}/power       ← 写一条公式，覆盖所有单位
        /nodes/*/cover            ← 不关心叫什么名字，只关心这一层

通配写法（在 JSON Pointer 之上加一层，规则保持简单）：
    *            匹配任意一层（**不捕获**）；
    {名字}        匹配任意一层，并把那一层"是什么"绑到名字上（捕获）；
    其余段        就是原来的字面量（含 `~0` / `~1` 转义）。
    捕获到的名字会作为**本次求值的参数**（表达式里用 ["arg", 名字] 取；
    字典键是字符串，列表下标是整数）。

枚举顺序（§13 确定性）：
    同一层里，字典按键名排序、列表按下标升序；逐层深入。
    所以同一棵树、同一个通配路径，永远展开出同一个顺序。

边界：
    - 只支持"单层"通配，不支持 `**` 递归通配（现在用不到，P8）；
    - 通配路径**只能匹配已经存在的路径**：展开是"枚举现有节点"，不会凭空空造出新条目
      （新实例的初始值来自模板，见 core/templates.py）；
    - 本模块只管匹配与展开，不管"谁该用通配"（那是编译期与调用方的事）。

草案依据：
    §22 F-05 路径通配 / 批量操作；§6.5 派生值（一条公式覆盖多实例的需要）；
    D-09 State 单树（引擎不预设结构，所以只能由 Mod 给出带通配的路径）；
    §13 遍历一律走有序容器（枚举顺序必须确定）。
"""

from dataclasses import dataclass
from typing import Any

from .errors import PathSyntaxError
from .path import format_path, parse

# 通配段的两种写法：单层任意（*）与单层捕获（{名字}）。
ANY_SEGMENT = "*"


@dataclass(frozen=True)
class Match:
    """一次展开的结果：具体路径 + 捕获到的名字。

    字段：
        path: 展开出来的具体路径（可以直接当普通路径用）；
        bindings: 捕获名 → 这一层的键（字典键是字符串，列表下标是整数）。
    """

    path: str
    bindings: dict


def is_pattern(path: str) -> bool:
    """判断一条路径里有没有通配段。

    输入：
        path: 路径字符串。
    输出：
        True 表示含 `*` 或 `{名字}` 段。
    异常：
        TypeError: path 不是字符串。
    变量：
        token: 遍历到的段。
    """
    if not isinstance(path, str):
        raise TypeError(f"路径必须是字符串，实际是 {type(path).__name__}")
    for token in parse(path):
        if token == ANY_SEGMENT or _capture_name(token) is not None:
            return True
    return False


def validate_pattern(path: str) -> None:
    """检查一条通配路径的写法（编译期用）。

    输入：
        path: 路径字符串（可以是普通路径）。
    输出：
        无；写法合法时直接返回。
    异常：
        PathSyntaxError: 路径本身不合法，或捕获段写法不对（`{` / `}` 不配对、空名字、
            同一个名字出现两次）。
    变量：
        seen: 已经出现过的捕获名（同一个名字出现两次没法保证两次都拿到同样的值）。
    """
    tokens = parse(path)
    seen: set = set()
    for token in tokens:
        if token == ANY_SEGMENT:
            continue
        name = _capture_name(token)
        if name is None:
            if "{" in token or "}" in token:
                raise PathSyntaxError(path, f"通配段写法不对：{token!r}（应写成 {{名字}} 或 *）")
            continue
        if not name:
            raise PathSyntaxError(path, "通配捕获的名字不能是空的（应写成 {名字}）")
        if name in seen:
            raise PathSyntaxError(path, f"同一个捕获名出现了两次：{name!r}")
        seen.add(name)


def match(pattern_path: str, concrete_path: str) -> dict | None:
    """判断一条具体路径是否匹配通配路径，并给出捕获。

    输入：
        pattern_path: 通配路径（也可以是普通路径）；
        concrete_path: 具体路径。
    输出：
        捕获字典（没有捕获时是空字典）；不匹配时是 None。
    异常：
        PathSyntaxError: 两条路径的段数不同（长度不一样就不可能匹配）。
    变量：
        pattern_tokens / concrete_tokens / index / name: 逐段比对的中间结果。
    """
    pattern_tokens = parse(pattern_path)
    concrete_tokens = parse(concrete_path)
    if len(pattern_tokens) != len(concrete_tokens):
        return None
    bindings: dict = {}
    for index, token in enumerate(pattern_tokens):
        actual = concrete_tokens[index]
        if token == ANY_SEGMENT:
            continue
        name = _capture_name(token)
        if name is not None:
            if name in bindings and str(bindings[name]) != actual:
                return None  # 同一个名字必须落到同一个键上
            bindings[name] = _as_binding(actual)
            continue
        if token != actual:
            return None
    return bindings


def patterns_overlap(left: str, right: str) -> bool:
    """判断两条路径（可以都是通配）有没有可能指向同一处。

    输入：
        left / right: 路径字符串。
    输出：
        True 表示"有可能重合"（保守判断：不确定时按重合处理）。
    异常：
        无（段数不同时直接返回 False）。
    变量：
        left_tokens / right_tokens / index / a / b: 逐段比对的中间结果。

    用途：
        派生值的加载期检查：两条公式如果可能写同一条路径，就要拦下来。
    """
    left_tokens = parse(left)
    right_tokens = parse(right)
    if len(left_tokens) != len(right_tokens):
        return False
    for index in range(len(left_tokens)):
        a = left_tokens[index]
        b = right_tokens[index]
        if a == ANY_SEGMENT or b == ANY_SEGMENT:
            continue
        if _capture_name(a) is not None or _capture_name(b) is not None:
            continue
        if a != b:
            return False
    return True


def substitute(pattern_path: str, bindings: dict) -> str:
    """用捕获值把通配路径还原成具体路径。

    输入：
        pattern_path: 通配路径（不含 `*` 段）；
        bindings: 捕获名 → 值。
    输出：
        具体路径字符串。
    异常：
        TypeError: bindings 不是 dict；
        KeyError: 某个捕获名没有给值；
        PathSyntaxError: 路径里还有没名字的通配段（`*`）。
    变量：
        token / name: 遍历到的段与它的捕获名。
    """
    if not isinstance(bindings, dict):
        raise TypeError(f"bindings 必须是 dict，实际是 {type(bindings).__name__}")
    tokens = []
    for token in parse(pattern_path):
        if token == ANY_SEGMENT:
            raise PathSyntaxError(pattern_path, "路径里有不捕获的通配段（*），没法还原成具体路径")
        name = _capture_name(token)
        if name is None:
            tokens.append(token)
            continue
        if name not in bindings:
            raise KeyError(f"捕获名 {name!r} 没有给值")
        tokens.append(str(bindings[name]))
    return format_path(tokens)


def expand(state: dict, pattern_path: str) -> tuple[Match, ...]:
    """在一棵树上展开通配路径，列出全部匹配（顺序确定）。

    输入：
        state: State 树根（dict）；
        pattern_path: 通配路径；没有通配段时，存在就给一条匹配，不存在就给空元组。
    输出：
        Match 元组（可能为空）：字典按键名排序、列表按下标升序逐层展开。
    异常：
        TypeError: state 不是 dict；
        PathSyntaxError: 路径写法不合法。
    变量：
        tokens: 模式段；
        results: 收集结果的列表。
    """
    if not isinstance(state, dict):
        raise TypeError(f"展开通配路径需要 dict 作为树根，实际是 {type(state).__name__}")
    tokens = parse(pattern_path)
    results: list = []
    _walk(state, tokens, 0, (), {}, pattern_path, results)
    return tuple(results)


def _walk(node: Any, tokens, index: int, prefix, bindings: dict, pattern_path: str, results: list) -> None:
    """递归展开（expand 的内部实现）。

    输入：
        node: 当前节点；
        tokens: 模式段；
        index: 当前段下标；
        prefix: 已经走过的段（元组）；
        bindings: 目前捕获到的名字；
        pattern_path: 原路径（报错用）；
        results: 收集结果。
    输出：
        无。
    异常：
        无（走不通的分支直接跳过）。
    变量：
        token / name / child_keys / key / child: 遍历的中间结果。
    """
    if index >= len(tokens):
        results.append(Match(path=format_path(prefix), bindings=dict(bindings)))
        return
    token = tokens[index]
    name = _capture_name(token)
    if token == ANY_SEGMENT or name is not None:
        for key in _child_keys(node):
            new_bindings = dict(bindings)
            if name is not None:
                new_bindings[name] = key
            _walk(_child(node, key), tokens, index + 1, prefix + (str(key),), new_bindings, pattern_path, results)
        return
    child = _child(node, token)
    if child is _MISSING:
        return
    _walk(child, tokens, index + 1, prefix + (token,), bindings, pattern_path, results)


class _Missing:
    """哨兵：表示"这一层不存在"（与"值是 None"区分开）。"""


_MISSING = _Missing()


def _child(node: Any, key: Any) -> Any:
    """按一段取子节点；取不到时返回 _MISSING。

    输入：
        node: 当前节点；
        key: 这一段（字典的键是字符串；列表的下标既可能是 int，也可能是路径里写的数字字符串）。
    输出：
        子节点，或 _MISSING。
    异常：
        无。
    变量：
        position: 解析出来的列表下标。
    """
    if isinstance(node, dict):
        return node[key] if isinstance(key, str) and key in node else _MISSING
    if isinstance(node, list):
        if isinstance(key, bool):
            return _MISSING
        if isinstance(key, int):
            position = key
        elif isinstance(key, str) and key.isdigit():
            position = int(key)
        else:
            return _MISSING
        if 0 <= position < len(node):
            return node[position]
        return _MISSING
    return _MISSING


def _child_keys(node: Any) -> tuple:
    """给出一层的全部子键（字典按键排序、列表按下标升序）。"""
    if isinstance(node, dict):
        return tuple(sorted(node.keys()))
    if isinstance(node, list):
        return tuple(range(len(node)))
    return ()


def _capture_name(token: str) -> str | None:
    """把 `{名字}` 段取出名字；不是捕获段时返回 None。"""
    if len(token) >= 2 and token.startswith("{") and token.endswith("}"):
        return token[1:-1]
    return None


def _as_binding(token: str) -> Any:
    """把具体路径的一段转成绑定值：纯数字段按整数给（列表下标），其余按字符串。"""
    if token.isdigit():
        return int(token)
    return token
