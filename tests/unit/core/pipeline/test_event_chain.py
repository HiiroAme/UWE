"""event_chain 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/pipeline/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 同一个服务连续产出、label 相同的多条变化量只算一次事件发生；
  - 被别的变化量隔开的同 label 变化量算两次；
  - 链式：订阅者产生的新事件继续被处理（三级链条）；
  - 订阅者能拿到事件数据（["arg", "event"] / ["arg", "deltas"]）；
  - 没有 label 的变化量不引出事件；没有订阅者也不报错。
"""

import unittest

from core.delta import DataType, Operation
from core.temp_state import TempState

from .pipeline_fixtures import entry, make_api, make_command, make_runtime, make_services


class TestOccurrenceGrouping(unittest.TestCase):
    """一次事件发生的判定。"""

    def test_same_source_label_fires_once(self):
        """同一服务连续产出的同 label 变化量：订阅者只跑一次。"""
        runtime, _ = make_runtime()
        command = make_command()
        view = TempState(runtime.state, command.command_id)
        service = make_api(view, runtime, command).for_service("a1", "demo:service:apply_move")
        service.emit(
            "/units/u1/position", Operation.MODIFY, DataType.STRING,
            value="n2", old_value="n1", label="demo:event:unit_moved",
        )
        service.emit(
            "/units/u1/ap", Operation.MODIFY, DataType.NUMBER,
            value=2, old_value=3, label="demo:event:unit_moved",
        )

        produced = runtime.event_chain.run(command, view)
        # 订阅者（mark_moved）把 moved 置为 True；跑两次会因旧值不一致报冲突。
        self.assertEqual(view.get("/units/u1/moved"), True)
        self.assertEqual([delta.path for delta in produced], ["/units/u1/moved"])

    def test_separated_same_label_fires_twice(self):
        """同 label 的两段之间夹了一条无 label 的变化量：算两次事件发生。"""
        calls = []
        runtime, _ = make_runtime(services=make_services(mark_moved=lambda api, args: calls.append(args)))
        command = make_command()
        view = TempState(runtime.state, command.command_id)
        service = make_api(view, runtime, command).for_service("a1", "demo:service:apply_move")
        service.emit(
            "/units/u1/position", Operation.MODIFY, DataType.STRING,
            value="n2", old_value="n1", label="demo:event:unit_moved",
        )
        service.emit("/units/u1/ap", Operation.MODIFY, DataType.NUMBER, value=2, old_value=3)
        service.emit(
            "/units/u1/moved", Operation.MODIFY, DataType.BOOL, value=False, old_value=False,
            label="demo:event:unit_moved",
        )

        runtime.event_chain.run(command, view)
        self.assertEqual(len(calls), 2)

    def test_unlabeled_deltas_do_not_fire(self):
        """没有 label 的变化量不引出任何事件。"""
        calls = []
        runtime, _ = make_runtime(services=make_services(mark_moved=lambda api, args: calls.append(args)))
        command = make_command()
        view = TempState(runtime.state, command.command_id)
        service = make_api(view, runtime, command).for_service("a1", "demo:service:apply_move")
        service.emit("/units/u1/ap", Operation.MODIFY, DataType.NUMBER, value=2, old_value=3)

        self.assertEqual(runtime.event_chain.run(command, view), ())
        self.assertEqual(calls, [])

    def test_no_subscriber_is_fine(self):
        """事件没有订阅者时不报错，只是什么都不做。"""
        runtime, _ = make_runtime(entries=[entry("demo:event:lonely", "event", {})])
        command = make_command()
        view = TempState(runtime.state, command.command_id)
        service = make_api(view, runtime, command).for_service("a1", "demo:service:x")
        service.emit(
            "/units/u1/ap", Operation.MODIFY, DataType.NUMBER, value=2, old_value=3,
            label="demo:event:lonely",
        )
        self.assertEqual(runtime.event_chain.run(command, view), ())


class TestChaining(unittest.TestCase):
    """链式执行与订阅数据。"""

    def _chain_entries(self) -> list:
        """三级链条：first → 服务 s2 产生 second → 服务 s3 改数据。"""
        return [
            entry("demo:event:first", "event", {}),
            entry("demo:event:second", "event", {}),
            entry("demo:service:s2", "service", {}),
            entry("demo:service:s3", "service", {}),
            entry("demo:action:a1", "action", {"steps": [{"service": "demo:service:s2"}]}),
            entry("demo:action:a2", "action", {"steps": [{"service": "demo:service:s3", "args": {"seen": ["arg", "seen"]}}]}),
            entry("demo:trigger:t1", "trigger", {"event": "demo:event:first", "actions": [{"action": "demo:action:a1"}]}),
            entry(
                "demo:trigger:t2",
                "trigger",
                {
                    "event": "demo:event:second",
                    # 订阅者的 args 里可以读事件数据（事件 id 与本次事件的变化量）。
                    "actions": [{"action": "demo:action:a2", "args": {"seen": ["arg", "event"]}}],
                },
            ),
        ]

    def test_chain_reaches_next_event(self):
        """订阅者产生的新事件继续被处理（链式），整条链在同一视图上跑。"""
        seen = []

        def s2(api, args):
            api.emit(
                "/turn", Operation.MODIFY, DataType.NUMBER, value=2, old_value=1,
                label="demo:event:second",
            )

        def s3(api, args):
            seen.append(args["seen"])
            api.emit("/units/u1/moved", Operation.MODIFY, DataType.BOOL, value=True, old_value=False)

        runtime, _ = make_runtime(
            entries=self._chain_entries(),
            services={"demo:service:s2": s2, "demo:service:s3": s3},
        )
        command = make_command()
        view = TempState(runtime.state, command.command_id)
        service = make_api(view, runtime, command).for_service("a1", "demo:service:starter")
        service.emit(
            "/units/u1/ap", Operation.MODIFY, DataType.NUMBER, value=2, old_value=3,
            label="demo:event:first",
        )

        produced = runtime.event_chain.run(command, view)
        self.assertEqual(view.get("/turn"), 2)
        self.assertEqual(view.get("/units/u1/moved"), True)
        self.assertEqual(seen, ["demo:event:second"])
        self.assertEqual([delta.path for delta in produced], ["/turn", "/units/u1/moved"])

    def test_trigger_args_carry_deltas(self):
        """订阅者的参数能拿到本次事件的变化量（JSON 形式：path / value / label）。"""
        seen = []

        def s3(api, args):
            seen.append(args["seen"])

        entries = [
            entry("demo:event:e", "event", {}),
            entry("demo:service:s3", "service", {}),
            entry(
                "demo:action:a",
                "action",
                {"steps": [{"service": "demo:service:s3", "args": {"seen": ["arg", "seen"]}}]},
            ),
            entry(
                "demo:trigger:t",
                "trigger",
                {
                    "event": "demo:event:e",
                    # 三层参数链：事件数据 → Action 的 args → Service 步骤的 args。
                    "actions": [{"action": "demo:action:a", "args": {"seen": ["arg", "deltas"]}}],
                },
            ),
        ]
        runtime, _ = make_runtime(entries=entries, services={"demo:service:s3": s3})
        command = make_command()
        view = TempState(runtime.state, command.command_id)
        make_api(view, runtime, command).for_service("a1", "demo:service:starter").emit(
            "/units/u1/ap", Operation.MODIFY, DataType.NUMBER, value=2, old_value=3, label="demo:event:e"
        )

        runtime.event_chain.run(command, view)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0]["path"], "/units/u1/ap")
        self.assertEqual(seen[0][0]["value"], 2)
        self.assertEqual(seen[0][0]["label"], "demo:event:e")


if __name__ == "__main__":
    unittest.main()
