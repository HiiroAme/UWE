"""渲染端口（P7）：把一帧 View 画到具体平台上。

位置：
    引擎核心逻辑层 → ports 子包。

职责：
    定义"画矩形、画多边形、画文字、画图片、刷新、报窗口尺寸"这几件事的契约。
    真正调用 pygame / canvas 的是适配器（例如 adapters.PygameRenderer）。

    画形原语只给**最小的几种**：矩形、多边形（任意多边形，包括六边形）、文字、图片。
    更复杂的观感（描边、圆角、阴影、贴图拼接）由内容侧用这几个原语组合出来——
    引擎不认识"六边形"这种概念（P1：引擎不含玩法与外观）。

边界：
    - 端口只负责"画"，不认识 Layer / View / 点击（那些是 core.ui 的事）；
    - 端口不读 State、不做布局计算：布局是 Mod 视图脚本的事；
    - 图片与字体资源由适配器自己缓存与加载（路径由 View 给出）。

草案依据：
    §17.1 渲染器是 UI 适配器的一部分，消费读取结果输出到具体平台；
    §4 核心只依赖抽象接口；P7 端口与适配器原则。
"""

from typing import Protocol


class Renderer(Protocol):
    """渲染端口。"""

    def size(self) -> tuple[int, int]:
        """返回画布尺寸 (宽, 高)，像素。"""

    def begin_frame(self, background: tuple[int, int, int, int] | None) -> None:
        """开始一帧：可按背景色清屏；background=None 表示不清屏。"""

    def draw_rect(self, rect: tuple[float, float, float, float], color: tuple[int, int, int, int]) -> None:
        """画一个实心矩形。"""

    def draw_polygon(
        self,
        points: tuple[tuple[float, float], ...],
        color: tuple[int, int, int, int],
        *,
        outline_color: tuple[int, int, int, int] | None = None,
        outline_width: float = 0.0,
    ) -> None:
        """画一个多边形（顶点按顺序给出、自动闭合；至少三个顶点）。

        输入：
            points: 顶点序列，像素坐标；
            color: (r, g, b, a) 填充色。
            outline_color: 描边色；None 表示不描边；
            outline_width: 描边宽度（像素，>0 才描边；描边画在多边形**内侧**）。
        输出：
            无。

        说明：
            "填充 + 描边"是**一个原语**，不是两个：内容侧只要给一次形状与两种颜色，
            不用再拿"大一圈的实心多边形"去假装描边（那种假描边会被邻居的填充盖住）。
        """

    def draw_text(
        self,
        text: str,
        rect: tuple[float, float, float, float],
        *,
        size: float,
        color: tuple[int, int, int, int],
        align: str = "center",
    ) -> None:
        """在矩形里画文字（按 align 对齐；字号是像素高）。"""

    def draw_image(self, path: str, rect: tuple[float, float, float, float]) -> None:
        """把图片缩放到矩形里画出来（路径由适配器解释）。"""

    def end_frame(self) -> None:
        """结束一帧（把内容真正显示出来）。"""
