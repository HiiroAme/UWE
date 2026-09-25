"""通用攻击骨架（模块服务）：校验交给规则，这里负责"扣量 + 记战报 + 抛事件"。

它不认识任何具体的 State 形状：扣哪条路径、行动力在哪、战报写哪儿，全从 Mod 的参数来
（平铺 params 会作为最低优先级合并进 args，见 模块与Mod格式.md §4.2）。

参数（Mod 通过 params / 绑定 params / 调用点参数给）：
    damage_target_path: 扣谁的哪条量，形如 "/units/{target}/men"（或 /hp）——**必填**
    damage_binding:     用哪个绑定来算伤害（默认 "damage"）
    damage_dice:        骰子表达式（如 "2d6"）；给了就用引擎随机掷，不给就按 0 算
    damage_base:        固定基础伤害（与骰子相加）
    unit_ap_path:       攻击者行动力路径（可选，如 "/units/{unit}/ap"）
    ap_cost:            攻击消耗的行动力（可选，默认 1）
    log_path:           战报列表路径（可选，如 "/log"）
    damaged_event:      标在变化量上的事件 id（可选，供触发链订阅）
"""


def attack_standard(api, args):
    """一次攻击：算伤害 → 扣目标量 → 扣行动力 → 记战报 → 抛事件。

    输入：
        api: 服务手柄（读状态、产变化量、取随机、调用函数表）；
        args: 见模块文档的参数表（attacker / target 由调用点给）。
    输出：
        无（改动经 api.emit 记账）。
    异常：
        KeyError / ValueError: 参数缺失或写法不对（引擎记成服务失败）。
    变量：
        attacker / target / rolled / amount / path / old / new: 中间结果。
    """
    log_path = args.get("log_path", "")
    attacker = args["attacker"]
    target = args["target"]
    label = args.get("damaged_event", "") or ""

    rolled = _roll(api, args.get("damage_dice", "")) + int(args.get("damage_base", 0) or 0)
    binding = args.get("damage_binding", "damage")
    # 修正值：只有 Mod 明确给了 damage_modifier 才覆盖——否则留给绑定自己的 params
    # （绑定里写了 modifier，就不该被这里的默认值盖掉；D-2 的优先级是"调用点 > 绑定"，
    #  所以"没给"就不许传，传了就等于调用点覆盖）。
    modifier = args.get("damage_modifier", None)
    if modifier is None:
        amount = api.call(binding, rolled)
    else:
        amount = api.call(binding, rolled, modifier=int(modifier))

    path = args["damage_target_path"].format(target=target)
    old = api.get(path)
    new = old - amount
    if new < 0:
        new = 0
    api.emit(path, "modify", "number", value=new, old_value=old, label=label)

    # 目标量归零时，可选地标一个标记位 + 事件（例如 /units/{target}/destroyed）。
    # 具体是什么含义由 Mod 决定：这只做"量为 0 就置标记"这一件机械的事。
    flag_path = args.get("zero_flag_path", "")
    if new <= 0 and flag_path:
        flag_path = flag_path.format(target=target)
        old_flag = api.get(flag_path) if api.exists(flag_path) else False
        if old_flag is not True:
            api.emit(flag_path, "modify", "bool", value=True, old_value=old_flag,
                     label=args.get("zero_event", "") or "")

    ap_path = args.get("unit_ap_path", "")
    if ap_path:
        ap_path = ap_path.format(unit=attacker)
        old_ap = api.get(ap_path)
        api.emit(ap_path, "modify", "number", value=max(0, old_ap - int(args.get("ap_cost", 1) or 1)),
                 old_value=old_ap, label=label)

    api.log_line(log_path, f"{attacker} 攻击 {target}：伤害 {amount}（{target} 剩余 {new}）")


def _roll(api, expression):
    """按 "NdM" 表达式掷骰（走引擎随机，确定性）。

    输入：
        api: 服务手柄；
        expression: 形如 "2d6" / "1d3"；空字符串表示不掷。
    输出：
        点数总和（int）。
    异常：
        ValueError: 表达式写法不认识。
    变量：
        count / sides / total: 中间结果。
    """
    text = (expression or "").strip().lower()
    if not text:
        return 0
    count_text, separator, sides_text = text.partition("d")
    if not separator or not count_text.isdigit() or not sides_text.isdigit():
        raise ValueError(f"骰子表达式要写成 NdM（例如 2d6），实际是 {expression!r}")
    count, sides = int(count_text), int(sides_text)
    if count <= 0 or sides <= 0:
        raise ValueError(f"骰子数量与面数都要大于 0，实际是 {expression!r}")
    total = 0
    for _ in range(count):
        total += api.rand_int(1, sides)
    return total
