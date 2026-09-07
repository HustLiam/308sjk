# solve 运行总结

## 迭代历史

- iter 1: 闸门 **consistency** 失败
  - R6: 变量 'cmd_reset' 地址 %QX0.6 与设备模型通道 %QX0.7 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'cmd_draw' 地址 %QX0.7 与设备模型通道 %QX2.0 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'pen_down' 地址 %QX1.4 与设备模型通道 %QX2.1 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'plot_done' 地址 %QX1.5 与设备模型通道 %QX2.2 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'x_fb' 地址 %QW1 与设备模型通道 %QW0 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'y_fb' 地址 %QW2 与设备模型通道 %QW1 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'z_fb' 地址 %QW3 与设备模型通道 %QW2 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'x_sp' 地址 %QW4 与设备模型通道 %QW10 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
- iter 2: 闸门 **generate** 失败
  - [xml2st] XML 无法解析: mismatched tag: line 21, column 97
- iter 3: 闸门 **consistency** 失败
  - R6: 变量 'cmd_reset' 地址 %QX0.6 与设备模型通道 %QX0.7 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'cmd_draw' 地址 %QX0.7 与设备模型通道 %QX2.0 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'pen_down' 地址 %QX1.4 与设备模型通道 %QX2.1 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'plot_done' 地址 %QX1.5 与设备模型通道 %QX2.2 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'x_sp' 地址 %QW3 与设备模型通道 %QW10 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'y_sp' 地址 %QW4 与设备模型通道 %QW11 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'z_sp' 地址 %QW5 与设备模型通道 %QW12 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
  - R6: 变量 'x_v' 地址 %QW9 与设备模型通道 %QW13 不一致（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）
- iter 4: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/374162.st:327: error: ')' missing at the end of expression in ST expression.
./st_files/374162.st:327: error: ';' missing at the end of statement in ST statement.
./st_files/374162.st:327: error: invalid statement in ST statement.

3 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 5: 闸门 **acceptance** 失败
  - [verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=2 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml
- iter 6: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   FAIL X 轴 sw.bit0=1（0040）
  -   FAIL Y 轴 sw.bit0=1（0040）
  -   FAIL Z 轴 sw.bit0=1（0040）
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能

## 结论

best = {"iter": 5, "gate": "acceptance", "errors": ["[verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=2 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml"]}