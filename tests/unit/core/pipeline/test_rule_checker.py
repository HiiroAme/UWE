"""rule_checker 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/pipeline/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 全部规则通过时不报错；
  - 某条规则不成立 → RuleRejection（带上规则 id 与原因）；
  - 第一条不成立就停，后面的规则不再求值；
  - 规则条件可以用 Action 的参数（["arg", 名字]）；
  - 条件求值报错 → 同样抛 RuleRejection，并说明"无法判定"。
"""

import unittest

from core.logger import LogLevel, Logger, MemorySink
from core.pipeline import RuleChecker, RuleRejection
from core.temp_state import TempState

from .pipeline_fixtures import entry, make_content, make_state


def action_definition(rules: list[str]):
    """造"一个带若干规则的空动作"，并返回它的编译结果。

    输入：
        rules: 规则 id 列表。
    输出：
        CompiledAction。
    异常：
        无（内容不合法会在测试里显式暴露）。
    变量：
        无。
    """
    content = make_content(
        [
            entry("demo:rule:ap_ok", "rule", {"condition": [">", ["get", "/units/u1/ap"], 0], "message": "行动力不足"}),
            entry("demo:rule:turn_ok", "rule", {"condition": ["==", ["get", "/turn"], 1], "message": "不是第一回合"}),
            entry("demo:rule:needs_arg", "rule", {"condition": ["==", ["arg", "mode"], "fast"], "message": "模式不对"}),
            entry("demo:rule:bad_path", "rule", {"condition": ["get", "/nope"], "message": "路径写错了"}),
            entry("demo:action:a", "action", {"rules": rules, "steps": []}),
        ]
    )
    return content.action("demo:action:a")


class TestCheck(unittest.TestCase):
    """规则检查的通过与拒绝。"""

    def test_all_rules_pass(self):
        """全部规则成立：不抛异常。"""
        checker = RuleChecker()
        checker.check(
            action_definition(["demo:rule:ap_ok", "demo:rule:turn_ok"]),
            TempState(make_state(), "c1"),
            command_id="c1",
            action_id="a1",
        )

    def test_rejection_carries_rule_and_reason(self):
        """规则不成立：抛 RuleRejection，带规则 id 与原因。"""
        checker = RuleChecker()
        with self.assertRaises(RuleRejection) as ctx:
            checker.check(
                action_definition(["demo:rule:turn_ok"]),
                TempState({"turn": 5, "units": {"u1": {"ap": 3}}}, "c1"),
                command_id="c1",
                action_id="a1",
            )
        self.assertEqual(ctx.exception.rule_id, "demo:rule:turn_ok")
        self.assertEqual(ctx.exception.reason, "不是第一回合")
        self.assertEqual(ctx.exception.action_id, "a1")

    def test_stops_at_first_failure(self):
        """第一条不成立就停：后面那条（会因路径不存在而报错）不再求值。"""
        checker = RuleChecker()
        with self.assertRaises(RuleRejection) as ctx:
            checker.check(
                action_definition(["demo:rule:turn_ok", "demo:rule:bad_path"]),
                TempState({"turn": 5, "units": {"u1": {"ap": 3}}}, "c1"),
                command_id="c1",
                action_id="a1",
            )
        self.assertEqual(ctx.exception.rule_id, "demo:rule:turn_ok")

    def test_condition_can_use_action_args(self):
        """规则条件能读 Action 的参数（["arg", 名字]）。"""
        checker = RuleChecker()
        definition = action_definition(["demo:rule:needs_arg"])
        checker.check(
            definition, TempState(make_state(), "c1"), command_id="c1", action_id="a1", args={"mode": "fast"}
        )
        with self.assertRaises(RuleRejection) as ctx:
            checker.check(
                definition, TempState(make_state(), "c1"), command_id="c1", action_id="a1", args={"mode": "slow"}
            )
        self.assertEqual(ctx.exception.reason, "模式不对")

    def test_condition_error_becomes_rejection(self):
        """条件求值报错：抛 RuleRejection，原因写明"无法判定"。"""
        checker = RuleChecker()
        with self.assertRaises(RuleRejection) as ctx:
            checker.check(
                action_definition(["demo:rule:bad_path"]),
                TempState(make_state(), "c1"),
                command_id="c1",
                action_id="a1",
            )
        self.assertEqual(ctx.exception.rule_id, "demo:rule:bad_path")
        self.assertIn("无法判定", ctx.exception.reason)

    def test_rejection_is_logged(self):
        """规则拒绝要写日志（拒绝变动必须有原因，P6）。"""
        sink = MemorySink()
        checker = RuleChecker(logger=Logger(sink=sink, level=LogLevel.TRACE))
        with self.assertRaises(RuleRejection):
            checker.check(
                action_definition(["demo:rule:turn_ok"]),
                TempState({"turn": 5, "units": {"u1": {"ap": 3}}}, "c1"),
                command_id="c1",
                action_id="a1",
            )
        warnings = [record for record in sink.records() if record.level is LogLevel.WARN]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].extra["rule_id"], "demo:rule:turn_ok")


if __name__ == "__main__":
    unittest.main()
