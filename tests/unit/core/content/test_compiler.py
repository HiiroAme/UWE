"""compiler 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/content/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 一份完整内容的编译结果（命令分支、Action 步骤、规则、订阅排序、输入映射）；
  - 加载期静态检查：缺字段、分支为空、参数块不是 dict、表达式写错、引用不存在；
  - 禁用条目被排除，且"引用一个被禁用的条目"直接报错；
  - 事件订阅的排序依据（order 优先、同 order 按注册顺序）。
"""

import unittest

from core.content import (
    CompiledContent,
    ContentFormatError,
    ContentReferenceError,
    compile_content,
)
from core.logic import LogicError
from core.registry import RegistryEntry, RegistryHub


def entry(entry_id: str, type_name: str, data: dict, **overrides) -> RegistryEntry:
    """造一条条目（测试里少写点样板）。

    输入：
        entry_id: 完整 id；
        type_name: 注册表类型；
        data: 条目负载；
        overrides: 要覆盖的字段。
    输出：
        RegistryEntry。
    异常：
        无。
    变量：
        fields: 默认字段字典。
    """
    fields = {"id": entry_id, "type": type_name, "data": data}
    fields.update(overrides)
    return RegistryEntry(**fields)


def build_hub(*entries: RegistryEntry) -> RegistryHub:
    """把一串条目登记进一个完整的注册表中心（默认 §14.2 全部类型）。

    输入：
        entries: 条目。
    输出：
        登记完成的 RegistryHub。
    异常：
        无（登记失败会在测试里显式暴露）。
    变量：
        hub: 注册表中心。
    """
    hub = RegistryHub()
    hub.register_all(list(entries))
    return hub


def minimal_entries() -> list[RegistryEntry]:
    """一套能编译通过的最小内容：事件、服务、规则、动作、命令、订阅、输入。

    输入：无。
    输出：
        条目列表。
    异常：
        无。
    变量：
        无。
    """
    return [
        entry("my_mod:event:unit_moved", "event", {}),
        entry("my_mod:service:apply_move", "service", {"script": "scripts/move.py", "callable": "apply"}),
        entry(
            "my_mod:rule:can_move",
            "rule",
            {"condition": [">", ["get", "/units/u1/move"], 0], "message": "没有行动力"},
        ),
        entry(
            "my_mod:action:move",
            "action",
            {
                "rules": ["my_mod:rule:can_move"],
                "steps": [
                    {"service": "my_mod:service:apply_move", "args": {"to": ["arg", "to"]}},
                ],
            },
        ),
        entry(
            "my_mod:command:move",
            "command",
            {
                "branches": [
                    {"condition": ["exists", "/units/u1"], "actions": [{"action": "my_mod:action:move",
                                                                        "args": {"to": ["arg", "target"]}}]},
                    {"condition": True, "actions": []},
                ]
            },
        ),
        entry(
            "my_mod:trigger:on_move",
            "trigger",
            {"event": "my_mod:event:unit_moved", "order": 5, "actions": [{"action": "my_mod:action:move"}]},
        ),
        entry(
            "my_mod:input:click_node",
            "input",
            {"kind": "click_node", "command": "my_mod:command:move", "payload": {"target": ["arg", "node"]}},
        ),
    ]


class TestCompileHappyPath(unittest.TestCase):
    """一份完整内容的编译结果。"""

    def test_compile_result(self):
        """各类型条目都编译出来，结构、引用与查询方法都对。"""
        content = compile_content(build_hub(*minimal_entries()))
        self.assertIsInstance(content, CompiledContent)

        command = content.command("my_mod:command:move")
        self.assertIsNotNone(command)
        self.assertEqual(len(command.branches), 2)
        self.assertEqual(command.branches[0].actions[0].action_id, "my_mod:action:move")

        action = content.action("my_mod:action:move")
        self.assertEqual([rule.rule_id for rule in action.rules], ["my_mod:rule:can_move"])
        self.assertEqual(action.steps[0].service_id, "my_mod:service:apply_move")

        rule = content.rule("my_mod:rule:can_move")
        self.assertEqual(rule.message, "没有行动力")

        self.assertTrue(content.has_service("my_mod:service:apply_move"))
        self.assertTrue(content.has_event("my_mod:event:unit_moved"))
        self.assertEqual(len(content.subscriptions("my_mod:event:unit_moved")), 1)

        candidates = content.input_candidates("click_node")
        self.assertEqual([item.command_id for item in candidates], ["my_mod:command:move"])

    def test_rule_message_defaults_to_id(self):
        """没写 message 时，拒绝原因用规则 id（日志里不会出现空原因）。"""
        hub = build_hub(entry("my_mod:rule:r", "rule", {"condition": True}))
        content = compile_content(hub)
        self.assertEqual(content.rule("my_mod:rule:r").message, "my_mod:rule:r")

    def test_missing_command_returns_none(self):
        """取不存在的命令定义给 None（运行期按"丢弃并记原因"处理）。"""
        content = compile_content(build_hub(*minimal_entries()))
        self.assertIsNone(content.command("my_mod:command:nope"))
        self.assertIsNone(content.action("my_mod:action:nope"))
        self.assertEqual(content.subscriptions("my_mod:event:nope"), ())


class TestTriggerOrder(unittest.TestCase):
    """事件订阅的排序。"""

    def test_order_then_registration(self):
        """先按 order 升序，order 相同时按注册顺序。"""
        hub = build_hub(
            entry("my_mod:event:e", "event", {}),
            entry("my_mod:action:a", "action", {"steps": []}),
            entry("my_mod:trigger:late", "trigger", {"event": "my_mod:event:e", "order": 9,
                                                     "actions": [{"action": "my_mod:action:a"}]}),
            entry("my_mod:trigger:first", "trigger", {"event": "my_mod:event:e", "order": 1,
                                                      "actions": [{"action": "my_mod:action:a"}]}),
            entry("my_mod:trigger:second", "trigger", {"event": "my_mod:event:e", "order": 1,
                                                       "actions": [{"action": "my_mod:action:a"}]}),
        )
        content = compile_content(hub)
        self.assertEqual(
            [item.trigger_id for item in content.subscriptions("my_mod:event:e")],
            ["my_mod:trigger:first", "my_mod:trigger:second", "my_mod:trigger:late"],
        )

    def test_order_must_be_int(self):
        """order 不是整数时报错（bool 也不算整数）。"""
        hub = build_hub(
            entry("my_mod:event:e", "event", {}),
            entry("my_mod:trigger:t", "trigger", {"event": "my_mod:event:e", "order": "1", "actions": []}),
        )
        with self.assertRaises(ContentFormatError):
            compile_content(hub)


class TestStaticChecks(unittest.TestCase):
    """加载期静态检查。"""

    def test_command_requires_branches(self):
        """command 缺 branches、或 branches 为空列表都报错。"""
        with self.assertRaises(ContentFormatError):
            compile_content(build_hub(entry("my_mod:command:c", "command", {})))
        with self.assertRaises(ContentFormatError):
            compile_content(build_hub(entry("my_mod:command:c", "command", {"branches": []})))

    def test_branch_requires_condition(self):
        """分支必须写明 condition（恒真写 true），缺了就报错。"""
        hub = build_hub(entry("my_mod:command:c", "command", {"branches": [{"actions": []}]}))
        with self.assertRaises(ContentFormatError):
            compile_content(hub)

    def test_args_must_be_dict(self):
        """参数块必须是 dict。"""
        hub = build_hub(
            entry("my_mod:service:s", "service", {}),
            entry("my_mod:action:a", "action", {"steps": [{"service": "my_mod:service:s", "args": [1, 2]}]}),
        )
        with self.assertRaises(ContentFormatError):
            compile_content(hub)

    def test_expression_is_checked_at_compile_time(self):
        """表达式里的操作符写错，在编译期就要报出来。"""
        hub = build_hub(entry("my_mod:rule:r", "rule", {"condition": ["adn", True, True]}))
        with self.assertRaises(LogicError):
            compile_content(hub)

    def test_missing_rule_reference(self):
        """action 引用不存在的规则 → 编译期报错。"""
        hub = build_hub(entry("my_mod:action:a", "action", {"rules": ["my_mod:rule:nope"], "steps": []}))
        with self.assertRaises(ContentReferenceError):
            compile_content(hub)

    def test_missing_action_reference(self):
        """command 分支引用不存在的 Action → 编译期报错。"""
        hub = build_hub(
            entry("my_mod:command:c", "command",
                  {"branches": [{"condition": True, "actions": [{"action": "my_mod:action:nope"}]}]})
        )
        with self.assertRaises(ContentReferenceError):
            compile_content(hub)

    def test_trigger_event_must_exist(self):
        """订阅一个没登记过的事件 → 编译期报错。"""
        hub = build_hub(entry("my_mod:trigger:t", "trigger", {"event": "my_mod:event:nope", "actions": []}))
        with self.assertRaises(Exception):
            compile_content(hub)

    def test_input_command_must_exist(self):
        """输入映射指向不存在的命令 → 编译期报错。"""
        hub = build_hub(entry("my_mod:input:i", "input", {"kind": "click", "command": "my_mod:command:nope"}))
        with self.assertRaises(ContentReferenceError):
            compile_content(hub)

    def test_input_requires_kind(self):
        """输入映射必须写明 kind（输入类别）。"""
        hub = build_hub(
            entry("my_mod:command:c", "command",
                  {"branches": [{"condition": True, "actions": []}]}),
            entry("my_mod:input:i", "input", {"command": "my_mod:command:c"}),
        )
        with self.assertRaises(ContentFormatError):
            compile_content(hub)

    def test_disabled_entry_is_skipped_and_cannot_be_referenced(self):
        """禁用条目不进编译结果；引用它会报错（按"等同于不存在"处理）。"""
        hub = build_hub(
            entry("my_mod:rule:off", "rule", {"condition": True}, enabled=False),
            entry("my_mod:action:a", "action", {"rules": ["my_mod:rule:off"], "steps": []}),
        )
        with self.assertRaises(ContentReferenceError):
            compile_content(hub)

        hub2 = build_hub(entry("my_mod:rule:off", "rule", {"condition": True}, enabled=False))
        self.assertEqual(compile_content(hub2).rules, {})


class TestFormulaCompilation(unittest.TestCase):
    """派生值公式（formula）的编译与层号检查。"""

    def test_compiles_target_expression_and_layer(self):
        """正常编译：target / expression / layer 都进了结果，缺省层号是 0。"""
        content = compile_content(
            build_hub(entry("my_mod:formula:power", "formula", {"target": "/units/u1/power",
                                                                "expression": ["+", 1, 2]}))
        )
        formula = content.formulas["my_mod:formula:power"]
        self.assertEqual(formula.target, "/units/u1/power")
        self.assertEqual(formula.layer, 0)
        self.assertIsNotNone(formula.expression)

    def test_layers_may_chain_upwards(self):
        """第 2 层可以读第 1 层的派生值（正向依赖允许）。"""
        content = compile_content(
            build_hub(
                entry("my_mod:formula:base", "formula",
                      {"target": "/units/u1/power", "layer": 1, "expression": ["+", 1, 1]}),
                entry("my_mod:formula:derived", "formula",
                      {"target": "/advantage", "layer": 2, "expression": ["get", "/units/u1/power"]}),
            )
        )
        self.assertEqual(sorted(content.formulas), ["my_mod:formula:base", "my_mod:formula:derived"])

    def test_missing_target_or_expression_rejected(self):
        """缺 target / expression 直接报错。"""
        with self.assertRaises(ContentFormatError):
            compile_content(build_hub(entry("my_mod:formula:f", "formula", {"expression": 1})))
        with self.assertRaises(ContentFormatError):
            compile_content(build_hub(entry("my_mod:formula:f", "formula", {"target": "/a"})))

    def test_bad_target_path_rejected(self):
        """target 不是合法路径（不以 / 开头）时报错。"""
        with self.assertRaises(ContentFormatError):
            compile_content(
                build_hub(entry("my_mod:formula:f", "formula", {"target": "units/u1/power",
                                                                "expression": 1}))
            )

    def test_duplicate_target_rejected(self):
        """两条公式写同一个 target：报错（谁负责这条路径必须唯一）。"""
        with self.assertRaises(ContentFormatError):
            compile_content(
                build_hub(
                    entry("my_mod:formula:a", "formula", {"target": "/x", "expression": 1}),
                    entry("my_mod:formula:b", "formula", {"target": "/x", "expression": 2}),
                )
            )

    def test_same_layer_dependency_rejected(self):
        """同层互相依赖：无法定序，报错（D-39）。"""
        with self.assertRaises(ContentFormatError):
            compile_content(
                build_hub(
                    entry("my_mod:formula:a", "formula",
                          {"target": "/a", "layer": 1, "expression": ["get", "/b"]}),
                    entry("my_mod:formula:b", "formula",
                          {"target": "/b", "layer": 1, "expression": ["get", "/a"]}),
                )
            )

    def test_backwards_dependency_rejected(self):
        """低层读高层的派生值：会读到上一轮的旧值，等同环，报错。"""
        with self.assertRaises(ContentFormatError):
            compile_content(
                build_hub(
                    entry("my_mod:formula:low", "formula",
                          {"target": "/low", "layer": 0, "expression": ["get", "/high"]}),
                    entry("my_mod:formula:high", "formula",
                          {"target": "/high", "layer": 3, "expression": 1}),
                )
            )

    def test_layer_must_be_non_negative_int(self):
        """layer 必须是非负整数。"""
        with self.assertRaises(ContentFormatError):
            compile_content(
                build_hub(entry("my_mod:formula:f", "formula",
                                {"target": "/a", "layer": -1, "expression": 1}))
            )
        with self.assertRaises(ContentFormatError):
            compile_content(
                build_hub(entry("my_mod:formula:f", "formula",
                                {"target": "/a", "layer": "1", "expression": 1}))
            )

    def test_wildcard_target_is_allowed_and_checked(self):
        """通配 target 合法；写错捕获段要报错；两条可能重合的 target 要报错。"""
        content = compile_content(
            build_hub(entry("my_mod:formula:power", "formula",
                            {"target": "/units/{unit}/power", "expression": 1}))
        )
        self.assertEqual(content.formulas["my_mod:formula:power"].target, "/units/{unit}/power")

        with self.assertRaises(ContentFormatError):
            compile_content(
                build_hub(entry("my_mod:formula:bad", "formula",
                                {"target": "/units/{}/power", "expression": 1}))
            )
        with self.assertRaises(ContentFormatError):
            compile_content(
                build_hub(
                    entry("my_mod:formula:a", "formula", {"target": "/units/{u}/power", "expression": 1}),
                    entry("my_mod:formula:b", "formula", {"target": "/units/*/power", "expression": 2}),
                )
            )

    def test_wildcard_reading_a_same_layer_target_is_rejected(self):
        """通配 target 与"读它"的公式在层号上也要能定序。"""
        with self.assertRaises(ContentFormatError):
            compile_content(
                build_hub(
                    entry("my_mod:formula:power", "formula",
                          {"target": "/units/{u}/power", "layer": 2, "expression": 1}),
                    entry("my_mod:formula:score", "formula",
                          {"target": "/score", "layer": 2, "expression": ["get", "/units/u1/power"]}),
                )
            )


class TestTemplatesPhasesAndSystems(unittest.TestCase):
    """entity / node / terrain / phase / system 的编译。"""

    def test_node_template_merges_terrain_preset(self):
        """node 引用 terrain 时，地形属性是底座、节点属性覆盖它。"""
        content = compile_content(
            build_hub(
                entry("my_mod:terrain:plain", "terrain", {"attributes": {"move_cost": 1, "cover": 0}}),
                entry("my_mod:node:plain", "node",
                      {"terrain": "my_mod:terrain:plain", "attributes": {"cover": 2}}),
                entry("my_mod:entity:unit", "entity", {"attributes": {"hp": 10}}),
            )
        )
        node = content.templates["my_mod:node:plain"]
        self.assertEqual(node.kind, "node")
        self.assertEqual(node.attributes["move_cost"], 1)
        self.assertEqual(node.attributes["cover"], 2)     # 节点自己的覆盖了地形预设
        self.assertEqual(content.templates["my_mod:entity:unit"].kind, "entity")

    def test_terrain_reference_must_exist(self):
        """node 引用的 terrain 不存在时报错（引用类错误）。"""
        with self.assertRaises(ContentReferenceError):
            compile_content(
                build_hub(entry("my_mod:node:plain", "node", {"terrain": "my_mod:terrain:nope"}))
            )

    def test_phase_actions_must_exist(self):
        """phase 引用的动作必须存在且启用。"""
        content = compile_content(
            build_hub(
                entry("my_mod:action:a", "action", {"steps": []}),
                entry("my_mod:phase:p", "phase", {"actions": ["my_mod:action:a"]}),
            )
        )
        self.assertEqual(content.phases["my_mod:phase:p"].actions, ("my_mod:action:a",))
        with self.assertRaises(ContentFormatError):
            compile_content(build_hub(entry("my_mod:phase:p", "phase", {"actions": ["my_mod:action:nope"]})))

    def test_system_paths_must_be_concrete(self):
        """system 的初始值路径必须写具体（不许通配），值可以是表达式。"""
        content = compile_content(
            build_hub(entry("my_mod:system:s", "system", {"state": {"/turn": 1, "/weather": "晴"}}))
        )
        self.assertEqual(sorted(content.systems["my_mod:system:s"].values), ["/turn", "/weather"])
        with self.assertRaises(ContentFormatError):
            compile_content(
                build_hub(entry("my_mod:system:s", "system", {"state": {"/units/*/hp": 10}}))
            )
        with self.assertRaises(ContentFormatError):
            compile_content(build_hub(entry("my_mod:system:s", "system", {})))


if __name__ == "__main__":
    unittest.main()
