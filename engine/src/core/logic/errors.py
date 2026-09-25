"""logic 包的异常类型。

位置：
    引擎核心逻辑层 → logic 子包。
职责：
    定义"表达式不合法 / 求值失败"的唯一异常 LogicError；本包其它文件都只抛它。

与其它包异常的分工：
    state / delta / temp_state 的异常管"数据与生命周期"；
    LogicError 管"表达式本身"——结构写错、操作符不认识、参数个数不对、类型对不上、
    路径读不到、外部函数不存在。表达式在注册表里的位置（哪一条、哪个字段）由调用方
    补上，本包只带表达式内部的位置。

位置（location）写法：
    与项目其它地方一致，用 JSON Pointer 风格，数字表示"第几个参数"：
        ""      整个表达式
        "/0"    根操作符的第 0 个参数
        "/0/1"  第 0 个参数的第 1 个参数
    只数参数，不含操作符名本身。
"""


class LogicError(Exception):
    """表达式不合法或求值失败。

    功能：
        让调用方（Orchestrator / Rule / 加载期检查）用一个 except 捕获全部表达式问题。

    属性：
        detail: 人可读的说明文字（哪一步失败、期望什么、实际是什么）。
        location: 表达式内部位置；整条表达式用空字符串。
    """

    def __init__(self, detail: str, location: str = "") -> None:
        """初始化异常。

        输入：
            detail: 说明文字；
            location: 表达式内部位置，默认空字符串。
        输出：
            无（构造异常对象本身）。
        变量：
            message: 最终消息；location 非空时拼在 detail 后面，日志可直接读。
        """
        self.detail = detail
        self.location = location
        message = f"{detail}（位置 {location}）" if location else detail
        super().__init__(message)

    def at(self, location: str) -> "LogicError":
        """补上表达式位置后返回异常。

        输入：
            location: 表达式内部位置。
        输出：
            已经有位置时返回自身，否则返回带位置的新异常。
        说明：
            求值器捕获底层操作抛出的异常时用它补位置；已经有位置的不再覆盖，
            保证最深的位置优先。
        """
        if self.location:
            return self
        return LogicError(self.detail, location)

