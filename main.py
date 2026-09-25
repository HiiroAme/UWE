"""程序入口：把引擎装起来跑起来（§15.1 启动流程）。

位置：
    仓库根目录。**只有这个文件负责"选平台"**：

        日志出货口  → adapters.FileLogSink / StreamLogSink（写文件与屏幕）
        窗口与渲染  → adapters.PygameAdapter（pygame）
        文件与脚本  → adapters.LocalFileSystem / PythonScriptLoader

    换平台（例如网页版）只换这里选用的适配器，core 与 Mod 都不用动（P7）。

启动流程（§15.1）：
    扫描 Mod（浅加载）→ 首页 → 选 Mod 或读档 → 全加载 → 初始化 → 主循环
    这些事情在 shell.ShellApp 里；本文件只做"装配 + 起停"。

用法：
    源码运行（在仓库根执行）：python main.py
    打包运行：双击 UWE.exe（同目录应有 mods/ 与 modules/，存档与日志也落在它旁边）
"""

import sys
import time
from datetime import datetime
from pathlib import Path

def _app_root(frozen: bool | None = None, executable: str | None = None) -> Path:
    """算"程序根目录"：源码运行时是仓库根，打包后是 exe 所在的文件夹。

    输入：
        frozen: 是否按"已打包"处理；缺省读 `sys.frozen`（PyInstaller 会设置它）。
        executable: 打包时的 exe 路径；缺省读 `sys.executable`。
    输出：
        Path：mods / modules / saves / logs 都相对它来算。
    异常：
        无。
    变量：
        无。

    说明：
        打包后 `__file__` 指向解包出来的临时位置，不能再用它推目录；否则 exe 会去
        临时目录里找 mods，存档也会写进随时会被清掉的地方（详见 plans 里的打包记录）。
        参数是为了让测试能直接注入两种模式，不必真的去改 sys。
    """
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if is_frozen:
        exe = sys.executable if executable is None else executable
        return Path(exe).resolve().parent
    return Path(__file__).resolve().parent


APP_ROOT = _app_root()

# 引擎源码根：core / adapters / modload / shell 都在它下面（玩法与几何都在 modules/ 里）。
# 只有源码运行需要加这条 import 路径；打包后引擎模块在 exe 内部，直接 import 即可。
if not getattr(sys, "frozen", False):
    ENGINE_SOURCE = APP_ROOT / "engine" / "src"
    if str(ENGINE_SOURCE) not in sys.path:
        sys.path.insert(0, str(ENGINE_SOURCE))

from adapters import FileLogSink, LocalFileSystem, PythonScriptLoader, StreamLogSink, TeeSink  # noqa: E402
from adapters.pygame_adapter import PygameAdapter  # noqa: E402
from core.logger import LogLevel, Logger  # noqa: E402
from core.version import ENGINE_NAME, ENGINE_VERSION  # noqa: E402
from shell import ShellApp  # noqa: E402
from shell.resources import brand_icon_candidates  # noqa: E402

# 目录约定（相对程序根目录：源码运行时是仓库根，打包后是 exe 所在文件夹）。
MODS_ROOT = APP_ROOT / "mods"
MODULES_ROOT = APP_ROOT / "modules"
SAVES_ROOT = APP_ROOT / "saves"
LOGS_ROOT = APP_ROOT / "logs"

# 窗口尺寸（外壳与 Mod 的视图脚本都按它排版）。
WINDOW_SIZE = (960, 640)


def build_logger() -> Logger:
    """装配日志：文件 + 屏幕，带时间戳口径。

    输入：无。
    输出：
        Logger。
    异常：
        OSError: logs 目录建不出来（那就真没地方写日志了）。
    变量：
        path: 本次运行的日志文件路径。
    """
    LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    path = LOGS_ROOT / f"engine_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    sink = TeeSink((FileLogSink(str(path)), StreamLogSink()))
    # 用 TRACE：每条变化量的溯源记录（路径、新旧值、事件标签）都在这一级，
    # DEBUG 会把它们全过滤掉（P6 要求"数据变动处有日志"）。
    logger = Logger(sink=sink, clock=time.time, level=LogLevel.TRACE)
    logger.info(f"引擎版本 {ENGINE_VERSION}，日志文件：{path}")
    return logger


def main() -> int:
    """启动外壳并跑主循环。

    输入：无。
    输出：
        进程退出码：0 正常退出。
    异常：
        装配失败（缺 pygame、窗口开不了）会抛出，让用户直接看到原因。
    变量：
        logger / window / app: 装配出来的日志、窗口与外壳应用。
    """
    logger = build_logger()
    window = PygameAdapter(ENGINE_NAME, WINDOW_SIZE)
    icon_candidates = brand_icon_candidates()
    if icon_candidates and hasattr(window, "set_icon"):
        for icon in icon_candidates:
            if window.set_icon(str(icon)):
                break
    elif not icon_candidates:
        logger.debug("没有找到引擎图标资源（engine/src/shell/assets/brand/），跳过窗口图标")
    app = ShellApp(
        window=window,
        files=LocalFileSystem(),
        scripts=PythonScriptLoader(),
        mods_root=str(MODS_ROOT),
        saves_root=str(SAVES_ROOT),
        modules_root=str(MODULES_ROOT),
        logger=logger,
    )
    logger.info("进入主循环（Esc 返回首页 / 退出，S 存档）")
    try:
        app.run()
    finally:
        logger.info("已退出")
        logger.close()  # 退出前把文件日志关掉
    return 0


if __name__ == "__main__":
    sys.exit(main())
