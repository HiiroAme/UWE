"""六边形移动规则（纯函数，模块实现）：可达范围、路径还原、控制区。

设计口径（与《示例兵棋》§7.3 一致，但**不写死任何玩法名词**）：
    enter_cost   进去要花多少移动力（缺省 = 不可进入）
    can_pass     能不能**穿过**（省略 = 只要能进就能穿）
    can_stop     能不能**当成落点**（省略 = 能穿就能停）
    extra_moves  特例通道：[{"from":…, "to":…, "cost":…}]（例如成对渡口）
    zoc          敌方控制区格子（进入即停；回合初在里面时必须先出来）

这些参数全部由调用方给——模块只算"按规则能到哪、怎么走"。
"""

import heapq


def reachable_cells(
    *,
    start,
    budget,
    neighbors,
    enter_cost=None,
    can_pass=None,
    can_stop=None,
    extra_moves=(),
    zoc=(),
    stop_on_enter_zoc=True,
    must_leave_zoc=False,
):
    """从 start 出发、花得起 budget 的落点（按地形消耗求最短路）。

    输入：
        start: 出发格（节点键）；
        budget: 这回合的移动力（非负数；地形消耗是"进一格花多少"）；
        neighbors: {格子: [相邻格子, …]}；
        enter_cost: {格子: 消耗}；没列的格子 = 不可进入；
        can_pass: 可穿过的格子集合（None = 只要进得去就能穿）；
        can_stop: 可作为落点的格子集合（None = 能穿就能停）；
        extra_moves: 特例通道 [{"from":…, "to":…, "cost":…}, …]（例如渡口，两个方向各写一条）；
        zoc: 敌方控制区格子集合；
        stop_on_enter_zoc: True = 进入敌方控制区后**不能再往下走**（可以停在那儿）；
        must_leave_zoc: True = 出发格在敌方控制区时，本回合第一步必须走到控制区外；
            出去之后按剩余移动力正常继续走（不是"走完就停"）。
    输出：
        {"costs": {落点: 花了多少}, "previous": {格子: 上一步}}——
        costs 只含"能停"的格子；previous 含所有走过的格子，用来还原路径。
    异常：
        ValueError: budget 是负数，或参数写法不对。
    变量：
        costs / previous / heap / step: 搜索过程的中间结果。
    """
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget < 0:
        raise ValueError(f"budget 必须是非负数，实际是 {budget!r}")
    if start not in neighbors and enter_cost is not None and start not in enter_cost:
        # 出发格本身也要在地图上；否则直接报错，免得静默算出空结果。
        raise ValueError(f"出发格 {start!r} 不在邻居表里")

    allowed_pass = None if can_pass is None else set(can_pass)
    allowed_stop = allowed_pass if can_stop is None else set(can_stop)
    zoc_set = set(zoc)
    extra = list(extra_moves or ())

    # 出发格在敌方控制区里：第一步必须先到控制区外，之后按剩余移动力继续走。
    must_leave_now = must_leave_zoc and start in zoc_set
    if must_leave_now:
        # 起点记 0 只用来挡住"绕回起点"，不放进最终落点（它本来就是出发格）。
        costs, previous = {start: 0.0}, {start: None}
        heap = []
        for nxt, price in _steps_out(start, neighbors, enter_cost, extra):
            if price > budget or nxt in zoc_set:
                continue
            if allowed_pass is not None and nxt not in allowed_pass:
                continue
            costs[nxt] = float(price)
            previous[nxt] = start
            heapq.heappush(heap, (float(price), nxt))
    else:
        costs = {start: 0.0}
        previous = {start: None}
        heap = [(0.0, start)]
    while heap:
        spent, cell = heapq.heappop(heap)
        if spent > costs.get(cell, float("inf")):
            continue
        if stop_on_enter_zoc and cell in zoc_set and cell != start:
            continue          # 进了敌方控制区就停住，不再往外扩
        for nxt, price in _steps_out(cell, neighbors, enter_cost, extra):
            if allowed_pass is not None and nxt not in allowed_pass:
                continue
            total = spent + price
            if total > budget or total >= costs.get(nxt, float("inf")):
                continue
            costs[nxt] = float(total)
            previous[nxt] = cell
            heapq.heappush(heap, (float(total), nxt))

    stops = {cell: value for cell, value in costs.items()
             if (allowed_stop is None or cell in allowed_stop)
             and not (must_leave_now and cell == start)}
    return {"costs": stops, "previous": previous}


def path_to(previous, start, target):
    """按 reachable_cells 给的 previous 还原一条路径。

    输入：
        previous: {格子: 上一步}（起点对到 None）；
        start: 起点；target: 终点。
    输出：
        从 start 到 target 的格子列表（含两端）；走不到时是空列表。
    异常：
        无。
    变量：
        path / cell: 回溯过程。
    """
    if target not in previous:
        return []
    path = []
    cell = target
    while cell is not None:
        path.append(cell)
        if cell == start:
            break
        cell = previous.get(cell)
    path.reverse()
    return path if path and path[0] == start else []


def zoc_cells(units, neighbors, skip=()):
    """算每一方的控制区（相邻格）。

    输入：
        units: {单位键: {"at": 格子, "side": 阵营[, "destroyed": 真假]}}；
        neighbors: {格子: [相邻格子, …]}；
        skip: 不产生控制区的单位键（例如**混乱**的单位）。
    输出：
        {阵营: [格子, …]}（去重、排序；同一格可以同时出现在两方里）。
    异常：
        无（单位没有 at / side 就跳过）。
    变量：
        table / cell / unit: 中间结果。
    """
    skipped = set(skip or ())
    table = {}
    for unit_id in sorted(units):
        if unit_id in skipped:
            continue
        unit = units[unit_id]
        if unit.get("destroyed"):
            continue
        cell = unit.get("at", "")
        side = unit.get("side", "")
        if not cell or not side:
            continue
        cells = table.setdefault(side, set())
        for neighbor in neighbors.get(cell, []):
            cells.add(neighbor)
    return {side: sorted(cells) for side, cells in table.items()}


def levels_from_state(nodes, units, moving_key, *, cost_key="move_cost",
                      passable_key="passable", occupant_key="at",
                      side_key="side", destroyed_key="destroyed",
                      default_cost=1.0):
    """把"地形 + 占位"翻成通行等级：进一格花多少 / 能不能穿 / 能不能停。

    为什么要成一份：移动服务算"真能走到哪"、视图算"画给玩家看到哪"，两处必须是同一套判定
    （P5：同一个对象上的逻辑只能有一处）。以前两边各写了一遍，改一处忘一处就是
    "看得到、走不到"。现在两边都调这个函数。

    输入：
        nodes: {格子: {<passable_key>: 真假, <cost_key>: 消耗}}；
        units: {单位键: {<occupant_key>: 格子, <side_key>: 阵营[, <destroyed_key>: 真假]}}；
        moving_key: 正在移动的单位键——它自己不算占位（"不能跟自己堆叠"要排除掉"我"）；
        cost_key / passable_key / occupant_key / side_key / destroyed_key: 字段名（缺省是一般写法）；
        default_cost: 节点没写消耗时按几算（写了但为假值则按 0 算，与旧写法一致）。
    输出：
        (enter_cost, can_pass, can_stop)：
        enter_cost = {格子: 进去要花的移动力}（**不可通行**的格子不在表里）；
        can_pass = 能穿过的格子集合（被敌军占的格子不在这里）；can_stop = 能当落点的格子集合。
        三者一起递给 reachable_cells：能不到能进看 can_pass、能不能停看 can_stop——
        己方占的格子能穿不能停，敌方占的格子连穿都不行。
    异常：
        无（节点 / 单位写法不对就跳过那一条，不打断整张图）。
    变量：
        occupant / mine / enter_cost / can_pass / can_stop / other: 中间结果。
    """
    occupant = {}
    for unit_key, unit in units.items():
        if unit_key != moving_key and not unit.get(destroyed_key) and unit.get(occupant_key):
            occupant[unit[occupant_key]] = unit

    mine = (units.get(moving_key) or {}).get(side_key, "")
    enter_cost = {}
    can_pass = set()
    can_stop = set()
    for cell, node in nodes.items():
        if not node.get(passable_key, True):
            continue                      # 不可通行（比如河流）：进都不许进
        enter_cost[cell] = float(node.get(cost_key, default_cost) or 0)
        other = occupant.get(cell)
        if other is not None and other.get(side_key) != mine:
            continue                      # 敌军：硬阻挡（不能穿也不能停）
        can_pass.add(cell)
        if other is None:
            can_stop.add(cell)            # 己方占的格子：能穿、不能停
    return enter_cost, can_pass, can_stop


def retreat_path(*, start, battle, steps, neighbors, can_enter, can_stop=None,
                 rand_int=None):
    """算一条撤退路径：每步都比上一步离战斗点更远一格，方向随机。

    输入：
        start: 撤退单位所在格；battle: 战斗点（被打的那一格）；
        steps: 要退几格（>=1）；
        neighbors: {格子: [相邻格子, …]}（**整张图**的相邻关系，用来算格距）；
        can_enter: 能经过的格子集合（可通行 / 没被占 / 不在敌方控制区 / 不走渡口）；
        can_stop: 能作为终点的格子集合（省略 = 能进入就能停）；
        rand_int: 随机函数（注入，保证可回放）：rand_int(low, high) -> int。
    输出：
        长度 steps+1 的格子列表（含起点）；**退不了就返回空列表**（调用方按"被消灭"处理）。
    异常：
        ValueError: steps < 1 或缺 rand_int。
    变量：
        dist / path / remaining / candidates: 中间结果。
    """
    if steps < 1:
        raise ValueError(f"steps 必须是正整数，实际是 {steps!r}")
    if rand_int is None:
        raise ValueError("必须注入随机函数 rand_int（确定性 / 可回放）")

    stops = set(can_enter) if can_stop is None else set(can_stop)
    enter = set(can_enter)

    # 格距：从战斗点在全图上做一次 BFS（六边形格距 = 图上最短步数）。
    dist = {battle: 0}
    queue = [battle]
    while queue:
        cell = queue.pop(0)
        for neighbor in neighbors.get(cell, []):
            if neighbor not in dist:
                dist[neighbor] = dist[cell] + 1
                queue.append(neighbor)

    def feasible(cell, remaining):
        """从 cell 出发还能不能走完 remaining 步（记忆化搜索）。"""
        if remaining == 0:
            return cell in stops
        for nxt in neighbors.get(cell, []):
            if nxt in enter and dist.get(nxt, -1) == dist.get(cell, -1) + 1 \
                    and feasible(nxt, remaining - 1):
                return True
        return False

    path = [start]
    cell, remaining = start, steps
    while remaining > 0:
        candidates = [nxt for nxt in neighbors.get(cell, [])
                      if nxt in enter and dist.get(nxt, -1) == dist.get(cell, -1) + 1
                      and feasible(nxt, remaining - 1)]
        if not candidates:
            return []
        cell = candidates[rand_int(0, len(candidates) - 1)]
        path.append(cell)
        remaining -= 1
    return path


def _steps_out(cell, neighbors, enter_cost, extra_moves):
    """列出一个格子能走到的下一步：(目标, 花多少)。"""
    for neighbor in neighbors.get(cell, []):
        if enter_cost is None:
            yield neighbor, 1.0
            continue
        price = enter_cost.get(neighbor)
        if price is None:
            continue
        yield neighbor, float(price)
    for link in extra_moves:
        if link.get("from") == cell:
            yield link["to"], float(link.get("cost", 1))
