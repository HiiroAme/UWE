"""ports 包：引擎核心依赖的抽象接口（P7 端口与适配器）。

位置：
    引擎核心逻辑层。核心只 import 本包里的抽象，不 import 任何适配器实现：
        UI / 文件 / 时钟 / 媒体 / 网络 → 由适配器实现端口，在组装期注入。

为什么端口放在核心这一侧：
    依赖方向必须是"核心 → 抽象 ← 适配器"。端口如果放在适配器那一侧，
    核心就得反向 import 适配器，违反 P3（单向原则）。

草案依据：
    §4 分层与依赖方向（核心 → 端口接口 允许；核心 → 适配器实现 禁止）；
    P7 端口与适配器原则；D-06 只有核心逻辑层禁止引用平台库。
"""

from .files import FileSystem
from .media import Media, SilentMedia
from .renderer import Renderer
from .window import EVENT_KINDS, Window, WindowEvent
from .scripts import ScriptLoader, ScriptNotAvailableError
from .services import ServiceCallable, ServiceNotAvailableError, ServiceResolver

__all__ = [
    "ServiceCallable",
    "ServiceResolver",
    "ServiceNotAvailableError",
    "FileSystem",
    "Media",
    "SilentMedia",
    "Renderer",
    "Window",
    "WindowEvent",
    "EVENT_KINDS",
    "ScriptLoader",
    "ScriptNotAvailableError",
]
