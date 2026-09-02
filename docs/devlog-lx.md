# lx 本地开发日志

> 仅存在于 `lx` 个人分支，只做代码上的技术说明；**合并 master 前移除**（分支历史保留可回溯）。进度协调类内容写 `docs/协作看板.md`，不写这里。
> 历史回溯：`git show <commit>:docs/devlog-lx.md`。

## 2026-09-02（第七轮：主方案 v1.4 删减）

- §4/§5/§6/§7 内容删除仅留标题（难点表/实施计划/指标/风险表整体移除，git 可回溯）；§1–§3 与 §8 治理不动。纯文档删减，74/74 复核绿，第六次许可制合并纯快进。
- 未处理项（按指令范围未动，看板已登记待负责人示下）：gc 文档 3 处交叉引用现在指向空章节——§5 一致性归属引用"总体方案 §4 难点"、gc §7 标题"对齐总体方案 §5"、gc §8 标题"总体方案 §6 摘录"。

### 待续（下一轮候选，不变）

- 一键回归脚本 run_regression.py；
- serve.py GET /status。

## 2026-09-02（第八轮：CiA 402 + PLCopen MC 运动栈）

### 排障知识（matiec/OpenPLC 新踩坑，全部进入 changelog v2.1）

1. **FB 内禁用 VAR CONSTANT 块**（"unexpected located variable(s) declaration in function block"）——初值常量降级为普通 VAR + initialValue；
2. **INT 不能 AND/OR**（"Data type mismatch"）——控制字/状态字一律 WORD；但 WORD 与 0 的比较合法（`<> 0` 可用，`<> 16#0000` 亦可）；
3. **DT 与 STEP 是保留字**（DATE_AND_TIME / SFC STEP）——分别改名 SCAN_T / pstep；
4. **FB 输出不可作 VAR_IN_OUT 实参**（"Assignment to FB output variable is not allowed"）——MC 块与驱动之间经扫描头采样到定位字（`x_sw := ax_x.sw`）再绑定；
5. **BOOL 赋 INT 要显式转换**（BOOL_TO_INT）。

### CiA 402 驱动模型设计决策

- **状态机**：CASE on INT state（1-7 = SOD/RTSO/SO/OE/QSA/FRA/FA），控制字低 4 位命令译码；QSA 内 v=0 后可重入 0x0F/0x06/0x00；FRA 减速完自动转 FA。
- **梯形规划**：加速段 v→±VMAX、减速段 stop_d = v²/(2A) 触发、端点 snap（dist≤POSWIN 且 v=0 时 demand := tgt 消除离散化残差）。
- **伺服环**：v_out = v + KP·(demand − pos_fb)，仅 OE/QSA/FRA 输出（后者走减速曲线）；**静止死区**（v=0 且 |err|≤1.5 → v_out=0）消除编码器量化抖动——没有它 fb 在 ±1 量化带内振荡产生 ±25 速度指令毛刺。
- **快停减速与轨迹减速分离**（QS_DECEL > ACCEL），demand 在 OE/QSA/FRA 统一推进——否则快停路径 demand 冻结、误差累积导致电机反转。
- **设定点锁存**：电平触发（OE 且 bit4=1 时，目标有变化或未被确认即锁存）——放弃边沿方案（new_prev 在多写者竞争下脆弱）；ack 生命周期 = 主站持有 bit4（H2：主站清 bit4 后 ack 才降），到达信号独立走 bit10。

### MC API 层设计决策

- **MC_Power**：自动上电序列 0x06→0x07→0x0F（pstep 步进机），丢使能（非故障）自动重启序列；Enable=FALSE 持续发 shutdown 0x06（轴停 RTSO——不是 SOD，这是 CiA 402 语义：shutdown ≠ disable voltage）。
- **MC_MoveAbsolute**：边沿扫描**立即落目标并置 bit4**（不等 Busy 门）；等 bit12 后清 bit4（握手完成）；到达（bit10）后清 mov_busy → Done。新指令抢占时清旧 bit4；中止（非使能/故障）时也清——不留悬空握手位。
- **MC_MoveJog**：追赶目标法（target := fb ± 6），**所有权判定**（挂起目标 = 上次追赶值才认领收尾）——不加这个会误杀其他块刚写的新指令（实测踩坑）。
- **MC_Stop**：快停命令 0x02 覆盖低 4 位；**调用顺序即优先级**——MC_Power 先调、MC_Stop 后调（最后落笔者胜）。
- **MC_Reset**：bit7 置位至 bit3 清零，然后主动降 bit7。

### 主站时序（脚本侧，教训记入 spec C3）

目标寄存器必须**先于**指令上升沿建立（≥1 扫描 20ms）——`pulse(CMD_GO)` 只保持 0.15s（约 7 个扫描），若在脉冲**后**写 sp 寄存器，Execute 沿锁存的是旧值，ack 永不置位（[5] 项三轴全挂的根因）。修复：`wreg(sp) → sleep(0.15) → pulse(cmd)`。

### 验收覆盖（36/36）

8 组：上电 RTSO / 使能序列 / 并发定位 / 仅 Z 运动 / 快停 QSA+重使能+重发 / 点动 / 故障注入+复位 / 回零+失能。全程不变量：失能态（bit2=0,bit5=1）电机指令必须为零 + 速度限幅 ±120。

### gc 侧适配（动了 gc 的文件，看板提请评审）

spec 24 变量（io_list 加 WORD 型 sw、带符号 INT 速度指令、应用级 BOOL 指令）；test_consistency_check 变异目标换新（x_fb→x_enc、%QX0.4→%QX0.0、drop=14 x_sw）；test_orchestrator mismatch 种子（move_done→done_flag）；test_patternlib ST 锚点（prog_id）；test_xml2st 断言（FUNCTION_BLOCK AXIS402 / MC_POWER / CASE state OF）。74/74 绿。
