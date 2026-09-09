# lx 开发日志（仅个人分支，合入 master 前移除）

> 只记代码技术说明：改了什么 / 为什么 / 怎么验证 / 踩坑解法。进度协调看板，不写这里。

## 2026-09-09 同步 + 链路 A L3 打通会话

### 0. 背景与同步

本地落后远端一周（lx 停在 09-03）：期间 csk 落地 ②b/③b + 链路 A v0 + 契约③ io_map draft.1（**点名 lx 评审**）+ SceneSpec 契约包 v1.1；gc 分支推进画圆场景 LLM 闭环（plotter3axis/plotter_circle 两新场景、R6 地址腿、pytest 149，未合 master）。合并 origin/master 进 lx（干净无冲突），host pytest 100/100 基线绿。

### 1. 链路 A L3 真编译回环打通（csk 点名请求，6/6 全绿）

**环境**（Windows 无可用 WSL/Linux 机，全程 Docker，recipe 已固化 lx 文档 §6.1）：
- matiec 来源：运行中的 openplc 容器 `/workdir/utils/matiec_src/`（含预编译 iec2c + lib/）→ docker cp 到 workspace/matiec_toolchain/（不入库）；
- 执行容器：`python:3.12-slim` + `apt install gcc libc6-dev`（slim 不带 libc 头文件，`limits.h` include_next 落空是缺 libc6-dev 的典型症状）+ pip jsonschema；
- matiec 以**原路径** `/workdir/utils/matiec_src` 挂进容器（防 iec2c 内部路径假设）。

**Docker 三坑**（各浪费一轮）：
1. `fdamador/openplc` 的 ENTRYPOINT 直接起 webserver——`docker run image bash -c ...` 的命令被吞、容器悬挂输出 Flask 日志。必须 `--entrypoint bash`；
2. openplc 镜像 = Debian 9 stretch（EOL）——apt 源 404，别想在里面装 python3（且 stretch 的 python3.5 跑不了 f-strings）；
3. Windows bind mount 源路径**反斜杠第二块挂载会静默变空目录**（第一块碰巧正常，极具迷惑性）——一律正斜杠 `D:/...`。

**为通过 L3 修正 csk 侧四处**（均为"从未真跑过"的偏差，L2 纯字符串断言暴露不了；OpenPLC 内置 matiec 2019 实测行为）：

(a) `toolchain/tests/fixtures/minimal.st`：
- CONFIGURATION 从文件头移到 POU 之后——该 matiec 单趟解析符号，前置 CONFIGURATION 引用未定义的 PLC_PRG 报 `invalid program type name`（xml2st 装配模板正是 POU 后追加配置，见 lx 契约② §3.3）；
- `VAR_OUTPUT` → 普通 `VAR`——定位变量（AT）在 PROGRAM 的 VAR_OUTPUT 段被拒（`unknown error in output variable(s) declaration`）。已知可用产物 workspace/program.st（motion3axis 部署编译过的）全部用普通 VAR。

(b) `toolchain/tests/fixtures/io_map_motion3axis.json` 补 `echo` 条目（%QX1.0/dir=output/coil 8）：L3 断言 `o["echo"]`，但 7 条 entry 里没有 echo——unpack_outputs 按 io_map 输出键返回，必 KeyError。fixture 是 minimal.st 超集（多出的 motion3axis 变量由 shim 自带存储兜底，无冲突）。

(c) `toolchain/shim_gen.py`（核心）：
- **定位符号是 `type*` 指针语义**：生成码 `__INIT_LOCATED(type, location, name, retained)` 展开为 `extern type *location; name.value = location;`（accessor.h 实读确认）——OpenPLC 由 core/glue 提供指针实体，独立 DLL 没有 core。shim 原来发 `extern BOOL __QX0_0;`（值语义）链接必失败。改为 shim 充当 glue：每变量 `static BOOL st_QX0_0; BOOL *__QX0_0 = &st_QX0_0;`；
- `#include "config.h"` 不存在（该版本配置头随 CONFIGURATION 名生成 Cfg.h）——改直接声明原型 `void config_init__(void); void config_run__(unsigned long);`（入口符号恒定，头文件名不定）；
- **输出镜像赋值方向反了**：原 `__QX1_1 = dq[0];`（把输出镜像写进 PLC 变量），plc_read_image 应是 `dq[0] = *__QX1_1;`。输入方向 `*__QX0_0 = di[0];`。

(d) `toolchain/build_dll.py`：
- iec2c 需在 cwd 看到 `lib/ieclib.txt`（OpenPLC 的用法是 webserver 目录放 lib 副本）——build() 在 matiec_root 已知且未显式覆盖参数时自动追加 `-I <matiec_root>/lib`（iec2c -h 确认 -I 即标准库目录）；
- 头文件在 `lib/C/` 不在 `lib/`——gcc -I 同时加 lib 与 lib/C；
- **unity build**：该版本 StdRes.c 直接 `#include "POUS.c"`（由包含方先提供头）——POUS.c 单独编译必重复符号。新增 `drop_textually_included()`：扫描生成 .c 的 `#include "*.c"` 剔除被包含者（通用规则，不硬编码 POUS.c）。

**L2 golden 断言同步**（test_link_a.py）：extern 行 → glue 定义行、赋值方向断言、sizes dq 1→2（echo）、unpack dq bytes([1,0]) + echo False 断言。

**验证**：容器内 `python3 toolchain/tests/test_link_a.py` → **6/6 PASS 无 SKIP**（含 L3：minimal.st→iec2c→Cfg.c/StdRes.c→gcc→plc_logic.so→ctypes 回环：run 注入→echo 直通→prog_id=1 身份→反向写 run→echo 跟随 False）。宿主机 L2 `pytest toolchain/tests/` 6/6（无工具链优雅降级）；主套 pytest 100/100 不受影响。build_result.json：iec2c files=[Cfg.c, StdRes.c]（POUS.c 已正确剔除）。

### 2. 契约③ io_map Schema draft.1 评审（已回看板）

- 结构/plc_var 对齐键/scale INT16 钳位 [-32768,32767]/word 无符号语义 ✅（L3 实测 plc_binding 换算行为符合契约②）；
- **收紧意见**：`modbus.plc_addr` pattern `^%(Q[XW]|IW)\d+...` 允许 %IW，与同 schema 自述"%I 区变量不进 io_map（契约②）"自相矛盾，与 csk 契约包 README"输入 → 传感区块 %IW"口径冲突。契约② §5.1 的传感注入约定 = 外部直写 %QX/%QW、PLC 读回（motion3axis 验收脚本与 csk 自己的 L3 fixture 均此实践）。建议 pattern 收紧为仅 %QX/%QW。README 对应行随 RFC 一并改。

### 3. gc 分支新场景 prog_id 复核

plotter3axis=2、plotter_circle=3，顺延合规 ✅。plotter_circle.xml 头注释写 `prog_id = 2` 与实现 `:= 3` 不符（小瑕疵，已提请 gc 顺手改）。serve.py（lx 文件）被 gc 扩展 PROG_NAMES {1,2,3}——功能无异议，已在看板登记留痕（跨侧改动登记惯例）。

### 4. 提交清单

- toolchain：shim_gen.py / build_dll.py / tests/test_link_a.py / tests/fixtures/{minimal.st, io_map_motion3axis.json}
- docs：协作看板（lx 区块 + 共同议题 + 变更记录）、lx 详细设计（§6.1 recipe + §7 待办更新）、本 devlog
- 合并 origin/master（7d6bc47）

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
