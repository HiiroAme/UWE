"""编译后的内容（引擎运行期真正使用的那份结构）。

位置：
    引擎核心逻辑层 → content 子包。

为什么要有"编译"这一步：
    注册表里存的是**纯数据**（P2：数据对象不携带可执行内容），而运行期需要的是
    "已经静态检查过、能直接求值"的结构。两者之间隔一层编译：
        注册表（数据） --compile--> CompiledContent（引擎对象）
    好处有三条：
      1. 静态检查只做一次：操作符、参数个数、路径写法、引用是否存在都在加载期查完
         （D-29、P6），运行期不再逐条复查（D-14）；
      2. 运行期不重复解析：表达式已经 parse 成 Node，反复求值不需要再解析；
      3. 数据与执行分离：注册表条目始终是干净的 JSON，引擎用的是自己这层对象。

这里放的是"结构"（哪个 action 有哪几步、哪个命令有哪些分支），不是游戏数据；
游戏数据只在 State 里（单树原则，D-09）。

草案依据：
    §9 管线第 2、5、6、9 步；§11.2 Trigger 的链式执行；
    §14.2 各注册表的"作用与连接"；D-35 command → action 是 condition → result 的分支；
    P2（数据与执行分离）、P4（显式）、P8（只做现在用得上的部分）。
"""

from dataclasses import dataclass
from typing import Any, Mapping, Callable

from ..logic import resolve_mapping, truth
from ..logic.eval import Reader


@dataclass(frozen=True)
class CompiledRule:
    """一条规则（rule 条目的编译结果）。

    字段：
        rule_id: 规则条目 id；
        condition: 已解析的条件表达式（Node 或字面量）；
        message: 条件不成立时写进日志的原因（缺省是 rule_id）；
        location: 来源位置串，便于日志定位（来自哪个条目）。
    """

    rule_id: str
    condition: Any
    message: str
    location: str

    def check(
        self,
        reader: Reader,
        *,
        args: Mapping[str, Any] | None = None,
        functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> bool:
        """判断规则是否成立。

        输入：
            reader: 读取入口（运行期传临时状态视图，§10）；
            args: 当前 Action 的参数表，供条件里的 ["arg", 名字] 使用；
            functions: 外部函数表（随机、几何……）。
        输出：
            True 表示通过；False 表示被这条规则拒绝。
        异常：
            LogicError: 条件结果不是布尔（logic.truth 的口径）；
            state 层异常: 条件里的路径走不通。
        变量：
            无。
        """
        return truth(self.condition, reader, args=args, functions=functions)


@dataclass(frozen=True)
class CompiledStep:
    """Action 里的一次 Service 调用（action 条目 steps 里的一项）。

    字段：
        service_id: 要调用的 service 条目 id；
        args: 参数表达式块（dict；未求值）；
        location: 来源位置串。
    """

    service_id: str
    args: Any
    location: str

    def resolve_args(
        self,
        reader: Reader,
        *,
        args: Mapping[str, Any] | None = None,
        functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> dict:
        """在"调用这一刻"把参数表达式算成具体参数。

        输入：
            reader: 读取入口（临时状态视图）；
            args: 当前 Action 的参数表（脚本里写 ["arg", 名字] 就取这里的值）；
            functions: 外部函数表。
        输出：
            新的 dict：参数已全部求值。
        异常：
            LogicError: 参数不是 dict、表达式不合法或求值失败。
        变量：
            无。
        """
        return resolve_mapping(self.args, reader, args=args, functions=functions)


@dataclass(frozen=True)
class CompiledAction:
    """一个 Action 的定义（action 条目的编译结果）。

    字段：
        action_id: action 条目 id；
        rules: 执行前必须全部通过的规则（按声明顺序）；
        steps: 依次调用的 Service 及参数（按声明顺序）；
        source: 条目来源（Mod 与文件），日志溯源用。
    """

    action_id: str
    rules: tuple[CompiledRule, ...]
    steps: tuple[CompiledStep, ...]
    source: str


@dataclass(frozen=True)
class CompiledInvocation:
    """一次 Action 调用（command 分支的 result、trigger 的执行内容）。

    字段：
        action_id: 被调用的 action 条目 id；
        args: 参数表达式块（未求值；求值时机见 Applier）；
        location: 来源位置串。
    """

    action_id: str
    args: Any
    location: str


@dataclass(frozen=True)
class CompiledBranch:
    """命令的一条分支（condition 成立就走它给出的 Action 调用序列）。

    字段：
        condition: 条件表达式（Node 或字面量；字面量 True 表示恒真）；
        actions: 条件成立时依次调用的 Action；
        location: 来源位置串。
    """

    condition: Any
    actions: tuple[CompiledInvocation, ...]
    location: str

    def test(
        self,
        reader: Reader,
        *,
        args: Mapping[str, Any] | None = None,
        functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> bool:
        """判断本条分支的条件是否成立。

        输入：
            reader: 读取入口（临时状态视图）；
            args: Command 的 payload；
            functions: 外部函数表。
        输出：
            True / False。
        异常：
            LogicError / state 层异常: 与 CompiledRule.check 相同。
        变量：
            无。
        """
        return truth(self.condition, reader, args=args, functions=functions)


@dataclass(frozen=True)
class CompiledCommand:
    """一个 Command 的拆解定义（command 条目的编译结果）。

    字段：
        command_id: command 条目 id；
        branches: 按声明顺序排列的分支；运行时取**第一个条件成立的分支**；
        source: 条目来源。
    """

    command_id: str
    branches: tuple[CompiledBranch, ...]
    source: str


@dataclass(frozen=True)
class CompiledTrigger:
    """一个事件订阅（trigger 条目的编译结果）。

    字段：
        trigger_id: trigger 条目 id；
        event_id: 订阅的事件 id；
        order: 订阅顺序（小的先执行；相同则按注册顺序）；
        actions: 事件发生时依次调用的 Action；
        source: 条目来源。
    """

    trigger_id: str
    event_id: str
    order: int
    actions: tuple[CompiledInvocation, ...]
    source: str


@dataclass(frozen=True)
class CompiledInput:
    """一条输入映射（input 条目的编译结果）。

    字段：
        input_id: input 条目 id；
        kind: 输入类别（来自 data.kind，例如 "click_node"）；同一类别可以有多条映射，
            运行期按注册顺序取第一条条件成立的；
        command_id: 该输入要产生的 Command 条目 id；
        condition: 条件表达式（Node 或字面量）；由输入数据与 State 决定是否匹配；
        payload: Command 的 payload 表达式块（dict）；
        location: 来源位置串。
    """

    input_id: str
    kind: str
    command_id: str
    condition: Any
    payload: Any
    location: str


@dataclass(frozen=True)
class CompiledPage:
    """游戏内界面的一页（ui 条目的编译结果，§17.1）。

    字段：
        page_id: ui 条目 id；
        title: 显示名（缺省取条目的 metadata.name / id 第三段）；
        script: 脚本路径（相对 Mod 文件夹）；
        callable_name: 视图函数名（缺省 build_view）；
        location: 来源位置串。
    """

    page_id: str
    title: str
    script: str
    callable_name: str
    location: str


@dataclass(frozen=True)
class CompiledFormula:
    """一条派生值公式（formula 条目的编译结果，§6.5 / D-12 / D-33）。

    字段：
        formula_id: formula 条目 id；
        target: 算出来的值写回哪条路径（完整 JSON Pointer，不能指向根）；
        expression: 已解析的公式（Node 或字面量容器）；
        layer: 第几层（小的先算；同层之间不允许互相依赖）；
        location: 来源位置串。

    说明：
        派生值 = **由基础值完全决定的量**，自己没有自由度；引擎只负责"按层把它们算一遍"，
        怎么算完全由 Mod 的公式决定（引擎不认识任何玩法公式）。
    """

    formula_id: str
    target: str
    expression: Any
    layer: int
    location: str


@dataclass(frozen=True)
class CompiledSyscall:
    """一条系统事件登记（syscall 条目的编译结果，§17.3）。

    字段：
        syscall_id: 条目 id（例如 "engine:syscall:save"）；
        handler: 处理函数名（适配器 / 外壳按这个名字提供实现）；
        args: 条目里写死的参数（调用时可以被覆盖）；
        location: 来源位置串。
    """

    syscall_id: str
    handler: str
    args: dict
    location: str


@dataclass(frozen=True)
class CompiledTemplate:
    """一个模板（entity / node 条目的编译结果）。

    字段：
        template_id: 条目 id；
        kind: "entity" 或 "node"（决定它是给"东西"用的还是给"格子"用的，仅仅为了日志与报错）；
        attributes: 属性名 → 值表达式（已解析；运行时按名字算成具体值）；
        source: 条目来源（日志溯源）。

    说明：
        模板只是"一类东西的初始属性表"。**引擎不会自己去实例化**（它不知道实例该放在
        State 的哪条路径上，D-09），实例化由 Mod 决定——既可以自己在初始化钩子里写，
        也可以调用引擎原子服务 `engine:service:create_instance` 并给出目标路径。
    """

    template_id: str
    kind: str
    attributes: Mapping[str, Any]
    source: str


@dataclass(frozen=True)
class CompiledPhase:
    """一个阶段（phase 条目的编译结果）。

    字段：
        phase_id: 条目 id；
        actions: 这个阶段允许的动作 id（已校验存在且启用）；
        settings: 阶段附带的其他设置（名字 → 值表达式）；
        source: 条目来源。

    说明：
        "这个阶段能不能做某件事"由 Mod 自己判断（它可以在自己的脚本或公式里读这些内容），
        引擎只保证"登记的东西是自洽的"。
    """

    phase_id: str
    actions: tuple[str, ...]
    settings: Mapping[str, Any]
    source: str


@dataclass(frozen=True)
class CompiledSystem:
    """一组系统级初始值（system 条目的编译结果，§6.4 全局变量的初始值）。

    字段：
        system_id: 条目 id；
        values: 路径 → 值表达式（路径必须写具体，不许用通配）；
        source: 条目来源。

    说明：
        这些值在**新游戏初始化时**写进初始 State（那时 State 正在被构造，谈不上"改动"），
        之后就和其他数据一样：要改就走变化量。
    """

    system_id: str
    values: Mapping[str, Any]
    source: str


@dataclass(frozen=True)
class CompiledContent:
    """全部编译结果：运行期按 id 直接查，不重新解析。

    字段（每个都是"注册顺序"的元组或按 id 的字典，遍历一律有序——§13）：
        commands: command_id → CompiledCommand；
        actions: action_id → CompiledAction；
        rules: rule_id → CompiledRule；
        triggers: event_id → 该事件的订阅（已按 order 与注册顺序排好）；
        inputs: 按 kind 分组的输入映射（已按注册顺序排好）；
        services: service 条目 id 的集合（脚本解析由加载层负责，这里只记"引用得到"）；
        events: 事件 id 的集合（§11.1 事件注册表）；
        pages: ui 条目 id → 页面定义（游戏内界面的页，由 Mod 提供）。
        formulas: formula 条目 id → 派生值公式（按层重算时按 layer 排序）。
        syscalls: syscall 条目 id → 系统事件登记（保存 / 退出等）。
        templates: entity / node 条目 id → 模板（node 已经合并过它引用的 terrain）；
        phases: phase 条目 id → 阶段；
        systems: system 条目 id → 系统级初始值。
    """

    commands: Mapping[str, CompiledCommand]
    actions: Mapping[str, CompiledAction]
    rules: Mapping[str, CompiledRule]
    triggers: Mapping[str, tuple[CompiledTrigger, ...]]
    inputs: Mapping[str, tuple[CompiledInput, ...]]
    services: tuple[str, ...]
    events: tuple[str, ...]
    pages: Mapping[str, CompiledPage]
    formulas: Mapping[str, CompiledFormula]
    syscalls: Mapping[str, CompiledSyscall]
    templates: Mapping[str, CompiledTemplate]
    phases: Mapping[str, CompiledPhase]
    systems: Mapping[str, CompiledSystem]

    def command(self, command_id: str) -> CompiledCommand | None:
        """按 id 取命令定义（没有则 None）。

        输入：
            command_id: command 条目 id。
        输出：
            CompiledCommand 或 None。
        异常：
            无。
        变量：
            无。

        说明：
            这里给 None 而不是抛异常，是因为"命令定义不存在"在运行期是一条
            可记录的丢弃原因（§19.2 一个 Command 被丢弃并记原因），不是致命错误。
        """
        return self.commands.get(command_id)

    def action(self, action_id: str) -> CompiledAction | None:
        """按 id 取 Action 定义（没有则 None）。"""
        return self.actions.get(action_id)

    def rule(self, rule_id: str) -> CompiledRule | None:
        """按 id 取规则（没有则 None）。"""
        return self.rules.get(rule_id)

    def subscriptions(self, event_id: str) -> tuple[CompiledTrigger, ...]:
        """取某个事件的订阅者（已排好顺序；没有订阅者时是空元组）。

        输入：
            event_id: 事件 id。
        输出：
            订阅者元组。
        异常：
            无。
        变量：
            无。
        """
        return self.triggers.get(event_id, ())

    def input_candidates(self, kind: str) -> tuple[CompiledInput, ...]:
        """取某个输入类别的映射（按注册顺序；没有时是空元组）。

        输入：
            kind: 输入类别（input 条目 id 的第三段）。
        输出：
            CompiledInput 元组。
        异常：
            无。
        变量：
            无。
        """
        return self.inputs.get(kind, ())

    def has_service(self, service_id: str) -> bool:
        """判断某个 service 条目是否登记过。"""
        return service_id in self.services

    def has_event(self, event_id: str) -> bool:
        """判断某个事件 id 是否登记过。"""
        return event_id in self.events
