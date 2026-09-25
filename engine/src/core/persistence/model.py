"""存档的结构与校验（§18.1）。

位置：
    引擎核心逻辑层 → persistence 子包。

职责：
    定义一份存档里有哪几样东西、每样东西什么形状，以及"读进来时怎么校验"：

        引擎版本            ENGINE_VERSION（core/version.py）
        存档格式版本        SAVE_FORMAT_VERSION（本模块）
        变化量格式版本      DELTA_FORMAT_VERSION（core/delta/model.py）
        Mod 记录            id / 版本 / 条目 schema 版本
        场景 id             由 Mod / 调用方给，引擎只存不解释
        基础 State          最近一次快照（纯数据树）
        结算批次变化量日志  快照之后的每个结算批次（含 Mod 标签）
        随机状态            随机模块导出的纯数据（D-23）
        Command 序列        引擎这次运行收到/产生的全部命令
        元信息              存档名、时间等，引擎不解释

回放口径（D-40）：
    回放 = 基础 State **依次叠加**结算批次变化量，不重跑 Command、不重跑规则。
    因此存档里"快照 + 之后的批次"就足以还原出当前状态（见 replay.py）。

版本口径（D-25 / D-45）：
    存档格式、变化量格式、条目 schema 三个号必须完全相等，否则拒绝加载；
    引擎版本同样要求相等（本阶段采用最简单的处理，将来若要放宽属于新决策）。

草案依据：
    §18.1 存档结构、Mod 标签、版本拒绝加载；§18.2 回放口径；
    D-23 随机状态入档；D-40 回放不重跑规则；D-45 变化量格式版本；P5 可替换原则。
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ..delta import (
    DELTA_FORMAT_VERSION,
    Delta,
    DeltaError,
    from_dict as delta_from_dict,
    to_dict as delta_to_dict,
)
from ..pipeline.command import Command
from ..registry import ENTRY_SCHEMA_VERSION
from ..version import ENGINE_VERSION
from .errors import SaveFormatError, SaveVersionError

# 存档自身的结构版本。字段一变就要升这里（与变化量格式版本各管各的）。
SAVE_FORMAT_VERSION = 1


@dataclass(frozen=True)
class ModRecord:
    """存档记录的 Mod 身份（用来在加载时挡住"用错 Mod 读档"）。

    字段：
        id: Mod 标识；
        version: Mod 自己的版本号（来自 mod_info.json，本阶段由调用方给）；
        entry_schema_version: 条目结构版本（ENTRY_SCHEMA_VERSION 当时的取值）。
    """

    id: str
    version: str
    entry_schema_version: int = ENTRY_SCHEMA_VERSION

    def to_data(self) -> dict:
        """转成纯数据。

        输入：无。
        输出：
            {"id": …, "version": …, "entry_schema_version": …}。
        异常：
            无。
        变量：
            无。
        """
        return {
            "id": self.id,
            "version": self.version,
            "entry_schema_version": self.entry_schema_version,
        }

    @classmethod
    def from_data(cls, data: Any, *, path: str = "") -> "ModRecord":
        """从纯数据还原。

        输入：
            data: 存档里的 mod 字段；
            path: 存档路径（报错用）。
        输出：
            ModRecord。
        异常：
            SaveFormatError: 不是对象、缺字段或类型不对。
        变量：
            无。
        """
        record = _require_dict(data, "mod 必须是对象", path=path)
        return cls(
            id=_require_str(record, "id", path=path),
            version=_require_str(record, "version", path=path),
            entry_schema_version=_require_int(record, "entry_schema_version", path=path),
        )


@dataclass(frozen=True)
class SettlementRecord:
    """一个结算批次的记录（§18.1 的"结算批次变化量日志"里的一项）。

    字段：
        labels: Mod 贴在这个批次上的标签（如 "turn:3"），引擎只存不解释；
        deltas: 该批次里全部已提交的 CommandDelta（合并后的变化量）。
    """

    labels: tuple[str, ...]
    deltas: tuple[Delta, ...]

    def to_data(self) -> dict:
        """转成纯数据（变化量走 delta.codec）。"""
        return {"labels": list(self.labels), "deltas": [delta_to_dict(delta) for delta in self.deltas]}

    @classmethod
    def from_data(cls, data: Any, *, path: str = "") -> "SettlementRecord":
        """从纯数据还原。
        输入：
            data: 存档里的一个批次；
            path: 存档路径（报错用）。
        输出：
            SettlementRecord。
        异常：
            SaveFormatError: 结构不合法，或批次里的变化量记录不合法。
        变量：
            record / labels / deltas: 中间结果。
        """
        record = _require_dict(data, "结算批次必须是对象", path=path)
        labels = _require_list(record, "labels", path=path)
        for label in labels:
            if not isinstance(label, str):
                raise SaveFormatError(f"结算批次标签必须是字符串，实际是 {type(label).__name__}", path=path)
        raw_deltas = _require_list(record, "deltas", path=path)
        deltas: list[Delta] = []
        for index, item in enumerate(raw_deltas):
            try:
                deltas.append(delta_from_dict(item))
            except DeltaError as exc:
                raise SaveFormatError(
                    f"结算批次里第 {index} 条变化量记录不合法：{exc}",
                    path=path,
                ) from exc
        return cls(labels=tuple(labels), deltas=tuple(deltas))
@dataclass(frozen=True)
class Snapshot:
    """基础 State 快照（§18.1 的"最近一次快照"）。
    字段：
        state: 快照那一刻的 State 树（纯数据，深拷贝后保存）；
    说明：
        存档里只放"快照 + 快照之后记录的批次"，所以不必再记"快照盖到第几批"：
        settlements 列表里的每一条都是叠在它之上的。运行中的记录器自己维护
        "快照时已记了多少批"这条内部账（见 journal.py），那是内存里的事。
    """
    state: dict
    def to_data(self) -> dict:
        """转成纯数据。"""
        return {"state": deepcopy(self.state)}

    @classmethod
    def from_data(cls, data: Any, *, path: str = "") -> "Snapshot":
        """从纯数据还原。

        输入：
            data: 存档里的 snapshot 字段；
            path: 存档路径（报错用）。
        输出：
            Snapshot。
        异常：
            SaveFormatError: 结构不合法。
        变量：
            无。
        """
        record = _require_dict(data, "snapshot 必须是对象", path=path)
        state = record.get("state")
        if not isinstance(state, dict):
            raise SaveFormatError("snapshot.state 必须是对象（State 树的根）", path=path)
        return cls(state=deepcopy(state))


@dataclass(frozen=True)
class SaveFile:
    """一份存档（内存里的结构；落盘 / 读盘见 store.py）。

    字段：
        mod: Mod 记录；
        scenario_id: 场景 id（由 Mod / 调用方给，引擎只存）；
        snapshot: 基础 State 快照；
        settlements: 快照之后记录的结算批次（按发生顺序）；
        random_state: 随机状态（Rng.state() 的产物）；
        commands: 本次运行的 Command 序列（按入队顺序）；
        engine_version / save_format_version / delta_format_version: 三个版本号；
        meta: 元信息（存档名、时间……），引擎不解释。
    """

    mod: ModRecord
    snapshot: Snapshot
    settlements: tuple[SettlementRecord, ...] = ()
    random_state: dict = field(default_factory=dict)
    commands: tuple[Command, ...] = ()
    engine_version: str = ENGINE_VERSION
    save_format_version: int = SAVE_FORMAT_VERSION
    delta_format_version: int = DELTA_FORMAT_VERSION
    scenario_id: str = ""
    meta: dict = field(default_factory=dict)

    def to_data(self) -> dict:
        """转成可以直接 json.dumps 的纯数据。

        输入：无。
        输出：
            存档字典（键名与 §18.1 的清单一一对应）。
        异常：
            无。
        变量：
            无。
        """
        return {
            "engine_version": self.engine_version,
            "save_format_version": self.save_format_version,
            "delta_format_version": self.delta_format_version,
            "mod": self.mod.to_data(),
            "scenario_id": self.scenario_id,
            "snapshot": self.snapshot.to_data(),
            "settlements": [record.to_data() for record in self.settlements],
            "random_state": deepcopy(self.random_state),
            "commands": [_command_to_data(command) for command in self.commands],
            "meta": deepcopy(self.meta),
        }

    @classmethod
    def from_data(
        cls,
        data: Any,
        *,
        path: str = "",
        expected_mod: ModRecord | None = None,
    ) -> "SaveFile":
        """从纯数据还原并做版本校验。

        输入：
            data: 存档字典（json.loads 的产物）；
            path: 存档路径（报错用）；
            expected_mod: 当前运行使用的 Mod 记录；给了就一并比对（D-25）。
        输出：
            SaveFile。
        异常：
            SaveFormatError: 结构不合法；
            SaveVersionError: 任一版本号对不上。
        变量：
            record / mod / snapshot / settlements / commands: 逐项还原的中间结果。
        """
        record = _require_dict(data, "存档必须是一个对象（JSON 对象）", path=path)
        engine_version = _require_str(record, "engine_version", path=path)
        save_format_version = _require_int(record, "save_format_version", path=path)
        delta_format_version = _require_int(record, "delta_format_version", path=path)

        if save_format_version != SAVE_FORMAT_VERSION:
            raise SaveVersionError(
                f"存档格式版本不匹配：存的是 {save_format_version}，当前引擎是 {SAVE_FORMAT_VERSION}",
                expected=SAVE_FORMAT_VERSION,
                actual=save_format_version,
                path=path,
            )
        if delta_format_version != DELTA_FORMAT_VERSION:
            raise SaveVersionError(
                f"变化量格式版本不匹配：存的是 {delta_format_version}，当前引擎是 {DELTA_FORMAT_VERSION}",
                expected=DELTA_FORMAT_VERSION,
                actual=delta_format_version,
                path=path,
            )
        if engine_version != ENGINE_VERSION:
            raise SaveVersionError(
                f"引擎版本不匹配：存的是 {engine_version}，当前引擎是 {ENGINE_VERSION}",
                expected=ENGINE_VERSION,
                actual=engine_version,
                path=path,
            )

        mod = ModRecord.from_data(record.get("mod"), path=path)
        if mod.entry_schema_version != ENTRY_SCHEMA_VERSION:
            raise SaveVersionError(
                f"条目 schema 版本不匹配：存的是 {mod.entry_schema_version}，"
                f"当前引擎是 {ENTRY_SCHEMA_VERSION}",
                expected=ENTRY_SCHEMA_VERSION,
                actual=mod.entry_schema_version,
                path=path,
            )
        if expected_mod is not None:
            require_same_mod(expected_mod, mod, path=path)

        scenario_id = record.get("scenario_id", "")
        if not isinstance(scenario_id, str):
            raise SaveFormatError(f"scenario_id 必须是字符串，实际是 {type(scenario_id).__name__}", path=path)
        meta = record.get("meta", {})
        if not isinstance(meta, dict):
            raise SaveFormatError(f"meta 必须是对象，实际是 {type(meta).__name__}", path=path)
        random_state = record.get("random_state")
        if not isinstance(random_state, dict):
            raise SaveFormatError("random_state 必须是对象（Rng.state() 的产物）", path=path)

        snapshot = Snapshot.from_data(record.get("snapshot"), path=path)
        raw_settlements = _require_list(record, "settlements", path=path)
        settlements = tuple(SettlementRecord.from_data(item, path=path) for item in raw_settlements)
        raw_commands = _require_list(record, "commands", path=path)
        commands = tuple(_command_from_data(item, path=path) for item in raw_commands)

        return cls(
            mod=mod,
            snapshot=snapshot,
            settlements=settlements,
            random_state=deepcopy(random_state),
            commands=commands,
            engine_version=engine_version,
            save_format_version=save_format_version,
            delta_format_version=delta_format_version,
            scenario_id=scenario_id,
            meta=deepcopy(meta),
        )


def require_same_mod(expected: ModRecord, actual: ModRecord, *, path: str) -> None:
    """比对"当前运行的 Mod"与"存档记录的 Mod"。

    输入：
        expected: 当前运行的 Mod 记录；
        actual: 存档里的 Mod 记录；
        path: 存档路径。
    输出：
        无（一致时直接返回）。
    异常：
        SaveVersionError: id 或版本不一致（D-25 拒绝加载）。
    变量：
        无。

    说明：
        本函数是公开的：读档时除了 SaveFile.from_data 会用它，EngineRuntime.apply_save
        也要再确认一次（防止有人绕过读档路径直接塞进来一份别的 Mod 的存档）。
    """
    if expected.id != actual.id:
        raise SaveVersionError(
            f"Mod 不一致：存档属于 {actual.id!r}，当前运行的是 {expected.id!r}",
            expected=expected.id,
            actual=actual.id,
            path=path,
        )
    if expected.version != actual.version:
        raise SaveVersionError(
            f"Mod 版本不匹配：存档是 {actual.version!r}，当前 Mod 是 {expected.version!r}",
            expected=expected.version,
            actual=actual.version,
            path=path,
        )


def _command_to_data(command: Command) -> dict:
    """把一个 Command 转成纯数据（存档里的 Command 序列用）。

    输入：
        command: 命令。
    输出：
        {"command_id": …, "definition_id": …, "source": …, "payload": …, "created_at": …}。
    异常：
        无。
    变量：
        无。
    """
    return {
        "command_id": command.command_id,
        "definition_id": command.definition_id,
        "source": command.source,
        "payload": deepcopy(command.payload),
        "created_at": command.created_at,
    }


def _command_from_data(data: Any, *, path: str) -> Command:
    """从纯数据还原一个 Command（校验交给 Command 自己）。

    输入：
        data: 存档里的一条命令记录；
        path: 存档路径。
    输出：
        Command。
    异常：
        SaveFormatError: 不是对象或字段类型不对（ValueError / TypeError 也转成它）。
    变量：
        无。
    """
    record = _require_dict(data, "Command 记录必须是对象", path=path)
    try:
        return Command(
            command_id=record["command_id"],
            definition_id=record["definition_id"],
            source=record["source"],
            payload=record["payload"],
            created_at=record["created_at"],
        )
    except KeyError as exc:
        raise SaveFormatError(f"Command 记录缺少字段：{exc}", path=path) from exc
    except (TypeError, ValueError) as exc:
        raise SaveFormatError(f"Command 记录不合法：{exc}", path=path) from exc


def _require_dict(value: Any, message: str, *, path: str) -> dict:
    """要求值是 dict。"""
    if not isinstance(value, dict):
        raise SaveFormatError(f"{message}（实际是 {type(value).__name__}）", path=path)
    return value


def _require_list(record: dict, key: str, *, path: str) -> list:
    """要求 dict 里的某个键是列表。"""
    if key not in record:
        raise SaveFormatError(f"存档缺少字段：{key}", path=path)
    value = record[key]
    if not isinstance(value, list):
        raise SaveFormatError(f"{key} 必须是列表，实际是 {type(value).__name__}", path=path)
    return value


def _require_str(record: dict, key: str, *, path: str) -> str:
    """要求 dict 里的某个键是非空字符串。"""
    if key not in record:
        raise SaveFormatError(f"存档缺少字段：{key}", path=path)
    value = record[key]
    if not isinstance(value, str) or not value:
        raise SaveFormatError(f"{key} 必须是非空字符串，实际是 {value!r}", path=path)
    return value


def _require_int(record: dict, key: str, *, path: str) -> int:
    """要求 dict 里的某个键是 int（bool 不算）。"""
    if key not in record:
        raise SaveFormatError(f"存档缺少字段：{key}", path=path)
    value = record[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise SaveFormatError(f"{key} 必须是整数，实际是 {value!r}", path=path)
    return value
