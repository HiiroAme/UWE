# core/logic——JSON 表达式求值

**一句话**：注册表里的逻辑和公式是 JSON，这个包负责把它读懂、算出来。

## 为什么需要它

Mod 的 command 分支条件、rule 检查、formula 都是写在 JSON 里的，引擎不能拿 Python 的
`eval` 去跑（数据对象不许携带可执行内容，P2），所以要有自己的解释器。它解析一遍、
检查一遍，之后可以反复求值。

## 表达式长什么样

```json
["and",
  [">", ["get", "/units/u1/hp"], 0],
  ["<=", ["get", "/units/u1/ammo"], 3],
  ["between", ["get", "/weather/temperature"], -10, 40]]
```

- 字面量：数字、字符串、true / false、null、对象（对象永远是字面量）。
- 操作调用：`[操作名, 参数…]`，操作名必须在 `ops.py` 的操作表里登记。
- 列表的第一个元素是字符串时一律当调用，所以写"一串字符串当数据"要用 `["list", "a", "b"]`；
  好处是操作名拼错（`["adn", …]`）会当场报错，不会悄悄变成列表。
- **路径参数的两种写法**：`["get", "/units/u1/hp"]` 是字面量路径，加载期就校验写法；
  `["get", ["format", "/units/{0}/hp", ["arg", "unit"]]]` 是表达式路径，运行期算出来再读
  （单位 id 这类"按参数拼出来的路径"只能用后者）。`get` / `exists` / `get_or` 都支持。

## 用什么读数据

求值器自己不认 State，它要一个"读取器"（有 `get(path)` / `exists(path)` 两个方法）：

- `StateReader(state)`：读一棵 State 树；
- `TempState` 视图本身就可以直接传进去——Rule 和 Orchestrator 就是这么用的，
  这样读到的是"本 Command 已经算到现在的样子"。

## 怎么用

```python
from core.logic import LogicError, StateReader, evaluate, parse, truth

expression = [">", ["get", "/units/u1/hp"], 0]
node = parse(expression)              # 加载期：检查操作符、参数个数、路径写法
reader = StateReader(state)
truth(node, reader)                   # 运行期：条件（结果必须是布尔）
evaluate(["clamp", ["get", "/units/u1/hp"], 0, 100], reader)   # 算值
```

## 文件

| 文件 | 内容 |
|---|---|
| `errors.py` | `LogicError`：表达式不合法 / 求值失败的唯一异常，带表达式内部位置 |
| `ops.py` | 操作表 `OPERATORS`：四则、比较、布尔、字符串、列表字典等；新增操作符在这里登记 |
| `ast.py` | `parse()`：判断字面量还是调用，拼出 `Node`，并做完全部静态检查 |
| `eval.py` | `evaluate()` 求值、`truth()` 判真假、`Reader` 协议与 `StateReader` |
| `resolve.py` | `parse_data()` 加载期把一整块数据里的表达式解析成 Node；`resolve()` / `resolve_mapping()` 运行期把它们算成具体数据 |
| `__init__.py` | 对外导出这几样东西 |

## 口径（必须记住的几条）

- **纯函数、确定性**：同样输入永远同样输出；不读时钟、不取随机、不写文件。
- **不用 `eval`**：表达式由操作表解释执行。
- **布尔要干净**：`and` / `or` / `not` / `if` 只接受真正的布尔，数字和字符串不算；
  条件结果必须是布尔（`truth` 会拦住"把数量当条件"的写法）。
- **数字口径与 delta 一致**：`int` 和 `float` 同属 number（`1 == 1.0`），`bool` 不算数字。
- **比较只允许同类型**：两个数字或两个字符串；字符串按 Unicode 码点比，不看系统语言。
- **`round` 是"半值远离零"**（2.5 → 3，-2.5 → -3），不用 Python 的银行家舍入。
- **`and` / `or` / `if` 短路**：后面的分支不算，所以 `["if", 有弹药, ["get", ...], 0]`
  不会因为缺字段而报错。
- **字面量容器返回深拷贝**；读取器返回的容器是只读的，不要就地改。

## 明确不做（这一版）

- `map` / `filter` / `sort`：要引入迭代变量和作用域，等基础稳了再加。
- 自定义函数、循环、赋值：表达式不是脚本；改数据只能产生变化量。
- 正则、时间、随机：随机将来走 RNG 模块（带 stream），通过 `call` 注入，保证可复现。
- 注册表里"要改的数据"的写法（path + 操作 + 值表达式）：那是 Applier 的事，不属于本包。
