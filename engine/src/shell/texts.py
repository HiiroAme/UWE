"""引擎外壳文字：JSON 外置 + 代码中文兜底。

位置：
    shell 层。core 不读文件；本模块只负责把 `assets/texts/zh-CN.json`
    读成"逻辑键 → 文字"的表，调用方仍然带中文默认值。

约定：
    - 文件缺失 / JSON 坏了 / 键缺失：一律退回代码里的默认值，界面不崩；
    - 打包后 exe 旁边的 `shell/assets/texts/zh-CN.json` 优先于内置副本，
      所以 one-folder 发布可以改文字不重打包；
    - 引擎名 / 版本 / demo 标签仍由 core/version.py 管，不放进这个文件。
"""

import json

from .resources import AssetResolver, resource_roots

TEXTS_FILE = "texts/zh-CN.json"


class ShellTexts:
    """外壳文字的只读查询表。"""

    def __init__(self, data: dict | None = None) -> None:
        self._data = dict(data) if isinstance(data, dict) else {}

    @classmethod
    def load(cls, files, *, roots=None, logger=None) -> "ShellTexts":
        """从资源根里读文字文件；读不到就返回空表（调用方用兜底）。"""
        for base in (roots if roots is not None else resource_roots()):
            path = AssetResolver(base).path(TEXTS_FILE)
            if not path.is_file():
                continue
            try:
                data = json.loads(files.read_text(str(path)))
            except Exception as exc:
                if logger is not None:
                    logger.warn(f"外壳文字文件不可用，使用中文兜底：{exc}")
                return cls()
            if not isinstance(data, dict):
                if logger is not None:
                    logger.warn("外壳文字文件必须是 JSON 对象，使用中文兜底")
                return cls()
            return cls(data)
        return cls()

    def get(self, key: str, default: str, **fields) -> str:
        """取一条文字；缺失/类型不对用 default，fields 用来填模板占位符。"""
        value = self._data.get(key, default)
        if not isinstance(value, str):
            value = default
        if fields:
            try:
                return value.format(**fields)
            except (KeyError, IndexError, ValueError):
                return value
        return value
