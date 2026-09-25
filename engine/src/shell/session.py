"""游戏会话：把"一局游戏"需要的对象串起来（§15.1 的运行阶段）。

位置：
    shell 包（引擎外壳）。它是界面层与引擎之间的唯一门面：

        外壳 UI / 游戏内 UI
              ↓  submit_input / settle / save / load
          GameSession
              ↓
          EngineRuntime（核心）+ 存档端口 + 文件端口

职责：
    - 新游戏：用 Mod 的初始 State 建运行期（随机种子由调用方给，便于复现）；
    - 运行时：把统一输入交给分发器、触发结算、按需拍快照；
    - 存档 / 读档：组装存档、走文件端口落盘、读回来应用到运行期；
    - 只读给界面用：state / content / mod_info / 日志出口。

边界：
    - 不做玩法判断（P1）；
    - 不画界面（渲染是 UI 适配器的事）；
    - 不认识平台（文件与脚本都走端口注入）。

草案依据：
    §15.1 启动与运行阶段；§17.2 三条流（互动流 / 只读流）；§18 存档与读档；
    D-24 每次运行只加载一个 Mod；D-30 快照时机由 Mod 决定。
"""

from typing import Any

from core.logger import LogSink, LogLevel
from core.persistence import SaveFile, read_save, write_save
from core.pipeline import Input
from core.ports import FileSystem
from core.runtime import EngineRuntime
from modload import LoadedMod, ModInfo


class GameSession:
    """一局游戏（一个 Mod + 一个运行期 + 一条存档路径）。

    字段：
        _loaded: 全加载完的 Mod（注册表、编译内容、脚本、初始 State）；
        _runtime: 引擎运行期；
        _files: 文件端口（读写存档）；
        _save_path: 本次会话使用的存档路径（可为空字符串：表示还没定）；
        _snapshot_every_settlement: 是否每次结算后自动拍快照（快照时机的一种选择）。
    """

    def __init__(
        self,
        loaded: LoadedMod,
        *,
        files: FileSystem,
        seed: int,
        runtime: EngineRuntime | None = None,
        save_path: str = "",
        log_sink: LogSink | None = None,
        log_level: LogLevel = LogLevel.INFO,
        clock: Any = None,
        snapshot_every_settlement: bool = False,
        state: dict | None = None,
    ) -> None:
        """创建会话（默认就是"开新局"）。

        输入：
            loaded: 全加载完的 Mod；
            files: 文件端口（存档读写用）；
            seed: 新游戏的随机种子；
            runtime: 直接注入的运行期（读档流程里用得上）；缺省按 Mod 新建一个；
            save_path: 存档路径；空字符串表示由调用方在 save() / load() 时再给；
            log_sink / log_level / clock: 日志与时钟（适配器提供）；
            snapshot_every_settlement: 每次结算后自动拍快照——这是"快照时机"的一种
                现成选择；Mod 想按回合 / 按存档拍，可以自己调 session.take_snapshot()；
            state: 自定义初始 State；缺省用 Mod 的初始 State。
        输出：
            无（构造对象）。
        异常：
            TypeError / ValueError: 参数不合法（由运行期与各端口抛出）。
        变量：
            无。
        """
        self._loaded: LoadedMod = loaded
        self._files: FileSystem = files
        self._save_path: str = save_path
        self._snapshot_every_settlement: bool = snapshot_every_settlement
        # 日志与时钟是"这台机器怎么装引擎"的事，与 Mod 内容无关：
        # 热重载重建运行期时要原样沿用，否则重载之后日志会莫名其妙地消失。
        self._log_sink: LogSink | None = log_sink
        self._log_level: LogLevel = log_level
        self._clock: Any = clock
        self._runtime: EngineRuntime = runtime if runtime is not None else self._build_runtime(
            seed=seed,
            state=loaded.initial_state if state is None else state,
            log_sink=log_sink,
            log_level=log_level,
            clock=clock,
        )

    def _build_runtime(
        self,
        *,
        seed: int,
        state: dict,
        log_sink: LogSink | None,
        log_level: LogLevel,
        clock: Any,
    ) -> EngineRuntime:
        """按 Mod 装一个引擎运行期。

        输入：
            seed: 随机种子；state: 初始 State；
            log_sink / log_level / clock: 日志与时钟。
        输出：
            EngineRuntime。
        异常：
            TypeError / ValueError: 参数不合法（由运行期抛出）。
        变量：
            无。
        """
        return EngineRuntime(
            state,
            hub=self._loaded.hub,
            content=self._loaded.content,
            services=self._loaded.services,
            mod_id=self._loaded.info.id,
            mod_version=self._loaded.info.version,
            seed=seed,
            log_sink=log_sink,
            log_level=log_level,
            clock=clock,
            functions=self._loaded.functions or None,
            params=self._loaded.params or None,
        )

    @property
    def mod_info(self) -> ModInfo:
        """返回本局 Mod 的元信息。"""
        return self._loaded.info

    @property
    def loaded(self) -> LoadedMod:
        """返回全加载的 Mod（界面读命令 / 动作定义时会用到）。"""
        return self._loaded

    @property
    def runtime(self) -> EngineRuntime:
        """返回引擎运行期（高级用法与测试直接用它）。"""
        return self._runtime

    @property
    def state(self) -> dict:
        """返回真实 State（界面只读拉取用，§17.2 的只读流）。"""
        return self._runtime.state

    @property
    def save_path(self) -> str:
        """返回本会话的存档路径（可能为空）。"""
        return self._save_path

    def submit_input(self, input_event: Input):
        """把一条统一输入交给分发器（互动流的入口）。

        输入：
            input_event: 统一输入。
        输出：
            映射成功时返回新建的 Command；没有匹配的映射时返回 None。
        异常：
            TypeError: input_event 不是 Input。
        变量：
            无。
        """
        return self._runtime.dispatcher.submit_input(input_event)

    def settle(self, labels: Any = ()):
        """触发"结算 Command 队列"特殊事件（何时结算由 Mod / 界面决定，§12）。

        输入：
            labels: Mod 贴在这一批上的标签（如 "turn:3"）。
        输出：
            SettlementResult。
        异常：
            TypeError: labels 不是字符串序列。
        变量：
            result: 结算结果。
        """
        result = self._runtime.dispatcher.trigger_settlement(labels)
        if self._snapshot_every_settlement:
            self.take_snapshot()
        return result

    def take_snapshot(self) -> None:
        """给当前 State 拍一次快照（快照时机由 Mod / 界面决定，D-30）。

        输入：无。
        输出：
            无。
        异常：
            无。
        变量：
            无。
        """
        self._runtime.journal.snapshot(self._runtime.state)

    def build_save(self, *, meta: dict | None = None) -> SaveFile:
        """组装一份当前存档（不落盘）。

        输入：
            meta: 元信息（存档名、时间等，引擎只存）。
        输出：
            SaveFile。
        异常：
            TypeError: meta 不是 dict。
        变量：
            无。
        """
        return self._runtime.build_save(
            scenario_id=self._loaded.info.scenario, meta=meta
        )

    def save(self, path: str = "", *, meta: dict | None = None) -> SaveFile:
        """把当前局面存档到文件（§18.1）。

        写盘前会**先拍一次快照**：这样存档里的基础 State 就是"现在"，
        不必再带"自上次快照以来的批次"——存档因此不会随局数一直涨
        （批次是"快照之后怎么变的"，快照刷新之后它们已经是冗余内容）。

        输入：
            path: 存档路径；空字符串表示用本会话之前设的路径；
            meta: 元信息。
        输出：
            写出去的 SaveFile（测试与日志用）。
        异常：
            ValueError: 没有给路径且会话也没有路径；
            其余异常由文件端口与存档组装抛出（保存失败不改 State，§19.2）。
        变量：
            target / save: 目标路径与组装好的存档。
        """
        target = path or self._save_path
        if not target:
            raise ValueError("没有指定存档路径：请给 save(path) 传路径")
        self.take_snapshot()          # 先拍快照：批次随快照归零（存档不随局数涨）
        save = self.build_save(meta=meta)
        write_save(self._files, target, save)
        self._save_path = target
        self._runtime.logger.info(
            f"存档已写入：{target}",
            mod_id=self._loaded.info.id,
            extra={"commands": len(save.commands), "settlements": len(save.settlements)},
        )
        return save

    def load(self, path: str = "") -> SaveFile:
        """从文件读档并应用到本会话（§18.2 的回放口径）。

        输入：
            path: 存档路径；空字符串表示用本会话之前设的路径。
        输出：
            读到的 SaveFile。
        异常：
            ValueError: 没有给路径且会话也没有路径；
            SaveFormatError / SaveVersionError: 存档读不懂或版本 / Mod 对不上（拒绝加载）；
            其余异常由文件端口抛出。
        变量：
            target / save: 目标路径与读到的存档。
        """
        target = path or self._save_path
        if not target:
            raise ValueError("没有指定存档路径：请给 load(path) 传路径")
        save = read_save(self._files, target, expected_mod=self._runtime.mod_record())
        self._runtime.apply_save(save)
        self._save_path = target
        return save

    def apply_reload(self, loaded: LoadedMod) -> None:
        """热重载：换成新加载的 Mod 内容，**保留当前局面**（F-01 的基础版）。

        输入：
            loaded: 重新全加载得到的 Mod（同一个 Mod 文件夹，内容可以是改过的）。
        输出：
            无。
        异常：
            TypeError: loaded 不是 LoadedMod；
            ValueError: 新内容的 Mod id 与当前不一致（那是换 Mod，不是重载）；
            LogicError / state 层异常: 新公式在自愈时算不出来。
        变量：
            state / random_state: 重载前先抓下来的局面与随机状态；
            runtime: 用新内容重建的运行期。

        说明：
            - **State 不换**：同一棵树的同一个对象（§6.2 的根身份不变），所以局面保留；
            - **随机状态接回**：随机不是 Mod 内容，重载不该把它重置（D-23）；
            - **派生值自愈**：新公式可能改了算法，重建之后立刻按新公式算一遍（§6.5）；
            - **命令与批次历史不接回**：那是"这一局的流水"，重载会把它清空；
              重载之后第一次存档会重新拍快照，所以回放不会漂（§18.2）。
        """
        if not isinstance(loaded, LoadedMod):
            raise TypeError(f"apply_reload 需要 LoadedMod，实际是 {type(loaded).__name__}")
        if loaded.info.id != self._loaded.info.id:
            raise ValueError(
                f"重载的 Mod 必须是同一个：当前是 {self._loaded.info.id!r}，"
                f"新加载的是 {loaded.info.id!r}"
            )

        state = self._runtime.state
        random_state = self._runtime.rng.state()

        self._loaded = loaded
        self._runtime = self._build_runtime(
            seed=0,
            state=state,
            log_sink=self._log_sink,
            log_level=self._log_level,
            clock=self._clock,
        )
        self._runtime.restore_random_state(random_state)
        updated = self._runtime.refresh_derived()
        self._runtime.logger.info(
            f"已重载 Mod：{loaded.info.name} v{loaded.info.version}",
            mod_id=loaded.info.id,
            extra={"derived_updates": len(updated)},
        )
