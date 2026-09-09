# solve 运行总结

## 迭代历史

- iter 1: 闸门 **generate** 失败
  - [consistency] R2: io_list 变量 'run' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'cmd_home' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'cmd_go' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'jog_fwd' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'jog_rev' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'quickstop' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'cmd_reset' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'cmd_draw' 在 XML 定位变量表中不存在（未生成或改名）
- iter 2: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/453652.st:32-9..32-27: error: Data type mismatch for '>' expression.
1 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 3: 闸门 **acceptance** 失败
  - [verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=0 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml
  - 运行时内部状态时间线（诊断口自动采集）：
  -     [diag t=0.5s] pstep_ax=4 pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0
  -     [diag t=1.0s] pstep_ax=4 pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0
  -     [diag t=1.5s] pstep_ax=4 pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0
  -     [diag t=2.0s] pstep_ax=4 pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0
  -     [diag t=2.5s] pstep_ax=4 pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0
  -     [diag t=3.0s] pstep_ax=4 pl_step=4 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0
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
- iter 7: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/766796.st:26-9..26-27: error: Data type mismatch for '>' expression.
1 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 8: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/522670.st:34: error: invalid variable before ':=' in ST assignment statement.

1 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 9: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0031）
  -   PASS Y 轴 sw.bit0=1（0031）
  -   PASS Z 轴 sw.bit0=1（0031）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 10: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0031）
  -   PASS Y 轴 sw.bit0=1（0031）
  -   PASS Z 轴 sw.bit0=1（0031）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能

## 结论

best = {"iter": 3, "gate": "acceptance", "errors": ["[verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=0 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml", "运行时内部状态时间线（诊断口自动采集）：", "    [diag t=0.5s] pstep_ax=4 pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=1.0s] pstep_ax=4 pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=1.5s] pstep_ax=4 pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=2.0s] pstep_ax=4 pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=2.5s] pstep_ax=4 pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=3.0s] pstep_ax=4 pl_step=4 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=3.5s] pstep_ax=4 pl_step=5 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=4.0s] pstep_ax=4 pl_step=6 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=4.5s] pstep_ax=4 pl_step=7 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=5.0s] pstep_ax=4 pl_step=8 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=5.5s] pstep_ax=4 pl_step=9 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=6.0s] pstep_ax=4 pl_step=9 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=6.5s] pstep_ax=4 pl_step=10 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0", "    [diag t=7.0s] pstep_ax=4 pl_step=11 go_x_exe=1 go_y_exe=1 go_z_exe=1 jog_exe=0"]}