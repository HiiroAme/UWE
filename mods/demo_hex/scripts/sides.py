"""控制权（哪一方在行动）的服务（Mod 侧脚本）。

这是**这一局的玩法**，不是引擎机制：引擎只认 /control_side 这个普通字符串，
谁在什么时候拿走控制权，全写在这里与动作数据里（红方行动完 → 结束回合 → 蓝方）。

参数（Mod 通过 params / 调用点参数给）：
    log_path:  战报列表路径（可选，如 "/log"）
    red_name / blue_name: 显示名（可选，默认 "红方" / "蓝方"）
"""

RED = "red"
BLUE = "blue"


def switch_control(api, args):
    """把控制权交给另一方，并清掉当前选中（避免选着刚交出去那一方的单位）。

    输入：
        api: 服务手柄；
        args: 可选参数（见模块文档）。
    输出：
        无（改动经 api.emit 记账）。
    异常：
        KeyError: /control_side 不存在（Mod 自己的 State 问题）。
    变量：
        current / other: 换边前后的控制方；
        text: 写进战报的一行。
    """
    current = api.get("/control_side")
    other = BLUE if current == RED else RED
    api.emit("/control_side", "modify", "string", value=other, old_value=current)
    api.context.put("demo_hex.selected", "")

    log_path = args.get("log_path", "")
    if log_path:
        text = f"轮到{_display_name(args, other)}行动"
        log = api.get(log_path)
        api.emit(log_path, "modify", "list", value=list(log) + [text], old_value=log)


def _display_name(args, side):
    """取一方的显示名（Mod 可以用参数改叫法）。"""
    if side == RED:
        return str(args.get("red_name", "") or "红方")
    return str(args.get("blue_name", "") or "蓝方")
