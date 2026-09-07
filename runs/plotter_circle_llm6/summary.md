# solve 运行总结

## 迭代历史

- iter 1: 闸门 **consistency** 失败
  - R6: 变量 'cmd_reset' 地址 %QX0.6 与设备模型通道 %QX0.7 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'cmd_draw' 地址 %QX0.7 与设备模型通道 %QX2.0 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'pen_down' 地址 %QX1.4 与设备模型通道 %QX2.1 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'plot_done' 地址 %QX1.5 与设备模型通道 %QX2.2 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'x_fb' 地址 %QW10 与设备模型通道 %QW0 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'y_fb' 地址 %QW11 与设备模型通道 %QW1 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'z_fb' 地址 %QW12 与设备模型通道 %QW2 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'x_sp' 地址 %QW13 与设备模型通道 %QW10 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
- iter 2: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/294605.st:388-38..388-39: error: Invalid parameter 'sw' when invoking FB 'st_x'
./st_files/294605.st:389-38..389-39: error: Invalid parameter 'sw' when invoking FB 'st_y'
./st_files/294605.st:390-38..390-39: error: Invalid parameter 'sw' when invoking FB 'st_z'

Internal compiler error in file function_param_iterator.cc at line 400.
Error generating C files
Compilation finished with errors!
- iter 3: 闸门 **acceptance** 失败
  - [verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=2 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml
- iter 4: 闸门 **acceptance** 失败
  -     [trace 超时] pos=(59,39,9) pen=0 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (59,39,9)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 1）
  -   FAIL 落笔轨迹在圆包围盒（x 7..7, y 7..7，越界 1 次，AC7）
  -   PASS 完成后抬笔
  - [5] 画圆中急停：受控减速≤3s、序列中止、复跑可完成
  -   FAIL 第二次画圆已启动
- iter 5: 闸门 **acceptance** 失败
  -     [trace t=36s] pos=(51,26,9) v=(0,0,0) pen=0 done=0 moving=0
  -     [trace t=38s] pos=(51,26,9) v=(0,0,0) pen=0 done=0 moving=0
  -     [trace 超时] pos=(51,26,9) pen=0 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (51,26,9)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 0）
  -   PASS 落笔轨迹在圆包围盒（x 99..0, y 99..0，越界 0 次，AC7）
  -   PASS 完成后抬笔
- iter 6: 闸门 **acceptance** 失败
  -     [trace t=36s] pos=(51,26,9) v=(0,0,0) pen=0 done=0 moving=0
  -     [trace t=38s] pos=(51,26,9) v=(0,0,0) pen=0 done=0 moving=0
  -     [trace 超时] pos=(51,26,9) pen=0 done=0
  -   FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）
  -   FAIL 终态回圆心 (50,50,10)（实际 (51,26,9)，AC6）
  -   FAIL 落笔期间 XY 联动发生过（采样 0）
  -   PASS 落笔轨迹在圆包围盒（x 99..0, y 99..0，越界 0 次，AC7）
  -   PASS 完成后抬笔

## 结论

best = {"iter": 3, "gate": "acceptance", "errors": ["[verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=2 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml"]}