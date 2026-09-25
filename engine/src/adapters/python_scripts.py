"""Python 脚本加载适配器（core.ports.ScriptLoader 的一个实现）。

位置：
    引擎适配层（core 之外）。

职责：
    按文件路径 import 一个 Python 文件，并取出里面的一个函数：
        loader.load("mods/demo_hex/scripts/battle.py", "finish_battle") → 函数对象
        loader.load("modules/pack_hex_grid/scripts/grid.py", "distance_hex") → 函数对象

缓存口径：
    同一个脚本路径只 import 一次（按路径缓存模块对象）。这样反复取同一个脚本里的
    不同函数不会重复执行模块代码，也避免同名文件互相覆盖。

安全口径（本阶段有意如此）：
    不限制脚本能做什么（D-26：脚本安全限制属于暂缓项）。脚本与引擎同一个进程、
    同样的权限，这是"Mod 是玩法唯一来源"的必然代价。

草案依据：
    §15.2 Mod 提供脚本；§16 脚本经引擎接口产生变化量；D-26 不设脚本安全限制；
    P7 端口与适配器（核心只认 ScriptLoader 抽象）。
"""

import importlib.util
import sys
from pathlib import Path

from core.ports import ScriptNotAvailableError


class PythonScriptLoader:
    """按文件路径加载 Python 脚本。

    字段：
        _modules: 脚本路径 → 已加载的模块对象（避免重复执行模块代码）；
        _counter: 生成模块名的计数器（模块名只需在本次运行内唯一）。
    """

    def __init__(self) -> None:
        """创建一个脚本加载器。

        输入：无。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self._modules: dict[str, object] = {}
        self._counter: int = 0
        self._module_names: set[str] = set()

    def load(self, script_path: str, callable_name: str):
        """取出脚本文件里的一个可调用对象。

        输入：
            script_path: 脚本文件路径；
            callable_name: 函数名（脚本里的顶层名字）。
        输出：
            可调用对象。
        异常：
            TypeError: 参数类型不对；
            ScriptNotAvailableError: 文件不存在、语法错、import 失败、或没有这个名字。
        变量：
            module: 已加载的模块对象；
            target: 模块里取到的名字。
        """
        if not isinstance(script_path, str) or not script_path:
            raise TypeError(f"script_path 必须是非空字符串，实际是 {script_path!r}")
        if not isinstance(callable_name, str) or not callable_name:
            raise TypeError(f"callable_name 必须是非空字符串，实际是 {callable_name!r}")

        module = self._modules.get(script_path)
        if module is None:
            module = self._import_module(script_path)
            self._modules[script_path] = module

        if not hasattr(module, callable_name):
            raise ScriptNotAvailableError(
                f"脚本里没有名字 {callable_name!r}", script_path=script_path
            )
        target = getattr(module, callable_name)
        if not callable(target):
            raise ScriptNotAvailableError(
                f"脚本里的 {callable_name!r} 不是可调用对象（实际是 {type(target).__name__}）",
                script_path=script_path,
            )
        return target

    def _import_module(self, script_path: str) -> object:
        """按路径导入一个 Python 文件。

        输入：
            script_path: 脚本路径。
        输出：
            模块对象。
        异常：
            ScriptNotAvailableError: 文件不存在 / 不是 .py / 导入失败（含语法错）。
        变量：
            file: 路径对象；
            module_name: 本次运行内唯一的模块名；
            spec: importlib 的模块规格；
            module: 执行后的模块对象。
        """
        file = Path(script_path)
        if not file.is_file():
            raise ScriptNotAvailableError("脚本文件不存在", script_path=script_path)
        if file.suffix != ".py":
            raise ScriptNotAvailableError(
                f"脚本必须是 .py 文件，实际是 {file.suffix!r}", script_path=script_path
            )

        self._counter += 1
        module_name = f"mod_script_{self._counter}"
        spec = importlib.util.spec_from_file_location(module_name, file)
        if spec is None or spec.loader is None:
            raise ScriptNotAvailableError("无法为这个脚本建立导入规格", script_path=script_path)
        module = importlib.util.module_from_spec(spec)
        # 先放进 sys.modules：脚本内部的相对 import / 自引用才不会出问题。
        sys.modules[module_name] = module
        self._module_names.add(module_name)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # 语法错、运行时错都在这里暴露成"脚本不可用"
            sys.modules.pop(module_name, None)
            raise ScriptNotAvailableError(
                f"导入脚本失败：{type(exc).__name__}: {exc}", script_path=script_path
            ) from exc
        return module

    def clear(self) -> None:
        """忘掉所有已加载的脚本（**热重载**用）。

        输入：无。
        输出：
            无。
        异常：
            无。
        变量：
            无。

        说明：
            本加载器按路径缓存模块对象（同一脚本只 import 一次）。热重载时脚本文件
            已经在磁盘上改过了，必须把缓存丢掉，下一次加载才会读到新内容——
            这也是"热重载"能生效的前提（F-01）。
        """
        for module_name in self._module_names:
            sys.modules.pop(module_name, None)   # 热重载不留旧对象（R4-13）
        self._module_names.clear()
        self._modules.clear()
