"""command 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/pipeline/
运行：在仓库根执行 `python run_tests.py`。
覆盖：Command / Action 的字段校验与不可变性（frozen）。
"""

import unittest
from dataclasses import FrozenInstanceError

from core.pipeline import Action, Command


def make_command(**overrides) -> Command:
    """造一个字段合法的 Command，允许用关键字覆盖单个字段做反例。

    输入：
        overrides: 要覆盖的字段（关键字参数）。
    输出：
        构造好的 Command。
    异常：
        无（构造异常由被测用例自己触发）。
    变量：
        fields: 默认字段字典，被 overrides 覆盖后交给 Command。
    """
    fields = {
        "command_id": "c1",
        "definition_id": "my_mod:command:move",
        "source": "ui",
        "payload": {"unit": "u1"},
        "created_at": 1.5,
    }
    fields.update(overrides)
    return Command(**fields)


class TestCommand(unittest.TestCase):
    """Command 的字段校验与不可变性。"""

    def test_valid_command(self):
        """合法字段全部保留。"""
        command = make_command()
        self.assertEqual(command.command_id, "c1")
        self.assertEqual(command.definition_id, "my_mod:command:move")
        self.assertEqual(command.source, "ui")
        self.assertEqual(command.payload, {"unit": "u1"})
        self.assertEqual(command.created_at, 1.5)

    def test_empty_command_id_rejected(self):
        """command_id 不能是空字符串。"""
        with self.assertRaises(ValueError):
            make_command(command_id="")

    def test_definition_id_must_be_string(self):
        """definition_id 必须是字符串。"""
        with self.assertRaises(TypeError):
            make_command(definition_id=123)

    def test_payload_must_be_dict(self):
        """payload 必须是 dict（内容不检查，P1 / D-14）。"""
        with self.assertRaises(TypeError):
            make_command(payload=["u1"])

    def test_created_at_must_be_number(self):
        """created_at 必须是数字；bool 是 int 的子类，也要拒绝。"""
        with self.assertRaises(TypeError):
            make_command(created_at=True)

    def test_command_is_frozen(self):
        """frozen：字段不可重新赋值。"""
        command = make_command()
        with self.assertRaises(FrozenInstanceError):
            command.command_id = "c2"


class TestAction(unittest.TestCase):
    """Action 的字段校验与不可变性。"""

    def test_valid_action(self):
        """合法字段全部保留。"""
        action = Action(action_id="a1", definition_id="my_mod:action:move", payload={})
        self.assertEqual(action.action_id, "a1")
        self.assertEqual(action.definition_id, "my_mod:action:move")
        self.assertEqual(action.payload, {})

    def test_empty_action_id_rejected(self):
        """action_id 不能是空字符串。"""
        with self.assertRaises(ValueError):
            Action(action_id="", definition_id="my_mod:action:move", payload={})

    def test_action_payload_must_be_dict(self):
        """payload 必须是 dict。"""
        with self.assertRaises(TypeError):
            Action(action_id="a1", definition_id="my_mod:action:move", payload="x")

    def test_action_is_frozen(self):
        """frozen：字段不可重新赋值。"""
        action = Action(action_id="a1", definition_id="my_mod:action:move", payload={})
        with self.assertRaises(FrozenInstanceError):
            action.definition_id = "my_mod:action:attack"


if __name__ == "__main__":
    unittest.main()
