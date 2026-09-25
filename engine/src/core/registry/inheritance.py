"""条目继承（extends）的解析与合并。

位置：
    引擎核心逻辑层 → registry 子包；由加载层在"登记进表之前"调用一次。

职责：
    把 `extends` 链展开成一条**数据已经合并好**的条目：
    从最顶层的父条目往下，逐层把 data 合并进子条目，子条目的值覆盖父条目；
    同时检测父条目缺失、类型不一致与继承成环。

为什么在加载期一次算完，而不是运行期逐层查：
    1. 运行期（Rule / Orchestrator / Applier）不该重复做同一件事（P8）；
    2. 加载期一次解析等于一次静态检查：父条目写错、继承成环会立刻报错，
       而不是等某个 Command 跑到一半才发现（P6 日志与失败原因）；
    3. 解析后表里存的就是"最终条目"，运行期读取路径只有一条，最省事也最确定。

合并口径（O-02 尚未定稿，先按下面这条实现，需要时再改）：
    - 两边都是 dict 时**递归合并**，键相同的继续往下走；
    - 其余情况（列表、数字、字符串、布尔、null，以及"一边是 dict 另一边不是"）
      **整体由子条目覆盖**；列表不做追加。
    这样"给一批单位加一件通用装备"这类写法不会意外合并成两份列表。

草案依据：
    §14.1 extends 字段；§14.2 注册表条目"要加来源与 schema 版本"；
    O-04 已并入 O-02（条目级继承与覆盖规则属于注册表 JSON 结构问题）。
"""

from copy import deepcopy
from typing import Callable, Iterable

from .entry import RegistryEntry
from .errors import DuplicateEntryError, InheritanceError

# 查父条目的回调：给 id，返回条目或 None（表示查不到）。
EntryLookup = Callable[[str], RegistryEntry | None]


def merge_data(base: dict, child: dict) -> dict:
    """按"dict 递归、其余整体覆盖"的口径合并两份 data。

    输入：
        base: 父条目的 data（不会被修改）；
        child: 子条目的 data（不会被修改，值优先）。
    输出：
        新的 dict：两边键的并集，同名键按上面的口径取合并结果。
    异常：
        TypeError: base / child 不是 dict。
    变量：
        result: 结果字典（先深拷贝 base，再逐键并入 child）；
        key: 正在合并的键名；
        value: child 在该键上的值。
    """
    if not isinstance(base, dict):
        raise TypeError(f"merge_data 的 base 必须是 dict，实际是 {type(base).__name__}")
    if not isinstance(child, dict):
        raise TypeError(f"merge_data 的 child 必须是 dict，实际是 {type(child).__name__}")

    result: dict = deepcopy(base)
    for key, value in child.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = merge_data(current, value)
        else:
            result[key] = deepcopy(value)
    return result


def resolve_entry(entry: RegistryEntry, lookup: EntryLookup) -> RegistryEntry:
    """把一条条目连同它的 extends 链解析成"数据已合并"的新条目。

    输入：
        entry: 待解析的条目（可以是链中间的一环，也可以是链顶）；
        lookup: 查父条目的回调；传入的 id 查不到时应返回 None。
    输出：
        新的 RegistryEntry：data / metadata 已按链合并，其余字段取本条目的，
        并且 extends 变为 None（已经展开过，不再需要运行期再查）。
    异常：
        TypeError: entry 不是 RegistryEntry，或 lookup 不是可调用对象。
        InheritanceError: 父条目查不到、父条目类型不一致、或继承成环。
    变量：
        chain: 从子到父的条目列表（含本条）；
        seen: 已经走过的 id 集合，用来检测环；
        current: 当前正在向上走的条目；
        parent: lookup 查到的父条目；
        merged: 逐层合并中的 data；
        merged_metadata: 逐层合并中的 metadata。
    """
    if not isinstance(entry, RegistryEntry):
        raise TypeError(f"resolve_entry 需要 RegistryEntry，实际是 {type(entry).__name__}")
    if not callable(lookup):
        raise TypeError("resolve_entry 的 lookup 必须是可调用对象")

    chain: list[RegistryEntry] = [entry]
    seen: set[str] = {entry.id}
    current = entry
    while current.extends is not None:
        parent = lookup(current.extends)
        if parent is None:
            raise InheritanceError(
                f"extends 指向的父条目没有找到：{current.extends!r}",
                entry_id=current.id,
                source=current.source,
            )
        if parent.type != entry.type:
            raise InheritanceError(
                f"父条目 {parent.id!r} 的类型是 {parent.type!r}，与子条目类型 {entry.type!r} 不一致",
                entry_id=entry.id,
                source=entry.source,
            )
        if parent.id in seen:
            raise InheritanceError(
                "extends 链成环：" + " -> ".join([item.id for item in chain] + [parent.id]),
                entry_id=entry.id,
                source=entry.source,
            )
        seen.add(parent.id)
        chain.append(parent)
        current = parent

    merged: dict = {}
    merged_metadata: dict = {}
    for item in reversed(chain):  # 从最顶层的父条目开始，逐层往下覆盖
        merged = merge_data(merged, item.data)
        merged_metadata = merge_data(merged_metadata, item.metadata)

    return RegistryEntry(
        id=entry.id,
        type=entry.type,
        data=merged,
        metadata=merged_metadata,
        extends=None,
        enabled=entry.enabled,
        source=entry.source,
        schema_version=entry.schema_version,
    )


def resolve_entries(
    entries: Iterable[RegistryEntry],
    external_lookup: EntryLookup | None = None,
) -> tuple[RegistryEntry, ...]:
    """批量解析一组条目，允许组内的条目互相继承。

    输入：
        entries: 待解析的条目（通常是"一个 Mod 文件里读出来的全部条目"）；
        external_lookup: 可选的额外查找来源（通常是"已经登记进注册表的条目"），
            当组内查不到父条目时再问它。
    输出：
        按输入顺序排列的解析结果元组；每条只解析一次（父条目被多次继承时复用结果）。
    异常：
        InheritanceError: 任意一条的继承链不成立。
        DuplicateEntryError: 同一批里出现重复 id（同一张表里 id 必须唯一）。
    变量：
        batch: 组内条目按 id 建立的索引；
        cache: 已解析结果的缓存（id → 条目）；
        entry: 当前正在解析的条目；
        lookup: 查父条目的回调（先组内、后外部）。
    """
    batch: dict[str, RegistryEntry] = {}
    for entry in entries:
        if not isinstance(entry, RegistryEntry):
            raise TypeError(f"resolve_entries 的每个元素都必须是 RegistryEntry，实际是 {type(entry).__name__}")
        if entry.id in batch:
            raise DuplicateEntryError(
                "同一批条目里出现重复 id",
                entry_id=entry.id,
                source=entry.source,
            )
        batch[entry.id] = entry

    cache: dict[str, RegistryEntry] = {}

    def lookup(entry_id: str) -> RegistryEntry | None:
        """查父条目：先在本批条目里找，再问外部查找来源。"""
        if entry_id in batch:
            return batch[entry_id]
        if external_lookup is not None:
            return external_lookup(entry_id)
        return None

    results: list[RegistryEntry] = []
    for entry in batch.values():
        if entry.id not in cache:
            cache[entry.id] = resolve_entry(entry, lookup)
        results.append(cache[entry.id])
    return tuple(results)
