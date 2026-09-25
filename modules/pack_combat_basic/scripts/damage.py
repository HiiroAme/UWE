"""伤害计算（模块实现，纯函数）：同一件事的两种做法，由 Mod 选。

为什么随机不在这里：本文件是**纯函数**（同输入同输出），而掷骰子要取随机。
需要随机的做法由调用方（例如攻击服务）先用引擎随机掷好，再把结果传给这里——
这样模块函数保持可测试、可复现，也不会偷偷引入随机源（§13 随机统一由引擎管）。
"""


def damage_linear(amount, *, modifier=0):
    """定值伤害：等于 amount + modifier。

    输入：
        amount: 基础伤害；
        modifier: 修正（可正可负）。
    输出：
        伤害值（不小于 0）。
    异常：
        TypeError: amount / modifier 不是数字。
    """
    total = float(amount) + float(modifier)
    return max(0, int(total)) if total == int(total) else max(0.0, total)


def damage_dice(rolled, *, modifier=0):
    """骰子伤害：把"已经掷出来的点数"加上修正。

    输入：
        rolled: 调用方掷好的点数（例如 2d6 的结果）；
        modifier: 修正。
    输出：
        伤害值（不小于 0）。
    异常：
        TypeError: rolled / modifier 不是数字。
    """
    return damage_linear(rolled, modifier=modifier)
