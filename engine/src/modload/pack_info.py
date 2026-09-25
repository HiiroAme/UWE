"""逻辑模块的身份与能力声明（pack_info.json，见 plans/新架构细节/01_实施依据/模块与Mod格式.md）。

位置：
    modload 包（Mod 加载层）。模块与 Mod 走同一条加载路径，差别只在"入口文件叫什么、
    里面声明什么"：Mod 声明"这是一局游戏"，模块声明"我提供哪些能力"。

职责：
    读取并校验 pack_info.json：
        - 身份三元组：id（唯一、永不改名）、文件夹名（从哪读）、name（称呼名）；
        - 基线声明（base）：这一份模块是否"每个 Mod 都自动带上"；
        - 版本与依赖（requires）；
        - 能力表：能力名 → 实现方式（纯函数 / 服务）、脚本、变体、默认变体；
        - 默认资源（assets）：资源键 → 模块内相对路径。

约定（与规格文档一致）：
    variants 写成"变体名 → 脚本里的函数名"的映射，例如
        "variants": {"hex": "neighbors_hex", "square": "neighbors_square"}
    函数名必须显式写出（P4）：一个脚本里有多个同名概念时不会互相撞，
    模块作者也能一眼看出"变体名"与"实际函数名"的对应。

草案依据：
    §14.2 service / formula 条目"引用脚本用路径或名字"；§15.2 Mod 提供脚本；
    P1-3 内置内容本质上属于 Mod（模块同理，只是加载路径不同）；P4 显式优于隐式。
"""

import json
from dataclasses import dataclass, field
from typing import Any

# 模块目录里的固定文件名与子目录（与 Mod 同构）。
PACK_FILE = "pack_info.json"
DATA_FOLDER = "data"
SCRIPTS_FOLDER = "scripts"

# 能力的两类实现方式。
CAPABILITY_KINDS: tuple[str, ...] = ("function", "service")

# 模块类别：module（给实现） / preset（只给引用与参数）。
PACK_KINDS: tuple[str, ...] = ("module", "preset")


class PackInfoError(Exception):
    """pack_info.json 不合法（或引用了不存在的东西）。

    字段：
        detail: 说明文字；
        path: 出问题的文件 / 模块目录。
    """

    def __init__(self, detail: str, *, path: str = "") -> None:
        """构造异常。"""
        self.detail = detail
        self.path = path
        super().__init__(f"PackInfoError | {path} | {detail}" if path else f"PackInfoError | {detail}")


@dataclass(frozen=True)
class Capability:
    """一个能力（模块对外提供的"一件事"）。

    字段：
        module_id: 所属模块 id（同时是命名空间）；
        name: 能力名（例如 damage / neighbors）；
        kind: "function"（纯函数）或 "service"（会改数据）；
        script: 实现脚本（相对模块目录）；
        variants: 变体名 → 脚本里的函数名；
        default: 默认变体名（缺省取唯一变体）；
        folder: 模块目录（解析脚本用）。
    """

    module_id: str
    name: str
    kind: str
    script: str
    variants: dict
    default: str
    folder: str

    def qualified(self, variant: str) -> str:
        """返回全限定名：`<模块id>.<能力>.<变体>`。

        输入：
            variant: 变体名。
        输出：
            全限定名字符串。
        异常：
            PackInfoError: 这个能力没有该变体。
        变量：
            无。
        """
        if variant not in self.variants:
            raise PackInfoError(
                f"能力 {self.name!r} 没有变体 {variant!r}（可用：{sorted(self.variants)}）",
                path=self.module_id,
            )
        return f"{self.module_id}.{self.name}.{variant}"

    def callable_name(self, variant: str) -> str:
        """返回某变体在脚本里的函数名。"""
        return self.variants[variant]

    def script_path(self) -> str:
        """返回实现脚本的完整路径（模块目录 + 相对路径）。"""
        return f"{self.folder}/{self.script}"


@dataclass(frozen=True)
class ModuleInfo:
    """一个逻辑模块（或预设）的元信息与能力表。

字段：
    id / name / version: 身份三元组里的 id 与称呼名，加上版本；
    folder: 模块目录（文件夹名，可以不等于 id）；
    kind: "module" 或 "preset"；
    base: 是否是基线模块（True 表示任何 Mod 都自动带上它，不用写进 uses）；
    requires: 依赖的其他模块（id → 版本约束）；
    capabilities: 能力名 → Capability；
    assets: 资源键 → 模块内相对路径；
    info_path: pack_info.json 的路径（报错与日志用）。
    """

    id: str
    name: str
    version: str
    folder: str
    kind: str = "module"
    base: bool = False
    requires: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    assets: dict = field(default_factory=dict)
    info_path: str = ""

    def capability(self, name: str) -> Capability:
        """按名字取能力。

        输入：
            name: 能力名。
        输出：
            Capability。
        异常：
            PackInfoError: 这个模块没有该能力。
        变量：
            无。
        """
        found = self.capabilities.get(name)
        if found is None:
            raise PackInfoError(
                f"模块 {self.id!r} 没有能力 {name!r}（可用：{sorted(self.capabilities)}）",
                path=self.info_path,
            )
        return found

    def asset_path(self, key: str) -> str:
        """按资源键取资源路径。

        输入：
            key: 资源键（pack_info.json 的 assets 里的名字）。
        输出：
            实际路径（模块目录 + 相对路径）。
        异常：
            PackInfoError: 这个模块没有该资源。
        变量：
            无。
        """
        relative = self.assets.get(key)
        if relative is None:
            raise PackInfoError(
                f"模块 {self.id!r} 没有资源 {key!r}（可用：{sorted(self.assets)}）",
                path=self.info_path,
            )
        return f"{self.folder}/{relative}"


def parse_module_info(files, folder: str) -> ModuleInfo:
    """读取并校验一个模块目录里的 pack_info.json。

    输入：
        files: 文件端口；
        folder: 模块目录。
    输出：
        ModuleInfo。
    异常：
        PackInfoError: 文件缺失 / 不是合法 JSON / 字段不合法。
    变量：
        info_path / raw / data: 读取与解析的中间结果。
    """
    info_path = f"{folder}/{PACK_FILE}"
    if not files.exists(info_path):
        raise PackInfoError(f"找不到 {PACK_FILE}", path=folder)
    raw = files.read_text(info_path)
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PackInfoError(f"不是合法的 JSON：{exc}", path=info_path) from exc
    if not isinstance(data, dict):
        raise PackInfoError(f"{PACK_FILE} 必须是一个对象", path=info_path)

    known = {"id", "name", "version", "kind", "base", "author", "description",
             "requires", "assets", "capabilities"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise PackInfoError(
            f"{PACK_FILE} 里有不认识的键 {unknown}；允许的键是 {sorted(known)}",
            path=info_path,
        )
    module_id = _require_str(data, "id", path=info_path)
    name = _require_str(data, "name", path=info_path)
    version = _require_str(data, "version", path=info_path)
    kind = data.get("kind", "module")
    if kind not in PACK_KINDS:
        raise PackInfoError(f"kind 只能是 {list(PACK_KINDS)}，实际是 {kind!r}", path=info_path)
    base = data.get("base", False)
    if not isinstance(base, bool):
        raise PackInfoError(f"base 必须是 true / false，实际是 {base!r}", path=info_path)

    requires = _require_mapping(data.get("requires", {}), "requires", path=info_path)
    assets = _require_mapping(data.get("assets", {}), "assets", path=info_path)
    for key, value in assets.items():
        if not isinstance(value, str) or not value:
            raise PackInfoError(f"assets[{key!r}] 必须是非空字符串（相对路径）", path=info_path)

    capabilities: dict = {}
    raw_capabilities = _require_mapping(data.get("capabilities", {}), "capabilities", path=info_path)
    for capability_name, spec in raw_capabilities.items():
        capabilities[capability_name] = _parse_capability(
            module_id, capability_name, spec, folder, info_path
        )
    return ModuleInfo(
        id=module_id,
        name=name,
        version=version,
        folder=folder,
        kind=kind,
        base=base,
        requires=dict(requires),
        capabilities=capabilities,
        assets=dict(assets),
        info_path=info_path,
    )


def _parse_capability(module_id: str, name: str, spec: Any, folder: str, info_path: str) -> Capability:
    """解析一个 capability 声明。

    输入：
        module_id: 所属模块 id；
        name: 能力名；
        spec: 原始声明；
        folder: 模块目录；
        info_path: pack_info.json 路径（报错用）。
    输出：
        Capability。
    异常：
        PackInfoError: 缺字段、类型不对、变体表写法不对、默认变体不在变体表里。
    变量：
        kind / script / variants / default: 中间结果。
    """
    allowed = {"kind", "script", "variants", "default"}
    if isinstance(spec, dict):
        unknown = sorted(set(spec) - allowed)
        if unknown:
            raise PackInfoError(
                f"capabilities[{name!r}] 里有不认识的键 {unknown}；"
                f"允许的键是 {sorted(allowed)}",
                path=info_path,
            )

    if not isinstance(name, str) or not name:
        raise PackInfoError(f"能力名必须是非空字符串，实际是 {name!r}", path=info_path)
    if not isinstance(spec, dict):
        raise PackInfoError(f"capabilities[{name!r}] 必须是一个对象", path=info_path)
    kind = spec.get("kind", "function")
    if kind not in CAPABILITY_KINDS:
        raise PackInfoError(
            f"capabilities[{name!r}].kind 只能是 {list(CAPABILITY_KINDS)}，实际是 {kind!r}",
            path=info_path,
        )
    script = _require_str(spec, "script", path=info_path, where=f"capabilities[{name!r}]")
    raw_variants = spec.get("variants")
    if not isinstance(raw_variants, dict) or not raw_variants:
        raise PackInfoError(
            f"capabilities[{name!r}].variants 必须写成「变体名: 函数名」的对象",
            path=info_path,
        )
    variants: dict = {}
    for variant, callable_name in raw_variants.items():
        if not isinstance(variant, str) or not variant:
            raise PackInfoError(f"capabilities[{name!r}] 的变体名必须是非空字符串", path=info_path)
        if not isinstance(callable_name, str) or not callable_name:
            raise PackInfoError(
                f"capabilities[{name!r}].variants[{variant!r}] 必须是非空字符串（脚本里的函数名）",
                path=info_path,
            )
        variants[variant] = callable_name
    default = spec.get("default", next(iter(variants)))
    if default not in variants:
        raise PackInfoError(
            f"capabilities[{name!r}].default 指向不存在的变体 {default!r}（可用：{sorted(variants)}）",
            path=info_path,
        )
    return Capability(
        module_id=module_id,
        name=name,
        kind=kind,
        script=script,
        variants=variants,
        default=default,
        folder=folder,
    )


def _require_str(data: dict, key: str, *, path: str, where: str = "") -> str:
    """要求字段是非空字符串。"""
    if key not in data:
        raise PackInfoError(f"{where or PACK_FILE} 缺少必填字段 {key!r}", path=path)
    value = data[key]
    if not isinstance(value, str) or not value:
        raise PackInfoError(f"{where or ''}{key} 必须是非空字符串，实际是 {value!r}", path=path)
    return value


def _require_mapping(value: Any, key: str, *, path: str) -> dict:
    """要求字段是对象（缺省时调用方传空字典）。"""
    if not isinstance(value, dict):
        raise PackInfoError(f"{key} 必须是一个对象，实际是 {type(value).__name__}", path=path)
    return value
