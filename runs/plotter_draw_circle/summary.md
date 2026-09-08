# solve 运行总结

## 迭代历史

- iter 1: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/508030.st:258: error: ';' missing at end of variable(s) declaration.
./st_files/508030.st:258: error: invalid variable(s) declaration.

2 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 2: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/761599.st:254: error: ';' missing at end of variable(s) declaration.
./st_files/761599.st:254: error: invalid variable(s) declaration.

2 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 3: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/128633.st:388-38..388-39: error: Invalid parameter 'sw' when invoking FB 'st_x'
./st_files/128633.st:389-38..389-39: error: Invalid parameter 'sw' when invoking FB 'st_y'
./st_files/128633.st:390-38..390-39: error: Invalid parameter 'sw' when invoking FB 'st_z'
./st_files/128633.st:451-90..451-99: error: Invalid assignment syntax ':=' used for parameter 'interp_exe', when invoking FB 'go_x'
./st_files/128633.st:452-90..452-99: error: Invalid assignment syntax ':=' used for parameter 'interp_exe', when invoking FB 'go_y'
./st_files/128633.st:453-79..453-88: error: Invalid assignment syntax ':=' used for parameter 'interp_exe', when invoking FB 'go_z'
./st_files/128633.st:454-63..454-70: error: Invalid assignment syntax ':=' used for parameter 'home_exe', when invoking FB 'hm_x'
./st_files/128633.st:455-63..455-70: error: Invalid assignment syntax ':=' used for parameter 'home_exe', when invoking FB 'hm_y'
./st_files/128633.st:456-63..456-70: error: Invalid assignment syntax ':=' used for parameter 'home_exe', when invoking FB 'hm_z'

Internal compiler error in file function_param_iterator.cc at line 400.
Error generating C files
Compilation finished with errors!
- iter 4: 闸门 **acceptance** 失败
  -   PASS Z 抬笔到位（实际 10）
  -   PASS pen_down=FALSE（已抬笔）
  -   FAIL X/Y 仍为 0
  - [5] cmd_go P(60,40,10)：抬笔态三轴并发定位
  -   FAIL 到位 move_done=TRUE
  -   PASS 运动期间 any_moving 曾置位
  -   PASS X 定位 |60-59|<=容差（实际 59）
  -   FAIL Y 定位 |40-100|<=容差（实际 100）
- iter 5: 闸门 **acceptance** 失败
  -   PASS Z 抬笔到位（实际 10）
  -   PASS pen_down=FALSE（已抬笔）
  -   FAIL X/Y 仍为 0
  - [5] cmd_go P(60,40,10)：抬笔态三轴并发定位
  -   FAIL 到位 move_done=TRUE
  -   PASS 运动期间 any_moving 曾置位
  -   PASS X 定位 |60-58|<=容差（实际 58）
  -   FAIL Y 定位 |40-100|<=容差（实际 100）
- iter 6: 闸门 **generate** 失败
  - [xml2st] XML 无法解析: mismatched tag: line 36, column 10

## 结论

best = {"iter": 4, "gate": "acceptance", "errors": ["  PASS Z 抬笔到位（实际 10）", "  PASS pen_down=FALSE（已抬笔）", "  FAIL X/Y 仍为 0", "[5] cmd_go P(60,40,10)：抬笔态三轴并发定位", "  FAIL 到位 move_done=TRUE", "  PASS 运动期间 any_moving 曾置位", "  PASS X 定位 |60-59|<=容差（实际 59）", "  FAIL Y 定位 |40-100|<=容差（实际 100）", "  FAIL Z 定位 |10-0|<=容差（实际 0）", "  PASS 静止后 X 状态字 bit10 target-reached", "[6] x_sp=150 手动定位：越程目标被插补引擎安全拒绝", "  PASS X 轴未运动（越程拒绝，末段速度 0）", "[7] cmd_draw（抬笔态启动，C6 前置）：定位(20,20)→落笔画 20..80 正方形→抬笔→回中心(50,50)", "  FAIL 绘图序列完成 plot_done=TRUE（实际 40.3s ≤ 30s，AC3）", "  PASS 序列启动前置=抬笔（C6）", "  FAIL 终态 X=50（实际 71）", "  FAIL 终态 Y=50（实际 7）", "  PASS 终态 Z=10 抬笔（实际 8）", "  PASS 落笔期间 XY 联动发生过（实际采样 333）", "  FAIL 落笔期间 XY 始终在绘图区 [15,85]（越区 329 次）", "  PASS 完成后 pen_down=FALSE", "[8] 绘图中急停：受控减速≤3s（AC4）、plot_done 熄灭（AC5）、复跑可完成", "  PASS 第二次绘图已启动（any_moving）", "  PASS 急停后受控减速至停（≤3s，实际 0.07s）", "  PASS plot_done=FALSE（序列被中止）", "  PASS 无故障", "  PASS 释放后重新使能", "  FAIL 原地抬笔（pen_down=FALSE）", "  FAIL 恢复：回参考点 (0,0,10)", "  FAIL 重新 cmd_draw 后完成", "  FAIL 终态回中心 (50,50,10)（实际 (51,75,10)）", "[9] cmd_home：X/Y 回零，Z 回抬笔安全位 10", "  FAIL 三轴回参考点（X=0 Y=0 Z=10）", "  PASS pen_down=FALSE（参考点=抬笔）", "[10] run=0 失能：速度归零", "  FAIL 失能 all_oe=FALSE", "  FAIL 失能后三轴速度为零", "  PASS 全程不变量无违例（失能态零速 / 速度限幅）", "", "场景验收: 存在失败 ❌", "运行时内部状态时间线（诊断口自动采集）：", "    [diag t=0.5s] pl_step=1 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=1.0s] pl_step=2 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=1.5s] pl_step=2 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=2.0s] pl_step=2 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=2.5s] pl_step=2 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=3.0s] pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=3.5s] pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=4.0s] pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=4.5s] pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=5.0s] pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=5.5s] pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=6.0s] pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=6.5s] pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0", "    [diag t=7.0s] pl_step=3 go_x_exe=1 go_y_exe=1 go_z_exe=1 hm_x_exe=0 hm_y_exe=0"]}