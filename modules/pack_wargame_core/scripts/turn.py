"""回合结构（模块服务）：一方回合开始时，要刷新的那几样东西。

只认参数、不认玩法——路径与字段名全部由调用方给：
    side:               这一方开始回合（必填，其他参数都有缺省）；
    units_path:         单位表路径（缺省 "/units"）；
    adjacency_path:     相邻关系路径（缺省 "/adjacency"）；
    unit_field_path:    单位字段的路径模板（缺省 "/units/{unit}/{field}"）；
    side_field / at_field / destroyed_field / disordered_field: 字段名（缺省是一般写法）；
    move_field / move_left_field: 行动力的**属性值**与**即时值**（回合开始把即时值补回属性值）；
    zoc_field / fought_field: "回合初处在敌方控制区"与"本回合已打过"两个标记的字段名。

控制区不自己算：调 `pack_path_hex` 的 zoc 纯函数（见 pack_info.json 的 requires）——
模块之间只认能力名，不互相抄一份实现。
"""


def begin_side(api, args):
    """把某一方"回合开始"该刷新的三样东西刷一遍。

    刷的三样（对应设计文档 §六 / G-14、§10.1 M-5）：
        move_left：即时值回到属性值（上回合花掉的行动力恢复）；
        zoc_field：现在有没有站在敌方控制区里（ZOC-2 的判定依据）；
        fought：清零（每个单位每一方的回合最多打一场）。

    只碰这一方的单位，而且是"活着、还在图上"的那一批——被消灭的、没上场的都不动。
    敌方控制区取"除本方以外**所有**阵营的控制区并集"：两方局就是对面那一方，
    多方局也不用改这一份实现。

    输入：
        api: 服务句柄；args: 上面那些路径与字段参数（side 必填）。
    输出：
        无（改动经 api.emit 记账）。
    异常：
        KeyError: 缺 side（Mod 数据写错，交给引擎记成服务失败）。
    变量：
        units / disordered / table / enemy_cells / unit / in_zoc: 中间结果。
    """
    side = args["side"]
    units_path = args.get("units_path", "/units")
    adjacency_path = args.get("adjacency_path", "/adjacency")
    field_path = args.get("unit_field_path", "/units/{unit}/{field}")
    side_field = args.get("side_field", "side")
    at_field = args.get("at_field", "at")
    destroyed_field = args.get("destroyed_field", "destroyed")
    disordered_field = args.get("disordered_field", "disordered")
    move_field = args.get("move_field", "move")
    move_left_field = args.get("move_left_field", "move_left")
    zoc_field = args.get("zoc_field", "zoc_start")
    fought_field = args.get("fought_field", "fought")

    units = api.get(units_path)
    # 混乱的单位不产生控制区（与移动、撤退用的是同一条规则）。
    disordered = [key for key, unit in units.items() if unit.get(disordered_field)]
    table = api.call("pack_path_hex.zoc.from_units", units, api.get(adjacency_path), disordered)
    enemy_cells = set()
    for other_side, cells in table.items():
        if other_side != side:
            enemy_cells.update(cells)

    for unit_key in sorted(units):
        unit = units[unit_key]
        if unit.get(side_field) != side or unit.get(destroyed_field) or not unit.get(at_field):
            continue
        # 行动力：即时值回到属性值（"回合开始恢复"这件事只有这一处实现）。
        if unit.get(move_left_field) != unit.get(move_field):
            api.emit(field_path.format(unit=unit_key, field=move_left_field), "modify", "number",
                     value=unit.get(move_field, 0), old_value=unit.get(move_left_field, 0))
        in_zoc = unit[at_field] in enemy_cells
        if bool(unit.get(zoc_field)) != in_zoc:
            api.emit(field_path.format(unit=unit_key, field=zoc_field), "modify", "bool",
                     value=in_zoc, old_value=bool(unit.get(zoc_field)))
        if unit.get(fought_field):
            api.emit(field_path.format(unit=unit_key, field=fought_field), "modify", "bool",
                     value=False, old_value=True)


def check_victory(api, args):
    """回合末判定：占胜利点 / 一方全灭 / 到回合上限。

    判定顺序**固定**（同时成立时取先判到的那个，Mod 作者不用猜）：
        ① 攻方站住胜利点  → 攻方胜；
        ② 守方一个不剩    → 攻方胜；
        ③ 攻方一个不剩    → 守方胜；
        ④ 回合数超过上限  → 守方胜。
    这里不判断"什么时候该判"——"大回合末才判"是这一局的回合结构，
    由 Mod 的命令分支决定（见 mods/demo_river/data/52_commands_turn.json）。

    输入：
        api: 服务句柄；args:
        attacker_side / defender_side: 攻方与守方（必填）；
        victory_field: 节点里"这是胜利点"的字段名（缺省 "victory"）；
        turn_limit: 回合上限（缺省 12）；
        nodes_path / units_path / turn_path / game_over_path / winner_path: 路径（有缺省）；
        side_field / at_field / destroyed_field: 字段名（有缺省）；
        win_point_message / win_defender_wiped_message / win_attacker_wiped_message /
        win_timeout_message: 四条战报文字（有缺省；win_timeout_message 里的 {limit} 会换成回合上限）；
        log_path: 战报路径（可选，没给就只写引擎日志）。
    输出：
        无（赢了才写 game_over_path 与 winner_path）。
    异常：
        KeyError: 缺 attacker_side / defender_side（Mod 数据写错，交给引擎记成服务失败）。
    变量：
        nodes / units / victory_node / on_point / alive_attacker / alive_defender / winner: 中间结果。
    """
    log_path = args.get("log_path", "")
    attacker = args["attacker_side"]
    defender = args["defender_side"]
    victory_field = args.get("victory_field", "victory")
    turn_limit = int(args.get("turn_limit", 12))
    nodes_path = args.get("nodes_path", "/nodes")
    units_path = args.get("units_path", "/units")
    turn_path = args.get("turn_path", "/turn")
    game_over_path = args.get("game_over_path", "/game_over")
    winner_path = args.get("winner_path", "/winner")
    side_field = args.get("side_field", "side")
    at_field = args.get("at_field", "at")
    destroyed_field = args.get("destroyed_field", "destroyed")

    if api.get(game_over_path):
        return
    nodes = api.get(nodes_path)
    units = api.get(units_path)
    victory_node = next((key for key, node in nodes.items() if node.get(victory_field)), "")

    def alive_on(side):
        """这一方还活着、还在图上的单位（按单位键排序，保证同样的局面给同样的结果）。"""
        return [key for key in sorted(units)
                if units[key].get(side_field) == side
                and not units[key].get(destroyed_field) and units[key].get(at_field)]

    on_point = [key for key in alive_on(attacker) if units[key].get(at_field) == victory_node]
    alive_defender = alive_on(defender)
    alive_attacker = alive_on(attacker)

    winner, message = "", ""
    if on_point:
        winner = attacker
        message = args.get("win_point_message", "攻方站住了胜利点")
    elif not alive_defender:
        winner = attacker
        message = args.get("win_defender_wiped_message", "守方已被全歼")
    elif not alive_attacker:
        winner = defender
        message = args.get("win_attacker_wiped_message", "攻方已被全歼")
    elif api.get(turn_path) > turn_limit:
        winner = defender
        message = str(args.get("win_timeout_message", "第 {limit} 回合已过")).format(limit=turn_limit)

    if winner:
        api.log_line(log_path, message)
        api.emit(game_over_path, "modify", "bool", value=True, old_value=False)
        api.emit(winner_path, "modify", "string", value=winner, old_value=api.get(winner_path))
