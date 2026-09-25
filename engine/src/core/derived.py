"""派生值：由基础值完全决定的量（§6.5、D-12、D-33、D-39）。

位置：
    引擎核心逻辑层。它建在 core.logic（公式求值）与 core.content（公式从哪来）之上，
    被两处使用：
        1. **引擎自愈**：读档之后与存档之前，按层把全部派生值算一遍（D-33 / O-01）；
        2. **引擎原子服务** `engine:service:refresh_derived`：Mod 的触发链可以调用它，
           让基础值一变就重算（D-12 第 2 条），而且变化量跟着触发它的 Command 一起提交。

定义（照抄草案，避免歧义）：
    派生值 = 由基础值完全决定的量，即某个纯函数的取值，自身没有自由度。
    所以它不该被"算错"或"改坏"卡住：每次按公式重算一遍就自愈了。

为什么"按层"算：
    第一层的公式只引用定值（基础值），第二层可以引用第一层算出来的派生值……
    逐层推进保证"计算顺序不影响结果"，也让环形依赖能在加载期被指出来（见 compiler.py）。

写回通道：
    重算也要走变化量（§16）：本模块只负责"算出该改成什么"，真正写回由调用方
    通过给它一个 `apply_update` 回调完成——引擎自愈时会开一个临时状态并提交，
    原子服务则直接用 api.emit 把变化量记进当前 Command。

草案依据：
    §6.5 派生值（重算逻辑写在 Mod 里、链式触发、存读自愈、按层重算、环形报错）；
    D-12 / D-33 / D-39；§16 改动必须经引擎接口产生变化量；P9 纯逻辑与副作用分离。
"""

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .content import CompiledFormula
from .delta import MISSING, data_type_of, values_match
from .logic import Reader, evaluate
from .state.wildcard import is_pattern


def emit_update(api, update: "DerivedUpdate", *, label: str = "") -> None:
    """把一条派生值更新写成变化量（新增键或改值）。

    输入：
        api: 能 emit 的服务手柄（core.pipeline.ServiceApi）；
        update: 要写回的更新；
        label: 可选的 事件 id（Mod 想在重算后触发别的动作可以订阅它）。
    输出：
        无。
    异常：
        DeltaError / state 层异常: 路径或值不合法时上抛。
    变量：
        无。

    说明：
        引擎自愈与 Mod 的触发链都走这一个出口，保证两条路径写出来的变化量完全同形
        （P5 可替换原则：同地位对象的核心逻辑一致）。
    """
    if update.is_new:
        api.emit(update.path, "add", data_type_of(update.new_value), value=update.new_value, label=label)
        return
    api.emit(
        update.path,
        "modify",
        data_type_of(update.new_value),
        value=update.new_value,
        old_value=update.old_value,
        label=label,
    )


@dataclass(frozen=True)
class DerivedUpdate:
    """一条"派生值需要更新"的记录。

    字段：
        formula_id: 哪条公式；
        path: 写回路径；
        old_value: 更新前的值；路径原本不存在时是 MISSING；
        new_value: 按公式算出来的值；
        is_new: True 表示这条路径原本不存在（要新增键，而不是改值）。
    """

    formula_id: str
    path: str
    old_value: Any
    new_value: Any
    is_new: bool


def sort_formulas(formulas: Mapping[str, CompiledFormula]) -> tuple[CompiledFormula, ...]:
    """按"先算的排前面"给出公式顺序。

    输入：
        formulas: formula 条目 id → 公式。
    输出：
        排序后的公式元组：先按 layer 升序，同层按注册顺序（字典顺序稳定）。
    异常：
        无。
    变量：
        无。

    说明：
        Python 的 sorted 是稳定排序，所以同层公式保持注册顺序，
        同一份 Mod 在任何机器上算出的结果都一样（§13 确定性）。
    """
    return tuple(sorted(formulas.values(), key=lambda formula: formula.layer))


def refresh_derived(
    reader: Reader,
    formulas: Mapping[str, CompiledFormula],
    apply_update: Callable[[DerivedUpdate], None],
    *,
    functions: Mapping[str, Callable[..., Any]] | None = None,
) -> tuple[DerivedUpdate, ...]:
    """按层重算全部派生值，把需要更新的条目交给回调。

    输入：
        reader: 读取入口。**它必须能看到回调写进去的改动**：
            引擎自愈时传临时状态视图、原子服务时传服务手柄（api 本身就是 Reader）；
        formulas: 全部派生值公式；
        apply_update: 每发现一条需要更新就调用一次（由调用方决定怎么记账）；
        functions: 表达式可用到的外部函数表（随机、几何……）。
    输出：
        本次真正需要更新的条目（值没变的不会出现）。
    异常：
        LogicError / state 层异常: 公式求值失败或路径走不通（加载期本该查出来的问题）；
        DeltaError: 公式算出来的值不是纯数据（写进变化量会失败）。
    变量：
        ordered: 按层排好序的公式；
        updates: 需要更新的记录；
        formula: 当前计算的公式；
        old_value: 目标路径当前的值（不存在时是 MISSING）；
        new_value: 公式算出来的值；
        update: 组装出来的一条更新。

    说明：
        - 逐条"算 → 交出去 → 继续"：回调写进 reader 的改动会被后面（尤其是更高层）的公式看到，
          这正是"第二层用第一层结果"的实现方式；
        - target 写成**通配路径**时（F-05），先按通配枚举出全部具体路径，再逐条算
          （捕获到的名字作为这次求值的参数，表达式里用 ["arg", 名字] 取）；
        - 通配只匹配**已经存在**的路径：新实例的初始值由模板给（core/templates.py），
          引擎不会凭空空造出条目。
    """
    ordered = sort_formulas(formulas)
    updates: list[DerivedUpdate] = []
    for formula in ordered:
        if is_pattern(formula.target):
            for found in reader.expand(formula.target):
                update = _one_update(formula, found.path, reader, functions, args=found.bindings)
                if update is not None:
                    updates.append(update)
                    apply_update(update)
            continue
        update = _one_update(formula, formula.target, reader, functions, args=None)
        if update is not None:
            updates.append(update)
            apply_update(update)
    return tuple(updates)


def _one_update(
    formula: CompiledFormula,
    path: str,
    reader: Reader,
    functions,
    *,
    args,
):
    """算一条具体路径的派生值；已经正确时返回 None。

    输入：
        formula: 公式；
        path: 这次要算的具体路径（通配已经展开过）；
        reader: 读取入口；
        functions: 外部函数表；
        args: 这次求值的参数（通配捕获到的名字；普通公式是 None）。
    输出：
        DerivedUpdate；值已经正确时是 None。
    异常：
        LogicError / state 层异常: 公式求值或路径出问题（与调用方同口径）。
    变量：
        old_value: 目标路径当前的值（不存在时是 MISSING）；
        new_value: 算出来的值。
    """
    old_value = reader.get(path) if reader.exists(path) else MISSING
    new_value = evaluate(formula.expression, reader, args=args, functions=functions)
    if old_value is not MISSING and values_match(old_value, new_value):
        return None
    return DerivedUpdate(
        formula_id=formula.formula_id,
        path=path,
        old_value=old_value,
        new_value=new_value,
        is_new=old_value is MISSING,
    )


def make_refresh_service(formulas: Mapping[str, CompiledFormula]):
    """造出引擎原子服务 `engine:service:refresh_derived` 的实现。

    输入：
        formulas: 本 Mod 的全部派生值公式（来自编译后的内容）。
    输出：
        服务函数 fn(api, args) -> None（契约见 core.ports.services）。
    异常：
        无（函数在被调用时才可能抛错）。
    变量：
        无。

    说明：
        服务函数拿到的是 ServiceApi：它本身就有 get / exists（满足 Reader），
        也有 emit（产出变化量）。所以"重算派生值"这件事在 Mod 的触发链里
        就是一次普通的服务调用，产出物也照常进同一个 CommandDelta 一起提交。
    """

    def refresh_service(api, args):
        """按层重算派生值，把每一条更新记成变化量。

        输入：
            api: 服务手柄（同时充当读取入口）；
            args: 本服务不需要参数（Mod 写 {"service": "engine:service:refresh_derived"} 即可）。
        输出：
            无。
        异常：
            LogicError / state 层异常 / DeltaError: 公式或路径有问题时上抛（由引擎记成服务失败）。
        变量：
            label: 写回时标的事件 id（Mod 想在事后触发别的动作可以订阅它）。
        """
        label = ""
        if isinstance(args, dict) and isinstance(args.get("label"), str):
            label = args["label"]
        refresh_derived(api, formulas, lambda update: emit_update(api, update, label=label))

    return refresh_service
