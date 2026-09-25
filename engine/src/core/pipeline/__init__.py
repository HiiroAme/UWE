"""pipeline 包：Command 管线（§9）。

位置：
    引擎核心逻辑层 → pipeline 子包。位于 core.temp_state 之上：
    temp_state 负责"一个 Command 的改动怎么被看到、怎么提交"，
    content 负责"注册表里的内容编译成运行期结构"，
    pipeline 负责"把这些内容按 12 步跑起来"。

包含的内容：
    - Command / Action：管线里的纯数据对象（command.py）；
    - SequenceCounter：全局单调递增的序号分配器（sequence.py）；
    - EngineApi / ServiceApi：把 TempState 视图与序号、随机、日志、上下文绑在一起的
      记账台（engine_api.py）；
    - Input：各输入源送进管线的统一输入（input.py）；
    - Orchestrator：Command → 有序 Action 调用（orchestrator.py，第 5 步）；
    - RuleChecker：Action 前的规则检查（rule_checker.py，第 6 步）；
    - Applier：调用服务（applier.py，第 7～8 步）；
    - EventChain：按 label 链式执行订阅者（event_chain.py，第 9 步）；
    - run_action：一次 Action 调用的公共流程（action_runner.py，第 6～7 步）；
    - Dispatcher：命令队列、结算与批次聚合（dispatcher.py，第 2～4、10～11 步）；
    - CommandResult / SettlementResult：结果对象（results.py）；
    - Recorder：记录端口（recording.py）：管线把命令与结算批次交给它，由存档层实现。

尚未包含（后续阶段）：
    - 存档 / 回放（§18）；
    - 派生值重算（§6.5）；
    - 系统事件（syscall 注册表）与适配器；
    - 游戏内 UI 与几何脚本（§15.3、§17）。

草案依据：
    §2 术语表（Command / Action / Service / 变化量）；
    §7.3 Trace 字段；§9 管线 12 步；§11 事件与链式 Trigger；§16 脚本经引擎接口产生变化量；
    D-17 / D-18 三层变化量与提交单位；D-41 sequence 是唯一排序依据。
"""

from .action_runner import run_action
from .applier import Applier
from .command import Action, Command
from .dispatcher import Dispatcher
from .engine_api import EngineApi, ServiceApi
from .errors import OrchestrationError, PipelineError, RuleRejection, ServiceFailure
from .event_chain import EventChain
from .input import Input
from .orchestrator import Orchestrator
from .recording import Recorder
from .results import CommandResult, CommandStatus, SettlementResult
from .rule_checker import RuleChecker
from .sequence import SequenceCounter

__all__ = [
    # 数据对象
    "Command",
    "Action",
    "Input",
    # 结果对象
    "CommandResult",
    "CommandStatus",
    "SettlementResult",
    # 异常
    "PipelineError",
    "OrchestrationError",
    "RuleRejection",
    "ServiceFailure",
    # 序号分配
    "SequenceCounter",
    # 记账台
    "EngineApi",
    "ServiceApi",
    # 管线部件
    "Orchestrator",
    "RuleChecker",
    "Applier",
    "EventChain",
    "Dispatcher",
    "run_action",
    "Recorder",
]
