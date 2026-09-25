"""事件链执行器：按 label 路由，链式执行订阅者（§11.2，D-20）。

位置：
    引擎核心逻辑层 → pipeline 子包。

职责：
    一个 Command 的 Action / Service 产生变化量时会给变化量标上 label（事件 id）。
    本模块按**变化量的顺序**依次处理这些 label：
        事件发生 → 按注册顺序执行订阅它的 Trigger → Trigger 又可能产生新的事件
        → 继续执行 → …… 直到链条结束。
    整条链都发生在**同一个 Command 的事务里**：Trigger 产生的变化量与触发它的 Command
    一起提交（D-20），所以链跑在临时状态视图上，提交由 Dispatcher 统一做。

"一次事件发生"的口径（实现决策，草案未细化）：
    同一个 Action/Service 连续产生、且 label 相同的一段变化量算**一次事件发生**：
        - 连续：中间没有夹着 label 不同（或来自别的 Action/Service）的变化量；
        - 同一来源：trace 的 action_id / service_id 相同。
    这样"一次移动改了位置、行动力、状态三条，都标 unit:moved"只会触发一次订阅者，
    而同一个服务先标 A、隔了几条再标 A，仍然算两次独立的事件发生。
    触发时把这一整段变化量按 JSON 形式传给订阅者（表达式里用
    ["arg", "deltas"] / ["arg", "event"] 取），订阅者不必再去猜是哪条改动引出的。

不做的事：
    - 不做递归次数限制（无防炸阀，D-20）：Mod 自己写出循环触发是 Mod 的问题；
    - 不产生"提交后事件"（§11.2）：提交之后没有事件，UI 靠拉取读真实 State；
    - 不做跨 Command 的事件队列：链只在本次 Command 的事务内跑。

草案依据：
    §11.1 事件注册表与引用；§11.2 链式 Trigger（顺序、可见性、事务归属、无递归限制）；
    §9 第 9 步（事件链在第 10 步提交之前跑完）；D-20；D-28 订阅要声明顺序。
"""

from typing import Any, Callable, Mapping

from ..content import CompiledContent, CompiledTrigger
from ..delta import Delta, to_dict
from ..logger import Logger
from ..temp_state import TempState
from .action_runner import run_action
from .applier import Applier
from .command import Command
from .rule_checker import RuleChecker


class EventChain:
    """链式事件执行器。

    字段：
        _content: 编译后的内容（事件订阅表）；
        _rule_checker: 规则校验器（订阅者给出的 Action 同样要过规则）；
        _applier: 应用器；
        _logger: 日志对象；
        _functions: 表达式可用到的外部函数表。
    """

    def __init__(
        self,
        content: CompiledContent,
        *,
        rule_checker: RuleChecker,
        applier: Applier,
        logger: Logger,
        functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        """创建事件链执行器。

        输入：
            content: 编译后的内容；
            rule_checker: 规则校验器；
            applier: 应用器；
            logger: 日志对象；
            functions: 表达式可用到的外部函数表。
        输出：
            无（构造对象）。
        异常：
            TypeError: content / rule_checker / applier / logger 类型不符。
        变量：
            无。
        """
        if not isinstance(content, CompiledContent):
            raise TypeError(f"EventChain 需要 CompiledContent，实际是 {type(content).__name__}")
        if not isinstance(rule_checker, RuleChecker):
            raise TypeError(f"rule_checker 必须是 RuleChecker，实际是 {type(rule_checker).__name__}")
        if not isinstance(applier, Applier):
            raise TypeError(f"applier 必须是 Applier，实际是 {type(applier).__name__}")
        if not isinstance(logger, Logger):
            raise TypeError(f"logger 必须是 Logger，实际是 {type(logger).__name__}")
        self._content: CompiledContent = content
        self._rule_checker: RuleChecker = rule_checker
        self._applier: Applier = applier
        self._logger: Logger = logger
        self._functions: Mapping[str, Callable[..., Any]] = functions or {}

    def run(
        self,
        command: Command,
        view: TempState,
        *,
        functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> tuple[Delta, ...]:
        """把视图里（以及链上新产生的）所有 label 处理完。

        输入：
            command: 当前命令（事件链的事务归属）；
            view: 本命令的临时状态视图；
            functions: 本次求值用的外部函数表；缺省用构造时给的那份。
        输出：
            事件链新产生的变化量（按记录顺序；不含触发它的那批变化量）。
        异常：
            RuleRejection / ServiceFailure: 订阅者的 Action 被拒绝或服务失败
                （整条链与触发它的 Command 一起丢弃，由 Dispatcher 处理）；
            LogicError / state 层异常: 订阅者参数表达式求值失败。
        变量：
            table: 本次使用的外部函数表；
            cursor: 已经处理到视图变化量序列的第几条（链上新产生的会追加在后面）；
            last_key: 上一条扫描到的变化量的事件键，用来判断是否同一次事件发生；
            produced: 事件链新产生的变化量；
            pending: 当前视图里的变化量序列快照；
            delta: 当前扫描到的变化量；
            key: 当前变化量的事件键（label + 来源），没有 label 时是 None；
            occurrence: 本次事件发生那一段变化量。
        """
        table = self._functions if functions is None else functions
        produced: list[Delta] = []
        cursor = 0
        last_key: tuple[str, str, str] | None = None

        while True:
            pending = view.pending_deltas()
            if cursor >= len(pending):
                break
            delta = pending[cursor]
            key = _event_key(delta)
            if key is None:
                # 没有 label 的变化量不引出事件；它同时打断"连续段"的判断。
                cursor += 1
                last_key = None
                continue
            if key == last_key:
                # 同一次事件发生的后续变化量：已经处理过了。
                cursor += 1
                continue

            occurrence: list[Delta] = []
            while cursor < len(pending) and _event_key(pending[cursor]) == key:
                occurrence.append(pending[cursor])
                cursor += 1
            last_key = key
            produced.extend(self._fire(command, view, key[0], occurrence, table))

        return tuple(produced)

    def _fire(
        self,
        command: Command,
        view: TempState,
        event_id: str,
        occurrence: list[Delta],
        functions: Mapping[str, Callable[..., Any]],
    ) -> tuple[Delta, ...]:
        """执行一个事件的全部订阅者。

        输入：
            command: 当前命令；
            view: 临时状态视图；
            event_id: 事件 id（变化量上的 label）；
            occurrence: 本次事件发生那一段变化量；
            functions: 本次求值用的外部函数表。
        输出：
            订阅者新产生的变化量（按记录顺序）。
        异常：
            RuleRejection / ServiceFailure / LogicError / state 层异常: 由订阅者的 Action 抛出。
        变量：
            subscriptions: 该事件的订阅者（已按 order 与注册顺序排好）；
            trigger: 当前订阅者；
            event_args: 传给订阅者表达式的参数表；
            index / invocation: 订阅者给出的 Action 调用；
            action_id: 引擎为本实例分配的 Action 标识（用事件首条变化量的
                sequence 与本订阅内的下标拼成，保证同一 Command 内唯一且确定）；
            produced: 本次事件的产出。
        """
        subscriptions = self._content.subscriptions(event_id)
        if not subscriptions:
            self._logger.trace(
                f"事件 {event_id} 没有订阅者",
                command_id=command.command_id,
                label=event_id,
                extra={"delta_count": len(occurrence)},
            )
            return ()

        self._logger.debug(
            f"事件 {event_id}：执行 {len(subscriptions)} 个订阅者",
            command_id=command.command_id,
            label=event_id,
            extra={"delta_count": len(occurrence), "sequence": occurrence[0].trace.sequence},
        )
        event_args: dict[str, Any] = {
            "event": event_id,
            "deltas": [to_dict(delta) for delta in occurrence],
        }
        produced: list[Delta] = []
        for trigger in subscriptions:
            produced.extend(self._run_trigger(command, view, trigger, occurrence, event_args, functions))
        return tuple(produced)

    def _run_trigger(
        self,
        command: Command,
        view: TempState,
        trigger: CompiledTrigger,
        occurrence: list[Delta],
        event_args: dict[str, Any],
        functions: Mapping[str, Callable[..., Any]],
    ) -> tuple[Delta, ...]:
        """执行一个订阅者给出的全部 Action 调用。

        输入：
            command: 当前命令；
            view: 临时状态视图；
            trigger: 订阅者；
            occurrence: 本次事件发生那一段变化量（取首条用于生成确定性的 action_id）；
            event_args: 传给订阅者表达式的参数表；
            functions: 本次求值用的外部函数表。
        输出：
            本订阅者新产生的变化量。
        异常：
            RuleRejection / ServiceFailure / LogicError / state 层异常。
        变量：
            index / invocation: 订阅者的 Action 调用；
            action_id: 引擎分配的 Action 标识；
            produced: 产出列表。
        """
        produced: list[Delta] = []
        for index, invocation in enumerate(trigger.actions):
            action_id = f"{command.command_id}#t{occurrence[0].trace.sequence}.{index}"
            produced.extend(
                run_action(
                    command=command,
                    invocation=invocation,
                    action_id=action_id,
                    view=view,
                    content=self._content,
                    rule_checker=self._rule_checker,
                    applier=self._applier,
                    outer_args=event_args,
                    functions=functions,
                )
            )
        return tuple(produced)


def _event_key(delta: Delta) -> tuple[str, str, str] | None:
    """算出一条变化量的事件键（用来判断"同一次事件发生"）。

    输入：
        delta: 一条变化量。
    输出：
        (label, action_id, service_id)；没有 label 时返回 None。
    异常：
        无。
    变量：
        无。
    """
    if not delta.label:
        return None
    return (delta.label, delta.trace.action_id, delta.trace.service_id)
