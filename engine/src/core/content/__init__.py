"""content 包：把注册表里的 Mod 内容编译成运行期结构。

位置：
    引擎核心逻辑层，位于 core.registry 之上、core.pipeline 之下：
        registry（纯数据） → content（编译 + 静态检查） → pipeline（执行）
    pipeline 里的 Orchestrator / RuleChecker / Applier / EventChain 都只读
    CompiledContent，不再各自解析 JSON、不再反复做静态检查。

职责：
    - compiled.py：编译结果的结构（命令分支、Action、规则、订阅、输入映射）；
    - compiler.py：从 RegistryHub 编译一遍，并做完加载期静态检查；
    - errors.py：内容编译自己的异常（与 registry 层的条目格式错误分开）。

草案依据：
    §9 管线第 2、5、6、9 步；§11.1 / §11.2 事件与链式 Trigger；
    §14.2 注册表清单；D-29 加载期静态检查；D-35 分支结构；P2 / P8。
"""

from .compiled import (
    CompiledAction,
    CompiledBranch,
    CompiledCommand,
    CompiledContent,
    CompiledInput,
    CompiledInvocation,
    CompiledPage,
    CompiledFormula,
    CompiledRule,
    CompiledSyscall,
    CompiledPhase,
    CompiledSystem,
    CompiledTemplate,
    CompiledStep,
    CompiledTrigger,
)
from .compiler import compile_content
from .errors import ContentError, ContentFormatError, ContentReferenceError

__all__ = [
    # 编译结果
    "CompiledAction",
    "CompiledBranch",
    "CompiledCommand",
    "CompiledContent",
    "CompiledInput",
    "CompiledInvocation",
    "CompiledPage",
    "CompiledFormula",
    "CompiledSyscall",
    "CompiledTemplate",
    "CompiledPhase",
    "CompiledSystem",
    "CompiledRule",
    "CompiledStep",
    "CompiledTrigger",
    # 编译入口
    "compile_content",
    # 异常
    "ContentError",
    "ContentFormatError",
    "ContentReferenceError",
]
