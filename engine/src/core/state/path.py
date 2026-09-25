"""State 树路径的解析与格式化（对齐 JSON Pointer，RFC 6901）。

位置：
    引擎核心逻辑层 → state 子包。
职责：
    只做"路径字符串 ↔ 路径段"的转换与转义处理，不接触任何 State 数据。
    某个段是字典键还是列表下标，由 tree.py 按父容器的实际类型解释。

草案依据：
    §6.3 索引用 "/" 分隔、转义符 "~"、对齐 JSON Pointer（RFC 6901）；
    §13  遍历必须走有序容器，保证确定性。

路径约定（严格 RFC 6901）：
    ""       表示整棵树（根）；
    "/a/b"   表示根下的 a 再下的 b；
    "/"      表示根下键名为空字符串的那个键；
    "~0"     表示字面量 "~"；"~1" 表示字面量 "/"；
    "~2"、"~" 结尾等非法转义直接报错。
"""

from typing import Sequence

from .errors import PathSyntaxError

# 反转义查表：键是 "~" 后面的那一个字符，值是它代表的字面量字符。
_UNESCAPE_PAIRS = {"0": "~", "1": "/"}


def parse(path: str) -> tuple[str, ...]:
    """把路径字符串解析成路径段元组。

    输入：
        path: 路径字符串。空字符串 "" 表示整棵树的根；其余必须严格以 "/" 开头。
    输出：
        元组，每个元素是一段**已反转义但未解释**的字符串。
        例如 "/entities/units/0" 得到 ("entities", "units", "0")，
        其中 "0" 是字典键还是列表下标，由 tree.py 按父容器类型决定。
    异常：
        TypeError: path 不是字符串。
        PathSyntaxError: 既不是空串也不以 "/" 开头；含非法转义。
            异常对象的 path 字段始终是**完整路径**，供日志按路径溯源（§19.1）。
    变量：
        raw_tokens: 按 "/" 切分后的原始段（尚未反转义）。
            split 结果的第一项是第一个 "/" 之前的部分，非空路径时恒为空串，
            因此从下标 1 开始取；"/" 本身会得到 [""]，表示空字符串键。
    """
    if not isinstance(path, str):
        raise TypeError(f"路径必须是字符串，实际是 {type(path).__name__}")
    if path == "":
        return ()
    if not path.startswith("/"):
        raise PathSyntaxError(path, "路径要么是空字符串（根），要么必须以 '/' 开头")

    raw_tokens = path.split("/")[1:]
    try:
        return tuple(unescape_token(token) for token in raw_tokens)
    except PathSyntaxError as exc:
        # unescape_token 只认识单个段，它抛出时 path 字段是那一段；
        # 这里换成完整路径，保证 StateError.path 的契约（日志按完整路径检索）。
        raise PathSyntaxError(path, exc.detail) from exc


def unescape_token(token: str) -> str:
    """把一段路径文本从 JSON Pointer 转义形式还原成字面量。

    输入：
        token: 段原文，可能含有 "~0" 与 "~1"。
    输出：
        还原后的字符串："~0" → "~"，"~1" → "/"，其余字符原样保留。
    异常：
        TypeError: token 不是字符串。
        PathSyntaxError: "~" 出现在段末尾，或 "~" 后面不是 "0" / "1"。
            注意：本函数不知道完整路径，所以异常的 path 字段就是这一段文本；
            parse() 会捕获它并替换成完整路径，直接调用本函数时才看到段文本。
    变量：
        result: 逐段累积的结果列表；
        index: 当前扫描到的字符下标；
        follower: "~" 后面的那个字符，只允许是 "0" 或 "1"。
    说明：
        这里手写扫描而不是连续 replace：既能顺便报出非法转义，
        又能保证 "~01" 正确还原为 "~1"（先还原 "~0"，后面的 "1" 是普通字符）。
    """
    if not isinstance(token, str):
        raise TypeError(f"路径段必须是字符串，实际是 {type(token).__name__}")

    result: list[str] = []
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            result.append(char)
            index += 1
            continue

        if index + 1 >= len(token):
            raise PathSyntaxError(token, "转义符 '~' 不能出现在段末尾")
        follower = token[index + 1]
        if follower not in _UNESCAPE_PAIRS:
            raise PathSyntaxError(token, f"非法转义 '~{follower}'，只允许 '~0' 与 '~1'")
        result.append(_UNESCAPE_PAIRS[follower])
        index += 2
    return "".join(result)


def escape_token(token: str) -> str:
    """把一段字面量编码成 JSON Pointer 的转义形式。

    输入：
        token: 字面量文本，可以包含 "~" 与 "/"。
    输出：
        转义后的文本，可直接拼进路径字符串（"~" → "~0"，"/" → "~1"）。
    异常：
        TypeError: token 不是字符串。
    变量：
        无。
    说明：
        两次 replace 的顺序不能反：必须先换 "~" 再换 "/"，
        否则会把 "/" 换出来的 "~1" 里的 "~" 再次转义成 "~01"。
    """
    if not isinstance(token, str):
        raise TypeError(f"路径段必须是字符串，实际是 {type(token).__name__}")
    return token.replace("~", "~0").replace("/", "~1")


def format_path(segments: Sequence[str]) -> str:
    """把路径段序列拼回 JSON Pointer 字符串（parse 的逆运算）。

    输入：
        segments: 每段都是字面量字符串的序列；空序列表示根。
    输出：
        空序列返回 ""；否则返回以 "/" 开头、各段已转义并以 "/" 连接的字符串。
    异常：
        TypeError: 序列里出现非字符串的段。
    变量：
        escaped_parts: 逐段转义后的结果列表，最后用 "/" 连接。
    """
    if len(segments) == 0:
        return ""

    escaped_parts: list[str] = []
    for token in segments:
        if not isinstance(token, str):
            raise TypeError(f"路径段必须是字符串，实际是 {type(token).__name__}")
        escaped_parts.append(escape_token(token))
    return "/" + "/".join(escaped_parts)
