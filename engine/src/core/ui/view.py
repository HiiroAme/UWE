"""View / Layer：一帧画面的纯数据描述与命中判定（§17.1）。

位置：
    引擎核心逻辑层 → ui 子包。

职责：
    定义"界面这一帧要显示什么"的纯数据，以及"点在哪儿点到了谁"：

        View   = 若干 Layer + 背景色
        Layer  = 一块区域 + 画什么（矩形 / 多边形 / 文字 / 图片）+ 点到它产生什么

    Mod 的视图脚本按当前 State 生成 View（例如一个格子一个 Layer、一个单位一个 Layer），
    外壳或渲染适配器只负责照单画出来、把点击翻译成统一输入。

口径：
    - 坐标是**像素**（左上角为原点），由 Mod 的视图脚本自己换算；
    - Layer 的绘制顺序按 z 从小到大（z 相同按声明顺序），命中判定取**最上面的那个**；
    - `polygon` 层用**顶点**描述形状（六边形、三角形……都算多边形），它的 rect 由顶点
      算出来；命中判定按点在不在多边形内算，所以相邻格子重叠的外接矩形不会点错；
    - `click` 是"这次点击要产生的统一输入"（kind + data），是否需要立刻结算由
      `settle` 决定——何时结算由 Mod 说了算（§12），引擎不替 Mod 定。

草案依据：
    §17.1 Layer 是"最小 UI 单位，也是产生点击的主要主体；可用 JSON 描述
    （图片、位置、缩放、点击区间、结果）"；§17.2 互动流与只读流；§12 结算时机由 Mod 决定。
"""

from dataclasses import dataclass, field
from typing import Any

# Layer 的画法（最小集合；要加新画法时在这里登记）。
LAYER_KINDS: tuple[str, ...] = ("rect", "polygon", "text", "image")

# 文字对齐方式。
ALIGNMENTS: tuple[str, ...] = ("center", "left", "right")


@dataclass(frozen=True)
class Viewport:
    """世界坐标 ↔ 屏幕坐标的换算（平移 + 缩放）。

    字段：
        offset_x / offset_y: 平移量（屏幕像素）；
        zoom: 缩放比（正数；1 表示不缩放）。

    换算口径（唯一入口，绘制与命中判定都走它）：
        屏幕 = 世界 × zoom + 平移
    也就是说：Layer 里的 rect / points 都是**世界坐标**，视口负责把它们摆到屏幕上。

    说明：
        - 视口是"这一屏怎么看"，**不是**内容：地图画多大、HUD 放哪里仍由 Mod 决定；
        - 视口的**值**（平移到哪、缩放多少）由 Mod 持有（例如放进 Context），
          引擎只提供这套换算与 `Layer.fixed` 这个"不跟视口动"的开关。
    """

    offset_x: float = 0.0
    offset_y: float = 0.0
    zoom: float = 1.0

    def __post_init__(self) -> None:
        """构造后校验。"""
        for name, value in (("offset_x", self.offset_x), ("offset_y", self.offset_y),
                            ("zoom", self.zoom)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"Viewport.{name} 必须是数字，实际是 {type(value).__name__}")
        if self.zoom <= 0:
            raise ValueError(f"Viewport.zoom 必须是正数，实际是 {self.zoom}")

    def to_screen(self, point: tuple[float, float]) -> tuple[float, float]:
        """世界坐标 → 屏幕坐标。"""
        x, y = _require_point(point)
        return x * self.zoom + self.offset_x, y * self.zoom + self.offset_y

    def to_world(self, point: tuple[float, float]) -> tuple[float, float]:
        """屏幕坐标 → 世界坐标（命中判定用的逆变换）。"""
        x, y = _require_point(point)
        return (x - self.offset_x) / self.zoom, (y - self.offset_y) / self.zoom

    def scale_rect(self, rect: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        """把世界坐标的矩形换算到屏幕坐标。"""
        x, y, width, height = rect
        screen_x, screen_y = self.to_screen((x, y))
        return screen_x, screen_y, width * self.zoom, height * self.zoom


@dataclass(frozen=True)
class ClickResult:
    """点到一个 Layer 之后要产生的统一输入。

    字段：
        kind: 输入类别（对应 input 注册表里的 kind）；
        data: 输入数据（dict，内容由 Mod 与它的 input 条目约定）；
        settle: 送出这条输入之后是否立刻触发结算（§12：何时结算由 Mod 决定）；
            回合制一般留 True，实时制会写 False 由自己的时间驱动来结算。
    """

    kind: str
    data: dict = field(default_factory=dict)
    settle: bool = True

    def __post_init__(self) -> None:
        """构造后校验。"""
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError(f"ClickResult.kind 必须是非空字符串，实际是 {self.kind!r}")
        if not isinstance(self.data, dict):
            raise TypeError(f"ClickResult.data 必须是 dict，实际是 {type(self.data).__name__}")
        if not isinstance(self.settle, bool):
            raise TypeError(f"ClickResult.settle 必须是布尔值，实际是 {type(self.settle).__name__}")


@dataclass(frozen=True)
class WheelResult:
    """滚轮落在一个 Layer 上时要产生的统一输入。

    字段：
        kind: 输入类别（对应 input 注册表里的 kind，例如 "scroll_log"）；
        data: 输入数据（dict，内容由 Mod 与它的 input 条目约定）；
            滚轮的 point / delta 由 UiHost 自动补进 data，层不用自己写。
    """

    kind: str
    data: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """构造后校验。"""
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError(f"WheelResult.kind 必须是非空字符串，实际是 {self.kind!r}")
        if not isinstance(self.data, dict):
            raise TypeError(f"WheelResult.data 必须是 dict，实际是 {type(self.data).__name__}")


@dataclass(frozen=True)
class Layer:
    """一个界面元素（最小 UI 单位）。

    字段：
        id: 本帧内的标识（调试与测试用；不要求跨帧稳定）；
        rect: (x, y, 宽, 高)，单位像素，左上角原点；polygon 层不用写（由顶点算出来）；
        kind: 画法，"rect" / "polygon" / "text" / "image"；
        points: kind="polygon" 时的顶点序列（至少三个；按顺序给出、自动闭合）；
        text: kind="text" 时的文字；
        image: kind="image" 时的资源路径（由渲染适配器解释）；
        color: 自己的颜色 (r, g, b, a)；"rect" / "polygon" 的填充色；
        outline_color / outline_width: polygon 层的描边色与宽度（None/0 = 不描边）；
        text_color: kind="text" 时的文字颜色；
        font_size: 字号（像素高）；
        align: 文字对齐，"center" / "left" / "right"；
        click: 点到它要产生的统一输入；None 表示这个层不吃点击（纯显示）；
        wheel: 滚轮落在它上面要产生的统一输入；None 表示这一层不接滚轮；
        visible: False = 这一层不绘制（但仍然参与命中判定，用来做隐形阻挡层 / 热区）；
        blocks_pointer: True = 这一层挡住它下面的点击 / 滚轮 / 拖动（弹窗锁输入用）；
        escape: 按 Esc 时这一层要产生的统一输入；None = 不接管 Esc（弹窗用）；
        z: 绘制顺序（小的先画，大的盖在上面）；命中判定取最上面那个；
        fixed: True 表示这一层**不跟视口动**（HUD、按钮这类"贴在屏幕上"的层）。

    描边这类观感不在这里开字段：要用描边就再叠一个多边形层（外圈描边色 + 内圈填充色），
    引擎只给原语，怎么组合是内容侧的事（P1 / P8）。
    """

    id: str
    rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    kind: str = "rect"
    points: tuple = ()
    text: str = ""
    image: str = ""
    color: tuple[int, int, int, int] = (200, 200, 200, 255)
    outline_color: tuple[int, int, int, int] | None = None
    outline_width: float = 0.0
    text_color: tuple[int, int, int, int] = (20, 20, 20, 255)
    font_size: float = 18.0
    align: str = "center"
    click: ClickResult | None = None
    z: int = 0
    fixed: bool = False
    wheel: WheelResult | None = None
    visible: bool = True
    blocks_pointer: bool = False
    escape: ClickResult | None = None

    def __post_init__(self) -> None:
        """构造后校验字段（写错了要当场报，而不是画出来才发现）。"""
        if not isinstance(self.id, str) or not self.id:
            raise ValueError(f"Layer.id 必须是非空字符串，实际是 {self.id!r}")
        if not isinstance(self.rect, tuple) or len(self.rect) != 4:
            raise TypeError(f"Layer.rect 必须是 (x, y, 宽, 高) 四元组，实际是 {self.rect!r}")
        for item in self.rect:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise TypeError(f"Layer.rect 的每一项都必须是数字，实际是 {item!r}")
        if self.rect[2] < 0 or self.rect[3] < 0:
            raise ValueError(f"Layer 的宽高不能是负数：{self.rect!r}")
        if self.kind not in LAYER_KINDS:
            raise ValueError(f"没有这种画法：{self.kind!r}（合法取值：{list(LAYER_KINDS)}）")
        if self.kind == "polygon":
            if not isinstance(self.points, tuple) or len(self.points) < 3:
                raise TypeError(
                    f"polygon 层必须给至少三个顶点（points 元组），实际是 {self.points!r}"
                )
            for point in self.points:
                _require_point(point)
            if self.rect != (0.0, 0.0, 0.0, 0.0):
                raise ValueError("polygon 层的 rect 不用写：它由顶点算出来")
            object.__setattr__(self, "rect", _bounds(self.points))
        if self.kind == "text" and not isinstance(self.text, str):
            raise TypeError(f"text 层的文字必须是字符串，实际是 {type(self.text).__name__}")
        if self.kind == "image" and (not isinstance(self.image, str) or not self.image):
            raise ValueError("image 层必须给资源路径（image）")
        if self.align not in ALIGNMENTS:
            raise ValueError(f"没有这种对齐方式：{self.align!r}（合法取值：{list(ALIGNMENTS)}）")
        if isinstance(self.z, bool) or not isinstance(self.z, int):
            raise TypeError(f"Layer.z 必须是整数，实际是 {type(self.z).__name__}")
        if self.click is not None and not isinstance(self.click, ClickResult):
            raise TypeError(f"Layer.click 必须是 ClickResult 或 None，实际是 {type(self.click).__name__}")
        if self.wheel is not None and not isinstance(self.wheel, WheelResult):
            raise TypeError(f"Layer.wheel 必须是 WheelResult 或 None，实际是 {type(self.wheel).__name__}")
        if not isinstance(self.visible, bool):
            raise TypeError(f"Layer.visible 必须是布尔值，实际是 {type(self.visible).__name__}")
        if not isinstance(self.blocks_pointer, bool):
            raise TypeError(f"Layer.blocks_pointer 必须是布尔值，实际是 {type(self.blocks_pointer).__name__}")
        if self.escape is not None and not isinstance(self.escape, ClickResult):
            raise TypeError(f"Layer.escape 必须是 ClickResult 或 None，实际是 {type(self.escape).__name__}")
        if not isinstance(self.fixed, bool):
            raise TypeError(f"Layer.fixed 必须是布尔值，实际是 {type(self.fixed).__name__}")
        if self.outline_color is not None:
            if not isinstance(self.outline_color, tuple) or len(self.outline_color) != 4:
                raise TypeError("Layer.outline_color 必须是 (r, g, b, a) 四元组或 None")
        if isinstance(self.outline_width, bool) or not isinstance(self.outline_width, (int, float)):
            raise TypeError(f"Layer.outline_width 必须是数字，实际是 {type(self.outline_width).__name__}")
        if self.outline_width < 0:
            raise ValueError(f"Layer.outline_width 不能是负数：{self.outline_width!r}")

    @property
    def center(self) -> tuple[float, float]:
        """返回矩形中心点（Mod 排版时常用）。"""
        x, y, width, height = self.rect
        return x + width / 2, y + height / 2

    def contains(self, point: tuple[float, float]) -> bool:
        """判断一个点是否落在本层内（矩形按范围算，多边形按点在不在形内算）。

        输入：
            point: (x, y) 像素坐标。
        输出：
            True 表示点在本层内。
        异常：
            TypeError: point 不是二元组或里面不是数字。
        变量：
            x / y / width / height: 本层矩形的四个数（非多边形层）。

        说明：
            多边形不按外接矩形判定：相邻格子的外接矩形是互相重叠的（斜边那一侧），
            按矩形判定会点到隔壁去。边界上的点算不算内不做保证（浮点射线法）。
        """
        px, py = _require_point(point)
        if self.kind == "polygon":
            return _point_in_polygon(px, py, self.points)
        x, y, width, height = self.rect
        return x <= px <= x + width and y <= py <= y + height


@dataclass(frozen=True)
class View:
    """一帧画面。

    字段：
        layers: 本帧的全部 Layer（绘制顺序由各自的 z 决定）；
        background: 背景色 (r, g, b, a)；None 表示不清屏（留给适配器决定）。
        viewport: 这一帧的视口（平移 + 缩放）；None = 不做任何变换（老行为）。
    """

    layers: tuple[Layer, ...] = ()
    background: tuple[int, int, int, int] | None = (24, 26, 32, 255)
    viewport: Viewport | None = None

    def __post_init__(self) -> None:
        """构造后校验。"""
        if not isinstance(self.layers, tuple):
            raise TypeError("View.layers 必须是元组（用 tuple(...) 传进来）")
        for layer in self.layers:
            if not isinstance(layer, Layer):
                raise TypeError(f"View.layers 里出现了 {type(layer).__name__}，只接受 Layer")
        if self.background is not None:
            if not isinstance(self.background, tuple) or len(self.background) != 4:
                raise TypeError("View.background 必须是 (r, g, b, a) 四元组或 None")
        if self.viewport is not None and not isinstance(self.viewport, Viewport):
            raise TypeError("View.viewport 必须是 Viewport 或 None，"
                            f"实际是 {type(self.viewport).__name__}")

    def sorted_layers(self) -> tuple[Layer, ...]:
        """按绘制顺序返回 Layer（z 小的先画；z 相同保持声明顺序）。

        输入：无。
        输出：
            排好序的 Layer 元组。
        异常：
            无。
        变量：
            无。

        说明：
            Python 的 sorted 是稳定排序，所以"z 相同"时声明顺序自然保持。
        """
        return tuple(sorted(self.layers, key=lambda layer: layer.z))

    def pointer_target(self, point: tuple[float, float], *, attr: str = "click") -> Layer | None:
        """取最上面那个"接某种指针输入"或"阻挡指针"的层。

        输入：
            point: (x, y) 像素坐标；
            attr: 指针结果的字段名（"click" / "wheel"；将来加别的指针也走这里）。
        输出：
            命中的 Layer；一个都没有时返回 None。返回的层可能是纯阻挡层
            （blocks_pointer=True 且没有对应的指针结果），由调用方决定怎么处理。
        异常：
            TypeError: point 不合法，或 attr 不是非空字符串。
        变量：
            layer / probe: 从最上面往下逐个检查的层，以及换算到层坐标系的点。
        """
        _require_point(point)
        if not isinstance(attr, str) or not attr:
            raise TypeError(f"attr 必须是非空字符串，实际是 {attr!r}")
        for layer in reversed(self.sorted_layers()):
            if getattr(layer, attr, None) is None and not layer.blocks_pointer:
                continue
            # 有视口时：非 fixed 层的坐标是世界坐标，要先把屏幕上的点换回世界坐标；
            # fixed 层（HUD / 按钮）就贴在屏幕上，直接用屏幕坐标判。
            probe = point
            if self.viewport is not None and not layer.fixed:
                probe = self.viewport.to_world(point)
            if layer.contains(probe):
                return layer
        return None

    def pointer_blocker(self, point: tuple[float, float]) -> Layer | None:
        """取最上面那个阻挡指针的层（用于"弹窗打开时锁住拖动"）。"""
        _require_point(point)
        for layer in reversed(self.sorted_layers()):
            if not layer.blocks_pointer:
                continue
            probe = point
            if self.viewport is not None and not layer.fixed:
                probe = self.viewport.to_world(point)
            if layer.contains(probe):
                return layer
        return None

    def escape_target(self) -> Layer | None:
        """取最上面那个声明了 Esc 动作的层（弹窗接管 Esc 用；不看坐标）。"""
        for layer in reversed(self.sorted_layers()):
            if layer.escape is not None:
                return layer
        return None

    def hit_test(self, point: tuple[float, float]) -> Layer | None:
        """算出点在哪个层上（取最上面那个能吃点击的层）。

        输入：
            point: (x, y) 像素坐标。
        输出：
            命中的 Layer；没命中、命中的是纯阻挡层、或命中的层不吃点击时返回 None。
        异常：
            TypeError: point 不合法。
        变量：
            target: 最上面的指针目标（可能是纯阻挡层）。
        """
        target = self.pointer_target(point, attr="click")
        if target is None or target.click is None:
            return None
        return target


def _require_point(point: Any) -> tuple[float, float]:
    """要求一个点是 (数字, 数字) 二元组。"""
    if not isinstance(point, tuple) or len(point) != 2:
        raise TypeError(f"坐标必须是 (x, y) 二元组，实际是 {point!r}")
    for item in point:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise TypeError(f"坐标必须是数字，实际是 {item!r}")
    return point


def _bounds(points: tuple) -> tuple[float, float, float, float]:
    """算一组顶点的外接矩形。

    输入：
        points: 顶点序列（每个是 (x, y)）。
    输出：
        (x, y, 宽, 高)。
    异常：
        无（顶点合法性由调用方先校验）。
    变量：
        xs / ys: 全部顶点的横坐标与纵坐标。
    """
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _point_in_polygon(px: float, py: float, points: tuple) -> bool:
    """射线法判断点是否在多边形内。

    输入：
        px / py: 点的坐标；
        points: 多边形顶点（至少三个，按顺序、自动闭合）。
    输出：
        True 表示点在多边形内。
    异常：
        无。
    变量：
        index / (x1, y1) / (x2, y2) / crossing: 逐条边判定的中间结果。

    说明：
        从点往右画一条水平射线，数它穿过几条边：奇数条就在里面。
        边界上的点不做保证（命中判定不需要那么严格）。
    """
    inside = False
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        if (y1 > py) != (y2 > py):  # 这条边跨过点的水平线
            crossing = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < crossing:
                inside = not inside
    return inside
