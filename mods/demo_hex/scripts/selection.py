"""选中服务（Mod 侧脚本）：把"当前选中的单位"记在界面状态里。

为什么放在 Context 而不是 State：
    "选中了谁"不影响任何规则判定（D-11）：读档之后没有选中任何单位也完全正常。
    所以它是**界面状态**（§6.1 的第三种状态），不进存档。

注意：
    本服务不产生任何变化量——它只改界面状态，属于"表现层"的事，不需要提交，
    也就不会进存档与回放（这正是选择它的原因）。
"""


def select_unit(api, args):
    """把 args["unit"] 记为当前选中的单位。

    输入：
        api: 服务手柄（这里只用到 context 与 log）；
        args: {"unit": 单位键}。
    输出：
        无。
    异常：
        KeyError: 缺 unit 参数（Mod 数据写错，交给引擎记成服务失败）。
    变量：
        unit_key: 单位键。
    """
    unit_key = args["unit"]
    api.context.put("demo_hex.selected", unit_key)
    api.log(f"选中了 {unit_key}")


def clear_selection(api, args):
    """把"当前选中的单位"清空（点空地、或再点一次选中的单位）。

    输入：
        api: 服务手柄（这里只用到 context）；
        args: 本动作没有参数。
    输出：
        无（只改界面状态，不产生变化量）。
    异常：
        无。
    变量：
        无。
    """
    api.context.put("demo_hex.selected", "")
