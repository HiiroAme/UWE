"""EngineRuntime：引擎运行期对象的总装（§5）。

位置：
    引擎核心逻辑层的最上层（仍然是核心：不认识具体 Mod 内容，只按 Mod 数据组装）。

职责：
    在**组装期**一次性建好运行期需要的全部对象，并把它们按依赖方向接起来：

        EngineRuntime
        ├── state        真实 State（存档 = 对它的快照）
        ├── context      非存档上下文
        ├── hub          注册表中心（Mod 内容）
        ├── content      编译后的内容（静态检查已在加载期做完）
        ├── sequence     变化量序号分配器
        ├── rng          确定性随机（状态入存档）
        ├── logger       分层日志
        ├── orchestrator 编排器
        ├── rule_checker 规则校验器
        ├── applier      应用器
        ├── event_chain  事件链执行器
        └── dispatcher   分发器（命令队列与结算）

为什么都在这里建：
    §5 要求运行期对象"运行期任何对象需要它都通过参数显式持有，禁止模块级单例"（P4）。
    总装放在一处，其它模块只接收自己需要的那几个依赖，谁都拿不到全局。

还没有纳入的对象（按 P8，等真正需要时再加）：
    - adapters：渲染 / 媒体 / 网络的具体实现（文件、时钟、日志出货口已经以端口形式注入）；
    - mods：已加载 Mod 的运行时信息（等 Mod 加载层成型后接入，见 §15）。
    这两项不影响管线的正确性，所以先不预置空壳。

草案依据：
    §5 引擎运行时对象与生命周期；§9 管线；§13 确定性（随机与序号都由运行时统一持有）；
    D-24 每次运行只加载一个 Mod；P4 显式优于隐式；P7 端口与适配器（时钟与日志出货口注入）。
"""

from typing import Any, Callable, Mapping

from .context import Context
from .content import CompiledContent
from .derived import emit_update, make_refresh_service, refresh_derived
from .engine_services import CREATE_INSTANCE_SERVICE, REFRESH_DERIVED_SERVICE
from .templates import make_create_instance_service
from .logger import LogLevel, Logger, LogSink
from .persistence import Journal, ModRecord, SaveFile, replay_state, require_same_mod
from .pipeline import (
    Applier,
    Command,
    Dispatcher,
    EngineApi,
    Orchestrator,
    RuleChecker,
    SequenceCounter,
    EventChain,
)
from .temp_state import TempState
from .ports import Media, ServiceResolver, SilentMedia
from .registry import RegistryHub
from .rng import Rng


class EngineRuntime:
    """一次游戏运行的全部引擎对象。

    字段（全部是对象引用，运行期不替换；state 的内容会随提交变化）：
        state / context / hub / content / sequence / rng / logger / journal /
        orchestrator / rule_checker / applier / event_chain / dispatcher；
        mod_id / mod_version: 本次运行的 Mod 身份（写进存档用于校验）；
        functions: 表达式可用到的外部函数表（随机函数 + 调用方追加的函数）。
    """

    def __init__(
        self,
        state: dict,
        *,
        hub: RegistryHub,
        content: CompiledContent,
        services: ServiceResolver,
        mod_id: str,
        mod_version: str,
        seed: int,
        log_sink: LogSink | None = None,
        log_level: LogLevel = LogLevel.INFO,
            clock: Callable[[], float] | None = None,
            media: Media | None = None,
            context: Context | None = None,
            params: Mapping[str, Any] | None = None,
            functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        """组装一个引擎运行期。

        输入：
            state: 初始真实 State（dict 根；由 Mod 的初始化内容构造）；
            hub: 已经登记完 Mod 内容的注册表中心；
            content: 已经编译好的内容；
            services: 服务解析端口（适配器实现：service 条目 id → 可调用对象）；
            mod_id: 本次运行的 Mod 标识（非空字符串）；
            mod_version: 本次运行的 Mod 版本（来自 mod_info.json；写进存档并在读档时比对）；
            seed: 随机种子（新游戏开局用；读档时用 restore_random_state 恢复）；
            log_sink: 日志出货口（适配器提供；None 表示不记日志）；
            log_level: 最低记录级别；
            clock: 读时钟的调用（适配器提供；None 表示不记录时间戳）；
            media: 媒体端口（适配器提供；None 表示静默——没有声音设备时也照常跑）；
            context: 界面状态（不存档）；缺省新建一个。热重载时把旧的那一个传进来，
                镜头 / 选中这类界面状态就能跨重载保留（R6-5）；
            params: 平铺参数（加载层从 Mod 与模块收集来的那一份，见模块规格 D-2）；
            functions: 追加的表达式函数表（例如几何函数）；随机的三个函数由引擎自动加入，
                如果调用方给了同名函数，以调用方给的为准。
        输出：
            无（构造对象）。
        异常：
            TypeError / ValueError: 参数不合法（由各部件自己抛出）。
        变量：
            table: 表达式函数表（先放随机函数，再并进调用方给的表）。
        """
        self._state: dict = state
        self._mod_id: str = mod_id
        self._mod_version: str = mod_version
        self._hub: RegistryHub = hub
        self._content: CompiledContent = content
        self._journal: Journal = Journal()
        self._context: Context = context if context is not None else Context()
        self._sequence: SequenceCounter = SequenceCounter()
        self._rng: Rng = Rng(seed)
        self._logger: Logger = Logger(sink=log_sink, clock=clock, level=log_level)
        self._media: Media = media if media is not None else SilentMedia()
        self._params: Mapping[str, Any] = dict(params or {})
        # 派生值刷新用的事务序号（拼 Command id 用，保证 id 确定、唯一）。
        self._derived_serial: int = 0

        table: dict[str, Callable[..., Any]] = dict(self._rng.functions())
        if functions:
            table.update(functions)
        self._functions: Mapping[str, Callable[..., Any]] = table

        self._orchestrator: Orchestrator = Orchestrator(
            content, functions=self._functions, logger=self._logger
        )
        self._rule_checker: RuleChecker = RuleChecker(functions=self._functions, logger=self._logger)
        self._applier: Applier = Applier(
            _EngineFirstServices(services, self._builtin_services()),
            self._sequence,
            self._rng,
            self._context,
            mod_id,
            logger=self._logger,
            media=self._media,
            params=self._params,
            functions=self._functions,
        )
        self._event_chain: EventChain = EventChain(
            content,
            rule_checker=self._rule_checker,
            applier=self._applier,
            logger=self._logger,
            functions=self._functions,
        )
        self._dispatcher: Dispatcher = Dispatcher(
            state,
            content,
            orchestrator=self._orchestrator,
            rule_checker=self._rule_checker,
            applier=self._applier,
            event_chain=self._event_chain,
            recorder=self._journal,
            logger=self._logger,
            mod_id=mod_id,
            functions=self._functions,
        )
        self._logger.info(
            "引擎运行时已组装",
            mod_id=mod_id,
            extra={
                "registry": hub.summary(),
                "commands": len(content.commands),
                "actions": len(content.actions),
                "rules": len(content.rules),
                "triggers": sum(len(items) for items in content.triggers.values()),
                "services": len(content.services),
                "seed": seed,
            },
        )

    @property
    def state(self) -> dict:
        """返回真实 State（只读用途：UI 拉取、存档快照；写入只能走提交）。"""
        return self._state

    @property
    def context(self) -> Context:
        """返回非存档上下文。"""
        return self._context

    @property
    def media(self) -> Media:
        """返回媒体端口（宿主可用它停音乐；测试用它核对注入是否生效）。"""
        return self._media

    @property
    def journal(self) -> Journal:
        """返回记录器（命令序列、结算批次日志、快照）。"""
        return self._journal

    @property
    def hub(self) -> RegistryHub:
        """返回注册表中心。"""
        return self._hub

    @property
    def content(self) -> CompiledContent:
        """返回编译后的内容。"""
        return self._content

    @property
    def mod_id(self) -> str:
        """返回本次运行的 Mod 标识。"""
        return self._mod_id

    @property
    def mod_version(self) -> str:
        """返回本次运行的 Mod 版本。"""
        return self._mod_version

    @property
    def rng(self) -> Rng:
        """返回确定性随机模块（存档要保存它的 state()）。"""
        return self._rng

    @property
    def logger(self) -> Logger:
        """返回日志对象。"""
        return self._logger

    @property
    def sequence(self) -> SequenceCounter:
        """返回变化量序号分配器。"""
        return self._sequence

    @property
    def orchestrator(self) -> Orchestrator:
        """返回编排器。"""
        return self._orchestrator

    @property
    def rule_checker(self) -> RuleChecker:
        """返回规则校验器。"""
        return self._rule_checker

    @property
    def applier(self) -> Applier:
        """返回应用器。"""
        return self._applier

    @property
    def event_chain(self) -> EventChain:
        """返回事件链执行器。"""
        return self._event_chain

    @property
    def dispatcher(self) -> Dispatcher:
        """返回分发器（输入源与 Mod 通过它提交输入、触发结算）。"""
        return self._dispatcher

    @property
    def functions(self) -> Mapping[str, Callable[..., Any]]:
        """返回表达式函数表（随机函数与调用方追加的函数）。"""
        return self._functions

    def _builtin_services(self) -> dict:
        """返回引擎自带的原子服务表（§2 术语表："引擎原子服务或 Mod 脚本"）。

        输入：无。
        输出：
            服务 id → 可调用对象。当前只有一项：
                engine:service:refresh_derived —— 按层重算派生值（§6.5 / D-12）。
        异常：
            无。
        变量：
            无。

        说明：
            Mod 想让"基础值一变就重算派生值"，只要在自己的触发链里写一个动作：
                {"service": "engine:service:refresh_derived"}
            产出物照常进同一个 CommandDelta（§16），不需要任何额外通道。
        """
        return {
            # 函数表要一起传：公式里写了 ["call", …] 时，触发链这条路才和引擎自愈那条路等价（R6-3）。
            REFRESH_DERIVED_SERVICE: make_refresh_service(
                self._content.formulas, functions=self._functions),
            # 模板属性里也可能写 ["call", …]：函数表同样要传（N-2）。
            CREATE_INSTANCE_SERVICE: make_create_instance_service(
                self._content, functions=self._functions),
        }

    def refresh_derived(self) -> tuple:
        """按层重算全部派生值（引擎自愈，§6.5 / D-33 / O-01）。

        输入：无。
        输出：
            本次真正改动的条目元组（DerivedUpdate）；已经正确时是空元组。
        异常：
            LogicError / state 层异常: 公式求值失败或目标路径走不通；
            DeltaError: 公式算出来的值不是纯数据。
        变量：
            command_id / command / view / api: 这次刷新用的临时事务；
            updates: 计算结果。

        说明：
            - 走的是"临时状态 + 提交"这条正规通道：每条改动都产生变化量、都进日志；
            - 因为不是玩家命令产生的，这批变化量以 `engine:derived` 标签记进批次日志，
              这样"快照 + 批次"的回放才完整（否则回放会漂）；
            - 读档之后与存档之前各调用一次，就是草案说的"存读自愈"。
        """
        if not self._content.formulas:
            return ()
        self._derived_serial += 1
        command_id = f"{self._mod_id}:derived:{self._derived_serial}"
        command = Command(
            command_id=command_id,
            definition_id=REFRESH_DERIVED_SERVICE,
            source="engine",
            payload={},
            created_at=0.0,
        )
        view = TempState(self._state, command_id)
        api = EngineApi(
            view,
            self._sequence,
            command,
            self._mod_id,
            rng=self._rng,
            logger=self._logger,
            context=self._context,
            media=self._media,
            functions=self._functions,
        )
        handle = api.for_service(f"{command_id}#derived", REFRESH_DERIVED_SERVICE)
        updates = refresh_derived(
            view,
            self._content.formulas,
            lambda update: emit_update(handle, update),
            functions=self._functions,
        )
        if not updates:
            view.discard()
            return ()
        changes = view.command_delta()
        view.commit(self._state)
        self._journal.record_maintenance(changes, label="engine:derived")
        self._logger.info(
            f"派生值已自愈：{len(changes)} 条",
            mod_id=self._mod_id,
            command_id=command_id,
            extra={"formulas": len(self._content.formulas), "updates": len(updates)},
        )
        return updates

    def restore_random_state(self, state: dict) -> None:
        """恢复随机状态（读档时调用；随机状态随存档保存，见 D-23）。

        输入：
            state: 存档里的随机状态（core.rng.Rng.state() 的产物）。
        输出：
            无。
        异常：
            TypeError / RngError: 状态不合法（见 core.rng.Rng.restore）。
        变量：
            无。
        """
        self._rng.restore(state)
        self._logger.info("随机状态已恢复", mod_id=self._mod_id)

    def mod_record(self) -> ModRecord:
        """返回本次运行的 Mod 记录（存档里记的就是它）。

        输入：无。
        输出：
            ModRecord（id / 版本 / 条目 schema 版本）。
        异常：
            无。
        变量：
            无。
        """
        return ModRecord(id=self._mod_id, version=self._mod_version)

    def build_save(self, *, scenario_id: str = "", meta: dict | None = None) -> SaveFile:
        """组装一份当前局面的存档（§18.1）。

        输入：
            scenario_id: 场景 id（引擎只存不解释）；
            meta: 元信息（存档名、时间等；引擎只存不解释）。
        输出：
            SaveFile：基础 State = 记录器里最近一次快照（没取过就现在取一次），
            之后是快照之后记录的全部结算批次、随机状态、命令序列。
        异常：
            TypeError: meta 不是 dict。
        变量：
            无。

        说明：
            本方法只组装结构，不落盘：要写文件就用 core.persistence.write_save
            配一个文件端口（例如 adapters.LocalFileSystem）。
        """
        # 存档前先自愈一遍派生值（§6.5 第 3 条：保存 / 加载时都重算一次）。
        self.refresh_derived()
        return self._journal.build_save(
            state=self._state,
            mod=self.mod_record(),
            random_state=self._rng.state(),
            scenario_id=scenario_id,
            meta=meta,
        )

    def apply_save(self, save: SaveFile) -> None:
        """把一份存档应用到这个运行期（读档）。

        输入：
            save: 已通过结构 / 版本校验的存档（见 core.persistence.read_save）。
        输出：
            无；现场变成存档记录的局面。
        异常：
            SaveVersionError: 存档的 Mod 与本次运行不一致（D-25 拒绝加载）；
            TypeError: save 不是 SaveFile。
        变量：
            replayed: 从快照 + 变化量叠出来的终态；本次直接采用它作为真实 State。

        说明：
            - **就地替换** State 的内容而不换根对象：引擎各处（视图、管线）都持有
              这个根，换对象会像 §6.2 说的那样破坏锚点；
            - 采用"快照 + 叠加变化量"的回放结果（§18.2），不重跑 Command 与规则；
            - 随机状态、命令序列与批次日志一并接回，读档后再存档不会丢历史；
            - **未结算的命令队列清空**（R6-4）：那是旧时间线上的意图，不能落到新局面上；
            - **界面状态清空**（R6-5）：Context 不随存档恢复，读档之后 Mod 视图从干净状态开始。
        """
        if not isinstance(save, SaveFile):
            raise TypeError(f"apply_save 需要 SaveFile，实际是 {type(save).__name__}")

        require_same_mod(self.mod_record(), save.mod, path="")
        replayed = replay_state(save)
        self._state.clear()
        self._state.update(replayed)
        self._rng.restore(save.random_state)
        self._journal.restore(save)
        # 读档 = 换到另一条时间线：旧时间线上"点了还没结算"的命令要丢掉（R6-4）。
        dropped = self._dispatcher.clear_queue()
        # 界面状态不跨存档沿用（R6-5）：文档口径就是"读档后 Context 是空的"。
        self._context.clear()
        if dropped:
            self._logger.info("读档丢弃了未结算的命令", extra={"dropped": dropped})
        # 命令 id 接着历史走（R5-1）：不然读档后 id 从 1 重来、与历史撞车
        # （一份存档里两条同名命令，破坏 P6 / D-41 的溯源）。
        # **变化量序号不接**（R5-5）：它不存档、只用于本次运行的排序（SequenceCounter 的类文档），
        # 读档＝新的一段运行、从 1 重新发号即可；何况批次 15 之后存档里没有批次，想接也接不到。
        self._dispatcher.resume_command_ids(command.command_id for command in save.commands)
        # 读档之后自愈一遍派生值（§6.5 第 3 条）：旧存档里算错的派生值会被改回正确值。
        self.refresh_derived()
        self._logger.info(
            "存档已应用",
            mod_id=self._mod_id,
            extra={
                "scenario_id": save.scenario_id,
                "commands": len(save.commands),
                "settlements": len(save.settlements),
            },
        )


class _EngineFirstServices:
    """服务解析的包装：引擎自带的原子服务优先，其余交给 Mod 的服务表。

    字段：
        _mod_services: Mod 侧的服务解析端口（ServiceResolver）；
        _builtin: 引擎自带的服务（id → 可调用对象）。

    说明：
        为什么不直接把内置服务塞进 Mod 的服务表：那张表是 Mod 加载层建的，
        引擎自带的东西不该混进 Mod 的数据里（P1）；这一层包装让"谁来提供实现"
        在运行期是显式的（P4），并且引擎服务的 id 带 `engine:` 前缀，一眼能认出来。
    """

    def __init__(self, mod_services: ServiceResolver, builtin: Mapping[str, Callable[..., Any]]) -> None:
        """创建包装。

        输入：
            mod_services: Mod 的服务解析端口；
            builtin: 引擎自带服务表。
        输出：
            无（构造对象）。
        异常：
            TypeError: mod_services 没有 resolve 方法。
        变量：
            无。
        """
        if not hasattr(mod_services, "resolve"):
            raise TypeError("mod_services 必须实现 resolve(service_id)（见 core.ports.ServiceResolver）")
        self._mod_services = mod_services
        self._builtin = dict(builtin)

    def resolve(self, service_id: str):
        """按 id 取服务函数：先查引擎自带的，再问 Mod 的服务表。

        输入：
            service_id: 服务条目 id。
        输出：
            可调用对象。
        异常：
            ServiceNotAvailableError: 两边都没有这个 id（由 Mod 侧或这里抛出）。
        变量：
            无。
        """
        service = self._builtin.get(service_id)
        if service is not None:
            return service
        return self._mod_services.resolve(service_id)
