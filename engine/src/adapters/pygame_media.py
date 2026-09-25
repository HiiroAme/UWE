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
        _base: 相对路径的基准目录（通常是这一局的 Mod 文件夹；空串表示没有基准）；
        _ready: 混音器是否可用；
        _sounds: 音效路径 → Sound（缓存）；
        _current_music: 当前音乐路径（没有时为 None）。
    """

    def __init__(self, base_dir: str = "") -> None:
        """初始化混音器（失败就静默）。

        输入：
            base_dir: 相对路径的基准目录（通常是这一局的 Mod 文件夹）；
                空串表示没有基准，此时相对路径按进程当前工作目录解析。
        输出：
            无（构造对象）。
        异常：
            无（混音器起不来不算错，退化成静默即可）。
        变量：
            无。
        """
        self._base: Path | None = Path(base_dir) if base_dir else None
        self._sounds: dict[str, Any] = {}
        self._current_music: str | None = None
        try:
            pygame.mixer.init()
            self._ready = True
        except Exception as exc:  # 没有声音设备、驱动缺失……
            print(f"[媒体] 没有可用的声音设备，音效将被忽略：{exc}")
            self._ready = False

    def _resolve(self, path: str) -> Path:
        """把资源路径解析成实际文件：绝对路径照用；相对路径先按基准目录，找不到再回退当前目录。

        输入：
            path: 逻辑资源路径（Mod 里通常写 "assets/hit.wav"）。
        输出：
            Path：准备交给 pygame 的实际路径。
        异常：
            无。
        变量：
            raw / candidate: 原路径与"按基准目录拼出来"的候选路径。
        """
        raw = Path(path)
        if raw.is_absolute() or self._base is None:
            return raw
        candidate = self._base / raw
        return candidate if candidate.is_file() else raw

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
        resolved = self._resolve(path)
        key = str(resolved)
        sound = self._sounds.get(key)
        if sound is None:
            if not resolved.is_file():
                print(f"[媒体] 音效文件不存在，跳过：{path}")
                self._sounds[key] = False
                return False
            try:
                sound = pygame.mixer.Sound(str(resolved))
            except Exception as exc:
                print(f"[媒体] 音效加载失败，跳过：{path}：{exc}")
                self._sounds[key] = False
                return False
            self._sounds[key] = sound
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
        resolved = self._resolve(path)
        if not resolved.is_file():
            print(f"[媒体] 音乐文件不存在，跳过：{path}")
            return False
        try:
            pygame.mixer.music.load(str(resolved))
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
