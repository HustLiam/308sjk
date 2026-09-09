# scenegen —— ②b 仿真环境生成的确定性支撑（csk）

闭环系统 ②b 号模块：**SceneSpec（JSON 中间表示）→ USD 仿真场景 + io_map 契约**。
SceneSpec 由 **gc 侧 LLM 生成本体**产出（本侧仅定义 Schema 与校验闸门，不含 agent——
分工见 `../docs/协作看板.md` 与 csk 文档 §0）；本模块提供确定性支撑：
Schema 校验、静态物理校验、USD 确定性构建、io_map Modbus 地址分配、结构冒烟。

**现役场景**：运动控制 motion3axis（PLC 侧，双链路联调基准）+ 三轴绘图仪（gantry_xyz，
本侧仿真场景，见 `examples/gantry_plotter.json` 与 `out/gantry/`）。

## 安装

```bash
pip install -r requirements.txt        # usd-core（pxr）+ jsonschema，Python 3.10+（3.12 与 Isaac Sim 6.0 对齐）
```

运行时（Isaac Sim headless 冒烟）另需 `isaacsim`，见文档 3.2 节；未安装时冒烟退化为纯 pxr 结构检查。

## 用法

```bash
# 静态校验（Schema + 参数规则 + 引用完整性 + 布局穿模 + 物理量纲）
python -m scenegen.cli validate scenegen/examples/gantry_plotter.json

# 构建：scene.usda + io_map.json + st_io_declaration.st + modbus_summary.json
python -m scenegen.cli build scenegen/examples/gantry_plotter.json -o out/gantry

# 全流程（校验 → 构建 → 结构冒烟）
python -m scenegen.cli all scenegen/examples/gantry_plotter.json -o out/gantry

# 回归测试（无 pytest 依赖）
python scenegen/tests/test_scenegen.py
```

gc 侧生成器对接点：`scenegen/validate.py: validate(spec)` 返回结构化错误列表
（重生成回喂源）；Schema 见 `scenegen/schema.json`（本侧闸门）与
`schemas/io_map.schema.json`（契约③）。

## 模块结构

| 文件 | 职责 |
|---|---|
| `schema.json` | SceneSpec 的 JSON Schema（draft-07），`type`/参数枚举封闭 |
| `geom.py` | 位姿复合（父子链）、欧拉角/四元数、包围盒变换 |
| `components.py` | 组件注册表：quantity 清单、参数规则、USD 构建函数、包围盒 |
| `validate.py` | 五层静态校验，错误信息面向 LLM 反馈（带资产 id 与具体原因） |
| `iomap.py` | io_map 富化：usd_prim 绑定 + OpenPLC Modbus 地址确定性分配 |
| `build_usd.py` | 确定性构建器（只使用标准 UsdPhysics 模式，Isaac 直接可跑） |
| `smoke.py` | 结构冒烟（纯 pxr；含"关节 body 必须刚体"黄金规则）+ Isaac headless 冒烟（可选） |
| `cli.py` | 命令行入口 |

## 产物说明（outdir）

- **`scene.usda`**：USD 场景（文本格式，可 diff、可入 git）。含物理场景/重力、地面、
  物理材质、各组件实例（刚体/碰撞/关节驱动/限位已按参数烘焙）；组件根 prim 带
  `simio:*` 标记属性供运行时 IOBridge 自检。
- **`io_map.json`**：三方契约（`plc_var` ↔ `usd_prim` ↔ `modbus`）。
  Modbus 地址按 io_map 声明顺序确定性分配：
  输出 bool → `%QX` 线圈；输出 float → `%QW`（2 寄存器 float32 大端）；
  输入（bool/float）→ Isaac 传感区块 / OpenPLC `%IW`（bool 占 1 寄存器 0/1）。
  编码 float32→int16 定点的收敛随共同议题"float32/INT16 换算归属"定稿（契约③ RFC）。
- **`st_io_declaration.st`**：与地址分配一致的 ST 全局定位变量声明，
  供代码生成模块（②a）生成 `plc_project.xml` 时对齐，保证
  `io_list ≡ ST 定位变量 ≡ io_map` 三方一致。
- **`modbus_summary.json`**：运行时/桥端通道概览（线圈数、%QW 寄存器数、
  传感区块长度、OpenPLC 轮询表配置项）。

## 首批组件（封闭枚举）

`conveyor_belt` / `pneumatic_cylinder` / `photoelectric_sensor` / `bin_chute` /
`rigid_box` / `contact_pad` / `vacuum_gripper` / `gantry_xyz`（三轴龙门，X/Y/Z
直线轴 + 笔针，q=0..travel，Z 行程末端笔尖触台面）/ `articular_arm`（引用外部 USD）

> 现役场景只用 `gantry_xyz`（三轴绘图仪）；其余组件为 csk 文档 §4.3 首批清单的
> 预置能力，按后续场景需要启用。

每个组件在 `components.py` 的 `REGISTRY` 注册四件事：quantity 清单（名称/方向/类型）、
参数规则（区间与枚举）、USD 构建函数、局部包围盒。新增组件 = 新增一个注册项，
Schema 的封闭枚举随即生效，校验器与构建器无需改动。

## 与闭环其他模块的边界

- 输入：`requirement_spec.json`（①，io_list 为 IO 单一源头）→ gc 侧 LLM 生成
  `scene.spec.json`（②b LLM 本体，本侧评审）；
- 本侧：`validate(spec)` 闸门（失败错误列表回喂 gc 重生成）→ `build_usd` →
  `scene.usda` + `io_map.json` 交 ③b 运行时；`st_io_declaration.st` 交 ②a 代码生成；
- 本侧**不含 LLM/agent**——归因、反馈 Prompt 拼装、编排器均归 gc（见 csk 文档 §7.2–7.4 职责边界）。
