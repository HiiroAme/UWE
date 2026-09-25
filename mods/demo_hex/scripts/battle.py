"""阵亡处理与战斗收尾（由触发链调用，Mod 侧脚本）。

触发链：事件 unit_destroyed → 触发 → 动作 after_destroyed → 本服务。做两件事：

1. **把 hp ≤ 0 的单位撤出地图**（把 /units/<key>/at 置成空字符串）：
   单位本身留在 /units 里当阵亡记录（血量 0、destroyed=true），但它不再占格子、
   不再挡路、也不该能被选中或操作——"被打死之后还站在那儿挡路"是说不通的。
2. **看战斗是否结束**：只剩一方还有活着的单位时，置 /game_over 与 /winner。

为什么"撤出地图"不是引擎的事：引擎不认识"单位""血""地图"，这只是这一局对
"阵亡"的处置（也可以改成留残骸、改成伤兵撤离，都是改这一段 + 数据）。
"""


def after_destroyed(api, args):
    """一个单位被打到 0 血之后：撤出地图，再判战斗是否结束。

    输入：
        api: 服务手柄；
        args: 本动作的参数（用 log_path 写战报）。
    输出：
        无（改动经 api.emit 记账）。
    异常：
        无。
    变量：
        units / retired / alive / winner: 中间结果。
    """
    log_path = args.get("log_path", "")
    units = api.get("/units")

    # 1. 撤出所有"已经没血、还在地图上"的单位（幂等：撤过的 at 是空字符串）。
    retired = []
    for unit_key in sorted(units):
        if units[unit_key].get("hp", 0) > 0:
            continue
        at_path = f"/units/{unit_key}/at"
        at = api.get(at_path)
        if not at:
            continue  # 已经撤出过了
        api.emit(at_path, "modify", "string", value="", old_value=at)
        retired.append(units[unit_key].get("name", unit_key))
    for name in retired:
        api.log_line(log_path, f"{name} 被击毁，撤出战场")

    # 2. 判胜负：还有活人的阵营只剩一个就结束。
    alive = sorted({
        units[unit_key]["side"]
        for unit_key in units
        if units[unit_key].get("hp", 0) > 0
    })
    if len(alive) != 1:
        return

    winner = alive[0]
    api.emit("/game_over", "modify", "bool", value=True, old_value=False)
    api.emit("/winner", "modify", "string", value=winner, old_value=api.get("/winner"))
    api.log_line(log_path, f"战斗结束：{_side_name(args, winner)}获胜")
    api.log(f"战斗结束，获胜方：{winner}")


def _side_name(args, side):
    """取一方的显示名（Mod 可以用参数改叫法）。"""
    if side == "red":
        return str(args.get("red_name", "") or "红方")
    return str(args.get("blue_name", "") or "蓝方")


