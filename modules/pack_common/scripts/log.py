"""往一个列表追一行（通用工具服务，模块实现）：路径由 Mod 的参数决定。

它不认识任何具体的 State 形状——"战报在哪条路径上"是调用方的事。
"""


def append_line(api, args):
    """把 args["text"] 追加到 args["log_path"] 指的列表里。

    输入：
        api: 服务手柄；
        args: {"text": 文字, "log_path": 列表路径（可选）}。
    输出：
        无。
    异常：
        KeyError: 缺 text（Mod 数据写错，交给引擎记成服务失败）。
    变量：
        log_path / text / current: 中间结果。
    """
    log_path = args.get("log_path", "")
    text = args["text"]
    if not log_path:
        api.log(text)  # 没给路径就只写引擎日志，不碰 State
        return
    current = api.get(log_path)
    api.emit(log_path, "modify", "list", value=list(current) + [text], old_value=current)
