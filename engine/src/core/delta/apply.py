"""把变化量应用到 State 树。

位置：
    引擎核心逻辑层 → delta 子包。
职责：
    按一条 Delta 的描述调用 state 层的读写原语；不负责合并、提交事务与回滚。

为什么这样分层（规范第 3 条的复用结论）：
    路径解析与树读写已经在 core.state 里实现，本模块只做"变化量 → 原语"的分派，
    绝不重新实现寻址，这样两层的规则不会各写一套。

与上层的契约：
    - 函数直接修改传入的树；改真实 State 还是临时状态由调用方决定；
    - **写入的值一律先做深拷贝快照**（新增、修改、列表插入三处）：State 必须是树，
      Mod 复用手上的容器时（把模板 dict 写进 State、把树内子树复制到另一条路径等）
      不能让两条路径共享同一个对象；快照同时保证提交后的 State 不再受调用方
      后续就地修改的影响。标量不受影响，容器才有拷贝成本。快照逻辑集中在
      core.state 的 snapshot_value()（"State 是一棵树"的写入保证）：
       delta 的写入路径、temp_state 冻结记录、templates / modload 的初始化写入都复用它；
    - **单条变化量的应用是原子的**：先把当前值读出来核对，全部通过之后才动手改；
      校验失败时树保持原样（否则一旦在提交阶段发现不一致，真实 State 会留下半截改动）；
    - state 层的异常原样向上抛（PathNotFoundError / StateShapeError / StateConflictError），
      由 Command 级事务统一处理（§8 / §9）；
    - 旧值一致性：删除与替换会先读出当前值和 delta.old_value 比对，不一致抛
      StateConflictError。这样回放快照不对、临时状态顺序出错时会立刻暴露，
      而不是让数据悄悄漂移。比对是严格比对（见 values_match）：bool 与数值严格区分，
      int 与 float 同属 number 类型；这层校验不读取 data_type（它仍然只是元数据），
      只看值本身的类型与内容。

异常口径（与 state 层一致）：
    路径寻址失败        → PathNotFoundError（父容器不存在等）；
    目标类型不支持操作  → StateShapeError；
    操作参数与数据冲突  → StateConflictError（键已存在/不存在、参数下标越界、
    以及旧值与记录不一致）。
"""

from typing import Any

from ..state.errors import StateConflictError, StateShapeError
from ..state.path import format_path, parse
from ..state.tree import add_key, get, list_insert, list_remove, remove_key, replace
from ..state.values import snapshot_value
from .errors import DeltaError
from .model import Delta, ListOp, Operation


def apply_delta(tree: dict, delta: Delta) -> None:
    """把一条变化量应用到树上（就地修改）。

    输入：
        tree: State 树（或临时状态的底层树），根必须是 dict；
        delta: 已构造好的 Delta。
    输出：
        无；直接修改 tree。
    异常：
        DeltaError: delta 不是 Delta 实例。
        PathNotFoundError: 父容器或目标路径走不通。
        StateShapeError: 目标类型不支持该操作。
        StateConflictError: 键已存在/不存在、参数下标越界，或旧值与记录不一致；
            检查失败时树保持原样（不会留下半截改动）。
    变量：
        parent_path / key: 新增与删除键时，把 path 拆成"父容器路径 + 键名"；
        actual_old_value: 原语返回的旧值，用于一致性比对。
    说明：
        新增 / 修改 / 列表插入写入的 value 会先经 snapshot_value 深拷贝，
        树因此不会与 delta.value 共享容器对象（§6.2 的"State 是一棵树"）。
    """
    if not isinstance(delta, Delta):
        raise DeltaError(f"apply_delta 需要 Delta 实例，实际是 {type(delta).__name__}")

    # 列表插入 / 删除：path 指向列表本身，index 是动作参数。
    if delta.list_op is ListOp.INSERT:
        list_insert(tree, delta.path, delta.index, snapshot_value(delta.value))
        return
    if delta.list_op is ListOp.REMOVE:
        actual_old_value = _read_list_element(tree, delta.path, delta.index)
        _require_same_old_value(delta, actual_old_value)
        list_remove(tree, delta.path, delta.index)
        return

    # 非列表增删：新增 / 删除针对"键"，修改针对"值"。
    if delta.operation is Operation.ADD:
        parent_path, key = _split_parent(delta.path)
        add_key(tree, parent_path, key, snapshot_value(delta.value))
        return
    if delta.operation is Operation.REMOVE:
        parent_path, key = _split_parent(delta.path)
        actual_old_value = _read_dict_key(tree, parent_path, key)
        _require_same_old_value(delta, actual_old_value)
        remove_key(tree, parent_path, key)
        return
    if delta.operation is Operation.MODIFY:
        actual_old_value = get(tree, delta.path)
        _require_same_old_value(delta, actual_old_value)
        replace(tree, delta.path, snapshot_value(delta.value))
        return

    raise DeltaError(f"无法识别的操作：{delta.operation.value}", delta.path)


def _read_dict_key(tree: dict, parent_path: str, key: str) -> Any:
    """删除字典键之前先读出它的当前值。

    输入：
        tree: State 树；
        parent_path: 父容器路径（"" 表示根）；
        key: 要删除的键名。
    输出：
        该键当前的值。
    异常：
        PathNotFoundError: 父容器路径走不通；
        StateShapeError: 父容器不是 dict；
        StateConflictError: 键不存在。
    变量：
        container: 父容器对象。
    说明：
        先读后删是为了让 apply_delta 保持原子性——核对失败时树保持原样。
    """
    container = get(tree, parent_path)
    if not isinstance(container, dict):
        raise StateShapeError(parent_path, f"删除要求目标容器是 dict，实际是 {type(container).__name__}")
    if key not in container:
        raise StateConflictError(parent_path, f"删除失败：键 {key!r} 不存在")
    return container[key]


def _read_list_element(tree: dict, list_path: str, index: int) -> Any:
    """列表删除之前先读出被删元素的当前值。

    输入：
        tree: State 树；
        list_path: 列表本身的路径；
        index: 下标参数（模型层已保证是非负 int）。
    输出：
        该下标上的元素当前值。
    异常：
        PathNotFoundError: 列表路径走不通；
        StateShapeError: 目标不是 list；
        StateConflictError: 下标越界。
    变量：
        target: 列表对象。
    说明：
        先读后删是为了让 apply_delta 保持原子性——核对失败时树保持原样。
    """
    target = get(tree, list_path)
    if not isinstance(target, list):
        raise StateShapeError(list_path, f"列表删除要求目标是 list，实际是 {type(target).__name__}")
    if index < 0 or index >= len(target):
        raise StateConflictError(list_path, f"列表下标 {index} 越界（当前长度 {len(target)}）")
    return target[index]


def _split_parent(path: str) -> tuple[str, str]:
    """把"新键 / 被删键"的路径拆成父容器路径与键名。

    输入：
        path: 指向键本身的完整 JSON Pointer（例如 "/units/u2"）。
    输出：
        (父容器路径, 键名)。父容器路径会重新转义拼接，例如 ("/units", "u2")；
        根下的键得到 ("", "u2")。
    异常：
        DeltaError: path 指向根（模型层已禁止，这里兜底）。
    变量：
        tokens: parse 后的路径段元组；
        key: 最后一段，就是键名（已反转义）。
    """
    tokens = parse(path)
    if len(tokens) == 0:
        raise DeltaError("新增 / 删除的 path 不能指向根（空字符串）", path)
    return format_path(tokens[:-1]), tokens[-1]


def _require_same_old_value(delta: Delta, actual_old_value: Any) -> None:
    """核对原语返回的旧值与变化量记录的 old_value 是否一致。

    输入：
        delta: 当前变化量；其 old_value 必须存在（模型层已保证）。
        actual_old_value: state 层原语刚从树里取出的旧值。
    输出：
        无；一致时直接返回。
    异常：
        StateConflictError: 两者不一致，说明树的内容与变化量记录对不上
            （常见于回放快照不对，或临时状态的应用顺序出错）。
    变量：
        无。
    说明：
        使用严格比对（values_match）而不是裸 ==：True 与 1、1 与 "1" 这类
        类型不同但 == 成立的情况必须报冲突。
    """
    if not values_match(actual_old_value, delta.old_value):
        raise StateConflictError(
            delta.path,
            f"旧值与变化量记录不一致：记录为 {delta.old_value!r}，实际是 {actual_old_value!r}",
        )


def values_match(actual: Any, recorded: Any) -> bool:
    """严格比对两个值（变化量核对旧值、派生值比对新旧值都用它）。

    输入：
        actual: 树里当前的值；
        recorded: 变化量里记录的 old_value。
    输出：
        True 表示两者在"严格口径"下相等；False 表示不一致。
    异常：
        无。
    变量：
        无。
    规则：
        - bool 与数值严格区分：True 不等于 1（DataType 里 bool 与 number 是两类）；
        - int 与 float 同属 number 类型（DataType.NUMBER），按数值比较，1 == 1.0 通过，
          这样 JSON 存档往返不会因为整数/浮点写法差异产生假冲突；
        - dict 必须键集合相同且逐值递归相等；list 必须等长且逐元素递归相等；
        - 其余标量要求类型相同且相等；
        - NaN 与任何值（包括另一个 NaN）都不匹配，会让应用显式报冲突，而不是静默通过。
    """
    if isinstance(actual, bool) or isinstance(recorded, bool):
        # bool 是 int 的子类，必须先单独处理，避免 True 被当成 1。
        return type(actual) is type(recorded) and actual == recorded

    if isinstance(actual, (int, float)) and isinstance(recorded, (int, float)):
        return actual == recorded

    if isinstance(actual, dict) or isinstance(recorded, dict):
        if not isinstance(actual, dict) or not isinstance(recorded, dict):
            return False
        if actual.keys() != recorded.keys():
            return False
        return all(values_match(actual[key], recorded[key]) for key in actual)

    if isinstance(actual, list) or isinstance(recorded, list):
        if not isinstance(actual, list) or not isinstance(recorded, list):
            return False
        if len(actual) != len(recorded):
            return False
        return all(values_match(actual_item, recorded_item) for actual_item, recorded_item in zip(actual, recorded))

    return type(actual) is type(recorded) and actual == recorded
