"""基础卡牌动作（模块服务）：抽牌、出牌、洗牌。

牌堆 / 手牌 / 弃牌堆都是 State 里的普通列表，"抽牌"就是"从牌堆删一张、往手牌插一张"
——这正是草案说的**场外变化**（D-01：不在地图空间内的状态变化，仍是同一棵树里的变化量）。
洗牌要用随机，所以走 api.rand_int（引擎的确定性随机，D-23）。

它们不认识任何具体的 State 形状：路径、事件名、战报措辞全从 Mod 的参数来
（平铺 params 会作为最低优先级合并进 args，见 模块与Mod格式.md §4.2）。

参数（Mod 通过 params / 绑定 params / 调用点参数给）：
    deck_path:       牌堆路径（默认 "/deck"）
    hand_path:       手牌路径（默认 "/hand"）
    discard_path:    弃牌堆路径（默认 "/discard"）
    card_types_path: 卡牌定义表路径（可选，如 "/card_types"；用来取 name / cost / value）
    energy_path:     能量路径（可选，如 "/energy"）
    score_path:      得分路径（可选，如 "/score"）
    played_path:     出牌计数路径（可选，如 "/played"）
    deck_empty_path: 牌堆空标记路径（可选，如 "/deck_empty"）
    log_path:        战报列表路径（可选）
    drawn_event / played_event: 标在变化量上的事件 id（可选，供触发链订阅）
    empty_event:     牌堆被抽空时标的事件 id（可选；不给就沿用 drawn_event）
    draw_log / play_log / reshuffle_log: 战报措辞模板（可选）
"""


def draw_card(api, args):
    """把牌堆顶（下标 0）的一张牌放进手牌。

    输入：
        api: 服务手柄；
        args: 本动作没有调用点参数（路径与事件名见模块文档）。
    输出：
        无（改动经 api.emit 记账）。
    异常：
        KeyError / IndexError: 牌堆是空的（Mod 自己的规则该拦住这种情况）。
    变量：
        deck / card / hand: 抽牌前后的列表；
        empty_path / old_flag: 牌堆空标记的路径与旧值。
    """
    log_path = args.get("log_path", "")
    deck_path = args.get("deck_path", "/deck")
    hand_path = args.get("hand_path", "/hand")
    empty_path = args.get("deck_empty_path", "")
    label = args.get("drawn_event", "") or ""
    empty_label = args.get("empty_event", "") or label

    deck = api.get(deck_path)
    card = deck[0]
    # 列表删除只给 list_op 与 index："没有 value 这个字段"和"value 是 null"是两回事（§7.3）。
    api.emit(deck_path, "modify", "list", old_value=card, list_op="remove", index=0, label=label)
    hand = api.get(hand_path)
    api.emit(hand_path, "modify", "list", value=card, list_op="insert",
             index=len(hand), label=label)
    api.log_line(log_path, _template(args, "draw_log", "抽到 {name}").format(
        name=_name(api, args, card)))

    if empty_path and len(deck) == 1:  # 刚抽走的是最后一张 → 牌堆空了，引出洗牌事件
        old_flag = api.get(empty_path) if api.exists(empty_path) else False
        if old_flag is not True:
            api.emit(empty_path, "modify", "bool", value=True, old_value=old_flag,
                     label=empty_label)


def play_card(api, args):
    """打出手牌里第 index 张：进弃牌堆，按卡牌定义扣能量、加分。

    输入：
        api: 服务手柄；
        args: {"index": 手牌下标} + 路径参数。
    输出：
        无。
    异常：
        KeyError / IndexError: 参数缺失或下标越界（Mod 数据写错，交给引擎记成服务失败）。
    变量：
        index / hand / card / cost / value: 中间结果。
    """
    log_path = args.get("log_path", "")
    index = args["index"]
    hand_path = args.get("hand_path", "/hand")
    discard_path = args.get("discard_path", "/discard")
    types_path = args.get("card_types_path", "")
    label = args.get("played_event", "") or ""

    hand = api.get(hand_path)
    card = hand[index]
    cost, value = _cost_and_value(api, args, card)

    api.emit(hand_path, "modify", "list", old_value=card, list_op="remove", index=index,
             label=label)
    discard = api.get(discard_path)
    api.emit(discard_path, "modify", "list", value=card, list_op="insert",
             index=len(discard), label=label)
    _spend(api, args.get("energy_path", ""), -cost, label)
    _spend(api, args.get("score_path", ""), value, label)
    _count_up(api, args.get("played_path", ""), label)
    api.log_line(log_path, _template(args, "play_log", "打出 {name}（花费 {cost}，得分 {value}）").format(
        name=_name(api, args, card), cost=cost, value=value))


def reshuffle(api, args):
    """把弃牌堆洗回牌堆（通常由"牌堆空了"事件触发）。

    输入：
        api: 服务手柄；
        args: 路径参数（弃牌堆为空时什么都不做）。
    输出：
        无。
    异常：
        无。
    变量：
        discard / order / deck: 洗牌前后的列表；
        order 的打乱用 api.rand_int 做 Fisher–Yates，确定可复现。
    """
    log_path = args.get("log_path", "")
    discard_path = args.get("discard_path", "/discard")
    deck_path = args.get("deck_path", "/deck")
    empty_path = args.get("deck_empty_path", "")
    label = args.get("drawn_event", "") or ""

    discard = list(api.get(discard_path))
    if not discard:
        api.log_line(log_path, "弃牌堆是空的，没有牌可以洗回来")
        return

    order = list(discard)
    for position in range(len(order) - 1, 0, -1):
        swap = api.rand_int(0, position)
        order[position], order[swap] = order[swap], order[position]

    api.emit(discard_path, "modify", "list", value=[], old_value=discard, label=label)
    deck = api.get(deck_path)
    api.emit(deck_path, "modify", "list", value=order + list(deck), old_value=deck, label=label)
    if empty_path and api.exists(empty_path) and api.get(empty_path) is True:
        api.emit(empty_path, "modify", "bool", value=False, old_value=True, label=label)
    api.log_line(log_path, _template(
        args, "reshuffle_log", "洗牌：{count} 张弃牌回到牌堆").format(count=len(order)))


def _cost_and_value(api, args, card):
    """按 card_types_path 查一张牌的花费与分值（没给路径就都是 0）。

    输入：
        api: 服务手柄；
        args: 参数表（用 card_types_path）；
        card: 卡牌键。
    输出：
        (cost, value) 二元组（都转成 int）。
    异常：
        无（查不到或字段缺失按 0 算）。
    变量：
        types_path / card_type: 中间结果。
    """
    cost = value = 0
    types_path = args.get("card_types_path", "")
    if types_path:
        card_type = api.get(f"{types_path}/{card}")
        if isinstance(card_type, dict):
            cost = int(card_type.get("cost", 0) or 0)
            value = int(card_type.get("value", 0) or 0)
    return cost, value


def _name(api, args, card):
    """取一张牌的显示名（没给定义表或查不到就用键本身）。

    输入：
        api: 服务手柄；
        args: 参数表（用 card_types_path）；
        card: 卡牌键。
    输出：
        显示名字符串。
    异常：
        无。
    变量：
        types_path / card_type: 中间结果。
    """
    types_path = args.get("card_types_path", "")
    if not types_path:
        return card
    card_type = api.get(f"{types_path}/{card}")
    if isinstance(card_type, dict):
        return card_type.get("name", card)
    return card


def _spend(api, path, delta, label):
    """给一条数字路径加上 delta（路径为空或不存在时什么都不做）。

    输入：
        api: 服务手柄；
        path: 数值路径；
        delta: 增减量（可正可负）；
        label: 事件 id（可为空）。
    输出：
        无。
    异常：
        无。
    变量：
        current: 旧值。
    """
    if not path:
        return
    current = api.get(path)
    api.emit(path, "modify", "number", value=current + delta, old_value=current, label=label)


def _count_up(api, path, label):
    """给一条数字路径 +1（路径为空时什么都不做）。"""
    _spend(api, path, 1, label)


def _template(args, key, default):
    """取战报措辞模板（Mod 没给就用默认写法）。"""
    text = args.get(key, "") or default
    return str(text)
