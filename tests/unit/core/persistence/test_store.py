"""store 模块与文件适配器的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/persistence/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 存档 ↔ JSON 文本的往返；
  - 三类版本不匹配一律拒绝加载（存档格式 / 变化量格式 / 条目 schema）；
  - 结构不合法（不是对象、缺字段、类型不对、含 NaN）→ SaveFormatError；
  - Mod 身份不一致 → SaveVersionError；
  - LocalFileSystem 适配器：写读往返、自动建目录、原文件不被写坏。
"""

import shutil
import unittest
from pathlib import Path

from adapters import LocalFileSystem
from core.delta import DELTA_FORMAT_VERSION, DataType, Delta, Operation, Trace
from core.persistence import (
    SAVE_FORMAT_VERSION,
    ModRecord,
    SaveFile,
    SaveFormatError,
    SaveVersionError,
    SettlementRecord,
    Snapshot,
    read_save,
    save_from_text,
    save_to_text,
    write_save,
)
from core.registry import ENTRY_SCHEMA_VERSION
from core.version import ENGINE_VERSION

# 测试用的临时目录：放在仓库内（tests/_scratch），并在 tearDown 里清掉。
# 为什么不用系统临时目录：本机环境下系统临时目录可能没有写权限，
# 而仓库目录一定可写；测试结束会删掉自己建的那一层。
SCRATCH_ROOT = Path(__file__).resolve().parents[3] / "_scratch"


def make_delta() -> Delta:
    """造一条用于存档测试的变化量。"""
    return Delta(
        path="/turn",
        operation=Operation.MODIFY,
        data_type=DataType.NUMBER,
        trace=Trace(sequence=1, timestamp=1.0, command_id="demo:cmd:1", action_id="a1",
                    service_id="s1", mod_id="demo"),
        value=2,
        old_value=1,
    )


def make_save(state: dict | None = None) -> SaveFile:
    """造一份最小但完整的存档。"""
    return SaveFile(
        mod=ModRecord(id="demo", version="0.0.0"),
        snapshot=Snapshot(state={"turn": 1} if state is None else state),
        settlements=(SettlementRecord(labels=("turn:1",), deltas=(make_delta(),)),),
        random_state={"version": 3, "state": [1, 2, 3], "gauss_next": None},
        scenario_id="demo_scene",
        meta={"name": "第一回合"},
    )


class TestTextRoundTrip(unittest.TestCase):
    """存档与 JSON 文本的往返。"""

    def test_roundtrip_keeps_everything(self):
        """往返之后结构完全一致（含批次、随机状态与 Mod 记录）。"""
        original = make_save()
        restored = save_from_text(save_to_text(original))

        self.assertEqual(restored.mod, original.mod)
        self.assertEqual(restored.snapshot.state, original.snapshot.state)
        self.assertEqual(restored.scenario_id, original.scenario_id)
        self.assertEqual(restored.meta, original.meta)
        self.assertEqual(restored.random_state, original.random_state)
        self.assertEqual(restored.settlements[0].labels, ("turn:1",))
        self.assertEqual(restored.settlements[0].deltas, original.settlements[0].deltas)

    def test_text_is_readable_json(self):
        """写出来的文本是"人也能读"的 JSON（中文不转义、缩进两格）。"""
        text = save_to_text(make_save())
        self.assertIn('"engine_version"', text)
        self.assertIn("\n  ", text)
        self.assertIn("第一回合", text)

    def test_non_json_value_is_rejected(self):
        """存档里出现 NaN（JSON 里没有这号值）时当场报错，不写出一份坏存档。"""
        with self.assertRaises(SaveFormatError):
            save_to_text(make_save(state={"turn": float("nan")}))


class TestVersionChecks(unittest.TestCase):
    """三类版本不匹配一律拒绝加载（D-25 / D-45）。"""

    def _data(self) -> dict:
        """取一份存档的纯数据形式。"""
        return make_save().to_data()

    def test_save_format_version_mismatch(self):
        """存档格式版本不一致 → SaveVersionError。"""
        data = self._data()
        data["save_format_version"] = SAVE_FORMAT_VERSION + 1
        with self.assertRaises(SaveVersionError) as ctx:
            SaveFile.from_data(data)
        self.assertEqual(ctx.exception.expected, SAVE_FORMAT_VERSION)

    def test_delta_format_version_mismatch(self):
        """变化量格式版本不一致 → SaveVersionError。"""
        data = self._data()
        data["delta_format_version"] = DELTA_FORMAT_VERSION + 1
        with self.assertRaises(SaveVersionError):
            SaveFile.from_data(data)

    def test_engine_version_mismatch(self):
        """引擎版本不一致 → SaveVersionError（本阶段按最简单口径拒绝）。"""
        data = self._data()
        data["engine_version"] = ENGINE_VERSION + "-old"
        with self.assertRaises(SaveVersionError) as ctx:
            SaveFile.from_data(data)
        self.assertEqual(ctx.exception.actual, ENGINE_VERSION + "-old")

    def test_entry_schema_version_mismatch(self):
        """条目的 schema 版本不一致 → SaveVersionError。"""
        data = self._data()
        data["mod"]["entry_schema_version"] = ENTRY_SCHEMA_VERSION + 1
        with self.assertRaises(SaveVersionError):
            SaveFile.from_data(data)

    def test_mod_identity_mismatch(self):
        """用另一个 Mod 读档 → SaveVersionError（id 或版本对不上）。"""
        data = self._data()
        with self.assertRaises(SaveVersionError):
            SaveFile.from_data(data, expected_mod=ModRecord("other_mod", "0.0.0"))
        with self.assertRaises(SaveVersionError):
            SaveFile.from_data(data, expected_mod=ModRecord("demo", "2.0.0"))
        # 完全一致时通过。
        self.assertEqual(SaveFile.from_data(data, expected_mod=ModRecord("demo", "0.0.0")).mod.id, "demo")


class TestFormatErrors(unittest.TestCase):
    """结构不合法的存档被明确拒绝。"""

    def test_not_an_object(self):
        """顶层不是对象。"""
        with self.assertRaises(SaveFormatError):
            SaveFile.from_data([1, 2, 3])

    def test_missing_fields(self):
        """缺字段。"""
        for missing in ("save_format_version", "mod", "snapshot", "settlements", "commands", "random_state"):
            with self.subTest(missing=missing):
                data = make_save().to_data()
                del data[missing]
                with self.assertRaises(SaveFormatError):
                    SaveFile.from_data(data)

    def test_wrong_types(self):
        """字段类型不对。"""
        data = make_save().to_data()
        data["snapshot"] = {"state": [1, 2], "settlement_index": 0}
        with self.assertRaises(SaveFormatError):
            SaveFile.from_data(data)

        data = make_save().to_data()
        data["settlements"] = {"labels": []}
        with self.assertRaises(SaveFormatError):
            SaveFile.from_data(data)

    def test_broken_delta_record(self):
        """批次里的变化量记录不合法 → SaveFormatError，并指出是第几条（R5-12）。"""
        data = make_save().to_data()
        broken = dict(data["settlements"][0]["deltas"][0])
        broken["unexpected"] = 1
        data["settlements"][0]["deltas"] = [data["settlements"][0]["deltas"][0], broken]
        with self.assertRaises(SaveFormatError) as ctx:
            SaveFile.from_data(data)
        message = str(ctx.exception)
        self.assertIn("第 1 条变化量记录", message)
        self.assertIn("未知字段", message)

    def test_broken_json(self):
        """文本不是合法 JSON。"""
        with self.assertRaises(SaveFormatError):
            save_from_text("{ 这不是 JSON")


class TestLocalFileSystem(unittest.TestCase):
    """本机文件适配器。"""

    def setUp(self):
        """每个用例一个独立的临时目录。"""
        self._folder = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(self._folder, ignore_errors=True)
        self._folder.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """用例结束后清掉临时目录。"""
        shutil.rmtree(self._folder, ignore_errors=True)
        try:
            SCRATCH_ROOT.rmdir()  # 全都清干净时顺手把外层目录也收掉
        except OSError:
            pass

    def test_write_read_and_exists(self):
        """写入后能读回；父目录会自动建出来。"""
        files = LocalFileSystem()
        path = str(self._folder / "sub" / "save.json")
        self.assertFalse(files.exists(path))
        files.write_text(path, "内容")
        self.assertTrue(files.exists(path))
        self.assertEqual(files.read_text(path), "内容")

    def test_no_leftover_temp_file(self):
        """写完不留临时文件（先写 .tmp 再原子替换）。"""
        files = LocalFileSystem()
        path = self._folder / "save.json"
        files.write_text(str(path), "第一版")
        files.write_text(str(path), "第二版")
        self.assertEqual(files.read_text(str(path)), "第二版")
        self.assertEqual(sorted(item.name for item in self._folder.iterdir()), ["save.json"])

    def test_read_missing_file_raises(self):
        """读不存在的文件由适配器抛 OSError（调用方去记"读取失败"）。"""
        files = LocalFileSystem()
        with self.assertRaises(OSError):
            files.read_text(str(self._folder / "nope.json"))

    def test_save_and_load_through_filesystem(self):
        """write_save / read_save 走文件端口：往返一致，版本不匹配时拒绝。"""
        files = LocalFileSystem()
        save = make_save()
        path = str(self._folder / "slot1.json")
        write_save(files, path, save)
        restored = read_save(files, path, expected_mod=ModRecord("demo", "0.0.0"))
        self.assertEqual(restored.snapshot.state, save.snapshot.state)
        self.assertEqual(restored.settlements[0].deltas, save.settlements[0].deltas)

        with self.assertRaises(SaveVersionError):
            read_save(files, path, expected_mod=ModRecord("demo", "9.9.9"))


if __name__ == "__main__":
    unittest.main()
