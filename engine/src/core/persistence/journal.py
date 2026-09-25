"""记录器（Journal）：把"要存档的东西"按顺序记下来（§18.1）。

位置：
    引擎核心逻辑层 → persistence 子包。由 EngineRuntime 持有，Dispatcher 每处理完
    一个命令与一次结算就报给它。

职责：
    1. 记 Command 序列：引擎收到/产生的每个命令（按入队顺序）；
    2. 记结算批次变化量日志：每次结算里已提交的全部 CommandDelta（含 Mod 标签）；
    3. 取基础 State 快照：**时机由 Mod 决定**（D-30）——Mod 想在开局、每回合结束、
       每次保存前取都行；取快照之后，存档只需要带上"快照 + 之后的批次"。

为什么记录器要留在引擎里（而不是让调用方自己攒）：
    §18.1 要求存档保存"命令与变化量"，而这两样东西是引擎在处理命令时产生的；
    引擎顺手记下来，比让每个调用方自己复刻一遍更不容易漏（也保证顺序与日志一致）。

本模块只记账，不碰文件（P7）：落盘与读盘在 store.py，文件端口在 core.ports。

草案依据：
    §18.1 存档结构（基础 State、结算批次变化量日志、Command 序列、随机状态）；
    §18.2 回放 = 快照 + 叠加变化量；D-17 三层变化量；D-30 快照时机由 Mod 决定；
    D-40 回放不重跑规则。
"""

from copy import deepcopy
from collections.abc import Sequence

from ..delta import Delta
from ..pipeline.command import Command
from ..pipeline.results import SettlementResult
from .model import ModRecord, SaveFile, SettlementRecord, Snapshot


class Journal:
    """引擎运行的记录器。

    字段：
        _commands: 已记录的命令（按入队顺序）；
        _settlements: 已记录的结算批次（按发生顺序）；
        _snapshot: 最近一次快照的 State 深拷贝；没取过时是 None；
        _snapshot_index: 取快照时已记录的批次数。
    """

    def __init__(self) -> None:
        """创建一个空记录器。

        输入：无。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self._commands: list[Command] = []
        self._moved_marks: set[int] = set()      # 有变化量的命令的入队位置（id 可能重复，不能用它当键）
        self._settlements: list[SettlementRecord] = []
        self._snapshot: dict | None = None
        self._snapshot_index: int = 0

    def record_command(self, command: Command) -> None:
        """记下一个命令（由 Dispatcher 在入队时调用）。

        输入：
            command: 命令。
        输出：
            无。
        异常：
            TypeError: command 不是 Command。
        变量：
            无。
        """
        if not isinstance(command, Command):
            raise TypeError(f"Journal.record_command 需要 Command，实际是 {type(command).__name__}")
        self._commands.append(command)

    def _latest_unmarked(self, command_id: str):
        """找这个 id 最近一次、且还没被标过的入队位置（找不到给 None）。

        为什么要最近一次：旧存档里可能有重复 id；按位置标记才能区分哪一次入队
        产生了变化量（R5-1）。正常情况下同一个 id 只出现一次。
        """
        for index in range(len(self._commands) - 1, -1, -1):
            if self._commands[index].command_id == command_id and index not in self._moved_marks:
                return index
        return None

    def record_settlement(self, settlement: SettlementResult) -> None:
        """记下一个结算批次里已提交的变化量（由 Dispatcher 在结算结束时调用）。

        输入：
            settlement: 结算结果。
        输出：
            无。
        异常：
            TypeError: settlement 不是 SettlementResult。
        变量：
            无。

        说明：
            只记**已提交**的变化量：被丢弃的命令没有改动真实 State，不该进日志
            （它们的失败原因在引擎日志里，不在存档的变化量日志里）。
            空批次（没有任何已提交变化量）也照记：它的 Mod 标签对"按回合划分的回放"
            有意义（§18.1）。
            顺手把"真的产生过变化量"的命令标出来：**存档只导出这些命令**——
            纯界面命令（镜头缩放 / 平移这类只改 Context 的）不进命令史，否则拖动几千帧
            就会把存档撑大几十万字节（甲-7 / N-4）。内存里的 `commands()` 仍是全量。
        """
        if not isinstance(settlement, SettlementResult):
            raise TypeError(f"Journal.record_settlement 需要 SettlementResult，实际是 {type(settlement).__name__}")
        for result in settlement.commands:
            if result.deltas:
                index = self._latest_unmarked(result.command_id)
                if index is not None:
                    self._moved_marks.add(index)
        self._settlements.append(
            SettlementRecord(labels=tuple(settlement.labels), deltas=tuple(settlement.deltas))
        )

    def record_maintenance(self, deltas: Sequence[Delta], *, label: str) -> None:
        """记下一批"不是命令产生、但确实改了 State"的变化量（例如派生值刷新）。

        输入：
            deltas: 这批已提交的变化量；
            label: 说明来源的标签（例如 "engine:derived"）。
        输出：
            无。
        异常：
            TypeError: deltas 类型不对，或 label 不是非空字符串。
        变量：
            无。

        说明：
            为什么也记进批次日志：存档的基础 State 是"最近一次快照 + 之后的批次"，
            快照之后发生的**任何** State 变化都必须能在回放里被叠加回来，否则回放会漂。
            派生值刷新正是这样一种变化（引擎自愈时发生），所以给它一个批次与标签，
            回放照旧按顺序叠加即可；标签让日志能说清"这批不是玩家操作引起的"。
        """
        if not isinstance(label, str) or not label:
            raise ValueError(f"label 必须是非空字符串，实际是 {label!r}")
        for delta in deltas:
            if not isinstance(delta, Delta):
                raise TypeError(f"record_maintenance 只接受 Delta，实际是 {type(delta).__name__}")
        if not deltas:
            return
        self._settlements.append(SettlementRecord(labels=(label,), deltas=tuple(deltas)))

    def snapshot(self, state: dict) -> None:
        """给当前 State 取一次快照（成为新的基础 State）。

        输入：
            state: 真实 State（必须是 dict 根）。
        输出：
            无；快照是深拷贝，之后 State 再变也不影响它。
        异常：
            TypeError: state 不是 dict。
        变量：
            无。

        说明：
            取快照之后，之前的批次已经"包"在快照里，存档不再需要它们；
            但仍留在记录器里，供日志与将来的整局统计使用（build_save 只导出之后的）。
        """
        if not isinstance(state, dict):
            raise TypeError(f"Journal.snapshot 需要 dict 作为 State 根，实际是 {type(state).__name__}")
        self._snapshot = deepcopy(state)
        self._snapshot_index = len(self._settlements)

    @property
    def has_snapshot(self) -> bool:
        """是否已经取过快照。"""
        return self._snapshot is not None

    @property
    def settlement_count(self) -> int:
        """已记录的结算批次数。"""
        return len(self._settlements)

    @property
    def command_count(self) -> int:
        """已记录的命令数。"""
        return len(self._commands)

    def commands(self) -> tuple[Command, ...]:
        """返回全部命令（只读快照）。"""
        return tuple(self._commands)

    def settlements(self) -> tuple[SettlementRecord, ...]:
        """返回全部结算批次（只读快照）。"""
        return tuple(self._settlements)

    def build_save(
        self,
        *,
        state: dict,
        mod: ModRecord,
        random_state: dict,
        scenario_id: str = "",
        meta: dict | None = None,
    ) -> SaveFile:
        """组装一份存档。

        输入：
            state: 当前真实 State（没有取过快照时，会先用它取一次快照）；
            mod: 本局 Mod 记录；
            random_state: 随机状态（Rng.state() 的产物）；
            scenario_id: 场景 id（引擎只存）；
            meta: 元信息（引擎只存）。
        输出：
            SaveFile：基础 State = 最近一次快照；settlements = 快照之后记录的批次。
        异常：
            TypeError: state / mod / random_state / meta 类型不符。
        变量：
            snapshot: 导出用的快照；rest: 快照之后的批次。
        """
        if not isinstance(state, dict):
            raise TypeError(f"build_save 需要 dict 作为 State 根，实际是 {type(state).__name__}")
        if not isinstance(mod, ModRecord):
            raise TypeError(f"build_save 需要 ModRecord，实际是 {type(mod).__name__}")
        if not isinstance(random_state, dict):
            raise TypeError(f"build_save 需要 dict 作为随机状态，实际是 {type(random_state).__name__}")
        if meta is not None and not isinstance(meta, dict):
            raise TypeError(f"meta 必须是 dict，实际是 {type(meta).__name__}")

        if self._snapshot is None:
            # 调用方没取过快照：现在就取一次（快照时机由 Mod 决定，这里只是兜底）。
            self.snapshot(state)
        base_state = self._snapshot if self._snapshot is not None else deepcopy(state)
        later = self._settlements[self._snapshot_index:]
        return SaveFile(
            mod=mod,
            snapshot=Snapshot(state=deepcopy(base_state)),
            settlements=tuple(
                SettlementRecord(labels=record.labels, deltas=record.deltas) for record in later
            ),
            random_state=deepcopy(random_state),
            # 只带"真的产生过变化量"的命令（纯界面命令对回放没贡献，见 record_settlement 的说明）。
            commands=tuple(command for index, command in enumerate(self._commands)
                            if index in self._moved_marks),
            scenario_id=scenario_id,
            meta=deepcopy(meta) if meta else {},
        )

    def restore(self, save: SaveFile) -> None:
        """读档：把存档里的命令序列与批次日志接回记录器。

        输入：
            save: 已校验过的存档。
        输出：
            无；记录器变成"这份存档对应的历史"，之后再次存档不会丢历史。
        异常：
            TypeError: save 不是 SaveFile。
        变量：
            无。

        说明：
            快照直接采用存档里的那份（它已经是历史基线），批次日志接在它之后；
            这样"读档 → 再存档"与"一直玩下去再存档"得到的结果一致。
        """
        if not isinstance(save, SaveFile):
            raise TypeError(f"Journal.restore 需要 SaveFile，实际是 {type(save).__name__}")
        self._commands = list(save.commands)
        self._moved_marks = set(range(len(save.commands)))
        self._settlements = list(save.settlements)
        self._snapshot = deepcopy(save.snapshot.state)
        # 存档里的批次全都是"快照之后"记的，所以快照覆盖的批次数是 0。
        self._snapshot_index = 0
