"""Mod 加载（§15.1 生命周期：浅加载 → 全加载 → 注册 → 编译 → 初始化）。

位置：
    modload 包（引擎源码树，不是核心逻辑层）。文件与脚本都通过端口访问（P7）。

职责：
    1. scan()：在 Mod 根目录下列出全部 Mod 文件夹，浅加载各自的 mod_info.json；
    2. load(info)：全加载一个 Mod：
         - 读 data/ 下全部 .json（按文件名排序，确定性），逐个建成注册表条目；
         - 解析条目继承（extends），登记进注册表中心；
         - 编译内容（加载期静态检查：引用、表达式、结构都在这一步查完）；
         - 按 service 条目把脚本函数取出来，组成服务解析表；
         - 调用 Mod 的初始化钩子（如果配了），得到初始 State。

目录约定（写在 Mod 结构说明里）：
    <Mod 文件夹>/
        mod_info.json     元信息（浅加载只读它）
        data/*.json       注册表条目；**每个文件是一个数组**，条目自带 type
        scripts/*.py      服务脚本与初始化脚本（路径相对 Mod 文件夹）

失败口径：
    加载期任何一步出错都直接抛异常（ModInfoError / RegistryError / ContentError /
    LogicError / ScriptNotAvailableError）：Mod 数据写错要在进游戏之前就说清楚，
    不能带着坏数据跑起来（D-29 / P6）。

草案依据：
    §15.1 生命周期；§15.2 Mod 提供元信息 / 注册表 JSON / 脚本；
    §14.1 条目外壳；D-29 加载期静态检查；D-24 每次运行只加载一个 Mod；§13 顺序确定。
"""

import json
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from core.content import CompiledContent, ContentReferenceError, compile_content
from core.ports import (
    FileSystem,
    ScriptLoader,
    ScriptNotAvailableError,
    ServiceNotAvailableError,
    ServiceResolver,
)
from core.registry import (
    REGISTRY_TYPES,
    EntryFormatError,
    RegistryEntry,
    RegistryHub,
    resolve_entries,
)
from core.logic import evaluate
from core.templates import apply_system_defaults, phase_actions as phase_actions_of

from .info import ModInfo, ModInfoError
from .modules import Binding, ModuleCatalog
from .pack_info import PackInfoError
from .reference_check import check_expressions, check_imports, check_scripts

# 每个 Mod 的目录约定（写在模块文档里，也在这里集中一处，改的时候只改这里）。
INFO_FILE = "mod_info.json"
DATA_FOLDER = "data"
SCRIPTS_FOLDER = "scripts"


@dataclass(frozen=True)
class LoadedMod:
    """一个全加载完成的 Mod（引擎运行期需要的东西都在这里）。

    字段：
        info: Mod 元信息；
        hub: 注册表中心（已经登记完成、继承已解析）；
        content: 编译后的内容（静态检查已做完）；
        services: 服务解析表（service 条目 id → 可调用对象）；
        initial_state: 初始 State（纯数据树；没有初始化钩子时是空树）；
        user_functions: Mod 自己的表达式函数表（可注入 ["call", …]；可以为空）。
        pages: 游戏内界面的页（ui 条目 id → 视图函数）；空字典表示这个 Mod 没有自带界面。
        default_page: 进入游戏后默认显示哪一页（没有页面时是空字符串）。
        system_paths: 初始化时由 system 条目写进 State 的路径（加载日志用）。
        functions: **组装好的函数表**：模块提供的全限定函数 + 绑定出来的名字 + Mod 自己的函数；
            运行期表达式与视图脚本都用它（["call", 名字, …] / page_context.functions）；
        modules: 本次用到的逻辑模块（依赖在前，注册顺序也是这个顺序）；
        bindings: 解析后的绑定（能力名 → 实现 + 默认参数）；
        params: 平铺参数 = 模块默认资源路径 + Mod 自己写的 params；
            传给模块实现时优先级最低（调用点参数 > 绑定 params > 平铺 params）。
    """

    info: ModInfo
    hub: RegistryHub
    content: CompiledContent
    services: ServiceResolver
    initial_state: dict
    user_functions: dict
    pages: dict = field(default_factory=dict)
    functions: dict = field(default_factory=dict)
    modules: tuple = ()
    bindings: tuple = ()
    params: dict = field(default_factory=dict)
    default_page: str = ""
    system_paths: tuple = ()


@dataclass(frozen=True)
class ScanProblem:
    """扫描时遇到的、读不出来的 Mod 文件夹（不中断扫描，但要报出来）。

    字段：
        folder: 出问题的文件夹；
        detail: 原因说明。
    """

    folder: str
    detail: str


@dataclass(frozen=True)
class ScanResult:
    """扫描结果。

    字段：
        mods: 能正常读出的 Mod（按文件夹名排序）；
        problems: 读不出来的文件夹及其原因。
    """

    mods: tuple[ModInfo, ...] = ()
    problems: tuple[ScanProblem, ...] = ()


class ModLoader:
    """Mod 加载器。

    字段：
        _files: 文件端口；
        _scripts: 脚本端口；
        _mods_root: Mod 根目录（每个子目录是一个 Mod）。
        _module_roots: 逻辑模块的根目录列表（每个根目录下的子文件夹是一个模块）。
    """

    def __init__(
        self,
        files: FileSystem,
        scripts: ScriptLoader,
        mods_root: str,
        *,
        module_roots: Sequence[str] = (),
    ) -> None:
        """创建加载器。

        输入：
            files: 文件端口（适配器实现）；
            scripts: 脚本端口（适配器实现）；
            mods_root: 存放 Mod 文件夹的目录。
            module_roots: 逻辑模块的根目录列表；每个根目录下的子文件夹都是一个候选模块
                （见 plans/新架构细节/01_实施依据/模块与Mod格式.md）。空列表表示这个加载器不带模块。
                随引擎分发的那份内容就在这里面（pack_info.json 写 `"base": true` 的模块
                每个 Mod 都自动带上）——引擎自己不再持有任何内置内容目录。
        输出：
            无（构造对象）。
        异常：
            TypeError: 端口不满足契约。
        变量：
            无。
        """
        if not hasattr(files, "read_text") or not hasattr(files, "list_files"):
            raise TypeError("files 必须实现 FileSystem 端口（read_text / list_files / …）")
        if not hasattr(scripts, "load"):
            raise TypeError("scripts 必须实现 ScriptLoader 端口（load 方法）")
        self._files: FileSystem = files
        self._scripts: ScriptLoader = scripts
        self._mods_root: str = mods_root
        self._module_roots: tuple = tuple(root for root in module_roots if root)

    def scan(self) -> tuple[ModInfo, ...]:
        """浅加载：列出 Mod 根目录下所有能读出 mod_info.json 的 Mod。

        输入：无。
        输出：
            ModInfo 元组，按 Mod 文件夹名排序（确定性）。
        异常：
            TypeError: 端口返回值类型不对；
            ModInfoError: 某个 mod_info.json 不是合法 JSON 或字段不合法。
        变量：
            info_path / raw / data / folder: 遍历与解析的中间结果。

        说明：
            没有 mod_info.json 的文件夹直接跳过（例如顺手放的说明目录）；
            但**有文件却写错**的会报错——那属于 Mod 数据写错，不能静默忽略。
        """
        found: list[ModInfo] = []
        for folder in self._list_mod_folders():
            info_path = f"{folder}/{INFO_FILE}"
            if not self._files.exists(info_path):
                continue
            raw = self._files.read_text(info_path)
            try:
                data: Any = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ModInfoError(f"不是合法的 JSON：{exc}", path=info_path) from exc
            found.append(ModInfo.from_data(data, folder=folder, path=info_path))
        return tuple(found)

    def load_info(self, folder: str) -> ModInfo:
        """浅加载指定的一个 Mod 文件夹。

        输入：
            folder: Mod 文件夹路径。
        输出：
            ModInfo。
        异常：
            ModInfoError: 文件不存在、不是合法 JSON 或字段不合法。
        变量：
            info_path / raw / data: 中间结果。
        """
        info_path = f"{folder}/{INFO_FILE}"
        if not self._files.exists(info_path):
            raise ModInfoError(f"找不到 {INFO_FILE}", path=info_path)
        raw = self._files.read_text(info_path)
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModInfoError(f"不是合法的 JSON：{exc}", path=info_path) from exc
        return ModInfo.from_data(data, folder=folder, path=info_path)

    def try_scan(self) -> ScanResult:
        """宽松扫描：能读的读出来，读不出来的记成问题（不中断）。

        输入：无。
        输出：
            ScanResult（可用的 Mod + 读不出来的文件夹及原因）。
        异常：
            TypeError: 端口返回值类型不对。
        变量：
            mods / problems: 逐个文件夹尝试的结果；
            folder: 当前遍历到的 Mod 文件夹。

        说明：
            为什么要有宽松版：Mod 选择页不能因为"某个文件夹里躺着半个 mod_info.json"
            就整个点不动——把问题列出来让用户自己处理（谁提供的东西谁负责，§其他细节 6），
            同时原因要看得见（P6）。严格版 scan() 留给工具与测试。
        """
        mods: list[ModInfo] = []
        problems: list[ScanProblem] = []
        for folder in self._list_mod_folders():
            try:
                info = self.load_info(folder)
            except (ModInfoError, OSError) as exc:
                problems.append(ScanProblem(folder=folder, detail=str(exc)))
                continue
            mods.append(info)
        return ScanResult(mods=tuple(mods), problems=tuple(problems))

    def load(self, info: ModInfo) -> LoadedMod:
        """全加载一个 Mod：读数据 → 注册 → 编译 → 取脚本 → 初始化 State。

        输入：
            info: 该 Mod 的元信息（浅加载的产物）。
        输出：
            LoadedMod。
        异常：
            ModInfoError: 数据文件不是合法 JSON / 不是数组 / 条目不是对象；
            RegistryError / EntryFormatError / DuplicateEntryError / InheritanceError:
                条目不合法（由 registry 层抛出）；
            ContentError / LogicError: 内容结构或表达式不合法（由编译器抛出）；
            ScriptNotAvailableError: 脚本缺失或函数名写错。
        变量：
            entries: 全部条目（继承已解析）；
            catalog / modules / bindings: 逻辑模块、用到的那几个、解析后的绑定；
            hub / content / services: 注册、编译、服务解析的产物；
            initial_state: 初始化钩子的产物。
        """
        catalog = ModuleCatalog.scan(self._files, self._module_roots)
        modules = catalog.resolve_uses(info.uses)
        bindings = catalog.resolve_bindings(info.bindings, modules)
        entries, base_dirs = self._read_entries(info, modules)
        hub = RegistryHub(types=REGISTRY_TYPES)
        hub.register_all(entries)
        content = compile_content(hub)
        for index, entry in enumerate(info.menu):
            if not content.input_candidates(entry.input):
                raise ModInfoError(
                    f"menu[{index}] 指向没有声明的输入 kind：{entry.input!r}",
                    path=info.info_path,
                )
        paths = self._collect_check_paths(info, modules, hub, base_dirs)
        check_imports(self._files, paths)     # import 要先查（脚本还没被执行）
        services = self._build_services(info, hub, base_dirs)
        user_functions = self._build_user_functions(info)
        # 平铺参数 = 模块默认资源路径 + Mod 自己写的 params（资源键带模块前缀，不会撞车）。
        params = {**catalog.asset_paths(modules), **info.params}
        functions = self._build_functions(user_functions, modules, bindings, params)
        # 加载期引用核对（E-1 / 甲-8）：脚本里的字面量函数名与 import、数据里的 ["call", …]。
        check_scripts(self._files, paths, functions)
        check_expressions(content, functions)
        pages = self._build_pages(info, content, base_dirs)
        initial_state = self._build_initial_state(info, hub, content, functions, params)
        # system 条目里的初始值最后写：Mod 的初始化钩子把树搭出来，系统初值再补齐 / 覆盖
        # （这是"搭初始 State"的一部分，不是游戏中的改动，所以不产生变化量）。
        written = apply_system_defaults(content.systems, initial_state, functions=functions)
        return LoadedMod(
            info=info,
            hub=hub,
            content=content,
            services=services,
            initial_state=initial_state,
            user_functions=user_functions,
            functions=functions,
            modules=modules,
            bindings=bindings,
            params=params,
            pages=pages,
            default_page=self._pick_default_page(info, content, pages),
            system_paths=written,
        )

    def _list_mod_folders(self) -> tuple[str, ...]:
        """列出 Mod 根目录下的子目录（按名字排序，确定性）。

        输入：无。
        输出：
            子目录路径元组；根目录不存在时是空元组。
        异常：
            TypeError: 端口返回值类型不对。
        变量：
            无。
        """
        return tuple(self._files.list_dirs(self._mods_root))

    def _read_entries(self, info: ModInfo, modules: Sequence[Any] = ()) -> tuple:
        """读取模块与 Mod 的 data/*.json，建成条目（继承已解析）。

        输入：
            info: Mod 元信息；
            modules: 本次用到的模块（依赖在前；它们的 data/ 也一起读）。
        输出：
            (条目元组, 基础目录表)：顺序 = 各模块（基线内容在前，然后按依赖序）→ Mod；
            基础目录表记录"某个条目来自哪个目录"，用来解析它的脚本（模块的服务脚本
            要相对模块目录解析，Mod 的脚本相对 Mod 目录解析）。
        异常：
            ModInfoError: 文件不是合法 JSON、顶层不是数组、数组元素不是对象；
            EntryFormatError / DuplicateEntryError / InheritanceError: 条目本身不合法。
        变量：
            raw_entries / base_dirs / folders: 遍历与解析的中间结果。

        说明：
            模块先读、Mod 后读（§4.1 的注册顺序）：随引擎分发的那份内容也是模块，
            它登记的条目（例如系统事件 engine:syscall:save）因此先就位，
            Mod 与别的模块都能在自己的条目里引用它们。
        """
        raw_entries: list[RegistryEntry] = []
        base_dirs: dict = {}
        folders: list = []
        for module in modules:
            folders.append((f"{module.folder}/{DATA_FOLDER}", module.id, module.folder))
        folders.append((f"{info.folder}/{DATA_FOLDER}", info.id, info.folder))

        for folder, source_name, base_dir in folders:
            for entry in self._read_entry_folder(folder, source_name):
                base_dirs[entry.id] = base_dir
                raw_entries.append(entry)
        return resolve_entries(raw_entries), base_dirs

    def _read_entry_folder(self, data_folder: str, source_name: str) -> list[RegistryEntry]:
        """读一个 data 文件夹里的全部条目文件。

        输入：
            data_folder: 条目文件夹；
            source_name: 来源名（写进条目的 source，用于日志溯源）。
        输出：
            RegistryEntry 列表（尚未解析继承）。
        异常：
            ModInfoError: 文件不是合法 JSON / 不是数组 / 元素不是对象。
        变量：
            path / raw / data / index / item: 遍历与解析的中间结果。
        """
        entries: list[RegistryEntry] = []
        for path in self._files.list_files(data_folder, ".json"):
            raw = self._files.read_text(path)
            try:
                data: Any = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ModInfoError(f"不是合法的 JSON：{exc}", path=path) from exc
            if not isinstance(data, list):
                raise ModInfoError(
                    f"条目文件必须是一个数组（每个元素是一条条目），实际是 {type(data).__name__}",
                    path=path,
                )
            for index, item in enumerate(data):
                if not isinstance(item, dict):
                    raise ModInfoError(
                        f"数组第 {index} 项不是对象（条目必须是对象）", path=path
                    )
                entries.append(_build_entry(item, source=f"{source_name}:{_file_name(path)}"))
        return entries

    def _build_services(self, info: ModInfo, hub: RegistryHub, base_dirs: Mapping[str, str] = None) -> ServiceResolver:
        """按 service 条目把脚本函数取出来，组成服务解析表。

        输入：
            info: Mod 元信息；
            hub: 注册表中心；
            base_dirs: 条目 id → 它来自哪个目录（模块的服务脚本要相对模块目录解析）。
        输出：
            服务解析表（adapters.MappingServiceResolver 那样的 Mapping 实现）。
        异常：
            ModInfoError: service 条目缺少 script / callable，或类型不对；
            ScriptNotAvailableError: 取不到脚本函数。
        变量：
            table / entry / script / name: 遍历与组装的中间结果。

        说明：
            这里**在加载期一次性取全部脚本函数**（饿加载）：脚本写错、函数名拼错
            会在进游戏之前就报出来（P6 / D-29）。以后脚本很多时可以改成用到才取。
        """
        base_dirs = base_dirs or {}
        table: dict[str, object] = {}
        for entry in hub.table("service").enabled():
            script = entry.data.get("script")
            name = entry.data.get("callable", "run")
            if not isinstance(script, str) or not script:
                raise ModInfoError(
                    f"service {entry.id!r} 缺少 script（脚本路径）", path=info.info_path
                )
            if not isinstance(name, str) or not name:
                raise ModInfoError(
                    f"service {entry.id!r} 的 callable 必须是非空字符串", path=info.info_path
                )
            full_path = f"{base_dirs.get(entry.id, info.folder)}/{script}"
            try:
                table[entry.id] = self._scripts.load(full_path, name)
            except ScriptNotAvailableError as exc:
                raise ScriptNotAvailableError(
                    f"service {entry.id!r} 的脚本取不到：{exc.detail}", script_path=full_path
                ) from exc
        return _MappingResolver(table)

    def _collect_check_paths(self, info: ModInfo, modules: Sequence[Any], hub: RegistryHub,
                             base_dirs: Mapping[str, str]) -> list[str]:
        """列出"加载期引用核对"要看的脚本：模块能力脚本 + service / ui 条目脚本 + Mod 的 scripts/*.py。

        输入：
            info / modules / hub / base_dirs: 加载过程已有的东西。
        输出：
            脚本路径列表（可能重复，核对那边会去重）。
        异常：
            无。
        变量：
            paths / module / capability / entry: 遍历的中间结果。
        """
        paths: list[str] = []
        for module in modules:
            for capability in module.capabilities.values():
                paths.append(f"{capability.folder}/{capability.script}")
        for entry_type in ("service", "ui"):
            for entry in hub.table(entry_type).enabled():
                script = entry.data.get("script")
                if isinstance(script, str) and script:
                    paths.append(f"{base_dirs.get(entry.id, info.folder)}/{script}")
        scripts_folder = f"{info.folder}/{SCRIPTS_FOLDER}"
        if self._files.exists(scripts_folder):
            paths.extend(self._files.list_files(scripts_folder, ".py"))
        return paths

    def _build_user_functions(self, info: ModInfo) -> dict:
        """按 mod_info.json 的 functions 把 Mod 自己的表达式函数取出来。

        输入：
            info: Mod 元信息（functions 字段：名字 → "脚本路径:函数名"）。
        输出：
            函数表：名字 → 可调用对象；没声明时是空字典。
        异常：
            ScriptNotAvailableError: 脚本或函数名取不到。
        变量：
            table / name / declaration / script / callable_name: 中间结果。

        说明：
            这些函数在表达式里用 ["call", 名字, …] 调用，例如演示 Mod 的 adjacent / distance
            就是包了一层的几何函数（§6.7"几何属于核心之外、供 Mod 声明调用"）。
            **不再占用 formula 注册表**：formula 现在专管派生值公式（§6.5）。
        """
        table: dict = {}
        for name, declaration in info.functions.items():
            script, _, callable_name = declaration.partition(":")
            full_path = f"{info.folder}/{script}"
            try:
                table[name] = self._scripts.load(full_path, callable_name)
            except ScriptNotAvailableError as exc:
                raise ScriptNotAvailableError(
                    f"functions[{name!r}] 的脚本取不到：{exc.detail}", script_path=full_path
                ) from exc
        return table

    def _build_functions(
        self,
        user_functions: Mapping[str, Any],
        modules: Sequence[Any],
        bindings: Sequence[Binding],
        params: Mapping[str, Any],
    ) -> dict:
        """组装运行期函数表：模块的全限定函数 + 绑定出来的名字 + Mod 自己的函数。

        输入：
            user_functions: Mod 自己声明的函数（名字 → 可调用对象）；
            modules: 本次用到的模块（它们的每个变体都会以全限定名登记进来）；
            bindings: 解析后的绑定（能力名 → 实现 + 默认参数）；
            params: 平铺参数（模块默认资源路径 + Mod 的 params）。
        输出：
            函数表：名字 → 可调用对象。三类名字同时在表里：
                - 全限定名（`<模块id>.<能力>.<变体>`）：模块直接提供的实现，任何 Mod 都能用；
                - 绑定名（`bindings` 的键）：把实现与默认参数绑好的"偏函数"；
                - Mod 自己的函数名：逃生舱。
        异常：
            ScriptNotAvailableError: 模块脚本或函数名取不到；
            PackInfoError: 绑定名与 Mod 自己的函数重名（默认拒绝，不做隐式覆盖）。
        变量：
            table / module / capability / variant / implementation: 组装过程的中间结果。

        参数优先级（D-2）：调用点参数 > 绑定 params > 平铺 params。
        实现方式：绑定时做一个包装器，把"平铺 + 绑定"两级参数按签名过滤后追加成关键字参数；
        调用点自己给的关键字参数最后覆盖（也就是优先级最高）。
        """
        table: dict = dict(user_functions)
        # 三类名字共用一个命名空间（R5-9）：Mod 函数 / 模块全限定名 / 绑定名，三对组合都要查重，
        # 不然会静默覆盖——作者以为注册生效，或者模块自己的脚本拿到被顶替的实现。
        qualified_names = {
            capability.qualified(variant)
            for module in modules
            for capability in module.capabilities.values()
            for variant in capability.variants
        }
        binding_names = {binding.name for binding in bindings}
        for first, first_names, second, second_names in (
            ("Mod 的 functions", set(user_functions), "模块的全限定名", qualified_names),
            ("绑定名", binding_names, "模块的全限定名", qualified_names),
        ):
            overlap = sorted(first_names & second_names)
            if overlap:
                raise PackInfoError(
                    f"{first} 与{second}撞名：{overlap}；"
                    "三者共用一张函数表，撞名会静默覆盖——请改名（要覆盖模块实现就显式写清楚）"
                )
        for binding in bindings:
            if binding.name in user_functions:
                raise PackInfoError(
                    f"bindings[{binding.name!r}] 与 Mod 自己的 functions 重名："
                    "默认拒绝（要做覆盖必须显式改名）"
                )

        # 一、模块提供的全限定名：任何 Mod 都能直接 ["call", "模块id.能力.变体", …]
        for module in modules:
            for capability in module.capabilities.values():
                for variant in capability.variants:
                    qualified = capability.qualified(variant)
                    table[qualified] = self._load_capability_function(
                        capability, variant, what=qualified
                    )

        # 二、绑定名：把实现与默认参数绑成"偏函数"
        for binding in bindings:
            implementation = self._load_capability_function(
                binding.capability, binding.variant, what=binding.use
            )
            table[binding.name] = _bind_params(implementation, params, binding.params)
        return table

    def _load_capability_function(self, capability: Any, variant: str, *, what: str):
        """按一条绑定取出实现函数。

        输入：
            capability: 能力（pack_info.json 里声明的一条）；
            variant: 变体名；
            what: 全限定名，仅用于报错。
        输出：
            模块脚本里的可调用对象。
        异常：
            ScriptNotAvailableError: 脚本或函数名取不到。
        变量：
            full_path / callable_name: 中间结果。
        """
        full_path = capability.script_path()
        callable_name = capability.callable_name(variant)
        try:
            return self._scripts.load(full_path, callable_name)
        except ScriptNotAvailableError as exc:
            raise ScriptNotAvailableError(
                f"模块实现 {what!r} 的脚本取不到：{exc.detail}", script_path=full_path
            ) from exc

    def _build_initial_state(
        self,
        info: ModInfo,
        hub: RegistryHub,
        content: CompiledContent,
        functions: Mapping[str, Any] = None,
        params: Mapping[str, Any] = None,
    ) -> dict:
        """调用 Mod 的初始化钩子，得到初始 State。

        输入：
            info: Mod 元信息；
            hub: 注册表中心；
            content: 编译后的内容。
        输出：
            初始 State（必须是 dict 根）；没有配 init 时是空字典。
        异常：
            ModInfoError: init 写法不对、函数返回的不是 dict；
            ScriptNotAvailableError: 取不到初始化脚本。
        变量：
            parts / script_path / function / result: 中间结果。

        说明：
            **State 树由 Mod 设计**（D-09），引擎不可能知道该往树里放什么，
            所以初始 State 由 Mod 的初始化钩子给（§15.2 的"初始化钩子"）。
            钩子的签名：build_state(mod) -> dict，mod 是一个 ModContext，里面有
            注册表中心、编译内容、文件端口与 Mod 根目录。
        """
        parts = info.init_parts()
        if parts is None:
            return {}
        script, name = parts
        full_path = f"{info.folder}/{script}"
        function = self._scripts.load(full_path, name)
        context = ModContext(
            info=info, hub=hub, content=content, files=self._files,
            functions=functions or {}, params=params or {},
        )
        result = function(context)
        if not isinstance(result, dict):
            raise ModInfoError(
                f"初始化钩子必须返回 dict（State 树的根），实际是 {type(result).__name__}",
                path=info.info_path,
            )
        return result

    def _build_pages(self, info: ModInfo, content: CompiledContent, base_dirs: Mapping[str, str] = None) -> dict:
        """按 ui 条目把游戏内界面的视图函数取出来。

        输入：
            info: Mod 元信息；
            content: 编译后的内容（page 定义在这里）。
        输出：
            {ui 条目 id: 视图函数}；没有 ui 条目时是空字典。
        异常：
            ScriptNotAvailableError: 视图脚本取不到。
        变量：
            page / full_path / function: 遍历与加载的中间结果。

        说明：
            视图函数签名：build_view(page_context) -> View（见 core.ui.PageCallable）；
            与 service 一样在加载期一次性取出来（脚本写错要在进游戏前就报）。
        """
        pages: dict = {}
        base_dirs = base_dirs or {}
        for page_id, page in content.pages.items():
            full_path = f"{base_dirs.get(page_id, info.folder)}/{page.script}"
            try:
                function = self._scripts.load(full_path, page.callable_name)
            except ScriptNotAvailableError as exc:
                raise ScriptNotAvailableError(
                    f"ui {page_id!r} 的视图脚本取不到：{exc.detail}", script_path=full_path
                ) from exc
            pages[page_id] = function
        return pages

    def _pick_default_page(self, info: ModInfo, content: CompiledContent, pages: dict) -> str:
        """决定进入游戏后默认显示哪一页。

        输入：
            info: Mod 元信息（可能写了 ui 字段）；
            content: 编译后的内容（用来校验写的 id 是否存在）；
            pages: 已经取到视图函数的页面表。
        输出：
            默认页面 id；这个 Mod 没有页面时是空字符串。
        异常：
            ModInfoError: mod_info.json 里写的 ui 条目不存在。
        变量：
            无。

        说明：
            mod_info.json 没写 ui 时取**第一个 ui 条目**（按注册顺序），
            这样简单的 Mod 完全不必关心这件事。
        """
        if not pages:
            return ""
        if info.page_id:
            if info.page_id not in pages:
                raise ModInfoError(
                    f"mod_info.json 里的 ui 指向了不存在的页面：{info.page_id!r}"
                    f"（可用的有 {sorted(pages)}）",
                    path=info.info_path,
                )
            return info.page_id
        return next(iter(pages))


@dataclass(frozen=True)
class ModContext:
    """交给 Mod 初始化钩子的上下文。

    字段：
        info: 本 Mod 的元信息（含 folder，用来定位资源）；
        hub: 注册表中心（可以读自己写的 entity / node 等条目）；
        content: 编译后的内容（可以读命令 / 动作定义）；
        files: 文件端口（读 Mod 自己的数据文件）；
        functions: 加载期组装好的函数表（模块实现 / 绑定 / Mod 函数）——
            初始化钩子用它调用模块函数（例如"按坐标算相邻关系表"），
            这样 Mod 连"建地图"都不必自己写几何；
        params: 平铺参数（模块默认资源 + Mod 的 params），钩子里也能读。
    """

    info: ModInfo
    hub: RegistryHub
    content: CompiledContent
    files: FileSystem
    functions: Mapping[str, Any] = field(default_factory=dict)
    params: Mapping[str, Any] = field(default_factory=dict)

    def template_values(self, template_id: str, reader: Any = None, *, functions=None) -> dict:
        """把 entity / node 模板的属性算成具体值（初始化时建实例用）。

        输入：
            template_id: 模板条目 id；
            reader: 可选的读取入口（模板属性里若引用了 State，就靠它读）；
            functions: 可选的外部函数表；**缺省用加载期组装好的那一份**（self.functions）。
                这个默认值很关键：模板属性里写 ["call", …] 时，不传就该用 Mod 自己的函数表，
                而不是空表（N-2 / R6-3 同族）。
        输出：
            属性名 → 具体值 的新字典。
        异常：
            KeyError: 没有这个模板；
            LogicError / state 层异常: 属性表达式算不出来。
        变量：
            无。
        """
        from core.state.values import snapshot_value   # 加载层只在这里用一次

        template = self.content.templates[template_id]
        table = self.functions if functions is None else functions
        return {
            name: snapshot_value(evaluate(expression, reader, functions=table))
            for name, expression in template.attributes.items()
        }

    def phase_actions(self, phase_id: str) -> tuple:
        """取一个阶段允许的动作 id（初始化或脚本里判断"现在能做哪些事"）。

        输入：
            phase_id: phase 条目 id。
        输出：
            动作 id 元组。
        异常：
            KeyError: 没有这个阶段。
        变量：
            无。
        """
        return phase_actions_of(self.content, phase_id)


class _MappingResolver:
    """把一张内存字典当服务解析表（与 adapters.MappingServiceResolver 同口径）。

    字段：
        _table: service 条目 id → 可调用对象。
    """

    def __init__(self, table: dict) -> None:
        """创建解析表。

        输入：
            table: id → 可调用对象。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self._table = dict(table)

    def resolve(self, service_id: str):
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
        service = self._table.get(service_id)
        if service is None:
            raise ServiceNotAvailableError(
                f"服务表里没有这个 id（已登记 {len(self._table)} 个）", service_id=service_id
            )
        return service


def _build_entry(item: dict, *, source: str) -> RegistryEntry:
    """从 JSON 对象建一条注册表条目（引擎自动填 source）。

    输入：
        item: 条目对象；
        source: 来源（Mod id + 文件路径），用于日志溯源（D-36）。
    输出：
        RegistryEntry。
    异常：
        EntryFormatError / TypeError: 字段不合法（由 RegistryEntry 自己检查）。
    变量：
        fields: 传给 RegistryEntry 的字段。
    """
    fields = dict(item)
    fields.setdefault("metadata", {})
    fields["source"] = source
    try:
        return RegistryEntry(**fields)
    except TypeError as exc:      # 条目顶层键写错：包成内容错误，带上 id / 来源（R5-6）
        raise EntryFormatError(
            f"条目的顶层键不对：{exc}（条目 {fields.get('id')!r}，来自 {fields.get('source')!r}）",
            entry_id=str(fields.get('id', "")),
        ) from exc


def _file_name(path: str) -> str:
    """取路径里的文件名（写进条目的 source，只用于日志溯源）。"""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _bind_params(
    function: Callable[..., Any],
    base_params: Mapping[str, Any],
    binding_params: Mapping[str, Any],
) -> Callable[..., Any]:
    """把两级默认参数绑到一个实现函数上，返回"偏函数"。

    输入：
        function: 模块提供的实现（纯函数）；
        base_params: 平铺参数（优先级最低：模块默认资源 + Mod 的 params）；
        binding_params: 这条绑定附带的参数（优先级居中）。
    输出：
        包装后的可调用对象：调用时把两级参数按**实现声明的参数名**过滤后追加成关键字参数，
        调用点自己给的关键字参数最后覆盖（优先级最高）。
    异常：
        PackInfoError: **绑定参数**里有实现不认识的键（见下面说明）。
    变量：
        accepted: 实现接受的参数名集合；
        merged: 合并后的默认参数。

    说明：
        为什么要按签名过滤：平铺参数是"整个 Mod 的一堆键"（路径、数值、资源路径），
        实现当然只关心其中几个。不过滤的话，任何一个多余的键都会让调用抛 TypeError；
        过滤之后，模块作者只要在实现上声明自己需要的参数（带默认值）即可，
        Mod 则把自己那套事实平铺出来，两边互不打扰。
        **平铺参数**照旧过滤（它本来就要被所有实现共享）；但**绑定参数**是给这一个实现写的，
        键名写错只会在运行期以"莫名其妙走了默认路径"的形式出现（甲-3），所以在加载期直接报错。
    """
    try:
        signature = inspect.signature(function)
        accepted = set(signature.parameters)
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
    except (TypeError, ValueError):  # 拿不到签名（例如内置函数）时不过滤，按原样透传
        accepted = None
        accepts_kwargs = True
    if accepted is not None and not accepts_kwargs:
        unknown = sorted(key for key in binding_params if key not in accepted)
        if unknown:
            raise PackInfoError(
                f"绑定参数里有实现不认识的键 {unknown}；"
                f"{function.__name__} 的参数是 {sorted(accepted)}"
                "（绑定参数是给这一个实现写的，写错就报错；平铺 params 仍然照旧过滤）"
            )

    def bound(*args: Any, **kwargs: Any) -> Any:
        """按优先级合并参数并调用实现。"""
        merged: dict = {}
        merged.update(base_params)
        merged.update(binding_params)
        if accepted is not None:
            merged = {name: value for name, value in merged.items() if name in accepted}
        merged.update(kwargs)
        return function(*args, **merged)

    return bound
