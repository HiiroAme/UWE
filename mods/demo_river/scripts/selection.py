"""选中与"两步移动"的界面状态服务（Mod 侧）：全写 Context，不进存档。"""

KEY_SELECTED = "demo_river.selected"
KEY_PENDING = "demo_river.pending"
KEY_ATTACKERS = "demo_river.attackers"


def select(api, args):
    """选中一个单位（顺带清掉"待确认的目标格"）。"""
    api.context.put(KEY_SELECTED, args["unit"])
    api.context.put(KEY_PENDING, "")


def preview(api, args):
    """第一步：记下"玩家想走到哪"（先只显示路径，不移动）。"""
    api.context.put(KEY_PENDING, args["to"])


def clear(api, args):
    """取消选中（也清掉待确认的目标格）。"""
    api.context.put(KEY_SELECTED, "")
    api.context.put(KEY_PENDING, "")


def toggle_attacker(api, args):
    """把某个单位加进 / 移出"本场攻击的参战名单"（联合攻击用）。

    输入：
        api: 服务手柄；
        args: {"unit": 单位键}。
    输出：
        无（只改界面状态）。
    异常：
        KeyError: 缺 unit（Mod 数据写错）。
    变量：
        unit_key / attackers: 中间结果。
    """
    unit_key = args["unit"]
    attackers = list(api.context.get(KEY_ATTACKERS, []) or [])
    if unit_key in attackers:
        attackers.remove(unit_key)
    else:
        attackers.append(unit_key)
    api.context.put(KEY_ATTACKERS, attackers)


def clear_attackers(api, args):
    """清空"本场攻击的参战名单"（声明成功之后调它，见 N-1）。

    为什么必须清：声明成功的那些单位**立刻被标成"本回合已打过"**，
    而视图只给"还没打过"的单位"加入 / 移出名单"的点击——名单里留着已打过的单位，
    玩家既点不掉它、下一次声明又会把它一起带上，服务端一看 `fought` 就整场拒绝，
    结果一个攻击阶段只能打一场。声明即消耗 ≠ 名单要继续留着。

    输入：
        api / args：本服务没有参数（键名是本 Mod 的界面状态约定）。
    输出：
        无（只改界面状态）。
    异常：
        无。
    变量：
        无。
    """
    api.context.put(KEY_ATTACKERS, [])
