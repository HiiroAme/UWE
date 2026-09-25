"""外壳应用：首页 / 选 Mod / 读档 / 游戏内界面 + 主循环（§15.1 启动流程、§17.1）。

位置：
    shell 包。它是"外壳 UI"的实现：外壳 UI 由引擎提供（首页、选 Mod、读档、设置、关于），
    游戏内界面由 Mod 提供（通过 ui 条目 + 视图脚本）。两者共用同一套机制：
    都是"产出 View → 命中判定 → 统一输入"（§17.1 两个来源一套机制）。

职责：
    - 启动流程：扫 Mod → 首页 → 选 Mod / 读档 → 全加载 → 开新局或读档 → 进游戏；
    - 每帧：把当前画面交给渲染端口画出来，把窗口事件翻译成点击与按键；
    - 游戏内：Mod 的画面在下层，外壳的按钮（存档 / 返回）在上层；点击先给上层；
    - 界面状态（当前在哪个页面、提示文字、选中了什么）放在 Context 里（不存档，§6.1）。

边界：
    - 引擎不硬编码任何玩法：游戏内画面完全由 Mod 的视图脚本产出；
    - 外壳不认识平台：窗口与渲染都走端口（adapters.PygameAdapter 实现）。

草案依据：
    §15.1 启动流程；§17.1 外壳 UI 与游戏内 UI 共用一套机制；§17.2 三条流；
    §12 结算时机由 Mod 决定（点击结果里的 settle）；D-08 两个来源的分工。
"""

import time
import gc
from dataclasses import replace
from typing import Any, Callable

from core.context import Context
from core.logger import LogLevel, Logger
from core.pipeline import Input
from core.ports import FileSystem, ScriptLoader, Window, WindowEvent
from core.syscall import SyscallResult, SyscallRunner
from core.ui import ClickResult, Layer, View
from core.version import ENGINE_NAME, version_text
from modload import ModInfo, ModLoader
from shell.session import GameSession
from shell.texts import ShellTexts
from shell.ui_host import UiHost, draw_view, make_page_provider

# 界面状态里用到的键（不存档，§6.1）。
_KEY_SCREEN = "shell.screen"
_KEY_MESSAGE = "shell.message"
_KEY_MODS = "shell.mods"
_KEY_SAVES = "shell.saves"
_KEY_INDEX = "shell.index"
_KEY_MENU_OPEN = "shell.menu_open"

# 外壳状态提示的存活时间（秒）：到点自动消失；失败类提示给得长一点，免得没看清就没了。
_MESSAGE_SECONDS = 3.0
_MESSAGE_SECONDS_LONG = 6.0

# 外壳自己的层用这个前缀，点击时不会被当成 Mod 的输入（§17.1 两个来源）。
SHELL_PREFIX = "shell:"


class ShellApp:
    """外壳应用（一个主循环 + 四个界面）。

    字段：
        _window / _files / _scripts: 端口；
        _loader: Mod 加载器；
        _saves_root: 存档目录；
        _context: 界面状态（不存档）；
        _logger: 日志对象（可为 None）；
        _session: 当前这一局（None 表示还没开始）；
        _screen_host / _game_host / _overlay_host: 画面来源与点击分发的三个宿主；
        _seed_supplier: 取新游戏种子的调用（缺省用当前时间，便于复现时可以换成固定值）；
        _clock: 读时间的调用（缺省 time.monotonic，用于状态提示的过期）；
        _message_until: 当前状态提示该在什么时刻消失（0 = 没有提示）；
        _title: 窗口标题。
        _mods_root / _saves_root: 目录（设置页要显示，重载时还要用）。
    """

    def __init__(
        self,
        *,
        window: Window,
        files: FileSystem,
        scripts: ScriptLoader,
        mods_root: str,
        saves_root: str,
        modules_root: str = "",
        logger: Logger | None = None,
        seed_supplier: Callable[[], int] | None = None,
        clock: Callable[[], float] | None = None,
        title: str = ENGINE_NAME,
    ) -> None:
        """创建外壳应用。

        输入：
            window: 窗口端口（同时要满足渲染端口，通常就是 adapters.PygameAdapter）；
            files: 文件端口；
            scripts: 脚本端口；
            mods_root: Mod 根目录；
            saves_root: 存档目录；
            modules_root: 逻辑模块根目录（每个子文件夹一个模块）；空字符串表示不带模块。
            logger: 日志对象；
            seed_supplier: 取新游戏随机种子的调用；缺省用秒级时间戳；
            clock: 读时间的调用（秒，单调递增）；缺省 time.monotonic，测试可注入假时钟；
            title: 窗口标题。
        输出：
            无（构造对象）。
        异常：
            TypeError: 端口不满足契约。
        变量：
            无。
        """
        if not hasattr(window, "poll_events") or not hasattr(window, "size"):
            raise TypeError("window 必须实现 Window 端口（size / poll_events / close）")
        if not hasattr(window, "begin_frame") or not hasattr(window, "end_frame"):
            raise TypeError("window 必须同时实现 Renderer 端口（begin_frame / draw_* / end_frame）")
        self._window: Any = window
        self._files: FileSystem = files
        self._scripts: ScriptLoader = scripts
        module_roots = [modules_root] if modules_root else []
        self._loader: ModLoader = ModLoader(files, scripts, mods_root, module_roots=module_roots)
        self._mods_root: str = mods_root
        self._saves_root: str = saves_root
        self._logger: Logger | None = logger
        self._texts: ShellTexts = ShellTexts.load(files, logger=logger)
        self._context: Context = Context()
        self._session: GameSession | None = None
        self._syscalls: SyscallRunner | None = None
        self._screen_host: UiHost = UiHost(window, context=self._context, logger=logger)
        self._game_host: UiHost = UiHost(window, context=self._context, logger=logger)
        self._overlay_host: UiHost = UiHost(window, context=self._context, logger=logger)
        self._seed_supplier: Callable[[], int] = seed_supplier or (lambda: int(time.time()))
        self._clock: Callable[[], float] = clock or time.monotonic
        self._message_until: float = 0.0
        self._title: str = title
        # 主循环开关：退出类操作把它置 False，由 run() 统一收尾（关窗口只做一次）。
        self._running: bool = True
        self._set_screen("menu")

    # ------------------------------------------------------------------ 主循环

    def run(self) -> None:
        """跑主循环直到用户退出。

        输入：无。
        输出：
            无。
        异常：
            由端口抛出（画不出来 / 事件取不出来都该如实报错）。
        变量：
            event: 当前窗口事件；
            running: 是否继续循环。
        """
        while self._running:
            self.render()
            for event in self._window.poll_events():
                if event.kind == "quit":
                    self._running = False
                elif event.kind == "click":
                    self.handle_click(event)
                elif event.kind == "wheel":
                    self.handle_wheel(event)
                elif event.kind == "drag":
                    self.handle_drag(event)
                elif event.kind == "key":
                    if not self.handle_key(event.key):
                        self._running = False
            if hasattr(self._window, "tick"):
                self._window.tick()
        self._window.close()

    def render(self) -> None:
        """画当前这一帧（外壳画面 + 游戏内画面 + 外壳按钮）。"""
        draw_view(self._window, self.build_view())

    def build_view(self) -> View:
        """按当前界面组装这一帧的画面（游戏内时：Mod 的层在下、外壳的按钮在上）。

        输入：无。
        输出：
            View；同时会刷新各宿主的"最近一帧"，点击命中判定用的就是它。
        异常：
            由画面来源抛出（Mod 视图脚本写错会在这里暴露）。
        变量：
            无。
        """
        if self._session is None:
            view = self._screen_host.build_view()
            return _with_title(view, self._title)
        game_view = self._game_host.build_view()
        overlay = self._overlay_host.build_view()
        # 外壳按钮"贴在屏幕上"：把它们标成 fixed，这样它们不跟着 Mod 的视口一起缩放。
        overlay_layers = tuple(replace(layer, fixed=True) for layer in overlay.layers)
        layers = tuple(game_view.layers) + overlay_layers
        return View(layers=layers, background=game_view.background,
                    viewport=game_view.viewport)

    # ------------------------------------------------------------------ 事件

    @property
    def session(self) -> GameSession | None:
        """返回当前这一局（还没进入游戏时是 None）。

        输入：无。
        输出：
            GameSession 或 None。界面与测试都通过它拿当前会话，
            不必去碰内部字段。
        """
        return self._session

    @property
    def context(self) -> Context:
        """返回外壳自己的界面状态（不存档，§6.1）。"""
        return self._context

    def _text(self, key: str, default: str, **fields: Any) -> str:
        """取一条外壳文字：JSON 优先，缺失时用代码里的默认值。"""
        return self._texts.get(key, default, **fields)

    def _put_message(self, text: str, ttl: float = _MESSAGE_SECONDS) -> None:
        """写一行外壳状态提示，并记下它什么时候该消失。

        输入：
            text: 提示文字；空串表示"清掉提示"；
            ttl: 存活秒数（默认 3 秒，失败类提示用 _MESSAGE_SECONDS_LONG）。
        输出：
            无。
        异常：
            无。
        变量：
            无。

        说明：
            提示是**外壳自己的界面状态**（§6.1，不进存档）：存档、重载、读档失败
            这类系统事件的反馈都由外壳产生，所以生命周期也在外壳这一层管。
        """
        self._context.put(_KEY_MESSAGE, text)
        self._message_until = self._clock() + ttl if text else 0.0

    def _visible_message(self) -> str:
        """当前还没过期的状态提示；到点就顺手清掉（视图每帧都会问一次）。

        输入：无。
        输出：
            提示文字；没有提示或已经过期时是空串。
        异常：
            无。
        变量：
            无。
        """
        if self._clock() >= self._message_until:
            if self._context.get(_KEY_MESSAGE, ""):
                self._context.put(_KEY_MESSAGE, "")
            self._message_until = 0.0
            return ""
        return self._context.get(_KEY_MESSAGE, "")

    def handle_click(self, event: WindowEvent) -> None:
        """处理一次点击：先给最上层（外壳按钮），再给 Mod 的界面。

        输入：
            event: 点击事件（必须带坐标）。
        输出：
            无。
        异常：
            无（未知的点击什么都不做）。
        变量：
            point / layer / overlay_layer: 点击位置与命中的层。
        """
        if event.point is None:  # 理论上不会发生（WindowEvent 已校验），兜一层
            return
        point = (float(event.point[0]), float(event.point[1]))

        if self._session is None:
            layer = self._screen_host.view.hit_test(point)
            if layer is not None:
                self._handle_shell_action(_kind_of(layer))
            return

        overlay_layer = self._overlay_host.view.hit_test(point)
        if overlay_layer is not None:
            self._handle_shell_action(_kind_of(overlay_layer))
            return
        if self._overlay_host.view.pointer_blocker(point) is not None:
            return          # 菜单打开：挡住地图点击
        self._game_host.click_at(point, event.timestamp)

    def handle_wheel(self, event: WindowEvent) -> None:
        """处理一次滚轮：转给游戏内界面（外壳界面暂时不用滚轮）。

        输入：
            event: 滚轮事件（带鼠标位置与滚动量）。
        输出：
            无。
        异常：
            无。
        变量：
            无。

        说明：
            滚轮也是**内部事件**：外壳只负责转发，不关心它被用来缩放还是滚动；
            而且**只在 Mod 声明过这类输入时**才转发——否则会刷一堆"没有这个输入类别"的日志。
        """
        if self._session is None or event.point is None:
            return
        point = (float(event.point[0]), float(event.point[1]))
        if self._overlay_host.view.pointer_blocker(point) is not None:
            return          # 菜单打开：挡住地图滚轮（滚动条留给菜单自己）
        if not self._session.loaded.content.input_candidates("wheel"):
            return
        self._game_host.wheel_at(
            point, event.delta, event.timestamp
        )

    def handle_drag(self, event: WindowEvent) -> None:
        """处理一次拖动（按住左键移动）：只在 Mod 声明过 drag 输入时才转发。

        输入：
            event: 拖动事件（带鼠标位置与本帧位移）。
        输出：
            无。
        异常：
            无。
        变量：
            无。

        说明：
            拖动是"拖到哪动到哪"的即时反馈：每一条拖动事件都会立刻结算一次，
            这样下一帧画面就跟上手指（代价是拖动期间每帧多跑一次命令）。
        """
        if self._session is None or event.point is None:
            return
        point = (float(event.point[0]), float(event.point[1]))
        if self._overlay_host.view.pointer_blocker(point) is not None:
            return          # 菜单打开：挡住地图拖动
        if not self._session.loaded.content.input_candidates("drag"):
            return
        self._game_host.drag_at(
            point, event.delta, event.timestamp
        )

    def handle_key(self, key: str) -> bool:
        """处理按键；返回是否继续运行。

        输入：
            key: 键名（小写）。
        输出：
            True 表示继续运行，False 表示退出。
        异常：
            无。
        变量：
            无。
        """
        if self._session is None:
            return key not in ("escape", "q")
        if key == "escape":
            # 1) Mod 弹窗（规则 / 开局介绍）优先接管 Esc：发它的 close_rules 输入。
            if self._game_host.escape_event() is not None:
                return True
            # 2) 菜单已打开：Esc 返回首页（走正常的自动存档路径）。
            if self._context.get(_KEY_MENU_OPEN, False):
                self.trigger_syscall("engine:syscall:back_to_menu")
                return True
            # 3) 游戏界面：Esc 打开菜单。
            self._context.put(_KEY_MENU_OPEN, True)
            return True
        if key == "s":
            self.trigger_syscall("engine:syscall:save")
        if key == "f5":
            self.trigger_syscall("engine:syscall:reload_mod")
        return True

    # ------------------------------------------------------------------ 界面切换

    def _set_screen(self, screen: str) -> None:
        """切到某个外壳界面并（需要时）刷新它要显示的数据。"""
        self._context.put(_KEY_SCREEN, screen)
        if screen == "menu":
            self._screen_host.set_provider(self._menu_view)
        elif screen == "settings":
            self._screen_host.set_provider(self._settings_view)
        elif screen == "about":
            self._screen_host.set_provider(self._about_view)
        elif screen == "mod_select":
            self._context.put(_KEY_MODS, self._scan_mods())
            self._screen_host.set_provider(self._mod_select_view)
        elif screen == "load":
            self._context.put(_KEY_SAVES, self._list_saves())
            self._screen_host.set_provider(self._load_view)
        else:
            raise ValueError(f"没有这个界面：{screen!r}")

    def _scan_mods(self) -> tuple[ModInfo, ...]:
        """扫描 Mod 目录；读不出来的文件夹记进界面提示，不影响其他 Mod。

        输入：无。
        输出：
            ModInfo 元组（能读出来的那些）。
        异常：
            无（错误转成界面上的提示文字）。
        变量：
            result: 扫描结果（可用的 Mod + 读不出来的文件夹）。
        """
        try:
            result = self._loader.try_scan()
        except Exception as exc:
            self._put_message(f"扫描 Mod 失败：{exc}", _MESSAGE_SECONDS_LONG)
            return ()
        if result.problems:
            details = "；".join(
                f"{_file_name(problem.folder)}：{problem.detail}" for problem in result.problems
            )
            self._put_message(
                f"有 {len(result.problems)} 个文件夹读不出来 —— {details}",
                _MESSAGE_SECONDS_LONG,
            )
        return result.mods

    def _list_saves(self) -> tuple[str, ...]:
        """列出存档目录里的 .json 文件（按名字排序）。"""
        return tuple(self._files.list_files(self._saves_root, ".json"))

    # ------------------------------------------------------------------ 三个外壳界面

    def _menu_view(self, host: UiHost) -> View:
        """首页：开始新游戏 / 读取存档 / 退出。"""
        width, height = self._window.size()
        return View(
            layers=(
                _label("shell:title", ENGINE_NAME, (0, height * 0.18, width, 48), 32),
                _button("new", self._text("home.new", "开始新游戏"),
                        (width / 2 - 120, height * 0.42, 240, 44)),
                _button("load", self._text("home.load", "读取存档"),
                        (width / 2 - 120, height * 0.42 + 60, 240, 44)),
                _button("settings", self._text("home.settings", "设置"),
                        (width / 2 - 120, height * 0.42 + 120, 240, 44)),
                _button("quit", self._text("home.quit", "退出"),
                        (width / 2 - 120, height * 0.42 + 180, 240, 44)),
                Layer(
                    id="shell:version",
                    rect=(12.0, max(0.0, height - 24.0), 260.0, 18.0),
                    kind="text",
                    text=version_text(),
                    font_size=13,
                    color=(0, 0, 0, 0),
                    text_color=(180, 184, 194, 255),
                    align="left",
                    z=10,
                ),
                _message_layer(self._visible_message(), (0, height - 60, width, 40)),
            )
        )

    def _settings_view(self, host: UiHost) -> View:
        """设置页：目前只留标题、关于按钮与返回按钮。

        输入：
            host: 界面宿主（本页不需要）。
        输出：
            View。
        异常：
            无。
        变量：
            width / height: 窗口尺寸。

        说明：
            目前没有可调项；说明性文字放"关于"页，并且从外置文字文件读取。
        """
        width, height = self._window.size()
        return View(
            layers=(
                _label("shell:title", self._text("settings.title", "设置"), (0, 40, width, 40), 26),
                _button("about", self._text("settings.about", "关于"),
                        (width / 2 - 90, height / 2 - 22, 180, 44)),
                _button("back", self._text("settings.back", "返回"), (24, 24, 120, 40)),
                _message_layer(self._visible_message(), (0, height - 64, width, 40)),
            )
        )

    def _about_view(self, host: UiHost) -> View:
        """关于页：正文来自外置文字文件，代码保留兜底。"""
        width, height = self._window.size()
        return View(
            layers=(
                _label("shell:title", self._text("about.title", "关于"), (0, 40, width, 40), 26),
                Layer(
                    id="shell:about_text",
                    rect=(60, 110, width - 120, height - 220),
                    kind="text",
                    text=self._text("about.body", "关于信息未配置"),
                    align="left",
                    font_size=18,
                    color=(30, 34, 44, 255),
                    text_color=(230, 230, 230, 255),
                ),
                _button("about_back", self._text("about.back", "返回"), (24, 24, 120, 40)),
                _message_layer(self._visible_message(), (0, height - 64, width, 40)),
            )
        )

    def _mod_select_view(self, host: UiHost) -> View:
        """选 Mod：列出扫描到的 Mod，点一个就开新局。"""
        width, height = self._window.size()
        mods: tuple[ModInfo, ...] = self._context.get(_KEY_MODS, ())
        layers: list[Layer] = [
            _label("shell:title", self._text("mod_select.title", "选择一个 Mod"),
                   (0, 40, width, 40), 26),
            _button("back", self._text("common.back", "返回"), (24, 24, 120, 40)),
            _message_layer(self._visible_message(), (0, height - 64, width, 40)),
        ]
        if not mods:
            layers.append(_label(
                "shell:empty",
                self._text("mod_select.empty", "没有找到任何 Mod（检查 mods 目录）"),
                (0, 120, width, 32), 20,
            ))
        for index, info in enumerate(mods):
            top = 110 + index * 56
            text = self._text(
                "mod_select.entry", "{name}（{id} v{version}）",
                name=info.name, id=info.id, version=info.version,
            )
            layers.append(
                Layer(
                    id=f"{SHELL_PREFIX}mod:{info.id}",
                    rect=(width / 2 - 260, top, 520, 44),
                    kind="text",
                    text=text,
                    align="left",
                    font_size=20,
                    color=(40, 46, 60, 255),
                    text_color=(235, 235, 235, 255),
                    click=ClickResult(kind=f"{SHELL_PREFIX}mod:{info.id}", settle=False),
                )
            )
        return View(layers=tuple(layers))

    def _load_view(self, host: UiHost) -> View:
        """读档：列出存档文件，点一个就把它读进来。"""
        width, height = self._window.size()
        saves: tuple[str, ...] = self._context.get(_KEY_SAVES, ())
        layers: list[Layer] = [
            _label("shell:title", self._text("load.title", "读取存档"), (0, 40, width, 40), 26),
            _button("back", self._text("common.back", "返回"), (24, 24, 120, 40)),
            _message_layer(self._visible_message(), (0, height - 64, width, 40)),
        ]
        if not saves:
            layers.append(_label(
                "shell:empty",
                self._text("load.empty", "存档目录里还没有存档"),
                (0, 120, width, 32), 20,
            ))
        for index, path in enumerate(saves):
            top = 110 + index * 52
            layers.append(
                Layer(
                    id=f"{SHELL_PREFIX}save:{path}",
                    rect=(width / 2 - 260, top, 520, 40),
                    kind="text",
                    text=_file_name(path),
                    align="left",
                    font_size=20,
                    color=(40, 46, 60, 255),
                    text_color=(235, 235, 235, 255),
                    click=ClickResult(kind=f"{SHELL_PREFIX}save:{path}", settle=False),
                )
            )
        return View(layers=tuple(layers))

    # ------------------------------------------------------------------ 外壳动作

    def _handle_shell_action(self, kind: str) -> None:
        """按点击结果里的外壳动作名执行动作（外壳界面与游戏内按钮都走这里）。

        输入：
            kind: 层的点击结果 kind；必须以 "shell:" 开头（否则什么都不做）。
        输出：
            无。
        异常：
            无。
        变量：
            action: 去掉前缀之后的具体动作名。
        """
        if not kind.startswith(SHELL_PREFIX):
            return
        action = kind[len(SHELL_PREFIX):]
        if action == "new":
            self._set_screen("mod_select")
        elif action == "load":
            self._set_screen("load")
        elif action == "settings":
            self._set_screen("settings")
        elif action == "about":
            self._set_screen("about")
        elif action == "quit":
            # 走同一个退出路径：先自动存档、再让主循环停（不在这里直接关窗口，
            # 否则这一帧接下来的 poll_events 会因为窗口已经没了而报错）。
            self._put_message(self._text("message.bye", "再见"))
            self._quit()
        elif action == "back":
            self._set_screen("menu")
        elif action == "about_back":
            self._set_screen("settings")
        elif action.startswith("mod:"):
            self.start_new_game(action[len("mod:"):])
        elif action.startswith("save:"):
            self._load_game(action[len("save:"):])
        elif action == "game:save":
            self.trigger_syscall("engine:syscall:save")
        elif action == "game:back":
            self.trigger_syscall("engine:syscall:back_to_menu")
        elif action == "game:menu_toggle":
            self._context.put(_KEY_MENU_OPEN, not self._context.get(_KEY_MENU_OPEN, False))
        elif action.startswith("modmenu:"):
            self._trigger_mod_menu(action[len("modmenu:"):])

    def _trigger_mod_menu(self, index_text: str) -> None:
        """点菜单里的 Mod 条目：把它的输入送进当前会话并结算。

        外壳不认识条目内容（例如"规则"）；它只按 Mod 声明的 input kind 发统一输入。
        """
        if self._session is None:
            return
        try:
            index = int(index_text)
            entry = self._session.loaded.info.menu[index]
        except (ValueError, IndexError, TypeError):
            return
        # 点了菜单项就先把外壳菜单收起来：Mod 的动作（例如打开规则）直接叠在游戏上，
        # 关掉它之后回到游戏，而不是回到菜单。
        self._context.put(_KEY_MENU_OPEN, False)
        self._session.submit_input(Input(entry.input, dict(entry.data), 0.0, "ui"))
        self._session.settle((f"ui:modmenu:{index}",))

    def start_new_game(self, mod_id: str) -> None:
        """全加载选中的 Mod 并开新局。"""
        try:
            mods: tuple[ModInfo, ...] = self._context.get(_KEY_MODS, ())
            info = next((item for item in mods if item.id == mod_id), None)
            if info is None:
                raise ValueError(f"找不到 Mod：{mod_id!r}")
            loaded = self._loader.load(info)
            session = GameSession(
                loaded,
                files=self._files,
                seed=self._seed_supplier(),
                save_path=f"{self._saves_root}/{mod_id}_slot1.json",
                log_sink=None,
                log_level=self._logger.min_level if self._logger is not None else LogLevel.INFO,
            )
        except Exception as exc:
            self._put_message(f"加载 Mod 失败：{exc}", _MESSAGE_SECONDS_LONG)
            return
        self._enter_game(session)

    def _load_game(self, path: str) -> None:
        """读档进游戏：先从存档里读出 Mod 身份，加载对应 Mod，再应用存档。"""
        try:
            from core.persistence import read_save

            save = read_save(self._files, path)
            mods: tuple[ModInfo, ...] = self._scan_mods()
            info = next((item for item in mods if item.id == save.mod.id), None)
            if info is None:
                raise ValueError(f"存档属于 Mod {save.mod.id!r}，但没找到它")
            loaded = self._loader.load(info)
            session = GameSession(loaded, files=self._files, seed=0, save_path=path)
            session.runtime.apply_save(save)
        except Exception as exc:
            self._put_message(f"读档失败：{exc}", _MESSAGE_SECONDS_LONG)
            return
        self._enter_game(session)

    def _enter_game(self, session: GameSession) -> None:
        """进入游戏：把 Mod 的视图脚本接上，并装好外壳按钮。"""
        self._session = session
        self._put_message("")
        self._context.put(_KEY_MENU_OPEN, False)
        page_id = session.loaded.default_page
        if page_id:
            page = session.loaded.pages[page_id]
            # 界面状态用**运行期的 Context**：Mod 的服务与 Mod 的视图脚本要读写同一份
            # （§6.1：Context 由 Service 与 UI 共同使用）；外壳自己的界面状态另存一份。
            self._game_host.set_provider(
                make_page_provider(
                    page,
                    state_getter=lambda: session.state,
                    context=session.runtime.context,
                    # 传函数而不是值：窗口被拉伸 / 最大化之后，Mod 每帧都要拿到**新**尺寸。
                    size=lambda: self._window.size(),
                    page_id=page_id,
                    functions=session.runtime.functions,
                )
            )
        else:  # 这个 Mod 没有自带界面：给一张空画面，外壳按钮仍然可用
            self._game_host.set_provider(lambda host: View())
        self._game_host.set_input_sink(
            session.submit_input, lambda labels: session.settle(labels)
        )
        # 系统事件派发器：处理函数由**外壳**提供（核心不碰文件与窗口，§17.3 / P7）。
        self._syscalls = SyscallRunner(
            session.loaded.content,
            {
                "save": lambda args: self._save_current(),
                "autosave": lambda args: self._autosave(),
                "cleanup": lambda args: self._cleanup(),
                "reload_mod": lambda args: self._reload_mod(),
                "quit": lambda args: self._quit(),
                "back_to_menu": lambda args: self._leave_game(),
            },
            logger=self._logger,
        )
        if self._logger is not None:
            # R4-6：启动时把「这台宿主提供哪些系统事件处理」记一条 INFO——
            # handler 名写错时，一眼就能对照出来。
            self._logger.info(
                f"宿主可用的系统事件处理：{sorted(self._syscalls.available())}"
            )
        self._overlay_host.set_provider(self._game_overlay_view)
        self._screen_host.set_provider(None)
        if self._logger is not None:
            self._logger.info(
                f"进入游戏：{session.mod_info.name} v{session.mod_info.version}",
                mod_id=session.mod_info.id,
                extra={
                    "system_initial_values": len(session.loaded.system_paths),
                    "templates": len(session.loaded.content.templates),
                },
            )

    def _leave_game(self) -> None:
        """回到首页（先自动存档，再放下这一局）。"""
        if self._session is not None:
            self.trigger_syscall("engine:syscall:autosave")
            # 清理内存要在丢掉派发器之前触发，否则那次调用会被静默跳过。
            self.trigger_syscall("engine:syscall:cleanup")
        self._session = None
        self._syscalls = None
        self._context.put(_KEY_MENU_OPEN, False)
        self._game_host.set_provider(None)
        self._overlay_host.set_provider(None)
        self._set_screen("menu")

    def _quit(self) -> None:
        """退出程序（先自动存档，再让主循环停下来）。

        说明：
            这里**不直接关窗口**：窗口一关，事件系统就没了
            （pygame 会抛 "video system not initialized"），而主循环这一帧还在取事件。
            所以只置标志，由 `run()` 统一收尾关窗口。
        """
        if self._session is not None:
            self.trigger_syscall("engine:syscall:autosave")
        self._running = False

    def _autosave(self) -> str:
        """自动存档到固定路径（handler 调用）。

        输入：无。
        输出：
            写出去的存档路径。
        异常：
            OSError 等由文件适配器抛出（会被 SyscallRunner 记成失败结果）。
        变量：
            target: 自动存档路径（与手动槽位分开，不覆盖玩家自己的存档）。
        """
        if self._session is None:
            return ""
        target = f"{self._saves_root}/{self._session.mod_info.id}_autosave.json"
        self._session.save(target, meta={"kind": "autosave"})
        self._put_message(f"已自动存档：{_file_name(target)}")
        return target

    def _cleanup(self) -> str:
        """清理内存（handler 调用）：把这一局留下的临时对象尽量交还给系统。

        输入：无。
        输出：
            一句说明（放进 SyscallResult.value，日志里能看到）。
        异常：
            无。
        变量：
            collected: 本次回收的对象数（只用于日志）。

        说明：
            引擎的运行期对象（注册表、编译内容、脚本模块）**不在这里清**：
            它们是 Mod 的一部分，清了下次进游戏还得重载；这里只做一轮垃圾回收，
            把上次那局留在内存里的 State 副本、视图、日志记录等收回去。
        """
        collected = gc.collect()
        return f"已回收 {collected} 个对象"

    def _reload_mod(self) -> str:
        """重载 Mod（handler 调用）：重新全加载内容与脚本，保留当前局面（F-01 基础版）。

        输入：无。
        输出：
            一句说明（放进日志与界面提示）。
        异常：
            Exception: 加载失败时上抛（SyscallRunner 会记成失败结果并提示用户）。
        变量：
            info / loaded: 当前 Mod 的元信息与重新加载的结果。

        说明：
            开发时改了 Mod 的数据或脚本，不必重启程序：重载会
            ① 丢掉脚本缓存（否则会拿到旧模块）、② 重新全加载与编译、
            ③ 用同一棵 State 重建运行期（局面保留）、④ 按新公式自愈派生值。
        """
        if self._session is None:
            return "还没进入游戏，没有可重载的 Mod"
        info = self._session.mod_info
        forget = getattr(self._scripts, "clear", None)
        if callable(forget):
            forget()  # 丢掉脚本缓存，下一次加载才会读到改过的文件
        loaded = self._loader.load(info)
        self._session.apply_reload(loaded)
        self._enter_game(self._session)  # 重新接界面（页面脚本也可能换了）
        self._put_message(f"已重载 Mod：{info.name} v{info.version}")
        return f"已重载 {info.id}"

    def trigger_syscall(self, syscall_id: str, *, args: dict | None = None) -> SyscallResult | None:
        """触发一个系统事件（保存 / 退出 / 返回首页）。

        输入：
            syscall_id: syscall 条目 id（例如 "engine:syscall:save"）；
            args: 附加参数（覆盖条目里写的同名参数）。
        输出：
            SyscallResult；还没进入游戏（没有派发器）时返回 None。
        异常：
            TypeError: args 不是 dict。
        变量：
            result: 派发结果。

        说明：
            外壳只认"系统事件的名字"，具体做什么由 syscall 条目 + 处理函数决定
            （§17.3：由 syscall 注册表登记、交适配器 / 宿主执行）。
        """
        if self._syscalls is None:
            return None
        result = self._syscalls.run(syscall_id, args=args)
        if not result.ok:
            self._put_message(f"系统事件失败：{result.detail}", _MESSAGE_SECONDS_LONG)
        return result

    def _save_current(self) -> None:
        """把当前这一局存到本会话的存档路径。"""
        if self._session is None:
            return
        try:
            save = self._session.save()
        except Exception as exc:
            self._put_message(f"存档失败：{exc}", _MESSAGE_SECONDS_LONG)
            return
        self._put_message(
            f"已存档：{_file_name(self._session.save_path)}（命令 {len(save.commands)} 条）"
        )

    def _game_overlay_view(self, host: UiHost) -> View:
        """游戏内菜单栏：左上角「菜单」；打开后是 存档 / 退出到首页 / Mod 菜单项。"""
        width, height = self._window.size()
        message = self._visible_message()
        layers: list[Layer] = []
        # Mod 的模态弹窗（规则 / 开局介绍）打开时：外壳菜单按钮让位，Esc 由 Mod 的
        # escape_target 接管（见 handle_key 的第一优先级）。
        game_blocked = self._game_host.view.pointer_blocker((1.0, 1.0)) is not None
        if not game_blocked:
            layers.append(_button("game:menu_toggle", self._text("menu.button", "菜单"),
                                  (16, 12, 120, 36), z=1000))
            if self._context.get(_KEY_MENU_OPEN, False):
                entries = tuple(self._session.loaded.info.menu) if self._session is not None else ()
                panel_h = 56 + 44 * (2 + len(entries))
                layers.append(Layer(
                    id="shell:menu:blocker", rect=(0.0, 0.0, float(width), float(height)),
                    kind="rect", color=(0, 0, 0, 0), visible=False, blocks_pointer=True,
                    fixed=True, z=900,
                ))
                layers.append(Layer(
                    id="shell:menu:panel", rect=(16.0, 56.0, 220.0, float(panel_h)),
                    kind="rect", color=(30, 34, 44, 255), fixed=True, z=1000,
                ))
                layers.append(_button("game:save", self._text("menu.save", "存档（S）"),
                                      (24, 64, 204, 36), z=1001))
                layers.append(_button("game:back", self._text("menu.back", "退出到首页"),
                                      (24, 108, 204, 36), z=1001))
                for index, entry in enumerate(entries):
                    layers.append(_button(
                        f"modmenu:{index}", entry.label,
                        (24, 152 + 44 * index, 204, 36), z=1001,
                    ))
        if message:
            layers.append(
                Layer(
                    id="shell:game:message",
                    rect=(0, height - 44, width, 36),
                    kind="text",
                    text=message,
                    font_size=18,
                    color=(40, 46, 60, 255),
                    text_color=(240, 240, 240, 255),
                    z=1000,
                    fixed=True,
                )
            )
        return View(layers=tuple(layers), background=None)


def _with_title(view: View, title: str) -> View:
    """给一个画面加上窗口标题文字（外壳界面用）。

    说明：
        标题固定在外壳画面**右上角**：左上角留给"返回"等导航按钮，
        两个角各管一件事，窗口再窄也不会互相压住。
    """
    width = 0
    for layer in view.layers:
        width = max(width, layer.rect[0] + layer.rect[2])
    layers = tuple(view.layers) + (
        Layer(
            id="shell:window_title",
            rect=(max(0.0, width - 280.0), 8, 260.0, 24),
            kind="text",
            text=title,
            align="right",
            font_size=16,
            color=(0, 0, 0, 0),
            text_color=(150, 150, 160, 255),
        ),
    )
    return View(layers=layers, background=view.background)


def _button(
    action: str,
    text: str,
    rect: tuple[float, float, float, float],
    z: int = 0,
) -> Layer:
    """造一个外壳按钮（可点击的文字层）。

    输入：
        action: 外壳动作名（会拼成 "shell:<动作>" 的 ClickResult.kind）；
        text: 按钮文字；
        rect: 位置与尺寸；
        z: 绘制层级。
    输出：
        Layer。
    异常：
        无。
    变量：
        无。

    说明：
        外壳按钮的 ClickResult 只是"这里可以点"的标记：外壳应用按 kind 自己处理，
        不会把它当成统一输入送进管线（§17.1 两个来源，各管各的）。
    """
    return Layer(
        id=f"shell:{action}",
        rect=rect,
        kind="text",
        text=text,
        font_size=20,
        color=(60, 70, 92, 255),
        text_color=(240, 240, 240, 255),
        click=ClickResult(kind=f"shell:{action}", settle=False),
        z=z,
    )


def _kind_of(layer: Layer) -> str:
    """取一个层点击结果的 kind（没有点击结果时给空字符串）。"""
    return layer.click.kind if layer.click is not None else ""


def _label(layer_id: str, text: str, rect: tuple[float, float, float, float], size: float) -> Layer:
    """造一个纯文字标签。"""
    return Layer(
        id=layer_id,
        rect=rect,
        kind="text",
        text=text,
        font_size=size,
        color=(0, 0, 0, 0),
        text_color=(235, 235, 235, 255),
    )


def _message_layer(text: str, rect: tuple[float, float, float, float]) -> Layer:
    """造一行提示文字（文字由调用方给：外壳按存活时间过滤过）。"""
    return Layer(
        id="shell:message",
        rect=rect,
        kind="text",
        text=text,
        font_size=18,
        color=(0, 0, 0, 0),
        text_color=(230, 180, 120, 255),
    )


def _file_name(path: str) -> str:
    """取路径里的文件名（界面上显示用）。"""
    return path.replace("\\", "/").rsplit("/", 1)[-1]
