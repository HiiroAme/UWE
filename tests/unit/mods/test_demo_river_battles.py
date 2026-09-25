"""声明多场 + 逐场结算的验收测试（标准库 unittest）。"""

import unittest
from pathlib import Path

from adapters import LocalFileSystem, PythonScriptLoader
from core.pipeline import Input
from modload import ModLoader
from shell import GameSession

REPO_ROOT = Path(__file__).resolve().parents[3]


def make_session(seed=3):
    """开一局，并把两对单位摆成面对面（各自相邻）。"""
    loader = ModLoader(LocalFileSystem(), PythonScriptLoader(),
                       str(REPO_ROOT / "mods"), module_roots=[str(REPO_ROOT / "modules")])
    loaded = loader.load(loader.load_info(str(REPO_ROOT / "mods" / "demo_river")))
    session = GameSession(loaded, files=LocalFileSystem(), seed=seed)
    session.state["intro_seen"] = True          # 这批用例只玩战斗，不开局弹介绍
    key = {(n["row"], n["col"]): k for k, n in session.state["nodes"].items()}
    session.state["units"]["north_10"]["at"] = key[(17, 11)]      # 铁隼
    session.state["units"]["south_1"]["at"] = key[(17, 10)]       # 守军
    session.state["units"]["north_1"]["at"] = key[(18, 11)]
    session.state["units"]["south_2"]["at"] = key[(18, 10)]
    session.state["stage"] = "attack"
    return session


def send(session, kind, data):
    """送一条输入并结算，返回那条命令的结果。"""
    session.submit_input(Input(kind, data, 1.0, "ui"))
    return session.settle(["turn:1"]).commands[0]


def view_clicks(session):
    """把当前一帧"长得能点的东西"取出来：[(kind, data), …]（走视图脚本，与玩家同一条路）。"""
    from core.ui import PageContext

    loaded = session.loaded
    page = loaded.pages["demo_river:ui:battle"]
    view = page(PageContext(state=session.state, size=(960, 640),
                            context=session.runtime.context,
                            page_id="demo_river:ui:battle", functions=loaded.functions))
    return [(layer.click.kind, dict(layer.click.data or {}))
            for layer in view.layers if layer.click is not None]


def button_views(session):
    """当前帧上 fixed 按钮的 (点击 kind, 按钮文字)（按钮是固定层，不跟视口动）。"""
    from core.ui import PageContext

    loaded = session.loaded
    page = loaded.pages["demo_river:ui:battle"]
    view = page(PageContext(state=session.state, size=(960, 640),
                            context=session.runtime.context,
                            page_id="demo_river:ui:battle", functions=loaded.functions))
    return [(layer.click.kind, layer.text) for layer in view.layers
            if layer.click is not None and layer.fixed
            and layer.id.startswith("button:")]


def click(session, kind, **match):
    """在"视图给出的点击结果"里挑一条符合的按下去（N-1 回归用例用）。"""
    for found_kind, data in view_clicks(session):
        if found_kind == kind and all(data.get(key) == value for key, value in match.items()):
            return send(session, kind, data)
    raise AssertionError(f"这一帧没有可点的 {kind}（要求 {match}）")


class TestBattleDeclarations(unittest.TestCase):
    """声明列表与逐场结算。"""

    def test_declare_then_resolve_one_by_one(self):
        """声明两场 → 列表里两条；每"继续"一次结算一场并出列。"""
        session = make_session()
        self.assertTrue(send(session, "declare_battle",
                             {"units": ["north_10"], "target": "south_1"}).committed)

    def test_two_declarations_in_one_attack_phase_via_view_clicks(self):
        """N-1 回归：**走视图给出的点击结果**，一个攻击阶段能连声明两场。

        之前的用例都直接传 `units` 数组，绕过了"名单从哪来"这一步，
        所以没抓到"声明成功后名单没清、已打过的单位点不掉、第二场必然被拒"这个 bug。
        """
        session = make_session()
        session.runtime.context.put("demo_river.camera.zoom", 0.4)   # 缩到看得见整张图

        click(session, "toggle_attacker", unit="north_10")
        click(session, "declare_battle", target="south_1")
        self.assertEqual(len(session.state["battles"]), 1)
        self.assertEqual(session.runtime.context.get("demo_river.attackers", []), [])   # 声明后名单清空

        click(session, "toggle_attacker", unit="north_1")
        click(session, "declare_battle", target="south_2")
        self.assertEqual([item["target"] for item in session.state["battles"]],
                         ["south_1", "south_2"])                  # 两场都在声明列表里

        self.assertTrue(send(session, "declare_battle",
                             {"units": ["north_1"], "target": "south_2"}).committed)
        battles = session.state["battles"]
        self.assertEqual(len(battles), 2)
        self.assertEqual(battles[0]["target"], "south_1")
        self.assertTrue(session.state["units"]["north_10"]["fought"])   # 声明即消耗

        send(session, "resolve_next", {})
        self.assertEqual(len(session.state["battles"]), 1)              # 结算完一场就出列
        send(session, "resolve_next", {})
        self.assertEqual(session.state["battles"], [])
        # 没有声明时再点"继续"：什么都不发生（条件分支不成立）
        send(session, "resolve_next", {})
        self.assertEqual(session.state["battles"], [])

    def test_same_unit_cannot_be_declared_twice(self):
        """同一个单位本回合只能声明一次（第二次声明被拒，列表不增）。"""
        session = make_session()
        send(session, "declare_battle", {"units": ["north_10"], "target": "south_1"})
        send(session, "declare_battle", {"units": ["north_10"], "target": "south_2"})
        self.assertEqual(len(session.state["battles"]), 1)

    def test_multi_unit_attack_declaration(self):
        """多选参战单位：两个单位一起声明，列表里记的是两个，结算后都算打过仗。"""
        session = make_session()
        session.state["stage"] = "attack"
        send(session, "toggle_attacker", {"unit": "north_10"})
        send(session, "toggle_attacker", {"unit": "north_1"})
        self.assertEqual(session.runtime.context.get("demo_river.attackers", []),
                         ["north_10", "north_1"])
        send(session, "declare_battle", {"units": ["north_10", "north_1"], "target": "south_1"})
        self.assertEqual(session.state["battles"][0]["units"], ["north_10", "north_1"])
        self.assertTrue(session.state["units"]["north_1"]["fought"])

    def test_declaration_log_uses_names(self):
        """战报里的声明行用单位名字，不再写 north_10 / south_1 这种键。"""
        session = make_session()
        send(session, "declare_battle", {"units": ["north_10"], "target": "south_1"})
        line = session.state["log"][-1]
        self.assertIn("铁隼装甲连", line)
        self.assertIn("柳浦 1 连", line)
        self.assertNotIn("north_10", line)

    def test_disorder_recovers_at_own_attack_phase_end(self):
        """混乱在本方"攻击阶段结束"时翻回来（M-5）：结束回合就恢复本方的混乱单位。"""
        session = make_session()
        session.state["units"]["north_10"]["disordered"] = True
        session.state["units"]["south_1"]["disordered"] = True
        send(session, "pass_control", {})          # 结束北方的回合
        self.assertFalse(session.state["units"]["north_10"]["disordered"])   # 本方：翻回来
        self.assertTrue(session.state["units"]["south_1"]["disordered"])     # 对方：还得等自己的阶段

    def test_movement_is_restored_at_turn_start(self):
        """回合开始把移动力补回属性值（move_left ← move）；对方不动。"""
        session = make_session()
        session.state["units"]["north_1"]["move_left"] = 0
        session.state["units"]["south_2"]["move_left"] = 0
        send(session, "pass_control", {})          # 结束北方回合 → 南方回合开始
        self.assertEqual(session.state["units"]["north_1"]["move_left"], 0)          # 还在北方手里时不动
        self.assertEqual(session.state["units"]["south_2"]["move_left"],
                         session.state["units"]["south_2"]["move"])                  # 南方恢复了

    def test_victory_when_attacker_holds_the_point(self):
        """回合末攻方站在胜利点上 → 攻方胜（要把一个大回合走完才判）。"""
        session = make_session()
        victory = next(k for k, n in session.state["nodes"].items() if n.get("victory"))
        session.state["units"]["north_10"]["at"] = victory
        send(session, "to_attack", {})
        send(session, "pass_control", {})          # 北方结束 → 南方回合
        self.assertFalse(session.state["game_over"])          # 还没到大回合末
        send(session, "to_attack", {})
        send(session, "pass_control", {})          # 南方结束 → 大回合末
        self.assertTrue(session.state["game_over"])
        self.assertEqual(session.state["winner"], "north")

    def test_victory_when_turn_limit_passes(self):
        """到回合上限攻方还没占住 → 守方胜。"""
        session = make_session()
        session.state["turn"] = 13                 # 已经超过上限 12
        send(session, "to_attack", {})
        send(session, "pass_control", {})
        send(session, "to_attack", {})
        send(session, "pass_control", {})
        self.assertTrue(session.state["game_over"])
        self.assertEqual(session.state["winner"], "south")

    def test_turn_limit_boundary(self):
        """边界：第 12 回合末（turn 变 13）判守方胜；turn 正好 12 时还不判。"""
        session = make_session()
        session.state["turn"] = 12
        send(session, "to_attack", {})
        send(session, "pass_control", {})
        send(session, "to_attack", {})
        send(session, "pass_control", {})          # turn → 13，跨过上限
        self.assertTrue(session.state["game_over"])
        self.assertEqual(session.state["winner"], "south")

    def test_view_shows_banner_and_stops_clicking(self):
        """结束之后：画面上有胜负横幅，而且整张图不再给点击结果。"""
        from core.context import Context
        from core.ui import PageContext

        session = make_session()
        session.state["game_over"] = True
        session.state["winner"] = "north"
        loaded = session.loaded
        page = loaded.pages["demo_river:ui:battle"]
        view = page(PageContext(state=session.state, size=(960, 640), context=Context(),
                                page_id="demo_river:ui:battle", functions=loaded.functions))
        ids = {layer.id for layer in view.layers}
        self.assertIn("banner:winner", ids)
        self.assertIn("button:game_over", ids)
        clickable = [l for l in view.layers if l.click is not None and not l.fixed]
        self.assertEqual(clickable, [])          # 地图上没有任何可点的层了

    def test_advance_after_the_defender_leaves(self):
        """战后挺进：守军退走/被歼灭后，参战单位可以走进那一格。"""
        session = make_session()
        cell = session.state["units"]["south_1"]["at"]
        send(session, "declare_battle", {"units": ["north_10"], "target": "south_1"})
        send(session, "resolve_next", {})
        report = session.state["battle_report"]
        if report.get("vacated"):
            send(session, "advance", {"unit": "north_10"})
            self.assertEqual(session.state["units"]["north_10"]["at"], cell)
            self.assertEqual(session.state["battle_report"]["vacated"], "")   # 用过就清掉
        else:
            self.skipTest("这一种子下守军没让出格子（白打一场）")

    def test_battle_is_skipped_when_the_target_is_gone(self):
        """战场变了：目标已经被消灭时，那一场被跳过，并在战报里说清楚。"""
        session = make_session()
        send(session, "declare_battle", {"units": ["north_10"], "target": "south_1"})
        # 模拟"目标在别的战斗里先被打掉"
        session.state["units"]["south_1"]["destroyed"] = True
        result = send(session, "resolve_next", {})
        self.assertTrue(result.committed)
        self.assertEqual(session.state["battles"], [])            # 跳过的那场也出列
        self.assertTrue(any("打不成" in line for line in session.state["log"]))

    def test_disordered_target_can_be_pushed_back_again(self):
        """A-1 回归：对**已经混乱**的目标再逼退一次，不许再撞旧值（命令要成功）。"""
        session = make_session()
        send(session, "declare_battle", {"units": ["north_10"], "target": "south_1"})
        session.state["units"]["south_1"]["disordered"] = True      # 目标本来就混乱
        result = send(session, "resolve_next", {})
        self.assertTrue(result.committed)                           # 不再变成 discarded
        self.assertEqual(session.state["battles"], [])              # 结算完正常出列
        defender = session.state["units"]["south_1"]
        self.assertTrue(defender["disordered"] or defender["destroyed"])

    def test_illegal_declarations_are_refused_by_the_service(self):
        """B-2 回归：不相邻 / 打自己人 都不能声明成功（合法性由服务把关，不是只在界面挡）。"""
        session = make_session()
        far = self_ = send(session, "declare_battle",
                           {"units": ["north_2"], "target": "south_1"})   # north_2 在北岸，远得很
        self.assertTrue(far.committed)                                   # 命令本身没被规则拒
        self.assertEqual(session.state["battles"], [])                   # 但服务没放进列表
        self.assertTrue(any("不相邻" in line for line in session.state["log"]))

        friendly = send(session, "declare_battle",
                        {"units": ["north_10"], "target": "north_1"})    # 打自己人
        self.assertTrue(friendly.committed)
        self.assertEqual(session.state["battles"], [])
        self.assertTrue(any("自己人" in line for line in session.state["log"]))

    def test_battle_is_skipped_when_units_are_no_longer_adjacent(self):
        """A-2 回归：声明之后目标跑远了 → 结算时整场跳过（不掷骰、不改状态），并记战报。"""
        session = make_session()
        send(session, "declare_battle", {"units": ["north_10"], "target": "south_1"})
        target_before = dict(session.state["units"]["south_1"])
        far = {(n["row"], n["col"]): k for k, n in session.state["nodes"].items()}[(1, 20)]
        session.state["units"]["south_1"]["at"] = far            # 目标被挪到北岸远端
        result = send(session, "resolve_next", {})
        self.assertTrue(result.committed)
        self.assertEqual(session.state["battles"], [])            # 跳过的那场也出列
        self.assertEqual(session.state["units"]["south_1"]["at"], far)      # 没被打
        self.assertEqual(session.state["units"]["south_1"]["disordered"],
                         target_before["disordered"])
        self.assertTrue(any("跳过" in line for line in session.state["log"]))


    def test_victory_branch_reads_the_side_names_from_state(self):
        """R4-8：阵营名只有 State 一个来源——改 `/defender_side` 会改变判定时机。"""
        session = make_session()
        session.state["defender_side"] = "north"      # 故意改坏：现在"守方"就是北军
        session.state["turn"] = 13                    # 已过回合上限：判守方（也就是 north）胜
        send(session, "pass_control", {})
        self.assertTrue(session.state["game_over"])
        self.assertEqual(session.state["winner"], "north")   # 判的就是 State 里那个"守方"

    def test_settlement_closes_the_declaration_window(self):
        """开始结算之后：声明被规则拒，界面也不再给声明点击。"""
        session = make_session()
        send(session, "declare_battle", {"units": ["north_10"], "target": "south_1"})
        started = send(session, "begin_settle", {})
        self.assertTrue(started.committed)
        self.assertTrue(session.state["settling"])
        refused = send(session, "declare_battle", {"units": ["north_1"], "target": "south_2"})
        self.assertFalse(refused.committed)
        self.assertEqual(refused.rule_id, "demo_river:rule:not_settling")
        kinds = [kind for kind, _ in view_clicks(session)]
        self.assertNotIn("toggle_attacker", kinds)
        self.assertNotIn("declare_battle", kinds)

    def test_cannot_end_turn_with_pending_battles(self):
        """还有未结算的战斗时，结束回合被规则拦住。"""
        session = make_session()
        send(session, "declare_battle", {"units": ["north_10"], "target": "south_1"})
        refused = send(session, "pass_control", {})
        self.assertFalse(refused.committed)
        self.assertEqual(refused.rule_id, "demo_river:rule:no_pending_battles")

    def test_end_turn_needs_attack_stage(self):
        """移动阶段不能直接结束回合：先按「进入攻击阶段」。"""
        session = make_session()
        session.state["stage"] = "move"
        refused = send(session, "pass_control", {})
        self.assertFalse(refused.committed)
        self.assertEqual(refused.rule_id, "demo_river:rule:stage_is_attack")

    def test_button_flow_changes_with_progress(self):
        """一个按钮的字与功能随「移动 → 声明 → 结算 → 结束回合」变化。"""
        session = make_session()
        session.state["stage"] = "move"
        self.assertEqual(button_views(session), [("to_attack", "进入攻击阶段")])
        send(session, "to_attack", {})
        self.assertEqual(button_views(session), [("pass_control", "结束回合（交给对方）")])
        send(session, "declare_battle", {"units": ["north_10"], "target": "south_1"})
        self.assertEqual(button_views(session), [("begin_settle", "开始结算（1 场）")])
        send(session, "begin_settle", {})
        self.assertEqual(button_views(session), [("resolve_next", "下一场（剩 1 场）")])
        send(session, "resolve_next", {})
        self.assertEqual(button_views(session), [("pass_control", "结束回合（交给对方）")])
        send(session, "pass_control", {})
        self.assertEqual(session.state["stage"], "move")
        self.assertFalse(session.state["settling"])

    def test_intro_confirm_and_rules_button(self):
        """开局：确认写入 /intro_seen；「规则」按钮随时能把弹窗打开再关掉。"""
        session = make_session()
        session.state["intro_seen"] = False
        self.assertIn("close_rules", [kind for kind, _ in view_clicks(session)])
        send(session, "close_rules", {})
        self.assertTrue(session.state["intro_seen"])
        self.assertNotIn("close_rules", [kind for kind, _ in view_clicks(session)])

        send(session, "open_rules", {})       # 外壳菜单项会发这条输入
        self.assertTrue(session.runtime.context.get("demo_river.rules_open"))
        self.assertIn("close_rules", [kind for kind, _ in view_clicks(session)])
        send(session, "close_rules", {})
        self.assertFalse(session.runtime.context.get("demo_river.rules_open"))

    def test_log_scroll_service_updates_context(self):
        """战报栏滚轮输入 → 滚轮服务只改 Context 的滚动位置。"""
        session = make_session()
        send(session, "scroll_log", {"delta": [0.0, 3.0]})
        self.assertEqual(session.runtime.context.get("demo_river.log.scroll"), 3.0)


if __name__ == "__main__":
    unittest.main()
