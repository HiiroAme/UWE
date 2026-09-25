"""把注册表里的纯数据编译成运行期结构（加载期做一次）。

位置：
    引擎核心逻辑层 → content 子包；由 Mod 加载层在登记完注册表之后调用一次。

职责：
    1. 逐类型读出条目内容，按下面的口径解析成 CompiledContent；
    2. 做**加载期静态检查**：结构写法、表达式写法（交给 logic.parse）、
       条目之间的引用（action / rule / service / event / command 是否真的存在且启用）；
    3. 把"按事件分组并排好顺序的订阅""按输入类别分组的映射"这类运行期查表
       一次算好，运行期只做取值。

条目内容口径（O-02 尚未定稿，这里是本引擎 v1 的写法，逐条登记在实现决策里）：

    command.data = {
        "branches": [                      # 必填、非空；运行时取第一个条件成立的分支
            {"condition": <表达式>,         # 必填；恒真写成 true
             "actions": [                   # 必填；可以是空列表（条件成立但什么都不做）
                 {"action": "<action id>", "args": {<名字>: <表达式>}}   # args 可省略
             ]}
        ]
    }

    action.data = {
        "rules": ["<rule id>", ...],       # 可省略，表示不做规则检查
        "steps": [                          # 可省略，表示这个 Action 不调用任何 Service
            {"service": "<service id>", "args": {<名字>: <表达式>}}     # args 可省略
        ]
    }

    rule.data = {"condition": <表达式>, "message": "<拒绝原因>"}          # message 可省略

    trigger.data = {
        "event": "<event id>",             # 必填；必须能在事件注册表里查到
        "order": <整数>,                    # 可省略，缺省 0；小的先执行
        "actions": [<Action 调用>, ...]     # 必填；可以是空列表
    }

    input.data = {
        "kind": "<输入类别>",              # 必填；同一类别可以有多条映射（按注册顺序取第一条成立的）
        "command": "<command id>",         # 必填
        "condition": <表达式>,              # 可省略，缺省恒真
        "payload": {<名字>: <表达式>}        # 可省略，缺省空
    }

    ui.data = {
        "script": "scripts/ui_battle.py",  # 必填：视图脚本（相对 Mod 文件夹）
        "callable": "build_view",          # 可省略，缺省 build_view
        "title": "战场"                     # 可省略，缺省取 metadata.name / id 第三段
    }

    formula.data = {
        "target": "/units/u1/power",        # 必填：派生值写回哪条路径
        "expression": <表达式>,              # 必填：公式本身（"完全由基础值决定"）
        "layer": 1                          # 可省略，缺省 0；小的先算
    }

    syscall.data = {
        "handler": "save",                  # 必填：处理函数名（由适配器 / 外壳提供）
        "args": {}                          # 可省略：条目里写死的参数
    }

    entity.data = { "attributes": {<属性名>: <表达式或字面量>} }     # 模板：一类"东西"的初始属性
    node.data   = { "terrain": "<terrain id>",                    # 可省略：套用一份地形预设
                    "attributes": {<属性名>: <表达式或字面量>} }    # 模板：一类"格子"的初始属性
    terrain.data = { "attributes": {<属性名>: <表达式或字面量>} }   # 便捷编辑 node 的预设（被 node 引用）
    phase.data   = { "actions": ["<action id>", ...],              # 这个阶段允许的动作（要存在）
                     "settings": {<名字>: <表达式>} }              # 可省略：阶段附带的其他设置
    system.data  = { "state": {"/turn": 1, "/weather": "晴"} }      # 新游戏时写进初始 State 的值

    event.data = { }                        # 引擎只登记事件 id 与含义，不解释内容

要点：
    - 引用一律"必须存在且启用"（RegistryTable.require 的口径），加载期就报错；
    - args / payload 是表达式块：值可以是字面量，也可以是 ["arg", …] / ["get", …] 这样的调用；
    - 所有表达式都在这里过一遍 logic.parse 做静态检查，运行期不再重新检查结构。

草案依据：
    §9 管线第 2、5、6、9 步；§11.1 事件注册表；§11.2 订阅顺序；
    §14.2 注册表清单；D-29 加载期静态检查；D-35 分支结构；D-14 运行期不复查；
    §13 遍历顺序必须确定（排序一律给出明确的比较依据）。
"""

from typing import Any

from ..engine_services import CREATE_INSTANCE_SERVICE, REFRESH_DERIVED_SERVICE
from ..logic import collect_literal_paths, parse, parse_data
from ..registry import MissingEntryError, RegistryEntry, RegistryHub, merge_data
from ..state.errors import PathSyntaxError
from ..state.path import parse as parse_path
from ..state.wildcard import is_pattern, patterns_overlap, validate_pattern
from .compiled import (
    CompiledAction,
    CompiledBranch,
    CompiledCommand,
    CompiledContent,
    CompiledInput,
    CompiledInvocation,
    CompiledPage,
    CompiledFormula,
    CompiledRule,
    CompiledSyscall,
    CompiledPhase,
    CompiledSystem,
    CompiledTemplate,
    CompiledStep,
    CompiledTrigger,
)
from .errors import ContentFormatError, ContentReferenceError



# 每种条目类型的 data 允许键（R5-6）。键的来源：各自 _compile_* 里实际读的那些
# （`rules` / `steps` / `attributes` / `settings` / `payload` 这些**可省略**，
# 省略与拼错在引擎里分不出来，所以拼错要在加载期报出来）。
# 没登记的条目类型不查：宁可不查，也不误报还没覆盖到的写法。
_DATA_KEYS = {
    "action": {"rules", "steps"},
    "rule": {"condition", "message"},
    "command": {"branches"},
    # event 不查：data 是自由元数据（见 注册表细节.md 的 event 口径与 §4.3 通则）
    "trigger": {"actions", "event", "order"},
    "input": {"command", "condition", "kind", "payload"},
    "phase": {"actions", "settings"},
    "formula": {"expression", "layer", "target"},
    "syscall": {"args", "handler"},
    "system": {"state"},
    "entity": {"attributes", "terrain"},
    "node": {"attributes", "terrain"},
    "terrain": {"attributes", "terrain"},
    "ui": {"callable", "script", "title"},
    "service": {"script", "callable"},
}


def _check_data_keys(hub) -> None:
    """按类型核对每条条目的 data 键（R5-6）。

    输入：hub（注册表中心）。
    输出：无。
    异常：ContentFormatError（未知键，消息里列出允许的键）。
    变量：type_name / allowed / entry / unknown: 遍历的中间结果。
    """
    for type_name, allowed in _DATA_KEYS.items():
        for entry in hub.table(type_name).enabled():
            unknown = sorted(set(entry.data) - allowed)
            if unknown:
                raise ContentFormatError(
                    f"data 里有不认识的键 {unknown}；{type_name} 允许的键是 {sorted(allowed)}"
                    "（拼错的键以前会被静默忽略、等于悄悄丢掉规则或效果）",
                    entry_id=entry.id,
                    location=f"{entry.id} → data",
                )

def compile_content(hub: RegistryHub) -> CompiledContent:
    """把注册表中心里的内容编译成运行期结构。

    输入：
        hub: 已经登记完条目（继承也已解析）的注册表中心。
    输出：
        CompiledContent。
    异常：
        ContentFormatError: 条目内容写法不合法；
        ContentReferenceError: 条目引用了不存在 / 已禁用 / 类型不对的目标；
        LogicError: 表达式写法不合法（操作符、参数个数、路径写法）。
    变量：
        rules / actions / commands: 三个编译结果（按依赖顺序编译）；
        triggers: 事件 id → 排好序的订阅元组；
        inputs: 输入类别 → 按注册顺序排好的映射元组。
    """
    _check_data_keys(hub)          # 逐类型核对 data 键（R5-6）

    rules = {entry.id: _compile_rule(entry) for entry in hub.table("rule").enabled()}
    actions = {entry.id: _compile_action(entry, rules, hub) for entry in hub.table("action").enabled()}
    commands = {entry.id: _compile_command(entry, actions) for entry in hub.table("command").enabled()}
    triggers = _compile_triggers(hub, actions)
    inputs = _compile_inputs(hub)
    pages = _compile_pages(hub)
    formulas = _compile_formulas(hub)
    syscalls = _compile_syscalls(hub)
    templates = _compile_templates(hub)
    phases = _compile_phases(hub, actions)
    systems = _compile_systems(hub)

    return CompiledContent(
        commands=commands,
        actions=actions,
        rules=rules,
        triggers=triggers,
        inputs=inputs,
        services=tuple(entry.id for entry in hub.table("service").enabled()),
        events=tuple(entry.id for entry in hub.table("event").enabled()),
        pages=pages,
        formulas=formulas,
        syscalls=syscalls,
        templates=templates,
        phases=phases,
        systems=systems,
    )


def _compile_rule(entry: RegistryEntry) -> CompiledRule:
    """编译一条 rule 条目。

    输入：
        entry: rule 条目。
    输出：
        CompiledRule。
    异常：
        ContentFormatError: 缺少 condition，或 message 不是字符串；
        LogicError: condition 表达式写法不合法。
    变量：
        data: 条目负载；
        message: 拒绝原因（缺省用 rule id）。
    """
    data = entry.data
    condition = _require_key(data, "condition", entry, "rule 需要 condition")
    message = data.get("message", "")
    if not isinstance(message, str):
        raise ContentFormatError(
            f"message 必须是字符串，实际是 {type(message).__name__}",
            entry_id=entry.id,
            location=f"{entry.id} → data.message",
        )
    return CompiledRule(
        rule_id=entry.id,
        condition=parse(condition, location=f"{entry.id} → data.condition"),
        message=message or entry.id,
        location=entry.id,
    )


def _compile_action(entry: RegistryEntry, rules: dict[str, CompiledRule],
                    hub: RegistryHub) -> CompiledAction:
    """编译一条 action 条目。

    输入：
        entry: action 条目；
        rules: 已编译的规则表（用于解析引用）。
    输出：
        CompiledAction。
    异常：
        ContentFormatError: rules / steps 不是列表，或某一项写法不合法；
        ContentReferenceError: 引用的 rule 不存在或被禁用；
        LogicError: 参数表达式写法不合法。
    变量：
        compiled_rules / compiled_steps: 编译结果；
        index / item: 遍历 rules / steps 时的下标与元素。
    """
    data = entry.data
    raw_rules = data.get("rules", [])
    location = f"{entry.id} → data.rules"
    _require_list(raw_rules, entry, location, "rules")
    compiled_rules: list[CompiledRule] = []
    for index, rule_id in enumerate(raw_rules):
        item_location = f"{entry.id} → data.rules[{index}]"
        if not isinstance(rule_id, str) or not rule_id:
            raise ContentFormatError(
                "rules 的每一项都必须是非空字符串（rule 条目 id）", entry_id=entry.id, location=item_location
            )
        if rule_id not in rules:
            raise ContentReferenceError(
                f"引用的规则不存在或已被禁用：{rule_id!r}", entry_id=entry.id, location=item_location
            )
        compiled_rules.append(rules[rule_id])

    raw_steps = data.get("steps", [])
    location = f"{entry.id} → data.steps"
    _require_list(raw_steps, entry, location, "steps")
    compiled_steps: list[CompiledStep] = []
    for index, item in enumerate(raw_steps):
        item_location = f"{entry.id} → data.steps[{index}]"
        step = _require_dict(item, entry, item_location, "steps 的每一项")
        service_id = _require_key(step, "service", entry, "step 需要 service", location=item_location)
        if not isinstance(service_id, str) or not service_id:
            raise ContentFormatError(
                "service 必须是非空字符串（service 条目 id）", entry_id=entry.id, location=item_location
            )
        # 服务名必须在注册表里登记过（与 event / command / action 的引用同样的加载期检查）：
        # 写错一个字母不该等到玩家点下去才发现（D-29 / P6）。
        # `engine:` 前缀是**引擎自己提供的原子服务**（由运行期装配，不在注册表里）。
        # 它们是**已知的一小撮**，所以按"前缀 + 清单"校验：拼错的名字也在加载期就报出来，
        # 而不是等玩家点下去才变成"取不到服务实现"（R4-11）。清单与两个来源常量同源。
        if service_id.startswith("engine:"):
            if service_id not in (REFRESH_DERIVED_SERVICE, CREATE_INSTANCE_SERVICE):
                raise ContentReferenceError(
                    f"引擎没有这个服务：{service_id!r}；"
                    f"现在提供的是 {[REFRESH_DERIVED_SERVICE, CREATE_INSTANCE_SERVICE]}",
                    entry_id=entry.id,
                    location=item_location,
                )
        else:
            _require_reference(hub, "service", service_id, entry, item_location, "服务")
        compiled_steps.append(
            CompiledStep(
                service_id=service_id,
                args=_compile_args(step.get("args", {}), entry, f"{item_location}.args"),
                location=item_location,
            )
        )
    return CompiledAction(
        action_id=entry.id,
        rules=tuple(compiled_rules),
        steps=tuple(compiled_steps),
        source=entry.source,
    )


def _compile_command(entry: RegistryEntry, actions: dict[str, CompiledAction]) -> CompiledCommand:
    """编译一条 command 条目。

    输入：
        entry: command 条目；
        actions: 已编译的 Action 表（用于解析引用）。
    输出：
        CompiledCommand。
    异常：
        ContentFormatError: branches 不是列表或为空、分支写法不合法；
        ContentReferenceError: 引用的 action 不存在或被禁用；
        LogicError: 条件或参数表达式写法不合法。
    变量：
        branches: 编译结果；
        index / item: 遍历分支时的下标与元素。
    """
    raw_branches = _require_key(entry.data, "branches", entry, "command 需要 branches")
    location = f"{entry.id} → data.branches"
    _require_list(raw_branches, entry, location, "branches")
    if not raw_branches:
        raise ContentFormatError(
            "branches 不能是空列表：一个命令至少要有一条分支（恒真分支写成 condition: true）",
            entry_id=entry.id,
            location=location,
        )

    branches: list[CompiledBranch] = []
    for index, item in enumerate(raw_branches):
        item_location = f"{entry.id} → data.branches[{index}]"
        branch = _require_dict(item, entry, item_location, "分支")
        condition = _require_key(branch, "condition", entry, "分支需要 condition", location=item_location)
        raw_actions = _require_key(branch, "actions", entry, "分支需要 actions", location=item_location)
        branches.append(
            CompiledBranch(
                condition=parse(condition, location=f"{item_location}.condition"),
                actions=_compile_invocations(raw_actions, entry, f"{item_location}.actions", actions),
                location=item_location,
            )
        )
    return CompiledCommand(command_id=entry.id, branches=tuple(branches), source=entry.source)


def _compile_triggers(
    hub: RegistryHub,
    actions: dict[str, CompiledAction],
) -> dict[str, tuple[CompiledTrigger, ...]]:
    """编译全部 trigger 条目，并按事件分组排序。

    输入：
        hub: 注册表中心；
        actions: 已编译的 Action 表。
    输出：
        事件 id → 订阅元组（先按 order 升序，再按注册顺序——排序稳定且确定）。
    异常：
        ContentFormatError: 写法不合法；
        ContentReferenceError: 订阅的事件不存在 / 动作不存在，或条目类型不对；
        LogicError: 参数表达式写法不合法。
    变量：
        grouped: 事件 id → (注册下标, 订阅) 列表；
        index / entry: 遍历 trigger 注册表时的注册顺序与条目。
    """
    grouped: dict[str, list[tuple[int, CompiledTrigger]]] = {}
    for index, entry in enumerate(hub.table("trigger").enabled()):
        data = entry.data
        event_id = _require_key(data, "event", entry, "trigger 需要 event")
        if not isinstance(event_id, str) or not event_id:
            raise ContentFormatError(
                "event 必须是非空字符串（事件条目 id）", entry_id=entry.id, location=f"{entry.id} → data.event"
            )
        # 订阅的事件必须在事件注册表里登记过且启用（§11.1）。
        _require_reference(hub, "event", event_id, entry, f"{entry.id} → data.event", "事件")

        order = data.get("order", 0)
        if not isinstance(order, int) or isinstance(order, bool):
            raise ContentFormatError(
                f"order 必须是整数，实际是 {type(order).__name__}",
                entry_id=entry.id,
                location=f"{entry.id} → data.order",
            )
        raw_actions = _require_key(data, "actions", entry, "trigger 需要 actions")
        trigger = CompiledTrigger(
            trigger_id=entry.id,
            event_id=event_id,
            order=order,
            actions=_compile_invocations(raw_actions, entry, f"{entry.id} → data.actions", actions),
            source=entry.source,
        )
        grouped.setdefault(event_id, []).append((index, trigger))

    # 排序依据必须确定：先按 order，再按注册下标（注册表本身保持插入顺序）。
    result: dict[str, tuple[CompiledTrigger, ...]] = {}
    for event_id, items in grouped.items():
        result[event_id] = tuple(
            trigger for _, trigger in sorted(items, key=lambda pair: (pair[1].order, pair[0]))
        )
    return result


def _compile_inputs(hub: RegistryHub) -> dict[str, tuple[CompiledInput, ...]]:
    """编译全部 input 条目，并按输入类别分组（保持注册顺序）。

    输入：
        hub: 注册表中心。
    输出：
        输入类别（条目 id 的第三段）→ 映射元组。
    异常：
        ContentFormatError: 写法不合法；
        ContentReferenceError: 引用的 command 不存在或被禁用；
        LogicError: 条件或 payload 表达式写法不合法。
    变量：
        grouped: 类别 → 映射列表；
        entry: 当前遍历到的 input 条目。
    """
    grouped: dict[str, list[CompiledInput]] = {}
    for entry in hub.table("input").enabled():
        data = entry.data
        kind = _require_key(data, "kind", entry, "input 需要 kind")
        if not isinstance(kind, str) or not kind:
            raise ContentFormatError(
                "kind 必须是非空字符串（输入类别）",
                entry_id=entry.id,
                location=f"{entry.id} → data.kind",
            )
        command_id = _require_key(data, "command", entry, "input 需要 command")
        if not isinstance(command_id, str) or not command_id:
            raise ContentFormatError(
                "command 必须是非空字符串（command 条目 id）",
                entry_id=entry.id,
                location=f"{entry.id} → data.command",
            )
        _require_reference(hub, "command", command_id, entry, f"{entry.id} → data.command", "命令")
        compiled = CompiledInput(
            input_id=entry.id,
            kind=kind,
            command_id=command_id,
            condition=parse(data.get("condition", True), location=f"{entry.id} → data.condition"),
            payload=_compile_args(data.get("payload", {}), entry, f"{entry.id} → data.payload"),
            location=entry.id,
        )
        grouped.setdefault(compiled.kind, []).append(compiled)
    return {kind: tuple(items) for kind, items in grouped.items()}


def _compile_pages(hub: RegistryHub) -> dict[str, CompiledPage]:
    """编译全部 ui 条目（游戏内界面的页）。

    输入：
        hub: 注册表中心。
    输出：
        ui 条目 id → CompiledPage。
    异常：
        ContentFormatError: 缺 script，或 callable / title 类型不对。
    变量：
        entry / script / callable_name / title: 遍历与组装的中间结果。
    """
    pages: dict[str, CompiledPage] = {}
    for entry in hub.table("ui").enabled():
        data = entry.data
        script = _require_key(data, "script", entry, "ui 需要 script（视图脚本路径）")
        if not isinstance(script, str) or not script:
            raise ContentFormatError(
                "script 必须是非空字符串（相对 Mod 文件夹的脚本路径）",
                entry_id=entry.id,
                location=f"{entry.id} → data.script",
            )
        callable_name = data.get("callable", "build_view")
        if not isinstance(callable_name, str) or not callable_name:
            raise ContentFormatError(
                "callable 必须是非空字符串（视图函数名）",
                entry_id=entry.id,
                location=f"{entry.id} → data.callable",
            )
        title = data.get("title", entry.display_name)
        if not isinstance(title, str) or not title:
            raise ContentFormatError(
                "title 必须是非空字符串",
                entry_id=entry.id,
                location=f"{entry.id} → data.title",
            )
        pages[entry.id] = CompiledPage(
            page_id=entry.id,
            title=title,
            script=script,
            callable_name=callable_name,
            location=entry.id,
        )
    return pages


def _compile_formulas(hub: RegistryHub) -> dict[str, CompiledFormula]:
    """编译全部 formula 条目（派生值公式），并检查层号规则。

    输入：
        hub: 注册表中心。
    输出：
        formula 条目 id → CompiledFormula（保持注册顺序，运行期再按 layer 排序）。
    异常：
        ContentFormatError: 缺 target / expression、路径写法不合法、layer 不合法、
            两条公式写同一个 target、或依赖关系无法定序（读到了同层或更高层派生值）。
    变量：
        formulas / by_target: 编译结果与"目标路径 → 公式"的索引（查依赖用）；
        entry / target / layer: 遍历与解析的中间结果。

    层号规则（D-12 / D-33 / D-39 的落地）：
        派生值按 layer 从小到大算（第一层只由定值引出，第二层可以用第一层的结果……）。
        因此一条公式**只能读层号更小的派生值**：
            读到同层的另一个派生值 → 谁先算都说不通，直接报错；
            读到更高层的派生值 → 会读到上一轮的旧值，等于环形依赖，也报错。
        路径按参数拼出来的（动态路径）在加载期看不出来，那部分由 Mod 自己保证顺序。
    """
    formulas: dict[str, CompiledFormula] = {}
    by_target: dict[str, CompiledFormula] = {}
    for entry in hub.table("formula").enabled():
        data = entry.data
        target = _require_key(data, "target", entry, "formula 需要 target（派生值写回哪条路径）")
        if not isinstance(target, str) or not target:
            raise ContentFormatError(
                "target 必须是非空字符串（完整路径）", entry_id=entry.id, location=f"{entry.id} → data.target"
            )
        if target == "":
            raise ContentFormatError(
                "target 不能指向整棵树（根不是「一个变量」）",
                entry_id=entry.id,
                location=f"{entry.id} → data.target",
            )
        try:
            parse_path(target)
        except PathSyntaxError as exc:
            raise ContentFormatError(
                f"target 路径写法不合法：{exc.detail}", entry_id=entry.id, location=f"{entry.id} → data.target"
            ) from exc
        try:
            validate_pattern(target)
        except PathSyntaxError as exc:
            raise ContentFormatError(
                f"target 的通配写法不合法：{exc.detail}", entry_id=entry.id, location=f"{entry.id} → data.target"
            ) from exc
        for other_target, other in by_target.items():
            if patterns_overlap(other_target, target):
                raise ContentFormatError(
                    f"两条公式可能写同一条路径：{target!r} 与 {other_target!r}"
                    f"（后者来自 {other.formula_id!r}）",
                    entry_id=entry.id,
                    location=f"{entry.id} → data.target",
                )
        expression = _require_key(data, "expression", entry, "formula 需要 expression（公式本身）")
        layer = data.get("layer", 0)
        if not isinstance(layer, int) or isinstance(layer, bool):
            raise ContentFormatError(
                f"layer 必须是整数，实际是 {type(layer).__name__}",
                entry_id=entry.id,
                location=f"{entry.id} → data.layer",
            )
        if layer < 0:
            raise ContentFormatError(
                f"layer 不能是负数：{layer}", entry_id=entry.id, location=f"{entry.id} → data.layer"
            )

        compiled = CompiledFormula(
            formula_id=entry.id,
            target=target,
            expression=parse_data(expression, location=f"{entry.id} → data.expression"),
            layer=layer,
            location=entry.id,
        )
        formulas[entry.id] = compiled
        by_target[target] = compiled

    for compiled in formulas.values():
        for path in collect_literal_paths(compiled.expression):
            for other_target, other in by_target.items():
                if patterns_overlap(path, other_target):
                    if other.layer >= compiled.layer:
                        raise ContentFormatError(
                            f"派生值的依赖关系无法定序：本条 target={compiled.target!r}（第 {compiled.layer} 层）"
                            f"读了 {other_target!r}（第 {other.layer} 层）；只能读层号更小的派生值",
                            entry_id=compiled.formula_id,
                            location=f"{compiled.formula_id} → data.expression",
                        )
    return formulas


def _compile_syscalls(hub: RegistryHub) -> dict[str, CompiledSyscall]:
    """编译全部 syscall 条目（系统事件登记）。

    输入：
        hub: 注册表中心。
    输出：
        syscall 条目 id → CompiledSyscall。
    异常：
        ContentFormatError: 缺 handler、或 handler / args 类型不对。
    变量：
        entry / handler / args: 遍历与解析的中间结果。

    说明：
        引擎只登记"这条系统事件该由哪个处理函数执行、带什么参数"，
        处理函数由适配器 / 外壳提供（P7）。所以这里除了查格式，什么都不做。
    """
    syscalls: dict[str, CompiledSyscall] = {}
    for entry in hub.table("syscall").enabled():
        data = entry.data
        handler = _require_key(data, "handler", entry, "syscall 需要 handler（处理函数名）")
        if not isinstance(handler, str) or not handler:
            raise ContentFormatError(
                "handler 必须是非空字符串",
                entry_id=entry.id,
                location=f"{entry.id} → data.handler",
            )
        args = data.get("args", {})
        if not isinstance(args, dict):
            raise ContentFormatError(
                f"args 必须是对象，实际是 {type(args).__name__}",
                entry_id=entry.id,
                location=f"{entry.id} → data.args",
            )
        syscalls[entry.id] = CompiledSyscall(
            syscall_id=entry.id,
            handler=handler,
            args=dict(args),
            location=entry.id,
        )
    return syscalls


def _compile_invocations(
    raw_actions: Any,
    entry: RegistryEntry,
    location: str,
    actions: dict[str, CompiledAction],
) -> tuple[CompiledInvocation, ...]:
    """编译一串 Action 调用（command 分支的 result、trigger 的执行内容共用）。

    输入：
        raw_actions: 原始内容（必须是列表）；
        entry: 所属条目（报错用）；
        location: 位置串；
        actions: 已编译的 Action 表。
    输出：
        编译结果元组（保持声明顺序）。
    异常：
        ContentFormatError: 不是列表、某一项不是 dict、action 字段缺失或不是字符串；
        ContentReferenceError: 引用的 action 不存在或被禁用；
        LogicError: 参数表达式写法不合法。
    变量：
        index / item: 遍历时的下标与元素；
        action_id: 本项引用的 action id。
    """
    _require_list(raw_actions, entry, location, "actions")
    result: list[CompiledInvocation] = []
    for index, item in enumerate(raw_actions):
        item_location = f"{location}[{index}]"
        invocation = _require_dict(item, entry, item_location, "Action 调用")
        action_id = _require_key(invocation, "action", entry, "调用需要 action", location=item_location)
        if not isinstance(action_id, str) or not action_id:
            raise ContentFormatError(
                "action 必须是非空字符串（action 条目 id）", entry_id=entry.id, location=item_location
            )
        if action_id not in actions:
            raise ContentReferenceError(
                f"引用的 Action 不存在或已被禁用：{action_id!r}", entry_id=entry.id, location=item_location
            )
        result.append(
            CompiledInvocation(
                action_id=action_id,
                args=_compile_args(invocation.get("args", {}), entry, f"{item_location}.args"),
                location=item_location,
            )
        )
    return tuple(result)


def _compile_args(value: Any, entry: RegistryEntry, location: str) -> Any:
    """检查并解析一个参数块（args / payload）。

    输入：
        value: 参数块（必须是 dict；内部可以任意嵌套表达式）；
        entry: 所属条目（报错用）；
        location: 位置串。
    输出：
        已静态检查的表达式块（dict，内部是 Node 或字面量）。
    异常：
        ContentFormatError: value 不是 dict；
        LogicError: 内部表达式写法不合法。
    变量：
        key / item: 遍历参数块时的键与值。
    """
    if not isinstance(value, dict):
        raise ContentFormatError(
            f"参数块必须是 dict，实际是 {type(value).__name__}", entry_id=entry.id, location=location
        )
    return {key: parse_data(item, location=f"{location}.{key}") for key, item in value.items()}


def _require_reference(
    hub: RegistryHub,
    type_name: str,
    target_id: str,
    entry: RegistryEntry,
    location: str,
    what: str,
) -> RegistryEntry:
    """要求某个 id 在指定注册表里存在且启用，否则报内容引用错。

    输入：
        hub: 注册表中心；
        type_name: 目标注册表类型名；
        target_id: 被引用的条目 id；
        entry: 发起引用的条目（报错用）；
        location: 位置串；
        what: 目标的中文说法（拼报错消息，例如 "事件" / "命令"）。
    输出：
        被引用的条目。
    异常：
        ContentReferenceError: 目标不存在、被禁用，或注册表类型不对。
    变量：
        无。

    说明：
        registry 层抛的是 MissingEntryError（"注册表里没有"），本层把它转成
        ContentReferenceError 并补上"谁在哪里引用了它"，这样报错既能直接定位条目，
        又能说清引用关系；两层异常各管各的，不互相替代。
    """
    try:
        return hub.require(type_name, target_id)
    except MissingEntryError as exc:
        raise ContentReferenceError(
            f"引用的{what}不可用：{exc.detail}（{target_id!r}）",
            entry_id=entry.id,
            location=location,
        ) from exc


def _require_key(
    data: dict,
    key: str,
    entry: RegistryEntry,
    message: str,
    location: str | None = None,
) -> Any:
    """要求 dict 里有某个键，并返回它的值。

    输入：
        data: 条目负载；
        key: 必需的键名；
        entry: 所属条目（报错用）；
        message: 缺失时的说明；
        location: 位置串；缺省用 "条目 id → data.键名"。
    输出：
        键对应的值。
    异常：
        ContentFormatError: 键缺失。
    变量：
        无。
    """
    if key not in data:
        raise ContentFormatError(
            message, entry_id=entry.id, location=location or f"{entry.id} → data.{key}"
        )
    return data[key]


def _require_dict(value: Any, entry: RegistryEntry, location: str, what: str) -> dict:
    """要求一个值是 dict。

    输入：
        value: 待检查的值；
        entry: 所属条目；
        location: 位置串；
        what: 这个值的用途说明（拼报错消息）。
    输出：
        value 本身（当它是 dict 时）。
    异常：
        ContentFormatError: value 不是 dict。
    变量：
        无。
    """
    if not isinstance(value, dict):
        raise ContentFormatError(
            f"{what}必须是 dict，实际是 {type(value).__name__}", entry_id=entry.id, location=location
        )
    return value


def _require_list(value: Any, entry: RegistryEntry, location: str, what: str) -> list:
    """要求一个值是 list。

    输入：
        value: 待检查的值；
        entry: 所属条目；
        location: 位置串；
        what: 用途说明。
    输出：
        value 本身（当它是 list 时）。
    异常：
        ContentFormatError: value 不是 list。
    变量：
        无。
    """
    if not isinstance(value, list):
        raise ContentFormatError(
            f"{what} 必须是列表，实际是 {type(value).__name__}", entry_id=entry.id, location=location
        )
    return value


def _compile_templates(hub: RegistryHub) -> dict[str, CompiledTemplate]:
    """编译 entity / node 条目（模板），并把 node 引用的 terrain 预设合并进去。

    输入：
        hub: 注册表中心。
    输出：
        条目 id → CompiledTemplate（entity 与 node 放在同一张表里，靠 kind 区分）。
    异常：
        ContentFormatError: attributes 不是对象、terrain 引用写法不对或不是 terrain 条目。
    变量：
        templates / entry / kind / attributes / terrain: 遍历与合并的中间结果。

    合并口径：
        terrain 的 attributes 是**底座**，node 自己的 attributes 覆盖同名项
        （与 extends 的口径一致：dict 递归合并，其余整体覆盖）。这样"地形预设"
        与"具体节点"的分工和草案 §14.2 说的一致：terrain 是便捷编辑 node 的手段。
    """
    templates: dict[str, CompiledTemplate] = {}
    for kind in ("entity", "node"):
        for entry in hub.table(kind).enabled():
            attributes = _require_dict(
                entry.data.get("attributes", {}),
                entry,
                f"{entry.id} → data.attributes",
                "attributes",
            )
            parsed: dict = {}
            if kind == "node":
                parsed = _terrain_attributes(hub, entry)
            parsed = merge_data(parsed, _parse_value_table(attributes, entry, f"{entry.id} → data.attributes"))
            templates[entry.id] = CompiledTemplate(
                template_id=entry.id,
                kind=kind,
                attributes=parsed,
                source=entry.source,
            )
    return templates


def _terrain_attributes(hub: RegistryHub, entry: RegistryEntry) -> dict:
    """取一个 node 条目引用的 terrain 预设属性（没有引用时是空字典）。

    输入：
        hub: 注册表中心；
        entry: node 条目。
    输出：
        已解析的地形属性表。
    异常：
        ContentFormatError: terrain 引用不是字符串、找不到、或没有 attributes。
    变量：
        terrain_id / terrain_entry / attributes: 中间结果。
    """
    terrain_id = entry.data.get("terrain", "")
    if terrain_id == "":
        return {}
    if not isinstance(terrain_id, str):
        raise ContentFormatError(
            f"terrain 必须是字符串（terrain 条目 id），实际是 {type(terrain_id).__name__}",
            entry_id=entry.id,
            location=f"{entry.id} → data.terrain",
        )
    terrain_entry = _require_reference(
        hub, "terrain", terrain_id, entry, f"{entry.id} → data.terrain", "地形"
    )
    attributes = _require_dict(
        terrain_entry.data.get("attributes", {}),
        terrain_entry,
        f"{terrain_entry.id} → data.attributes",
        "attributes",
    )
    return _parse_value_table(attributes, terrain_entry, f"{terrain_entry.id} → data.attributes")


def _compile_phases(hub: RegistryHub, actions: dict) -> dict[str, CompiledPhase]:
    """编译 phase 条目（阶段）。

    输入：
        hub: 注册表中心；
        actions: 已编译的 Action 表（用来校验引用）。
    输出：
        phase 条目 id → CompiledPhase。
    异常：
        ContentFormatError: actions 不是列表、某个动作不是字符串或不存在；
            settings 不是对象。
    变量：
        phases / entry / raw_actions / action_id / settings: 中间结果。
    """
    phases: dict[str, CompiledPhase] = {}
    for entry in hub.table("phase").enabled():
        raw_actions = entry.data.get("actions", [])
        if not isinstance(raw_actions, list):
            raise ContentFormatError(
                f"actions 必须是列表，实际是 {type(raw_actions).__name__}",
                entry_id=entry.id,
                location=f"{entry.id} → data.actions",
            )
        compiled_actions: list = []
        for index, action_id in enumerate(raw_actions):
            location = f"{entry.id} → data.actions[{index}]"
            if not isinstance(action_id, str) or not action_id:
                raise ContentFormatError(
                    "actions 的每一项都必须是非空字符串（action 条目 id）",
                    entry_id=entry.id,
                    location=location,
                )
            if action_id not in actions:
                raise ContentFormatError(
                    f"这个阶段引用的动作不存在或已被禁用：{action_id!r}",
                    entry_id=entry.id,
                    location=location,
                )
            compiled_actions.append(action_id)
        settings = entry.data.get("settings", {})
        phases[entry.id] = CompiledPhase(
            phase_id=entry.id,
            actions=tuple(compiled_actions),
            settings=_parse_value_table(settings, entry, f"{entry.id} → data.settings"),
            source=entry.source,
        )
    return phases


def _compile_systems(hub: RegistryHub) -> dict[str, CompiledSystem]:
    """编译 system 条目（系统级初始值）。

    输入：
        hub: 注册表中心。
    输出：
        system 条目 id → CompiledSystem。
    异常：
        ContentFormatError: 缺 state、state 不是对象、路径不合法或写了通配。
    变量：
        systems / entry / raw_values / path: 中间结果。

    说明：
        路径必须写具体（不许通配）：这些值是在**新游戏初始化**时写进初始 State 的，
        那一刻树正在被搭出来，没有"枚举现有节点"可言（通配留给派生值用）。
    """
    systems: dict[str, CompiledSystem] = {}
    for entry in hub.table("system").enabled():
        raw_values = _require_key(entry.data, "state", entry, "system 需要 state（初始值表）")
        if not isinstance(raw_values, dict):
            raise ContentFormatError(
                f"state 必须是对象（路径 → 值），实际是 {type(raw_values).__name__}",
                entry_id=entry.id,
                location=f"{entry.id} → data.state",
            )
        values: dict = {}
        for path, expression in raw_values.items():
            location = f"{entry.id} → data.state[{path!r}]"
            if not isinstance(path, str) or not path:
                raise ContentFormatError("初始值的键必须是非空路径字符串", entry_id=entry.id, location=location)
            try:
                parse_path(path)
                validate_pattern(path)
            except PathSyntaxError as exc:
                raise ContentFormatError(
                    f"初始值路径写法不合法：{exc.detail}", entry_id=entry.id, location=location
                ) from exc
            if is_pattern(path):
                raise ContentFormatError(
                    "系统初始值的路径必须写具体（不许用通配）：初始化时树还没搭好，没法枚举",
                    entry_id=entry.id,
                    location=location,
                )
            values[path] = parse_data(expression, location=location)
        systems[entry.id] = CompiledSystem(system_id=entry.id, values=values, source=entry.source)
    return systems


def _parse_value_table(table: dict, entry: RegistryEntry, location: str) -> dict:
    """把"名字 → 表达式"的表逐项解析成已检查的表达式。

    输入：
        table: 原始表（名字 → 值）；
        entry: 所属条目（报错用）；
        location: 位置串。
    输出：
        新的表：名字 → 已解析的表达式。
    异常：
        ContentFormatError: 名字不是字符串；
        LogicError: 值写得不合法。
    变量：
        name / value: 遍历到的键与值。
    """
    parsed: dict = {}
    for name, value in table.items():
        if not isinstance(name, str):
            raise ContentFormatError(
                f"名字必须是字符串，实际是 {type(name).__name__}", entry_id=entry.id, location=location
            )
        parsed[name] = parse_data(value, location=f"{location}.{name}")
    return parsed
