"""初始化钩子：读地图 → 建节点与相邻关系 → 摆双方部队（Mod 侧脚本，尽量薄）。

契约（见 modload.loader._build_initial_state）：
    build_state(mod) -> dict
        mod: 引擎给的上下文（ModContext）：info / content / files / functions / params。

这个脚本只做三件事，**不写玩法逻辑**：
    1. 把地图文件读进来（读文件是加载层的事，模块不碰文件）；
    2. 调模块的纯函数：解析字符地图、行列 → 轴向坐标、算相邻关系、配渡口对；
    3. 把地形模板与编成文件拼成初始 State。
"""

import json


def build_state(mod):
    """构造初始 State。

    输入：
        mod: ModContext（用到 files / functions / params / info / template_values）。
    输出：
        初始 State 字典（纯数据树）。
    异常：
        KeyError: 地图上出现没登记过的符号，或编成文件里的单位放到了地图外。
        ValueError: 地图本身不合法（各行格数不一致等，由模块的解析函数抛）。
    变量：
        params / files: 参数与文件端口；
        parse_map / to_axial / node_key / adjacency_map / find_fords: 模块提供的纯函数；
        layout / cells: 解析出来的地图；
        nodes / coords / adjacency / fords / units: 拼出来的 State 内容。
    """
    params = mod.params
    files = mod.files
    folder = mod.info.folder

    parse_map = mod.functions["pack_hex_grid.text_map.rows_cols"]
    to_axial = mod.functions["pack_hex_grid.offset.to_axial"]
    node_key = mod.functions["pack_hex_grid.node_key.q_r"]
    adjacency_map = mod.functions["pack_hex_grid.adjacency_map.from_coords"]
    find_fords = mod.functions["pack_hex_grid.map_check.fords"]

    layout = parse_map(files.read_text(f"{folder}/{params['map_path']}"))
    cells = layout["cells"]
    code_to_terrain = params["terrain_codes"]

    nodes = {}
    coords = []
    for (row, col) in sorted(cells):
        code = cells[(row, col)]
        if code not in code_to_terrain:
            raise KeyError(f"地图第 {row} 行第 {col} 列的符号 {code!r} 没有登记地形"
                           f"（在 mod_info.json 的 params.terrain_codes 里补）")
        node_template = code_to_terrain[code]
        node = mod.template_values(node_template)
        q, r = to_axial(row, col)
        node.update({"row": row, "col": col, "q": q, "r": r,
                     "code": code, "template": node_template})
        key = node_key(q, r)
        if key in nodes:
            raise ValueError(f"两个格子算出了同一个节点键 {key!r}（行列换算有问题）")
        nodes[key] = node
        coords.append((q, r))

    adjacency = adjacency_map(tuple(coords))
    fords = _ford_pairs(find_fords, cells, to_axial, node_key, params)
    units = _units(mod, nodes, to_axial, node_key, params)

    return {
        "turn": 1,
        # 阵营名写进 State：规则 / 命令分支这类**数据侧表达式**读不到 Mod 的 params
        # （甲-1 的方案 A），所以由 init 把 params 里的名字落到平铺路径上，谁都用这一份。
        "attacker_side": params.get("attacker_side", "north"),
        "defender_side": params.get("defender_side", "south"),
        "control_side": params.get("attacker_side", "north"),
        "stage": "move",
        "game_over": False,
        "winner": "",
        "map": {
            "rows": layout["rows"],
            "cols": layout["cols"],
            "path": params["map_path"],
        },
        "nodes": nodes,
        "adjacency": adjacency,
        "fords": fords,
        "units": units,
        "intro": _intro(mod, params),
        "intro_seen": False,
        "battles": [],
        "battle_report": {},
        "log": ["开局：青隼战斗群在洛川平原北岸集结，柳浦守备队退守白鹭河南岸"],
    }


def _intro(mod, params):
    """读开局介绍 / 规则说明（纯数据；内容由 Mod 作者编辑）。"""
    raw = json.loads(mod.files.read_text(f"{mod.info.folder}/{params['intro_path']}"))
    if not isinstance(raw, dict) or not raw.get("title") or not isinstance(raw.get("sections"), list):
        raise ValueError("setup/intro.json 必须有 title 和 sections 列表")
    return raw


def _ford_pairs(find_fords, cells, to_axial, node_key, params):
    """把"成对的渡口格"翻成 State 里的双向渡河通道。

    输入：
        find_fords: 模块的渡口配对函数；
        cells: {(行, 列): 符号}；
        to_axial / node_key: 模块的换算与键函数；
        params: Mod 参数（map_codes / ford_cost）。
    输出：
        [{"from": 节点键, "to": 节点键, "cost": 3}, …]（每个渡口两个方向各一条）。
    异常：
        ValueError: 有渡口找不到搭档（地图写错）。
    变量：
        result / key_of: 中间结果。
    """
    codes = params["map_codes"]
    result = find_fords(cells, codes["ford"], codes["river"])
    if result["problems"]:
        raise ValueError("；".join(result["problems"]))

    cost = int(params.get("ford_cost", 3))
    crossings = []
    for (north_cell, _middle, south_cell) in result["pairs"]:
        north_key = node_key(*to_axial(*north_cell))
        south_key = node_key(*to_axial(*south_cell))
        crossings.append({"from": north_key, "to": south_key, "cost": cost})
        crossings.append({"from": south_key, "to": north_key, "cost": cost})
    return crossings


def _units(mod, nodes, to_axial, node_key, params):
    """读编成文件，把每个单位放到它的初始格子上。

    输入：
        mod / nodes / to_axial / node_key / params: 同上。
    输出：
        {单位键: 单位数据}；每个单位带上 CRT 与状态字段（本例只要这几项）。
    异常：
        KeyError: 编成文件写了地图外的格；或两个单位摆在同一个格子上。
    变量：
        raw / units / seen: 中间结果。
    """
    folder = mod.info.folder
    raw = json.loads(mod.files.read_text(f"{folder}/{params['forces_path']}"))

    units = {}
    seen = {}
    for item in raw["units"]:
        unit_id = item["id"]
        if unit_id in units:
            raise KeyError(f"编成文件里有两个单位用了同一个 id：{unit_id!r}")
        at = node_key(*to_axial(int(item["row"]), int(item["col"])))
        if at not in nodes:
            raise KeyError(f"单位 {unit_id!r} 被放在地图外：行 {item['row']} 列 {item['col']}")
        if at in seen:
            raise KeyError(f"单位 {unit_id!r} 与 {seen[at]!r} 摆在同一个格子上（不能堆叠）")
        seen[at] = unit_id
        units[unit_id] = {
            "id": unit_id,
            "name": item["name"],
            "side": item["side"],
            "attack": int(item["attack"]),
            "move": int(item["move"]),
            "move_left": int(item["move"]),      # 即时值：本回合还剩多少移动力
            "at": at,
            "destroyed": False,
            "disordered": False,
            "zoc_start": False,
            "fought": False,
        }
    return units
