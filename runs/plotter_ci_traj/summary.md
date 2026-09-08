# solve 运行总结

## 迭代历史

- iter 1: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0031）
  -   PASS Y 轴 sw.bit0=1（0031）
  -   PASS Z 轴 sw.bit0=1（0031）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 2: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0031）
  -   PASS Y 轴 sw.bit0=1（0031）
  -   PASS Z 轴 sw.bit0=1（0031）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 3: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0031）
  -   PASS Y 轴 sw.bit0=1（0031）
  -   PASS Z 轴 sw.bit0=1（0031）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 4: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0031）
  -   PASS Y 轴 sw.bit0=1（0031）
  -   PASS Z 轴 sw.bit0=1（0031）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 5: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0031）
  -   PASS Y 轴 sw.bit0=1（0031）
  -   PASS Z 轴 sw.bit0=1（0031）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 6: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0031）
  -   PASS Y 轴 sw.bit0=1（0031）
  -   PASS Z 轴 sw.bit0=1（0031）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 7: 闸门 **consistency** 失败
  - R4: 定位变量 'quickstop' 在 XML 中重复声明
  - R4: 定位变量 'cmd_reset' 在 XML 中重复声明
  - R4: 定位变量 'cmd_draw' 在 XML 中重复声明
  - R4: 定位变量 'fault_any' 在 XML 中重复声明
  - R4: 定位变量 'plot_done' 在 XML 中重复声明
  - R4: 定位变量 'x_fb' 在 XML 中重复声明
  - R4: 定位变量 'y_fb' 在 XML 中重复声明
  - R4: 定位变量 'z_fb' 在 XML 中重复声明
- iter 8: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0031）
  -   PASS Y 轴 sw.bit0=1（0031）
  -   PASS Z 轴 sw.bit0=1（0031）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能

## 结论

best = {"iter": 1, "gate": "acceptance", "errors": ["[verify] 程序身份确认: plotter_circle (prog_id=3)", "[1] 上电（run=0）：三轴就绪；初始笔位 z=0", "  PASS X 轴 sw.bit0=1（0031）", "  PASS Y 轴 sw.bit0=1（0031）", "  PASS Z 轴 sw.bit0=1（0031）", "  PASS all_oe=FALSE", "  PASS pen_down=TRUE（初始触纸）", "[2] run=1 → 三轴使能", "  PASS all_oe=TRUE（0.20s）", "[3] 落笔态 cmd_go(60,40,0)：X/Y 请求被笔互锁拒绝", "  PASS X/Y 未运动（互锁拒绝，AC5）", "[4] cmd_draw：抬笔→(50,25)→落笔画整圆→回圆心(50,50,10)", "    [trace t=2s] pos=(43,24,9) v=(16,0,0) pen=0 done=0 moving=1", "    [trace t=4s] pos=(56,27,1) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=6s] pos=(74,45,1) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=8s] pos=(63,67,1) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=10s] pos=(42,73,1) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=12s] pos=(29,49,1) v=(50,-50,0) pen=1 done=0 moving=1", "    [trace t=14s] pos=(37,32,1) v=(0,0,0) pen=1 done=0 moving=0", "    [trace t=16s] pos=(51,25,7) v=(0,0,0) pen=0 done=0 moving=0", "  PASS 画圆序列完成 plot_done=TRUE（17.3s ≤ 30s，AC1）", "  PASS 终态回圆心 (50,50,10)（实际 (51,51,9)，AC6）", "  PASS 落笔期间 XY 联动发生过（采样 78）", "  PASS 落笔轨迹在圆包围盒（x 26..74, y 24..75，越界 0 次，AC7）", "  PASS 完成后抬笔", "[5] 画圆中急停：受控减速≤3s、序列中止、复跑可完成", "  FAIL 第二次画圆已启动", "  PASS 急停后三轴静止≤3s（0.00s，AC2-4）", "  FAIL plot_done=FALSE（序列中止）", "  PASS 释放后重新使能", "  PASS 重新 cmd_draw 后完成（0.0s）", "（验收暂停：段 [5] 存在失败，后续段未执行；已通过段：[1] [2] [3] [4]——先修复本段，通过后重跑继续）", "运行时内部状态时间线（诊断口自动采集）：", "    [diag t=0.5s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=1.0s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=1.5s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=2.0s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=2.5s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=3.0s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=3.5s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=4.0s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=4.5s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=5.0s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=5.5s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=6.0s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=6.5s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1", "    [diag t=7.0s] pl_step=1 draw_edge=0 go_edge=0 qs_latch=0 go_x_exe=1 go_y_exe=1"]}