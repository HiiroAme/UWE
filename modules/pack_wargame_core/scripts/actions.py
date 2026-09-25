"""兵棋基础动作（模块服务）：移动与回合推进。

它们不认识任何具体的 State 形状——路径全部从 Mod 的参数来（平铺 params 会作为
最低优先级合并进 args，见 模块与Mod格式.md §4.2）。同一个服务因此既能服务
"扣行动力"的模组，也能服务"扣时间片""扣油耗"的模组，只改参数。

移动参数：
    units_path:     单位表路径（如 "/units"）
    unit_at_path:   单位位置的路径模板（如 "/units/{unit}/at"）
    unit_ap_path:   单位行动力的路径模板（如 "/units/{unit}/ap"）
    adjacency_path: 相邻关系的路径模板（如 "/adjacency/{node}"）
    move_cost:      一次移动扣多少行动力（默认 1）
    moved_event:    标在变化量上的事件 id（可选）
    log_path:       战报路径（可选）
回合参数：
    turn_path:      回合数路径（如 "/turn"）
    ap_restore:     每回合把行动力补到多少（默认 2）
    units_path / unit_ap_path / log_path: 同上
"""


def move_unit(api, args):
    """把单位挪到目标节点：相邻才准、目标格不能被占、移动扣行动力。

    输入：
        api: 服务手柄；
        args: {"unit": 单位键, "to": 目标节点键} + 上面那些路径参数。
    输出：
        无（改动经 api.emit 记账）。
    异常：
        KeyError: 缺 unit / to（Mod 数据写错，交给引擎记成服务失败）。
    变量：
        unit_key / to_node / current / units / ap: 中间结果。
    """
    log_path = args.get("log_path", "")
    unit_key = args["unit"]
    to_node = args["to"]
    at_path = args["unit_at_path"].format(unit=unit_key)
    ap_path = args["unit_ap_path"].format(unit=unit_key)
    current = api.get(at_path)
    label = args.get("moved_event", "") or ""

    if current == to_node:
        api.log_line(log_path, f"{unit_key} 已经在 {to_node}，忽略本次移动")
        return

    # 相邻检查：读 Mod 给的相邻表（哪条路径由参数决定）
    neighbors_path = args.get("adjacency_path", "")
    if neighbors_path:
        neighbors = api.get(neighbors_path.format(node=current))
        if to_node not in (neighbors or ()):
            api.log_line(log_path, f"{to_node} 与 {current} 不相邻，忽略本次移动")
            return

    units = api.get(args["units_path"])
    for other_key in sorted(units):
        if other_key != unit_key and units[other_key].get("at") == to_node:
            api.log_line(log_path, f"{to_node} 已被 {other_key} 占据，忽略本次移动")
            return

    api.emit(at_path, "modify", "string", value=to_node, old_value=current, label=label)
    ap = api.get(ap_path)
    cost = int(args.get("move_cost", 1) or 1)
    api.emit(ap_path, "modify", "number", value=ap - cost, old_value=ap, label=label)
    api.log_line(log_path, f"{unit_key} 从 {current} 移动到 {to_node}")


def advance_turn(api, args):
    """回合 +1，并把每个单位的行动力补到 ap_restore（只补不扣）。

    输入：
        api: 服务手柄；
        args: 路径参数。
    输出：
        无。
    异常：
        无（读不到的东西按"没有这个东西"处理）。
    变量：
        turn / units / unit_key / ap_path / current / restore: 中间结果。
    """
    log_path = args.get("log_path", "")
    turn_path = args.get("turn_path", "/turn")
    turn = api.get(turn_path)
    api.emit(turn_path, "modify", "number", value=turn + 1, old_value=turn)

    restore = int(args.get("ap_restore", 2) or 2)
    units = api.get(args["units_path"])
    for unit_key in sorted(units):
        ap_path = args["unit_ap_path"].format(unit=unit_key)
        current = api.get(ap_path)
        if current < restore:
            api.emit(ap_path, "modify", "number", value=restore, old_value=current)
    api.log_line(log_path, f"第 {turn + 1} 回合开始")
