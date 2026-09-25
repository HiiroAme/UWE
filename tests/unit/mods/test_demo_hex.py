"""演示 Mod（mods/demo_hex）的端到端验收测试。

位置：tests/unit/mods/
运行：在仓库根执行 `python run_tests.py`。
覆盖（草案 §21 的最小闭环与验收标准）：
  - 加载演示 Mod：地图与相邻关系来自模块 pack_hex_grid，玩法实现全部来自模块，
    本 Mod 自己一个函数都不写（user_functions 为空）；
  - 移动、攻击（含随机伤害）、规则拒绝、触发链收尾（某方全灭 → 战斗结束）；
  - 结束回合（固定拆解分支）；
  - 存档 → 回放终态一致；读档后继续玩与一直玩下去一致；
  - P1 守卫：引擎核心（engine/src/core）里不出现这个 Mod 的任何名字。
"""

import shutil
import unittest
from pathlib import Path

from adapters import LocalFileSystem, PythonScriptLoader
from core.persistence import replay_state
from core.pipeline import CommandStatus, Input
from modload import ModLoader
from shell import GameSession

REPO_ROOT = Path(__file__).resolve().parents[3]
MODS_ROOT = REPO_ROOT / "mods"
MODULES_ROOT = REPO_ROOT / "modules"
DEMO_FOLDER = MODS_ROOT / "demo_hex"
CORE_ROOT = REPO_ROOT / "engine" / "src" / "core"
SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"


def load_demo():
    """加载演示 Mod。

    输入：无。
    输出：
        LoadedMod。
    异常：
        加载失败时由加载层抛出（测试会直接看到）。
    变量：
        loader: 加载器。
    """
    loader = ModLoader(LocalFileSystem(), PythonScriptLoader(), str(MODS_ROOT),
                       module_roots=[str(MODULES_ROOT)])
    return loader.load(loader.load_info(str(DEMO_FOLDER)))


def make_session(seed: int = 2026):
    """开一局演示游戏。

    输入：
        seed: 随机种子（攻击伤害来自它）。
    输出：
        GameSession。
    异常：
        无（加载失败会直接抛出）。
    变量：
        loaded: 加载好的 Mod。
    """
    loaded = load_demo()
    return GameSession(loaded, files=LocalFileSystem(), seed=seed)


def send(session, kind: str, data: dict, *, timestamp: float = 1.0):
    """送一条输入并立即结算。

    输入：
        session: 游戏会话；
        kind: 输入类别（move / attack / end_turn）；
        data: 输入数据；
        timestamp: 交互时间。
    输出：
        SettlementResult。
    异常：
        无。
    变量：
        无。
    """
    session.submit_input(Input(kind, data, timestamp, "ui"))
    return session.settle(["turn:1"])


def give_control_to(session, side: str) -> None:
    """结束回合直到控制权落到 side 手里（热座：一次只有一方能行动）。

    输入：
        session: 游戏会话；
        side: "red" 或 "blue"。
    输出：
        无。
    异常：
        AssertionError: 换了四次还没轮到这个阵营（Mod 数据写错了）。
    变量：
        无。
    """
    for _ in range(4):
        if session.state["control_side"] == side:
            return
        send(session, "end_turn", {})
    raise AssertionError(f"控制权没能交给 {side}")


def defeat_blue(session) -> None:
    """让红军把蓝军打到 0 血（行动力用完就换边再换回来）。

    输入：
        session: 游戏会话。
    输出：
        无。
    异常：
        AssertionError: 打不下来（随机伤害太小或规则写错）。
    变量：
        无。
    """
    give_control_to(session, "red")
    send(session, "move", {"unit": "red_1", "to": "n0_0"})
    for _ in range(80):
        if session.state["units"]["blue_1"]["hp"] <= 0:
            return
        give_control_to(session, "red")
        if session.state["units"]["red_1"]["ap"] <= 0:
            send(session, "end_turn", {})     # 交给蓝方
            continue                          # 下一圈再交回红方（行动力恢复）
        send(session, "attack", {"unit": "red_1", "target": "blue_1"})
    raise AssertionError("红军没能打下蓝军")


class TestDemoMod(unittest.TestCase):
    """演示 Mod 的加载与玩法。"""

    def test_load_shape(self):
        """加载结果：地图 7 格、相邻关系齐、两个单位、命令与触发都在。"""
        loaded = load_demo()
        state = loaded.initial_state
        self.assertEqual(len(state["nodes"]), 7)
        self.assertEqual(len(state["adjacency"]), 7)
        self.assertEqual(sorted(state["units"]), ["blue_1", "red_1"])
        self.assertEqual(state["units"]["red_1"]["at"], "n-1_0")
        self.assertEqual(state["units"]["blue_1"]["at"], "n1_0")
        self.assertEqual(state["control_side"], "red")     # 开局红方行动（热座）
        self.assertIsNotNone(loaded.content.command("demo_hex:command:move"))
        self.assertEqual(len(loaded.content.subscriptions("demo_hex:event:unit_destroyed")), 1)
        # 函数全部来自逻辑模块，Mod 自己不写任何函数；绑定名 adjacent 可用。
        self.assertEqual(loaded.user_functions, {})
        self.assertIn("adjacent", loaded.functions)
        self.assertIn("pack_hex_grid.distance.hex", loaded.functions)
        # 基线模块（pack_info.json 写 base=true 的那份）永远排在最前，
        # Mod 不写 uses 也自动带上：系统事件登记与通用工具服务都在它里面。
        # pack_path_hex 是跟着 pack_wargame_core 一起进来的：后者"回合开始刷控制区标记"
        # 要用前者的 zoc 实现（模块之间靠 requires 声明依赖，加载顺序"依赖先、被依赖后"）。
        self.assertEqual([module.id for module in loaded.modules],
                         ["pack_common", "pack_hex_grid", "pack_combat_basic",
                          "pack_path_hex", "pack_wargame_core"])
        # 模板（entity / node）与地形预设（terrain）都编译好了：node 已经合并 terrain 的属性。
        self.assertIn("demo_hex:entity:unit", loaded.content.templates)
        node_template = loaded.content.templates["demo_hex:node:plain"]
        self.assertEqual(node_template.kind, "node")
        self.assertEqual(set(node_template.attributes), {"terrain", "move_cost", "cover"})
        # phase：允许的动作是编译好的列表。
        self.assertEqual(
            loaded.content.phases["demo_hex:phase:action"].actions,
            (
                "demo_hex:action:move",
                "demo_hex:action:attack",
                "demo_hex:action:end_turn",
                "demo_hex:action:select",
                "demo_hex:action:clear_selection",
            ),
        )
        # system：初始值确实写进了初始 State（这几条是 init 钩子没写的）。
        self.assertEqual(state["weather"], "晴")
        self.assertEqual(state["rules_version"], 1)
        self.assertIn("/weather", loaded.system_paths)
        # 通配派生值：一条公式覆盖全部单位。
        self.assertEqual(loaded.content.formulas["demo_hex:formula:unit_power"].target, "/units/{unit}/power")

    def test_state_initialised_from_templates(self):
        """初始局面来自模板：单位属性与节点属性都带上了模板里的内容。"""
        state = load_demo().initial_state
        self.assertEqual(state["units"]["red_1"]["hp"], 10)
        self.assertEqual(state["units"]["red_1"]["power"], 14)
        self.assertEqual(state["nodes"]["n0_0"]["move_cost"], 1)
        self.assertEqual(state["nodes"]["n0_0"]["cover"], 0)      # 来自 terrain 预设
        self.assertEqual(state["phase"], "demo_hex:phase:action")
        self.assertIn("demo_hex:action:move", state["phase_actions"])

    def test_move_updates_state_and_fires_event(self):
        """移动：位置与行动力变化，事件 unit_moved 被标在变化量上。"""
        session = make_session()
        result = send(session, "move", {"unit": "red_1", "to": "n0_0"})

        self.assertTrue(result.ok)
        self.assertEqual(session.state["units"]["red_1"]["at"], "n0_0")
        self.assertEqual(session.state["units"]["red_1"]["ap"], 1)
        labels = {delta.label for delta in result.deltas}
        self.assertIn("demo_hex:event:unit_moved", labels)

    def test_move_to_non_adjacent_is_rejected_by_rule(self):
        """移动到不相邻的格子：规则拒绝，整个命令丢弃，State 不变。"""
        session = make_session()
        result = send(session, "move", {"unit": "red_1", "to": "n1_0"})

        self.assertFalse(result.ok)
        self.assertIs(result.commands[0].status, CommandStatus.DISCARDED)
        self.assertEqual(result.commands[0].rule_id, "demo_hex:rule:target_adjacent")
        self.assertEqual(session.state["units"]["red_1"]["at"], "n-1_0")

    def test_attack_uses_seeded_random_and_is_reproducible(self):
        """攻击伤害来自引擎的确定性随机：同种子同输入 → 同样的血量。"""
        first = make_session(seed=99)
        second = make_session(seed=99)
        for session in (first, second):
            send(session, "move", {"unit": "red_1", "to": "n0_0"})
            send(session, "attack", {"unit": "red_1", "target": "blue_1"})
        self.assertEqual(first.state["units"]["blue_1"]["hp"], second.state["units"]["blue_1"]["hp"])
        self.assertLess(first.state["units"]["blue_1"]["hp"], 10)

        third = make_session(seed=100)
        send(third, "move", {"unit": "red_1", "to": "n0_0"})
        send(third, "attack", {"unit": "red_1", "target": "blue_1"})
        # 换种子后结果不同（这里用一对确定会不同的种子，练的是"随机真的进了判定"）。
        self.assertNotEqual(first.state["units"]["blue_1"]["hp"], third.state["units"]["blue_1"]["hp"])

    def test_attack_without_ap_is_rejected(self):
        """行动力用完：规则 has_ap 拒绝攻击。"""
        session = make_session()
        send(session, "move", {"unit": "red_1", "to": "n0_0"})     # ap: 2 → 1
        send(session, "attack", {"unit": "red_1", "target": "blue_1"})  # ap: 1 → 0
        result = send(session, "attack", {"unit": "red_1", "target": "blue_1"})

        self.assertIs(result.commands[0].status, CommandStatus.DISCARDED)
        self.assertEqual(result.commands[0].rule_id, "demo_hex:rule:has_ap")

    def test_friendly_fire_is_rejected(self):
        """打自己人：规则 enemy_target 拒绝。"""
        session = make_session()
        send(session, "move", {"unit": "red_1", "to": "n0_0"})
        result = send(session, "attack", {"unit": "red_1", "target": "red_1"})
        self.assertEqual(result.commands[0].rule_id, "demo_hex:rule:enemy_target")

    def test_end_turn_restores_action_points(self):
        """结束回合：回合 +1、行动力恢复（固定拆解分支）。"""
        session = make_session()
        send(session, "move", {"unit": "red_1", "to": "n0_0"})
        self.assertEqual(session.state["units"]["red_1"]["ap"], 1)

        result = send(session, "end_turn", {})
        self.assertTrue(result.ok)
        self.assertEqual(session.state["turn"], 2)
        self.assertEqual(session.state["units"]["red_1"]["ap"], 2)
        self.assertEqual(session.state["units"]["blue_1"]["ap"], 2)
        # 结束回合的第二步是"把控制权交给对方"（红 → 蓝）。
        self.assertEqual(session.state["control_side"], "blue")

    def test_trigger_finishes_battle_when_one_side_is_wiped(self):
        """触发链：把蓝军打到 0 血后，自动置 game_over 与 winner。"""
        session = make_session(seed=7)
        defeat_blue(session)

        self.assertTrue(session.state["game_over"])
        self.assertEqual(session.state["winner"], "red")
        self.assertEqual(session.state["units"]["blue_1"]["hp"], 0)
        self.assertTrue(session.state["units"]["blue_1"]["destroyed"])
        self.assertIn("战斗结束：红方获胜", session.state["log"][-1])
        # 被打死的单位撤出地图：记录还在（当阵亡名单），但不再占格子。
        self.assertEqual(session.state["units"]["blue_1"]["at"], "")

    def test_only_the_controlling_side_can_act(self):
        """热座：只有当前控制方能操作；结束回合把控制权交给对方。"""
        session = make_session()
        self.assertEqual(session.state["control_side"], "red")

        result = send(session, "select", {"unit": "blue_1"})
        self.assertIs(result.commands[0].status, CommandStatus.DISCARDED)
        self.assertEqual(result.commands[0].rule_id, "demo_hex:rule:own_side")

        send(session, "end_turn", {})
        self.assertEqual(session.state["control_side"], "blue")
        result = send(session, "select", {"unit": "red_1"})
        self.assertEqual(result.commands[0].rule_id, "demo_hex:rule:own_side")
        self.assertTrue(send(session, "select", {"unit": "blue_1"}).commands[0].committed)

    def test_destroyed_unit_is_retired_and_does_not_block(self):
        """阵亡：撤出地图、不能再操作、也不挡路（但记录留着当阵亡名单）。"""
        session = make_session(seed=7)
        # 先给蓝方补一个单位，这样打死一个之后战斗还没结束，能继续验证。
        send(session, "recruit", {"unit": "blue_2", "side": "blue", "at": "n0_1"})
        defeat_blue(session)

        blue = session.state["units"]["blue_1"]
        self.assertEqual(blue["hp"], 0)
        self.assertEqual(blue["at"], "")           # 撤出地图
        self.assertEqual(blue["destroyed"], True)  # 记录留着
        self.assertFalse(session.state["game_over"])  # 蓝方还有 blue_2，战斗继续

        # 已经撤出的单位不能被选中（轮到蓝方时也选不中它）。
        give_control_to(session, "blue")
        result = send(session, "select", {"unit": "blue_1"})
        self.assertEqual(result.commands[0].rule_id, "demo_hex:rule:unit_alive")

        # 也不挡路：红军可以走进蓝军原来那一格。
        give_control_to(session, "red")
        session.state["units"]["red_1"]["ap"] = 2   # 直接给行动力，专心验"不挡路"
        result = send(session, "move", {"unit": "red_1", "to": "n1_0"})
        self.assertTrue(result.commands[0].committed)
        self.assertEqual(session.state["units"]["red_1"]["at"], "n1_0")

    def test_battle_over_blocks_every_action(self):
        """战斗结束后：选 / 走 / 打 / 结束回合 全被 battle_running 拦下。"""
        session = make_session(seed=7)
        defeat_blue(session)
        self.assertTrue(session.state["game_over"])

        for kind, data in (("select", {"unit": "red_1"}),
                           ("move", {"unit": "red_1", "to": "n0_1"}),
                           ("attack", {"unit": "red_1", "target": "blue_1"}),
                           ("end_turn", {})):
            with self.subTest(kind=kind):
                result = send(session, kind, data)
                self.assertIs(result.commands[0].status, CommandStatus.DISCARDED)
                self.assertEqual(result.commands[0].rule_id, "demo_hex:rule:battle_running")

    def test_save_replay_and_continue(self):
        """存档 → 回放终态一致；读档后继续玩与一直玩下去一致。"""
        folder = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            original = make_session(seed=5)
            send(original, "move", {"unit": "red_1", "to": "n0_0"})
            send(original, "attack", {"unit": "red_1", "target": "blue_1"})

            path = str(folder / "slot1.json")
            save = original.save(path)
            self.assertEqual(replay_state(save), original.state)

            loaded = make_session(seed=5)
            loaded.load(path)
            self.assertEqual(loaded.state, original.state)

            # 两条线做同样的事：再打一次 + 结束回合。
            send(original, "attack", {"unit": "red_1", "target": "blue_1"})
            send(loaded, "attack", {"unit": "red_1", "target": "blue_1"})
            send(original, "end_turn", {})
            send(loaded, "end_turn", {})
            self.assertEqual(loaded.state, original.state)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            try:
                SCRATCH_ROOT.rmdir()
            except OSError:
                pass

    def test_core_does_not_know_this_mod(self):
        """P1 守卫：引擎核心的任何文件里都不出现这个 Mod 的名字。"""
        needle = "demo_hex"
        offenders = []
        for path in sorted(CORE_ROOT.rglob("*.py")):
            if needle in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [], f"引擎核心出现了演示 Mod 的名字：{offenders}")

    def test_derived_values_are_recomputed_by_triggers(self):
        """派生值：基础值一变，触发链调用引擎原子服务把它重算（§6.5 / D-12）。"""
        session = make_session()
        self.assertEqual(session.state["units"]["red_1"]["power"], 14)  # 开局：2*2 + 10
        self.assertEqual(session.state["advantage"], 0)                 # 14 - 14

        send(session, "move", {"unit": "red_1", "to": "n0_0"})          # 行动力 2 → 1

        self.assertEqual(session.state["units"]["red_1"]["power"], 12)  # 2*1 + 10
        self.assertEqual(session.state["advantage"], -2)                # 12 - 14（第 2 层用第 1 层结果）

    def test_derived_values_heal_on_save(self):
        """存读自愈：派生值被改坏之后，保存时会按公式重算纠正（D-33 / O-01）。"""
        folder = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            session = make_session()
            # 把派生值改坏，再拍快照：这模拟"旧存档里带着算错的派生值"——
            # 坏值进了基础 State，自愈才有意义（快照之后直接改 State 属于绕过引擎，会被报冲突）。
            session.state["units"]["red_1"]["power"] = 999
            session.state["advantage"] = 999
            session.take_snapshot()

            save = session.save(str(folder / "slot.json"))
            self.assertEqual(session.state["units"]["red_1"]["power"], 14)
            self.assertEqual(session.state["advantage"], 0)

            # 存档里带的也是纠正过的基础 State（回放出来的终态同样正确）。
            self.assertEqual(replay_state(save)["units"]["red_1"]["power"], 14)
            # 这一批"不是命令产生的"改动会以 engine:derived 标签记进批次日志，
            # 这样"快照 + 批次"的回放才完整。
            labels = [label for record in save.settlements for label in record.labels]
            self.assertIn("engine:derived", labels)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            try:
                SCRATCH_ROOT.rmdir()
            except OSError:
                pass

    def test_replay_covers_derived_refresh(self):
        """触发链里的派生值刷新随命令一起提交：回放能把它叠加回来（不然回放会漂）。"""
        session = make_session()
        session.take_snapshot()  # 先拍快照：之后的批次才会进存档（§18.1 的增量存档）
        send(session, "move", {"unit": "red_1", "to": "n0_0"})
        send(session, "attack", {"unit": "red_1", "target": "blue_1"})
        save = session.build_save()

        replayed = replay_state(save)
        self.assertEqual(replayed["units"]["red_1"]["power"], session.state["units"]["red_1"]["power"])
        self.assertEqual(replayed["advantage"], session.state["advantage"])
        self.assertEqual(replayed, session.state)

        # 派生值的变化量就在命令那一批里（触发链与命令一起提交，§11.2）。
        paths = [delta.path for record in save.settlements for delta in record.deltas]
        self.assertIn("/units/red_1/power", paths)
        self.assertIn("/advantage", paths)

    def test_batch_derived_values_cover_every_unit(self):
        """通配派生值：一条公式把两个单位的战斗力都算出来（F-05）。"""
        session = make_session()
        self.assertEqual(session.state["units"]["red_1"]["power"], 14)
        self.assertEqual(session.state["units"]["blue_1"]["power"], 14)

        # 热座：先把控制权交给蓝方，蓝军才能动（行动力 2 → 1）。
        send(session, "end_turn", {})
        self.assertEqual(session.state["control_side"], "blue")
        send(session, "move", {"unit": "blue_1", "to": "n0_0"})   # 蓝军行动力 2 → 1

        self.assertEqual(session.state["units"]["blue_1"]["power"], 12)
        self.assertEqual(session.state["units"]["red_1"]["power"], 14)  # 红军没动，不受影响
        self.assertEqual(session.state["advantage"], 2)                 # 14 - 12

    def test_recruit_creates_instance_from_template(self):
        """招募：引擎原子服务按 entity 模板在指定路径建实例（F-05 的批量 / 模板用法）。"""
        session = make_session()
        result = send(session, "recruit", {"unit": "red_2", "side": "red", "at": "n-1_0"})

        self.assertTrue(result.ok)
        new_unit = session.state["units"]["red_2"]
        self.assertEqual(new_unit["hp"], 10)          # 来自模板
        self.assertEqual(new_unit["ap"], 2)
        self.assertEqual(new_unit["power"], 14)
        self.assertEqual(new_unit["side"], "red")     # 来自本次覆盖
        self.assertEqual(new_unit["at"], "n-1_0")
        # 新单位也能被通配派生值管到：让它动一步，战斗力应当跟着重算。
        send(session, "move", {"unit": "red_2", "to": "n0_0"})
        self.assertEqual(session.state["units"]["red_2"]["power"], 12)

    def test_recruit_existing_unit_is_reported(self):
        """招募一个已经存在的单位 id：走"恒真分支"记一条说明，不产生变化量。"""
        session = make_session()
        result = send(session, "recruit", {"unit": "red_1", "side": "red", "at": "n-1_0"})
        self.assertTrue(result.ok)
        self.assertEqual(session.state["units"]["red_1"]["hp"], 10)
        self.assertIn("招募被忽略", session.state["log"][-1])

    def test_grid_layers_come_from_module_widget(self):
        """格子层由模块的界面件产出：Mod 的视图脚本只负责传参，不自己画网格。"""
        loaded = load_demo()
        make_grid = loaded.functions["pack_hex_grid.hex_grid.plain"]
        nodes = loaded.initial_state["nodes"]

        layers = make_grid(nodes=nodes, center=(100, 100), size=10.0)

        self.assertEqual(len(layers), len(nodes))
        self.assertTrue(all(layer.id.startswith("grid:") for layer in layers))
        # 界面件是纯函数：同样的参数两次结果一样（不读 State、不取随机）。
        self.assertEqual(layers, make_grid(nodes=nodes, center=(100, 100), size=10.0))


if __name__ == "__main__":
    unittest.main()
