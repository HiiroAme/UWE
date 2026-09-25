"""规则校验器：Action 执行前的玩法约束检查（§9 第 6 步）。

位置：
    引擎核心逻辑层 → pipeline 子包。

职责：
    对"某个 Action 即将执行"这件事，按 action 条目里声明的规则顺序逐条求值：
    全部成立才算通过；**任何一条不成立，整个 Command 被丢弃**（§8：提交单位是 Command）。

读什么：
    规则条件读的是**临时状态视图**（§10）：同一个 Command 里前面的 Action 已经产生的
    改动必须能被后面的规则看到；表达式里的 ["arg", 名字] 取的是这个 Action 的参数。

为什么不在这里做数据校验：
    引擎不在运行期复查 Mod 数据与结构（D-14）：结构在加载期由 content 编译器检查，
    数值范围与类型由 Mod 自己声明、由规则自己判断。规则只回答"玩法上能不能这么做"。

草案依据：
    §9 第 6 步（Rule → 拒绝 → 丢弃 Command 并销毁临时状态）；
    §10 只读口径（Rule 读临时状态）；§14.2 rule 注册表；
    D-14 取消 Validator、运行期不复查；D-29 加载期检查由 Mod 给出。
"""

from typing import Any, Callable, Mapping

from ..content import CompiledAction
from ..logger import Logger
from ..logic import LogicError, Reader
from ..state.errors import StateError
from .errors import RuleRejection


class RuleChecker:
    """按声明顺序执行 Action 的规则检查。

    字段：
        _functions: 外部函数表（随机、几何……）；
        _logger: 日志对象（可为 None）。
    """

    def __init__(
        self,
        *,
        functions: Mapping[str, Callable[..., Any]] | None = None,
        logger: Logger | None = None,
    ) -> None:
        """创建规则校验器。

        输入：
            functions: 表达式可用到的外部函数表；
            logger: 日志对象。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self._functions: Mapping[str, Callable[..., Any]] = functions or {}
        self._logger: Logger | None = logger

    def check(
        self,
        action: CompiledAction,
        reader: Reader,
        *,
        command_id: str,
        action_id: str,
        args: Mapping[str, Any] | None = None,
        functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        """逐条检查 Action 的规则；不通过就抛 RuleRejection。

        输入：
            action: 已编译的 Action 定义（含它声明的规则）；
            reader: 读取入口（临时状态视图）；
            command_id / action_id: 当前命令与动作实例的标识（日志与报错用）；
            args: 本 Action 已求值的参数（表达式里 ["arg", 名字] 取它）；
            functions: 本次求值用的外部函数表；缺省用构造时给的那份。
        输出：
            无（全部通过时直接返回）。
        异常：
            RuleRejection: 某条规则不成立；
            RuleRejection: 条件求值本身报错（路径走不通、表达式算不出来）——
                这种情况同样必须丢弃 Command，并把原因记清楚。
        变量：
            table: 本次使用的外部函数表；
            rule: 当前检查的规则；
            passed: 该规则的求值结果。
        """
        table = self._functions if functions is None else functions
        for rule in action.rules:
            try:
                passed = rule.check(reader, args=args, functions=table)
            except (LogicError, StateError) as exc:
                raise RuleRejection(
                    f"规则条件求值失败（{rule.location}）：{exc}",
                    rule_id=rule.rule_id,
                    reason=f"规则无法判定：{exc}",
                    command_id=command_id,
                    action_id=action_id,
                ) from exc
            if not passed:
                self._log_rejection(rule.rule_id, rule.message, command_id, action_id, action.action_id)
                raise RuleRejection(
                    f"规则不成立：{rule.message}",
                    rule_id=rule.rule_id,
                    reason=rule.message,
                    command_id=command_id,
                    action_id=action_id,
                )
            self._log_trace("规则通过", rule.rule_id, command_id, action_id)

    def _log_rejection(
        self,
        rule_id: str,
        reason: str,
        command_id: str,
        action_id: str,
        definition_id: str,
    ) -> None:
        """写一条 WARN 日志（规则拒绝属于"拒绝变动，必须给原因"）。

        输入：
            rule_id: 规则 id；
            reason: 拒绝原因；
            command_id / action_id: 标识；
            definition_id: Action 定义 id。
        输出：
            无。
        异常：
            无。
        变量：
            无。
        """
        if self._logger is None:
            return
        self._logger.warn(
            f"规则拒绝：{reason}",
            command_id=command_id,
            action_id=action_id,
            extra={"rule_id": rule_id, "action_definition": definition_id},
        )

    def _log_trace(self, message: str, rule_id: str, command_id: str, action_id: str) -> None:
        """写一条 TRACE 日志（规则逐条通过这类细节）。

        输入：
            message: 消息；
            rule_id: 规则 id；
            command_id / action_id: 标识。
        输出：
            无。
        异常：
            无。
        变量：
            无。
        """
        if self._logger is None:
            return
        self._logger.trace(
            message, command_id=command_id, action_id=action_id, extra={"rule_id": rule_id}
        )
