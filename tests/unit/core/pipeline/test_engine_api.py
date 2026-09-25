"""engine_api 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/pipeline/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - Trace 自动组装（sequence / timestamp / command_id / action_id / service_id / mod_id）；
  - 序号在服务之间、Command 之间共享并严格递增；
  - 只读入口看到未提交改动，真实 State 不变；
  - 随机、日志、Context 三个引擎公共手段的转交（脚本只能用受限形式取它们）；
  - 非法字段组合与视图冲突原样透传；
  - 构造参数与"视图 ↔ Command"一致性校验。
"""

import unittest

from core.context import Context
from core.delta import DataType, DeltaError, ListOp, Operation
from core.logger import LogLevel, Logger, MemorySink
from core.pipeline import Command, EngineApi, SequenceCounter, ServiceApi
from core.ports import SilentMedia
from core.rng import Rng
from core.state import PathNotFoundError, StateConflictError, StateShapeError
from core.temp_state import TempState, TempStateError


def make_state() -> dict:
    """造一棵用于记账台测试的示例树。

    输入：无。
    输出：新的示例 State 字典。
    变量：无。
    """
    return {"turn": 1, "units": {"u1": {"hp": 10, "tags": ["infantry"]}}}


def make_command(command_id: str = "c1", created_at: float = 12.5) -> Command:
    """造一个字段合法的 Command。

    输入：
        command_id: Command 标识，默认 "c1"；
        created_at: 交互时间戳，默认 12.5。
    输出：
        构造好的 Command。
    异常：
        无。
    变量：
        无。
    """
    return Command(
        command_id=command_id,
        definition_id="my_mod:command:move",
        source="ui",
        payload={},
        created_at=created_at,
    )


def make_env():
    """造一套"真实 State + 视图 + 序号分配器 + EngineApi"。

    输入：无。
    输出：
        (state, view, sequence, api) 四元组。
    异常：
        无。
    变量：
        state: 真实 State；view: 它的临时视图；
        sequence: 新建的全局序号分配器；api: 绑定三者的记账台。
    """
    state = make_state()
    view = TempState(state, "c1")
    sequence = SequenceCounter()
    api = make_api(view, sequence=sequence)
    return state, view, sequence, api


def make_api(
    view: TempState,
    *,
    sequence: SequenceCounter | None = None,
    command: Command | None = None,
    mod_id: str = "my_mod",
    rng: Rng | None = None,
    logger: Logger | None = None,
    context: Context | None = None,
) -> EngineApi:
    """按给定视图造一个记账台（其余依赖给默认值）。

    输入：
        view: 临时状态视图；
        sequence / command / mod_id / rng / logger / context: 可覆盖的依赖。
    输出：
        构造好的 EngineApi。
    异常：
        无（构造异常由被测用例自己触发）。
    变量：
        无。
    """
    return EngineApi(
        view,
        sequence if sequence is not None else SequenceCounter(),
        command if command is not None else make_command(),
        mod_id,
        rng=rng if rng is not None else Rng(1),
        logger=logger if logger is not None else Logger(sink=None),
        context=context if context is not None else Context(),
        media=SilentMedia(),
        functions={},
    )


class TestTraceAssembly(unittest.TestCase):
    """emit 自动组装 Trace 与 Delta。"""

    def test_emit_fills_all_trace_fields(self):
        """一次 emit 后，Trace 的六个字段全部由引擎填好。"""
        _, view, _, api = make_env()
        api.for_service("a1", "s1").emit(
            "/turn", Operation.MODIFY, DataType.NUMBER, value=2, old_value=1
        )
        delta = view.pending_deltas()[0]
        self.assertEqual(delta.path, "/turn")
        self.assertIs(delta.operation, Operation.MODIFY)
        self.assertIs(delta.data_type, DataType.NUMBER)
        self.assertEqual(delta.value, 2)
        self.assertEqual(delta.old_value, 1)
        self.assertEqual(delta.trace.sequence, 1)
        self.assertEqual(delta.trace.timestamp, 12.5)
        self.assertEqual(delta.trace.command_id, "c1")
        self.assertEqual(delta.trace.action_id, "a1")
        self.assertEqual(delta.trace.service_id, "s1")
        self.assertEqual(delta.trace.mod_id, "my_mod")

    def test_label_is_passed_through(self):
        """label 原样传给变化量，供 Trigger 路由。"""
        _, view, _, api = make_env()
        api.for_service("a1", "s1").emit(
            "/turn", Operation.MODIFY, DataType.NUMBER, value=2, old_value=1, label="turn:end"
        )
        self.assertEqual(view.pending_deltas()[0].label, "turn:end")

    def test_timestamp_comes_from_command_not_wall_clock(self):
        """timestamp 取 Command.created_at，不在结算时读墙钟。"""
        state = make_state()
        view = TempState(state, "c1")
        api = make_api(view, command=make_command(created_at=99.0))
        api.for_service("a1", "s1").emit(
            "/turn", Operation.MODIFY, DataType.NUMBER, value=2, old_value=1
        )
        self.assertEqual(view.pending_deltas()[0].trace.timestamp, 99.0)


class TestSequenceSharing(unittest.TestCase):
    """序号是一次运行共享的，严格递增（D-41）。"""

    def test_sequence_increases_across_services(self):
        """同一 Command 的不同服务共用计数器。"""
        _, view, _, api = make_env()
        api.for_service("a1", "s1").emit(
            "/turn", Operation.MODIFY, DataType.NUMBER, value=2, old_value=1
        )
        api.for_service("a2", "s2").emit(
            "/turn", Operation.MODIFY, DataType.NUMBER, value=3, old_value=2
        )
        self.assertEqual([item.trace.sequence for item in view.pending_deltas()], [1, 2])

    def test_sequence_shared_across_commands(self):
        """两个 Command 的视图共用一个计数器，序号继续递增。"""
        sequence = SequenceCounter()
        state1 = make_state()
        view1 = TempState(state1, "c1")
        state2 = make_state()
        view2 = TempState(state2, "c2")
        api1 = make_api(view1, sequence=sequence, command=make_command("c1"))
        api2 = make_api(view2, sequence=sequence, command=make_command("c2"))
        api1.for_service("a1", "s1").emit(
            "/turn", Operation.MODIFY, DataType.NUMBER, value=2, old_value=1
        )
        api2.for_service("a1", "s1").emit(
            "/turn", Operation.MODIFY, DataType.NUMBER, value=2, old_value=1
        )
        self.assertEqual(view1.pending_deltas()[0].trace.sequence, 1)
        self.assertEqual(view2.pending_deltas()[0].trace.sequence, 2)

    def test_counter_rejects_bad_start(self):
        """起始序号必须是非负 int（bool 不算）。"""
        with self.assertRaises(TypeError):
            SequenceCounter(True)
        with self.assertRaises(ValueError):
            SequenceCounter(-1)


class TestReadThrough(unittest.TestCase):
    """服务的只读入口看到未提交改动，真实 State 不变。"""

    def test_get_and_exists_see_uncommitted_changes(self):
        """emit 之后 get 立刻读到新值，真实 State 保持不变。"""
        state, _, _, api = make_env()
        service = api.for_service("a1", "s1")
        self.assertTrue(service.exists("/units/u1/hp"))
        service.emit(
            "/units/u1/hp", Operation.MODIFY, DataType.NUMBER, value=11, old_value=10
        )
        self.assertEqual(service.get("/units/u1/hp"), 11)
        self.assertEqual(state["units"]["u1"]["hp"], 10)

    def test_missing_path_raises(self):
        """不存在的路径按 state 层口径抛 PathNotFoundError。"""
        _, _, _, api = make_env()
        with self.assertRaises(PathNotFoundError):
            api.for_service("a1", "s1").get("/nope")


class TestFailurePropagation(unittest.TestCase):
    """非法组合与视图冲突原样透传。"""

    def test_wrong_old_value_conflicts(self):
        """旧值不一致抛 StateConflictError，且什么都没记录。"""
        _, view, _, api = make_env()
        with self.assertRaises(StateConflictError):
            api.for_service("a1", "s1").emit(
                "/turn", Operation.MODIFY, DataType.NUMBER, value=5, old_value=99
            )
        self.assertEqual(view.pending_deltas(), ())

    def test_invalid_list_op_rejected_by_model(self):
        """列表插入缺 index 由 delta 模型拒绝。"""
        _, view, _, api = make_env()
        with self.assertRaises(DeltaError):
            api.for_service("a1", "s1").emit(
                "/units/u1/tags", Operation.MODIFY, DataType.LIST,
                value="x", list_op=ListOp.INSERT,
            )
        self.assertEqual(view.pending_deltas(), ())

    def test_emit_after_terminal_view_rejected(self):
        """视图进入终态后 emit 抛 TempStateError。"""
        _, view, _, api = make_env()
        view.discard()
        with self.assertRaises(TempStateError):
            api.for_service("a1", "s1").emit(
                "/turn", Operation.MODIFY, DataType.NUMBER, value=2, old_value=1
            )


class TestConstruction(unittest.TestCase):
    """构造参数与一致性校验。"""

    def test_command_must_match_view(self):
        """视图与 Command 的 command_id 必须一致。"""
        view = TempState(make_state(), "c1")
        with self.assertRaises(ValueError):
            make_api(view, command=make_command("c2"))

    def test_mod_id_must_be_non_empty_string(self):
        """mod_id 必须是非空字符串。"""
        view = TempState(make_state(), "c1")
        with self.assertRaises(TypeError):
            make_api(view, mod_id=123)
        with self.assertRaises(ValueError):
            make_api(view, mod_id="")

    def test_view_type_checked(self):
        """view 必须是 TempState。"""
        with self.assertRaises(TypeError):
            make_api({})

    def test_for_service_validates_ids(self):
        """action_id / service_id 必须是非空字符串。"""
        _, _, _, api = make_env()
        with self.assertRaises(TypeError):
            api.for_service(1, "s1")
        with self.assertRaises(ValueError):
            api.for_service("", "s1")
        with self.assertRaises(ValueError):
            api.for_service("a1", "")

    def test_service_api_exposes_bound_ids(self):
        """手柄暴露自己绑定的 Action / Service 标识。"""
        _, _, _, api = make_env()
        service = api.for_service("a1", "s1")
        self.assertIsInstance(service, ServiceApi)
        self.assertEqual(service.action_id, "a1")
        self.assertEqual(service.service_id, "s1")


class TestEndToEnd(unittest.TestCase):
    """记账台 → 视图 → 提交 的整条链路。"""

    def test_emit_then_commit_reaches_real_state(self):
        """服务产出的变化量经提交后写进真实 State。"""
        state, view, _, api = make_env()
        api.for_service("a1", "s1").emit(
            "/turn", Operation.MODIFY, DataType.NUMBER, value=2, old_value=1
        )
        view.commit(state)
        self.assertEqual(state["turn"], 2)

    def test_adjacent_emits_are_merged_in_command_delta(self):
        """同一路径的两条相邻修改合并进 CommandDelta。"""
        _, view, _, api = make_env()
        service = api.for_service("a1", "s1")
        service.emit("/turn", Operation.MODIFY, DataType.NUMBER, value=2, old_value=1)
        service.emit("/turn", Operation.MODIFY, DataType.NUMBER, value=3, old_value=2)
        merged = view.command_delta()
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].old_value, 1)
        self.assertEqual(merged[0].value, 3)


class TestRuntimeHandles(unittest.TestCase):
    """随机 / 日志 / Context 三个引擎公共手段的转交。"""

    def test_random_comes_from_shared_rng(self):
        """服务取到的随机数来自引擎的随机模块：同一个种子得到同一串结果。"""
        view = TempState(make_state(), "c1")
        first = make_api(view, rng=Rng(2026))
        second = make_api(view, rng=Rng(2026))
        service_a = first.for_service("a1", "s1")
        service_b = second.for_service("a1", "s1")

        rolls_a = [service_a.rand_int(1, 6) for _ in range(5)]
        rolls_b = [service_b.rand_int(1, 6) for _ in range(5)]
        self.assertEqual(rolls_a, rolls_b)
        self.assertTrue(all(1 <= value <= 6 for value in rolls_a))

    def test_random_and_chance_validate_arguments(self):
        """随机接口把参数错误原样报出来（概率必须在 [0,1]）。"""
        service = make_api(TempState(make_state(), "c1")).for_service("a1", "s1")
        self.assertTrue(service.chance(1.0))
        self.assertFalse(service.chance(0.0))
        self.assertTrue(0.0 <= service.rand_float() < 1.0)
        with self.assertRaises(ValueError):
            service.chance(1.5)

    def test_context_is_the_runtime_context(self):
        """Context 是同一个对象：服务写进去的东西别处也读得到。"""
        context = Context()
        api = make_api(TempState(make_state(), "c1"), context=context)
        api.for_service("a1", "s1").context.put("anim.frame", 3)
        self.assertEqual(context.get("anim.frame"), 3)

    def test_mod_log_is_filled_with_trace_fields(self):
        """Mod 写日志时，引擎自动补上 Mod / Command / Action / Service 标识。"""
        sink = MemorySink()
        api = make_api(TempState(make_state(), "c1"), logger=Logger(sink=sink, level=LogLevel.TRACE))
        api.for_service("a1", "s1").log("因为弹药不足所以少打一发", level=LogLevel.WARN, path="/units/u1/ammo")

        record = sink.records()[-1]
        self.assertIs(record.level, LogLevel.WARN)
        self.assertEqual(record.mod_id, "my_mod")
        self.assertEqual(record.command_id, "c1")
        self.assertEqual(record.action_id, "a1")
        self.assertEqual(record.service_id, "s1")
        self.assertEqual(record.path, "/units/u1/ammo")

    def test_emit_writes_trace_log_with_delta_details(self):
        """每条成功记录的变化量都会写一条 TRACE 日志（路径、新旧值、序号）。"""
        sink = MemorySink()
        api = make_api(TempState(make_state(), "c1"), logger=Logger(sink=sink, level=LogLevel.TRACE))
        api.for_service("a1", "s1").emit(
            "/turn", Operation.MODIFY, DataType.NUMBER, value=2, old_value=1, label="turn:end"
        )

        record = sink.records()[-1]
        self.assertIs(record.level, LogLevel.TRACE)
        self.assertEqual(record.path, "/turn")
        self.assertEqual(record.label, "turn:end")
        self.assertEqual(record.extra["value"], 2)
        self.assertEqual(record.extra["old_value"], 1)
        self.assertEqual(record.extra["sequence"], 1)

    def test_add_key_log_shows_missing_field_marker(self):
        """新增键的变化量在日志里把"没有旧值"写成占位符，与 None 区分开。"""
        sink = MemorySink()
        api = make_api(TempState(make_state(), "c1"), logger=Logger(sink=sink, level=LogLevel.TRACE))
        api.for_service("a1", "s1").emit(
            "/units/u2", Operation.ADD, DataType.DICT, value={"hp": 1}
        )
        record = sink.records()[-1]
        self.assertEqual(record.extra["old_value"], "<缺省>")

    def test_handle_types_are_checked(self):
        """rng / logger / context 三个依赖也会做类型检查。"""
        view = TempState(make_state(), "c1")
        base = dict(
            sequence=SequenceCounter(),
            command=make_command(),
            mod_id="my_mod",
        )
        with self.assertRaises(TypeError):
            EngineApi(view, **base, rng="不是随机模块", logger=Logger(sink=None), context=Context())
        with self.assertRaises(TypeError):
            EngineApi(view, **base, rng=Rng(1), logger="不是日志", context=Context())
        with self.assertRaises(TypeError):
            EngineApi(view, **base, rng=Rng(1), logger=Logger(sink=None), context="不是上下文")


class TestLogLine(unittest.TestCase):
    """服务手柄的"往列表追一行"（C-1 收进来的那一处）。"""

    def test_appends_one_line_and_records_the_change(self):
        """有路径：列表末尾多一行，记的变化量是"整表改成新表"（与旧写法同形）。"""
        state, view, _, api = make_env()
        api.for_service("a1", "s1").log_line("/units/u1/tags", "armor")
        self.assertEqual(view.get("/units/u1/tags"), ["infantry", "armor"])
        delta = view.pending_deltas()[0]
        self.assertEqual(delta.path, "/units/u1/tags")
        self.assertEqual(delta.old_value, ["infantry"])
        self.assertEqual(delta.value, ["infantry", "armor"])
        self.assertEqual(state["units"]["u1"]["tags"], ["infantry"])   # 真实 State 没动

    def test_empty_path_writes_engine_log_only(self):
        """空路径：不碰 State，只写一条引擎日志（旧写法就是这个兜底）。"""
        sink = MemorySink()
        view = TempState(make_state(), "c1")
        api = make_api(view, logger=Logger(sink=sink, level=LogLevel.TRACE))
        api.for_service("a1", "s1").log_line("", "只写日志")
        self.assertEqual(view.pending_deltas(), ())
        self.assertEqual(sink.records()[-1].message, "只写日志")

    def test_missing_path_is_an_error(self):
        """路径不存在就报错（与直接 get 同口径，属于 Mod 数据写错）。"""
        _, view, _, api = make_env()
        with self.assertRaises(PathNotFoundError):
            api.for_service("a1", "s1").log_line("/nope", "x")
        self.assertEqual(view.pending_deltas(), ())

class TestLogLinePathShape(unittest.TestCase):
    """R5-11：`log_line` 的路径不是列表时给 StateShapeError（不是裸 TypeError）。"""

    def test_non_list_path_is_a_state_shape_error(self):
        """`/turn` 是个数字：报 StateShapeError，消息里说清要列表。"""
        _, view, _, api = make_env()
        with self.assertRaises(StateShapeError) as ctx:
            api.for_service("a1", "s1").log_line("/turn", "x")
        self.assertIn("列表", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
