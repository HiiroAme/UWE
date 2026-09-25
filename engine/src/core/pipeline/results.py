"""管线的结果对象（结算批次与每个 Command 的处理结果）。

位置：
    引擎核心逻辑层 → pipeline 子包。

职责：
    把"这一批 Command 处理完发生了什么"整理成纯数据，交给上层（存档、回放、UI、日志）：
        - 每个 Command 的结果：提交了还是丢弃了、丢弃原因、提交的变化量；
        - 整个结算批次：全部已提交的变化量（结算批次变化量）、Mod 标签、是否致命。

边界：
    - 结算批次变化量**不是提交单位**（D-18）：提交单位是 CommandDelta，
      这里只是把已提交的 CommandDelta 聚合起来，供存档 / 回放 / 分组使用（D-17）；
    - 引擎不解释 Mod 标签的内容（§18.1），只原样存下来。

草案依据：
    §9 第 10、11 步；§18.1 存档结构（结算批次变化量日志、Mod 标签）；
    D-17 三层变化量；D-18 提交单位；D-19 队列不存在"满"。
"""

from dataclasses import dataclass
from enum import Enum

from ..delta import Delta


class CommandStatus(str, Enum):
    """一个 Command 的处理结果。"""

    COMMITTED = "committed"  # 全部 Action 通过并已提交到真实 State
    DISCARDED = "discarded"  # 被规则拒绝 / 编排失败 / 服务异常：整个 Command 丢弃
    FAILED = "failed"        # 提交中途失败：引擎不变量被破坏，属于致命情况

    def __str__(self) -> str:
        """返回取值本身（如 "committed"），便于日志阅读。"""
        return self.value


@dataclass(frozen=True)
class CommandResult:
    """一个 Command 的处理结果。

    字段：
        command_id: 命令 id；
        status: 见 CommandStatus；
        reason: 失败 / 丢弃的原因（成功时是空字符串）；
        rule_id: 若因规则拒绝而丢弃，这里是那条规则；否则是空字符串；
        deltas: 已提交的 CommandDelta（合并后的变化量）；丢弃或失败时是空元组。
    """

    command_id: str
    status: CommandStatus
    reason: str = ""
    rule_id: str = ""
    deltas: tuple[Delta, ...] = ()

    @property
    def committed(self) -> bool:
        """是否已提交（status 是 COMMITTED）。"""
        return self.status is CommandStatus.COMMITTED


@dataclass(frozen=True)
class SettlementResult:
    """一次结算（触发一次"结算 Command 队列"特殊事件）的结果。

    字段：
        commands: 本批次处理的每个 Command 的结果（按处理顺序）；
        deltas: 结算批次变化量：本批次全部已提交 CommandDelta 的聚合；
        labels: Mod 贴在本批次上的标签（例如 "turn:3"），引擎只存不解释；
        fatal: 是否发生了致命失败（提交失败）。为真时调用方应停止继续模拟。
    """

    commands: tuple[CommandResult, ...]
    deltas: tuple[Delta, ...]
    labels: tuple[str, ...] = ()
    fatal: bool = False

    @property
    def committed_count(self) -> int:
        """本批次提交成功的命令数。"""
        return sum(1 for result in self.commands if result.committed)

    @property
    def discarded_count(self) -> int:
        """本批次被丢弃的命令数（不含致命失败）。"""
        return sum(1 for result in self.commands if result.status is CommandStatus.DISCARDED)

    @property
    def ok(self) -> bool:
        """本批次是否没有任何丢弃与致命失败。"""
        return not self.fatal and self.discarded_count == 0
