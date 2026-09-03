# lx 开发日志（仅个人分支，合入 master 前移除）

> 只记代码技术说明：改了什么 / 为什么 / 怎么验证 / 踩坑解法。进度协调看板，不写这里。

## 2026-09-03 评审响应会话

### 1. gc 编排器闸门4 消费语义复核（✅ 确认）

对象：`src/agent/orchestrator.py` `acceptance_gate()`（master a7e4bcc）。

复核结论与理由：
- **require_program 内置于 scenario 脚本**是正确位置：身份校验（%QW20 prog_id）与验收行为断言在同一执行体内，编排器子进程 exit code 即全部语义，无需编排器重复实现；
- **skipped/failed 划分**：连接期失败（stdout 含"无法连接"/ConnectionError）记 skipped，运行期失败回喂末 40 行。scenario 脚本的 Modbus 连接发生在 `connect()`（一切断言之前），所以连接错误必然是"环境不在线"而非"行为失败"，划分可靠；运行中途掉线会以非连接类异常退出 → failed 回喂，语义正确；
- 无异议，已在看板 gc 区块标 ✅。

### 2. INT16 域落实复核（✅ 方向正确 + 收紧建议）

对象：`src/agent/spec_validator.py` S2（WORD_DOMAIN = [-32768, 65535]）。

发现一个边界缺口：S2 用 INT16/UINT16 **并集域**做界，允许跨域混合量程（如 `[-100, 65535]`）通过。但单个 16 位 %QW 字只能选一种符号解释：有符号顶 32767，无符号底 0——跨域量程没有一致的表达方式，io_map 定点换算会二义。

收紧建议（已提 gc 评估入 draft.3）：range 须**完整落入 [-32768, 32767] 或 [0, 65535] 之一**。motion3axis 现有 x_sw [0,65535]（UINT 满量程）与位置量程（有符号）在新规则下均不受影响。

### 3. csk 龙门 Modbus runtime 评审（f85ca48）

对象：csk 分支 `runtime/`（gantry_bridge.py + isaac_modbus_server.py，pymodbus<3.9 服务端）。

- 设计 ✅：Isaac 侧 Modbus 服务端 :5020，传感区 [0,6) + 指令区 [6,12)，示教器/无头回环测试齐全，断线重连有回归；
- **摩擦点（已登记共同议题）**：线上格式 float32 大端（2 寄存器/值）vs 契约② {BOOL,INT} 16 位字域。lx 建议**换算归桥侧**：gantry_bridge 按 io_map 量程做 float↔定点 INT16，PLC 侧 ST 保持 16 位字（避免 REAL 位重组——matiec 里 WORD 对拼 REAL 要移位+或+类型转换，绕且易踩坑）。若坚持 float32 直上线，须 RFC 扩契约②（%QD/REAL），首版不推荐；
- **对接面备忘**：闭环时 OpenPLC 需配 modbus_client 轮询（FC03 读 :5020 反馈区 → %IW、FC16 写指令区 ← %QW）。这与当前链路 B 验收（脚本扮被控对象直写 OpenPLC :502）方向相反但兼容——OpenPLC 同时作 :502 服务端与 :5020 客户端。桥定型后出一页配置 recipe。

### 验证

- pytest 100/100 全绿（评审未动代码，基线确认）；
- 本轮无代码变更，仅看板 + devlog；lx 分支 7 提交推送同步 origin/lx。
