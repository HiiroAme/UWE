"""界面状态服务（Mod 侧）：战报滚动、规则弹窗、开局介绍标记。

全部只写 Context / State 里的普通字段；不碰引擎内部，也不读文件。
"""

KEY_LOG_SCROLL = "demo_river.log.scroll"
KEY_RULES_OPEN = "demo_river.rules_open"


def scroll_log(api, args):
    """战报栏滚轮：delta[1] > 0 向上看历史，< 0 回到最新。"""
    delta = args.get("delta", (0.0, 0.0))
    if isinstance(delta, (list, tuple)) and len(delta) >= 2:
        step = float(delta[1])
    else:
        step = float(delta)
    current = float(api.context.get(KEY_LOG_SCROLL, 0.0))
    api.context.put(KEY_LOG_SCROLL, max(0.0, current + step))


def open_rules(api, args):
    """打开规则 / 介绍弹窗（界面状态，不进存档）。"""
    api.context.put(KEY_RULES_OPEN, True)


def close_rules(api, args):
    """关闭弹窗；开局介绍确认时把 /intro_seen 写进存档。"""
    api.context.put(KEY_RULES_OPEN, False)
    if not api.exists("/intro_seen"):
        api.emit("/intro_seen", "add", "bool", value=True)
    elif not api.get("/intro_seen"):
        api.emit("/intro_seen", "modify", "bool", value=True, old_value=False)
