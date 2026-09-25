"""服务脚本的解析端口（§15.2、§16）。

位置：
    引擎核心逻辑层 → ports 子包。

职责：
    定义"给一个 service 条目 id，拿到可以调用的服务函数"这一件事的契约。
    真正去读文件、import 脚本、取函数的是适配器（Mod 加载层），核心不认识路径与文件系统
    （P7：核心不自已 open() 文件）。

调用契约（Mod 作者必须遵守，写在注册表条目里）：
    service 函数签名：fn(api, args) -> None
        api:  core.pipeline.ServiceApi —— 读临时状态、产出变化量、取随机、写日志；
        args: 引擎已求值的参数块（dict）；
        返回值：不使用（产出改动只能走 api.emit，§16）。

草案依据：
    §14.2 service 注册表"自定义服务脚本"，引用脚本用路径或名字、不嵌代码（P2）；
    §16 脚本经引擎接口产生变化量；§9 第 7 步 Applier → Service；P7 端口与适配器。
"""

from typing import Any, Callable, Protocol

# 服务函数的标准签名：收到服务手柄与已求值的参数块，不返回值。
ServiceCallable = Callable[[Any, dict], None]


class ServiceNotAvailableError(Exception):
    """解析不到某个服务的实现（脚本不存在、函数名写错、脚本加载失败）。

    字段：
        detail: 说明文字；
        service_id: 出问题的服务条目 id。
    """

    def __init__(self, detail: str, *, service_id: str = "") -> None:
        """构造异常。

        输入：
            detail: 说明文字；
            service_id: 相关服务条目 id。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self.detail = detail
        self.service_id = service_id
        super().__init__(f"ServiceNotAvailableError | 服务={service_id} | {detail}" if service_id else detail)


class ServiceResolver(Protocol):
    """服务解析端口：service 条目 id → 可调用对象。"""

    def resolve(self, service_id: str) -> ServiceCallable:
        """取某个服务条目对应的可调用对象。

        输入：
            service_id: service 条目 id。
        输出：
            符合 ServiceCallable 签名的可调用对象（由适配器保证）。
        异常：
            ServiceNotAvailableError: 找不到实现（脚本缺失、函数名写错等）。
        """
