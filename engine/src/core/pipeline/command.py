"""Command 与 Action：管线里的两种纯数据对象。

位置：
    引擎核心逻辑层 → pipeline 子包。

职责：
    - Command：玩家 / AI / 计时器 / 网络产生的一次完整意图，是唯一的提交原子单位
      （§2 术语、§8"提交单位严格是 Command"）；
    - Action：Orchestrator 从 Command 里拆出的最小逻辑操作单元（§9 第 5 步）。
      本模块只承载"是哪个 Action、参数是什么"，不认识具体玩法。

边界（P2 数据包原则）：
    - 两个类都是 frozen dataclass：只装数据，不带任何可执行内容（没有回调、
      没有脚本对象、没有执行方法）；
    - payload 里放什么由 Mod 决定，引擎只要求它是 dict，不解释其中任何键
      （P1：引擎不认识玩法；D-14：运行时不校验 Mod 数据）；
    - payload 是引用而非副本：构造后应视为只读（§16 的只读约定），
      需要冻结成引擎副本与否属于后续可讨论项。

草案依据：
    §2 术语表；§9 管线第 2、5 步；P2 / P4。
"""

from dataclasses import dataclass
from typing import Any
from ._checks import require_non_empty_str as _require_non_empty_str


@dataclass(frozen=True)
class Command:
    """一次完整意图（Command 管线的提交原子单位）。

    字段：
        command_id: 全局唯一的 Command 标识；与 TempState 视图、Trace.command_id 一致；
        definition_id: 对应的 command 注册表条目 id（引擎不解释它的含义）；
        source: 发起来源（如 "ui" / "ai" / "timer" / "network"），用于日志；
        payload: 纯数据参数（dict），内容由 Mod 定义；
        created_at: 交互发生时间戳；传给 Trace.timestamp，只用于显示 / 回放 / 统计，
            不参与排序（D-41）。
    """

    command_id: str
    definition_id: str
    source: str
    payload: dict
    created_at: float

    def __post_init__(self) -> None:
        """构造后校验字段类型与取值。

        输入：无（读取 dataclass 已赋值的字段）。
        输出：无；全部合法时直接返回。
        异常：
            TypeError: 标识字段不是字符串，payload 不是 dict，created_at 不是数字。
            ValueError: 标识字段或 source 是空字符串。
        变量：
            无。
        """
        _require_non_empty_str(self.command_id, "command_id")
        _require_non_empty_str(self.definition_id, "definition_id")
        _require_non_empty_str(self.source, "source")
        _require_payload(self.payload)
        _require_number(self.created_at, "created_at")


@dataclass(frozen=True)
class Action:
    """Command 执行过程中的最小逻辑操作单元。

    字段：
        action_id: 引擎分配的 Action 实例标识；写进 Trace.action_id；
        definition_id: 对应的 action 注册表条目 id（由 Orchestrator 解析）；
        payload: 本条 Action 的参数（dict），内容由 Mod 定义。
    """

    action_id: str
    definition_id: str
    payload: dict

    def __post_init__(self) -> None:
        """构造后校验字段类型与取值。

        输入：无（读取 dataclass 已赋值的字段）。
        输出：无；全部合法时直接返回。
        异常：
            TypeError: 标识字段不是字符串，payload 不是 dict。
            ValueError: 标识字段是空字符串。
        变量：
            无。
        """
        _require_non_empty_str(self.action_id, "action_id")
        _require_non_empty_str(self.definition_id, "definition_id")
        _require_payload(self.payload)


def _require_payload(value: Any) -> None:
    """要求 payload 是 dict。

    输入：
        value: 待检查的 payload。
    输出：
        无；通过检查时直接返回。
    异常：
        TypeError: value 不是 dict。
    变量：
        无。

    说明：
        只检查最外层类型，不检查内容（P1 / D-14：引擎不解释、不校验 Mod 数据）。
    """
    if not isinstance(value, dict):
        raise TypeError(f"payload 必须是 dict，实际是 {type(value).__name__}")


def _require_number(value: Any, name: str) -> None:
    """要求一个字段是数字（int / float，且不是 bool）。

    输入：
        value: 待检查的值；
        name: 字段名，用于异常消息。
    输出：
        无；通过检查时直接返回。
    异常：
        TypeError: value 不是数字或是 bool。
    变量：
        无。
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} 必须是数字，实际是 {type(value).__name__}")
