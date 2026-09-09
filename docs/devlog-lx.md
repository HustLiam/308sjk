# lx 开发日志（仅个人分支，合入 master 前移除）

> 只记代码技术说明：改了什么 / 为什么 / 怎么验证 / 踩坑解法。进度协调看板，不写这里。

## 2026-09-09 会话 2：motion3axis v4.0（PLCopen MC 直接对齐，负责人指令）

### 1. 改了什么

- **MC API 层对齐 PLCopen MC Part 1 单轴子集**（src/plc/motion3axis.xml，743→1315 行）：
  - 现有 6 块补齐标准签名：MC_Power（+EnablePositive/Negative 门控、Busy/Error）、MC_Reset/MC_Stop/MC_HOME（+Busy/Error）、MC_MoveAbsolute（+Velocity/Acceleration/Deceleration REAL 输入 + Done/Busy/Active/CommandAborted/Error/ErrorID 全状态组）、MC_MoveJog（+Velocity 步进量换算 + cur_pos 起跳锚定）；
  - 新增 4 块：MC_HALT（Stopping/Done/Busy/Error，受控减速不失能）、MC_MOVERELATIVE（触发沿绝对目标=ActualPosition+Distance）、MC_READSTATUS（Valid/Moving/StandStill/Disabled/ErrorStop，从 sw 映射）、MC_READACTUALPOSITION；
  - INTERP：VMAX/ACCEL 定值参数 → vel_req/acc_req/dec_req 命令输入（MC 块触发沿锁存到 {axis}_vel/acc/dec_bus 总线）；新增 Aborted 输出（hold 减速到零速置位）；
  - PLC_PRG：目标总线 {axis}_tgt（默认 {axis}_sp，rel 触发改写）、状态聚合改经 rds_*（any_moving/fault_any）、ErrorID 诊断出口 {axis}_err_id；
  - io_list 24→32：cmd_halt %QX2.0、cmd_rel %QX2.1、rel_{x,y,z}_d %QW3-5、{x,y,z}_err_id %QW16-18。

### 2. 踩坑（两条新的，已入生成方案 §3.3）

- **标识符大小写不敏感**：MC_HALT 输入 `busy` 与输出 `Busy` 同 POU 冲突（matiec 报 invalid variable(s) declaration，行号指到声明处但不说明原因）——改 `ax_busy`；
- **锁存中止误杀新命令**：第一版中止传播 `IF interp_exe AND abort_bus` 电平检测——Halt 后 INTERP.Aborted 锁存 TRUE，后续 go_x 触发当拍即被清（X 轴从此拒动，在线验收 [9][10] 抓到）。修：abort_bus 上升沿检测（edge_ab），与 PLCopen"中止是事件"语义一致；
- **偶发判据三处**（连续多轮实测暴露）：① jog 起跳不锚定实际位置（jog_pos 是 FB 残留状态，裸复跑时设定点跳变）→ MC_MOVEJOG 触发沿 jog_pos := cur_pos；② 验收 [4] "仅 Z 运动"判 X/Y 速度严格为零，但到位容差内 X/Y 有 1~2 单位伺服修正 → 改按位移（≤3）判；③ [6] jog 进给判定依赖松开后位置（受设定点回跳影响）→ 改按住期间采样判进给。修后连续 6 轮 47/47 全绿。

### 3. 怎么验证的

- xml2st --check PASS → run_deploy matiec 真编译 PASS → 在线验收 35→47 项（新增 [7] 越程 ErrorID=1 拒绝 / [8] Halt 受控暂停+重发恢复 / [9] 相对定位与越程拒绝），**连续 6 轮全绿**（幂等性含裸复跑）；
- spec io_list 32 项（旧 24 序不变+新 8 追加，保住 R5 索引锚定）；AML 示例补 8 点位（device 文本与 spec 逐字一致，预填契约测试保持）；
- gc 侧测试锚定同步（**已登记看板请 gc 复核**）：test_aml_parser 三处 24→32 + x_fb 按名定位、test_consistency_check 一处 24→32；pytest 100/100。

### 4. 已知偏差（生成方案 §2.3 已文档化）

fire=Execute（matiec 保留字）；无 AXIS_REF（每轴实例接线即绑定，真机移植时评估）；BufferMode 首版不做；MC_Home 目标仍走"场景约定 sp=0"（未加 Position 输入）。

---

## 2026-09-09 会话（全部已合入 master，最新 035c127）

> 技术详情见 git 历史（e85680b / 491a7dc / a2d6826 / f7e59c7）。摘要：链路 A L3 真编译回环打通（Docker 工具链 + csk 侧 toolchain 四处版本适配修正，6/6 无 SKIP）；契约③ draft.1 评审已回（%IW pattern 收紧意见）；gc 分支新场景 prog_id 编号复核合规；《项目术语通俗手册》《进度周报模板》（精简为五固定大标题空白骨架）新增（负责人要求）。两次 master 合入走查均无事故（快进/干净合并、无 devlog 残留、pytest 100/100 + toolchain L2 6/6 复验绿）。
