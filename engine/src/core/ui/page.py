"""页面与视图脚本的上下文（§17.1）。

位置：
    引擎核心逻辑层 → ui 子包。

职责：
    定义"Mod 的视图脚本怎么被调用"：

        build_view(page_context) -> View

    其中 PageContext 是**只读输入**：当前 State、窗口尺寸、UI 上下文（不存档的界面状态）。

为什么把 State 直接给界面：
    §17.2 的只读流就是"UI 直接读真实 State"——界面不需要经过命令或结算就能显示最新事实
    （引擎的写入通道仍然只有提交，界面**不能**通过这个上下文改数据）。

边界：
    - 视图脚本是纯函数：给同样的上下文，应当给出同样的画面（不要读时钟、不要取随机）；
    - 视图脚本只能读 State，改动必须走输入 → 命令 → 服务（§16）。

草案依据：
    §17.1 Page / Layer；§17.2 只读流（互动 → 直接读 State → 渲染器显示）；
    §12 何时结算由 Mod 决定；D-08 游戏内 UI 全部由 Mod 提供。
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from ..context import Context
from .view import View


class PageCallable(Protocol):
    """Mod 视图脚本的签名：build_view(page_context) -> View。"""

    def __call__(self, page_context: "PageContext") -> View:
        """按当前局面生成这一帧的画面。"""


@dataclass(frozen=True)
class PageContext:
    """Mod 视图脚本拿到的只读输入。

    字段：
        state: 真实 State（只读：界面直接读事实，§17.2）；
        size: 窗口尺寸 (宽, 高)，像素；
        context: 不存档的界面状态（选中了什么、悬停在哪，§6.1 的"页面 UI 状态"）；
        page_id: 当前页面的条目 id（一个 Mod 可以有多个页面）。
        functions: 加载期组装好的函数表（模块实现 + 绑定 + Mod 自己的函数）。
            视图脚本靠它调用模块提供的界面件（例如"给一组节点画成网格"），
            这样"长什么样"写在模块/Mod 手里，引擎只提供机制与函数表入口。
    """

    state: dict
    size: tuple[int, int]
    context: Context
    page_id: str = ""
    functions: Mapping[str, Callable[..., Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """构造后校验。"""
        if not isinstance(self.state, dict):
            raise TypeError(f"PageContext.state 必须是 dict 根，实际是 {type(self.state).__name__}")
        if not isinstance(self.size, tuple) or len(self.size) != 2:
            raise TypeError(f"PageContext.size 必须是 (宽, 高) 二元组，实际是 {self.size!r}")
        for item in self.size:
            if isinstance(item, bool) or not isinstance(item, int):
                raise TypeError(f"PageContext.size 的每一项都必须是 int，实际是 {item!r}")
        if not isinstance(self.context, Context):
            raise TypeError(f"PageContext.context 必须是 Context，实际是 {type(self.context).__name__}")
