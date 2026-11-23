# PLCopen Motion Control（域条目 MC-xx）

权威 = 《运动控制代码生成方案》§2（v4.0，2026-09-09）+ src/plc/motion3axis.xml
（已验收 47 项在线验收）。skill 内联骨架与本文件的同步由
tests/test_skill_skeleton_sync.py 指纹锁定。

## 分层语义（v4.0）

- **MC-01**（分层）INTERP **不是** MC 块的内部实现，而是**每轴唯一的共享内核**
  （一个轴可能同时收到 Absolute/Relative/Home/Halt 命令，但只能有一条轨迹）；
  MC 块是命令适配器：校验（ErrorID）→ 触发沿锁存动力学到总线 → 置触发线；
  PLC_PRG 把各命令块触发线 OR 后喂 INTERP。方案 §2.0。
- **MC-02**（AXIS_REF 模拟）无 REF_TO，"总线 + OR 触发线"即 PLCopen AXIS_REF
  的手工模拟；每轴一组 MC 实例（接线即绑定）。方案 §2.3 偏差 2。
- **MC-03**（块清单）Power/Reset/Stop/**Halt**/MoveAbsolute/**MoveRelative**/
  MoveJog/Home/**ReadStatus**/**ReadActualPosition** 十块（粗体为 v4.0 新增）；
  生成器**只调不改实现**。方案 §2.3 表。
- **MC-04**（签名映射）`fire`=标准 `Execute`、`pos_target`=`Position`
  （matiec 保留字映射）；命令块全状态组 Done/Busy/Active/CommandAborted/
  Error/ErrorID。方案 §2.3 偏差 1。
- **MC-05**（ErrorID 枚举）0=无 | 1=目标越程 | 2=轴故障 | 3=轴未使能——经
  io_list `{axis}_err_id`（%QW16~18）诊断出口可读。**越程语义 = MC 层拒绝并
  报 ErrorID=1**（不是静默拒绝；INTERP 内仅兜底）。方案 §2.3。
- **MC-06**（中止语义）MC_Halt 受控减速到零速**不失能**，活动命令置
  CommandAborted，重发命令即恢复；INTERP.Aborted 在 MC 块内做**上升沿检测**
  ——上一命令的锁存中止不得误杀新命令（2026-09-09 实测坑）。BufferMode
  首版不做。方案 §2.3 偏差 3/5。
- **MC-07**（动力学）Velocity/Acceleration/Deceleration 为 REAL、仅程序内部
  传递（io_list 仍 {BOOL,INT,WORD} 定点，契约②不变）；MC 块在触发沿把动力学
  锁存到 `{axis}_vel/acc/dec_bus` 总线供 INTERP 消费。方案 §2.3 偏差 4。
- **MC-08**（相对定位）MC_MOVERELATIVE 绝对目标 = **触发时** ActualPosition
  + Distance（先采样后改写目标总线）。方案 §2.3 表。
- **MC-09**（状态聚合）any_moving/fault_any 一律经 MC_READSTATUS 路由
  （Moving/ErrorStop），不自造聚合表达式。方案 §2.3 接线约定。
- **MC-10**（回零偏差）MC_Home 未加 Position 输入，目标仍走场景约定
  （{axis}_sp=0 → cmd_home）。方案 §4.3 已知偏差。
- **MC-11**（连续跟踪扩展）多步工序（序列器）用连续跟踪模式：触发线保持到
  Done、目标变化自动起新轨迹——在 v4.0 INTERP 上加跟踪分支（skill 内联）；
  plotter 绘图序列即此形态（实测 45/45）。方案 §4.1。
- **MC-12**（互锁层级）安全互锁封锁**应用级触发**，不动 MC/内核内部状态
  （如 z_safe 门控 cmd_go）。方案 §4.2。
