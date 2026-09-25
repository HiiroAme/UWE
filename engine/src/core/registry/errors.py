"""注册表失败的异常分类（core.registry 专用）。

位置：
    引擎核心逻辑层 → registry 子包。

职责：
    把"注册表这一层会出的错"分成互不重叠的几类，让日志能直接说清是
    "条目写法不对""条目重复""引用不到""继承链有问题"还是"注册表类型没登记"。

草案依据：
    §14.1 通用条目外壳（id / type / data 等字段的必填与含义）；
    §14.2 注册表清单（每个注册表的类型名是一张固定的表）；
    §19.2 错误处理（加载失败属于"记录并停止"的加载期错误，不属于运行管线）。

与 state / delta 层异常的分界：
    - state 层的异常描述"数据树寻址失败"；
    - delta 层的异常描述"一条变化量自己不成立"；
    - 本模块的异常只描述"注册表条目本身或条目之间引用不成立"，
      三者不互相替代，也不互相包裹。
"""


class RegistryError(Exception):
    """注册表相关失败的基类。

    字段：
        detail: 人类可读的说明（日志与报错消息都用它）；
        entry_id: 出问题的条目 id；不知道时是空字符串；
        source: 条目来源（Mod 标识与文件路径，由加载层填）；不知道时是空字符串。
    """

    def __init__(self, detail: str, *, entry_id: str = "", source: str = "") -> None:
        """构造一个注册表异常。

        输入：
            detail: 说明文字（非空）。
            entry_id: 相关条目 id，缺省为空字符串。
            source: 相关条目的来源，缺省为空字符串。
        输出：
            无（构造对象）。
        异常：
            无（构造异常对象本身不再抛异常）。
        变量：
            无。
        """
        self.detail: str = detail
        self.entry_id: str = entry_id
        self.source: str = source
        super().__init__(self.__str__())

    def __str__(self) -> str:
        """拼出"类型 + 条目 + 来源 + 说明"的完整消息，供日志直接使用。"""
        parts = [self.__class__.__name__]
        if self.entry_id:
            parts.append(f"条目={self.entry_id}")
        if self.source:
            parts.append(f"来源={self.source}")
        parts.append(self.detail)
        return " | ".join(parts)


class EntryFormatError(RegistryError):
    """条目本身写法不合法（字段缺失、类型不对、id 结构不对）。

    典型情形：
        id 不是"namespace:type:name"三段；id 的第二段与 type 字段不一致；
        data 不是 dict；schema_version 不是正整数。
    """


class DuplicateEntryError(RegistryError):
    """同一个注册表里出现重复 id。

    说明：
        每次运行只加载一个 Mod（D-24），所以不存在"多 Mod 覆盖"的合法场景；
        重复 id 一律视为 Mod 数据写错。
    """


class MissingEntryError(RegistryError):
    """引用了一个不存在的条目（或引用了一个已被禁用的条目）。

    说明：
        条目被 enabled=false 禁用时，引擎按"等同于不存在"处理，
        但报错消息里会写清是"未注册"还是"已禁用"，便于 Mod 作者定位。
    """


class InheritanceError(RegistryError):
    """extends 继承链不成立。

    典型情形：
        父条目不存在、父条目类型与子条目不同、继承成环。
    """


class UnknownRegistryTypeError(RegistryError):
    """出现了清单之外的注册表类型（§14.2 的清单是固定的）。

    说明：
        这条属于引擎与 Mod 的契约被破坏：条目 type 既不在 REGISTRY_TYPES 里，
        也没有对应的注册表可放。
    """
