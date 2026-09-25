"""示例兵棋（mods/demo_river）加载与初始局面的验收测试（标准库 unittest）。

位置：tests/unit/mods/
运行：在仓库根执行 `python run_tests.py`。

覆盖（对应示例兵棋设计文档 §5 / §7 / §13）：
  - 加载：729 格地图（27×27 正方形）→ State（节点 / 相邻关系 / 渡口对 / 编成）；
  - 单位：10 北 + 9 南，摆在合法格子上，**不能堆叠**；
  - 相邻关系对称（A 连 B ⇒ B 连 A）；
  - 地形属性进了 State（河不可进入、村庄防守 ×2、胜利点标记）；
  - 渡口成对：3 对 → 6 条双向通道，每对两岸隔一格河水；
  - 这个 Mod **自己一个函数都不写**（玩法逻辑全在模块里）；
  - 视图脚本能把整张地图画出来（层数 = 729 × 3 + HUD）。
"""

import unittest
from pathlib import Path

from adapters import LocalFileSystem, PythonScriptLoader
from core.context import Context
from core.pipeline import Input
from core.ui import PageContext
from modload import ModLoader
from shell import GameSession

REPO_ROOT = Path(__file__).resolve().parents[3]
MODS_ROOT = REPO_ROOT / "mods"
MODULES_ROOT = REPO_ROOT / "modules"
RIVER_FOLDER = MODS_ROOT / "demo_river"


def load_river():
    """加载示例兵棋 Mod。"""
    loader = ModLoader(LocalFileSystem(), PythonScriptLoader(), str(MODS_ROOT),
                       module_roots=[str(MODULES_ROOT)])
    return loader.load(loader.load_info(str(RIVER_FOLDER)))


class TestDemoRiverLoad(unittest.TestCase):
    """加载结果与初始局面。"""

    def test_builds_729_hex_state(self):
        """地图 729 格 → 节点与相邻关系都在，回合 / 控制方 / 阶段有初值。"""
        state = load_river().initial_state
        self.assertEqual(len(state["nodes"]), 729)
        self.assertEqual(len(state["adjacency"]), 729)
        self.assertEqual(state["control_side"], "north")
        self.assertEqual(state["stage"], "move")
        self.assertEqual(state["turn"], 1)
        self.assertFalse(state["game_over"])
        self.assertEqual(state["map"]["cols"], 27)

    def test_units_are_placed_without_stacking(self):
        """19 个单位（北 10 / 南 9）都在地图上，而且没有两个挤在一格。"""
        state = load_river().initial_state
        units = state["units"]
        self.assertEqual(len(units), 19)
        self.assertEqual(sum(1 for u in units.values() if u["side"] == "north"), 10)
        self.assertEqual(sum(1 for u in units.values() if u["side"] == "south"), 9)

        positions = [u["at"] for u in units.values()]
        self.assertEqual(len(set(positions)), len(positions))     # 不能堆叠
        for unit in units.values():
            with self.subTest(unit=unit["id"]):
                self.assertIn(unit["at"], state["nodes"])
                self.assertFalse(unit["destroyed"])
                self.assertFalse(unit["disordered"])

    def test_adjacency_is_symmetric(self):
        """相邻关系必须对称（A 连 B ⇒ B 连 A），每格最多 6 个邻居。"""
        state = load_river().initial_state
        for key, neighbors in state["adjacency"].items():
            self.assertLessEqual(len(neighbors), 6)
            for neighbor in neighbors:
                self.assertIn(key, state["adjacency"][neighbor])

    def test_terrain_attributes_reach_the_state(self):
        """地形属性进了 State：河不可进入、村庄防守 ×2、胜利点被标出来。"""
        state = load_river().initial_state
        nodes = state["nodes"]

        river = [n for n in nodes.values() if n["terrain"] == "river"]
        self.assertEqual(len(river), 51)
        self.assertFalse(river[0]["passable"])

        village = [n for n in nodes.values() if n["terrain"] == "village"]
        self.assertEqual(len(village), 7)
        self.assertEqual(village[0]["defense_mult"], 2)

        victory = [n for n in nodes.values() if n.get("victory")]
        self.assertEqual(len(victory), 1)
        self.assertEqual((victory[0]["row"], victory[0]["col"]), (26, 13))

        hill = [n for n in nodes.values() if n["terrain"] == "hill"]
        self.assertEqual(hill[0]["move_cost"], 2)
        self.assertEqual(hill[0]["defense_mult"], 2)

    def test_ford_crossings_are_paired_and_two_way(self):
        """渡口：3 对 → 6 条双向通道，每条的"两地"中间隔着一格河水。"""
        state = load_river().initial_state
        fords = state["fords"]
        self.assertEqual(len(fords), 6)

        by_node = {key: node for key, node in state["nodes"].items()}
        for crossing in fords:
            with self.subTest(crossing=crossing):
                self.assertEqual(crossing["cost"], 3)
                start = by_node[crossing["from"]]
                end = by_node[crossing["to"]]
                self.assertEqual(start["col"], end["col"])           # 同一列
                self.assertEqual(abs(start["row"] - end["row"]), 2)  # 相隔两行
                middle = [n for n in state["nodes"].values()
                          if n["col"] == start["col"]
                          and n["row"] == (start["row"] + end["row"]) // 2]
                self.assertEqual(len(middle), 1)
        self.assertEqual(middle[0]["terrain"], "river")

    def test_mod_writes_no_logic_functions(self):
        """这个 Mod 自己不写玩法函数（几何 / 地图解析都在模块里）。"""
        loaded = load_river()
        self.assertEqual(loaded.user_functions, {})
        self.assertIn("pack_hex_grid.text_map.rows_cols", loaded.functions)
        self.assertIn("pack_hex_grid.map_check.fords", loaded.functions)
        self.assertIn("pack_combat_crt.crt.table", loaded.functions)

    def test_view_draws_the_whole_map(self):
        """视图脚本：带视口、只画可见范围（裁剪）、HUD 固定不跟视口动。"""
        loaded = load_river()
        state = loaded.initial_state
        state["intro_seen"] = True
        page = loaded.pages["demo_river:ui:battle"]
        view = page(PageContext(state=state, size=(960, 640),
                                context=Context(), page_id="demo_river:ui:battle",
                                functions=loaded.functions))

        self.assertIsNotNone(view.viewport)                      # 有了视口
        hud = next(layer for layer in view.layers if layer.id == "hud")
        self.assertTrue(hud.fixed)                               # HUD 不跟视口动
        self.assertTrue(any(layer.kind == "polygon" for layer in view.layers))
        # 裁剪：只画镜头内的格子，层的数量远小于"整图 729 × 3 + 1"。
        drawn = sum(1 for layer in view.layers if layer.kind == "polygon")
        self.assertGreater(drawn, 0)
        self.assertLess(len(view.layers), 729 * 3 + 1)

    def test_camera_inputs_are_declared(self):
        """滚轮与拖动都登记过输入（外壳只会转发"Mod 声明过的"那两类事件）。"""
        content = load_river().content
        self.assertTrue(content.input_candidates("wheel"))
        self.assertTrue(content.input_candidates("drag"))

    def test_zoom_out_switches_to_lod(self):
        """缩到阈值（0.5×）以下才换"缩略模式"：每格只有一层（不画格线、不写文字）。"""
        loaded = load_river()
        state = loaded.initial_state
        state["intro_seen"] = True
        page = loaded.pages["demo_river:ui:battle"]
        context = Context()
        context.put("demo_river.camera.zoom", 0.3)
        view = page(PageContext(state=state, size=(960, 640),
                                context=context, page_id="demo_river:ui:battle",
                                functions=loaded.functions))
        polygons = [layer for layer in view.layers if layer.kind == "polygon"]
        self.assertTrue(polygons)
        # 格子文字层：缩略模式下一格都不该有（侧栏 / HUD / 按钮是 fixed 的，不算格子文字）。
        cell_texts = [layer for layer in view.layers
                      if layer.kind == "text" and not layer.fixed and layer.id.startswith("grid:")]
        self.assertEqual(cell_texts, [])
        self.assertTrue(any(layer.id.startswith("grid:") and layer.kind == "polygon"
                            for layer in view.layers))

    def test_view_with_a_pending_move_path_is_ok(self):
        """选中单位 + 预览了目标格时，视图要能画出"路径"（曾经因为模块名写错崩过）。"""
        loaded = load_river()
        page = loaded.pages["demo_river:ui:battle"]
        state = loaded.initial_state
        state["intro_seen"] = True
        unit = state["units"]["north_5"]
        key = {(n["row"], n["col"]): k for k, n in state["nodes"].items()}
        context = Context()
        context.put("demo_river.camera.zoom", 0.3)      # 缩小到整张图可见（地图区变窄了）
        context.put("demo_river.selected", "north_5")
        context.put("demo_river.pending", key[(3, 9)])

        view = page(PageContext(state=state, size=(960, 640), context=context,
                                page_id="demo_river:ui:battle", functions=loaded.functions))
        path_layers = [l for l in view.layers if l.id.startswith("grid:") and l.kind == "polygon"]
        self.assertTrue(path_layers)
        self.assertIn(unit["at"], [l.id[len("grid:"):] for l in path_layers])

    def test_wheel_zooms_and_drag_pans_the_camera(self):
        """滚轮缩放、拖动平移：都写进 Context（界面状态，不进存档）。"""
        session = GameSession(load_river(), files=LocalFileSystem(), seed=1)
        context = session.runtime.context

        session.submit_input(Input("wheel", {"delta": [0.0, 3.0], "point": [100.0, 100.0]}, 1.0, "ui"))
        session.settle(["ui:wheel"])
        zoom = context.get("demo_river.camera.zoom", 1.0)
        self.assertGreater(zoom, 1.0)

        session.submit_input(Input("drag", {"delta": [24.0, -10.0], "point": [120.0, 90.0]}, 2.0, "ui"))
        session.settle(["ui:drag"])
        self.assertAlmostEqual(context.get("demo_river.camera.x", 0.0), 24.0, places=6)
        self.assertAlmostEqual(context.get("demo_river.camera.y", 0.0), -10.0, places=6)


class TestDemoRiverView(unittest.TestCase):
    """视图层的观感与"读数据不写死"约定（2026-09-24 UI 轮）。"""

    def _view(self, loaded, state=None, zoom=0.6, *, show_intro=False):
        """按给定缩放生成一帧（0.6× 能看到整张图，又还在全画法）。"""
        state = loaded.initial_state if state is None else state
        if not show_intro:
            state["intro_seen"] = True      # 这批用例只测视图，不开局弹介绍
        context = Context()
        context.put("demo_river.camera.zoom", zoom)
        page = loaded.pages["demo_river:ui:battle"]
        return page(PageContext(state=state, size=(960, 640), context=context,
                                page_id="demo_river:ui:battle", functions=loaded.functions))

    def _cell_layer(self, view, cell):
        """取某一格的 polygon 层（不是它的文字层）。"""
        return next(layer for layer in view.layers if layer.id == f"grid:{cell}")

    def _layer(self, view, layer_id):
        """按 id 取一层。"""
        return next(layer for layer in view.layers if layer.id == layer_id)

    def test_victory_marker_is_drawn_even_when_occupied(self):
        """胜利点开局被守军占着，金色五角星仍然独立画出来。"""
        loaded = load_river()
        state = loaded.initial_state
        victory = next(key for key, node in state["nodes"].items() if node.get("victory"))
        view = self._view(loaded, state)
        marker = next(layer for layer in view.layers if layer.id == "marker:victory")
        self.assertEqual(marker.kind, "polygon")
        self.assertEqual(marker.color, (230, 200, 100, 255))
        # 地块本身露出胜利点的金色，守军的红色只在中心兵牌上；标记照样存在。
        text = next(layer for layer in view.layers if layer.id == f"grid:{victory}:text")
        self.assertIn("柳浦 4 连", text.text)
        self.assertEqual(self._cell_layer(view, victory).color, (108, 92, 52, 255))
        self.assertEqual(self._layer(view, "unit:south_4").color, (104, 62, 62, 255))

    def test_unit_text_reads_move_and_attack_from_state(self):
        """单位格写"名字 + 移动-攻击"；改 State 里的属性，文字跟着变。"""
        loaded = load_river()
        state = loaded.initial_state
        cell = state["units"]["north_1"]["at"]
        unit = state["units"]["north_1"]
        text = next(layer for layer in self._view(loaded, state).layers
                    if layer.id == f"grid:{cell}:text")
        self.assertIn("青隼 1 营", text.text)
        self.assertIn(f"{unit['move']}-{unit['attack']}", text.text)
        unit["attack"] = 7
        changed = next(layer for layer in self._view(loaded, state).layers
                       if layer.id == f"grid:{cell}:text")
        self.assertIn(f"{unit['move']}-7", changed.text)

    def test_disordered_unit_has_red_cross_marker(self):
        """混乱单位在右上角画红色 ×；没有混乱的单位不画。"""
        loaded = load_river()
        state = loaded.initial_state
        state["units"]["north_1"]["disordered"] = True
        view = self._view(loaded, state)
        marker = next(layer for layer in view.layers if layer.id == "marker:disorder:north_1")
        self.assertEqual(marker.text, "×")
        self.assertEqual(marker.text_color, (240, 70, 70, 255))
        self.assertEqual(marker.color[3], 0)       # 没有黑色底片
        outline = self._layer(view, "marker:disorder-outline:north_1")
        self.assertEqual(outline.text_color, (245, 242, 235, 255))
        self.assertFalse(any(layer.id == "marker:disorder:north_2" for layer in view.layers))

    def test_spent_unit_gets_a_darker_fill_in_move_stage(self):
        """移动阶段：当前控制方没有行动力的单位用深一档的颜色。"""
        loaded = load_river()
        state = loaded.initial_state
        state["units"]["north_1"]["move_left"] = 0
        view = self._view(loaded, state)
        spent = self._layer(view, "unit:north_1").color
        ready = self._layer(view, "unit:north_2").color
        self.assertEqual(spent, (38, 52, 78, 255))
        self.assertEqual(ready, (56, 78, 116, 255))
        # 地块本身还是平原绿：兵牌没有盖满整个格子
        self.assertEqual(self._cell_layer(view, state["units"]["north_1"]["at"]).color,
                         (86, 122, 74, 255))

    def test_zoc_cells_keep_terrain_colors(self):
        """控制区不在界面上染色：空格子保持地形色（不再盖住地块类型）。"""
        loaded = load_river()
        state = loaded.initial_state
        disordered = [key for key, unit in state["units"].items() if unit.get("disordered")]
        table = loaded.functions["pack_path_hex.zoc.from_units"](
            state["units"], state["adjacency"], disordered)
        enemy = "south" if state["control_side"] == "north" else "north"
        occupied = {unit.get("at") for unit in state["units"].values() if unit.get("at")}
        cell = next(cell for cell in sorted(table[enemy])
                    if cell not in occupied and state["nodes"][cell].get("terrain") == "plain")
        layer = self._cell_layer(self._view(loaded, state), cell)
        self.assertEqual(layer.color, (86, 122, 74, 255))          # 还是平原绿
        self.assertEqual(layer.outline_color, (86, 94, 110, 255))  # 还是平原描边，不是控制区红边

    def test_unit_counter_keeps_side_color(self):
        """单位格的地形色露出来，阵营色只在中心兵牌上：攻方进控制区仍然是蓝的。"""
        loaded = load_river()
        state = loaded.initial_state
        south_cell = state["units"]["south_1"]["at"]
        occupied = {unit.get("at") for unit in state["units"].values() if unit.get("at")}
        target = next(cell for cell in state["adjacency"][south_cell] if cell not in occupied)
        state["units"]["north_1"]["at"] = target
        view = self._view(loaded, state)
        self.assertNotEqual(self._cell_layer(view, target).color, (56, 78, 116, 255))
        self.assertEqual(self._layer(view, "unit:north_1").color, (56, 78, 116, 255))

    def test_sidebar_has_tables_and_scrollable_log(self):
        """右栏：地形表 + CRT 表 + 带滚轮目标的战报栏。"""
        loaded = load_river()
        state = loaded.initial_state
        state["log"] = [f"第 {index} 条战报" for index in range(30)]
        view = self._view(loaded, state)
        ids = {layer.id for layer in view.layers}
        self.assertIn("table:地形表:title", ids)
        self.assertIn("table:CRT:title", ids)
        self.assertEqual(self._layer(view, "log:bg").wheel.kind, "scroll_log")
        self.assertIn("log:thumb", ids)
        log_bg = self._layer(view, "log:bg")
        self.assertIsNone(view.hit_test((log_bg.rect[0] + 5, log_bg.rect[1] + 5)))

    def test_intro_modal_blocks_map_and_has_confirm(self):
        """开局介绍弹窗：有隐形阻挡层与确认按钮，地图点不进去。"""
        loaded = load_river()
        view = self._view(loaded, loaded.initial_state, show_intro=True)
        ids = {layer.id for layer in view.layers}
        self.assertIn("modal:blocker", ids)
        self.assertIn("modal:panel", ids)
        self.assertEqual(self._layer(view, "modal:confirm").click.kind, "close_rules")
        self.assertIsNone(view.hit_test((100.0, 100.0)))          # 地图被挡住
        self.assertIsNotNone(view.pointer_blocker((100.0, 100.0)))

    def test_table_columns_do_not_overlap(self):
        """表格第一列从 x 开始，右边紧接第二列（不再两列重叠）。"""
        loaded = load_river()
        view = self._view(loaded)
        for prefix in ("table:地形表", "table:CRT"):
            first = self._layer(view, f"{prefix}:0:0")
            second = self._layer(view, f"{prefix}:0:1")
            self.assertAlmostEqual(first.rect[0] + first.rect[2], second.rect[0], places=6)
            self.assertLess(first.rect[0], second.rect[0])

    def test_log_lines_use_the_panel_width(self):
        """战报换行按像素宽度算：一行能放下的中文比旧实现多。"""
        loaded = load_river()
        state = loaded.initial_state
        state["log"] = ["这是一条很长的战报" * 4]
        line = self._layer(self._view(loaded, state), "log:line:0")
        self.assertGreater(len(line.text), 12)     # 旧实现约 10 个字就折行

    def test_intro_lines_use_the_panel_width(self):
        """开局介绍按面板像素宽折行，不会只占左半边。"""
        loaded = load_river()
        view = self._view(loaded, loaded.initial_state, show_intro=True)
        lines = [layer for layer in view.layers if layer.id.startswith("modal:line:")]
        self.assertTrue(any(len(layer.text) > 30 for layer in lines))


if __name__ == "__main__":
    unittest.main()
