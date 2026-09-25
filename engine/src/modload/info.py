"""Mod 元信息（mod_info.json，§15.2 / §15.1 的"浅加载"）。

位置：
    modload 包。

职责：
    定义 mod_info.json 里引擎会用到的字段，并做格式检查。
    浅加载只读这一个文件：Mod 选择页要列出全部候选 Mod，不能把每个 Mod 的
    全部内容都读进内存（§15.1）。

字段口径（引擎只认这几个，其余字段原样保留在 extra 里给 UI 看）：

    {
      "id": "demo_hex",                       // 必填：Mod 唯一标识（存档里记的就是它）
      "name": "六边形演示",                    // 必填：显示名
      "version": "0.0.0",                     // 必填：写进存档，读档时比对（D-25）；未发布期一律 0.0.0
      "author": "",                           // 可选
      "description": "",                      // 可选
      "init": "scripts/init.py:build_state",  // 可选：初始化钩子（脚本路径:函数名）
      "scenario": "demo_hex_scene",           // 可选：场景 id（引擎只存不解释）
      "ui": "demo_hex:ui:battle"              // 可选：进入游戏后默认显示哪个 ui 条目（缺省取第一个）
      "functions": {                          // 可选：表达式里可用的 Mod 函数（名字 → 脚本:函数）
        "adjacent": "scripts/adjacency.py:is_adjacent"
      },
      "uses": ["pack_hex_grid>=0.0.0"],       // 可选：声明用到哪些逻辑模块
      "bindings": {                           // 可选：能力 → 选哪个实现（+ 默认参数）
        "damage": {"use": "pack_combat_basic.damage.dice", "params": {"dice": "2d6"}}
      },
      "params": {"turn_path": "/turn"}        // 可选：平铺参数（模块实现与脚本共用，优先级最低）
      "menu": [{"label": "规则", "input": "open_rules"}]  // 可选：游戏内菜单栏的 Mod 条目
    }

草案依据：
    §15.1 浅加载；§15.2 Mod 提供元信息；D-25 读档校验 Mod 版本；
    §18.1 存档记录 Mod 版本与场景 id。
"""

from dataclasses import dataclass, field
from typing import Any


class ModInfoError(Exception):
    """mod_info.json 不合法。

    字段：
        detail: 说明文字；
        path: 出问题的文件路径。
    """

    def __init__(self, detail: str, *, path: str = "") -> None:
        """构造异常。

        输入：
            detail: 说明文字；
            path: 文件路径。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self.detail = detail
        self.path = path
        super().__init__(f"ModInfoError | 文件={path} | {detail}" if path else detail)


@dataclass(frozen=True)
class MenuEntry:
    """游戏内菜单栏的一条 Mod 条目：显示标签 + 要送进会话的输入 kind。"""

    label: str
    input: str
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ModInfo:
    """一个 Mod 的元信息与它在磁盘上的位置。

    字段：
        id: Mod 唯一标识；
        name: 显示名；
        version: Mod 版本（写进存档）；
        folder: Mod 文件夹路径（全加载时用它去找 data / scripts）；
        author / description: 可选的展示信息；
        init_script: 初始化钩子，"脚本路径:函数名"两个冒号分隔；空字符串表示没有；
        scenario: 场景 id；空字符串表示没有；
        page_id: 进入游戏后默认显示哪个 ui 条目（空字符串表示取第一个）；
        functions: 表达式里可用的 Mod 函数（名字 → "脚本路径:函数名"）；
        uses: 声明用到的逻辑模块（每项 "模块id" 或 "模块id>=x.y.z"）；
        bindings: 能力名 → 绑定声明（{"use": 全限定实现名, "params": {...}} 或直接给实现名）；
        params: 平铺参数（模块实现与脚本共用；调用点参数与绑定的 params 优先于它）；
        menu: 游戏内菜单栏的 Mod 条目（标签 + 输入 kind + 可选数据）；
        extra: mod_info.json 里其他字段（引擎不解释，留给 UI）。
    """

    id: str
    name: str
    version: str
    folder: str
    author: str = ""
    description: str = ""
    init_script: str = ""
    scenario: str = ""
    page_id: str = ""
    functions: dict = field(default_factory=dict)
    uses: tuple = ()
    bindings: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    menu: tuple = ()
    extra: dict = field(default_factory=dict)

    @property
    def info_path(self) -> str:
        """返回这个 Mod 的 mod_info.json 路径（展示与报错用）。"""
        return f"{self.folder}/mod_info.json".replace("\\", "/")

    @classmethod
    def from_data(cls, data: Any, *, folder: str, path: str = "") -> "ModInfo":
        """从 mod_info.json 的内容构造 ModInfo 并做格式检查。

        输入：
            data: json.loads 的结果（必须是对象）；
            folder: 这个 Mod 的文件夹路径；
            path: 文件路径（报错用）。
        输出：
            ModInfo。
        异常：
            ModInfoError: 不是对象、缺必填字段、字段类型不对、init 写法不对。
        变量：
            record: 内容字典；
            known: 引擎自己用掉的键集合（其余进 extra）。
        """
        if not isinstance(data, dict):
            raise ModInfoError(f"mod_info.json 必须是一个对象，实际是 {type(data).__name__}", path=path)

        known = {
            "id", "name", "version", "author", "description",
            "init", "scenario", "ui", "functions", "uses", "bindings", "params",
            "menu",
            "tags",          # 自由元数据：给工具 / 以后做筛选用，引擎不解释
        }
        unknown = sorted(set(data) - known)
        if unknown:
            raise ModInfoError(
                f"mod_info.json 里有不认识的键 {unknown}；允许的键是 {sorted(known)}"
                "（拼错的键以前会被静默忽略，现在直接报错）",
                path=path,
            )
        info = cls(
            id=_require_str(data, "id", path=path),
            name=_require_str(data, "name", path=path),
            version=_require_str(data, "version", path=path),
            folder=folder,
            author=_optional_str(data, "author", path=path),
            description=_optional_str(data, "description", path=path),
            init_script=_parse_init(data.get("init", ""), path=path),
            scenario=_optional_str(data, "scenario", path=path),
            page_id=_optional_str(data, "ui", path=path),
            functions=_parse_functions(data.get("functions", {}), path=path),
            uses=_parse_uses(data.get("uses", []), path=path),
            bindings=_parse_bindings(data.get("bindings", {}), path=path),
            params=_parse_params(data.get("params", {}), path=path),
            menu=_parse_menu(data.get("menu", []), path=path),
            extra={key: value for key, value in data.items() if key not in known},
        )
        return info

    def init_parts(self) -> tuple[str, str] | None:
        """把初始化钩子拆成 (脚本路径, 函数名)。

        输入：无。
        输出：
            二元组；没有配置初始化钩子时返回 None。
        异常：
            ModInfoError: 写法不合法（构造时已检查过，这里再兜一层）。
        变量：
            无。
        """
        if not self.init_script:
            return None
        script, _, name = self.init_script.partition(":")
        if not script or not name:
            raise ModInfoError(
                f"init 必须写成 脚本路径:函数名，实际是 {self.init_script!r}", path=self.info_path
            )
        return script, name


def _require_str(data: dict, key: str, *, path: str) -> str:
    """要求字段是非空字符串。"""
    if key not in data:
        raise ModInfoError(f"缺少必填字段 {key!r}", path=path)
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ModInfoError(f"{key} 必须是非空字符串，实际是 {value!r}", path=path)
    return value


def _optional_str(data: dict, key: str, *, path: str) -> str:
    """要求字段是字符串（缺省为空字符串）。"""
    if key not in data:
        return ""
    value = data[key]
    if not isinstance(value, str):
        raise ModInfoError(f"{key} 必须是字符串，实际是 {type(value).__name__}", path=path)
    return value


def _parse_init(value: Any, *, path: str) -> str:
    """检查 init 字段的写法（"脚本路径:函数名"）。"""
    if value == "" or value is None:
        return ""
    if not isinstance(value, str):
        raise ModInfoError(f"init 必须是字符串，实际是 {type(value).__name__}", path=path)
    script, _, name = value.partition(":")
    if not script or not name:
        raise ModInfoError(f"init 必须写成 脚本路径:函数名，实际是 {value!r}", path=path)
    return value


def _parse_functions(value: Any, *, path: str) -> dict:
    """检查 functions 字段：名字 → "脚本路径:函数名"。

    输入：
        value: mod_info.json 里的 functions；
        path: 文件路径（报错用）。
    输出：
        新的字典（名字 → 声明字符串）。
    异常：
        ModInfoError: 不是对象、名字为空或重复、声明写法不对、用了引擎保留前缀。
    变量：
        name / declaration: 遍历时的名字与声明。

    说明：
        表达式里的 ["call", 名字, …] 用的就是这些名字；`rng.` 前缀是引擎自己的随机函数
        （§13 要求随机统一走引擎），Mod 不许占用。
    """
    if value == {} or value is None:
        return {}
    if not isinstance(value, dict):
        raise ModInfoError(f"functions 必须是一个对象，实际是 {type(value).__name__}", path=path)
    result: dict = {}
    for name, declaration in value.items():
        if not isinstance(name, str) or not name:
            raise ModInfoError(f"functions 的名字必须是非空字符串，实际是 {name!r}", path=path)
        if name.startswith("rng."):
            raise ModInfoError(
                f"functions 的名字不能以 rng. 开头（那是引擎的随机函数）：{name!r}", path=path
            )
        if not isinstance(declaration, str):
            raise ModInfoError(
                f"functions[{name!r}] 必须写成 脚本路径:函数名（字符串），实际是 {type(declaration).__name__}",
                path=path,
            )
        script, _, callable_name = declaration.partition(":")
        if not script or not callable_name:
            raise ModInfoError(
                f"functions[{name!r}] 必须写成 脚本路径:函数名，实际是 {declaration!r}", path=path
            )
        result[name] = declaration
    return result


def _parse_uses(value: Any, *, path: str) -> tuple:
    """检查 uses 字段：声明用到的逻辑模块。

    输入：
        value: mod_info.json 里的 uses；
        path: 文件路径（报错用）。
    输出：
        声明项元组（每项形如 "pack_x" 或 "pack_x>=0.0.0"）。
    异常：
        ModInfoError: 不是列表，或某项不是非空字符串、或写法明显不对。
    变量：
        item: 遍历到的声明项。

    说明：
        这里只做**格式**检查；"模块是否存在、版本是否满足"由加载层的模块目录负责
        （那里才知道有哪些模块可用）。
    """
    if value == [] or value is None:
        return ()
    if isinstance(value, str):
        raise ModInfoError("uses 必须是列表（如 [\"pack_hex_grid>=0.0.0\"]），不能直接给字符串", path=path)
    if not isinstance(value, list):
        raise ModInfoError(f"uses 必须是列表，实际是 {type(value).__name__}", path=path)
    result = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ModInfoError(f"uses 的每一项都必须是非空字符串，实际是 {item!r}", path=path)
        module_id, separator, version_text = item.partition(">=")
        if not module_id.strip():
            raise ModInfoError(f"uses 的模块 id 不能为空：{item!r}", path=path)
        if separator and not version_text.strip():
            raise ModInfoError(f"uses 的版本约束写完了吗：{item!r}", path=path)
        result.append(item)
    return tuple(result)


def _parse_bindings(value: Any, *, path: str) -> dict:
    """检查 bindings 字段：能力名 → 选哪个实现（+ 默认参数）。

    输入：
        value: mod_info.json 里的 bindings；
        path: 文件路径（报错用）。
    输出：
        新的字典（能力名 → 原始声明）。
    异常：
        ModInfoError: 不是对象、键为空、声明不是字符串或对象、params 不是对象。
    变量：
        name / spec: 遍历到的绑定名与声明。

    说明：
        与 uses 一样，这里只检查格式；"实现是否存在、变体对不对"由模块目录负责。
    """
    if value == {} or value is None:
        return {}
    if not isinstance(value, dict):
        raise ModInfoError(f"bindings 必须是一个对象，实际是 {type(value).__name__}", path=path)
    result: dict = {}
    for name, spec in value.items():
        if not isinstance(name, str) or not name:
            raise ModInfoError(f"bindings 的键必须是非空字符串，实际是 {name!r}", path=path)
        if isinstance(spec, str):
            result[name] = spec
            continue
        if not isinstance(spec, dict):
            raise ModInfoError(
                f"bindings[{name!r}] 必须是字符串（实现名）或对象（use + params），"
                f"实际是 {type(spec).__name__}",
                path=path,
            )
        if "use" in spec and (not isinstance(spec["use"], str) or not spec["use"]):
            raise ModInfoError(f"bindings[{name!r}].use 必须是非空字符串", path=path)
        if "params" in spec and not isinstance(spec["params"], dict):
            raise ModInfoError(f"bindings[{name!r}].params 必须是对象", path=path)
        result[name] = spec
    return result


def _parse_params(value: Any, *, path: str) -> dict:
    """检查 params 字段：平铺参数（模块实现与脚本共用）。

    输入：
        value: mod_info.json 里的 params；
        path: 文件路径（报错用）。
    输出：
        新的字典（原样保留；引擎只当纯数据传下去，不解释内容）。
    异常：
        ModInfoError: 不是对象。
    变量：
        无。
    """
    if value == {} or value is None:
        return {}
    if not isinstance(value, dict):
        raise ModInfoError(f"params 必须是一个对象，实际是 {type(value).__name__}", path=path)
    return dict(value)


def _parse_menu(value: Any, *, path: str) -> tuple:
    """检查 menu 字段：游戏内菜单栏的 Mod 条目。

    每项：{"label": 显示文字, "input": 输入 kind, "data": 可选数据}。
    输入 kind 是否存在由加载层在内容编译完之后核对（这里只看格式）。
    """
    if value in ({}, None):
        return ()
    if not isinstance(value, list):
        raise ModInfoError(f"menu 必须是一个列表，实际是 {type(value).__name__}", path=path)
    entries = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ModInfoError(f"menu[{index}] 必须是对象，实际是 {type(item).__name__}", path=path)
        unknown = sorted(set(item) - {"label", "input", "data"})
        if unknown:
            raise ModInfoError(
                f"menu[{index}] 里有不认识的键 {unknown}；允许的键是 ['data', 'input', 'label']",
                path=path,
            )
        label = item.get("label")
        input_kind = item.get("input")
        data = item.get("data", {})
        if not isinstance(label, str) or not label:
            raise ModInfoError(f"menu[{index}].label 必须是非空字符串", path=path)
        if not isinstance(input_kind, str) or not input_kind:
            raise ModInfoError(f"menu[{index}].input 必须是非空字符串", path=path)
        if not isinstance(data, dict):
            raise ModInfoError(f"menu[{index}].data 必须是对象", path=path)
        entries.append(MenuEntry(label=label, input=input_kind, data=dict(data)))
    return tuple(entries)
