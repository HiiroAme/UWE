"""state 包的异常类型。

位置：
    引擎核心逻辑层 → state 子包。
职责：
    定义 State 树寻址与读写过程中的失败情况，供本包的 path / tree 以及后续的
    使用者（变化量引擎、临时状态视图、提交逻辑）区分并处理。

为什么单独成文件（规范第 3 条的解耦评估结论）：
    异常是 state 包的对外契约之一。集中在一个文件里，path.py 与 tree.py 都只
    依赖它、彼此不依赖，避免循环 import；上层也只需要 import 一个位置。

草案依据：
    §6.2 State 是一棵纯数据树；§6.3 路径对齐 JSON Pointer（RFC 6901）；
    D-14 引擎运行时不校验 Mod 数据，但结构性问题必须能被上层区分并处理。

异常与失败原因的对应关系（本表是 state 包的唯一权威口径）：
    PathSyntaxError    路径字符串写法错（与数据无关，属于调用方的写法问题）；
    PathNotFoundError  路径寻址失败：数据里没有这一级（键不存在、路径里的下标越界）；
    StateShapeError    地址存在，但类型不支持该操作，或无法继续向下寻址；
    StateConflictError 地址没问题，操作参数与数据冲突
                       （键已存在/不存在、参数形式的下标越界、替换整棵树）；
    TypeError          参数类型错误（路径不是 str、键名不是 str、下标不是 int），
                       属于调用方的编程错误，不是数据问题。

"路径"与"操作参数"的分界（有意如此，不是不统一）：
    路径负责寻址，参数负责动作。
    - 下标写在路径里（get / exists / replace 的 "/nodes/3"）：越界是寻址失败
      → PathNotFoundError；
    - 下标作为函数参数传入（list_insert / list_remove 的 index）：越界是动作前提
      与当前数据对不上 → StateConflictError。
    这与变化量的两种形态一一对应：修改类变化量用路径寻址；新增/删除/列表增删类
    变化量把键名或下标放在数据里当参数。两类的失败信息不同，上层不必再猜。

两个边角：
    - 负数下标："-1" 写在路径里是 PathSyntaxError（RFC 6901 没有负数下标）；
      作为参数传入是 StateConflictError（合法 int，但插入/删除位置不允许）。
    - exists() 是唯一把"找不到"和"形状不允许"都压平成 False 的探测接口，
      它不会抛 PathNotFoundError；需要区分失败原因时用 get 或其他写操作。
"""


class StateError(Exception):
    """state 包所有异常的基类。

    功能：
        让上层用一次 `except StateError` 捕获本包抛出的全部结构性异常；
        具体子类用于区分失败原因，便于日志与上层分支处理。

    属性：
        path: 出错时使用的路径字符串；根路径是空字符串 ""。
        detail: 人可读的补充说明，供日志与调试使用。
    """

    def __init__(self, path: str, detail: str = "") -> None:
        """初始化异常。

        输入：
            path: 出错时的路径字符串；根节点用空字符串表示。
            detail: 补充说明文字；可以为空。
        输出：
            无（构造异常对象本身）。
        变量：
            message: 最终消息文本；path 与 detail 同时保留为独立属性，便于日志检索。
        """
        self.path = path
        self.detail = detail
        message = f"{detail}（path={path!r}）" if detail else f"path={path!r}"
        super().__init__(message)


class PathSyntaxError(StateError):
    """路径字符串不符合 JSON Pointer（RFC 6901）语法。

    典型情形：
        - 路径既不是空字符串，也不以 "/" 开头；
        - 出现 "~2"、结尾单独的 "~" 这类非法转义；
        - 对列表使用 "01"、"-1"、"-" 这类不合法的下标写法。
    """


class PathNotFoundError(StateError):
    """路径在 State 树里走不通：某一段在数据中不存在。

    适用范围：
        只表示**寻址失败**（键不存在、路径里的下标越界）。由操作参数
        （键名、参数下标）造成的失败属于 StateConflictError，见本模块文档。

    为什么要与"值是 None"区分：
        None 是合法的游戏数据值，读取它必须成功返回 None；
        路径不存在说明 Mod 数据或调用方有问题，必须报错并被上层记入日志。

    属性：
        failed_token: 第一段走不通的**反转义后**的段文本。例如路径写
            "/a~1b/x" 时它是 "a/b" 而不是 "a~1b"；需要还原成路径写法时
            用 path.escape_token() 重新转义。
    """

    def __init__(self, path: str, failed_token: str) -> None:
        """初始化异常。

        输入：
            path: 完整路径字符串。
            failed_token: 第一段走不通的反转义后段文本。
        输出：
            无。
        变量：
            无。
        """
        self.failed_token = failed_token
        super().__init__(path, f"路径不存在：在 {failed_token!r} 段走不通")


class StateShapeError(StateError):
    """路径走到了某个值，但该值的类型不允许继续操作。

    典型情形：
        - State 根节点不是 dict；
        - 对字符串、数字、布尔这些标量继续向下寻址；
        - 新增/删除的目标不是 dict，列表插入/删除的目标不是 list。
    """


class StateConflictError(StateError):
    """地址没问题，但操作参数与当前数据冲突。

    典型情形（都由操作参数引起）：
        - 新增时键已经存在；
        - 删除时键不存在；
        - list_insert / list_remove 的参数下标越界（含负数）；
        - 试图替换整棵 State（根节点）。

    不属于这里的情形：
        下标写在**路径里**造成的越界属于寻址失败，抛 PathNotFoundError。
        两者为什么要分开，见本模块文档的"路径与操作参数的分界"。

    说明：
        这是"操作与数据对不上"的错误，不是语法错误；引擎不做自动纠正（D-14）。
    """
