"""窗口端口（P7）：窗口尺寸与"用户做了什么"。

位置：
    引擎核心逻辑层 → ports 子包。

职责：
    定义"报窗口尺寸、把平台事件取成统一事件"的契约。事件有五种：
        click  鼠标点击（像素坐标）
        key    按键（键的名字，例如 "escape" / "s" / "f5"）
        wheel  滚轮（鼠标位置 + 滚动量）
        drag   按住左键拖动（鼠标位置 + 本帧位移）
        quit   用户关闭窗口

口径（重要）：
    **只有适配器碰平台**：把 pygame / 网页 / Qt 的事件翻成这里的统一事件；
    引擎其余部分（外壳、管线、Mod）一律只认这些内部事件，不直接碰平台。

边界：
    - 端口不认识界面元素：点到了哪个 Layer 由 core.ui 的命中判定算（§17.1）；
    - 端口不做输入翻译：把事件变成统一 Input 是 UiHost 的事（§17.2 互动流）。

草案依据：
    §17.2 三条流（互动 → UI 适配器 → Dispatcher）；§4 核心只依赖抽象；
    P7 端口与适配器原则。
"""

from dataclasses import dataclass
from typing import Protocol

# 事件类别（初版够用的最小集合）。
EVENT_KINDS: tuple[str, ...] = ("click", "key", "wheel", "drag", "quit")


@dataclass(frozen=True)
class WindowEvent:
    """一次窗口事件（纯数据）。

    字段：
        kind: "click" / "key" / "wheel" / "drag" / "quit"；
        point: 鼠标类事件的坐标 (x, y)（click / wheel / drag）；其他情况是 None；
        key: kind="key" 时的键名（小写，例如 "escape"）；其他情况是空字符串；
        delta: 位移量：kind="wheel" 时是 (0, 滚动格数)、kind="drag" 时是 (dx, dy)（像素）；
        timestamp: 事件发生时间（墙钟秒数，由适配器给；只用于显示与统计）。
    """

    kind: str
    point: tuple[int, int] | None = None
    key: str = ""
    delta: tuple[float, float] = (0.0, 0.0)
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        """构造后校验。"""
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"没有这种窗口事件：{self.kind!r}（合法取值：{list(EVENT_KINDS)}）")
        if self.kind == "click":
            if not isinstance(self.point, tuple) or len(self.point) != 2:
                raise TypeError(f"click 事件必须给坐标二元组，实际是 {self.point!r}")
        if self.kind == "key" and (not isinstance(self.key, str) or not self.key):
            raise ValueError("key 事件必须给键名")
        if self.kind in ("wheel", "drag"):
            if not isinstance(self.point, tuple) or len(self.point) != 2:
                raise TypeError(f"{self.kind} 事件必须给鼠标坐标二元组，实际是 {self.point!r}")
            if (not isinstance(self.delta, tuple) or len(self.delta) != 2
                    or any(isinstance(item, bool) or not isinstance(item, (int, float))
                           for item in self.delta)):
                raise TypeError(f"{self.kind} 事件的 delta 必须是数字二元组，实际是 {self.delta!r}")


class Window(Protocol):
    """窗口端口。"""

    def size(self) -> tuple[int, int]:
        """返回窗口尺寸 (宽, 高)，像素。"""

    def poll_events(self) -> tuple[WindowEvent, ...]:
        """取走自上次调用以来发生的事件（一次调用拿一批）。"""

    def close(self) -> None:
        """关闭窗口（退出时调用）。"""
