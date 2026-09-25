"""写入 State 的值快照工具（"State 是一棵树"的写入保证）。

位置：
    引擎核心逻辑层 → state 子包。它是"值怎么写进树"的最后一层工具，
    delta 的写入路径、temp_state 冻结记录、templates / modload 的初始化写入都复用它。

职责：
    给一个即将写进 State 的值生成独占副本（容器深拷贝、标量原样返回），
    让树不会和调用方手上的容器共享对象结构。

为什么放在 state 而不是 delta：
    这条保证本来就属于"State 是一棵树"（§6.2），delta 只是其中一个调用方。
    放在 state 后，所有写入方（delta / temp_state / templates / modload）都依赖
    state，方向单一；delta 也不必再对外暴露一个不属于它的工具。

草案依据：
    §6.2 State 是一棵树；《评估报告 5》"原则审查"#2。
"""

from copy import deepcopy
from typing import Any


def snapshot_value(value: Any) -> Any:
    """为即将写入树的值生成一份独占副本。

    输入：
        value: 变化量携带的新值 / 插入元素值 / 模板属性值，可以是标量或任意深度的容器。
    输出：
        与传入对象不共享容器结构的新值；标量（int / float / str / bool / None 等
        不可变对象）由 deepcopy 直接返回原对象，不产生额外开销。
    异常：
        无（deepcopy 自身可能抛出，按其原样向上传播）。
    变量：
        无。
    说明：
        快照解决两个问题：
        1. Mod 把同一个容器写到两条路径时，State 会从树变成有向图，
           改一条路径会连带改另一条（记账里没有这条变化量）；
        2. 提交后的 State 与 CommandDelta 日志共享对象时，改日志等于改 State。
        这是 §6.2"State 是一棵树"在写入路径上的保证。
    """
    return deepcopy(value)
