"""战斗结果表（纯函数）：档位 → 1d6 → 结果码 → 效果。

表是**数据**（DEFAULT_TABLE），换一张表就是换一个变体；骰子由调用方掷
（服务里走引擎随机，保证可回放），这里只做"查表 + 翻译"。
"""

# 档位与它们的数值（比值 = 攻方合计攻击力 ÷ 守方抵抗值）。
BANDS = (("1:3", 1 / 3), ("1:2", 1 / 2), ("1:1", 1.0),
         ("2:1", 2.0), ("3:1", 3.0), ("4:1+", 4.0))

# 表：行 = 骰点 1~6，列 = 档位；格 = 结果码。
DEFAULT_TABLE = {
    "1:3": {1: "AE", 2: "AE", 3: "A3", 4: "A2", 5: "A1", 6: "D1"},
    "1:2": {1: "A3", 2: "A2", 3: "A2", 4: "A1", 5: "A1", 6: "D1"},
    "1:1": {1: "A2", 2: "A1", 3: "O", 4: "D1", 5: "D1", 6: "D2"},
    "2:1": {1: "A1", 2: "O", 3: "D1", 4: "D1", 5: "D2", 6: "D3"},
    "3:1": {1: "D1", 2: "D2", 3: "D2", 4: "D3", 5: "DE", 6: "DE"},
    "4:1+": {1: "DE", 2: "DE", 3: "DE", 4: "DE", 5: "DE", 6: "DE"},
}


def table():
    """返回 CRT 的只读表格结构（给界面 / 工具显示用）。

    输出：
        {"bands": [档位, …], "rows": [{"roll": 骰点, "results": {档位: 结果码}}, …]}。
        每次返回新 dict，调用方改它不会污染模块里的 DEFAULT_TABLE。
    异常：
        无。
    变量：
        bands / rows / roll: 表头、行与骰点。
    """
    bands = [label for label, _ in BANDS]
    rows = [
        {"roll": roll, "results": {band: DEFAULT_TABLE[band][roll] for band in bands}}
        for roll in range(1, 7)
    ]
    return {"bands": bands, "rows": rows}


def band_of(attack, defense):
    """算这一战落在哪一档。

    输入：
        attack: 攻方合计攻击力（>= 0）；defense: 守方抵抗值（> 0）。
    输出：
        档位名（"1:3" … "4:1+"）；**不足最低档时返回空字符串**（这一战不许打）。
    异常：
        ValueError: defense <= 0，或 attack 是负数。
    变量：
        ratio / label / value: 中间结果。
    """
    if defense is None or float(defense) <= 0:
        raise ValueError(f"守方抵抗值必须为正数，实际是 {defense!r}")
    if float(attack) < 0:
        raise ValueError(f"攻方攻击力不能是负数，实际是 {attack!r}")
    ratio = float(attack) / float(defense)

    found = ""
    for label, value in BANDS:          # 从低到高：取"不超过比值"的最高一档（对守方有利）
        if value <= ratio + 1e-9:
            found = label
    return found


def resolve(attack, defense, roll, table=None):
    """查表：给攻防与骰点，返回结果码。

    输入：
        attack / defense: 攻方合计攻击力、守方抵抗值；
        roll: 骰点（1~6）；
        table: 换一张表（省略就用内置的 DEFAULT_TABLE）。
    输出：
        结果码："DE" / "AE" / "A1"~"A3" / "D1"~"D3" / "O"；
        不足最低档时返回空字符串（调用方按"拒绝这一战"处理）。
    异常：
        ValueError: 骰点不是 1~6，或档位算不出来。
    变量：
        band / data: 中间结果。
    """
    if isinstance(roll, bool) or not isinstance(roll, int) or not 1 <= roll <= 6:
        raise ValueError(f"骰点必须是 1~6 的整数，实际是 {roll!r}")
    band = band_of(attack, defense)
    if not band:
        return ""
    data = table or DEFAULT_TABLE
    return data[band][roll]


def result_effects(code):
    """把结果码翻成"谁、怎么了"。

    输入：
        code: 结果码（见 resolve）。
    输出：
        {"eliminated": "attacker"/"defender"/""， "retreat": 格数， "side": 谁退}
        —— 空结果码（不足最低档）也是全空。
    异常：
        ValueError: 不认识的码。
    变量：
        无。
    """
    effects = {"eliminated": "", "retreat": 0, "side": ""}
    if not code:
        return effects
    if code == "DE":
        effects["eliminated"] = "defender"
    elif code == "AE":
        effects["eliminated"] = "attacker"
    elif code == "O":
        pass
    elif code[0] in ("A", "D") and code[1:].isdigit():
        effects["side"] = "attacker" if code[0] == "A" else "defender"
        effects["retreat"] = int(code[1:])
    else:
        raise ValueError(f"不认识的结果码：{code!r}")
    return effects
