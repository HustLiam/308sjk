# solve 运行总结

## 迭代历史

- iter 1: 闸门 **acceptance** 失败
  - [verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=2 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml
  - 运行时内部状态时间线（诊断口自动采集）：
  -     [diag t=0.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0
  -     [diag t=1.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0
  -     [diag t=1.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0
  -     [diag t=2.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0
  -     [diag t=2.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0
  -     [diag t=3.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0
- iter 2: 闸门 **acceptance** 失败
  -     [trace t=36s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0
  -     [trace t=38s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0
  -     [trace 超时] pos=(0,0,0) pen=1 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (0,0,0)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 0）
  -   PASS 落笔轨迹在圆包围盒（x 99..0, y 99..0，越界 0 次，AC7）
  -   FAIL 完成后抬笔
- iter 3: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0031）
  -   PASS Y 轴 sw.bit0=1（0031）
  -   PASS Z 轴 sw.bit0=1（0031）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 4: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/213903.st:102: error: invalid variable before ':=' in ST assignment statement.
./st_files/213903.st:102: error: expecting 'THEN' after test expression in 'ELSEIF' statement of ST 'IF' statement.

1 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 5: 闸门 **acceptance** 失败
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   PASS X 轴 sw.bit0=1（0031）
  -   PASS Y 轴 sw.bit0=1（0031）
  -   PASS Z 轴 sw.bit0=1（0031）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
  -   PASS all_oe=TRUE（0.13s）
- iter 6: 闸门 **acceptance** 失败
  -     [trace t=38s] pos=(0,0,6) v=(0,0,0) pen=0 done=0 moving=0
  -     [trace t=40s] pos=(0,0,6) v=(0,0,0) pen=0 done=0 moving=0
  -     [trace 超时] pos=(0,0,6) pen=0 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (0,0,6)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 0）
  -   PASS 落笔轨迹在圆包围盒（x 99..0, y 99..0，越界 0 次，AC7）
  -   PASS 完成后抬笔
- iter 7: 闸门 **acceptance** 失败
  -     [trace t=2s] pos=(100,100,10) v=(40,40,20) pen=0 done=0 moving=1
  -     [trace t=4s] pos=(33,100,10) v=(-40,40,20) pen=0 done=0 moving=1
  -     [trace t=6s] pos=(0,100,10) v=(-40,40,20) pen=0 done=0 moving=1
  -     [trace t=8s] pos=(0,100,10) v=(-40,40,20) pen=0 done=0 moving=1
  -     [trace t=10s] pos=(10,100,0) v=(40,40,-20) pen=1 done=0 moving=1
  -     [trace t=12s] pos=(84,100,0) v=(40,40,-20) pen=1 done=0 moving=1
  -     [trace t=14s] pos=(100,100,0) v=(40,40,-20) pen=1 done=0 moving=1
  -     [trace t=16s] pos=(100,78,0) v=(40,-40,-20) pen=1 done=0 moving=1
- iter 8: 闸门 **acceptance** 失败
  -     [trace t=36s] pos=(0,0,9) v=(0,0,0) pen=0 done=0 moving=0
  -     [trace t=38s] pos=(0,0,9) v=(0,0,0) pen=0 done=0 moving=0
  -     [trace 超时] pos=(0,0,9) pen=0 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (0,0,9)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 0）
  -   PASS 落笔轨迹在圆包围盒（x 99..0, y 99..0，越界 0 次，AC7）
  -   PASS 完成后抬笔
- iter 9: 闸门 **acceptance** 失败
  -     [trace t=36s] pos=(49,24,0) v=(0,0,-20) pen=1 done=0 moving=1
  -     [trace t=38s] pos=(49,24,0) v=(0,0,-20) pen=1 done=0 moving=1
  -     [trace 超时] pos=(49,24,0) pen=1 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (49,24,10)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 0）
  -   PASS 落笔轨迹在圆包围盒（x 99..0, y 99..0，越界 0 次，AC7）
  -   PASS 完成后抬笔
- iter 10: 闸门 **acceptance** 失败
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (100,100,9)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 2）
  -   FAIL 落笔轨迹在圆包围盒（x 2..5, y 2..5，越界 2 次，AC7）
  -   PASS 完成后抬笔
  - [5] 画圆中急停：受控减速≤3s、序列中止、复跑可完成
  -   PASS 第二次画圆已启动
  -   PASS 急停后三轴静止≤3s（0.06s，AC2-4）

## 结论

best = {"iter": 1, "gate": "acceptance", "errors": ["[verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=2 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml", "运行时内部状态时间线（诊断口自动采集）：", "    [diag t=0.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=1.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=1.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=2.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=2.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=3.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=3.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=4.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=4.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=5.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=5.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=6.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=6.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=7.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0"]}