"""journal 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/persistence/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 命令与结算批次的记录（含"只记已提交部分"的口径）；
  - 取快照：深拷贝、快照之后的批次才进存档、快照前后的批次划分正确；
  - 没取过快照时 build_save 自动兜底取一次；
  - restore：把存档里的历史接回记录器。
"""

import unittest

from core.delta import DataType, Delta, Operation, Trace
from core.persistence import Journal, ModRecord, SaveFile, SettlementRecord, Snapshot
from core.pipeline import Command, CommandResult, CommandStatus, SettlementResult


def make_command(command_id: str = "demo:cmd:1") -> Command:
    """造一个命令。"""
    return Command(
        command_id=command_id,
        definition_id="demo:command:move",
        source="ui",
        payload={"node": "n2"},
        created_at=1.0,
    )


def make_delta(sequence: int, value: int, old_value: int) -> Delta:
    """造一条 /turn 的修改变化量。"""
    return Delta(
        path="/turn",
        operation=Operation.MODIFY,
        data_type=DataType.NUMBER,
        trace=Trace(
            sequence=sequence,
            timestamp=1.0,
            command_id="demo:cmd:1",
            action_id="a1",
            service_id="s1",
            mod_id="demo",
        ),
        value=value,
        old_value=old_value,
    )


def make_settlement(*deltas: Delta, labels: tuple[str, ...] = (),
                    command_id: str = "demo:cmd:1") -> SettlementResult:
    """造一个"一个命令提交了这些变化量"的结算结果。"""
    return SettlementResult(
        commands=(CommandResult(command_id=command_id, status=CommandStatus.COMMITTED, deltas=deltas),),
        deltas=deltas,
        labels=labels,
    )


class TestRecording(unittest.TestCase):
    """记录本身。"""

    def test_records_commands_and_settlements(self):
        """命令与批次都记下来，计数正确。"""
        journal = Journal()
        journal.record_command(make_command())
        journal.record_settlement(make_settlement(make_delta(1, 2, 1), labels=("turn:1",)))

        self.assertEqual(journal.command_count, 1)
        self.assertEqual(journal.settlement_count, 1)
        self.assertEqual(journal.settlements()[0].labels, ("turn:1",))
        self.assertEqual([delta.path for delta in journal.settlements()[0].deltas], ["/turn"])

    def test_empty_batch_is_recorded(self):
        """空批次也记：它的 Mod 标签对"按回合划分的回放"有意义（§18.1）。"""
        journal = Journal()
        journal.record_settlement(make_settlement(labels=("turn:1", "phase:end")))
        self.assertEqual(journal.settlement_count, 1)
        self.assertEqual(journal.settlements()[0].labels, ("turn:1", "phase:end"))
        self.assertEqual(journal.settlements()[0].deltas, ())

    def test_types_are_checked(self):
        """入参类型不对直接报错（不悄悄记下坏数据）。"""
        journal = Journal()
        with self.assertRaises(TypeError):
            journal.record_command("不是命令")
        with self.assertRaises(TypeError):
            journal.record_settlement("不是结算结果")
        with self.assertRaises(TypeError):
            journal.snapshot(["不是 dict"])


class TestSnapshotAndBuild(unittest.TestCase):
    """快照与组装存档。"""

    def test_snapshot_is_deep_copy(self):
        """快照是深拷贝：之后改 State 不影响快照。"""
        journal = Journal()
        state = {"turn": 1, "units": {"u1": {"hp": 10}}}
        journal.snapshot(state)
        state["units"]["u1"]["hp"] = 3
        save = journal.build_save(state=state, mod=ModRecord("demo", "0.0.0"), random_state={"v": 1})
        self.assertEqual(save.snapshot.state["units"]["u1"]["hp"], 10)

    def test_only_batches_after_snapshot_go_into_save(self):
        """快照之前的批次已经包在快照里，不进存档；之后才进。"""
        journal = Journal()
        journal.record_settlement(make_settlement(make_delta(1, 2, 1)))
        journal.snapshot({"turn": 2})
        journal.record_settlement(make_settlement(make_delta(2, 3, 2), labels=("turn:2",)))

        save = journal.build_save(state={"turn": 3}, mod=ModRecord("demo", "0.0.0"), random_state={})
        self.assertEqual(len(save.settlements), 1)
        self.assertEqual(save.settlements[0].labels, ("turn:2",))
        self.assertEqual(save.snapshot.state, {"turn": 2})

    def test_build_takes_snapshot_when_missing(self):
        """没取过快照时，build_save 用当前 State 兜底取一次。"""
        journal = Journal()
        save = journal.build_save(state={"turn": 5}, mod=ModRecord("demo", "0.0.0"), random_state={})
        self.assertEqual(save.snapshot.state, {"turn": 5})
        self.assertTrue(journal.has_snapshot)

    def test_build_requires_dicts(self):
        """state / random_state / meta 类型不对时报错。"""
        journal = Journal()
        mod = ModRecord("demo", "0.0.0")
        with self.assertRaises(TypeError):
            journal.build_save(state=[], mod=mod, random_state={})
        with self.assertRaises(TypeError):
            journal.build_save(state={}, mod=mod, random_state=[])
        with self.assertRaises(TypeError):
            journal.build_save(state={}, mod=mod, random_state={}, meta=[])
        with self.assertRaises(TypeError):
            journal.build_save(state={}, mod="demo", random_state={})

    def test_commands_and_random_state_are_kept(self):
        """命令史只带"真的产生过变化量"的命令；随机状态原样进存档（甲-7 / N-4）。

        镜头缩放 / 平移这类纯界面命令只改 Context、不产生变化量，对回放没贡献——
        把它们也写进存档，拖动几千帧就能把存档撑大几十万字节。
        内存里的 `commands()` 仍然是全量（日志 / 排查用）。
        """
        journal = Journal()
        journal.record_command(make_command("demo:cmd:1"))
        journal.record_command(make_command("demo:cmd:2"))
        journal.record_command(make_command("demo:cmd:3"))      # 只改 Context 的"纯界面命令"
        journal.record_settlement(make_settlement(
            make_delta(1, 2, 1), command_id="demo:cmd:1"))
        journal.record_settlement(make_settlement(
            make_delta(2, 3, 2), command_id="demo:cmd:2"))
        save = journal.build_save(
            state={"turn": 1}, mod=ModRecord("demo", "0.0.0"), random_state={"version": 3}
        )
        self.assertEqual([command.command_id for command in save.commands], ["demo:cmd:1", "demo:cmd:2"])
        self.assertEqual(journal.command_count, 3)              # 内存里仍是全量
        self.assertEqual(save.random_state, {"version": 3})


class TestMovedMarks(unittest.TestCase):
    """R5-1：过滤按"入队位置"，id 复用也不会认错。"""

    def test_reused_command_id_does_not_confuse_the_filter(self):
        """两条同名命令，只有第二条产生变化量 → 存档只带第二条。"""
        journal = Journal()
        journal.record_command(make_command("demo:cmd:7"))
        journal.record_command(make_command("demo:cmd:7"))
        journal.record_settlement(make_settlement(
            make_delta(1, 2, 1), command_id="demo:cmd:7"))
        save = journal.build_save(state={"turn": 1}, mod=ModRecord("demo", "0.0.0"),
                                  random_state={})
        self.assertEqual(len(save.commands), 1)          # 只有"有变化量"的那一次
        self.assertEqual(journal.command_count, 2)       # 内存里仍是全量


class TestRestore(unittest.TestCase):
    """读档后把历史接回记录器。"""

    def test_restore_keeps_history(self):
        """restore 之后，命令、批次与快照都变成存档里的那一份。"""
        save = SaveFile(
            mod=ModRecord("demo", "0.0.0"),
            snapshot=Snapshot(state={"turn": 7}),
            settlements=(SettlementRecord(labels=("turn:3",), deltas=(make_delta(9, 8, 7),)),),
            random_state={"version": 3},
            commands=(make_command("demo:cmd:9"),),
        )
        journal = Journal()
        journal.restore(save)

        self.assertEqual(journal.command_count, 1)
        self.assertEqual(journal.settlement_count, 1)
        self.assertEqual(journal.commands()[0].command_id, "demo:cmd:9")
        # 再存一次：内容与刚读进来的一致（不会因为读档丢历史）。
        again = journal.build_save(state={"turn": 9}, mod=save.mod, random_state=save.random_state)
        self.assertEqual(again.snapshot.state, {"turn": 7})
        self.assertEqual(len(again.settlements), 1)

    def test_restore_requires_save_file(self):
        """restore 只接受 SaveFile。"""
        with self.assertRaises(TypeError):
            Journal().restore({"不是存档"})


if __name__ == "__main__":
    unittest.main()
