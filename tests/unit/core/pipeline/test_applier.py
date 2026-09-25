"""applier 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/pipeline/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 按步骤顺序调用服务，返回本次新产生的变化量；
  - 参数表达式在**调用这一刻**求值（第二步能看到第一步改过的值）；
  - 服务解析不到、服务内部抛异常、参数求值失败 → ServiceFailure（带原因与原始异常）；
  - 服务返回值被忽略（改动只能走 api.emit）。
"""

import unittest

from core.delta import DataType, Delta, Operation
from core.pipeline import Action, ServiceFailure
from core.temp_state import TempState

from .pipeline_fixtures import entry, make_command, make_runtime, make_services


def make_view_and_definition(runtime, definition_id: str, payload: dict):
    """准备"一次动作调用"需要的三件东西。

    输入：
        runtime: 引擎运行期；
        definition_id: 动作定义 id；
        payload: 动作参数。
    输出：
        (command, view, action, definition) 四元组。
    异常：
        无。
    变量：
        command / view / action / definition: 四个对象。
    """
    command = make_command()
    view = TempState(runtime.state, command.command_id)
    action = Action(action_id="a1", definition_id=definition_id, payload=payload)
    definition = runtime.content.action(definition_id)
    return command, view, action, definition


class TestExecute(unittest.TestCase):
    """服务调用与变化量收集。"""

    def test_returns_new_deltas(self):
        """执行完成后返回本 Action 新产生的变化量（不提交真实 State）。"""
        runtime, _ = make_runtime()
        command, view, action, definition = make_view_and_definition(runtime, "demo:action:move", {"to": "n2"})
        deltas = runtime.applier.execute(command, action, definition, view)

        self.assertEqual(len(deltas), 2)
        self.assertTrue(all(isinstance(delta, Delta) for delta in deltas))
        self.assertEqual([delta.path for delta in deltas], ["/units/u1/position", "/units/u1/ap"])
        self.assertEqual(view.get("/units/u1/position"), "n2")
        self.assertEqual(runtime.state["units"]["u1"]["position"], "n1")

    def test_action_without_steps_returns_nothing(self):
        """没有步骤的动作：返回空元组，视图不变。"""
        runtime, _ = make_runtime(entries=[entry("demo:action:noop", "action", {"steps": []})])
        command, view, action, definition = make_view_and_definition(runtime, "demo:action:noop", {})
        self.assertEqual(runtime.applier.execute(command, action, definition, view), ())
        self.assertEqual(view.pending_deltas(), ())

    def test_step_args_are_resolved_at_call_time(self):
        """参数表达式在调用时求值：服务收到的是具体值，不是表达式。"""
        seen = []

        def capture(api, args):
            seen.append(args)

        runtime, _ = make_runtime(services=make_services(apply_move=capture))
        command, view, action, definition = make_view_and_definition(runtime, "demo:action:move", {"to": "n2"})
        runtime.applier.execute(command, action, definition, view)
        self.assertEqual(seen, [{"to": "n2"}])

    def test_later_step_sees_earlier_changes(self):
        """第二步的参数能读到第一步刚改过的值（同一命令内前后可见）。"""
        seen = []
        entries = [
            entry("demo:service:first", "service", {}),
            entry("demo:service:second", "service", {}),
            entry(
                "demo:action:two_steps",
                "action",
                {
                    "steps": [
                        {"service": "demo:service:first"},
                        {
                            "service": "demo:service:second",
                            "args": {"seen_position": ["get", "/units/u1/position"]},
                        },
                    ]
                },
            ),
        ]

        def first(api, args):
            old = api.get("/units/u1/position")
            api.emit("/units/u1/position", Operation.MODIFY, DataType.STRING, value="n9", old_value=old)

        def second(api, args):
            seen.append(args)

        runtime, _ = make_runtime(
            entries=entries, services={"demo:service:first": first, "demo:service:second": second}
        )
        command, view, action, definition = make_view_and_definition(runtime, "demo:action:two_steps", {})
        runtime.applier.execute(command, action, definition, view)
        self.assertEqual(seen, [{"seen_position": "n9"}])


class TestFailures(unittest.TestCase):
    """三类失败都包成 ServiceFailure。"""

    def test_missing_service(self):
        """服务解析不到 → ServiceFailure（带服务 id）。"""
        runtime, _ = make_runtime(services={})
        command, view, action, definition = make_view_and_definition(runtime, "demo:action:move", {"to": "n2"})
        with self.assertRaises(ServiceFailure) as ctx:
            runtime.applier.execute(command, action, definition, view)
        self.assertEqual(ctx.exception.service_id, "demo:service:apply_move")
        self.assertIn("取不到服务实现", ctx.exception.detail)

    def test_service_exception_wrapped(self):
        """服务内部抛异常 → ServiceFailure，并保留原始异常（cause）。"""

        def boom(api, args):
            raise ZeroDivisionError("除以零")

        runtime, _ = make_runtime(services=make_services(apply_move=boom))
        command, view, action, definition = make_view_and_definition(runtime, "demo:action:move", {"to": "n2"})
        with self.assertRaises(ServiceFailure) as ctx:
            runtime.applier.execute(command, action, definition, view)
        self.assertIsInstance(ctx.exception.cause, ZeroDivisionError)
        self.assertIn("ZeroDivisionError", ctx.exception.detail)

    def test_args_resolution_failure(self):
        """参数表达式求值失败 → ServiceFailure（原因指向那一步）。"""
        entries = [
            entry("demo:service:s", "service", {}),
            entry(
                "demo:action:a",
                "action",
                {"steps": [{"service": "demo:service:s", "args": {"x": ["get", "/nope"]}}]},
            ),
        ]
        runtime, _ = make_runtime(entries=entries, services={"demo:service:s": lambda api, args: None})
        command, view, action, definition = make_view_and_definition(runtime, "demo:action:a", {})
        with self.assertRaises(ServiceFailure) as ctx:
            runtime.applier.execute(command, action, definition, view)
        self.assertIn("参数求值失败", ctx.exception.detail)

    def test_service_return_value_is_ignored(self):
        """服务返回值不参与判定：返回什么都当没发生（改动只能走 emit）。"""

        def returns_value(api, args):
            return {"anything": 1}

        runtime, _ = make_runtime(services=make_services(apply_move=returns_value))
        command, view, action, definition = make_view_and_definition(runtime, "demo:action:move", {"to": "n2"})
        self.assertEqual(runtime.applier.execute(command, action, definition, view), ())
        self.assertEqual(view.pending_deltas(), ())


if __name__ == "__main__":
    unittest.main()
