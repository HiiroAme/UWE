"""modload 的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/modload/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 扫描：只把有 mod_info.json 的文件夹当 Mod，且按名字排序；
  - 浅加载：mod_info.json 的必填字段与 init 写法检查；
  - 全加载：条目登记、内容编译、脚本取出、初始化钩子返回初始 State；
  - 失败口径：脚本缺失、条目文件不是数组、init 返回值不是 dict；
  - 加载期引用核对（E-1 / 甲-8 / R4-1～R4-3）：字面量函数名、数据侧 ["call", …]、
    import 白名单（标准库 + core.ui）、相对 import 明确拒绝；
  - 绑定参数写错键名要报错（甲-3）。

测试用的 Mod 数据写在 tests/_scratch 下（用例结束即删），不依赖 mods/ 里的演示 Mod。
"""

import json
import pathlib
import shutil
import unittest
from pathlib import Path

from adapters import LocalFileSystem, PythonScriptLoader
from core.content import ContentReferenceError
from core.logic import LogicError
from modload import ModLoader
from modload.info import ModInfoError
from modload.pack_info import PackInfoError

SCRATCH_ROOT = Path(__file__).resolve().parents[2] / "_scratch"


def write(path: Path, text: str) -> None:
    """写一个文本文件（父目录自动建）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data) -> None:
    """写一个 JSON 文件。"""
    write(path, json.dumps(data, ensure_ascii=False))


class TestLoader(unittest.TestCase):
    """加载器的扫描、浅加载与全加载。"""

    def setUp(self):
        """每个用例一个干净的 Mod 根目录。"""
        self._root = SCRATCH_ROOT / f"case_{self._testMethodName}" / "mods"
        shutil.rmtree(self._root, ignore_errors=True)
        self._root.mkdir(parents=True, exist_ok=True)
        self._loader = ModLoader(LocalFileSystem(), PythonScriptLoader(), str(self._root))

    def tearDown(self):
        """清掉临时目录。"""
        shutil.rmtree(self._root.parent, ignore_errors=True)
        try:
            SCRATCH_ROOT.rmdir()
        except OSError:
            pass

    def _make_mod(self, name: str = "demo", *, info: dict | None = None, entries: list | None = None,
                  scripts: dict | None = None) -> Path:
        """在临时根目录里造一个 Mod。

        输入：
            name: 文件夹名；
            info: mod_info.json 内容（缺省给一份最小可用的）；
            entries: data/10.json 里的条目数组；
            scripts: {脚本文件名: 源码}。
        输出：
            Mod 文件夹路径。
        异常：
            无。
        变量：
            folder / default_info / file_name / source: 中间结果。
        """
        folder = self._root / name
        default_info = {"id": name, "name": "演示", "version": "0.0.0"}
        if info:
            default_info.update(info)
        write_json(folder / "mod_info.json", default_info)
        if entries is not None:
            write_json(folder / "data" / "10_entries.json", entries)
        for file_name, source in (scripts or {}).items():
            write(folder / "scripts" / file_name, source)
        return folder

    def test_scan_sorted_and_skips_non_mods(self):
        """扫描：按文件夹名排序；没有 mod_info.json 的目录被跳过。"""
        self._make_mod("beta")
        self._make_mod("alpha")
        (self._root / "not_a_mod").mkdir()
        found = self._loader.scan()
        self.assertEqual([info.id for info in found], ["alpha", "beta"])

    def test_scan_reports_broken_info(self):
        """有 mod_info.json 但写错：报 ModInfoError（不静默跳过）。"""
        folder = self._root / "broken"
        folder.mkdir()
        write(folder / "mod_info.json", "{不是 JSON")
        with self.assertRaises(ModInfoError):
            self._loader.scan()

    def test_load_info_requires_fields(self):
        """浅加载：缺 id / name / version 都报错。"""
        folder = self._root / "bad"
        folder.mkdir()
        write_json(folder / "mod_info.json", {"id": "bad"})
        with self.assertRaises(ModInfoError):
            self._loader.load_info(str(folder))

    def test_unknown_key_is_rejected(self):
        """R5-2：mod_info.json 里不认识的键要报错（拼错不再静默失效）。"""
        folder = self._root / "typo_mod"
        folder.mkdir()
        write_json(folder / "mod_info.json",
                   {"id": "x", "name": "x", "version": "0.0.0", "uis": "demo:ui:x"})
        with self.assertRaises(ModInfoError) as ctx:
            self._loader.load_info(str(folder))
        self.assertIn("uis", str(ctx.exception))

    def test_menu_parses_and_requires_declared_input(self):
        """menu 条目格式正确；input 没有在内容里声明时加载报错。"""
        folder = self._make_mod(info={"menu": [{"label": "规则", "input": "open_rules"}]})
        info = self._loader.load_info(str(folder))
        self.assertEqual(info.menu[0].label, "规则")
        self.assertEqual(info.menu[0].input, "open_rules")
        with self.assertRaises(ModInfoError) as ctx:
            self._loader.load(info)
        self.assertIn("open_rules", str(ctx.exception))

    def test_menu_entry_rejects_unknown_keys(self):
        """menu 条目里多写键要报错（和 mod_info 顶层一样严格）。"""
        folder = self._make_mod(
            info={"menu": [{"label": "规则", "input": "open_rules", "oops": 1}]})
        with self.assertRaises(ModInfoError) as ctx:
            self._loader.load_info(str(folder))
        self.assertIn("oops", str(ctx.exception))

    def test_menu_with_declared_input_loads(self):
        """menu 指向一个真的声明过的 input kind：加载通过。"""
        folder = self._make_mod(
            info={"menu": [{"label": "规则", "input": "open_rules"}]},
            entries=[
                {"id": "demo:command:open_rules", "type": "command",
                 "data": {"branches": [{"condition": True, "actions": []}]}},
                {"id": "demo:input:open_rules", "type": "input",
                 "data": {"kind": "open_rules", "command": "demo:command:open_rules"}},
            ],
        )
        loaded = self._loader.load(self._loader.load_info(str(folder)))
        self.assertEqual(loaded.info.menu[0].label, "规则")

    def test_init_field_must_be_script_and_function(self):
        """init 必须写成 脚本路径:函数名。"""
        folder = self._root / "bad_init"
        folder.mkdir()
        write_json(folder / "mod_info.json",
                   {"id": "bad_init", "name": "x", "version": "0.0.0", "init": "scripts/init.py"})
        with self.assertRaises(ModInfoError):
            self._loader.load_info(str(folder))

    def test_full_load_builds_state_and_functions(self):
        """全加载：init 钩子搭出初始 State；Mod 自己的 functions 进函数表。"""
        scripts = {
            "init.py": 'def build_state(mod):\n    """给一棵最小 State。"""\n    return {"turn": 1}\n',
            "helper.py": 'def twice(value):\n    """乘二。"""\n    return value * 2\n',
        }
        folder = self._make_mod(
            "demo",
            info={"init": "scripts/init.py:build_state",
                  "functions": {"twice": "scripts/helper.py:twice"}},
            scripts=scripts,
        )
        loaded = self._loader.load(self._loader.load_info(str(folder)))
        self.assertEqual(loaded.initial_state, {"turn": 1})
        self.assertEqual(loaded.functions["twice"](3), 6)

    def test_functions_cannot_use_rng_namespace(self):
        """functions 不许占用引擎的随机函数名字（rng.）。"""
        folder = self._root / "bad_functions"
        folder.mkdir()
        write_json(
            folder / "mod_info.json",
            {"id": "x", "name": "x", "version": "1", "functions": {"rng.int": "scripts/a.py:f"}},
        )
        with self.assertRaises(ModInfoError):
            self._loader.load_info(str(folder))

    def test_missing_script_is_reported(self):
        """service 指向的脚本不存在：加载期就报出来。"""
        entries = [
            {"id": "demo:service:missing", "type": "service",
             "data": {"script": "scripts/nope.py", "callable": "run"}},
        ]
        folder = self._make_mod("demo", entries=entries)
        with self.assertRaises(Exception):
            self._loader.load(self._loader.load_info(str(folder)))

    def test_data_file_must_be_array(self):
        """条目文件必须是数组（不是对象）。"""
        folder = self._root / "bad_data"
        folder.mkdir()
        write_json(folder / "mod_info.json", {"id": "bad_data", "name": "x", "version": "1"})
        write_json(folder / "data" / "10.json", {"id": "demo:event:e", "type": "event", "data": {}})
        with self.assertRaises(ModInfoError):
            self._loader.load(self._loader.load_info(str(folder)))

    def test_init_must_return_dict(self):
        """初始化钩子返回非 dict：报错（State 根必须是 dict）。"""
        scripts = {"init.py": "def build_state(mod):\n    return [1, 2]\n"}
        folder = self._make_mod("demo", info={"init": "scripts/init.py:build_state"}, scripts=scripts)
        with self.assertRaises(ModInfoError):
            self._loader.load(self._loader.load_info(str(folder)))

    def test_broken_expression_reported_at_load(self):
        """规则里的表达式写错：加载期就报出来（不会等到运行时）。"""
        entries = [
            {"id": "demo:rule:r", "type": "rule", "data": {"condition": ["adn", True, True]}},
        ]
        folder = self._make_mod("demo", entries=entries)
        with self.assertRaises(LogicError):
            self._loader.load(self._loader.load_info(str(folder)))

    def test_script_call_to_missing_function_is_caught_at_load(self):
        """E-1：脚本里写死的函数名不存在 → 加载期就报（两种写法都查）。"""
        entries = [
            {"id": "demo:service:move", "type": "service", "data":
                {"script": "scripts/move.py", "callable": "move"}},
            {"id": "demo:ui:board", "type": "ui", "data":
                {"script": "scripts/view.py", "callable": "build_view"}},
        ]
        cases = {
            "服务里写错 api.call 的名字": {"move.py":
                'def move(api, args):\n    """移动。"""\n    api.call("pack_hex_grid.nope.missing")\n'},
            "视图里写错 functions 的名字": {"view.py":
                'def build_view(page_context):\n    """排一下版。"""\n'
                '    return page_context.functions["pack_path_hex.nope.missing"]\n'},
        }
        for name, scripts in cases.items():
            with self.subTest(case=name):
                folder = self._make_mod("demo", entries=entries, scripts=scripts)
                with self.assertRaises(ContentReferenceError):
                    self._loader.load(self._loader.load_info(str(folder)))

    def test_script_reference_check_accepts_valid_names(self):
        """E-1 的反面：Mod 自己的函数名与 rng.* 都算合法。"""
        entries = [
            {"id": "demo:service:roll", "type": "service", "data":
                {"script": "scripts/roll.py", "callable": "roll"}},
        ]
        scripts = {
            "roll.py": (
                'def roll(api, args):\n    """掷一把骰子。"""\n'
                '    api.call("roll_dice")\n'
                '    api.call("rng.int", 1, 6)\n'
            ),
            "dice.py": 'def roll_dice():\n    """返回一个固定值（示例）。"""\n    return 3\n',
        }
        folder = self._make_mod(
            "demo", info={"functions": {"roll_dice": "scripts/dice.py:roll_dice"}},
            entries=entries, scripts=scripts,
        )
        self._loader.load(self._loader.load_info(str(folder)))

    def test_reference_check_uses_ast_not_text(self):
        """甲-8 / N-3：注释与 docstring 里提到名字不报错；跨行写法照样查得到。"""
        entries = [{"id": "demo:service:s", "type": "service",
                    "data": {"script": "scripts/s.py", "callable": "run"}}]
        ok = {"s.py": 'def run(api, args):\n    """用法：api.call("pack_x.cap.v") 这样写。"""\n'
                      '    return None\n'}
        folder = self._make_mod("ok_mod", entries=entries, scripts=ok)
        self._loader.load(self._loader.load_info(str(folder)))

        bad = {"s.py": 'def run(api, args):\n    """跨行写的调用。"""\n'
                       '    api.call(\n        "pack_x.cap.v",\n    )\n'}
        folder = self._make_mod("bad_mod", entries=[
            {"id": "bad_mod:service:s", "type": "service",
             "data": {"script": "scripts/s.py", "callable": "run"}}], scripts=bad)
        with self.assertRaises(ContentReferenceError):
            self._loader.load(self._loader.load_info(str(folder)))

    def test_data_call_to_missing_function_is_caught_at_load(self):
        """甲-8：数据里的 ["call", "名字", …] 也要核对。"""
        entries = [{"id": "demo:rule:r", "type": "rule",
                    "data": {"condition": ["call", "pack_x.cap.v", 1]}}]
        folder = self._make_mod("demo", entries=entries)
        with self.assertRaises(ContentReferenceError):
            self._loader.load(self._loader.load_info(str(folder)))

    def test_script_importing_engine_internals_is_rejected(self):
        """契约 §4.3：脚本不许 import 引擎内部包；core.ui 的公开数据类是唯一例外。"""
        bad = {"s.py": 'import core.context\n\n\ndef run(api, args):\n    """空的。"""\n'
                       '    return None\n'}
        folder = self._make_mod("bad_mod", entries=[
            {"id": "bad_mod:service:s", "type": "service",
             "data": {"script": "scripts/s.py", "callable": "run"}}], scripts=bad)
        with self.assertRaises(ContentReferenceError):
            self._loader.load(self._loader.load_info(str(folder)))

        good = {"s.py": 'import math\nfrom core.ui import Layer\n\n\n'
                        'def run(api, args):\n    """用标准库与 core.ui。"""\n    return None\n'}
        folder = self._make_mod("ok_mod", entries=[
            {"id": "ok_mod:service:s", "type": "service",
             "data": {"script": "scripts/s.py", "callable": "run"}}], scripts=good)
        self._loader.load(self._loader.load_info(str(folder)))

    def test_binding_params_with_unknown_key_is_rejected(self):
        """甲-3：绑定参数是给这一个实现写的，键名写错在加载期就报错。"""
        modules_root = pathlib.Path(__file__).resolve().parents[3] / "modules"
        loader = ModLoader(LocalFileSystem(), PythonScriptLoader(), str(self._root),
                           module_roots=[str(modules_root)])
        folder = self._make_mod(
            "demo",
            info={"uses": ["pack_combat_basic"],
                  "bindings": {"dice": {"use": "pack_combat_basic.damage.dice",
                                        "params": {"不存在的键": 1}}}},
        )
        with self.assertRaises(PackInfoError):
            loader.load(loader.load_info(str(folder)))

    def test_template_values_uses_mod_functions_by_default(self):
        """N-2：init 不显式传函数表时，模板属性里的 ["call", 模块函数] 也要算得出来。"""
        modules_root = pathlib.Path(__file__).resolve().parents[3] / "modules"
        entries = [{
            "id": "demo:entity:unit",
            "type": "entity",
            "data": {"attributes": {"home": ["call", "pack_hex_grid.node_key.q_r", 0, 0]}},
        }]
        scripts = {"init.py": 'def build_state(mod):\n'
                              '    """只调 template_values，不显式传函数表。"""\n'
                              '    return {"unit": mod.template_values("demo:entity:unit")}\n'}
        folder = self._make_mod(
            "demo",
            info={"init": "scripts/init.py:build_state", "uses": ["pack_hex_grid>=0.0.0"]},
            entries=entries,
            scripts=scripts,
        )
        loader = ModLoader(LocalFileSystem(), PythonScriptLoader(), str(self._root),
                           module_roots=[str(modules_root)])
        loaded = loader.load(loader.load_info(str(folder)))
        self.assertEqual(loaded.initial_state["unit"]["home"], "n0_0")


class TestReferenceCheckEdges(unittest.TestCase):
    """R4-1 / R4-2 / R4-3：核对器的三条边界。"""

    def setUp(self):
        """每个用例一个干净的 Mod 根目录。"""
        self._root = SCRATCH_ROOT / f"case_{self._testMethodName}" / "mods"
        shutil.rmtree(self._root, ignore_errors=True)
        self._root.mkdir(parents=True, exist_ok=True)
        self._loader = ModLoader(LocalFileSystem(), PythonScriptLoader(), str(self._root))

    def tearDown(self):
        """清掉临时目录。"""
        shutil.rmtree(self._root.parent, ignore_errors=True)

    def _load_script(self, mod, source):
        """造一个只有一个服务脚本的 Mod 并加载它。"""
        entries = [{"id": f"{mod}:service:s", "type": "service",
                    "data": {"script": "scripts/s.py", "callable": "run"}}]
        folder = TestLoader._make_mod(self, mod, entries=entries, scripts={"s.py": source})
        return self._loader.load(self._loader.load_info(str(folder)))

    def test_local_dict_named_functions_is_not_a_function_table(self):
        """R4-1：本地字典叫 functions 不再误报；真函数表下标照旧核对。"""
        self._load_script("ok_local", 'def run(api, args):\n    """本地字典。"""\n'
                                      '    functions = {"my_own_key": 1}\n'
                                      '    return functions["my_own_key"]\n')
        with self.assertRaises(ContentReferenceError):
            self._load_script("bad_table", 'def run(api, args):\n    """真函数表。"""\n'
                                            '    return page_context.functions["nope.cap.v"]\n')

    def test_relative_import_is_rejected_with_a_clear_reason(self):
        """R4-2：相对 import 在加载期被拒，报错说清"脚本之间不能互相 import"。"""
        with self.assertRaises(ContentReferenceError) as ctx:
            self._load_script("rel_mod", 'from .helper import thing\n\n\n'
                                         'def run(api, args):\n    """空的。"""\n    return None\n')
        self.assertIn("不能互相 import", str(ctx.exception))

    def test_third_party_import_is_rejected(self):
        """R4-3：只许标准库与 core.ui；第三方库在加载期拦下。"""
        with self.assertRaises(ContentReferenceError):
            self._load_script("np_mod", 'import numpy\n\n\n'
                                        'def run(api, args):\n    """空的。"""\n    return None\n')
        self._load_script("std_mod", 'import math\n\n\n'
                                     'def run(api, args):\n    """空的。"""\n    return None\n')


class TestEngineServices(unittest.TestCase):
    """R4-11：`engine:` 前缀的服务按白名单校验。"""

    def setUp(self):
        """每个用例一个干净的 Mod 根目录。"""
        self._root = SCRATCH_ROOT / f"case_{self._testMethodName}" / "mods"
        shutil.rmtree(self._root, ignore_errors=True)
        self._root.mkdir(parents=True, exist_ok=True)
        self._loader = ModLoader(LocalFileSystem(), PythonScriptLoader(), str(self._root))

    def tearDown(self):
        """清掉临时目录。"""
        shutil.rmtree(self._root.parent, ignore_errors=True)

    def _load_with_service(self, mod, service_id):
        """造一个"动作里调这个服务"的 Mod 并加载。"""
        entries = [
            {"id": f"{mod}:action:a", "type": "action", "data": {
                "rules": [], "steps": [{"service": service_id}]}},
        ]
        folder = TestLoader._make_mod(self, mod, entries=entries)
        return self._loader.load(self._loader.load_info(str(folder)))

    def test_wrong_engine_service_is_caught_at_load(self):
        """拼错的 engine: 名字在加载期就报（不再等到运行期"取不到实现"）。"""
        with self.assertRaises(ContentReferenceError) as ctx:
            self._load_with_service("bad_engine", "engine:service:refresh_deriveD_TYPO")
        self.assertIn("引擎没有这个服务", str(ctx.exception))

    def test_both_builtin_engine_services_still_pass(self):
        """引擎真正提供的两个（刷新派生值 / 建实例）照旧放行。"""
        self._load_with_service("ok_refresh", "engine:service:refresh_derived")
        self._load_with_service("ok_instance", "engine:service:create_instance")
class TestFunctionNameCollisions(unittest.TestCase):
    """R5-9：三类名字共用一张表，三对撞名都要报错。"""

    def setUp(self):
        """每个用例一个干净的 Mod 根目录 + 真模块根（要拿全限定名来撞）。"""
        self._root = SCRATCH_ROOT / f"case_{self._testMethodName}" / "mods"
        shutil.rmtree(self._root, ignore_errors=True)
        self._root.mkdir(parents=True, exist_ok=True)
        modules_root = pathlib.Path(__file__).resolve().parents[3] / "modules"
        self._loader = ModLoader(LocalFileSystem(), PythonScriptLoader(), str(self._root),
                                 module_roots=[str(modules_root)])

    def tearDown(self):
        """清掉临时目录。"""
        shutil.rmtree(self._root.parent, ignore_errors=True)

    def test_mod_function_cannot_use_a_module_qualified_name(self):
        """Mod 的 functions 键写成模块全限定名 → 报错（以前静默被顶掉）。"""
        folder = TestLoader._make_mod(
            self, "clash_a",
            info={"uses": ["pack_hex_grid"],
                  "functions": {"pack_hex_grid.distance.hex": "scripts/h.py:f"}},
            scripts={"h.py": 'def f(*args):\n    """空的。"""\n    return 0\n'},
        )
        with self.assertRaises(PackInfoError):
            self._loader.load(self._loader.load_info(str(folder)))

    def test_binding_name_cannot_use_a_module_qualified_name(self):
        """绑定名写成模块全限定名 → 报错（以前会顶掉模块实现）。"""
        folder = TestLoader._make_mod(
            self, "clash_b",
            info={"uses": ["pack_hex_grid"],
                  "bindings": {"pack_hex_grid.distance.hex": "pack_hex_grid.distance.hex"}},
        )
        with self.assertRaises(PackInfoError):
            self._loader.load(self._loader.load_info(str(folder)))
