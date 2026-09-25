"""EngineApi / ServiceApi：把临时状态视图与序号、溯源信息绑在一起的记账台。

位置：
    引擎核心逻辑层 → pipeline 子包，位于 core.temp_state 之上。

职责：
    - EngineApi 绑定"一个 Command + 它的 TempState 视图 + 全局序号分配器 + Mod 标识"；
    - ServiceApi 是发给"某个 Action 里的某个 Service"的手柄：只读（get / exists）
      读本 Command 的临时视图，写只能通过 emit 产出变化量；
    - Trace（sequence / command_id / action_id / service_id / mod_id / timestamp）
      由引擎统一组装，服务不自己拼，避免漏字段或乱填序号（§7.3 / D-41）。
    - EngineApi 还持有引擎的公共手段：确定性随机（Rng）、日志（Logger）、
      不存档的运行时数据（Context）；ServiceApi 把它们以受限的形式转给脚本：
      取随机、写日志、读写 Context，但**拿不到随机状态**（状态只属于存档与确定性）。

边界：
    - 不执行规则、不调用服务（那是 Applier 的职责，属于后续步骤）；
    - 不直接写真实 State：一切改动经 view.record 记账（§16）；
    - 不向服务暴露视图对象本身：服务拿不到 record / commit，只能走 emit。

草案依据：
    §9 管线第 7 步（Applier → Service）；§16 脚本默认读临时视图、改动必须经引擎接口
    产生变化量；§7.3 Trace 字段；§13 随机统一由随机模块管理；§19.1 数据变动处要有日志；
    D-41；D-11（Context 只放不影响规则判定的东西）。
"""

from typing import Any, Callable, Mapping

from ..context import Context
from ..delta.errors import DeltaError
from ..delta.model import MISSING, DataType, Delta, ListOp, Operation, Trace
from ..logic import LogicError
from ..logger import LogLevel, Logger
from ..ports import Media
from ..rng import Rng
from ..state.errors import StateShapeError
from ..temp_state import TempState
from .command import Command
from .sequence import SequenceCounter
from ._checks import require_non_empty_str as _require_non_empty_str


class EngineApi:
    """一个 Command 的引擎接口：只读视图入口 + 变化量记账台。

    字段：
        _view: 本 Command 的临时状态视图（唯一写入通道是它的 record）；
        _sequence: 全局序号分配器，所有变化量共用；
        _command: 本 Command；command_id 与 created_at 从它取；
        _mod_id: 来源 Mod 标识，写进每条 Trace。
        _rng: 确定性随机模块（一次运行一个实例）；
        _logger: 分层日志；
        _context: 不存档的运行时数据。
        _media: 媒体端口（音效 / 音乐；没有声音设备时是静默实现）。
        _functions: 加载期组装好的函数表（模块实现 + 绑定 + Mod 自己的函数）；
            服务脚本可以经 api.call(...) 调用它（例如调用模块提供的伤害实现）。
    """

    def __init__(
        self,
        view: TempState,
        sequence: SequenceCounter,
        command: Command,
        mod_id: str,
        *,
        rng: Rng,
        logger: Logger,
        context: Context,
        media: Media,
        functions: Mapping[str, Callable[..., Any]],
    ) -> None:
        """创建记账台。

        输入：
            view: 本 Command 的临时状态视图；其 command_id 必须与 command 一致；
            sequence: 全局序号分配器（一次运行一个实例，多处共用）；
            command: 正在处理的 Command；
            mod_id: 来源 Mod 标识（非空字符串），写进每条变化量的 trace.mod_id。
            rng: 确定性随机模块（一次运行一个实例）；
            logger: 分层日志对象（可以是不记日志的实例，但不能是 None）；
            context: 不存档的运行时数据。
            media: 媒体端口（音效 / 音乐；核心不碰具体声音后端）。
            functions: 加载期组装好的函数表（表达式与服务共用的那一份）。
        输出：
            无（构造对象）。
        异常：
            TypeError: 参数类型不符；
            ValueError: mod_id 是空字符串，或 view 与 command 的 command_id 不一致。
        变量：
            无。
        """
        if not isinstance(view, TempState):
            raise TypeError(f"view 必须是 TempState，实际是 {type(view).__name__}")
        if not isinstance(sequence, SequenceCounter):
            raise TypeError(f"sequence 必须是 SequenceCounter，实际是 {type(sequence).__name__}")
        if not isinstance(command, Command):
            raise TypeError(f"command 必须是 Command，实际是 {type(command).__name__}")
        if not isinstance(mod_id, str):
            raise TypeError(f"mod_id 必须是字符串，实际是 {type(mod_id).__name__}")
        if not mod_id:
            raise ValueError("mod_id 不能是空字符串")
        if not isinstance(rng, Rng):
            raise TypeError(f"rng 必须是 Rng，实际是 {type(rng).__name__}")
        if not isinstance(logger, Logger):
            raise TypeError(f"logger 必须是 Logger，实际是 {type(logger).__name__}")
        if not isinstance(context, Context):
            raise TypeError(f"context 必须是 Context，实际是 {type(context).__name__}")
        if not hasattr(media, "play_sound") or not hasattr(media, "stop_music"):
            raise TypeError("media 必须实现 Media 端口（play_sound / play_music / stop_music）")
        if command.command_id != view.command_id:
            raise ValueError(
                f"视图属于 Command {view.command_id!r}，传入的 command 是 "
                f"{command.command_id!r}：两者必须一致"
            )
        self._view: TempState = view
        self._sequence: SequenceCounter = sequence
        self._command: Command = command
        self._mod_id: str = mod_id
        self._rng: Rng = rng
        self._logger: Logger = logger
        self._context: Context = context
        self._media: Media = media
        self._functions: Mapping[str, Callable[..., Any]] = functions

    @property
    def command(self) -> Command:
        """返回本记账台绑定的 Command（只读；Applier 日志用）。"""
        return self._command

    @property
    def mod_id(self) -> str:
        """返回本记账台的 Mod 标识（只读）。"""
        return self._mod_id

    def for_service(self, action_id: str, service_id: str) -> "ServiceApi":
        """为"某个 Action 里的某个 Service"创建手柄。

        输入：
            action_id: 当前 Action 的实例标识（非空字符串）；
            service_id: 当前 Service 的标识（非空字符串），写进 trace.service_id。
        输出：
            绑定到本记账台与这两个标识的 ServiceApi。
        异常：
            TypeError: action_id / service_id 不是字符串；
            ValueError: action_id / service_id 是空字符串。
        变量：
            无。
        """
        _require_non_empty_str(action_id, "action_id")
        _require_non_empty_str(service_id, "service_id")
        return ServiceApi(self, action_id, service_id)

    def _random_int(self, low: int, high: int) -> int:
        """取一个随机整数（ServiceApi.rand_int 的实际实现）。"""
        return self._rng.next_int(low, high)

    def _random_float(self) -> float:
        """取一个 [0, 1) 的随机浮点数（ServiceApi.rand_float 的实际实现）。"""
        return self._rng.next_float()

    def _chance(self, probability: float) -> bool:
        """按概率判定（ServiceApi.chance 的实际实现）。"""
        return self._rng.chance(probability)

    def _play_sound(self, path: str) -> bool:
        """播一次音效（ServiceApi.play_sound 的实际实现）。

        输入：
            path: 音效文件路径（相对 Mod 文件夹或绝对路径，由适配器解释）。
        输出：
            True 表示真的播了；False 表示这台机器放不了（静默、缺文件……）。
        异常：
            无（媒体是可选功能，放不出来只记日志，不影响逻辑，§19.2）。
        变量：
            无。
        """
        played = self._media.play_sound(path)
        if not played:
            self._logger.debug(f"音效没有播放（静默或缺失）：{path}", mod_id=self._mod_id)
        return played

    def _play_music(self, path: str, *, loop: bool = True) -> bool:
        """放背景音乐（ServiceApi.play_music 的实际实现）。"""
        played = self._media.play_music(path, loop=loop)
        if not played:
            self._logger.debug(f"音乐没有播放（静默或缺失）：{path}", mod_id=self._mod_id)
        return played

    def _stop_music(self) -> None:
        """停背景音乐（ServiceApi.stop_music 的实际实现）。"""
        self._media.stop_music()

    def _call_function(self, name: str, args: tuple, kwargs: dict) -> Any:
        """调用加载期函数表里的一个函数（ServiceApi.call 的实际实现）。

        输入：
            name: 函数名（可以是绑定名、模块全限定名、随机的 rng.* 等）；
            args / kwargs: 调用参数。
        输出：
            函数的返回值。
        异常：
            LogicError: 函数表里没有这个名字或调用失败（与表达式里的 ["call", …] 同口径）。
        变量：
            function: 取到的可调用对象。

        说明：
            服务脚本需要"模块提供的某个实现"时（例如攻击骨架要用 Mod 绑定的伤害实现），
            就通过这个入口拿——这样模块之间不必互相认识，只认识"函数名"（由 Mod 绑定决定）。
        """
        function = self._functions.get(name)
        if function is None:
            available = sorted(self._functions)
            raise LogicError(f"函数表里没有 {name!r}（可用：{available}）")
        return function(*args, **kwargs)

    def _log(
        self,
        action_id: str,
        service_id: str,
        message: str,
        *,
        level: LogLevel,
        path: str,
        label: str,
        extra: dict | None,
    ) -> None:
        """写一条来自 Mod 的日志（ServiceApi.log 的实际实现）。

        输入：
            action_id / service_id: 产出这条日志的 Action 与 Service；
            message: 说明文字；
            level: 级别（LogLevel，或名字字符串如 "warn"）；Mod 脚本写字符串即可；
            path: 相关数据路径（可空）；
            label: 相关事件 id（可空）；
            extra: 额外键值对（可空）。
        输出：
            无。
        异常：
            TypeError: level 不是 LogLevel / 名字字符串，或 message 不是字符串。
        变量：
            无。
        """
        self._logger.log(
            _as_log_level(level),
            message,
            mod_id=self._mod_id,
            command_id=self._command.command_id,
            action_id=action_id,
            service_id=service_id,
            path=path,
            label=label,
            extra=extra,
        )

    def _read(self, path: str) -> Any:
        """在临时状态视图上读一个值（ServiceApi.get 的实际实现）。

        输入：
            path: JSON Pointer 路径；"" 表示视图根。
        输出：
            视图上的当前值（含尚未提交的改动）。
        异常：
            PathSyntaxError / PathNotFoundError / StateShapeError: 路径问题（来自 state 层）；
            TempStateError: 视图已进入终态。
        变量：
            无。
        """
        return self._view.get(path)

    def _exists(self, path: str) -> bool:
        """判断路径在临时视图上是否存在（ServiceApi.exists 的实际实现）。

        输入：
            path: JSON Pointer 路径。
        输出：
            True 表示按该路径能取到值。
        异常：
            PathSyntaxError: 路径写法不合法（来自 state 层）；
            TempStateError: 视图已进入终态。
        变量：
            无。
        """
        return self._view.exists(path)

    def _expand(self, pattern: str) -> tuple:
        """按通配路径列出当前视图上的全部匹配（ServiceApi.expand 的实际实现）。"""
        return self._view.expand(pattern)

    def _emit(
        self,
        action_id: str,
        service_id: str,
        path: str,
        operation: Operation,
        data_type: DataType,
        *,
        value: Any = MISSING,
        old_value: Any = MISSING,
        label: str = "",
        list_op: ListOp | None = None,
        index: int | None = None,
    ) -> None:
        """组装 Trace、构造 Delta 并记进临时视图（ServiceApi.emit 的实际实现）。

        输入：
            action_id / service_id: 产出这条变化量的 Action 与 Service；
            path / operation / data_type / value / old_value / label / list_op / index:
                与 delta 模型一致（见 core.delta.model.Delta）；
                **operation / data_type / list_op 可以直接写字符串取值**
                （"add" / "remove" / "modify"、"number" / "string" / "bool" / "null" /
                "list" / "dict"、"insert" / "remove"），这样 Mod 脚本不必 import 引擎内部模块。
        输出：
            无；变化量按序记进视图（并接受 TempState 的归属与序号校验）。
        异常：
            DeltaError: 字段组合不合法（模型层校验）；
            PathNotFoundError / StateShapeError / StateConflictError: 变化量在当前视图上不成立；
            TempStateError: 视图已进入终态，或序号 / Command 归属校验失败。
        变量：
            trace: 引擎组装的溯源块；timestamp 取 Command.created_at（交互时间），
                不在结算时读墙钟，保证确定性；
            delta: 组装好的变化量。
        说明：
            序号在构造 Delta 时即分配；record 失败不会把序号退回——序号只需要
            单调递增，不要求连续。
            记录成功后写一条 TRACE 日志（§19.1 要求数据变动处有日志）：
            路径、操作、新旧值、label、来源与顺序号都写进去，供存档后的追溯使用。
        """
        # 字符串取值入口：Mod 脚本写 "modify" / "number" 也能用（枚举本身是字符串枚举）。
        operation = _as_enum(operation, Operation, "operation")
        data_type = _as_enum(data_type, DataType, "data_type")
        if list_op is not None:
            list_op = _as_enum(list_op, ListOp, "list_op")

        trace = Trace(
            sequence=self._sequence.next(),
            timestamp=self._command.created_at,
            command_id=self._command.command_id,
            action_id=action_id,
            service_id=service_id,
            mod_id=self._mod_id,
        )
        delta = Delta(
            path=path,
            operation=operation,
            data_type=data_type,
            trace=trace,
            label=label,
            value=value,
            old_value=old_value,
            list_op=list_op,
            index=index,
        )
        self._view.record(delta)
        self._logger.trace(
            f"变化量 {delta.operation} {delta.path}" + (f"（事件 {label}）" if label else ""),
            mod_id=self._mod_id,
            command_id=self._command.command_id,
            action_id=action_id,
            service_id=service_id,
            path=delta.path,
            label=label,
            extra={
                "sequence": trace.sequence,
                "operation": str(delta.operation),
                "data_type": str(delta.data_type),
                "value": _render_value(delta.value),
                "old_value": _render_value(delta.old_value),
            },
        )


class ServiceApi:
    """服务脚本拿到的引擎接口手柄（一个 Action 内一个 Service 一份）。

    字段：
        _api: 所属的 EngineApi；
        _action_id: 当前 Action 的实例标识；
        _service_id: 当前 Service 的标识。

    说明：
        由 EngineApi.for_service 创建，不要在别处直接构造；服务通过它读临时视图、
        产出变化量，但拿不到视图对象本身，因此无法绕过记账台直接 record。
    """

    def __init__(self, api: EngineApi, action_id: str, service_id: str) -> None:
        """创建一个服务手柄。

        输入：
            api: 所属的 EngineApi；
            action_id: 当前 Action 的实例标识（由 EngineApi.for_service 校验）；
            service_id: 当前 Service 的标识（由 EngineApi.for_service 校验）。
        输出：
            无（构造对象）。
        异常：
            TypeError: api 不是 EngineApi。
        变量：
            无。
        """
        if not isinstance(api, EngineApi):
            raise TypeError(f"ServiceApi 需要 EngineApi，实际是 {type(api).__name__}")
        self._api: EngineApi = api
        self._action_id: str = action_id
        self._service_id: str = service_id

    @property
    def action_id(self) -> str:
        """返回本手柄绑定的 Action 标识（只读）。"""
        return self._action_id

    @property
    def service_id(self) -> str:
        """返回本手柄绑定的 Service 标识（只读）。"""
        return self._service_id

    def get(self, path: str) -> Any:
        """读取临时状态视图上的值（含本 Command 尚未提交的改动）。

        输入：
            path: JSON Pointer 路径；"" 表示视图根。
        输出：
            路径上的当前值。
        异常：
            PathSyntaxError / PathNotFoundError / StateShapeError: 路径问题（来自 state 层）；
            TempStateError: 视图已进入终态。
        变量：
            无。
        """
        return self._api._read(path)

    def exists(self, path: str) -> bool:
        """判断路径在临时视图上是否存在。

        输入：
            path: JSON Pointer 路径。
        输出：
            True 表示按该路径能取到值。
        异常：
            PathSyntaxError: 路径写法不合法（来自 state 层）；
            TempStateError: 视图已进入终态。
        变量：
            无。
        """
        return self._api._exists(path)

    def expand(self, pattern: str) -> tuple:
        """按通配路径列出当前视图上的全部匹配（F-05 批量操作）。

        输入：
            pattern: 通配路径，例如 "/units/{unit}/power"。
        输出：
            core.state.Match 元组（每条带具体路径与捕获到的名字）。
        异常：
            PathSyntaxError: 路径写法不合法；
            TempStateError: 视图已进入终态。
        变量：
            无。
        """
        return self._api._expand(pattern)

    @property
    def context(self) -> Context:
        """返回不存档的运行时数据（Context）。

        输入：无。
        输出：
            Context 对象（本运行的同一个实例）。
        异常：
            无。
        变量：
            无。

        说明：
            Context 里只能放**不影响规则判定**的数据（D-11）：会影响玩法的量
            必须写进 State（走 emit），否则读档后行为会和读档前不一致。
        """
        return self._api._context

    def rand_int(self, low: int, high: int) -> int:
        """取一个 [low, high] 闭区间上的随机整数（走引擎的确定性随机）。

        输入：
            low / high: 区间（int）。
        输出：
            整数。
        异常：
            TypeError / ValueError: 参数不合法（见 core.rng.Rng.next_int）。
        变量：
            无。

        说明：
            脚本只能用这里取随机，不能自己 import random：随机状态要随存档一起
            保存与恢复（D-23），绕过引擎取随机会让回放与读档失效（§13）。
        """
        return self._api._random_int(low, high)

    def rand_float(self) -> float:
        """取一个 [0.0, 1.0) 上的随机浮点数（走引擎的确定性随机）。

        输入：无。
        输出：
            浮点数。
        异常：
            无。
        变量：
            无。
        """
        return self._api._random_float()

    def chance(self, probability: float) -> bool:
        """按概率判定（走引擎的确定性随机）。

        输入：
            probability: 概率，取值 [0.0, 1.0]。
        输出：
            True / False。
        异常：
            TypeError / ValueError: 参数不合法（见 core.rng.Rng.chance）。
        变量：
            无。
        """
        return self._api._chance(probability)

    def play_sound(self, path: str) -> bool:
        """播一次音效（媒体走适配器，放不出来只记日志、不影响逻辑，§19.2）。

        输入：
            path: 音效文件路径（写法由媒体适配器解释，通常是相对 Mod 文件夹的路径）。
        输出：
            True 表示真的播了；False 表示这台机器放不了。
        异常：
            无。
        变量：
            无。

        说明：
            脚本不能自己 import pygame / 调用系统播放器（§17.1）：所有声音都经过这里，
            这样"有没有声音设备"只影响体验，不影响确定性与数据。
        """
        return self._api._play_sound(path)

    def play_music(self, path: str, *, loop: bool = True) -> bool:
        """放背景音乐（同一时刻只有一段）。

        输入：
            path: 音乐文件路径；
            loop: 是否循环。
        输出：
            True 表示真的开始放了；False 表示放不了。
        异常：
            无。
        变量：
            无。
        """
        return self._api._play_music(path, loop=loop)

    def stop_music(self) -> None:
        """停止背景音乐。"""
        self._api._stop_music()

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """调用引擎函数表里的函数（模块实现 / 绑定名 / rng.* 等）。

        输入：
            name: 函数名；模块实现的全限定名形如 "pack_combat_basic.damage.dice"，
                绑定名则是 Mod 在 bindings 里自己起的键（如 "damage"）；
            args / kwargs: 传给函数的参数（关键字参数优先于绑定与平铺参数）。
        输出：
            函数返回值。
        异常：
            LogicError: 没有这个名字，或函数本身报错。
        变量：
            无。
        """
        return self._api._call_function(name, args, kwargs)

    def log(
        self,
        message: str,
        *,
        level: LogLevel = LogLevel.INFO,
        path: str = "",
        label: str = "",
        extra: dict | None = None,
    ) -> None:
        """替本 Service 写一条日志（§19.1 的"Mod 主动记原因"）。

        输入：
            message: 说明文字（引擎会自动补上 Mod / Command / Action / Service 标识）；
            level: 级别，缺省 INFO；
            path: 相关数据路径（可空）；
            label: 相关事件 id（可空）；
            extra: 额外键值对（可空）。
        输出：
            无。
        异常：
            TypeError: level 不是 LogLevel，或 message 不是字符串。
        变量：
            无。
        """
        self._api._log(
            self._action_id,
            self._service_id,
            message,
            level=level,
            path=path,
            label=label,
            extra=extra,
        )

    def emit(
        self,
        path: str,
        operation: Operation,
        data_type: DataType,
        *,
        value: Any = MISSING,
        old_value: Any = MISSING,
        label: str = "",
        list_op: ListOp | None = None,
        index: int | None = None,
    ) -> None:
        """产出并记录一条变化量（服务唯一的写出口）。

        输入：
            path: 完整 JSON Pointer；列表增删指向列表本身，其余指向被改的值；
            operation: 新增 / 删除 / 修改；
            data_type: 被改动变量的类型（元数据，参与合并与日志）；
            value: 新值 / 插入元素值；缺省的语义是"字段不存在"，不是 null（MISSING）；
            old_value: 旧值 / 被删除元素值；缺省语义同上；
            label: 事件 id，供 Trigger 路由；没有事件时留空；
            list_op / index: 仅列表插入 / 删除使用。
        输出：
            无；Trace 由引擎填好后记进本 Command 的临时状态。
        异常：
            DeltaError: 字段组合不合法（模型层校验）；
            PathNotFoundError / StateShapeError / StateConflictError: 变化量在当前视图上不成立；
            TempStateError: 视图已进入终态，或 Command 归属 / 序号校验失败。
        变量：
            无。
        """
        self._api._emit(
            self._action_id,
            self._service_id,
            path,
            operation,
            data_type,
            value=value,
            old_value=old_value,
            label=label,
            list_op=list_op,
            index=index,
        )

    def log_line(self, path: str, text: str) -> None:
        """往 path 指的列表**末尾追一行**（战报、队列这类"加一条"的通用写法）。

        为什么放在服务手柄上：脚本之间不能互相调用，而"读当前列表 → 拼上新的一行 →
        记账"是每个服务脚本都可能用的**写操作**（纯函数做不到、服务又调不了服务），
        所以收进引擎这一处，替代各脚本自己写的一份 `_log`。

        输入：
            path: 列表的路径（如 "/log"）；**空字符串 = 不碰 State，只写引擎日志**；
            text: 这一行的文字。
        输出：
            无（记成一条"列表整体改成新列表"的变化量，与旧写法逐字节同形）。
        异常：
            PathNotFoundError: path 不存在（与直接 get 同口径，属于 Mod 数据写错）；
            StateShapeError / TempStateError: 见 emit。
        变量：
            current: 追加之前的列表；new_value: 追加之后的新列表。
        """
        if not path:
            self.log(text)
            return
        current = self.get(path)
        if not isinstance(current, list):
            raise StateShapeError(
                path, f"log_line 需要这个路径上是一个列表，实际是 {type(current).__name__}"
            )
        new_value = list(current) + [text]
        self.emit(path, Operation.MODIFY, DataType.LIST, value=new_value, old_value=current)


def _render_value(value: Any) -> Any:
    """把变化量字段渲染成适合写进日志的形式。

    输入：
        value: 变化量的 value / old_value（可能是 MISSING 哨兵）。
    输出：
        MISSING 渲染成字符串 "<缺省>"（表示"这条变化量没有这个字段"）；
        其余值原样返回（日志适配器负责怎么显示）。
    异常：
        无。
    变量：
        无。

    说明：
        为什么要有这一步：MISSING 与 None 是两回事（§7.3），日志必须能把
        "没有这个字段"与"值就是空值"分开，否则看图说话会读错。
    """
    return "<缺省>" if value is MISSING else value


def _as_enum(value: Any, enum_type: Any, name: str) -> Any:
    """把"枚举成员或它的字符串取值"统一成枚举成员。

    输入：
        value: 枚举成员，或它的字符串取值（例如 "modify"）；
        enum_type: 目标枚举类（Operation / DataType / ListOp）；
        name: 字段名（报错用）。
    输出：
        枚举成员。
    异常：
        DeltaError: 取值不在枚举里，或类型完全不对。
    变量：
        无。

    说明：
        delta 的三个枚举都是"字符串枚举"，所以 Mod 脚本可以直接写 "modify" / "number"，
        不必 import 引擎内部模块；引擎在这里统一转成枚举成员再构造变化量。
    """
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            legal = [item.value for item in enum_type]
            raise DeltaError(f"{name} 不认识取值 {value!r}（合法取值：{legal}）") from exc
    raise DeltaError(
        f"{name} 必须是 {enum_type.__name__} 或它的字符串取值，实际是 {type(value).__name__}"
    )


def _as_log_level(value: Any) -> LogLevel:
    """把"日志级别或它的名字字符串"统一成 LogLevel。

    输入：
        value: LogLevel 成员，或名字字符串（"warn" / "WARN" / "info" …）。
    输出：
        LogLevel 成员。
    异常：
        TypeError: 既不是 LogLevel 也不是字符串；
        ValueError: 名字不认识。
    变量：
        name: 大写后的名字。

    说明：
        与 _as_enum 同一个目的：Mod 脚本直接写 "warn" 就能记一条警告，
        不必 import 引擎内部模块。
    """
    if isinstance(value, LogLevel):
        return value
    if isinstance(value, str):
        name = value.upper()
        if name in LogLevel.__members__:
            return LogLevel[name]
        legal = list(LogLevel.__members__)
        raise ValueError(f"日志级别不认识 {value!r}（合法取值：{legal}）")
    raise TypeError(f"日志级别必须是 LogLevel 或名字字符串，实际是 {type(value).__name__}")
