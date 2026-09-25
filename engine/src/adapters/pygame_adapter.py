"""pygame 适配器：窗口、事件与渲染（core.ports.Window / Renderer 的实现）。

位置：
    引擎适配层（core 之外）。**只有这个文件与 shell 的启动代码碰 pygame**
    （§4：核心逻辑层禁止引用平台库；适配层允许）。

职责：
    - 开窗口、按帧取事件（点击 / 按键 / 关闭），翻译成 core.ports.WindowEvent；
    - 实现渲染端口：清屏、实心矩形、文字、图片；
    - 缓存字体与图片（同一路径不重复加载）。

资源路径口径（与 PygameMedia 一致）：
    绝对路径照用；相对路径先按 `set_asset_root()` 给的基准目录解析，
    找不到再回退进程当前工作目录。基准目录由**宿主**在进入一局时设置
    （main.py 把它接到正在加载的 Mod 文件夹上），所以 Mod 里写
    `kind="image", image="assets/x.png"` 这种相对路径才有意义（C-3）。

字体口径：
    中文要能显示，所以优先用调用方给的字体文件（例如 msyh.ttc）；
    没给或加载失败时退回 pygame 的默认字体（英文数字能看，中文可能是方框）。
    这是适配器的事，核心与 Mod 都不该关心字体从哪来。

草案依据：
    §4 适配层允许引用平台库；§17.1 渲染器是 UI 适配器的一部分；
    P7 端口与适配器（换渲染后端只换这一个文件）。
"""

import math
import os
from pathlib import Path
from typing import Any

import pygame

from core.ports import WindowEvent

# 常见的中文字体候选（找不到就退回默认字体）。
DEFAULT_FONT_CANDIDATES: tuple[str, ...] = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)

# 拖动阈值（像素）：按下后移动超过它才算拖动，抬起时不再当成点击。
_DRAG_THRESHOLD = 4


class PygameAdapter:
    """pygame 的窗口 + 事件 + 渲染实现（同时满足 Window 与 Renderer 两个端口）。

    字段：
        _screen: pygame 的窗口 Surface；
        _font_path: 正在用的字体文件（空字符串表示用默认字体）；
        _fonts: 字号 → 字体对象（缓存）；
        _images: 解析后的路径 → Surface（缓存；加载失败记 None）；
        _asset_root: 相对资源路径的基准目录（None = 没有基准，按进程当前目录）；
        _clock: 帧率控制；
        _size: 窗口尺寸；
        _pressed: 左键是否按下（用来区分点击与拖动）；
        _press_point: 按下时的屏幕坐标；
        _dragged: 这一次按下是否已经超过拖动阈值。
    """

    def __init__(
        self,
        title: str,
        size: tuple[int, int] = (960, 640),
        *,
        font_path: str = "",
        fps: int = 60,
        asset_root: str = "",
    ) -> None:
        """创建窗口与渲染器。

        输入：
            title: 窗口标题；
            size: 窗口尺寸 (宽, 高)；
            font_path: 中文字体文件路径；空字符串表示自动挑一个候选；
            fps: 帧率上限（避免空转吃满 CPU）。
            asset_root: 相对资源路径的基准目录（通常是这一局的 Mod 文件夹）；
                空字符串表示没有基准，此时相对路径按进程当前工作目录解析。
        输出：
            无（构造对象）。
        异常：
            ValueError: 尺寸 / 帧率不合法。
        变量：
            无。
        """
        if not isinstance(size, tuple) or len(size) != 2 or size[0] <= 0 or size[1] <= 0:
            raise ValueError(f"窗口尺寸必须是正整数二元组，实际是 {size!r}")
        if fps <= 0:
            raise ValueError(f"帧率必须是正数，实际是 {fps}")
        pygame.init()
        self._size: tuple[int, int] = size
        # RESIZABLE：窗口可以被玩家拉伸；尺寸以 surface 的当前值为准（见 size()）。
        self._screen = pygame.display.set_mode(size, pygame.RESIZABLE)
        pygame.display.set_caption(title)
        self._font_path: str = font_path or _first_existing(DEFAULT_FONT_CANDIDATES)
        self._fonts: dict[int, Any] = {}
        self._images: dict[str, Any] = {}
        self._asset_root: Path | None = Path(asset_root) if asset_root else None
        self._clock = pygame.time.Clock()
        self._fps: int = fps
        self._pressed: bool = False
        self._press_point: tuple[int, int] = (0, 0)
        self._dragged: bool = False

    # ---------------------------------------------------------------- Window 端口

    def size(self) -> tuple[int, int]:
        """返回窗口尺寸（**当前**尺寸：窗口被拉伸过也照实报）。"""
        current = self._screen.get_size()
        self._size = (int(current[0]), int(current[1]))
        return self._size

    def set_icon(self, path: str) -> bool:
        """设置窗口 / 任务栏图标；加载失败返回 False（不影响游戏启动）。"""
        try:
            icon = pygame.image.load(path)
            pygame.display.set_icon(icon)
            return True
        except Exception as exc:
            print(f"[适配器] 设置窗口图标失败：{path}：{exc}")
            return False

    def set_asset_root(self, path: str) -> None:
        """换"相对资源路径的基准目录"（宿主在进入 / 离开一局时调用）。

        输入：
            path: 新的基准目录；空字符串表示没有基准（相对路径按当前工作目录）。
        输出：
            无。
        异常：
            无。

        说明：
            只管**往后**画的图：已缓存的 Surface 按解析后的绝对路径存着，
            换基准不会让旧图串到新图上（换了基准又画同一个逻辑路径时会重新加载）。
        """
        self._asset_root = Path(path) if path else None

    def resolve_asset(self, path: str) -> str:
        """把资源路径解析成"准备交给 pygame 的实际路径"。

        输入：
            path: 逻辑资源路径（Mod 里通常写 "assets/x.png"）。
        输出：
            str：绝对路径照用；相对路径在基准目录下存在就用基准目录那份，
            否则原样返回（回退当前工作目录，和 PygameMedia 的口径一致）。
        异常：
            无。
        """
        raw = Path(path)
        if raw.is_absolute() or self._asset_root is None:
            return str(raw)
        candidate = self._asset_root / raw
        return str(candidate) if candidate.is_file() else str(raw)

    def poll_events(self) -> tuple[WindowEvent, ...]:
        """取走本帧的全部事件，翻译成统一窗口事件。

        输入：无。
        输出：
            WindowEvent 元组（可能为空）。
        异常：
            无。
        变量：
            event: 当前遍历到的 pygame 事件；
            stamp: 事件时间（秒，取 pygame 的毫秒计数）。
        """
        events: list[WindowEvent] = []
        stamp = pygame.time.get_ticks() / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                events.append(WindowEvent("quit", timestamp=stamp))
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                # 点击不在按下时发：先记住按点，抬起时如果没拖动才算点击。
                self._pressed = True
                self._press_point = (int(event.pos[0]), int(event.pos[1]))
                self._dragged = False
            elif event.type == pygame.MOUSEWHEEL:
                # 滚轮：delta=(0, 滚动格数)。鼠标位置要另外问 pygame（滚轮事件本身不带坐标）。
                events.append(WindowEvent(
                    "wheel", point=pygame.mouse.get_pos(),
                    delta=(0.0, float(event.y)), timestamp=stamp,
                ))
            elif event.type == pygame.MOUSEMOTION and event.buttons[0]:
                # 按住左键移动 = 拖动（dx, dy 是本帧位移，用来做"拖到哪动到哪"）。
                point = (int(event.pos[0]), int(event.pos[1]))
                if not self._pressed:
                    # 没看到按下事件（合成事件 / 丢帧）：按旧口径直接转成拖动。
                    self._dragged = True
                    events.append(WindowEvent(
                        "drag", point=point,
                        delta=(float(event.rel[0]), float(event.rel[1])), timestamp=stamp,
                    ))
                elif not self._dragged:
                    dx = point[0] - self._press_point[0]
                    dy = point[1] - self._press_point[1]
                    if dx * dx + dy * dy >= _DRAG_THRESHOLD * _DRAG_THRESHOLD:
                        self._dragged = True
                        # 第一次越过阈值时把"按下 → 现在"的位移一次补上，拖动不落后。
                        events.append(WindowEvent(
                            "drag", point=point, delta=(float(dx), float(dy)), timestamp=stamp,
                        ))
                else:
                    events.append(WindowEvent(
                        "drag", point=point,
                        delta=(float(event.rel[0]), float(event.rel[1])), timestamp=stamp,
                    ))
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                if self._pressed and not self._dragged:
                    events.append(WindowEvent("click", point=self._press_point, timestamp=stamp))
                self._pressed = False
                self._dragged = False
            elif event.type in (pygame.VIDEORESIZE, pygame.WINDOWRESIZED):
                # 窗口被拉伸 / 最大化：按新尺寸**重建绘制表面**。
                # （SDL 不是所有平台都会自动换；不重建的话画面只画在左上角。）
                width = int(getattr(event, "w", getattr(event, "x", 0)))
                height = int(getattr(event, "h", getattr(event, "y", 0)))
                if width > 0 and height > 0 and self._screen.get_size() != (width, height):
                    self._screen = pygame.display.set_mode((width, height), pygame.RESIZABLE)
                    self._size = (width, height)
            elif event.type == pygame.KEYDOWN:
                name = pygame.key.name(event.key)
                events.append(WindowEvent("key", key=name, timestamp=stamp))
        return tuple(events)

    def tick(self) -> None:
        """按帧率上限等一会儿（主循环每帧调用一次）。

        输入：无。
        输出：
            无。
        异常：
            无。
        变量：
            无。
        """
        self._clock.tick(self._fps)

    def close(self) -> None:
        """关闭窗口并退出 pygame（退出时调用一次）。"""
        try:
            pygame.display.quit()
        finally:
            pygame.quit()

    # -------------------------------------------------------------- Renderer 端口

    def begin_frame(self, background: tuple[int, int, int, int] | None) -> None:
        """开始一帧：按背景色清屏。"""
        if background is not None:
            self._screen.fill(_rgb(background))

    def draw_rect(self, rect: tuple[float, float, float, float], color: tuple[int, int, int, int]) -> None:
        """画一个实心矩形；alpha=0 表示"不画"（透明层不该涂黑，R6-8 / N-1）。"""
        if color[3] <= 0:
            return
        self._screen.fill(_rgb(color), _rect(rect))

    def draw_polygon(
        self,
        points: tuple[tuple[float, float], ...],
        color: tuple[int, int, int, int],
        *,
        outline_color: tuple[int, int, int, int] | None = None,
        outline_width: float = 0.0,
    ) -> None:
        """画一个多边形（可带描边）。

        输入：
            points: 顶点序列（至少三个），像素坐标；
            color: (r, g, b, a) 填充色；
            outline_color / outline_width: 描边色与宽度（宽度 >0 才描边，画在内侧）。
        输出：
            无。
        异常：
            无（顶点少于三个时跳过这一层，界面不该因为一个坏形状崩掉）。
        变量：
            polygon: 转成 int 的顶点列表（pygame 只吃整数坐标）。
        """
        polygon = [(int(x), int(y)) for x, y in points]
        if len(polygon) < 3:
            return
        # alpha=0 只表示"这一部分不画"：填充透明就跳过填充（否则会被画成实心黑），
        # 描边透明就跳过描边——模块画"只有格线、没有底色"的格子正是这种写法（N-1）。
        if color[3] > 0:
            pygame.draw.polygon(self._screen, _rgb(color), polygon)
        if (outline_color is not None and outline_color[3] > 0
                and outline_width and outline_width > 0):
            width = max(1, int(round(outline_width)))
            # 描边要落在多边形**内侧**：把顶点朝中心缩半个线宽，
            # 再用**闭合折线**画（pygame 的"实心多边形 + 线宽"会漏边，折线不会）。
            center_x = sum(point[0] for point in polygon) / len(polygon)
            center_y = sum(point[1] for point in polygon) / len(polygon)
            inset = []
            for x, y in polygon:
                dx, dy = x - center_x, y - center_y
                length = math.hypot(dx, dy) or 1.0
                keep = max(0.0, (length - width / 2.0)) / length
                inset.append((center_x + dx * keep, center_y + dy * keep))
            pygame.draw.lines(self._screen, _rgb(outline_color), True, inset, width)

    def draw_text(
        self,
        text: str,
        rect: tuple[float, float, float, float],
        *,
        size: float,
        color: tuple[int, int, int, int],
        align: str = "center",
    ) -> None:
        """在矩形里画文字（按 align 对齐；多行用 \\n 分隔）。

        输入：
            text: 文字（可以含换行）；
            rect: 目标矩形；
            size: 字号（像素高）；
            color: 文字颜色；
            align: "center" / "left" / "right"。
        输出：
            无。
        异常：
            无（画不出来就算了，界面不该因为一行字崩掉）。
        变量：
            font / surface / drawn: 字体、文字画布与逐行绘制的中间结果。
        """
        font = self._font(size)
        lines = str(text).split("\n")
        line_height = font.get_linesize()
        x, y, width, height = rect
        total_height = line_height * len(lines)
        top = y + (height - total_height) / 2
        for index, line in enumerate(lines):
            surface = font.render(line, True, _rgb(color))
            if align == "left":
                left = x + 6
            elif align == "right":
                left = x + width - surface.get_width() - 6
            else:
                left = x + (width - surface.get_width()) / 2
            self._screen.blit(surface, (left, top + index * line_height))

    def draw_image(self, path: str, rect: tuple[float, float, float, float]) -> None:
        """把图片缩放进矩形画出来（同一个实际路径只加载一次）。

        输入：
            path: 图片路径（相对路径按 set_asset_root 给的基准目录解析）；
            rect: 目标矩形。
        输出：
            无。
        异常：
            无（加载失败时跳过这张图，并在控制台留一行说明）。
        变量：
            resolved / surface / scaled: 实际路径、原图与缩放后的图。
        """
        resolved = self.resolve_asset(path)
        if resolved not in self._images:
            try:
                self._images[resolved] = pygame.image.load(resolved).convert_alpha()
            except Exception as exc:  # 图片缺失不该让整局游戏崩掉
                print(f"[适配器] 图片加载失败：{path}：{exc}")
                self._images[resolved] = None
        surface = self._images[resolved]
        if surface is None:
            return
        width, height = int(rect[2]), int(rect[3])
        scaled = pygame.transform.smoothscale(surface, (max(1, width), max(1, height)))
        self._screen.blit(scaled, (rect[0], rect[1]))

    def end_frame(self) -> None:
        """结束一帧：把内容显示出来。"""
        pygame.display.flip()

    def _font(self, size: float) -> Any:
        """取指定字号的字体对象（带缓存）。"""
        key = max(8, int(size))
        font = self._fonts.get(key)
        if font is None:
            if self._font_path and Path(self._font_path).is_file():
                font = pygame.font.Font(self._font_path, key)
            else:
                font = pygame.font.Font(None, key)
            self._fonts[key] = font
        return font


def _rgb(color: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """把 (r, g, b, a) 转成 pygame 用的 RGB（透明度交给绘制方式处理）。"""
    return int(color[0]), int(color[1]), int(color[2])


def _rect(rect: tuple[float, float, float, float]):
    """把 (x, y, 宽, 高) 转成 pygame 的 Rect。"""
    return pygame.Rect(int(rect[0]), int(rect[1]), max(0, int(rect[2])), max(0, int(rect[3])))


def _first_existing(candidates: tuple[str, ...]) -> str:
    """在候选路径里挑第一个存在的（都没有就返回空字符串）。"""
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""
