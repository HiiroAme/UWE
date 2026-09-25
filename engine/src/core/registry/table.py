"""一张注册表：同一类型条目的有序容器（§14.2 清单里的一格）。

位置：
    引擎核心逻辑层 → registry 子包。

职责：
    - 收下同一 type 的条目，保证 id 唯一（重复即 Mod 数据写错，D-24 单 Mod 运行）；
    - 按**注册顺序**遍历（顺序是显式的、确定的，满足 §13 的确定性约束）；
    - 提供"取条目"的两种口径：
        get()     只要注册过就返回（含 enabled=false 的条目，供诊断）；
        require() 必须注册过**且启用**（供引擎各处解析引用）；
    - 内容完全由 Mod 决定，注册表不解释、不校验 data（P1 / D-14）。

边界：
    - 不解析 extends（由 inheritance 在加载期完成，表里存的是解析后的条目）；
    - 不校验条目之间的引用（那是引用方的事，例如 Orchestrator 查 action 引用）；
    - 不写日志：注册表只抛异常，由加载层记录（P6 的日志由上层统一写）。

草案依据：
    §14.1 通用条目外壳；§14.2 注册表清单与"引用用路径或名字"；
    D-24 每次运行只加载一个 Mod；D-25 版本不匹配直接报错；
    §13 确定性（遍历顺序必须确定）。
"""

from typing import Iterator, Sequence

from .entry import RegistryEntry
from .errors import DuplicateEntryError, EntryFormatError, MissingEntryError


class RegistryTable:
    """一种注册表类型的条目容器。

    字段：
        _type: 本表负责的条目 type（构造后不可变）；
        _entries: id → 条目；Python 字典保持插入顺序，因此它就是"注册顺序"。
    """

    def __init__(self, type_name: str) -> None:
        """创建一张注册表。

        输入：
            type_name: 注册表类型名（非空字符串，通常是 §14.2 清单里的名字）。
        输出：
            无（构造对象）。
        异常：
            TypeError: type_name 不是字符串。
            ValueError: type_name 是空字符串。
        变量：
            无。

        说明：
            这里不检查 type_name 是否在 REGISTRY_TYPES 里——登记清单是 RegistryHub
            的职责；单独使用本类做测试时允许任意名字。
        """
        if not isinstance(type_name, str):
            raise TypeError(f"注册表类型名必须是字符串，实际是 {type(type_name).__name__}")
        if not type_name:
            raise ValueError("注册表类型名不能是空字符串")
        self._type: str = type_name
        self._entries: dict[str, RegistryEntry] = {}

    @property
    def type_name(self) -> str:
        """返回本表的注册表类型名（只读）。"""
        return self._type

    def register(self, entry: RegistryEntry) -> None:
        """登记一条条目。

        输入：
            entry: 已经解析完继承（如果有）的条目；其 type 必须与本表一致。
        输出：
            无。
        异常：
            TypeError: entry 不是 RegistryEntry。
            EntryFormatError: 条目 type 与本表不一致。
            DuplicateEntryError: 同 id 已经登记过。
        变量：
            无。
        """
        if not isinstance(entry, RegistryEntry):
            raise TypeError(f"RegistryTable.register 需要 RegistryEntry，实际是 {type(entry).__name__}")
        if entry.type != self._type:
            raise EntryFormatError(
                f"条目 type {entry.type!r} 与注册表 {self._type!r} 不一致",
                entry_id=entry.id,
                source=entry.source,
            )
        if entry.id in self._entries:
            raise DuplicateEntryError(
                "条目 id 重复（每个 id 在一张注册表里只能登记一次）",
                entry_id=entry.id,
                source=entry.source,
            )
        self._entries[entry.id] = entry

    def register_all(self, entries: Sequence[RegistryEntry]) -> None:
        """按顺序批量登记。

        输入：
            entries: 条目序列；其中任何一条失败都会抛出异常（已登记的保持登记状态）。
        输出：
            无。
        异常：
            TypeError / EntryFormatError / DuplicateEntryError: 与 register 相同。
        变量：
            entry: 当前正在登记的条目。
        """
        for entry in entries:
            self.register(entry)

    def find(self, entry_id: str) -> RegistryEntry | None:
        """按 id 取条目（含被禁用的），取不到返回 None。

        输入：
            entry_id: 条目 id。
        输出：
            RegistryEntry 或 None。
        异常：
            TypeError: entry_id 不是字符串。
        变量：
            无。
        """
        if not isinstance(entry_id, str):
            raise TypeError(f"条目 id 必须是字符串，实际是 {type(entry_id).__name__}")
        return self._entries.get(entry_id)

    def get(self, entry_id: str) -> RegistryEntry:
        """按 id 取条目；不区分是否启用。

        输入：
            entry_id: 条目 id。
        输出：
            条目（可能是 enabled=false 的）。
        异常：
            MissingEntryError: 没登记过这个 id。
        变量：
            无。
        """
        entry = self.find(entry_id)
        if entry is None:
            raise MissingEntryError(
                f"注册表 {self._type!r} 里没有这个 id（可能是 Mod 里引用写错了）",
                entry_id=entry_id,
            )
        return entry

    def require(self, entry_id: str) -> RegistryEntry:
        """按 id 取"可用条目"：必须登记过且 enabled=true。

        输入：
            entry_id: 条目 id。
        输出：
            启用状态的条目。
        异常：
            MissingEntryError: 没登记过，或登记了但被禁用（消息里写明是哪种）。
        变量：
            无。

        说明：
            引擎各处解析引用一律用本方法（被引用的东西必须真的可用）；
            需要"即使禁用也要看内容"的诊断场景用 get / find。
        """
        entry = self.find(entry_id)
        if entry is None:
            raise MissingEntryError(
                f"注册表 {self._type!r} 里没有这个 id（可能是 Mod 里引用写错了）",
                entry_id=entry_id,
            )
        if not entry.enabled:
            raise MissingEntryError(
                f"注册表 {self._type!r} 里的这个条目已被 enabled=false 禁用",
                entry_id=entry_id,
                source=entry.source,
            )
        return entry

    def has(self, entry_id: str) -> bool:
        """判断 id 是否登记过（不区分是否启用）。

        输入：
            entry_id: 条目 id。
        输出：
            True / False。
        异常：
            TypeError: entry_id 不是字符串。
        变量：
            无。
        """
        return self.find(entry_id) is not None

    def all(self) -> tuple[RegistryEntry, ...]:
        """返回全部条目（注册顺序，含被禁用的）。

        输入：无。
        输出：
            条目元组；调用方拿到的是快照，不能通过它改动注册表。
        异常：
            无。
        变量：
            无。
        """
        return tuple(self._entries.values())

    def enabled(self) -> tuple[RegistryEntry, ...]:
        """返回全部启用条目（注册顺序）。

        输入：无。
        输出：
            条目元组（只含 enabled=true 的）。
        异常：
            无。
        变量：
            entry: 当前遍历到的条目。
        """
        return tuple(entry for entry in self._entries.values() if entry.enabled)

    def ids(self) -> tuple[str, ...]:
        """返回全部 id（注册顺序，含被禁用的）。

        输入：无。
        输出：
            id 元组。
        异常：
            无。
        变量：
            无。
        """
        return tuple(self._entries.keys())

    def __len__(self) -> int:
        """返回条目总数（含被禁用的）。"""
        return len(self._entries)

    def __contains__(self, entry_id: object) -> bool:
        """支持 `"my_mod:rule:x" in table` 写法（不区分是否启用）。"""
        return isinstance(entry_id, str) and entry_id in self._entries

    def __iter__(self) -> Iterator[RegistryEntry]:
        """按注册顺序迭代全部条目（含被禁用的）。"""
        return iter(self._entries.values())

    def __repr__(self) -> str:
        """返回便于调试的短描述（类型名与条目数）。"""
        return f"RegistryTable({self._type!r}, entries={len(self._entries)})"
