# runtime —— Isaac Sim 运行时与龙门示教器（Modbus TCP）

本目录存放让仿真场景"活起来"的运行时脚本。链路与《总体实施方案》§3.4.2 /
csk 文档 §4 一致：**Modbus TCP，float32 大端**，寄存器布局由 scenegen 的
`io_map.json` 推导（传感区块 `[0, 6)`，指令区紧随其后）。

```
gantry_jog_gui.py ──FC16 写指令──► :5020 ──关节驱动──► gantry 场景
（Modbus 客户端） ◄─FC03 读反馈──                        （Isaac Sim 内）
            isaac_modbus_server.py = GantryBridge（gantry_bridge.py 核心）
```

指令来源可以是示教器（手动）或 OpenPLC %QW 桥（闭环）；OpenPLC 侧轮询配置照抄
`scenegen/out/gantry/modbus_summary.json`（FC03 读 :5020 保持寄存器 0..5 → %IW0）。

## 文件

| 文件 | 说明 |
|---|---|
| `gantry_bridge.py` | 回环桥核心：布局推导 + pymodbus 服务端 + 数据面（可脱离 Isaac 单测） |
| `isaac_modbus_server.py` | Isaac Sim Script Editor 接线脚本（场景 ⇄ 寄存器） |
| `gantry_jog_gui.py` | 龙门三轴鼠标示教器（拖画笔写 X/Y 指令，Z 抬/落笔，反馈回读） |
| `tests/test_modbus_loop.py` | 无头回环测试（独立脚本或 pytest，不需要 Isaac/usd-core） |
| `legacy_opcua/` | 已废弃的 OPC UA 时期实现（asyncua），仅 v4 备选链路评估时参考 |

## 使用步骤

1. Isaac Sim 6.0 打开 `scenegen/out/gantry/scene.usda`（**重生成后的版本**），按 **Play**；
2. Script Editor 粘贴运行 `isaac_modbus_server.py`（首次自动 pipapi 安装
   `pymodbus<3.9`；粘贴运行前把脚本内 `HERE` 兜底路径改成 runtime 目录），
   Console 出现 "Modbus server ready" 与寄存器表；
3. 同机或局域网运行 `python gantry_jog_gui.py --host <Isaac主机IP>`，
   拖动画笔驱动 X/Y，"落笔/抬笔"按钮控制 Z。画笔显示**反馈寄存器的实际位置**，
   与目标点偏差即跟随误差，直观可见。

依赖：`pip install -r requirements.txt`（服务端锁 `pymodbus>=3.7,<3.9`——
3.13+ 移除了从站读写 API；GUI 客户端任意 3.x）。

## 寄存器表（out/gantry 场景，float32 大端，2 寄存器/值）

| 寄存器 | 含义 | 写方 |
|---|---|---|
| 0–1 / 2–3 / 4–5 | AxisX/Y/Z_pos 位置反馈（米） | Isaac 每帧写 |
| 6–7 / 8–9 / 10–11 | AxisX/Y/Z_cmd 轴指令（米，超程钳位） | 示教器或 OpenPLC 桥 |

Z 轴语义：`0 = 落笔（笔尖贴纸面）`，`travel_z = 抬笔`；场景开场为抬笔位。
X/Y 指令 0 = 行程原点（左下角），与示教器画布一致。

## 验证

```bash
python tests/test_modbus_loop.py          # 或 python -m pytest tests/ -v
```

覆盖：地址推导（对照仓库 io_map）、FC16 写指令→桥读取、超程钳位、反馈 FC03 回读、
断开重连（asyncua 时代痛点回归）、迷你闭环（指令→跟随→反馈）。
