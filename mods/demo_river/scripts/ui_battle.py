"""洛川平原视图（Mod 侧）：M1 阶段先把整张地图画出来。

契约（见 core.ui.PageCallable）：
    build_view(page_context) -> View

说明：
    - 格子怎么画：模块 `pack_hex_grid.hex_grid.hexagon`（真六边形界面件）；
    - 怎么把 729 格（27×27）放进一个窗口：`_fit` 按所有格子的像素范围反算"一格的半径"，
      这属于排版，留在 Mod；
    - 相机（平移 / 缩放）与操作界面是 M1 的下一步，这里先给一张"整图"。
"""

from math import cos, pi, sin

from core.ui import ClickResult, Layer, View, Viewport, WheelResult

# 世界坐标里的"一格半径"（固定值；缩放交给视口，不重算坐标）。
BASE_SIZE = 26.0
# 兵牌（单位标记）的半径比例：只盖住格子中心，四周留出地形色。
UNIT_COUNTER_SCALE = 0.62

# 缩到多小才换"缩略模式"（一格一层、不画格线也不写文字）。调到 0.5 之前都按全画法画。
LOD_BELOW_ZOOM = 0.5

# 右侧信息栏：按窗口比例取宽（带上下限；地图至少留 MAP_MIN_WIDTH）。
PANEL_WIDTH_RATIO = 0.28
PANEL_MIN_WIDTH = 220
PANEL_MAX_WIDTH = 360
MAP_MIN_WIDTH = 360
PANEL_PAD = 8
LOG_MIN_HEIGHT = 160
TABLE_RATIO = 0.55

# 相机（界面状态）在 Context 里的键：与模块 pack_camera 的 state_prefix 参数一致。
KEY_PAN_X = "demo_river.camera.x"
KEY_PAN_Y = "demo_river.camera.y"
KEY_ZOOM = "demo_river.camera.zoom"
KEY_SELECTED = "demo_river.selected"
KEY_PENDING = "demo_river.pending"
KEY_ATTACKERS = "demo_river.attackers"
KEY_LOG_SCROLL = "demo_river.log.scroll"
KEY_RULES_OPEN = "demo_river.rules_open"

# 配色：底色 / 地形 / 双方单位。
BACKGROUND = (18, 20, 26, 255)
PLAIN_FILL = (86, 122, 74, 255)       # 平原：绿
HILL_FILL = (132, 134, 136, 255)      # 丘陵：灰
RIVER_FILL = (98, 156, 196, 255)      # 河水：浅蓝
FORD_FILL = (146, 104, 64, 255)       # 渡口：木棕
VILLAGE_FILL = (182, 152, 112, 255)   # 村庄：浅棕
VICTORY_FILL = (108, 92, 52, 255)
NORTH_FILL = (56, 78, 116, 255)
SOUTH_FILL = (104, 62, 62, 255)
# 行动力耗尽（只有移动阶段、当前控制方）：同阵营的深一档。
NORTH_SPENT_FILL = (38, 52, 78, 255)
SOUTH_SPENT_FILL = (70, 42, 42, 255)

PLAIN_LINE = (86, 94, 110, 255)
HILL_LINE = (120, 120, 90, 255)
RIVER_LINE = (60, 90, 130, 255)
FORD_LINE = (120, 170, 210, 255)
VILLAGE_LINE = (150, 130, 90, 255)
VICTORY_LINE = (230, 200, 100, 255)
# 胜利点标记：金色五角星（独立于格子填充，单位站在上面也看得见）。
VICTORY_STAR_OFFSET = 10.0
VICTORY_STAR_OUTER = 7.5
VICTORY_STAR_INNER = 3.0

# 操作高亮：自己的单位 / 可达落点 / 待确认目标 / 路径 / 选中
OWN_LINE = (132, 168, 232, 255)
MOVE_LINE = (124, 206, 140, 255)
PENDING_LINE = (250, 208, 88, 255)
PATH_LINE = (240, 190, 120, 255)
SELECT_LINE = (250, 208, 88, 255)
ATTACK_LINE = (232, 112, 108, 255)   # 攻击阶段：可以打的目标
ATTACKER_LINE = (250, 208, 88, 255)  # 攻击阶段：已加入参战名单的单位
ADVANCE_LINE = (150, 220, 250, 255)  # 攻击阶段：这一场打完，可以挺进过去的参战单位
DISORDER_COLOR = (240, 70, 70, 255)  # 混乱单位角标：红色 ×（右上角）

TERRAIN_FILL = {
    "plain": PLAIN_FILL,
    "hill": HILL_FILL,
    "river": RIVER_FILL,
    "ford": FORD_FILL,
    "village": VILLAGE_FILL,
    "victory": VICTORY_FILL,
}
TERRAIN_LINE = {
    "plain": PLAIN_LINE,
    "hill": HILL_LINE,
    "river": RIVER_LINE,
    "ford": FORD_LINE,
    "village": VILLAGE_LINE,
    "victory": VICTORY_LINE,
}


def build_view(page_context):
    """把整张地图画出来（M1：还没有操作界面）。

    输入：
        page_context: 引擎给的只读上下文（state / size / context / page_id / functions）。
    输出：
        View。
    异常：
        无（State 缺字段时按"没这回事"处理）。
    变量：
        state / size: 局面与窗口尺寸；
        size_per_hex / center: 排版算出来的格子半径与网格中心；
        items / layers: 逐格外观与最终画面。
    """
    state = page_context.state
    width, height = page_context.size
    nodes = state.get("nodes", {})
    units = state.get("units", {})

    panel_w = _panel_width(width)
    map_w = max(1.0, float(width - panel_w))
    panel_rect = (map_w, 0.0, float(panel_w), float(height))
    camera = _camera(page_context.context)

    # 按缩放挑画法：放大时用"全画法"（格线 + 文字），缩小时换"缩略模式"（一格一层）。
    variant = "hexagon" if camera[2] >= LOD_BELOW_ZOOM else "hexagon_lod"
    make_grid = page_context.functions[f"pack_hex_grid.hex_grid.{variant}"]
    # 取景中心与视野裁剪都是通用件，住在模块里（换张地图、换个 Mod 都能用）。
    center_x, center_y = page_context.functions["pack_hex_grid.view_center.bounds"](
        nodes, BASE_SIZE)
    viewport = Viewport(offset_x=map_w / 2.0 + camera[0] - center_x * camera[2],
                        offset_y=height / 2.0 + camera[1] - center_y * camera[2],
                        zoom=camera[2])
    visible = page_context.functions["pack_hex_grid.cull.visible"](
        nodes, BASE_SIZE, viewport, map_w, height)
    world_center = (0.0, 0.0)

    enemy_zoc = _enemy_zoc(page_context, units)
    plan = _plan_cells(page_context, visible, nodes, units, enemy_zoc)
    items = {}
    for key, node in visible.items():
        occupant = _unit_at(units, key)
        unit = units.get(occupant) if occupant else None
        entry = plan.get(key, {})
        items[key] = {
            "text": _node_text(unit),
            "color": entry.get("color", _terrain_fill(node)),
            "outline_color": entry.get("line", TERRAIN_LINE.get(node.get("terrain"), PLAIN_LINE)),
            "text_color": (240, 238, 232, 255),
            "font_size": 11,
            "click": entry.get("click"),
        }

    grid_args = {"nodes": visible, "size": BASE_SIZE, "center": world_center, "items": items}
    if variant == "hexagon":     # 全画法要格线宽度；缩略模式没有格线，也就没有这个参数
        grid_args["outline_width"] = 0.75   # 原来 1.5 的一半；屏幕整数像素由适配器取整
    layers = list(make_grid(**grid_args))
    layers.extend(_unit_counters(page_context, visible, units, state, world_center))
    marker = _victory_marker(page_context, visible, world_center)
    if marker is not None:
        layers.append(marker)
    layers.extend(_disorder_markers(page_context, visible, units, world_center))
    layers.extend(_sidebar_layers(page_context, panel_rect, nodes))
    layers.append(Layer(
        id="hud", rect=(12, 46, max(80.0, map_w - 24.0), 44), kind="text",
        text=_hud_text(state, nodes, units, camera, len(items)), align="left", font_size=14,
        color=(30, 34, 44, 235), text_color=(232, 234, 240, 255), z=50, fixed=True,
    ))
    layers.extend(_buttons(state, map_w, height))
    layers.extend(_modal_layers(page_context, width, height))
    if state.get("game_over"):
        layers.append(_banner(state, width, height))
    return View(layers=tuple(layers), background=BACKGROUND, viewport=viewport)


def _banner(state, width, height):
    """结束横幅：谁赢了 + 接下来能按什么（fixed，贴在屏幕中央）。"""
    winner = "攻方（青隼）" if state.get("winner") == "north" else "守方（柳浦）"
    return Layer(
        id="banner:winner",
        rect=(width / 2 - 240, height / 2 - 60, 480, 120),
        kind="text",
        text=f"战斗结束：{winner}获胜\n\n按 Esc 返回首页 · 按 S 存档",
        font_size=24,
        color=(26, 30, 40, 245),
        text_color=(255, 226, 150, 255),
        z=70, fixed=True,
    )


def _panel_width(width):
    """右侧信息栏宽度：按窗口比例，带上下限，并给地图留最小宽度。"""
    by_ratio = int(round(width * PANEL_WIDTH_RATIO))
    panel = max(PANEL_MIN_WIDTH, min(PANEL_MAX_WIDTH, by_ratio))
    if width - panel < MAP_MIN_WIDTH:
        panel = max(0, width - MAP_MIN_WIDTH)
    return min(panel, width)


def _panel_rects(panel_rect):
    """把右栏切成 地形表 / CRT 表 / 战报栏 三块（小窗口缩表格，战报留最小高度）。"""
    x, y, w, h = panel_rect
    inner_x = x + PANEL_PAD
    inner_y = y + PANEL_PAD
    inner_w = max(1.0, w - 2 * PANEL_PAD)
    log_min = max(80.0, min(float(LOG_MIN_HEIGHT), h * 0.35))
    table_h = max(60.0, min(h * TABLE_RATIO, h - log_min - 3 * PANEL_PAD))
    terrain_h = max(48.0, table_h * 0.42)
    crt_h = max(48.0, table_h - terrain_h - PANEL_PAD)
    terrain_rect = (inner_x, inner_y, inner_w, terrain_h)
    crt_rect = (inner_x, inner_y + terrain_h + PANEL_PAD, inner_w, crt_h)
    log_y = inner_y + terrain_h + crt_h + 2 * PANEL_PAD
    log_h = max(60.0, h - (terrain_h + crt_h + 3 * PANEL_PAD))
    return terrain_rect, crt_rect, (inner_x, log_y, inner_w, log_h)


def _table_layers(title, headers, rows, rect, *, first_ratio, z):
    """把一张小表画成"每格一个 text 层"的网格（字体不等宽也不会错列）。"""
    x, y, w, h = rect
    title_h = 18.0
    line_count = len(rows) + 1
    row_h = max(10.0, (h - title_h) / max(1, line_count))
    font = int(max(9, min(14, row_h * 0.55)))
    layers = [
        Layer(id=f"table:{title}:bg", rect=rect, kind="rect", color=(28, 33, 42, 255),
              z=z - 1, fixed=True),
        Layer(id=f"table:{title}:title", rect=(x + 4, y, max(1.0, w - 8), title_h),
              kind="text", text=title, font_size=max(10, font + 2), align="left",
              color=(0, 0, 0, 0), text_color=(255, 226, 150, 255), z=z, fixed=True),
    ]
    col_count = max(1, len(headers))
    first_w = w * first_ratio
    other_w = (w - first_w) / max(1, col_count - 1)

    def column_x(index):
        """第 index 列的左边界。"""
        return x + (0.0 if index == 0 else first_w + (index - 1) * other_w)

    def column_w(index):
        """第 index 列的宽度。"""
        return first_w if index == 0 else other_w

    all_rows = (tuple(headers),) + tuple(tuple(row) for row in rows)
    for row_index, row in enumerate(all_rows):
        for col_index in range(col_count):
            text = str(row[col_index]) if col_index < len(row) else ""
            cell_w = column_w(col_index)
            cell_font = font
            estimated = _text_width(text, font)
            if estimated > cell_w - 4:
                cell_font = max(8, int(font * max(0.1, (cell_w - 4) / max(1.0, estimated))))
            layers.append(Layer(
                id=f"table:{title}:{row_index}:{col_index}",
                rect=(column_x(col_index), y + title_h + row_index * row_h,
                      cell_w, row_h),
                kind="text", text=text, font_size=cell_font, align="center",
                color=(0, 0, 0, 0),
                text_color=(255, 226, 150, 255) if row_index == 0 else (226, 230, 238, 255),
                z=z + 1, fixed=True,
            ))
    return layers


def _terrain_rows(nodes):
    """地形表：直接从 State 的节点归类（地形数据改了表就跟着变）。"""
    order = ("plain", "hill", "river", "ford", "village", "victory")
    seen = {}
    for node in nodes.values():
        terrain = node.get("terrain")
        if terrain and terrain not in seen:
            seen[terrain] = node
    rows = []
    for terrain in order:
        node = seen.get(terrain)
        if node is None:
            continue
        passable = bool(node.get("passable", True))
        move = "—" if not passable else str(node.get("move_cost", "?"))
        rows.append((str(node.get("terrain_name", terrain)), move,
                     f"×{node.get('defense_mult', 1)}", "是" if passable else "否"))
    return tuple(rows)


def _crt_rows(functions):
    """CRT 表：从模块的只读表结构来（不在 UI 里抄一份）。"""
    table = functions["pack_combat_crt.crt.table"]()
    bands = tuple(table["bands"])
    headers = ("骰",) + bands
    rows = tuple(
        (str(row["roll"]),) + tuple(row["results"][band] for band in bands)
        for row in table["rows"]
    )
    return headers, rows


def _char_px(char, font_size):
    """单个字符的粗略像素宽：CJK / 全角约一个字宽，ASCII 约 0.55。"""
    return font_size if ord(char) > 0x2E7F else font_size * 0.55


def _text_width(text, font_size):
    """整段文字的粗略像素宽（折行、表格缩字号共用）。"""
    return sum(_char_px(char, font_size) for char in str(text))


def _wrap_text(text, max_px, font_size=14.0):
    """按像素估算折行：中文约一个字号宽，ASCII 约 0.55 个字号宽。"""
    lines = []
    limit = max(1.0, float(max_px))
    size = max(1.0, float(font_size))
    for raw in str(text).split("\n"):
        current = ""
        width = 0.0
        for char in raw:
            char_px = _char_px(char, size)
            if current and width + char_px > limit:
                lines.append(current)
                current, width = "", 0.0
            current += char
            width += char_px
        lines.append(current)
    return lines


def _log_layers(page_context, rect):
    """战报栏：固定高度、滚轮上下翻；滚动位置放在 Context，不存档。"""
    state = page_context.state
    context = page_context.context
    x, y, w, h = rect
    font = 12
    line_h = font + 4
    title_h = 20.0
    lines = []
    for text in state.get("log") or ():
        lines.extend(_wrap_text(text, max_px=w - 30.0, font_size=font))
    if not lines:
        lines = ["（还没有战报）"]
    visible_n = max(1, int((h - title_h - 6) / line_h))
    max_offset = max(0, len(lines) - visible_n)
    offset = int(max(0.0, min(float(context.get(KEY_LOG_SCROLL, 0.0)), float(max_offset))))
    end = len(lines) - offset
    start = max(0, end - visible_n)
    window = lines[start:end]

    layers = [
        Layer(id="log:bg", rect=rect, kind="rect", color=(18, 22, 30, 255),
              wheel=WheelResult(kind="scroll_log"), z=36, fixed=True),
        Layer(id="log:title", rect=(x + 6, y, max(1.0, w - 12), title_h),
              kind="text", text="战报", font_size=14, align="left",
              color=(0, 0, 0, 0), text_color=(255, 226, 150, 255), z=38, fixed=True),
    ]
    for index, line in enumerate(window):
        layers.append(Layer(
            id=f"log:line:{index}",
            rect=(x + 6, y + title_h + index * line_h, max(1.0, w - 24), line_h),
            kind="text", text=line, font_size=font, align="left",
            color=(0, 0, 0, 0), text_color=(226, 230, 238, 255), z=38, fixed=True,
        ))
    if max_offset > 0:
        track = (x + w - 8, y + title_h, 4, max(4.0, h - title_h - 4))
        layers.append(Layer(id="log:track", rect=track, kind="rect",
                            color=(44, 50, 62, 255), z=37, fixed=True))
        thumb_h = max(12.0, track[3] * visible_n / max(1, len(lines)))
        thumb_y = track[1] + (track[3] - thumb_h) * (1.0 - offset / max_offset)
        layers.append(Layer(id="log:thumb", rect=(track[0], thumb_y, track[2], thumb_h),
                            kind="rect", color=(140, 150, 170, 255), z=38, fixed=True))
    return layers


def _sidebar_layers(page_context, panel_rect, nodes):
    """右侧整栏：上地形表 + CRT 表，下战报栏。"""
    terrain_rect, crt_rect, log_rect = _panel_rects(panel_rect)
    layers = [Layer(id="sidebar:bg", rect=panel_rect, kind="rect",
                    color=(22, 26, 34, 255), blocks_pointer=True, z=30, fixed=True)]
    layers.extend(_table_layers("地形表", ("地形", "移动", "防御", "通行"),
                                _terrain_rows(nodes), terrain_rect,
                                first_ratio=0.30, z=32))
    headers, rows = _crt_rows(page_context.functions)
    layers.extend(_table_layers("CRT", headers, rows, crt_rect,
                                first_ratio=0.14, z=32))
    layers.extend(_log_layers(page_context, log_rect))
    return layers


def _modal_layers(page_context, width, height):
    """开局介绍 / 规则弹窗：隐形全屏阻挡层 + 居中面板 + 确认按钮。"""
    state = page_context.state
    context = page_context.context
    # 老存档没有 /intro_seen：当成"看过"，读档不弹；新局 init 明确写 false。
    intro_seen = state.get("intro_seen", True)
    rules_open = bool(context.get(KEY_RULES_OPEN, False))
    if intro_seen and not rules_open:
        return []
    intro = state.get("intro") or {
        "title": "规则",
        "confirm": "关闭",
        "sections": [{"heading": "", "text": "这份存档创建时还没有规则说明。"}],
    }
    panel_w = min(float(width) - 40.0, 760.0)
    panel_h = min(float(height) - 40.0, 560.0)
    px = (width - panel_w) / 2.0
    py = (height - panel_h) / 2.0
    layers = [
        Layer(id="modal:blocker", rect=(0.0, 0.0, float(width), float(height)),
              kind="rect", color=(0, 0, 0, 0), visible=False, blocks_pointer=True,
              escape=ClickResult(kind="close_rules"), z=90, fixed=True),
        Layer(id="modal:panel", rect=(px, py, panel_w, panel_h), kind="rect",
              color=(24, 28, 36, 255), z=91, fixed=True),
        Layer(id="modal:title", rect=(px + 20, py + 12, max(1.0, panel_w - 40), 30),
              kind="text", text=str(intro.get("title", "规则")), font_size=20, align="left",
              color=(0, 0, 0, 0), text_color=(255, 226, 150, 255), z=92, fixed=True),
    ]
    sections = tuple(intro.get("sections", ()))
    content_px = max(40.0, panel_w - 60.0)
    body_top = py + 52.0
    body_h = max(40.0, panel_h - 52.0 - 60.0)

    def build_lines(font_size):
        """按给定字号折行；段落之间留一个空行。"""
        result = []
        for section in sections:
            heading = str(section.get("heading", "")).strip()
            if heading:
                result.append(f"【{heading}】")
            result.extend(_wrap_text(section.get("text", ""), content_px, font_size))
            result.append("")
        while result and not result[-1]:
            result.pop()
        return result

    font = 15
    lines = build_lines(font)
    for _ in range(3):       # 行数超了就把字号收一档、再折一次；最多迭代三次
        line_h = font + 5
        max_lines = max(1, int(body_h / line_h))
        if len(lines) <= max_lines:
            break
        smaller = max(11, int(font * 0.92))
        if smaller == font:
            break
        font = smaller
        lines = build_lines(font)
    line_h = font + 5
    max_lines = max(1, int(body_h / line_h))
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + ["……"]
    for index, line in enumerate(lines):
        layers.append(Layer(
            id=f"modal:line:{index}",
            rect=(px + 24, body_top + index * line_h, max(1.0, panel_w - 48), line_h),
            kind="text", text=line, font_size=font, align="left",
            color=(0, 0, 0, 0), text_color=(224, 228, 236, 255), z=92, fixed=True,
        ))
    layers.append(Layer(
        id="modal:confirm",
        rect=(px + panel_w / 2.0 - 90, py + panel_h - 46, 180, 34),
        kind="text", text=str(intro.get("confirm", "确认")), font_size=16,
        color=(70, 96, 140, 255), text_color=(245, 245, 245, 255),
        click=ClickResult(kind="close_rules"), z=93, fixed=True,
    ))
    return layers


def _buttons(state, width, height):
    """地图区右下角：一个动态主按钮 + 常驻「规则」按钮（fixed，不跟视口动）。

    按钮的字与点击结果跟着"移动 → 声明 → 结算 → 结束回合"这条进度走：
        移动阶段                 → 进入攻击阶段
        攻击阶段·有声明·没结算  → 开始结算
        攻击阶段·结算中·剩 N 场 → 下一场
        攻击阶段·没有未结算战斗 → 结束回合（交给对方）
    """
    def button_rect(offset_y):
        """主按钮 / 规则按钮共用的矩形（贴在地图区右下角）。"""
        button_w = min(288.0, max(120.0, width - 20.0))
        x = max(8.0, width - button_w - 12.0)
        return (x, max(8.0, height - offset_y), button_w, 40.0)

    if state.get("game_over"):
        return [Layer(id="button:game_over", rect=button_rect(80),
                      kind="text", text="战斗已经结束", font_size=17,
                      color=(52, 56, 66, 255), text_color=(235, 235, 235, 255),
                      z=60, fixed=True)]
    stage = state.get("stage", "move")
    settling = bool(state.get("settling"))
    pending = len(state.get("battles") or ())
    if stage == "move":
        button_id, text, kind = "button:to_attack", "进入攻击阶段", "to_attack"
        color = (70, 110, 96, 255)
    elif settling and pending:
        button_id, text, kind = "button:resolve_next", f"下一场（剩 {pending} 场）", "resolve_next"
        color = (96, 84, 132, 255)
    elif pending:
        button_id, text, kind = "button:begin_settle", f"开始结算（{pending} 场）", "begin_settle"
        color = (120, 88, 120, 255)
    else:
        button_id, text, kind = "button:pass_control", "结束回合（交给对方）", "pass_control"
        color = (70, 96, 140, 255)
    return [Layer(id=button_id, rect=button_rect(80),
                  kind="text", text=text, font_size=17,
                  color=color, text_color=(245, 245, 245, 255),
                  click=ClickResult(kind=kind, data={}), z=60, fixed=True)]


def _enemy_zoc(page_context, units):
    """算出"当前控制方的敌人"控制着哪些格子（混乱单位不产生控制区）。"""
    functions = page_context.functions
    state = page_context.state
    disordered = [key for key, value in units.items() if value.get("disordered")]
    table = functions["pack_path_hex.zoc.from_units"](
        units, state.get("adjacency", {}), disordered)
    side = state.get("control_side", "")
    enemy = "south" if side == "north" else "north"
    return set(table.get(enemy, ()))


def _plan_cells(page_context, visible, nodes, units, enemy_zoc):
    """算每格的底色、描边与点击结果：移动两步走；攻击先声明、再逐场结算。

    可达区域用**和移动服务同一套模块函数**算，保证"画出来的"和"能走的"一致。
    敌方控制区先铺一层实色浅红，后面的高亮只改描边/点击，不会把底色冲掉。
    """
    context = page_context.context
    state = page_context.state
    if state.get("game_over"):
        return {}                     # 打完了：整张图都不再吃点击

    if state.get("stage") == "attack":
        return _attack_cells(page_context, units)

    selected = context.get(KEY_SELECTED, "")
    pending = context.get(KEY_PENDING, "")
    unit = units.get(selected) if selected else None
    if not unit or unit.get("destroyed") or unit.get("disordered") or unit.get("at") == "":
        return _advance_cells(state, units, _click_only(units, state))

    functions = page_context.functions
    adjacency = state.get("adjacency", {})
    fords = list(state.get("fords") or ())
    # 与移动服务同一个函数：视图画出来的"能走到哪"就是真能走到的（P5，只在模块里写一遍）。
    enter_cost, can_pass, can_stop = functions["pack_path_hex.levels.from_state"](
        nodes, units, selected)
    result = functions["pack_path_hex.reachable.dijkstra"](
        start=unit["at"], budget=unit.get("move_left", 0), neighbors=adjacency,
        enter_cost=enter_cost, can_pass=can_pass, can_stop=can_stop,
        extra_moves=fords, zoc=sorted(enemy_zoc), stop_on_enter_zoc=True,
        must_leave_zoc=bool(unit.get("zoc_start")),
    )

    plan = _click_only(units, state)
    for cell in result["costs"]:
        if cell == unit["at"]:
            continue
        if cell == pending:
            _put(plan, cell, line=PENDING_LINE,
                 click=ClickResult(kind="move", data={"unit": selected, "to": cell}))
        else:
            _put(plan, cell, line=MOVE_LINE,
                 click=ClickResult(kind="preview", data={"to": cell}))
    if pending:
        path = functions["pack_path_hex.path.previous"](result["previous"], unit["at"], pending)
        for cell in path:                      # 路径高亮（含终点）
            _put(plan, cell, line=PATH_LINE)
    _put(plan, unit["at"], line=SELECT_LINE)
    return plan


def _attack_cells(page_context, units):
    """攻击阶段：点己方单位加入 / 移出参战名单，点相邻敌军声明一场。

    结算开始后（/settling 为真）不再给"加入名单 / 声明"的点击，只保留战后挺进；
    把关与规则层一致：界面不展示，规则再兜底拒绝。
    """
    context = page_context.context
    state = page_context.state
    plan = {}
    if state.get("settling"):
        return _advance_cells(state, units, plan)

    side = state.get("control_side", "")
    # 名单里只认"还能打"的单位：已经打过 / 被消灭 / 不在图上的会被丢掉。
    # （声明成功后服务会清空名单，这里是第二道防线——名单是界面状态，可能残留旧值。）
    attackers = [key for key in (context.get(KEY_ATTACKERS, []) or ())
                 if key in units and not units[key].get("fought")
                 and not units[key].get("destroyed") and units[key].get("at")]
    for key, other_unit in units.items():
        cell = other_unit.get("at", "")
        if (cell and not other_unit.get("destroyed") and other_unit.get("side") == side
                and not other_unit.get("fought") and not other_unit.get("disordered")):
            _put(plan, cell, line=ATTACKER_LINE if key in attackers else OWN_LINE,
                 click=ClickResult(kind="toggle_attacker", data={"unit": key}))
    targets = set()
    adjacency = state.get("adjacency", {})
    for key in attackers:
        targets.update(adjacency.get(units[key].get("at", ""), ()))
    for cell in targets:
        occupant = _unit_at(units, cell)
        other = units.get(occupant) if occupant else None
        if other and other.get("side") != side and attackers:
            _put(plan, cell, line=ATTACK_LINE,
                 click=ClickResult(kind="declare_battle",
                                   data={"units": list(attackers), "target": occupant}))
    return _advance_cells(state, units, plan)


def _advance_cells(state, units, plan):
    """战后挺进：上一场结算空出来的格子，让参战单位点自己走进去（可选，不点也能继续）。

    依据是结算写在 /battle_report 里的 vacated——挺进成功 / 用过之后它会被清掉，
    所以这个点击只在"刚好打完一场、有格子空出来"的时候出现。
    """
    report = state.get("battle_report") or {}
    if not report.get("vacated"):
        return plan
    for key in report.get("units") or ():
        unit = units.get(key) or {}
        cell = unit.get("at", "")
        if cell and not unit.get("destroyed"):
            _put(plan, cell, line=ADVANCE_LINE,
                 click=ClickResult(kind="advance", data={"unit": key}))
    return plan


def _click_only(units, state):
    """移动阶段没选中单位时的点击结果：点自己的单位 = 选中；其余格子不产生点击。"""
    plan = {}
    side = state.get("control_side", "")
    for cell, unit in ((u.get("at"), u) for u in units.values()):
        if (not cell or unit.get("destroyed") or unit.get("side") != side
                or unit.get("disordered")):
            continue
        _put(plan, cell, line=OWN_LINE,
             click=ClickResult(kind="select", data={"unit": unit["id"]}))
    return plan


def _put(plan, cell, *, line=None, click=None):
    """往格子计划里合并一项：只覆盖给到的字段（描边 / 点击），不碰格子底色。"""
    entry = plan.setdefault(cell, {})
    if line is not None:
        entry["line"] = line
    if click is not None:
        entry["click"] = click


def _camera(context):
    """从界面状态里读相机（没有就给缺省：不缩放、不平移）。"""
    return (_number(context.get(KEY_PAN_X, 0.0)),
            _number(context.get(KEY_PAN_Y, 0.0)),
            max(0.05, _number(context.get(KEY_ZOOM, 1.0))))


def _unit_at(units, node_key):
    """找停在这一格上的单位（还活着的才算）。"""
    for unit_id in sorted(units):
        unit = units[unit_id]
        if unit.get("at") == node_key and not unit.get("destroyed"):
            return unit_id
    return ""


def _node_text(unit):
    """格子上写什么：单位格两行"名字 + 移动-攻击"；属性直接读单位数据，空格子不写。"""
    if unit is None:
        return ""
    name = unit.get("name", unit.get("id", ""))
    return f"{name}\n{_attr_text(unit.get('move'))}-{_attr_text(unit.get('attack'))}"


def _attr_text(value):
    """把单位属性转成显示文字（读不到就写 ?；整数按整数显示）。"""
    if isinstance(value, bool) or value is None:
        return "?"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _terrain_fill(node):
    """空格子的底色：按地形。"""
    return TERRAIN_FILL.get(node.get("terrain"), PLAIN_FILL)


def _unit_fill(unit, state):
    """兵牌颜色：按阵营；移动阶段里当前控制方用尽行动力的单位深一档。"""
    side = unit.get("side")
    spent = (state.get("stage") == "move"
             and side == state.get("control_side")
             and not unit.get("destroyed")
             and _number(unit.get("move_left", 0)) <= 0)
    if side == "north":
        return NORTH_SPENT_FILL if spent else NORTH_FILL
    return SOUTH_SPENT_FILL if spent else SOUTH_FILL


def _unit_counters(page_context, visible, units, state, world_center):
    """单位兵牌：格子中心的小六边形，四周留出地形色；点击仍由底下的整格层吃。"""
    counters = []
    to_pixel = page_context.functions["pack_hex_grid.to_pixel.hex_pointy"]
    hex_points = page_context.functions["pack_hex_grid.hex_points.at"]
    for key in sorted(units):
        unit = units[key]
        cell = unit.get("at", "")
        if not cell or unit.get("destroyed") or cell not in visible:
            continue
        node = visible[cell]
        x, y = to_pixel(node["q"], node["r"], BASE_SIZE, world_center)
        counters.append(Layer(
            id=f"unit:{key}",
            kind="polygon",
            points=hex_points(x, y, BASE_SIZE * UNIT_COUNTER_SCALE),
            color=_unit_fill(unit, state),
            outline_color=(22, 24, 30, 255),
            outline_width=1.0,
            z=10,
        ))
    return counters


def _victory_marker(page_context, visible, world_center):
    """胜利点标记：金色五角星（左上角），独立于格子填充，单位站在上面也看得见。"""
    cell = next((key for key, node in visible.items() if node.get("victory")), "")
    if not cell:
        return None
    node = visible[cell]
    x, y = page_context.functions["pack_hex_grid.to_pixel.hex_pointy"](
        node["q"], node["r"], BASE_SIZE, world_center)
    points = _star_points(x - VICTORY_STAR_OFFSET, y - VICTORY_STAR_OFFSET,
                          VICTORY_STAR_OUTER, VICTORY_STAR_INNER)
    return Layer(
        id="marker:victory",
        kind="polygon",
        points=points,
        color=VICTORY_LINE,
        outline_color=(70, 52, 20, 255),
        outline_width=1.0,
        z=20,
    )


def _star_points(cx, cy, outer, inner):
    """算一个尖朝上的五角星（10 个顶点，内外半径交替）。"""
    points = []
    for index in range(10):
        angle = -pi / 2.0 + index * pi / 5.0
        radius = outer if index % 2 == 0 else inner
        points.append((cx + cos(angle) * radius, cy + sin(angle) * radius))
    return tuple(points)


def _disorder_markers(page_context, visible, units, world_center):
    """混乱角标：红色 × 画在单位格右上角（左上角留给胜利星）。"""
    markers = []
    to_pixel = page_context.functions["pack_hex_grid.to_pixel.hex_pointy"]
    for key in sorted(units):
        unit = units[key]
        cell = unit.get("at", "")
        if not unit.get("disordered") or unit.get("destroyed") or cell not in visible:
            continue
        node = visible[cell]
        x, y = to_pixel(node["q"], node["r"], BASE_SIZE, world_center)
        rect = (x + VICTORY_STAR_OFFSET - 10, y - VICTORY_STAR_OFFSET - 10, 20, 20)
        markers.append(Layer(
            id=f"marker:disorder-outline:{key}",
            rect=rect,
            kind="text",
            text="×",
            font_size=19,
            color=(0, 0, 0, 0),             # 不要黑底片，只当白描边
            text_color=(245, 242, 235, 255),
            align="center",
            z=20,
        ))
        markers.append(Layer(
            id=f"marker:disorder:{key}",
            rect=rect,
            kind="text",
            text="×",
            font_size=15,
            color=(0, 0, 0, 0),
            text_color=DISORDER_COLOR,
            align="center",
            z=21,
        ))
    return markers


def _hud_text(state, nodes, units, camera, visible_count):
    """顶部信息条：局面、地图规模、相机状态，以及"现在该干什么"的提示。"""
    north = sum(1 for u in units.values() if u.get("side") == "north")
    south = sum(1 for u in units.values() if u.get("side") == "south")
    stage = state.get("stage", "?")
    settling = bool(state.get("settling"))
    pending = len(state.get("battles") or ())
    vacated = (state.get("battle_report") or {}).get("vacated", "")
    if vacated:
        hint = f"可战后挺进：点参战单位进入 {vacated}"
    elif stage == "attack" and settling and pending:
        hint = f"结算中：按「下一场」结算第 1 场（剩 {pending} 场）；结算期间不能再声明"
    elif stage == "attack" and settling:
        hint = "结算结束：按「结束回合」交给对方（可先点参战单位挺进）"
    elif stage == "attack" and pending:
        hint = f"攻击阶段：已声明 {pending} 场；继续「点己方单位，再点相邻敌军」，或按「开始结算」"
    elif stage == "attack":
        hint = "攻击阶段：先点己方单位，再点相邻敌军声明；没有要打的就按「结束回合」"
    else:
        hint = "移动阶段：点自己的单位选中；按「进入攻击阶段」结束移动"
    status = (f"第 {state.get('turn', '?')} 回合 · 控制方：{_side_name(state.get('control_side'))}"
              f" · 阶段：{_stage_name(stage, settling)} · 地图 {len(nodes)} 格"
              f"（本帧画 {visible_count} 格）· 单位 北 {north} / 南 {south}"
              f" · 缩放 {camera[2]:.2f}×")
    return f"{status}\n{hint}"


def _stage_name(stage, settling=False):
    """阶段显示名：攻击阶段再分「声明 / 结算」两个子状态。"""
    if stage == "attack":
        return "攻击·结算" if settling else "攻击·声明"
    return {"move": "移动"}.get(stage, stage)


def _side_name(side):
    """阵营显示名。"""
    return "青隼（北）" if side == "north" else "柳浦（南）"


def _number(value):
    """把值转成 float（转不动就当 0）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
