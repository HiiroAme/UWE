"""日志出货口的具体实现（core.ports 之外的适配层）。

位置：
    引擎适配层。核心只认 core.logger.LogSink 这个出货口，落到文件还是屏幕是这里的事。

职责：
    - FileLogSink：把每条记录按一行写进文件（UTF-8 追加）；
    - StreamLogSink：写到标准输出（开发时看）；
    - TeeSink：同时给多个出货口（例如文件 + 屏幕）。

草案依据：
    §19.1 日志分层与可溯源；P7 端口与适配器（写文件是适配器的事）；
    §19.2 日志写失败不该拖垮游戏（这里失败只打印一行提醒）。
"""

from pathlib import Path
from typing import Any, Sequence

from core.logger import LogRecord


def format_record(record: LogRecord) -> str:
    """把一条记录排成一行文本。

    输入：
        record: 日志记录。
    输出：
        形如 "时间 [级别] mod=… 命令=… 动作=… 服务=… 路径=… 事件=… 消息 {extra}" 的一行。
    异常：
        无。
    变量：
        parts / extra: 拼装中的片段与附加字段。
    """
    stamp = f"{record.timestamp:.3f}" if record.timestamp else "-"
    parts = [stamp, f"[{record.level}]"]
    for name, value in (
        ("mod", record.mod_id),
        ("命令", record.command_id),
        ("动作", record.action_id),
        ("服务", record.service_id),
        ("路径", record.path),
        ("事件", record.label),
    ):
        if value:
            parts.append(f"{name}={value}")
    parts.append(record.message)
    if record.extra:
        parts.append(str(record.extra))
    return " ".join(parts)


class FileLogSink:
    """把日志追加写进一个文件。

    字段：
        _path: 目标文件；
        _handle: 已打开的文件对象（第一次写入时打开，避免空文件）。
    """

    def __init__(self, path: str) -> None:
        """创建文件日志口。

        输入：
            path: 日志文件路径（父目录会自动建出来）。
        输出：
            无（构造对象）。
        异常：
            ValueError: 路径是空字符串。
        变量：
            无。
        """
        if not path:
            raise ValueError("日志文件路径不能是空字符串")
        self._path = Path(path)
        self._handle: Any = None

    def write(self, record: LogRecord) -> None:
        """写一条记录（失败只提醒，不抛出）。

        输入：
            record: 日志记录。
        输出：
            无。
        异常：
            无（写不进去时打印一行提醒：日志失败不该拖垮游戏）。
        变量：
            无。
        """
        try:
            if self._handle is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._handle = self._path.open("a", encoding="utf-8")
            self._handle.write(format_record(record) + "\n")
            self._handle.flush()
        except OSError as exc:  # 磁盘满、权限不足……
            print(f"[日志] 写入失败：{exc}")

    def close(self) -> None:
        """关闭文件（退出时调用）。"""
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class StreamLogSink:
    """把日志写到标准输出（开发时看）。"""

    def write(self, record: LogRecord) -> None:
        """打印一条记录。"""
        print(format_record(record), flush=True)


class TeeSink:
    """同时给多个出货口。

    字段：
        _sinks: 目标出货口（按顺序写）。
    """

    def __init__(self, sinks: Sequence[Any]) -> None:
        """创建转发口。

        输入：
            sinks: 出货口序列（每个都要有 write(record)）。
        输出：
            无（构造对象）。
        异常：
            TypeError: 某个出货口没有 write 方法。
        变量：
            无。
        """
        for sink in sinks:
            if not hasattr(sink, "write"):
                raise TypeError(f"出货口必须有 write(record)：{sink!r}")
        self._sinks = tuple(sinks)

    def write(self, record: LogRecord) -> None:
        """按顺序转发给每个出货口。"""
        for sink in self._sinks:
            sink.write(record)

    def close(self) -> None:
        """关闭所有能关闭的出货口。"""
        for sink in self._sinks:
            close = getattr(sink, "close", None)
            if callable(close):
                close()
