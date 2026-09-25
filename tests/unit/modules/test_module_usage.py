"""逻辑模块的端到端验收测试：一个"零函数 Mod"只靠引用模块就能打起来。

位置：tests/unit/modules/
运行：在仓库根执行 `python run_tests.py`。
覆盖（对应 plans/新架构细节/01_实施依据/模块与Mod格式.md）：
  - 模块放在引擎之外（`modules/`），引擎用"模块根目录"加载它；
  - Mod 用 `uses` 声明、用 `bindings` 选实现、用 `params` 给自己的事实（路径、骰子）；
  - Mod 的 `functions` 是空的——**一行函数都不写**：规则调用模块的纯函数，
    攻击动作调用模块的服务，界面调用模块的界面件；
  - 换实现只改绑定（骰子 → 定值），换"扣哪条量"只改参数（血量 → 人数）；
  - 模块界面件产出的文字层带底色（引擎翻译层时先铺底色再写字）。
"""

import json
import shutil
import unittest
from pathlib import Path

from adapters import LocalFileSystem, PythonScriptLoader
from core.context import Context
from core.pipeline import Input
from core.ui import PageContext
from modload import ModLoader
from shell import GameSession

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULES_ROOT = REPO_ROOT / "modules"
SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"

INIT_SCRIPT = '''
def build_state(mod):
    """三个格子连成一条线，两个单位面对面（这里只写"这一局的事实"）。"""
    return {
        "turn": 1,
        "nodes": {"n0_0": {"q": 0, "r": 0}, "n1_0": {"q": 1, "r": 0}, "n2_0": {"q": 2, "r": 0}},
        "adjacency": {"n0_0": ["n1_0"], "n1_0": ["n0_0", "n2_0"], "n2_0": ["n1_0"]},
        "units": {"a1": {"at": "n0_0", "hp": 10, "men": 8, "ap": 3},
                  "b1": {"at": "n1_0", "hp": 5, "men": 4, "ap": 2}},
        "log": [],
    }
'''

VIEW_SCRIPT = '''
from core.ui import ClickResult, Layer, View


def build_view(page_context):
    """界面：格子交给模块画，本脚本只决定"每格显示什么、点了产生什么输入"。"""
    make_grid = page_context.functions["pack_hex_grid.hex_grid.plain"]
    state = page_context.state
    width, height = page_context.size
    items = {}
    for node_key in state["nodes"]:
        occupants = [k for k, u in state["units"].items() if u["at"] == node_key]
        items[node_key] = {
            "text": "\\n".join(f"{k} hp{state['units'][k]['hp']}" for k in occupants) or node_key,
            "click": ClickResult(kind="attack", data={"attacker": "a1", "target": "b1"}, settle=True),
        }
    grid = make_grid(nodes=state["nodes"], size=48.0, center=(width / 2, height / 2), items=items)
    hud = Layer(id="hud", rect=(10, 10, width - 20, 60), kind="text",
                text=f"日志 {len(state['log'])} 条", font_size=16,
                color=(30, 34, 44, 255), text_color=(230, 230, 230, 255), z=20)
    return View(layers=grid + (hud,))
'''


def write(path: Path, text: str) -> None:
    """写文本文件（父目录自动建）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data) -> None:
    """写 JSON 文件。"""
    write(path, json.dumps(data, ensure_ascii=False))


def entries(*, target_path: str = "/units/{target}/hp", dice: str = "1d6") -> list:
    """造这个零函数 Mod 的注册表条目。

    输入：
        target_path: 攻击要扣哪条量（走 params，不写死在条目里）；
        dice: 骰子表达式（走 params）。
    输出：
        条目数组。
    """
    return [
        {
            "id": "zero_fn_mod:rule:adjacent",
            "type": "rule",
            "data": {
                # 相邻判断由模块提供（bindings 里的 adjacent）：本 Mod 不写函数
                "condition": ["call", "adjacent",
                              ["get", ["format", "/adjacency/{0}",
                                       ["get", ["format", "/units/{0}/at", ["arg", "attacker"]]]]],
                              ["get", ["format", "/units/{0}/at", ["arg", "target"]]]],
                "message": "目标不相邻",
            },
        },
        {
            "id": "zero_fn_mod:action:attack",
            "type": "action",
            "data": {
                "rules": ["zero_fn_mod:rule:adjacent"],
                # 攻击骨架由模块提供：扣哪条量、骰子怎么算，全在 params 里
                "steps": [{"service": "pack_combat_basic:service:attack",
                           "args": {"attacker": ["arg", "attacker"], "target": ["arg", "target"]}}],
            },
        },
        {
            "id": "zero_fn_mod:command:attack",
            "type": "command",
            "data": {"branches": [{"condition": True,
                                   "actions": [{"action": "zero_fn_mod:action:attack",
                                                "args": {"attacker": ["arg", "attacker"],
                                                         "target": ["arg", "target"]}}]}]},
        },
        {
            "id": "zero_fn_mod:input:attack",
            "type": "input",
            "data": {"kind": "attack", "command": "zero_fn_mod:command:attack",
                     "payload": {"attacker": ["arg", "attacker"], "target": ["arg", "target"]}},
        },
        {
            "id": "zero_fn_mod:ui:board",
            "type": "ui",
            "data": {"script": "scripts/view.py", "callable": "build_view", "title": "棋盘"},
        },
    ]


class TestZeroFunctionMod(unittest.TestCase):
    """Mod 一个函数都不写，靠模块打起来。"""

    def setUp(self):
        """在临时目录里造这个零函数 Mod。"""
        self._root = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(self._root, ignore_errors=True)
        self._mods = self._root / "mods"
        self._folder = self._mods / "zero_fn_mod"
        self._write_mod()
        self._write_view()

    def tearDown(self):
        """清掉临时目录。"""
        shutil.rmtree(self._root, ignore_errors=True)
        try:
            SCRATCH_ROOT.rmdir()
        except OSError:
            pass

    def _write_mod(self, *, binding: str = "pack_combat_basic.damage.dice",
                   target_path: str = "/units/{target}/hp", dice: str = "1d6",
                   extra_params: dict | None = None) -> None:
        """写 mod_info.json 与条目（改绑定 / 参数时复用）。"""
        params = {
            "damage_target_path": target_path,
            "damage_dice": dice,
            "damage_base": 1,
            "unit_ap_path": "/units/{unit}/ap",
            "ap_cost": 1,
            "log_path": "/log",
        }
        params.update(extra_params or {})
        write_json(self._folder / "mod_info.json", {
            "id": "zero_fn_mod",
            "name": "零函数模组",
            "version": "0.0.0",
            "init": "scripts/init.py:build_state",
            "ui": "zero_fn_mod:ui:board",
            "uses": ["pack_hex_grid>=0.0.0", "pack_combat_basic>=0.0.0"],
            "bindings": {
                "adjacent": "pack_hex_grid.is_adjacent.in_list",
                "damage": {"use": binding, "params": {"modifier": 1}},
            },
            "params": params,
        })
        write_json(self._folder / "data" / "10_entries.json", entries())
        write(self._folder / "scripts" / "init.py", INIT_SCRIPT)

    def _write_view(self) -> None:
        """写这个 Mod 自己的视图脚本（只是排版，画法来自模块）。"""
        write(self._folder / "scripts" / "view.py", VIEW_SCRIPT)

    def _load(self):
        """用"引擎外的模块根目录"加载这个 Mod。"""
        loader = ModLoader(
            LocalFileSystem(), PythonScriptLoader(), str(self._mods),
            module_roots=[str(MODULES_ROOT)],
        )
        return loader.load(loader.load_info(str(self._folder)))

    def test_functions_come_from_modules(self):
        """函数表里全是模块的东西与绑定名；Mod 自己的 functions 是空的。"""
        loaded = self._load()
        self.assertEqual(loaded.user_functions, {})
        for name in ("pack_hex_grid.distance.hex", "pack_hex_grid.hex_grid.plain",
                     "pack_combat_basic.damage.dice", "pack_combat_basic.damage.linear",
                     "adjacent", "damage"):
            self.assertIn(name, loaded.functions)
        self.assertEqual([module.id for module in loaded.modules],
                         ["pack_common", "pack_hex_grid", "pack_combat_basic"])
        self.assertEqual(loaded.params["log_path"], "/log")

    def test_attack_runs_entirely_from_modules(self):
        """攻击：模块的规则函数 + 模块的服务 + Mod 的 params，改的是 Mod 指定的那条量。"""
        session = GameSession(self._load(), files=LocalFileSystem(), seed=7)
        session.submit_input(Input("attack", {"attacker": "a1", "target": "b1"}, 1.0, "ui"))
        result = session.settle(["turn:1"])

        self.assertTrue(result.ok)
        state = session.state
        self.assertLess(state["units"]["b1"]["hp"], 5)          # 扣的是 hp（params 指定）
        self.assertEqual(state["units"]["a1"]["ap"], 2)          # 行动力也按 params 扣了
        self.assertEqual(len(state["log"]), 1)                   # 模块服务自己写了战报
        self.assertEqual(state["units"]["b1"]["men"], 4)         # 别的量没动

    def test_deterministic_and_swap_parameters_and_binding(self):
        """同种子同结果；换绑定（骰子→定值）与换参数（扣人数）都只改声明。"""
        def hp_after(seed: int, *, binding: str, target_path: str, dice: str, extra: dict | None = None):
            """跑一次攻击，返回 (目标 hp, 目标 men)。"""
            self._write_mod(binding=binding, target_path=target_path, dice=dice, extra_params=extra)
            session = GameSession(self._load(), files=LocalFileSystem(), seed=seed)
            session.submit_input(Input("attack", {"attacker": "a1", "target": "b1"}, 1.0, "ui"))
            session.settle(["turn:1"])
            return session.state["units"]["b1"]["hp"], session.state["units"]["b1"]["men"]

        first = hp_after(11, binding="pack_combat_basic.damage.dice",
                         target_path="/units/{target}/hp", dice="1d6")
        second = hp_after(11, binding="pack_combat_basic.damage.dice",
                          target_path="/units/{target}/hp", dice="1d6")
        self.assertEqual(first, second)                      # 确定性

        linear = hp_after(11, binding="pack_combat_basic.damage.linear",
                          target_path="/units/{target}/hp", dice="")
        self.assertEqual(linear[0], 3)                        # 定值：base 1 + 绑定里的 modifier 1 = 2 → 5-2

        on_men = hp_after(11, binding="pack_combat_basic.damage.linear",
                          target_path="/units/{target}/men", dice="")
        self.assertEqual(on_men[0], 5)                        # hp 没动
        self.assertEqual(on_men[1], 2)                        # 扣的是人数：4 - 2

    def test_view_uses_module_widget_and_text_layers_get_background(self):
        """界面：格子由模块界面件产出；文字层会先铺底色再写字。"""
        loaded = self._load()
        page = loaded.pages["zero_fn_mod:ui:board"]
        page_context = PageContext(state=loaded.initial_state, size=(640, 480),
                                   context=Context(), page_id="zero_fn_mod:ui:board",
                                   functions=loaded.functions)
        view = page(page_context)

        ids = {layer.id for layer in view.layers}
        self.assertIn("grid:n0_0", ids)     # 模块界面件产出的格子
        self.assertIn("hud", ids)           # Mod 自己的排版
        grid_layer = next(layer for layer in view.layers if layer.id == "grid:n1_0")
        self.assertGreater(grid_layer.color[3], 0)
        self.assertIsNotNone(grid_layer.click)

        renderer = _RecordingRenderer()
        from shell.ui_host import draw_view

        draw_view(renderer, view)
        self.assertIn(("rect", grid_layer.rect), renderer.calls)   # 文字层先铺了底色
        self.assertTrue(any(call[0] == "text" for call in renderer.calls))


class _RecordingRenderer:
    """记账用的假渲染器：只记录被调用了什么，不真画。"""

    def __init__(self) -> None:
        """创建假渲染器。"""
        self.calls: list = []

    def size(self) -> tuple:
        """返回画布尺寸。"""
        return (640, 480)

    def begin_frame(self, background) -> None:
        """记账：开始一帧。"""
        self.calls.append(("begin", background))

    def draw_rect(self, rect, color) -> None:
        """记账：矩形。"""
        self.calls.append(("rect", rect))

    def draw_polygon(self, points, color, *, outline_color=None, outline_width=0.0) -> None:
        """记账：多边形。"""
        self.calls.append(("polygon", points))

    def draw_text(self, text, rect, *, size, color, align="center") -> None:
        """记账：文字。"""
        self.calls.append(("text", rect))

    def draw_image(self, path, rect) -> None:
        """记账：图片。"""
        self.calls.append(("image", rect))

    def end_frame(self) -> None:
        """记账：结束一帧。"""
        self.calls.append(("end",))


if __name__ == "__main__":
    unittest.main()
