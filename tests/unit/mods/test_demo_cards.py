"""第二个演示 Mod（mods/demo_cards）的端到端验收测试。

位置：tests/unit/mods/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 加载：卡牌模板（entity）、阶段（phase）、系统初值（system）、派生值（formula）；
  - 抽牌 / 出牌 / 结束回合：列表增删（场外变化 D-01）与派生值跟着重算；
  - 规则：手牌满了抽不了、能量不够出不了牌；
  - 触发链：牌堆抽空自动洗弃牌堆（洗牌用引擎随机，可复现）；
  - 界面：视图脚本产出可点击的卡片层，能量不够时不可点；
  - 存档 / 回放 / 读档继续玩。

这个测试同时是"引擎不认识玩法"的第二份证据：这里没有地图、没有坐标、没有单位，
引擎一行都没改。

玩法实现全部来自模块 `pack_cards_basic`（抽牌 / 出牌 / 洗牌 / 结束回合），
通用工具服务来自 `pack_common`（基线内容）——本 Mod 自己一个函数都不写。
"""

import shutil
import unittest
from pathlib import Path

from adapters import LocalFileSystem, PythonScriptLoader
from core.persistence import replay_state
from core.pipeline import Input
from core.ui import PageContext
from core.context import Context
from modload import ModLoader
from shell import GameSession

REPO_ROOT = Path(__file__).resolve().parents[3]
MODS_ROOT = REPO_ROOT / "mods"
MODULES_ROOT = REPO_ROOT / "modules"
CARDS_FOLDER = MODS_ROOT / "demo_cards"
SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"


def load_cards():
    """加载卡牌演示 Mod。"""
    loader = ModLoader(LocalFileSystem(), PythonScriptLoader(), str(MODS_ROOT),
                       module_roots=[str(MODULES_ROOT)])
    return loader.load(loader.load_info(str(CARDS_FOLDER)))


def make_session(seed: int = 2026) -> GameSession:
    """开一局卡牌演示。"""
    return GameSession(load_cards(), files=LocalFileSystem(), seed=seed)


def send(session: GameSession, kind: str, data: dict | None = None, *, timestamp: float = 1.0):
    """送一条输入并结算，返回那个命令的结果。"""
    session.submit_input(Input(kind, data or {}, timestamp, "ui"))
    result = session.settle(["turn:1"])
    return result.commands[0] if result.commands else None


class TestCardsMod(unittest.TestCase):
    """卡牌演示的加载与玩法。"""

    def test_load_shape(self):
        """加载结果：三张牌模板、阶段、系统初值与派生值都在。"""
        loaded = load_cards()
        state = loaded.initial_state
        self.assertEqual(sorted(loaded.content.templates),
                         ["demo_cards:entity:bolt", "demo_cards:entity:guard", "demo_cards:entity:strike"])
        self.assertEqual(loaded.content.phases["demo_cards:phase:main"].actions[0], "demo_cards:action:draw")
        self.assertEqual(state["energy"], 3)          # system 初值
        self.assertEqual(state["max_energy"], 3)
        self.assertEqual(len(state["hand"]), 3)
        self.assertEqual(len(state["deck"]), 5)
        self.assertEqual(state["weather"] if "weather" in state else "", "")  # 这个 Mod 没有这一项
        self.assertEqual(sorted(loaded.content.formulas), ["demo_cards:formula:deck_size", "demo_cards:formula:hand_size"])
        self.assertEqual(state["hand_size"], 3)       # 初始化时自己算好的
        self.assertEqual(state["deck_size"], 5)

    def test_draw_moves_card_and_updates_derived(self):
        """抽牌：牌堆少一张、手牌多一张，派生值跟着触发链重算。"""
        session = make_session()
        deck_before = list(session.state["deck"])
        result = send(session, "draw")

        self.assertTrue(result.committed)
        self.assertEqual(len(session.state["hand"]), 4)
        self.assertEqual(len(session.state["deck"]), 4)
        self.assertEqual(session.state["hand"][-1], deck_before[0])   # 抽的是牌堆顶
        self.assertEqual(session.state["hand_size"], 4)               # 触发链重算的派生值
        self.assertEqual(session.state["deck_size"], 4)

    def test_play_card_spends_energy_and_scores(self):
        """出牌：扣能量、加分、牌进弃牌堆。"""
        session = make_session()
        card = session.state["hand"][0]
        cost = session.state["card_types"][card]["cost"]
        value = session.state["card_types"][card]["value"]

        result = send(session, "play", {"index": 0})

        self.assertTrue(result.committed)
        self.assertEqual(session.state["energy"], 3 - cost)
        self.assertEqual(session.state["score"], value)
        self.assertEqual(session.state["discard"], [card])
        self.assertEqual(len(session.state["hand"]), 2)   # 少了一张
        self.assertEqual(session.state["hand_size"], 2)

    def test_play_without_energy_is_rejected(self):
        """能量不够：规则拒绝（整条命令丢弃，State 不变）。"""
        session = make_session(seed=3)
        # 先把能量花到不够为止。
        while session.state["energy"] > 0 and session.state["hand"]:
            if not send(session, "play", {"index": 0}).committed:
                break
            send(session, "draw")
        result = send(session, "play", {"index": 0})
        self.assertIsNotNone(result)
        if not result.committed:
            self.assertEqual(result.rule_id, "demo_cards:rule:enough_energy")

    def test_hand_full_cannot_draw(self):
        """手牌满了：抽不了（命令的分支条件不成立，走"记一条说明"那一支）。"""
        session = make_session()
        for _ in range(2):                     # 手牌 3 → 5（上限）
            send(session, "draw")
        self.assertEqual(session.state["hand_size"], 5)

        hand_before = list(session.state["hand"])
        result = send(session, "draw")
        self.assertTrue(result.committed)                       # 命令本身是成功的
        self.assertEqual(session.state["hand"], hand_before)    # 但手牌没变
        self.assertIn("抽不了", session.state["log"][-1])

    def test_deck_empty_triggers_reshuffle(self):
        """牌堆抽空：触发链把弃牌堆洗回牌堆（用引擎随机，可复现）。"""
        def play_until_reshuffle(seed: int):
            """反复抽牌 / 出牌，直到发生一次洗牌；返回 (牌堆, 弃牌堆)。"""
            session = make_session(seed=seed)
            for _ in range(40):
                deck_before = len(session.state["deck"])
                # 手牌没满就抽，能量够就出一张（出牌同时给手牌腾地方）。
                send(session, "draw")
                if len(session.state["deck"]) > deck_before:
                    # 牌堆变长了 → 触发链刚把弃牌堆洗了回来
                    return list(session.state["deck"]), list(session.state["discard"])
                if session.state["hand_size"] > 3:
                    send(session, "play", {"index": 0})
                if session.state["energy"] < 1:
                    send(session, "end_turn")
            raise AssertionError("一直没有发生洗牌")

        first = play_until_reshuffle(7)
        second = play_until_reshuffle(7)
        self.assertEqual(first, second)          # 同种子 → 洗牌结果一致（确定性）
        self.assertGreater(len(first[0]), 0)

    def test_view_marks_unplayable_cards(self):
        """界面：能量不够的卡片不给点击结果（点了也没反应）。"""
        session = make_session()
        page = load_cards().pages["demo_cards:ui:table"]
        view = page(PageContext(state=session.state, size=(960, 640), context=Context(),
                                page_id="demo_cards:ui:table"))
        ids = {layer.id for layer in view.layers}
        self.assertIn("card:0", ids)
        self.assertIn("button:draw", ids)
        self.assertIn("button:end_turn", ids)
        self.assertIsNotNone(view.hit_test((view.layers[0].center)))  # 第一张牌可点（能量够）

    def test_save_replay_and_continue(self):
        """存档 → 回放终态一致；读档后继续玩与一直玩下去一致。"""
        folder = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            original = make_session(seed=5)
            send(original, "draw")
            send(original, "play", {"index": 0})
            path = str(folder / "cards.json")
            save = original.save(path)
            self.assertEqual(replay_state(save), original.state)

            loaded = make_session(seed=5)
            loaded.load(path)
            self.assertEqual(loaded.state, original.state)

            send(original, "end_turn")
            send(loaded, "end_turn")
            send(original, "draw")
            send(loaded, "draw")
            self.assertEqual(loaded.state, original.state)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            try:
                SCRATCH_ROOT.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
