"""存档 / 读档 / 回放的端到端测试（§18、§20 里的"回放测试"）。

位置：tests/unit/core/persistence/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 玩几手 → 组装存档 → 回放终态 == 原始终态（§20 的回放测试）；
  - 存档落盘 / 读盘的完整往返（走文件适配器）；
  - apply_save：State、随机状态、命令序列与批次历史一起接回；
  - 读档之后继续玩，两条线（一直玩下去 / 读档再玩）结果一致；
  - 快照时机由 Mod 决定：中途取快照后，存档只带之后的批次且回放仍然正确；
  - 用别的 Mod 的存档读档被拒绝。
"""

import shutil
import unittest
from pathlib import Path

from adapters import LocalFileSystem
from core.persistence import SaveVersionError, read_save, replay_state, write_save
from core.pipeline import Input

from ..pipeline.pipeline_fixtures import make_runtime

# 测试用的临时目录：放仓库内（tests/_scratch），用例结束清掉。
SCRATCH_ROOT = Path(__file__).resolve().parents[3] / "_scratch"


def play(runtime, *, nodes: list[str], labels: tuple[str, ...] = ("turn:1",)) -> None:
    """按给定节点依次"点一下并结算"。

    输入：
        runtime: 引擎运行期；
        nodes: 每次点击的目标节点；
        labels: 每次结算贴的 Mod 标签。
    输出：
        无。
    异常：
        无。
    变量：
        index / node: 遍历时的序号与节点名。
    """
    for index, node in enumerate(nodes):
        runtime.dispatcher.submit_input(Input("click_node", {"node": node}, float(index + 1), "ui"))
        runtime.dispatcher.trigger_settlement(labels)


class TestSaveAndReplay(unittest.TestCase):
    """存档 → 读档 → 回放。"""

    def setUp(self):
        """准备一个干净的临时目录。"""
        self._folder = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(self._folder, ignore_errors=True)
        self._folder.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """清掉临时目录。"""
        shutil.rmtree(self._folder, ignore_errors=True)
        try:
            SCRATCH_ROOT.rmdir()  # 全都清干净时顺手把外层目录也收掉
        except OSError:
            pass

    def test_replay_matches_live_state(self):
        """回放终态 == 原始终态（§20 的回放测试）。"""
        runtime, _ = make_runtime()
        play(runtime, nodes=["n2", "n3"])
        save = runtime.build_save(scenario_id="demo_scene", meta={"name": "测试存档"})

        self.assertEqual(replay_state(save), runtime.state)
        self.assertEqual(save.scenario_id, "demo_scene")
        self.assertEqual(save.meta["name"], "测试存档")

    def test_file_roundtrip_then_replay(self):
        """存档写文件、读回来、回放：三层（内存 / 文件 / 回放）结果一致。"""
        runtime, _ = make_runtime()
        play(runtime, nodes=["n2"])
        save = runtime.build_save()

        files = LocalFileSystem()
        path = str(self._folder / "slot1.json")
        write_save(files, path, save)
        restored = read_save(files, path, expected_mod=runtime.mod_record())

        self.assertEqual(restored.to_data(), save.to_data())
        self.assertEqual(replay_state(restored), runtime.state)
        self.assertEqual(restored.random_state, runtime.rng.state())

    def test_apply_save_restores_everything(self):
        """apply_save 之后：State、随机状态、命令序列、批次历史都接回。"""
        source, _ = make_runtime(seed=4242)
        play(source, nodes=["n2", "n3"])
        save = source.build_save()

        target, _ = make_runtime(seed=1)  # 另一局：种子与 State 都不同
        target.apply_save(save)

        self.assertEqual(target.state, source.state)
        # 随机状态接回：两边接下来取到的随机数一致（D-23 的意义）。
        self.assertEqual(
            [target.rng.next_int(1, 6) for _ in range(5)],
            [source.rng.next_int(1, 6) for _ in range(5)],
        )
        # 历史接回：再存一次，命令序列与批次日志与原来一致。
        again = target.build_save()
        self.assertEqual([command.command_id for command in again.commands],
                         [command.command_id for command in save.commands])
        self.assertEqual(len(again.settlements), len(save.settlements))

    def test_continue_after_load_matches_original_run(self):
        """读档之后继续玩，与"一直玩下去"得到同样的 State。"""
        original, _ = make_runtime(seed=7)
        play(original, nodes=["n2"])
        save = original.build_save()

        loaded, _ = make_runtime(seed=7)
        loaded.apply_save(save)

        # 两条线做同样的事：再点一个节点并结算。
        play(original, nodes=["n4"])
        play(loaded, nodes=["n4"])
        self.assertEqual(loaded.state, original.state)

    def test_midway_snapshot_keeps_save_small(self):
        """中途取快照之后：存档只带之后的批次，回放仍然等于当前状态。"""
        runtime, _ = make_runtime()
        play(runtime, nodes=["n2"])
        runtime.journal.snapshot(runtime.state)  # 快照时机由 Mod 决定（D-30）
        play(runtime, nodes=["n3", "n1"])

        save = runtime.build_save()
        self.assertEqual(len(save.settlements), 2)          # 快照之前那批不进存档
        self.assertEqual(save.snapshot.state["units"]["u1"]["position"], "n2")
        self.assertEqual(replay_state(save), runtime.state)

    def test_foreign_save_is_rejected(self):
        """用别的 Mod 的存档读档：拒绝加载（D-25）。"""
        source, _ = make_runtime(mod_id="demo")
        play(source, nodes=["n2"])
        save = source.build_save()

        other, _ = make_runtime(mod_id="other_mod")
        with self.assertRaises(SaveVersionError):
            other.apply_save(save)
        # 被拒绝时现场不该被改动。
        self.assertEqual(other.state["units"]["u1"]["position"], "n1")

    def test_version_mismatch_is_rejected_on_read(self):
        """版本对不上的存档：读的时候就被拒绝，不会被误用。"""
        runtime, _ = make_runtime()
        play(runtime, nodes=["n2"])
        save = runtime.build_save()

        files = LocalFileSystem()
        path = str(self._folder / "old.json")
        write_save(files, path, save)
        text = files.read_text(path).replace('"save_format_version": 1', '"save_format_version": 99')
        files.write_text(path, text)

        with self.assertRaises(SaveVersionError):
            read_save(files, path)


if __name__ == "__main__":
    unittest.main()
