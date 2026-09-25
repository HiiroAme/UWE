"""modload 包：Mod 加载层（§15 生命周期）。

位置：
    引擎源码树，但**不是核心逻辑层**：它读文件、import 脚本，天生与外部环境打交道。
    它使用的每一件事都走端口（FileSystem / ScriptLoader），所以核心仍然干净（P7）。

生命周期（§15.1）：
    浅加载（只读 mod_info.json，用于列表页）
        → 全加载（读 data/*.json 与脚本）
        → 注册（登记进注册表中心）
        → 编译（内容编译 + 加载期静态检查）
        → 初始化（调用 Mod 的初始化钩子，得到初始 State）
    卸载 / 热重载本阶段不做（§15.4）。

文件分工：
    info.py       ModInfo：mod_info.json 的数据与校验（含 uses / bindings / params）
    pack_info.py  ModuleInfo：pack_info.json（模块身份、能力 / 变体、基线标记 base）
    modules.py    ModuleCatalog：扫模块根，解析 uses / bindings 与模块资源路径
    loader.py     ModLoader：扫描、浅加载、全加载（返回 LoadedMod）

草案依据：
    §15.1 生命周期与启动流程；§15.2 Mod 提供元信息 / 注册表 JSON / 资源 / 脚本 / UI；
    §14 注册表体系；§6.2 State 由 Mod 设计；D-24 每次运行只加载一个 Mod。
"""

from .info import ModInfo
from .loader import LoadedMod, ModLoader, ScanProblem, ScanResult

__all__ = ["ModInfo", "ModLoader", "LoadedMod", "ScanProblem", "ScanResult"]
