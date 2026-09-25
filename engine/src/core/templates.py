"""模板与系统初始值：entity / node / terrain / phase / system 的引擎侧行为。

位置：
    引擎核心逻辑层。与 core.derived 同级——两者都是"按 Mod 声明的数据做机械的事"。

本模块做三件事：
    1. `make_create_instance_service(content)`：引擎原子服务
       `engine:service:create_instance`——按 entity / node 模板在 State 的**指定路径**上
       新建一个实例（路径由 Mod 给，引擎不猜，D-09）；
    2. `apply_system_defaults(systems, state, ...)`：把 system 条目里的初始值写进
       新游戏的初始 State（§6.4 全局变量的初始值）；
    3. 两个小工具：`template_attributes`（取模板属性）与 `phase_actions`（取阶段允许的动作），
       供 Mod 的初始化钩子通过 `ModContext` 使用。

为什么引擎能这么做而不算"懂玩法"（P1）：
    模板是 Mod 写的、目标路径是 Mod 给的、属性名是 Mod 起的；
    引擎只做"把这张表搬到那条路径上"这种机械动作，遇到任何玩法名词都不认识。

草案依据：
    §14.2 entity / node / terrain / phase / system 五行注册表的作用；
    §6.4 全局变量注册进 State；D-09 State 单树（引擎不预设结构）；
    §16 改动必须经引擎接口产生变化量（实例化走 api.emit）；P1 引擎不认识玩法。
"""

from typing import Any, Callable, Mapping

from .content import CompiledContent, CompiledTemplate
from .delta import data_type_of
from .logic import StateReader, evaluate
from .state.path import escape_token
from .state.path import format_path
from .state.path import parse as parse_path
from .state.tree import add_key, exists as state_exists, replace
from .state.values import snapshot_value


def make_create_instance_service(content: CompiledContent,
                                 *, functions: Mapping[str, Callable[..., Any]] | None = None):
    """造出引擎原子服务 `engine:service:create_instance` 的实现。

    输入：
        content: 编译后的内容（模板在这里）。
        functions: 表达式可用到的外部函数表（随机、几何……）。运行期要传进来：
            模板属性里写 ["call", …] 时才算得出来（N-2 / R6-3 同族）。
    输出：
        服务函数 fn(api, args) -> None。
    异常：
        无（真正的校验在调用时做）。
    变量：
        无。

    服务参数（Mod 写在动作步骤的 args 里）：
        template: 模板条目 id（必填；entity 或 node 都行）；
        path:     新实例放在哪条具体路径（必填；父容器必须已经存在）；
        overrides: 覆盖模板属性的值（可选；同样可以写表达式）；
        label:    标在变化量上的事件 id（可选）。
    """

    def create_instance(api, args):
        """按模板在指定路径建一个实例（逐条属性产出"新增键"的变化量）。

        输入：
            api: 服务手柄（读状态、产变化量）；
            args: 见上面的服务参数说明。
        输出：
            无。
        异常：
            KeyError / ValueError: 参数缺失或写法不对（引擎会记成服务失败）；
            PathNotFoundError / StateConflictError: 路径走不通或已经存在实例时由 state 层抛出。
        变量：
            template / target / values / name / value: 中间结果。
        """
        template_id = args["template"]
        target = args["path"]
        overrides = args.get("overrides", {}) or {}
        label = args.get("label", "") if isinstance(args.get("label", ""), str) else ""

        if not isinstance(template_id, str) or not template_id:
            raise ValueError(f"template 必须是非空字符串，实际是 {template_id!r}")
        template = content.templates.get(template_id)
        if template is None:
            raise ValueError(
                f"没有这个模板：{template_id!r}（可用：{sorted(content.templates)}）"
            )
        if not isinstance(target, str) or not target:
            raise ValueError(f"path 必须是非空字符串，实际是 {target!r}")
        if not isinstance(overrides, dict):
            raise ValueError(f"overrides 必须是对象，实际是 {type(overrides).__name__}")

        values: dict = {}
        for name, expression in template.attributes.items():
            values[name] = evaluate(expression, api, args=args, functions=functions)
        for name, value in overrides.items():
            values[name] = value

        # 先把这个实例的容器建出来（空字典），再往里加属性——树里"新增"永远只往已存在的
        # 容器里加键（§7.2），所以实例本身也必须是一条明确的新增。
        api.emit(target, "add", "dict", value={}, label=label)
        for name in values:
            # 属性名可能含 / 或 ~，写进路径时要转义（§6.3）。
            api.emit(
                f"{target}/{escape_token(name)}",
                "add",
                data_type_of(values[name]),
                value=values[name],
                label=label,
            )
        api.log(f"已按模板 {template_id} 建立实例：{target}")

    return create_instance


def apply_system_defaults(
    systems: Mapping[str, Any],
    state: dict,
    *,
    functions: Mapping[str, Callable[..., Any]] | None = None,
) -> tuple[str, ...]:
    """把 system 条目里的初始值写进初始 State（新游戏初始化的一部分）。

    输入：
        systems: system 条目 id → CompiledSystem（按注册顺序遍历）；
        state: 正在搭建的初始 State（会被就地修改）；
        functions: 表达式可用到的外部函数表（Mod 的 functions）。
    输出：
        实际写进去的路径元组（按写入顺序；便于加载日志）。
    异常：
        TypeError: state 不是 dict；
        LogicError / PathNotFoundError / StateConflictError / StateShapeError:
            表达式算不出来、父容器不存在、路径穿过标量等（Mod 数据写错）。
    变量：
        reader: 以当前这棵树为读取入口（表达式可以引用先前写好的值）；
        written / system / path / value: 遍历与写入的中间结果。

    说明：
        - 这一步发生在**运行期建立之前**，属于"把初始 State 搭完"，不是游戏中的改动，
          所以直接写树、不产生变化量（和 Mod 的初始化钩子同一性质）；
        - 值已经存在时是替换（覆盖），不存在时新增键（父容器必须已存在）。
    """
    if not isinstance(state, dict):
        raise TypeError(f"apply_system_defaults 需要 dict 作为 State 根，实际是 {type(state).__name__}")
    reader = StateReader(state)
    written: list[str] = []
    for system in systems.values():
        for path, expression in system.values.items():
            # 深拷贝一次再写进 State（R5-4）：表达式可能是「引用型」取值，直接挂上去
            # 会让两条路径共享同一个对象（State 就不是树了）。
            value = snapshot_value(evaluate(expression, reader, functions=functions))
            if state_exists(state, path):
                replace(state, path, value)
            else:
                tokens = parse_path(path)
                add_key(state, format_path(tokens[:-1]), tokens[-1], value)
            written.append(path)
    return tuple(written)


def template_attributes(content: CompiledContent, template_id: str) -> dict:
    """取一个模板的属性表（给 Mod 的初始化钩子用）。

    输入：
        content: 编译后的内容；
        template_id: 模板条目 id。
    输出：
        属性名 → 值表达式 的表（**未求值**；钩子里可以用 evaluate 自己算）。
    异常：
        KeyError: 没有这个模板。
    变量：
        无。
    """
    template: CompiledTemplate = content.templates[template_id]
    return dict(template.attributes)


def phase_actions(content: CompiledContent, phase_id: str) -> tuple[str, ...]:
    """取一个阶段允许的动作 id（给 Mod 的脚本用）。

    输入：
        content: 编译后的内容；
        phase_id: phase 条目 id。
    输出：
        动作 id 元组。
    异常：
        KeyError: 没有这个阶段。
    变量：
        无。
    """
    return content.phases[phase_id].actions
