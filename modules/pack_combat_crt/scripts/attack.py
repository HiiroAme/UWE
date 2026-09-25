"""CRT 战斗（模块实现）：结算一场 + 声明列表的推进 + 战后挺进。

四个服务（都在 data/10_services.json 里登记）：
    attack       ：结算**一场**（掷骰 → 查表 → 按结果改数据）；
    declare      ：把一场放进声明列表（参战资格在这里把关，不在界面里）；
    resolve_next ：结算声明列表里的第一场，然后把它出列（打不成也出列 = 不重试）；
    advance      ：战后挺进（一个参战单位走进刚空出来的那一格，不能超堆叠）。

结算本身只有一份实现：resolve_next 直接调本文件的 attack，不另写一遍。

参数（Mod 通过 params / 调用点给）：
    units:            参战单位键列表（**调用点给**）
    target:           目标单位键（**调用点给**）
    attack_path:      参战单位"攻击力"的路径模板（默认 "/units/{unit}/attack"）
    defense_path:     目标"抵抗值基数"的路径模板（默认 "/units/{target}/attack"）
    defense_mult_path: 目标所在地形的防守倍率路径模板（可选，如 "/nodes/{node}/defense_mult"）
    node_path:        目标所在格的路径模板（默认 "/units/{target}/at"）
    log_path:         战报路径（可选）
    report_path:      战报数据路径（可选）：结算完往这里写一条
                      {"code":结果码, "units":参战单位, "target":目标, "vacated":空出来的格子}
                      ——"战后挺进"就是靠它知道该往哪进。
    adjacency_path:   相邻关系路径（可选，如 "/adjacency"）：给了就做**结算前复查**——
                      目标还在、每个参战单位都还活着且与目标相邻；不成立就整场跳过。
    骰子走 api.rand_int（引擎随机，可回放）；撤退走注入的随机函数（同样可回放）。
"""


def attack(api, args):
    """结算一次战斗：算档位 → 掷骰 → 查表 → 消灭 / 撤退 / 白打。

    输入：
        api: 服务手柄；
        args: 见模块文档。
    输出：
        无（改动经 api.emit 记账）。
    异常：
        KeyError: 缺 target（Mod 数据写错）。
    变量：
        total / resistance / roll / code / effects: 中间结果。
    """
    log_path = args.get("log_path", "")
    participants = list(args.get("units") or ())
    target = args["target"]
    units = api.get(args.get("units_path", "/units"))
    name_field = args.get("name_field", "name")
    if not participants or target not in units:
        return
    skip_reason = _stale_battle(api, args, participants, target)
    if skip_reason:
        api.log_line(log_path, f"这一场因战场变化跳过：{skip_reason}")
        return
    target_cell = units[target].get("at", "")

    defense_path = args.get("defense_path", "/units/{target}/attack")
    attack_path = args.get("attack_path", "/units/{unit}/attack")
    total = sum(float(api.get(attack_path.format(unit=key))) for key in participants)
    resistance = float(api.get(defense_path.format(target=target)))

    mult_path = args.get("defense_mult_path", "")
    if mult_path:
        node = api.get(args.get("node_path", "/units/{target}/at").format(target=target))
        resolved = mult_path.format(node=node)
        if api.exists(resolved):                     # 地形倍率缺省按 ×1 算
            resistance *= float(api.get(resolved) or 1.0)

    roll = api.rand_int(1, 6)
    code = api.call("pack_combat_crt.crt.basic", total, resistance, roll)
    attacker_names = "、".join(_name(units.get(key), key, name_field) for key in participants)
    target_name = _name(units.get(target), target, name_field)
    if not code:
        api.log_line(
            log_path,
            f"{attacker_names} → {target_name}：攻防比不足 1:3"
            f"（{total:.0f} : {resistance:.0f}），这一战打不起来",
        )
        return

    effects = api.call("pack_combat_crt.crt.effects", code)
    api.log_line(
        log_path,
        f"CRT：{attacker_names} → {target_name}（攻 {total:.0f} / 守 {resistance:.0f}），"
        f"骰 {roll} → {code}",
    )

    if effects["eliminated"] == "defender":
        _eliminate(api, target, units[target], args)
    elif effects["eliminated"] == "attacker":
        for key in participants:
            if key in units:
                _eliminate(api, key, units[key], args)
    elif effects["retreat"]:
        mover = target if effects["side"] == "defender" else _lead(participants, units)
        if mover:
            _retreat(api, mover, units.get(mover, {}), units, target, effects["retreat"], args)

    report_path = args.get("report_path", "")
    if report_path:
        # "目标原来站的那一格"现在是不是空出来了（退走或被消灭都算）→ 战后挺进用。
        # 要**重新读**当前位置：函数开头那份 units 是命令刚开始时的快照，
        # 后面的消灭 / 撤退改不到它（照旧写法永远算不出"空出来的格子"，挺进也就打不开）。
        still_there = api.get(args.get("node_path", "/units/{target}/at").format(target=target))
        vacated = "" if still_there == target_cell else target_cell
        api.emit(report_path, "modify", "dict", value={
            "code": code, "units": participants, "target": target, "vacated": vacated,
        }, old_value=api.get(report_path) if api.exists(report_path) else {})


def declare(api, args):
    """把一场战斗放进声明列表（同一个参战单位本回合只能声明一次）。

    为什么要"先声明、后逐场结算"（设计文档 §六 / §九）：玩家先把要打的仗都讲清楚，
    结算按声明顺序一场一场来；结算中不能再加、也不能再取消。
    参战资格在这里把关（不是"只在界面里挡"）：参战单位必须是当前控制方**还活着、
    与目标相邻**的单位，目标必须是**还活着的敌军**。

    输入：
        api: 服务句柄；
        args: units（参战单位键列表）与 target（目标单位键）由调用点给；
              路径与字段名参数（都有缺省）：battles_path / units_path /
              control_side_path / adjacency_path / unit_field_path /
              side_field / at_field / destroyed_field / fought_field /
              disordered_field / log_path。
    输出：
        无（改动经 api.emit 记账）。
    异常：
        KeyError: 缺 units / target（Mod 数据写错，交给引擎记成服务失败）。
    变量：
        participants / target / units / battles / entry: 中间结果。
    """
    log_path = args.get("log_path", "")
    participants = list(args["units"])
    target = args["target"]
    units_path = args.get("units_path", "/units")
    battles_path = args.get("battles_path", "/battles")
    control_path = args.get("control_side_path", "/control_side")
    adjacency_path = args.get("adjacency_path", "/adjacency")
    field_path = args.get("unit_field_path", "/units/{unit}/{field}")
    side_field = args.get("side_field", "side")
    at_field = args.get("at_field", "at")
    destroyed_field = args.get("destroyed_field", "destroyed")
    fought_field = args.get("fought_field", "fought")
    disordered_field = args.get("disordered_field", "disordered")
    name_field = args.get("name_field", "name")

    units = api.get(units_path)
    participant_names = "、".join(_name(units.get(key), key, name_field) for key in participants)
    for key in participants:                       # 已经打过仗的不能再声明
        if (units.get(key) or {}).get(fought_field):
            api.log_line(log_path, f"{_name(units.get(key), key, name_field)} 本回合已经打过仗了，这场声明不算")
            return
        if (units.get(key) or {}).get(disordered_field):   # 混乱单位不能参战（M-6）
            api.log_line(log_path, f"{_name(units.get(key), key, name_field)} 还在混乱中，不能参战")
            return

    control = api.get(control_path)
    target_unit = units.get(target) or {}
    target_at = target_unit.get(at_field, "")
    target_name = _name(target_unit, target, name_field)
    if target_unit.get(destroyed_field) or not target_at:
        api.log_line(log_path, f"{target_name} 已经不在战场，这场声明不算")
        return
    if target_unit.get(side_field) == control:
        api.log_line(log_path, f"{target_name} 是自己人，不能打")
        return
    adjacency = api.get(adjacency_path)
    for key in participants:
        unit = units.get(key) or {}
        if (unit.get(side_field) != control or unit.get(destroyed_field)
                or not unit.get(at_field)):
            api.log_line(log_path, f"{_name(unit, key, name_field)} 不是当前控制方还活着的单位，这场声明不算")
            return
        if target_at not in adjacency.get(unit[at_field], []):
            api.log_line(log_path, f"{_name(unit, key, name_field)} 与 {target_name} 不相邻，这场声明不算")
            return

    battles = list(api.get(battles_path) or ())
    entry = {"units": participants, "target": target, "order": len(battles)}
    api.emit(battles_path, "modify", "list", value=list(battles) + [entry],
             old_value=battles, label="")
    for key in participants:                       # 声明即消耗：标成"打过仗"
        api.emit(field_path.format(unit=key, field=fought_field), "modify", "bool",
                 value=True, old_value=False)
    api.log_line(log_path, f"声明一场：{participant_names} → {target_name}")


def resolve_next(api, args):
    """结算声明列表里的第一场，然后把它出列。

    结算本身走同一个 `attack`（CRT 逻辑只有一份）：先看这一场还打不打得成
    （目标还在、参战单位还与它相邻），打不成就只记一条战报；不管打没打成，这一场都出列
    （跳过 = 不再重试）。

    输入：
        api: 服务句柄；args: 路径与字段名参数（同 declare；另外 attack 用的
        defense_path / defense_mult_path / node_path / report_path / fords_path 也照收）。
    输出：
        无（改动经 api.emit 记账）。
    异常：
        无（列表空、目标消失都只是"什么都不做 / 记一条战报"）。
    变量：
        battles / first / participants / target / units: 中间结果。
    """
    log_path = args.get("log_path", "")
    battles_path = args.get("battles_path", "/battles")
    units_path = args.get("units_path", "/units")
    battles = list(api.get(battles_path) or ())
    if not battles:
        return
    first = battles[0] or {}
    participants = list(first.get("units") or ())
    target = first.get("target", "")
    units = api.get(units_path)
    target_unit = units.get(target) or {}
    name_field = args.get("name_field", "name")
    target_gone = (not target or target not in units
                   or target_unit.get(args.get("destroyed_field", "destroyed"))
                   or not target_unit.get(args.get("at_field", "at")))
    if target_gone:
        # 目标已经不在战场：这一场连掷骰都不掷（与旧的两分支写法同一条路）。
        api.log_line(
            log_path,
            f"这一场打不成了：{_name(target_unit, target or '（没有目标）', name_field)}"
            " 已经不在战场，跳过",
        )
    elif not participants:
        api.log_line(log_path, "这一场没有参战单位，跳过")
    else:
        attack(api, {**args, "units": participants, "target": target})
    api.emit(battles_path, "modify", "list", value=battles[1:], old_value=battles, label="")


def advance(api, args):
    """战后挺进：一个参战单位走进刚才空出来的那一格（不能超堆叠）。

    往哪进不自己算——上一场结算写在战报数据里的 `vacated` 就是"目标原来站的那一格，
    现在空出来了"。没空、没参加那一场、那一格已经有人，都只记一条日志就作罢。

    输入：
        api: 服务句柄；args: {"unit": 参战单位键} + 路径与字段名参数
        （report_path / units_path / unit_field_path / at_field / destroyed_field /
        name_field / log_path）。
    输出：
        无（改动经 api.emit 记账）。
    异常：
        KeyError: 缺 unit（Mod 数据写错）。
    变量：
        report / cell / units / unit / old: 中间结果。
    """
    log_path = args.get("log_path", "")
    unit_key = args["unit"]
    report_path = args.get("report_path", "/battle_report")
    units_path = args.get("units_path", "/units")
    field_path = args.get("unit_field_path", "/units/{unit}/{field}")
    at_field = args.get("at_field", "at")
    destroyed_field = args.get("destroyed_field", "destroyed")
    name_field = args.get("name_field", "name")

    report = api.get(report_path) or {}
    cell = report.get("vacated", "")
    if not cell:
        return
    if unit_key not in (report.get("units") or ()):
        api.log_line(log_path, f"{unit_key} 没参加这一场，不能挺进")
        return
    units = api.get(units_path)
    unit = units.get(unit_key) or {}
    if unit.get(destroyed_field) or not unit.get(at_field):
        return
    for other_key, other in units.items():
        if (other_key != unit_key and not other.get(destroyed_field)
                and other.get(at_field) == cell):
            api.log_line(log_path, f"{cell} 已经有人了，不能挺进（不能超堆叠）")
            return
    old = unit[at_field]
    api.emit(field_path.format(unit=unit_key, field=at_field), "modify", "string",
             value=cell, old_value=old)
    api.emit(report_path, "modify", "dict",
             value={"code": report.get("code", ""), "units": list(report.get("units") or ()),
                    "target": report.get("target", ""), "vacated": ""},
             old_value=report)
    api.log_line(log_path, f"{unit.get(name_field, unit_key)} 战后挺进到 {cell}")


def _lead(participants, units):
    """参战单位里的"主攻单位"：攻击力最高、一样高时取 id 最前（确定性）。"""
    ordered = sorted(participants, key=lambda key: (-float(units[key]["attack"]), key))
    return ordered[0] if ordered else ""


def _stale_battle(api, args, participants, target):
    """结算前复查：这一场还打不打得成？打不成返回原因文字，打得成返回空串。

    只做"跟距离有关"的复查（目标还在、参战单位还在且相邻）——**这是可选的**：
    调用方不给 `adjacency_path` 就不查（有些玩法允许远程/区域火力）。
    """
    adjacency_path = args.get("adjacency_path", "")
    if not adjacency_path:
        return ""
    units = api.get(args.get("units_path", "/units"))
    name_field = args.get("name_field", "name")
    target_unit = units.get(target) or {}
    target_at = target_unit.get("at", "")
    if target_unit.get("destroyed") or not target_at:
        return f"{_name(target_unit, target, name_field)} 已经不在战场"
    adjacency = api.get(adjacency_path)
    for key in participants:
        unit = units.get(key) or {}
        if unit.get("destroyed") or not unit.get("at"):
            return f"{_name(unit, key, name_field)} 已经不在战场"
        if target_at not in adjacency.get(unit["at"], []):
            return f"{_name(unit, key, name_field)} 与 {_name(target_unit, target, name_field)} 不再相邻"
    return ""


def _name(unit, key, name_field="name"):
    """战报里显示单位名字；没有名字就退回单位键（模块不规定 State 结构）。"""
    return str((unit or {}).get(name_field, key))


def _eliminate(api, unit_key, unit, args):
    """把一个单位打出局：撤出地图 + 标记 destroyed。"""
    log_path = args.get("log_path", "")
    field_path = args.get("unit_field_path", "/units/{unit}/{field}")
    at = unit.get("at", "")
    if at:
        api.emit(field_path.format(unit=unit_key, field="at"), "modify", "string",
                 value="", old_value=at)
    # 先读后写：已经消灭过的就别再写（写死 old_value=False 会在"重复消灭"时硬失败）。
    if not api.get(field_path.format(unit=unit_key, field="destroyed")):
        api.emit(field_path.format(unit=unit_key, field="destroyed"), "modify", "bool",
                 value=True, old_value=False)
    api.log_line(log_path, f"{unit.get('name', unit_key)} 被消灭")


def _retreat(api, unit_key, unit, units, battle_owner, steps, args):
    """按"每步更远、方向随机、不进敌方控制区、不用渡口"撤退；退不了就消灭。"""
    log_path = args.get("log_path", "")
    adjacency = api.get(args.get("adjacency_path", "/adjacency"))
    nodes = api.get(args.get("nodes_path", "/nodes"))
    start = unit.get("at", "")
    battle = units.get(battle_owner, {}).get("at", start)

    side = unit.get("side", "")
    disordered = [key for key, value in units.items() if value.get("disordered")]
    # 敌方控制区 = 控制区表里"不是我方"的那些格（不写死任何阵营名）。
    zoc_table = api.call("pack_path_hex.zoc.from_units", units, adjacency, disordered)
    enemy_zoc = set()
    for zoc_side, cells in zoc_table.items():
        if zoc_side != side:
            enemy_zoc.update(cells)
    occupant_side = {}
    for key, value in units.items():
        if key != unit_key and not value.get("destroyed") and value.get("at"):
            occupant_side[value["at"]] = value.get("side")
    # 撤退不能进渡口（R-4）：渡口格由调用方给的 /fords 数据标出来。
    ford_cells = set()
    fords_path = args.get("fords_path", "")
    if fords_path and api.exists(fords_path):
        for link in (api.get(fords_path) or ()):
            ford_cells.add(link.get("from", ""))
            ford_cells.add(link.get("to", ""))
    # 能穿 ≠ 能停（R-3）：友军占的格可以穿过去，但不能当终点；敌军占的格两边都不行。
    can_enter, can_stop = set(), set()
    for cell, node in nodes.items():
        if not node.get("passable", True) or cell in enemy_zoc or cell in ford_cells:
            continue
        other_side = occupant_side.get(cell)
        if other_side is not None and other_side != side:
            continue
        can_enter.add(cell)
        if other_side is None:
            can_stop.add(cell)

    path = api.call("pack_path_hex.retreat.random",
                    start=start, battle=battle, steps=int(steps), neighbors=adjacency,
                    can_enter=can_enter, can_stop=can_stop, rand_int=api.rand_int)
    if not path:
        api.log_line(log_path, f"{unit.get('name', unit_key)} 退不了，被消灭")
        _eliminate(api, unit_key, unit, args)
        return

    field_path = args.get("unit_field_path", "/units/{unit}/{field}")
    api.emit(field_path.format(unit=unit_key, field="at"), "modify", "string",
             value=path[-1], old_value=start)
    # 先读后写：本来就混乱的（被再次逼退）就不要再写一次。
    if not api.get(field_path.format(unit=unit_key, field="disordered")):
        api.emit(field_path.format(unit=unit_key, field="disordered"), "modify", "bool",
                 value=True, old_value=False)
    api.log_line(log_path, f"{unit.get('name', unit_key)} 撤退到 {path[-1]}，陷入混乱")
