"""shell 包：引擎外壳（启动流程与游戏会话）。

位置：
    引擎源码树，位于 core 与 adapters 之上。外壳自己不做玩法判断，也不碰渲染细节：
    它负责"把 Mod 装起来、把运行期建起来、把输入送进去、把存档读出来"，
    界面（外壳 UI 与游戏内 UI）再调用它。

文件分工：
    session.py  GameSession：一个"正在玩的这局游戏"——Mod、运行期、存档路径、
                新游戏 / 输入 / 结算 / 存档 / 读档。
    ui_host.py  UiHost：把"当前画面"与点击接起来（点击 → 统一输入 → 结算）。
    shell_app.py ShellApp：外壳接口（首页 / 选 Mod / 读档 / 游戏内）+ 主循环。

草案依据：
    §15.1 启动流程（浅加载 → 选择 → 全加载 → 注册 → 初始化 → 运行）；
    §17.1 外壳 UI 由引擎提供、游戏内 UI 由 Mod 提供；§18 存档与读档；D-24 单 Mod 运行。
"""

from .session import GameSession
from .shell_app import ShellApp
from .ui_host import UiHost, make_page_provider

__all__ = ["GameSession", "UiHost", "make_page_provider", "ShellApp"]
