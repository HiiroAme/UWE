"""内容编译的异常（core.content 专用）。

位置：
    引擎核心逻辑层 → content 子包。

职责：
    区分"注册表条目的格式问题"（registry 层的错）与"条目里写的内容不成立"
    （本层的错：分支表不是列表、引用不到 action、condition 缺失……）。

草案依据：
    §9 管线第 2、5、6、9 步（input / command / action / rule / trigger 的内容）；
    §19.2 加载失败属于"记录并停止"的错误；
    D-29 载入时的静态检查（Mod 给多少检查多少——本层只检查"引擎自己要用到的结构"）。
"""


class ContentError(Exception):
    """内容编译失败的基类。

    字段：
        detail: 说明文字；
        entry_id: 出问题的条目 id（可能为空）；
        location: 位置串（如 "my_mod:command:move → branches[0].actions[1]"），便于定位。
    """

    def __init__(self, detail: str, *, entry_id: str = "", location: str = "") -> None:
        """构造异常。

        输入：
            detail: 说明文字；
            entry_id: 相关条目 id；
            location: 位置串。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self.detail: str = detail
        self.entry_id: str = entry_id
        self.location: str = location
        super().__init__(self.__str__())

    def __str__(self) -> str:
        """拼出便于日志阅读的完整消息。"""
        parts = [self.__class__.__name__]
        if self.entry_id:
            parts.append(f"条目={self.entry_id}")
        if self.location:
            parts.append(f"位置={self.location}")
        parts.append(self.detail)
        return " | ".join(parts)


class ContentFormatError(ContentError):
    """条目内容的结构不符合引擎约定的写法（缺字段、类型不对、取值不合法）。"""


class ContentReferenceError(ContentError):
    """条目里引用了不存在 / 已禁用 / 类型不对的目标（action、rule、service、event）。"""
