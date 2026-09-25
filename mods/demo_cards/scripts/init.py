"""初始化钩子：把卡牌模板搬进 State，摆好牌堆与手牌。

契约（见 modload.loader._build_initial_state）：
    build_state(mod) -> dict

这个演示与六边形演示用了**完全不同的 State 形状**（没有地图、没有坐标，
牌堆 / 手牌 / 弃牌堆都是普通列表）——引擎对此一无所知，这正是要证明的事（P1 / D-01）。
"""

def build_state(mod):
    """构造初始 State。

    输入：
        mod: 引擎给的上下文（ModContext）——用 template_values 取卡牌模板。
    输出：
        初始 State 字典。
    异常：
        KeyError: 卡牌模板 id 写错（Mod 数据问题，加载期就会报）。
    变量：
        card_ids: 本演示用到的卡牌模板 id；
        card_types: 卡牌定义表（从模板搬到 State，规则与服务都从 State 读）；
        deck: 初始牌堆。
    """
    card_ids = (
        "demo_cards:entity:strike",
        "demo_cards:entity:guard",
        "demo_cards:entity:bolt",
    )
    card_types = {}
    for template_id in card_ids:
        card_types[_card_key(template_id)] = mod.template_values(template_id)

    # 牌堆：每张牌只记它的键，具体属性去 /card_types 里查（实体用引用，不嵌套对象，§6.6）。
    deck = ["strike", "strike", "guard", "bolt", "strike", "guard", "bolt", "strike"]
    return {
        "turn": 1,
        "energy": 3,
        "max_energy": 3,
        "hand_limit": 5,
        "score": 0,
        "played": 0,
        "phase": "demo_cards:phase:main",
        "phase_actions": list(mod.phase_actions("demo_cards:phase:main")),
        "card_types": card_types,
        "deck": deck[3:],
        "hand": deck[:3],
        "discard": [],
        # 牌堆是否空过（抽空时置 True、洗牌后置回 False）；初值必须在，
        # 因为"修改"只对已存在的键成立（§7.2）。
        "deck_empty": False,
        "hand_size": 3,
        "deck_size": len(deck) - 3,
        "log": ["开局：抽三张牌"],
    }


def _card_key(template_id):
    """把模板 id 的第三段当作卡牌键（strike / guard / bolt）。"""
    return template_id.rsplit(":", 1)[-1]
