"""统一 Input：各输入源送进管线之前的统一形态（§9 第 0～2 步）。

位置：
    引擎核心逻辑层 → pipeline 子包。

职责：
    承载"玩家点了一下""计时器到了""AI 决定了""网络收到一条"这类**原始交互**，
    让 Dispatcher 能按 input 注册表把它映射成 Command。

边界：
    - 平台相关的事件（鼠标坐标、按键码）属于 UI 适配器的世界，翻译成统一 Input 之后
      才允许进入核心；本模块只认 kind / data / timestamp / source 四个字段；
    - data 的内容由输入源与 Mod 约定，引擎不解释（P1）；它只作为 args 传给 input 条目的
      condition / payload 表达式（写 ["arg", 名字] 就能取到）。

草案依据：
    §9 第 0～2 步（输入产生 → 翻译 → 映射为 Command → 入队）；
    §2 术语表（Input 由 UI、AI、计时器、网络产生；翻译器是 UI 适配器的专称）；
    D-41 顺序只认 sequence，所以这里的时间戳同样只用于显示。
"""

from dataclasses import dataclass
from typing import Any
from ._checks import require_non_empty_str as _require_non_empty_str


@dataclass(frozen=True)
class Input:
    """一次已经翻译好的统一输入。

    字段：
        kind: 输入类别；与 input 条目 id 的第三段对应（例如 "click_node"）；
        data: 输入数据（dict），内容由输入源与 Mod 约定；表达式里用 ["arg", 名字] 取；
        timestamp: 交互发生的时间（由输入源给的墙钟时间），只用于显示 / 回放 / 统计；
        source: 来源（"ui" / "ai" / "timer" / "network"），写进 Command 并用于日志。
    """

    kind: str
    data: dict
    timestamp: float
    source: str

    def __post_init__(self) -> None:
        """构造后校验字段类型与取值。

        输入：无（读取 dataclass 已赋值的字段）。
        输出：无；全部合法时直接返回。
        异常：
            TypeError: kind / source 不是字符串，data 不是 dict，timestamp 不是数字。
            ValueError: kind 或 source 是空字符串。
        变量：
            无。
        """
        _require_non_empty_str(self.kind, "kind")
        _require_non_empty_str(self.source, "source")
        if not isinstance(self.data, dict):
            raise TypeError(f"Input.data 必须是 dict，实际是 {type(self.data).__name__}")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, (int, float)):
            raise TypeError(f"Input.timestamp 必须是数字，实际是 {type(self.timestamp).__name__}")


