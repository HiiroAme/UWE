# 演示 Mod：六边形遭遇战

这个 Mod 覆盖了草案 §21 的最小闭环：

```
一张六边形地图、两个单位、移动、攻击、规则、随机、触发、保存、回放
```

玩法是**热座回合制**：开局红方行动，点自己的单位选中、点绿框走过去、点红框打人，
行动力用完（或想收手）就点右下角「结束回合」，控制权交给蓝方；把对方打到 0 血即获胜。
画面左侧一直写着这份操作说明，中间的地图会用颜色标出"哪一格能走、哪一格能打"。

它同时是"怎么写一个 Mod"的样板，而且有两层意思：

1. **引擎里没有任何一处认识这些玩法名词**（没有单位、没有攻击、没有六边形）；
2. **本 Mod 自己也不写玩法实现**——几何来自模块 `pack_hex_grid`、攻击与伤害来自
   `pack_combat_basic`、移动与回合推进来自 `pack_wargame_core`。本 Mod 只写
   "这一局的事实"（地图多大、谁在哪、扣哪条量）与排版。

## 目录

```
demo_hex/
├── mod_info.json     元信息：uses / bindings / params 都写在这里（浅加载只读这个文件）
├── data/             注册表条目：每个文件是一个数组，条目自带 type
│   ├── 00_events.json         事件定义
│   ├── 05_terrain.json        地形模板
│   ├── 06_nodes.json          节点模板（格子）
│   ├── 07_entities.json       实体模板（单位）
│   ├── 08_phases.json         阶段
│   ├── 09_system.json         全局初值
│   ├── 10_services.json       本局自己的服务（选中 / 取消选中 / 换控制方 / 阵亡收尾）
│   ├── 20_formulas.json       派生值公式（战斗力、优势；战斗力那条用了通配路径）
│   ├── 30_rules.json          规则（条件 + 拒绝原因）
│   ├── 40_actions.json        动作：规则 + 依次调用的服务（模块的与本局的）
│   ├── 44_actions_refresh.json / 45_actions_select.json / 46_actions_recruit.json
│   ├── 50_commands.json / 55_commands_select.json / 56_commands_recruit.json
│   ├── 60_triggers.json / 65_triggers_refresh.json
│   ├── 70_inputs.json / 75_inputs_select.json / 76_inputs_recruit.json
│   └── 80_ui.json             战场页面
├── scripts/          只放"这一局的事实"与排版，不放可复用的玩法实现
│   ├── init.py            初始化钩子：调模块的几何函数摆出地图与两个单位
│   ├── battle.py          阵亡处理与胜负判定（把阵亡单位撤出地图，收尾由触发链调用）
│   ├── selection.py       选中 / 取消选中（写界面状态，不进存档）
│   ├── sides.py           控制权交给对方（红 ↔ 蓝）
│   └── ui_battle.py       战场界面：真六边形格子（调模块界面件）+ 操作说明 + 高亮 + 战报
├── assets/           资源（音频 / 图片）；缺失时媒体只记录不报错
├── 需要的资源.md      需要作者提供但当前拿不到的资源清单
└── README.md
```

## 初始局面

- 地图：半径 1 的六边形（7 个格子），坐标用轴向坐标 (q, r)；画法是模块的
  `pack_hex_grid.hex_grid.hexagon`（每格两层多边形铺出格线，命中按六边形形状算）；
  格子坐标由模块的
  `pack_hex_grid.all_within.hex` 算出来，相邻关系由 `pack_hex_grid.adjacency_map.from_coords`
  算出来写进 State 的 `/adjacency`；
- 单位：`red_1` 在 n-1_0，`blue_1` 在 n1_0（相距 2 格），各 10 点血、每回合 2 点行动力。
- 控制权：`/control_side` 开局是 `red`；它只是个普通字符串，引擎不知道"控制方"是什么。

## 玩法（数据 + 模块实现，没有引擎硬编码，也没有 Mod 自写的玩法脚本）

| 行为 | 表达方式 |
|------|----------|
| 选中 | `select` 命令 → 动作 `select` → 规则 `battle_running`、`own_side`、`unit_alive` → 本局服务 `demo_hex:service:select_unit`（写界面状态） |
| 移动 | `move` 命令 → 动作 `move` → 规则 `battle_running`、`own_side`、`unit_alive`、`has_ap`、`target_adjacent` → 模块服务 `pack_wargame_core:service:move` |
| 攻击 | `attack` 命令 → 动作 `attack` → 规则（同上 + `enemy_target`、`target_alive`、`unit_adjacent`）→ 模块服务 `pack_combat_basic:service:attack`（伤害用绑定 `damage` = `pack_combat_basic.damage.dice`，骰子写在 params 里） |
| 结束回合 | `end_turn` 命令 → 动作 `end_turn` → 模块服务 `pack_wargame_core:service:advance_turn`（回合 +1、行动力恢复）+ 本局服务 `demo_hex:service:switch_control`（控制权交给对方） |
| 取消选中 | 点空地或再点一次选中的单位 → `clear_selection` 命令 → 本局服务（把界面状态清空） |
| 记一条被忽略的说明 | 模块服务 `pack_common:service:log_append`（基线内容里的通用工具） |
| 阵亡 | 事件 `unit_destroyed` → 触发 → 本局服务 `demo_hex:service:after_destroyed`：把 hp ≤ 0 的单位**撤出地图**（`at` 置空，记录留着当阵亡名单），只剩一方时再置 `/game_over`、`/winner` |
| 存档 / 回放 | 引擎的存档机制：命令与变化量进存档，回放 = 快照 + 叠加变化量 |

界面只给合法操作点击结果（够不着的敌军点了没反应、战斗结束后整张图都不能点），
免得玩家反复撞规则；真正拦住非法操作的是上面那几条规则——界面只是提前一步。

## 参数写在哪儿

`mod_info.json` 的 `params` 是**这一局的事实**：哪条路径是单位表、扣血还是扣人数、
骰子多大、每回合恢复多少行动力。想换一套玩法数值（例如把伤害目标改成人数、
把移动消耗改成 2 点），只改这里，一个函数都不用写。
