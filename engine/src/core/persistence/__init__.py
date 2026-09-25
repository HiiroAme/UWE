"""persistence 包：存档、读档与回放（§18）。

位置：
    引擎核心逻辑层 → persistence 子包。它建在 core.delta / core.pipeline 之上：
        delta   提供变化量（存档里记的就是它）
        pipeline 提供 Command 与结算结果（记录器收的就是它们）
        persistence 提供"怎么把这些东西装进一份存档、怎么读回来、怎么回放"

文件分工：
    model.py    存档结构与版本校验（SaveFile / Snapshot / SettlementRecord / ModRecord）
    journal.py  记录器：Command 序列、结算批次日志、取快照（快照时机由 Mod 决定）
    replay.py   回放：快照 + 依次叠加变化量 → 终态（不重跑规则）
    store.py    与 JSON 文本 / 文件端口的转换（含读取时的校验）
    errors.py   SaveError / SaveFormatError / SaveVersionError

草案依据：
    §18.1 存档结构、Mod 标签、版本拒绝加载；§18.2 回放口径；
    D-23 随机状态入档；D-25 版本不匹配直接拒绝；D-30 快照时机由 Mod 决定；
    D-40 回放不重跑规则；D-45 变化量格式版本。
"""

from .errors import SaveError, SaveFormatError, SaveVersionError
from .journal import Journal
from .model import (
    SAVE_FORMAT_VERSION,
    ModRecord,
    SaveFile,
    SettlementRecord,
    Snapshot,
    require_same_mod,
)
from .replay import replay_state
from .store import read_save, save_from_text, save_to_text, write_save

__all__ = [
    # 常量与结构
    "SAVE_FORMAT_VERSION",
    "ModRecord",
    "Snapshot",
    "SettlementRecord",
    "SaveFile",
    "require_same_mod",
    # 记录器与回放
    "Journal",
    "replay_state",
    # 落盘 / 读盘
    "save_to_text",
    "save_from_text",
    "write_save",
    "read_save",
    # 异常
    "SaveError",
    "SaveFormatError",
    "SaveVersionError",
]
