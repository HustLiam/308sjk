# solve 运行总结

## 迭代历史

- iter 1: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/449189.st:417-25..417-34: error: Invalid assignment syntax ':=' used for parameter 'interp_exe', when invoking FB 'go_x'
./st_files/449189.st:419-25..419-34: error: Invalid assignment syntax ':=' used for parameter 'interp_exe', when invoking FB 'go_y'
./st_files/449189.st:421-25..421-34: error: Invalid assignment syntax ':=' used for parameter 'interp_exe', when invoking FB 'go_z'
./st_files/449189.st:422-69..422-76: error: Invalid assignment syntax ':=' used for parameter 'home_exe', when invoking FB 'hm_x'
./st_files/449189.st:423-69..423-76: error: Invalid assignment syntax ':=' used for parameter 'home_exe', when invoking FB 'hm_y'
./st_files/449189.st:424-69..424-76: error: Invalid assignment syntax ':=' used for parameter 'home_exe', when invoking FB 'hm_z'
6 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 2: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter3axis (prog_id=2)
  - [1] 上电（run=0）：三轴 Ready To Switch On；初始笔位 z=0（触纸）
  -   FAIL X 轴 sw.bit0=1 (实际 0000)
  -   FAIL Y 轴 sw.bit0=1 (实际 0000)
  -   FAIL Z 轴 sw.bit0=1 (实际 0000)
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始笔触纸）
  - （验收暂停：段 [1] 存在失败，后续段未执行；已通过段：无——先修复本段，通过后重跑继续）
- iter 3: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter3axis (prog_id=2)
  - [1] 上电（run=0）：三轴 Ready To Switch On；初始笔位 z=0（触纸）
  -   PASS X 轴 sw.bit0=1 (实际 0031)
  -   PASS Y 轴 sw.bit0=1 (实际 0031)
  -   PASS Z 轴 sw.bit0=1 (实际 0031)
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始笔触纸）
  - [2] run=1 → 三轴使能（AC1：≤2s）
- iter 4: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter3axis (prog_id=2)
  - [1] 上电（run=0）：三轴 Ready To Switch On；初始笔位 z=0（触纸）
  -   PASS X 轴 sw.bit0=1 (实际 0031)
  -   PASS Y 轴 sw.bit0=1 (实际 0031)
  -   PASS Z 轴 sw.bit0=1 (实际 0031)
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始笔触纸）
  - [2] run=1 → 三轴使能（AC1：≤2s）
- iter 5: 闸门 **all** 通过

## 结论

best = {"iter": 5, "gate": "all", "ok": true}