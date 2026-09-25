"""管线内的失败类型（core.pipeline 专用）。

位置：
    引擎核心逻辑层 → pipeline 子包。

职责：
    把"处理一个 Command 时可能出现的、需要丢弃整个 Command 的失败"分成几类，
    让 Dispatcher 能写出明确的日志与结果（§19.1：拒绝变动要有失败原因）：

        OrchestrationError  编排失败（命令定义缺失、条件求值失败）
        RuleRejection       规则拒绝（哪条规则、什么原因）
        ServiceFailure      服务调用失败（哪个服务、原始异常）

与别的层的分界：
    - 数据树的失败（PathNotFound / StateConflict 等）由 state 层抛出；
    - 一条变化量自己不成立由 delta 层抛出；
    - 本层的异常描述"这一次 Command 的处理为什么没走下去"。

草案依据：
    §9 第 5～7 步的失败处理（编排失败 / 规则拒绝 / 服务异常 → 丢弃 Command）；
    §19.1 日志要写清失败原因；D-18 提交单位是 CommandDelta。
"""


class PipelineError(Exception):
    """管线失败类异常的基类。

    字段：
        detail: 说明文字；
        command_id: 出问题的 Command（可能为空）；
        action_id: 出问题的 Action（可能为空）；
        service_id: 出问题的 Service（可能为空）。
    """

    def __init__(
        self,
        detail: str,
        *,
        command_id: str = "",
        action_id: str = "",
        service_id: str = "",
    ) -> None:
        """构造异常。

        输入：
            detail: 说明文字；
            command_id / action_id / service_id: 相关标识，缺省为空。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self.detail = detail
        self.command_id = command_id
        self.action_id = action_id
        self.service_id = service_id
        super().__init__(self.__str__())

    def __str__(self) -> str:
        """拼出"类型 + 位置 + 说明"的完整消息。"""
        parts = [self.__class__.__name__]
        if self.command_id:
            parts.append(f"命令={self.command_id}")
        if self.action_id:
            parts.append(f"动作={self.action_id}")
        if self.service_id:
            parts.append(f"服务={self.service_id}")
        parts.append(self.detail)
        return " | ".join(parts)


class OrchestrationError(PipelineError):
    """编排失败：命令定义缺失，或分支条件求值失败。"""


class RuleRejection(PipelineError):
    """规则拒绝：某条规则不成立，整个 Command 丢弃。

    字段（除基类字段外）：
        rule_id: 拒绝它的规则 id；
        reason: 写进日志的拒绝原因（rule 条目里的 message）。
    """

    def __init__(
        self,
        detail: str,
        *,
        rule_id: str = "",
        reason: str = "",
        command_id: str = "",
        action_id: str = "",
    ) -> None:
        """构造异常。

        输入：
            detail: 说明文字；
            rule_id: 规则 id；
            reason: 拒绝原因（给日志与用户看的那句话）；
            command_id / action_id: 相关标识。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self.rule_id = rule_id
        self.reason = reason or rule_id
        super().__init__(detail, command_id=command_id, action_id=action_id)


class ServiceFailure(PipelineError):
    """服务调用失败（脚本抛异常、或解析不到服务实现）。

    字段（除基类字段外）：
        cause: 原始异常对象（保留堆栈用于日志；可为 None）。
    """

    def __init__(
        self,
        detail: str,
        *,
        command_id: str = "",
        action_id: str = "",
        service_id: str = "",
        cause: BaseException | None = None,
    ) -> None:
        """构造异常。

        输入：
            detail: 说明文字；
            command_id / action_id / service_id: 相关标识；
            cause: 原始异常。
        输出：
            无（构造对象）。
        异常：
            无。
        变量：
            无。
        """
        self.cause = cause
        super().__init__(detail, command_id=command_id, action_id=action_id, service_id=service_id)
