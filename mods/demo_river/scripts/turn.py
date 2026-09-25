"""阶段推进与回合开始刷新（Mod 侧服务）：写的都是 State 里的普通字段。"""

STAGE_MOVE = "move"
STAGE_ATTACK = "attack"


def to_attack(api, args):
    """移动阶段 → 攻击阶段（本方的攻击阶段开始）。"""
    api.emit("/stage", "modify", "string", value=STAGE_ATTACK,
             old_value=api.get("/stage"))
    _set_settling(api, False)
    _clear_context(api)


def begin_settle(api, args):
    """攻击阶段内：关闭"声明战斗"窗口，进入逐场结算（幂等）。

    声明窗口由规则层读 `/settling` 把关；本服务只负责翻标记与清界面状态，
    不直接结算——"下一场"按钮会调 pack_combat_crt 的 resolve_next。
    """
    _set_settling(api, True)
    _clear_context(api)


def pass_control(api, args):
    """结束本方回合：**先恢复本方混乱单位**，再回合 +1、把控制权交给对方。

    本服务只管这一局的事：混乱恢复、回合 / 控制方 / 阶段三个状态的推进、界面状态清空。
    "新控制方的单位要恢复行动力、刷新控制区标记、清掉已打过"属于通用回合骨架，
    由动作的第二步调 `pack_wargame_core:service:begin_side`（见 42_actions_turn.json）。
    "大回合末判胜负"由命令的第二个动作调 `pack_wargame_core:service:check_victory`
    （见 52_commands_turn.json 的分支："守方交棒 = 大回合末"）。

    混乱恢复（设计文档 §10.1 M-5）：**在该单位所属方的战斗阶段结束时**翻回来——
    "结束回合"正是该方攻击阶段的结束，所以恢复放在这里（在换控制方之前，恢复的是**本方**）。
    """
    # 阵营名从 State 读（init 从 params 写进来的那一份）：换名字不会静默失效（甲-1 / N-5）。
    current = api.get("/control_side")
    attacker_side = api.get("/attacker_side")
    defender_side = api.get("/defender_side")
    other = defender_side if current == attacker_side else attacker_side
    _recover_disordered(api, current)
    turn = api.get("/turn")
    api.emit("/turn", "modify", "number", value=turn + 1, old_value=turn)
    api.emit("/control_side", "modify", "string", value=other, old_value=current)
    _set_settling(api, False)
    api.emit("/stage", "modify", "string", value=STAGE_MOVE, old_value=api.get("/stage"))
    _clear_context(api)


def _set_settling(api, value):
    """把 /settling 写成指定值；旧存档没有这个字段时按缺省 false 处理。"""
    if not api.exists("/settling"):
        if value:
            api.emit("/settling", "add", "bool", value=True)
        return
    old = api.get("/settling")
    if bool(old) != value:
        api.emit("/settling", "modify", "bool", value=value, old_value=old)


def _clear_context(api):
    """清空三样界面状态（选中 / 待确认目标 / 参战名单）。"""
    api.context.put("demo_river.selected", "")
    api.context.put("demo_river.pending", "")
    api.context.put("demo_river.attackers", [])


def _recover_disordered(api, side):
    """把某一方还处于混乱的单位翻回来（只在它的攻击阶段结束时做）。"""
    units = api.get("/units")
    for unit_id in sorted(units):
        unit = units[unit_id]
        if unit.get("side") == side and unit.get("disordered") and not unit.get("destroyed"):
            api.emit(f"/units/{unit_id}/disordered", "modify", "bool",
                     value=False, old_value=True)
