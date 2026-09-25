"""分层日志（§19.1）。

位置：
    引擎核心逻辑层。它被所有层引用（State 改动、Rule 拒绝、提交、存档点），
    但自身不依赖任何上层，也不碰平台库。

职责：
    - 把"发生了什么"整理成一条结构化记录（LogRecord），交给出货口（LogSink）；
    - 按级别过滤（TRACE / DEBUG / INFO / WARN / ERROR / FATAL）；
    - 给记录配上运行内唯一、严格递增的序号，让日志的先后顺序可追溯
      （与变化量的 sequence 一样，日志也靠序号而不是时间戳排序）。

边界（P7 端口与适配器）：
    - 本模块**不写文件、不打印**：写到哪里是适配器的事，通过 LogSink 注入；
    - 读时钟也走注入（clock 是一个返回浮点秒数的零参调用），核心不直接调 time；
    - 本模块不做"日志即存档"这类事：日志不入存档，存档只用快照与变化量（§18.1）。

草案依据：
    §19.1 日志分工（引擎自动记什么、Mod 主动记什么）与日志字段；
    §19.1 日志层级 TRACE / DEBUG / INFO / WARN / ERROR / FATAL；
    §5 引擎运行时对象（logger 常驻）；P6 / P7。
"""

import sys
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Protocol


class LogLevel(IntEnum):
    """日志级别。数值越大越严重，过滤时按数值比较。

    说明：
        继承 IntEnum 是为了能直接比较大小（`level >= self._min_level`）；
        __str__ 返回名字（如 "INFO"），便于适配器一行写完。
    """

    TRACE = 10  # 单条变化量这一级
    DEBUG = 20  # Action / Service 调用这一级
    INFO = 30   # 提交、结算点、存档点
    WARN = 40   # 被拒绝、被覆盖
    ERROR = 50  # 事务失败、加载失败
    FATAL = 60  # 引擎不可继续

    def __str__(self) -> str:
        """返回级别名字（如 "INFO"）。"""
        return self.name


@dataclass(frozen=True)
class LogRecord:
    """一条日志记录（纯数据，可序列化）。

    字段：
        sequence: 运行内唯一的顺序号；日志的先后顺序以它为准；
        timestamp: 记录时间（秒，由注入的时钟给出）；只用于显示，不用于排序；
        level: 级别；
        message: 引擎自动写的事实描述（Mod 想解释"为什么"时走同一条通道）；
        mod_id / command_id / action_id / service_id: 溯源标识，没有时是空字符串；
        path: 涉及的数据路径（如变化量的 path），没有时是空字符串；
        label: 事件 id（变化量上的 label），没有时是空字符串；
        extra: 额外的键值对（例如 rejection_reason、rule_id）；内容由调用方决定。
    """

    sequence: int
    timestamp: float
    level: LogLevel
    message: str
    mod_id: str = ""
    command_id: str = ""
    action_id: str = ""
    service_id: str = ""
    path: str = ""
    label: str = ""
    extra: dict = field(default_factory=dict)


class LogSink(Protocol):
    """日志出货口（端口）：把一条记录交给具体实现去落盘 / 显示 / 丢弃。"""

    def write(self, record: LogRecord) -> None:
        """接收一条日志记录。"""


class MemorySink:
    """把日志留在内存里的出货口（测试与"引擎内查看日志"用）。

    字段：
        _records: 已收到的记录，按接收顺序。
    """

    def __init__(self) -> None:
        """创建一个内存日志口。

        输入：无。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self._records: list[LogRecord] = []

    def write(self, record: LogRecord) -> None:
        """收下一条记录。

        输入：
            record: 日志记录。
        输出：
            无。
        异常：
            TypeError: record 不是 LogRecord。
        变量：
            无。
        """
        if not isinstance(record, LogRecord):
            raise TypeError(f"MemorySink.write 需要 LogRecord，实际是 {type(record).__name__}")
        self._records.append(record)

    def records(self) -> tuple[LogRecord, ...]:
        """返回全部记录（只读快照）。"""
        return tuple(self._records)

    def clear(self) -> None:
        """清空记录（测试用；不影响别处）。"""
        self._records.clear()

    def __len__(self) -> int:
        """返回已收到的记录条数。"""
        return len(self._records)


class Logger:
    """引擎日志（分层、可溯源、顺序确定）。

    字段：
        _sink: 出货口；None 表示丢弃（测试里不关心日志时的省事写法）；
        _clock: 读时钟的调用（零参，返回浮点秒数），由适配器注入；
        _min_level: 最低记录级别：低于它的记录直接丢掉，连序号都不消耗；
        _next_sequence: 下一个要发的日志序号。
    """

    def __init__(
        self,
        sink: LogSink | None = None,
        clock: Callable[[], float] | None = None,
        level: LogLevel = LogLevel.INFO,
    ) -> None:
        """创建日志对象。

        输入：
            sink: 日志出货口；None 表示"不记日志"；
            clock: 读时钟的调用（零参、返回浮点秒数）；None 表示不读时钟，
                这种情况下记录里的 timestamp 恒为 0.0（测试里够用）；
            level: 最低记录级别，缺省 INFO。
        输出：
            无（构造对象）。
        异常：
            TypeError: clock 不是可调用对象，或 level 不是 LogLevel。
        变量：
            无。

        说明：
            clock 允许为 None 是刻意的：核心不自己去调 time.time()，
            真实运行时由适配器注入系统时钟（P7）。
        """
        if clock is not None and not callable(clock):
            raise TypeError(f"clock 必须是可调用对象，实际是 {type(clock).__name__}")
        if not isinstance(level, LogLevel):
            raise TypeError(f"level 必须是 LogLevel，实际是 {type(level).__name__}")
        self._sink: LogSink | None = sink
        self._sink_failed: bool = False      # 出货口抛过一次错就不再重复提示（R4-12）
        self._clock: Callable[[], float] | None = clock
        self._min_level: LogLevel = level
        self._next_sequence: int = 1

    @property
    def min_level(self) -> LogLevel:
        """返回当前的最低记录级别。"""
        return self._min_level

    def set_level(self, level: LogLevel) -> None:
        """调整最低记录级别（例如设置界面里改日志级别）。

        输入：
            level: 新的最低级别。
        输出：
            无。
        异常：
            TypeError: level 不是 LogLevel。
        变量：
            无。
        """
        if not isinstance(level, LogLevel):
            raise TypeError(f"level 必须是 LogLevel，实际是 {type(level).__name__}")
        self._min_level = level

    def trace(self, message: str, **fields: Any) -> None:
        """记一条 TRACE（单条变化量这一级）。"""
        self._emit(LogLevel.TRACE, message, fields)

    def debug(self, message: str, **fields: Any) -> None:
        """记一条 DEBUG（Action / Service 调用这一级）。"""
        self._emit(LogLevel.DEBUG, message, fields)

    def info(self, message: str, **fields: Any) -> None:
        """记一条 INFO（提交、结算点、存档点）。"""
        self._emit(LogLevel.INFO, message, fields)

    def warn(self, message: str, **fields: Any) -> None:
        """记一条 WARN（被拒绝、被覆盖）。"""
        self._emit(LogLevel.WARN, message, fields)

    def error(self, message: str, **fields: Any) -> None:
        """记一条 ERROR（事务失败、加载失败）。"""
        self._emit(LogLevel.ERROR, message, fields)

    def fatal(self, message: str, **fields: Any) -> None:
        """记一条 FATAL（引擎不可继续）。"""
        self._emit(LogLevel.FATAL, message, fields)

    def log(self, level: LogLevel, message: str, **fields: Any) -> None:
        """按给定级别记一条（Mod 自己挑级别时走这个入口）。

        输入：
            level: 级别；
            message: 消息文字；
            fields: 其余命名字段，与 trace / info 等相同。
        输出：
            无；低于最低级别、或没有出货口时直接丢弃。
        异常：
            TypeError: level 不是 LogLevel，或 message / fields 不合法。
        变量：
            无。
        """
        if not isinstance(level, LogLevel):
            raise TypeError(f"level 必须是 LogLevel，实际是 {type(level).__name__}")
        self._emit(level, message, fields)

    def close(self) -> None:
        """关闭日志：把出货口的资源（文件句柄等）交回给适配器。

        输入：无。
        输出：
            无。
        异常：
            无（出货口没有 close 就什么都不做）。
        变量：
            close: 出货口自己的关闭方法（可能没有）。

        说明：
            核心不关心资源怎么关，只负责把"该关了"这件事转告给出货口。
        """
        close = getattr(self._sink, "close", None)
        if callable(close):
            close()

    def _emit(self, level: LogLevel, message: str, fields: dict) -> None:
        """组装并发出记录（所有级别的公共实现）。

        输入：
            level: 级别；
            message: 消息文字；
            fields: 其余命名字段（mod_id / command_id / action_id / service_id /
                path / label / extra）。
        输出：
            无；低于最低级别、或没有出货口时直接丢弃。
        异常：
            TypeError: message 不是字符串，或 fields 里出现未知的键。
        变量：
            record: 组装好的记录。
        """
        if not isinstance(message, str):
            raise TypeError(f"日志消息必须是字符串，实际是 {type(message).__name__}")
        if level < self._min_level or self._sink is None:
            return

        unknown = set(fields) - {"mod_id", "command_id", "action_id", "service_id", "path", "label", "extra"}
        if unknown:
            raise TypeError(f"日志字段不认识：{', '.join(sorted(unknown))}")
        extra = fields.get("extra")
        if extra is not None and not isinstance(extra, dict):
            raise TypeError(f"extra 必须是 dict，实际是 {type(extra).__name__}")

        record = LogRecord(
            sequence=self._next_sequence,
            timestamp=self._clock() if self._clock is not None else 0.0,
            level=level,
            message=message,
            mod_id=fields.get("mod_id", ""),
            command_id=fields.get("command_id", ""),
            action_id=fields.get("action_id", ""),
            service_id=fields.get("service_id", ""),
            path=fields.get("path", ""),
            label=fields.get("label", ""),
            extra=dict(extra) if extra else {},
        )
        try:
            self._sink.write(record)
        except Exception as exc:      # 日志失败不该拖垮游戏（§19.2）：只报一次，之后安静
            if not self._sink_failed:
                self._sink_failed = True
                print(f"[日志] 写日志失败（之后不再重复这条提示）：{type(exc).__name__}: {exc}",
                      file=sys.stderr)
        self._next_sequence += 1
