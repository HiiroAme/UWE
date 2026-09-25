"""dispatcher 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/pipeline/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 输入 → 映射 → 入队 → 结算 → 提交的整条路径，以及队列的先进先出；
  - 输入映射的条件与参数（读真实 State、参数来自输入数据）；
  - 没有匹配映射、命令定义缺失、规则拒绝、服务异常 → 丢弃并对 State 无影响；
  - 事件链产生的变化量并入同一个 CommandDelta；
  - 结算批次变化量与 Mod 标签；
  - 提交失败 → 致命（fatal）。
"""

import unittest

from core.delta import DataType, Operation
from core.logger import LogLevel
from core.pipeline import CommandStatus, Input
from .pipeline_fixtures import entry, make_command, make_runtime, make_services, make_state


def click(node: str, *, timestamp: float = 1.0, kind: str = "click_node") -> Input:
    """造一个"点节点"的输入。

    输入：
        node: 目标节点；timestamp: 交互时间；kind: 输入类别。
    输出：
        Input。
    异常：
        无。
    变量：
        无。
    """
    return Input(kind=kind, data={"node": node}, timestamp=timestamp, source="ui")


class TestInputMapping(unittest.TestCase):
    """输入映射（§9 第 2、3 步）。"""

    def test_submit_input_creates_and_enqueues_command(self):
        """输入映射成命令并入队：参数来自输入数据。"""
        runtime, _ = make_runtime()
        command = runtime.dispatcher.submit_input(click("n2", timestamp=5.0))
        self.assertIsNotNone(command)
        self.assertEqual(command.definition_id, "demo:command:move")
        self.assertEqual(command.payload, {"node": "n2"})
        self.assertEqual(command.source, "ui")
        self.assertEqual(command.created_at, 5.0)
        self.assertEqual(runtime.dispatcher.queue_length, 1)
        # 入队不会立刻改数据：要等结算。
        self.assertEqual(runtime.state["units"]["u1"]["position"], "n1")

    def test_unknown_kind_is_dropped(self):
        """没有匹配的输入类别：返回 None，队列不增加。"""
        runtime, sink = make_runtime()
        self.assertIsNone(runtime.dispatcher.submit_input(click("n2", kind="click_empty")))
        self.assertEqual(runtime.dispatcher.queue_length, 0)
        self.assertTrue(any(record.level is LogLevel.WARN for record in sink.records()))

    def test_condition_false_then_next_candidate(self):
        """同一个类别有多条映射时，按注册顺序取第一条条件成立的。"""
        entries = [
            entry("demo:service:s", "service", {}),
            entry("demo:action:a", "action", {"steps": [{"service": "demo:service:s", "args": {"node": ["arg", "node"]}}]}),
            entry(
                "demo:command:c",
                "command",
                {"branches": [{"condition": True, "actions": [{"action": "demo:action:a"}]}]},
            ),
            entry(
                "demo:input:pick",
                "input",
                {
                    "kind": "pick",
                    "command": "demo:command:c",
                    "condition": ["==", ["arg", "node"], "n1"],
                    "payload": {"node": "first"},
                },
            ),
            entry(
                "demo:input:pick_backup",
                "input",
                {"kind": "pick", "command": "demo:command:c", "condition": True, "payload": {"node": "second"}},
            ),
        ]
        runtime, _ = make_runtime(entries=entries)
        self.assertEqual(runtime.dispatcher.submit_input(Input("pick", {"node": "n1"}, 1.0, "ui")).payload,
                         {"node": "first"})
        self.assertEqual(runtime.dispatcher.submit_input(Input("pick", {"node": "n2"}, 2.0, "ui")).payload,
                         {"node": "second"})

    def test_no_candidate_matches(self):
        """有同类别映射但条件都不成立：返回 None（丢弃并记日志）。"""
        entries = [
            entry("demo:service:s", "service", {}),
            entry("demo:action:a", "action", {"steps": [{"service": "demo:service:s", "args": {"node": ["arg", "node"]}}]}),
            entry("demo:command:c", "command", {"branches": [{"condition": True, "actions": [{"action": "demo:action:a"}]}]}),
            entry(
                "demo:input:pick",
                "input",
                {"kind": "pick", "command": "demo:command:c", "condition": ["==", ["arg", "node"], "n1"]},
            ),
        ]
        runtime, _ = make_runtime(entries=entries)
        self.assertIsNone(runtime.dispatcher.submit_input(Input("pick", {"node": "n9"}, 1.0, "ui")))
        self.assertEqual(runtime.dispatcher.queue_length, 0)


class TestSettlement(unittest.TestCase):
    """结算（§9 第 4～11 步）。"""

    def test_input_to_state_end_to_end(self):
        """输入 → 命令 → 动作 → 服务 → 事件链 → 提交：数据真的进了真实 State。"""
        runtime, _ = make_runtime()
        runtime.dispatcher.submit_input(click("n2"))
        settlement = runtime.dispatcher.trigger_settlement(["turn:1"])

        self.assertTrue(settlement.ok)
        self.assertEqual(settlement.committed_count, 1)
        self.assertEqual(settlement.labels, ("turn:1",))
        self.assertEqual(runtime.state["units"]["u1"]["position"], "n2")
        self.assertEqual(runtime.state["units"]["u1"]["ap"], 2)
        self.assertEqual(runtime.state["units"]["u1"]["moved"], True)  # 事件链的产物
        self.assertEqual(runtime.dispatcher.queue_length, 0)

    def test_trigger_deltas_join_the_same_command_delta(self):
        """事件链产生的变化量与触发它的命令一起提交（同一个 CommandDelta）。"""
        runtime, _ = make_runtime()
        runtime.dispatcher.submit_input(click("n2"))
        settlement = runtime.dispatcher.trigger_settlement()

        result = settlement.commands[0]
        self.assertEqual(
            [delta.path for delta in result.deltas],
            ["/units/u1/position", "/units/u1/ap", "/units/u1/moved"],
        )
        self.assertEqual(settlement.deltas, result.deltas)

    def test_queue_is_first_in_first_out(self):
        """队列按先进先出处理：两次移动依次生效。"""
        runtime, _ = make_runtime()
        runtime.dispatcher.submit_input(click("n2", timestamp=1.0))
        runtime.dispatcher.submit_input(click("n3", timestamp=2.0))
        settlement = runtime.dispatcher.trigger_settlement()

        self.assertEqual([result.command_id for result in settlement.commands], ["demo:cmd:1", "demo:cmd:2"])
        self.assertEqual(runtime.state["units"]["u1"]["position"], "n3")
        self.assertEqual(runtime.state["units"]["u1"]["ap"], 1)

    def test_empty_queue_settlement(self):
        """空队列结算：什么都不发生，结果也是干净的。"""
        runtime, _ = make_runtime()
        settlement = runtime.dispatcher.trigger_settlement()
        self.assertEqual(settlement.commands, ())
        self.assertEqual(settlement.deltas, ())
        self.assertTrue(settlement.ok)

    def test_labels_must_be_strings(self):
        """Mod 标签必须是字符串序列（不能直接给一个字符串）。"""
        runtime, _ = make_runtime()
        with self.assertRaises(TypeError):
            runtime.dispatcher.trigger_settlement("turn:1")


class TestDiscarding(unittest.TestCase):
    """丢弃与致命失败。"""

    def test_rule_rejection_discards_command(self):
        """规则拒绝：整个命令丢弃，真实 State 不变，后面的命令继续处理。"""
        runtime, sink = make_runtime(state={"turn": 1, "units": {"u1": {"ap": 0, "position": "n1", "moved": False}}})
        runtime.dispatcher.submit_input(click("n2"))
        settlement = runtime.dispatcher.trigger_settlement()

        result = settlement.commands[0]
        self.assertIs(result.status, CommandStatus.DISCARDED)
        self.assertEqual(result.rule_id, "demo:rule:has_ap")
        self.assertEqual(result.reason, "行动力不足")
        self.assertEqual(runtime.state["units"]["u1"]["position"], "n1")
        self.assertEqual(settlement.discarded_count, 1)
        self.assertFalse(settlement.ok)
        self.assertTrue(any(record.level is LogLevel.WARN for record in sink.records()))

    def test_service_failure_discards_command(self):
        """服务异常：命令丢弃并记原因，真实 State 不变。"""

        def boom(api, args):
            raise RuntimeError("脚本炸了")

        runtime, sink = make_runtime(services=make_services(apply_move=boom))
        runtime.dispatcher.submit_input(click("n2"))
        settlement = runtime.dispatcher.trigger_settlement()

        result = settlement.commands[0]
        self.assertIs(result.status, CommandStatus.DISCARDED)
        self.assertIn("脚本炸了", result.reason)
        self.assertEqual(runtime.state["units"]["u1"]["position"], "n1")
        self.assertTrue(any(record.level is LogLevel.ERROR for record in sink.records()))

    def test_missing_command_definition_discards_command(self):
        """命令定义不存在：编排失败 → 丢弃。"""
        runtime, _ = make_runtime()
        runtime.dispatcher.enqueue(make_command(definition_id="demo:command:nope"))
        settlement = runtime.dispatcher.trigger_settlement()

        result = settlement.commands[0]
        self.assertIs(result.status, CommandStatus.DISCARDED)
        self.assertIn("命令定义不存在", result.reason)

    def test_no_branch_matches_commits_nothing(self):
        """没有任何分支成立：命令正常提交，但没有任何变化量。"""
        entries = [
            entry("demo:action:a", "action", {"steps": []}),
            entry("demo:command:c", "command", {"branches": [{"condition": False, "actions": [{"action": "demo:action:a"}]}]}),
        ]
        runtime, _ = make_runtime(entries=entries)
        runtime.dispatcher.enqueue(make_command(definition_id="demo:command:c"))
        settlement = runtime.dispatcher.trigger_settlement()

        self.assertIs(settlement.commands[0].status, CommandStatus.COMMITTED)
        self.assertEqual(settlement.commands[0].deltas, ())
        self.assertEqual(settlement.deltas, ())

    def test_commit_conflict_is_fatal(self):
        """提交时真实 State 与记录不一致：结果标致命，队列停止处理。"""
        state = make_state()

        def sabotage(api, args):
            """先记一条变化量，再绕过引擎直接改真实 State（模拟被别处改过）。"""
            old = api.get("/units/u1/ap")
            api.emit("/units/u1/ap", Operation.MODIFY, DataType.NUMBER, value=old - 1, old_value=old)
            state["units"]["u1"]["ap"] = 99

        runtime, sink = make_runtime(state=state, services=make_services(apply_move=sabotage))
        runtime.dispatcher.submit_input(click("n2"))
        runtime.dispatcher.submit_input(click("n3"))  # 这条不该被处理
        settlement = runtime.dispatcher.trigger_settlement()

        self.assertTrue(settlement.fatal)
        self.assertIs(settlement.commands[0].status, CommandStatus.FAILED)
        self.assertEqual(len(settlement.commands), 1)
        self.assertEqual(runtime.dispatcher.queue_length, 1)
        self.assertTrue(any(record.level is LogLevel.FATAL for record in sink.records()))


if __name__ == "__main__":
    unittest.main()
