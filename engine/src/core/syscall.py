"""系统事件：与操作系统 / 宿主环境打交道的那类事件（§17.3、D-32）。

位置：
    引擎核心逻辑层。它只做"查表 + 派发 + 记录结果"，真正执行的是适配器提供的处理函数。

系统事件有哪些（§17.3）：
    保存、（自动）保存、重载 Mod、清理内存、退出。它们由 syscall 注册表登记，
    交给适配器执行——**核心不碰文件、不碰窗口、不碰进程**（P7）。

要不要走 Trigger 链（D-32）：
    "按需"——需要就走，不需要就不走。本模块给出结果（SyscallResult），
    调用方（外壳）想让 Mod 知道"存过档了"，可以拿结果去触发一个事件；
    引擎不强制。

与特殊事件的区别（§17.3）：
    特殊事件（结算 Command 队列）由引擎实现、Mod 决定何时触发；
    系统事件由 syscall 注册表登记、适配器执行。

草案依据：
    §17.3 特殊事件与系统事件；D-32 系统事件按需走 Trigger 链；
    §19.2 可选功能失败要记录并继续（保存失败不回滚 State）；
    P1-3 内置内容本质上属于 Mod，只是加载路径不同；P7 端口与适配器。
"""

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .content import CompiledContent
from .logger import Logger


@dataclass(frozen=True)
class SyscallResult:
    """一次系统事件执行的结果。

    字段：
        syscall_id: 被触发的系统事件条目 id；
        handler: 实际执行的处理函数名（适配器按名字提供）；
        ok: 是否成功；
        detail: 失败原因（成功时是空字符串）；
        value: 处理函数的返回值（例如保存路径），引擎不解释。
    """

    syscall_id: str
    handler: str
    ok: bool
    detail: str = ""
    value: Any = None


class SyscallRunner:
    """系统事件的派发器。

    字段：
        _content: 编译后的内容（syscall 条目在这里）；
        _handlers: 处理函数表（名字 → 可调用对象），由适配器 / 外壳提供；
        _logger: 日志对象。
    """

    def __init__(
        self,
        content: CompiledContent,
        handlers: Mapping[str, Callable[[dict], Any]],
        *,
        logger: Logger | None = None,
    ) -> None:
        """创建派发器。

        输入：
            content: 编译后的内容；
            handlers: 处理函数表；函数签名 fn(args) -> 任意值；
            logger: 日志对象（可为 None）。
        输出：
            无（构造对象）。
        异常：
            TypeError: content 类型不对，或某个处理函数不可调用。
        变量：
            无。
        """
        if not isinstance(content, CompiledContent):
            raise TypeError(f"SyscallRunner 需要 CompiledContent，实际是 {type(content).__name__}")
        for name, handler in handlers.items():
            if not isinstance(name, str) or not name:
                raise TypeError(f"处理函数的名字必须是非空字符串，实际是 {name!r}")
            if not callable(handler):
                raise TypeError(f"处理函数 {name!r} 不可调用")
        self._content: CompiledContent = content
        self._handlers: Mapping[str, Callable[[dict], Any]] = dict(handlers)
        self._logger: Logger | None = logger

    def available(self) -> tuple[str, ...]:
        """返回这台机器上可用的处理函数名（外壳用它决定哪些按钮可用）。"""
        return tuple(sorted(self._handlers))

    def run(self, syscall_id: str, *, args: dict | None = None) -> SyscallResult:
        """触发一个系统事件。

        输入：
            syscall_id: syscall 条目 id（例如 "engine:syscall:save"）；
            args: 本次调用附带的参数（会覆盖条目里写的同名参数）。
        输出：
            SyscallResult。
        异常：
            TypeError: args 不是 dict。
        变量：
            entry: 条目定义；
            merged: 条目参数与本次参数的合并结果；
            handler: 取到的处理函数。

        说明：
            失败（条目不存在、处理函数缺失、处理函数抛异常）都会被记成一条失败的
            SyscallResult 并写日志，**不往上抛**：系统事件是"可选的边角功能"，
            不该把游戏本身拖垮（§19.2）。调用方（外壳）拿 ok 与 detail 去提示用户。
        """
        if args is not None and not isinstance(args, dict):
            raise TypeError(f"args 必须是 dict 或 None，实际是 {type(args).__name__}")
        entry = self._content.syscalls.get(syscall_id)
        if entry is None:
            return self._fail(syscall_id, "", f"没有登记这个系统事件：{syscall_id!r}")
        handler = self._handlers.get(entry.handler)
        if handler is None:
            return self._fail(
                syscall_id,
                entry.handler,
                f"这台机器没有提供处理函数 {entry.handler!r}（可用：{list(self.available())}）",
            )
        merged = dict(entry.args)
        if args:
            merged.update(args)
        try:
            value = handler(merged)
        except Exception as exc:  # 处理函数出问题：记录并继续
            return self._fail(syscall_id, entry.handler, f"{type(exc).__name__}: {exc}")
        if self._logger is not None:
            self._logger.info(
                f"系统事件已执行：{entry.handler}",
                extra={"syscall": syscall_id, "args": merged},
            )
        return SyscallResult(syscall_id=syscall_id, handler=entry.handler, ok=True, value=value)

    def _fail(self, syscall_id: str, handler: str, detail: str) -> SyscallResult:
        """记一条失败结果并写日志。"""
        if self._logger is not None:
            self._logger.warn(
                f"系统事件执行失败：{detail}",
                extra={"syscall": syscall_id, "handler": handler},
            )
        return SyscallResult(syscall_id=syscall_id, handler=handler, ok=False, detail=detail)
