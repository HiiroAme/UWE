"""pygame 媒体适配器（core.ports.Media 的一个实现）。

位置：
    引擎适配层（core 之外）。只有这个文件碰 pygame.mixer。

职责：
    - 初始化混音器（失败就退化成"什么都不放"，只提醒一次）；
    - 播放音效 / 音乐、停止音乐；
    - 缓存已加载的音效（同一路径不重复读盘）。

失败口径（§19.2）：
    没有声音设备、音频文件缺失、格式不支持——一律返回 False 并提醒一句，
    绝不抛异常：音效是可选功能，不该影响玩法与数据。

草案依据：
    §17.1 媒体走适配器；§19.2 可选功能失败记录并继续；P7 端口与适配器原则。
"""

from pathlib import Path
from typing import Any

import pygame


class PygameMedia:
    """用 pygame.mixer 放声音。

    字段：
        _ready: 混音器是否可用；
        _sounds: 音效路径 → Sound（缓存）；
        _current_music: 当前音乐路径（没有时为 None）。
    """

    def __init__(self) -> None:
        """初始化混音器（失败就静默）。

        输入：无。
        输出：
            无（构造对象）。
        异常：
            无（混音器起不来不算错，退化成静默即可）。
        变量：
            无。
        """
        self._sounds: dict[str, Any] = {}
        self._current_music: str | None = None
        try:
            pygame.mixer.init()
            self._ready = True
        except Exception as exc:  # 没有声音设备、驱动缺失……
            print(f"[媒体] 没有可用的声音设备，音效将被忽略：{exc}")
            self._ready = False

    def play_sound(self, path: str) -> bool:
        """播放一次音效。

        输入：
            path: 音效文件路径。
        输出：
            True 表示真的播了；False 表示没有（静默 / 缺文件 / 格式不支持）。
        异常：
            无。
        变量：
            sound: 缓存的 Sound 对象。
        """
        if not self._ready:
            return False
        sound = self._sounds.get(path)
        if sound is None:
            if not Path(path).is_file():
                print(f"[媒体] 音效文件不存在，跳过：{path}")
                self._sounds[path] = False
                return False
            try:
                sound = pygame.mixer.Sound(path)
            except Exception as exc:
                print(f"[媒体] 音效加载失败，跳过：{path}：{exc}")
                self._sounds[path] = False
                return False
            self._sounds[path] = sound
        if sound is False:
            return False
        try:
            sound.play()
        except Exception as exc:  # 播放通道被占满之类
            print(f"[媒体] 音效播放失败：{path}：{exc}")
            return False
        return True

    def play_music(self, path: str, *, loop: bool = True) -> bool:
        """播放背景音乐。

        输入：
            path: 音乐文件路径；
            loop: 是否循环。
        输出：
            True 表示真的开始放了；False 表示没有。
        异常：
            无。
        变量：
            无。
        """
        if not self._ready:
            return False
        if not Path(path).is_file():
            print(f"[媒体] 音乐文件不存在，跳过：{path}")
            return False
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play(-1 if loop else 0)
        except Exception as exc:
            print(f"[媒体] 音乐播放失败：{path}：{exc}")
            return False
        self._current_music = path
        return True

    def stop_music(self) -> None:
        """停止背景音乐（没有在放时什么都不做）。"""
        if not self._ready or self._current_music is None:
            return
        try:
            pygame.mixer.music.stop()
        except Exception as exc:  # 停不下来也不用管
            print(f"[媒体] 停止音乐失败：{exc}")
        self._current_music = None
