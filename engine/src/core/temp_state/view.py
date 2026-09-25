"""命令级临时状态：真实 State 的叠加视图（路径复制实现）。

位置：
    引擎核心逻辑层 → temp_state 子包。位于 core.state 与 core.delta 之上——
    state 负责"树怎么读写"，delta 负责"一次改动怎么记录"，
    本模块负责"一个 Command 处理途中，这些改动怎么被看到、怎么提交"。

职责：
    - 以真实 State 为只读底座，维护一份**叠加视图**：变化量改到哪条路径，
      就把那条路径上的容器复制成新副本，副本之间用树结构串起来；
    - 视图与**一个 Command 绑定**：构造时必须给出 command_id，记录的每条变化量
      都必须属于这个 Command（否则拒绝），这是 D-46"只在一个 Command 内合并"的前提；
    - 按顺序记录本 Command 待提交的变化量，并在记录时把 value / old_value 快照成
      引擎自己的副本（调用方事后改自己手上的容器不影响记录），
      同时要求 trace.sequence 严格递增（D-41：sequence 是唯一排序依据），
      借此挡住重复记录与错序记录；
    - 提供读（看到已生效但未提交的改动）、CommandDelta（合并结果）与提交
      （把合并后的变化量重放到真实 State）。

两份变化量清单的分工（写清楚以免误用）：
    - pending_deltas()：按记录顺序的**原始**变化量，供日志与诊断；合并是有损的，
      中间记录只在这里可见；
    - command_delta()：把原始记录按 §7.4 / D-44 合并后的 **CommandDelta**，
      也是 commit() 实际重放、将来进入结算批次与存档的内容。

为什么用路径复制，而不是"只存变化量、读不到就回退真实 State"：
    列表插入 / 删除会让后续下标整体移位，"精确路径查不到就回退"会读到错元素。
    路径复制让每条变化量都打在**当前视图**上，下标语义天然正确（§7.1 的"应用当时"），
    而且读就是普通树读，不需要任何下标推演。

为什么 v1 不做"已拥有容器"缓存（dirty 集合）优化：
    正确性优先。每条变化量沿路径复制一遍，简单且可证明正确；优化需要维护
    "哪些副本归视图所有"，还要持有强引用防止 id 复用，等性能数据出现再做（F-10）。
    优化只会改本模块内部的 _copy_for_delta，对外接口不变。

草案依据：
    §8 临时状态 = 命令级叠加视图；D-16；
    §7.1 列表下标"应用当时"语义；§9 事件链→提交；
    D-41 sequence 是唯一排序依据；D-44 / D-46 合并规则与范围；D-47 四态生命周期。
"""

from dataclasses import replace as dataclass_replace
from enum import Enum
from typing import Any

from ..delta.apply import apply_delta
from ..delta.errors import DeltaError
from ..delta.merge import merge_adjacent
from ..delta.model import Delta
from ..state.errors import StateShapeError
from ..state.path import format_path, parse
from ..state.tree import exists as tree_exists
from ..state.tree import get as tree_get
from ..state.tree import replace as tree_replace
from ..state.values import snapshot_value
from ..state.wildcard import expand as expand_pattern
from .errors import TempStateError


class ViewStatus(str, Enum):
    """临时状态视图的生命周期状态。"""

    OPEN = "open"            # 可以读写与提交
    COMMITTED = "committed"  # 已提交到真实 State
    DISCARDED = "discarded"  # 已丢弃（Command 失败）
    FAILED = "failed"        # 提交中途失败：引擎不变量被破坏，属于致命情况

    def __str__(self) -> str:
        """返回状态取值本身（如 "open"），便于日志阅读。"""
        return self.value


class TempState:
    """一个 Command 的临时状态：真实 State 的只读底座 + 叠加视图 + 待提交变化量。

    对外约定：
        - 读（get / exists）看到的是"已记录的变化量都生效之后"的结果；
        - 写只有 record（记录一条变化量），它同时把变化量打到叠加视图上；
        - record 只接受属于本视图 Command 的变化量，且 trace.sequence 必须严格递增；
        - command_delta 给出合并后的 CommandDelta，commit 重放的就是它；
        - 提交（commit）把变化量重放到创建视图时的那棵真实 State 上；
        - 视图进入终态（committed / discarded / failed）后，读写、提交、丢弃都会报错；
        - 终态后叠加视图的副本链被释放，pending_deltas / command_delta 仍可读，
          供日志与批次聚合使用（D-47）。

    字段：
        _base: 创建视图时的真实 State（只读底座），提交目标必须就是它；
        _command_id: 本视图对应的 Command 标识，record 用它校验变化量归属；
        _root: 当前叠加视图的根；没改过的部分与 _base 共享子树；
            进入终态后置为 None，释放副本链；
        _pending: 按顺序记录、尚未提交的变化量；每条记录在写入时已冻结
            （value / old_value 是引擎自己的副本）；
        _merged: command_delta() 的缓存；record 成功时失效，终态后仍可用；
        _status: 生命周期状态，见 ViewStatus。
    """

    def __init__(self, base_state: dict, command_id: str) -> None:
        """创建一个以 base_state 为底座的叠加视图。

        输入：
            base_state: 真实 State；必须是 dict 结构的纯数据树。本类不修改它。
            command_id: 本视图对应的 Command 标识；必须是非空字符串，
                与将要记录的每条变化量的 trace.command_id 一致。
        输出：
            无（构造对象）。
        异常：
            TypeError: base_state 不是 dict。
            TypeError: command_id 不是字符串。
            ValueError: command_id 是空字符串。
        变量：
            无。
        """
        if not isinstance(base_state, dict):
            raise TypeError(f"TempState 需要 dict 作为真实 State，实际是 {type(base_state).__name__}")
        if not isinstance(command_id, str):
            raise TypeError(f"TempState 需要字符串 command_id，实际是 {type(command_id).__name__}")
        if not command_id:
            raise ValueError("TempState 的 command_id 不能是空字符串：每个视图必须绑定一个 Command")
        self._base: dict = base_state
        self._command_id: str = command_id
        self._root: dict | None = base_state
        self._pending: list[Delta] = []
        self._merged: tuple[Delta, ...] | None = None
        self._status: ViewStatus = ViewStatus.OPEN

    @property
    def status(self) -> ViewStatus:
        """返回当前生命周期状态（任何状态都可读，便于日志与断言）。"""
        return self._status

    @property
    def command_id(self) -> str:
        """返回本视图绑定的 Command 标识（任何状态都可读）。"""
        return self._command_id

    @property
    def base_state(self) -> dict:
        """返回创建视图时的真实 State（只读用途：查提交目标、写日志）。

        说明：
            返回的是真实 State 对象本身，不是副本；调用方不得通过它写入，
            一切改动仍然只能经引擎接口产生变化量（§16）。
        """
        return self._base

    def get(self, path: str) -> Any:
        """在叠加视图上按路径取值。

        输入：
            path: JSON Pointer 路径；"" 表示读取当前视图的根。
        输出：
            当前视图上的值（包含尚未提交的改动）。
        异常：
            TempStateError: 视图已进入终态。
            PathSyntaxError / PathNotFoundError / StateShapeError: 路径问题（来自 state 层）。
        变量：
            无。
        """
        self._require_open("读取")
        return tree_get(self._root, path)

    def exists(self, path: str) -> bool:
        """判断路径在当前视图上是否存在（值为 None 也算存在）。

        输入：
            path: JSON Pointer 路径。
        输出：
            True 表示能按该路径取到值；False 表示走不通。
        异常：
            TempStateError: 视图已进入终态。
            PathSyntaxError: 路径写法不合法（来自 state 层）。
        变量：
            无。
        """
        self._require_open("探测")
        return tree_exists(self._root, path)

    def expand(self, pattern: str) -> tuple:
        """按通配路径列出当前视图上的全部匹配（F-05 批量操作）。

        输入：
            pattern: 通配路径（也可以是普通路径）。
        输出：
            core.state.Match 元组（顺序确定：字典按键排序、列表按下标升序）。
        异常：
            TempStateError: 视图已进入终态；
            PathSyntaxError: 路径写法不合法。
        变量：
            无。

        说明：
            展开的是**当前视图**（含本 Command 尚未提交的改动），
            这样批量操作与顺序执行看到的是同一份状态（§8 / §10）。
        """
        self._require_open("按通配枚举")
        return expand_pattern(self._root, pattern)

    def record(self, delta: Delta) -> None:
        """记录一条变化量，并把它应用到叠加视图上。

        输入：
            delta: 已构造好的 Delta；其 old_value / index 必须是**视图上的当前值**，
                其 trace.command_id 必须等于本视图的 command_id，
                其 trace.sequence 必须大于上一条已记录变化量的 sequence。
        输出：
            无。
        异常：
            TempStateError: 视图已进入终态。
            TempStateError: delta 属于别的 Command，或 sequence 没有严格递增。
            DeltaError: delta 不是 Delta 实例。
            PathNotFoundError / StateShapeError / StateConflictError: 该变化量在
                当前视图上不成立（路径不存在、类型不符、旧值不一致、键已存在等）。
        变量：
            stored: delta 的冻结副本：value / old_value 深拷贝后存进 _pending，
                保证记录、视图与提交三者看到同一份数据，调用方事后改自己手上的
                容器不会改变已记录的内容；
            new_root: 复制好路径之后的新根；只有 apply 成功才替换 _root，
                保证 record 单条原子——失败时视图与真实 State 都不变。
        说明：
            存进 _pending 的是 stored 而不是入参 delta；pending_deltas() 返回的
            记录同样只读（§16），不通过访问器就地修改其中的容器。
        """
        self._require_open("写入")
        if not isinstance(delta, Delta):
            raise DeltaError(f"TempState.record 需要 Delta 实例，实际是 {type(delta).__name__}")
        if delta.trace.command_id != self._command_id:
            raise TempStateError(
                f"变化量属于 Command {delta.trace.command_id!r}，本视图属于 Command "
                f"{self._command_id!r}：一个视图只能记录同一个 Command 的变化量"
            )
        if self._pending and delta.trace.sequence <= self._pending[-1].trace.sequence:
            raise TempStateError(
                f"trace.sequence 必须严格递增（D-41）：上一条是 "
                f"{self._pending[-1].trace.sequence}，本条是 {delta.trace.sequence}"
            )

        stored = _freeze_delta(delta)
        new_root = _copy_for_delta(self._root, stored)
        apply_delta(new_root, stored)
        self._root = new_root
        self._pending.append(stored)
        self._merged = None

    def pending_deltas(self) -> tuple[Delta, ...]:
        """返回按记录顺序排列的**原始**待提交变化量（只读快照）。

        输入：无。
        输出：
            元组；调用方拿到的是快照，不能通过它改动视图内部列表。
        异常：
            无（终态下也允许读取，用于日志与诊断）。
        变量：
            无。
        说明：
            这里保留未合并的原始记录（合并有损，中间记录只在此可见）；
            提交依据是 command_delta() 的合并结果。记录里的 value / old_value
            在 record 时已经冻结成引擎副本，但取回后仍应视为只读（§16）。
        """
        return tuple(self._pending)

    def command_delta(self) -> tuple[Delta, ...]:
        """返回本 Command 合并后的变化量（CommandDelta）。

        输入：无。
        输出：
            元组；按 §7.4 / D-44 把相邻、同路径、同操作、同类型且首尾相接的
            修改压紧后的结果；没有记录时是空元组。
        异常：
            无（终态下也允许读取：提交后的日志与批次聚合都要用它）。
        变量：
            merged: merge_adjacent 的结果，首次计算后缓存；record 成功时缓存失效。
        说明：
            合并只在一个 Command 内进行：record 已经保证所有记录的
            trace.command_id 与本视图一致（D-46）。
        """
        if self._merged is None:
            self._merged = tuple(merge_adjacent(self._pending))
        return self._merged

    def commit(self, target: dict) -> None:
        """把合并后的 CommandDelta 重放到真实 State 上。

        输入：
            target: 必须是创建本视图时传入的那个真实 State 对象本身。
        输出：
            无；直接修改 target。
        异常：
            TempStateError: 视图已进入终态；或 target 不是本视图的底座。
            PathNotFoundError / StateShapeError / StateConflictError: 重放失败——
                说明真实 State 在本 Command 处理期间被别处改过。按草案
                "提交失败 → 致命"，视图进入 FAILED 终态，不做回滚。
        变量：
            delta: 当前正在重放的变化量（来自 command_delta()）。
        说明：
            重放的是合并结果而不是原始记录，与"提交单位是 CommandDelta"
            （§9 / D-18）一致；被合并掉的中间记录仍保留在 pending_deltas() 里。
        """
        self._require_open("提交")
        if target is not self._base:
            raise TempStateError("提交目标必须是创建本视图时的那个真实 State 对象，不能用别的树")

        for delta in self.command_delta():
            try:
                apply_delta(target, delta)
            except Exception:
                # 提交中途失败：真实 State 已经带上前面几条变化量，按草案属于
                # "引擎不变量被破坏"的致命情况；进入 FAILED 终态防止继续误用。
                self._status = ViewStatus.FAILED
                # 终态后不再需要叠加视图：释放副本链；_pending 保留供日志诊断。
                self._root = None
                raise
        self._status = ViewStatus.COMMITTED
        # 终态后不再需要叠加视图：释放副本链；_pending 保留供日志诊断。
        self._root = None

    def discard(self) -> None:
        """丢弃本视图（Command 失败时调用），不改动真实 State。

        输入：无。
        输出：无。
        异常：
            TempStateError: 视图已进入终态。
        变量：
            无。
        """
        self._require_open("丢弃")
        self._status = ViewStatus.DISCARDED
        # 终态后不再需要叠加视图：释放副本链；_pending 保留供日志诊断。
        self._root = None

    def _require_open(self, action: str) -> None:
        """要求视图必须处于 OPEN 状态。

        输入：
            action: 正在执行的动作名（读取 / 探测 / 写入 / 提交 / 丢弃），用于报错消息。
        输出：
            无；状态是 OPEN 时直接返回。
        异常：
            TempStateError: 视图已进入终态。
        变量：
            无。
        """
        if self._status is not ViewStatus.OPEN:
            raise TempStateError(f"视图已处于 {self._status} 状态，不能再{action}")


def _freeze_delta(delta: Delta) -> Delta:
    """把一条变化量里可变的 value / old_value 快照下来，返回新的 Delta。

    输入：
        delta: 调用方构造好的变化量；其 value / old_value 可能是调用方
            仍然持有的容器对象。
    输出：
        新的 Delta：path / operation / data_type / trace / label / list_op / index
        原样保留，value / old_value 换成引擎自己的深拷贝副本。
    异常：
        无（快照失败时按 snapshot_value 自身的异常向上传播）。
    变量：
        value: 快照后的新值；列表删除等不带 value 的情况是 MISSING 哨兵，
            snapshot_value 对它原样返回；
        old_value: 快照后的旧值，规则同 value。
    说明：
        为什么必须在 record 时冻结：commit 是从记录重放的。若不冻结，
        调用方在 record 之后、commit 之前修改自己手上的容器，就会把已经记录
        的变化量改掉，出现"视图看到 A、提交写入 B"的静默分歧；
        old_value 被改还会让提交时的旧值核对失去意义。
        冻结之后，视图、pending_deltas() 与 command_delta() + commit 三条路径
        基于同一份数据；取回的变化量本身仍应视为只读（§16 的只读约定）。
    """
    value = snapshot_value(delta.value)
    old_value = snapshot_value(delta.old_value)
    # dataclass_replace 会重新跑 Delta 的字段校验；内容不变，只是换了副本。
    return dataclass_replace(delta, value=value, old_value=old_value)


def _copy_for_delta(root: dict, delta: Delta) -> dict:
    """为一条变化量复制出"路径上的容器副本链"，返回新的根。

    输入：
        root: 当前视图的根（dict）；
        delta: 即将应用的变化量。
    输出：
        新的根：根本身是新副本；从根到"会被这条变化量改动的容器"之间的每一层
        都是新副本，副本之间用树结构串起来；其余子树继续与旧根共享。
    异常：
        TypeError: root 不是 dict；
        PathSyntaxError / PathNotFoundError / StateShapeError: 路径走不通
            （此时没有做任何修改）。
    变量：
        tokens: 变化量路径的段元组；
        include_target: True 表示"变化量会就地改路径指向的那个容器"（列表插入 / 删除），
            所以连目标容器本身也要复制；False 表示改的是目标的父容器
            （新增 / 删除键、替换值），复制到父容器为止；
        stop: 需要复制的层数（从根算起）；
        level: 当前复制的层号；
        prefix: 当前复制的路径前缀；
        child: 该前缀当前指向的容器（仍与旧根共享）；
        copied_child: 它的浅拷贝。
    说明：
        这里刻意只使用 state 包的公开函数（get / replace），不自己解析下标，
        避免出现两套路径规则。代价是每一层都从根查一遍（深度^2 次查找）；
        State 深度通常很小，等有性能需求时再在模块内部优化（见模块文档）。
    """
    if not isinstance(root, dict):
        raise TypeError(f"TempState 的根必须是 dict，实际是 {type(root).__name__}")

    tokens = parse(delta.path)
    include_target = delta.list_op is not None
    stop = len(tokens) if include_target else len(tokens) - 1

    new_root = dict(root)  # 根永远要复制：写入可能替换根下的条目
    for level in range(1, stop + 1):
        prefix = format_path(tokens[:level])
        child = tree_get(new_root, prefix)
        copied_child = _shallow_clone(child, prefix)
        tree_replace(new_root, prefix, copied_child)
    return new_root


def _shallow_clone(container: Any, path: str) -> Any:
    """浅拷贝一个容器（dict / list）。

    输入：
        container: 待拷贝的容器；
        path: 它所在的路径，仅用于异常消息。
    输出：
        dict 返回 dict(container)，list 返回 list(container)；元素本身继续共享。
    异常：
        StateShapeError: container 不是 dict / list（说明路径穿过了标量）。
    变量：
        无。
    """
    if isinstance(container, dict):
        return dict(container)
    if isinstance(container, list):
        return list(container)
    raise StateShapeError(path, f"路径上的 {type(container).__name__} 不是容器，无法复制")
