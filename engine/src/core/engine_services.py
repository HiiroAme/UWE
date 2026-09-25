"""引擎内置 `engine:` 服务 id 的公共常量。

位置：
    引擎核心逻辑层的中立工具位——本文件只放常量，不 import core 的任何其他模块。

职责：
    给"引擎自己提供哪些服务"这件事一个不落在 content / derived / templates
    任何一方身上的定义处：
        - `core/content/compiler.py` 加载期校验 `engine:` 服务名的白名单（R4-11）；
        - `core/runtime.py` 装配这些服务的实现；
        - `core/derived.py` / `core/templates.py` 只负责实现，不再拥有 id。

为什么单独成文件：
    编译器属于 content，而派生值 / 模板又依赖 content。若 id 常量继续留在
    derived / templates 里，compiler 只能"函数内 import"它们来避开同级成环；
    常量本身不依赖任何东西，提到这里之后依赖方向全部向下，环自然消失。

草案依据：
    P3 单向依赖；《评估报告 5》"原则审查"#1；
    架构守卫 `tests/unit/architecture/test_layering.py`（core 内部不成环、
    不用函数内 import 绕环）。
"""

# 派生值刷新服务 id（Mod 的触发链可以直接引用它）。
REFRESH_DERIVED_SERVICE = "engine:service:refresh_derived"

# 模板实例化服务 id（Mod 的动作步骤里直接引用它）。
CREATE_INSTANCE_SERVICE = "engine:service:create_instance"
