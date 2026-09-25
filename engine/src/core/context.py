"""Context：不存档的运行时数据（§6.1）。

位置：
    引擎核心逻辑层。由 EngineRuntime 持有，随"进入游戏 → 退出"存活。

职责：
    放**不影响规则判定**的运行时数据：动画进度、界面用的临时缓存、
    Service 自己算了一半的中间量等等。这类数据存档时完全丢掉也不会改变玩法。

判据（D-11，必须记住）：
    这个量会不会影响规则的判定结果？
        会  → 必须进 State（否则读档后行为与读档前不一致）；
        不会 → 才可以放 Context。

边界：
    - 不入存档（§18.1 的存档结构里没有它）；
    - 不参与确定性契约：读档后 Context 是空的，游戏行为仍必须一致；
    - 本模块只做存取，不做类型校验、不解释内容（P2：这里放的是运行时对象，
      不是 Mod 数据包）。

草案依据：
    §6.1 三种状态的分工（State / Context / 页面 UI 状态）与判据；
    §5 引擎运行时对象（context 常驻、不入存档）；
    D-11 判据：是否影响规则判定结果。
"""

from typing import Any, Iterator


class Context:
    """不存档的键值容器（按名字存取）。

    字段：
        _values: 名字 → 值；名字必须是字符串，值可以是任意 Python 对象。
    """

    def __init__(self) -> None:
        """创建一个空的 Context。

        输入：无。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self._values: dict[str, Any] = {}

    def put(self, name: str, value: Any) -> None:
        """写入一个值（同名覆盖）。

        输入：
            name: 名字（非空字符串）；
            value: 任意对象。
        输出：
            无。
        异常：
            TypeError: name 不是字符串；
            ValueError: name 是空字符串。
        变量：
            无。
        """
        _require_name(name)
        self._values[name] = value

    def get(self, name: str, default: Any = None) -> Any:
        """读一个值。

        输入：
            name: 名字（非空字符串）；
            default: 没有这个名字时返回的值，缺省 None。
        输出：
            存进去的值，或 default。
        异常：
            TypeError / ValueError: name 不合法。
        变量：
            无。

        说明：
            默认值 None 与"存进去的就是 None"无法区分，需要区分时用 has()。
        """
        _require_name(name)
        return self._values.get(name, default)

    def has(self, name: str) -> bool:
        """判断名字是否存在（值就是 None 也算存在）。

        输入：
            name: 名字。
        输出：
            True / False。
        异常：
            TypeError / ValueError: name 不合法。
        变量：
            无。
        """
        _require_name(name)
        return name in self._values

    def remove(self, name: str) -> None:
        """删掉一个名字（不存在时什么都不做）。

        输入：
            name: 名字。
        输出：
            无。
        异常：
            TypeError / ValueError: name 不合法。
        变量：
            无。
        """
        _require_name(name)
        self._values.pop(name, None)

    def names(self) -> tuple[str, ...]:
        """返回全部名字（写入顺序，便于日志阅读）。

        输入：无。
        输出：
            名字元组。
        异常：
            无。
        变量：
            无。
        """
        return tuple(self._values.keys())

    def clear(self) -> None:
        """清空全部内容（切换 Mod / 退出游戏时用）。"""
        self._values.clear()

    def __len__(self) -> int:
        """返回已存的名字数量。"""
        return len(self._values)

    def __iter__(self) -> Iterator[str]:
        """按写入顺序迭代名字。"""
        return iter(self._values.keys())

    def __repr__(self) -> str:
        """返回便于调试的短描述。"""
        return f"Context(names={len(self._values)})"


def _require_name(name: Any) -> None:
    """要求名字是非空字符串。

    输入：
        name: 待检查的名字。
    输出：
        无；通过时直接返回。
    异常：
        TypeError: name 不是字符串；
        ValueError: name 是空字符串。
    变量：
        无。
    """
    if not isinstance(name, str):
        raise TypeError(f"Context 的名字必须是字符串，实际是 {type(name).__name__}")
    if not name:
        raise ValueError("Context 的名字不能是空字符串")
