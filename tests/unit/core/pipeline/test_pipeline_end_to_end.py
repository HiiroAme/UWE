"""整条管线的端到端测试（§20 初版三项里的两项：确定性测试、端到端管线测试）。

位置：tests/unit/core/pipeline/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 输入 → 命令 → 动作 → 服务 → 事件链 → 提交 → 读 State 的完整路径；
  - 确定性：相同 State + 相同种子 + 相同输入 ⇒ 完全相同的 State 与变化量（§13）；
  - 随机数走引擎的随机模块，结果可复现；
  - 提交之前一切只发生在临时状态上（UI 拉取看到的仍是旧事实）。

（第三项"回放测试"要等存档与回放阶段，见 §18.2。）
"""

import unittest

from core.delta import DataType, Operation
from core.pipeline import CommandStatus, Input

from .pipeline_fixtures import entry, make_runtime


def random_entries() -> list:
    """造一套"掷骰子写进日志"的内容（用来验证随机可复现）。"""
    return [
        entry("demo:service:roll", "service", {}),
        entry(
            "demo:action:roll",
            "action",
            {"steps": [{"service": "demo:service:roll"}]},
        ),
        entry(
            "demo:command:roll",
            "command",
            {"branches": [{"condition": True, "actions": [{"action": "demo:action:roll"}]}]},
        ),
        entry("demo:input:roll", "input", {"kind": "roll", "command": "demo:command:roll"}),
    ]


def roll_services(rolls: list) -> dict:
    """返回"掷骰子"的服务实现：把点数追加到 /units/u1/log。

    输入：
        rolls: 外部收集器（测试用来记录每次掷出的点数）。
    输出：
        服务表。
    异常：
        无。
    变量：
        convert / roll_dice: 两个服务函数。
    """

    def roll_dice(api, args):
        points = api.rand_int(1, 6)
        rolls.append(points)
        old = api.get("/units/u1/log")
        api.emit(
            "/units/u1/log",
            Operation.MODIFY,
            DataType.LIST,
            value=list(old) + [points],
            old_value=old,
        )

    return {"demo:service:roll": roll_dice}


class TestFullFlow(unittest.TestCase):
    """端到端：输入进、State 变。"""

    def test_input_to_state(self):
        """一次点击走完整条管线：真实 State 只在本命令提交时被改。"""
        runtime, sink = make_runtime()
        dispatcher = runtime.dispatcher

        dispatcher.submit_input(Input("click_node", {"node": "n5"}, 3.0, "ui"))
        # 结算之前：UI 拉取看到的仍是旧事实（§10 只读直读 State）。
        self.assertEqual(runtime.state["units"]["u1"]["position"], "n1")

        settlement = dispatcher.trigger_settlement(["turn:1", "phase:movement"])
        self.assertTrue(settlement.ok)
        self.assertIs(settlement.commands[0].status, CommandStatus.COMMITTED)
        self.assertEqual(runtime.state["units"]["u1"]["position"], "n5")
        self.assertEqual(runtime.state["units"]["u1"]["ap"], 2)
        self.assertEqual(runtime.state["units"]["u1"]["moved"], True)
        self.assertEqual(settlement.labels, ("turn:1", "phase:movement"))

        # 日志里能追溯：TRACE（变化量）、DEBUG（服务调用）、INFO（提交、结算点）都有。
        levels = {record.level.name for record in sink.records()}
        self.assertTrue({"TRACE", "DEBUG", "INFO"} <= levels)

    def test_two_commands_in_one_settlement(self):
        """一个批次里的两个命令按顺序提交，批次变化量是两者的聚合。"""
        runtime, _ = make_runtime()
        dispatcher = runtime.dispatcher
        dispatcher.submit_input(Input("click_node", {"node": "n2"}, 1.0, "ui"))
        dispatcher.submit_input(Input("click_node", {"node": "n4"}, 2.0, "ui"))

        settlement = dispatcher.trigger_settlement(["turn:1"])
        self.assertEqual(settlement.committed_count, 2)
        self.assertEqual(runtime.state["units"]["u1"]["position"], "n4")
        self.assertEqual(runtime.state["units"]["u1"]["ap"], 1)
        first, second = settlement.commands
        self.assertEqual(settlement.deltas, first.deltas + second.deltas)


class TestDeterminism(unittest.TestCase):
    """确定性契约（§13）。"""

    def _run(self, seed: int, rolls: list) -> tuple:
        """跑一遍"掷两次骰子"的流程，返回 (State, 结算结果, 命令序列)。"""
        runtime, _ = make_runtime(
            entries=random_entries(),
            services=roll_services(rolls),
            seed=seed,
        )
        dispatcher = runtime.dispatcher
        dispatcher.submit_input(Input("roll", {}, 1.0, "ui"))
        dispatcher.submit_input(Input("roll", {}, 2.0, "ui"))
        settlement = dispatcher.trigger_settlement(["turn:1"])
        return runtime.state, settlement

    def test_same_seed_same_state_and_deltas(self):
        """同种子同输入：State、变化量、命令 id 全部一致。"""
        rolls_a: list = []
        rolls_b: list = []
        state_a, settlement_a = self._run(2026, rolls_a)
        state_b, settlement_b = self._run(2026, rolls_b)

        self.assertEqual(rolls_a, rolls_b)
        self.assertEqual(state_a, state_b)
        self.assertEqual(settlement_a.deltas, settlement_b.deltas)
        self.assertEqual(
            [result.command_id for result in settlement_a.commands],
            [result.command_id for result in settlement_b.commands],
        )

    def test_different_seed_changes_outcome(self):
        """不同种子：随机结果一般不同（这里用一个确定会不同的种子对）。"""
        rolls_a: list = []
        rolls_b: list = []
        self._run(1, rolls_a)
        self._run(2, rolls_b)
        self.assertNotEqual(rolls_a, rolls_b)

    def test_random_state_roundtrip_keeps_future_rolls(self):
        """随机状态导出后恢复：后续掷出的点数与导出前一致（存档可复现的前提）。"""
        rolls_first: list = []
        runtime, _ = make_runtime(entries=random_entries(), services=roll_services(rolls_first), seed=99)
        runtime.dispatcher.submit_input(Input("roll", {}, 1.0, "ui"))
        runtime.dispatcher.trigger_settlement()
        saved = runtime.rng.state()

        expected = [runtime.rng.next_int(1, 6) for _ in range(5)]
        runtime.restore_random_state(saved)
        self.assertEqual([runtime.rng.next_int(1, 6) for _ in range(5)], expected)


if __name__ == "__main__":
    unittest.main()
