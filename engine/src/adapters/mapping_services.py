"""把内存里的一张表当作服务实现（ServiceResolver 的一个实现）。

位置：
    引擎适配层（core 之外）。

职责：
    实现 core.ports.ServiceResolver：
        resolve(service_id) → 可调用对象（签名 fn(api, args) -> None）。

用途：
    - 单元测试：测试要的是"服务被怎么调用"，不是"脚本怎么从磁盘加载"；
    - 内置内容与演示：把几个函数直接摆进表里，不必建文件。
    真正从 Mod 文件夹读脚本、import 模块、取函数的实现，属于 Mod 加载层（§15.2）。

草案依据：
    §14.2 service 注册表（引用脚本用路径或名字）；§16 脚本经引擎接口产生变化量；
    P7 端口与适配器（核心只依赖抽象，实现在适配器侧）。
"""

from typing import Mapping

from core.ports import ServiceCallable, ServiceNotAvailableError


class MappingServiceResolver:
    """以一张字典为后端的服务解析器。

    字段：
        _services: service 条目 id → 可调用对象；构造时拷贝一份，之后不受外部改动影响。
    """

    def __init__(self, services: Mapping[str, ServiceCallable]) -> None:
        """创建解析器。

        输入：
            services: id → 可调用对象；键必须是非空字符串，值必须是可调用对象。
        输出：
            无（构造对象）。
        异常：
            TypeError: services 不是映射，或有键 / 值类型不对。
            ValueError: 出现空字符串键。
        变量：
            service_id / service: 遍历时当前检查的键与值。
        """
        if not isinstance(services, Mapping):
            raise TypeError(f"services 必须是映射，实际是 {type(services).__name__}")
        table: dict[str, ServiceCallable] = {}
        for service_id, service in services.items():
            if not isinstance(service_id, str):
                raise TypeError(f"service id 必须是字符串，实际是 {type(service_id).__name__}")
            if not service_id:
                raise ValueError("service id 不能是空字符串")
            if not callable(service):
                raise TypeError(f"服务 {service_id!r} 对应的值必须是可调用对象")
            table[service_id] = service
        self._services: dict[str, ServiceCallable] = table

    def resolve(self, service_id: str) -> ServiceCallable:
        """按 id 取服务函数。

        输入：
            service_id: service 条目 id。
        输出：
            可调用对象。
        异常：
            ServiceNotAvailableError: 表里没有这个 id。
        变量：
            无。
        """
        service = self._services.get(service_id)
        if service is None:
            raise ServiceNotAvailableError(
                f"内存服务表里没有这个 id（已登记 {len(self._services)} 个）", service_id=service_id
            )
        return service

    def ids(self) -> tuple[str, ...]:
        """返回表里全部服务 id（按建立顺序，便于测试与日志）。"""
        return tuple(self._services.keys())

    def __len__(self) -> int:
        """返回服务数量。"""
        return len(self._services)
