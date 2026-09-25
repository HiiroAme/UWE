"""注册表条目的通用外壳（§14.1）。

位置：
    引擎核心逻辑层 → registry 子包。

职责：
    定义"所有注册表条目共用的一层结构"：id / type / metadata / extends /
    enabled / data，再加引擎自动填的 source 与 schema_version；
    在构造时做完成**格式检查**（字段类型与 id 结构）。

边界（P2 数据包原则）：
    - 条目是纯数据：本模块只读不写，data / metadata 里放什么由 Mod 决定，
      引擎不解释其中任何一个键（P1 / D-14）；
    - 本模块不查重、不解析继承、不查引用——那是 RegistryTable（查重）、
      inheritance（继承）与上层（引用）的事；
    - 本模块不校验 data 里的值与类型，那是加载期静态检查（D-29，由 Mod 给出）
      与 Rule 的事。

草案依据：
    §14.1 通用条目外壳与字段表；
    §14.2 注册表清单（REGISTRY_TYPES 就是清单里的类型名）；
    D-36 条目要加"来源"与"schema 版本"字段；
    P2 / P5（所有条目结构一致，地位相同的对象格式统一）。
"""

from dataclasses import dataclass, field
from typing import Any

from .errors import EntryFormatError

# 条目结构自身的版本号（D-36 的"schema 版本"）。
# 为什么单独一个号：条目会随 Mod 数据一起进入存档校验，字段一旦变动，
# 旧 Mod 数据与新引擎的对应关系必须能按版本判断，不能靠猜。
ENTRY_SCHEMA_VERSION = 1

# 注册表清单（§14.2）：type 字段的合法取值就是这些名字。
# 顺序即"清单里的书写顺序"，供日志与文档展示使用，不代表加载优先级。
REGISTRY_TYPES: tuple[str, ...] = (
    "action",    # 每个 Action 先后调用的 Service 及数据
    "rule",      # Action 前的玩法约束检查
    "entity",    # 实体（单位、建筑、设施……）的属性定义
    "node",      # 节点的属性定义
    "input",     # 某类 UI 交互 / AI 输入产生哪个 Command 及其条件
    "command",   # 一个 Command 拆解成哪些 Action
    "event",     # 事件定义（事件 id 与含义）
    "trigger",   # 订阅哪个事件、执行什么、订阅顺序
    "system",    # 系统级变量与全局参数的初始值
    "formula",   # 可复用公式
    "service",   # 自定义服务脚本
    "phase",     # 可选：便捷设置可用行动等时间内容
    "terrain",   # 可选：便捷编辑 node 注册表
    "syscall",   # 系统事件注册表（保存、重载、清理内存、退出）
    "ui",        # 游戏内 UI 的 Page / Layer 注册
)

# id 的三种命名空间的书写要求：以冒号分成三段。
_ID_PART_COUNT = 3


@dataclass(frozen=True)
class RegistryEntry:
    """一条注册表条目（所有注册表共用的外壳）。

    字段：
        id: 全局唯一标识，写法 `namespace:type:name`；第二段必须与 type 字段一致，
            这样单看一个 id 就能知道它属于哪张注册表；
        type: 注册表类型，取值必须是 REGISTRY_TYPES 里的名字；
        data: 该类型自有的负载（dict）；引擎事先不知道其中内容（P1）；
        metadata: 显示用元信息（名称、描述、作者、版本、标签……），引擎不关心内容；
        extends: 父条目 id；缺省表示不继承；
        enabled: 是否启用；缺省 True；禁用条目被引用时按"不存在"处理；
        source: 来源（Mod 标识与文件路径），由加载层自动填，用于日志溯源（D-36）；
        schema_version: 条目结构版本，由加载层填或留默认值（D-36）。

    说明：
        条目是 frozen dataclass：字段本身不可重新赋值。data / metadata 是容器，
        构造后按 §16 的只读约定使用，不要就地修改。
    """

    id: str
    type: str
    data: dict
    metadata: dict = field(default_factory=dict)
    extends: str | None = None
    enabled: bool = True
    source: str = ""
    schema_version: int = ENTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """构造后校验字段类型与 id 结构。

        输入：无（读取 dataclass 已赋值的字段）。
        输出：无；全部合法时直接返回。
        异常：
            EntryFormatError: 字段类型不对（如 id 不是字符串、data 不是 dict）——
                以前这些抛裸 TypeError，宿主得同时捕两种（R5-13）；
            EntryFormatError: id 结构不合法，或 id 的第二段与 type 不一致，
                或 schema_version 不是正整数，或 enabled 不是布尔。
        变量：
            parts: id 按冒号切成的段列表。
        """
        if not isinstance(self.id, str):
            raise EntryFormatError(f"条目 id 必须是字符串，实际是 {type(self.id).__name__}")
        if not isinstance(self.type, str):
            raise EntryFormatError(f"条目 type 必须是字符串，实际是 {type(self.type).__name__}")
        if not isinstance(self.data, dict):
            raise EntryFormatError(f"条目 data 必须是 dict，实际是 {type(self.data).__name__}")
        if not isinstance(self.metadata, dict):
            raise EntryFormatError(f"条目 metadata 必须是 dict，实际是 {type(self.metadata).__name__}")
        if self.extends is not None and not isinstance(self.extends, str):
            raise EntryFormatError(f"条目 extends 必须是字符串或 None，实际是 {type(self.extends).__name__}")
        if not isinstance(self.enabled, bool):
            raise EntryFormatError("enabled 必须是布尔值", entry_id=_safe_id(self.id))
        if not isinstance(self.source, str):
            raise EntryFormatError(f"条目 source 必须是字符串，实际是 {type(self.source).__name__}")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError(
                f"条目 schema_version 必须是 int，实际是 {type(self.schema_version).__name__}"
            )

        if not self.id:
            raise EntryFormatError("条目 id 不能是空字符串")
        parts = self.id.split(":")
        if len(parts) != _ID_PART_COUNT or any(part == "" for part in parts):
            raise EntryFormatError(
                f"条目 id 必须写成 namespace:type:name 三段且每段非空，实际是 {self.id!r}",
                entry_id=self.id,
                source=self.source,
            )
        if self.type not in REGISTRY_TYPES:
            raise EntryFormatError(
                f"未登记的注册表类型 {self.type!r}；合法取值见 §14.2 清单",
                entry_id=self.id,
                source=self.source,
            )
        if parts[1] != self.type:
            raise EntryFormatError(
                f"条目 id 的第二段 {parts[1]!r} 必须与 type {self.type!r} 一致",
                entry_id=self.id,
                source=self.source,
            )
        if self.extends is not None and self.extends == self.id:
            raise EntryFormatError("条目不能继承自己", entry_id=self.id, source=self.source)
        if self.schema_version <= 0:
            raise EntryFormatError(
                f"schema_version 必须是正整数，实际是 {self.schema_version}",
                entry_id=self.id,
                source=self.source,
            )

    @property
    def name(self) -> str:
        """返回 id 的第三段（name），便于日志与报错简短书写。

        输入：无。
        输出：
            id 里 `namespace:type:name` 的 name 部分；构造时已保证存在。
        异常：
            无。
        变量：
            无。
        """
        return self.id.split(":")[-1]

    @property
    def display_name(self) -> str:
        """返回 metadata 里的显示名；没有就退回 name。

        输入：无。
        输出：
            字符串：metadata["name"]（当它是非空字符串时），否则是 id 的 name 段。
        异常：
            无。
        变量：
            无。
        """
        value: Any = self.metadata.get("name")
        if isinstance(value, str) and value:
            return value
        return self.name


def _safe_id(value: Any) -> str:
    """在构造失败时尽量取到一个可打印的 id 片段。

    输入：
        value: 可能是 id 的任意值。
    输出：
        字符串：value 本身（当它是字符串时），否则空字符串。
    异常：
        无。
    变量：
        无。

    说明：
        只用于拼报错消息；不参与任何判定。
    """
    return value if isinstance(value, str) else ""
