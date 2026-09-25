"""R5-5：在一个已经玩过的会话上读档，不许抛异常 + 改一半。

位置：tests/unit/shell/
运行：在仓库根执行 `python run_tests.py`。
"""

import copy
import pathlib
import shutil
import unittest

from adapters import LocalFileSystem, PythonScriptLoader
from core.pipeline import Input
from modload import ModLoader
from shell import GameSession

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRATCH = pathlib.Path(__file__).resolve().parents[2] / "_scratch"


def make_session():
    """开一局 demo_hex（最小演示）。"""
    loader = ModLoader(LocalFileSystem(), PythonScriptLoader(),
                       str(REPO_ROOT / "mods"), module_roots=[str(REPO_ROOT / "modules")])
    loaded = loader.load(loader.load_info(str(REPO_ROOT / "mods" / "demo_hex")))
    return GameSession(loaded, files=LocalFileSystem(), seed=1)


def send(session, kind, data):
    """送一条输入并结算。"""
    session.submit_input(Input(kind, data, 1.0, "ui"))
    return session.settle(["turn:1"]).commands[0]


class TestLoadOnPlayedSession(unittest.TestCase):
    """读档到已经用过的会话：不抛异常，且局面正好是存档里的那一份。"""

    def test_load_back_into_a_played_session(self):
        """先存档（会发过序号）再读档：不许抛，也不许留下半加载的现场。"""
        folder = SCRATCH / "case_load_on_played"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        path = str(folder / "slot.json")
        try:
            session = make_session()
            send(session, "move", {"unit": "red_1", "to": "n0_0"})   # 尽力玩一步（成不成无妨）
            session.save(path)
            expected = copy.deepcopy(session.state)                  # 存档那一刻的现场

            session.load(path)             # 修复前：抛「读档只能往前推」+ 现场被改一半
            self.assertEqual(session.state, expected)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            try:
                SCRATCH.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
