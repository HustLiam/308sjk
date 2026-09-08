# 仿真环境生成与 IO 闭环详细设计（csk 负责部分）

> 本文档是《总体实施方案》中 **②b 仿真环境生成的确定性支撑（规范/校验器/组件库，兼评审方）**、**③b Isaac Sim 仿真引擎（json→USD 构建 / lockstep 运行时 / trace 采集）**、**④ 验证与反馈的判定引擎**与**链路 A 构建流水线（matiec→DLL+shim）**的详细设计与实施记录，负责人 **csk**。
>
> 与 PLC 执行侧（lx）的衔接：**同一份 xml2st 产出的 .st**——本侧链路 A 把它编成 Isaac 进程内 DLL，lx 的链路 B 把它部署到 OpenPLC 软 PLC，双链路互为交叉验证。与智能体与闭环侧（gc）的衔接：**②b 的 LLM 生成本体归 gc**（本侧提供规范/校验器并任评审方），verdict 被 gc 编排器消费驱动迭代。
>
> 涉及 Isaac Sim 的 API 在 4.2 前后有过一次包名迁移（`omni.isaac.*` → `isaacsim.*`），文中代码以 **Isaac Sim 4.5（`isaacsim.*` 命名空间）** 为基准编写，旧版本的对应关系在附录 A 给出。

---

## 0. 职责范围（TL;DR）

| 总体方案模块 | 本侧职责 | 关键产物 | 状态 |
|---|---|---|---|
| ②b 确定性支撑（兼评审方） | SceneSpec 规范/Schema、静态校验器、组件资产库；②b LLM 本体（归 gc）的评审 | 校验器 + `components/` | ✅ 首批落地（`scenegen/`：Schema/validate/build_usd/iomap/smoke/cli；回归 20 绿，见 §4.1。**本侧不含 LLM/agent**） |
| ③b Isaac Sim 仿真引擎 | json→USD 确定性构建、加载冒烟、headless lockstep 运行、IOBridge、trace 采集 | `run_sim.py` + 构建器 + iobridge | 🟨 部分（json→USD/冒烟随 scenegen ✅；Modbus 运行时桥+独立运行时+示教器 `runtime/` ✅；lockstep 主循环与 trace 待链路 A） |
| ④ 判定引擎 | 四类验收准则的确定性规则引擎，产出 `verdict.json` | `verifier/` | 🚧 未启动（设计完成，见 §7） |
| 链路 A 构建流水线 | `plc.st → iec2c → C → DLL` + shim/地址表自动生成（工具链 Docker 锁版本） | `toolchain/` | 🟨 代码就绪（shim 生成/构建编排/ctypes 绑定 + L2 全绿；**L3 真编译待 matiec+gcc 工具链**，见 §6.2.4） |
| 详细设计文档 | 本文档 | — | ✅ 完成 |

不归本侧的：②b 场景描述的 LLM 生成本体（gc，本侧评审）；③a 链路 B 部署编排与 Modbus IO（lx）；④ 的归因/反馈/编排器（gc——本文 §7.2–7.4 为职责边界+指针）。

## 1. 在总体架构中的位置

```
需求规格 requirement_spec.json
        │
        ├──► 【②b LLM 生成 · gc 负责】scene.spec.json ──► 本侧校验闸门（Schema/物理校验）
        │                                                      │
        ▼                                                      ▼
【本侧】③b：json→USD 确定性构建（组件库）──► scene.usda ──► headless lockstep 仿真（IOBridge）
        ▲                                                      │
        │ 同一份 .st（xml2st 产物）                             ▼
【本侧】链路 A 构建流水线：iec2c→C→DLL+shim ──► plc_logic.dll 进程内调用   trace/events/exit
        │                                                      ▼
        └── 链路 B（OpenPLC+Modbus，lx 负责）交叉验收 ◄── 【本侧】④ 判定引擎 ──► verdict.json
                                                               （gc 编排器消费，驱动迭代）
```

本侧是闭环的**确定性仿真与判定底座**：给 gc 的生成物提供校验闸门与运行环境，给 lx 的链路 B 提供交叉验证的另一条腿；除 ②b 评审与确定性支撑外，不触碰 LLM 与编排。

## 2. 关键技术决策（TL;DR）

| 决策点 | 结论 |
|---|---|
| 仿真资产最终格式 | **USD**（Isaac Sim 原生格式），不用 URDF 作为最终格式 |
| LLM 直接生成什么 | 不直接写 USD，而是生成**场景中间表示 SceneSpec（JSON）**，由确定性代码转换成 USD |
| URDF 的角色 | 降级为**组件库的输入格式之一**（复用现成机器人/设备 URDF，导入后转 USD 存入组件库） |
| 软 PLC 与 Isaac Sim 的耦合方式 | **matiec 把 ST 编译成 C 共享库，加载进仿真主进程，用 ctypes 逐物理步调用**（函数调用级 IO 交换，无网络开销） |
| 同步机制 | **lockstep 锁步**：每个物理步先采输入 → 跑一个 PLC 扫描 → 写输出 → 再推物理 |
| 验证判定 | **确定性规则引擎**判定通过/失败（不让 LLM 判定），LLM 只做失败归因与代码再生成 |
| 闭环载体 | 每轮迭代落盘一个目录（代码 / 场景 / IO 映射 / trace / 判定报告），反馈 Prompt 由这些产物自动拼装 |

---

## 3. 仿真资产格式选型

### 3.1 候选格式对比

| 格式 | 表达能力 | Isaac Sim 支持 | 结论 |
|---|---|---|---|
| **URDF** | 单个机器人的连杆/关节；**没有**场景、灯光、材质、传感器语义、物理场景参数 | 需经 URDF Importer 转成 USD 才能用，转换过程参数受限 | 不适合作为最终格式，仅作组件输入 |
| **MJCF** (MuJoCo) | MuJoCo 生态格式，物理参数表达强 | 无原生支持 | 排除 |
| **SDF** (SDFormat) | Gazebo 生态，场景表达较好 | 无原生支持 | 排除 |
| **glTF** | 几何/材质优秀，无工业物理语义 | 仅作可视化网格来源 | 排除 |
| **USD** | 层（layer）与引用（reference）组合、变体（variant）、`UsdPhysics`/`PhysxSchema` 物理模式、传感器、MDL 材质、非破坏式分层编辑 | **原生格式，一等公民** | ✅ 采用 |

选 USD 的三个决定性理由：

1. **表达能力完整**：一个 `.usda/.usd` 文件可以同时描述机器人、传送带、传感器、物料、地面、灯光和物理场景参数（重力、求解器设置），URDF 只能描述一台机器人；
2. **组合性**：USD 的 reference/layer 机制天然支持"基础场景层 + 每次迭代只改设备布局层"，与我们的迭代闭环（每轮重新生成场景）完美契合——基础资产不动，只重写实例层；
3. **无转换损耗**：URDF 导入是一次有损转换（惯量、驱动参数在导入配置里才能指定），直接以 USD 为源头可避免每次迭代的转换不确定性。

### 3.2 三层资产策略

```
第 1 层  SceneSpec（JSON，中间表示）
         LLM 生成的目标格式；人可读、可 diff、可校验（JSON Schema）
                │  确定性转换器（无 LLM 参与）
                ▼
第 2 层  场景 USD（.usda）
         由转换器用 pxr API 构建或拼装，是仿真实际加载的文件
                │  reference 引用
                ▼
第 3 层  组件资产库（.usd，预制作、人工校核过）
         传送带 / 气缸 / 光电传感器 / 夹爪 / 机械臂 / 标准物料箱 …
```

**为什么不让 LLM 直接写 USD**：USD 的 schema 细节多、嵌套深，直接生成正确率低且难以定位错误；而 SceneSpec 只需描述"有什么设备、放哪、参数多少、IO 怎么接"，语法面小一个数量级，出错时错误信息（JSON Schema 校验失败的具体字段）可以精准反馈给 LLM 重生成。**转换器是确定性代码，保证同样的 SceneSpec 一定产出同样的 USD，迭代行为可复现。**

---

## 4. 仿真环境生成的确定性支撑（②b）

### 4.1 生成流水线

```
结构化需求（来自需求理解模块）
   │
   ▼
① LLM 生成 SceneSpec（JSON）────────────┐
   │                                     │ 校验失败（Schema/物理参数）
   ▼                                     │
② JSON Schema 校验 + 静态物理校验 ───────┘→ 错误信息反馈 LLM 重新生成
   │ 通过
   ▼
③ SceneSpec → USD 构建器（pxr 确定性代码）
   │ 产出 scene.usda + io_map.json + scene_meta.json
   ▼
④ 场景冒烟测试（headless 加载 + 空 PLC 跑 2 秒，检查加载无错、无 NaN、设备在位）
   │ 失败 → 归因（资产缺失 / 布局穿模 / 参数非法）反馈重生成
   ▼
   交付仿真引擎使用
```

> **落地状态（2026-09-07）**：②③④ 已实现于仓库 `scenegen/`（schema/validate/build_usd/iomap/smoke/cli + components 注册表）。
> 入口：`python -m scenegen.cli all <spec>.json -o out/<场景>`；示例产物 `scenegen/out/{example,gantry}`。
> smoke 在结构检查中固化了一条黄金规则：**关节 body0/body1 必须指向 RigidBodyAPI 刚体**——
> 纯静态碰撞体作关节体会被 PhysX 整体拒用、链条散架（实机教训，见 §4.5 注）。

### 4.2 SceneSpec 规范

一个完整的示例（**三轴绘图仪**——现役仿真场景，`scenegen/examples/gantry_plotter.json` 与 `out/gantry/` 同源）：

```json
{
  "scene_id": "gantry_circle_001",
  "spec_version": "1.1",
  "units": "m",
  "physics": {
    "gravity": [
      0,
      0,
      -9.81
    ],
    "physics_dt": 0.00833,
    "solver": "tgs"
  },
  "ground": {
    "size": [
      20,
      20
    ],
    "friction": 0.8
  },
  "lighting": "warehouse_preset",
  "assets": [
    {
      "id": "gantry_1",
      "type": "gantry_xyz",
      "pose": {
        "position": [
          -0.3,
          -0.2,
          0.0
        ],
        "rpy_deg": [
          0,
          0,
          0
        ]
      },
      "params": {
        "travel_x": 0.6,
        "travel_y": 0.4,
        "travel_z": 0.2,
        "speed": 0.5
      }
    }
  ],
  "io_map": [
    {
      "plc_var": "AxisX_cmd",
      "dir": "output",
      "type": "float",
      "bind": {
        "asset": "gantry_1",
        "quantity": "x_cmd",
        "range": [
          0,
          0.6
        ]
      }
    },
    {
      "plc_var": "AxisY_cmd",
      "dir": "output",
      "type": "float",
      "bind": {
        "asset": "gantry_1",
        "quantity": "y_cmd",
        "range": [
          0,
          0.4
        ]
      }
    },
    {
      "plc_var": "AxisZ_cmd",
      "dir": "output",
      "type": "float",
      "bind": {
        "asset": "gantry_1",
        "quantity": "z_cmd",
        "range": [
          0,
          0.2
        ]
      }
    },
    {
      "plc_var": "AxisX_pos",
      "dir": "input",
      "type": "float",
      "bind": {
        "asset": "gantry_1",
        "quantity": "x_pos",
        "range": [
          0,
          0.6
        ]
      }
    },
    {
      "plc_var": "AxisY_pos",
      "dir": "input",
      "type": "float",
      "bind": {
        "asset": "gantry_1",
        "quantity": "y_pos",
        "range": [
          0,
          0.4
        ]
      }
    },
    {
      "plc_var": "AxisZ_pos",
      "dir": "input",
      "type": "float",
      "bind": {
        "asset": "gantry_1",
        "quantity": "z_pos",
        "range": [
          0,
          0.2
        ]
      }
    }
  ],
  "script": {
    "spawn_schedule": [],
    "perturbations": [],
    "termination": {
      "max_sim_time": 30.0,
      "early_stop": "none"
    }
  }
}
```

设计要点：

- **`type` 是封闭枚举**，每个取值对应组件库里的一个 USD 资产 + 一段参数校验规则，LLM 不能发明新类型（发明了会在 Schema 校验被拒，错误信息直接回喂）；
- **`io_map` 是仿真与 PLC 的单一契约**：`plc_var` 必须与 ST 代码中的变量声明一致（由代码生成模块与场景生成模块共享同一份需求规格中的 IO 清单来保证），`bind.asset + quantity` 指向组件暴露的物理量；
- **`script` 定义激励与终止**：物料何时投放、扰动注入、仿真何时结束——这使同一个场景可以反复、确定地复现，是闭环可比较的前提。

### 4.3 工业组件资产库（首批清单）

| type | 物理实现 | 暴露的 quantity（供 io_map 绑定） |
|---|---|---|
| `conveyor_belt` | 静态碰撞体 + 表面速度（对接触刚体施加带速方向的表面摩擦速度；实现为每步对接触物体设置带向速度，Omniverse 常用做法） | `run_cmd`(in), `speed_setpoint`(in), `measured_speed`(out) |
| `pneumatic_cylinder` | 基座 + 滑动副（prismatic）+ 关节驱动，速度限幅模拟气动伸出/缩回动力学 | `extend_cmd`(in), `position`(out), `at_end`(out) |
| `photoelectric_sensor` | RayCaster 光线传感器，被物料遮挡 = 检测到 | `beam_broken`(out) |
| `contact_pad` | ContactSensor 接触传感器 | `in_contact`(out) |
| `bin_chute` | 静态碰撞容器 + 区域触发器（判定物料是否入槽） | `object_inside`(out) |
| `rigid_box` | 参数化刚体（尺寸/质量/颜色） | `position`(out) |
| `vacuum_gripper` | 刚体 + SurfaceGripper（吸附/释放） | `suck_cmd`(in), `holding`(out) |
| `articular_arm` | 引用现成机械臂 USD（如 Franka），关节由 PLC 侧关节目标驱动 | `joint_cmd[i]`(in), `joint_pos[i]`(out) |
| `pid_valve` / `tank` | 一阶惯性被控对象（仿真侧自带，用于过程控制场景） | `opening`(in), `level`(out) |

组件库中每个组件附带一份**参数校验规则**（如气缸 `stroke ∈ (0, 1m]`、`extend_speed ∈ (0.01, 5]`）和一份** quantity 清单**，供 SceneSpec 校验器和 io_map 校验器使用。

> **现役场景（2026-09-07 负责人指令对齐）**：运动控制 motion3axis（PLC 侧，双链路联调基准，后续按需扩展其 USD 组件）+ 三轴绘图仪 `gantry_xyz`（本侧仿真场景）。滚筒/传送带分拣线等早期示例已删除（git 历史可回溯）；清单内其余组件为预置能力，按后续场景启用。

### 4.4 SceneSpec → USD 构建器（代码骨架）

```python
# builder.py —— 确定性转换，无 LLM 参与
from pxr import Usd, UsdGeom, UsdPhysics, Gf

COMPONENT_LIB = {
    "conveyor_belt":      "assets/components/conveyor_belt.usd",
    "pneumatic_cylinder": "assets/components/pneumatic_cylinder.usd",
    "photoelectric_sensor": "assets/components/photoelectric_sensor.usd",
    # ...
}

def build(spec: dict, out_path: str):
    stage = Usd.Stage.CreateNew(out_path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdPhysics.SetStageGravity(stage, Gf.Vec3f(*spec["physics"]["gravity"]))

    for asset in spec["assets"]:
        prim = stage.DefinePrim(f"/World/{asset['id']}", "Xform")
        prim.GetReferences().AddReference(COMPONENT_LIB[asset["type"]])
        xf = UsdGeom.Xformable(prim)
        xf.AddTranslateOp().Set(Gf.Vec3d(*asset["pose"]["position"]))
        # rpy_deg → quaternion 后 AddOrientOp().Set(...)
        for k, v in asset.get("params", {}).items():
            prim.CreateAttribute(f"params:{k}", _sdf_type(v)).Set(v)  # 组件内脚本按属性名读取

    stage.GetRootLayer().Save()
```

要点：组件 USD 内部预埋好关节驱动、传感器 prim，构建器只负责"引用 + 摆位 + 传参"，所以生成的场景永远是合法的 USD——**合法性由组件库保证，而不是靠 LLM 写对 USD**。

### 4.5 场景静态校验（转换前）

在调用构建器之前跑一遍纯 Python 检查，便宜且能拦住绝大多数生成错误：

1. JSON Schema 校验（字段齐全、类型正确、`type` 在枚举内）；
2. 引用完整性：`io_map` 绑定的 asset/quantity 存在；`parent` 指向已声明资产；
3. 布局粗查：资产包围盒两两不相交（穿模检测）；`pose` 在地面范围内；
4. 物理量纲：质量 > 0、惯量张量正定（组件库参数范围内）、速度/行程在合理区间。

校验失败的具体条目（`"cyl_1.stroke=0 超出 (0,1]"` 这类）拼进反馈 Prompt，LLM 只需做定向修改。

> **落地补充（2026-09-07）**：静态校验之外，转换后的结构冒烟（`scenegen/scenegen/smoke.py: structural_check`）
> 固化了一条黄金规则——**关节 body0/body1 必须指向带 RigidBodyAPI 的刚体**：纯静态碰撞体作关节体
> 会被 PhysX 整体拒用、整条运动链散架（实机复现过，龙门部件因此飘移/穿模）。

---

## 5. Isaac Sim 仿真引擎（③b）

### 5.1 安装形态

| 形态 | 适用 | 说明 |
|---|---|---|
| 原生安装（Omniverse Launcher / 官网安装器） | 开发调试、GUI 查看 | Windows 默认路径 `C:\Users\<user>\AppData\Local\ov\pkg\isaac-sim-4.5.0\` |
| pip 安装（4.2+） | CI、自动化闭环 | 在独立 venv/conda 中 `pip install isaacsim --extra-index-url https://pypi.nvidia.com`（版本号以官方文档为准） |
| Docker（NGC 镜像 `nvcr.io/nvidia/isaac-sim:4.5.0`） | 服务器部署、批量回归 | GPU 直通，headless 运行 |

硬件要求：需要 RTX GPU（渲染/ livestream）；headless 物理仿真对渲染无要求，但官方仍以 RTX 为最低配置。开发机建议 ≥ RTX 3060、32GB 内存。

> **当前实机基准（2026-09-07）**：Isaac Sim **Full 6.0.0**（Ubuntu + RTX 4080 SUPER，GUI 与 Script Editor 工作流已验证）。`runtime/` 脚本按 6.0 入口 `isaacsim.simulation_app.SimulationApp` 编写（附录 A）；6.0 的 API 变化（`omni.isaac.*` 垫片移除、`isaacsim.core.*` 弃用期、Python 3.12）在选型时已纳入考量。

### 5.2 四种启动方式（Windows 命令）

```bat
:: ① GUI 模式（开发调试，人工观察场景）
"C:\Users\<user>\AppData\Local\ov\pkg\isaac-sim-4.5.0\isaac-sim.bat"

:: ② 无头运行（闭环迭代主力：不开渲染窗口，速度最快）
"C:\...\isaac-sim-4.5.0\python.bat" run_sim.py --headless --scene runs/iter_001/scene.usda

:: ③ WebRTC livestream（无显示器的服务器上远程看画面，物理照跑）
"C:\...\python.bat" run_sim.py --livestream 1

:: ④ Docker headless（Linux 服务器 / 批量回归）
:: docker run --gpus all -e ACCEPT_EULA=Y --rm ^
::   -v %cd%/runs:/workspaces/runs nvcr.io/nvidia/isaac-sim:4.5.0 ^
::   ./python.sh /workspaces/run_sim.py --headless --scene /workspaces/runs/iter_001/scene.usda
```

**闭环迭代全部走 ②（headless + 独立 Python 脚本）**：不开渲染（`world.step(render=False)`），一次 30 秒物理场景通常数十秒内跑完，才能支撑一天几十上百轮迭代。GUI/livestream 只留给人工抽查和演示。

### 5.3 仿真主脚本骨架

```python
# run_sim.py —— 一次闭环仿真的完整骨架
import argparse
from isaacsim import SimulationApp          # 必须最先创建，之后才能 import 其它 isaacsim 模块
args = argparse.ArgumentParser().parse_args()
app = SimulationApp({"headless": "--headless" in sys.argv})

import omni.usd
from isaacsim.core.api import World
from isaacsim.core.prims import RigidPrim

# ---- 1. 加载生成的场景 ----
import omni.kit.commands
ctx = omni.usd.get_context()
ctx.open_stage(args.scene)

world = World(physics_dt=1/120, rendering_dt=1/30)   # physics_dt 即 lockstep 步长
world.reset()

# ---- 2. 绑定 IO（依据 io_map.json 实例化的桥接对象）----
bridge = IOBridge(args.io_map, world.stage)          # 见第 4 章
plc    = load_plc(args.plc_lib)                      # ctypes 封装的软 PLC，见第 4 章
plc.init()

# ---- 3. lockstep 主循环 ----
recorder = TraceRecorder(args.io_map)
for tick in range(args.max_ticks):
    t = tick * world.get_physics_dt()
    inject_script_events(t)                # 按 scene.spec 的 spawn_schedule 投放物料/扰动

    bridge.read_inputs()                   # 传感器/物理量 → 输入镜像
    plc.run(tick)                          # 一个 PLC 扫描周期（进程内函数调用）
    bridge.write_outputs()                 # 输出镜像 → 关节目标/带速/吸附指令

    world.step(render=False)               # 物理推进一步
    recorder.sample(t, bridge, world)      # 采样记录 trace

    if terminated(world, args):            # max_sim_time / early_stop 条件
        break

recorder.save(args.out_dir / "trace.parquet")
app.close()
```

### 5.4 传感器与执行器的仿真实现（IOBridge 内部）

```python
class IOBridge:
    """io_map 中每个条目实例化为一个绑定对象，负责双向换算。"""

    def read_inputs(self):
        for b in self.input_bindings:
            self.image[b.plc_var] = b.read(self.stage, self.world)

    def write_outputs(self):
        for b in self.output_bindings:
            b.write(self.image[b.plc_var], self.stage, self.world)
```

以典型 binding 为例：

- **光电传感器**：RayCaster 沿 `beam_direction` 发一条光线，`hit_distance < beam_length` → 有物体遮挡 → `beam_broken = True`；
- **接触传感器**：ContactSensor 的 `get_current_frame()` 取 `inContact` 布尔量；
- **气缸位置**：读 prismatic 关节的 `position`，线性映射到 io_map 声明的 `range`；
- **气缸指令**：`extend_cmd=True` → 关节驱动目标位置设为 `stroke`，目标速度设为 `extend_speed`（用速度限幅模拟气缸动力学，让"动作时间"这一指标有意义）；
- **传送带指令**：`run_cmd` 切换带速设定（0 或 `max_speed`），带体对接触物体施加表面速度；
- **夹爪**：`suck_cmd` 上升沿调用 SurfaceGripper 的 attach/detach。

这样，**PLC 看到的就是真实的物理后果**（气缸伸出需要时间、物料遮挡有先有后），验证才有意义。

> **实机排障知识（Isaac Sim 6.0 / 龙门三轴，2026-09-07）**——三条已固化为代码与回归：
> 1. **关节开场驱动目标必须等于作者位姿（零初始误差）**：authoring 了一个非零 target（如抬笔位 0.2m）而场景从 q=0 启动时，Play 瞬间误差饱和驱动全力（300N）弹射轻质量滑块，60Hz 下单步位移厘米级、隧穿限位扎穿台面。抬笔类动作一律由运行时完成；
> 2. **指令写入必须按轴速速率限制**（`runtime/stage_link.py` 按 `simio:axisSpeed` 斜坡）：GUI 按钮或 PLC 一次写入的阶跃指令同样会弹射机构；
> 3. **关节固定端用 kinematic 锚刚体**（`scenegen` `_kinematic_body`）：关节 body 引用纯静态碰撞体会被 PhysX 拒用，整链散架（同 §4.5 黄金规则）。
> 运行时桥已落地：`runtime/gantry_bridge.py`（寄存器布局由 io_map 推导）+ `stage_link.py`（stage 接线，编辑器/独立运行时共用）+ `isaac_jog_runtime.py`（免 Script Editor 的独立运行时，闭环 run_sim 骨架的同型前驱）。

### 5.5 一次仿真的输入与产物

输入：`scene.usda` + `io_map.json` + `plc 逻辑（共享库）` + `scene.spec.json`（script 部分）。
产物：

| 文件 | 内容 |
|---|---|
| `trace.parquet` | 每个 tick 的全部 IO 值 + 关节状态 + 关键物体位姿（时间序列） |
| `events.json` | 自动提取的离散事件（上升沿/下降沿、碰撞、物料入槽、超时） |
| `sim_log.txt` | Isaac 运行日志（加载错误、物理警告、NaN 等） |
| `exit.json` | 结束原因（正常完成 / 超时 / 物料掉落 / 仿真发散） |

---

## 6. IO 数据交换（链路 A / 链路 B，③a ⇄ ③b）

### 6.1 候选链路对比与选型

| 链路 | IO 交换延迟 | 确定性 | 实现工作量 | 适用 |
|---|---|---|---|---|
| **A. 进程内共享库**（matiec 编译 ST → C DLL，ctypes 调用） | 微秒级（函数调用） | 完全 lockstep，最佳 | 中（一次性搭好编译流水线） | ✅ 闭环迭代主力 |
| B. OpenPLC 软 PLC + Modbus TCP | ~1–10ms（本机） | 好（周期轮询） | 低（✅ 已落地，场景验收通过） | 工业代表性验收、真实软 PLC 运行时 |
| C. OPC UA（CODESYS / 任意软 PLC） | ~10–50ms | 一般 | 中 | 需要开放互操作时 |
| D. ROS 2 bridge（Isaac 原生 `ros2_bridge`） | ~5–20ms | 一般 | 中 | 已有 ROS 2 生态的团队 |

**选型：A 为主链路（开发和 CI 闭环），B 为验收链路（证明代码能在工业级软 PLC 上跑）。** A 的关键优势是 **lockstep 完全可控**——PLC 扫描和物理步进在同一个循环里顺序执行，不存在网络抖动导致的时序歧义，失败归因时可以排除通信因素。两条链路跑的是同一份 ST 代码，只是运行时不同。

### 6.2 主链路 A：matiec 编译 ST → C 共享库 → 进程内调用

#### 6.2.1 ST 侧的约定

生成的 ST 遵循固定骨架（CONFIGURATION/任务配置由 xml2st 统一装配，见 lx 文档 §3.3，不手写）：IO 全部声明为**定位变量（located variables）**，地址与 `io_map.json` 一一对应。**统一 IO 约定（双链路一致，契约见 lx 文档 §3.1）**：主 POU 名固定 `PLC_PRG`；对外 IO 一律 `%Q` 区——**方向（输入/输出）由 io_map 声明，不由地址前缀表达**；模拟量一律 `INT @ %QW` + 定点换算（系数写入 io_map）——`REAL` 与 `%I` 区仅链路 A 技术上可行，为保证两条链路跑同一份代码而统一弃用：

```iecst
PROGRAM PLC_PRG
  VAR   (* 输入：传感器，由仿真/验收侧写入注入 *)
    PE1_detected AT %QX0.0 : BOOL;    (* 光电传感器 → 线圈 0 *)
    Cyl1_pos     AT %QW0   : INT;     (* 气缸位置反馈，定点 0.1mm/LSB *)
    Belt1_speed  AT %QW1   : INT;     (* 带速反馈 *)
  END_VAR
  VAR   (* 输出：PLC → 仿真 *)
    Cyl1_extend AT %QX1.0 : BOOL;     (* 气缸推出 → 线圈 8 *)
    Belt1_run   AT %QX1.1 : BOOL;     (* 传送带运行 → 线圈 9 *)
  END_VAR
  (* —— 控制逻辑 —— *)
  ...
END_PROGRAM
```

#### 6.2.2 编译流水线

```
plc_project.xml ──(xml2st 校验+转换，复用 PLC 侧，转换点唯一)──> plc.st
        ──(matiec iec2c，输入为 ST 文本，如 iec2c -f -l -p Cfg plc.st)──>  POUS.c / POUS.h / accessor.h ...
        ──(gcc/clang 编译为共享库)──>  plc_logic.dll（Windows）/ plc_logic.so（Linux）
```

说明：

- matiec（Beremiz 项目的 IEC 61131-3 编译器，开源）的 iec2c **输入是 ST 文本**（官方说明：接受 ST/IL/SFC 文本，不解析 XML）；XML→ST 统一由 PLC 侧 xml2st 完成——**两条链路编译的是同一份 .st 产物**（链路 B 的 OpenPLC 内置 matiec，编译的正是同一份转换结果），转换点唯一，杜绝双链路语义漂移；
- 编译在 WSL/Linux 下最顺（gcc 工具链现成）；Windows 侧可用 MinGW 交叉产出 `.dll`，或整个闭环在 Docker 里跑；
- matiec 生成代码的符号命名（定位变量对应的 C 符号、init/run 函数签名）在不同版本间略有差异，**因此必须有一层 shim 把这些差异隔离掉**，见下。

#### 6.2.3 C shim 与 ctypes 绑定（核心代码）

```c
/* plc_shim.c —— 把 matiec 生成代码封装成稳定接口，隔离版本差异 */
#include "config.h"
#include "POUS.h"
#include "accessor.h"

/* matiec 生成的配置入口（不同版本签名可能带后缀，shim 内适配） */
extern void config_init__(void);
extern void config_run__(unsigned long tick);

/* 定位变量在生成代码中即 C 外部符号（__QX0_0 / __QW0 风格），
   适配层按地址表逐个引用，上层只认下面的稳定接口 */
void plc_init(void)            { config_init__(); }
void plc_run(unsigned long t)  { config_run__(t); }

/* 显式 IO 镜像：由构建脚本按 io_map 生成的地址表直接读写定位变量 */
extern BOOL __QX0_0;                      /* PE1_detected（输入） */
extern INT  __QW0;  extern INT  __QW1;    /* Cyl1_pos / Belt1_speed（输入） */
extern BOOL __QX1_0; extern BOOL __QX1_1; /* Cyl1_extend / Belt1_run（输出） */

void plc_write_image(const uint8_t* di, const int16_t* ai) {
    __QX0_0 = di[0];                      /* 传感器注入 */
    __QW0   = ai[0];  __QW1 = ai[1];
}
void plc_read_image(uint8_t* dq, int16_t* aq) {
    dq[0] = __QX1_0;  dq[1] = __QX1_1;    /* 输出 */
}
```

```python
# plc_binding.py —— Python 侧 ctypes 封装
import ctypes, pathlib

class SoftPLC:
    def __init__(self, lib_path: str, io_layout: dict):
        self.lib = ctypes.CDLL(lib_path)
        self.lib.plc_init.argtypes = []
        self.lib.plc_run.argtypes  = [ctypes.c_ulong]
        self.lib.plc_write_image.argtypes = [ctypes.POINTER(ctypes.c_uint8),
                                             ctypes.POINTER(ctypes.c_int16)]
        self.lib.plc_read_image.argtypes  = [ctypes.POINTER(ctypes.c_uint8),
                                             ctypes.POINTER(ctypes.c_int16)]
        self.layout = io_layout          # 由 io_map.json 生成的通道表

    def init(self): self.lib.plc_init()

    def run(self, tick: int, image: dict) -> dict:
        di = (ctypes.c_uint8 * self.layout.n_di)(*image["_di"])
        ai = (ctypes.c_int16 * self.layout.n_ai)(*image["_ai"])
        self.lib.plc_write_image(di, ai)      # ① 写输入
        self.lib.plc_run(tick)                # ② 一个扫描周期
        dq = (ctypes.c_uint8 * self.layout.n_dq)()
        aq = (ctypes.c_int16 * self.layout.n_aq)()
        self.lib.plc_read_image(dq, aq)       # ③ 读输出
        return self.layout.unpack(dq, aq)
```

> shim 中的地址表（`__QX0_0` / `__QW0` 等）由构建脚本从 `io_map.json` 自动生成，**不手写**；shim 这个文件本身就是代码生成模块的产物之一。

#### 6.2.4 落地状态与契约③（2026-09-07）

**代码已落地（L2 层全绿，L3 待工具链）**：

| 件 | 位置 | 说明 |
|---|---|---|
| shim 生成器 | `toolchain/shim_gen.py` | io_map → `plc_shim.c/.h`（extern 符号表 + 紧凑镜像 di/ai/dq/aq + 稳定接口）；符号风格 `__QX0_0`/`__QW0` 是 matiec 版本差异的**隔离点**（`SYMBOL_RULES` 一处可调） |
| 构建编排 | `toolchain/build_dll.py` | xml→st（复用 lx xml2st，转换点唯一）→ iec2c → shim → gcc 共享库；工具链经 `MATEC`/`CC`/PATH 发现，缺失时优雅降级 + 可操作提示；结果落 `build_result.json` 供编排器消费 |
| ctypes 绑定 | `runtime/plc_binding.py` | `IOLayout`（镜像索引/定点换算/打包，纯逻辑）+ `SoftPLC`（init/run/write_inputs/read_outputs）；索引规则与 shim **双实现 golden 测试锁定** |
| 分层测试 | `toolchain/tests/test_link_a.py` | L2：golden/一致性/定点换算/Schema 校验/降级（本机全绿）；L3：minimal.st → DLL → 写读回环（**缺 matiec+gcc 自动 SKIP**，在 WSL/Docker/工具链机上执行——`MATEC_ROOT` 指向 matiec 根目录） |

**契约③ io_map 结构（v1.0.0-draft.1，`schemas/io_map.schema.json`，待三方评审冻结）**：
每条记录 `plc_var`（≡ ST 定位变量 ≡ io_list，R1/R2 对齐键）/ `dir`（input=传感注入、output=PLC 指令，统一 %Q 区不由前缀表达）/ `type`（bool→%QX；analog→%QW 定点 INT16，`scale`=每 LSB 工程量；word→%QW 原始 16 位如 CiA402 状态字）/ `modbus`（链路 B 地址，scenegen 确定性分配）/ `bind`+`usd_prim`（③b 绑定）。`modbus.encoding` 现状 float32_be、目标 int16_be——**随共同议题"float32/INT16 换算归属"定稿按 §8.3 RFC 收敛**，draft 期并存。约束：%QD 禁用、%I 区不进 io_map（契约②）。

### 6.3 lockstep 时序同步

```
每个物理步（physics_dt = 1/120 s ≈ 8.3ms）：
  t=0.000  ① IOBridge.read_inputs()        传感器 → 输入镜像        (~0.1ms)
  t=0.000  ② plc_run(tick)                 ST 扫描（进程内调用）    (~0.01ms)
  t=0.000  ③ IOBridge.write_outputs()      输出镜像 → 驱动目标      (~0.1ms)
  t=0.000  ④ world.step(render=False)      物理推进一步             (~2–10ms)
  ...重复
```

约定与说明：

- **1 个物理步 = 1 个 PLC 扫描**，扫描周期即 `physics_dt`。这模拟的是一台扫描周期 8.3ms 的 PLC，对绝大多数工厂级逻辑（秒级动作）远超真实需求；若被控对象带宽高（伺服同步），把 `physics_dt` 调到 1/500 以上即可，同一框架无需改动；
- 若要模拟**慢扫描 PLC**（如 10ms/20ms 扫描），按 `tick % N == 0` 降频调用 `plc_run`，输入输出在两次扫描之间保持（零阶保持），更贴近真实行为；
- 由于 采集/扫描/写输出 在物理步进之前顺序执行，**PLC 与物理之间不存在竞态与时钟漂移**，trace 中的时序可以逐 tick 精确对账——这是失败归因可靠性的基础。

### 6.4 备选链路 B：OpenPLC 软 PLC + Modbus TCP（工业验收用，已落地）

拓扑：`OpenPLC v3 运行时（Modbus TCP 服务端 :502）⇄ pymodbus 客户端（Isaac/验证侧，周期轮询）`

- OpenPLC 以 Docker 部署（`fdamador/openplc`，Web API :8080 / Modbus TCP :502），部署编排复用 PLC 侧已实现的 HTTP 流水线（xml2st 校验 → 上传 → 内置 matiec 编译 → 启动，含 POST /deploy 服务化端点）；
- IO 映射：`%QX` → Modbus 线圈、`%QW` → 保持寄存器；验证/桥接侧按 `io_map.json` 地址表读输出、写传感器注入（同为 %Q 区）；
- **线圈写入红线**：OpenPLC 的 Modbus 服务端在窄范围线圈写入时会破坏相邻位，必须经 PLC 侧 `modbus_io.SafeCoilIO`（读-改-写整组）访问，禁止裸 write_coil；
- 轮询周期 10ms 量级即可（工厂级逻辑对抖动不敏感）；本机延迟约 1–10ms。

此链路用于**最终轮验收**：证明生成的代码在真实软 PLC 运行时上可编译、可运行、行为一致（motion3axis 三轴运动控制场景已按此链路验收通过）。A/B 两条链路共用同一份 XML→.st 产物与 io_map，差异只在运行时。

---

## 7. 闭环验证与判定引擎（④）

### 7.1 判定引擎：验收准则的机器可读表示

需求理解模块输出的验收准则落成如下结构（与 SceneSpec 同一需求规格的两个视图）：

```json
{
  "acceptance": [
    { "id": "AC1", "desc": "光电检测到箱体后 0.5s 内气缸推出",
      "type": "event_delay",
      "from": { "signal": "PE1_detected", "edge": "rising" },
      "to":   { "signal": "Cyl1_extend",  "edge": "rising" },
      "op": "<=", "value": 0.5, "unit": "s" },

    { "id": "AC2", "desc": "箱体最终落入料槽",
      "type": "region_containment",
      "asset": "box_a", "region_center": [2.0, 0.8, 0.15], "tolerance": 0.2,
      "check_at": "end" },

    { "id": "AC3", "desc": "无检测信号时气缸禁止动作（安全联锁）",
      "type": "forbidden_state",
      "when": { "signal": "PE1_detected", "equals": false },
      "forbid": { "signal": "Cyl1_extend", "equals": true } },

    { "id": "AC4", "desc": "仿真无发散、物料未掉出地面",
      "type": "sim_health" }
  ]
}
```

判定器是**纯确定性的规则引擎**（每个 `type` 对应一个对 trace 的检查函数，基于 pandas 实现），输出：

```json
{ "verdict": "FAIL", "passed": ["AC2", "AC4"], "failed": ["AC1", "AC3"],
  "details": [
    { "id": "AC1", "evidence": "t=4.286s PE1 上升沿 → t=5.431s Cyl1 上升沿，延迟 1.145s > 0.5s" },
    { "id": "AC3", "evidence": "t=1.033s~1.212s 期间 PE1=false 且 Cyl1_extend=true" }
  ] }
```

**为什么不 letting LLM 判定**：通过/失败必须是可复现的客观事实。LLM 负责的是下一环节——拿着这份确定性证据做归因和改代码。

### 7.2 失败归因与反馈 Prompt 的组织

**归因、反馈包拼装与路由的实现归 gc**（权威定义见《gc-需求理解与闭环编排详细设计》§3.2 / §4）。本侧职责边界：只产出 §7.1 的确定性 verdict 证据，**不参与归因**；归因所需的 trace 窗口截取（±1s）由本侧 trace 工具提供接口。

### 7.3 迭代管理与终止条件

**迭代管理（runs/ 产物目录、终止条件、best-effort）归 gc**（权威定义见 gc 文档 §4）。本侧只约定产物格式：`trace.parquet / events.json / exit.json` 的通道与字段见 §5.5，`verdict.json` 见 §7.1。

### 7.4 端到端编排

**solve() 闭环循环的权威定义在 gc 文档 §4**（编排器实现归 gc）。本侧在该循环中暴露的接口契约（均定义于本文档各节）：

| 接口 | 定义处 |
|---|---|
| `gen_scene_spec(spec, history)`（SceneSpec LLM 生成） | §4.1 / §4.2 |
| `validate_scene(scene)`（Schema + 物理校验） | §4.5 |
| `build_usd(scene)` → `scene.usda + io_map.json` | §4.4 |
| `run_isaac_headless(usd, io_map, dll)` → trace/events/exit | §5.3 / §5.5 |
| `evaluate(acceptance, trace, ...)` → `verdict.json` | §7.1 |

---

## 8. 工程目录与依赖

目标布局（sim-loop）与**当前落地对照**（2026-09-07，monorepo 根目录）：

```
sim-loop/（目标布局）                    本仓现状
├── orchestrator/          # 端到端编排、迭代管理          → （gc 侧 src/agent/orchestrator）
├── codegen/               # xml2st 接入、shim/地址表生成   → ⬜ 链路 A 未启动（xml2st 复用 lx src/pipeline）
├── scenegen/              # SceneSpec Schema、校验器、USD 构建器
│                          → ✅ 本仓 scenegen/：scenegen/{schema.json,components.py,validate.py,
│                             build_usd.py,iomap.py,smoke.py,cli.py} + out/ 产物（不含 LLM/agent——②b 本体归 gc）
├── components/            # 组件 USD 资产库 + quantity 清单 → ✅ 程序化构建（components.py 注册表，9 类）
├── runtime/
│   ├── run_sim.py         # Isaac headless 主脚本（lockstep 循环）→ ⬜ 待链路 A（同型前驱 isaac_jog_runtime.py ✅）
│   ├── iobridge/          # IOBridge 各类 binding          → 🟨 Modbus 桥版 stage_link.py ✅
│   └── plc_binding.py     # ctypes 封装                    → ⬜ 链路 A
│                          → ✅ 另有 gantry_bridge.py/isaac_modbus_server.py/isaac_jog_runtime.py/
│                             gantry_jog_gui.py（Modbus TCP 桥 + 示教器，tests/ 回环 9 项）
├── verifier/              # 判定引擎 + trace 分析 + 反馈 Prompt 拼装 → ⬜ 未启动
├── toolchain/             # matiec 构建脚本、Dockerfile      → ⬜ 链路 A
└── runs/                  # 迭代产物（git 管理）             → scenegen/out/（场景构建产物）
```

依赖：Isaac Sim **6.0**（实机基准，Full 安装；pip 元包见 §5.1）、matiec（Beremiz 项目，链路 A）、
gcc/MinGW 或 WSL（链路 A）、Python 3.12（Isaac 6.0 强制；scenegen 3.10+ 亦可）——
scenegen：usd-core / jsonschema（`scenegen/requirements.txt`）；runtime：**pymodbus>=3.7,<3.9**
（服务端从站 API 锁定，`runtime/requirements.txt`）；pandas / pyarrow（判定引擎用，待实现）、
OpenPLC v3 Docker 镜像（仅验收链路）。

---

## 9. 实施计划与待办

| 时间 | 目标 | 验收标志 |
|---|---|---|
| D1–2 | 手工制作首个场景（✅ 已由三轴绘图仪替代推进：gantry_xyz 场景 + runtime 示教链路） | headless 跑完并出 trace |
| D3–4 | matiec 流水线打通：示例 ST → DLL → ctypes 在循环内 lockstep 跑 | 逻辑改动能反映到仿真行为 |
| D5–7 | SceneSpec Schema + 构建器 + 校验器；LLM 接入生成 SceneSpec | LLM 生成的场景加载成功 |
| D8–10 | 判定引擎 4 种准则类型 + 反馈 Prompt 拼装 | 人为埋错能被正确判 FAIL 并归因 |
| D11–14 | 编排器串起全流程，跑通"故意给错代码 → 闭环修正 → 通过"的演示 | 无人干预完成一次收敛 |

### 待办（按优先级）

1. **D3–4 链路 A（代码就绪，待工具链 L3）**：shim 生成/构建编排/ctypes 绑定已落地（§6.2.4），在 WSL/Docker/工具链机上设 `MATEC_ROOT` 跑 L3 回环（`toolchain/tests/test_link_a.py`），通过即通知 **lx 启动 motion3axis 双链路比对**；
2. **真机复验收尾**：龙门场景 Play 稳定性与示教（joint_z 弹射穿纸已修复，待 Isaac 实机确认）；随后按 §5.3 骨架把 `isaac_jog_runtime.py` 扩展为带 trace 采集的 `run_sim.py`；
3. SceneSpec Schema/校验器已落地（`scenegen/`），待与 gc 场景描述生成器对接联调 + acceptance 结构确认冻结；
4. `io_map` 契约③定稿：实现样例已出（`scenegen/scenegen/iomap.py` + `out/gantry/io_map.json`），**编码（float32 vs 桥侧 INT16 定点）随共同议题"float32/INT16 换算归属"定稿后按 §8.3 RFC 同步**；
5. 判定引擎四类准则实现（verdict 结构底稿见 §7.1）；
6. 与 lx 双链路联调（motion3axis 场景 A/B trace 比对，主方案风险表"双链路行为不一致"的应对）。

---

## 附录 A：Isaac Sim 版本 API 对照

| 功能 | Isaac Sim ≤ 4.1 | Isaac Sim ≥ 4.2 | Isaac Sim 6.0（**本仓 runtime 基准**） |
|---|---|---|---|
| 应用入口 | `from omni.isaac.kit import SimulationApp` | `from isaacsim import SimulationApp` | `from isaacsim.simulation_app import SimulationApp`（`runtime/isaac_jog_runtime.py` 采用） |
| World | `omni.isaac.core.api.World` | `isaacsim.core.api.World` | 同左（弃用期仍可用，新方向 `isaacsim.core.experimental.*`） |
| 传感器 | `omni.isaac.sensor` / `omni.isaac.core.api` | `isaacsim.core.api.sensors` | 迁移至 `isaacsim.sensors.experimental.*` |
| URDF 导入 | 扩展 `omni.isaac.urdf_importer` | 扩展 `isaacsim.asset_importer.urdf` | 同左（`fix_base` 三态） |
| Python | 3.10 | 3.10 / 3.11 | **3.12** |

## 附录 B：遗留决策点

1. **传送带物理实现**首选"接触物体表面速度注入"，若高速场景物料打滑失真，再换履带关节方案；
2. **扫描周期**默认与物理步同步（8.3ms），是否需要模拟 10ms 慢扫描 PLC 视验收准则的时序精度要求决定；
3. 组件库从 7 类起步（belt/cylinder/PE/chute/box/gripper/arm），按场景需求逐步扩充，`pid_valve/tank` 等过程控制组件放二期。
