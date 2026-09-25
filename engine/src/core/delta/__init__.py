"""delta 包：变化量模型、序列化、应用与合并。

位置：
    引擎核心逻辑层，位于 core.state 之上——state 负责"树怎么读写"，
    delta 负责"一次改动怎么记录、怎么落地"。

职责边界：
    - 本包不负责提交事务（那是 Command 级事务的事）；
    - 本包不负责临时状态视图（它只依赖本包的数据结构）；
    - 本包不负责产出变化量（Service 通过引擎接口产出，属于尚未定稿的 Engine API）。

草案依据：
    §7    变化量模型（三种粒度、操作 × 类型、字段、合并 / 求逆 / 冲突）；
    D-15  一个变化量只改一个变量；列表用下标 + 严格顺序 + 禁止合并；
    D-34  新增/删除 = 加/删一个键；列表增删元素属于"修改该列表"；
    §13   sequence 是唯一排序依据，timestamp 只用于显示。
"""

from .apply import apply_delta, values_match
from .codec import from_dict, to_dict
from .errors import DeltaError
from .merge import merge_adjacent
from .model import (
    DELTA_FORMAT_VERSION,
    MISSING,
    DataType,
    Delta,
    ListOp,
    Operation,
    Trace,
    data_type_of,
)

__all__ = [
    # 常量与枚举
    "DELTA_FORMAT_VERSION",
    "MISSING",
    "Operation",
    "DataType",
    "ListOp",
    # 数据模型
    "Trace",
    "Delta",
    "data_type_of",
    # 异常
    "DeltaError",
    # 序列化
    "to_dict",
    "from_dict",
    # 应用与合并
    "apply_delta",
    "merge_adjacent",
    "values_match",
]
