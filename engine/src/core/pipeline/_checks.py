"""pipeline 包内部共用的小校验。

位置：core/pipeline/ —— **包内部工具**：下划线开头的文件名、不进 __all__，不算对外 API。
为什么单独一个文件：同一段"字段必须是非空字符串"的检查原先在 command / input / engine_api
三处逐字重复（N-7）。三份收一处后，口径只有一个，改一处就全改。
"""

from typing import Any


def require_non_empty_str(value: Any, name: str) -> None:
    """要求一个字段是"非空字符串"。

    输入：
        value: 待检查的值；
        name: 字段名，用于异常消息。
    输出：
        无；通过检查时直接返回。
    异常：
        TypeError: value 不是字符串；
        ValueError: value 是空字符串。
    变量：
        无。
    """
    if not isinstance(value, str):
        raise TypeError(f"{name} 必须是字符串，实际是 {type(value).__name__}")
    if not value:
        raise ValueError(f"{name} 不能是空字符串")
