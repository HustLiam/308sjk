# runtime —— Isaac Sim 运行时与龙门示教器（Modbus TCP）

本目录存放让仿真场景"活起来"的运行时脚本。链路与《总体实施方案》§3.4.2 /
csk 文档 §4 一致：**Modbus TCP，float32 大端**，寄存器布局由 scenegen 的
`io_map.json` 推导（传感区块 `[0, 6)`，指令区紧随其后）。

```
gantry_jog_gui.py ──FC16 写指令──► :5020 ──关节驱动──► gantry 场景
（Modbus 客户端） ◄─FC03 读反馈──                        （Isaac 进程内）
                    GantryBridge + StageLink（gantry_bridge.py / stage_link.py）
```

指令来源可以是示教器（手动）或 OpenPLC %QW 桥（闭环）；OpenPLC 侧轮询配置照抄
`scenegen/out/gantry/modbus_summary.json`（FC03 读 :5020 保持寄存器 0..5 → %IW0）。

## 文件

| 文件 | 说明 |
|---|---|
| `gantry_bridge.py` | 回环桥核心：布局推导 + pymodbus 服务端 + 数据面（可脱离 Isaac 单测） |
| `stage_link.py` | stage ⇄ 桥接线（simio 标记 + io_map；编辑器与独立运行时共用） |
| `isaac_jog_runtime.py` | **独立运行时（推荐）**：一个进程承载物理仿真 + Modbus 服务端，无需 Script Editor |
| `isaac_modbus_server.py` | Script Editor 粘贴脚本（GUI 编辑器工作流，与独立运行时二选一） |
| `mujoco_build.py` | SceneSpec → MuJoCo MJCF 组装器（gantry_xyz 组件；与 USD 链路同源 spec） |
| `mujoco_jog_runtime.py` | **MuJoCo 独立运行时（轻量备选后端）**：仿真 + Modbus 服务端，秒级启动、无需 Isaac |
| `gantry_jog_gui.py` | 龙门三轴鼠标示教器（拖画笔写 X/Y 指令，Z 抬/落笔，反馈回读） |
| `tests/` | 无头测试（桥回环 + StageLink 真场景接线 + MuJoCo 闭环；独立脚本或 pytest，不需要 Isaac） |
| `legacy_opcua/` | 已废弃的 OPC UA 时期实现（asyncua），仅 v4 备选链路评估时参考 |

## 工作流 A：独立运行时（无编辑器，推荐）

```bash
python isaac_jog_runtime.py --scene ../scenegen/out/gantry/scene.usda           # 无头最快
python isaac_jog_runtime.py --scene ... --window                                # 带 Isaac 窗口观察
python gantry_jog_gui.py --host <Isaac主机IP>                                   # 另一终端示教
```

进程自己启动 SimulationApp、加载场景、逐物理步推 World，并把指令寄存器写入关节
驱动目标、轴位置回写反馈寄存器——**不开 Isaac GUI、不碰 Script Editor**。这也是
闭环的正式形态（csk 文档 §3.3 run_sim 骨架），将来 OpenPLC 桥只是把"指令来源"
从示教器换成 %QW 客户端，本脚本不变。停止：Ctrl+C。

## 工作流 B：Script Editor（GUI 编辑器内联，适合边看边调）

1. Isaac Sim 6.0 打开 `scene.usda`，按 **Play**；
2. Script Editor 粘贴运行 `isaac_modbus_server.py`（首次自动 pipapi 安装
   `pymodbus<3.9`；粘贴运行前把脚本内 `HERE` 兜底路径改成 runtime 目录）；
3. 同机或局域网运行 `python gantry_jog_gui.py --host <Isaac主机IP>`。

## 工作流 C：MuJoCo 轻量后端（无 Isaac，秒级启动）

```bash
pip install mujoco>=3.2                                  # 一次安装（几 MB，无 GPU/许可证依赖）
python mujoco_jog_runtime.py --scene ../scenegen/out/gantry/scene.spec.json --watch
python gantry_jog_gui.py                                  # 示教器零改动直接连（:5020 同端口同布局）
```

`mujoco_build.py` 消费与 USD 链路**同一份** scene.spec.json 组装 MJCF（三段滑动关节链
+ 位置执行器 + 行程限位，质量/kp/阻尼与 USD 侧同源），运行时主循环与工作流 A 同构：
指令寄存器 →（axisSpeed 速率限制）→ 执行器 ctrl；关节 qpos → 反馈寄存器。寄存器布局、
钳位、Z 轴语义（0=落笔，travel=抬笔）、开场抬笔全部复用 `gantry_bridge` 约定——
**PLC 侧与示教器零改动即可切换仿真后端**。适用：io 闭环回归、PLC 代码验证的快速迭代、
无 GPU 机器。Isaac 工作流保留用于高保真渲染/传感器场景。

## 寄存器表（out/gantry 场景，float32 大端，2 寄存器/值）

| 寄存器 | 含义 | 写方 |
|---|---|---|
| 0–1 / 2–3 / 4–5 | AxisX/Y/Z_pos 位置反馈（米） | Isaac 每帧写 |
| 6–7 / 8–9 / 10–11 | AxisX/Y/Z_cmd 轴指令（米，超程钳位） | 示教器或 OpenPLC 桥 |

Z 轴语义：`0 = 落笔（笔尖距纸面 2mm，场景开场静置位）`，`travel_z = 抬笔`。
所有指令经 **axisSpeed（默认 0.5 m/s）速率限制**写入驱动目标——寄存器阶跃不会
弹射机构（实机教训：开场非零驱动目标曾把滑块弹穿纸面）。两个工作流开场指令均为
"X/Y 原点 + Z 抬笔"，Play 后笔在 ~0.4s 内平滑抬起到位；客户端连上后由它接管。
X/Y 指令 0 = 行程原点（左下角），与示教器画布一致。

## 验证

```bash
python tests/test_modbus_loop.py          # 桥回环 6 项（不需要 usd-core）
python tests/test_stage_link.py           # StageLink 真场景接线 2 项（需要 usd-core）
python tests/test_mujoco_loop.py          # MuJoCo 闭环 5 项（需要 mujoco）
# 或 python -m pytest tests/ -v
```

覆盖：地址推导、FC16 写指令→桥读取、超程钳位、反馈 FC03 回读、断开重连、
迷你闭环；StageLink 用 usd-core 打开仓库 scene.usda 验证「指令→关节驱动属性→
位置回读」整条链（无需 Isaac）。
