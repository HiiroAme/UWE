"""state 包：State 纯数据树与路径寻址（引擎核心逻辑层的最底层）。

对外只暴露本文件 __all__ 中的名字。上层模块（变化量引擎、临时状态视图、
派生值重算、提交逻辑）一律通过它们访问 State 树，不允许各自实现路径解析。

草案依据：
  §6.2  State 是一棵树，结构由 Mod 决定；
  §6.3  路径语法对齐 JSON Pointer（RFC 6901），索引分隔符 "/"、转义符 "~"；
  §7.2  新增/删除 = 加/删一个键；列表增删元素属于"修改该列表"（D-34）；
  D-09  State 单树；D-15 一个变化量只改一个变量；D-14 运行时不校验 Mod 数据。
"""

from .errors import (
    PathNotFoundError,
    PathSyntaxError,
    StateConflictError,
    StateError,
    StateShapeError,
)
from .path import escape_token, format_path, parse, unescape_token
from .tree import add_key, exists, get, list_insert, list_remove, remove_key, replace
from .values import snapshot_value
from .wildcard import (
    ANY_SEGMENT,
    Match,
    expand,
    is_pattern,
    match,
    patterns_overlap,
    substitute,
    validate_pattern,
)

__all__ = [
    # 异常
    "StateError",
    "PathSyntaxError",
    "PathNotFoundError",
    "StateShapeError",
    "StateConflictError",
    # 路径
    "parse",
    "format_path",
    "escape_token",
    "unescape_token",
    # 树读写
    "get",
    "exists",
    "add_key",
    "remove_key",
    "replace",
    "list_insert",
    "list_remove",
    # 值快照（写入 State 前统一走它）
    "snapshot_value",
    # 通配路径（F-05）
    "ANY_SEGMENT",
    "Match",
    "is_pattern",
    "validate_pattern",
    "match",
    "patterns_overlap",
    "substitute",
    "expand",
]
