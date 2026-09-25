"""网格界面件（画面）：像素换算、六边形顶点、三种画法。

三种画法：方块格子（plain，快）、真六边形（hexagon，带格线）、
真六边形缩略（hexagon_lod，一格一层、不写文字，缩放很小时用）。

`to_pixel_hex_pointy` 放在这里而不是几何文件：画法要用它，而同一个文件里的函数才能互相调用
（脚本文件之间不能 import；几何在 hex_math.py、字符地图在 hex_text_map.py）。

这里的函数全是**纯函数**：不碰 State、不读时钟、不取随机；返回 core.ui 的 Layer（画面数据），
由外壳统一渲染。
"""

import math


def to_pixel_hex_pointy(q, r, size, center=(0.0, 0.0)):
    """把坐标换算成平面像素中心点（尖顶朝上的排法）。

    输入：
        q / r: 坐标（int）；
        size: 外接圆半径（像素，必须为正数）；
        center: 整张网格的中心点（像素）。
    输出：
        (x, y) 像素坐标；同一个 size 下相邻格子的距离相等。
    异常：
        TypeError: 坐标不是 int，或 size / center 不是数字；
        ValueError: size 不是正数。
    变量：
        x / y: 相对 center 的偏移。

    说明：
        这只是**一种**排法：Mod 的视图脚本可以把结果再平移、缩放，或者完全不用它。
    """
    _require_int(q, "q")
    _require_int(r, "r")
    if isinstance(size, bool) or not isinstance(size, (int, float)):
        raise TypeError(f"size 必须是数字，实际是 {type(size).__name__}")
    if size <= 0:
        raise ValueError(f"size 必须是正数，实际是 {size}")
    cx, cy = _require_number_pair(center, "center")
    x = size * math.sqrt(3.0) * (q + r / 2.0)
    y = size * 1.5 * r
    return (cx + x, cy + y)


def world_center(nodes, size):
    """地图在像素坐标里的中心（把镜头默认对准它，而不是对准左上角那一格）。

    输入：
        nodes: {节点键: {"q": 轴向, "r": 轴向}}；
        size: 一格的外接半径（像素）。
    输出：
        (x, y)；空地图给 (0.0, 0.0)。
    异常：
        TypeError / ValueError: 与 to_pixel_hex_pointy 相同（坐标不是 int、size 不是正数）。
    变量：
        xs / ys: 所有格子的像素坐标范围。
    """
    if not nodes:
        return 0.0, 0.0
    xs, ys = [], []
    for node in nodes.values():
        x, y = to_pixel_hex_pointy(node["q"], node["r"], size, (0.0, 0.0))
        xs.append(x)
        ys.append(y)
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def visible_nodes(nodes, size, viewport, width, height):
    """只挑"这一帧看得见"的格子（大地图必须裁剪：700 格 × 3 层会拖垮帧率）。

    判据是"这一格的外接矩形与镜头范围有没有重叠"——按点尖朝上的六边形算
    （半宽 = √3/2 × size、半高 = size），与 to_pixel_hex_pointy 同一种摆法。

    输入：
        nodes: 全部节点；size: 一格的外接半径（像素）；
        viewport: 当前视口（core.ui.Viewport，含屏幕 ↔ 世界换算）；
        width / height: 窗口尺寸。
    输出：
        {节点键: 节点}——镜头范围内的那一部分。
    异常：
        无。
    变量：
        left_top / right_bottom: 屏幕四角换算回世界坐标之后的可见范围；
        half_w / half_h: 一格在世界坐标里的半宽 / 半高。
    """
    left_top = viewport.to_world((0.0, 0.0))
    right_bottom = viewport.to_world((float(width), float(height)))
    half_w = 0.8660254 * size
    half_h = size

    visible = {}
    for key, node in nodes.items():
        x, y = to_pixel_hex_pointy(node["q"], node["r"], size, (0.0, 0.0))
        if (x + half_w >= left_top[0] and x - half_w <= right_bottom[0]
                and y + half_h >= left_top[1] and y - half_h <= right_bottom[1]):
            visible[key] = node
    return visible


def hexagon_points(q, r, size, center=(0.0, 0.0)):
    """算一个格子的六个顶点（尖顶朝上的六边形）。

    输入：
        q / r: 坐标（int）；
        size: 外接圆半径（像素，中心到顶点的距离）；
        center: 整张网格的中心点。
    输出：
        六个 (x, y) 顶点的元组，从 30° 开始、每 60° 一个（顺序固定 = 确定性）。
    异常：
        TypeError / ValueError: 与 to_pixel_hex_pointy 相同（坐标不是 int、size 不是正数）。
    变量：
        cx / cy: 格子中心；
        index / angle: 第几个顶点与它的角度。

    说明：
        尖顶朝上的排法下，外接圆半径是 size、宽是 √3·size、高是 2·size——
        所以相邻格子的**外接矩形是互相重叠的**，命中判定必须按"点在不在多边形内"算
        （引擎的 polygon 层就是这么做的）。

        要画"同一个中心、小一圈"的六边形（例如格线的内圈），别把这个函数的 size 改小：
        中心位置本身也由 size 决定（`to_pixel_hex_pointy` 按 size 换算像素），
        那样内圈会连位置一起缩小、与外圈不同心。用 `hexagon_points_at` 拿中心再缩。
    """
    cx, cy = to_pixel_hex_pointy(q, r, size, center)
    return hexagon_points_at(cx, cy, size)


def hexagon_points_at(cx, cy, radius):
    """以 (cx, cy) 为中心、外接圆半径 radius 的正六边形顶点（尖顶朝上）。

    输入：
        cx / cy: 中心点（像素）；
        radius: 外接圆半径（像素，正数）。
    输出：
        六个 (x, y) 顶点的元组，从 30° 开始、每 60° 一个（顺序固定 = 确定性）。
    异常：
        TypeError: 中心不是数字二元组，或 radius 不是数字；
        ValueError: radius 不是正数。
    变量：
        index / angle: 第几个顶点与它的角度。
    """
    x0, y0 = _require_number_pair((cx, cy), "中心")
    if isinstance(radius, bool) or not isinstance(radius, (int, float)):
        raise TypeError(f"radius 必须是数字，实际是 {type(radius).__name__}")
    if radius <= 0:
        raise ValueError(f"radius 必须是正数，实际是 {radius}")
    radius = float(radius)
    points = []
    for index in range(6):
        angle = math.radians(60.0 * index + 30.0)
        points.append((x0 + radius * math.cos(angle), y0 + radius * math.sin(angle)))
    return tuple(points)


def hex_grid_layers(
    *,
    nodes,
    size=52.0,
    center=(0.0, 0.0),
    items=None,
    empty_color=(44, 50, 62, 255),
    text_color=(240, 240, 240, 255),
    font_size=16,
    z=10,
):
    """把一组节点画成网格层（模块提供的"画法"，不是引擎机制）。

    调用方式（Mod 的视图脚本里）：

        make_grid = page_context.functions["pack_hex_grid.hex_grid.plain"]
        layers = make_grid(nodes=state["nodes"], size=52.0, center=(360, 340),
                           items={node_key: {"text": "...", "click": ClickResult(...)}})

    输入：
        nodes: {节点键: {"q":…, "r":…}}（Mod 的 State 里怎么放坐标都行，只要有 q / r）；
        size: 一个格子的外接圆半径（像素）；
        center: 整张网格的中心点（像素）；
        items: {节点键: {"text": 显示文字, "color": 底色, "click": ClickResult}}；
            缺省表示这个格子没有额外内容；
        empty_color / text_color / font_size / z: 外观缺省值（items 里可逐个覆盖）。
    输出：
        层元组（引擎的 View 直接用）。
    异常：
        KeyError: nodes 里的条目缺 q / r（Mod 数据问题，加载期就该发现）。
    变量：
        layers / node_key / coord / item / x / y / rect: 逐格计算的中间结果。

    约定（重要）：
        - 本函数**只画**，不知道玩法：每格显示什么文字、点了产生什么输入，由 Mod 通过
          `items` 传进来；
        - 它不读 State，只吃参数 → 纯函数，任何 Mod 都能复用；
        - 它画的是一块**矩形**（"方块格子"），要真六边形用同一个能力的另一个变体
          `pack_hex_grid.hex_grid.hexagon`。
    """
    from core.ui import Layer

    size = float(size)
    width = size * 1.72
    height = size
    items = items or {}
    layers = []
    for node_key in sorted(nodes):
        coord = nodes[node_key]
        x, y = to_pixel_hex_pointy(coord["q"], coord["r"], size, center)
        rect = (x - width / 2, y - height / 2, width, height)
        item = items.get(node_key, {})
        layers.append(
            Layer(
                id=f"grid:{node_key}",
                rect=rect,
                kind="text",
                text=item.get("text", node_key),
                font_size=item.get("font_size", font_size),
                color=item.get("color", empty_color),
                text_color=item.get("text_color", text_color),
                click=item.get("click"),
                z=item.get("z", z),
            )
        )
    return tuple(layers)


def hex_grid_hexagon_layers(
    *,
    nodes,
    size=52.0,
    center=(0.0, 0.0),
    items=None,
    fill_color=(44, 50, 62, 255),
    outline_color=(128, 138, 158, 255),
    outline_width=3.0,
    text_color=(240, 240, 240, 255),
    font_size=16,
    z=10,
):
    """把一组节点画成**真六边形**网格（模块提供的"画法"之一）。

    调用方式（Mod 的视图脚本里）：

        make_grid = page_context.functions["pack_hex_grid.hex_grid.hexagon"]
        layers = make_grid(nodes=state["nodes"], size=52.0, center=(360, 340),
                           items={node_key: {"text": "...", "click": ClickResult(...)}})

    输入：
        nodes: {节点键: {"q":…, "r":…}}（Mod 的 State 里怎么放坐标都行，只要有 q / r）；
        size: 一个格子的外接圆半径（像素，中心到顶点）；
        center: 整张网格的中心点（像素）；
        items: {节点键: {"text": 显示文字, "color": 填充色, "outline_color": 描边色,
            "click": ClickResult}}；缺省表示这个格子没有额外内容；
        fill_color / outline_color / outline_width / text_color / font_size / z: 外观缺省值。
    输出：
        层元组：每格两到三层（格子多边形、内圈填充多边形、文字）。引擎的 View 直接用。
    异常：
        KeyError: nodes 里的条目缺 q / r（Mod 数据问题，加载期就该发现）。
    变量：
        layers / node_key / coord / item / points / box: 逐格计算的中间结果。

    约定（重要）：
        - **描边靠"铺两层多边形"实现**：外圈是全尺寸的格子（描边色），内圈缩进
          outline_width（填充色）——外圈露出的那一圈就是格线。这样相邻格子之间
          也有一圈边线（如果反过来把描边画在尺寸之外，内侧那一半会被邻居的填充盖掉）。
          引擎只给"画多边形"这一个原语，组合出观感是内容侧的事（P1）；
        - **点击挂在外圈上**：它是全尺寸的六边形，点格子边缘也算点到这个格子；
        - **文字层不吃点击**：文字层是矩形，而相邻六边形的外接矩形互相重叠，
          让它吃点击就会点到隔壁的格子去；
        - 命中判定由引擎按"点在不在多边形内"算（射线法），所以六个斜边都能点准；
        - 它不读 State，只吃参数 → 纯函数，任何 Mod 都能复用。
    """
    from core.ui import Layer

    size = float(size)
    border = max(0.0, float(outline_width))
    items = items or {}
    layers = []
    for node_key in sorted(nodes):
        coord = nodes[node_key]
        item = items.get(node_key, {})
        # 中心只算一次：外圈与内圈都必须以它为心（改 size 重算会把位置也挪走）。
        cx, cy = to_pixel_hex_pointy(coord["q"], coord["r"], size, center)
        points = hexagon_points_at(cx, cy, size)
        box = _bbox(points)
        layer_z = item.get("z", z)
        fill = item.get("color", fill_color)
        # 一格 = **一个**多边形：填充 + 描边由引擎的同一条原语一次画完。
        # （以前用"大一圈的实心多边形垫在下面"假装描边，结果高亮框会被邻居的填充盖住。）
        layers.append(
            Layer(
                id=f"grid:{node_key}",
                kind="polygon",
                points=points,
                color=fill,
                outline_color=item.get("outline_color", outline_color) if border > 0 else None,
                outline_width=border,
                click=item.get("click"),
                z=layer_z,
            )
        )
        layers.append(
            Layer(
                id=f"grid:{node_key}:text",
                rect=box,
                kind="text",
                text=item.get("text", node_key),
                font_size=item.get("font_size", font_size),
                color=(0, 0, 0, 0),  # 不吃底色：底下就是模块画好的六边形
                text_color=item.get("text_color", text_color),
                z=layer_z + 1,
            )
        )
    return tuple(layers)


def hex_grid_hexagon_lod_layers(
    *,
    nodes,
    size=52.0,
    center=(0.0, 0.0),
    items=None,
    fill_color=(44, 50, 62, 255),
    z=10,
):
    """缩略模式：一格只画**一个**多边形（不画格线、不写文字）。

    什么时候用它：**缩小看全图**的时候。整图有几百格可见，全画法（格线 + 填充 + 文字）
    是 3 层/格，缩略模式是 1 层/格——层数直接少三分之二（模块与 Mod 的实测见
    `plans/新架构细节/04_示例Mod/示例Mod.md` 的 G-10）。

    输入：
        nodes: {节点键: {"q":…, "r":…}}；
        size: 一格的外接圆半径（像素）；
        center: 网格中心；
        items: {节点键: {"color": 填充色, "click": ClickResult}}（**文字与描边在这里不用**）；
        fill_color / z: 缺省外观。
    输出：
        层元组（每格一层）。
    异常：
        KeyError: nodes 里的条目缺 q / r。
    变量：
        layers / node_key / item / points。
    """
    from core.ui import Layer

    size = float(size)
    items = items or {}
    layers = []
    for node_key in sorted(nodes):
        coord = nodes[node_key]
        item = items.get(node_key, {})
        layers.append(
            Layer(
                id=f"grid:{node_key}",
                kind="polygon",
                points=hexagon_points(coord["q"], coord["r"], size, center),
                color=item.get("color", fill_color),
                click=item.get("click"),
                z=item.get("z", z),
            )
        )
    return tuple(layers)


def _bbox(points):
    """算一组顶点的外接矩形 (x, y, 宽, 高)。

    输入：
        points: 顶点序列。
    输出：
        (x, y, 宽, 高)。
    异常：
        无。
    变量：
        xs / ys: 全部顶点的横坐标与纵坐标。
    """
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


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


def _require_number_pair(value, name):
    """要求一个值是 (数字, 数字) 二元组（像素中心的校验用）。

    输入：
        value: 待检查的值；
        name: 出错信息里用的名字。
    输出：
        (x, y) 二元组。
    异常：
        TypeError: 不是二元组，或里面不是数字（bool 不算）。
    变量：
        无。
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise TypeError(f"{name} 必须是 (x, y) 二元组，实际是 {type(value).__name__}")
    if len(value) != 2:
        raise TypeError(f"{name} 必须是 (x, y) 二元组，实际长度是 {len(value)}")
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise TypeError(f"{name}[{index}] 必须是数字，实际是 {type(item).__name__}")
    return float(value[0]), float(value[1])
