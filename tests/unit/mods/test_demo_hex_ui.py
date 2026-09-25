"""演示 Mod 的界面链路验收测试（§17：外壳 UI 与游戏内 UI 共用一套机制）。

位置：tests/unit/mods/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 外壳流程：首页 → 选 Mod → 开新局 → 游戏内 → 存档 → 返回首页 → 读档；
  - 游戏内画面完全由 Mod 的视图脚本产出（引擎侧一个玩法名词都没有）；
  - 点击 → 统一输入 → 命令 → 结算 → State 变化 的整条互动流；
  - 外壳按钮与 Mod 界面的层次关系（外壳在上，先收到点击）。

窗口用一个假实现（记录绘制调用、不依赖 pygame），所以这个测试可以在无显示环境跑。
"""

import shutil
import unittest
from pathlib import Path

from adapters import LocalFileSystem, PythonScriptLoader
from core.ports import WindowEvent
from core.ui import View
from core.version import ENGINE_NAME, version_text
from shell import ShellApp

REPO_ROOT = Path(__file__).resolve().parents[3]
MODS_ROOT = REPO_ROOT / "mods"
MODULES_ROOT = REPO_ROOT / "modules"
SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"
SIZE = (960, 640)


class FakeWindow:
    """假窗口：满足 Window + Renderer 两个端口，记录被画了什么。"""

    def __init__(self) -> None:
        """创建假窗口。"""
        self.drawn: list[tuple] = []
        self.closed = False
        self._events: list[WindowEvent] = []

    def size(self) -> tuple[int, int]:
        """返回窗口尺寸。"""
        return SIZE

    def feed(self, *events: WindowEvent) -> None:
        """塞入事件（模拟用户操作）。"""
        self._events.extend(events)

    def poll_events(self) -> tuple[WindowEvent, ...]:
        """取走事件。"""
        events = tuple(self._events)
        self._events.clear()
        return events

    def close(self) -> None:
        """记下关闭。"""
        self.closed = True

    def begin_frame(self, background) -> None:
        """记账：开始一帧。"""
        self.drawn.append(("begin", background))

    def draw_rect(self, rect, color) -> None:
        """记账：矩形。"""
        self.drawn.append(("rect", rect))

    def draw_polygon(self, points, color, *, outline_color=None, outline_width=0.0) -> None:
        """记账：多边形。"""
        self.drawn.append(("polygon", points))

    def draw_text(self, text, rect, *, size, color, align="center") -> None:
        """记账：文字。"""
        self.drawn.append(("text", text, rect))

    def draw_image(self, path, rect) -> None:
        """记账：图片。"""
        self.drawn.append(("image", path, rect))

    def end_frame(self) -> None:
        """记账：结束一帧。"""
        self.drawn.append(("end",))


def layer_center(view: View, layer_id: str) -> tuple[int, int]:
    """取某个层的中心点（模拟"点这个层"）。

    输入：
        view: 当前画面；
        layer_id: 层的 id。
    输出：
        (x, y) 整数坐标。
    异常：
        AssertionError: 画面上没有这个层（说明上一步没走到预期界面）。
    变量：
        无。
    """
    for layer in view.layers:
        if layer.id == layer_id:
            center = layer.center
            return int(center[0]), int(center[1])
    raise AssertionError(f"当前画面上没有层 {layer_id!r}：{[layer.id for layer in view.layers]}")


class TestDemoUiFlow(unittest.TestCase):
    """外壳 + Mod 界面的整条链路。"""

    def setUp(self):
        """准备存档目录与外壳应用。"""
        self._folder = SCRATCH_ROOT / f"case_{self._testMethodName}"
        shutil.rmtree(self._folder, ignore_errors=True)
        self._folder.mkdir(parents=True, exist_ok=True)
        self.window = FakeWindow()
        self.app = ShellApp(
            window=self.window,
            files=LocalFileSystem(),
            scripts=PythonScriptLoader(),
            mods_root=str(MODS_ROOT),
            saves_root=str(self._folder),
            modules_root=str(MODULES_ROOT),
            seed_supplier=lambda: 2026,
        )

    def tearDown(self):
        """清掉临时目录。"""
        shutil.rmtree(self._folder, ignore_errors=True)
        try:
            SCRATCH_ROOT.rmdir()
        except OSError:
            pass

    def _click(self, view: View, layer_id: str) -> None:
        """点画面上某个层（走外壳的点击处理）。"""
        self.app.handle_click(WindowEvent("click", point=layer_center(view, layer_id), timestamp=1.0))

    @staticmethod
    def _click_kind(view: View, layer_id: str):
        """返回某个层被点时产生的输入类别（不吃点击就是 None）。"""
        layer = next(layer for layer in view.layers if layer.id == layer_id)
        return layer.click.kind if layer.click is not None else None

    def _enter_game(self) -> View:
        """从首页走到游戏内，返回游戏画面。"""
        view = self.app.build_view()
        self._click(view, "shell:new")           # 首页 → 选 Mod
        view = self.app.build_view()
        self._click(view, "shell:mod:demo_hex")  # 选 Mod → 开新局
        return self.app.build_view()

    def test_menu_to_game_and_click_flow(self):
        """首页 → 开新局 → 点单位选中 → 点相邻格子移动 → 点敌军攻击。"""
        view = self.app.build_view()
        self.assertTrue(any(layer.id == "shell:new" for layer in view.layers))
        title = next(layer for layer in view.layers if layer.id == "shell:title")
        self.assertEqual(title.text, ENGINE_NAME)
        version = next(layer for layer in view.layers if layer.id == "shell:version")
        self.assertEqual(version.text, version_text())

        view = self._enter_game()
        ids = {layer.id for layer in view.layers}
        self.assertIn("grid:n-1_0", ids)          # 格子由模块界面件产出
        self.assertIn("grid:n1_0", ids)
        self.assertIn("hud", ids)                  # Mod 画的战报
        self.assertIn("button:end_turn", ids)      # Mod 画的按钮
        self.assertIn("shell:game:menu_toggle", ids)   # 外壳菜单按钮叠在上面
        self.assertNotIn("shell:version", ids)          # 版本角标只在首页
        self.assertNotIn("shell:game:version", ids)

        session = self.app.session
        assert session is not None
        self.assertEqual(session.state["units"]["red_1"]["at"], "n-1_0")

        # 点红军的格子 → 选中（select 输入 → 命令 → 服务写界面状态）
        self._click(view, "grid:n-1_0")
        self.assertEqual(session.runtime.context.get("demo_hex.selected"), "red_1")

        # 重画（选中会影响每个格子的点击结果），点相邻的中心格 → 移动
        view = self.app.build_view()
        self._click(view, "grid:n0_0")
        self.assertEqual(session.state["units"]["red_1"]["at"], "n0_0")
        self.assertEqual(session.state["units"]["red_1"]["ap"], 1)

        # 点蓝军所在的格子（与红军相邻）→ 攻击
        view = self.app.build_view()
        before_hp = session.state["units"]["blue_1"]["hp"]
        self._click(view, "grid:n1_0")
        self.assertLess(session.state["units"]["blue_1"]["hp"], before_hp)

    def test_menu_bar_opens_with_escape_and_returns_home(self):
        """游戏界面 Esc 开菜单；菜单页面 Esc 返回首页。"""
        view = self._enter_game()
        ids = {layer.id for layer in view.layers}
        self.assertIn("shell:game:menu_toggle", ids)
        self.assertNotIn("shell:game:save", ids)

        self.app.handle_key("escape")
        view = self.app.build_view()
        ids = {layer.id for layer in view.layers}
        self.assertIn("shell:game:save", ids)
        self.assertIn("shell:game:back", ids)

        self.app.handle_key("escape")          # 菜单页面：返回首页
        self.assertIn("shell:new", {layer.id for layer in self.app.build_view().layers})

    def test_mod_menu_entry_opens_rules_and_escape_closes(self):
        """demo_river 的「规则」菜单项：外壳把 open_rules 送进会话；规则页 Esc 关闭。"""
        view = self.app.build_view()
        self._click(view, "shell:new")                 # 首页 → 选 Mod
        view = self.app.build_view()
        self._click(view, "shell:mod:demo_river")      # 选 demo_river → 开新局
        ids = {layer.id for layer in self.app.build_view().layers}
        self.assertIn("modal:blocker", ids)                    # 开局介绍先弹
        self.assertNotIn("shell:game:menu_toggle", ids)        # 弹窗期间菜单按钮让位

        self.app.handle_key("escape")                          # 规则页 Esc = 确认/关闭
        ids = {layer.id for layer in self.app.build_view().layers}
        self.assertIn("shell:game:menu_toggle", ids)

        self.app.handle_key("escape")                          # 游戏界面 Esc = 打开菜单
        view = self.app.build_view()
        self.assertIn("shell:modmenu:0", {layer.id for layer in view.layers})
        self._click(view, "shell:modmenu:0")
        self.assertIn("modal:blocker", {layer.id for layer in self.app.build_view().layers})

        self.app.handle_key("escape")                          # 规则页 Esc = 关闭
        session = self.app.session
        assert session is not None
        self.assertFalse(session.runtime.context.get("demo_river.rules_open"))
        ids = {layer.id for layer in self.app.build_view().layers}
        self.assertIn("shell:game:menu_toggle", ids)
        self.assertNotIn("shell:menu:panel", ids)   # 关闭规则后直接回游戏，不是回菜单

    def test_end_turn_button_and_keys(self):
        """结束回合按钮（Mod 提供）与外壳按键（S 存档、Esc 返回）。"""
        view = self._enter_game()
        session = self.app.session
        assert session is not None

        self._click(view, "button:end_turn")
        self.assertEqual(session.state["turn"], 2)

        self.app.handle_key("s")
        self.assertTrue(Path(session.save_path).is_file())

        self.app.handle_key("escape")     # 游戏界面：开菜单
        self.app.handle_key("escape")     # 菜单页面：返回首页
        view = self.app.build_view()
        self.assertTrue(any(layer.id == "shell:new" for layer in view.layers))

    def test_map_cells_are_real_hexagons(self):
        """地图是真的六边形：格子层是多边形，命中判定按形状算（外接矩形的角不算命中）。"""
        view = self._enter_game()
        cell = next(layer for layer in view.layers if layer.id == "grid:n-1_0")
        self.assertEqual(cell.kind, "polygon")
        self.assertEqual(len(cell.points), 6)
        # 外接矩形的左上角在形状外：按矩形判定会错点到这个格子，按多边形判定不会。
        self.assertIsNone(view.hit_test((cell.rect[0] + 1.0, cell.rect[1] + 1.0)))
        self.assertEqual(view.hit_test(cell.center).id, "grid:n-1_0")

    def test_battle_screen_explains_itself(self):
        """画面自己讲清楚怎么玩：左侧说明、右侧状态、控制方与高亮都在。"""
        view = self._enter_game()
        ids = {layer.id for layer in view.layers}
        self.assertIn("help", ids)
        self.assertIn("hud", ids)

        help_layer = next(layer for layer in view.layers if layer.id == "help")
        self.assertIn("怎么玩", help_layer.text)
        self.assertIn("结束回合", help_layer.text)
        self.assertIn("金框", help_layer.text)

        hud = next(layer for layer in view.layers if layer.id == "hud")
        self.assertIn("当前控制方：红方", hud.text)
        self.assertIn("红方单位", hud.text)
        self.assertIn("蓝方单位", hud.text)
        # 结算按钮写明"交给谁"。
        button = next(layer for layer in view.layers if layer.id == "button:end_turn")
        self.assertIn("交给蓝方", button.text)

    def test_only_legal_actions_are_clickable(self):
        """界面只给合法操作点击结果：自己的单位可选、够不着的敌军点不动、空地取消选中。"""
        view = self._enter_game()
        # 没有选中单位时：自己的单位能选；敌军与空地都不产生输入。
        self.assertEqual(self._click_kind(view, "grid:n-1_0"), "select")
        self.assertIsNone(self._click_kind(view, "grid:n1_0"))
        self.assertIsNone(self._click_kind(view, "grid:n0_1"))

        # 选中红军之后：相邻空格 = 能走；够不着的敌军 = 仍然点不动。
        self._click(view, "grid:n-1_0")
        view = self.app.build_view()
        self.assertEqual(self._click_kind(view, "grid:n0_0"), "move")
        self.assertIsNone(self._click_kind(view, "grid:n1_0"))   # 距离 2，打不到

        # 点一个够不着的空格 = 只是取消选中，不产生游戏动作。
        session = self.app.session
        assert session is not None
        self._click(view, "grid:n0_1")
        self.assertEqual(session.runtime.context.get("demo_hex.selected", ""), "")
        self.assertEqual(session.state["units"]["red_1"]["at"], "n-1_0")

    def test_control_side_switches_and_dead_units_disappear(self):
        """结束回合换控制方；被打死的单位从棋盘上消失（状态里留记录）。"""
        view = self._enter_game()
        session = self.app.session
        assert session is not None

        # 结束回合：控制权交给蓝方，此时红军不可点、蓝军可点。
        self._click(view, "button:end_turn")
        view = self.app.build_view()
        self.assertEqual(session.state["control_side"], "blue")
        self.assertIsNone(self._click_kind(view, "grid:n-1_0"))
        self.assertEqual(self._click_kind(view, "grid:n1_0"), "select")

        # 直接把蓝军打到 0 血（界面链路之外的状态改动，只为看画面怎么处理）。
        session.state["units"]["blue_1"]["hp"] = 0
        session.state["units"]["blue_1"]["destroyed"] = True
        session.state["units"]["blue_1"]["at"] = ""
        session.state["game_over"] = True
        session.state["winner"] = "red"
        session.runtime.refresh_derived()
        view = self.app.build_view()

        ids = {layer.id for layer in view.layers}
        self.assertIn("banner:winner", ids)                  # 胜负横幅
        # 阵亡单位不再画在格子上：那一格回到"空格"的样子（底色变回深灰、只显示格子键）。
        fill = next(layer for layer in view.layers if layer.id == "grid:n1_0")
        text = next(layer for layer in view.layers if layer.id == "grid:n1_0:text")
        self.assertEqual(fill.color, (44, 50, 62, 255))
        self.assertEqual(text.text, "n1_0")
        for layer_id in ("grid:n-1_0", "grid:n0_0", "grid:n1_0"):
            self.assertIsNone(self._click_kind(view, layer_id), layer_id)   # 打完了就不能再点
    def test_save_then_load_from_shell(self):
        """外壳读档：从存档列表里点一份存档，局面回来。"""
        view = self._enter_game()
        self._click(view, "grid:n-1_0")
        view = self.app.build_view()
        self._click(view, "grid:n0_0")
        session = self.app.session
        assert session is not None
        session.save()
        moved = dict(session.state["units"]["red_1"])

        self.app.handle_key("escape")
        self.app.handle_key("escape")
        self.app.start_new_game("demo_hex")
        reopened = self.app.session
        assert reopened is not None
        self.assertEqual(reopened.state["units"]["red_1"]["at"], "n-1_0")

        self.app.handle_key("escape")
        self.app.handle_key("escape")
        view = self.app.build_view()
        self._click(view, "shell:load")
        view = self.app.build_view()
        # 返回首页时会自动存档（engine:syscall:autosave），所以列表里有两份：
        # 手动槽位与自动存档。这里点手动那份。
        save_layers = [
            layer.id for layer in view.layers
            if layer.id.startswith("shell:save:") and not layer.id.endswith("_autosave.json")
        ]
        self.assertEqual(len(save_layers), 1)
        auto_layers = [layer.id for layer in view.layers if layer.id.endswith("_autosave.json")]
        self.assertEqual(len(auto_layers), 1)   # 自动存档也在
        self._click(view, save_layers[0])
        loaded = self.app.session
        assert loaded is not None
        self.assertEqual(loaded.state["units"]["red_1"], moved)

    def test_settings_screen_and_hot_reload_key(self):
        """设置页可以进能出；游戏内按 F5 走"重载 Mod"系统事件，局面保留。"""
        # 设置页：标题 + 关于 + 返回；关于页读外置文字。
        view = self.app.build_view()
        self._click(view, "shell:settings")
        view = self.app.build_view()
        ids = {layer.id for layer in view.layers}
        self.assertIn("shell:title", ids)
        self.assertIn("shell:about", ids)
        self.assertIn("shell:back", ids)
        self.assertNotIn("shell:settings_text", ids)
        self._click(view, "shell:about")
        view = self.app.build_view()
        about = next(layer.text for layer in view.layers if layer.id == "shell:about_text")
        self.assertIn("通用兵棋引擎", about)
        self._click(view, "shell:about_back")
        view = self.app.build_view()
        self.assertIn("shell:about", {layer.id for layer in view.layers})
        self._click(view, "shell:back")
        self.assertTrue(any(layer.id == "shell:new" for layer in self.app.build_view().layers))

        # 游戏内按 F5：重载 Mod（内容重新加载，局面不变）。
        view = self._enter_game()
        session = self.app.session
        assert session is not None
        self._click(view, "grid:n-1_0")            # 选中，制造一点局面
        position_before = session.state["units"]["red_1"]["at"]
        self.app.handle_key("f5")
        self.assertIs(self.app.session, session)   # 还是同一个会话
        self.assertEqual(session.state["units"]["red_1"]["at"], position_before)
        self.assertIn("重载", self.app.context.get("shell.message", ""))


if __name__ == "__main__":
    unittest.main()
