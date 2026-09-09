# solve 运行总结

## 迭代历史

- iter 1: 闸门 **generate** 失败
  - [xml2st] XML 无法解析: unbound prefix: line 34, column 18
- iter 2: 闸门 **generate** 失败
  - [xml2st] XML 无法解析: mismatched tag: line 209, column 100
- iter 3: 闸门 **consistency** 失败
  - R4: 定位变量 'quickstop' 在 XML 中重复声明
  - R4: 地址 %QX0.5 被 'quickstop' 与 'quickstop' 重复占用
- iter 4: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/129695.st:26-9..26-27: error: Data type mismatch for '>' expression.
1 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 5: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0401）
  -   PASS Y 轴 sw.bit0=1（0401）
  -   PASS Z 轴 sw.bit0=1（0401）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 6: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0401）
  -   PASS Y 轴 sw.bit0=1（0401）
  -   PASS Z 轴 sw.bit0=1（0401）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 7: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0401）
  -   PASS Y 轴 sw.bit0=1（0401）
  -   PASS Z 轴 sw.bit0=1（0401）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 8: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0401）
  -   PASS Y 轴 sw.bit0=1（0401）
  -   PASS Z 轴 sw.bit0=1（0401）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 9: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0401）
  -   PASS Y 轴 sw.bit0=1（0401）
  -   PASS Z 轴 sw.bit0=1（0401）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 10: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/203504.st:26-9..26-27: error: Data type mismatch for '>' expression.
1 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!

## 结论

best = {"iter": 5, "gate": "acceptance", "errors": ["[verify] 程序身份确认: plotter_circle (prog_id=3)", "[1] 上电（run=0）：三轴就绪；初始笔位 z=0", "  PASS X 轴 sw.bit0=1（0401）", "  PASS Y 轴 sw.bit0=1（0401）", "  PASS Z 轴 sw.bit0=1（0401）", "  PASS all_oe=FALSE", "  PASS pen_down=TRUE（初始触纸）", "[2] run=1 → 三轴使能", "  PASS all_oe=TRUE（0.13s）", "[3] 落笔态 cmd_go(60,40,0)：X/Y 请求被笔互锁拒绝", "  PASS X/Y 未运动（互锁拒绝，AC5）", "[4] cmd_draw：抬笔→(50,25)→落笔画整圆→回圆心(50,50,10)", "    [trace t=2s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=4s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=6s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=8s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=10s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=12s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=14s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=16s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=18s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=20s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=22s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=24s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=26s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=28s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=30s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=32s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=34s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=36s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=38s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0", "    [trace 超时] pos=(0,0,0) pen=1 done=0", "  FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）", "  FAIL 终态回圆心 (50,50,10)（实际 (0,0,0)，AC6）", "  FAIL 落笔期间 XY 联动发生过（采样 0）", "  PASS 落笔轨迹在圆包围盒（x 99..0, y 99..0，越界 0 次，AC7）", "  FAIL 完成后抬笔", "（验收暂停：段 [4] 存在失败，后续段未执行；已通过段：[1] [2] [3]——先修复本段，通过后重跑继续）", "运行时内部状态时间线（诊断口自动采集）：", "    [diag t=0.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=1.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=1.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=2.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=2.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=3.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=3.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=4.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=4.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=5.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=5.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=6.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=6.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0", "    [diag t=7.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 edge_draw=0 edge_go=0"]}