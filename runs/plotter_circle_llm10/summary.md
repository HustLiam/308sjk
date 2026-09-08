# solve 运行总结

## 迭代历史

- iter 1: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/664347.st:164: error: invalid statement in case element of ST 'CASE' statement.
./st_files/664347.st:164: error: invalid variable before ':=' in ST assignment statement.
./st_files/664347.st:164: error: ':' missing after case list in ST 'CASE' statement.
./st_files/664347.st:165: error: invalid variable before ':=' in ST assignment statement.
./st_files/664347.st:166: error: invalid variable before ':=' in ST assignment statement.
./st_files/664347.st:170: error: invalid variable before ':=' in ST assignment statement.
./st_files/664347.st:194: error: invalid variable before ':=' in ST assignment statement.
./st_files/664347.st:196: error: invalid variable before ':=' in ST assignment statement.
./st_files/664347.st:211: error: invalid variable before ':=' in ST assignment statement.
./st_files/664347.st:250: error: invalid variable before ':=' in ST assignment statement.
./st_files/664347.st:252: error: invalid statement list in 'ELSEIF' statement of ST 'IF' statement.
./st_files/664347.st:250: error: invalid variable before ':=' in ST assignment statement.
./st_files/664347.st:420: error: invalid expression after ':=' in ST assignment statement.
./st_files/664347.st:420: error: ';' missing at the end of statement in ST statement.
./st_files/664347.st:420: error: invalid statement in ST statement.
./st_files/664347.st:421: error: invalid expression after ':=' in ST assignment statement.
./st_files/664347.st:421: error: ';' missing at the end of statement in ST statement.
./st_files/664347.st:421: error: invalid statement in ST statement.
./st_files/664347.st:422: error: invalid expression after ':=' in ST assignment statement.
./st_files/664347.st:422: error: ';' missing at the end of statement in ST statement.
./st_files/664347.st:422: error: invalid statement in ST statement.

21 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 2: 闸门 **generate** 失败
  - [xml2st] XML 无法解析: unbound prefix: line 37, column 18
- iter 3: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/567573.st:164: error: invalid statement in case element of ST 'CASE' statement.
./st_files/567573.st:164: error: invalid variable before ':=' in ST assignment statement.
./st_files/567573.st:164: error: ':' missing after case list in ST 'CASE' statement.
./st_files/567573.st:165: error: invalid variable before ':=' in ST assignment statement.
./st_files/567573.st:166: error: invalid variable before ':=' in ST assignment statement.
./st_files/567573.st:170: error: invalid variable before ':=' in ST assignment statement.
./st_files/567573.st:194: error: invalid variable before ':=' in ST assignment statement.
./st_files/567573.st:196: error: invalid variable before ':=' in ST assignment statement.
./st_files/567573.st:211: error: invalid variable before ':=' in ST assignment statement.
./st_files/567573.st:250: error: invalid variable before ':=' in ST assignment statement.
./st_files/567573.st:252: error: invalid statement list in 'ELSEIF' statement of ST 'IF' statement.
./st_files/567573.st:250: error: invalid variable before ':=' in ST assignment statement.
./st_files/567573.st:420: error: invalid expression after ':=' in ST assignment statement.
./st_files/567573.st:420: error: ';' missing at the end of statement in ST statement.
./st_files/567573.st:420: error: invalid statement in ST statement.
./st_files/567573.st:421: error: invalid expression after ':=' in ST assignment statement.
./st_files/567573.st:421: error: ';' missing at the end of statement in ST statement.
./st_files/567573.st:421: error: invalid statement in ST statement.
./st_files/567573.st:422: error: invalid expression after ':=' in ST assignment statement.
./st_files/567573.st:422: error: ';' missing at the end of statement in ST statement.
./st_files/567573.st:422: error: invalid statement in ST statement.

21 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 4: 闸门 **acceptance** 失败
  - [verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=2 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml
  - 运行时内部状态时间线（诊断口自动采集）：
  -     [diag t=0.5s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0
  -     [diag t=1.0s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0
  -     [diag t=1.5s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0
  -     [diag t=2.0s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0
  -     [diag t=2.5s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0
  -     [diag t=3.0s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0
- iter 5: 闸门 **acceptance** 失败
  -     [trace t=36s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0
  -     [trace t=38s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0
  -     [trace 超时] pos=(0,0,0) pen=1 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (0,0,0)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 0）
  -   PASS 落笔轨迹在圆包围盒（x 99..0, y 99..0，越界 0 次，AC7）
  -   FAIL 完成后抬笔
- iter 6: 闸门 **acceptance** 失败
  -     [trace t=36s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0
  -     [trace t=38s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0
  -     [trace 超时] pos=(0,0,0) pen=1 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (0,0,0)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 0）
  -   PASS 落笔轨迹在圆包围盒（x 99..0, y 99..0，越界 0 次，AC7）
  -   FAIL 完成后抬笔
- iter 7: 闸门 **acceptance** 失败
  -     [trace t=36s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0
  -     [trace t=38s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0
  -     [trace 超时] pos=(0,0,0) pen=1 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (0,0,0)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 0）
  -   PASS 落笔轨迹在圆包围盒（x 99..0, y 99..0，越界 0 次，AC7）
  -   FAIL 完成后抬笔
- iter 8: 闸门 **acceptance** 失败
  -   PASS 终态回圆心 (50,50,10)（实际 (51,50,9)，AC6）
  -   PASS 落笔期间 XY 联动发生过（采样 62）
  -   FAIL 落笔轨迹在圆包围盒（x 3..71, y 3..75，越界 3 次，AC7）
  -   PASS 完成后抬笔
  - [5] 画圆中急停：受控减速≤3s、序列中止、复跑可完成
  -   PASS 第二次画圆已启动
  -   FAIL 急停后三轴静止≤3s（3.05s，AC2-4）
  -   PASS plot_done=FALSE（序列中止）

## 结论

best = {"iter": 4, "gate": "acceptance", "errors": ["[verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=2 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml", "运行时内部状态时间线（诊断口自动采集）：", "    [diag t=0.5s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=1.0s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=1.5s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=2.0s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=2.5s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=3.0s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=3.5s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=4.0s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=4.5s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=5.0s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=5.5s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=6.0s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=6.5s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0", "    [diag t=7.0s] pl_step=1 abort_seq=0 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_z_exe=0"]}