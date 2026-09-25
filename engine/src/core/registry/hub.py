"""注册表中心（RegistryHub）：§14.2 清单里全部注册表的集合。

位置：
    引擎核心逻辑层 → registry 子包。

职责：
    - 持有 §14.2 清单里每一种注册表各一张表（REGISTRY_TYPES 是唯一的类型来源）；
    - 按条目的 type 字段把条目分派到对应的表里；
    - 给上层提供"按类型取表 / 按类型取条目"的统一入口。

边界：
    - 不读文件、不认识 Mod 目录结构（那是 Mod 加载层与文件端口的事）；
    - 不解析 extends（加载层在登记前调用 inheritance.resolve_entries）；
    - 不解释 data（P1）；不做运行期校验（D-14）。

草案依据：
    §14.2 注册表清单与"每个注册表的具体 data 结构暂不定义"；
    §5 引擎运行时对象（registry 由加载 Mod 时重建）；
    D-24 每次运行只加载一个 Mod。
"""

from typing import Iterator, Sequence

from .entry import REGISTRY_TYPES, RegistryEntry
from .errors import UnknownRegistryTypeError
from .table import RegistryTable


class RegistryHub:
    """注册表中心：按类型持有全部注册表。

    字段：
        _tables: 类型名 → RegistryTable；构造时按 types 顺序建立，顺序即遍历顺序。
    """

    def __init__(self, types: Sequence[str] = REGISTRY_TYPES) -> None:
        """建立注册表中心。

       输入：
            types: 要建立的注册表类型名序列；缺省是 §14.2 的完整清单。
        输出：
            无（构造对象）。
        异常：
            TypeError: types 不是序列，或其中有元素不是字符串；
            ValueError: types 里有空字符串或重复的类型名。
        变量：
            type_name: 当前正在建立的类型名。
        """
        if isinstance(types, str):
            raise TypeError("types 必须是类型名序列，不能直接给一个字符串")
        self._tables: dict[str, RegistryTable] = {}
        for type_name in types:
            if not isinstance(type_name, str):
                raise TypeError(f"注册表类型名必须是字符串，实际是 {type(type_name).__name__}")
            if not type_name:
                raise ValueError("注册表类型名不能是空字符串")
            if type_name in self._tables:
                raise ValueError(f"注册表类型名重复：{type_name!r}")
            self._tables[type_name] = RegistryTable(type_name)

    @property
    def types(self) -> tuple[str, ...]:
        """返回本中心持有的全部注册表类型名（建立顺序）。"""
        return tuple(self._tables.keys())

    def table(self, type_name: str) -> RegistryTable:
        """按类型取注册表。

        输入：
            type_name: 注册表类型名。
        输出：
            对应的 RegistryTable。
        异常：
            TypeError: type_name 不是字符串；
            UnknownRegistryTypeError: 本中心没有这种注册表。
        变量：
            无。
        """
        if not isinstance(type_name, str):
            raise TypeError(f"注册表类型名必须是字符串，实际是 {type(type_name).__name__}")
        table = self._tables.get(type_name)
        if table is None:
            raise UnknownRegistryTypeError(
                f"没有这种注册表：{type_name!r}；本中心持有的是 {', '.join(self._tables)}"
            )
        return table

    def register(self, entry: RegistryEntry) -> None:
        """按条目的 type 登记进对应的注册表。

        输入：
            entry: 已解析继承的条目。
        输出：
            无。
        异常：
            TypeError: entry 不是 RegistryEntry；
            UnknownRegistryTypeError: 条目的 type 不在本中心持有的类型里；
            DuplicateEntryError / EntryFormatError: 由目标注册表抛出。
        变量：
            无。
        """
        if not isinstance(entry, RegistryEntry):
            raise TypeError(f"RegistryHub.register 需要 RegistryEntry，实际是 {type(entry).__name__}")
        self.table(entry.type).register(entry)

    def register_all(self, entries: Sequence[RegistryEntry]) -> None:
        """按顺序批量登记。

        输入：
            entries: 条目序列；其中任何一条失败都会抛出异常。
        输出：
            无。
        异常：
            TypeError / UnknownRegistryTypeError / DuplicateEntryError / EntryFormatError。
        变量：
            entry: 当前正在登记的条目。
        """
        for entry in entries:
            self.register(entry)

    def get(self, type_name: str, entry_id: str) -> RegistryEntry:
        """按类型与 id 取条目（不区分是否启用）。

        输入：
            type_name: 注册表类型名；
            entry_id: 条目 id。
        输出：
            条目。
        异常：
            UnknownRegistryTypeError / MissingEntryError / TypeError。
        变量：
            无。
        """
        return self.table(type_name).get(entry_id)

    def require(self, type_name: str, entry_id: str) -> RegistryEntry:
        """按类型与 id 取"可用条目"（必须已启用）。

        输入：
            type_name: 注册表类型名；
            entry_id: 条目 id。
        输出：
            启用状态的条目。
        异常：
            UnknownRegistryTypeError / MissingEntryError / TypeError。
        变量：
            无。
        """
        return self.table(type_name).require(entry_id)

    def summary(self) -> dict[str, int]:
        """返回"类型名 → 条目数"的统计，供加载日志使用。

        输入：无。
        输出：
            新的 dict（只含条目数大于 0 的类型）。
        异常：
            无。
        变量：
            type_name / table: 当前遍历到的类型名与它的注册表。
        """
        return {
            type_name: len(table)
            for type_name, table in self._tables.items()
            if len(table) > 0
        }

    def total(self) -> int:
        """返回本中心持有的条目总数（含被禁用的）。

        输入：无。
        输出：
            条目总数。
        异常：
            无。
        变量：
            table: 当前遍历到的注册表。
        """
        return sum(len(table) for table in self._tables.values())

    def __iter__(self) -> Iterator[RegistryTable]:
        """按类型建立顺序迭代全部注册表。"""
        return iter(self._tables.values())

    def __repr__(self) -> str:
        """返回便于调试的短描述（类型数与条目总数）。"""
        return f"RegistryHub(types={len(self._tables)}, entries={self.total()})"
