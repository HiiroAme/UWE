"""战场视图脚本（Mod 侧）：排版、指引、高亮、点击结果，全在这里。

契约（见 core.ui.PageCallable）：
    build_view(page_context) -> View
        page_context.state     真实 State（**只读**：界面直接读事实，§17.2 只读流）
        page_context.size      窗口尺寸
        page_context.context   界面状态（不存档；选中的单位放在这里）
        page_context.functions 加载期函数表（格子画法从模块来）

分工：
    - **格子怎么画**：模块 `pack_hex_grid.hex_grid.hexagon`（真六边形界面件）；
    - **每格显示什么、高亮成什么颜色、点了产生什么输入**：本脚本；
    - **引擎**：只把层翻译成绘制指令、把点击翻译成统一输入。

这个脚本同时承担"让人一眼看懂怎么玩"的责任：左边是操作说明与图例，
右边是当前控制方 / 双方单位 / 战报，中圈用颜色告诉你哪一格能走、哪一格能打。
"""

from core.ui import ClickResult, Layer, View

# 界面状态里存"当前选中的单位"的键（与 selection.py 一致）。
SELECTED_KEY = "demo_hex.selected"

# 一个格子在屏幕上的大小（像素半径）。
HEX_SIZE = 60.0

# 配色：底色 / 格线 / 阵营 / 三种高亮。
BACKGROUND = (20, 23, 30, 255)
PANEL_COLOR = (30, 34, 44, 255)
PANEL_TEXT = (226, 230, 238, 255)
GRID_COLOR = (96, 106, 126, 255)
EMPTY_FILL = (44, 50, 62, 255)
RED_FILL = (92, 58, 58, 255)
BLUE_FILL = (52, 68, 100, 255)
SELECT_COLOR = (250, 208, 88, 255)     # 选中的单位：金
MOVE_COLOR = (124, 206, 140, 255)      # 能走的空格：绿
ATTACK_COLOR = (232, 112, 108, 255)    # 能打的敌军：红
OWN_COLOR = (132, 168, 232, 255)       # 自己还活着的单位：淡蓝


def build_view(page_context):
    """生成战场画面。

    输入：
        page_context: 引擎给的只读上下文（state / size / context / page_id）。
    输出：
        View。
    异常：
        无（State 缺字段时按"没这回事"处理，界面不该因为数据不全而崩）。
    变量：
        state / size / side / game_over: 局面与当前控制方；
        selected: 当前选中的单位键（没有、或不是当前控制方时为空字符串）；
        plan: 每格的高亮与点击结果；
        layers: 逐层拼起来的画面。
    """
    state = page_context.state
    width, height = page_context.size
    units = state.get("units", {})
    nodes = state.get("nodes", {})
    adjacency = state.get("adjacency", {})
    side = state.get("control_side", "red")
    game_over = bool(state.get("game_over"))

    # 选中状态只在"确实轮到这个单位的阵营、且它还活着"时算数。
    selected = page_context.context.get(SELECTED_KEY, "") if not game_over else ""
    selected_unit = units.get(selected) if selected else None
    if not _is_playable_unit(selected_unit, side):
        selected = ""
        selected_unit = None

    plan = _plan_cells(node_keys=tuple(nodes), units=units, adjacency=adjacency, side=side,
                       game_over=game_over, selected=selected, selected_unit=selected_unit)

    items = {}
    for node_key in nodes:
        occupant = _unit_at(units, node_key)
        entry = plan.get(node_key, {})
        items[node_key] = {
            "text": _node_text(node_key, occupant, units.get(occupant) if occupant else None),
            "color": _node_fill(occupant, units.get(occupant) if occupant else None),
            "outline_color": entry.get("outline_color", GRID_COLOR),
            "font_size": 15 if occupant else 12,
            "text_color": (238, 238, 238, 255) if occupant else (138, 146, 162, 255),
            "click": entry.get("click"),
        }

    make_grid = page_context.functions["pack_hex_grid.hex_grid.hexagon"]
    layers = list(make_grid(
        nodes=nodes,
        size=HEX_SIZE,
        center=(width / 2 - 4, height / 2 + 12),
        items=items,
        outline_width=3.0,
    ))

    layers.extend(_panel_layers(width=width, height=height, state=state, units=units,
                                side=side, selected=selected, game_over=game_over))
    layers.append(_end_turn_button(width=width, height=height, side=side,
                                   game_over=game_over))
    if game_over:
        layers.append(_banner(state, width, height))
    return View(layers=tuple(layers), background=BACKGROUND)


def _plan_cells(node_keys, units, adjacency, side, game_over, selected, selected_unit):
    """算每一格的高亮与点击结果（这一局的"操作方式"）。

    输入：
        node_keys: 地图上的全部格子键（用来给普通空格补"取消选中"的点击）；
        units: 单位表；adjacency: 相邻关系表；
        side: 当前控制方；game_over: 战斗是否结束；
        selected / selected_unit: 当前选中的单位（键与数据，没有则空 / None）。
    输出：
        {节点键: {"outline_color": 颜色, "click": ClickResult 或 None}}；
        没有条目的格子（例如够不着的敌军）就是"点了没反应"。
    异常：
        无。
    变量：
        plan / my_node / neighbors: 中间结果。

    点击口径（与规则一致，先拦在这里能让玩家少看到"拒绝"）：
        - 战斗结束：所有格子都不给点击；
        - 点当前控制方还活着的单位：选中它；再点一次 = 取消选中；
        - 点相邻的敌军（且已经选中自己的单位）：攻击；
        - 点相邻的空格（且已经选中自己的单位）：移动；
        - 其余格子：取消选中（不产生任何游戏动作）。
    """
    plan = {}
    if game_over:
        return plan

    my_node = selected_unit.get("at", "") if selected_unit else ""
    neighbors = adjacency.get(my_node, []) if my_node else []

    # 自己还活着的单位：可选（选中 = 点它；已选中时再点 = 取消）。
    for unit_key in sorted(units):
        unit = units[unit_key]
        node_key = unit.get("at", "")
        if not node_key or not _is_alive(unit):
            continue
        if unit.get("side") != side:
            continue
        if unit_key == selected:
            plan[node_key] = {"outline_color": SELECT_COLOR,
                              "click": ClickResult(kind="clear_selection", data={})}
        else:
            plan[node_key] = {"outline_color": OWN_COLOR,
                              "click": ClickResult(kind="select", data={"unit": unit_key})}

    if not selected_unit:
        return plan

    # 选中之后：相邻空格 = 能走，相邻敌军 = 能打。
    for node_key in neighbors:
        if not isinstance(node_key, str):
            continue
        occupant = _unit_at(units, node_key)
        occupant_unit = units.get(occupant) if occupant else None
        if occupant_unit is None:
            plan[node_key] = {"outline_color": MOVE_COLOR,
                              "click": ClickResult(kind="move",
                                                   data={"unit": selected, "to": node_key})}
        elif _is_alive(occupant_unit) and occupant_unit.get("side") != side:
            plan[node_key] = {"outline_color": ATTACK_COLOR,
                              "click": ClickResult(kind="attack",
                                                   data={"unit": selected, "target": occupant})}

    # 选中的那一格自己：金框（覆盖上面的淡蓝）。
    plan[my_node] = {"outline_color": SELECT_COLOR,
                     "click": ClickResult(kind="clear_selection", data={})}

    # 剩下的普通空格：点一下 = 取消选中（有单位的格子不在这里补，避免误点敌军）。
    for node_key in node_keys:
        if node_key in plan or _unit_at(units, node_key):
            continue
        plan[node_key] = {"outline_color": GRID_COLOR,
                          "click": ClickResult(kind="clear_selection", data={})}
    return plan


def _panel_layers(*, width, height, state, units, side, selected, game_over):
    """左侧说明面板 + 右侧状态面板 + 战报。

    输入：
        width / height: 窗口尺寸；
        state / units / side / selected / game_over: 局面与界面状态。
    输出：
        Layer 列表。
    异常：
        无。
    变量：
        layers: 逐块拼起来的层。
    """
    layers = [
        Layer(id="help", rect=(16, 56, 272, height - 72), kind="text",
              text=_help_text(), align="left", font_size=15,
              color=PANEL_COLOR, text_color=PANEL_TEXT, z=20),
        Layer(id="hud", rect=(width - 296, 56, 280, height - 140), kind="text",
              text=_hud_text(state, units, side, selected, game_over), align="left",
              font_size=15, color=PANEL_COLOR, text_color=PANEL_TEXT, z=20),
    ]
    return layers


def _end_turn_button(*, width, height, side, game_over):
    """右下角的"结束回合"按钮（文字写明交给谁）。"""
    if game_over:
        label = "战斗已结束"
        click = None
    else:
        label = f"结束回合（交给{_side_name(_other_side(side))}）"
        click = ClickResult(kind="end_turn", data={}, settle=True)
    return Layer(
        id="button:end_turn",
        rect=(width - 296, height - 76, 280, 48),
        kind="text",
        text=label,
        font_size=17,
        color=(70, 96, 140, 255) if not game_over else (52, 56, 66, 255),
        text_color=(245, 245, 245, 255),
        click=click,
        z=30,
    )


def _banner(state, width, height):
    """战斗结束时盖在中间的横幅。"""
    winner = _side_name(state.get("winner", ""))
    return Layer(
        id="banner:winner",
        rect=(width / 2 - 230, height / 2 - 70, 380, 130),
        kind="text",
        text=f"{winner}获胜\n\n按 Esc 返回首页 · 按 S 存档",
        font_size=26,
        color=(24, 28, 38, 245),
        text_color=(255, 226, 150, 255),
        z=40,
    )


def _help_text():
    """左侧的操作说明（这一局怎么玩，一眼看完）。"""
    return "\n".join([
        "怎么玩",
        "",
        "1. 点自己的单位 → 选中（金框）",
        "2. 选中后：",
        "   绿框 = 能走过去，点它移动",
        "   红框 = 能打，点它攻击",
        "3. 走一步 / 打一次各花 1 点行动力",
        "   每回合恢复 2 点（看单位格上的「行」）",
        "4. 行动完点右下角「结束回合」",
        "   → 交给对方行动",
        "5. 点空地 / 再点一次选中的单位",
        "   = 取消选中",
        "",
        "图例",
        "",
        "金框：当前选中的单位",
        "淡蓝框：你能操作的单位",
        "绿框：能移动到的格子",
        "红框：能攻击的敌军",
        "",
        "目标：把对方单位的血量打到 0。",
        "打光一方即获胜。",
    ])


def _hud_text(state, units, side, selected, game_over):
    """右侧状态面板：谁在行动、双方单位、阵亡、优势、战报。"""
    lines = [f"第 {state.get('turn', '?')} 回合", ""]
    if game_over:
        lines.append(f"战斗结束：{_side_name(state.get('winner', ''))}获胜")
    else:
        lines.append(f"当前控制方：{_side_name(side)}")
    lines.append("")

    for group_side in ("red", "blue"):
        lines.append(f"{_side_name(group_side)}单位")
        found = False
        for unit_key in sorted(units):
            unit = units[unit_key]
            if unit.get("side") != group_side:
                continue
            found = True
            mark = "（已选中）" if unit_key == selected else ""
            if not _is_alive(unit):
                lines.append(f"  {unit.get('name', unit_key)}{mark} 已阵亡")
                continue
            lines.append(
                f"  {unit.get('name', unit_key)}{mark}\n"
                f"    血 {unit.get('hp', '?')}  行 {unit.get('ap', '?')}"
                f"  战力 {unit.get('power', '?')}  在 {unit.get('at', '?')}"
            )
        if not found:
            lines.append("  （没有）")
        lines.append("")

    lines.append(f"红方优势：{state.get('advantage', '?')}")
    lines.extend(["", "战报："])
    for line in state.get("log", [])[-7:]:
        lines.append(f"· {line}")
    return "\n".join(lines)


def _unit_at(units, node_key):
    """找出停在这个格子上的单位键（按 id 排序取第一个，保证确定）。

    只认"还活着、且还在棋盘上"的单位：阵亡的 at 是空字符串，不占格子。
    """
    for unit_key in sorted(units):
        unit = units[unit_key]
        if unit.get("at") == node_key and _is_alive(unit) and node_key:
            return unit_key
    return ""


def _is_alive(unit):
    """单位是否还活着（血量 > 0）。"""
    return bool(unit) and unit.get("hp", 0) > 0


def _is_playable_unit(unit, side):
    """这个单位现在能不能被操作（活着 + 属于当前控制方 + 还在棋盘上）。"""
    return _is_alive(unit) and unit.get("side") == side and bool(unit.get("at"))


def _node_text(node_key, occupant, occupant_unit):
    """格子上显示的文字：有单位就显示名字 / 血量 / 行动力，没单位就显示格子键。"""
    if not occupant or occupant_unit is None:
        return node_key
    return (f"{occupant_unit.get('name', occupant)}\n"
            f"血 {occupant_unit.get('hp', '?')} · 行 {occupant_unit.get('ap', '?')}")


def _node_fill(occupant, occupant_unit):
    """格子的底色：空的是深灰，红方单位偏红，蓝方单位偏蓝（一眼看出阵营）。"""
    if not occupant or occupant_unit is None:
        return EMPTY_FILL
    if occupant_unit.get("side") == "red":
        return RED_FILL
    return BLUE_FILL


def _other_side(side):
    """另一方的名字。"""
    return "blue" if side == "red" else "red"


def _side_name(side):
    """阵营的显示名（State 里存的是 red / blue）。"""
    return "红方" if side == "red" else "蓝方"
