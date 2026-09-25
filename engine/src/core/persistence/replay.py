"""回放：基础 State 依次叠加结算批次变化量（§18.2、D-40）。

位置：
    引擎核心逻辑层 → persistence 子包。

职责：
    从存档里的快照出发，按顺序把每个结算批次里的变化量应用到树上，得到终态：

        快照 → 第 1 批变化量 → 第 2 批 → …… → 终态

    回放**不重跑 Command、不重跑规则、不触发任何外部副作用**（不播音效、不发网络）：
    它只是"把记录下来的变化量再叠一遍"，所以速度与结果都只取决于记录本身。

为什么这样定义（而不是重跑命令序列）：
    "重跑命令"是确定性测试用的另一种手段（§18.2、§20），它要重新求值规则、
    依赖随机与时间戳，任何一处漂移都会让结果不同；存档回放不该背这个包袱。

草案依据：
    §18.2 回放口径；D-40 回放 = 快照 + 叠加变化量，不重跑规则；
    D-44 单条变化量的应用是原子的（不一致会当场报冲突，而不是悄悄漂移）。
"""

from copy import deepcopy

from ..delta import apply_delta
from .model import SaveFile


def replay_state(save: SaveFile) -> dict:
    """按存档回放出终态 State。

    输入：
        save: 存档（快照 + 快照之后记录的结算批次）。
    输出：
        新的 State 树（终态）；存档本身不被修改。
    异常：
        TypeError: save 不是 SaveFile；
        PathNotFoundError / StateShapeError / StateConflictError: 变化量与快照对不上
            （说明存档与记录不一致，按 D-44 直接报冲突，不猜、不跳过）。
    变量：
        state: 从快照深拷贝出来的工作树；
        settlement: 当前叠加的批次；
        delta: 当前叠加的变化量。
    """
    if not isinstance(save, SaveFile):
        raise TypeError(f"replay_state 需要 SaveFile，实际是 {type(save).__name__}")

    state = deepcopy(save.snapshot.state)
    for settlement in save.settlements:
        for delta in settlement.deltas:
            apply_delta(state, delta)
    return state
