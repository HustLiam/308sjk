# solve 运行总结

## 迭代历史

- iter 1: 闸门 **generate** 失败
  - [xml2st] XML 无法解析: mismatched tag: line 354, column 94
- iter 2: 闸门 **generate** 失败
  - [consistency] R2: io_list 变量 'run' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'cmd_home' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'cmd_go' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'jog_fwd' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'jog_rev' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'quickstop' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'cmd_reset' 在 XML 定位变量表中不存在（未生成或改名）
  - [consistency] R2: io_list 变量 'cmd_draw' 在 XML 定位变量表中不存在（未生成或改名）
- iter 3: 闸门 **acceptance** 失败
  - [verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=2 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml
  - 运行时内部状态时间线（诊断口自动采集）：
  -     [diag t=0.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0
  -     [diag t=1.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0
  -     [diag t=1.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0
  -     [diag t=2.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0
  -     [diag t=2.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0
  -     [diag t=3.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0
- iter 4: 闸门 **acceptance** 失败
  -     [trace t=38s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0
  -     [trace 超时] pos=(0,0,0) pen=1 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (0,0,0)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 0）
  -   PASS 落笔轨迹在圆包围盒（x 99..0, y 99..0，越界 0 次，AC7）
  -   FAIL 完成后抬笔
  - [5] 画圆中急停：受控减速≤3s、序列中止、复跑可完成
- iter 5: 闸门 **acceptance** 失败
  -     [trace t=36s] pos=(49,25,10) v=(0,0,20) pen=0 done=0 moving=1
  -     [trace t=38s] pos=(49,25,10) v=(0,0,20) pen=0 done=0 moving=1
  -     [trace 超时] pos=(49,25,0) pen=1 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (49,25,0)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 0）
  -   PASS 落笔轨迹在圆包围盒（x 99..0, y 99..0，越界 0 次，AC7）
  -   FAIL 完成后抬笔
- iter 6: 闸门 **acceptance** 失败
  -     [trace t=36s] pos=(50,24,10) v=(0,0,20) pen=0 done=0 moving=1
  -     [trace t=38s] pos=(50,24,10) v=(0,0,20) pen=0 done=0 moving=1
  -     [trace 超时] pos=(40,72,0) pen=1 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (76,50,0)，AC6）
  -   PASS 落笔期间 XY 联动发生过（采样 51）
  -   PASS 落笔轨迹在圆包围盒（x 24..77, y 24..75，越界 0 次，AC7）
  -   FAIL 完成后抬笔
- iter 7: 闸门 **acceptance** 失败
  -     [trace t=2s] pos=(76,51,0) v=(0,0,-20) pen=1 done=0 moving=1
  -     [trace t=4s] pos=(80,55,10) v=(58,58,20) pen=0 done=0 moving=1
  -     [trace t=6s] pos=(100,100,10) v=(120,120,20) pen=0 done=0 moving=1
  -     [trace t=8s] pos=(100,100,10) v=(120,120,20) pen=0 done=0 moving=1
  -     [trace t=10s] pos=(100,100,10) v=(120,120,20) pen=0 done=0 moving=1
  -     [trace t=12s] pos=(100,100,10) v=(120,120,20) pen=0 done=0 moving=1
  -     [trace t=14s] pos=(100,100,10) v=(120,120,20) pen=0 done=0 moving=1
  -     [trace t=16s] pos=(100,100,0) v=(120,120,-20) pen=1 done=0 moving=1
- iter 8: 闸门 **acceptance** 失败
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (76,51,0)，AC6）
  -   PASS 落笔期间 XY 联动发生过（采样 60）
  -   FAIL 落笔轨迹在圆包围盒（x 7..79, y 7..74，越界 2 次，AC7）
  -   FAIL 完成后抬笔
  - [5] 画圆中急停：受控减速≤3s、序列中止、复跑可完成
  -   FAIL 第二次画圆已启动
  -   PASS 急停后三轴静止≤3s（0.00s，AC2-4）

## 结论

best = {"iter": 3, "gate": "acceptance", "errors": ["[verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=2 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml", "运行时内部状态时间线（诊断口自动采集）：", "    [diag t=0.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=1.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=1.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=2.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=2.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=3.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=3.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=4.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=4.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=5.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=5.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=6.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=6.5s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0", "    [diag t=7.0s] pl_step=0 go_x_exe=0 go_y_exe=0 go_z_exe=0 hm_x_exe=0 hm_y_exe=0"]}