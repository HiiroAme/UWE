"""orchestrator 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/pipeline/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 取第一个条件成立的分支（后面的分支不再求值）；
  - 所有分支都不成立：空结果（不是失败）；
  - 命令定义不存在、条件求值失败 → OrchestrationError；
  - 条件读的是临时状态视图（能看到本命令已经生效的改动）。
"""

import unittest

from core.context import Context
from core.delta import DataType, Operation
from core.logger import Logger
from core.logic import LogicError
from core.pipeline import EngineApi, OrchestrationError, Orchestrator, SequenceCounter
from core.ports import SilentMedia
from core.rng import Rng
from core.temp_state import TempState

from .pipeline_fixtures import entry, make_command, make_content, make_state


def branch_entries(command_branches: list) -> list:
    """造"两个动作 + 一个命令"的最小内容。

    输入：
        command_branches: 命令的分支表。
    输出：
        条目列表（含两个 Action，便于不同分支指向不同动作）。
    异常：
        无。
    变量：
        无。
    """
    return [
        entry("demo:action:a", "action", {"steps": []}),
        entry("demo:action:b", "action", {"steps": []}),
        entry("demo:command:c", "command", {"branches": command_branches}),
    ]


class TestPlan(unittest.TestCase):
    """分支选择。"""

    def test_takes_first_matching_branch(self):
        """第一个条件成立的分支生效：后面的分支条件不再求值（不然会因路径不存在而报错）。"""
        entries = branch_entries(
            [
                {"condition": True, "actions": [{"action": "demo:action:a"}]},
                # 第二个分支读一个不存在的路径；它若被求值会报错。
                {"condition": ["get", "/nope"], "actions": [{"action": "demo:action:b"}]},
            ]
        )
        orchestrator = Orchestrator(make_content(entries))
        plan = orchestrator.plan(make_command(definition_id="demo:command:c"), TempState(make_state(), "c1"))
        self.assertEqual([item.action_id for item in plan], ["demo:action:a"])

    def test_condition_false_falls_through_to_next_branch(self):
        """前一条条件不成立时继续看下一条。"""
        entries = branch_entries(
            [
                {"condition": [">", 1, 2], "actions": [{"action": "demo:action:a"}]},
                {"condition": True, "actions": [{"action": "demo:action:b"}]},
            ]
        )
        plan = Orchestrator(make_content(entries)).plan(
            make_command(definition_id="demo:command:c"), TempState(make_state(), "c1")
        )
        self.assertEqual([item.action_id for item in plan], ["demo:action:b"])

    def test_no_branch_matches_is_empty_plan(self):
        """所有条件都不成立：给空结果，不算失败。"""
        entries = branch_entries([{"condition": False, "actions": [{"action": "demo:action:a"}]}])
        plan = Orchestrator(make_content(entries)).plan(
            make_command(definition_id="demo:command:c"), TempState(make_state(), "c1")
        )
        self.assertEqual(plan, ())

    def test_condition_sees_uncommitted_changes(self):
        """条件读的是临时状态视图：本命令已经生效的改动立刻可见，真实 State 不变。"""
        entries = branch_entries(
            [
                {"condition": ["==", ["get", "/turn"], 2], "actions": [{"action": "demo:action:a"}]},
                {"condition": True, "actions": []},
            ]
        )
        state = make_state()
        command = make_command(definition_id="demo:command:c")
        view = TempState(state, command.command_id)
        api = EngineApi(
            view,
            SequenceCounter(),
            command,
            "demo",
            rng=Rng(1),
            logger=Logger(sink=None),
            context=Context(),
            media=SilentMedia(),
            functions={},
        )
        api.for_service("a0", "s0").emit("/turn", Operation.MODIFY, DataType.NUMBER, value=2, old_value=1)

        plan = Orchestrator(make_content(entries)).plan(command, view)
        self.assertEqual([item.action_id for item in plan], ["demo:action:a"])
        self.assertEqual(state["turn"], 1)


class TestFailures(unittest.TestCase):
    """失败口径。"""

    def test_missing_command_definition(self):
        """命令定义不存在 → OrchestrationError（丢弃 Command）。"""
        orchestrator = Orchestrator(make_content())
        with self.assertRaises(OrchestrationError):
            orchestrator.plan(make_command(definition_id="demo:command:nope"), TempState(make_state(), "c1"))

    def test_condition_error_wrapped(self):
        """条件求值报错（路径不存在）→ OrchestrationError，并保留原始异常（logic 层的错误）。"""
        entries = branch_entries([{"condition": ["get", "/nope"], "actions": [{"action": "demo:action:a"}]}])
        orchestrator = Orchestrator(make_content(entries))
        with self.assertRaises(OrchestrationError) as ctx:
            orchestrator.plan(make_command(definition_id="demo:command:c"), TempState(make_state(), "c1"))
        self.assertIsInstance(ctx.exception.__cause__, LogicError)


if __name__ == "__main__":
    unittest.main()
