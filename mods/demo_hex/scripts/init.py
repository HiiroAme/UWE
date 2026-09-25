"""初始化钩子：摆出这一局的地图与两个单位（Mod 侧脚本，只写"这一局的事实"）。

契约（见 modload.loader._build_initial_state）：
    build_state(mod) -> dict
        mod: 引擎给的上下文（ModContext），带 info / hub / content / files；
        返回值就是这局游戏的初始 State（纯数据树）。

说明：
    几何与画法都在模块里：本脚本通过 `mod.functions` 调用六边形模块的函数
    （节点键、相邻关系表），自己不写任何几何。
"""

def build_state(mod):
    """构造初始 State。

    输入：
        mod: 引擎给的上下文（ModContext）——这里用到模板、阶段与模块函数：
            mod.template_values(...) 取模板属性（entity / node 注册表里的模板）；
            mod.phase_actions(...)   取某个阶段允许的动作。
            mod.functions[...]       调用模块提供的函数（几何：键、相邻关系表）。
    输出：
        初始 State 字典。
    异常：
        KeyError: 模板 / 阶段 id 写错了（Mod 数据问题，加载期就会炸）。
    变量：
        radius: 地图半径（半径 1 → 7 个格子）；
        coords: 全部格子坐标（由模块的 all_within 算出来）；
        nodes: 节点表（坐标写在节点上）；
        adjacency: 相邻关系表（节点 → 相邻节点列表）；
        units: 单位表（属性来自实体模板 + 每个实例自己的覆盖）；
        phase_id: 本局从哪个阶段开始；
        node_key / all_within / adjacency_map: 六边形模块提供的三个函数（Mod 不自己写几何）。
    """
    radius = 1
    node_key = mod.functions["pack_hex_grid.node_key.q_r"]
    all_within = mod.functions["pack_hex_grid.all_within.hex"]
    adjacency_map = mod.functions["pack_hex_grid.adjacency_map.from_coords"]
    coords = all_within(radius)
    adjacency = adjacency_map(coords)
    phase_id = "demo_hex:phase:action"

    # 节点：属性来自 node 模板（它已经合并了 terrain 预设），每个格子再补上自己的坐标。
    node_template = mod.template_values("demo_hex:node:plain")
    nodes = {}
    for q, r in coords:
        node = dict(node_template)
        node.update({"q": q, "r": r})
        nodes[node_key(q, r)] = node

    # 单位：属性来自 entity 模板，每个实例补上自己的身份与位置。
    unit_template = mod.template_values("demo_hex:entity:unit")
    units = {}
    for unit_id, name, side, at in (
        ("red_1", "红军 1", "red", node_key(-1, 0)),
        ("blue_1", "蓝军 1", "blue", node_key(1, 0)),
    ):
        unit = dict(unit_template)
        unit.update({"id": unit_id, "name": name, "side": side, "at": at})
        units[unit_id] = unit

    return {
        "turn": 1,
        # 谁在行动：热座对局（同一个人轮流替两边走），结束回合时交给对方。
        # 引擎只当它是个普通字符串，规则与服务读它来判断"这一手该谁下"。
        "control_side": "red",
        "phase": phase_id,
        "phase_actions": list(mod.phase_actions(phase_id)),
        "game_over": False,
        "winner": "",
        "scenario": mod.info.scenario,
        # 派生值（§6.5）：由基础值完全决定，引擎会在存读时按公式重算并纠正；
        # 开局这里先写一份（模板的 power 已经在单位属性里了）。
        "advantage": 0,
        "nodes": nodes,
        "adjacency": adjacency,
        "units": units,
        "log": ["开局：红军 1 对阵 蓝军 1"],
    }
