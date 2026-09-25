"""编排器：Command → 有序的 Action 调用（§9 第 5 步，D-35）。

位置：
    引擎核心逻辑层 → pipeline 子包，位于 core.content 之上。

职责：
    按 command 条目的内容，把一次 Command 拆成"要按顺序做哪些事"：
        1. 按**声明顺序**逐个判断分支条件（condition）——判断发生在运行时，
           依据是当下的临时状态（本命令开始时的视图），不是预先写死的选择；
        2. 取**第一个条件成立的分支**，把它给出的 Action 调用按声明顺序返回。
    判断顺序是确定的（就是书写顺序），一旦某条分支成立就定下这次选择，它后面的分支
    不再判断：既省算力，也给 Mod 作者一个确定的判断次序。
    同时成立多条分支在实际设计里应当被避免：条件要写成互斥的（读同一份状态，
    并写清"另一条不成立"），"多条同时成立"是数据写错的征兆而非正常用法。
    固定拆解只是"条件恒真"的特例（D-35）。
    （将来若要"分若干层、每层各取第一条成立的、层与层之间都判断"，属于扩展项，
    本阶段不做——P8。）

为什么返回的是"未求值的调用"而不是填好参数的 Action：
    Action 的参数（args）里可以有 ["get", "/units/u1/hp"] 这样的表达式，
    它的正确取值时刻是**这个 Action 即将被处理的那一刻**——那时它能看到同一个 Command
    里前面 Action 已经产生的改动（§8 临时状态的用途、§10 只读口径）。
    所以编排器只决定"做什么、按什么顺序做"，参数的求值交给执行侧在恰当的时机做
    （见 Applier 的文档）。

失败口径（§9 第 5 步：编排失败 → 丢弃 Command）：
    - 命令定义不存在 → OrchestrationError；
    - 分支条件求值报错 → OrchestrationError（把原始异常挂在 cause 上）。
    没有任何分支成立**不算失败**：这个 Command 什么都不做，正常提交一个空 CommandDelta。

草案依据：
    §9 第 5 步；§10 只读口径（条件读的是临时状态视图）；
    D-35 command → action 是 condition → result 的分支；D-14 运行期不复查结构。
"""

from typing import Any, Callable, Mapping

from ..content import CompiledContent, CompiledInvocation
from ..logger import Logger
from ..logic import LogicError, Reader
from ..state.errors import StateError
from .command import Command
from .errors import OrchestrationError


class Orchestrator:
    """把 Command 拆解成有序的 Action 调用。

    字段：
        _content: 编译后的内容（命令定义与分支）；
        _functions: 外部函数表（随机、几何……），供条件表达式里的 ["call", …] 使用；
        _logger: 日志对象（可为 None：测试里不关心日志时）。
    """

    def __init__(
        self,
        content: CompiledContent,
        *,
        functions: Mapping[str, Callable[..., Any]] | None = None,
        logger: Logger | None = None,
    ) -> None:
        """创建编排器。

        输入：
            content: 编译后的内容；
            functions: 表达式可用到的外部函数表；
            logger: 日志对象（core.logger.Logger 或 None）。
        输出：
            无（构造对象）。
        异常：
            TypeError: content 不是 CompiledContent。
        变量：
            无。
        """
        if not isinstance(content, CompiledContent):
            raise TypeError(f"Orchestrator 需要 CompiledContent，实际是 {type(content).__name__}")
        self._content: CompiledContent = content
        self._functions: Mapping[str, Callable[..., Any]] = functions or {}
        self._logger: Logger | None = logger

    def plan(
        self,
        command: Command,
        reader: Reader,
        *,
        functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> tuple[CompiledInvocation, ...]:
        """按分支条件把 Command 拆成要执行的 Action 调用序列。

        输入：
            command: 正在处理的 Command；
            reader: 读取入口（运行期传本 Command 的临时状态视图，§10）；
            functions: 本次求值用的外部函数表；缺省用构造时给的那份。
        输出：
            有顺序的 Action 调用元组：**第一条条件成立的分支**给出的调用（保持声明顺序）；
            没有任何分支成立时是空元组。
        异常：
            OrchestrationError: 命令定义不存在，或分支条件求值失败。
        变量：
            definition: 命令定义；
            branch: 当前检查的分支；
            index: 分支下标（日志用）；
            table: 本次使用的外部函数表。
        """
        definition = self._content.command(command.definition_id)
        if definition is None:
            raise OrchestrationError(
                f"命令定义不存在（command 注册表里没有 {command.definition_id!r}）",
                command_id=command.command_id,
            )
        table = self._functions if functions is None else functions

        for index, branch in enumerate(definition.branches):
            try:
                matched = branch.test(reader, args=command.payload, functions=table)
            except (LogicError, StateError) as exc:
                raise OrchestrationError(
                    f"分支条件求值失败（{branch.location}）：{exc}",
                    command_id=command.command_id,
                ) from exc
            if matched:
                self._log_debug(
                    "编排命中分支",
                    command=command,
                    branch_index=index,
                    action_count=len(branch.actions),
                )
                return branch.actions

        # 所有分支条件都不成立：这是一个正常结果（这个 Command 什么都不做）。
        self._log_debug("没有命中任何分支", command=command, branch_count=len(definition.branches))
        return ()

    def _log_debug(self, message: str, *, command: Command, **extra: Any) -> None:
        """写一条 DEBUG 日志（没给日志对象时什么都不做）。

        输入：
            message: 消息；
            command: 当前命令（用于溯源字段）；
            extra: 额外的键值对，放进日志的 extra。
        输出：
            无。
        异常：
            无。
        变量：
            无。
        """
        if self._logger is None:
            return
        self._logger.debug(message, command_id=command.command_id, extra=dict(extra))
