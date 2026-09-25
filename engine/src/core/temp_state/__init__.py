"""temp_state 包：命令级临时状态（真实 State 的叠加视图）。

位置：
    引擎核心逻辑层，位于 core.state 与 core.delta 之上。
职责：
    让同一个 Command 处理途中的后续 Action / Rule / 脚本看到前面产生的改动，
    并在 Command 全部通过后把变化量按顺序提交到真实 State（§8 / §9）。

草案依据：
    §8 临时状态 = 命令级叠加视图；D-16；
    §7.1 列表下标"应用当时"语义；D-44 合并发生在事件链之后、提交之前。
"""

from .errors import TempStateError
from .view import TempState, ViewStatus

__all__ = [
    "TempState",
    "ViewStatus",
    "TempStateError",
]
