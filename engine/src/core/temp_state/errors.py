"""temp_state 包的异常类型。

位置：
    引擎核心逻辑层 → temp_state 子包。
职责：
    定义"视图这个对象本身被用错了"的异常：在终态上继续读写、提交到错误的树等。

与 state / delta 异常的分工：
    视图里的**数据问题**（路径走不通、类型不符、旧值不一致）仍然抛 state / delta
    层的异常；本文件只管生命周期与使用方式这一类错误。
"""


class TempStateError(RuntimeError):
    """临时状态视图的使用方式错误。

    典型情形：
        - 视图已经提交 / 丢弃 / 提交失败，仍然调用读、写、提交、丢弃；
        - 提交时传入的不是创建视图时的那个真实 State 对象。

    属性：
        detail: 人可读的说明文字。
    """

    def __init__(self, detail: str) -> None:
        """初始化异常。

        输入：
            detail: 人可读的说明文字。
        输出：
            无（构造异常对象本身）。
        变量：
            无。
        """
        self.detail = detail
        super().__init__(detail)
