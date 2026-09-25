"""模块目录（ModuleCatalog）：扫模块根、按 id 建索引、解析 Mod 的 uses / bindings。

位置：
    modload 包（Mod 加载层）。引擎只认"一串模块根目录"，不认具体模块（P1）。

职责：
    1. scan()：把每个模块根目录下的子文件夹当作候选模块，读 pack_info.json；
    2. 校验：模块 id 全局唯一（两个根目录里同 id 直接拒绝）、依赖存在、版本满足；
    3. 解析 Mod 的 uses（声明用到哪些模块）与 bindings（能力 → 选哪个实现 + 默认参数）；
    4. 把模块的资源解析成实际路径（模块目录 + 相对路径），键为 `<模块id>/<资源键>`。

基线模块（pack_info.json 里写 `"base": true`）：
    每个 Mod 都自动带上、不用写进 uses 的那一份内容（例如系统事件登记、通用工具服务）。
    引擎只认"base 这个标记"，不认识任何具体模块名——把内置内容从引擎里踢出去之后，
    随引擎分发的那份内容就是靠这个标记挂进每次加载的。

依赖与顺序（本版最小实现）：
    requires 只做"存在 + 版本满足"的检查与递归收集；加载顺序按"依赖先、被依赖后"的拓扑序，
    保证模块的 data/ 条目先于依赖它的模块注册。

草案依据：
    plans/新架构细节/01_实施依据/模块与Mod格式.md（模块根、uses/bindings/params、冲突默认拒绝）；
    P1-3 内置内容本质上属于 Mod（模块同理）；D-25 版本不匹配直接拒绝加载。
"""

from dataclasses import dataclass
from typing import Mapping, Sequence

from .pack_info import ModuleInfo, PackInfoError, parse_module_info


def parse_version(text: str) -> tuple:
    """把版本号字符串解析成可比较的三元组。

    输入：
        text: 形如 "0.0.0" / "1.2" 的字符串。
    输出：
        (主, 次, 修订) 三元组；缺的部分补 0。
    异常：
        PackInfoError: 写法不认识（不是数字点分）。
    变量：
        parts: 切出来的数字段。
    """
    if not isinstance(text, str) or not text:
        raise PackInfoError(f"版本号必须是非空字符串，实际是 {text!r}")
    parts = text.split(".")
    numbers = []
    for part in parts:
        if not part.isdigit():
            raise PackInfoError(f"版本号只能由数字和点组成，实际是 {text!r}")
        numbers.append(int(part))
    while len(numbers) < 3:
        numbers.append(0)
    if len(numbers) > 3:
        raise PackInfoError(
            f"版本号最多三段（x.y.z），实际是 {'.'.join(str(n) for n in numbers)}"
            "（多写的段以前会被静默丢掉，写错也算满足约束）",
        )
    return tuple(numbers[:3])


def version_satisfies(version: str, constraint: str) -> bool:
    """判断版本是否满足约束。

    输入：
        version: 实际版本（如 "0.0.0"）；
        constraint: 约束，支持 ""（任意）、精确版本、"≥>=x.y.z"（写成 ">=x.y.z"）。
    输出：
        True / False。
    异常：
        PackInfoError: 版本或约束写法不认识。
    变量：
        actual / wanted: 解析出来的版本三元组。
    """
    actual = parse_version(version)
    text = (constraint or "").strip()
    if not text:
        return True
    if text.startswith(">="):
        wanted = parse_version(text[2:].strip())
        return actual >= wanted
    return actual == parse_version(text)


@dataclass(frozen=True)
class Binding:
    """一条绑定：能力名 → 选定的实现 + 默认参数。

    字段：
        name: 绑定键（Mod 自己起的名字，引用时就用它，例如 "damage"）；
        use: 全限定实现名（`<模块id>.<能力>.<变体>`）；
        params: 这条绑定附带的默认参数（调用点参数优先于它）；
        module: 提供该实现的模块；
        capability: 能力；
        variant: 变体名。
    """

    name: str
    use: str
    params: dict
    module: ModuleInfo
    capability: object
    variant: str


@dataclass
class ModuleCatalog:
    """模块目录：所有已扫描到的模块。

    字段：
        modules: 模块 id → ModuleInfo（保持扫描顺序）。
    """

    modules: dict

    @classmethod
    def scan(cls, files, roots: Sequence[str]) -> "ModuleCatalog":
        """扫描模块根目录，建立模块目录。

        输入：
            files: 文件端口；
            roots: 模块根目录列表（每个根目录下的子文件夹都是候选模块）。
        输出：
            ModuleCatalog（模块按"根目录顺序 + 文件夹名字典序"排列）。
        异常：
            PackInfoError: 某个模块的 pack_info.json 不合法，或同一个模块 id 出现在多个地方。
        变量：
            folder / module: 遍历到的模块目录与解析结果。
        """
        found: dict = {}
        for root in roots:
            if not root:
                continue
            for folder in files.list_dirs(root):
                if not files.exists(f"{folder}/pack_info.json"):
                    continue  # 不是模块（允许根目录里放说明文件与别的目录）
                module = parse_module_info(files, folder)
                if module.id in found:
                    raise PackInfoError(
                        f"模块 id 重复：{module.id!r} 同时出现在 {found[module.id].folder!r} "
                        f"与 {module.folder!r}（本版默认拒绝，不做隐式覆盖）",
                        path=module.info_path,
                    )
                found[module.id] = module
        return cls(modules=found)

    def __len__(self) -> int:
        """返回模块数量。"""
        return len(self.modules)

    def ids(self) -> tuple:
        """返回全部模块 id（扫描顺序）。"""
        return tuple(self.modules)

    def base_modules(self) -> tuple:
        """返回全部基线模块（pack_info.json 里写了 `"base": true` 的），按扫描顺序。

        输入：无。
        输出：
            ModuleInfo 元组。
        异常：
            无。
        变量：
            module: 遍历到的模块。
        """
        return tuple(module for module in self.modules.values() if module.base)

    def get(self, module_id: str) -> ModuleInfo:
        """按 id 取模块。

        输入：
            module_id: 模块 id。
        输出：
            ModuleInfo。
        异常：
            PackInfoError: 没有这个模块（报错里列出可用的）。
        变量：
            无。
        """
        found = self.modules.get(module_id)
        if found is None:
            raise PackInfoError(
                f"找不到模块 {module_id!r}（可用：{sorted(self.modules)}）"
            )
        return found

    def resolve_uses(self, uses: Sequence[str]) -> tuple:
        """解析 Mod 的 uses 声明，按"依赖先"的顺序返回要用到的模块。

        输入：
            uses: 声明项列表；每项是 "模块id" 或 "模块id>=x.y.z"。
        输出：
            模块元组（依赖在前，保证先注册）；**基线模块永远排在最前**，
            所以 Mod 就算不写 uses 也能引用它们登记的条目与服务。
        异常：
            PackInfoError: 语法不对、模块不存在、版本不满足、或依赖链成环。
        变量：
            order / visiting / seen: 拓扑排序用的中间结果；
            item: 当前声明项。
        """
        order: list = []
        seen: set = set()
        visiting: set = set()

        def visit(module: ModuleInfo, constraint: str, where: str) -> None:
            """递归收集一个模块及其依赖（依赖先入队）。"""
            if not version_satisfies(module.version, constraint):
                raise PackInfoError(
                    f"{where} 要求的版本约束 {constraint or '（任意）'!r} 不满足："
                    f"{module.id} 实际是 {module.version}",
                    path=module.info_path,
                )
            if module.id in seen:
                return
            if module.id in visiting:
                raise PackInfoError(f"模块依赖成环：{module.id!r}", path=module.info_path)
            visiting.add(module.id)
            for dependency_id, dependency_constraint in module.requires.items():
                dependency = self.get(dependency_id)
                visit(dependency, str(dependency_constraint), f"模块 {module.id}")
            visiting.discard(module.id)
            seen.add(module.id)
            order.append(module)

        for module in self.base_modules():
            visit(module, "", "基线内容")
        for item in uses:
            module_id, constraint = _split_use(item)
            visit(self.get(module_id), constraint, "uses")
        return tuple(order)

    def resolve_bindings(self, bindings: Mapping, modules: Sequence[ModuleInfo]) -> tuple:
        """解析 Mod 的 bindings 声明。

        输入：
            bindings: 绑定表（能力名 → {"use": 全限定名, "params": {...}} 或字符串）；
            modules: 本次用到的模块（用于检查"绑定的实现属于已声明的模块"）。
        输出：
            Binding 元组（按声明顺序）。
        异常：
            PackInfoError: 写法不对、全限定名解析不了、模块没在 uses 里、变体不存在。
        变量：
            name / spec / use: 遍历与解析的中间结果。
        """
        allowed = {module.id for module in modules}
        resolved: list = []
        for name, spec in bindings.items():
            if not isinstance(name, str) or not name:
                raise PackInfoError(f"bindings 的键必须是非空字符串，实际是 {name!r}")
            params: dict = {}
            if isinstance(spec, dict):
                use = spec.get("use")
                params = spec.get("params", {}) or {}
                if not isinstance(params, dict):
                    raise PackInfoError(f"bindings[{name!r}].params 必须是对象")
            else:
                use = spec
            if not isinstance(use, str) or not use:
                raise PackInfoError(f"bindings[{name!r}] 必须写 use（全限定实现名）")
            module_id, capability_name, variant = _split_qualified(use)
            if module_id not in allowed:
                raise PackInfoError(
                    f"bindings[{name!r}] 用了模块 {module_id!r}，但它没写在 uses 里"
                )
            module = self.get(module_id)
            capability = module.capability(capability_name)
            capability.qualified(variant)  # 变体不存在时在这里报错
            resolved.append(
                Binding(
                    name=name,
                    use=use,
                    params=dict(params),
                    module=module,
                    capability=capability,
                    variant=variant,
                )
            )
        return tuple(resolved)

    def asset_paths(self, modules: Sequence[ModuleInfo]) -> dict:
        """把本次用到的模块的默认资源解析成路径表。

        输入：
            modules: 本次用到的模块。
        输出：
            资源表：`"<模块id>/<资源键>"` → 实际路径。
        异常：
            无。
        变量：
            module / key: 遍历时的模块与资源键。
        """
        table: dict = {}
        for module in modules:
            for key in module.assets:
                table[f"{module.id}/{key}"] = module.asset_path(key)
        return table


def _split_use(item: str) -> tuple:
    """把 uses 里的一项拆成 (模块id, 版本约束)。

    输入：
        item: "pack_x" 或 "pack_x>=0.0.0"。
    输出：
        (模块id, 约束字符串)。
    异常：
        PackInfoError: 写法不对。
    变量：
        head / _, tail: 拆分结果。
    """
    if not isinstance(item, str) or not item:
        raise PackInfoError(f"uses 的每一项都必须是非空字符串，实际是 {item!r}")
    head, separator, tail = item.partition(">=")
    if separator:
        return head.strip(), f">={tail.strip()}"
    return item.strip(), ""


def _split_qualified(use: str) -> tuple:
    """把全限定实现名拆成 (模块id, 能力, 变体)。

    输入：
        use: 形如 "pack_combat_basic.damage.dice"。
    输出：
        (模块id, 能力名, 变体名)。
    异常：
        PackInfoError: 分段数量不对。
    变量：
        parts: 按点切出来的段。
    """
    parts = use.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise PackInfoError(
            f"实现名必须写成「模块id.能力.变体」，实际是 {use!r}"
        )
    return parts[0], parts[1], parts[2]
