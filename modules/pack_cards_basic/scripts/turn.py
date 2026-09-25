"""结束回合（模块服务）：回合 +1、能量回满。

引擎不知道"回合"是什么（§12）——回合一到要做哪些事，由模块的实现 + Mod 的参数决定。

参数：
    turn_path:       回合数路径（默认 "/turn"）
    energy_path:     能量路径（可选，如 "/energy"）
    max_energy_path: 能量上限路径（可选，如 "/max_energy"）
    log_path:        战报列表路径（可选）
    turn_log:        战报措辞模板（可选，默认 "第 {turn} 回合开始"）
"""


def advance_turn(api, args):
    """结束当前回合：回合 +1，并把能量补到上限（只补不扣）。

    输入：
        api: 服务手柄；
        args: 路径参数（见模块文档）。
    输出：
        无。
    异常：
        KeyError: 回合路径不存在（Mod 自己的 State 问题）。
    变量：
        turn / energy / cap: 中间结果。
    """
    log_path = args.get("log_path", "")
    turn_path = args.get("turn_path", "/turn")
    turn = api.get(turn_path)
    api.emit(turn_path, "modify", "number", value=turn + 1, old_value=turn)

    energy_path = args.get("energy_path", "")
    max_energy_path = args.get("max_energy_path", "")
    if energy_path and max_energy_path:
        energy = api.get(energy_path)
        cap = api.get(max_energy_path)
        if energy != cap:
            api.emit(energy_path, "modify", "number", value=cap, old_value=energy)

    template = str(args.get("turn_log", "") or "第 {turn} 回合开始")
    api.log_line(log_path, template.format(turn=turn + 1))
