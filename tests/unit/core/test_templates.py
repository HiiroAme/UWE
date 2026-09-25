"""core.templates 的单元测试（标准库 unittest）。

位置：tests/unit/core/
运行：在仓库根执行 `python run_tests.py`。
覆盖（N-2）：
  - `engine:service:create_instance` 建实例时，模板属性里的 `["call", …]` 要能算出来
    （函数表由运行期传进来，而不是空表）。
"""

import unittest

from core.content import CompiledContent, CompiledTemplate
from core.context import Context
from core.logger import Logger
from core.pipeline import Command, EngineApi, SequenceCounter
from core.ports import SilentMedia
from core.rng import Rng
from core.temp_state import TempState
from core.templates import make_create_instance_service


def content_with(templates: dict) -> CompiledContent:
    """造一份只有模板的最小 CompiledContent（其它表留空）。"""
    return CompiledContent(
        commands={}, actions={}, rules={}, triggers={}, inputs={}, services=(), events=(),
        pages={}, formulas={}, syscalls={}, templates=templates, phases={}, systems={},
    )


class TestCreateInstance(unittest.TestCase):
    """`create_instance` 服务：模板属性可以调用外部函数。"""

    def test_template_attribute_may_call_functions(self):
        """N-2：属性写 ["call", 函数名, …] 时，服务要带函数表求值。"""
        template = CompiledTemplate(
            template_id="demo:entity:unit", kind="entity",
            attributes={"hp": ["call", "demo.double", 3]}, source="test",
        )
        content = content_with({"demo:entity:unit": template})
        functions = {"demo.double": lambda value: value * 2}

        view = TempState({"units": {}}, "c1")
        command = Command(
            command_id="c1", definition_id="engine:service:create_instance",
            source="engine", payload={}, created_at=0.0,
        )
        api = EngineApi(
            view, SequenceCounter(), command, "demo",
            rng=Rng(1), logger=Logger(sink=None), context=Context(),
            media=SilentMedia(), functions=functions,
        )
        handle = api.for_service("a1", "engine:service:create_instance")
        service = make_create_instance_service(content, functions=functions)
        service(handle, {"template": "demo:entity:unit", "path": "/units/u1"})
        self.assertEqual(view.get("/units/u1/hp"), 6)


if __name__ == "__main__":
    unittest.main()
