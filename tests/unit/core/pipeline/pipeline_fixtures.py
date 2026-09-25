"""管线测试共用的夹具（不是测试文件，不被 run_tests.py 收集）。

位置：tests/unit/core/pipeline/

提供：
    - make_state()：一棵小 State 树（一个单位 u1，带行动力、位置、标记与日志）；
    - demo 内容（事件 / 服务 / 规则 / 动作 / 命令 / 订阅 / 输入）与默认服务实现；
    - build_hub / make_content / make_runtime / make_command 等组装助手。

为什么集中在这里：
    编排、规则、应用、事件链、分发五个部件的测试都要用同一套内容；
    夹具只有一份，规则改了以后测试跟着改一次即可（避免各写一份互相漂移）。

这套演示内容的口径：
    命令 demo:command:move
        └─ 分支（存在 /units/u1）→ 动作 demo:action:move（参数 to 来自输入）
              ├─ 规则 demo:rule:has_ap：行动力 > 0
              └─ 服务 demo:service:apply_move：改位置、扣行动力，两条变化量都标 unit_moved
    事件 demo:event:unit_moved
        └─ 订阅 demo:trigger:on_moved → 动作 demo:action:mark → 服务 demo:service:mark_moved：置 moved=true
"""

from core.content import CompiledContent, compile_content
from core.delta import DataType, Operation
from core.logger import LogLevel, MemorySink
from core.pipeline import Command, EngineApi
from core.ports import SilentMedia
from core.registry import RegistryEntry, RegistryHub
from core.runtime import EngineRuntime
from adapters import MappingServiceResolver


def entry(entry_id: str, type_name: str, data: dict, **overrides) -> RegistryEntry:
    """造一条条目。

    输入：
        entry_id: 完整 id；type_name: 注册表类型；data: 负载；overrides: 覆盖字段。
    输出：
        RegistryEntry。
    异常：
        无。
    变量：
        fields: 默认字段字典。
    """
    fields = {"id": entry_id, "type": type_name, "data": data}
    fields.update(overrides)
    return RegistryEntry(**fields)


def make_state() -> dict:
    """造一棵测试用的 State 树。

    输入：无。
    输出：
        新的 dict。
    异常：
        无。
    变量：
        无。
    """
    return {
        "turn": 1,
        "units": {"u1": {"ap": 3, "position": "n1", "moved": False, "log": ["start"]}},
    }


def demo_entries() -> list[RegistryEntry]:
    """返回那套演示内容（见模块文档）。

    输入：无。
    输出：
        条目列表。
    异常：
        无。
    变量：
        无。
    """
    return [
        entry("demo:event:unit_moved", "event", {}),
        entry("demo:service:apply_move", "service", {"script": "scripts/move.py", "callable": "apply"}),
        entry("demo:service:mark_moved", "service", {"script": "scripts/mark.py", "callable": "mark"}),
        entry(
            "demo:rule:has_ap",
            "rule",
            {"condition": [">", ["get", "/units/u1/ap"], 0], "message": "行动力不足"},
        ),
        entry(
            "demo:action:move",
            "action",
            {
                "rules": ["demo:rule:has_ap"],
                "steps": [{"service": "demo:service:apply_move", "args": {"to": ["arg", "to"]}}],
            },
        ),
        entry(
            "demo:action:mark",
            "action",
            {"steps": [{"service": "demo:service:mark_moved", "args": {"at": ["arg", "at"]}}]},
        ),
        entry(
            "demo:command:move",
            "command",
            {
                "branches": [
                    {
                        "condition": ["exists", "/units/u1"],
                        "actions": [{"action": "demo:action:move", "args": {"to": ["arg", "node"]}}],
                    },
                    {"condition": True, "actions": []},
                ]
            },
        ),
        entry(
            "demo:trigger:on_moved",
            "trigger",
            {
                "event": "demo:event:unit_moved",
                "order": 0,
                "actions": [{"action": "demo:action:mark", "args": {"at": ["get", "/units/u1/position"]}}],
            },
        ),
        entry(
            "demo:input:click_node",
            "input",
            {
                "kind": "click_node",
                "command": "demo:command:move",
                "payload": {"node": ["arg", "node"]},
            },
        ),
    ]


def build_hub(entries: list[RegistryEntry] | None = None) -> RegistryHub:
    """建一个登记了给定条目的注册表中心。

    输入：
        entries: 条目列表；缺省用演示内容。
    输出：
        RegistryHub。
    异常：
        无（登记失败会在测试里显式暴露）。
    变量：
        hub: 注册表中心。
    """
    hub = RegistryHub()
    hub.register_all(list(demo_entries() if entries is None else entries))
    return hub


def make_content(entries: list[RegistryEntry] | None = None) -> CompiledContent:
    """编译给定条目（缺省是演示内容）。

    输入：
        entries: 条目列表。
    输出：
        CompiledContent。
    异常：
        ContentError / LogicError: 内容不合法（由编译器抛出）。
    变量：
        无。
    """
    return compile_content(build_hub(entries))


def make_services(**overrides) -> dict:
    """返回演示内容需要的服务实现（可以用关键字覆盖其中某一个）。

    输入：
        overrides: 要替换的服务（服务 id 的最后一段做键，例如 apply_move=fn）。
    输出：
        service 条目 id → 函数。
    异常：
        无。
    变量：
        table: 默认实现表。
    """

    def apply_move(api, args):
        """把单位挪到 args["to"] 并扣一点行动力，两条变化量都标 unit_moved。"""
        old_position = api.get("/units/u1/position")
        api.emit(
            "/units/u1/position",
            Operation.MODIFY,
            DataType.STRING,
            value=args["to"],
            old_value=old_position,
            label="demo:event:unit_moved",
        )
        ap = api.get("/units/u1/ap")
        api.emit(
            "/units/u1/ap",
            Operation.MODIFY,
            DataType.NUMBER,
            value=ap - 1,
            old_value=ap,
            label="demo:event:unit_moved",
        )

    def mark_moved(api, args):
        """把 moved 置为 True（事件链的订阅者）。"""
        api.emit(
            "/units/u1/moved",
            Operation.MODIFY,
            DataType.BOOL,
            value=True,
            old_value=api.get("/units/u1/moved"),
        )

    table = {
        "demo:service:apply_move": apply_move,
        "demo:service:mark_moved": mark_moved,
    }
    for name, service in overrides.items():
        table[f"demo:service:{name}"] = service
    return table


def make_runtime(
    *,
    state: dict | None = None,
    entries: list[RegistryEntry] | None = None,
    services: dict | None = None,
    seed: int = 7,
    level: LogLevel = LogLevel.TRACE,
    functions: dict | None = None,
    mod_id: str = "demo",
    mod_version: str = "0.0.0",
) -> tuple[EngineRuntime, MemorySink]:
    """组装一个跑这套演示内容的引擎运行期。

    输入：
        state: 初始 State（缺省用 make_state()）；
        entries: 条目（缺省用演示内容）；
        services: 服务表（缺省用 make_services()）；
        seed: 随机种子；level: 日志级别；
        functions: 追加的表达式函数表；mod_id: Mod 标识；mod_version: Mod 版本。
    输出：
        (runtime, sink) 二元组；sink 是收集日志的内存出口。
    异常：
        无（组装失败会在测试里显式暴露）。
    变量：
        hub / content / resolver / sink: 组装过程中的中间对象。
    """
    hub = build_hub(entries)
    content = compile_content(hub)
    resolver = MappingServiceResolver(make_services() if services is None else services)
    sink = MemorySink()
    runtime = EngineRuntime(
        make_state() if state is None else state,
        hub=hub,
        content=content,
        services=resolver,
        mod_id=mod_id,
        mod_version=mod_version,
        seed=seed,
        log_sink=sink,
        log_level=level,
        functions=functions,
    )
    return runtime, sink


def make_command(
    command_id: str = "demo:cmd:1",
    *,
    node: str = "n2",
    created_at: float = 12.5,
    definition_id: str = "demo:command:move",
) -> Command:
    """造一个演示命令（move 到 node）。

    输入：
        command_id: 命令 id；node: 目标节点；created_at: 交互时间；
        definition_id: 命令定义 id。
    输出：
        Command。
    异常：
        无。
    变量：
        无。
    """
    return Command(
        command_id=command_id,
        definition_id=definition_id,
        source="ui",
        payload={"node": node},
        created_at=created_at,
    )


def make_api(view, runtime: EngineRuntime, command: Command) -> EngineApi:
    """按运行期的公共手段造一个记账台（测试里手工产出变化量时用）。

    输入：
        view: 临时状态视图；
        runtime: 引擎运行期（序号、随机、日志、上下文从它取）；
        command: 本视图对应的命令。
    输出：
        EngineApi。
    异常：
        无（视图与命令不一致时由构造函数抛出）。
    变量：
        无。
    """
    return EngineApi(
        view,
        runtime.sequence,
        command,
        runtime.mod_id,
        rng=runtime.rng,
        logger=runtime.logger,
        context=runtime.context,
        media=SilentMedia(),
        functions=runtime.functions,
    )
