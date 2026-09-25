"""牌桌界面（Mod 侧）：按当前 State 生成这一帧的画面。

画什么完全由本脚本决定：手牌是**一排可点击的卡片**，每张牌上写它的名字、
花费与得分；能量够不够决定了它是否可点（不够就不给点击结果，点了也没反应）。
引擎只知道"有一些矩形与文字、点到哪个层产生什么输入"。
"""

from core.ui import ClickResult, Layer, View


def build_view(page_context):
    """生成牌桌画面。

    输入：
        page_context: 引擎给的只读上下文（state / size / context / page_id）。
    输出：
        View。
    异常：
        无（缺字段时按"还没有"处理，界面不该因为数据不全而崩）。
    变量：
        state / width / height: 局面与窗口尺寸；
        hand / card_types / energy: 手牌、卡牌定义与当前能量；
        layers: 逐层拼起来的画面。
    """
    state = page_context.state
    width, height = page_context.size
    hand = state.get("hand", [])
    card_types = state.get("card_types", {})
    energy = state.get("energy", 0)

    layers = []
    card_width, card_height, gap = 150.0, 190.0, 16.0
    total = len(hand)
    left = width / 2 - (total * card_width + max(0, total - 1) * gap) / 2
    for index, card in enumerate(hand):
        card_type = card_types.get(card, {})
        cost = card_type.get("cost", 0)
        playable = energy >= cost
        x = left + index * (card_width + gap)
        layers.append(
            Layer(
                id=f"card:{index}",
                rect=(x, height / 2 - card_height / 2, card_width, card_height),
                kind="text",
                text=_card_text(card_type, cost, playable),
                font_size=16,
                color=(70, 96, 140, 255) if playable else (52, 56, 66, 255),
                text_color=(245, 245, 245, 255),
                click=ClickResult(kind="play", data={"index": index}, settle=True) if playable else None,
                z=10,
            )
        )
    if total == 0:
        layers.append(
            Layer(id="empty_hand", rect=(0, height / 2 - 20, width, 40), kind="text",
                  text="手牌是空的：点下面的「抽牌」或者「结束回合」",
                  font_size=18, color=(0, 0, 0, 0), text_color=(220, 220, 220, 255), z=10)
        )

    layers.append(
        Layer(
            id="button:draw",
            rect=(width / 2 - 220, height - 90, 200, 46),
            kind="text",
            text="抽牌",
            font_size=20,
            color=(70, 96, 140, 255),
            text_color=(245, 245, 245, 255),
            click=ClickResult(kind="draw", data={}, settle=True),
            z=20,
        )
    )
    layers.append(
        Layer(
            id="button:end_turn",
            rect=(width / 2 + 20, height - 90, 200, 46),
            kind="text",
            text="结束回合",
            font_size=20,
            color=(70, 96, 140, 255),
            text_color=(245, 245, 245, 255),
            click=ClickResult(kind="end_turn", data={}, settle=True),
            z=20,
        )
    )
    layers.append(
        Layer(
            id="hud",
            rect=(16, 60, width - 32, 120),
            kind="text",
            text=_hud_text(state),
            align="left",
            font_size=16,
            color=(30, 34, 44, 255),
            text_color=(230, 230, 230, 255),
            z=5,
        )
    )
    return View(layers=tuple(layers), background=(20, 22, 30, 255))


def _card_text(card_type, cost, playable):
    """一张牌上写什么：名字 / 花费 / 得分，能量不够时加一行提示。"""
    name = card_type.get("name", "?")
    value = card_type.get("value", "?")
    lines = [name, f"花费 {cost}", f"得分 {value}"]
    if not playable:
        lines.append("（能量不足）")
    return "\n".join(lines)


def _hud_text(state):
    """顶部的状态行 + 最近的战报。"""
    lines = [
        f"第 {state.get('turn', '?')} 回合    能量 {state.get('energy', '?')}/{state.get('max_energy', '?')}"
        f"    得分 {state.get('score', '?')}    已出牌 {state.get('played', '?')}",
        f"牌堆 {state.get('deck_size', '?')} 张    手牌 {state.get('hand_size', '?')} 张"
        f"    弃牌 {len(state.get('discard', []))} 张",
        "",
        "战报：",
    ]
    for line in state.get("log", [])[-3:]:
        lines.append(f"· {line}")
    return "\n".join(lines)
