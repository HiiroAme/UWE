"""字符地图（人写的文本地图）：行列 ↔ 轴向坐标、解析与地图校验。

文本地图是给人写的，用"第几行第几列"；引擎与相邻关系用轴向坐标。
两个坐标系之间必须**只有这一个**换算口，免得各写各的。
解析只认"符号"，不认识"河""村庄"这些含义——那是地图作者与 Mod 的事。

这里的函数全是**纯函数**：同样输入同样输出，不读时钟、不取随机、不碰 State。
（脚本文件之间不能互相 import：本文件自己带一份 6 行的参数校验小工具。）
"""


def offset_to_axial(row, col, layout="odd_r"):
    """错行地图的行列 → 轴向坐标（文本地图用的换算）。

    输入：
        row / col: 地图文本里的行号与列号（从 0 开始，int）；
        layout: 错行方式，目前只支持 "odd_r"（奇数行向右移半格）。
    输出：
        (q, r) 轴向坐标二元组。
    异常：
        TypeError: 行列不是 int（bool 不算）；
        ValueError: layout 不认识。
    变量：
        无。

    说明：
        文本地图是给人写的，用"第几行第几列"；引擎与相邻关系用轴向坐标。
        两个坐标系之间必须**只有这一个**换算口，免得各写各的。
    """
    _require_int(row, "row")
    _require_int(col, "col")
    _require_layout(layout)
    return (col - (row - (row & 1)) // 2, row)


def axial_to_offset(q, r, layout="odd_r"):
    """轴向坐标 → 错行地图的行列（offset_to_axial 的逆运算）。

    输入：
        q / r: 轴向坐标（int）；
        layout: 错行方式（目前只有 "odd_r"）。
    输出：
        (行, 列) 二元组。
    异常：
        TypeError: 坐标不是 int（bool 不算）；
        ValueError: layout 不认识。
    变量：
        无。
    """
    _require_int(q, "q")
    _require_int(r, "r")
    _require_layout(layout)
    return (r, q + (r - (r & 1)) // 2)


def parse_text_map(text, layout="odd_r"):
    """把"字符地图"文本解析成格子表（不读文件，文本由调用方给）。

    输入：
        text: 地图文本；规则：
            - 以 # 开头的行是注释，空行忽略；
            - 其余每一行 = 一个地图行，按空白切成若干格（所以"奇数行缩进半格"只是给人看的）；
            - 所有地图行的格数必须一样。
        layout: 错行方式（目前只有 "odd_r"）。
    输出：
        {"rows": 行数, "cols": 列数, "cells": {(行, 列): 符号}}。
    异常：
        ValueError: 一行都没解析出来，或各行的格数不一致。
    变量：
        rows / cells / codes: 中间结果。

    说明：
        解析只认"符号"，不认识"河""村庄"这些含义——那是地图作者与 Mod 的事。
    """
    _require_layout(layout)
    if not isinstance(text, str):
        raise TypeError(f"text 必须是字符串，实际是 {type(text).__name__}")

    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        rows.append(stripped.split())
    if not rows:
        raise ValueError("地图文本里一行格子都没有（注释和空行不算）")

    width = len(rows[0])
    for index, row in enumerate(rows):
        if len(row) != width:
            raise ValueError(
                f"地图第 {index} 行有 {len(row)} 格，与第 0 行的 {width} 格不一致"
            )

    cells = {}
    for row_index, row in enumerate(rows):
        for col_index, code in enumerate(row):
            cells[(row_index, col_index)] = code
    return {"rows": len(rows), "cols": width, "cells": cells}


def check_barrier(cells, barrier_code, rows=None, cols=None):
    """校验"屏障"（例如一条横贯东西的河）是否连成一体、并且真的挡住了南北。

    输入：
        cells: {(行, 列): 符号}（parse_text_map 的产物）；
        barrier_code: 屏障用的符号（如 "r"）；
        rows / cols: 地图尺寸；不给就按 cells 推断。
    输出：
        {"connected": 屏障连成一体吗, "blocks": 南北被挡住吗, "problems": [说明, …]}。
    异常：
        ValueError: cells 是空的，或尺寸对不上。
    变量：
        barrier / seen / queue: 中间结果。

    口径：
        - `connected`：屏障格自己连成一体（不能断成几截）；
        - `blocks`：**不经过屏障格**时，从最上面一行到不了最下面一行。
          （渡口格不是屏障格，但两个渡口格被一格河水隔开、彼此不相邻，
          所以渡口不会让这条检查失败——这也正是"渡口是成对的"该有的样子。）
    """
    if not cells:
        raise ValueError("cells 是空的")
    if not isinstance(barrier_code, str) or not barrier_code:
        raise TypeError("barrier_code 必须是非空字符串")

    if rows is None:
        rows = max(row for row, _ in cells) + 1
    if cols is None:
        cols = max(col for _, col in cells) + 1
    if len(cells) != rows * cols:
        raise ValueError(f"cells 有 {len(cells)} 格，与 {rows}×{cols} 对不上")

    barrier = {pos for pos, code in cells.items() if code == barrier_code}
    problems = []
    if not barrier:
        problems.append(f"地图里没有任何 {barrier_code!r} 屏障格")
        return {"connected": False, "blocks": False, "problems": problems}

    # 1. 屏障自己连成一体
    start = next(iter(barrier))
    seen = {start}
    queue = [start]
    while queue:
        row, col = queue.pop()
        for neighbor in _offset_neighbors(row, col):
            if neighbor in barrier and neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    connected = len(seen) == len(barrier)
    if not connected:
        problems.append(f"屏障分成 {len(barrier) - len(seen)} 格没有连上主体")

    # 2. 不经过屏障时，最上面一行到不了最下面一行
    top = [(0, col) for col in range(cols) if (0, col) not in barrier]
    bottom = {(rows - 1, col) for col in range(cols)}
    reached = set(top)
    queue = list(top)
    blocks = True
    while queue:
        row, col = queue.pop()
        if (row, col) in bottom:
            blocks = False
            break
        for neighbor in _offset_neighbors(row, col):
            if neighbor in barrier or neighbor in reached:
                continue
            if neighbor not in cells:
                continue
            reached.add(neighbor)
            queue.append(neighbor)
    if not blocks:
        problems.append("不经过屏障也能从最上面一行走到最下面一行（有缝可以绕过去）")
    return {"connected": connected, "blocks": blocks, "problems": problems}


def ford_pairs(cells, ford_code, barrier_code):
    """找出成对的渡口格（两岸各一格、中间隔一格屏障）。

    输入：
        cells: {(行, 列): 符号}；
        ford_code: 渡口格的符号（如 "f"）；
        barrier_code: 屏障（河）的符号（如 "r"）。
    输出：
        {"pairs": [((北岸行, 列), (河水行, 列), (南岸行, 列)), …], "problems": [说明, …]}。
    异常：
        ValueError: cells 是空的。
    变量：
        fords / used / pairs: 中间结果。

    说明：
        配对口径：两个渡口格在**同一列**、行号相差 2，中间那一格是屏障。
        对不上号的渡口格会写进 problems——这属于地图写错，不该带到游戏里。
    """
    if not cells:
        raise ValueError("cells 是空的")

    fords = sorted(pos for pos, code in cells.items() if code == ford_code)
    pairs = []
    used = set()
    problems = []
    for (row, col) in fords:
        if (row, col) in used:
            continue
        partner = (row + 2, col)
        between = (row + 1, col)
        if cells.get(between) == barrier_code and cells.get(partner) == ford_code:
            pairs.append(((row, col), between, partner))
            used.add((row, col))
            used.add(partner)
            continue
        before = (row - 1, col)
        partner_up = (row - 2, col)
        if cells.get(before) == barrier_code and cells.get(partner_up) == ford_code:
            pairs.append((partner_up, before, (row, col)))
            used.add((row, col))
            used.add(partner_up)
            continue
        problems.append(f"渡口格 {(row, col)} 找不到对岸的搭档（中间应当是 {barrier_code!r}）")
    return {"pairs": pairs, "problems": problems}


def _offset_neighbors(row, col):
    """错行（odd_r）地图里一个格子的六个邻居（行, 列）。"""
    if row % 2 == 0:
        candidates = [(row, col - 1), (row, col + 1),
                      (row - 1, col - 1), (row - 1, col),
                      (row + 1, col - 1), (row + 1, col)]
    else:
        candidates = [(row, col - 1), (row, col + 1),
                      (row - 1, col), (row - 1, col + 1),
                      (row + 1, col), (row + 1, col + 1)]
    return candidates


def _require_layout(layout):
    """只认一种错行方式（以后要加别的再加变体）。"""
    if layout != "odd_r":
        raise ValueError(f"现在只支持 odd_r 这一种错行方式，实际是 {layout!r}")


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
