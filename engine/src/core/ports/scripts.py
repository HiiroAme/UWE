"""脚本加载端口（P7）：把"脚本文件里的某个函数"取出来。

位置：
    引擎核心逻辑层 → ports 子包。

职责：
    定义"给定脚本路径与函数名，给我一个可调用对象"这件事的契约。
    真正读文件、import 模块、取函数的是适配器（adapters.PythonScriptLoader）。

为什么核心不自己 import：
    - import 是平台相关行为（文件系统、模块搜索路径、字节码缓存），属于适配器的事（P7）；
    - 核心只依赖这个抽象，将来换成"打包进 exe 的脚本"或"从压缩包读取"只需换适配器。

草案依据：
    §14.2 service 注册表"引用脚本用路径或名字，不把代码嵌进条目"（P2）；
    §15.2 Mod 提供脚本；§16 脚本经引擎接口产生变化量；P7 端口与适配器。
"""

from typing import Protocol


class ScriptNotAvailableError(Exception):
    """取不到脚本函数（文件缺失 / 语法错 / 名字写错 / 不是可调用对象）。

    字段：
        detail: 说明文字；
        script_path: 脚本路径。
    """

    def __init__(self, detail: str, *, script_path: str = "") -> None:
        """构造异常。

        输入：
            detail: 说明文字；
            script_path: 脚本路径。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self.detail = detail
        self.script_path = script_path
        super().__init__(
            f"ScriptNotAvailableError | 脚本={script_path} | {detail}" if script_path else detail
        )


class ScriptLoader(Protocol):
    """脚本加载端口。"""

    def load(self, script_path: str, callable_name: str) -> object:
        """取出脚本文件里的一个可调用对象。

        输入：
            script_path: 脚本文件路径（具体写法由适配器解释）；
            callable_name: 函数名。
        输出：
            可调用对象（调用契约见 core.ports.ServiceCallable）。
        异常：
            ScriptNotAvailableError: 文件读不到、导入失败、或里面没有这个名字。
        """
