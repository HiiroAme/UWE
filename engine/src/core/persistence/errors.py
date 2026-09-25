"""存档相关的异常（core.persistence 专用）。

位置：
    引擎核心逻辑层 → persistence 子包。

职责：
    区分"存档结构读不懂"与"版本对不上"两类失败：
        读不懂（缺字段、类型不对、JSON 不是对象）→ SaveFormatError；
        版本对不上（存档格式 / 变化量格式 / 条目 schema / Mod 版本）→ SaveVersionError。
    两者都不做任何"猜一猜、试一试"的兼容处理：按 D-25 直接拒绝加载。

草案依据：
    §18.1 存档结构与版本字段；D-25 版本不匹配直接报错拒绝加载；
    D-45 变化量记录自带格式版本，版本不符直接拒绝。
"""


class SaveError(Exception):
    """存档相关失败的基类。

    字段：
        detail: 说明文字；
        path: 出问题的存档路径（可能为空，例如内存里构造的存档）；
    """

    def __init__(self, detail: str, *, path: str = "") -> None:
        """构造异常。

        输入：
            detail: 说明文字；
            path: 存档路径。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self.detail = detail
        self.path = path
        super().__init__(self.__str__())

    def __str__(self) -> str:
        """拼出"类型 + 存档 + 说明"的完整消息。"""
        parts = [self.__class__.__name__]
        if self.path:
            parts.append(f"存档={self.path}")
        parts.append(self.detail)
        return " | ".join(parts)


class SaveFormatError(SaveError):
    """存档结构不合法（不是对象、缺必填字段、字段类型不对、值不是纯 JSON 数据）。"""


class SaveVersionError(SaveError):
    """版本对不上：引擎版本 / 存档格式 / 变化量格式 / 条目 schema / Mod 版本。

    字段（除基类外）：
        expected: 期望的值（当前引擎这一侧）；
        actual: 存档里写的值。
    """

    def __init__(self, detail: str, *, expected: object = None, actual: object = None, path: str = "") -> None:
        """构造异常。

        输入：
            detail: 说明文字；
            expected / actual: 期望值与实际值（用于日志与报错）；
            path: 存档路径。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self.expected = expected
        self.actual = actual
        super().__init__(detail, path=path)
