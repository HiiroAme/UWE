"""变化量的合并。

位置：
    引擎核心逻辑层 → delta 子包。
职责：
    把一串变化量按"只合并相邻的同路径、同操作、同类型的修改"压紧，
    让 CommandDelta 与结算批次变化量更小、应用更快（§7.4）。

草案依据与口径说明：
    §7.1 说"禁止对同一个列表的多个变化量做合并或重排"。本模块把它精确化为：
        - 带 list_op 的列表增删**永不合并**；
        - 元素替换、元素内部字段的修改，按普通"相邻合并"规则处理。
    理由是那句话的本意：列表下标会漂移，跨着别的操作合并会让下标指向别的元素。
    只合并**紧邻**的两条时，中间不存在任何操作，下标不可能漂移，
    因此合并是可证明安全的（正确性只依赖顺序）。

合并时机（写给将来的管线）：
    合并发生在**事件链跑完之后、提交之前**，所以被合并掉的中间 label 不会让
    Trigger 漏订阅；本模块对 label / trace 的取法是"保留最晚那条"，它代表最终写入。

适用范围（重要）：
    本模块只做**一个 Command 内部**的压缩：要求两条变化的 trace.command_id 相同。
    跨 Command 的合并（结算批次变化量的聚合）不在这里定义——那样会把中间 Command 的
    trace 丢掉，破坏 §19.1 的溯源要求；结算批次怎么聚合等它真正需要时再单独设计。

合并是有损的、也不是代数化简：
    - 合并**不可逆**：中间记录的 value / label / trace 不再存在于结果里，
      原始序列只能从日志还原；
    - 合并不做化简：3 → 5 → 3 这样的完整往返会保留成一条"3 → 3"的空操作，
      它仍然是合法记录（合并只做机械压紧，不做聪明推理）。

结果说明：
    返回新列表，不改动传入的列表；被合并掉的中间条目只保留在日志里。
"""

from dataclasses import replace as dataclass_replace
from typing import Sequence

from .errors import DeltaError
from .apply import values_match
from .model import Delta, Operation


def merge_adjacent(deltas: Sequence[Delta]) -> list[Delta]:
    """按相邻规则合并一串变化量。

    输入：
        deltas: 有序的变化量序列，顺序就是应用顺序。
    输出：
        新的列表；不可合并的条目保持原来的相对顺序。
    异常：
        DeltaError: 序列里出现非 Delta 的对象。
    变量：
        merged: 结果列表；
        previous: merged 里最后一条，用来判断当前条能否与它合并；
        combined: 合并后生成的新 Delta（保留前一条的旧值、当前条的新值）。
    """
    merged: list[Delta] = []
    for delta in deltas:
        if not isinstance(delta, Delta):
            raise DeltaError(f"merge_adjacent 只接受 Delta，实际是 {type(delta).__name__}")

        if merged and _can_merge(merged[-1], delta):
            previous = merged[-1]
            combined = dataclass_replace(
                previous,
                value=delta.value,
                label=delta.label,
                trace=delta.trace,
            )
            merged[-1] = combined
            continue

        merged.append(delta)
    return merged


def _can_merge(previous: Delta, current: Delta) -> bool:
    """判断紧邻的两条变化量能否合并成一条。

    输入：
        previous: 前一条（必须与 current 紧邻）；
        current: 后一条。
    输出：
        True 表示可以合并；False 表示保持两条。
    异常：
        无。
    变量：
        无。
    规则：
        - 两条都不能带 list_op（列表增删永不合并）；
        - 两条都必须是 modify（新增 / 删除不可能对同一路径合法地出现两次）；
        - trace.command_id 必须相同（只在一个 Command 内合并，见模块文档的适用范围）；
        - path 与 data_type 必须相同；
        - 链条必须首尾相接：前一条的新值与后一条的旧值**严格相等**（用 apply 的
          `values_match`，不是裸 `==`：`True == 1` 这种"类型不同但 == 成立"不算相接，
          否则会把"记录本来就不一致"的链条合并掉，等于吞掉 apply 该报的冲突，D-44）。
          不相接时不合并，留给 apply_delta 报"旧值与记录不一致"，
          避免把前后矛盾的记录悄悄抹平。
    """
    if previous.list_op is not None or current.list_op is not None:
        return False
    if previous.operation is not Operation.MODIFY or current.operation is not Operation.MODIFY:
        return False
    if previous.trace.command_id != current.trace.command_id:
        return False
    if previous.path != current.path or previous.data_type is not current.data_type:
        return False
    return values_match(previous.value, current.old_value)
