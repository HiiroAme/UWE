"""记录端口（Recorder）：管线把"要存档的东西"交给谁。

位置：
    引擎核心逻辑层 → pipeline 子包（端口定义在需要它的那一侧）。

职责：
    定义两个动作的契约：
        record_command(command)      记下一个命令（§18.1 的 Command 序列）
        record_settlement(result)    记下一个结算批次已提交的变化量（§18.1 的批次日志）

为什么是一个端口而不是直接引用记录器：
    依赖方向要单向（P3）：存档层（core.persistence）认识管线（要存 Command 与结算结果），
    所以反过来让管线去 import 存档层就会成环。这里定义一个抽象，管线只依赖抽象，
    真正的实现（core.persistence.Journal）在组装期注入——与 FileSystem / ServiceResolver
    的处理方式同一个套路（P7）。

草案依据：
    §18.1 存档保存 Command 与变化量；D-30 快照时机由 Mod 决定；
    P3 单向原则；P7 端口与适配器。
"""

from typing import Protocol

from .command import Command
from .results import SettlementResult


class Recorder(Protocol):
    """管线记录端口。"""

    def record_command(self, command: Command) -> None:
        """记下一个命令（入队时调用）。

        输入：
            command: 命令。
        输出：
            无。
        """

    def record_settlement(self, settlement: SettlementResult) -> None:
        """记下一个结算批次（结算结束时调用）。

        输入：
            settlement: 结算结果（只有已提交的变化量需要进存档）。
        输出：
            无。
        """
