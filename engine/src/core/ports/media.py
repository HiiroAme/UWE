"""媒体端口（P7）：播放音效 / 音乐。

位置：
    引擎核心逻辑层 → ports 子包。

职责：
    定义"播一下这个声音""放这段音乐""停音乐"的契约。
    真正调 pygame.mixer 或别的声音后端的是适配器。

为什么要单独一个端口：
    §17.1 明说"除了适配器，任何内容不能接触外部渲染或调用媒体"——
    所以 Mod 脚本想放音效，只能经过引擎接口（ServiceApi.play_sound），
    引擎再转给这个端口。这样"有没有声音设备"只影响适配器，不影响玩法逻辑。

失败口径（§19.2）：
    媒体是可选功能：放不出来就记录并继续，绝不影响规则判定与数据变动。

草案依据：
    §17.1 媒体走适配器；§19.2 可选功能失败记录并继续；P7 端口与适配器原则。
"""

from typing import Protocol


class Media(Protocol):
    """媒体端口。"""

    def play_sound(self, path: str) -> bool:
        """播放一次音效。

        输入：
            path: 音效文件路径（写法由适配器解释）。
        输出：
            True 表示确实开始播放了；False 表示这台机器放不了（缺文件 / 缺声音设备）。
        """

    def play_music(self, path: str, *, loop: bool = True) -> bool:
        """播放背景音乐（同一时刻只有一段）。

        输入：
            path: 音乐文件路径；
            loop: 是否循环。
        输出：
            True 表示确实开始播放了；False 表示放不了。
        """

    def stop_music(self) -> None:
        """停止背景音乐（没有在放时什么都不做）。"""


class SilentMedia:
    """什么都不放的媒体实现（默认值：没有声音设备时用它）。

    字段：
        无。
    """

    def play_sound(self, path: str) -> bool:
        """静默：返回 False 表示"没有真的播放"。"""
        return False

    def play_music(self, path: str, *, loop: bool = True) -> bool:
        """静默：返回 False。"""
        return False

    def stop_music(self) -> None:
        """静默：什么都不做。"""
