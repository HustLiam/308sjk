# IEC 61131-3 ST 子集与边界行为（域条目 ST-xx）

权威分层：语言/类型/保留字以 lx 契约②（lx 文档 §3 + xml2st 拒绝清单）为准，
本文件只登记**生成器必须知道的语义与 OpenPLC/matiec 边界行为**；每条附可运行出处。

## 语言子集

- **ST-01**（语法）语句以分号结尾；`END_CASE` 后必须带分号——漏分号是 matiec 单错高频
  （坑库 P09 实证：修前仅此一错）。出处：plotter3axis 在线验收 v1.0。
- **ST-02**（类型）对外定位变量封闭集 {BOOL→%QX, INT→%QW}；WORD 仅驱动器状态/控制字内部
  使用（R3 位宽兼容集 INT/UINT/WORD）。契约② v1.1；R1/R3 闸门强制。
- **ST-03**（类型）REAL 初值必须带小数点（`40.0` 非 `40`）；INT 不能 AND/OR 位运算，
  位运算用 WORD；BOOL→INT 需 BOOL_TO_INT() 显式转换。出处：生成方案 §3.3。
- **ST-04**（保留字）`Execute`/`Target`/`Hold`/`DT`/`STEP` 是保留字——用
  `fire`/`pos_target`/`hold_req`/`SCAN_T`/`pstep` 替代。出处：生成方案 §3.3。
- **ST-05**（保留字）标识符**大小写不敏感**：`busy` 与 `Busy` 同名冲突（2026-09-09 实测）。
  同一 POU 内输入输出避免仅大小写差异，必要时 `ax_busy` 后缀。出处：生成方案 §3.3。
- **ST-06**（结构）FB 内禁 CONSTANT 块——参数用带初值的普通 VAR。出处：生成方案 §3.3。

## 编译器/优化器边界行为（生成侧硬知识）

- **ST-07**（优化器）PLC_PRG 内**单扫描选通/边沿记忆赋值会被优化器静默吞掉**
  （症状：序列器空转、FB 状态不翻转）——多段轨迹必须用连续跟踪模式；
  边沿检测集中在 FB 内部且**不同 FB 的边沿变量必须不同名**
  （`edge_ir/edge_ma/edge_ab/edge_hl/...`）。出处：坑库 P10；生成方案 v2 实测。
- **ST-08**（优化器）多个同模式 FB 实例会被优化器合并、体内赋值被吞——稳定解法：
  共用一个参数化 FB 或内联到 PROGRAM（plotter 的 INTERP 参数化共用即此解）。
  出处：lx 避坑（changelog 场景 v3.0 行）。
- **ST-09**（优化器，P23）**CASE 的 ELSE 复位兜底 + 三轴同款 FB 多实例 = 周期性重握手
  振荡**：`ELSE state := 1` / `ELSE sw := 0` 在 pwr/dv 三实例并行下被周期触发
  （三轴 sw 同步 0031↔0033↔0437 循环、all_oe 闪烁），cmd_home 时刻 all_oe=FALSE
  拒动或使能门坍缩设定点。封闭值域+显式初值时**不写 ELSE**；确需防记错用空兜底
  （保持现状态）。三组对照补丁实验实证（2026-09-09，plotter_cell 战役）。
- **ST-10**（初值）被 CASE 索引的状态变量声明必须带 `:= 初值`（402 从 1=SOD 起步，
  0 不是合法状态）——无初值上电落入未定义态、驱动永不使能。出处：坑库 P18；
  R7 RFC 提案（lx 评审中）。注意与 ST-09 的边界：**初值必需；ELSE 兜底受限**。

## 语义约定

- **ST-11**（状态机）CiA402 状态字位义：bit0=ready-to-switch-on、bit1=switched-on、
  bit2=operation-enabled、bit3=fault、bit4=voltage-enabled、bit5=quickstop-active、
  bit10=target-reached；控制字握手 06→07→0F（SOD→SO→OE）。出处：DRIVE402 骨架
  （生成方案 §2；motion3axis.xml）。
- **ST-12**（赋值语义）定位变量（AT %Q）由本程序写的即输出、由主站写的即输入，
  统一 %Q 区、%I 禁用、%QD 禁用——方向由 io_list/dir 声明表达。契约② v1.1。
