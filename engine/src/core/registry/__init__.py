"""registry 包：注册表体系（§14）。

位置：
    引擎核心逻辑层 → registry 子包。它位于 core.state 之外——注册表装的是
    Mod 的静态数据（条目），State 装的是游戏事实；两者的唯一交汇点是
    初始化与公式/规则求值（RegistryHub 提供数据，写成 State 仍走变化量）。

职责：
    - 条目外壳与格式检查（entry.py）；
    - 条目继承（extends）的解析与合并（inheritance.py）；
    - 一张注册表：有序、去重、可解析引用（table.py）；
    - 注册表中心：§14.2 清单里的全部注册表（hub.py）。

草案依据：
    §14.1 通用条目外壳；§14.2 注册表清单（具体 data 结构留后处理，即 O-02）；
    D-24 每次运行只加载一个 Mod；D-36 条目要加来源与 schema 版本。
"""

from .entry import ENTRY_SCHEMA_VERSION, REGISTRY_TYPES, RegistryEntry
from .errors import (
    DuplicateEntryError,
    EntryFormatError,
    InheritanceError,
    MissingEntryError,
    RegistryError,
    UnknownRegistryTypeError,
)
from .hub import RegistryHub
from .inheritance import EntryLookup, merge_data, resolve_entries, resolve_entry
from .table import RegistryTable

__all__ = [
    # 常量与条目
    "ENTRY_SCHEMA_VERSION",
    "REGISTRY_TYPES",
    "RegistryEntry",
    # 异常
    "RegistryError",
    "EntryFormatError",
    "DuplicateEntryError",
    "MissingEntryError",
    "InheritanceError",
    "UnknownRegistryTypeError",
    # 注册表
    "RegistryTable",
    "RegistryHub",
    # 继承
    "EntryLookup",
    "merge_data",
    "resolve_entry",
    "resolve_entries",
]
