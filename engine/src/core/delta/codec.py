"""变化量的序列化与反序列化。

位置：
    引擎核心逻辑层 → delta 子包。
职责：
    在 Delta 对象与 JSON 兼容的 dict 之间来回转换，供存档、回放、日志使用。

格式约定（与 model.py 的格式约定配套）：
    - 每条记录自带 "format_version"，值必须等于 DELTA_FORMAT_VERSION；
    - value / old_value 缺省时**不写这个键**，也不写 null（见 model.py 约定 2）；
    - 枚举一律写英文取值（Operation / DataType / ListOp 的 value）；
    - trace 写成嵌套 dict，字段与 Trace 一一对应；
    - label 是必填字段（空字符串表示没有事件）；
    - 未知字段、缺少必填字段、枚举取值不明、版本不符一律报 DeltaError
      （字段拼错要尽早暴露；将来新增字段必须同时提升 DELTA_FORMAT_VERSION）。

字段清单的来源：
    trace 与变化量的字段清单都从对应的 dataclass **自动推导**，不再手写维护，
    避免"类加了字段、清单忘了改"造成静默漏写或误判未知字段。
"""

import dataclasses
from typing import Any

from .errors import DeltaError
from .model import (
    DELTA_FORMAT_VERSION,
    DataType,
    Delta,
    ListOp,
    MISSING,
    Operation,
    Trace,
)

# trace 的字段名与顺序：直接从 dataclass 推导，避免手工清单与类定义漂移。
# 顺序沿用 Trace 里的字段定义顺序（只影响记录里键的排列，不影响语义）。
_TRACE_FIELDS = tuple(field.name for field in dataclasses.fields(Trace))

# 一条变化量记录允许出现的全部键 = Delta 的字段 + 格式版本号。
# 同样从 dataclass 推导，将来加字段不会再出现"静默漏写 / 误判未知字段"。
_DELTA_FIELDS = {field.name for field in dataclasses.fields(Delta)} | {"format_version"}

# 不带就无法还原的键。label 必填（引擎产出时总是写，空字符串表示无事件）；
# value / old_value / list_op / index 有缺省语义：不出现表示"字段不存在"。
_REQUIRED_DELTA_FIELDS = {"format_version", "path", "operation", "data_type", "trace", "label"}


def to_dict(delta: Delta) -> dict[str, Any]:
    """把一条变化量转成 JSON 兼容的 dict。

    输入：
        delta: 已构造好的 Delta 实例。
    输出：
        可直接 json.dumps 的 dict；缺省字段（MISSING）不会出现在结果里。
    异常：
        DeltaError: 传入对象不是 Delta 实例。
    变量：
        trace_record: trace 的嵌套字典，键与 _TRACE_FIELDS 一致；
        record: 正在拼装的输出字典。
    """
    if not isinstance(delta, Delta):
        raise DeltaError(f"to_dict 需要 Delta 实例，实际是 {type(delta).__name__}")

    trace_record = {name: getattr(delta.trace, name) for name in _TRACE_FIELDS}
    record: dict[str, Any] = {
        "format_version": DELTA_FORMAT_VERSION,
        "path": delta.path,
        "operation": delta.operation.value,
        "data_type": delta.data_type.value,
        "trace": trace_record,
        "label": delta.label,
    }
    if delta.list_op is not None:
        record["list_op"] = delta.list_op.value
    if delta.index is not None:
        record["index"] = delta.index
    if delta.value is not MISSING:
        record["value"] = delta.value
    if delta.old_value is not MISSING:
        record["old_value"] = delta.old_value
    return record


def from_dict(record: dict) -> Delta:
    """从 dict 还原一条变化量。

    输入：
        record: 来自 to_dict 或存档 JSON 的字典。
    输出：
        Delta 实例；缺省的 value / old_value 会还原成 MISSING 哨兵。
    异常：
        DeltaError: 不是 dict、版本不符、缺少必填字段、出现未知字段、
            枚举取值不合法、字段组合自相矛盾（由 Delta 构造时校验）。
            异常的 path 属性会尽量带上记录里的 path，便于定位坏存档。
    变量：
        无（具体校验过程见 _from_dict）。
    """
    try:
        return _from_dict(record)
    except DeltaError as exc:
        if exc.path or not isinstance(record, dict):
            raise
        raw_path = record.get("path", "")
        raise DeltaError(exc.detail, raw_path if isinstance(raw_path, str) else "") from exc


def _from_dict(record: dict) -> Delta:
    """from_dict 的实际实现（输入 / 输出说明见 from_dict）。

    输入：
        record: 来自 to_dict 或存档 JSON 的字典。
    输出：
        Delta 实例。
    异常：
        DeltaError: 记录不合法。
    变量：
        unknown: 记录里不属于格式定义的键；
        lack: 记录里缺少的必填键；
        value / old_value: 用"键是否存在"判断，键不存在才是 MISSING，值为 None 是合法值。
    """
    if not isinstance(record, dict):
        raise DeltaError(f"from_dict 需要 dict，实际是 {type(record).__name__}")

    unknown = sorted(set(record) - _DELTA_FIELDS)
    if unknown:
        raise DeltaError(f"变化量记录出现未知字段：{unknown}")
    lack = sorted(_REQUIRED_DELTA_FIELDS - set(record))
    if lack:
        raise DeltaError(f"变化量记录缺少必填字段：{lack}")

    version = record["format_version"]
    if version != DELTA_FORMAT_VERSION:
        raise DeltaError(f"变化量格式版本不匹配：记录是 {version!r}，当前支持 {DELTA_FORMAT_VERSION}")

    list_op_raw = record.get("list_op")
    list_op = None if list_op_raw is None else _parse_enum(ListOp, list_op_raw, "list_op")

    return Delta(
        path=record["path"],
        operation=_parse_enum(Operation, record["operation"], "operation"),
        data_type=_parse_enum(DataType, record["data_type"], "data_type"),
        trace=_trace_from_dict(record["trace"]),
        label=record["label"],
        value=record["value"] if "value" in record else MISSING,
        old_value=record["old_value"] if "old_value" in record else MISSING,
        list_op=list_op,
        index=record.get("index"),
    )


def _parse_enum(enum_class: type, raw: Any, field_name: str) -> Any:
    """把序列化字符串还原成枚举成员。

    输入：
        enum_class: 目标枚举类（Operation / DataType / ListOp）。
        raw: 记录里的原始取值。
        field_name: 字段名，仅用于异常消息。
    输出：
        对应的枚举成员。
    异常：
        DeltaError: raw 不是字符串，或不是该枚举的合法取值。
    变量：
        allowed: 该枚举当前允许的全部取值，用于异常消息。
    """
    if not isinstance(raw, str):
        raise DeltaError(f"{field_name} 必须是字符串，实际是 {type(raw).__name__}")
    try:
        return enum_class(raw)
    except ValueError as exc:
        allowed = [member.value for member in enum_class]
        raise DeltaError(f"{field_name} 取值不合法：{raw!r}，只允许 {allowed}") from exc


def _trace_from_dict(record: Any) -> Trace:
    """把嵌套 dict 还原成 Trace。

    输入：
        record: trace 子字典。
    输出：
        Trace 实例。
    异常：
        DeltaError: 不是 dict、出现未知字段、缺少字段，或字段类型不合法
            （字段类型由 Trace 构造时校验）。
    变量：
        unknown: trace 里不属于格式定义的键；
        lack: trace 里缺少的键。
    """
    if not isinstance(record, dict):
        raise DeltaError(f"trace 必须是 dict，实际是 {type(record).__name__}")

    unknown = sorted(set(record) - set(_TRACE_FIELDS))
    if unknown:
        raise DeltaError(f"trace 出现未知字段：{unknown}")
    lack = [name for name in _TRACE_FIELDS if name not in record]
    if lack:
        raise DeltaError(f"trace 缺少字段：{lack}")

    return Trace(**{name: record[name] for name in _TRACE_FIELDS})
