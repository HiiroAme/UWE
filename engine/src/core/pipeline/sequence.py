"""SequenceCounter：全局单调递增的序号分配器。

位置：
    引擎核心逻辑层 → pipeline 子包。

职责：
    为每条变化量的 trace.sequence 分配唯一序号。D-41 规定 sequence 是唯一排序依据，
    TempState.record 要求同一视图内严格递增；因此一次运行只能有一个计数器实例
    （由将来的 EngineRuntime 持有），所有产出变化量的地方共用它。

边界：
    - 只发号：不排序、不去重、不记录谁用了哪个号；
    - 不入存档：序号只用于本次运行的排序与溯源，不参与规则判定；
    - 不提供重置：重置会破坏"同一次运行内单调递增"，需要新序号时应新建实例
      （那只在测试或新的运行开始时才合理）。

草案依据：
    §7.3 Trace.sequence；D-41。
"""


class SequenceCounter:
    """单调递增的整数序号分配器。

    字段：
        _next: 下一个将要发出的序号。
    """

    def __init__(self, start: int = 1) -> None:
        """创建一个序号分配器。

        输入：
            start: 第一个要发出的序号；必须是非负 int（bool 不算）。
        输出：
            无（构造对象）。
        异常：
            TypeError: start 不是 int 或 start 是 bool。
            ValueError: start 是负数。
        变量：
            无。
        """
        if not isinstance(start, int) or isinstance(start, bool):
            raise TypeError(f"SequenceCounter 的起始序号必须是 int，实际是 {type(start).__name__}")
        if start < 0:
            raise ValueError(f"SequenceCounter 的起始序号不能是负数：{start}")
        self._next: int = start

    def next(self) -> int:
        """返回下一个序号，并推进计数器。

        输入：无。
        输出：
            本次分配的序号；同一实例的多次调用结果严格递增。
        异常：
            无。
        变量：
            current: 本次要发出的序号。
        """
        current = self._next
        self._next += 1
        return current
