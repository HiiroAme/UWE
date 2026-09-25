"""热重载（F-01 基础版）的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/shell/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 改脚本之后重载：新行为立刻生效，**局面保留**；
  - 改数据（规则）之后重载：新规则生效；
  - 随机状态在重载前后不断（随机不是 Mod 内容）；
  - 派生值在重载时按新公式自愈；
  - 换一个别的 Mod 来"重载"会被拒绝。

用的是临时目录里的一个小 Mod，改文件、重载、再看结果。
"""

import json
import shutil
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "engine" / "src"))

from adapters import LocalFileSystem, PythonScriptLoader  # noqa: E402
from core.pipeline import Input  # noqa: E402
from modload import ModLoader  # noqa: E402
from shell import GameSession  # noqa: E402

SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"

# 一个小 Mod：一条服务（脚本可改）、一条规则（数据可改）、一个派生值。
MOD_INFO = {"id": "hot_demo", "name": "热重载演示", "version": "0.0.0",
            "init": "scripts/init.py:build_state"}
SERVICE_SCRIPT = '''
def bump(api, args):
    """给 /counter 加 step；step 由参数给。"""
    old = api.get("/counter")
    api.emit("/counter", "modify", "number", value=old + args.get("step", 1), old_value=old)
'''
SERVICE_SCRIPT_V2 = SERVICE_SCRIPT.replace('args.get("step", 1)', 'args.get("step", 1) * 10')
INIT_SCRIPT = '''
def build_state(mod):
    """初始 State：一个计数器 + 一个派生值。"""
    return {"counter": 0, "counter_x2": 0}
'''
ENTRIES = [
    {"id": "hot_demo:service:bump", "type": "service",
     "data": {"script": "scripts/bump.py", "callable": "bump"}},
    {"id": "hot_demo:rule:always", "type": "rule",
     "data": {"condition": True, "message": "永远通过"}},
    {"id": "hot_demo:action:bump", "type": "action",
     "data": {"rules": ["hot_demo:rule:always"], "steps": [{"service": "hot_demo:service:bump",
                                                           "args": {"step": ["arg", "step"]}}]}},
    {"id": "hot_demo:command:bump", "type": "command",
     "data": {"branches": [{"condition": True,
                            "actions": [{"action": "hot_demo:action:bump",
                                         "args": {"step": ["arg", "step"]}}]}]}},
    {"id": "hot_demo:input:bump", "type": "input",
     "data": {"kind": "bump", "command": "hot_demo:command:bump",
              "payload": {"step": ["arg", "step"]}}},
    {"id": "hot_demo:formula:counter_x2", "type": "formula",
     "data": {"target": "/counter_x2", "expression": ["*", ["get", "/counter"], 2]}},
]


def write(path: Path, text: str) -> None:
    """写文本文件（父目录自动建）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data) -> None:
    """写 JSON 文件。"""
    write(path, json.dumps(data, ensure_ascii=False))


class TestHotReload(unittest.TestCase):
    """改文件 → 重载 → 看效果。"""

    def setUp(self):
        """在临时目录里造一个 Mod。"""
        self._root = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(self._root, ignore_errors=True)
        self._mods = self._root / "mods"
        self._folder = self._mods / "hot_demo"
        write_json(self._folder / "mod_info.json", MOD_INFO)
        write_json(self._folder / "data" / "10_entries.json", ENTRIES)
        write(self._folder / "scripts" / "init.py", INIT_SCRIPT)
        write(self._folder / "scripts" / "bump.py", SERVICE_SCRIPT)
        self._scripts = PythonScriptLoader()
        self._loader = ModLoader(LocalFileSystem(), self._scripts, str(self._mods))

    def tearDown(self):
        """清掉临时目录。"""
        shutil.rmtree(self._root, ignore_errors=True)
        try:
            SCRATCH_ROOT.rmdir()
        except OSError:
            pass

    def _load(self):
        """全加载这个 Mod。"""
        return self._loader.load(self._loader.load_info(str(self._folder)))

    def _send(self, session, step: int):
        """送一条 bump 输入并结算。"""
        session.submit_input(Input("bump", {"step": step}, 1.0, "ui"))
        return session.settle(["turn:1"])

    def test_script_change_takes_effect_and_state_is_kept(self):
        """改脚本后重载：新行为立刻生效，局面（计数器）保留。"""
        session = GameSession(self._load(), files=LocalFileSystem(), seed=1)
        self._send(session, 1)
        self.assertEqual(session.state["counter"], 1)

        write(self._folder / "scripts" / "bump.py", SERVICE_SCRIPT_V2)
        self._scripts.clear()                       # 丢掉脚本缓存，否则会拿到旧模块
        session.apply_reload(self._load())

        self.assertEqual(session.state["counter"], 1)   # 局面没丢
        self._send(session, 1)
        self.assertEqual(session.state["counter"], 11)  # 新脚本：1 * 10

    def test_data_change_takes_effect(self):
        """改数据（规则条件）后重载：新规则立刻生效（这里把规则改成永远失败）。"""
        session = GameSession(self._load(), files=LocalFileSystem(), seed=1)
        entries = json.loads((self._folder / "data" / "10_entries.json").read_text(encoding="utf-8"))
        for entry in entries:
            if entry["id"] == "hot_demo:rule:always":
                entry["data"] = {"condition": False, "message": "永远拒绝"}
        write_json(self._folder / "data" / "10_entries.json", entries)
        session.apply_reload(self._load())

        result = self._send(session, 1)
        self.assertFalse(result.commands[0].committed)
        self.assertEqual(result.commands[0].rule_id, "hot_demo:rule:always")
        self.assertEqual(session.state["counter"], 0)

    def test_random_state_survives_reload(self):
        """随机状态是运行时的事实，不该被重载重置（D-23）。"""
        session = GameSession(self._load(), files=LocalFileSystem(), seed=99)
        session.runtime.rng.next_int(1, 100)
        expected = session.runtime.rng.state()
        session.apply_reload(self._load())
        self.assertEqual(session.runtime.rng.state(), expected)

    def test_derived_values_heal_on_reload(self):
        """重载会按新公式自愈派生值：改坏的、或者公式改了的，都算对。"""
        session = GameSession(self._load(), files=LocalFileSystem(), seed=1)
        self._send(session, 3)
        session.state["counter_x2"] = 999          # 先改坏
        session.apply_reload(self._load())
        self.assertEqual(session.state["counter_x2"], 6)

        entries = json.loads((self._folder / "data" / "10_entries.json").read_text(encoding="utf-8"))
        for entry in entries:
            if entry["id"] == "hot_demo:formula:counter_x2":
                entry["data"] = {"target": "/counter_x2", "expression": ["*", ["get", "/counter"], 100]}
        write_json(self._folder / "data" / "10_entries.json", entries)
        session.apply_reload(self._load())
        self.assertEqual(session.state["counter_x2"], 300)   # 新公式

    def test_reload_rejects_another_mod(self):
        """换成别的 Mod 来"重载"会被拒绝（那是换 Mod，不是重载）。"""
        session = GameSession(self._load(), files=LocalFileSystem(), seed=1)
        other_folder = self._mods / "other"
        write_json(other_folder / "mod_info.json", {"id": "other", "name": "别的 Mod", "version": "0.0.0"})
        other = self._loader.load(self._loader.load_info(str(other_folder)))
        with self.assertRaises(ValueError):
            session.apply_reload(other)


if __name__ == "__main__":
    unittest.main()
