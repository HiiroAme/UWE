"""变化量（Delta）的数据模型。

位置：
    引擎核心逻辑层 → delta 子包。
职责：
    定义一条变化量的字段、取值约束与自洽性校验；不负责序列化、应用与合并。

草案依据：
    §7.1 三种粒度；一个变化量只改一个变量；列表按下标定位、严格顺序、禁止合并；
    §7.2 操作 × 类型表；列表增删元素属于"修改该列表"（D-34）；
    §7.3 字段表（本模块在它基础上补了 list_op / index，用于表达列表增删）；
    §7.4 必须是可 JSON 化的纯数据；§13 sequence 是唯一排序依据。

本模块的四条格式约定（codec / apply / merge 共同遵守的契约）：
    1. 初版只有六种 data_type：number / string / bool / null / list / dict；
       集合 / 引用 / 对象等扩展类型留给 F-11。
    2. value / old_value 用"字段不存在"表达缺省，**不用 None**：
       插入只带 value，删除只带 old_value，替换两个都带。
       否则"插入一个值为 None 的元素"与"删除一个值为 None 的元素"无法区分。
    3. 列表上"整元素"的改动有两种写法，各司其职：
       插入 / 删除 → path 指向列表本身，list_op 指出方向，index 给出下标；
       元素替换   → 不加 list_op/index，path 直接指到那个元素（下标写在路径里）。
    4. path 是完整 JSON Pointer，且不允许指向根（空字符串）——
       根不是"一个变量"，替换整棵树不在变化量模型里表达。

data_type 的含义：
    指"被这条变化量修改的那个变量"的类型。新增键时它是键值的类型；
    列表增删改的是列表本身，因此固定为 list（插入的元素类型由 value 自身携带）。

    它是**元数据**：参与合并判断（类型不同的两条修改不能合并）、序列化与日志展示；
    应用（apply）不看它，也不校验值的实际 Python 类型与它是否一致——这是 D-14
    "运行时不校验 Mod 数据"的直接结果。也就是说 `data_type=number` + 值 `"abc"`
    能构造、能应用，属于有意为之；想早发现 Mod 写错值，要靠加载期静态检查或 Rule。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..state.errors import PathSyntaxError
from ..state.path import parse
from .errors import DeltaError

# 变化量的序列化格式版本。
# 为什么单独一个号：变化量会进入存档（§18.1 的结算批次变化量日志），
# 字段一旦变动，旧存档就读不懂了，必须能按版本拒绝或在将来做迁移。
DELTA_FORMAT_VERSION = 1


class _Missing:
    """哨兵类型：表示"这个字段不存在"，与"值就是 None"区分开。

    这个类保证全进程只有一个实例（就是模块级的 MISSING）：
        - `_Missing()` 永远返回同一个对象，防止有人重新构造出第二个哨兵；
        - 浅拷贝 / 深拷贝 / pickle 也返回同一个对象，防止"临时状态视图深拷贝
          变化量表"这类操作把哨兵复制成另一个对象，让 `is MISSING` 判断失效。
    """

    __slots__ = ()

    # 类级缓存：第一次构造时创建，之后 _Missing() 一律返回它。
    _instance: "_Missing | None" = None

    def __new__(cls) -> "_Missing":
        """保证全进程只有一个哨兵实例。

        输入：无。
        输出：MISSING 单例。
        变量：
            cls: 当前类对象（始终是 _Missing）。
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        """返回便于日志阅读的固定写法。"""
        return "MISSING"

    def __copy__(self) -> "_Missing":
        """浅拷贝返回自身，保持单例语义。"""
        return self

    def __deepcopy__(self, memo: dict) -> "_Missing":
        """深拷贝返回自身，保持单例语义。

        输入：
            memo: copy 模块传入的已拷贝对象表；本类用不到。
        输出：
            同一个哨兵对象。
        变量：
            无。
        """
        return self

    def __reduce__(self) -> tuple:
        """pickle 时按"重新调用 _Missing()"重建，从而仍然拿到同一个哨兵。"""
        return (_Missing, ())


# 单例哨兵：value / old_value 的默认值。
# 判断"字段是否存在"一律用 `is MISSING`，不要用真值判断（None 是合法值）。
MISSING = _Missing()


class _ValueStrEnum(str, Enum):
    """delta 包枚举的公共基类。

    功能：
        让 `str(成员)` 返回枚举取值（如 "add"），而不是 "Operation.ADD"；
        这样 f-string / 日志里直接写 `{成员}` 也稳定，不需要到处改成 `.value`，
        也不会因为将来换用 StrEnum 而改变行为。
    """

    def __str__(self) -> str:
        """返回枚举取值本身（如 "add"）。"""
        return self.value


class Operation(_ValueStrEnum):
    """变化量的操作类别（草案 §7.2 的三类操作）。

    取值用英文，中文含义只写在注释与文档里（正式代码与存档保持 ASCII，
    便于跨平台工具、日志检索与脚本处理）。
    """

    ADD = "add"        # 新增：往一个容器里加进一个键（键名 + 键值）
    REMOVE = "remove"  # 删除：从容器里删掉一个键（键名 + 键值）
    MODIFY = "modify"  # 修改（替换）：把已有值换成新值；列表增删也记在这一类（D-34）


class DataType(_ValueStrEnum):
    """变化量作用的数据类型（初版六种 JSON 兼容类型）。"""

    NUMBER = "number"  # 数值
    STRING = "string"  # 字符串
    BOOL = "bool"      # 布尔
    NULL = "null"      # 空值
    LIST = "list"      # 列表
    DICT = "dict"      # 字典


class ListOp(_ValueStrEnum):
    """列表增删的子操作。

    只有 operation=modify 且 data_type=list 时才允许出现；
    元素替换不使用它（见模块文档的格式约定 3）。
    """

    INSERT = "insert"  # 在 index 位置插入 value
    REMOVE = "remove"  # 删除 index 位置的元素，被删的值记在 old_value


@dataclass(frozen=True)
class Trace:
    """一条变化量的溯源信息（草案 §7.3 的 trace 块）。

    字段：
        sequence: 唯一排序依据；同一次运行内单调递增，由引擎在产出时分配。
        timestamp: 交互发生时间；只用于显示 / 回放 / 统计，**不参与排序**。
        command_id: 产生这条变化量的 Command 标识。
        action_id: 产生这条变化量的 Action 标识。
        service_id: 产生这条变化量的 Service 标识。
        mod_id: 来源 Mod 的标识。
    """

    sequence: int
    timestamp: float
    command_id: str
    action_id: str
    service_id: str
    mod_id: str

    def __post_init__(self) -> None:
        """构造后校验字段类型与取值范围。

        输入：无（读取 dataclass 已赋值的字段）。
        输出：无；通过校验直接返回。
        异常：DeltaError 当 sequence 不是非负 int、timestamp 不是数字，
            或者四个标识字段不是字符串。
        变量：
            name: 当前检查的标识字段名；
            value: 当前检查的标识字段值。
        """
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool):
            raise DeltaError(f"trace.sequence 必须是 int，实际是 {type(self.sequence).__name__}")
        if self.sequence < 0:
            raise DeltaError(f"trace.sequence 不能是负数：{self.sequence}")
        if not isinstance(self.timestamp, (int, float)) or isinstance(self.timestamp, bool):
            raise DeltaError(f"trace.timestamp 必须是数字，实际是 {type(self.timestamp).__name__}")

        for name, value in (
            ("command_id", self.command_id),
            ("action_id", self.action_id),
            ("service_id", self.service_id),
            ("mod_id", self.mod_id),
        ):
            if not isinstance(value, str):
                raise DeltaError(f"trace.{name} 必须是字符串，实际是 {type(value).__name__}")


@dataclass(frozen=True)
class Delta:
    """一条变化量：引擎记账的最小单位（对应草案 §7.3 的字段表）。

    字段说明：
        path: 完整 JSON Pointer。修改 / 删除指向被改的值；新增指向新键；
            列表插入 / 删除指向那个列表本身。
        operation: 操作类别，见 Operation。
        data_type: 被改动的那个变量的类型，见 DataType。只作元数据（合并、
            序列化、日志用）；应用时不读它，也不校验值的实际类型。
        trace: 溯源块，见 Trace。
        label: 事件 id，供 Trigger 路由；允许为空字符串（无事件）。
        value: 新值 / 插入的元素值；缺省时是 MISSING（不是 None）。
        old_value: 旧值 / 被删除的元素值；缺省时是 MISSING（不是 None）。
        list_op: 仅列表插入 / 删除使用；其余情况必须是 None。
        index: 仅列表插入 / 删除使用，非负整数；其余情况必须是 None。
    """

    path: str
    operation: Operation
    data_type: DataType
    trace: Trace
    label: str = ""
    value: Any = MISSING
    old_value: Any = MISSING
    list_op: ListOp | None = None
    index: int | None = None

    def __post_init__(self) -> None:
        """构造后校验字段自洽性，并把路径补进异常。

        输入：无（读取 dataclass 已赋值的字段）。
        输出：无；通过校验直接返回。
        异常：DeltaError 只要有一处不符合本模块文档里的四条格式约定；
            异常的 path 属性会带上本条变化量的路径（path 本身不合法时为空串）。
        变量：
            known_path: 可用于异常消息的路径字符串。
        """
        known_path = self.path if isinstance(self.path, str) else ""
        try:
            self._validate_fields()
        except DeltaError as exc:
            if exc.path:
                raise
            raise DeltaError(exc.detail, known_path) from exc

    def _validate_fields(self) -> None:
        """逐条校验字段自洽性（只被 __post_init__ 调用）。

        输入：无（读取 dataclass 已赋值的字段）。
        输出：无；通过校验直接返回。
        异常：DeltaError 字段组合不合法。
        变量：
            tokens: parse 出来的路径段，仅用于校验路径是否合法、是否指向根。
        """
        if not isinstance(self.path, str):
            raise DeltaError(f"path 必须是字符串，实际是 {type(self.path).__name__}")
        try:
            tokens = parse(self.path)
        except PathSyntaxError as exc:
            raise DeltaError(f"path 不是合法的 JSON Pointer：{exc.detail}（path={self.path!r}）") from exc
        if len(tokens) == 0:
            raise DeltaError("path 不能指向根（空字符串），因为根不是一个变量")

        if not isinstance(self.operation, Operation):
            raise DeltaError(f"operation 必须是 Operation 枚举，实际是 {type(self.operation).__name__}")
        if not isinstance(self.data_type, DataType):
            raise DeltaError(f"data_type 必须是 DataType 枚举，实际是 {type(self.data_type).__name__}")
        if not isinstance(self.trace, Trace):
            raise DeltaError(f"trace 必须是 Trace 实例，实际是 {type(self.trace).__name__}")
        if not isinstance(self.label, str):
            raise DeltaError(f"label 必须是字符串，实际是 {type(self.label).__name__}")
        if self.list_op is not None and not isinstance(self.list_op, ListOp):
            raise DeltaError(f"list_op 必须是 ListOp 枚举或 None，实际是 {type(self.list_op).__name__}")

        # 列表插入 / 删除：改的是列表本身，index 是动作参数。
        if self.list_op is not None:
            if self.operation is not Operation.MODIFY:
                raise DeltaError(f"列表增删必须用 operation=modify，实际是 {self.operation.value}")
            if self.data_type is not DataType.LIST:
                raise DeltaError(f"列表增删必须用 data_type=list，实际是 {self.data_type.value}")
            _require_index(self.index)

            if self.list_op is ListOp.INSERT:
                if self.value is MISSING:
                    raise DeltaError("列表插入必须带 value（被插入的元素值）")
                if self.old_value is not MISSING:
                    raise DeltaError("列表插入不允许带 old_value（用字段不存在表达，不是 null）")
            else:
                if self.old_value is MISSING:
                    raise DeltaError("列表删除必须带 old_value（被移除的元素值）")
                if self.value is not MISSING:
                    raise DeltaError("列表删除不允许带 value（用字段不存在表达，不是 null）")
            return

        # 非列表增删：index 不允许出现，value / old_value 的存在性按操作类别决定。
        if self.index is not None:
            raise DeltaError("没有 list_op 的变化量不允许带 index")

        if self.operation is Operation.ADD:
            if self.value is MISSING:
                raise DeltaError("新增必须带 value（新键的键值）")
            if self.old_value is not MISSING:
                raise DeltaError("新增不允许带 old_value（键在新增前不存在）")
        elif self.operation is Operation.REMOVE:
            if self.old_value is MISSING:
                raise DeltaError("删除必须带 old_value（被删掉的键值）")
            if self.value is not MISSING:
                raise DeltaError("删除不允许带 value（用字段不存在表达，不是 null）")
        else:
            if self.value is MISSING or self.old_value is MISSING:
                raise DeltaError("修改必须同时带 value 与 old_value")


def _require_index(index: Any) -> None:
    """校验列表增删的 index 必须是非负整数。

    输入：
        index: 待校验的下标参数。
    输出：
        无；通过校验直接返回。
    异常：
        DeltaError: index 不是 int、是 bool（bool 是 int 的子类但语义上不是下标），
            或者为负数。
    变量：
        无。
    """
    if not isinstance(index, int) or isinstance(index, bool):
        raise DeltaError(f"列表增删的 index 必须是 int，实际是 {type(index).__name__}")
    if index < 0:
        raise DeltaError(f"列表增删的 index 不能是负数：{index}")


def data_type_of(value: Any) -> "DataType":
    """按一个值算出它的变化量类型（DataType）。

    输入：
        value: 任意纯数据值（None / bool / int / float / str / list / dict）。
    输出：
        对应的 DataType 成员。
    异常：
        DeltaError: 不是 JSON 兼容的纯数据（例如自定义对象）。
    变量：
        无。

    用途：
        调用方只知道"新值是什么"，却必须给变化量填 data_type（元数据，参与合并与日志）；
        派生值刷新与 Mod 脚本都用得上这个换算，所以放在模型这一层，只有一份口径。
    """
    if value is None:
        return DataType.NULL
    if isinstance(value, bool):
        return DataType.BOOL
    if isinstance(value, (int, float)):
        return DataType.NUMBER
    if isinstance(value, str):
        return DataType.STRING
    if isinstance(value, list):
        return DataType.LIST
    if isinstance(value, dict):
        return DataType.DICT
    raise DeltaError(f"不是 JSON 兼容的纯数据：{type(value).__name__}")
