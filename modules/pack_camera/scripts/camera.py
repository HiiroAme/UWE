"""界面相机（模块服务）：滚轮缩放 + 拖动平移。

相机是**界面状态**（D-11：不影响任何规则判定），所以写在 Context 里、不进存档：
    <前缀>.x / <前缀>.y ：平移量（屏幕像素）
    <前缀>.zoom        ：缩放比

参数（Mod 通过 params / 调用点给）：
    state_prefix: 键名前缀（默认 "camera"）
    min_zoom / max_zoom / zoom_step: 缩放上下限与每格滚轮的倍率（默认 0.35 / 3.0 / 1.15）

坐标口径（与视图脚本约定）：`屏幕 = 世界 × zoom + (屏幕中心 + 平移)`——
所以"围绕屏幕中心缩放"只要把平移量按比例缩放一次，服务**不需要知道窗口尺寸**。
"""

DEFAULT_PREFIX = "camera"
DEFAULT_MIN_ZOOM = 0.35
DEFAULT_MAX_ZOOM = 3.0
DEFAULT_ZOOM_STEP = 1.15


def zoom(api, args):
    """滚轮缩放（围绕屏幕中心）：只看 args["delta"] 的第二个分量。

    输入：
        api / args（见模块文档；delta = [dx, dy]，正值放大）。
    输出：
        无（相机是界面状态，不产生变化量）。
    异常：
        无（参数写法不对就当没滚动）。
    变量：
        prefix / old / new / factor: 中间结果。
    """
    delta = args.get("delta") or (0.0, 0.0)
    steps = _number(delta[1]) if len(delta) > 1 else 0.0
    if steps == 0:
        return
    prefix = _prefix(args)
    old = _number(api.context.get(f"{prefix}.zoom", 1.0))
    step = _number(args.get("zoom_step", DEFAULT_ZOOM_STEP)) or DEFAULT_ZOOM_STEP
    low = _number(args.get("min_zoom", DEFAULT_MIN_ZOOM)) or DEFAULT_MIN_ZOOM
    high = _number(args.get("max_zoom", DEFAULT_MAX_ZOOM)) or DEFAULT_MAX_ZOOM
    new = min(high, max(low, old * (step ** steps)))
    if new == old:
        return
    factor = new / old
    api.context.put(f"{prefix}.x", _number(api.context.get(f"{prefix}.x", 0.0)) * factor)
    api.context.put(f"{prefix}.y", _number(api.context.get(f"{prefix}.y", 0.0)) * factor)
    api.context.put(f"{prefix}.zoom", new)


def pan(api, args):
    """拖动平移（拖到哪动到哪）：把本帧位移加到平移量上。

    输入：
        api / args（见模块文档；delta = [dx, dy]，屏幕像素）。
    输出：
        无。
    异常：
        无。
    变量：
        prefix / dx / dy: 中间结果。
    """
    delta = args.get("delta") or (0.0, 0.0)
    dx = _number(delta[0]) if len(delta) > 0 else 0.0
    dy = _number(delta[1]) if len(delta) > 1 else 0.0
    prefix = _prefix(args)
    api.context.put(f"{prefix}.x", _number(api.context.get(f"{prefix}.x", 0.0)) + dx)
    api.context.put(f"{prefix}.y", _number(api.context.get(f"{prefix}.y", 0.0)) + dy)


def _prefix(args):
    """取 Context 键名前缀（Mod 没给就用缺省）。"""
    text = args.get("state_prefix", "") or DEFAULT_PREFIX
    return str(text)


def _number(value):
    """把值转成 float（转不动就按 0 算）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
