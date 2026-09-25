"""ui 包：界面机制（§17）。

位置：
    引擎核心逻辑层 → ui 子包。它只定义"要画什么"的纯数据结构与命中判定，
    **不碰任何平台库**：真正画到屏幕上的是适配器（adapters.PygameRenderer）。

职责：
    - View / Layer：一帧画面的纯数据描述（矩形、文字、图片、点击结果、层级）；
    - 命中判定：给一个点，算出点到了哪个 Layer（点击的主要主体是 Layer）；
    - PageContext：Mod 的视图脚本用来生成本帧画面的只读输入（State、窗口尺寸、UI 上下文）。

为什么这样分：
    §17.1 规定了 Page / Layer / 适配器三层：Mod 与外壳都只产出"页与层"的描述，
    适配器把它画出来。于是：
        - 引擎里没有任何一处知道"六边形""单位""血条"长什么样（P1）；
        - 换渲染后端（pygame → 网页 canvas）只需要换适配器（P7）。

草案依据：
    §17.1 两个来源一套机制（Page / Layer / 适配器 / 渲染器）；
    §17.2 三条流（互动流、只读流、UI 事件流）；D-08 游戏内 UI 全部由 Mod 提供。
"""

from .page import PageCallable, PageContext
from .view import ClickResult, Layer, View, Viewport, WheelResult

__all__ = [
    "ClickResult",
    "WheelResult",
    "Layer",
    "View",
    "Viewport",
    "PageContext",
    "PageCallable",
]
