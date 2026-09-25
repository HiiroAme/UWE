"""应用器：调用服务、收集变化量（§9 第 7～8 步）。

位置：
    引擎核心逻辑层 → pipeline 子包。

职责：
    执行一个 Action 声明的那串 Service 调用：
        1. **在调用这一刻**把这一步的参数表达式算成具体参数；
        2. 给脚本发一个 ServiceApi 手柄（读临时视图、产出变化量、取随机、写日志）；
        3. 调用服务；服务产出的变化量由 TempState.record 记账（§16）。

参数求值时机（实现决策，O-02 未定稿部分）：
    每一步的参数都在**该步即将调用前**求值，而不是在编排时一次算完。
    这样"后面的步骤能看到前面步骤已经产生的改动"（§8 临时状态的用途、§10 只读口径），
    并且同一套口径对规则、参数、服务都成立：谁在用表达式，就用当下的视图去算。
    顺带的好处是 Action 本身仍是纯数据对象（payload 是算好的参数，见 command.py）。

为什么服务不返回值：
    改动只能经 api.emit 产生变化量（§16），否则记账、日志、存档、回放都无从谈起。
    服务函数返回什么都视为不用（返回值不参与任何判定）。

失败口径（§9 第 7 步：服务异常 → 丢弃 Command）：
    参数求值失败、服务解析不到、服务内部抛异常，全部包成 ServiceFailure 上行，
    由 Dispatcher 记录原因并丢弃整个 Command（真实 State 不变）。

草案依据：
    §9 第 7、8 步；§10（Rule 与脚本读临时状态）；§16 脚本经引擎接口产生变化量；
    §14.2 service 注册表；P7（服务的实现在适配器侧，通过端口解析）；P9（计算与副作用分离）。
"""

from typing import Any, Callable, Mapping

from ..context import Context
from ..content import CompiledAction
from ..delta import Delta
from ..logger import Logger
from ..logic import LogicError
from ..ports import Media, ServiceNotAvailableError, ServiceResolver
from ..rng import Rng
from ..state.errors import StateError
from ..temp_state import TempState
from .command import Action, Command
from .engine_api import EngineApi
from .errors import ServiceFailure
from .sequence import SequenceCounter


class Applier:
    """按 Action 定义调用服务，并返回本次产生的变化量。

    字段：
        _services: 服务解析端口（service 条目 id → 可调用对象）；
        _sequence: 全局序号分配器（与其它产出变化量的地方共用）；
        _rng: 确定性随机模块；
        _context: 不存档的运行时数据；
        _mod_id: 来源 Mod 标识；
        _logger: 日志对象；
        _functions: 表达式可用到的外部函数表（随机、几何……）。
        _media: 媒体端口（音效 / 音乐）。
    """

    def __init__(
        self,
        services: ServiceResolver,
        sequence: SequenceCounter,
        rng: Rng,
        context: Context,
        mod_id: str,
        *,
        logger: Logger,
        media: Media,
        params: Mapping[str, Any] | None = None,
        functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        """创建应用器。

        输入：
            services: 服务解析端口；
            sequence: 全局序号分配器；
            rng: 确定性随机模块；
            context: 不存档的运行时数据；
            mod_id: 来源 Mod 标识（非空字符串）；
            logger: 日志对象；
            media: 媒体端口（交给 ServiceApi 用，核心不碰具体声音后端）；
            params: 平铺参数（Mod 与模块声明的那一份）；调用服务时作为**最低优先级**的
                默认参数合并进 args，让模块服务不必在每个调用点重复传路径；
            functions: 表达式可用到的外部函数表。
        输出：
            无（构造对象）。
        异常：
            TypeError: 参数类型不符；
            ValueError: mod_id 是空字符串。
        变量：
            无。
        """
        if not isinstance(sequence, SequenceCounter):
            raise TypeError(f"sequence 必须是 SequenceCounter，实际是 {type(sequence).__name__}")
        if not isinstance(rng, Rng):
            raise TypeError(f"rng 必须是 Rng，实际是 {type(rng).__name__}")
        if not isinstance(context, Context):
            raise TypeError(f"context 必须是 Context，实际是 {type(context).__name__}")
        if not isinstance(logger, Logger):
            raise TypeError(f"logger 必须是 Logger，实际是 {type(logger).__name__}")
        if not isinstance(mod_id, str):
            raise TypeError(f"mod_id 必须是字符串，实际是 {type(mod_id).__name__}")
        if not mod_id:
            raise ValueError("mod_id 不能是空字符串")
        if not hasattr(services, "resolve"):
            raise TypeError("services 必须实现 resolve(service_id) 接口（见 core.ports.ServiceResolver）")
        self._services: ServiceResolver = services
        self._sequence: SequenceCounter = sequence
        self._rng: Rng = rng
        self._context: Context = context
        self._mod_id: str = mod_id
        self._logger: Logger = logger
        if not hasattr(media, "play_sound"):
            raise TypeError("media 必须实现 Media 端口（play_sound / play_music / stop_music）")
        self._media: Media = media
        self._params: Mapping[str, Any] = params or {}
        self._functions: Mapping[str, Callable[..., Any]] = functions or {}

    def execute(
        self,
        command: Command,
        action: Action,
        definition: CompiledAction,
        view: TempState,
        *,
        functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> tuple[Delta, ...]:
        """执行一个 Action 的全部步骤，返回它新产生的变化量。

        输入：
            command: 当前命令（提供 command_id / created_at 等溯源信息）；
            action: 本 Action 实例（action_id 与已求值的 payload）；
            definition: 本 Action 的编译结果（步骤序列）；
            view: 本命令的临时状态视图；
            functions: 本次求值用的外部函数表；缺省用构造时给的那份。
        输出：
            本 Action 期间新产生的变化量（按记录顺序；可能为空元组）。
        异常：
            ServiceFailure: 参数求值失败、服务解析不到、或服务内部抛异常。
        变量：
            table: 本次使用的外部函数表；
            before: 调用前的变化量条数（用来切出"本次新增的"那一段）；
            api: 本命令的记账台（由它组装 Trace 并记账）；
            step: 当前正在执行的步骤；
            args: 本步已求值的参数；
            service: 本步的服务函数。
        """
        table = self._functions if functions is None else functions
        before = len(view.pending_deltas())
        api = EngineApi(
            view,
            self._sequence,
            command,
            self._mod_id,
            rng=self._rng,
            logger=self._logger,
            context=self._context,
            media=self._media,
            functions=table,
        )

        for index, step in enumerate(definition.steps):
            try:
                args = step.resolve_args(view, args=action.payload, functions=table)
            except (LogicError, StateError) as exc:
                raise ServiceFailure(
                    f"第 {index} 步的参数求值失败（{step.location}）：{exc}",
                    command_id=command.command_id,
                    action_id=action.action_id,
                    service_id=step.service_id,
                    cause=exc,
                ) from exc
            # 平铺参数是兜底：调用点写了的键优先（D-2 的优先级）。
            if self._params:
                args = {**self._params, **args}
            service = self._resolve(
                step.service_id, command=command, action_id=action.action_id, location=step.location
            )
            self._logger.debug(
                "调用服务",
                mod_id=self._mod_id,
                command_id=command.command_id,
                action_id=action.action_id,
                service_id=step.service_id,
                extra={"args": args, "step": index},
            )
            service_api = api.for_service(action.action_id, step.service_id)
            try:
                service(service_api, args)
            except Exception as exc:  # 服务脚本的任何异常都算这次调用失败
                raise ServiceFailure(
                    f"服务执行失败（{step.location}）：{type(exc).__name__}: {exc}",
                    command_id=command.command_id,
                    action_id=action.action_id,
                    service_id=step.service_id,
                    cause=exc,
                ) from exc

        return view.pending_deltas()[before:]

    def _resolve(
        self,
        service_id: str,
        *,
        command: Command,
        action_id: str,
        location: str,
    ) -> Callable[[Any, dict], None]:
        """向服务端口要一个可调用对象。

        输入：
            service_id: 服务条目 id；
            command: 当前命令（报错用）；
            action_id: 当前 Action 实例 id（报错用）；
            location: 位置串（报错用）。
        输出：
            可调用对象。
        异常：
            ServiceFailure: 端口报"取不到这个服务"。
        变量：
            无。
        """
        try:
            return self._services.resolve(service_id)
        except ServiceNotAvailableError as exc:
            raise ServiceFailure(
                f"取不到服务实现（{location}）：{exc.detail}",
                command_id=command.command_id,
                action_id=action_id,
                service_id=service_id,
                cause=exc,
            ) from exc
