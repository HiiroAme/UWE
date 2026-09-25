"""确定性随机（§13、D-23）。

位置：
    引擎核心逻辑层。运行时只有一个实例（由 EngineRuntime 持有），
    所有需要随机数的地方（Service 脚本、表达式里的 call）共用它。

职责：
    - 提供随机数：整数、浮点、按概率判定；
    - 提供**可序列化的随机状态**：存档时写进存档，读档时原样恢复，
      从而做到"相同 State + 相同随机状态 + 相同 Command 序列 ⇒ 相同结果"；
    - 提供一份函数表（functions()），供 core.logic 的 ["call", 名字, …] 使用，
      这样公式与规则里也能取随机，且同样受同一份随机状态约束。

边界：
    - **不允许**任何代码自己 import random / 取系统随机源（草案 §13）；
      本模块内部的随机数发生器是唯一入口，它是标准库 random.Random 的实例，
      但每次取值都必须经过本类，状态才能被存档完整覆盖；
    - 不提供"由种子重新推导状态"：状态直接存、直接恢复（D-23），
      种子只作为新游戏开局的输入；
    - 不做随机流（stream）分组：一条链上的随机数顺序由调用顺序决定，
      将来若需要多流，属于扩展项。

草案依据：
    §13 随机与确定性（硬性不变量、随机状态存入存档）；
    D-23 随机状态存入存档；
    §6.1 判据（随机状态会影响规则判定结果，所以它必须能随存档恢复）。
"""

import random
from typing import Any, Callable, Sequence


class RngError(Exception):
    """随机模块自己的失败（状态格式不对、参数不合法）。

    字段：
        detail: 说明文字。
    """

    def __init__(self, detail: str) -> None:
        """构造异常。

        输入：
            detail: 说明文字。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self.detail = detail
        super().__init__(detail)


class Rng:
    """确定性随机数发生器（标准库 random.Random 的受控包装）。

    字段：
        _random: 内部发生器；它的状态就是本对象的全部随机状态。
    """

    def __init__(self, seed: int) -> None:
        """按种子创建一个随机模块。

        输入：
            seed: 开局种子（int，bool 不算）；同一个种子得到同一条随机序列。
        输出：
            无（构造对象）。
        异常：
            TypeError: seed 不是 int 或 seed 是 bool。
        变量：
            无。
        """
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise TypeError(f"Rng 的种子必须是 int，实际是 {type(seed).__name__}")
        self._random: random.Random = random.Random(seed)

    def next_float(self) -> float:
        """返回 [0.0, 1.0) 上的浮点数。

        输入：无。
        输出：
            浮点数；同一状态下结果确定。
        异常：
            无。
        变量：
            无。
        """
        return self._random.random()

    def next_int(self, low: int, high: int) -> int:
        """返回 [low, high] 闭区间上的整数。

        输入：
            low: 下限（int，bool 不算）；
            high: 上限（int，bool 不算），必须不小于 low。
        输出：
            整数。
        异常：
            TypeError: low / high 不是 int 或是 bool；
            ValueError: high < low。
        变量：
            无。
        """
        _require_int(low, "low")
        _require_int(high, "high")
        if high < low:
            raise ValueError(f"随机整数区间的上限 {high} 小于下限 {low}")
        return self._random.randint(low, high)

    def chance(self, probability: float) -> bool:
        """按概率判定：以 probability 的概率返回 True。

        输入：
            probability: 概率，取值 [0.0, 1.0]（int 也接受，1 表示必然）。
        输出：
            True / False。
        异常：
            TypeError: probability 不是数字或是 bool；
            ValueError: probability 不在 [0, 1] 内。
        变量：
            无。
        """
        if isinstance(probability, bool) or not isinstance(probability, (int, float)):
            raise TypeError(f"概率必须是数字，实际是 {type(probability).__name__}")
        if probability < 0 or probability > 1:
            raise ValueError(f"概率必须落在 [0, 1] 内，实际是 {probability}")
        return self._random.random() < probability

    def choice(self, items: Sequence) -> Any:
        """从非空序列里等概率取一个元素。

        输入：
            items: 序列（list / tuple / str 等）；必须是序列且非空。
        输出：
            取到的元素。
        异常：
            TypeError: items 不是序列；
            ValueError: items 是空的（空序列取元素属于写错，直接报错而不是悄悄返回 None）。
        变量：
            无。
        """
        if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
            raise TypeError(f"choice 需要一个非空序列，实际是 {type(items).__name__}")
        if len(items) == 0:
            raise ValueError("choice 的序列是空的：没有元素可以取")
        return self._random.choice(items)

    def state(self) -> dict:
        """导出随机状态（JSON 可序列化的纯数据）。

        输入：无。
        输出：
            字典：{"version": 版本号, "state": 内部状态整数列表, "gauss_next": 高斯缓存的
            下一个值（可能为 None）}。
        异常：
            无。
        变量：
            version / internal / gauss_next: 标准库 getstate() 返回的三部分。
        """
        version, internal, gauss_next = self._random.getstate()
        return {
            "version": version,
            "state": list(internal),
            "gauss_next": gauss_next,
        }

    def restore(self, state: dict) -> None:
        """恢复随机状态（读档时用）。

        输入：
            state: state() 导出的字典（结构必须完全一致）。
        输出：
            无；恢复后本对象的后续取值与导出时的那次运行一致。
        异常：
            TypeError: state 不是 dict；
            RngError: 缺少字段、字段类型不对、或标准库拒绝这份状态。
        变量：
            version / internal / gauss_next: 从 state 里读出的三部分。
        """
        if not isinstance(state, dict):
            raise TypeError(f"随机状态必须是 dict，实际是 {type(state).__name__}")
        missing = {"version", "state", "gauss_next"} - set(state)
        if missing:
            raise RngError(f"随机状态缺少字段：{', '.join(sorted(missing))}")
        version = state["version"]
        internal = state["state"]
        gauss_next = state["gauss_next"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise RngError(f"随机状态的 version 必须是 int，实际是 {type(version).__name__}")
        if not isinstance(internal, list) or not all(
            isinstance(item, int) and not isinstance(item, bool) for item in internal
        ):
            raise RngError("随机状态的 state 必须是整数列表")
        if gauss_next is not None and not isinstance(gauss_next, (int, float)):
            raise RngError(f"随机状态的 gauss_next 必须是数字或 null，实际是 {type(gauss_next).__name__}")
        try:
            self._random.setstate((version, tuple(internal), gauss_next))
        except (ValueError, TypeError) as exc:
            raise RngError(f"随机状态不被标准库接受：{exc}") from exc

    def functions(self) -> dict[str, Callable[..., Any]]:
        """返回供表达式 ["call", 名字, …] 使用的函数表。

        输入：无。
        输出：
            新的字典，名字 → 绑定在本对象上的方法：
                "rng.int"    → next_int(low, high)
                "rng.float"  → next_float()
                "rng.chance" → chance(probability)
        异常：
            无。
        变量：
            无。

        说明：
            表达式里取名用点号前缀，避免与将来的几何函数（geometries）撞名；
            这些函数取随机的方式与脚本调 api 完全一致，因此共用同一份随机状态。
        """
        return {
            "rng.int": self.next_int,
            "rng.float": self.next_float,
            "rng.chance": self.chance,
        }


def _require_int(value: Any, name: str) -> None:
    """要求一个值是 int（bool 不算）。

    输入：
        value: 待检查的值；
        name: 字段名，用于报错。
    输出：
        无；通过时直接返回。
    异常：
        TypeError: value 不是 int 或是 bool。
    变量：
        无。
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} 必须是 int，实际是 {type(value).__name__}")
