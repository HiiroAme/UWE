"""界面宿主：把"当前的 View"与"点击"接起来（外壳与游戏内界面共用一套机制）。

位置：
    shell 包（引擎外壳）。它不含平台代码：画由 Renderer 端口做，事件由事件源提供。

职责：
    1. 持有"当前要显示的画面"（一个 View）与提供画面的来源（外壳页面 / Mod 视图脚本）；
    2. 把一次**点击**翻译成统一输入：
           点 (x, y) → View.hit_test → Layer.click → Input(kind, data) → 会话
       需要时按点击结果里的 settle 立刻结算（§12：何时结算由 Mod 决定）；
    3. 把 View 交给渲染端口逐层画出来（z 小的先画）；
    4. 记住外壳侧的界面状态（当前在哪个页面、选中了什么、提示文字）。

为什么"点击 → 输入"放在这里而不是渲染适配器里：
    适配器只该认识像素与窗口；"点到哪个 Layer、产生什么输入"是引擎机制，
    外壳与游戏内界面都要用同一套（§17.1 两个来源一套机制）。

草案依据：
    §17.1 Page / Layer / 适配器；§17.2 三条流（互动流、只读流、UI 事件流）；
    §12 结算时机由 Mod 决定；D-08 游戏内 UI 全部由 Mod 提供、外壳 UI 由引擎提供。
"""

from typing import Any, Callable, Mapping

from core.context import Context
from core.logger import Logger
from core.pipeline import Input
from core.ports import Renderer
from core.ui import Layer, PageCallable, PageContext, View

# 视图错误去重集合的上限：错误文字里若带会变的值，不让集合无限增长（满了整体清一次）。
_VIEW_ERROR_LIMIT = 64


class UiHost:
    """一帧画面的来源 + 点击分发。

    字段：
        _renderer: 渲染端口（适配器）；
        _context: 界面状态（不存档，§6.1）；
        _size: 画布尺寸；
        _provider: 当前画面的来源函数：给 UiHost，返回一个 View；
        _on_input: 产生的统一输入要送到哪里（通常转发给 GameSession）；
        _on_settle: 需要立刻结算时调用（签名 settle(labels)）；
        _logger: 日志对象（可为 None）；
        _last_view: 最近一帧的 View（点击命中判定要用它，保证"看到什么点什么"）；
        _view_errors: 已经记录过的视图错误（同一条只记一次，集合有上限）。
    """

    def __init__(
        self,
        renderer: Renderer,
        *,
        context: Context | None = None,
        logger: Logger | None = None,
    ) -> None:
        """创建界面宿主。

        输入：
            renderer: 渲染端口；
            context: 界面状态（缺省新建一个）；
            logger: 日志对象。
        输出：
            无（构造对象）。
        异常：
            TypeError: renderer 不满足端口契约。
        变量：
            无。
        """
        if not hasattr(renderer, "size") or not hasattr(renderer, "begin_frame"):
            raise TypeError("renderer 必须实现 Renderer 端口（size / begin_frame / draw_* / end_frame）")
        self._renderer: Renderer = renderer
        self._context: Context = context if context is not None else Context()
        self._size: tuple[int, int] = renderer.size()
        self._provider: Callable[["UiHost"], View] | None = None
        self._on_input: Callable[[Input], Any] | None = None
        self._on_settle: Callable[[tuple[str, ...]], Any] | None = None
        self._logger: Logger | None = logger
        self._last_view: View = View()
        self._view_errors: set[str] = set()

    @property
    def context(self) -> Context:
        """返回界面状态（不存档）。"""
        return self._context

    @property
    def size(self) -> tuple[int, int]:
        """返回画布尺寸（**实时**读渲染端口：窗口被拉伸过也照实报）。"""
        current = self._renderer.size()
        self._size = (int(current[0]), int(current[1]))
        return self._size

    @property
    def view(self) -> View:
        """返回最近一帧的画面。"""
        return self._last_view

    def set_provider(self, provider: Callable[["UiHost"], View] | None) -> None:
        """设置当前画面的来源函数。

        输入：
            provider: 给一个 UiHost，返回要显示的一帧 View；None 表示"没有画面"
                （宿主会给一张只有背景的空画面）。
        输出：
            无。
        异常：
            TypeError: provider 既不是可调用对象也不是 None。
        变量：
            无。
        """
        if provider is not None and not callable(provider):
            raise TypeError("provider 必须是可调用对象或 None（给 UiHost 返回 View）")
        self._provider = provider

    def set_input_sink(
        self,
        on_input: Callable[[Input], Any],
        on_settle: Callable[[tuple[str, ...]], Any] | None = None,
    ) -> None:
        """设置"统一输入送哪里"与"何时结算"。

        输入：
            on_input: 收到 Input 时调用（通常转给 GameSession.submit_input）；
            on_settle: 需要立刻结算时调用（签名 settle(labels)）；None 表示不自动结算。
        输出：
            无。
        异常：
            TypeError: on_input 不是可调用对象。
        变量：
            无。
        """
        if not callable(on_input):
            raise TypeError("on_input 必须是可调用对象")
        if on_settle is not None and not callable(on_settle):
            raise TypeError("on_settle 必须是可调用对象或 None")
        self._on_input = on_input
        self._on_settle = on_settle

    def build_view(self) -> View:
        """生成当前这一帧的画面并记下来。

        输入：无。
        输出：
            View；没有设置来源时返回一张只有背景的空画面。
        异常：
            无（来源函数的异常与返回值类型错误都记一条 WARN、沿用上一帧，R5-3 / R5-8）。
        变量:
            view: 来源函数给出的画面。
        """
        if self._provider is None:
            self._last_view = View()
            return self._last_view
        try:
            view = self._provider(self)
            if not isinstance(view, View):     # 返回类型不对也算视图的错（R5-8）
                raise TypeError(f"画面来源必须返回 View，实际是 {type(view).__name__}")
        except Exception as exc:      # 视图脚本的错不该让整个游戏挂掉（R5-3 / R5-8）
            detail = f"{type(exc).__name__}: {exc}"
            if detail not in self._view_errors:
                if len(self._view_errors) >= _VIEW_ERROR_LIMIT:
                    self._view_errors.clear()
                self._view_errors.add(detail)
                if self._logger is not None:
                    self._logger.warn(f"视图脚本抛异常，先沿用上一帧：{detail}")
            return self._last_view
        self._last_view = view
        return view

    def render(self) -> None:
        """把最近一帧画面交给渲染端口画出来。

        输入：无。
        输出：
            无。
        异常：
            由渲染适配器抛出（画不出来是要如实报错的）。
        变量：
            view / layer: 当前画面与逐个绘制的层。
        """
        draw_view(self._renderer, self.build_view())

    def click_at(self, point: tuple[float, float], timestamp: float = 0.0, *, source: str = "ui") -> Input | None:
        """处理一次点击并（按需）结算。

        输入：
            point: 点击位置（像素）；
            timestamp: 交互发生时间（由事件源给的墙钟时间，只用于显示/回放/统计）；
            source: 输入来源（"ui" / "ai" / "timer" / "network"）。
        输出：
            产生了统一输入时返回 Input；没命中可点击的层时返回 None。
        异常：
            TypeError: 参数不合法。
        变量：
            layer / result / input_event: 命中的层、点击结果、组装好的输入。

        说明：
            命中判定用的是**最近一帧**的 View：玩家点到的一定是他看到的那一帧，
            布局变了也不会点错东西。
        """
        layer = self._last_view.hit_test(point)
        if layer is None or layer.click is None:
            return None
        result = layer.click
        input_event = Input(result.kind, dict(result.data), timestamp, source)
        if self._on_input is not None:
            self._on_input(input_event)
        if result.settle and self._on_settle is not None:
            self._on_settle(("ui:click",))
        if self._logger is not None:
            self._logger.debug(
                f"界面点击：层 {layer.id} → 输入 {result.kind}",
                extra={"layer": layer.id, "kind": result.kind, "settle": result.settle},
            )
        return input_event

    def wheel_at(self, point: tuple[float, float], delta: tuple[float, float],
                 timestamp: float = 0.0, *, source: str = "ui") -> Input | None:
        """把一次滚轮转成统一输入（kind="wheel"）。

        输入：
            point: 鼠标位置（像素，屏幕坐标）；
            delta: 滚轮量 (0, 滚动格数)；
            timestamp / source: 同 click_at。
        输出：
            产生的统一输入；被弹窗 / 阻挡层挡住时返回 None。
        异常：
            TypeError: 参数不合法。
        变量：
            target: 鼠标位置命中的"滚轮目标"层（可能是纯阻挡层）。

        说明：
            滚轮先看鼠标位置有没有命中带 WheelResult 的层（例如战报栏）：
            有就发那个层的目标输入；没有就发通用 "wheel"（内容通常拿它做相机缩放）。
            命中纯阻挡层时什么都不发，弹窗打开期间就锁住了滚轮。
        """
        px, py, dx, dy = self._check_pointer(point, delta)
        target = self._last_view.pointer_target((px, py), attr="wheel")
        if target is not None:
            if target.wheel is None:       # 纯阻挡层：锁住滚轮
                return None
            data = dict(target.wheel.data or {})
            data.setdefault("point", [px, py])
            data.setdefault("delta", [dx, dy])
            input_event = Input(target.wheel.kind, data, timestamp, source)
            if self._on_input is not None:
                self._on_input(input_event)
            if self._on_settle is not None:
                self._on_settle(("ui:wheel",))
            if self._logger is not None:
                self._logger.debug(f"界面滚轮：层 {target.id} → 输入 {input_event.kind}")
            return input_event
        return self._send_pointer_event("wheel", (px, py), (dx, dy), timestamp, source)

    def drag_at(self, point: tuple[float, float], delta: tuple[float, float],
                timestamp: float = 0.0, *, source: str = "ui") -> Input | None:
        """把一次"按住左键的移动"转成统一输入（kind="drag"）。

        输入：
            point: 鼠标位置（屏幕坐标）；
            delta: 本帧位移 (dx, dy)；
            timestamp / source: 同 click_at。
        输出：
            产生的统一输入；被弹窗 / 阻挡层挡住时返回 None。
        异常：
            TypeError: 参数不合法。
        变量：
            input_event: 组装好的输入。
        """
        px, py, dx, dy = self._check_pointer(point, delta)
        if self._last_view.pointer_blocker((px, py)) is not None:
            return None
        return self._send_pointer_event("drag", (px, py), (dx, dy), timestamp, source)

    def escape_event(self, timestamp: float = 0.0, *, source: str = "ui") -> Input | None:
        """按 Esc：如果当前帧有层声明了 Esc 动作，就发它的输入；没有返回 None。"""
        layer = self._last_view.escape_target()
        if layer is None or layer.escape is None:
            return None
        result = layer.escape
        input_event = Input(result.kind, dict(result.data), timestamp, source)
        if self._on_input is not None:
            self._on_input(input_event)
        if result.settle and self._on_settle is not None:
            self._on_settle(("ui:escape",))
        if self._logger is not None:
            self._logger.debug(f"界面 Esc：层 {layer.id} → 输入 {input_event.kind}")
        return input_event

    def _check_pointer(self, point: tuple[float, float],
                       delta: tuple[float, float]) -> tuple[float, float, float, float]:
        """校验并展开指针事件的 point / delta（滚轮与拖动共用）。"""
        px, py = point
        dx, dy = delta
        for name, value in (("point", px), ("point", py), ("delta", dx), ("delta", dy)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} 必须是数字，实际是 {value!r}")
        return float(px), float(py), float(dx), float(dy)

    def _send_pointer_event(self, kind: str, point: tuple[float, float],
                            delta: tuple[float, float], timestamp: float, source: str) -> Input:
        """把带坐标与位移的输入（滚轮 / 拖动）送进管线并立刻结算。"""
        px, py, dx, dy = self._check_pointer(point, delta)
        input_event = Input(kind, {"point": [float(px), float(py)],
                                   "delta": [float(dx), float(dy)]}, timestamp, source)
        if self._on_input is not None:
            self._on_input(input_event)
        if self._on_settle is not None:
            self._on_settle((f"ui:{kind}",))
        if self._logger is not None:
            self._logger.debug(f"界面输入：{kind} delta=({dx}, {dy})")
        return input_event


def make_page_provider(
    page: PageCallable,
    *,
    state_getter: Callable[[], dict],
    context: Context,
    size: Callable[[], tuple[int, int]],
    page_id: str = "",
    functions: Mapping[str, Any] | None = None,
) -> Callable[[UiHost], View]:
    """把 Mod 的视图函数包成 UiHost 需要的"画面来源"。

    输入：
        page: Mod 的视图函数（build_view(page_context) -> View）；
        state_getter: 取当前真实 State 的调用（UI 只读流，§17.2）；
        context: 界面状态（不存档）；
        size: **取窗口尺寸的函数**（每次建画面时调它一次，返回 (宽, 高)）。
            只收函数、不收现成的值：窗口会被拉伸 / 最大化，拍了快照就再也拿不到新尺寸
            （这个坑真踩过：进游戏时把 (960, 640) 存进闭包，拉伸后 Mod 一直按旧尺寸排版）。
        page_id: 页面 id。
        functions: 加载期函数表（模块实现 / 绑定 / Mod 函数）；视图脚本用它调用模块界面件。
    输出：
        给 UiHost 用的画面来源函数。
    异常：
        TypeError: page 不是可调用对象。
    变量：
        无。
    """
    if not callable(page):
        raise TypeError("page 必须是可调用对象（build_view(page_context) -> View）")
    if not callable(size):
        raise TypeError("size 必须是「取尺寸的函数」（形如 lambda: window.size()）；"
                        "直接传 (宽, 高) 会在窗口拉伸后失效")

    def provider(host: UiHost) -> View:
        """给宿主返回这一帧的画面（调用 Mod 的视图函数）。"""
        current_size = size()
        page_context = PageContext(
            state=state_getter(), size=current_size, context=context, page_id=page_id,
            functions=functions or {},
        )
        return page(page_context)

    return provider


def draw_view(renderer: Renderer, view: View) -> None:
    """把一帧画面交给渲染端口画出来（"层 → 绘制指令"的唯一翻译处）。

    输入：
        renderer: 渲染端口（适配器实现）；
        view: 要画的这一帧。
    输出：
        无。
    异常：
        由渲染适配器抛出（画不出来要如实报错）。
    变量：
        layer: 按 z 序逐个绘制的层。

    翻译口径（层怎么变成指令）：
        rect     → 画一块底色；
        polygon  → 按顶点画一块色块（六边形、三角形……都走这一条）；
        text     → 先画底色（层的 color，alpha 不为 0 时），再写字（text_color）；
        image    → 贴图。
    "文字层也带底色"是刻意的：格子、卡片、按钮、面板都是"一块底色 + 上面一行字"，
    这是模块与 Mod 画界面时最常用的写法；不把底色画出来的话，它们全都只剩文字。
    形状、配色仍由模块/Mod 在 Layer 里给，引擎只照单翻译。

    视口（视口不为 None 时）：
        非 `fixed` 层的 rect / 顶点 / 字号会按视口换算到屏幕上；`fixed=True` 的层
        （HUD、按钮）原样画。**换算只用 Viewport 上的那两个方法**，别在这儿自己算。
    """
    if not isinstance(view, View):      # 内部助手也把错误说清楚（R5-14）
        raise TypeError(f"draw_view 需要一个 View，实际是 {type(view).__name__}")

    renderer.begin_frame(view.background)
    for layer in view.sorted_layers():
        if not layer.visible:       # 隐形层：只参与命中（弹窗阻挡层 / 热区）
            continue
        rect, points, font_size = _place(layer, view.viewport)
        if layer.kind == "text":
            if layer.color[3] > 0:
                renderer.draw_rect(rect, layer.color)
            renderer.draw_text(
                layer.text, rect, size=font_size, color=layer.text_color, align=layer.align
            )
        elif layer.kind == "rect":
            renderer.draw_rect(rect, layer.color)
        elif layer.kind == "polygon":
            scale = view.viewport.zoom if (view.viewport is not None and not layer.fixed) else 1.0
            renderer.draw_polygon(points, layer.color,
                                  outline_color=layer.outline_color,
                                  outline_width=layer.outline_width * scale)
        else:  # image
            renderer.draw_image(layer.image, rect)
    renderer.end_frame()


def _place(layer: Layer, viewport) -> tuple:
    """算出一层"画在屏幕上的样子"：(rect, points, 字号)。

    输入：
        layer: 要画的层；
        viewport: 这一帧的视口（None = 不换算）。
    输出：
        (屏幕 rect, 屏幕顶点, 屏幕字号)。
    异常：
        无。
    变量：
        无。

    口径：`fixed=True` 或没有视口时原样返回；否则矩形按 rect 换算、
    多边形顶点逐个换算、字号乘 zoom。
    """
    if viewport is None or layer.fixed:
        return layer.rect, layer.points, layer.font_size
    points = tuple(viewport.to_screen(point) for point in layer.points) if layer.points else ()
    return viewport.scale_rect(layer.rect), points, layer.font_size * viewport.zoom
