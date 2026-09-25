"""分发器：Command 队列与结算（§9 管线第 2～4 步与第 10～11 步）。

位置：
    引擎核心逻辑层 → pipeline 子包，是管线的"总台"。

职责：
    1. 把统一 Input 映射成 Command 并入队（第 2、3 步）；
    2. 结算特殊事件触发时，按队列顺序处理每个 Command（第 4～10 步）；
    3. 把本批次已提交的 CommandDelta 聚合成**结算批次变化量**，贴上 Mod 标签（第 11 步）。

一个 Command 的处理流程（第 5～10 步，全部在**它自己的临时状态视图**上做）：
    编排（分支条件）→ 逐个 Action：求参数 → 规则校验 → 调用服务
    → 事件链（Trigger 产生的变化量并入同一次提交）
    → 合并成 CommandDelta → 提交到真实 State。

失败口径（§9 与 §19.2）：
    - 编排失败 / 规则拒绝 / 服务异常 / 数据冲突 → **丢弃**这个 Command（视图销毁，
      真实 State 一个字节都不变），记下原因，继续处理队列里的下一个；
    - 提交中途失败 → 致命（fatal）：结果里标出，调用方应停止模拟；
    - 让出控制权的只有结算：一个 Command 处理完才处理下一个（确定性、可回放）。

草案依据：
    §9 管线 12 步；§12 何时结算 = 触发结算特殊事件（不由调度器决定）；
    §18.1 结算批次支持 Mod 标签；D-17 三层变化量；D-18 提交单位；
    D-19 队列不存在"满"；D-20 事件链随 Command 一起提交；D-24 单 Mod。
"""

from typing import Any, Callable, Mapping, Sequence

from ..content import CompiledContent
from ..delta import Delta
from ..logger import Logger
from ..logic import LogicError, StateReader, resolve_mapping, truth
from .recording import Recorder
from ..state.errors import StateError
from ..temp_state import TempState
from .action_runner import run_action
from .command import Command
from .errors import OrchestrationError, RuleRejection, ServiceFailure
from .event_chain import EventChain
from .input import Input
from .orchestrator import Orchestrator
from .results import CommandResult, CommandStatus, SettlementResult
from .rule_checker import RuleChecker


class Dispatcher:
    """Command 队列与结算总台。

    字段：
        _state: 真实 State（提交目标）；
        _content: 编译后的内容（命令定义与输入映射）；
        _orchestrator: 编排器；
        _rule_checker: 规则校验器；
        _applier: 应用器；
        _event_chain: 事件链执行器；
        _recorder: 记录端口（命令序列与结算批次日志，供存档与回放使用）；
        _logger: 日志对象；
        _mod_id: 来源 Mod 标识（用来拼 Command id 与日志）；
        _functions: 表达式可用到的外部函数表；
        _queue: 待处理的 Command（先进先出；不存在"满"，D-19）；
        _next_serial: 下一个 Command 的序号（确定性地拼 Command id）。
    """

    def __init__(
        self,
        state: dict,
        content: CompiledContent,
        *,
        orchestrator: Orchestrator,
        rule_checker: RuleChecker,
        applier: Any,
        event_chain: EventChain,
        recorder: Recorder,
        logger: Logger,
        mod_id: str,
        functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        """创建分发器。

        输入：
            state: 真实 State（必须是 dict 根）；
            content: 编译后的内容；
            orchestrator / rule_checker / applier / event_chain: 管线的四个执行部件；
            recorder: 记录端口（命令序列与结算批次日志会在处理过程中记进去）；
            logger: 日志对象；
            mod_id: 来源 Mod 标识（非空字符串）；
            functions: 表达式可用到的外部函数表。
        输出：
            无（构造对象）。
        异常：
            TypeError: 参数类型不符；
            ValueError: mod_id 是空字符串。
        变量：
            无。
        """
        if not isinstance(state, dict):
            raise TypeError(f"Dispatcher 的 State 必须是 dict 根，实际是 {type(state).__name__}")
        if not isinstance(content, CompiledContent):
            raise TypeError(f"content 必须是 CompiledContent，实际是 {type(content).__name__}")
        if not isinstance(orchestrator, Orchestrator):
            raise TypeError(f"orchestrator 必须是 Orchestrator，实际是 {type(orchestrator).__name__}")
        if not isinstance(rule_checker, RuleChecker):
            raise TypeError(f"rule_checker 必须是 RuleChecker，实际是 {type(rule_checker).__name__}")
        if not isinstance(event_chain, EventChain):
            raise TypeError(f"event_chain 必须是 EventChain，实际是 {type(event_chain).__name__}")
        if not hasattr(recorder, "record_command") or not hasattr(recorder, "record_settlement"):
            raise TypeError(
                "recorder 必须实现 record_command(command) 与 record_settlement(result) "
                "两个接口（见 core.pipeline.Recorder）"
            )
        if not isinstance(logger, Logger):
            raise TypeError(f"logger 必须是 Logger，实际是 {type(logger).__name__}")
        if not isinstance(mod_id, str):
            raise TypeError(f"mod_id 必须是字符串，实际是 {type(mod_id).__name__}")
        if not mod_id:
            raise ValueError("mod_id 不能是空字符串")
        if not hasattr(applier, "execute"):
            raise TypeError("applier 必须实现 execute(...) 接口（见 core.pipeline.Applier）")

        self._state: dict = state
        self._content: CompiledContent = content
        self._orchestrator: Orchestrator = orchestrator
        self._rule_checker: RuleChecker = rule_checker
        self._applier: Any = applier
        self._event_chain: EventChain = event_chain
        self._recorder: Recorder = recorder
        self._logger: Logger = logger
        self._mod_id: str = mod_id
        self._functions: Mapping[str, Callable[..., Any]] = functions or {}
        self._queue: list[Command] = []
        self._next_serial: int = 1

    @property
    def queue_length(self) -> int:
        """返回队列里待处理的 Command 数。"""
        return len(self._queue)

    def resume_command_ids(self, command_ids) -> None:
        """读档时把命令序号接到历史之后：mod:cmd:N 里最大的 N + 1（R5-1）。

        输入：command_ids: 历史里的命令 id（可迭代）。
        输出：无。
        异常：无（认不出的 id 直接跳过）。
        变量：highest / tail: 解析过程的中间结果。
        """
        highest = 0
        for command_id in command_ids or ():
            tail = str(command_id).rsplit(":cmd:", 1)[-1]
            if tail.isdigit():
                highest = max(highest, int(tail))
        if highest:
            self._next_serial = max(self._next_serial, highest + 1)

    def enqueue(self, command: Command) -> None:
        """把一个已经形成的 Command 放进队列（§9 第 3 步）。

        输入：
            command: 待处理的命令。
        输出：
            无。
        异常：
            TypeError: command 不是 Command。
        变量：
            无。
        """
        if not isinstance(command, Command):
            raise TypeError(f"Dispatcher.enqueue 需要 Command，实际是 {type(command).__name__}")
        self._queue.append(command)
        # 命令序列要进存档（§18.1）：入队时记一笔，顺序就是处理顺序。
        self._recorder.record_command(command)
        self._logger.debug(
            "命令入队",
            mod_id=self._mod_id,
            command_id=command.command_id,
            extra={
                "command_definition": command.definition_id,
                "source": command.source,
                "queue_length": len(self._queue),
            },
        )

    def clear_queue(self) -> int:
        """清空"还没结算"的命令队列，返回丢掉的条数（读档用）。

        输入：无。
        输出：
            被丢弃的命令条数（给调用方记日志）。
        异常：
            无。
        变量：
            无。

        说明：
            读档 = 换到另一条时间线：旧时间线上"点了还没结算"的命令不能落到新局面上（R6-4）。
            已经结算过的命令与批次日志不受影响（它们归 Journal 管）。
        """
        dropped = len(self._queue)
        self._queue.clear()
        return dropped

    def submit_input(self, input_event: Input) -> Command | None:
        """把统一输入映射成 Command 并入队（§9 第 1～3 步）。

        输入：
            input_event: 已经翻译好的统一输入。
        输出：
            映射成功时返回新建的 Command；没有匹配的映射时返回 None（丢弃并记日志）。
        异常：
            TypeError: input_event 不是 Input。
        变量：
            candidates: 该输入类别的映射（按注册顺序）；
            reader: 真实 State 的读取器（输入映射发生在命令之前，没有临时状态）；
            candidate: 当前尝试的映射；
            matched: 条件结果；
            payload: 算好的命令参数。

        说明：
            条件与参数都在**真实 State** 上求值：这一步还没有 Command，也就没有
            临时状态视图（§10 的只读口径里，Rule 与脚本才读临时状态）。
            条件求值失败按"这条映射不成立"处理并记 WARN，继续尝试下一条。
        """
        if not isinstance(input_event, Input):
            raise TypeError(f"submit_input 需要 Input，实际是 {type(input_event).__name__}")
        candidates = self._content.input_candidates(input_event.kind)
        if not candidates:
            self._logger.warn(
                f"没有匹配的输入映射：{input_event.kind}",
                mod_id=self._mod_id,
                extra={"kind": input_event.kind, "source": input_event.source},
            )
            return None

        reader = StateReader(self._state)
        for candidate in candidates:
            try:
                matched = truth(candidate.condition, reader, args=input_event.data, functions=self._functions)
            except (LogicError, StateError) as exc:
                self._logger.warn(
                    f"输入映射条件求值失败，跳过这条（{candidate.location}）：{exc}",
                    mod_id=self._mod_id,
                    extra={"input": candidate.input_id, "source": input_event.source},
                )
                continue
            if not matched:
                continue

            try:
                payload = resolve_mapping(
                    candidate.payload, reader, args=input_event.data, functions=self._functions
                )
            except (LogicError, StateError) as exc:
                self._logger.warn(
                    f"输入映射的参数求值失败，跳过这条（{candidate.location}）：{exc}",
                    mod_id=self._mod_id,
                    extra={"input": candidate.input_id, "source": input_event.source},
                )
                continue

            command = Command(
                command_id=self._next_command_id(),
                definition_id=candidate.command_id,
                source=input_event.source,
                payload=payload,
                created_at=input_event.timestamp,
            )
            self.enqueue(command)
            return command

        self._logger.warn(
            f"输入 {input_event.kind} 没有满足条件的映射（共 {len(candidates)} 条）",
            mod_id=self._mod_id,
            extra={"source": input_event.source, "kind": input_event.kind},
        )
        return None

    def trigger_settlement(self, labels: Sequence[str] = ()) -> SettlementResult:
        """处理"结算 Command 队列"特殊事件：把队列清空（§9 第 4～11 步）。

        输入：
            labels: Mod 贴在本结算批次上的标签（如 "turn:3"）；引擎只存不解释（§18.1）。
        输出：
            SettlementResult：每个命令的结果、聚合后的结算批次变化量、标签、是否致命。
        异常：
            TypeError: labels 不是字符串序列，或其中某项不是字符串。
        变量：
            batch_labels: 校验后的标签元组；
            results: 每个 Command 的结果；
            batch_deltas: 本批次已提交的变化量聚合；
            fatal: 是否发生致命失败；
            command: 当前处理的命令；
            result: 它的处理结果。
        """
        batch_labels = _require_labels(labels)
        self._logger.info(
            f"结算开始：队列 {len(self._queue)} 个命令",
            mod_id=self._mod_id,
            extra={"labels": list(batch_labels)},
        )

        results: list[CommandResult] = []
        batch_deltas: list[Delta] = []
        fatal = False
        while self._queue:
            command = self._queue.pop(0)
            result = self._process(command)
            results.append(result)
            batch_deltas.extend(result.deltas)
            if result.status is CommandStatus.FAILED:
                fatal = True
                break

        settlement = SettlementResult(
            commands=tuple(results),
            deltas=tuple(batch_deltas),
            labels=batch_labels,
            fatal=fatal,
        )
        # 结算批次变化量日志也要进存档（§18.1）：只记已提交的部分。
        self._recorder.record_settlement(settlement)
        self._logger.info(
            f"结算结束：提交 {settlement.committed_count} 个、丢弃 {settlement.discarded_count} 个"
            + ("、发生致命失败" if fatal else ""),
            mod_id=self._mod_id,
            extra={
                "delta_count": len(settlement.deltas),
                "labels": list(batch_labels),
                "queue_left": len(self._queue),
            },
        )
        return settlement

    def _process(self, command: Command) -> CommandResult:
        """处理一个 Command：编排 → 逐个 Action → 事件链 → 提交。

        输入：
            command: 待处理的命令。
        输出：
            该命令的处理结果。
        异常：
            无（所有失败都被记成结果与日志；这会保证队列能一直往前走）。
        变量：
            view: 本命令的临时状态视图；
            invocations: 编排结果；
            index / invocation / action_id: 逐个 Action 的循环变量与标识；
            command_delta: 合并后的 CommandDelta。
        """
        view = TempState(self._state, command.command_id)
        self._logger.debug(
            "开始处理命令",
            mod_id=self._mod_id,
            command_id=command.command_id,
            extra={"command_definition": command.definition_id},
        )
        try:
            invocations = self._orchestrator.plan(command, view, functions=self._functions)
            for index, invocation in enumerate(invocations):
                run_action(
                    command=command,
                    invocation=invocation,
                    action_id=f"{command.command_id}#a{index}",
                    view=view,
                    content=self._content,
                    rule_checker=self._rule_checker,
                    applier=self._applier,
                    outer_args=command.payload,
                    functions=self._functions,
                )
            self._event_chain.run(command, view, functions=self._functions)
        except RuleRejection as exc:
            view.discard()
            self._logger.warn(
                f"命令被丢弃（规则拒绝）：{exc.reason}",
                mod_id=self._mod_id,
                command_id=command.command_id,
                action_id=exc.action_id,
                extra={"rule_id": exc.rule_id, "detail": exc.detail},
            )
            return CommandResult(
                command_id=command.command_id,
                status=CommandStatus.DISCARDED,
                reason=exc.reason,
                rule_id=exc.rule_id,
            )
        except (OrchestrationError, ServiceFailure) as exc:
            view.discard()
            self._logger.error(
                f"命令被丢弃：{exc.detail}",
                mod_id=self._mod_id,
                command_id=command.command_id,
                action_id=exc.action_id,
                service_id=exc.service_id,
            )
            return CommandResult(
                command_id=command.command_id,
                status=CommandStatus.DISCARDED,
                reason=exc.detail,
            )
        except Exception as exc:  # 数据树冲突、表达式失败、引擎内部不一致……
            view.discard()
            self._logger.error(
                f"命令被丢弃（{type(exc).__name__}）：{exc}",
                mod_id=self._mod_id,
                command_id=command.command_id,
            )
            return CommandResult(
                command_id=command.command_id,
                status=CommandStatus.DISCARDED,
                reason=f"{type(exc).__name__}: {exc}",
            )

        command_delta = view.command_delta()
        try:
            view.commit(self._state)
        except Exception as exc:
            # 提交中途失败：真实 State 已经被改了前半截，引擎不变量被破坏（D-47）。
            self._logger.fatal(
                f"提交失败，引擎不变量被破坏：{type(exc).__name__}: {exc}",
                mod_id=self._mod_id,
                command_id=command.command_id,
            )
            return CommandResult(
                command_id=command.command_id,
                status=CommandStatus.FAILED,
                reason=f"提交失败：{type(exc).__name__}: {exc}",
                deltas=tuple(command_delta),
            )

        self._logger.info(
            f"命令已提交：{len(command_delta)} 条变化量",
            mod_id=self._mod_id,
            command_id=command.command_id,
            extra={"delta_count": len(command_delta)},
        )
        return CommandResult(
            command_id=command.command_id,
            status=CommandStatus.COMMITTED,
            deltas=tuple(command_delta),
        )

    def _next_command_id(self) -> str:
        """分配一个确定性的 Command id。

        输入：无。
        输出：
            形如 "my_mod:cmd:12" 的字符串。
        异常：
            无。
        变量：
            serial: 本次分配的序号。

        说明：
            故意不用随机 id：Command 序列是回放与确定性测试的输入之一（§13），
            id 必须能由"第几个命令"推出来。
        """
        serial = self._next_serial
        self._next_serial += 1
        return f"{self._mod_id}:cmd:{serial}"


def _require_labels(labels: Sequence[str]) -> tuple[str, ...]:
    """校验 Mod 标签是一串字符串。

    输入：
        labels: 标签序列。
    输出：
        标签元组。
    异常：
        TypeError: labels 直接给了一个字符串，或其中某项不是字符串。
    变量：
        label: 当前检查到的标签。
    """
    if isinstance(labels, str):
        raise TypeError("labels 必须是标签序列（如 ['turn:3']），不能直接给一个字符串")
    result: list[str] = []
    for label in labels:
        if not isinstance(label, str):
            raise TypeError(f"标签必须是字符串，实际是 {type(label).__name__}")
        result.append(label)
    return tuple(result)
