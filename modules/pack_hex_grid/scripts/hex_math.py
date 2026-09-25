"""六边形几何（轴向坐标）：键、相邻、距离、半径、相邻关系表。

坐标口径：每个格子写成 (q, r) 两个整数；六个方向的邻居是固定偏移。
不确定"格子叫什么名字"是调用方的事——`key_q_r(q, r)` 只是给一个开箱可用的写法。

这里的函数全是**纯函数**：同样的输入永远同样的输出，不读时钟、不取随机、不碰 State。
它们可以被规则、公式、初始化钩子或别的模块调用（表达式里写
["call", "pack_hex_grid.distance.hex", a, b]）。

参数校验口径：坐标必须是 int（bool 不算）、坐标对必须是长度为 2 的二元组、
半径必须是非负整数——写错的东西在调用点就报错，而不是算出一个错的结果。

（本文件只放"不画、不看文本地图"的几何。脚本文件之间不能互相 import，
所以像素换算在 hex_canvas.py、行列换算与字符地图在 hex_text_map.py，
那两份各自带一份很小的参数校验工具。）
"""

# 六个方向的邻居偏移（轴向坐标）。
DIRECTIONS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))


def key_q_r(q, r):
    """把坐标写成节点键，形如 "n0_0"、"n-1_2"。

    输入：
        q / r: 轴向坐标（int，bool 不算）。
    输出：
        字符串键；负数直接把 '-' 写在里面，前缀固定是 n。
    异常：
        TypeError: q / r 不是 int 或是 bool。
    变量：
        无。
    """
    _require_int(q, "q")
    _require_int(r, "r")
    return f"n{q}_{r}"


def parse_key_q_r(node_key):
    """把 key_q_r 生成的字符串还原成坐标。

    输入：
        node_key: 形如 "n-1_2" 的字符串。
    输出：
        (q, r) 二元组。
    异常：
        TypeError: node_key 不是字符串；
        ValueError: 写法不合法（前缀或分隔符不对、坐标不是整数）。
    变量：
        body / left / right: 拆出来的中间结果。
    """
    if not isinstance(node_key, str):
        raise TypeError(f"节点键必须是字符串，实际是 {type(node_key).__name__}")
    if not node_key.startswith("n") or "_" not in node_key:
        raise ValueError(f"节点键写法不合法（应形如 n0_0）：{node_key!r}")
    body = node_key[1:]
    left, _, right = body.partition("_")
    try:
        return int(left), int(right)
    except ValueError as exc:
        raise ValueError(f"节点键里的坐标不是整数：{node_key!r}") from exc


def neighbors_hex(q, r):
    """返回一个格子的六个邻居坐标（顺序固定 = 确定性）。

    输入：
        q / r: 轴向坐标（int，bool 不算）。
    输出：
        六个 (q, r) 的元组，顺序与 DIRECTIONS 一致。
    异常：
        TypeError: q / r 不是 int 或是 bool。
    变量：
        dq / dr: 当前方向的偏移。
    """
    _require_int(q, "q")
    _require_int(r, "r")
    return tuple((q + dq, r + dr) for dq, dr in DIRECTIONS)


def distance_hex(a, b):
    """两个坐标之间的六边形距离（走几步能到）。

    输入：
        a / b: (q, r) 二元组。
    输出：
        非负整数（相邻为 1、自身为 0）。
    异常：
        TypeError: a / b 不是二元组，或里面不是 int。
    变量：
        dq / dr / ds: 两点的坐标差（第三个轴向分量 q + r + s = 0）。
    """
    aq, ar = _require_coord(a, "a")
    bq, br = _require_coord(b, "b")
    dq = aq - bq
    dr = ar - br
    return max(abs(dq), abs(dr), abs(-dq - dr))


def all_within_hex(radius):
    """返回以原点为中心、给定半径内的全部格子坐标。

    输入：
        radius: 半径（非负 int；半径 1 → 7 个格子）。
    输出：
        坐标元组，顺序按 r 从小到大、q 从小到大（固定顺序 = 确定性）。
    异常：
        TypeError: radius 不是 int 或是 bool；
        ValueError: radius 是负数。
    变量：
        q / r: 遍历时的坐标。
    """
    _require_int(radius, "radius")
    if radius < 0:
        raise ValueError(f"半径不能是负数：{radius}")
    result = []
    for r in range(-radius, radius + 1):
        low = max(-radius, -r - radius)
        high = min(radius, -r + radius)
        for q in range(low, high + 1):
            result.append((q, r))
    return tuple(result)


def adjacency_map_from_coords(coords):
    """按一组坐标算相邻关系表（只连组内存在的格子）。

    输入：
        coords: 坐标序列（(q, r) 二元组）。
    输出：
        {节点键: [相邻节点键, …]}；邻居顺序与 neighbors_hex 一致。
    异常：
        TypeError: 某个元素不是坐标二元组；
        ValueError: 坐标重复。
    变量：
        present / table / found / item: 中间结果。
    """
    present = set()
    for item in coords:
        coord = _require_coord(item, "coords 的元素")
        if coord in present:
            raise ValueError(f"坐标重复：{coord}")
        present.add(coord)

    table = {}
    for q, r in sorted(present, key=lambda pair: (pair[1], pair[0])):
        found = [key_q_r(nq, nr) for nq, nr in neighbors_hex(q, r) if (nq, nr) in present]
        table[key_q_r(q, r)] = found
    return table


def is_adjacent_in_list(neighbors, node_key):
    """判断某个节点键是否在相邻列表里（给规则用的小函数）。"""
    return node_key in (neighbors or ())


def _require_int(value, name):
    """要求一个值是 int（bool 不算）。

    输入：
        value: 待检查的值；
        name: 出错信息里用的名字。
    输出：
        无（通过检查就返回）。
    异常：
        TypeError: 不是 int，或者是 bool。
    变量：
        无。
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} 必须是 int，实际是 {type(value).__name__}")


def _require_coord(value, name):
    """要求一个值是 (int, int) 二元组。

    输入：
        value: 待检查的值；
        name: 出错信息里用的名字。
    输出：
        (q, r) 二元组。
    异常：
        TypeError: 不是元组 / 列表，或长度不是 2，或里面不是 int。
    变量：
        无。
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise TypeError(f"{name} 必须是 (q, r) 二元组，实际是 {type(value).__name__}")
    if len(value) != 2:
        raise TypeError(f"{name} 必须是 (q, r) 二元组，实际长度是 {len(value)}")
    _require_int(value[0], f"{name}[0]")
    _require_int(value[1], f"{name}[1]")
    return value[0], value[1]
