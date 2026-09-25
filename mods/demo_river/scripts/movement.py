"""移动服务（Mod 侧）：沿模块算出来的路径走，按地形扣移动力。

规则一条都不在这里：地形消耗、能不能穿、能不能停、渡口、控制区全在模块
`pack_path_hex` 里；本脚本只做"把 State 翻成参数 → 调模块 → 把结果写回 State"。
"""


def move(api, args):
    """把一个单位移动到目标格（走不到就不动）。

    输入：
        api: 服务手柄；
        args: {"unit": 单位键, "to": 目标格}（可带 moved_event 作为变化量标签）。
    输出：
        无（改动经 api.emit 记账）。
    异常：
        KeyError: 单位不存在（Mod 数据写错）。
    变量：
        unit / start / levels / zoc_table / result / spent / path: 中间结果。
    """
    log_path = args.get("log_path", "")
    unit_key = args["unit"]
    to_cell = args["to"]
    units = api.get("/units")
    unit = units[unit_key]
    if unit.get("destroyed"):
        return
    start = unit["at"]
    if to_cell == start:
        return

    nodes = api.get("/nodes")
    adjacency = api.get("/adjacency")
    fords = list(api.get("/fords") or [])
    # 通行等级（地形消耗 / 能不能穿 / 能不能停）由模块算——视图那边画"能走到哪"用的是同一个函数。
    enter_cost, can_pass, can_stop = api.call(
        "pack_path_hex.levels.from_state", nodes, units, unit_key)

    # 敌方控制区：混乱的单位不产生控制区（zoc_cells 的 skip 参数）。
    disordered = [key for key, value in units.items() if value.get("disordered")]
    zoc_table = api.call("pack_path_hex.zoc.from_units", units, adjacency, disordered)
    enemy_side = "south" if unit.get("side") == "north" else "north"
    enemy_zoc = zoc_table.get(enemy_side, [])

    result = api.call(
        "pack_path_hex.reachable.dijkstra",
        start=start, budget=unit["move_left"], neighbors=adjacency,
        enter_cost=enter_cost, can_pass=can_pass, can_stop=can_stop,
        extra_moves=fords, zoc=enemy_zoc, stop_on_enter_zoc=True,
        must_leave_zoc=bool(unit.get("zoc_start")),
    )
    if to_cell not in result["costs"]:
        api.log(f"{unit_key} 走不到 {to_cell}：规则或移动力不允许")
        return

    spent = result["costs"][to_cell]
    path = api.call("pack_path_hex.path.previous", result["previous"], start, to_cell)
    label = args.get("moved_event", "") or ""
    api.emit(f"/units/{unit_key}/at", "modify", "string", value=to_cell, old_value=start,
             label=label)

    # 行动力：进入敌方控制区（ZOC-1）归零；移出控制区按地形正常扣点，剩余保留（ZOC-2）。
    remaining = unit["move_left"] - spent
    if to_cell in enemy_zoc:
        remaining = 0
    api.emit(f"/units/{unit_key}/move_left", "modify", "number",
             value=remaining, old_value=unit["move_left"], label=label)
    api.context.put("demo_river.pending", "")     # 走完清掉"待确认目标"，选中留着
    api.log_line(log_path, f"{unit.get('name', unit_key)} 从 {start} 走到 {to_cell}"
                    f"（{len(path) - 1} 步，花 {spent:.0f} 点）")
