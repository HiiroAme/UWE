"""执行一次 Action 调用：求参数 → 规则校验 → 调用服务（§9 第 6、7 步）。

位置：
    引擎核心逻辑层 → pipeline 子包。

职责：
    把"一次 Action 调用"从开始到结束的固定动作收在一个地方，供两处复用：
        - Dispatcher：处理 Command 时执行分支给出的 Action；
        - EventChain：事件链里执行订阅者给出的 Action。
    两者对 Action 的处理必须完全一致（P5 可替换原则：同地位对象核心逻辑一致），
    所以这里不写成两份。

一次调用的四步：
    1. 在**调用这一刻**把 args 表达式按临时状态视图求值（args 里的 ["arg", 名字]
       取的是上层给的参数表：命令分支取 Command 的 payload，事件订阅取事件数据）；
    2. 组装 Action 实例（引擎分配 action_id）——Action 是纯数据，不含可执行内容；
    3. 规则校验（RuleChecker）：任何一条规则不成立就抛 RuleRejection；
    4. 调用服务（Applier）：由它组装 Trace、产出变化量。

草案依据：
    §9 第 5～7 步；§8（同一个 Command 内后面的改动看得到前面的改动）；
    §10 只读口径；§16 脚本经引擎接口产生变化量；P9 计算与副作用分离。
"""

from typing import Any, Callable, Mapping

from ..content import CompiledContent, CompiledInvocation
from ..delta import Delta
from ..logic import resolve_mapping
from ..temp_state import TempState
from .applier import Applier
from .command import Action, Command
from .errors import PipelineError
from .rule_checker import RuleChecker


def run_action(
    *,
    command: Command,
    invocation: CompiledInvocation,
    action_id: str,
    view: TempState,
    content: CompiledContent,
    rule_checker: RuleChecker,
    applier: Applier,
    outer_args: Mapping[str, Any] | None = None,
    functions: Mapping[str, Callable[..., Any]] | None = None,
) -> tuple[Delta, ...]:
    """执行一次 Action 调用，返回本次新产生的变化量。

    输入：
        command: 当前命令（提供 command_id / created_at 等溯源信息）；
        invocation: 要执行的 Action 调用（定义 id + 未求值的参数表达式）；
        action_id: 引擎为本实例分配的 Action 标识（由调用方按确定规则生成）；
        view: 本命令的临时状态视图；
        content: 编译后的内容（用来取 Action 定义）；
        rule_checker: 规则校验器；
        applier: 应用器（真正调用服务）；
        outer_args: 上层参数表：命令行 Action 的 args 表达式里 ["arg", 名字] 取它；
            事件链执行时传事件数据；
        functions: 表达式可用到的外部函数表。
    输出：
        本次调用新产生的变化量（按记录顺序；可能为空元组）。
    异常：
        PipelineError: Action 定义缺失（编译期本应保证存在，属于引擎内部不一致）；
        RuleRejection: 规则拒绝（由 RuleChecker 抛出）；
        ServiceFailure: 服务调用失败（由 Applier 抛出）；
        LogicError / state 层异常: 参数表达式求值失败。
    变量：
        definition: Action 定义；
        payload: 已求值的参数（Action 的纯数据 payload）；
        action: 组装好的 Action 实例。
    """
    definition = content.action(invocation.action_id)
    if definition is None:
        raise PipelineError(
            f"Action 定义缺失：{invocation.action_id!r}（位置 {invocation.location}）；"
            "编译期本应保证它存在，说明内容与运行期对象不同步",
            command_id=command.command_id,
            action_id=action_id,
        )

    payload = resolve_mapping(
        invocation.args, view, args=outer_args, functions=functions
    )
    action = Action(action_id=action_id, definition_id=invocation.action_id, payload=payload)
    rule_checker.check(
        definition,
        view,
        command_id=command.command_id,
        action_id=action_id,
        args=payload,
        functions=functions,
    )
    return applier.execute(command, action, definition, view, functions=functions)
