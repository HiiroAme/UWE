"""delta 包的异常类型。

位置：
    引擎核心逻辑层 → delta 子包。
职责：
    定义"变化量自身不合法"的异常：字段组合矛盾、路径写法不合法、
    序列化数据缺字段/多字段/版本不符。

为什么单独成文件（规范第 3 条的解耦评估结论）：
    model / codec / apply / merge 四个模块都要抛同一种异常，集中在一个文件里
    可以避免它们互相 import 形成环，也让上层（将来的存档、回放）只需要
    import 一个位置就能捕获全部格式问题。

与 state 层异常的分工：
    DeltaError 管"变化量记录本身"的问题，通常意味着代码 bug 或坏存档；
    变化量应用到树时遇到的路径/冲突问题，仍然抛 state 层的异常
    （PathNotFoundError / StateShapeError / StateConflictError），见 apply.py。
"""


class DeltaError(ValueError):
    """变化量的字段组合或序列化数据不合法。

    功能：
        作为 delta 包唯一的异常类型，让调用方一次捕获全部格式问题。

    为什么继承 ValueError：
        构造一条自相矛盾的变化量属于编程错误，符合 Python 里"值不合法用
        ValueError"的惯例；从存档里读出的坏数据由上层统一按 ValueError 处理。

    属性（与 state 层的 StateError 保持同一套读法）：
        detail: 人可读的说明文字；
        path: 出问题的变化量路径；不知道时是空字符串。

    消息约定：
        抛出时消息里必须写清是哪个字段、期望什么、实际是什么，
        便于日志定位（§19.1 的分层日志要求）。
    """

    def __init__(self, detail: str, path: str = "") -> None:
        """初始化异常。

        输入：
            detail: 人可读的说明文字；
            path: 出问题的变化量路径；不知道时传空字符串。
        输出：
            无（构造异常对象本身）。
        变量：
            message: 最终消息；path 非空时拼在 detail 后面，便于直接阅读日志。
        """
        self.detail = detail
        self.path = path
        message = f"{detail}（path={path!r}）" if path else detail
        super().__init__(message)
