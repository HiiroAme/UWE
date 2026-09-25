"""引擎核心逻辑层（core）。

分层依据（草案 §4、D-06）：
  - 本层是纯逻辑层：禁止 import pygame 等平台库，禁止直接读写文件；
  - 一切与外部环境的交互都必须通过抽象接口（ports）注入实现；
  - 本层不认识任何具体 Mod 内容（P1）。

当前包含的子包：
  - state：State 纯数据树与 JSON Pointer 路径寻址，是引擎数据层的地基；
  - delta：变化量模型、序列化、应用与合并，构建在 state 之上；
  - temp_state：命令级临时状态（叠加视图），构建在 state 与 delta 之上；
  - logic：把 Mod 写在注册表里的 JSON 表达式变成运算与判断；
  - registry：注册表体系（§14 的条目外壳、继承、表与注册表中心）；
  - content：把注册表内容编译成运行期结构，并做完加载期静态检查；
  - ports：核心依赖的抽象接口（依赖方向：核心 → 端口 ← 适配器）；
  - pipeline：Command 管线（编排 / 规则 / 应用 / 事件链 / 分发）；
  - 单文件模块：logger（分层日志）、rng（确定性随机）、context（不存档上下文）、
    runtime（EngineRuntime：运行期对象总装）。
"""
