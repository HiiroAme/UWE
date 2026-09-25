"""存档的落盘与读盘（结构校验 + 版本校验，文件口由端口提供）。

位置：
    引擎核心逻辑层 → persistence 子包。

职责：
    把 SaveFile 与"一段 JSON 文本"互相转换，并通过**文件端口**读写：
        write_save(files, path, save)  组装 JSON 文本 → 交给文件端口写
        read_save(files, path)         从文件端口读文本 → 解析 → 校验 → SaveFile

边界（P7）：
    本模块不直接 open() 文件、不 import os：文件读写的具体实现由适配器提供
    （例如 adapters.LocalFileSystem）。这样换运行环境（桌面 / 网页）时只有适配器要换。

校验口径（D-25 / D-45）：
    JSON 解析失败、顶层不是对象、缺字段、字段类型不对 → SaveFormatError；
    三个格式版本或 Mod 身份对不上 → SaveVersionError。
    一律拒绝加载，不做兼容猜测。

草案依据：
    §18.1 存档结构与版本字段；D-25 版本不匹配直接报错拒绝加载；
    D-45 变化量记录自带格式版本；P7 端口与适配器。
"""

import json
from typing import Any

from ..ports import FileSystem
from .errors import SaveFormatError
from .model import ModRecord, SaveFile


def save_to_text(save: SaveFile) -> str:
    """把存档转成 JSON 文本。

    输入：
        save: 存档结构。
    输出：
        JSON 文本（UTF-8 友好：中文不转义，缩进两格便于人读）。
    异常：
        TypeError: save 不是 SaveFile；
        SaveFormatError: 存档里有不是纯 JSON 数据的值（例如 NaN / 自定义对象）。
    变量：
        data: 存档的纯数据形式。
    """
    if not isinstance(save, SaveFile):
        raise TypeError(f"save_to_text 需要 SaveFile，实际是 {type(save).__name__}")
    data = save.to_data()
    try:
        # allow_nan=False：JSON 标准里没有 NaN / Infinity，宁可当场报错，
        # 也不写出一份别的解析器读不懂的"存档"。
        return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SaveFormatError(f"存档里出现了不能写成 JSON 的值：{exc}") from exc


def save_from_text(
    text: str,
    *,
    path: str = "",
    expected_mod: ModRecord | None = None,
) -> SaveFile:
    """把 JSON 文本读成存档结构（含版本校验）。

    输入：
        text: JSON 文本；
        path: 存档路径（报错用）；
        expected_mod: 当前运行的 Mod 记录；给了就一并比对。
    输出：
        SaveFile。
    异常：
        TypeError: text 不是字符串；
        SaveFormatError: 不是合法 JSON、或结构不合法；
        SaveVersionError: 版本 / Mod 对不上。
    变量：
        data: json.loads 的结果。
    """
    if not isinstance(text, str):
        raise TypeError(f"save_from_text 需要字符串，实际是 {type(text).__name__}")
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SaveFormatError(f"存档不是合法的 JSON：{exc}", path=path) from exc
    return SaveFile.from_data(data, path=path, expected_mod=expected_mod)


def write_save(files: FileSystem, path: str, save: SaveFile) -> None:
    """把存档写进文件（通过文件端口）。

    输入：
        files: 文件端口（适配器实现）；
        path: 目标路径；
        save: 存档结构。
    输出：
        无。
    异常：
        TypeError: files 没有 write_text 方法；
        SaveFormatError / 适配器自己的异常: 写失败或内容不能序列化。
    变量：
        text: 组装好的 JSON 文本。
    """
    if not hasattr(files, "write_text"):
        raise TypeError("files 必须实现 write_text(path, text) 接口（见 core.ports.FileSystem）")
    text = save_to_text(save)
    files.write_text(path, text)


def read_save(files: FileSystem, path: str, *, expected_mod: ModRecord | None = None) -> SaveFile:
    """从文件读一份存档（通过文件端口，含版本校验）。

    输入：
        files: 文件端口（适配器实现）；
        path: 存档路径；
        expected_mod: 当前运行的 Mod 记录；给了就一并比对。
    输出：
        SaveFile。
    异常：
        TypeError: files 没有 read_text 方法；
        SaveFormatError / SaveVersionError: 见模块文档；
        适配器自己的异常: 读失败（例如文件不存在）。
    变量：
        text: 读到的 JSON 文本。
    """
    if not hasattr(files, "read_text"):
        raise TypeError("files 必须实现 read_text(path) 接口（见 core.ports.FileSystem）")
    text = files.read_text(path)
    return save_from_text(text, path=path, expected_mod=expected_mod)
