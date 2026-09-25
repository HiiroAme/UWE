"""adapters 包：端口的具体实现（P7 端口与适配器）。

位置：
    引擎核心之外。核心只 import core.ports 里的抽象，适配器在组装期被注入进核心
    （§4：核心 → 适配器实现 禁止；外部组装 → 适配器 允许）。

当前包含：
    - MappingServiceResolver：把内存里的一张"service 条目 id → 函数"表当作服务实现。
      它不读文件，适合测试与内置内容；读文件的 Mod 脚本加载器属于后续阶段（§15.2）。
    - LocalFileSystem：本机文件系统适配器（读写存档等文本文件）。
    - PythonScriptLoader：按路径加载 Mod 的 Python 脚本。
    - PygameMedia（在 pygame_media.py 里，**不从这里导出**）：pygame.mixer 的音频实现。
    - PygameAdapter（在 pygame_adapter.py 里，**不从这里导出**）：pygame 的窗口、
      事件与渲染。它需要 pygame，所以按需直接 import：
          from adapters.pygame_adapter import PygameAdapter
      这样"只想用文件适配器"的地方不必装 pygame（依赖各自独立，P7）。
"""

from .local_file_system import LocalFileSystem
from .log_sinks import FileLogSink, StreamLogSink, TeeSink, format_record
from .mapping_services import MappingServiceResolver
from .python_scripts import PythonScriptLoader

__all__ = [
    "FileLogSink",
    "LocalFileSystem",
    "MappingServiceResolver",
    "PythonScriptLoader",
    "StreamLogSink",
    "TeeSink",
    "format_record",
]
